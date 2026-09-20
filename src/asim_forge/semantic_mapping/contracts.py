"""Prediction contracts shared by semantic mapping approaches."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Protocol

from pydantic import Field, model_validator

from ..models import AsimCatalog, StrictModel
from .types import (
    AsimName,
    Identifier,
    Locator,
    MappingDisposition,
    NonBlankText,
    SemanticMappingInput,
    SemanticRole,
    SourceKind,
)


def _scores_are_descending(
    candidates: Sequence[RankedSchemaCandidate | RankedFieldCandidate],
) -> bool:
    return all(
        candidate.score >= next_candidate.score
        for candidate, next_candidate in zip(candidates, candidates[1:], strict=False)
    )


class SourceFrameHint(StrictModel):
    """One gold source role supplied by the harness oracle."""

    source_kind: SourceKind
    locator: Locator
    role: SemanticRole


class MappingRequest(StrictModel):
    """Provider input with no access to expected labels."""

    case_id: Identifier
    catalogue_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    input: SemanticMappingInput
    # A reviewer-selected target for field projection. Schema ranking still uses
    # the original evidence; unlike schema_hint this is not a harness oracle.
    review_schema: AsimName | None = None
    # Diagnostic oracle constraints set only by the evaluation harness, never in production.
    schema_hint: AsimName | None = None
    frame_hint: list[SourceFrameHint] | None = None


class ApproachIdentity(StrictModel):
    name: Identifier
    version: NonBlankText


class RankedSchemaCandidate(StrictModel):
    schema_name: AsimName
    score: float = Field(ge=0, le=1)
    evidence: list[str] = Field(default_factory=list)


class RankedFieldCandidate(StrictModel):
    asim_field: AsimName
    score: float = Field(ge=0, le=1)
    # Other generated candidates holding this identical score before the cut-off.
    tied_with: int = Field(default=0, ge=0)
    evidence: list[str] = Field(default_factory=list)


class PredictedSourceSemantic(StrictModel):
    source_kind: SourceKind
    locator: Locator
    role: SemanticRole
    score: float = Field(ge=0, le=1)
    evidence: list[str] = Field(default_factory=list)


class PredictedAsimField(StrictModel):
    source_kind: SourceKind
    locator: Locator
    asim_field: AsimName
    constant_value: str | int | float | bool | None = None
    score: float = Field(ge=0, le=1)
    ranked_candidates: list[RankedFieldCandidate] = Field(min_length=1)
    # Candidate generation sizes, kept so retrieval loss stays separable from ranking loss.
    candidate_pool_size: int | None = Field(default=None, ge=0)
    considered_field_count: int | None = Field(default=None, ge=0)
    evidence: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def selected_field_must_head_ranking(self) -> PredictedAsimField:
        if self.ranked_candidates[0].asim_field != self.asim_field:
            raise ValueError("selected ASIM field must be the first ranked candidate")
        if self.ranked_candidates[0].score != self.score:
            raise ValueError("selected ASIM field score must match the first ranked candidate")
        names = [candidate.asim_field for candidate in self.ranked_candidates]
        if len(names) != len(set(names)):
            raise ValueError("ranked ASIM field candidates must be unique")
        if not _scores_are_descending(self.ranked_candidates):
            raise ValueError("ranked ASIM field candidate scores must be descending")
        return self


class SemanticMappingPrediction(StrictModel):
    """Provider output stored separately from provider-neutral gold cases."""

    format_version: Literal["1"] = "1"
    case_id: Identifier
    catalogue_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    approach: ApproachIdentity
    disposition: MappingDisposition
    ranked_schemas: list[RankedSchemaCandidate] = Field(default_factory=list)
    source_semantics: list[PredictedSourceSemantic] = Field(default_factory=list)
    asim_fields: list[PredictedAsimField] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def prediction_must_be_consistent(self) -> SemanticMappingPrediction:
        schema_names = [candidate.schema_name for candidate in self.ranked_schemas]
        if len(schema_names) != len(set(schema_names)):
            raise ValueError("ranked schema candidates must be unique")
        if not _scores_are_descending(self.ranked_schemas):
            raise ValueError("ranked schema candidate scores must be descending")
        if self.disposition == "mapped":
            if not self.ranked_schemas:
                raise ValueError("mapped predictions require a schema candidate")
            if not self.asim_fields:
                raise ValueError("mapped predictions require ASIM fields")
        elif self.disposition == "not_applicable":
            if self.ranked_schemas or self.asim_fields:
                raise ValueError("not_applicable predictions cannot contain ASIM targets")

        semantic_keys = [
            (semantic.source_kind, semantic.locator.casefold(), semantic.role.casefold())
            for semantic in self.source_semantics
        ]
        if len(semantic_keys) != len(set(semantic_keys)):
            raise ValueError("predicted source semantics must be unique")
        field_keys = [
            (field.source_kind, field.locator.casefold(), field.asim_field)
            for field in self.asim_fields
        ]
        if len(field_keys) != len(set(field_keys)):
            raise ValueError("predicted source and ASIM field combinations must be unique")
        return self


class SemanticMappingApproach(Protocol):
    """Small boundary implemented independently by every approach."""

    identity: ApproachIdentity

    def predict(
        self,
        request: MappingRequest,
        catalog: AsimCatalog,
    ) -> SemanticMappingPrediction: ...
