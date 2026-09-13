"""Normalize approved decisions from JSONL or Potato's live user state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .models import FieldMapping, ReviewDecision


class ReviewError(ValueError):
    """Raised when human review data cannot be safely normalized."""


def load_review_decisions(path: Path) -> list[ReviewDecision]:
    if not path.is_file():
        raise ReviewError(f"Review file does not exist: {path}")
    if path.suffix.casefold() == ".jsonl":
        return _load_canonical_jsonl(path)

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ReviewError(f"Invalid JSON in {path}: {error}") from error
    if not isinstance(data, dict) or "instance_id_to_label_to_value" not in data:
        raise ReviewError(
            "JSON review input must be a Potato user_state.json; "
            "use JSONL for canonical ReviewDecision records"
        )
    return _load_potato_state(data)


def _load_canonical_jsonl(path: Path) -> list[ReviewDecision]:
    decisions: list[ReviewDecision] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            decisions.append(ReviewDecision.model_validate_json(line))
        except (ValidationError, ValueError) as error:
            raise ReviewError(f"Invalid review on line {line_number} of {path}: {error}") from error
    if not decisions:
        raise ReviewError(f"No review decisions found in {path}")
    return decisions


def _load_potato_state(data: dict[str, Any]) -> list[ReviewDecision]:
    reviewer = str(data.get("user_id") or "unknown-reviewer")
    annotations = data.get("instance_id_to_label_to_value")
    if not isinstance(annotations, dict):
        raise ReviewError("Potato user state has no annotation mapping")

    decisions: list[ReviewDecision] = []
    for cluster_id, raw_values in annotations.items():
        values = _normalize_potato_values(raw_values)
        if values is None:
            continue
        status = _extract_choice(values.get("cluster_decision"))
        if status is None:
            continue
        schema_name = _extract_choice(values.get("asim_schema"))
        notes = _extract_text(values.get("review_notes")) or ""
        raw_spec = _extract_text(values.get("parser_spec"))
        spec: dict[str, Any] = {}
        if status == "approved" and raw_spec:
            try:
                parsed_spec = json.loads(raw_spec)
            except json.JSONDecodeError as error:
                raise ReviewError(f"Invalid parser_spec JSON for {cluster_id}: {error}") from error
            if not isinstance(parsed_spec, dict):
                raise ReviewError(f"parser_spec for {cluster_id} must be a JSON object")
            if _parser_spec_is_complete(parsed_spec):
                spec = parsed_spec

        payload = {
            "cluster_id": str(cluster_id),
            "reviewer": reviewer,
            "status": status,
            "schema_name": schema_name,
            "notes": notes,
            **spec,
        }
        try:
            decisions.append(ReviewDecision.model_validate(payload))
        except ValidationError as error:
            raise ReviewError(f"Invalid Potato review for {cluster_id}: {error}") from error

    if not decisions:
        raise ReviewError("Potato user state contains no completed cluster decisions")
    return decisions


def load_potato_annotations(path: Path) -> tuple[str, dict[str, dict[str, Any]]]:
    """Read Potato's stored values for task-specific engineering review adapters."""
    data = json.loads(path.read_bytes())
    if not isinstance(data, dict) or not isinstance(
        data.get("instance_id_to_label_to_value"), dict
    ):
        raise ReviewError("Expected a Potato user_state.json with annotation values")
    reviewer = data.get("user_id")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ReviewError("Potato mapping review must identify its reviewer")
    annotations = {}
    for case_id, raw in data["instance_id_to_label_to_value"].items():
        normalized = _normalize_potato_values(raw)
        if normalized is not None:
            annotations[str(case_id)] = normalized
    return reviewer, annotations


def _normalize_potato_values(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, list):
        return None

    normalized: dict[str, Any] = {}
    for entry in value:
        if not isinstance(entry, list) or len(entry) != 2:
            continue
        identifier, stored_value = entry
        if not isinstance(identifier, dict):
            continue
        schema = identifier.get("schema")
        name = identifier.get("name")
        if not isinstance(schema, str) or not isinstance(name, str):
            continue
        if name == "text_box":
            normalized[schema] = {"text": str(stored_value)}
        else:
            schema_value = normalized.setdefault(schema, {"labels": {}})
            labels = schema_value.setdefault("labels", {})
            labels[name] = stored_value is not None and stored_value is not False
    return normalized


def _parser_spec_is_complete(spec: dict[str, Any]) -> bool:
    required_strings = ("parser_name", "vendor", "product")
    if any(not isinstance(spec.get(key), str) or not spec[key].strip() for key in required_strings):
        return False
    mappings = spec.get("field_mappings")
    if not isinstance(mappings, list):
        return False
    for mapping in mappings:
        try:
            FieldMapping.model_validate(mapping)
        except ValidationError:
            return False
    return True


def _extract_choice(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return None
    labels = value.get("labels")
    if isinstance(labels, dict):
        selected = [str(label) for label, enabled in labels.items() if enabled]
        if len(selected) > 1:
            raise ReviewError(f"Expected one selected label, received {selected}")
        return selected[0] if selected else None
    scalar = value.get("value")
    return str(scalar) if scalar is not None else None


def _extract_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return None
    for key in ("text", "value", "content"):
        text = value.get(key)
        if isinstance(text, str):
            return text
    return None
