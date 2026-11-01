"""Validate reviewer drafts and adapt saved Potato state to the existing compiler."""

from __future__ import annotations

from pathlib import Path
from typing import cast, get_args

from ..models import (
    AsimCatalog,
    AsimSchema,
    FieldMapping,
    MappingReviewProvenance,
    ReviewDecision,
    order_field_mappings,
)
from ..reviews import ReviewError, _extract_text, load_potato_annotations
from .bundle import load_mapping_review
from .contracts import MappingReviewDraft, MappingReviewTask, _file_hash
from .drafts import (
    _constant_value,
    _managed_fields,
    configured_supplied_mappings,
    required_mapping_fields,
)


def validate_mapping_draft(
    draft: MappingReviewDraft, task: MappingReviewTask, catalog: AsimCatalog
) -> list[FieldMapping]:
    if draft.cluster_status != "approved":
        raise ReviewError("Resolve the cluster review before approving its mappings")
    if draft.schema_name not in get_args(AsimSchema):
        raise ReviewError("Choose a supported ASIM schema before approving the mapping")
    if task.setup is not None:
        if draft.schema_name not in task.setup.schema_versions:
            raise ReviewError("The selected schema has no version configured in review setup")
        if not draft.schema_confirmed:
            raise ReviewError("Assess and confirm one ASIM schema before approving its mappings")
        metadata = task.source_task.input.source_metadata
        if any(
            getattr(draft, key) != getattr(metadata, key)
            for key in ("vendor", "product", "source_table", "message_field")
        ):
            raise ReviewError("Source metadata is fixed by setup and cannot be changed in a review")
    supplied = configured_supplied_mappings(task, catalog, draft.schema_name)
    if not draft.rows and not supplied:
        raise ReviewError("An approved mapping must contain at least one field")
    for row in draft.rows:
        if row.asim_field in _managed_fields(task):
            raise ReviewError(
                f"{row.asim_field} is supplied by schema selection, setup, or ingestion"
            )
    fields = {field.name: field for field in catalog.fields_for_schema(draft.schema_name)}
    required = {
        field.name
        for field in required_mapping_fields(task, catalog, draft.schema_name, draft.rows)
    }
    from ..potato_bundle import example_literal_spans, example_slot_spans

    source_input = task.source_task.input
    slots = {slot.slot_id for slot in source_input.parameter_slots}
    captures = example_slot_spans(
        source_input.template, source_input.representative_events, source_input.parameter_slots
    )
    literals = example_literal_spans(
        source_input.template, source_input.representative_events, source_input.parameter_slots
    )
    mappings = []
    for row in [*draft.rows, *supplied]:
        if row.source_span is not None:
            span = row.source_span
            if span.example_index >= len(source_input.representative_events):
                raise ReviewError("Selected source span refers to an unknown example")
            example = source_input.representative_events[span.example_index].text
            if (
                span.end > len(example)
                or span.start >= span.end
                or example[span.start : span.end] != span.text
            ):
                raise ReviewError("Selected source span does not match the frozen example")
            if row.source_kind == "constant":
                if not any(
                    literal["start"] <= span.start and span.end <= literal["end"]
                    for literal in literals[span.example_index]
                ):
                    raise ReviewError(
                        "Selected constant evidence is not within verified fixed template text"
                    )
                if not row.constant_value.strip():
                    raise ReviewError(
                        "Choose an output value for the selected fixed template evidence"
                    )
            elif row.source_kind == "source_field":
                raise ReviewError("Source-table mappings cannot use event text span evidence")
            elif row.source_kind == "output_field":
                raise ReviewError("ASIM output-field mappings cannot use event text span evidence")
            elif not any(
                capture["slot_id"] == row.locator
                and capture["start"] == span.start
                and capture["end"] == span.end
                for capture in captures[span.example_index]
            ):
                raise ReviewError(
                    "Selected source span is not an extracted slot; mark needs extraction"
                )
        field = fields.get(row.asim_field)
        if field is None or field.kql_type != row.transform:
            raise ReviewError(f"Unknown target or incompatible conversion: {row.asim_field}")
        value = row.constant_value if row.source_kind == "constant" else row.locator
        if row.asim_field in required and not value.strip():
            raise ReviewError(f"Fill the required mapping for {row.asim_field}")
        source = {}
        if row.source_kind == "slot":
            if row.locator not in slots:
                raise ReviewError(f"Unknown extracted slot: {row.locator}")
            source["slot_id"] = row.locator
        elif row.source_kind == "source_field":
            ingestion_time = (
                task.setup is not None
                and task.setup.time_generated == "ingestion"
                and row.locator == "TimeGenerated"
            )
            if (
                task.reference is not None
                and row.locator not in task.reference.source_columns
                and not ingestion_time
            ):
                raise ReviewError(f"Unknown source-table field: {row.locator}")
            source["source_field"] = row.locator
        elif row.source_kind == "output_field":
            dependency = next(
                (
                    item
                    for name, item in fields.items()
                    if name.casefold() == row.locator.casefold()
                ),
                None,
            )
            if dependency is None or dependency.kql_type != row.transform:
                raise ReviewError(f"Unknown or incompatible ASIM output field: {row.locator}")
            source["output_field"] = dependency.name
        else:
            value = _constant_value(row)
            if field.allowed_values and str(value) not in field.allowed_values:
                raise ReviewError(f"Constant is outside the catalogue values for {row.asim_field}")
            source["constant_value"] = value
        mappings.append(
            FieldMapping.model_validate(
                {**source, "asim_field": row.asim_field, "transform": row.transform}
            )
        )
    targets = [mapping.asim_field.casefold() for mapping in mappings]
    assigned_slots = [mapping.slot_id for mapping in mappings if mapping.slot_id is not None]
    if len(targets) != len(set(targets)) or len(assigned_slots) != len(set(assigned_slots)):
        raise ReviewError("Resolve duplicate target fields or slot assignments before approval")
    missing = required - {row.asim_field for row in draft.rows}
    if missing:
        raise ReviewError("Fill the required mappings: " + ", ".join(sorted(missing)))
    try:
        order_field_mappings(mappings)
    except ValueError as exc:
        raise ReviewError(str(exc)) from exc
    return mappings


def load_mapping_decisions(bundle_dir: Path, state_path: Path) -> list[ReviewDecision]:
    tasks, catalog = load_mapping_review(bundle_dir)
    by_id = {task.source_task.case_id: task for task in tasks}
    state_hash = _file_hash(state_path)
    reviewer, annotations = load_potato_annotations(state_path)
    if _file_hash(state_path) != state_hash:
        raise ReviewError(
            "Potato saved new review data during compilation; retry with the saved state"
        )
    decisions = []
    for case_id, values in annotations.items():
        raw = _extract_text(values.get("mapping_review"))
        if not raw:
            continue
        if case_id not in by_id:
            raise ReviewError(f"Mapping decision references an unknown task: {case_id}")
        task = by_id[case_id]
        draft = MappingReviewDraft.model_validate_json(raw)
        if draft.review_revision != task.review_revision:
            raise ReviewError(
                "Mapping decision refers to a stale suggestion or different source evidence"
            )
        provenance = MappingReviewProvenance(
            status=draft.status,
            task_revision=task.source_task.task_revision,
            review_revision=task.review_revision,
            cluster_file_sha256=task.source_task.provenance.cluster_file_sha256,
            catalogue_revision=task.source_task.catalogue_revision,
            review_file_sha256=state_hash,
            time_generated=task.setup.time_generated if task.setup else "map",
        )
        payload = {
            "cluster_id": case_id,
            "reviewer": reviewer,
            "status": draft.cluster_status,
            "notes": "\n\n".join(
                note
                for note in (
                    f"Cluster review: {draft.cluster_notes}" if draft.cluster_notes else "",
                    draft.notes,
                )
                if note
            ),
            "mapping_review": provenance,
        }
        if draft.cluster_status == "approved" and draft.status == "approved":
            mappings = validate_mapping_draft(draft, task, catalog)
            payload.update(
                schema_name=draft.schema_name,
                schema_version=task.setup.schema_versions[cast(AsimSchema, draft.schema_name)]
                if task.setup
                else None,
                parser_name=draft.parser_name,
                vendor=draft.vendor,
                product=draft.product,
                source_table=draft.source_table,
                message_field=draft.message_field,
                field_mappings=mappings,
            )
        decisions.append(ReviewDecision.model_validate(payload))
    if not decisions:
        raise ReviewError("Potato contains no mapping review drafts or decisions")
    return decisions
