"""Reproducible, objective-aware corpus benchmarks."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import tarfile
import tempfile
from collections import Counter
from math import comb
from pathlib import Path
from time import sleep
from typing import Literal
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from pydantic import Field, model_validator

from .catalog import load_catalog
from .clustering import DeepParseClusterer
from .controlled import run_robustness
from .evaluation import SemanticMappingCase, load_semantic_mapping_cases
from .evaluation_splits import (
    load_semantic_dataset_split,
    validate_semantic_case_groups,
)
from .ingestion import read_events
from .models import AsimCatalog, StrictModel
from .redistribution import DESCRIPTIONS as REDISTRIBUTION_DESCRIPTIONS
from .redistribution import RedistributionClass, require_publishable, strictest
from .schema_ranking import rank_clusters
from .semantic_annotation import validate_semantic_promotion_artifacts
from .semantic_mapping.comparison import compare_approaches, compare_split_approaches

Track = Literal[
    "parsing-gold",
    "format-diagnostic",
    "schema-hint",
    "semantic-gold",
    "semantic-development",
]
SEMANTIC_TRACKS = frozenset({"semantic-gold", "semantic-development"})


class BenchmarkError(ValueError):
    """Raised when a corpus or benchmark result cannot be trusted."""


class CorpusSource(StrictModel):
    project: str
    url: str
    paper_url: str | None = None
    terms: str
    # Enforced by the report writers; `terms` above stays as the human explanation.
    redistribution: RedistributionClass = "metrics"
    attribution: str | None = None


class CorpusResource(StrictModel):
    role: Literal["input", "gold"]
    url: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    archive_member: str | None = None
    jsonl_field: str | None = None


class CorpusManifest(StrictModel):
    format_version: Literal["1"] = "1"
    corpus_id: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]*$")
    title: str
    track: Track
    objective: str
    system: str
    interpretation: str
    source: CorpusSource
    resources: list[CorpusResource] = Field(default_factory=list)
    cases: str | None = None
    split: str | None = None
    case_groups: str | None = None
    promotion_manifest: str | None = None
    evaluation_partition: Literal["validation", "test"] | None = None
    max_events: int | None = Field(default=None, ge=1)
    sample_size: int = Field(default=50, ge=1)
    samples_per_cluster: int = Field(default=5, ge=1)
    gold_column: str | None = None
    schema_hint: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9]*$")

    @model_validator(mode="after")
    def validate_track_inputs(self) -> CorpusManifest:
        roles = [resource.role for resource in self.resources]
        if self.track in SEMANTIC_TRACKS:
            if self.cases is None or self.resources:
                raise ValueError("semantic corpora require cases and no remote resources")
            if self.split is None and self.evaluation_partition is not None:
                raise ValueError("evaluation_partition requires a semantic split")
            if self.split is None and (
                self.case_groups is not None or self.promotion_manifest is not None
            ):
                raise ValueError("case_groups and promotion_manifest require a semantic split")
            if self.split is not None and (
                self.case_groups is None or self.promotion_manifest is None
            ):
                raise ValueError("semantic splits require case_groups and promotion_manifest")
        elif self.cases is not None or roles.count("input") != 1:
            raise ValueError("log corpora require exactly one input resource and no cases")
        elif (
            self.split is not None
            or self.case_groups is not None
            or self.promotion_manifest is not None
            or self.evaluation_partition is not None
        ):
            raise ValueError("only semantic corpora may define a split")
        if self.track == "parsing-gold":
            if roles.count("gold") != 1 or self.gold_column is None:
                raise ValueError("parsing-gold corpora require one gold resource and gold_column")
            if self.max_events is not None:
                raise ValueError("parsing-gold corpora cannot truncate inputs")
        elif "gold" in roles or self.gold_column is not None:
            raise ValueError("only parsing-gold corpora may define parsing gold")
        if self.track == "schema-hint":
            if self.schema_hint is None:
                raise ValueError("schema-hint corpora require schema_hint")
        elif self.schema_hint is not None:
            raise ValueError("only schema-hint corpora may define schema_hint")
        return self


class CorpusSummary(StrictModel):
    corpus_id: str
    title: str
    track: Track
    objective: str
    interpretation: str
    system: str
    source: CorpusSource
    resources: list[CorpusResource] = Field(default_factory=list)
    schema_hint: str | None = None
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class BenchmarkRow(StrictModel):
    corpus_id: str
    track: Track
    approach: str
    item_count: int = Field(ge=1)
    metrics: dict[str, float | int]
    primary_metric: str
    split_id: str | None = None
    evaluation_partition: Literal["validation", "test"] | None = None
    reference_item_count: int | None = Field(default=None, ge=1)
    baseline_delta: float | None = None
    # Source-family bootstrap interval on the primary metric, when one applies.
    primary_metric_low: float | None = None
    primary_metric_high: float | None = None


class SampleResolution(StrictModel):
    """What a corpus can decide, published beside the numbers it produced."""

    corpus_id: str
    case_count: int = Field(ge=1)
    group_count: int = Field(ge=1)
    grouping: str
    minimum_detectable_effect: float = Field(ge=0)


class RobustnessSummary(StrictModel):
    """One controlled perturbation result, reported outside the headline metrics."""

    corpus_id: str
    approach: str
    perturbation: str
    family: str
    baseline_field_micro_f1: float = Field(ge=0, le=1)
    perturbed_field_micro_f1: float = Field(ge=0, le=1)
    delta: float
    prediction_stability: float = Field(ge=0, le=1)
    passed: bool


class BenchmarkReport(StrictModel):
    format_version: Literal["1"] = "1"
    revision: str
    catalogue_revision: str | None = None
    baseline_revision: str | None = None
    # The least permissive class among the included corpora governs the whole report.
    redistribution: RedistributionClass = "content"
    attributions: list[str] = Field(default_factory=list)
    corpora: list[CorpusSummary] = Field(min_length=1)
    results: list[BenchmarkRow] = Field(min_length=1)
    sample_resolution: list[SampleResolution] = Field(default_factory=list)
    robustness: list[RobustnessSummary] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def load_corpus_manifests(root: Path) -> list[tuple[Path, CorpusManifest, str]]:
    paths = sorted(root.rglob("manifest.json"))
    if not paths:
        raise BenchmarkError(f"No corpus manifest.json files found in {root}")
    loaded: list[tuple[Path, CorpusManifest, str]] = []
    seen: set[str] = set()
    for path in paths:
        raw = path.read_bytes()
        try:
            manifest = CorpusManifest.model_validate_json(raw)
        except ValueError as error:
            raise BenchmarkError(f"Invalid corpus manifest {path}: {error}") from error
        if manifest.corpus_id in seen:
            raise BenchmarkError(f"Duplicate corpus_id: {manifest.corpus_id}")
        seen.add(manifest.corpus_id)
        fingerprint_input = raw
        if manifest.cases is not None:
            cases_path = _relative_file(path, manifest.cases)
            fingerprint_input += cases_path.read_bytes()
        if manifest.split is not None:
            split_path = _relative_file(path, manifest.split)
            fingerprint_input += split_path.read_bytes()
            assert manifest.case_groups is not None and manifest.promotion_manifest is not None
            fingerprint_input += _relative_file(path, manifest.case_groups).read_bytes()
            fingerprint_input += _relative_file(path, manifest.promotion_manifest).read_bytes()
        loaded.append((path, manifest, hashlib.sha256(fingerprint_input).hexdigest()))
    return loaded


def run_benchmarks(
    registry: Path,
    output_dir: Path,
    *,
    catalog_dir: Path | None,
    cache_dir: Path | None = None,
    revision: str = "unknown",
    baseline_path: Path | None = None,
) -> BenchmarkReport:
    manifests = load_corpus_manifests(registry)
    needs_catalog = any(manifest.track in SEMANTIC_TRACKS for _, manifest, _ in manifests)
    if needs_catalog and catalog_dir is None:
        raise BenchmarkError("--catalog is required when semantic corpora are selected")
    catalog = load_catalog(catalog_dir) if needs_catalog and catalog_dir is not None else None
    cache = cache_dir or output_dir / "cache"
    corpora: list[CorpusSummary] = []
    results: list[BenchmarkRow] = []
    sample_resolution: list[SampleResolution] = []
    robustness: list[RobustnessSummary] = []
    warnings: list[str] = []

    for path, manifest, fingerprint in manifests:
        require_publishable(
            manifest.source.redistribution,
            f"Corpus {manifest.corpus_id!r}",
        )
        corpora.append(
            CorpusSummary(
                corpus_id=manifest.corpus_id,
                title=manifest.title,
                track=manifest.track,
                objective=manifest.objective,
                interpretation=manifest.interpretation,
                system=manifest.system,
                source=manifest.source,
                resources=manifest.resources,
                schema_hint=manifest.schema_hint,
                fingerprint=fingerprint,
            )
        )
        if manifest.track in SEMANTIC_TRACKS:
            assert catalog is not None and manifest.cases is not None
            cases = load_semantic_mapping_cases(_relative_file(path, manifest.cases))
            if manifest.split is None:
                comparison = compare_approaches(cases, catalog)
            else:
                assert manifest.case_groups is not None and manifest.promotion_manifest is not None
                split = load_semantic_dataset_split(_relative_file(path, manifest.split))
                groups = validate_semantic_promotion_artifacts(
                    _relative_file(path, manifest.cases),
                    _relative_file(path, manifest.case_groups),
                    _relative_file(path, manifest.promotion_manifest),
                )
                validate_semantic_case_groups(cases, split, groups)
                comparison = compare_split_approaches(
                    cases,
                    catalog,
                    split,
                    manifest.evaluation_partition or "test",
                )
            for evaluation in comparison.approaches:
                metrics = evaluation.metrics.model_dump(mode="json")
                interval = next(
                    (item for item in evaluation.intervals if item.metric == "field_micro_f1"),
                    None,
                )
                results.append(
                    BenchmarkRow(
                        corpus_id=manifest.corpus_id,
                        track=manifest.track,
                        approach=evaluation.approach.name,
                        item_count=comparison.case_count,
                        metrics=metrics,
                        primary_metric="field_micro_f1",
                        primary_metric_low=interval.lower if interval is not None else None,
                        primary_metric_high=interval.upper if interval is not None else None,
                        split_id=comparison.split_id,
                        evaluation_partition=comparison.evaluation_partition,
                        reference_item_count=(
                            comparison.reference_case_count
                            if comparison.split_id is not None
                            else None
                        ),
                    )
                )
                warnings.extend(
                    f"{manifest.corpus_id}/{evaluation.approach.name}: {warning}"
                    for warning in evaluation.warnings
                )
            warnings.extend(f"{manifest.corpus_id}: {warning}" for warning in comparison.warnings)
            if comparison.sample is not None:
                sample_resolution.append(
                    SampleResolution(
                        corpus_id=manifest.corpus_id,
                        case_count=comparison.sample.case_count,
                        group_count=comparison.sample.group_count,
                        grouping=comparison.sample.grouping,
                        minimum_detectable_effect=comparison.sample.minimum_detectable_effect,
                    )
                )
            robustness.extend(_robustness_rows(manifest.corpus_id, cases, catalog))
            continue

        with tempfile.TemporaryDirectory(prefix="asim-forge-benchmark-") as temporary:
            staged = Path(temporary)
            resource_paths = {
                resource.role: _stage_resource(
                    resource, cache, staged, manifest.max_events, source_dir=path.parent
                )
                for resource in manifest.resources
            }
            events, _ = read_events(staged / "input")
            clustering = DeepParseClusterer(
                system=manifest.system,
                sample_size=manifest.sample_size,
                samples_per_cluster=manifest.samples_per_cluster,
            ).cluster(events)
            result_approach = "deepparse-default"
            if manifest.track == "parsing-gold":
                gold = _read_gold(resource_paths["gold"], manifest.gold_column or "")
                if len(gold) != len(clustering.assignments):
                    raise BenchmarkError(
                        f"{manifest.corpus_id}: {len(gold)} gold rows do not match "
                        f"{len(clustering.assignments)} input events"
                    )
                metrics = parsing_metrics(clustering.assignments, gold)
                primary = "pair_f1"
            elif manifest.track == "schema-hint":
                assert manifest.schema_hint is not None
                ranking = rank_clusters(clustering.clusters)
                ranked_clusters = ranking.clusters
                matching_clusters = sum(
                    cluster.schema_suggestion.schema_name == manifest.schema_hint
                    for cluster in ranked_clusters
                )
                matching_events = sum(
                    cluster.event_count
                    for cluster in ranked_clusters
                    if cluster.schema_suggestion.schema_name == manifest.schema_hint
                )
                fit_events = sum(
                    cluster.event_count
                    for cluster in ranked_clusters
                    if cluster.schema_suggestion.schema_name != "NoFit"
                )
                metrics = {
                    "cluster_count": len(ranked_clusters),
                    "provisional_asim_fit_event_rate": round(fit_events / len(events), 6),
                    "schema_hint_cluster_agreement": round(
                        matching_clusters / len(ranked_clusters), 6
                    ),
                    "schema_hint_event_agreement": round(matching_events / len(events), 6),
                }
                primary = "schema_hint_event_agreement"
                identity = ranking.predictions[0].approach
                result_approach = f"{identity.name}-v{identity.version}"
            else:
                ranking = rank_clusters(clustering.clusters)
                ranked_clusters = ranking.clusters
                slot_clusters = sum(bool(cluster.parameter_slots) for cluster in ranked_clusters)
                fit_events = sum(
                    cluster.event_count
                    for cluster in ranked_clusters
                    if cluster.schema_suggestion.schema_name != "NoFit"
                )
                metrics = {
                    "cluster_count": len(ranked_clusters),
                    "slot_cluster_rate": round(slot_clusters / len(ranked_clusters), 6),
                    "provisional_asim_fit_event_rate": round(fit_events / len(events), 6),
                }
                primary = "provisional_asim_fit_event_rate"
                identity = ranking.predictions[0].approach
                result_approach = f"{identity.name}-v{identity.version}"
            results.append(
                BenchmarkRow(
                    corpus_id=manifest.corpus_id,
                    track=manifest.track,
                    approach=result_approach,
                    item_count=len(events),
                    metrics=metrics,
                    primary_metric=primary,
                )
            )

    report = BenchmarkReport(
        revision=revision,
        catalogue_revision=catalog.manifest.resolved_revision if catalog is not None else None,
        redistribution=strictest([corpus.source.redistribution for corpus in corpora]),
        attributions=sorted(
            {
                corpus.source.attribution
                for corpus in corpora
                if corpus.source.attribution is not None
            }
        ),
        corpora=corpora,
        results=results,
        sample_resolution=sample_resolution,
        robustness=robustness,
        warnings=warnings,
    )
    if baseline_path is not None and baseline_path.is_file():
        report = _apply_baseline(report, _load_report(baseline_path))
    write_benchmark_report(output_dir, report)
    return report


def parsing_metrics(predicted: list[int], gold: list[str]) -> dict[str, float | int]:
    if not predicted or len(predicted) != len(gold):
        raise BenchmarkError("Predicted and gold labels must be non-empty and equal length")
    contingency = Counter(zip(predicted, gold, strict=True))
    predicted_counts = Counter(predicted)
    gold_counts = Counter(gold)
    true_pairs = sum(comb(count, 2) for count in contingency.values())
    predicted_pairs = sum(comb(count, 2) for count in predicted_counts.values())
    gold_pairs = sum(comb(count, 2) for count in gold_counts.values())
    precision = true_pairs / predicted_pairs if predicted_pairs else 0.0
    recall = true_pairs / gold_pairs if gold_pairs else 0.0
    pair_f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    purity_total = sum(
        max(count for (cluster, _), count in contingency.items() if cluster == predicted_id)
        for predicted_id in predicted_counts
    )
    return {
        "predicted_clusters": len(predicted_counts),
        "gold_templates": len(gold_counts),
        "pair_precision": round(precision, 6),
        "pair_recall": round(recall, 6),
        "pair_f1": round(pair_f1, 6),
        "cluster_purity": round(purity_total / len(predicted), 6),
    }


def _robustness_rows(
    corpus_id: str,
    cases: list[SemanticMappingCase],
    catalog: AsimCatalog,
) -> list[RobustnessSummary]:
    """Controlled perturbation results, kept out of the headline metrics."""
    report = run_robustness(cases, catalog)
    return [
        RobustnessSummary(
            corpus_id=corpus_id,
            approach=row.approach,
            perturbation=row.perturbation,
            family=row.family,
            baseline_field_micro_f1=row.baseline_field_micro_f1,
            perturbed_field_micro_f1=row.perturbed_field_micro_f1,
            delta=row.field_micro_f1_delta,
            prediction_stability=row.prediction_stability,
            passed=row.passed,
        )
        for row in report.rows
    ]


def write_benchmark_report(output_dir: Path, report: BenchmarkReport) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "benchmark-report.json").write_text(
        json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (output_dir / "benchmark-report.md").write_text(
        render_markdown(report), encoding="utf-8", newline="\n"
    )


def render_markdown(report: BenchmarkReport) -> str:
    summaries = {corpus.corpus_id: corpus for corpus in report.corpora}
    lines = ["# ASIM Forge evaluation", "", f"Revision: `{report.revision}`"]
    if report.baseline_revision is not None:
        lines.append(f"Baseline: `{report.baseline_revision}`")
    if report.catalogue_revision is not None:
        lines.append(f"ASIM catalogue: `{report.catalogue_revision}`")
    lines.extend(_provenance_section(report))
    lines.extend(["", "## Parsing gold", ""])
    parsing_rows = [row for row in report.results if row.track == "parsing-gold"]
    lines.extend(
        _table(
            ["Corpus", "Events", "Pred/gold", "Pair F1", "Purity", "Delta"],
            [
                [
                    summaries[row.corpus_id].title,
                    str(row.item_count),
                    f"{row.metrics['predicted_clusters']}/{row.metrics['gold_templates']}",
                    _number(row.metrics["pair_f1"]),
                    _number(row.metrics["cluster_purity"]),
                    _delta(row.baseline_delta),
                ]
                for row in parsing_rows
            ],
        )
    )
    lines.extend(
        [
            "",
            "Pairwise F1 and purity measure template grouping against corpus labels; they do "
            "not measure incident detection or ASIM correctness.",
            "",
            "## Security-format diagnostics",
            "",
        ]
    )
    diagnostic_rows = [row for row in report.results if row.track == "format-diagnostic"]
    lines.extend(
        _table(
            ["Corpus", "Events", "Clusters", "Slot clusters", "Provisional ASIM fit", "Delta"],
            [
                [
                    summaries[row.corpus_id].title,
                    str(row.item_count),
                    str(row.metrics["cluster_count"]),
                    _number(row.metrics["slot_cluster_rate"]),
                    _number(row.metrics["provisional_asim_fit_event_rate"]),
                    _delta(row.baseline_delta),
                ]
                for row in diagnostic_rows
            ],
        )
    )
    lines.extend(
        [
            "",
            "These are operational diagnostics only. The upstream security objectives and "
            "labels are not treated as ASIM schema ground truth.",
            "",
            "## Upstream ASIM schema hints",
            "",
        ]
    )
    hint_rows = [row for row in report.results if row.track == "schema-hint"]
    lines.extend(
        _table(
            ["Corpus", "Hint", "Events", "Clusters", "Provisional fit", "Agreement", "Delta"],
            [
                [
                    summaries[row.corpus_id].title,
                    summaries[row.corpus_id].schema_hint or "unknown",
                    str(row.item_count),
                    str(row.metrics["cluster_count"]),
                    _number(row.metrics["provisional_asim_fit_event_rate"]),
                    _number(row.metrics["schema_hint_event_agreement"]),
                    _delta(row.baseline_delta),
                ]
                for row in hint_rows
            ],
        )
    )
    lines.extend(
        [
            "",
            "Agreement uses Microsoft sample placement as a file-level schema hint. It is "
            "weak upstream supervision, not adjudicated ASIM schema or field ground truth.",
            "",
            "## ASIM semantic mapping",
            "",
            "Gold and development results are reported separately because only independently "
            "adjudicated labels support an ASIM correctness claim. Read the permitted claim "
            "for each corpus above.",
            "",
            "### Semantic gold and contract fixtures",
            "",
        ]
    )
    lines.extend(_semantic_table(report, summaries, "semantic-gold"))
    lines.extend(
        [
            "",
            "### Checked development labels",
            "",
            "These rows score checked but unadjudicated labels for error discovery and "
            "approach comparison. They do not support an ASIM correctness claim and are "
            "never combined with semantic gold.",
            "",
        ]
    )
    lines.extend(_semantic_table(report, summaries, "semantic-development"))
    lines.extend(_resolution_section(report))
    lines.extend(_robustness_section(report))
    if report.warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in report.warnings)
    lines.extend(_attribution_section(report))
    lines.append("")
    return "\n".join(lines)


def _semantic_table(
    report: BenchmarkReport,
    summaries: dict[str, CorpusSummary],
    track: Literal["semantic-gold", "semantic-development"],
) -> list[str]:
    rows = [row for row in report.results if row.track == track]
    return _table(
        [
            "Corpus",
            "Split",
            "Approach",
            "Refs/cases",
            "Schema@1",
            "Cand@5",
            "Role F1",
            "Facet F1",
            "Field F1",
            "Field F1 CI",
            "Exact",
            "Delta",
        ],
        [
            [
                summaries[row.corpus_id].title,
                (f"{row.split_id}:{row.evaluation_partition}" if row.split_id is not None else "—"),
                row.approach,
                (
                    f"{row.reference_item_count}/{row.item_count}"
                    if row.reference_item_count is not None
                    else f"—/{row.item_count}"
                ),
                _number(row.metrics["schema_top1_accuracy"]),
                _number(row.metrics.get("candidate_recall_at_5", 0)),
                _number(row.metrics["source_micro_f1"]),
                _number(row.metrics.get("source_facet_micro_f1", 0)),
                _number(row.metrics["field_micro_f1"]),
                _interval(row.primary_metric_low, row.primary_metric_high),
                _number(row.metrics["mapping_exact_match"]),
                _delta(row.baseline_delta),
            ]
            for row in rows
        ],
    )


def _provenance_section(report: BenchmarkReport) -> list[str]:
    """Name each corpus's evidence track and what may be published from it."""
    lines = [
        "",
        "## Evidence and redistribution",
        "",
        f"Governing redistribution class for this report: `{report.redistribution}`. "
        f"{REDISTRIBUTION_DESCRIPTIONS[report.redistribution]}",
        "",
        "Every section below is a separate evidence track. A result may only support the "
        "claim its own track permits, and results are never combined across tracks.",
        "",
    ]
    lines.extend(
        _table(
            ["Corpus", "Track", "Redistribution", "Permitted claim"],
            [
                [
                    corpus.title,
                    f"`{corpus.track}`",
                    f"`{corpus.source.redistribution}`",
                    corpus.interpretation,
                ]
                for corpus in report.corpora
            ],
        )
    )
    return lines


def _resolution_section(report: BenchmarkReport) -> list[str]:
    if not report.sample_resolution:
        return []
    lines = ["", "### What these samples can resolve", ""]
    lines.extend(
        _table(
            ["Corpus", "Cases", "Groups", "Grouping", "Smallest resolvable difference"],
            [
                [
                    entry.corpus_id,
                    str(entry.case_count),
                    str(entry.group_count),
                    entry.grouping,
                    _number(entry.minimum_detectable_effect),
                ]
                for entry in report.sample_resolution
            ],
        )
    )
    lines.extend(
        [
            "",
            "Resolution is bounded by group count, not case count. Differences smaller than "
            "the value above are not distinguishable from source-family noise.",
        ]
    )
    return lines


def _robustness_section(report: BenchmarkReport) -> list[str]:
    if not report.robustness:
        return []
    lines = [
        "",
        "## Controlled robustness",
        "",
        "Generated variants of the seed cases, scored as slices. These are synthetic by "
        "construction and are never averaged into the results above.",
        "",
    ]
    lines.extend(
        _table(
            [
                "Approach",
                "Perturbation",
                "Family",
                "Base F1",
                "Perturbed F1",
                "Stability",
                "Result",
            ],
            [
                [
                    row.approach,
                    row.perturbation,
                    row.family,
                    _number(row.baseline_field_micro_f1),
                    _number(row.perturbed_field_micro_f1),
                    _number(row.prediction_stability),
                    "pass" if row.passed else "**fail**",
                ]
                for row in report.robustness
            ],
        )
    )
    return lines


def _attribution_section(report: BenchmarkReport) -> list[str]:
    if not report.attributions:
        return []
    return ["", "## Attribution", "", *(f"- {item}" for item in report.attributions)]


def _interval(low: float | None, high: float | None) -> str:
    if low is None or high is None:
        return "—"
    return f"{low:.3f}–{high:.3f}"


def _stage_resource(
    resource: CorpusResource,
    cache_dir: Path,
    staged: Path,
    max_events: int | None,
    *,
    source_dir: Path | None = None,
) -> Path:
    content = _fetch_verified(resource, cache_dir, source_dir=source_dir)
    if resource.archive_member is not None:
        try:
            with tarfile.open(fileobj=io.BytesIO(content), mode="r:*") as archive:
                member = archive.getmember(resource.archive_member)
                extracted = archive.extractfile(member)
                if extracted is None or not member.isfile():
                    raise BenchmarkError(f"Archive member is not a file: {resource.archive_member}")
                content = extracted.read()
        except (KeyError, tarfile.TarError) as error:
            raise BenchmarkError(f"Cannot read archive member {resource.archive_member}") from error
    if resource.jsonl_field is not None:
        lines: list[str] = []
        for raw_line in content.decode("utf-8").splitlines():
            if raw_line.strip():
                payload = json.loads(raw_line)
                value = payload.get(resource.jsonl_field)
                if not isinstance(value, str) or not value.strip():
                    raise BenchmarkError(f"Missing string JSONL field {resource.jsonl_field!r}")
                lines.append(value.replace("\r", " ").replace("\n", " "))
                if max_events is not None and len(lines) >= max_events:
                    break
        content = ("\n".join(lines) + "\n").encode()
    elif resource.role == "input" and max_events is not None:
        content = b"\n".join(content.splitlines()[:max_events]) + b"\n"
    directory = staged / resource.role
    directory.mkdir(parents=True, exist_ok=True)
    suffix = ".csv" if resource.role == "gold" else ".log"
    path = directory / f"corpus{suffix}"
    path.write_bytes(content)
    return path


def _fetch_verified(
    resource: CorpusResource, cache_dir: Path, *, source_dir: Path | None = None
) -> bytes:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{resource.sha256}.blob"
    local = resource.url.startswith("local:")
    if local:
        if source_dir is None:
            raise BenchmarkError("A local corpus resource requires its manifest directory")
        source_path = (source_dir / resource.url.removeprefix("local:")).resolve()
        if not source_path.is_relative_to(source_dir.resolve()) or not source_path.is_file():
            raise BenchmarkError(
                f"Local corpus resource is missing or escapes its manifest: {resource.url}"
            )
        content = source_path.read_bytes()
    elif path.is_file():
        content = path.read_bytes()
    else:
        request = Request(resource.url, headers={"User-Agent": "ASIM-Forge-Benchmark/1"})
        for attempt in range(3):
            try:
                with urlopen(request, timeout=60) as response:  # noqa: S310
                    content = response.read()
                break
            except HTTPError as error:
                if error.code not in {429, 500, 502, 503, 504} or attempt == 2:
                    raise BenchmarkError(f"Could not retrieve {resource.url}: {error}") from error
            except OSError as error:
                if attempt == 2:
                    raise BenchmarkError(f"Could not retrieve {resource.url}: {error}") from error
            sleep(2**attempt)
    actual = hashlib.sha256(content).hexdigest()
    if actual != resource.sha256:
        raise BenchmarkError(
            f"Checksum mismatch for {resource.url}: expected {resource.sha256}, got {actual}"
        )
    if not local and not path.is_file():
        path.write_bytes(content)
    return content


def _read_gold(path: Path, column: str) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or column not in reader.fieldnames:
            raise BenchmarkError(f"Gold CSV does not contain {column!r}: {path}")
        return [row[column] for row in reader]


def _relative_file(manifest_path: Path, relative: str) -> Path:
    path = (manifest_path.parent / relative).resolve()
    if not path.is_file():
        raise BenchmarkError(f"Referenced corpus file does not exist: {path}")
    return path


def _load_report(path: Path) -> BenchmarkReport:
    try:
        return BenchmarkReport.model_validate_json(path.read_bytes())
    except ValueError as error:
        raise BenchmarkError(f"Invalid baseline benchmark report {path}: {error}") from error


def _apply_baseline(report: BenchmarkReport, baseline: BenchmarkReport) -> BenchmarkReport:
    current_fingerprints = {corpus.corpus_id: corpus.fingerprint for corpus in report.corpora}
    baseline_fingerprints = {corpus.corpus_id: corpus.fingerprint for corpus in baseline.corpora}
    old_rows = {(row.corpus_id, row.approach): row for row in baseline.results}
    for row in report.results:
        old = old_rows.get((row.corpus_id, row.approach))
        if old is None or current_fingerprints.get(row.corpus_id) != baseline_fingerprints.get(
            row.corpus_id
        ):
            continue
        if row.primary_metric != old.primary_metric:
            continue
        current_value = row.metrics.get(row.primary_metric)
        old_value = old.metrics.get(old.primary_metric)
        if isinstance(current_value, (int, float)) and isinstance(old_value, (int, float)):
            row.baseline_delta = round(float(current_value) - float(old_value), 6)
    report.baseline_revision = baseline.revision
    return report


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    return [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
        *("| " + " | ".join(row) + " |" for row in rows),
    ]


def _number(value: float | int) -> str:
    return f"{float(value):.3f}"


def _delta(value: float | None) -> str:
    return "—" if value is None else f"{value:+.3f}"
