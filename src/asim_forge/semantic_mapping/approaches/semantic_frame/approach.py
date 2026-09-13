"""Two-stage source-semantic-frame baseline."""

from __future__ import annotations

from ....models import AsimCatalog, ParameterSlot
from ....source_semantics import contains_source_phrase, source_tokens
from ...contracts import (
    ApproachIdentity,
    MappingRequest,
    PredictedAsimField,
    PredictedSourceSemantic,
    SemanticMappingPrediction,
)
from ...field_ranking import rank_field_candidates
from ...schema_candidates import rank_schemas
from ...source_context import slot_context
from ...types import SemanticMappingInput

_STATIC_SEMANTICS = (
    ("connection allowed", "network.connection.allowed", "event result", "Success"),
    ("connection denied", "network.connection.denied", "event result", "Failure"),
    ("login failed", "authentication.login.failed", "event result", "Failure"),
    ("login succeeded", "authentication.login.succeeded", "event result", "Success"),
)


class SemanticFrameApproach:
    """Infer source roles first, then project those roles into ASIM."""

    identity = ApproachIdentity(name="semantic-frame", version="3")

    def predict(
        self,
        request: MappingRequest,
        catalog: AsimCatalog,
    ) -> SemanticMappingPrediction:
        schemas = rank_schemas(request.input, catalog, schema_hint=request.schema_hint)
        if not schemas:
            return SemanticMappingPrediction(
                case_id=request.case_id,
                catalogue_revision=request.catalogue_revision,
                approach=self.identity,
                disposition="unresolved",
                warnings=["No schema had lexical evidence."],
            )

        fields = catalog.fields_for_schema(request.review_schema or schemas[0].schema_name)
        semantics: list[PredictedSourceSemantic] = []
        mappings: list[PredictedAsimField] = []
        oracle_roles = _oracle_roles(request)
        for slot in request.input.parameter_slots:
            context = slot_context(request.input, slot)
            role = oracle_roles.get(("slot", slot.slot_id))
            evidence = "harness source-frame oracle"
            if role is None:
                role = _infer_slot_role(request.input, slot, context)
                evidence = f"normalized slot context: {context}"
            if role is None:
                continue
            semantics.append(
                PredictedSourceSemantic(
                    source_kind="slot",
                    locator=slot.slot_id,
                    role=role,
                    score=1.0,
                    evidence=[evidence],
                )
            )
            candidates = rank_field_candidates(fields, f"{role} {slot.label}")
            if candidates.candidates:
                mappings.append(
                    PredictedAsimField(
                        source_kind="slot",
                        locator=slot.slot_id,
                        asim_field=candidates.candidates[0].asim_field,
                        score=candidates.candidates[0].score,
                        ranked_candidates=candidates.candidates,
                        candidate_pool_size=candidates.pool_size,
                        considered_field_count=candidates.considered_field_count,
                        evidence=[f"source role: {role}"],
                    )
                )

        for phrase, role, target_query, constant_value in _STATIC_SEMANTICS:
            if not contains_source_phrase(request.input.template, phrase):
                continue
            semantics.append(
                PredictedSourceSemantic(
                    source_kind="template_constant",
                    locator=phrase,
                    role=role,
                    score=1.0,
                    evidence=[f"template constant: {phrase}"],
                )
            )
            candidates = rank_field_candidates(fields, target_query)
            if candidates.candidates:
                mappings.append(
                    PredictedAsimField(
                        source_kind="template_constant",
                        locator=phrase,
                        asim_field=candidates.candidates[0].asim_field,
                        constant_value=constant_value,
                        score=candidates.candidates[0].score,
                        ranked_candidates=candidates.candidates,
                        candidate_pool_size=candidates.pool_size,
                        considered_field_count=candidates.considered_field_count,
                        evidence=[f"source role: {role}"],
                    )
                )

        disposition = "mapped" if mappings else "unresolved"
        warnings = [] if mappings else ["Source roles did not project to catalogue fields."]
        return SemanticMappingPrediction(
            case_id=request.case_id,
            catalogue_revision=request.catalogue_revision,
            approach=self.identity,
            disposition=disposition,
            ranked_schemas=schemas,
            source_semantics=semantics,
            asim_fields=mappings,
            warnings=warnings,
        )


def _oracle_roles(request: MappingRequest) -> dict[tuple[str, str], str]:
    """Gold roles supplied by the harness, isolating projection from inference."""
    if not request.frame_hint:
        return {}
    return {(hint.source_kind, hint.locator): hint.role for hint in request.frame_hint}


def _infer_slot_role(
    mapping_input: SemanticMappingInput,
    slot: ParameterSlot,
    context: str,
) -> str | None:
    terms = source_tokens(context)
    label_terms = source_tokens(slot.label)
    is_address = "ip" in label_terms or "address" in label_terms
    if "port" in terms:
        if "source" in terms:
            return "network.source.port"
        if "destination" in terms:
            return "network.destination.port"
        return None
    if is_address and "source" in terms:
        return "network.source.address"
    if is_address and "destination" in terms:
        return "network.destination.address"
    if "user" in terms:
        if source_tokens(mapping_input.template) & {"login", "logon", "authentication"}:
            return "authentication.target.user"
        return "event.actor.user"
    return None
