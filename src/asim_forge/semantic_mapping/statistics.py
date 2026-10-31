"""Uncertainty, significance, and selective-risk reporting for small case sets.

Semantic mapping is evaluated on tens of cases drawn from a handful of source
families, so a bare point estimate invites conclusions the sample cannot support.
Everything here resamples or permutes whole groups rather than individual cases,
because cases from one source family are not independent observations.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterable, Sequence
from itertools import groupby

from pydantic import Field

from ..evaluation import SemanticMappingCase
from ..evaluation_splits import SemanticDatasetSplit
from ..models import StrictModel
from .contracts import SemanticMappingPrediction
from .metrics import (
    EvaluationError,
    evaluate_predictions,
    expected_field_set,
    predicted_field_set,
    set_scores,
)

DEFAULT_RESAMPLES = 1000
DEFAULT_CONFIDENCE_LEVEL = 0.95
DEFAULT_SEED = 20260902

# Metrics carried in the headline report. Others remain available on EvaluationMetrics.
INTERVAL_METRICS = (
    "schema_top1_accuracy",
    "field_micro_f1",
    "field_macro_f1",
    "candidate_recall_at_5",
    "source_micro_f1",
    "mapping_exact_match",
)

# Worst-case standard deviation of a metric bounded to [0, 1].
_MAX_BOUNDED_SD = 0.5
# Normal approximation for a two-sided 5% test at 80% power.
_POWER_CONSTANT = 2.8


class BootstrapInterval(StrictModel):
    """A percentile interval from resampling whole source families."""

    metric: str
    point: float
    lower: float
    upper: float
    confidence_level: float = Field(gt=0, lt=1)
    resamples: int = Field(ge=1)
    group_count: int = Field(ge=1)


class PairedApproachTest(StrictModel):
    """A paired permutation test between two approaches on identical cases."""

    metric: str
    baseline: str
    candidate: str
    baseline_value: float
    candidate_value: float
    difference: float
    p_value: float = Field(gt=0, le=1)
    resamples: int = Field(ge=1)
    group_count: int = Field(ge=1)
    minimum_detectable_effect: float = Field(ge=0)
    significant: bool


class RiskCoveragePoint(StrictModel):
    coverage: float = Field(ge=0, le=1)
    risk: float = Field(ge=0, le=1)
    confidence_threshold: float = Field(ge=0, le=1)


class RiskCoverageCurve(StrictModel):
    """Selective risk as the approach is allowed to abstain.

    Ranking quality only. Scores are normalized lexical overlap, not calibrated
    probabilities, so this must not be read as a calibration result.
    """

    points: list[RiskCoveragePoint] = Field(min_length=1)
    area_under_curve: float = Field(ge=0, le=1)
    risk_at_full_coverage: float = Field(ge=0, le=1)


class SampleAdequacy(StrictModel):
    """What the sample can decide, stated before any comparison is read."""

    case_count: int = Field(ge=1)
    group_count: int = Field(ge=1)
    grouping: str
    minimum_detectable_effect: float = Field(ge=0)


def resolve_group_keys(
    cases: Sequence[SemanticMappingCase],
    split: SemanticDatasetSplit | None = None,
) -> dict[str, str]:
    """Map each case to the unit that resampling and permutation must move together.

    The pre-label split group is authoritative because it was frozen before any
    annotator saw a label. Source metadata is a fallback, not an equivalent.
    """
    if split is not None:
        frozen = {entry.case_id: entry.group_id for entry in split.entries}
        missing = sorted({case.case_id for case in cases} - set(frozen))
        if missing:
            raise EvaluationError(f"Split does not assign a group to cases: {missing}")
        return {case.case_id: frozen[case.case_id] for case in cases}
    return {case.case_id: _source_family(case) for case in cases}


def describe_sample(
    cases: Sequence[SemanticMappingCase],
    split: SemanticDatasetSplit | None = None,
) -> SampleAdequacy:
    """State the resolution of the sample before any difference is interpreted."""
    if not cases:
        raise EvaluationError("At least one case is required to describe a sample")
    group_keys = resolve_group_keys(cases, split)
    groups = sorted(set(group_keys.values()))
    return SampleAdequacy(
        case_count=len(cases),
        group_count=len(groups),
        grouping="split-group" if split is not None else "source-family",
        minimum_detectable_effect=_worst_case_mde(len(groups)),
    )


def bootstrap_intervals(
    cases: Sequence[SemanticMappingCase],
    predictions: Sequence[SemanticMappingPrediction],
    *,
    split: SemanticDatasetSplit | None = None,
    metrics: Sequence[str] = INTERVAL_METRICS,
    resamples: int = DEFAULT_RESAMPLES,
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL,
    seed: int = DEFAULT_SEED,
) -> list[BootstrapInterval]:
    """Percentile intervals from a cluster bootstrap over source families."""
    group_keys = resolve_group_keys(cases, split)
    grouped = _group_cases(cases, predictions, group_keys)
    point = evaluate_predictions(list(cases), list(predictions))
    unknown = sorted(set(metrics) - set(type(point).model_fields))
    if unknown:
        raise EvaluationError(f"Unknown metrics requested for intervals: {unknown}")

    samples: dict[str, list[float]] = {metric: [] for metric in metrics}
    for replicate in _resampled_replicates(grouped, resamples, seed):
        replicate_metrics = evaluate_predictions(*replicate)
        for metric in metrics:
            samples[metric].append(getattr(replicate_metrics, metric))

    lower_q = (1 - confidence_level) / 2
    return [
        BootstrapInterval(
            metric=metric,
            point=round(getattr(point, metric), 6),
            lower=round(_quantile(samples[metric], lower_q), 6),
            upper=round(_quantile(samples[metric], 1 - lower_q), 6),
            confidence_level=confidence_level,
            resamples=resamples,
            group_count=len(grouped),
        )
        for metric in metrics
    ]


def paired_permutation_test(
    cases: Sequence[SemanticMappingCase],
    baseline: Sequence[SemanticMappingPrediction],
    candidate: Sequence[SemanticMappingPrediction],
    *,
    split: SemanticDatasetSplit | None = None,
    metric: str = "field_micro_f1",
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = DEFAULT_SEED,
) -> PairedApproachTest:
    """Test whether two approaches differ by more than group-level noise.

    Labels are exchanged for a whole source family at a time. Swapping individual
    cases would treat one family's templates as independent evidence and report a
    difference that a new source family would not reproduce.
    """
    baseline_metrics = evaluate_predictions(list(cases), list(baseline))
    candidate_metrics = evaluate_predictions(list(cases), list(candidate))
    if metric not in type(baseline_metrics).model_fields:
        raise EvaluationError(f"Unknown metric requested for comparison: {metric!r}")

    group_keys = resolve_group_keys(cases, split)
    baseline_by_id = _predictions_by_id(baseline)
    candidate_by_id = _predictions_by_id(candidate)
    groups = sorted({group_keys[case.case_id] for case in cases})

    observed = getattr(candidate_metrics, metric) - getattr(baseline_metrics, metric)
    rng = random.Random(seed)
    at_least_as_extreme = 0
    for _ in range(resamples):
        swapped = {group for group in groups if rng.random() < 0.5}
        left: list[SemanticMappingPrediction] = []
        right: list[SemanticMappingPrediction] = []
        for case in cases:
            flip = group_keys[case.case_id] in swapped
            first = candidate_by_id[case.case_id] if flip else baseline_by_id[case.case_id]
            second = baseline_by_id[case.case_id] if flip else candidate_by_id[case.case_id]
            left.append(first)
            right.append(second)
        # Permuted predictions mix approaches, so read the metric without identity checks.
        difference = _metric_without_identity(cases, right, metric) - _metric_without_identity(
            cases, left, metric
        )
        at_least_as_extreme += abs(difference) >= abs(observed) - 1e-12

    p_value = (at_least_as_extreme + 1) / (resamples + 1)
    mde = _paired_mde(cases, baseline_by_id, candidate_by_id, group_keys, metric)
    return PairedApproachTest(
        metric=metric,
        baseline=baseline[0].approach.name,
        candidate=candidate[0].approach.name,
        baseline_value=round(getattr(baseline_metrics, metric), 6),
        candidate_value=round(getattr(candidate_metrics, metric), 6),
        difference=round(observed, 6),
        p_value=round(p_value, 6),
        resamples=resamples,
        group_count=len(groups),
        minimum_detectable_effect=round(mde, 6),
        significant=p_value < 0.05,
    )


def risk_coverage_curve(
    cases: Sequence[SemanticMappingCase],
    predictions: Sequence[SemanticMappingPrediction],
) -> RiskCoverageCurve:
    """Error against the fraction of cases an approach is willing to answer."""
    if not cases:
        raise EvaluationError("At least one case is required for a risk-coverage curve")
    predictions_by_id = _predictions_by_id(predictions)
    scored = sorted(
        (
            (
                _case_confidence(predictions_by_id[case.case_id]),
                _case_loss(case, predictions_by_id[case.case_id]),
            )
            for case in cases
        ),
        key=lambda item: -item[0],
    )

    points: list[RiskCoveragePoint] = []
    cumulative = 0.0
    accepted = 0
    area = 0.0
    for confidence, group in groupby(scored, key=lambda item: item[0]):
        losses = [loss for _, loss in group]
        cumulative += math.fsum(losses)
        accepted += len(losses)
        risk = cumulative / accepted
        area += risk * len(losses) / len(scored)
        points.append(
            RiskCoveragePoint(
                coverage=round(accepted / len(scored), 6),
                risk=round(risk, 6),
                confidence_threshold=round(confidence, 6),
            )
        )
    return RiskCoverageCurve(
        points=points,
        area_under_curve=round(area, 6),
        risk_at_full_coverage=points[-1].risk,
    )


def _source_family(case: SemanticMappingCase) -> str:
    metadata = case.input.source_metadata
    parts = [metadata.system, metadata.vendor or "", metadata.product or ""]
    return ":".join(part.casefold() for part in parts).rstrip(":")


def _predictions_by_id(
    predictions: Sequence[SemanticMappingPrediction],
) -> dict[str, SemanticMappingPrediction]:
    return {prediction.case_id: prediction for prediction in predictions}


def _group_cases(
    cases: Sequence[SemanticMappingCase],
    predictions: Sequence[SemanticMappingPrediction],
    group_keys: dict[str, str],
) -> list[list[tuple[SemanticMappingCase, SemanticMappingPrediction]]]:
    predictions_by_id = _predictions_by_id(predictions)
    missing = sorted({case.case_id for case in cases} - set(predictions_by_id))
    if missing:
        raise EvaluationError(f"Predictions are missing for cases: {missing}")
    grouped: dict[str, list[tuple[SemanticMappingCase, SemanticMappingPrediction]]] = {}
    for case in cases:
        grouped.setdefault(group_keys[case.case_id], []).append(
            (case, predictions_by_id[case.case_id])
        )
    return [grouped[key] for key in sorted(grouped)]


def _resampled_replicates(
    grouped: list[list[tuple[SemanticMappingCase, SemanticMappingPrediction]]],
    resamples: int,
    seed: int,
) -> Iterable[tuple[list[SemanticMappingCase], list[SemanticMappingPrediction]]]:
    rng = random.Random(seed)
    for _ in range(resamples):
        drawn = [grouped[rng.randrange(len(grouped))] for _ in range(len(grouped))]
        cases: list[SemanticMappingCase] = []
        predictions: list[SemanticMappingPrediction] = []
        for group in drawn:
            for case, prediction in group:
                cases.append(case)
                predictions.append(prediction)
        yield _deduplicate(cases, predictions)


def _deduplicate(
    cases: list[SemanticMappingCase],
    predictions: list[SemanticMappingPrediction],
) -> tuple[list[SemanticMappingCase], list[SemanticMappingPrediction]]:
    """Metrics require unique IDs, but a redrawn family must still count more than once."""
    seen: dict[str, int] = {}
    unique_cases: list[SemanticMappingCase] = []
    unique_predictions: list[SemanticMappingPrediction] = []
    for case, prediction in zip(cases, predictions, strict=True):
        count = seen.get(case.case_id, 0)
        seen[case.case_id] = count + 1
        case_id = case.case_id if count == 0 else f"{case.case_id}-r{count}"
        unique_cases.append(case.model_copy(update={"case_id": case_id}))
        unique_predictions.append(prediction.model_copy(update={"case_id": case_id}))
    return unique_cases, unique_predictions


def _metric_without_identity(
    cases: Sequence[SemanticMappingCase],
    predictions: Sequence[SemanticMappingPrediction],
    metric: str,
) -> float:
    identity = predictions[0].approach
    aligned = [prediction.model_copy(update={"approach": identity}) for prediction in predictions]
    return float(getattr(evaluate_predictions(list(cases), aligned), metric))


def _paired_mde(
    cases: Sequence[SemanticMappingCase],
    baseline_by_id: dict[str, SemanticMappingPrediction],
    candidate_by_id: dict[str, SemanticMappingPrediction],
    group_keys: dict[str, str],
    metric: str,
) -> float:
    """Smallest difference this sample could detect, from per-family paired deltas."""
    del metric
    deltas: dict[str, list[float]] = {}
    for case in cases:
        delta = _case_loss(case, baseline_by_id[case.case_id]) - _case_loss(
            case, candidate_by_id[case.case_id]
        )
        deltas.setdefault(group_keys[case.case_id], []).append(delta)
    family_means = [sum(values) / len(values) for values in deltas.values()]
    if len(family_means) < 2:
        return _worst_case_mde(len(family_means))
    mean = sum(family_means) / len(family_means)
    variance = sum((value - mean) ** 2 for value in family_means) / (len(family_means) - 1)
    return _POWER_CONSTANT * math.sqrt(variance) / math.sqrt(len(family_means))


def _worst_case_mde(group_count: int) -> float:
    if group_count < 1:
        return 1.0
    return min(1.0, _POWER_CONSTANT * _MAX_BOUNDED_SD / math.sqrt(group_count))


def _case_confidence(prediction: SemanticMappingPrediction) -> float:
    """Rank abstentions last, then order by the approach's own reported scores."""
    if prediction.disposition != "mapped" or not prediction.asim_fields:
        return 0.0
    field_score = sum(field.score for field in prediction.asim_fields) / len(prediction.asim_fields)
    schema_score = prediction.ranked_schemas[0].score if prediction.ranked_schemas else 0.0
    return field_score * schema_score


def _case_loss(
    case: SemanticMappingCase,
    prediction: SemanticMappingPrediction,
) -> float:
    _, _, _, f1 = set_scores(expected_field_set(case), predicted_field_set(prediction))
    return 1 - f1


def _quantile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = quantile * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + ((ordered[upper] - ordered[lower]) * (position - lower))
