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
from .contracts import SchemaRankingRequest
from .jev import SCHEMA_DEFINITIONS, JevNoul
from .jev_experiment import SchemaExperimentInput, from_labelled_case
from .recorded import VerifiedJevAnswer, index_case_rows, unique_choice, verify_jev_run


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


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


def _choice(answer: VerifiedJevAnswer) -> dict[str, Any]:
    choice = answer.response.schema_choice
    return {
        "selected": answer.selected_schema,
        "scores": choice.probabilities,
        "confidence": choice.confidence,
        "score_kind": "Probability",
        "status": "tied_top" if unique_choice(choice) is None else "completed",
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
        content = report_path.read_bytes()
        report = json.loads(content)
        if revision is not None and report["catalogue_revision"] != revision:
            raise EvaluationError("Dashboard runs must use the same catalogue revision")
        run = verify_jev_run(report, inputs)
        revision = run.catalogue_revision
        key = f"jev-{run_index}"
        classifiers.append(
            {
                "id": key,
                "label": label,
                "detail": f"{run.spec_version} · {run.context}",
                "model": run.model,
                "context": run.context,
            }
        )
        reference_path = directory / "reference-agreement.json"
        reference_rows = None
        if reference_path.exists():
            reference = _read(reference_path)
            if reference["catalogue_revision"] != revision:
                raise EvaluationError("Reference and run catalogue revisions differ")
            if reference["model"] != run.model or reference["context"] != run.context:
                raise EvaluationError("Reference model or context differs from the recorded run")
            if reference["track"] != "asim-parser-silver":
                raise EvaluationError("Expected an official-parser silver reference assessment")
            identity = reference["capture_sha256"]
            if reference_identity is not None and reference_identity != identity:
                raise EvaluationError("Dashboard runs must use the same reference captures")
            reference_identity = identity
            reference_rows = index_case_rows(reference["cases"], ids)
            files.append({"name": f"{label} reference", "sha256": _digest(reference_path)})
        for item in inputs:
            verified = run.cases[item.case_id]
            baseline = verified.baseline
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
            classifiers[-1]["criteria"] = verified.request.questions["schema"].criteria
            classifiers[-1]["questions"] = {
                name: question.instructions for name, question in verified.request.questions.items()
            }
            answer = verified.answer
            if answer is not None:
                prediction = _choice(answer)
                prediction.update(
                    {
                        "probes": [
                            {"id": name, "value": probe.noul}
                            for name, probe in answer.probes.answers.items()
                            if isinstance(probe, JevNoul)
                        ],
                        "first_choice": (answer.first_choice or "Tied top choices")
                        if run.decision_policy
                        else None,
                        "elapsed_seconds": verified.elapsed_seconds,
                        "usage": answer.response.usage.model_dump(),
                    }
                )
            else:
                prediction = {
                    "selected": None,
                    "scores": {},
                    "score_kind": "Probability",
                    "status": verified.status,
                }
            cases[item.case_id]["predictions"][key] = prediction
            if reference_rows is not None:
                ref_row = reference_rows[item.case_id]
                if ref_row["request_hash"] != verified.request_hash:
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
        files.append({"name": label, "sha256": hashlib.sha256(content).hexdigest()})
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
            rows = index_case_rows(
                [row.model_dump(mode="json") for row in approach.predictions], ids
            )
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
