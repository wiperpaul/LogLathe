"""Import recorded schema advice into engineering review without provider calls."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator

from ..models import StrictModel
from ..reviews import ReviewError
from .dashboard import dashboard_data
from .jev import SCHEMA_DEFINITIONS, UNSUPPORTED, DecisionSpec, Probability, QuestionEntry
from .jev_experiment import SchemaExperimentInput

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class RecordedSchemaSuggestion(StrictModel):
    """Validated, immutable advice; its identity participates in the review revision."""

    provider: Literal["Jev"] = "Jev"
    model: str = Field(pattern=r"^jev-\d+\.\d+\.\d+$")
    spec_version: DecisionSpec
    context: Literal["template", "enriched"]
    decision_policy: str | None = None
    report_sha256: Sha256
    request_hash: Sha256
    selected_schema: str | None
    probabilities: dict[str, Probability]
    confidence: Probability
    definitions: dict[str, QuestionEntry]

    @model_validator(mode="after")
    def selection_matches_distribution(self) -> RecordedSchemaSuggestion:
        if set(self.probabilities) != {*SCHEMA_DEFINITIONS, UNSUPPORTED}:
            raise ValueError("Recorded schema advice has unexpected choices")
        if not math.isclose(sum(self.probabilities.values()), 1, abs_tol=0.001):
            raise ValueError("Recorded schema probabilities must sum to one")
        top = max(self.probabilities.values())
        winners = [name for name, value in self.probabilities.items() if value == top]
        if self.selected_schema != (winners[0] if len(winners) == 1 else None):
            raise ValueError("Recorded schema selection must match its unique maximum")
        if self.definitions.keys() != self.probabilities.keys():
            raise ValueError("Recorded schema definitions must cover its choices")
        return self


def load_recorded_schema_suggestions(
    path: Path, inputs: list[SchemaExperimentInput], catalogue_revision: str
) -> dict[str, RecordedSchemaSuggestion]:
    """Reuse replay validation for source hashes, responses, ties and staged decisions.

    The report must cover the exact approved queue and contain unlabelled cases.
    Native references are never substituted for model suggestions.
    """
    try:
        if path.name != "report.json":
            raise ReviewError("Use the recorded schema-rank report.json file")
        content = path.read_bytes()
        report = json.loads(content)
        if report["catalogue_revision"] != catalogue_revision:
            raise ReviewError("Recorded schema report uses a different catalogue")
        if (
            report.get("format_version") != "1"
            or report.get("mode") not in ("live", "replay")
            or report.get("review_policy") != "advisory_only"
            or report.get("error") is not None
            or report.get("completed") != len(inputs)
            or any(row["status"] != "completed" for row in report["rows"])
        ):
            raise ReviewError("Schema review requires a complete recorded live or replay report")
        if any(row.get("expected_schema") or row.get("label_source") for row in report["rows"]):
            raise ReviewError("Use an unlabelled queue report for assisted schema review")
        validated = dashboard_data(inputs, [("Jev", path.parent)])
        if path.read_bytes() != content:
            raise ReviewError("Recorded schema report changed during import")
        rows = {row["case_id"]: row for row in report["rows"]}
        classifier = validated["classifiers"][1]
        return {
            case["id"]: RecordedSchemaSuggestion(
                model=report["model"],
                spec_version=report["spec_version"],
                context=report["context"],
                decision_policy=report.get("decision_policy"),
                report_sha256=hashlib.sha256(content).hexdigest(),
                request_hash=rows[case["id"]].get(
                    "final_request_hash", rows[case["id"]]["request_hash"]
                ),
                selected_schema=case["predictions"]["jev-0"]["selected"],
                probabilities=case["predictions"]["jev-0"]["scores"],
                confidence=case["predictions"]["jev-0"]["confidence"],
                definitions=classifier["criteria"],
            )
            for case in validated["cases"]
        }
    except (OSError, ValueError, KeyError, TypeError) as error:
        if isinstance(error, ReviewError):
            raise
        raise ReviewError(f"Cannot import recorded schema suggestions: {error}") from error
