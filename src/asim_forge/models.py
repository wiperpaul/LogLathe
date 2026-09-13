"""Typed interchange records for the Milestone 1 workflow."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AsimSchema = Literal["Authentication", "NetworkSession", "AuditEvent"]
ReviewStatus = Literal["approved", "rejected", "needs_split", "insufficient_evidence"]
Transform = Literal["string", "int", "long", "real", "datetime", "bool"]
MappingReviewStatus = Literal["in_progress", "approved", "deferred", "needs_extraction"]
TimeGeneratedMode = Literal["map", "source", "ingestion"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceEvent(StrictModel):
    source_file: str
    line_number: int = Field(ge=1)
    text: str = Field(min_length=1)


class MaskDefinition(StrictModel):
    label: str
    pattern: str
    justification: str = ""


class ParameterSlot(StrictModel):
    slot_id: str = Field(pattern=r"^p[1-9][0-9]*$")
    label: str
    placeholder: str
    occurrence: int = Field(ge=1)
    examples: list[str] = Field(default_factory=list)


class SchemaScore(StrictModel):
    schema_name: Literal["Authentication", "NetworkSession", "AuditEvent", "NoFit"]
    score: int = Field(ge=0)
    evidence: list[str] = Field(default_factory=list)


class SchemaSuggestion(StrictModel):
    schema_name: Literal["Authentication", "NetworkSession", "AuditEvent", "NoFit"]
    confidence: float = Field(ge=0, le=1)
    ranked_scores: list[SchemaScore]
    method: Literal["keyword-baseline", "source-concept-v1"] = "keyword-baseline"


class ParsedCluster(StrictModel):
    cluster_id: str
    engine_cluster_id: int = Field(ge=1)
    template: str
    event_count: int = Field(ge=1)
    representative_events: list[SourceEvent]
    parameter_slots: list[ParameterSlot] = Field(default_factory=list)


class ClusterRecord(ParsedCluster):
    schema_suggestion: SchemaSuggestion


class ReviewTask(StrictModel):
    id: str
    text: str
    cluster_id: str
    template: str
    template_html: str
    event_count: int
    representative_events_table: dict[str, object]
    parameter_slots_table: dict[str, object]
    suggested_schema: str | None = None
    suggestion_confidence: float | None = Field(default=None, ge=0, le=1)
    parameter_slots: list[dict[str, object]]


class FieldMapping(StrictModel):
    slot_id: str | None = Field(default=None, pattern=r"^p[1-9][0-9]*$")
    source_field: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    constant_value: str | int | float | bool | None = None
    asim_field: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    transform: Transform = "string"

    @model_validator(mode="after")
    def exactly_one_source(self) -> FieldMapping:
        if (
            sum(
                value is not None
                for value in (self.slot_id, self.source_field, self.constant_value)
            )
            != 1
        ):
            raise ValueError("A mapping requires exactly one slot, source field, or constant")
        if isinstance(self.constant_value, float) and not math.isfinite(self.constant_value):
            raise ValueError("Mapping constants must be finite")
        return self


class MappingReviewProvenance(StrictModel):
    mode: Literal["assisted-engineering"] = "assisted-engineering"
    status: MappingReviewStatus
    task_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    cluster_file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    catalogue_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    review_file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    time_generated: TimeGeneratedMode = "map"


class ReviewDecision(StrictModel):
    cluster_id: str
    reviewer: str = Field(min_length=1)
    status: ReviewStatus
    schema_name: AsimSchema | None = None
    schema_version: str | None = Field(default=None, pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    parser_name: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    vendor: str | None = None
    product: str | None = None
    source_table: str = Field(default="Syslog", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    message_field: str = Field(default="SyslogMessage", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    field_mappings: list[FieldMapping] = Field(default_factory=list)
    notes: str = ""
    mapping_review: MappingReviewProvenance | None = None

    @field_validator("schema_name", "parser_name", "vendor", "product")
    @classmethod
    def approved_values_cannot_be_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("value cannot be blank")
        return value


class ParserSource(StrictModel):
    vendor: str
    product: str
    table: str
    message_field: str


class ParserSpecification(StrictModel):
    format_version: Literal["1"] = "1"
    parser_name: str
    cluster_id: str
    schema_name: AsimSchema
    schema_version: str | None = Field(default=None, pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    template: str
    source: ParserSource
    field_mappings: list[FieldMapping]
    reviewer: str
    review_notes: str = ""
    mapping_review: MappingReviewProvenance | None = None


class InputFile(StrictModel):
    path: str
    event_count: int = Field(ge=0)


class BuildManifest(StrictModel):
    format_version: Literal["1"] = "1"
    system: str
    engine: Literal["DeepParse"] = "DeepParse"
    engine_revision: str
    engine_mode: Literal["offline"] = "offline"
    input_root: str
    input_files: list[InputFile]
    event_count: int = Field(ge=1)
    cluster_count: int = Field(ge=1)
    masks: list[MaskDefinition]
    outputs: dict[str, str]


class CompileManifest(StrictModel):
    format_version: Literal["1"] = "1"
    cluster_count: int = Field(ge=1)
    review_count: int = Field(ge=1)
    compiled_count: int = Field(ge=0)
    skipped_reviews: dict[str, int]
    outputs: list[str]


AsimFieldClass = Literal["Mandatory", "Recommended", "Optional", "Conditional", "Alias"]


class AsimCatalogField(StrictModel):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    kql_type: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9]*$")
    field_class: AsimFieldClass
    schema_name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9]*$")
    logical_type: str | None = None
    allowed_values: list[str] = Field(default_factory=list)
    aliased_field: str | None = None
    dynamic_type: str | None = None
    array_values_type: str | None = None


class AsimCatalogManifest(StrictModel):
    format_version: Literal["1"] = "1"
    source_repository: str
    source_path: str
    requested_revision: str
    resolved_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_count: int = Field(ge=1)
    field_count: int = Field(ge=1)
    schemas: list[str] = Field(min_length=1)


class AsimCatalog(StrictModel):
    manifest: AsimCatalogManifest
    fields: list[AsimCatalogField]

    def fields_for_schema(self, schema_name: str) -> list[AsimCatalogField]:
        if schema_name not in self.manifest.schemas:
            raise ValueError(f"Unknown ASIM schema: {schema_name}")
        schema_fields = [field for field in self.fields if field.schema_name == schema_name]
        overridden_names = {field.name for field in schema_fields}
        common_fields = [
            field
            for field in self.fields
            if field.schema_name == "Common" and field.name not in overridden_names
        ]
        return [*common_fields, *schema_fields]
