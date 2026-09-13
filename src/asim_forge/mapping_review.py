"""Assisted engineering review through Potato, using the existing frozen queue."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, cast, get_args

from pydantic import Field, model_validator

from .catalog import load_catalog
from .models import (
    AsimCatalog,
    AsimCatalogField,
    AsimSchema,
    FieldMapping,
    MappingReviewProvenance,
    MappingReviewStatus,
    ReviewDecision,
    ReviewStatus,
    StrictModel,
    TimeGeneratedMode,
    Transform,
)
from .reference.evidence import ReferenceReviewEvidence, reference_review_evidence
from .reviews import ReviewError, _extract_text, load_potato_annotations, load_review_decisions
from .semantic_annotation.artifacts import load_semantic_annotation_queue
from .semantic_annotation.contracts import SemanticAnnotationTask
from .semantic_mapping.approaches import build_approach
from .semantic_mapping.contracts import MappingRequest, SemanticMappingPrediction


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class MappingReviewSetup(StrictModel):
    """Target versions and ingestion assumptions supplied before human mapping review."""

    schema_versions: dict[
        AsimSchema, Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]
    ] = Field(min_length=1)
    time_generated: TimeGeneratedMode = "map"


def load_mapping_setup(path: Path) -> MappingReviewSetup:
    return MappingReviewSetup.model_validate_json(path.read_bytes())


class MappingReviewTask(StrictModel):
    mode: Literal["assisted-engineering"] = "assisted-engineering"
    source_task: SemanticAnnotationTask
    prediction: SemanticMappingPrediction
    schema_predictions: dict[AsimSchema, SemanticMappingPrediction] = Field(default_factory=dict)
    catalogue_sha256: str
    reference: ReferenceReviewEvidence | None = None
    cluster_notes: str = ""
    setup: MappingReviewSetup | None = None
    review_revision: str

    @model_validator(mode="after")
    def revision_matches(self) -> MappingReviewTask:
        excluded = {"review_revision"}
        if self.setup is None:
            excluded.add("setup")  # Keep already prepared bundles readable.
        if not self.schema_predictions:
            excluded.add("schema_predictions")  # Keep earlier frozen bundles readable.
        if self.review_revision != _fingerprint(self.model_dump(mode="json", exclude=excluded)):
            raise ValueError(
                "Mapping review revision does not match the frozen evidence and suggestion"
            )
        if (
            self.prediction.case_id != self.source_task.case_id
            or self.prediction.catalogue_revision != self.source_task.catalogue_revision
        ):
            raise ValueError("Suggestion and source task must identify the same case and catalogue")
        if self.prediction.ranked_schemas:
            top_schema = self.prediction.ranked_schemas[0].schema_name
            if (
                top_schema in self.schema_predictions
                and self.schema_predictions[top_schema] != self.prediction
            ):
                raise ValueError("Top-ranked field suggestions must match the original prediction")
        for schema, prediction in self.schema_predictions.items():
            if self.setup is not None and schema not in self.setup.schema_versions:
                raise ValueError("Field suggestions require a schema available in setup")
            if (
                prediction.case_id != self.source_task.case_id
                or prediction.catalogue_revision != self.source_task.catalogue_revision
                or prediction.approach != self.prediction.approach
            ):
                raise ValueError(
                    "Schema field suggestions must match the frozen source and approach"
                )
        return self


class SourceSpan(StrictModel):
    """Reviewer-selected offsets within one frozen representative event."""

    example_index: int = Field(ge=0)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1)


class MappingRow(StrictModel):
    source_kind: Literal["slot", "source_field", "constant"] = "slot"
    locator: str = ""
    role: str = ""
    asim_field: str = ""
    transform: Transform = "string"
    constant_value: str = ""
    source_span: SourceSpan | None = None


class MappingReviewDraft(StrictModel):
    format_version: Literal["1"] = "1"
    mode: Literal["assisted-engineering"] = "assisted-engineering"
    review_revision: str
    status: MappingReviewStatus = "in_progress"
    # The frozen queue contains approved clusters. A later engineering review
    # may reopen that decision without mutating the original review snapshot.
    cluster_status: ReviewStatus = "approved"
    cluster_notes: str = ""
    schema_name: str = ""
    schema_confirmed: bool = False
    parser_name: str = ""
    vendor: str = ""
    product: str = ""
    source_table: str = "Syslog"
    message_field: str = "SyslogMessage"
    rows: list[MappingRow] = Field(default_factory=list)
    # Inactive schema drafts are retained for switching back; only rows compile.
    schema_rows: dict[AsimSchema, list[MappingRow]] = Field(default_factory=dict)
    notes: str = ""


class MappingReviewManifest(StrictModel):
    format_version: Literal["1"] = "1"
    mode: Literal["assisted-engineering"] = "assisted-engineering"
    task_count: int = Field(ge=1)
    files: dict[str, str]


_BUNDLE_FILES = (
    "queue/queue-manifest.json",
    "queue/tasks.jsonl",
    "queue/submission-schema.json",
    "catalog/catalog-manifest.json",
    "catalog/asim-catalog.csv",
    "suggestions.jsonl",
    "potato/items.jsonl",
    "potato/config.yaml",
    "potato/mapping-layout.html",
)


def _managed_fields(task: MappingReviewTask) -> set[str]:
    names = {"EventSchema", "EventVendor", "EventProduct"}
    if task.setup is not None:
        names.update({"EventSchemaVersion", "Type"})
        if task.setup.time_generated != "map":
            names.add("TimeGenerated")
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
    if task.setup is not None and schema not in task.setup.schema_versions:
        schema = ""
    suggested_schema = prediction.ranked_schemas[0].schema_name if prediction.ranked_schemas else ""
    available = task.setup.schema_versions if task.setup else ([schema] if schema else [])
    schema_rows = {
        name: _suggested_rows(
            task,
            catalog,
            name,
            task.schema_predictions.get(name) or (prediction if name == suggested_schema else None),
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


def prepare_mapping_review(
    queue_dir: Path,
    catalog_dir: Path,
    output_dir: Path,
    *,
    approach_name: str = "semantic-frame",
    fixture_dir: Path | None = None,
    reference_bundle: Path | None = None,
    reference_capture: Path | None = None,
    cluster_reviews: Path | None = None,
    setup: MappingReviewSetup | None = None,
) -> MappingReviewManifest:
    """Freeze suggestions separately from labels and write an ordinary Potato task."""
    from .potato_bundle import write_mapping_potato_bundle

    if output_dir.exists() and any(output_dir.iterdir()):
        raise ReviewError("Use an empty mapping review directory to preserve existing work")
    queue, tasks = load_semantic_annotation_queue(queue_dir)
    catalog = load_catalog(catalog_dir)
    if queue.catalogue_revision != catalog.manifest.resolved_revision:
        raise ReviewError("Mapping review catalogue differs from the annotation queue")
    if approach_name not in ("semantic-frame", "direct-lexical", "matcher-ensemble"):
        raise ReviewError("Assisted review requires an event-reading mapping approach")
    notes = {}
    if cluster_reviews is not None:
        if _file_hash(cluster_reviews) != queue.review_file_sha256:
            raise ReviewError("Cluster-review notes must come from the queue's exact review state")
        notes = {
            decision.cluster_id: decision.notes
            for decision in load_review_decisions(cluster_reviews)
        }
    references = {}
    if any(value is not None for value in (fixture_dir, reference_bundle, reference_capture)):
        if fixture_dir is None or reference_bundle is None or reference_capture is None:
            raise ReviewError(
                "Reference evidence requires the fixture, prepared bundle, and native capture"
            )
        references = reference_review_evidence(
            fixture_dir, reference_bundle, reference_capture, tasks, catalog
        )
    if setup is None:
        if not references:
            raise ReviewError("Mapping review requires --setup with available schema_versions")
        reference = next(iter(references.values()))
        setup = MappingReviewSetup.model_validate(
            {"schema_versions": {reference.schema_name: reference.schema_version}}
        )
    for name in setup.schema_versions:
        if name not in catalog.manifest.schemas:
            raise ReviewError(f"Setup schema is absent from the pinned catalogue: {name}")
    for task in tasks:
        metadata = task.input.source_metadata
        if any(
            getattr(metadata, key) is None
            for key in ("vendor", "product", "source_table", "message_field")
        ):
            raise ReviewError(
                "Set vendor, product, source table, and message field "
                "when preparing the source queue"
            )
    if setup.time_generated == "source" and references:
        if any(
            reference.source_columns.get("TimeGenerated") != "datetime"
            for reference in references.values()
        ):
            raise ReviewError("Setup expects a datetime TimeGenerated column in the source table")
    approach = build_approach(approach_name)
    reviews = []
    for task in tasks:
        request = MappingRequest(
            case_id=task.case_id, catalogue_revision=task.catalogue_revision, input=task.input
        )
        prediction = approach.predict(request, catalog)
        suggested_schema = (
            prediction.ranked_schemas[0].schema_name if prediction.ranked_schemas else ""
        )
        schema_predictions = {
            name: prediction
            if name == suggested_schema
            else approach.predict(request.model_copy(update={"review_schema": name}), catalog)
            for name in setup.schema_versions
        }
        reference = references.get(task.case_id)
        payload = {
            "mode": "assisted-engineering",
            "source_task": task.model_dump(mode="json"),
            "prediction": prediction.model_dump(mode="json"),
            "schema_predictions": {
                name: suggestion.model_dump(mode="json")
                for name, suggestion in schema_predictions.items()
            },
            "catalogue_sha256": catalog.manifest.content_sha256,
            "reference": reference.model_dump(mode="json") if reference else None,
            "cluster_notes": notes.get(task.case_id, ""),
            "setup": setup.model_dump(mode="json"),
        }
        reviews.append(
            MappingReviewTask.model_validate({**payload, "review_revision": _fingerprint(payload)})
        )
    (output_dir / "queue").mkdir(parents=True)
    (output_dir / "catalog").mkdir()
    for name in ("queue-manifest.json", "tasks.jsonl", "submission-schema.json"):
        shutil.copyfile(queue_dir / name, output_dir / "queue" / name)
    for name in ("catalog-manifest.json", "asim-catalog.csv"):
        shutil.copyfile(catalog_dir / name, output_dir / "catalog" / name)
    (output_dir / "suggestions.jsonl").write_text(
        "".join(task.model_dump_json() + "\n" for task in reviews),
        encoding="utf-8",
        newline="\n",
    )
    write_mapping_potato_bundle(reviews, catalog, output_dir)
    manifest = MappingReviewManifest(
        task_count=len(reviews),
        files={name: _file_hash(output_dir / name) for name in _BUNDLE_FILES},
    )
    (output_dir / "review-manifest.json").write_text(
        manifest.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return manifest


def load_mapping_review(bundle_dir: Path) -> tuple[list[MappingReviewTask], AsimCatalog]:
    manifest = MappingReviewManifest.model_validate_json(
        (bundle_dir / "review-manifest.json").read_bytes()
    )
    if set(manifest.files) != set(_BUNDLE_FILES):
        raise ReviewError("Mapping review manifest has missing or unexpected files")
    for name, fingerprint in manifest.files.items():
        if _file_hash(bundle_dir / name) != fingerprint:
            raise ReviewError(f"Mapping review evidence changed: {name}")
    _, source_tasks = load_semantic_annotation_queue(bundle_dir / "queue")
    catalog = load_catalog(bundle_dir / "catalog")
    tasks = [
        MappingReviewTask.model_validate_json(line)
        for line in (bundle_dir / "suggestions.jsonl").read_bytes().splitlines()
        if line.strip()
    ]
    by_id = {task.source_task.case_id: task for task in tasks}
    if (
        len(by_id) != len(tasks)
        or len(tasks) != manifest.task_count
        or set(by_id) != {task.case_id for task in source_tasks}
    ):
        raise ReviewError("Mapping review tasks do not match the original queue")
    for task in source_tasks:
        review = by_id[task.case_id]
        if (
            review.source_task != task
            or review.catalogue_sha256 != catalog.manifest.content_sha256
            or task.catalogue_revision != catalog.manifest.resolved_revision
        ):
            raise ReviewError("Mapping review source or catalogue provenance does not match")
        for schema, prediction in review.schema_predictions.items():
            field_names = {field.name for field in catalog.fields_for_schema(schema)}
            if any(mapping.asim_field not in field_names for mapping in prediction.asim_fields):
                raise ReviewError(f"Field suggestions do not belong to {schema}")
    return tasks, catalog


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
    if row.transform == "datetime":
        datetime.fromisoformat(row.constant_value)
    return row.constant_value


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
    if not draft.rows:
        raise ReviewError("An approved mapping must contain at least one field")
    fields = {field.name: field for field in catalog.fields_for_schema(draft.schema_name)}
    required = {
        field.name
        for field in required_mapping_fields(task, catalog, draft.schema_name, draft.rows)
    }
    from .potato_bundle import example_slot_spans

    source_input = task.source_task.input
    slots = {slot.slot_id for slot in source_input.parameter_slots}
    captures = example_slot_spans(
        source_input.template, source_input.representative_events, source_input.parameter_slots
    )
    mappings = []
    for row in draft.rows:
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
            if row.source_kind != "slot" or not any(
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
        if row.asim_field in _managed_fields(task):
            raise ReviewError(
                f"{row.asim_field} is supplied by schema selection, setup, or ingestion"
            )
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
