import re
from collections import Counter
from pathlib import Path

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
from asim_forge.potato_bundle import example_slot_spans

SEED_FIXTURES = (EXAMPLE_CASES, Path("evaluation/corpora/asim-cef-dev/cases.jsonl"))
NETWORK_MEANINGS = {
    "source.address": ("IPV4", "SrcIpAddr"),
    "destination.address": ("IPV4", "DstIpAddr"),
    "source.port": ("NUMBER", "SrcPortNumber"),
    "destination.port": ("NUMBER", "DstPortNumber"),
}

LABEL_PRESERVING = tuple(
    definition.name for definition in perturbations() if definition.family == "label-preserving"
)
LABEL_CHANGING = tuple(
    definition.name for definition in perturbations() if definition.family == "label-changing"
)


def _seed() -> SemanticMappingCase:
    return load_semantic_mapping_cases(EXAMPLE_CASES)[0]


def _all_seeds() -> list[SemanticMappingCase]:
    return [case for path in SEED_FIXTURES for case in load_semantic_mapping_cases(path)]


def _captures(case: SemanticMappingCase) -> list[list[str]]:
    spans = example_slot_spans(
        case.input.template, case.input.representative_events, case.input.parameter_slots
    )
    return [[span["text"] for span in event] for event in spans]


def _example_mismatches(case: SemanticMappingCase) -> set[str]:
    """Slots whose recorded examples match none of the values the events capture."""
    captures = _captures(case)
    return {
        slot.slot_id
        for index, slot in enumerate(case.input.parameter_slots)
        if not set(slot.examples) & {event[index] for event in captures if event}
    }


def _network_case(template: str, slots: list[tuple[str, str]]) -> SemanticMappingCase:
    """A one-event case whose slots carry ``(meaning, value)`` pairs in template order.

    The event is rendered from the template, and the seed's ``connection allowed``
    constant is kept, so ``template`` must contain that phrase.
    """
    data = _seed().model_dump(mode="json")
    expected = data["expected"]
    constants = [
        item for item in expected["source_semantics"] if item["source_kind"] == "template_constant"
    ]
    constant_ids = {item["semantic_id"] for item in constants}
    constant_fields = [
        field for field in expected["asim_fields"] if field["semantic_id"] in constant_ids
    ]
    slot_evidence = expected["source_semantics"][0]["evidence"]
    field_evidence = expected["asim_fields"][0]["evidence"]

    occurrences: Counter[str] = Counter()
    parameter_slots, semantics, fields = [], [], []
    for number, (meaning, value) in enumerate(slots, start=1):
        label, asim_field = NETWORK_MEANINGS[meaning]
        occurrences[label] += 1
        slot_id = f"p{number}"
        parameter_slots.append(
            {
                "slot_id": slot_id,
                "label": label,
                "placeholder": f"<VAR:{label}>",
                "occurrence": occurrences[label],
                "examples": [value],
            }
        )
        semantics.append(
            {
                "semantic_id": meaning,
                "source_kind": "slot",
                "locator": slot_id,
                "role": f"network.{meaning}",
                "evidence": slot_evidence,
            }
        )
        fields.append(
            {
                "semantic_id": meaning,
                "asim_field": asim_field,
                "constant_value": None,
                "evidence": field_evidence,
            }
        )

    values = iter(value for _meaning, value in slots)
    data["input"]["template"] = template
    data["input"]["parameter_slots"] = parameter_slots
    data["input"]["representative_events"] = [
        {
            "source_file": "test.log",
            "line_number": 1,
            "text": re.sub(r"<VAR:[A-Z0-9_]+>", lambda _match: next(values), template),
        }
    ]
    expected["source_semantics"] = semantics + constants
    expected["asim_fields"] = fields + constant_fields
    return SemanticMappingCase.model_validate(data)


ADDRESSES = [("source.address", "10.0.0.4"), ("destination.address", "192.0.2.10")]
ADDRESSES_AND_PORT = [*ADDRESSES, ("destination.port", "443")]


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


@pytest.mark.parametrize("name", perturbation_names())
def test_every_fixture_variant_keeps_its_events_aligned(name: str) -> None:
    """Variants must stay valid evidence: events still match, with the seed's captures."""
    for seed in _all_seeds():
        variant = perturb_case(seed, name)
        if variant is None:
            continue
        captures = _captures(variant.case)
        assert all(captures), (seed.case_id, variant.case.input.template)
        if name != "decoy-slot":
            assert captures == _captures(seed), seed.case_id
        assert _example_mismatches(variant.case) <= _example_mismatches(seed), seed.case_id


def test_direction_swap_only_applies_where_the_text_states_direction() -> None:
    applied = [
        seed.case_id for seed in _all_seeds() if perturb_case(seed, "swapped-direction") is not None
    ]

    assert applied == ["demo.network.allowed"]


def test_compound_rewrites_template_and_events_together() -> None:
    variant = perturb_case(_seed(), "compound")
    assert variant is not None

    assert "destinationPort <VAR:NUMBER>" in variant.case.input.template
    for event in variant.case.input.representative_events:
        assert "destinationPort " in event.text


def test_uppercase_changes_prose_but_not_captured_values() -> None:
    seed = _network_case(
        "Gateway connection allowed from host <VAR:IPV4> to <VAR:IPV4>",
        [("source.address", "host-a.example"), ("destination.address", "192.0.2.10")],
    )

    variant = perturb_case(seed, "uppercase")
    assert variant is not None

    assert variant.case.input.representative_events[0].text == (
        "GATEWAY CONNECTION ALLOWED FROM HOST host-a.example TO 192.0.2.10"
    )
    assert _captures(variant.case) == _captures(seed)


def test_whitespace_is_preserved_by_prose_rewrites() -> None:
    seed = _network_case(
        "MAR  1 gateway connection allowed from <VAR:IPV4> to <VAR:IPV4>", ADDRESSES
    )

    variant = perturb_case(seed, "abbreviated")
    assert variant is not None

    assert variant.case.input.template.startswith("MAR  1 gw connection")
    assert variant.case.input.representative_events[0].text.startswith("MAR  1 gw connection")


def test_syslog_priority_is_not_mistaken_for_a_placeholder() -> None:
    seed = _network_case(
        "<134>gateway connection allowed from <VAR:IPV4> to <VAR:IPV4> "
        "destination port <VAR:NUMBER>",
        ADDRESSES_AND_PORT,
    )

    for name in perturbation_names():
        variant = perturb_case(seed, name)
        assert variant is not None, name
        assert all(_captures(variant.case)), name


def test_direction_swap_skips_whitespace_only_differences() -> None:
    """A CEF-style template with no direction words is unchanged, so nothing is swapped."""
    seed = _network_case(
        "<134>MAR  1 CEF connection allowed src=<VAR:IPV4> dst=<VAR:IPV4> dpt=<VAR:NUMBER>",
        ADDRESSES_AND_PORT,
    )

    assert perturb_case(seed, "swapped-direction") is None


def test_direction_swap_handles_each_from_to_pair() -> None:
    seed = _network_case(
        "connection allowed from <VAR:IPV4> to <VAR:IPV4> ports from <VAR:NUMBER> to <VAR:NUMBER>",
        [*ADDRESSES, ("source.port", "51515"), ("destination.port", "443")],
    )

    variant = perturb_case(seed, "swapped-direction")
    assert variant is not None

    assert variant.case.input.template == (
        "connection allowed to <VAR:IPV4> from <VAR:IPV4> ports to <VAR:NUMBER> from <VAR:NUMBER>"
    )
    assert dict(_expected_pairs(variant.case)) == {
        "p1": "DstIpAddr",
        "p2": "SrcIpAddr",
        "p3": "DstPortNumber",
        "p4": "SrcPortNumber",
        "connection allowed": "EventResult",
    }
    assert _captures(variant.case) == _captures(seed)


def test_direction_swap_skips_slots_whose_direction_comes_from_context() -> None:
    """Swapping the addresses here would leave the source port reading as a source."""
    seed = _network_case(
        "connection allowed from <VAR:IPV4> port <VAR:NUMBER> to <VAR:IPV4> port <VAR:NUMBER>",
        [
            ("source.address", "10.0.0.4"),
            ("source.port", "51515"),
            ("destination.address", "192.0.2.10"),
            ("destination.port", "443"),
        ],
    )

    assert perturb_case(seed, "swapped-direction") is None


@pytest.mark.parametrize(("locator", "alias"), [("p1", "Src"), ("p2", "Dst")])
def test_direction_swap_skips_multiple_targets_instead_of_breaking_robustness(locator, alias):
    seed = _seed()
    semantic = next(item for item in seed.expected.source_semantics if item.locator == locator)
    original = next(
        field for field in seed.expected.asim_fields if field.semantic_id == semantic.semantic_id
    )
    seed.expected.asim_fields.append(original.model_copy(update={"asim_field": alias}))
    seed = SemanticMappingCase.model_validate(seed.model_dump(mode="json"))

    assert perturb_case(seed, "swapped-direction") is None
    report = run_robustness([seed], build_catalog(), ["direct-lexical"])
    assert report.rows
    assert all(row.perturbation != "swapped-direction" for row in report.rows)
    assert any("swapped-direction" in warning for warning in report.warnings)


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


def test_robustness_rejects_an_empty_approach_list() -> None:
    with pytest.raises(RobustnessError, match="At least one semantic mapping approach"):
        run_robustness([_seed()], build_catalog(), [])


def test_robustness_rejects_duplicate_approach_names() -> None:
    with pytest.raises(RobustnessError, match="must be unique"):
        run_robustness([_seed()], build_catalog(), ["direct-lexical", "direct-lexical"])


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
