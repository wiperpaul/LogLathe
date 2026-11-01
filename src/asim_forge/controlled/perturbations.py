"""Label-preserving and label-changing perturbations generated from seed cases.

The answers here are known by construction, so this track needs no acquisition and
carries no licence risk. Its purpose is to separate two failures the headline
metrics cannot distinguish: an approach that understands an event, and an approach
that has memorised one surface form of it.

A label-preserving variant must not change the expected mapping. A label-changing
variant must change it. An approach that scores identically on both is reading
position rather than meaning.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Literal

from ..evaluation import SemanticMappingCase, SourceSemanticLabel
from ..models import ParameterSlot, SourceEvent, StrictModel
from ..source_semantics import parse_role

PerturbationFamily = Literal["label-preserving", "label-changing"]

# Applied to template prose only, never to placeholders or constant locators.
# Restricted to abbreviations that actually occur in vendor logs, so a failure here
# is a real gap rather than an invented one.
_ABBREVIATIONS: dict[str, str] = {
    "address": "addr",
    "destination": "dst",
    "device": "dvc",
    "gateway": "gw",
    "source": "src",
    "user": "usr",
}

_PLACEHOLDER = re.compile(r"<[^>]+>")


class Perturbation(StrictModel):
    """One named transformation with its expected effect on the labels."""

    name: str
    family: PerturbationFamily
    rationale: str


class PerturbedCase(StrictModel):
    case: SemanticMappingCase
    perturbation: Perturbation


def perturbation_names() -> tuple[str, ...]:
    return tuple(_PERTURBATIONS)


def perturbations() -> tuple[Perturbation, ...]:
    return tuple(definition for definition, _ in _PERTURBATIONS.values())


def perturb_case(case: SemanticMappingCase, name: str) -> PerturbedCase | None:
    """Apply one named perturbation, or return None when it does not apply."""
    if name not in _PERTURBATIONS:
        raise KeyError(f"Unknown perturbation: {name!r}")
    definition, transform = _PERTURBATIONS[name]
    variant = transform(case)
    if variant is None:
        return None
    return PerturbedCase(case=variant, perturbation=definition)


def perturb_cases(
    cases: list[SemanticMappingCase],
    name: str,
) -> list[PerturbedCase]:
    """Apply one perturbation across a case set, skipping cases it cannot alter."""
    variants = [perturb_case(case, name) for case in cases]
    return [variant for variant in variants if variant is not None]


def _rewrite_text(case: SemanticMappingCase, rewrite: Callable[[str], str]) -> SemanticMappingCase:
    """Apply a text rewrite to the template and every representative event."""
    events = [
        event.model_copy(update={"text": rewrite(event.text)})
        for event in case.input.representative_events
    ]
    return case.model_copy(
        update={
            "input": case.input.model_copy(
                update={
                    "template": rewrite(case.input.template),
                    "representative_events": events,
                }
            )
        }
    )


def _protected_words(case: SemanticMappingCase) -> set[str]:
    """Words a rewrite must leave alone to keep constant locators resolvable."""
    protected: set[str] = set()
    for semantic in case.expected.source_semantics:
        if semantic.source_kind == "template_constant":
            protected.update(semantic.locator.casefold().split())
    return protected


def _retag(case: SemanticMappingCase, suffix: str) -> SemanticMappingCase:
    return case.model_copy(update={"case_id": f"{case.case_id}.{suffix}"})


def _rewrite_words(text: str, replace: Callable[[str], str]) -> str:
    """Rewrite whitespace-separated words, leaving placeholders untouched."""
    return " ".join(
        word if _PLACEHOLDER.fullmatch(word) else replace(word) for word in text.split()
    )


def _abbreviate(case: SemanticMappingCase) -> SemanticMappingCase | None:
    protected = _protected_words(case)

    def replace(word: str) -> str:
        key = word.casefold()
        if key in protected:
            return word
        return _ABBREVIATIONS.get(key, word)

    def rewrite(text: str) -> str:
        return _rewrite_words(text, replace)

    if rewrite(case.input.template) == case.input.template:
        return None
    return _retag(_rewrite_text(case, rewrite), "abbreviated")


def _compound_identifiers(case: SemanticMappingCase) -> SemanticMappingCase | None:
    """Join the two words before a placeholder into one camelCase identifier."""
    protected = _protected_words(case)

    def rewrite(text: str) -> str:
        words = text.split()
        output: list[str] = []
        index = 0
        while index < len(words):
            window = words[index : index + 3]
            joinable = (
                len(window) == 3
                and _PLACEHOLDER.fullmatch(window[2])
                and not any(_PLACEHOLDER.fullmatch(word) for word in window[:2])
                and not {word.casefold() for word in window[:2]} & protected
            )
            if joinable:
                output.append(f"{window[0].casefold()}{window[1].capitalize()}")
                output.append(window[2])
                index += 3
                continue
            output.append(words[index])
            index += 1
        return " ".join(output)

    if rewrite(case.input.template) == case.input.template:
        return None
    return _retag(_rewrite_text(case, rewrite), "compound")


def _upper_case(case: SemanticMappingCase) -> SemanticMappingCase | None:
    def rewrite(text: str) -> str:
        return _rewrite_words(text, str.upper)

    if rewrite(case.input.template) == case.input.template:
        return None
    return _retag(_rewrite_text(case, rewrite), "uppercase")


def _opaque_slot_labels(case: SemanticMappingCase) -> SemanticMappingCase | None:
    """Strip meaning from slot labels while leaving the template untouched."""
    if not case.input.parameter_slots:
        return None
    slots = [
        slot.model_copy(update={"label": f"f{index}"})
        for index, slot in enumerate(case.input.parameter_slots, start=1)
    ]
    variant = case.model_copy(
        update={"input": case.input.model_copy(update={"parameter_slots": slots})}
    )
    return _retag(variant, "opaque-labels")


def _decoy_slot(case: SemanticMappingCase) -> SemanticMappingCase | None:
    """Append an unmapped slot of an existing physical type to test precision."""
    slots = case.input.parameter_slots
    if not slots:
        return None
    decoy_id = f"p{max(int(slot.slot_id[1:]) for slot in slots) + 1}"
    decoy = ParameterSlot(
        slot_id=decoy_id,
        label="NUMBER",
        placeholder="<VAR:NUMBER>",
        occurrence=1 + sum(slot.placeholder == "<VAR:NUMBER>" for slot in slots),
        examples=["4815", "162342"],
    )
    tail = f" correlation id {decoy.placeholder}"
    events = [
        SourceEvent(
            source_file=event.source_file,
            line_number=event.line_number,
            text=f"{event.text} correlation id {4815 + index}",
        )
        for index, event in enumerate(case.input.representative_events)
    ]
    variant = case.model_copy(
        update={
            "input": case.input.model_copy(
                update={
                    "template": case.input.template + tail,
                    "parameter_slots": [*slots, decoy],
                    "representative_events": events,
                }
            )
        }
    )
    return _retag(variant, "decoy-slot")


def _swap_source_destination(case: SemanticMappingCase) -> SemanticMappingCase | None:
    """Reverse direction words and the labels that depend on them.

    The physical values are unchanged, so an approach that reads position rather
    than direction evidence will produce the same answer and be marked wrong.
    """
    pair = _direction_pair(case)
    if pair is None:
        return None
    source_label, destination_label = pair

    def rewrite(text: str) -> str:
        return _rewrite_words(text, _swap_direction_word)

    if rewrite(case.input.template) == case.input.template:
        return None

    swapped_fields = _swapped_target_fields(case, source_label, destination_label)
    if swapped_fields is None:
        return None
    swapped_roles = {
        source_label.semantic_id: destination_label.role,
        destination_label.semantic_id: source_label.role,
    }
    semantics = [
        semantic.model_copy(update={"role": swapped_roles[semantic.semantic_id]})
        if semantic.semantic_id in swapped_roles
        else semantic
        for semantic in case.expected.source_semantics
    ]
    fields = [
        field.model_copy(update={"asim_field": swapped_fields[field.semantic_id]})
        if field.semantic_id in swapped_fields
        else field
        for field in case.expected.asim_fields
    ]
    variant = _rewrite_text(case, rewrite).model_copy(
        update={
            "expected": case.expected.model_copy(
                update={"source_semantics": semantics, "asim_fields": fields}
            )
        }
    )
    return _retag(variant, "swapped-direction")


def _swapped_target_fields(
    case: SemanticMappingCase,
    source_label: SourceSemanticLabel,
    destination_label: SourceSemanticLabel,
) -> dict[str, str] | None:
    source_fields = [
        field.asim_field
        for field in case.expected.asim_fields
        if field.semantic_id == source_label.semantic_id
    ]
    destination_fields = [
        field.asim_field
        for field in case.expected.asim_fields
        if field.semantic_id == destination_label.semantic_id
    ]
    if len(source_fields) != 1 or len(destination_fields) != 1:
        # shortcut: one target per side; extend when complete paired target sets are defined.
        return None
    return {
        source_label.semantic_id: destination_fields[0],
        destination_label.semantic_id: source_fields[0],
    }


def _direction_pair(
    case: SemanticMappingCase,
) -> tuple[SourceSemanticLabel, SourceSemanticLabel] | None:
    """The one source/destination slot pair sharing a domain and property.

    Matching on facets rather than role text avoids pairing a source address with
    a destination port, which would swap two labels that are not counterparts.
    """
    by_relation: dict[tuple[str, str, str], list[SourceSemanticLabel]] = {}
    for semantic in case.expected.source_semantics:
        if semantic.source_kind != "slot":
            continue
        facets = parse_role(semantic.role)
        if facets.relation not in ("source", "destination"):
            continue
        key = (facets.domain, facets.property, facets.relation)
        by_relation.setdefault(key, []).append(semantic)

    for (domain, prop, relation), labels in by_relation.items():
        if relation != "source" or len(labels) != 1:
            continue
        counterparts = by_relation.get((domain, prop, "destination"), [])
        if len(counterparts) == 1:
            return labels[0], counterparts[0]
    return None


def _swap_direction_word(word: str) -> str:
    """Swap only the prepositions that bind the relabelled pair.

    Rewriting `source`/`destination` as well would desynchronise other slots whose
    labels this perturbation does not swap, such as a destination port.
    """
    swaps = {"from": "to", "to": "from"}
    replacement = swaps.get(word.casefold())
    return replacement if replacement is not None else word


_PERTURBATIONS: dict[
    str,
    tuple[Perturbation, Callable[[SemanticMappingCase], SemanticMappingCase | None]],
] = {
    "abbreviated": (
        Perturbation(
            name="abbreviated",
            family="label-preserving",
            rationale="Common abbreviations must not change the mapping.",
        ),
        _abbreviate,
    ),
    "compound": (
        Perturbation(
            name="compound",
            family="label-preserving",
            rationale="Compound identifiers must tokenize back to the same concepts.",
        ),
        _compound_identifiers,
    ),
    "uppercase": (
        Perturbation(
            name="uppercase",
            family="label-preserving",
            rationale="Case must not carry meaning.",
        ),
        _upper_case,
    ),
    "opaque-labels": (
        Perturbation(
            name="opaque-labels",
            family="label-preserving",
            rationale="Meaningless slot labels leave the template evidence intact.",
        ),
        _opaque_slot_labels,
    ),
    "decoy-slot": (
        Perturbation(
            name="decoy-slot",
            family="label-preserving",
            rationale="An unmapped slot of a familiar type must not gain a target.",
        ),
        _decoy_slot,
    ),
    "swapped-direction": (
        Perturbation(
            name="swapped-direction",
            family="label-changing",
            rationale="Reversing direction words must reverse the mapping.",
        ),
        _swap_source_destination,
    ),
}
