"""Canonical storage helpers for semantic annotation artifacts."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .contracts import (
    QUEUE_MANIFEST_FILE,
    SemanticAnnotationDecision,
    SemanticAnnotationError,
    SemanticAnnotationQueueManifest,
    SemanticAnnotationTask,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


def load_semantic_annotation_queue(
    queue_dir: Path,
) -> tuple[SemanticAnnotationQueueManifest, list[SemanticAnnotationTask]]:
    """Load the existing frozen queue with its task and submission-schema checks."""
    manifest = _load_queue_manifest(queue_dir / QUEUE_MANIFEST_FILE)
    tasks_path = _queue_output_path(queue_dir, manifest, "tasks")
    schema_path = _queue_output_path(queue_dir, manifest, "submission_schema")
    if _sha256_file(tasks_path) != manifest.tasks_sha256:
        raise SemanticAnnotationError("Annotation tasks do not match queue-manifest.json")
    if _sha256_file(schema_path) != manifest.submission_schema_sha256:
        raise SemanticAnnotationError("Submission schema does not match queue-manifest.json")
    tasks = load_semantic_annotation_tasks(tasks_path)
    _validate_queue_contents(tasks, manifest)
    return manifest, tasks


def _load_queue_manifest(path: Path) -> SemanticAnnotationQueueManifest:
    if not path.is_file():
        raise SemanticAnnotationError(f"Queue manifest does not exist: {path}")
    try:
        return SemanticAnnotationQueueManifest.model_validate_json(path.read_bytes())
    except (OSError, ValidationError, ValueError) as error:
        raise SemanticAnnotationError(f"Invalid queue manifest {path}: {error}") from error


def _queue_output_path(
    queue_dir: Path,
    manifest: SemanticAnnotationQueueManifest,
    output: str,
) -> Path:
    relative = manifest.outputs.get(output)
    if relative is None:
        raise SemanticAnnotationError(f"Queue manifest has no {output!r} output")
    relative_path = Path(relative)
    if relative_path.is_absolute():
        raise SemanticAnnotationError(f"Queue output {output!r} must be relative to its queue")
    queue_root = queue_dir.resolve()
    resolved = (queue_root / relative_path).resolve()
    if not resolved.is_relative_to(queue_root):
        raise SemanticAnnotationError(f"Queue output {output!r} escapes its queue directory")
    return resolved


def _validate_queue_contents(
    tasks: list[SemanticAnnotationTask], manifest: SemanticAnnotationQueueManifest
) -> None:
    if len(tasks) != manifest.task_count:
        raise SemanticAnnotationError("Queue task count does not match queue-manifest.json")
    for task in tasks:
        provenance = task.provenance
        if (
            task.protocol_revision != manifest.protocol_revision
            or task.catalogue_revision != manifest.catalogue_revision
            or task.group_id != manifest.group_id
            or task.group_strategy != manifest.group_strategy
            or task.input.source_metadata != manifest.source_metadata
            or provenance.build_manifest_sha256 != manifest.build_manifest_sha256
            or provenance.cluster_file_sha256 != manifest.cluster_file_sha256
            or provenance.review_file_sha256 != manifest.review_file_sha256
        ):
            raise SemanticAnnotationError(
                f"Task {task.case_id!r} does not match queue-manifest.json"
            )


def load_semantic_annotation_tasks(path: Path) -> list[SemanticAnnotationTask]:
    """Load a canonical task JSONL file and reject duplicate case IDs."""
    tasks = _load_jsonl(path, SemanticAnnotationTask, "semantic annotation task")
    _ensure_unique([task.case_id for task in tasks], "Semantic annotation case IDs")
    return tasks


def write_semantic_annotation_tasks(path: Path, tasks: list[SemanticAnnotationTask]) -> None:
    """Write blinded tasks in deterministic case-ID order."""
    if not tasks:
        raise SemanticAnnotationError("At least one semantic annotation task is required")
    _ensure_unique([task.case_id for task in tasks], "Semantic annotation case IDs")
    _write_jsonl(
        path,
        [task.model_dump(mode="json") for task in sorted(tasks, key=lambda item: item.case_id)],
    )


def load_semantic_annotation_decisions(path: Path) -> list[SemanticAnnotationDecision]:
    """Load typed annotation/adjudication JSONL and reject duplicate decision IDs."""
    decisions = _load_jsonl(path, SemanticAnnotationDecision, "semantic annotation decision")
    _ensure_unique([decision.decision_id for decision in decisions], "Annotation decision IDs")
    return decisions


def write_semantic_annotation_decisions(
    path: Path, decisions: list[SemanticAnnotationDecision]
) -> None:
    """Write typed decisions in deterministic decision-ID order."""
    if not decisions:
        raise SemanticAnnotationError("At least one semantic annotation decision is required")
    _ensure_unique([decision.decision_id for decision in decisions], "Annotation decision IDs")
    _write_jsonl(
        path,
        [
            decision.model_dump(mode="json")
            for decision in sorted(decisions, key=lambda item: item.decision_id)
        ],
    )


def _load_jsonl(path: Path, model: type[ModelT], description: str) -> list[ModelT]:
    if not path.is_file():
        raise SemanticAnnotationError(f"{description.capitalize()} file does not exist: {path}")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise SemanticAnnotationError(
            f"Could not read {description} file {path}: {error}"
        ) from error
    records: list[ModelT] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            records.append(model.model_validate_json(line))
        except (ValidationError, ValueError) as error:
            raise SemanticAnnotationError(
                f"Invalid {description} on line {line_number} of {path}: {error}"
            ) from error
    if not records:
        raise SemanticAnnotationError(f"No {description}s found in {path}")
    return records


def _ensure_unique(values: list[str], label: str) -> None:
    counts = Counter(values)
    duplicates = sorted(value for value, count in counts.items() if count > 1)
    if duplicates:
        raise SemanticAnnotationError(f"{label} must be unique: {duplicates}")


def _sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise SemanticAnnotationError(f"Could not hash {path}: {error}") from error


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        for record in records
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _write_json(path: Path, record: dict[str, object]) -> None:
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
