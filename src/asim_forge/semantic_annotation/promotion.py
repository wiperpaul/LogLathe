"""Promote reviewed semantic annotations into trusted evaluation gold."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from ..evaluation import (
    EvaluationProvenance,
    SemanticMappingCase,
    write_semantic_mapping_cases,
)
from ..evaluation_splits import SemanticCaseGroup, write_semantic_case_groups
from ..models import AsimCatalog, AsimCatalogField
from .artifacts import (
    _sha256_file,
    _write_json,
    load_semantic_annotation_decisions,
    load_semantic_annotation_queue,
)
from .contracts import (
    CASE_GROUPS_FILE,
    CASES_FILE,
    PROMOTION_MANIFEST_FILE,
    QUEUE_MANIFEST_FILE,
    SemanticAnnotationDecision,
    SemanticAnnotationError,
    SemanticAnnotationPromotionManifest,
    SemanticAnnotationTask,
)


def promote_semantic_annotations(
    queue_dir: Path,
    decisions_path: Path,
    output_dir: Path,
    catalog: AsimCatalog,
    *,
    allow_single_review: bool = False,
) -> SemanticAnnotationPromotionManifest:
    """Promote completed annotations, preserving group metadata outside gold cases."""
    queue_manifest_path = queue_dir / QUEUE_MANIFEST_FILE
    queue_manifest, tasks = load_semantic_annotation_queue(queue_dir)
    decisions = load_semantic_annotation_decisions(decisions_path)
    catalogue_revision = catalog.manifest.resolved_revision
    _validate_catalogue_revisions(tasks, decisions, catalogue_revision)
    task_by_id = {task.case_id: task for task in tasks}
    _validate_decisions(decisions, task_by_id, catalog)

    annotations_by_case: dict[str, list[SemanticAnnotationDecision]] = {}
    adjudication_by_case: dict[str, SemanticAnnotationDecision] = {}
    decision_by_id = {decision.decision_id: decision for decision in decisions}
    for decision in decisions:
        if decision.decision_kind == "annotation":
            annotations_by_case.setdefault(decision.case_id, []).append(decision)
        elif decision.case_id in adjudication_by_case:
            raise SemanticAnnotationError(
                f"Case {decision.case_id!r} has more than one adjudication decision"
            )
        else:
            adjudication_by_case[decision.case_id] = decision

    _validate_adjudications(adjudication_by_case.values(), decision_by_id)
    _reject_duplicate_reviewers(annotations_by_case)

    cases: list[SemanticMappingCase] = []
    groups: list[SemanticCaseGroup] = []
    skipped: Counter[str] = Counter()
    label_sources: Counter[str] = Counter()
    for task in sorted(tasks, key=lambda item: item.case_id):
        source_decisions: list[SemanticAnnotationDecision] = []
        selected = adjudication_by_case.get(task.case_id)
        label_source: Literal["human_review", "adjudicated"]
        if selected is not None:
            label_source = "adjudicated"
            source_decisions = [decision_by_id[ref] for ref in selected.source_decision_refs]
        else:
            annotations = annotations_by_case.get(task.case_id, [])
            if not annotations:
                skipped["unsubmitted"] += 1
                continue
            if not allow_single_review or len(annotations) != 1:
                skipped["awaiting_adjudication"] += 1
                continue
            selected = annotations[0]
            label_source = "human_review"

        decision_refs = [decision.decision_id for decision in source_decisions]
        if selected.decision_id not in decision_refs:
            decision_refs.append(selected.decision_id)
        annotator_refs = sorted(
            {selected.reviewer_ref, *(decision.reviewer_ref for decision in source_decisions)}
        )
        case = SemanticMappingCase(
            case_id=task.case_id,
            catalogue_revision=task.catalogue_revision,
            input=task.input,
            expected=selected.expected,
            provenance=EvaluationProvenance(
                label_source=label_source,
                decision_refs=decision_refs,
                annotator_refs=annotator_refs,
                notes=selected.notes,
            ),
        )
        cases.append(case)
        groups.append(
            SemanticCaseGroup(
                case_id=task.case_id,
                group_id=task.group_id,
                group_strategy=task.group_strategy,
            )
        )
        label_sources[label_source] += 1

    if not cases:
        raise SemanticAnnotationError(
            "No decisions are eligible for promotion; complete adjudication or use "
            "--allow-single-review for provisional human-review cases"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    cases_path = output_dir / CASES_FILE
    groups_path = output_dir / CASE_GROUPS_FILE
    write_semantic_mapping_cases(cases_path, cases)
    write_semantic_case_groups(groups_path, groups)
    promotion_manifest = SemanticAnnotationPromotionManifest(
        catalogue_revision=catalogue_revision,
        protocol_revision=queue_manifest.protocol_revision,
        task_count=len(tasks),
        decision_count=len(decisions),
        promoted_count=len(cases),
        label_source_counts=dict(sorted(label_sources.items())),
        skipped_tasks=dict(sorted(skipped.items())),
        cases_sha256=_sha256_file(cases_path),
        case_groups_sha256=_sha256_file(groups_path),
        decisions_sha256=_sha256_file(decisions_path),
        queue_manifest_sha256=_sha256_file(queue_manifest_path),
        tasks_sha256=queue_manifest.tasks_sha256,
        outputs={"case_groups": CASE_GROUPS_FILE, "cases": CASES_FILE},
    )
    _write_json(
        output_dir / PROMOTION_MANIFEST_FILE,
        promotion_manifest.model_dump(mode="json"),
    )
    return promotion_manifest


def _validate_catalogue_revisions(
    tasks: list[SemanticAnnotationTask],
    decisions: list[SemanticAnnotationDecision],
    catalogue_revision: str,
) -> None:
    task_revisions = {task.catalogue_revision for task in tasks}
    if task_revisions != {catalogue_revision}:
        raise SemanticAnnotationError(
            "Annotation tasks do not match the loaded catalogue revision; "
            f"tasks={sorted(task_revisions)}, catalog={catalogue_revision}"
        )
    decision_revisions = {decision.catalogue_revision for decision in decisions}
    if decision_revisions != {catalogue_revision}:
        raise SemanticAnnotationError(
            "Annotation decisions do not match the loaded catalogue revision; "
            f"decisions={sorted(decision_revisions)}, catalog={catalogue_revision}"
        )


def _validate_decisions(
    decisions: list[SemanticAnnotationDecision],
    task_by_id: dict[str, SemanticAnnotationTask],
    catalog: AsimCatalog,
) -> None:
    for decision in decisions:
        task = task_by_id.get(decision.case_id)
        if task is None:
            raise SemanticAnnotationError(
                f"Decision {decision.decision_id!r} references unknown case {decision.case_id!r}"
            )
        if decision.input_fingerprint != task.input_fingerprint:
            raise SemanticAnnotationError(
                f"Decision {decision.decision_id!r} is stale for case {decision.case_id!r}"
            )
        if decision.task_revision != task.task_revision:
            raise SemanticAnnotationError(
                f"Decision {decision.decision_id!r} has a stale task revision for "
                f"case {decision.case_id!r}"
            )
        _validate_expected_against_catalog(decision, catalog)
        try:
            SemanticMappingCase(
                case_id=task.case_id,
                catalogue_revision=task.catalogue_revision,
                input=task.input,
                expected=decision.expected,
                provenance=EvaluationProvenance(
                    label_source="human_review",
                    decision_refs=[decision.decision_id],
                    annotator_refs=[decision.reviewer_ref],
                ),
            )
        except ValueError as error:
            raise SemanticAnnotationError(
                f"Decision {decision.decision_id!r} does not resolve against its task: {error}"
            ) from error


def _validate_expected_against_catalog(
    decision: SemanticAnnotationDecision, catalog: AsimCatalog
) -> None:
    expected = decision.expected
    if expected.asim_fields and expected.schema_name is None:
        raise SemanticAnnotationError(
            f"Decision {decision.decision_id!r} has ASIM fields without a schema"
        )
    if expected.schema_name is None:
        return
    if expected.schema_name not in catalog.manifest.schemas:
        raise SemanticAnnotationError(
            f"Decision {decision.decision_id!r} uses unknown ASIM schema {expected.schema_name!r}"
        )
    fields_by_name = {
        field.name: field for field in catalog.fields_for_schema(expected.schema_name)
    }
    unknown_fields = sorted(
        {field.asim_field for field in expected.asim_fields} - fields_by_name.keys()
    )
    if unknown_fields:
        raise SemanticAnnotationError(
            f"Decision {decision.decision_id!r} maps unknown fields for "
            f"{expected.schema_name}: {unknown_fields}"
        )
    for expected_field in expected.asim_fields:
        if expected_field.constant_value is not None:
            _validate_constant_value(
                decision.decision_id,
                expected_field.asim_field,
                expected_field.constant_value,
                fields_by_name[expected_field.asim_field],
            )


def _validate_constant_value(
    decision_id: str,
    field_name: str,
    value: str | int | float | bool,
    catalog_field: AsimCatalogField,
) -> None:
    kql_type = catalog_field.kql_type.casefold()
    if kql_type in {"string", "datetime", "dynamic"}:
        type_matches = isinstance(value, str)
    elif kql_type == "bool":
        type_matches = isinstance(value, bool)
    elif kql_type in {"int", "long"}:
        type_matches = isinstance(value, int) and not isinstance(value, bool)
    elif kql_type in {"real", "double"}:
        type_matches = isinstance(value, (int, float)) and not isinstance(value, bool)
    else:
        type_matches = False
    if not type_matches:
        raise SemanticAnnotationError(
            f"Decision {decision_id!r} uses a constant incompatible with "
            f"{field_name} ({catalog_field.kql_type})"
        )
    if catalog_field.allowed_values and str(value) not in catalog_field.allowed_values:
        raise SemanticAnnotationError(
            f"Decision {decision_id!r} uses constant {value!r} outside the allowed "
            f"values for {field_name}: {catalog_field.allowed_values}"
        )


def _reject_duplicate_reviewers(
    annotations_by_case: dict[str, list[SemanticAnnotationDecision]],
) -> None:
    for case_id, case_annotations in annotations_by_case.items():
        counts = Counter(annotation.reviewer_ref for annotation in case_annotations)
        duplicates = sorted(reviewer for reviewer, count in counts.items() if count > 1)
        if duplicates:
            raise SemanticAnnotationError(
                f"Case {case_id!r} has multiple annotations from the same reviewer: {duplicates}"
            )


def _validate_adjudications(
    adjudications: Iterable[SemanticAnnotationDecision],
    decision_by_id: dict[str, SemanticAnnotationDecision],
) -> None:
    for adjudication in adjudications:
        sources: list[SemanticAnnotationDecision] = []
        for reference in adjudication.source_decision_refs:
            source = decision_by_id.get(reference)
            if source is None:
                raise SemanticAnnotationError(
                    f"Adjudication {adjudication.decision_id!r} references unknown decision "
                    f"{reference!r}"
                )
            if source.decision_kind != "annotation":
                raise SemanticAnnotationError(
                    f"Adjudication {adjudication.decision_id!r} can reference annotations only"
                )
            if (
                source.case_id != adjudication.case_id
                or source.catalogue_revision != adjudication.catalogue_revision
                or source.input_fingerprint != adjudication.input_fingerprint
                or source.task_revision != adjudication.task_revision
            ):
                raise SemanticAnnotationError(
                    f"Adjudication {adjudication.decision_id!r} references a decision from "
                    "another case or revision"
                )
            sources.append(source)
        if len({source.reviewer_ref for source in sources}) < 2:
            raise SemanticAnnotationError(
                f"Adjudication {adjudication.decision_id!r} requires two independent reviewers"
            )
