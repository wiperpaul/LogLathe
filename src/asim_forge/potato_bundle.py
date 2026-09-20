"""Produce a self-contained Potato review task from cluster records."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

import yaml

from .models import AsimCatalog, ParameterSlot, ParsedCluster, ReviewTask, SourceEvent

if TYPE_CHECKING:
    from .mapping_review import MappingReviewTask

_PLACEHOLDER = re.compile(r"<VAR:[A-Za-z0-9_]+>")


class SlotSpan(TypedDict):
    slot_id: str
    start: int
    end: int
    text: str


class LiteralSpan(TypedDict):
    start: int
    end: int
    text: str


def write_potato_bundle(
    clusters: list[ParsedCluster],
    output_dir: Path,
) -> tuple[Path, Path]:
    bundle_dir = output_dir / "potato"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    items_path = bundle_dir / "items.jsonl"
    config_path = bundle_dir / "config.yaml"

    tasks = [_to_review_task(cluster) for cluster in clusters]
    _write_jsonl(items_path, [task.model_dump(mode="json", exclude_none=True) for task in tasks])
    config_path.write_text(
        yaml.safe_dump(_potato_config(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return items_path, config_path


def _to_review_task(cluster: ParsedCluster) -> ReviewTask:
    return ReviewTask(
        id=cluster.cluster_id,
        cluster_id=cluster.cluster_id,
        event_count=cluster.event_count,
        **source_evidence(cluster.template, cluster.representative_events, cluster.parameter_slots),
    )


def source_evidence(
    template: str,
    representative_events: list[SourceEvent],
    parameter_slots: list[ParameterSlot],
) -> dict:
    """Shared source display for cluster coherence and subsequent mapping review."""
    samples = "\n".join(
        f"[{event.source_file}:{event.line_number}] {event.text}" for event in representative_events
    )
    slots = (
        "\n".join(
            f"- {slot.slot_id} {slot.placeholder}: {', '.join(slot.examples) or 'no samples'}"
            for slot in parameter_slots
        )
        or "- No parameters detected"
    )
    text = f"TEMPLATE\n{template}\n\nREPRESENTATIVE EVENTS\n{samples}\n\nPARAMETER SLOTS\n{slots}"
    representative_events_table: dict[str, object] = {
        "headers": ["Source", "Event"],
        "rows": [
            [f"{event.source_file}:{event.line_number}", event.text]
            for event in representative_events
        ],
    }
    parameter_slots_table: dict[str, object] = {
        "headers": ["Slot", "Type", "Example values"],
        "rows": [
            [slot.slot_id, slot.label, ", ".join(slot.examples) or "No samples"]
            for slot in parameter_slots
        ],
    }
    return {
        "text": text,
        "template": template,
        "template_html": _render_template_html(template, parameter_slots),
        "representative_events_table": representative_events_table,
        "parameter_slots_table": parameter_slots_table,
        "parameter_slots": [slot.model_dump(mode="json") for slot in parameter_slots],
    }


def _render_template_html(template: str, parameter_slots: list[ParameterSlot]) -> str:
    parts: list[str] = []
    cursor = 0
    matches = list(_PLACEHOLDER.finditer(template))
    for index, match in enumerate(matches):
        parts.append(html.escape(template[cursor : match.start()]))
        if index < len(parameter_slots):
            slot = parameter_slots[index]
            parts.append(
                f"<mark><code>{html.escape(slot.slot_id)} · {html.escape(slot.label)}</code></mark>"
            )
        else:
            parts.append(html.escape(match.group(0)))
        cursor = match.end()
    parts.append(html.escape(template[cursor:]))
    return "".join(parts)


def example_slot_spans(
    template: str, representative_events: list[SourceEvent], parameter_slots: list[ParameterSlot]
) -> list[list[SlotSpan]]:
    """Locate existing template captures in examples; never infer a new extraction."""
    return _example_template_spans(template, representative_events, parameter_slots)[0]


def example_literal_spans(
    template: str, representative_events: list[SourceEvent], parameter_slots: list[ParameterSlot]
) -> list[list[LiteralSpan]]:
    """Locate fixed template text using unambiguous, code-point example offsets."""
    return _example_template_spans(template, representative_events, parameter_slots)[1]


def _example_template_spans(
    template: str, representative_events: list[SourceEvent], parameter_slots: list[ParameterSlot]
) -> tuple[list[list[SlotSpan]], list[list[LiteralSpan]]]:
    matches = list(_PLACEHOLDER.finditer(template))
    if len(matches) != len(parameter_slots) or any(
        left.end() == right.start() for left, right in zip(matches, matches[1:])
    ):
        return ([[] for _ in representative_events], [[] for _ in representative_events])
    parts = []
    greedy_parts = []
    cursor = 0
    for index, match in enumerate(matches):
        literal = re.escape(template[cursor : match.start()])
        parts.extend([literal, f"(?P<slot_{index}>.+?)"])
        greedy_parts.extend([literal, f"(?P<slot_{index}>.+)"])
        cursor = match.end()
    parts.append(re.escape(template[cursor:]))
    greedy_parts.append(re.escape(template[cursor:]))
    pattern = re.compile("".join(parts), re.DOTALL)
    greedy_pattern = re.compile("".join(greedy_parts), re.DOTALL)
    slot_results: list[list[SlotSpan]] = []
    literal_results: list[list[LiteralSpan]] = []
    for event in representative_events:
        found = pattern.fullmatch(event.text)
        greedy_found = greedy_pattern.fullmatch(event.text) if found else None
        # Lazy and greedy searches give the first and last possible capture
        # allocations. Different boundaries mean the text cannot be classified
        # as a literal or a particular slot with confidence.
        if found is None or greedy_found is None or found.regs != greedy_found.regs:
            slot_results.append([])
            literal_results.append([])
            continue
        slots = [
            SlotSpan(
                slot_id=slot.slot_id,
                start=found.start(f"slot_{index}"),
                end=found.end(f"slot_{index}"),
                text=found.group(f"slot_{index}"),
            )
            for index, slot in enumerate(parameter_slots)
        ]
        literals = []
        cursor = 0
        for slot in slots:
            if cursor < slot["start"]:
                literals.append(
                    LiteralSpan(
                        start=cursor, end=slot["start"], text=event.text[cursor : slot["start"]]
                    )
                )
            cursor = slot["end"]
        if cursor < len(event.text):
            literals.append(
                LiteralSpan(start=cursor, end=len(event.text), text=event.text[cursor:])
            )
        slot_results.append(slots)
        literal_results.append(literals)
    return slot_results, literal_results


def _potato_config() -> dict[str, object]:
    annotation_instructions = (
        "<p><strong>Goal:</strong> decide whether this cluster is coherent enough "
        "to become an ASIM parser candidate.</p>"
        "<ol>"
        "<li><strong>Inspect the evidence.</strong> Compare the template, example events, "
        "and parameter slots.</li>"
        "<li><strong>Choose a decision.</strong> Use <code>approved</code> only when the "
        "events belong together and the slots are meaningful. Use <code>needs_split</code> "
        "for mixed patterns, <code>rejected</code> for an unusable cluster, or "
        "<code>insufficient_evidence</code> when the samples are not enough.</li>"
        "<li><strong>Add review notes</strong> when you split or reject a cluster, "
        "identify missing evidence, or encounter ambiguity.</li>"
        "</ol>"
        "<p><strong>Not part of this review:</strong> vendor/product metadata, ASIM "
        "schema selection, field mappings, and parser generation are handled in later "
        "submission and engineering stages.</p>"
    )
    return {
        "annotation_task_name": "ASIM Forge cluster review",
        "task_dir": ".",
        "data_files": ["items.jsonl"],
        "item_properties": {"id_key": "id", "text_key": "text"},
        "instance_display": {
            "fields": [
                {
                    "key": "template_html",
                    "type": "html",
                    "label": "Template pattern",
                },
                {
                    "key": "representative_events_table",
                    "type": "spreadsheet",
                    "label": "Representative events",
                    "display_options": {
                        "selectable": False,
                        "compact": True,
                        "border_style": "rounded",
                        "header_style": "light",
                        "max_height": 320,
                    },
                },
                {
                    "key": "parameter_slots_table",
                    "type": "spreadsheet",
                    "label": "Extracted parameter slots",
                    "display_options": {
                        "selectable": False,
                        "compact": True,
                        "border_style": "rounded",
                        "header_style": "light",
                        "max_height": 240,
                    },
                },
            ],
            "layout": {"direction": "vertical", "gap": "16px"},
        },
        "output_annotation_dir": "annotation_output/",
        "export_annotation_format": "jsonl",
        "require_password": False,
        "user_config": {"allow_all_users": True, "users": []},
        "login": {"type": "open"},
        "annotation_instructions": annotation_instructions,
        "annotation_schemes": [
            {
                "annotation_type": "radio",
                "name": "cluster_decision",
                "description": "Does this cluster represent a coherent event pattern?",
                "labels": ["approved", "needs_split", "rejected", "insufficient_evidence"],
                "sequential_key_binding": True,
            },
            {
                "annotation_type": "text",
                "name": "review_notes",
                "description": "Explain corrections, ambiguities, or rejection reasons.",
                "multiline": True,
                "rows": 4,
                "cols": 100,
            },
        ],
    }


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def write_mapping_potato_bundle(
    tasks: list[MappingReviewTask],
    catalog: AsimCatalog,
    output_dir: Path,
) -> None:
    """Use Potato's task-layout extension and its ordinary annotation storage."""
    from .mapping_review import (
        configured_mapping_defaults,
        configured_supplied_mappings,
        initial_mapping_draft,
    )

    bundle = output_dir / "potato"
    bundle.mkdir()
    fields = {
        name: [field.model_dump(mode="json") for field in catalog.fields_for_schema(name)]
        for name in ("Authentication", "NetworkSession", "AuditEvent")
        if name in catalog.manifest.schemas
    }
    items = []
    for task in sorted(tasks, key=lambda task: (not task.cluster_notes, task.source_task.case_id)):
        source = task.source_task.input
        items.append(
            {
                "id": task.source_task.case_id,
                **source_evidence(
                    source.template, source.representative_events, source.parameter_slots
                ),
                "mapping_task": task.model_dump(mode="json"),
                "initial_draft": initial_mapping_draft(task, catalog).model_dump(mode="json"),
                "mapping_defaults_by_schema": {
                    schema: [
                        row.model_dump(mode="json")
                        for row in configured_mapping_defaults(task, catalog, schema)
                    ]
                    for schema in (task.setup.schema_versions if task.setup else [])
                },
                "supplied_mappings_by_schema": {
                    schema: [
                        row.model_dump(mode="json")
                        for row in configured_supplied_mappings(task, catalog, schema)
                    ]
                    for schema in (task.setup.schema_versions if task.setup else [])
                },
                "catalogue_fields": fields,
                "example_slot_spans": example_slot_spans(
                    source.template, source.representative_events, source.parameter_slots
                ),
                "example_literal_spans": example_literal_spans(
                    source.template, source.representative_events, source.parameter_slots
                ),
            }
        )
    _write_jsonl(bundle / "items.jsonl", items)
    config = _potato_config()
    config.update(
        {
            "annotation_task_name": "LogLathe ASIM mapping review",
            "host": "127.0.0.1",
            "task_layout": "mapping-layout.html",
            "annotation_instructions": (
                "<p>Review suggested ASIM mappings against the source evidence. "
                "Correct fields and conversions, add missing mappings, or flag needed extraction. "
                "Reference output is implementation-derived evidence and can be wrong. "
                "Mapping approval is separate from parser validation.</p>"
            ),
            "annotation_schemes": [
                {
                    "annotation_type": "text",
                    "name": "mapping_review",
                    "description": "Assisted mapping decision",
                    "multiline": True,
                }
            ],
        }
    )
    (bundle / "config.yaml").write_text(
        yaml.safe_dump(config, sort_keys=False), encoding="utf-8", newline="\n"
    )
    layout = Path(__file__).with_name("mapping_review.html").read_text(encoding="utf-8")
    (bundle / "mapping-layout.html").write_text(layout, encoding="utf-8", newline="\n")
