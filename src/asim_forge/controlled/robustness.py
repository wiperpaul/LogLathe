"""Score approaches against controlled perturbations as a separate robustness track.

These results are never mixed into the headline average. Generated volume would
otherwise dominate a real-case micro average and turn a synthetic transformation
family into an apparent quality claim.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from ..evaluation import SemanticMappingCase
from ..models import AsimCatalog, StrictModel
from ..semantic_mapping.approaches import (
    APPROACH_NAMES,
    PRIOR_APPROACH_NAMES,
    RETRIEVAL_APPROACH_NAMES,
    build_approach,
)
from ..semantic_mapping.contracts import (
    MappingRequest,
    SemanticMappingApproach,
    SemanticMappingPrediction,
)
from ..semantic_mapping.metrics import (
    EvaluationError,
    evaluate_predictions,
    predicted_field_set,
)
from .perturbations import (
    PerturbationFamily,
    PerturbedCase,
    perturb_cases,
    perturbation_names,
)

# Priors read no source evidence and retrieval would match a variant to its own
# seed, so neither produces a meaningful surface-robustness result.
EXCLUDED_APPROACH_NAMES = frozenset(PRIOR_APPROACH_NAMES) | frozenset(RETRIEVAL_APPROACH_NAMES)

ROBUSTNESS_APPROACH_NAMES = tuple(
    name for name in APPROACH_NAMES if name not in EXCLUDED_APPROACH_NAMES
)


class RobustnessError(EvaluationError):
    """Raised when a robustness run cannot be scored fairly."""


class RobustnessRow(StrictModel):
    approach: str
    perturbation: str
    family: PerturbationFamily
    case_count: int = Field(ge=1)
    baseline_field_micro_f1: float = Field(ge=0, le=1)
    perturbed_field_micro_f1: float = Field(ge=0, le=1)
    field_micro_f1_delta: float
    # Share of cases whose predicted field set survived the perturbation unchanged.
    prediction_stability: float = Field(ge=0, le=1)
    expected_stability: bool
    passed: bool
    notes: list[str] = Field(default_factory=list)


class RobustnessReport(StrictModel):
    format_version: Literal["1"] = "1"
    catalogue_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    seed_case_count: int = Field(ge=1)
    rows: list[RobustnessRow] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def run_robustness(
    cases: list[SemanticMappingCase],
    catalog: AsimCatalog,
    approach_names: list[str] | None = None,
) -> RobustnessReport:
    """Run every applicable perturbation against every selected approach."""
    if not cases:
        raise RobustnessError("At least one seed case is required")
    names = list(ROBUSTNESS_APPROACH_NAMES if approach_names is None else approach_names)
    unknown = sorted(set(names) - set(APPROACH_NAMES))
    if unknown:
        raise RobustnessError(f"Unknown semantic mapping approaches: {unknown}")
    excluded = sorted(set(names) & EXCLUDED_APPROACH_NAMES)
    if excluded:
        raise RobustnessError(
            "Priors read no source evidence and retrieval would match a variant to its "
            f"own seed, so these cannot be scored for robustness: {excluded}"
        )

    rows: list[RobustnessRow] = []
    warnings: list[str] = []
    for name in names:
        approach = build_approach(name, reference_cases=cases)
        baseline = _predict(approach, cases, catalog)
        baseline_fields = {
            prediction.case_id: predicted_field_set(prediction) for prediction in baseline
        }
        for perturbation_name in perturbation_names():
            variants = perturb_cases(cases, perturbation_name)
            if not variants:
                warnings.append(
                    f"{perturbation_name}: no seed case could be perturbed; "
                    "the family is unmeasured rather than passing."
                )
                continue
            seed_ids = {_seed_case_id(variant.case.case_id) for variant in variants}
            baseline_metrics = evaluate_predictions(
                [case for case in cases if case.case_id in seed_ids],
                [prediction for prediction in baseline if prediction.case_id in seed_ids],
            )
            rows.append(
                _score(
                    approach_name=name,
                    baseline_metrics_f1=baseline_metrics.field_micro_f1,
                    baseline_fields=baseline_fields,
                    variants=variants,
                    catalog=catalog,
                    approach=approach,
                )
            )
    return RobustnessReport(
        catalogue_revision=catalog.manifest.resolved_revision,
        seed_case_count=len(cases),
        rows=rows,
        warnings=sorted(set(warnings)),
    )


def _score(
    *,
    approach_name: str,
    baseline_metrics_f1: float,
    baseline_fields: dict[str, set[tuple[str, str, str, object]]],
    variants: list[PerturbedCase],
    catalog: AsimCatalog,
    approach: SemanticMappingApproach,
) -> RobustnessRow:
    definition = variants[0].perturbation
    variant_cases = [variant.case for variant in variants]
    predictions = _predict(approach, variant_cases, catalog)
    metrics = evaluate_predictions(variant_cases, predictions)

    unchanged = 0
    for variant, prediction in zip(variants, predictions, strict=True):
        seed_id = _seed_case_id(variant.case.case_id)
        unchanged += predicted_field_set(prediction) == baseline_fields.get(seed_id, set())
    stability = unchanged / len(variants)

    expect_stable = definition.family == "label-preserving"
    delta = metrics.field_micro_f1 - baseline_metrics_f1
    notes: list[str] = []
    if expect_stable:
        passed = stability == 1.0 and delta >= 0
        if not passed:
            notes.append(
                "Surface change altered the answer: the approach is matching form, not meaning."
            )
    else:
        # A correct answer here requires the prediction to move with the labels.
        passed = metrics.field_micro_f1 >= baseline_metrics_f1 and stability < 1.0
        if stability == 1.0:
            notes.append(
                "Prediction did not move when the meaning did: direction evidence is unused."
            )
    return RobustnessRow(
        approach=approach_name,
        perturbation=definition.name,
        family=definition.family,
        case_count=len(variants),
        baseline_field_micro_f1=round(baseline_metrics_f1, 6),
        perturbed_field_micro_f1=round(metrics.field_micro_f1, 6),
        field_micro_f1_delta=round(delta, 6),
        prediction_stability=round(stability, 6),
        expected_stability=expect_stable,
        passed=passed,
        notes=notes,
    )


def _predict(
    approach: SemanticMappingApproach,
    cases: list[SemanticMappingCase],
    catalog: AsimCatalog,
) -> list[SemanticMappingPrediction]:
    return [
        approach.predict(
            MappingRequest(
                case_id=case.case_id,
                catalogue_revision=case.catalogue_revision,
                input=case.input,
            ),
            catalog,
        )
        for case in cases
    ]


def _seed_case_id(case_id: str) -> str:
    """Variant IDs are the seed ID with one appended perturbation suffix."""
    return case_id.rsplit(".", 1)[0]


def write_robustness_report(path: Path, report: RobustnessReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
