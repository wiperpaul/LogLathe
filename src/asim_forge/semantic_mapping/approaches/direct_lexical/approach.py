"""Direct lexical slot-to-ASIM baseline."""

from __future__ import annotations

from ....models import AsimCatalog
from ...contracts import (
    ApproachIdentity,
    MappingRequest,
    PredictedAsimField,
    SemanticMappingPrediction,
)
from ...field_ranking import rank_field_candidates
from ...schema_candidates import rank_schemas
from ...source_context import slot_context


class DirectLexicalApproach:
    """Rank ASIM fields directly from local slot context."""

    identity = ApproachIdentity(name="direct-lexical", version="3")

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

        schema_name = request.review_schema or schemas[0].schema_name
        fields = catalog.fields_for_schema(schema_name)
        predictions: list[PredictedAsimField] = []
        for slot in request.input.parameter_slots:
            context = slot_context(request.input, slot)
            ranking = rank_field_candidates(fields, context)
            candidates = ranking.candidates
            if not candidates:
                continue
            predictions.append(
                PredictedAsimField(
                    source_kind="slot",
                    locator=slot.slot_id,
                    asim_field=candidates[0].asim_field,
                    score=candidates[0].score,
                    ranked_candidates=candidates,
                    candidate_pool_size=ranking.pool_size,
                    considered_field_count=ranking.considered_field_count,
                    evidence=[f"local slot context: {context}"],
                )
            )

        disposition = "mapped" if predictions else "unresolved"
        warnings = [] if predictions else ["No slot-to-field candidate had lexical overlap."]
        return SemanticMappingPrediction(
            case_id=request.case_id,
            catalogue_revision=request.catalogue_revision,
            approach=self.identity,
            disposition=disposition,
            ranked_schemas=schemas,
            asim_fields=predictions,
            warnings=warnings,
        )
