"""Editable mapping rows from field suggestions, setup mappings, and required fields."""

from __future__ import annotations

import hashlib
import json
import math
from typing import cast, get_args

from ..models import AsimCatalog, AsimCatalogField, AsimSchema, Transform, is_datetime_constant
from ..reviews import ReviewError
from ..semantic_mapping.contracts import SemanticMappingPrediction
from .contracts import MappingReviewDraft, MappingReviewTask, MappingRow


def _managed_fields(task: MappingReviewTask, *, include_supplied: bool = True) -> set[str]:
    names = {"EventSchema", "EventVendor", "EventProduct"}
    if task.setup is not None:
        names.update({"EventSchemaVersion", "Type"})
        if task.setup.time_generated != "map":
            names.add("TimeGenerated")
        if include_supplied:
            names.update(mapping.asim_field for mapping in task.setup.supplied_mappings or [])
    return names


def required_mapping_fields(
    task: MappingReviewTask, catalog: AsimCatalog, schema: str, rows: list[MappingRow]
) -> list[AsimCatalogField]:
    fields = catalog.fields_for_schema(schema) if schema else []
    managed = _managed_fields(task)
    represented = managed | {row.asim_field for row in rows}
    represented.update(field.name for field in fields if field.field_class == "Mandatory")
    return [
        field
        for field in fields
        if field.name not in managed
        and (
            field.field_class == "Mandatory"
            or (field.field_class == "Conditional" and field.aliased_field in represented)
        )
    ]


def configured_mapping_defaults(
    task: MappingReviewTask, catalog: AsimCatalog, schema: str
) -> list[MappingRow]:
    """Return editable defaults, validating both kinds of configured mappings."""
    names = (
        {mapping.asim_field for mapping in task.setup.mapping_defaults or []}
        if task.setup
        else set()
    )
    return [
        row for row in _configured_mapping_rows(task, catalog, schema) if row.asim_field in names
    ]


def configured_supplied_mappings(
    task: MappingReviewTask, catalog: AsimCatalog, schema: str
) -> list[MappingRow]:
    """Return read-only mappings applied from setup during approval and compilation."""
    names = (
        {mapping.asim_field for mapping in task.setup.supplied_mappings or []}
        if task.setup
        else set()
    )
    return [
        row for row in _configured_mapping_rows(task, catalog, schema) if row.asim_field in names
    ]


def _configured_mapping_rows(
    task: MappingReviewTask, catalog: AsimCatalog, schema: str
) -> list[MappingRow]:
    if task.setup is None:
        return []
    fields = {field.name: field for field in catalog.fields_for_schema(schema)}
    available = {
        field.name
        for name in task.setup.schema_versions
        for field in catalog.fields_for_schema(name)
    }
    managed = _managed_fields(task, include_supplied=False)
    supplied_names = {mapping.asim_field for mapping in task.setup.supplied_mappings or []}
    rows = []
    for mapping in (task.setup.mapping_defaults or []) + (task.setup.supplied_mappings or []):
        target = mapping.asim_field
        if target not in available:
            raise ReviewError(
                f"Setup default target is absent from configured ASIM schemas: {target}"
            )
        if target in managed:
            raise ReviewError(
                f"Setup default cannot override automatically supplied field: {target}"
            )
        field = fields.get(target)
        if field is None:
            continue
        if field.kql_type != mapping.transform:
            raise ReviewError(f"Setup default has an incompatible conversion for {schema}.{target}")
        if mapping.source_field is not None:
            locator = mapping.source_field
            source_type = (
                task.reference.source_columns.get(locator) if task.reference is not None else None
            )
            ingestion_time = task.setup.time_generated == "ingestion" and locator == "TimeGenerated"
            if task.reference is not None and source_type is None and not ingestion_time:
                raise ReviewError(
                    f"Setup default {target} uses unknown source-table field: {locator}"
                )
            if (source_type is not None and source_type != mapping.transform) or (
                ingestion_time and mapping.transform != "datetime"
            ):
                raise ReviewError(
                    f"Setup default {target} has incompatible source-column type: {locator}"
                )
            row = MappingRow(
                source_kind="source_field",
                locator=locator,
                asim_field=target,
                transform=mapping.transform,
            )
        elif mapping.output_field is not None:
            dependency = next(
                (
                    item
                    for name, item in fields.items()
                    if name.casefold() == mapping.output_field.casefold()
                ),
                None,
            )
            if dependency is None or dependency.name in managed:
                raise ReviewError(
                    f"Setup default {target} must reference a mapped ASIM output field: "
                    f"{mapping.output_field}"
                )
            if dependency.kql_type != mapping.transform:
                raise ReviewError(
                    f"Setup default {target} has an incompatible output-field type: "
                    f"{dependency.name}"
                )
            row = MappingRow(
                source_kind="output_field",
                locator=dependency.name,
                asim_field=target,
                transform=mapping.transform,
            )
        else:
            value = mapping.constant_value
            row = MappingRow(
                source_kind="constant",
                asim_field=target,
                transform=mapping.transform,
                constant_value=value if isinstance(value, str) else json.dumps(value),
            )
            if (
                target in supplied_names
                and field.field_class in {"Mandatory", "Conditional"}
                and not row.constant_value.strip()
            ):
                raise ReviewError(f"Supplied mapping requires a nonblank value for {target}")
            try:
                converted = _constant_value(row)
            except (ValueError, ReviewError) as exc:
                raise ReviewError(f"Setup default {target} has an invalid constant: {exc}") from exc
            if field.allowed_values and str(converted) not in field.allowed_values:
                raise ReviewError(f"Setup default {target} is outside the catalogue values")
        rows.append(row)
    # References to fields filled later by the reviewer are valid configuration;
    # cycles entirely within configured defaults can already be rejected here.
    dependencies = {
        row.asim_field.casefold(): row.locator.casefold()
        for row in rows
        if row.source_kind == "output_field"
    }
    for target in dependencies:
        seen = set()
        current = target
        while current in dependencies:
            if current in seen:
                raise ReviewError(f"Setup mapping defaults contain an output-field cycle: {target}")
            seen.add(current)
            current = dependencies[current]
    return rows


def _suggested_rows(
    task: MappingReviewTask,
    catalog: AsimCatalog,
    schema: str,
    prediction: SemanticMappingPrediction | None,
) -> list[MappingRow]:
    fields = {field.name: field for field in catalog.fields_for_schema(schema)} if schema else {}
    roles = {
        (item.source_kind, item.locator): item.role
        for item in (prediction.source_semantics if prediction else [])
    }
    rows: list[MappingRow] = []
    for mapping in prediction.asim_fields if prediction else []:
        if mapping.asim_field in _managed_fields(task):
            continue
        field = fields.get(mapping.asim_field)
        if field is None or field.kql_type not in get_args(Transform):
            continue
        if mapping.source_kind == "slot":
            kind = "slot"
        elif mapping.constant_value is not None:
            kind = "constant"
        else:
            continue
        value = mapping.constant_value
        rows.append(
            MappingRow(
                source_kind=kind,
                locator=mapping.locator,
                role=roles.get((mapping.source_kind, mapping.locator), ""),
                asim_field=mapping.asim_field,
                transform=cast(Transform, field.kql_type),
                constant_value=(value if isinstance(value, str) else json.dumps(value))
                if value is not None
                else "",
            )
        )
    represented = {row.asim_field for row in rows}
    for row in configured_mapping_defaults(task, catalog, schema):
        if row.asim_field not in represented:
            rows.append(row)
            represented.add(row.asim_field)
    for field in required_mapping_fields(task, catalog, schema, rows):
        if field.name not in represented:
            rows.append(
                MappingRow(
                    asim_field=field.name,
                    transform=cast(Transform, field.kql_type)
                    if field.kql_type in get_args(Transform)
                    else "string",
                )
            )
    return rows


def initial_mapping_draft(task: MappingReviewTask, catalog: AsimCatalog) -> MappingReviewDraft:
    prediction = task.prediction
    schema = prediction.ranked_schemas[0].schema_name if prediction.ranked_schemas else ""
    if task.schema_suggestion is not None:
        schema = task.schema_suggestion.selected_schema or ""
        if schema not in get_args(AsimSchema):
            schema = ""
    if task.setup is not None and schema not in task.setup.schema_versions:
        schema = ""
    suggested_schema = prediction.ranked_schemas[0].schema_name if prediction.ranked_schemas else ""
    available = task.setup.schema_versions if task.setup else ([schema] if schema else [])
    schema_rows = {
        name: _suggested_rows(
            task,
            catalog,
            name,
            prediction if name == suggested_schema else None,
        )
        for name in available
    }
    metadata = task.source_task.input.source_metadata
    return MappingReviewDraft(
        review_revision=task.review_revision,
        schema_name=schema,
        parser_name="vimReviewed"
        + hashlib.sha256(task.source_task.case_id.encode()).hexdigest()[:12],
        vendor=metadata.vendor or "",
        product=metadata.product or "",
        source_table=metadata.source_table or "Syslog",
        message_field=metadata.message_field or "SyslogMessage",
        rows=schema_rows.get(schema, []),
        schema_rows=schema_rows,
    )


def _constant_value(row: MappingRow) -> str | int | float | bool:
    if row.transform in ("int", "long"):
        value = int(row.constant_value)
        bits = 32 if row.transform == "int" else 64
        if not -(2 ** (bits - 1)) <= value < 2 ** (bits - 1):
            raise ReviewError(f"Integer constant is outside the {row.transform} range")
        return value
    if row.transform == "real":
        value = float(row.constant_value)
        if not math.isfinite(value):
            raise ReviewError("Numeric constants must be finite")
        return value
    if row.transform == "bool":
        if row.constant_value.casefold() not in ("true", "false"):
            raise ReviewError("Boolean constants must be true or false")
        return row.constant_value.casefold() == "true"
    if row.transform == "datetime" and not is_datetime_constant(row.constant_value):
        raise ReviewError(
            "Datetime constants must be ISO dates or timestamps, such as 2026-03-01 or "
            "2026-03-01T12:00:00Z"
        )
    return row.constant_value
