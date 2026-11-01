"""Frozen assisted-review records, reviewer drafts, and their revision fingerprints."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator

from ..models import (
    AsimSchema,
    FieldMapping,
    MappingReviewStatus,
    ReviewStatus,
    StrictModel,
    TimeGeneratedMode,
    Transform,
)
from ..reference.evidence import ReferenceReviewEvidence
from ..schema_ranking.review import RecordedSchemaSuggestion
from ..semantic_annotation.contracts import SemanticAnnotationTask
from ..semantic_mapping.contracts import SemanticMappingPrediction


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class MappingReviewSetup(StrictModel):
    """Target versions and ingestion assumptions supplied before human mapping review."""

    schema_versions: dict[
        AsimSchema, Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]
    ] = Field(min_length=1)
    time_generated: TimeGeneratedMode = "map"
    mapping_defaults: list[FieldMapping] | None = None
    supplied_mappings: list[FieldMapping] | None = None

    @model_validator(mode="after")
    def mappings_are_template_independent(self) -> MappingReviewSetup:
        defaults = (self.mapping_defaults or []) + (self.supplied_mappings or [])
        if any(mapping.slot_id is not None for mapping in defaults):
            raise ValueError("Setup mappings cannot refer to template-specific slots")
        targets = [mapping.asim_field.casefold() for mapping in defaults]
        if len(targets) != len(set(targets)):
            raise ValueError("Setup mappings must have unique ASIM target fields across both lists")
        return self


def _setup_payload(setup: MappingReviewSetup) -> dict[str, object]:
    # Omit absent optional lists to preserve revisions of earlier frozen tasks.
    return setup.model_dump(
        mode="json",
        exclude={
            name
            for name in ("mapping_defaults", "supplied_mappings")
            if getattr(setup, name) is None
        },
    )


def load_mapping_setup(path: Path) -> MappingReviewSetup:
    return MappingReviewSetup.model_validate_json(path.read_bytes())


class MappingReviewTask(StrictModel):
    mode: Literal["assisted-engineering"] = "assisted-engineering"
    source_task: SemanticAnnotationTask
    prediction: SemanticMappingPrediction
    schema_predictions: dict[AsimSchema, SemanticMappingPrediction] = Field(default_factory=dict)
    catalogue_sha256: str
    reference: ReferenceReviewEvidence | None = None
    cluster_notes: str = ""
    setup: MappingReviewSetup | None = None
    schema_suggestion: RecordedSchemaSuggestion | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    review_revision: str

    @model_validator(mode="after")
    def revision_matches(self) -> MappingReviewTask:
        excluded = {"review_revision"}
        if self.setup is None:
            excluded.add("setup")  # Keep already prepared bundles readable.
        if not self.schema_predictions:
            excluded.add("schema_predictions")  # Keep earlier frozen bundles readable.
        if self.schema_suggestion is None:
            excluded.add("schema_suggestion")
        payload = self.model_dump(mode="json", exclude=excluded)
        if self.setup is not None:
            payload["setup"] = _setup_payload(self.setup)
        if self.review_revision != _fingerprint(payload):
            raise ValueError(
                "Mapping review revision does not match the frozen evidence and suggestion"
            )
        if (
            self.prediction.case_id != self.source_task.case_id
            or self.prediction.catalogue_revision != self.source_task.catalogue_revision
        ):
            raise ValueError("Suggestion and source task must identify the same case and catalogue")
        if self.prediction.ranked_schemas:
            top_schema = self.prediction.ranked_schemas[0].schema_name
            if (
                top_schema in self.schema_predictions
                and self.schema_predictions[top_schema] != self.prediction
            ):
                raise ValueError("Top-ranked field suggestions must match the original prediction")
        for schema, prediction in self.schema_predictions.items():
            if self.setup is not None and schema not in self.setup.schema_versions:
                raise ValueError("Field suggestions require a schema available in setup")
            if (
                prediction.case_id != self.source_task.case_id
                or prediction.catalogue_revision != self.source_task.catalogue_revision
                or prediction.approach != self.prediction.approach
            ):
                raise ValueError(
                    "Schema field suggestions must match the frozen source and approach"
                )
        return self


class SourceSpan(StrictModel):
    """Reviewer-selected offsets within one frozen representative event."""

    example_index: int = Field(ge=0)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1)


class MappingRow(StrictModel):
    source_kind: Literal["slot", "source_field", "constant", "output_field"] = "slot"
    locator: str = ""
    role: str = ""
    asim_field: str = ""
    transform: Transform = "string"
    constant_value: str = ""
    source_span: SourceSpan | None = None


class MappingReviewDraft(StrictModel):
    format_version: Literal["1"] = "1"
    mode: Literal["assisted-engineering"] = "assisted-engineering"
    review_revision: str
    status: MappingReviewStatus = "in_progress"
    # The frozen queue contains approved clusters. A later engineering review
    # may reopen that decision without mutating the original review snapshot.
    cluster_status: ReviewStatus = "approved"
    cluster_notes: str = ""
    schema_name: str = ""
    schema_confirmed: bool = False
    parser_name: str = ""
    vendor: str = ""
    product: str = ""
    source_table: str = "Syslog"
    message_field: str = "SyslogMessage"
    rows: list[MappingRow] = Field(default_factory=list)
    # Inactive schema drafts are retained for switching back; only rows compile.
    schema_rows: dict[AsimSchema, list[MappingRow]] = Field(default_factory=dict)
    notes: str = ""


class MappingReviewManifest(StrictModel):
    format_version: Literal["1"] = "1"
    mode: Literal["assisted-engineering"] = "assisted-engineering"
    task_count: int = Field(ge=1)
    files: dict[str, str]
