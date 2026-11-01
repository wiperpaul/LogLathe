import hashlib
import json
from pathlib import Path

import pytest

from asim_forge.evaluation import load_semantic_mapping_cases
from asim_forge.semantic_annotation import (
    SemanticAnnotationError,
    promote_semantic_annotations,
    semantic_input_fingerprint,
    semantic_task_revision,
    write_semantic_annotation_decisions,
    write_semantic_annotation_tasks,
)


def test_single_review_requires_opt_in_and_promotes_without_group_leakage(
    annotation_queue,
    decision_factory,
    tmp_path: Path,
) -> None:
    _, catalog, queue_dir, _, task = annotation_queue
    decisions_path = tmp_path / "decisions.jsonl"
    write_semantic_annotation_decisions(
        decisions_path,
        [decision_factory(task, "auth.alice.v1", "alice")],
    )

    with pytest.raises(SemanticAnnotationError, match="No decisions are eligible"):
        promote_semantic_annotations(
            queue_dir,
            decisions_path,
            tmp_path / "not-promoted",
            catalog,
        )

    output_dir = tmp_path / "promoted"
    manifest = promote_semantic_annotations(
        queue_dir,
        decisions_path,
        output_dir,
        catalog,
        allow_single_review=True,
    )
    case = load_semantic_mapping_cases(output_dir / "cases.jsonl")[0]

    assert manifest.promoted_count == 1
    assert manifest.label_source_counts == {"human_review": 1}
    assert case.provenance.label_source == "human_review"
    assert case.provenance.decision_refs == ["auth.alice.v1"]
    assert case.input == task.input
    raw_case = (output_dir / "cases.jsonl").read_text(encoding="utf-8")
    assert '"group_id"' not in raw_case
    group = json.loads((output_dir / "case-groups.jsonl").read_text(encoding="utf-8"))
    assert group == {
        "case_id": task.case_id,
        "group_id": "openssh.family",
        "group_strategy": "source-family",
    }


def test_promotion_rejects_stale_and_unknown_catalogue_mappings(
    annotation_queue,
    decision_factory,
    tmp_path: Path,
) -> None:
    _, catalog, queue_dir, _, task = annotation_queue
    decisions_path = tmp_path / "bad.jsonl"
    write_semantic_annotation_decisions(
        decisions_path,
        [decision_factory(task, "auth.stale.v1", "alice", fingerprint="0" * 64)],
    )
    with pytest.raises(SemanticAnnotationError, match="is stale"):
        promote_semantic_annotations(
            queue_dir,
            decisions_path,
            tmp_path / "stale",
            catalog,
            allow_single_review=True,
        )


def test_promotion_reports_tasks_with_no_submission(
    annotation_queue,
    decision_factory,
    tmp_path: Path,
) -> None:
    _, catalog, queue_dir, _, first = annotation_queue
    second = first.model_copy(deep=True)
    second.case_id = "cluster-second"
    second.input.cluster_id = second.case_id
    second.input_fingerprint = semantic_input_fingerprint(second.input)
    second.task_revision = semantic_task_revision(
        case_id=second.case_id,
        catalogue_revision=second.catalogue_revision,
        group_id=second.group_id,
        group_strategy=second.group_strategy,
        input_fingerprint=second.input_fingerprint,
        provenance=second.provenance,
        protocol_revision=second.protocol_revision,
    )
    tasks_path = queue_dir / "tasks.jsonl"
    write_semantic_annotation_tasks(tasks_path, [first, second])
    queue_manifest_path = queue_dir / "queue-manifest.json"
    queue_manifest = json.loads(queue_manifest_path.read_text(encoding="utf-8"))
    queue_manifest["task_count"] = 2
    queue_manifest["tasks_sha256"] = hashlib.sha256(tasks_path.read_bytes()).hexdigest()
    queue_manifest_path.write_text(json.dumps(queue_manifest) + "\n", encoding="utf-8")
    decisions_path = tmp_path / "decisions.jsonl"
    write_semantic_annotation_decisions(
        decisions_path,
        [decision_factory(first, "auth.alice.v1", "alice")],
    )

    manifest = promote_semantic_annotations(
        queue_dir,
        decisions_path,
        tmp_path / "partial-gold",
        catalog,
        allow_single_review=True,
    )

    assert manifest.skipped_tasks == {"unsubmitted": 1}


def test_promotion_validates_constant_types_and_catalogue_domains(
    annotation_queue,
    decision_factory,
    tmp_path: Path,
) -> None:
    _, catalog, queue_dir, _, task = annotation_queue
    decisions_path = tmp_path / "constant-decisions.jsonl"
    invalid_domain = decision_factory(
        task,
        "auth.invalid-domain.v1",
        "alice",
        field="EventResult",
    )
    invalid_domain.expected.asim_fields[0].constant_value = "Banana"
    write_semantic_annotation_decisions(decisions_path, [invalid_domain])
    with pytest.raises(SemanticAnnotationError, match="outside the allowed values"):
        promote_semantic_annotations(
            queue_dir,
            decisions_path,
            tmp_path / "invalid-domain",
            catalog,
            allow_single_review=True,
        )

    invalid_type = decision_factory(task, "auth.invalid-type.v1", "alice")
    invalid_type.expected.asim_fields[0].constant_value = True
    write_semantic_annotation_decisions(decisions_path, [invalid_type])
    with pytest.raises(SemanticAnnotationError, match="constant incompatible"):
        promote_semantic_annotations(
            queue_dir,
            decisions_path,
            tmp_path / "invalid-type",
            catalog,
            allow_single_review=True,
        )

    write_semantic_annotation_decisions(
        decisions_path,
        [decision_factory(task, "auth.unknown-field.v1", "alice", field="UnknownAsimField")],
    )
    with pytest.raises(SemanticAnnotationError, match="maps unknown fields"):
        promote_semantic_annotations(
            queue_dir,
            decisions_path,
            tmp_path / "unknown-field",
            catalog,
            allow_single_review=True,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [("Flag", True), ("AttemptCount", 3), ("RiskScore", 0.75)],
)
def test_promotion_accepts_catalogue_compatible_scalar_constants(
    annotation_queue,
    decision_factory,
    field: str,
    tmp_path: Path,
    value: bool | int | float,
) -> None:
    _, catalog, queue_dir, _, task = annotation_queue
    decision = decision_factory(task, f"auth.{field}.v1", "alice", field=field)
    decision.expected.asim_fields[0].constant_value = value
    decisions_path = tmp_path / "scalar-decision.jsonl"
    write_semantic_annotation_decisions(decisions_path, [decision])

    manifest = promote_semantic_annotations(
        queue_dir,
        decisions_path,
        tmp_path / "scalar-gold",
        catalog,
        allow_single_review=True,
    )

    assert manifest.promoted_count == 1


@pytest.mark.parametrize(
    ("kind", "value", "valid"),
    [
        ("int", -(2**31), True),
        ("int", 2**31 - 1, True),
        ("int", -(2**31) - 1, False),
        ("int", 2**31, False),
        ("long", -(2**63), True),
        ("long", 2**63 - 1, True),
        ("long", -(2**63) - 1, False),
        ("long", 2**63, False),
        ("real", 0.75, True),
        ("real", float("inf"), False),
        ("real", float("-inf"), False),
        ("real", float("nan"), False),
        ("real", 10**1000, False),
        ("double", float("inf"), False),
        ("datetime", "2026-11-01T12:30:00Z", True),
        ("datetime", "2026-11-01", True),
        ("datetime", "2026-11-01T12:30:00.1234567+05:30", True),
        ("datetime", "not-a-date", False),
        ("datetime", "2026-02-30T12:30:00Z", False),
        ("datetime", "20261101", False),
        ("datetime", "2026-11-01 12:30:00", False),
    ],
)
def test_promotion_validates_constant_values_before_writing_gold(
    annotation_queue, decision_factory, tmp_path, kind, value, valid
):
    _, catalog, queue_dir, _, task = annotation_queue
    catalog = catalog.model_copy(
        update={
            "fields": [
                field.model_copy(update={"kql_type": kind})
                if field.name == "AttemptCount"
                else field
                for field in catalog.fields
            ]
        }
    )
    decision = decision_factory(task, "constant-check", "alice", field="AttemptCount")
    decision.expected.asim_fields[0].constant_value = value
    path = tmp_path / "decision.jsonl"
    write_semantic_annotation_decisions(path, [decision])
    output = tmp_path / "gold"

    if valid:
        assert (
            promote_semantic_annotations(
                queue_dir, path, output, catalog, allow_single_review=True
            ).promoted_count
            == 1
        )
    else:
        with pytest.raises(SemanticAnnotationError, match="constant incompatible"):
            promote_semantic_annotations(queue_dir, path, output, catalog, allow_single_review=True)
        assert not output.exists()


def test_promotion_rejects_catalogue_drift_and_unknown_cases(
    annotation_queue,
    decision_factory,
    other_catalog_revision: str,
    tmp_path: Path,
) -> None:
    _, catalog, queue_dir, _, task = annotation_queue
    decisions_path = tmp_path / "bad.jsonl"
    write_semantic_annotation_decisions(
        decisions_path,
        [
            decision_factory(
                task,
                "auth.other-catalog.v1",
                "alice",
                revision=other_catalog_revision,
            )
        ],
    )
    with pytest.raises(SemanticAnnotationError, match="catalogue revision"):
        promote_semantic_annotations(
            queue_dir,
            decisions_path,
            tmp_path / "other-catalog",
            catalog,
            allow_single_review=True,
        )

    unknown_payload = decision_factory(task, "auth.unknown-case.v1", "alice").model_dump(
        mode="json"
    )
    unknown_payload["case_id"] = "cluster-unknown"
    decisions_path.write_text(json.dumps(unknown_payload) + "\n", encoding="utf-8")
    with pytest.raises(SemanticAnnotationError, match="unknown case"):
        promote_semantic_annotations(
            queue_dir,
            decisions_path,
            tmp_path / "unknown-case",
            catalog,
            allow_single_review=True,
        )


def test_adjudication_requires_known_independent_annotations(
    annotation_queue,
    decision_factory,
    tmp_path: Path,
) -> None:
    _, catalog, queue_dir, _, task = annotation_queue
    decisions_path = tmp_path / "bad-adjudication.jsonl"
    same_reviewer = [
        decision_factory(task, "auth.alice.v1", "alice"),
        decision_factory(task, "auth.alice.v2", "alice"),
        decision_factory(
            task,
            "auth.adjudicated.v1",
            "carol",
            kind="adjudication",
            refs=["auth.alice.v1", "auth.alice.v2"],
        ),
    ]
    write_semantic_annotation_decisions(decisions_path, same_reviewer)
    with pytest.raises(SemanticAnnotationError, match="independent reviewers"):
        promote_semantic_annotations(
            queue_dir,
            decisions_path,
            tmp_path / "same-reviewer",
            catalog,
        )

    unknown_reference = [
        decision_factory(task, "auth.alice.v1", "alice"),
        decision_factory(
            task,
            "auth.adjudicated.v1",
            "carol",
            kind="adjudication",
            refs=["auth.alice.v1", "auth.missing.v1"],
        ),
    ]
    write_semantic_annotation_decisions(decisions_path, unknown_reference)
    with pytest.raises(SemanticAnnotationError, match="references unknown decision"):
        promote_semantic_annotations(
            queue_dir,
            decisions_path,
            tmp_path / "unknown-reference",
            catalog,
        )

    multiple_adjudications = [
        decision_factory(task, "auth.alice.v1", "alice"),
        decision_factory(task, "auth.bob.v1", "bob"),
        decision_factory(
            task,
            "auth.adjudicated.v1",
            "carol",
            kind="adjudication",
            refs=["auth.alice.v1", "auth.bob.v1"],
        ),
        decision_factory(
            task,
            "auth.adjudicated.v2",
            "dave",
            kind="adjudication",
            refs=["auth.alice.v1", "auth.bob.v1"],
        ),
    ]
    write_semantic_annotation_decisions(decisions_path, multiple_adjudications)
    with pytest.raises(SemanticAnnotationError, match="more than one adjudication"):
        promote_semantic_annotations(
            queue_dir,
            decisions_path,
            tmp_path / "multiple-adjudications",
            catalog,
        )


def test_promotion_checks_task_revision_schema_and_slot_references(
    annotation_queue,
    decision_factory,
    tmp_path: Path,
) -> None:
    _, catalog, queue_dir, _, task = annotation_queue
    decisions_path = tmp_path / "invalid-decisions.jsonl"
    stale = decision_factory(task, "auth.stale-task.v1", "alice").model_copy(
        update={"task_revision": "0" * 64}
    )
    write_semantic_annotation_decisions(decisions_path, [stale])
    with pytest.raises(SemanticAnnotationError, match="stale task revision"):
        promote_semantic_annotations(
            queue_dir,
            decisions_path,
            tmp_path / "stale-task",
            catalog,
            allow_single_review=True,
        )

    unknown_schema = decision_factory(task, "auth.unknown-schema.v1", "alice")
    unknown_schema.expected.schema_name = "Dns"
    write_semantic_annotation_decisions(decisions_path, [unknown_schema])
    with pytest.raises(SemanticAnnotationError, match="unknown ASIM schema"):
        promote_semantic_annotations(
            queue_dir,
            decisions_path,
            tmp_path / "unknown-schema",
            catalog,
            allow_single_review=True,
        )

    bad_slot = decision_factory(task, "auth.bad-slot.v1", "alice")
    bad_slot.expected.source_semantics[0].locator = "p9"
    write_semantic_annotation_decisions(decisions_path, [bad_slot])
    with pytest.raises(SemanticAnnotationError, match="does not resolve against its task"):
        promote_semantic_annotations(
            queue_dir,
            decisions_path,
            tmp_path / "bad-slot",
            catalog,
            allow_single_review=True,
        )
