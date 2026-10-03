"""Read-only presentation of recorded classifier evidence; never calls a provider."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..evaluation import EvaluationError, load_semantic_mapping_cases
from ..semantic_annotation.artifacts import load_semantic_annotation_queue
from ..semantic_mapping.comparison import ComparisonReport
from .approaches import SourceConceptSchemaRanker
from .contracts import SchemaRankingPrediction, SchemaRankingRequest
from .jev import (
    SCHEMA_DEFINITIONS,
    STAGED_POLICY_VERSION,
    JevNoul,
    JevResponse,
    build_jev_followup_request,
    build_jev_request,
    request_hash,
)
from .jev_experiment import SchemaExperimentInput, from_labelled_case


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _index(rows: list[dict[str, Any]], ids: set[str]) -> dict[str, dict[str, Any]]:
    result = {row["case_id"]: row for row in rows}
    if len(result) != len(rows) or set(result) != ids:
        raise EvaluationError("Dashboard reports must cover the same unique case IDs as the input")
    return result


def load_dashboard_inputs(path: Path, kind: str) -> list[SchemaExperimentInput]:
    if kind not in ("cases", "queue", "experiment"):
        raise EvaluationError("Unknown dashboard input kind")
    if kind == "cases":
        return [from_labelled_case(case) for case in load_semantic_mapping_cases(path)]
    if kind == "queue":
        _, tasks = load_semantic_annotation_queue(path)
        return [SchemaExperimentInput(case_id=task.case_id, source=task.input) for task in tasks]
    return [
        SchemaExperimentInput.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _reference(row: dict[str, Any]) -> dict[str, Any]:
    events = [
        {key: event[key] for key in ("event_id", "reference_schema", "reference_state")}
        for event in row["events"]
    ]
    labels = {event["reference_schema"] for event in events}
    eligible = bool(events) and all(event["reference_state"] == "labelled" for event in events)
    schema = next(iter(labels)) if eligible and len(labels) == 1 else None
    if schema != row["reference_schema"] or (row["reference_state"] == "labelled") != bool(schema):
        raise EvaluationError("Inconsistent template and event reference labels")
    return {
        "schema": schema,
        "state": row["reference_state"],
        "kind": "Official parser",
        "events": events,
    }


def _choice(response: JevResponse, selected: str | None) -> dict[str, Any]:
    choice = response.schema_choice
    top = max(choice.probabilities.values())
    tied = sum(value == top for value in choice.probabilities.values()) > 1
    expected = None if tied else choice.choice
    if selected != expected:
        raise EvaluationError("Recorded selection disagrees with the response or its tied maximum")
    return {
        "selected": selected,
        "scores": choice.probabilities,
        "confidence": choice.confidence,
        "score_kind": "Probability",
        "status": "tied_top" if tied else "completed",
    }


def dashboard_data(
    inputs: list[SchemaExperimentInput],
    runs: list[tuple[str, Path]],
    comparisons: list[Path] | None = None,
) -> dict[str, Any]:
    """Join by case ID and verify outbound request identity before presenting results."""
    ids = {item.case_id for item in inputs}
    if not inputs or len(ids) != len(inputs):
        raise EvaluationError("Dashboard requires nonempty inputs with unique case IDs")
    if not runs and not comparisons:
        raise EvaluationError("Supply at least one recorded run or mapping comparison")
    labels = [label for label, _ in runs]
    if len(labels) != len(set(labels)) or any(not label.strip() for label in labels):
        raise EvaluationError("Run labels must be nonblank and unique")
    ranker = SourceConceptSchemaRanker()
    cases: dict[str, dict[str, Any]] = {}
    for item in inputs:
        baseline = ranker.rank(
            SchemaRankingRequest(
                request_id=item.case_id,
                template=item.source.template,
                candidate_schemas=list(SCHEMA_DEFINITIONS),
            )
        )
        cases[item.case_id] = {
            "id": item.case_id,
            "template": item.source.template,
            "source": item.source.source_metadata.model_dump(mode="json", exclude_none=True),
            "examples": [event.text for event in item.source.representative_events],
            "slots": [slot.model_dump(mode="json") for slot in item.source.parameter_slots],
            "case_label": {
                "schema": item.expected_schema,
                "source": item.label_source,
            }
            if item.expected_schema
            else None,
            "reference": {
                "schema": item.expected_schema,
                "state": "labelled" if item.expected_schema else "unlabelled",
                "kind": f"Case label · {item.label_source}"
                if item.label_source
                else "No reference",
                "events": [],
            },
            "predictions": {
                "source-concept": {
                    "selected": baseline.selected_schema,
                    "scores": {row.schema_name: row.score for row in baseline.ranked_schemas},
                    "score_kind": "Evidence count",
                    "status": baseline.disposition,
                }
            },
        }
    classifiers: list[dict[str, Any]] = [
        {"id": "source-concept", "label": "Source concept", "detail": "Local · template only"}
    ]
    recorded_baselines = {}
    files = []
    revision = None
    reference_identity = None
    for run_index, (label, directory) in enumerate(runs):
        report_path = directory / "report.json"
        report = _read(report_path)
        if report.get("decision_policy") not in (None, STAGED_POLICY_VERSION):
            raise EvaluationError("Unknown recorded staged decision policy")
        current_revision = report["catalogue_revision"]
        if revision is not None and current_revision != revision:
            raise EvaluationError("Dashboard runs must use the same catalogue revision")
        revision = current_revision
        rows = _index(report["rows"], ids)
        key = f"jev-{run_index}"
        classifiers.append(
            {
                "id": key,
                "label": label,
                "detail": f"{report['spec_version']} · {report['context']}",
                "model": report["model"],
                "context": report["context"],
            }
        )
        reference_path = directory / "reference-agreement.json"
        reference_rows = None
        if reference_path.exists():
            reference = _read(reference_path)
            if reference["catalogue_revision"] != revision:
                raise EvaluationError("Reference and run catalogue revisions differ")
            if reference["model"] != report["model"] or reference["context"] != report["context"]:
                raise EvaluationError("Reference model or context differs from the recorded run")
            if reference["track"] != "asim-parser-silver":
                raise EvaluationError("Expected an official-parser silver reference assessment")
            identity = reference["capture_sha256"]
            if reference_identity is not None and reference_identity != identity:
                raise EvaluationError("Dashboard runs must use the same reference captures")
            reference_identity = identity
            reference_rows = _index(reference["cases"], ids)
            files.append({"name": f"{label} reference", "sha256": _digest(reference_path)})
        for item in inputs:
            row = rows[item.case_id]
            baseline = SchemaRankingPrediction.model_validate(row["baseline"])
            if baseline.request_id != item.case_id:
                raise EvaluationError("Baseline request identity does not match its case")
            frozen = baseline.model_dump(mode="json")
            if item.case_id in recorded_baselines and recorded_baselines[item.case_id] != frozen:
                raise EvaluationError("Recorded runs have different baseline predictions")
            recorded_baselines[item.case_id] = frozen
            cases[item.case_id]["predictions"]["source-concept"] = {
                "selected": baseline.selected_schema,
                "scores": {
                    candidate.schema_name: candidate.score for candidate in baseline.ranked_schemas
                },
                "score_kind": "Evidence count",
                "status": baseline.disposition,
            }
            classifiers[0]["detail"] = f"Recorded v{baseline.approach.version} · template only"
            request = build_jev_request(
                item.source,
                context=report["context"],
                nouls=report["nouls"],
                model=report["model"],
                spec_version=report["spec_version"],
            )
            digest = request_hash(request, revision, spec_version=report["spec_version"])
            classifiers[-1]["criteria"] = request.questions["schema"].criteria
            classifiers[-1]["questions"] = {
                name: question.instructions for name, question in request.questions.items()
            }
            if digest != row["request_hash"]:
                raise EvaluationError(f"Run evidence does not match input for {item.case_id}")
            if row.get("expected_schema") != item.expected_schema:
                raise EvaluationError("Recorded run and input expected labels differ")
            if row.get("label_source") != item.label_source:
                raise EvaluationError("Recorded run and input label provenance differ")
            if row["status"] == "completed":
                response = JevResponse.model_validate(row["jev"]["response"])
                probes = response
                first_choice = None
                if report.get("decision_policy"):
                    probes = JevResponse.model_validate(row["probe_stage"]["response"])
                    probes.validate_for(request)
                    followup = build_jev_followup_request(request, probes)
                    if row["final_request_hash"] != request_hash(
                        followup, revision, spec_version=report["spec_version"]
                    ):
                        raise EvaluationError("Staged final request identity does not match probes")
                    response.validate_for(followup)
                    first_scores = probes.schema_choice.probabilities
                    first_top = max(first_scores.values())
                    first_choice = (
                        "Tied top choices"
                        if sum(value == first_top for value in first_scores.values()) > 1
                        else probes.schema_choice.choice
                    )
                else:
                    response.validate_for(request)
                prediction = _choice(response, row["jev"]["selected_schema"])
                prediction.update(
                    {
                        "probes": [
                            {"id": name, "value": answer.noul}
                            for name, answer in probes.answers.items()
                            if isinstance(answer, JevNoul)
                        ],
                        "first_choice": first_choice,
                        "elapsed_seconds": row.get("elapsed_seconds"),
                        "usage": response.usage.model_dump(),
                    }
                )
            else:
                prediction = {
                    "selected": None,
                    "scores": {},
                    "score_kind": "Probability",
                    "status": row["status"],
                }
            cases[item.case_id]["predictions"][key] = prediction
            if reference_rows is not None:
                ref_row = reference_rows[item.case_id]
                if ref_row["request_hash"] != digest:
                    raise EvaluationError("Reference assessment does not belong to this request")
                ref = _reference(ref_row)
                if len(ref["events"]) != len(item.source.representative_events):
                    raise EvaluationError(
                        "Reference examples do not cover the frozen source examples"
                    )
                previous = cases[item.case_id]["reference"]
                if previous["kind"] == "Official parser" and previous != ref:
                    raise EvaluationError("Run reference labels or event coverage differ")
                cases[item.case_id]["reference"] = ref
        files.append({"name": label, "sha256": _digest(report_path)})
    for path in comparisons or []:
        comparison = ComparisonReport.model_validate(_read(path))
        if comparison.oracle != "none" or comparison.context_view != "full":
            raise EvaluationError(
                "Dashboard mapping comparisons require no oracle and full context"
            )
        if revision is not None and comparison.catalogue_revision != revision:
            raise EvaluationError("Mapping comparison catalogue differs from recorded runs")
        revision = comparison.catalogue_revision
        for approach in comparison.approaches:
            rows = _index([row.model_dump(mode="json") for row in approach.predictions], ids)
            key = f"mapping-{len(classifiers)}"
            classifiers.append(
                {
                    "id": key,
                    "label": approach.approach.name,
                    "detail": f"Mapping approach v{approach.approach.version} · imported scores",
                }
            )
            for item in inputs:
                row = rows[item.case_id]
                ranking = row["ranked_schemas"]
                # An unresolved field mapping can still have a non-tied schema ranking.
                selected = (
                    ranking[0]["schema_name"]
                    if ranking and (len(ranking) == 1 or ranking[0]["score"] > ranking[1]["score"])
                    else None
                )
                cases[item.case_id]["predictions"][key] = {
                    "selected": selected,
                    "scores": {r["schema_name"]: r["score"] for r in ranking},
                    "score_kind": "Approach score",
                    "status": row["disposition"],
                    "fields": row["asim_fields"],
                    "warnings": row["warnings"],
                }
        files.append({"name": "Mapping comparison", "sha256": _digest(path)})
    return {
        "classifiers": classifiers,
        "cases": list(cases.values()),
        "catalogue_revision": revision,
        "files": files,
    }


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export_dashboard(
    inputs_path: Path,
    *,
    kind: str,
    runs: list[tuple[str, Path]],
    comparisons: list[Path],
    output: Path,
) -> dict[str, Any]:
    if output.exists():
        raise EvaluationError("Use a new dashboard output file to preserve previous exports")
    try:
        data = dashboard_data(load_dashboard_inputs(inputs_path, kind), runs, comparisons)
        if inputs_path.is_file():
            data["input_sha256"] = _digest(inputs_path)
        # JSON is data, never markup or executable source; neutralize HTML script terminators.
        payload = json.dumps(data, ensure_ascii=True, allow_nan=False).replace("<", "\\u003c")
        template = Path(__file__).with_name("dashboard.html").read_text(encoding="utf-8")
        document = template.replace("__DASHBOARD_DATA__", payload)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(document, encoding="utf-8")
    except (OSError, ValueError, KeyError, TypeError, ValidationError) as error:
        if isinstance(error, EvaluationError):
            raise
        raise EvaluationError(f"Could not export dashboard: {error}") from error
    return data
