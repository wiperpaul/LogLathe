"""Provider-independent metrics for semantic mapping predictions."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from ..evaluation import SemanticMappingCase
from ..models import StrictModel
from ..source_semantics import FACET_NAMES, facet_keys, registered_role
from .contracts import SemanticMappingPrediction


class EvaluationError(ValueError):
    """Raised when predictions cannot be evaluated fairly."""


class EvaluationMetrics(StrictModel):
    case_count: int = Field(ge=1)
    disposition_accuracy: float = Field(ge=0, le=1)
    coverage: float = Field(ge=0, le=1)
    schema_top1_accuracy: float = Field(ge=0, le=1)
    schema_top3_hit_rate: float = Field(ge=0, le=1)
    schema_mrr: float = Field(ge=0, le=1)
    source_micro_precision: float = Field(ge=0, le=1)
    source_micro_recall: float = Field(ge=0, le=1)
    source_micro_f1: float = Field(ge=0, le=1)
    source_macro_f1: float = Field(ge=0, le=1)
    # Facet scores read a near miss as a near miss rather than a total failure.
    source_facet_micro_f1: float = Field(default=0.0, ge=0, le=1)
    source_domain_f1: float = Field(default=0.0, ge=0, le=1)
    source_relation_f1: float = Field(default=0.0, ge=0, le=1)
    source_entity_f1: float = Field(default=0.0, ge=0, le=1)
    source_property_f1: float = Field(default=0.0, ge=0, le=1)
    unregistered_role_rate: float = Field(default=0.0, ge=0, le=1)
    field_micro_precision: float = Field(ge=0, le=1)
    field_micro_recall: float = Field(ge=0, le=1)
    field_micro_f1: float = Field(ge=0, le=1)
    field_macro_f1: float = Field(ge=0, le=1)
    field_mrr: float = Field(ge=0, le=1)
    field_recall_at_gold: float = Field(ge=0, le=1)
    candidate_recall_at_1: float = Field(default=0.0, ge=0, le=1)
    candidate_recall_at_3: float = Field(default=0.0, ge=0, le=1)
    candidate_recall_at_5: float = Field(default=0.0, ge=0, le=1)
    candidate_reduction_ratio: float = Field(default=0.0, ge=0, le=1)
    field_top1_tie_rate: float = Field(default=0.0, ge=0, le=1)
    mapping_exact_match: float = Field(ge=0, le=1)
    full_exact_match: float = Field(ge=0, le=1)
    mean_mapping_edits: float = Field(ge=0)


def evaluate_predictions(
    cases: list[SemanticMappingCase],
    predictions: list[SemanticMappingPrediction],
) -> EvaluationMetrics:
    if not cases or len(cases) != len(predictions):
        raise EvaluationError("Cases and predictions must be non-empty and have equal length")
    predictions_by_id = {prediction.case_id: prediction for prediction in predictions}
    if len(predictions_by_id) != len(predictions):
        raise EvaluationError("Prediction case IDs must be unique")
    if set(predictions_by_id) != {case.case_id for case in cases}:
        raise EvaluationError("Predictions must cover exactly the evaluated case IDs")
    approaches = {
        (prediction.approach.name, prediction.approach.version) for prediction in predictions
    }
    if len(approaches) != 1:
        raise EvaluationError("Predictions must come from one approach name and version")
    for case in cases:
        prediction = predictions_by_id[case.case_id]
        if prediction.catalogue_revision != case.catalogue_revision:
            raise EvaluationError(
                f"Prediction {case.case_id!r} uses catalogue revision "
                f"{prediction.catalogue_revision!r}, expected {case.catalogue_revision!r}"
            )

    disposition_correct = 0
    mapped_predictions = 0
    schema_top1 = 0
    schema_top3 = 0
    schema_rr: list[float] = []
    source_tp = source_fp = source_fn = 0
    facet_counts: dict[str, list[int]] = {name: [0, 0, 0] for name in FACET_NAMES}
    unregistered_roles = 0
    predicted_role_count = 0
    field_tp = field_fp = field_fn = 0
    source_case_f1: list[float] = []
    field_case_f1: list[float] = []
    field_rr: list[float] = []
    field_recall_at_gold: list[float] = []
    candidate_hits = {1: 0, 3: 0, 5: 0}
    candidate_total = 0
    reduction_ratios: list[float] = []
    tied_top_fields = 0
    predicted_field_count = 0
    mapping_exact = full_exact = 0
    mapping_edits: list[int] = []
    mapped_gold_count = 0

    for case in cases:
        prediction = predictions_by_id[case.case_id]
        disposition_correct += prediction.disposition == case.expected.disposition
        mapped_predictions += prediction.disposition == "mapped"

        for predicted_field in prediction.asim_fields:
            predicted_field_count += 1
            tied_top_fields += predicted_field.ranked_candidates[0].tied_with > 0
            if predicted_field.candidate_pool_size is None:
                continue
            if not predicted_field.considered_field_count:
                continue
            reduction_ratios.append(
                1 - (predicted_field.candidate_pool_size / predicted_field.considered_field_count)
            )

        expected_sources = _expected_source_set(case)
        predicted_sources = _predicted_source_set(prediction)
        tp, fp, fn, f1 = set_scores(expected_sources, predicted_sources)
        source_tp += tp
        source_fp += fp
        source_fn += fn
        source_case_f1.append(f1)

        for facet, (tp, fp, fn) in _facet_scores(expected_sources, predicted_sources).items():
            facet_counts[facet][0] += tp
            facet_counts[facet][1] += fp
            facet_counts[facet][2] += fn
        for semantic in prediction.source_semantics:
            predicted_role_count += 1
            unregistered_roles += registered_role(semantic.role) is None

        expected_fields = expected_field_set(case)
        predicted_fields = predicted_field_set(prediction)
        tp, fp, fn, f1 = set_scores(expected_fields, predicted_fields)
        field_tp += tp
        field_fp += fp
        field_fn += fn
        field_case_f1.append(f1)
        field_recall_at_gold.append(_field_recall_at_ground_truth(case, prediction))

        expected_schema = case.expected.schema_name
        predicted_schema = (
            prediction.ranked_schemas[0].schema_name if prediction.ranked_schemas else None
        )
        schema_edit = int(expected_schema != predicted_schema)
        mapping_edits.append(schema_edit + len(expected_fields ^ predicted_fields))

        if case.expected.disposition != "mapped" or expected_schema is None:
            fields_exact = (
                expected_schema == predicted_schema and expected_fields == predicted_fields
            )
            mapping_exact += prediction.disposition == case.expected.disposition and fields_exact
            full_exact += (
                prediction.disposition == case.expected.disposition
                and fields_exact
                and predicted_sources == expected_sources
            )
            continue

        mapped_gold_count += 1
        ranked_schema_names = [candidate.schema_name for candidate in prediction.ranked_schemas]
        schema_top1 += bool(ranked_schema_names and ranked_schema_names[0] == expected_schema)
        schema_top3 += expected_schema in ranked_schema_names[:3]
        schema_rr.append(_reciprocal_rank(expected_schema, ranked_schema_names))
        field_rr.extend(_field_reciprocal_ranks(case, prediction))
        for cutoff, hits in _candidate_hits(case, prediction).items():
            candidate_hits[cutoff] += hits
        candidate_total += len(case.expected.asim_fields)

        fields_exact = expected_schema == predicted_schema and expected_fields == predicted_fields
        mapping_exact += fields_exact
        full_exact += fields_exact and expected_sources == predicted_sources

    source_precision, source_recall, source_f1 = _micro_scores(source_tp, source_fp, source_fn)
    field_precision, field_recall, field_f1 = _micro_scores(field_tp, field_fp, field_fn)
    facet_f1 = {facet: _micro_scores(*counts)[2] for facet, counts in facet_counts.items()}
    schema_divisor = mapped_gold_count or 1
    return EvaluationMetrics(
        case_count=len(cases),
        disposition_accuracy=round(disposition_correct / len(cases), 6),
        coverage=round(mapped_predictions / len(cases), 6),
        schema_top1_accuracy=round(schema_top1 / schema_divisor, 6),
        schema_top3_hit_rate=round(schema_top3 / schema_divisor, 6),
        schema_mrr=_mean(schema_rr),
        source_micro_precision=source_precision,
        source_micro_recall=source_recall,
        source_micro_f1=source_f1,
        source_macro_f1=_mean(source_case_f1),
        source_facet_micro_f1=round(sum(facet_f1.values()) / len(facet_f1), 6),
        source_domain_f1=facet_f1["domain"],
        source_relation_f1=facet_f1["relation"],
        source_entity_f1=facet_f1["entity"],
        source_property_f1=facet_f1["property"],
        unregistered_role_rate=_ratio(unregistered_roles, predicted_role_count),
        field_micro_precision=field_precision,
        field_micro_recall=field_recall,
        field_micro_f1=field_f1,
        field_macro_f1=_mean(field_case_f1),
        field_mrr=_mean(field_rr),
        field_recall_at_gold=_mean(field_recall_at_gold),
        candidate_recall_at_1=_ratio(candidate_hits[1], candidate_total),
        candidate_recall_at_3=_ratio(candidate_hits[3], candidate_total),
        candidate_recall_at_5=_ratio(candidate_hits[5], candidate_total),
        candidate_reduction_ratio=_mean(reduction_ratios),
        field_top1_tie_rate=_ratio(tied_top_fields, predicted_field_count),
        mapping_exact_match=round(mapping_exact / len(cases), 6),
        full_exact_match=round(full_exact / len(cases), 6),
        mean_mapping_edits=round(sum(mapping_edits) / len(cases), 6),
    )


def _facet_scores(
    expected: set[tuple[str, str, str]],
    predicted: set[tuple[str, str, str]],
) -> dict[str, tuple[int, int, int]]:
    """Score each facet on (kind, locator) pairs so one wrong facet costs one facet."""
    scores: dict[str, tuple[int, int, int]] = {}
    for facet in FACET_NAMES:
        expected_facet = {
            (kind, locator, facet_keys(role)[facet]) for kind, locator, role in expected
        }
        predicted_facet = {
            (kind, locator, facet_keys(role)[facet]) for kind, locator, role in predicted
        }
        tp, fp, fn, _ = set_scores(expected_facet, predicted_facet)
        scores[facet] = (tp, fp, fn)
    return scores


def _expected_source_set(case: SemanticMappingCase) -> set[tuple[str, str, str]]:
    return {
        (semantic.source_kind, semantic.locator.casefold(), semantic.role.casefold())
        for semantic in case.expected.source_semantics
    }


def _predicted_source_set(
    prediction: SemanticMappingPrediction,
) -> set[tuple[str, str, str]]:
    return {
        (semantic.source_kind, semantic.locator.casefold(), semantic.role.casefold())
        for semantic in prediction.source_semantics
    }


def expected_field_set(case: SemanticMappingCase) -> set[tuple[str, str, str, Any]]:
    semantics = {semantic.semantic_id: semantic for semantic in case.expected.source_semantics}
    return {
        (
            semantics[field.semantic_id].source_kind,
            semantics[field.semantic_id].locator.casefold(),
            field.asim_field,
            field.constant_value,
        )
        for field in case.expected.asim_fields
    }


def predicted_field_set(
    prediction: SemanticMappingPrediction,
) -> set[tuple[str, str, str, Any]]:
    return {
        (
            field.source_kind,
            field.locator.casefold(),
            field.asim_field,
            field.constant_value,
        )
        for field in prediction.asim_fields
    }


def _field_reciprocal_ranks(
    case: SemanticMappingCase,
    prediction: SemanticMappingPrediction,
) -> list[float]:
    semantics = {semantic.semantic_id: semantic for semantic in case.expected.source_semantics}
    retained = _ranked_fields_by_source(prediction)
    ranks: list[float] = []
    for expected in case.expected.asim_fields:
        source = semantics[expected.semantic_id]
        names = retained.get((source.source_kind, source.locator.casefold()), [])
        ranks.append(_reciprocal_rank(expected.asim_field, names))
    return ranks


def _ranked_fields_by_source(
    prediction: SemanticMappingPrediction,
) -> dict[tuple[str, str], list[str]]:
    scores: dict[tuple[str, str], dict[str, float]] = {}
    for field in prediction.asim_fields:
        candidates = scores.setdefault((field.source_kind, field.locator.casefold()), {})
        for candidate in field.ranked_candidates:
            candidates[candidate.asim_field] = max(
                candidates.get(candidate.asim_field, 0.0), candidate.score
            )
    return {
        source: sorted(candidates, key=lambda name: (-candidates[name], name))
        for source, candidates in scores.items()
    }


def _candidate_hits(
    case: SemanticMappingCase,
    prediction: SemanticMappingPrediction,
) -> dict[int, int]:
    """Count expected fields retained by candidate generation at each cut-off."""
    semantics = {semantic.semantic_id: semantic for semantic in case.expected.source_semantics}
    retained = _ranked_fields_by_source(prediction)
    hits = {1: 0, 3: 0, 5: 0}
    for expected in case.expected.asim_fields:
        source = semantics[expected.semantic_id]
        candidates = retained.get((source.source_kind, source.locator.casefold()), [])
        for cutoff in hits:
            hits[cutoff] += expected.asim_field in candidates[:cutoff]
    return hits


def _field_recall_at_ground_truth(
    case: SemanticMappingCase,
    prediction: SemanticMappingPrediction,
) -> float:
    semantics = {semantic.semantic_id: semantic for semantic in case.expected.source_semantics}
    expected = {
        (
            semantics[field.semantic_id].source_kind,
            semantics[field.semantic_id].locator.casefold(),
            field.asim_field,
        )
        for field in case.expected.asim_fields
    }
    if not expected:
        return 1.0 if not prediction.asim_fields else 0.0

    scores: dict[tuple[str, str, str], float] = {}
    for field in prediction.asim_fields:
        for candidate in field.ranked_candidates:
            key = (field.source_kind, field.locator.casefold(), candidate.asim_field)
            scores[key] = max(scores.get(key, 0.0), candidate.score)
    ranked = sorted(scores, key=lambda key: (-scores[key], key))
    top_at_gold = set(ranked[: len(expected)])
    return round(len(expected & top_at_gold) / len(expected), 6)


def set_scores(expected: set[Any], predicted: set[Any]) -> tuple[int, int, int, float]:
    if not expected and not predicted:
        return 0, 0, 0, 1.0
    tp = len(expected & predicted)
    fp = len(predicted - expected)
    fn = len(expected - predicted)
    _, _, f1 = _micro_scores(tp, fp, fn)
    return tp, fp, fn, f1


def _micro_scores(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return round(precision, 6), round(recall, 6), round(f1, 6)


def _reciprocal_rank(expected: str, ranked: list[str]) -> float:
    try:
        return 1 / (ranked.index(expected) + 1)
    except ValueError:
        return 0.0


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 6) if values else 0.0


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0
