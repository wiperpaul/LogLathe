"""Schema-only experiment artifacts; no mapping or review state is mutated."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Literal

from ..evaluation import EvaluationError, SemanticMappingCase
from ..models import StrictModel
from ..semantic_mapping.types import SemanticMappingInput
from .approaches.source_concept import SourceConceptSchemaRanker
from .contracts import SchemaRankingRequest
from .jev import (
    AUTH_LIFECYCLE_SPEC_VERSION,
    DEFAULT_MODEL,
    DEFINITION_SOURCES,
    SCHEMA_DEFINITIONS,
    SPEC_VERSION,
    STAGED_POLICY_VERSION,
    UNSUPPORTED,
    DecisionSpec,
    build_jev_followup_request,
    build_jev_request,
    request_hash,
)
from .jev_client import JevError, cached_response


class SchemaExperimentInput(StrictModel):
    case_id: str
    source: SemanticMappingInput
    expected_schema: str | None = None
    label_source: str | None = None


def from_labelled_case(case: SemanticMappingCase) -> SchemaExperimentInput:
    expected = case.expected
    label = None
    if expected.disposition == "mapped":
        if expected.schema_name not in SCHEMA_DEFINITIONS:
            raise EvaluationError(
                f"Case {case.case_id} targets a schema outside the Jev experiment candidate set"
            )
        label = expected.schema_name
    elif expected.disposition == "not_applicable":
        label = UNSUPPORTED
    return SchemaExperimentInput(
        case_id=case.case_id,
        source=case.input,
        expected_schema=label,
        label_source=case.provenance.label_source,
    )


def _metrics(rows: list[dict[str, Any]], approach: str) -> dict[str, Any]:
    labelled = [row for row in rows if row["expected_schema"] is not None]
    completed = [row for row in labelled if row.get(approach) is not None]
    correct = sum(row[approach]["selected_schema"] == row["expected_schema"] for row in completed)
    return {
        "labelled": len(labelled),
        "completed": len(completed),
        "correct": correct,
        "accuracy_on_completed": correct / len(completed) if completed else None,
        "completion_rate": len(completed) / len(labelled) if labelled else None,
        "unlabelled_or_unresolved": len(rows) - len(labelled),
    }


def run_schema_experiment(
    inputs: list[SchemaExperimentInput],
    *,
    catalogue_revision: str,
    output: Path,
    cache: Path,
    mode: Literal["prepare", "live", "replay"] = "prepare",
    context: Literal["template", "enriched"] = "enriched",
    nouls: bool = False,
    model: str = DEFAULT_MODEL,
    api_key: str = "",
    split_provenance: dict[str, str] | None = None,
    spec_version: DecisionSpec = SPEC_VERSION,
    staged: bool = False,
) -> dict[str, Any]:
    if not inputs:
        raise EvaluationError("The schema experiment requires at least one input")
    if len({item.case_id for item in inputs}) != len(inputs):
        raise EvaluationError("The schema experiment requires unique case IDs")
    if mode not in ("prepare", "live", "replay"):
        raise EvaluationError("Unknown schema experiment mode")
    if staged and spec_version != AUTH_LIFECYCLE_SPEC_VERSION:
        raise EvaluationError(
            "Staged classification requires --decision-spec asim-auth-lifecycle-v3"
        )
    nouls = nouls or staged
    if mode == "live" and not api_key.strip():
        raise JevError("Set TYPESAFE_API_KEY in the environment before using --live")
    if output.exists():
        raise EvaluationError("Use a new output directory to preserve previous experiment reports")
    if output.resolve() == cache.resolve():
        raise EvaluationError("Cache and output must use different directories")

    requests = [
        build_jev_request(
            item.source, context=context, nouls=nouls, model=model, spec_version=spec_version
        )
        for item in inputs
    ]
    rows: list[dict[str, Any]] = []
    baseline = SourceConceptSchemaRanker()
    for item, request in zip(inputs, requests, strict=True):
        lexical = baseline.rank(
            SchemaRankingRequest(
                request_id=item.case_id,
                template=item.source.template,
                candidate_schemas=list(SCHEMA_DEFINITIONS),
            )
        )
        rows.append(
            {
                "case_id": item.case_id,
                "request_hash": request_hash(
                    request, catalogue_revision, spec_version=spec_version
                ),
                "expected_schema": item.expected_schema,
                "label_source": item.label_source,
                "baseline": lexical.model_dump(mode="json"),
                "jev": None,
                "status": "prepared" if mode == "prepare" else "not_run",
            }
        )

    output.mkdir(parents=True)
    # Every outbound body is reviewable even if the first live request fails.
    (output / "requests.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "case_id": row["case_id"],
                    "request_hash": row["request_hash"],
                    "body": request.model_dump(mode="json"),
                },
                sort_keys=True,
            )
            + "\n"
            for row, request in zip(rows, requests, strict=True)
        ),
        encoding="utf-8",
    )
    failure: str | None = None
    if mode != "prepare":
        for row, request in zip(rows, requests, strict=True):
            started = time.perf_counter()
            try:
                response, hit = cached_response(
                    request,
                    catalogue_revision,
                    cache,
                    live=mode == "live",
                    api_key=api_key,
                    spec_version=spec_version,
                )
                if staged:
                    row["probe_stage"] = {
                        "request_hash": row["request_hash"],
                        "cache_hit": hit,
                        "response": response.model_dump(mode="json"),
                        "elapsed_seconds": round(time.perf_counter() - started, 6),
                    }
                    followup = build_jev_followup_request(request, response)
                    digest = request_hash(followup, catalogue_revision, spec_version=spec_version)
                    row["final_request_hash"] = digest
                    # Persist the exact dependent body before sending; never invent probe values.
                    with (output / "second-stage-requests.jsonl").open(
                        "a", encoding="utf-8"
                    ) as file:
                        file.write(
                            json.dumps(
                                {
                                    "case_id": row["case_id"],
                                    "request_hash": digest,
                                    "body": followup.model_dump(mode="json"),
                                },
                                sort_keys=True,
                            )
                            + "\n"
                        )
                    response, final_hit = cached_response(
                        followup,
                        catalogue_revision,
                        cache,
                        live=mode == "live",
                        api_key=api_key,
                        spec_version=spec_version,
                    )
                    row["final_cache_hit"] = final_hit
                    hit = hit and final_hit
                choice = response.schema_choice
                top = max(choice.probabilities.values())
                tied = sum(value == top for value in choice.probabilities.values()) > 1
                row.update(
                    {
                        "status": "completed",
                        "cache_hit": hit,
                        "jev": {
                            # A tie does not acquire a semantic meaning from the provider's argmax.
                            "selected_schema": None if tied else choice.choice,
                            "disposition": "tied_top"
                            if tied
                            else ("unsupported" if choice.choice == UNSUPPORTED else "suggested"),
                            "response": response.model_dump(mode="json"),
                        },
                    }
                )
            except JevError as error:
                failure = str(error)
                row.update({"status": "error", "error": failure})
            except OSError:
                failure = "Could not read or persist the Jev cache"
                row.update({"status": "error", "error": failure})
            row["elapsed_seconds"] = round(time.perf_counter() - started, 6)
            if failure:
                # Do not repeatedly send a batch after an authentication/service/cache failure.
                break

    report = {
        "format_version": "1",
        "spec_version": spec_version,
        "definition_sources": DEFINITION_SOURCES,
        "catalogue_revision": catalogue_revision,
        "model": model,
        "mode": mode,
        "context": context,
        "nouls": nouls,
        "split": split_provenance,
        "candidate_schemas": list(SCHEMA_DEFINITIONS),
        "review_policy": "advisory_only",
        "completed": sum(row["status"] == "completed" for row in rows),
        "cache_hits": sum(row.get("cache_hit", False) for row in rows),
        "error": failure,
        "metrics": {name: _metrics(rows, name) for name in ("baseline", "jev")},
        "rows": rows,
    }
    if staged:
        report["decision_policy"] = STAGED_POLICY_VERSION
        report["second_stage_status"] = (
            "requires_probe_answers"
            if mode == "prepare"
            else ("partial" if failure else "completed")
        )
    (output / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )
    if failure:
        raise JevError(f"{failure}; partial report saved in {output}")
    return report
