import pytest
from semantic_fixtures import EXAMPLE_CASES, build_catalog

from asim_forge.evaluation import SemanticMappingCase, load_semantic_mapping_cases
from asim_forge.evaluation_splits import SemanticDatasetSplit
from asim_forge.models import AsimCatalog
from asim_forge.semantic_mapping.approaches import build_approach
from asim_forge.semantic_mapping.contracts import MappingRequest, SemanticMappingPrediction
from asim_forge.semantic_mapping.metrics import EvaluationError
from asim_forge.semantic_mapping.statistics import (
    bootstrap_intervals,
    describe_sample,
    paired_permutation_test,
    resolve_group_keys,
    risk_coverage_curve,
)


def _family_cases(family_count: int) -> list[SemanticMappingCase]:
    """Clone the example case across independent source families."""
    template = load_semantic_mapping_cases(EXAMPLE_CASES)[0]
    cases: list[SemanticMappingCase] = []
    for index in range(family_count):
        metadata = template.input.source_metadata.model_copy(
            update={"system": f"system-{index}", "product": f"product-{index}"}
        )
        cases.append(
            template.model_copy(
                update={
                    "case_id": f"family{index}.case1",
                    "input": template.input.model_copy(update={"source_metadata": metadata}),
                }
            )
        )
    return cases


def _predict(
    approach_name: str,
    cases: list[SemanticMappingCase],
    catalog: AsimCatalog,
) -> list[SemanticMappingPrediction]:
    approach = build_approach(approach_name, reference_cases=cases)
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


def _split(cases: list[SemanticMappingCase], group_ids: list[str]) -> SemanticDatasetSplit:
    return SemanticDatasetSplit(
        split_id="pinned-split",
        catalogue_revision=cases[0].catalogue_revision,
        group_strategy="manual",
        entries=[
            {"case_id": case.case_id, "group_id": group_id, "partition": "test"}
            for case, group_id in zip(cases, group_ids, strict=True)
        ],
    )


def _interleave(
    strong: list[SemanticMappingPrediction],
    weak: list[SemanticMappingPrediction],
) -> list[SemanticMappingPrediction]:
    """Alternate two approaches under one identity so the metric engine accepts them."""
    identity = strong[0].approach
    return [
        (strong[index] if index % 2 else weak[index]).model_copy(update={"approach": identity})
        for index in range(len(strong))
    ]


def test_sample_adequacy_reports_families_not_cases() -> None:
    sample = describe_sample(_family_cases(9))

    assert sample.case_count == 9
    assert sample.group_count == 9
    assert sample.grouping == "source-family"
    # A [0,1] metric over nine families cannot resolve small differences.
    assert 0.4 < sample.minimum_detectable_effect < 0.5


def test_minimum_detectable_effect_shrinks_as_families_are_added() -> None:
    few = describe_sample(_family_cases(4)).minimum_detectable_effect
    many = describe_sample(_family_cases(16)).minimum_detectable_effect

    assert many < few


def test_group_keys_prefer_the_frozen_split_group() -> None:
    cases = _family_cases(2)
    split = _split(cases, ["shared-family", "shared-family"])

    keys = resolve_group_keys(cases, split)

    # Both cases belong to one pre-label group even though their metadata differs.
    assert set(keys.values()) == {"shared-family"}
    assert set(resolve_group_keys(cases).values()) != {"shared-family"}


def test_group_keys_reject_a_split_that_omits_a_case() -> None:
    cases = _family_cases(2)
    split = _split(cases[:1], ["only"])

    with pytest.raises(EvaluationError, match="does not assign a group"):
        resolve_group_keys(cases, split)


def test_bootstrap_interval_collapses_when_every_family_agrees() -> None:
    catalog = build_catalog()
    cases = _family_cases(8)
    predictions = _predict("semantic-frame", cases, catalog)

    intervals = {
        interval.metric: interval
        for interval in bootstrap_intervals(cases, predictions, resamples=200)
    }

    field = intervals["field_micro_f1"]
    assert field.point == 1.0
    assert field.lower == field.upper == 1.0
    assert field.group_count == 8


def test_bootstrap_interval_widens_when_families_disagree() -> None:
    catalog = build_catalog()
    cases = _family_cases(8)
    strong = _predict("semantic-frame", cases, catalog)
    weak = _predict("null-prior", cases, catalog)
    # Half the families are answered well and half not at all.
    mixed = _interleave(strong, weak)

    intervals = {
        interval.metric: interval for interval in bootstrap_intervals(cases, mixed, resamples=400)
    }

    field = intervals["field_micro_f1"]
    assert field.lower < field.point < field.upper
    assert field.lower >= 0.0
    assert field.upper <= 1.0


def test_paired_permutation_detects_a_difference_repeated_across_families() -> None:
    catalog = build_catalog()
    cases = _family_cases(8)
    baseline = _predict("direct-lexical", cases, catalog)
    candidate = _predict("semantic-frame", cases, catalog)

    result = paired_permutation_test(cases, baseline, candidate, resamples=500)

    assert result.baseline == "direct-lexical"
    assert result.candidate == "semantic-frame"
    assert result.difference > 0
    assert result.p_value < 0.05
    assert result.significant


def test_paired_permutation_cannot_resolve_a_single_family() -> None:
    catalog = build_catalog()
    cases = _family_cases(1)
    baseline = _predict("direct-lexical", cases, catalog)
    candidate = _predict("semantic-frame", cases, catalog)

    result = paired_permutation_test(cases, baseline, candidate, resamples=200)

    # One group offers no exchangeable unit, so a real gap still cannot be claimed.
    assert result.difference > 0
    assert not result.significant
    assert result.group_count == 1


def test_paired_permutation_rejects_an_unknown_metric() -> None:
    catalog = build_catalog()
    cases = _family_cases(2)
    predictions = _predict("direct-lexical", cases, catalog)

    with pytest.raises(EvaluationError, match="Unknown metric"):
        paired_permutation_test(cases, predictions, predictions, metric="not_a_metric")


def test_risk_coverage_curve_falls_as_abstention_rises() -> None:
    catalog = build_catalog()
    cases = _family_cases(6)
    strong = _predict("semantic-frame", cases, catalog)
    weak = _predict("null-prior", cases, catalog)
    mixed = _interleave(strong, weak)

    curve = risk_coverage_curve(cases, mixed)

    assert len(curve.points) == 2
    assert curve.points[-1].coverage == 1.0
    # Abstaining approaches score zero confidence, so they are rejected first.
    assert curve.points[0].risk < curve.risk_at_full_coverage
    assert curve.area_under_curve < curve.risk_at_full_coverage


def test_risk_coverage_curve_is_flat_when_every_case_is_correct() -> None:
    catalog = build_catalog()
    cases = _family_cases(4)
    predictions = _predict("semantic-frame", cases, catalog)

    curve = risk_coverage_curve(cases, predictions)

    assert curve.area_under_curve == 0.0
    assert curve.risk_at_full_coverage == 0.0


def test_equal_confidence_cases_share_one_threshold_and_ignore_input_order() -> None:
    cases = _family_cases(2)
    predictions = _predict("semantic-frame", cases, build_catalog())
    predictions[1] = predictions[1].model_copy(
        update={
            "asim_fields": [
                field.model_copy(
                    update={
                        "asim_field": f"Wrong{index}",
                        "ranked_candidates": [
                            field.ranked_candidates[0].model_copy(
                                update={"asim_field": f"Wrong{index}"}
                            )
                        ],
                    }
                )
                for index, field in enumerate(predictions[1].asim_fields)
            ]
        }
    )

    curve = risk_coverage_curve(cases, predictions)

    assert curve == risk_coverage_curve(cases[::-1], predictions[::-1])
    assert len(curve.points) == 1
    assert curve.points[0].coverage == 1.0
    assert curve.area_under_curve == curve.risk_at_full_coverage == 0.5


def test_curve_area_weights_thresholds_by_the_number_of_accepted_cases() -> None:
    cases = _family_cases(3)
    strong = _predict("semantic-frame", cases, build_catalog())
    weak = _predict("null-prior", cases, build_catalog())

    curve = risk_coverage_curve(cases, [strong[0], weak[1], weak[2]])

    assert len(curve.points) == 2
    assert curve.area_under_curve == pytest.approx(4 / 9, abs=1e-6)
