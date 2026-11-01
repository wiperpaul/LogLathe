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
from ..potato_bundle import example_literal_spans, example_slot_spans
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

# Same placeholder syntax as clustering and the review bundle; a looser pattern
# would treat syslog priorities such as `<134>` as slots.
_PLACEHOLDER = re.compile(r"<VAR:[A-Za-z0-9_]+>")
_PLACEHOLDER_PART = re.compile(r"(<VAR:[A-Za-z0-9_]+>)")
_WORD = re.compile(r"\S+")

# Words that state a slot's direction on their own, independent of from/to phrasing.
_DIRECTION_EVIDENCE = frozenset({"source", "src", "destination", "dst", "dest", "from", "to"})


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
    # model_copy skips validation, so re-run the case contract on every variant.
    variant = SemanticMappingCase.model_validate(variant.model_dump(mode="json"))
    return PerturbedCase(case=variant, perturbation=definition)


def perturb_cases(
    cases: list[SemanticMappingCase],
    name: str,
) -> list[PerturbedCase]:
    """Apply one perturbation across a case set, skipping cases it cannot alter."""
    variants = [perturb_case(case, name) for case in cases]
    return [variant for variant in variants if variant is not None]


def _event_captures(case: SemanticMappingCase) -> list[list[str]] | None:
    """Slot captures for every representative event, or None if any event misaligns."""
    template = case.input.template
    events = case.input.representative_events
    slots = case.input.parameter_slots
    captures: list[list[str]] = []
    for slot_spans, literal_spans in zip(
        example_slot_spans(template, events, slots),
        example_literal_spans(template, events, slots),
        strict=True,
    ):
        if not slot_spans and not literal_spans:
            return None
        captures.append([span["text"] for span in slot_spans])
    return captures


def _placeholders(template: str) -> list[str]:
    return [match.group(0) for match in _PLACEHOLDER.finditer(template)]


def _render(template: str, values: list[str]) -> str:
    remaining = iter(values)
    return _PLACEHOLDER.sub(lambda _: next(remaining), template)


def _with_template(case: SemanticMappingCase, template: str) -> SemanticMappingCase | None:
    """Replace template prose and rebuild each event from its own slot captures.

    Rebuilding from captures changes literal text identically in the template and
    every event, while observed values, and so slot examples, stay exactly as seen.
    Returns None when nothing changed or alignment cannot be preserved.
    """
    if template == case.input.template:
        return None
    if _placeholders(template) != _placeholders(case.input.template):
        return None
    captures = _event_captures(case)
    if captures is None:
        return None
    events = [
        event.model_copy(update={"text": _render(template, values)})
        for event, values in zip(case.input.representative_events, captures, strict=True)
    ]
    variant = case.model_copy(
        update={
            "input": case.input.model_copy(
                update={"template": template, "representative_events": events}
            )
        }
    )
    # Rewritten prose can make a capture boundary ambiguous; skip rather than misalign.
    if _event_captures(variant) != captures:
        return None
    return variant


def _protected_words(case: SemanticMappingCase) -> set[str]:
    """Words a rewrite must leave alone to keep constant locators resolvable."""
    protected: set[str] = set()
    for semantic in case.expected.source_semantics:
        if semantic.source_kind == "template_constant":
            protected.update(semantic.locator.casefold().split())
    return protected


def _retag(case: SemanticMappingCase, suffix: str) -> SemanticMappingCase:
    return case.model_copy(update={"case_id": f"{case.case_id}.{suffix}"})


def _retag_or_none(case: SemanticMappingCase | None, suffix: str) -> SemanticMappingCase | None:
    return None if case is None else _retag(case, suffix)


def _rewrite_words(text: str, replace: Callable[[str], str]) -> str:
    """Rewrite words in place, leaving placeholders and original spacing untouched."""

    def rewrite(match: re.Match[str]) -> str:
        return "".join(
            part if not part or _PLACEHOLDER.fullmatch(part) else replace(part)
            for part in _PLACEHOLDER_PART.split(match.group(0))
        )

    return _WORD.sub(rewrite, text)


def _abbreviate(case: SemanticMappingCase) -> SemanticMappingCase | None:
    protected = _protected_words(case)

    def replace(word: str) -> str:
        key = word.casefold()
        if key in protected:
            return word
        return _ABBREVIATIONS.get(key, word)

    template = _rewrite_words(case.input.template, replace)
    return _retag_or_none(_with_template(case, template), "abbreviated")


def _compound_identifiers(case: SemanticMappingCase) -> SemanticMappingCase | None:
    """Join the two words before a placeholder into one camelCase identifier."""
    protected = _protected_words(case)
    template = case.input.template
    tokens = list(_WORD.finditer(template))
    pieces: list[str] = []
    cursor = 0
    index = 0
    while index + 2 < len(tokens):
        first, second, slot = tokens[index : index + 3]
        words = (first.group(0), second.group(0))
        joinable = (
            _PLACEHOLDER.fullmatch(slot.group(0))
            and not any(_PLACEHOLDER.search(word) for word in words)
            and not {word.casefold() for word in words} & protected
        )
        if joinable:
            pieces.extend(
                [template[cursor : first.start()], f"{words[0].casefold()}{words[1].capitalize()}"]
            )
            cursor = second.end()
            index += 3
            continue
        index += 1
    pieces.append(template[cursor:])
    return _retag_or_none(_with_template(case, "".join(pieces)), "compound")


def _upper_case(case: SemanticMappingCase) -> SemanticMappingCase | None:
    """Upper-case template prose; captured values keep their observed case."""
    template = _rewrite_words(case.input.template, str.upper)
    return _retag_or_none(_with_template(case, template), "uppercase")


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
    # Draw event values from the declared examples so captures and examples agree.
    events = [
        SourceEvent(
            source_file=event.source_file,
            line_number=event.line_number,
            text=f"{event.text} correlation id {decoy.examples[index % len(decoy.examples)]}",
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
    """Swap the from/to words that bind a source/destination pair, and their labels.

    Only a `from` directly before the source slot and a `to` directly before its
    destination counterpart are rewritten, so other direction words, and the
    labels that depend on them, stay synchronized. The physical values are
    unchanged, so an approach that reads position rather than direction evidence
    will produce the same answer and be marked wrong.
    """
    template = case.input.template
    placeholders = list(_PLACEHOLDER.finditer(template))
    slot_index = {slot.slot_id: index for index, slot in enumerate(case.input.parameter_slots)}
    if len(placeholders) != len(slot_index):
        return None

    replacements: dict[int, tuple[int, str]] = {}
    swapped_roles: dict[str, str] = {}
    swapped_fields: dict[str, str] = {}
    for source_label, destination_label in _direction_pairs(case):
        source_word = _binding_word(template, placeholders[slot_index[source_label.locator]])
        destination_word = _binding_word(
            template, placeholders[slot_index[destination_label.locator]]
        )
        if source_word is None or destination_word is None:
            continue
        if (source_word.group(0).casefold(), destination_word.group(0).casefold()) != (
            "from",
            "to",
        ):
            continue
        targets = _swapped_target_fields(case, source_label, destination_label)
        if targets is None:
            continue
        replacements[source_word.start()] = (
            source_word.end(),
            _match_case("to", source_word.group(0)),
        )
        replacements[destination_word.start()] = (
            destination_word.end(),
            _match_case("from", destination_word.group(0)),
        )
        swapped_roles[source_label.semantic_id] = destination_label.role
        swapped_roles[destination_label.semantic_id] = source_label.role
        swapped_fields.update(targets)
    if not replacements or _direction_depends_on_context(case, placeholders, swapped_roles):
        return None

    pieces: list[str] = []
    cursor = 0
    for start in sorted(replacements):
        end, word = replacements[start]
        pieces.extend([template[cursor:start], word])
        cursor = end
    pieces.append(template[cursor:])
    rewritten = _with_template(case, "".join(pieces))
    if rewritten is None:
        return None

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
    variant = rewritten.model_copy(
        update={
            "expected": case.expected.model_copy(
                update={"source_semantics": semantics, "asim_fields": fields}
            )
        }
    )
    return _retag(variant, "swapped-direction")


def _binding_word(template: str, placeholder: re.Match[str]) -> re.Match[str] | None:
    """The whitespace-separated word immediately before a placeholder token."""
    start = placeholder.start()
    if start == 0 or not template[start - 1].isspace():
        return None
    words = list(_WORD.finditer(template, 0, start))
    return words[-1] if words else None


def _match_case(word: str, like: str) -> str:
    if like.isupper():
        return word.upper()
    if like[:1].isupper():
        return word.capitalize()
    return word


def _direction_depends_on_context(
    case: SemanticMappingCase,
    placeholders: list[re.Match[str]],
    swapped_roles: dict[str, str],
) -> bool:
    """Whether an unswapped directional slot lacks its own direction wording.

    In `from <IP> port <N> to <IP>` the port's direction comes from the
    surrounding `from`, so swapping the address pair would silently invalidate
    the port label. Such cases are skipped rather than mislabelled.
    """
    slot_index = {slot.slot_id: index for index, slot in enumerate(case.input.parameter_slots)}
    for semantic in case.expected.source_semantics:
        if semantic.source_kind != "slot" or semantic.semantic_id in swapped_roles:
            continue
        if parse_role(semantic.role).relation not in ("source", "destination"):
            continue
        index = slot_index[semantic.locator]
        start = placeholders[index - 1].end() if index else 0
        literal = case.input.template[start : placeholders[index].start()]
        if not set(re.findall(r"[a-z]+", literal.casefold())) & _DIRECTION_EVIDENCE:
            return True
    return False


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


def _direction_pairs(
    case: SemanticMappingCase,
) -> list[tuple[SourceSemanticLabel, SourceSemanticLabel]]:
    """Source/destination slot pairs that share a domain and property.

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

    pairs: list[tuple[SourceSemanticLabel, SourceSemanticLabel]] = []
    for (domain, prop, relation), labels in by_relation.items():
        if relation != "source" or len(labels) != 1:
            continue
        counterparts = by_relation.get((domain, prop, "destination"), [])
        if len(counterparts) == 1:
            pairs.append((labels[0], counterparts[0]))
    return pairs


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
