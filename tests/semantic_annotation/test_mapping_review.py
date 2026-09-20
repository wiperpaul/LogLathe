"""Assisted Potato review reuses the queue without becoming independent labels."""

import hashlib
import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from asim_forge.cli import main
from asim_forge.compiler import compile_reviews
from asim_forge.mapping_review import (
    MappingReviewDraft,
    MappingReviewSetup,
    MappingReviewTask,
    MappingRow,
    SourceSpan,
    initial_mapping_draft,
    load_mapping_decisions,
    load_mapping_review,
    prepare_mapping_review,
    required_mapping_fields,
    validate_mapping_draft,
)
from asim_forge.models import AsimCatalog, FieldMapping, ParameterSlot, SourceEvent
from asim_forge.potato_bundle import example_slot_spans
from asim_forge.reviews import ReviewError, load_review_decisions
from asim_forge.semantic_mapping.approaches import build_approach
from asim_forge.semantic_mapping.contracts import MappingRequest


@pytest.fixture
def mapping_bundle(tmp_path: Path, annotation_queue, semantic_build):
    catalog_dir, catalog, queue_dir, _, _ = annotation_queue
    build_dir, reviews = semantic_build
    bundle = tmp_path / "mapping"
    prepare_mapping_review(
        queue_dir,
        catalog_dir,
        bundle,
        cluster_reviews=reviews,
        setup=MappingReviewSetup(
            schema_versions={"Authentication": "0.1.3", "NetworkSession": "0.2.6"}
        ),
    )
    tasks, _ = load_mapping_review(bundle)
    return bundle, tasks[0], catalog, build_dir / "clusters.jsonl"


def _draft(task: MappingReviewTask, catalog: AsimCatalog) -> MappingReviewDraft:
    draft = initial_mapping_draft(task, catalog)
    draft.schema_name = "Authentication"
    draft.schema_confirmed = True
    draft.rows = [
        MappingRow(locator="p1", asim_field="TargetUsername"),
        MappingRow(source_kind="constant", constant_value="Failure", asim_field="EventResult"),
        MappingRow(
            source_kind="source_field", locator="Count", asim_field="EventCount", transform="int"
        ),
    ]
    return draft


def _state(path: Path, task: MappingReviewTask, draft: MappingReviewDraft) -> Path:
    path.write_text(
        json.dumps(
            {
                "user_id": "mapping-reviewer",
                "instance_id_to_label_to_value": {
                    task.source_task.case_id: [
                        [{"schema": "mapping_review", "name": "text_box"}, draft.model_dump_json()]
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_preparation_keeps_source_queue_blind_and_uses_potato(mapping_bundle):
    bundle, task, catalog, _ = mapping_bundle
    assert initial_mapping_draft(task, catalog).status == "in_progress"
    assert task.mode == "assisted-engineering"
    source = task.source_task.model_dump_json()
    assert "ExistingVendor" not in source
    assert "vimExistingMapping" not in source
    assert "prediction" not in source
    assert "reference" not in task.source_task.input.model_dump()
    config = yaml.safe_load((bundle / "potato/config.yaml").read_text("utf-8"))
    assert config["task_layout"] == "mapping-layout.html"
    assert config["annotation_schemes"][0]["annotation_type"] == "text"
    item = json.loads((bundle / "potato/items.jsonl").read_text("utf-8").splitlines()[0])
    assert "example_slot_spans" in item
    assert not (bundle / "potato/annotation_output").exists()
    with pytest.raises(ReviewError, match="empty mapping review"):
        prepare_mapping_review(bundle / "queue", bundle / "catalog", bundle)


def test_field_suggestions_are_independent_for_each_review_schema(mapping_bundle):
    _, task, catalog, _ = mapping_bundle
    assert task.setup is not None
    assert set(task.schema_predictions) == set(task.setup.schema_versions)
    draft = initial_mapping_draft(task, catalog)
    assert set(draft.schema_rows) == set(task.setup.schema_versions)
    suggested_schema = task.prediction.ranked_schemas[0].schema_name
    assert draft.rows == draft.schema_rows[suggested_schema]
    for schema, prediction in task.schema_predictions.items():
        fields = {field.name: field for field in catalog.fields_for_schema(schema)}
        assert all(mapping.asim_field in fields for mapping in prediction.asim_fields)
        assert all(row.asim_field in fields for row in draft.schema_rows[schema])
        assert all(
            row.transform == fields[row.asim_field].kql_type for row in draft.schema_rows[schema]
        )
    other_schema = next(name for name in draft.schema_rows if name != suggested_schema)
    before = [row.model_dump() for row in draft.schema_rows[other_schema]]
    draft.schema_rows[suggested_schema][0].locator = "changed-in-one-schema"
    assert [row.model_dump() for row in draft.schema_rows[other_schema]] == before


def test_new_schema_does_not_inherit_previous_schema_suggestions(mapping_bundle):
    _, task, catalog, _ = mapping_bundle
    suggested_schema = task.prediction.ranked_schemas[0].schema_name
    other_schema = next(name for name in task.schema_predictions if name != suggested_schema)
    assert task.schema_predictions[other_schema].asim_fields
    draft = initial_mapping_draft(task, catalog)
    assert any(row.locator for row in draft.schema_rows[suggested_schema])
    assert all(not row.locator for row in draft.schema_rows[other_schema])
    assert all(not row.constant_value for row in draft.schema_rows[other_schema])
    assert draft.schema_rows[other_schema]


@pytest.mark.parametrize("approach", ["semantic-frame", "direct-lexical", "matcher-ensemble"])
def test_review_schema_projects_fields_into_selected_catalogue(mapping_bundle, approach):
    _, task, catalog, _ = mapping_bundle
    request = MappingRequest(
        case_id=task.source_task.case_id,
        catalogue_revision=task.source_task.catalogue_revision,
        input=task.source_task.input,
        review_schema="NetworkSession",
    )
    prediction = build_approach(approach).predict(request, catalog)
    allowed = {field.name for field in catalog.fields_for_schema("NetworkSession")}
    assert all(mapping.asim_field in allowed for mapping in prediction.asim_fields)
    assert prediction.ranked_schemas == task.prediction.ranked_schemas


def test_example_spans_only_identify_template_captures():
    slots = [
        ParameterSlot(slot_id="p1", label="user", placeholder="<VAR:USER>", occurrence=1),
        ParameterSlot(slot_id="p2", label="ip", placeholder="<VAR:IP>", occurrence=1),
    ]
    events = [
        SourceEvent(source_file="test", line_number=1, text="user root from 192.0.2.1"),
        SourceEvent(source_file="test", line_number=2, text="different message"),
    ]
    spans = example_slot_spans("user <VAR:USER> from <VAR:IP>", events, slots)
    assert spans == [
        [
            {"slot_id": "p1", "start": 5, "end": 9, "text": "root"},
            {"slot_id": "p2", "start": 15, "end": 24, "text": "192.0.2.1"},
        ],
        [],
    ]
    assert example_slot_spans("<VAR:A><VAR:B>", events, slots) == [[], []]


def test_reviewed_span_must_match_frozen_extraction(mapping_bundle):
    _, task, catalog, _ = mapping_bundle
    captures = example_slot_spans(
        task.source_task.input.template,
        task.source_task.input.representative_events,
        task.source_task.input.parameter_slots,
    )
    example_index, selected = next(
        (index, span)
        for index, spans in enumerate(captures)
        for span in spans
        if span["slot_id"] == "p1"
    )
    draft = _draft(task, catalog)
    draft.rows[0].source_span = SourceSpan(
        example_index=example_index,
        start=selected["start"],
        end=selected["end"],
        text=selected["text"],
    )
    assert validate_mapping_draft(draft, task, catalog)
    draft.rows[0].source_span = draft.rows[0].source_span.model_copy(update={"text": "wrong"})
    with pytest.raises(ReviewError, match="does not match"):
        validate_mapping_draft(draft, task, catalog)
    draft.rows[0].source_span = SourceSpan(
        example_index=example_index,
        start=selected["start"],
        end=selected["end"] - 1,
        text=selected["text"][:-1],
    )
    with pytest.raises(ReviewError, match="not an extracted slot"):
        validate_mapping_draft(draft, task, catalog)


@pytest.mark.parametrize("status", ["in_progress", "deferred", "needs_extraction", "approved"])
def test_only_explicit_mapping_approval_compiles(tmp_path, mapping_bundle, status):
    bundle, task, catalog, clusters = mapping_bundle
    draft = _draft(task, catalog).model_copy(update={"status": status})
    state = _state(tmp_path / "user_state.json", task, draft)
    output = tmp_path / "compiled"
    manifest = compile_reviews(clusters, state, output, mapping_bundle=bundle)
    assert manifest.compiled_count == (1 if status == "approved" else 0)
    assert manifest.skipped_reviews == ({} if status == "approved" else {status: 1})
    if status == "approved":
        spec = json.loads(next(output.glob("*.parser-spec.json")).read_text("utf-8"))
        assert spec["reviewer"] == "mapping-reviewer"
        assert spec["schema_version"] == "0.1.3"
        assert spec["mapping_review"]["mode"] == "assisted-engineering"
        assert (
            spec["mapping_review"]["review_file_sha256"]
            == hashlib.sha256(state.read_bytes()).hexdigest()
        )
        kql = next(output.glob("*.kql")).read_text("utf-8")
        assert 'EventResult = tostring("Failure")' in kql
        assert 'EventSchemaVersion = "0.1.3"' in kql
        assert "EventCount = toint(_asim_forge_source_1)" in kql
    # The stage-one reader must never turn an assisted draft into a gold queue.
    with pytest.raises(ReviewError):
        load_review_decisions(state)


@pytest.mark.parametrize(
    "filename",
    [
        "suggestions.jsonl",
        "potato/items.jsonl",
        "potato/mapping-layout.html",
        "catalog/asim-catalog.csv",
        "queue/tasks.jsonl",
    ],
)
def test_changed_evidence_is_rejected(mapping_bundle, filename):
    bundle, _, _, _ = mapping_bundle
    with (bundle / filename).open("ab") as stream:
        stream.write(b"\n")
    with pytest.raises(ReviewError, match="evidence changed"):
        load_mapping_review(bundle)


def test_stale_decision_and_changed_clusters_are_rejected(tmp_path, mapping_bundle):
    bundle, task, catalog, clusters = mapping_bundle
    draft = _draft(task, catalog)
    draft.status = "approved"
    draft.review_revision = "0" * 64
    state = _state(tmp_path / "user_state.json", task, draft)
    with pytest.raises(ReviewError, match="stale"):
        load_mapping_decisions(bundle, state)
    draft.review_revision = task.review_revision
    _state(state, task, draft)
    with clusters.open("ab") as stream:
        stream.write(b"\n")
    with pytest.raises(ReviewError, match="changed cluster"):
        compile_reviews(clusters, state, tmp_path / "compiled", mapping_bundle=bundle)


@pytest.mark.parametrize("cluster_status", ["needs_split", "rejected", "insufficient_evidence"])
def test_reopened_cluster_blocks_even_previously_approved_mappings(
    tmp_path, mapping_bundle, cluster_status
):
    bundle, task, catalog, clusters = mapping_bundle
    original = _draft(task, catalog).model_copy(update={"status": "approved"})
    revised = original.model_copy(
        update={"cluster_status": cluster_status, "cluster_notes": "Examples mix two event types"}
    )
    state = _state(tmp_path / "state.json", task, revised)
    decisions = load_mapping_decisions(bundle, state)
    assert decisions[0].status == cluster_status
    assert "Examples mix two event types" in decisions[0].notes
    assert not decisions[0].field_mappings
    manifest = compile_reviews(clusters, state, tmp_path / "compiled", mapping_bundle=bundle)
    assert manifest.compiled_count == 0
    assert manifest.skipped_reviews == {cluster_status: 1}
    assert revised.rows == original.rows
    with pytest.raises(ReviewError, match="cluster review"):
        validate_mapping_draft(revised, task, catalog)
    # The revised source decision remains assisted engineering feedback.
    with pytest.raises(ReviewError):
        load_review_decisions(state)


def test_existing_saved_drafts_keep_the_frozen_cluster_approval(tmp_path, mapping_bundle):
    bundle, task, catalog, _ = mapping_bundle
    state = _state(tmp_path / "state.json", task, _draft(task, catalog))
    data = json.loads(state.read_bytes())
    pair = data["instance_id_to_label_to_value"][task.source_task.case_id][0]
    old_draft = json.loads(pair[1])
    old_draft.pop("cluster_status")
    old_draft.pop("cluster_notes")
    pair[1] = json.dumps(old_draft)
    state.write_text(json.dumps(data), encoding="utf-8")
    decision = load_mapping_decisions(bundle, state)[0]
    assert decision.status == "approved"
    assert decision.mapping_review is not None
    assert decision.mapping_review.status == "in_progress"


def test_unknown_task_and_missing_reviewer_are_rejected(tmp_path, mapping_bundle):
    bundle, task, catalog, _ = mapping_bundle
    state = _state(tmp_path / "state.json", task, _draft(task, catalog))
    data = json.loads(state.read_bytes())
    annotations = data["instance_id_to_label_to_value"]
    annotations["unknown-cluster"] = annotations.pop(task.source_task.case_id)
    state.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ReviewError, match="unknown task"):
        load_mapping_decisions(bundle, state)
    data["user_id"] = " "
    state.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ReviewError, match="reviewer"):
        load_mapping_decisions(bundle, state)


@pytest.mark.parametrize(
    "row",
    [
        MappingRow(locator="p9", asim_field="TargetUsername"),
        MappingRow(locator="p1", asim_field="MissingField"),
        MappingRow(locator="p1", asim_field="TargetUsername", transform="int"),
        MappingRow(
            source_kind="source_field", locator="bad;statement", asim_field="TargetUsername"
        ),
        MappingRow(source_kind="constant", constant_value="Maybe", asim_field="EventResult"),
        MappingRow(
            source_kind="constant", constant_value="maybe", asim_field="Flag", transform="bool"
        ),
        MappingRow(
            source_kind="constant",
            constant_value="2147483648",
            asim_field="AttemptCount",
            transform="int",
        ),
        MappingRow(
            source_kind="constant", constant_value="NaN", asim_field="RiskScore", transform="real"
        ),
    ],
)
def test_invalid_mapping_cannot_be_approved(mapping_bundle, row):
    _, task, catalog, _ = mapping_bundle
    draft = _draft(task, catalog)
    draft.rows = [row]
    with pytest.raises((ReviewError, ValidationError)):
        validate_mapping_draft(draft, task, catalog)


def test_approval_rejects_duplicate_assignments_and_blank_metadata(tmp_path, mapping_bundle):
    bundle, task, catalog, _ = mapping_bundle
    draft = _draft(task, catalog)
    draft.rows.append(draft.rows[0].model_copy())
    with pytest.raises(ReviewError, match="duplicate"):
        validate_mapping_draft(draft, task, catalog)
    draft.rows.pop()
    draft.status = "approved"
    draft.vendor = " "
    state = _state(tmp_path / "state.json", task, draft)
    with pytest.raises(ReviewError, match="fixed by setup"):
        load_mapping_decisions(bundle, state)


@pytest.mark.parametrize("value", [False, 0, ""])
def test_false_zero_and_empty_string_are_valid_mapping_constants(value):
    mapping = FieldMapping(constant_value=value, asim_field="Example")
    assert mapping.constant_value == value
    assert type(mapping.constant_value) is type(value)


@pytest.mark.parametrize(
    "sources", [{}, {"slot_id": "p1", "constant_value": 0}, {"constant_value": float("inf")}]
)
def test_mapping_requires_exactly_one_finite_source(sources):
    with pytest.raises(ValidationError):
        FieldMapping.model_validate({**sources, "asim_field": "Example"})


def test_cli_prepares_and_compiles_same_review_contract(tmp_path, annotation_queue, semantic_build):
    catalog_dir, catalog, queue_dir, _, _ = annotation_queue
    build_dir, _ = semantic_build
    bundle = tmp_path / "cli-mapping"
    setup_path = tmp_path / "setup.json"
    setup_path.write_text('{"schema_versions":{"Authentication":"0.1.3"}}', encoding="utf-8")
    main(
        [
            "review",
            "prepare",
            str(queue_dir),
            "--catalog",
            str(catalog_dir),
            "--output",
            str(bundle),
            "--setup",
            str(setup_path),
        ]
    )
    tasks, _ = load_mapping_review(bundle)
    draft = _draft(tasks[0], catalog)
    draft.status = "approved"
    state = _state(tmp_path / "state.json", tasks[0], draft)
    main(
        [
            "compile",
            str(build_dir / "clusters.jsonl"),
            str(state),
            "--mapping-bundle",
            str(bundle),
            "--output",
            str(tmp_path / "compiled"),
        ]
    )
    manifest = json.loads((tmp_path / "compiled/compile-manifest.json").read_bytes())
    assert manifest["compiled_count"] == 1


@pytest.mark.parametrize("field", ["vendor", "product", "source_table", "message_field"])
def test_review_cannot_override_setup_source_metadata(mapping_bundle, field):
    _, task, catalog, _ = mapping_bundle
    draft = _draft(task, catalog)
    setattr(draft, field, "ChangedSource")
    with pytest.raises(ReviewError, match="fixed by setup"):
        validate_mapping_draft(draft, task, catalog)


def test_mapping_approval_requires_a_confirmed_available_schema(mapping_bundle):
    _, task, catalog, _ = mapping_bundle
    draft = _draft(task, catalog)
    draft.schema_confirmed = False
    with pytest.raises(ReviewError, match="confirm one ASIM schema"):
        validate_mapping_draft(draft, task, catalog)
    draft.schema_confirmed = True
    draft.schema_name = "AuditEvent"
    with pytest.raises(ReviewError, match="no version configured"):
        validate_mapping_draft(draft, task, catalog)


def test_schema_choice_controls_generated_version(tmp_path, mapping_bundle):
    bundle, task, catalog, clusters = mapping_bundle
    draft = _draft(task, catalog)
    draft.schema_name = "NetworkSession"
    draft.rows = [
        MappingRow(locator="p1", asim_field="DstIpAddr"),
        MappingRow(
            source_kind="constant", constant_value="1", asim_field="EventCount", transform="int"
        ),
    ]
    draft.status = "approved"
    state = _state(tmp_path / "state.json", task, draft)
    output = tmp_path / "compiled"
    compile_reviews(clusters, state, output, mapping_bundle=bundle)
    kql = next(output.glob("*.kql")).read_text("utf-8")
    assert 'EventSchema = "NetworkSession"' in kql
    assert 'EventSchemaVersion = "0.2.6"' in kql


def test_setup_is_required_without_versioned_reference(tmp_path, annotation_queue):
    catalog_dir, _, queue_dir, _, _ = annotation_queue
    with pytest.raises(ReviewError, match="requires --setup"):
        prepare_mapping_review(queue_dir, catalog_dir, tmp_path / "missing-setup")


def test_ingestion_assumption_is_preserved_in_specification(
    tmp_path, annotation_queue, semantic_build
):
    catalog_dir, catalog, queue_dir, _, _ = annotation_queue
    build_dir, _ = semantic_build
    bundle = tmp_path / "ingestion-review"
    setup = MappingReviewSetup(
        schema_versions={"Authentication": "0.1.3"}, time_generated="ingestion"
    )
    prepare_mapping_review(queue_dir, catalog_dir, bundle, setup=setup)
    tasks, _ = load_mapping_review(bundle)
    draft = _draft(tasks[0], catalog)
    draft.status = "approved"
    state = _state(tmp_path / "state.json", tasks[0], draft)
    output = tmp_path / "compiled"
    compile_reviews(build_dir / "clusters.jsonl", state, output, mapping_bundle=bundle)
    spec = json.loads(next(output.glob("*.parser-spec.json")).read_bytes())
    assert spec["mapping_review"]["time_generated"] == "ingestion"
    kql = next(output.glob("*.kql")).read_text("utf-8")
    assert "Setup assumes TimeGenerated is supplied at ingestion" in kql
    assert "now()" not in kql
    assert "ingestion_time()" not in kql


@pytest.mark.parametrize("target", ["EventSchemaVersion", "TimeGenerated", "Type"])
def test_setup_managed_fields_cannot_be_redefined(mapping_bundle, target):
    _, task, catalog, _ = mapping_bundle
    # Add just the relevant field to this deliberately minimal synthetic catalogue.
    from asim_forge.models import AsimCatalogField

    catalog = catalog.model_copy(deep=True)
    catalog.fields.append(
        AsimCatalogField(
            name=target, schema_name="Authentication", kql_type="string", field_class="Mandatory"
        )
    )
    task = task.model_copy(deep=True)
    assert task.setup is not None
    task.setup.time_generated = "ingestion"
    draft = _draft(task, catalog)
    draft.rows = [MappingRow(source_kind="constant", constant_value="manual", asim_field=target)]
    with pytest.raises(ReviewError, match="supplied by"):
        validate_mapping_draft(draft, task, catalog)


def test_required_targets_start_without_invented_values(mapping_bundle):
    _, task, catalog, _ = mapping_bundle
    draft = initial_mapping_draft(task, catalog)
    count = next(row for row in draft.rows if row.asim_field == "EventCount")
    assert count.locator == count.constant_value == ""
    assert count.transform == "int"
    assert {
        field.name for field in required_mapping_fields(task, catalog, "Authentication", [])
    } == {"EventCount"}


@pytest.mark.parametrize("value", [None, "", " "])
def test_required_mapping_cannot_be_removed_or_left_blank(mapping_bundle, value):
    _, task, catalog, _ = mapping_bundle
    draft = _draft(task, catalog)
    draft.rows = [row for row in draft.rows if row.asim_field != "EventCount"]
    if value is not None:
        draft.rows.append(
            MappingRow(
                source_kind="constant",
                constant_value=value,
                asim_field="EventCount",
                transform="int",
            )
        )
    with pytest.raises(ReviewError, match="required mapping"):
        validate_mapping_draft(draft, task, catalog)


def test_inactive_schema_rows_are_saved_but_never_compiled(tmp_path, mapping_bundle):
    bundle, task, catalog, clusters = mapping_bundle
    draft = _draft(task, catalog)
    draft.status = "approved"
    draft.schema_rows = {
        "NetworkSession": [MappingRow(locator="p99", asim_field="DstIpAddr")],
        "Authentication": [MappingRow(locator="p98", asim_field="RetainedUnavailableField")],
    }
    state = _state(tmp_path / "state.json", task, draft)
    output = tmp_path / "compiled"
    manifest = compile_reviews(clusters, state, output, mapping_bundle=bundle)
    assert manifest.compiled_count == 1
    spec = json.loads(next(output.glob("*.parser-spec.json")).read_bytes())
    assert {field["asim_field"] for field in spec["field_mappings"]} == {
        "TargetUsername",
        "EventResult",
        "EventCount",
    }
    saved = json.loads(state.read_bytes())["instance_id_to_label_to_value"][
        task.source_task.case_id
    ][0][1]
    assert MappingReviewDraft.model_validate_json(saved).schema_rows == draft.schema_rows
