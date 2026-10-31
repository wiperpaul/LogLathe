import pytest
from semantic_fixtures import EXAMPLE_CASES, build_catalog

from asim_forge.controlled import (
    RobustnessError,
    perturb_case,
    perturb_cases,
    perturbation_names,
    perturbations,
    run_robustness,
)
from asim_forge.controlled.robustness import ROBUSTNESS_APPROACH_NAMES
from asim_forge.evaluation import SemanticMappingCase, load_semantic_mapping_cases

LABEL_PRESERVING = tuple(
    definition.name for definition in perturbations() if definition.family == "label-preserving"
)
LABEL_CHANGING = tuple(
    definition.name for definition in perturbations() if definition.family == "label-changing"
)


def _seed() -> SemanticMappingCase:
    return load_semantic_mapping_cases(EXAMPLE_CASES)[0]


def _expected_pairs(case: SemanticMappingCase) -> set[tuple[str, str]]:
    semantics = {semantic.semantic_id: semantic for semantic in case.expected.source_semantics}
    return {
        (semantics[field.semantic_id].locator, field.asim_field)
        for field in case.expected.asim_fields
    }


def test_every_perturbation_applies_to_the_seed_case() -> None:
    applied = {name for name in perturbation_names() if perturb_case(_seed(), name) is not None}

    assert applied == set(perturbation_names())


@pytest.mark.parametrize("name", perturbation_names())
def test_perturbed_case_revalidates_against_the_case_contract(name: str) -> None:
    variant = perturb_case(_seed(), name)
    assert variant is not None

    # A round trip re-runs every cross-reference validator on the case model.
    SemanticMappingCase.model_validate(variant.case.model_dump(mode="json"))


@pytest.mark.parametrize("name", perturbation_names())
def test_perturbation_preserves_placeholders(name: str) -> None:
    variant = perturb_case(_seed(), name)
    assert variant is not None

    for slot in variant.case.input.parameter_slots:
        assert slot.placeholder in variant.case.input.template


@pytest.mark.parametrize("name", LABEL_PRESERVING)
def test_label_preserving_perturbation_leaves_the_answer_alone(name: str) -> None:
    seed = _seed()
    variant = perturb_case(seed, name)
    assert variant is not None

    if name == "decoy-slot":
        # The decoy adds a slot that maps to nothing, so the answer set is unchanged.
        assert _expected_pairs(variant.case) == _expected_pairs(seed)
        assert len(variant.case.input.parameter_slots) == len(seed.input.parameter_slots) + 1
        return
    assert _expected_pairs(variant.case) == _expected_pairs(seed)


@pytest.mark.parametrize("name", LABEL_CHANGING)
def test_label_changing_perturbation_moves_the_answer(name: str) -> None:
    seed = _seed()
    variant = perturb_case(seed, name)
    assert variant is not None

    assert _expected_pairs(variant.case) != _expected_pairs(seed)


def test_direction_swap_exchanges_the_paired_targets_only() -> None:
    seed = _seed()
    variant = perturb_case(seed, "swapped-direction")
    assert variant is not None

    before = dict(_expected_pairs(seed))
    after = dict(_expected_pairs(variant.case))
    assert before["p1"] == "SrcIpAddr"
    assert before["p2"] == "DstIpAddr"
    assert after["p1"] == "DstIpAddr"
    assert after["p2"] == "SrcIpAddr"
    # The destination port is not a counterpart of either address and must not move.
    assert after["p3"] == before["p3"]


def test_direction_swap_reverses_the_template_prepositions() -> None:
    variant = perturb_case(_seed(), "swapped-direction")
    assert variant is not None

    words = variant.case.input.template.split()
    assert words.index("to") < words.index("from")


def test_abbreviation_leaves_constant_locators_resolvable() -> None:
    seed = _seed()
    variant = perturb_case(seed, "abbreviated")
    assert variant is not None

    template = variant.case.input.template.casefold()
    for semantic in variant.case.expected.source_semantics:
        if semantic.source_kind == "template_constant":
            assert semantic.locator.casefold() in template


def test_opaque_labels_strip_meaning_but_keep_the_template() -> None:
    seed = _seed()
    variant = perturb_case(seed, "opaque-labels")
    assert variant is not None

    assert variant.case.input.template == seed.input.template
    assert [slot.label for slot in variant.case.input.parameter_slots] == ["f1", "f2", "f3"]


def test_variant_case_ids_stay_traceable_to_their_seed() -> None:
    seed = _seed()

    for name in perturbation_names():
        variant = perturb_case(seed, name)
        assert variant is not None
        assert variant.case.case_id.startswith(f"{seed.case_id}.")
        assert variant.case.case_id.rsplit(".", 1)[0] == seed.case_id


def test_unknown_perturbation_is_rejected() -> None:
    with pytest.raises(KeyError, match="Unknown perturbation"):
        perturb_case(_seed(), "not-a-perturbation")


def test_perturb_cases_skips_cases_a_family_cannot_alter() -> None:
    seed = _seed()
    stripped = seed.model_copy(
        update={
            "input": seed.input.model_copy(update={"template": "opaque event <VAR:IPV4>"}),
        }
    )

    # No direction words remain, so the family is skipped rather than faked.
    assert perturb_cases([stripped], "swapped-direction") == []


def test_robustness_defaults_to_approaches_that_read_the_event() -> None:
    assert "null-prior" not in ROBUSTNESS_APPROACH_NAMES
    assert "case-retrieval" not in ROBUSTNESS_APPROACH_NAMES
    assert set(ROBUSTNESS_APPROACH_NAMES) == {
        "direct-lexical",
        "semantic-frame",
        "matcher-ensemble",
    }


@pytest.mark.parametrize("name", ["null-prior", "case-retrieval"])
def test_robustness_refuses_approaches_it_cannot_score_fairly(name: str) -> None:
    with pytest.raises(RobustnessError, match="cannot be scored for robustness"):
        run_robustness([_seed()], build_catalog(), [name])


def test_robustness_reports_one_row_per_approach_and_perturbation() -> None:
    report = run_robustness([_seed()], build_catalog(), ["direct-lexical"])

    assert {row.perturbation for row in report.rows} == set(perturbation_names())
    assert all(row.approach == "direct-lexical" for row in report.rows)
    assert report.seed_case_count == 1


def test_opaque_labels_separate_label_readers_from_value_readers() -> None:
    """The label-dependent approaches lose accuracy; the value-profiling one does not."""
    report = run_robustness([_seed()], build_catalog())

    rows = {(row.approach, row.perturbation): row for row in report.rows}
    for approach in ("direct-lexical", "semantic-frame"):
        row = rows[(approach, "opaque-labels")]
        assert row.field_micro_f1_delta < 0
        assert not row.passed
        assert any("matching form" in note for note in row.notes)

    ensemble = rows[("matcher-ensemble", "opaque-labels")]
    assert ensemble.field_micro_f1_delta == 0
    assert ensemble.prediction_stability == 1.0
    assert ensemble.passed


def test_surface_only_changes_do_not_move_the_answer() -> None:
    report = run_robustness([_seed()], build_catalog())

    for row in report.rows:
        if row.perturbation in {"abbreviated", "compound", "uppercase"}:
            assert row.passed, row
            assert row.prediction_stability == 1.0


def test_robustness_rejects_an_empty_seed_set() -> None:
    with pytest.raises(RobustnessError, match="At least one seed case"):
        run_robustness([], build_catalog())


def test_robustness_baseline_only_includes_cases_the_transformation_can_change() -> None:
    seed = _seed()
    uppercase = seed.model_dump(mode="json")
    uppercase["case_id"] = "already-uppercase"
    uppercase["input"]["template"] = "NETWORK SOURCE ADDRESS <VAR:IPV4>"
    uppercase["input"]["representative_events"] = [
        {"source_file": "test.log", "line_number": 1, "text": "NETWORK SOURCE ADDRESS 10.0.0.1"}
    ]
    uppercase["input"]["parameter_slots"] = uppercase["input"]["parameter_slots"][:1]
    uppercase["expected"]["source_semantics"] = uppercase["expected"]["source_semantics"][:1]
    semantic_id = uppercase["expected"]["source_semantics"][0]["semantic_id"]
    uppercase["expected"]["asim_fields"] = [
        field
        for field in uppercase["expected"]["asim_fields"]
        if field["semantic_id"] == semantic_id
    ]
    extra = SemanticMappingCase.model_validate(uppercase)
    catalog = build_catalog()

    def uppercase_result(cases):
        report = run_robustness(cases, catalog, ["direct-lexical"])
        return next(row for row in report.rows if row.perturbation == "uppercase")

    original = uppercase_result([seed])
    mixed = uppercase_result([seed, extra])

    assert original.passed
    assert mixed == original
