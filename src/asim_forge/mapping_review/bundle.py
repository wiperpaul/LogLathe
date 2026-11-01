"""Prepare and reload immutable assisted-review bundles for Potato."""

from __future__ import annotations

import shutil
from pathlib import Path

from ..catalog import load_catalog
from ..models import AsimCatalog
from ..reference.evidence import reference_review_evidence
from ..reviews import ReviewError, load_review_decisions
from ..schema_ranking.jev_experiment import SchemaExperimentInput
from ..schema_ranking.review import load_recorded_schema_suggestions
from ..semantic_annotation.artifacts import load_semantic_annotation_queue
from ..semantic_mapping.approaches import build_approach
from ..semantic_mapping.contracts import MappingRequest
from .contracts import (
    MappingReviewManifest,
    MappingReviewSetup,
    MappingReviewTask,
    _file_hash,
    _fingerprint,
    _setup_payload,
)
from .drafts import configured_mapping_defaults

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
    schema_report: Path | None = None,
) -> MappingReviewManifest:
    """Freeze suggestions separately from labels and write an ordinary Potato task."""
    from ..potato_bundle import write_mapping_potato_bundle

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
    schema_suggestions = (
        load_recorded_schema_suggestions(
            schema_report,
            [SchemaExperimentInput(case_id=task.case_id, source=task.input) for task in tasks],
            queue.catalogue_revision,
        )
        if schema_report is not None
        else {}
    )
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
            "setup": _setup_payload(setup),
        }
        if task.case_id in schema_suggestions:
            payload["schema_suggestion"] = schema_suggestions[task.case_id].model_dump(mode="json")
        review = MappingReviewTask.model_validate(
            {**payload, "review_revision": _fingerprint(payload)}
        )
        for schema in setup.schema_versions:
            configured_mapping_defaults(review, catalog, schema)
        reviews.append(review)
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
        for schema in review.setup.schema_versions if review.setup else []:
            configured_mapping_defaults(review, catalog, schema)
    return tasks, catalog
