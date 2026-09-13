from __future__ import annotations

import hashlib
import io
import json
import tarfile
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from asim_forge import benchmarking, cli
from asim_forge.benchmarking import (
    BenchmarkError,
    BenchmarkReport,
    BenchmarkRow,
    CorpusManifest,
    CorpusResource,
    CorpusSource,
    CorpusSummary,
    _apply_baseline,
    _fetch_verified,
    _stage_resource,
    load_corpus_manifests,
    parsing_metrics,
    render_markdown,
    run_benchmarks,
)
from asim_forge.commands import evaluation as evaluation_command


@pytest.mark.parametrize("track", ["semantic-gold", "semantic-development"])
def test_semantic_benchmark_split_requires_frozen_group_evidence(track: str) -> None:
    payload = {
        "corpus_id": "semantic-split",
        "title": "Semantic split",
        "track": track,
        "objective": "ASIM correctness",
        "system": "test",
        "interpretation": "Held-out evaluation.",
        "source": {
            "project": "test",
            "url": "https://invalid.example",
            "terms": "test",
        },
        "cases": "cases.jsonl",
        "split": "split.json",
    }

    with pytest.raises(ValueError, match="case_groups and promotion_manifest"):
        CorpusManifest.model_validate(payload)


def test_schema_hint_track_requires_an_exclusive_file_level_hint() -> None:
    payload = {
        "corpus_id": "schema-hint",
        "title": "Schema hint",
        "track": "schema-hint",
        "objective": "Weak schema supervision",
        "system": "test",
        "interpretation": "Not gold.",
        "source": {
            "project": "test",
            "url": "https://invalid.example",
            "terms": "test",
        },
        "resources": [
            {
                "role": "input",
                "url": "https://invalid.example/log",
                "sha256": "a" * 64,
            }
        ],
    }

    with pytest.raises(ValueError, match="require schema_hint"):
        CorpusManifest.model_validate(payload)

    payload["schema_hint"] = "Authentication"
    assert CorpusManifest.model_validate(payload).schema_hint == "Authentication"

    payload["track"] = "format-diagnostic"
    with pytest.raises(ValueError, match="only schema-hint"):
        CorpusManifest.model_validate(payload)


def test_checked_corpus_registry_has_separate_objective_tracks() -> None:
    loaded = load_corpus_manifests(Path("evaluation/corpora"))

    tracks = [manifest.track for _, manifest, _ in loaded]
    assert len(loaded) == 11
    assert tracks.count("parsing-gold") == 3
    assert tracks.count("format-diagnostic") == 3
    assert tracks.count("schema-hint") == 3
    assert tracks.count("semantic-gold") == 1
    assert tracks.count("semantic-development") == 1
    assert all(len(fingerprint) == 64 for _, _, fingerprint in loaded)


def test_pairwise_parsing_metrics_distinguish_merges_and_splits() -> None:
    metrics = parsing_metrics([1, 1, 1, 2], ["a", "a", "b", "b"])

    assert metrics == {
        "predicted_clusters": 2,
        "gold_templates": 2,
        "pair_precision": 0.333333,
        "pair_recall": 0.5,
        "pair_f1": 0.4,
        "cluster_purity": 0.75,
    }


def test_verified_fetch_rejects_poisoned_cache(tmp_path: Path) -> None:
    expected = hashlib.sha256(b"expected").hexdigest()
    (tmp_path / f"{expected}.blob").write_bytes(b"different")
    resource = CorpusResource(role="input", url="https://invalid.example", sha256=expected)

    with pytest.raises(BenchmarkError, match="Checksum mismatch"):
        _fetch_verified(resource, tmp_path)


def test_verified_fetch_retries_gateway_timeout_then_reuses_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = b"public corpus"
    resource = CorpusResource(
        role="input",
        url="https://invalid.example/corpus",
        sha256=hashlib.sha256(content).hexdigest(),
    )
    attempts = 0

    def fetch(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise HTTPError(resource.url, 504, "Gateway Time-out", Message(), None)
        return io.BytesIO(content)

    monkeypatch.setattr(benchmarking, "urlopen", fetch)
    monkeypatch.setattr(benchmarking, "sleep", lambda _seconds: None)
    assert _fetch_verified(resource, tmp_path) == content
    assert attempts == 2
    assert _fetch_verified(resource, tmp_path) == content
    assert attempts == 2


def test_verified_fetch_does_not_retry_missing_resource(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resource = CorpusResource(role="input", url="https://invalid.example/missing", sha256="a" * 64)
    attempts = 0

    def fetch(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise HTTPError(resource.url, 404, "Not Found", Message(), None)

    monkeypatch.setattr(benchmarking, "urlopen", fetch)
    with pytest.raises(BenchmarkError, match="HTTP Error 404"):
        _fetch_verified(resource, tmp_path)
    assert attempts == 1


def test_archive_jsonl_resource_extracts_only_requested_field(tmp_path: Path) -> None:
    jsonl = b'{"raw_log":"first log","secret":"x"}\n{"raw_log":"second log"}\n'
    archive_bytes = io.BytesIO()
    with tarfile.open(fileobj=archive_bytes, mode="w:gz") as archive:
        info = tarfile.TarInfo("artifact/logs.jsonl")
        info.size = len(jsonl)
        archive.addfile(info, io.BytesIO(jsonl))
    content = archive_bytes.getvalue()
    digest = hashlib.sha256(content).hexdigest()
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / f"{digest}.blob").write_bytes(content)
    resource = CorpusResource(
        role="input",
        url="https://invalid.example/archive.tar.gz",
        sha256=digest,
        archive_member="artifact/logs.jsonl",
        jsonl_field="raw_log",
    )

    path = _stage_resource(resource, cache, tmp_path / "stage", max_events=1)

    assert path.read_text(encoding="utf-8") == "first log\n"


def test_local_parsing_corpus_runs_and_writes_both_reports(tmp_path: Path) -> None:
    logs = b"service started pid=1\nservice started pid=2\nservice stopped pid=1\n"
    gold = b"LineId,EventId\n1,E1\n2,E1\n3,E2\n"
    log_hash = hashlib.sha256(logs).hexdigest()
    gold_hash = hashlib.sha256(gold).hexdigest()
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / f"{log_hash}.blob").write_bytes(logs)
    (cache / f"{gold_hash}.blob").write_bytes(gold)
    registry = tmp_path / "registry" / "tiny"
    registry.mkdir(parents=True)
    manifest = {
        "format_version": "1",
        "corpus_id": "tiny",
        "title": "Tiny parser corpus",
        "track": "parsing-gold",
        "objective": "Test parsing",
        "system": "tiny",
        "interpretation": "Parsing labels only.",
        "source": {
            "project": "test",
            "url": "https://invalid.example",
            "terms": "test fixture",
        },
        "resources": [
            {
                "role": "input",
                "url": "https://invalid.example/log",
                "sha256": log_hash,
            },
            {
                "role": "gold",
                "url": "https://invalid.example/gold",
                "sha256": gold_hash,
            },
        ],
        "gold_column": "EventId",
    }
    (registry / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "output"

    report = run_benchmarks(
        tmp_path / "registry",
        output,
        catalog_dir=None,
        cache_dir=cache,
        revision="abc123",
    )

    assert report.results[0].item_count == 3
    assert report.results[0].primary_metric == "pair_f1"
    assert (output / "benchmark-report.json").is_file()
    assert "Parsing gold" in (output / "benchmark-report.md").read_text(encoding="utf-8")


def test_local_format_corpus_reports_diagnostics_without_accuracy(tmp_path: Path) -> None:
    logs = b"accepted connection from 192.0.2.1\naccepted connection from 192.0.2.2\n"
    digest = hashlib.sha256(logs).hexdigest()
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / f"{digest}.blob").write_bytes(logs)
    corpus = tmp_path / "registry" / "diagnostic"
    corpus.mkdir(parents=True)
    manifest = {
        "format_version": "1",
        "corpus_id": "diagnostic",
        "title": "Diagnostic corpus",
        "track": "format-diagnostic",
        "objective": "Exercise an input format",
        "system": "diagnostic",
        "interpretation": "No accuracy labels.",
        "source": {
            "project": "test",
            "url": "https://invalid.example",
            "terms": "test fixture",
        },
        "resources": [
            {
                "role": "input",
                "url": "https://invalid.example/log",
                "sha256": digest,
            }
        ],
    }
    (corpus / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    report = run_benchmarks(
        tmp_path / "registry",
        tmp_path / "output",
        catalog_dir=None,
        cache_dir=cache,
    )

    metrics = report.results[0].metrics
    assert report.results[0].track == "format-diagnostic"
    assert "cluster_count" in metrics
    assert "pair_f1" not in metrics


def test_local_schema_hint_corpus_reports_weak_agreement_separately(tmp_path: Path) -> None:
    logs = b"user login accepted from 192.0.2.1\nuser login accepted from 192.0.2.2\n"
    digest = hashlib.sha256(logs).hexdigest()
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / f"{digest}.blob").write_bytes(logs)
    corpus = tmp_path / "registry" / "schema-hint"
    corpus.mkdir(parents=True)
    manifest = {
        "format_version": "1",
        "corpus_id": "schema-hint",
        "title": "Authentication schema hint",
        "track": "schema-hint",
        "objective": "Exercise weak schema supervision",
        "system": "schema-hint",
        "interpretation": "File-level upstream hint, not gold.",
        "source": {
            "project": "test",
            "url": "https://invalid.example",
            "terms": "test fixture",
        },
        "resources": [
            {
                "role": "input",
                "url": "https://invalid.example/log",
                "sha256": digest,
            }
        ],
        "schema_hint": "Authentication",
    }
    (corpus / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    report = run_benchmarks(
        tmp_path / "registry",
        tmp_path / "output",
        catalog_dir=None,
        cache_dir=cache,
    )

    result = report.results[0]
    assert result.track == "schema-hint"
    assert result.approach == "source-concept-v1"
    assert result.primary_metric == "schema_hint_event_agreement"
    assert result.metrics["schema_hint_event_agreement"] == 1.0
    assert report.corpora[0].schema_hint == "Authentication"
    markdown = (tmp_path / "output" / "benchmark-report.md").read_text(encoding="utf-8")
    assert "Upstream ASIM schema hints" in markdown
    assert "weak upstream supervision" in markdown


@pytest.mark.parametrize("track", ["semantic-gold", "semantic-development"])
def test_semantic_corpus_uses_the_shared_comparison_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, track: str
) -> None:
    corpus = tmp_path / "registry" / "semantic"
    corpus.mkdir(parents=True)
    (corpus / "cases.jsonl").write_text("fixture\n", encoding="utf-8")
    manifest = {
        "format_version": "1",
        "corpus_id": "semantic",
        "title": "Semantic corpus",
        "track": track,
        "objective": "ASIM correctness",
        "system": "test",
        "interpretation": "Adjudicated labels.",
        "source": {
            "project": "test",
            "url": "https://invalid.example",
            "terms": "test fixture",
        },
        "resources": [],
        "cases": "cases.jsonl",
    }
    (corpus / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    catalog = SimpleNamespace(manifest=SimpleNamespace(resolved_revision="a" * 40))
    metrics = {
        "schema_top1_accuracy": 1.0,
        "source_micro_f1": 0.8,
        "field_micro_f1": 0.75,
        "mapping_exact_match": 0.5,
    }
    evaluation = SimpleNamespace(
        approach=SimpleNamespace(name="test-approach"),
        metrics=SimpleNamespace(model_dump=lambda mode: metrics),
        intervals=[],
        warnings=["small fixture"],
    )
    monkeypatch.setattr(benchmarking, "load_catalog", lambda path: catalog)
    monkeypatch.setattr(benchmarking, "load_semantic_mapping_cases", lambda path: [object()])
    monkeypatch.setattr(benchmarking, "_robustness_rows", lambda corpus, cases, cat: [])
    monkeypatch.setattr(
        benchmarking,
        "compare_approaches",
        lambda cases, loaded_catalog: SimpleNamespace(
            approaches=[evaluation],
            case_count=1,
            reference_case_count=1,
            split_id=None,
            evaluation_partition=None,
            sample=None,
            warnings=[],
        ),
    )

    report = run_benchmarks(
        tmp_path / "registry",
        tmp_path / "output",
        catalog_dir=tmp_path / "catalog",
    )

    assert report.catalogue_revision == "a" * 40
    assert report.results[0].track == track
    assert report.results[0].metrics["field_micro_f1"] == 0.75
    assert report.warnings == ["semantic/test-approach: small fixture"]
    markdown = (tmp_path / "output" / "benchmark-report.md").read_text(encoding="utf-8")
    gold_section, development_section = markdown.split("### Checked development labels", 1)
    gold_section = gold_section.split("### Semantic gold and contract fixtures", 1)[1]
    expected_section = gold_section if track == "semantic-gold" else development_section
    other_section = development_section if track == "semantic-gold" else gold_section
    assert "Semantic corpus" in expected_section
    assert "Semantic corpus" not in other_section


def test_semantic_benchmark_verifies_frozen_groups_before_split_comparison(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    corpus = tmp_path / "registry" / "semantic-split"
    corpus.mkdir(parents=True)
    for name in (
        "cases.jsonl",
        "split.json",
        "case-groups.jsonl",
        "promotion-manifest.json",
    ):
        (corpus / name).write_text(name, encoding="utf-8")
    manifest = {
        "corpus_id": "semantic-split",
        "title": "Semantic split",
        "track": "semantic-gold",
        "objective": "ASIM correctness",
        "system": "test",
        "interpretation": "Held-out labels.",
        "source": {
            "project": "test",
            "url": "https://invalid.example",
            "terms": "test",
        },
        "cases": "cases.jsonl",
        "split": "split.json",
        "case_groups": "case-groups.jsonl",
        "promotion_manifest": "promotion-manifest.json",
        "evaluation_partition": "test",
    }
    (corpus / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    catalog = SimpleNamespace(manifest=SimpleNamespace(resolved_revision="a" * 40))
    split = object()
    groups = [object()]
    calls: list[str] = []
    metrics = {
        "schema_top1_accuracy": 1.0,
        "source_micro_f1": 0.75,
        "field_micro_f1": 0.5,
        "mapping_exact_match": 0.25,
    }
    evaluation = SimpleNamespace(
        approach=SimpleNamespace(name="case-retrieval"),
        metrics=SimpleNamespace(model_dump=lambda mode: metrics),
        intervals=[],
        warnings=[],
    )
    monkeypatch.setattr(benchmarking, "load_catalog", lambda path: catalog)
    monkeypatch.setattr(benchmarking, "load_semantic_mapping_cases", lambda path: [object()])
    monkeypatch.setattr(benchmarking, "_robustness_rows", lambda corpus, cases, cat: [])
    monkeypatch.setattr(benchmarking, "load_semantic_dataset_split", lambda path: split)
    monkeypatch.setattr(
        benchmarking,
        "validate_semantic_promotion_artifacts",
        lambda cases, case_groups, promotion: groups,
    )
    monkeypatch.setattr(
        benchmarking,
        "validate_semantic_case_groups",
        lambda cases, loaded_split, loaded_groups: calls.append("validated"),
    )
    monkeypatch.setattr(
        benchmarking,
        "compare_split_approaches",
        lambda cases, loaded_catalog, loaded_split, partition: SimpleNamespace(
            approaches=[evaluation],
            case_count=1,
            reference_case_count=2,
            split_id="source-family.v1",
            evaluation_partition=partition,
            sample=None,
            warnings=[],
        ),
    )

    report = run_benchmarks(
        tmp_path / "registry",
        tmp_path / "output",
        catalog_dir=tmp_path / "catalog",
    )

    assert calls == ["validated"]
    assert report.results[0].split_id == "source-family.v1"
    assert report.results[0].reference_item_count == 2


def test_baseline_delta_requires_same_corpus_fingerprint() -> None:
    source = CorpusSource(project="test", url="https://example.test", terms="test")
    summary = CorpusSummary(
        corpus_id="same",
        title="Same",
        track="parsing-gold",
        objective="parsing",
        interpretation="parsing only",
        system="test",
        source=source,
        fingerprint="a" * 64,
    )
    old = BenchmarkReport(
        revision="old",
        corpora=[summary],
        results=[
            BenchmarkRow(
                corpus_id="same",
                track="parsing-gold",
                approach="parser",
                item_count=2,
                metrics={
                    "predicted_clusters": 2,
                    "gold_templates": 2,
                    "pair_f1": 0.5,
                    "cluster_purity": 0.75,
                },
                primary_metric="pair_f1",
            )
        ],
    )
    current = BenchmarkReport(
        revision="new",
        corpora=[summary.model_copy(deep=True)],
        results=[
            BenchmarkRow(
                corpus_id="same",
                track="parsing-gold",
                approach="parser",
                item_count=2,
                metrics={
                    "predicted_clusters": 2,
                    "gold_templates": 2,
                    "pair_f1": 0.75,
                    "cluster_purity": 0.75,
                },
                primary_metric="pair_f1",
            )
        ],
    )

    compared = _apply_baseline(current, old)

    assert compared.baseline_revision == "old"
    assert compared.results[0].baseline_delta == 0.25
    assert "Pairwise F1" in render_markdown(compared)


def test_cli_runs_registered_benchmark(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = CorpusSource(project="test", url="https://example.test", terms="test")
    report = BenchmarkReport(
        revision="test",
        corpora=[
            CorpusSummary(
                corpus_id="diagnostic",
                title="Diagnostic",
                track="format-diagnostic",
                objective="format",
                interpretation="diagnostic only",
                system="test",
                source=source,
                fingerprint="a" * 64,
            )
        ],
        results=[
            BenchmarkRow(
                corpus_id="diagnostic",
                track="format-diagnostic",
                approach="parser",
                item_count=1,
                metrics={"provisional_asim_fit_event_rate": 0.0},
                primary_metric="provisional_asim_fit_event_rate",
            )
        ],
    )
    monkeypatch.setattr(evaluation_command, "run_benchmarks", lambda *args, **kwargs: report)

    cli.main(["evaluation", "benchmark", "registry", "--output", "output"])

    assert "Evaluated 1 corpora" in capsys.readouterr().out
