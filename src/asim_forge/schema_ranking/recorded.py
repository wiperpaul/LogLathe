"""Verify recorded Jev runs against their frozen inputs without calling a provider.

The read-only dashboard and assisted schema review both consume these results, so
request identity, staged probes, and recorded selections are checked in one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ..evaluation import EvaluationError
from .contracts import SchemaRankingPrediction
from .jev import (
    STAGED_POLICY_VERSION,
    DecisionSpec,
    JevChoice,
    JevRequest,
    JevResponse,
    build_jev_followup_request,
    build_jev_request,
    request_hash,
)
from .jev_experiment import SchemaExperimentInput


@dataclass(frozen=True)
class VerifiedJevAnswer:
    """A completed row whose responses answer the requests rebuilt from its input."""

    response: JevResponse
    # Staged runs record a separate probe response; otherwise this is ``response``.
    probes: JevResponse
    selected_schema: str | None
    first_choice: str | None
    final_request_hash: str


@dataclass(frozen=True)
class VerifiedJevCase:
    case_id: str
    request: JevRequest
    request_hash: str
    baseline: SchemaRankingPrediction
    status: str
    answer: VerifiedJevAnswer | None
    elapsed_seconds: float | None


@dataclass(frozen=True)
class VerifiedJevRun:
    catalogue_revision: str
    model: str
    context: Literal["template", "enriched"]
    spec_version: DecisionSpec
    decision_policy: str | None
    cases: dict[str, VerifiedJevCase]


def index_case_rows(rows: list[dict[str, Any]], ids: set[str]) -> dict[str, dict[str, Any]]:
    result = {row["case_id"]: row for row in rows}
    if len(result) != len(rows) or set(result) != ids:
        raise EvaluationError("Recorded reports must cover the same unique case IDs as the input")
    return result


def unique_choice(choice: JevChoice) -> str | None:
    """The response's choice, or ``None`` when several schemas share the top probability."""
    top = max(choice.probabilities.values())
    tied = sum(value == top for value in choice.probabilities.values()) > 1
    return None if tied else choice.choice


def verify_jev_run(report: dict[str, Any], inputs: list[SchemaExperimentInput]) -> VerifiedJevRun:
    """Rebuild every request from ``inputs`` and check it against the recorded report."""
    ids = {item.case_id for item in inputs}
    if not inputs or len(ids) != len(inputs):
        raise EvaluationError("Recorded runs require nonempty inputs with unique case IDs")
    policy = report.get("decision_policy")
    if policy not in (None, STAGED_POLICY_VERSION):
        raise EvaluationError("Unknown recorded staged decision policy")
    revision = report["catalogue_revision"]
    spec_version = report["spec_version"]
    rows = index_case_rows(report["rows"], ids)
    cases: dict[str, VerifiedJevCase] = {}
    for item in inputs:
        row = rows[item.case_id]
        baseline = SchemaRankingPrediction.model_validate(row["baseline"])
        if baseline.request_id != item.case_id:
            raise EvaluationError("Baseline request identity does not match its case")
        request = build_jev_request(
            item.source,
            context=report["context"],
            nouls=report["nouls"],
            model=report["model"],
            spec_version=spec_version,
        )
        digest = request_hash(request, revision, spec_version=spec_version)
        if digest != row["request_hash"]:
            raise EvaluationError(f"Run evidence does not match input for {item.case_id}")
        if row.get("expected_schema") != item.expected_schema:
            raise EvaluationError("Recorded run and input expected labels differ")
        if row.get("label_source") != item.label_source:
            raise EvaluationError("Recorded run and input label provenance differ")
        answer = None
        if row["status"] == "completed":
            answer = _verify_answer(
                row, request, digest, revision, spec_version, staged=bool(policy)
            )
        cases[item.case_id] = VerifiedJevCase(
            case_id=item.case_id,
            request=request,
            request_hash=digest,
            baseline=baseline,
            status=row["status"],
            answer=answer,
            elapsed_seconds=row.get("elapsed_seconds"),
        )
    return VerifiedJevRun(
        catalogue_revision=revision,
        model=report["model"],
        context=report["context"],
        spec_version=spec_version,
        decision_policy=policy,
        cases=cases,
    )


def _verify_answer(
    row: dict[str, Any],
    request: JevRequest,
    digest: str,
    revision: str,
    spec_version: DecisionSpec,
    *,
    staged: bool,
) -> VerifiedJevAnswer:
    response = JevResponse.model_validate(row["jev"]["response"])
    probes = response
    first_choice = None
    final_hash = digest
    if staged:
        probes = JevResponse.model_validate(row["probe_stage"]["response"])
        probes.validate_for(request)
        followup = build_jev_followup_request(request, probes)
        final_hash = request_hash(followup, revision, spec_version=spec_version)
        if row["final_request_hash"] != final_hash:
            raise EvaluationError("Staged final request identity does not match probes")
        response.validate_for(followup)
        first_choice = unique_choice(probes.schema_choice)
    else:
        response.validate_for(request)
    selected = row["jev"]["selected_schema"]
    if selected != unique_choice(response.schema_choice):
        raise EvaluationError("Recorded selection disagrees with the response or its tied maximum")
    return VerifiedJevAnswer(
        response=response,
        probes=probes,
        selected_schema=selected,
        first_choice=first_choice,
        final_request_hash=final_hash,
    )
