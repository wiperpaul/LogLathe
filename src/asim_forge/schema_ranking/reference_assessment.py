"""Compare schema predictions with verified native output, without creating gold labels."""

from __future__ import annotations

from collections import Counter
from typing import Any

from ..reference.evidence import ReferenceReviewEvidence, ReferenceReviewExample


def _reference_label(example: ReferenceReviewExample) -> tuple[str | None, str]:
    if not example.output.rows:
        return None, "not_selected"
    labels: list[str] = []
    for row in example.output.rows:
        label = row.get("EventSchema")
        if not isinstance(label, str) or not label.strip():
            return None, "missing_schema"
        labels.append(label)
    if len(set(labels)) != 1:
        return None, "mixed_schemas"
    return labels[0], "labelled"


def _prediction(row: dict[str, Any], approach: str) -> dict[str, Any]:
    prediction = row.get(approach)
    if prediction is None:
        return {"available": False, "selected_schema": None, "scores": {}}
    if approach == "baseline":
        scores = {item["schema_name"]: item["score"] for item in prediction["ranked_schemas"]}
    else:
        scores = prediction["response"]["answers"]["schema"]["probabilities"]
    return {"available": True, "selected_schema": prediction["selected_schema"], "scores": scores}


def _comparison(prediction: dict[str, Any], label: str) -> dict[str, Any]:
    scores = prediction["scores"]
    minimum = maximum = None
    if label in scores:
        minimum = 1 + sum(score > scores[label] for score in scores.values())
        maximum = sum(score >= scores[label] for score in scores.values())
    return {
        "available": prediction["available"],
        "selected_schema": prediction["selected_schema"],
        "agrees": prediction["selected_schema"] == label,
        "reference_rank_min": minimum,
        "reference_rank_max": maximum,
    }


def _metrics(comparisons: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(comparisons)
    agreed = sum(item["agrees"] for item in comparisons)
    lower = sum(
        1 / item["reference_rank_max"] if item["reference_rank_max"] else 0 for item in comparisons
    )
    upper = sum(
        1 / item["reference_rank_min"] if item["reference_rank_min"] else 0 for item in comparisons
    )
    return {
        "eligible": count,
        "predictions_available": sum(item["available"] for item in comparisons),
        "no_selection": sum(item["selected_schema"] is None for item in comparisons),
        "agreed": agreed,
        "top1_agreement": agreed / count if count else None,
        "mean_reciprocal_rank_lower": lower / count if count else None,
        "mean_reciprocal_rank_upper": upper / count if count else None,
    }


def compare_schema_reference(
    report: dict[str, Any], evidence: dict[str, ReferenceReviewEvidence]
) -> dict[str, Any]:
    """Score current experiment rows against EventSchema on actual emitted rows.

    Callers obtain evidence via reference_review_evidence, which verifies fixture,
    capture, queue/build identity, source row identity, and exact message equality.
    Empty output never implies Unsupported; heterogeneous templates are not forced
    into a majority label. Multiple rows of the same schema get one event vote.
    """
    rows = report["rows"]
    ids = [row["case_id"] for row in rows]
    if len(ids) != len(set(ids)) or set(ids) != set(evidence):
        raise ValueError("Reference evidence must cover unique experiment case IDs exactly")
    if not rows:
        raise ValueError("Reference agreement requires experiment rows")
    captures = {item.capture_sha256 for item in evidence.values()}
    if len(captures) != 1:
        raise ValueError("Reference agreement requires a single verified capture")

    case_records = []
    seen_events: set[str] = set()
    template_scores = {name: [] for name in ("baseline", "jev")}
    event_scores = {name: [] for name in ("baseline", "jev")}
    template_states: Counter[str] = Counter()
    event_states: Counter[str] = Counter()
    labels: Counter[str] = Counter()
    for row in rows:
        reference = evidence[row["case_id"]]
        predictions = {name: _prediction(row, name) for name in template_scores}
        events: list[dict[str, Any]] = []
        for example in reference.examples:
            if example.event_id in seen_events:
                raise ValueError("Reference events must not be counted in multiple examples")
            seen_events.add(example.event_id)
            label, state = _reference_label(example)
            event_states[state] += 1
            comparisons = {}
            if label is not None:
                labels[label] += 1
                for name, prediction in predictions.items():
                    comparisons[name] = _comparison(prediction, label)
                    event_scores[name].append(comparisons[name])
            events.append(
                {
                    "event_id": example.event_id,
                    "reference_state": state,
                    "reference_schema": label,
                    "output_row_count": len(example.output.rows),
                    "comparisons": comparisons,
                }
            )
        distinct = {event["reference_schema"] for event in events}
        template_label = None
        if not events:
            template_state = "no_examples"
        elif all(event["reference_state"] == "not_selected" for event in events):
            template_state = "not_selected"
        elif any(event["reference_state"] != "labelled" for event in events):
            template_state = "incomplete_or_ambiguous_reference"
        elif len(distinct) != 1:
            template_state = "mixed_schemas"
        else:
            template_state = "labelled"
            template_label = events[0]["reference_schema"]
        template_states[template_state] += 1
        comparisons = {}
        if template_label is not None:
            for name, prediction in predictions.items():
                comparisons[name] = _comparison(prediction, template_label)
                template_scores[name].append(comparisons[name])
        case_records.append(
            {
                "case_id": row["case_id"],
                "request_hash": row["request_hash"],
                "reference_state": template_state,
                "reference_schema": template_label,
                "comparisons": comparisons,
                "events": events,
                "reference_conformance_issue_count": len(reference.issues),
            }
        )
    return {
        "format_version": "1",
        "track": "asim-parser-silver",
        "metric": "reference_schema_agreement",
        "capture_sha256": next(iter(captures)),
        "catalogue_revision": report["catalogue_revision"],
        "model": report["model"],
        "context": report["context"],
        "nouls": report["nouls"],
        "templates": len(rows),
        "represented_source_events": len(seen_events),
        "template_states": dict(template_states),
        "event_states": dict(event_states),
        "reference_event_schema_counts": dict(labels),
        "single_schema_reference": len(labels) == 1,
        "approaches": {
            name: {
                "templates": _metrics(template_scores[name]),
                "events": _metrics(event_scores[name]),
            }
            for name in template_scores
        },
        "cases": case_records,
    }
