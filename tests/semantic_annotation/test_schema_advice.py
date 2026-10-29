"""Recorded Jev advice joins the existing engineering review, never approval."""

import hashlib
import json

import pytest

from asim_forge.cli import main
from asim_forge.compiler import compile_reviews
from asim_forge.mapping_review import (
    MappingReviewSetup,
    initial_mapping_draft,
    load_mapping_review,
    prepare_mapping_review,
    validate_mapping_draft,
)
from asim_forge.reviews import ReviewError
from asim_forge.schema_ranking import jev_client, jev_experiment
from asim_forge.schema_ranking.jev import (
    AUTH_LIFECYCLE_SPEC_VERSION,
    OPERATIONAL_STATE_SPEC_VERSION,
    JevResponse,
)
from asim_forge.schema_ranking.jev_experiment import SchemaExperimentInput, run_schema_experiment


@pytest.fixture
def recorded_schema(tmp_path, annotation_queue, monkeypatch):
    _, _, _, manifest, task = annotation_queue
    counter = 0

    def record(choice="NetworkSession", *, tied=False, staged=False):
        nonlocal counter
        counter += 1
        output = tmp_path / f"run-{counter}"

        def response(request, *_args, **_kwargs):
            scores = {name: float(name == choice) for name in request.questions["schema"].criteria}
            if tied:
                scores.update(Authentication=0.5, NetworkSession=0.5)
            answers = {
                name: {"type": "noul", "noul": 0.8}
                if question.type == "noul"
                else {
                    "type": "choice",
                    "choice": choice,
                    "confidence": 0.8,
                    "probabilities": scores,
                }
                for name, question in request.questions.items()
            }
            return JevResponse.model_validate(
                {
                    "model": request.model,
                    "answers": answers,
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                }
            ).validate_for(request), True

        with monkeypatch.context() as scope:
            scope.setattr(jev_experiment, "cached_response", response)
            run_schema_experiment(
                [SchemaExperimentInput(case_id=task.case_id, source=task.input)],
                catalogue_revision=manifest.catalogue_revision,
                output=output,
                cache=tmp_path / "unused-cache",
                mode="replay",
                spec_version=AUTH_LIFECYCLE_SPEC_VERSION
                if staged
                else OPERATIONAL_STATE_SPEC_VERSION,
                staged=staged,
            )
        return output / "report.json"

    def no_provider(*_args, **_kwargs):
        pytest.fail("Review preparation attempted a provider call")

    monkeypatch.setattr(jev_client, "send_request", no_provider)
    return record


def prepare(tmp_path, annotation_queue, report, *, schemas=None):
    catalog_dir, catalog, queue, _, _ = annotation_queue
    output = tmp_path / "mapping"
    prepare_mapping_review(
        queue,
        catalog_dir,
        output,
        schema_report=report,
        setup=MappingReviewSetup(
            schema_versions=schemas or {"Authentication": "0.1.3", "NetworkSession": "0.2.6"}
        ),
    )
    tasks, _ = load_mapping_review(output)
    return output, tasks[0], initial_mapping_draft(tasks[0], catalog)


@pytest.mark.parametrize("staged", [False, True])
def test_advice_selects_schema_without_carrying_other_mappings_or_approval(
    tmp_path, annotation_queue, recorded_schema, staged
):
    report = recorded_schema(staged=staged)
    _, task, draft = prepare(tmp_path, annotation_queue, report)
    assert task.prediction.ranked_schemas[0].schema_name == "Authentication"
    assert draft.schema_name == "NetworkSession"
    assert not draft.schema_confirmed and draft.status == "in_progress"
    assert not any(row.asim_field == "TargetUsername" for row in draft.rows)
    assert all(not row.locator for row in draft.rows)
    assert task.schema_suggestion.report_sha256 == hashlib.sha256(report.read_bytes()).hexdigest()
    assert task.schema_suggestion.selected_schema == "NetworkSession"
    with pytest.raises(ReviewError, match="confirm"):
        validate_mapping_draft(draft, task, annotation_queue[1])


@pytest.mark.parametrize(
    "choice,tied", [("Unsupported", False), ("Authentication", True), ("AuditEvent", False)]
)
def test_unsupported_tied_or_unavailable_advice_leaves_schema_unselected(
    tmp_path, annotation_queue, recorded_schema, semantic_build, choice, tied
):
    bundle, task, draft = prepare(tmp_path, annotation_queue, recorded_schema(choice, tied=tied))
    assert draft.schema_name == "" and not draft.schema_confirmed and not draft.rows
    assert task.schema_suggestion.selected_schema == (None if tied else choice)
    assert set(draft.schema_rows) == {"Authentication", "NetworkSession"}
    draft.status = "deferred"
    state = tmp_path / "user_state.json"
    state.write_text(
        json.dumps(
            {
                "user_id": "schema-reviewer",
                "instance_id_to_label_to_value": {
                    task.source_task.case_id: [
                        [{"schema": "mapping_review", "name": "text_box"}, draft.model_dump_json()]
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    manifest = compile_reviews(
        semantic_build[0] / "clusters.jsonl", state, tmp_path / "compiled", mapping_bundle=bundle
    )
    assert manifest.compiled_count == 0
    assert manifest.skipped_reviews == {"deferred": 1}


@pytest.mark.parametrize(
    "mutation",
    [
        "evidence",
        "catalogue",
        "missing",
        "duplicate",
        "partial",
        "label",
        "selection",
        "probability",
        "stage",
    ],
)
def test_mismatched_or_incomplete_advice_is_rejected_before_writing_bundle(
    tmp_path, annotation_queue, recorded_schema, mutation
):
    path = recorded_schema(staged=mutation == "stage")
    report = json.loads(path.read_text())
    if mutation == "evidence":
        report["rows"][0]["request_hash"] = "0" * 64
    elif mutation == "catalogue":
        report["catalogue_revision"] = "0" * 40
    elif mutation == "missing":
        report["rows"] = []
    elif mutation == "duplicate":
        report["rows"] *= 2
    elif mutation == "partial":
        report["rows"][0]["status"] = "error"
    elif mutation == "label":
        report["rows"][0]["expected_schema"] = "NetworkSession"
    elif mutation == "selection":
        report["rows"][0]["jev"]["selected_schema"] = "Authentication"
    elif mutation == "probability":
        report["rows"][0]["jev"]["response"]["answers"]["schema"]["probabilities"][
            "NetworkSession"
        ] = 0.2
    elif mutation == "stage":
        report["rows"][0]["final_request_hash"] = "0" * 64
    path.write_text(json.dumps(report))
    with pytest.raises(ReviewError):
        prepare(tmp_path, annotation_queue, path)
    assert not (tmp_path / "mapping").exists()


def test_cli_import_and_recorded_advice_participate_in_review_revision(
    tmp_path, annotation_queue, recorded_schema
):
    catalog_dir, _, queue, _, _ = annotation_queue
    setup = tmp_path / "setup.json"
    setup.write_text('{"schema_versions":{"Authentication":"0.1.3","NetworkSession":"0.2.6"}}')
    revisions = []
    for choice in ["Authentication", "NetworkSession"]:
        output = tmp_path / choice
        assert (
            main(
                [
                    "review",
                    "prepare",
                    str(queue),
                    "--catalog",
                    str(catalog_dir),
                    "--setup",
                    str(setup),
                    "--schema-report",
                    str(recorded_schema(choice)),
                    "--output",
                    str(output),
                ]
            )
            is None
        )
        tasks, _ = load_mapping_review(output)
        revisions.append(tasks[0].review_revision)
        assert tasks[0].schema_suggestion is not None
        assert tasks[0].schema_suggestion.selected_schema == choice
    assert len(set(revisions)) == 2
