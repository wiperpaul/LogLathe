"""Combine lexical, character, type, and value signals into one ranked mapping.

This is the N3 rung of the approach ladder. It differs from `direct-lexical` in two
ways that the controlled track showed matter: candidates are generated from the
whole schema rather than only from fields sharing a token with the slot label, and
a candidate whose catalogue type cannot hold the profiled value is vetoed outright.
"""

from __future__ import annotations

from ....models import AsimCatalog, AsimCatalogField
from ...contracts import (
    ApproachIdentity,
    MappingRequest,
    PredictedAsimField,
    RankedFieldCandidate,
    SemanticMappingPrediction,
)
from ...field_ranking import generate_candidates
from ...match_signals import (
    MatchSignals,
    character_ngram_similarity,
    range_plausibility,
    type_affinity,
    type_compatibility,
    value_enumeration,
)
from ...profiling import SlotProfile, profile_slot
from ...schema_candidates import rank_schemas
from ...source_context import slot_context

# Untuned by design: no train partition exists yet, so these are declared constants
# rather than fitted weights. They must not be tuned on the evaluation families.
_WEIGHTS: dict[str, float] = {
    "lexical": 0.45,
    "character_ngram": 0.15,
    "value_enumeration": 0.20,
    "type_affinity": 0.20,
}

# A candidate must clear this to be offered at all, which is what stops an unmapped
# slot of a familiar physical type from silently acquiring a target.
_ACCEPTANCE_FLOOR = 0.20
_CANDIDATE_LIMIT = 5


class MatcherEnsembleApproach:
    """Rank catalogue fields by several independently inspectable signals."""

    identity = ApproachIdentity(name="matcher-ensemble", version="1")

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

        fields = [
            field
            for field in catalog.fields_for_schema(request.review_schema or schemas[0].schema_name)
            if field.field_class != "Alias"
        ]
        mappings: list[PredictedAsimField] = []
        for slot in request.input.parameter_slots:
            profile = profile_slot(slot)
            context = slot_context(request.input, slot)
            signals = _score_fields(fields, context, slot.examples, profile)
            accepted = [signal for signal in signals if signal.combined >= _ACCEPTANCE_FLOOR]
            if not accepted:
                continue
            candidates = _to_candidates(accepted[:_CANDIDATE_LIMIT])
            mappings.append(
                PredictedAsimField(
                    source_kind="slot",
                    locator=slot.slot_id,
                    asim_field=candidates[0].asim_field,
                    score=candidates[0].score,
                    ranked_candidates=candidates,
                    candidate_pool_size=len(accepted),
                    considered_field_count=len(fields),
                    evidence=[
                        f"profile: {profile.physical_type} "
                        f"(confidence {profile.type_confidence:.2f})",
                        f"local slot context: {context}",
                        *accepted[0].evidence,
                    ],
                )
            )

        disposition = "mapped" if mappings else "unresolved"
        warnings = [] if mappings else ["No candidate cleared the ensemble acceptance floor."]
        return SemanticMappingPrediction(
            case_id=request.case_id,
            catalogue_revision=request.catalogue_revision,
            approach=self.identity,
            disposition=disposition,
            ranked_schemas=schemas,
            asim_fields=mappings,
            warnings=warnings,
        )


def _score_fields(
    fields: list[AsimCatalogField],
    context: str,
    values: list[str],
    profile: SlotProfile,
) -> list[MatchSignals]:
    """Score every schema field, vetoing those the value could not populate."""
    lexical_scores = {field.name: score for field, score, _ in generate_candidates(fields, context)}
    maximum = max(lexical_scores.values(), default=0.0)

    scored: list[MatchSignals] = []
    for field in fields:
        type_score = type_compatibility(profile, field)
        range_score = range_plausibility(profile, field)
        if type_score == 0.0 or range_score == 0.0:
            continue
        lexical = (lexical_scores.get(field.name, 0.0) / maximum) if maximum else 0.0
        ngram = character_ngram_similarity(context, field.name)
        enumeration = value_enumeration(values, field)
        affinity = type_affinity(profile, field)
        base = (
            (_WEIGHTS["lexical"] * lexical)
            + (_WEIGHTS["character_ngram"] * ngram)
            + (_WEIGHTS["value_enumeration"] * enumeration)
            + (_WEIGHTS["type_affinity"] * affinity)
        )
        # Type evidence scales the result rather than adding to it, so a confident
        # type match cannot by itself invent a mapping with no lexical support.
        combined = base * (0.5 + (0.5 * type_score))
        scored.append(
            MatchSignals(
                asim_field=field.name,
                lexical=round(lexical, 6),
                character_ngram=round(ngram, 6),
                type_compatibility=type_score,
                type_affinity=round(affinity, 6),
                value_enumeration=round(enumeration, 6),
                range_plausibility=range_score,
                combined=round(min(combined, 1.0), 6),
                evidence=_signal_evidence(lexical, ngram, enumeration, affinity, profile),
            )
        )
    scored.sort(key=lambda signal: (-signal.combined, signal.asim_field))
    return scored


def _signal_evidence(
    lexical: float,
    ngram: float,
    enumeration: float,
    affinity: float,
    profile: SlotProfile,
) -> list[str]:
    evidence: list[str] = []
    if lexical:
        evidence.append(f"lexical overlap {lexical:.2f}")
    if ngram:
        evidence.append(f"character n-gram {ngram:.2f}")
    if enumeration:
        evidence.append(f"value enumeration match {enumeration:.2f}")
    if affinity:
        evidence.append(f"{profile.physical_type} value shape affinity {affinity:.2f}")
    return evidence


def _to_candidates(signals: list[MatchSignals]) -> list[RankedFieldCandidate]:
    maximum = signals[0].combined or 1.0
    ties = {signal.combined: 0 for signal in signals}
    for signal in signals:
        ties[signal.combined] += 1
    return [
        RankedFieldCandidate(
            asim_field=signal.asim_field,
            score=round(signal.combined / maximum, 6),
            tied_with=ties[signal.combined] - 1,
            evidence=signal.evidence,
        )
        for signal in signals
    ]
