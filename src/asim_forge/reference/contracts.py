"""Contracts for native parser regression evidence, separate from semantic gold."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue, model_validator

from ..models import StrictModel

KqlType = Literal["string", "int", "long", "real", "datetime", "bool", "dynamic"]


class Resource(StrictModel):
    file: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    source_path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReferenceFixture(StrictModel):
    format_version: Literal["1"] = "1"
    fixture_id: str
    track: Literal["asim-parser-silver"] = "asim-parser-silver"
    group_id: str
    source_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    catalogue_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    schema_name: str
    schema_version: str
    source_table: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    message_field: str
    vendor: str
    product: str
    csv_file: str
    parser_file: str
    helpers: list[str]
    columns: dict[str, KqlType]
    datetime_format: str
    resources: list[Resource]
    redistribution: Literal["content"]
    attribution: str
    mutation_suite: Literal["openssh-v1"]

    @model_validator(mode="after")
    def validate_resources(self) -> ReferenceFixture:
        names = [resource.file for resource in self.resources]
        if len(names) != len(set(names)):
            raise ValueError("Fixture resource filenames must be unique")
        if not {self.csv_file, self.parser_file, *self.helpers}.issubset(names):
            raise ValueError("All fixture inputs must be hash-pinned resources")
        if self.message_field not in self.columns:
            raise ValueError("Fixture message field must exist in source columns")
        return self


class ReferenceEvent(StrictModel):
    event_id: str
    group_id: str
    origin: Literal["public-sample", "controlled-variant"]
    source_row: int = Field(ge=1)
    parent_event_id: str | None = None
    mutation: str | None = None
    values: dict[str, JsonValue]


class QueryOutput(StrictModel):
    columns: dict[str, str]
    rows: list[dict[str, JsonValue]]

    @model_validator(mode="after")
    def row_columns_match(self) -> QueryOutput:
        if any(set(row) != set(self.columns) for row in self.rows):
            raise ValueError("Every output row must match the declared result columns")
        return self


class EventOutput(StrictModel):
    event_id: str
    query_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output: QueryOutput


class NativeCapture(StrictModel):
    format_version: Literal["1"] = "1"
    track: Literal["asim-parser-silver"] = "asim-parser-silver"
    fixture_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    events_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: Literal["reference", "candidate"]
    program_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution: Literal["native-kusto"] = "native-kusto"
    engine: str = Field(min_length=1)
    runtime_identity: str = Field(min_length=1)
    captured_at: str
    outputs: list[EventOutput] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_events(self) -> NativeCapture:
        ids = [item.event_id for item in self.outputs]
        if len(ids) != len(set(ids)):
            raise ValueError("Captured event IDs must be unique")
        return self


class PreparedBundle(StrictModel):
    format_version: Literal["1"] = "1"
    fixture_sha256: str
    events_sha256: str
    event_count: int
    public_sample_count: int
    controlled_variant_count: int
    cluster_count: int
    files: dict[str, str]


class Difference(StrictModel):
    event_id: str
    kind: Literal["row-count", "missing-field", "extra-field", "type", "value"]
    field: str | None = None
    expected: JsonValue = None
    actual: JsonValue = None


class ConformanceIssue(StrictModel):
    event_id: str
    field: str
    kind: Literal["missing-mandatory", "type", "enum", "logical-type"]
    detail: str


class OutputComparison(StrictModel):
    format_version: Literal["1"] = "1"
    track: Literal["asim-parser-silver"] = "asim-parser-silver"
    fixture_sha256: str
    reference_sha256: str
    candidate_sha256: str
    catalogue_revision: str
    slices: dict[str, dict[str, int]]
    differences: list[Difference]
    reference_issues: list[ConformanceIssue]
    candidate_issues: list[ConformanceIssue]
    warnings: list[str]
