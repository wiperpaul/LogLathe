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
    configured_mapping_defaults,
    initial_mapping_draft,
    load_mapping_decisions,
    load_mapping_review,
    prepare_mapping_review,
    required_mapping_fields,
    validate_mapping_draft,
)
from asim_forge.models import (
    AsimCatalog,
    AsimCatalogField,
    FieldMapping,
    ParameterSlot,
    SourceEvent,
)
from asim_forge.potato_bundle import example_literal_spans, example_slot_spans
from asim_forge.reference.evidence import ReferenceReviewEvidence
from asim_forge.reviews import ReviewError, load_review_decisions
from asim_forge.semantic_mapping.approaches import build_approach
from asim_forge.semantic_mapping.contracts import MappingRequest, PredictedAsimField


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
    assert item["example_literal_spans"] == [[{"start": 0, "end": 17, "text": "login failed for "}]]
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


def test_literal_spans_follow_template_boundaries_including_unicode_and_multiline():
    slots = [ParameterSlot(slot_id="p1", label="user", placeholder="<VAR:USER>", occurrence=1)]
    events = [
        SourceEvent(source_file="test", line_number=1, text="🔒 Failed Alice van Smith\nend"),
        SourceEvent(source_file="test", line_number=2, text="🔒 Failed Bob\nend"),
    ]
    template = "🔒 Failed <VAR:USER>\nend"
    assert example_literal_spans(template, events, slots) == [
        [
            {"start": 0, "end": 9, "text": "🔒 Failed "},
            {"start": 24, "end": 28, "text": "\nend"},
        ],
        [
            {"start": 0, "end": 9, "text": "🔒 Failed "},
            {"start": 12, "end": 16, "text": "\nend"},
        ],
    ]
    assert example_slot_spans(template, events, slots)[0] == [
        {"slot_id": "p1", "start": 9, "end": 24, "text": "Alice van Smith"}
    ]
    assert example_literal_spans("🔒 Failed", [events[0]], []) == [[]]
    assert example_literal_spans(
        "🔒 Failed", [events[0].model_copy(update={"text": "🔒 Failed"})], []
    ) == [[{"start": 0, "end": 8, "text": "🔒 Failed"}]]


@pytest.mark.parametrize(
    ("template", "text"),
    [
        ("<VAR:A><VAR:B>", "first second"),
        ("before <VAR:A> : <VAR:B> after", "before first : second : third after"),
        ("before <VAR:A> : <VAR:B> after", "unmatched message"),
        ("before <VAR:A> after", "before first after"),
    ],
)
def test_unverified_alignment_has_no_literal_or_slot_spans(template, text):
    slots = [
        ParameterSlot(slot_id=f"p{index}", label=label, placeholder=f"<VAR:{label}>", occurrence=1)
        for index, label in enumerate(("A", "B"), start=1)
    ]
    events = [SourceEvent(source_file="test", line_number=1, text=text)]
    assert example_literal_spans(template, events, slots) == [[]]
    assert example_slot_spans(template, events, slots) == [[]]


def test_fixed_template_span_supports_a_catalogue_constant(mapping_bundle):
    _, task, catalog, _ = mapping_bundle
    draft = _draft(task, catalog)
    draft.rows[1].source_span = SourceSpan(example_index=0, start=6, end=12, text="failed")
    assert validate_mapping_draft(draft, task, catalog)[1] == FieldMapping(
        constant_value="Failure", asim_field="EventResult", transform="string"
    )
    draft.rows[1].constant_value = "Failed"
    with pytest.raises(ReviewError, match="outside the catalogue values"):
        validate_mapping_draft(draft, task, catalog)


@pytest.mark.parametrize("value", ["", "   "])
def test_literal_evidence_requires_output_even_for_optional_fields(mapping_bundle, value):
    _, task, catalog, _ = mapping_bundle
    draft = _draft(task, catalog)
    draft.rows[0] = MappingRow(
        source_kind="constant",
        asim_field="TargetUsername",
        constant_value=value,
        source_span=SourceSpan(example_index=0, start=6, end=12, text="failed"),
    )
    with pytest.raises(ReviewError, match="Choose an output value"):
        validate_mapping_draft(draft, task, catalog)
    # Preserve existing manually specified empty constants without evidence.
    draft.rows[0].source_span = None
    assert validate_mapping_draft(draft, task, catalog)[0].constant_value == value


@pytest.mark.parametrize(("start", "end"), [(17, 22), (18, 21), (13, 20)])
def test_constant_evidence_cannot_use_slots_partial_slots_or_cross_boundaries(
    mapping_bundle, start, end
):
    _, task, catalog, _ = mapping_bundle
    draft = _draft(task, catalog)
    text = task.source_task.input.representative_events[0].text
    draft.rows[1].source_span = SourceSpan(
        example_index=0, start=start, end=end, text=text[start:end]
    )
    with pytest.raises(ReviewError, match="not within verified fixed template text"):
        validate_mapping_draft(draft, task, catalog)


def test_repeated_literal_text_in_a_slot_is_not_constant_evidence(mapping_bundle):
    _, frozen_task, catalog, _ = mapping_bundle
    task = frozen_task.model_copy(deep=True)
    task.source_task.input.representative_events[0].text = "login failed for failed"
    task.source_task.input.parameter_slots[0].examples = ["failed"]
    draft = _draft(task, catalog)
    draft.rows[1].source_span = SourceSpan(example_index=0, start=6, end=12, text="failed")
    assert validate_mapping_draft(draft, task, catalog)
    draft.rows[1].source_span = SourceSpan(example_index=0, start=17, end=23, text="failed")
    with pytest.raises(ReviewError, match="not within verified fixed template text"):
        validate_mapping_draft(draft, task, catalog)


def test_constant_evidence_must_match_the_frozen_example(mapping_bundle):
    _, task, catalog, _ = mapping_bundle
    draft = _draft(task, catalog)
    draft.rows[1].source_span = SourceSpan(example_index=0, start=6, end=12, text="Failed")
    with pytest.raises(ReviewError, match="does not match the frozen example"):
        validate_mapping_draft(draft, task, catalog)
    draft.rows[1].source_span = SourceSpan(example_index=5, start=6, end=12, text="failed")
    with pytest.raises(ReviewError, match="unknown example"):
        validate_mapping_draft(draft, task, catalog)


def test_source_table_mappings_cannot_use_event_span_evidence(mapping_bundle):
    _, task, catalog, _ = mapping_bundle
    draft = _draft(task, catalog)
    draft.rows[2].source_span = SourceSpan(example_index=0, start=6, end=12, text="failed")
    with pytest.raises(ReviewError, match="Source-table mappings cannot use"):
        validate_mapping_draft(draft, task, catalog)


def test_approved_literal_mapping_roundtrips_evidence_and_compiles(tmp_path, mapping_bundle):
    bundle, task, catalog, clusters = mapping_bundle
    draft = _draft(task, catalog)
    draft.status = "approved"
    evidence = SourceSpan(example_index=0, start=6, end=12, text="failed")
    draft.rows[1].source_span = evidence
    state = _state(tmp_path / "user_state.json", task, draft)
    saved_data = json.loads(state.read_bytes())
    saved_draft = MappingReviewDraft.model_validate_json(
        saved_data["instance_id_to_label_to_value"][task.source_task.case_id][0][1]
    )
    assert saved_draft.rows[1].source_span == evidence
    decisions = load_mapping_decisions(bundle, state)
    assert decisions[0].field_mappings[1].constant_value == "Failure"
    output = tmp_path / "compiled"
    manifest = compile_reviews(clusters, state, output, mapping_bundle=bundle)
    assert manifest.compiled_count == 1
    assert 'EventResult = tostring("Failure")' in next(output.glob("*.kql")).read_text("utf-8")
    spec = json.loads(next(output.glob("*.parser-spec.json")).read_text("utf-8"))
    assert spec["template"] == task.source_task.input.template
    assert (
        spec["mapping_review"]["review_file_sha256"]
        == hashlib.sha256(state.read_bytes()).hexdigest()
    )
    assert spec["mapping_review"]["review_revision"] == task.review_revision


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


def _timing_defaults() -> list[FieldMapping]:
    return [
        FieldMapping(asim_field="EventCount", constant_value=1, transform="int"),
        FieldMapping(
            asim_field="EventStartTime", source_field="TimeGenerated", transform="datetime"
        ),
        FieldMapping(
            asim_field="EventEndTime", output_field="EventStartTime", transform="datetime"
        ),
    ]


@pytest.fixture
def default_task(mapping_bundle):
    _, task, catalog, _ = mapping_bundle
    task = task.model_copy(deep=True)
    catalog = catalog.model_copy(deep=True)
    for name in ("EventStartTime", "EventEndTime"):
        catalog.fields.append(
            AsimCatalogField(
                name=name, schema_name="Common", kql_type="datetime", field_class="Mandatory"
            )
        )
    assert task.setup is not None
    task.setup.mapping_defaults = _timing_defaults()
    task.reference = ReferenceReviewEvidence(
        capture_sha256="0" * 64,
        schema_name="Authentication",
        schema_version="0.1.3",
        source_columns={"TimeGenerated": "datetime", "ObservedTime": "datetime", "Count": "int"},
        examples=[],
        issues=[],
    )
    return task, catalog


def test_configured_defaults_seed_each_schema_and_leave_unconfigured_fields_blank(default_task):
    task, catalog = default_task
    draft = initial_mapping_draft(task, catalog)
    for rows in draft.schema_rows.values():
        by_field = {row.asim_field: row for row in rows}
        assert by_field["EventCount"].constant_value == "1"
        assert by_field["EventStartTime"].source_kind == "source_field"
        assert by_field["EventStartTime"].locator == "TimeGenerated"
        assert by_field["EventEndTime"].source_kind == "output_field"
        assert by_field["EventEndTime"].locator == "EventStartTime"
    # The setup snapshot supplies reset values without sharing editable row state.
    draft.schema_rows["Authentication"][-1].locator = "changed"
    defaults = configured_mapping_defaults(task, catalog, "Authentication")
    assert defaults[-1].locator == "EventStartTime"
    assert draft.schema_rows["NetworkSession"][-1].locator == "EventStartTime"


def test_explicit_count_prediction_wins_over_setup_default(default_task):
    task, catalog = default_task
    task.prediction.asim_fields.append(
        PredictedAsimField(
            source_kind="slot",
            locator="p1",
            asim_field="EventCount",
            score=1,
            ranked_candidates=[{"asim_field": "EventCount", "score": 1}],
        )
    )
    draft = initial_mapping_draft(task, catalog)
    count = next(row for row in draft.rows if row.asim_field == "EventCount")
    assert count.source_kind == "slot"
    assert count.locator == "p1"
    assert count.constant_value == ""


def test_review_can_override_start_and_count_while_end_follows_normalized_start(default_task):
    task, catalog = default_task
    draft = _draft(task, catalog)
    draft.rows.extend(configured_mapping_defaults(task, catalog, "Authentication")[1:])
    start = next(row for row in draft.rows if row.asim_field == "EventStartTime")
    start.locator = "ObservedTime"
    draft.rows.reverse()  # End may appear before its producer in the reviewer layout.
    mappings = {
        mapping.asim_field: mapping for mapping in validate_mapping_draft(draft, task, catalog)
    }
    assert mappings["EventCount"].source_field == "Count"
    assert mappings["EventStartTime"].source_field == "ObservedTime"
    assert mappings["EventEndTime"].output_field == "EventStartTime"


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("EventEndTime", "own output"),
        ("EventResult", "incompatible ASIM output"),
        ("MissingTimestamp", "Unknown or incompatible ASIM output"),
    ],
)
def test_invalid_normalized_output_dependencies_are_rejected(default_task, source, message):
    task, catalog = default_task
    draft = _draft(task, catalog)
    draft.rows.extend(configured_mapping_defaults(task, catalog, "Authentication")[1:])
    draft.rows[-1].locator = source
    with pytest.raises(ReviewError, match=message):
        validate_mapping_draft(draft, task, catalog)


def test_output_dependency_needs_a_populated_mapping_not_just_a_catalogue_field(default_task):
    task, catalog = default_task
    catalog.fields.append(
        AsimCatalogField(
            name="OtherTime",
            schema_name="Authentication",
            kql_type="datetime",
            field_class="Optional",
        )
    )
    draft = _draft(task, catalog)
    draft.rows.extend(configured_mapping_defaults(task, catalog, "Authentication")[1:])
    draft.rows[-1].locator = "OtherTime"
    with pytest.raises(ReviewError, match="unmapped output"):
        validate_mapping_draft(draft, task, catalog)
    draft.rows[-1].locator = "EventStartTime"
    draft.rows[-2].source_kind = "output_field"
    draft.rows[-2].locator = "EventEndTime"
    with pytest.raises(ReviewError, match="dependency cycle"):
        validate_mapping_draft(draft, task, catalog)


def test_output_mapping_rejects_text_span_evidence(default_task):
    task, catalog = default_task
    draft = _draft(task, catalog)
    draft.rows.extend(configured_mapping_defaults(task, catalog, "Authentication")[1:])
    draft.rows[-1].source_span = SourceSpan(example_index=0, start=6, end=12, text="failed")
    with pytest.raises(ReviewError, match="output-field mappings cannot use"):
        validate_mapping_draft(draft, task, catalog)


@pytest.mark.parametrize(
    ("mapping", "message"),
    [
        ({"asim_field": "NoSuchField", "constant_value": "x"}, "absent from configured"),
        ({"asim_field": "EventCount", "constant_value": 1}, "incompatible conversion"),
        ({"asim_field": "EventResult", "constant_value": "Failed"}, "outside the catalogue"),
        (
            {"asim_field": "EventCount", "constant_value": "banana", "transform": "int"},
            "invalid constant",
        ),
        (
            {
                "asim_field": "EventStartTime",
                "source_field": "MissingColumn",
                "transform": "datetime",
            },
            "unknown source-table",
        ),
        (
            {"asim_field": "EventStartTime", "source_field": "Count", "transform": "datetime"},
            "incompatible source-column",
        ),
        (
            {"asim_field": "EventEndTime", "output_field": "EventCount", "transform": "datetime"},
            "incompatible output-field",
        ),
        (
            {"asim_field": "EventEndTime", "output_field": "MissingTime", "transform": "datetime"},
            "editable ASIM output",
        ),
        (
            {"asim_field": "EventEndTime", "output_field": "EventEndTime", "transform": "datetime"},
            "output-field cycle",
        ),
    ],
)
def test_invalid_configured_defaults_fail_with_target_context(default_task, mapping, message):
    task, catalog = default_task
    assert task.setup is not None
    task.setup.mapping_defaults = [FieldMapping.model_validate(mapping)]
    with pytest.raises(ReviewError, match=message):
        configured_mapping_defaults(task, catalog, "Authentication")


def test_defaults_cannot_override_managed_metadata(default_task):
    task, catalog = default_task
    catalog.fields.append(
        AsimCatalogField(
            name="EventSchema", schema_name="Common", kql_type="string", field_class="Mandatory"
        )
    )
    assert task.setup is not None
    task.setup.mapping_defaults = [
        FieldMapping(asim_field="EventSchema", constant_value="Authentication")
    ]
    with pytest.raises(ReviewError, match="automatically supplied"):
        configured_mapping_defaults(task, catalog, "Authentication")


def test_configured_output_defaults_reject_cycles_but_allow_later_reviewer_values(default_task):
    task, catalog = default_task
    assert task.setup is not None
    task.setup.mapping_defaults = [_timing_defaults()[-1]]
    assert (
        configured_mapping_defaults(task, catalog, "Authentication")[0].locator == "EventStartTime"
    )
    task.setup.mapping_defaults.append(
        FieldMapping(asim_field="EventStartTime", output_field="EventEndTime", transform="datetime")
    )
    with pytest.raises(ReviewError, match="output-field cycle"):
        configured_mapping_defaults(task, catalog, "Authentication")


def test_ingestion_timestamp_default_can_reference_future_source_column(default_task):
    task, catalog = default_task
    assert task.setup is not None and task.reference is not None
    task.setup.time_generated = "ingestion"
    del task.reference.source_columns["TimeGenerated"]
    assert (
        configured_mapping_defaults(task, catalog, "Authentication")[1].locator == "TimeGenerated"
    )
    task.setup.time_generated = "source"
    with pytest.raises(ReviewError, match="unknown source-table field: TimeGenerated"):
        configured_mapping_defaults(task, catalog, "Authentication")


def test_schema_specific_default_is_only_seeded_where_target_exists(default_task):
    task, catalog = default_task
    assert task.setup is not None
    task.setup.mapping_defaults = [FieldMapping(asim_field="EventResult", constant_value="Failure")]
    assert (
        configured_mapping_defaults(task, catalog, "Authentication")[0].constant_value == "Failure"
    )
    assert configured_mapping_defaults(task, catalog, "NetworkSession") == []


@pytest.mark.parametrize(
    ("defaults", "message"),
    [
        (
            [{"asim_field": "EventCount", "slot_id": "p1", "transform": "int"}],
            "template-specific slots",
        ),
        (
            [
                {"asim_field": "EventCount", "constant_value": 1},
                {"asim_field": "eventcount", "constant_value": 2},
            ],
            "unique ASIM target",
        ),
    ],
)
def test_setup_rejects_unusable_default_sources_and_duplicate_targets(defaults, message):
    with pytest.raises(ValidationError, match=message):
        MappingReviewSetup(schema_versions={"Authentication": "0.1.3"}, mapping_defaults=defaults)


def test_preparation_validates_defaults_and_preserves_them_in_frozen_tasks(
    tmp_path, annotation_queue
):
    catalog_dir, catalog, queue_dir, _, _ = annotation_queue
    setup = MappingReviewSetup(
        schema_versions={"Authentication": "0.1.3"},
        mapping_defaults=[FieldMapping(asim_field="EventCount", constant_value=1, transform="int")],
    )
    bundle = tmp_path / "with-defaults"
    prepare_mapping_review(queue_dir, catalog_dir, bundle, setup=setup)
    tasks, _ = load_mapping_review(bundle)
    assert tasks[0].setup == setup
    changed = tasks[0].model_dump(mode="json")
    changed["setup"]["mapping_defaults"][0]["constant_value"] = 2
    with pytest.raises(ValidationError, match="revision does not match"):
        MappingReviewTask.model_validate(changed)
    count = next(
        row
        for row in initial_mapping_draft(tasks[0], catalog).rows
        if row.asim_field == "EventCount"
    )
    assert count.constant_value == "1"
    invalid_setup = setup.model_copy(
        update={"mapping_defaults": [FieldMapping(asim_field="UnknownField", constant_value=1)]}
    )
    invalid_bundle = tmp_path / "invalid-defaults"
    with pytest.raises(ReviewError, match="UnknownField"):
        prepare_mapping_review(queue_dir, catalog_dir, invalid_bundle, setup=invalid_setup)
    assert not invalid_bundle.exists()


def test_pre_defaults_frozen_revision_and_bundle_remain_readable(mapping_bundle):
    bundle, task, _, _ = mapping_bundle
    payload = task.model_dump(mode="json", exclude={"review_revision"})
    payload["setup"].pop("mapping_defaults", None)
    old_revision = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()
    payload["review_revision"] = old_revision
    assert old_revision == task.review_revision
    restored = MappingReviewTask.model_validate(payload)
    assert restored.setup is not None and restored.setup.mapping_defaults is None
    # Make the frozen record truly omit the new member, as an existing bundle does.
    suggestions = bundle / "suggestions.jsonl"
    suggestions.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    manifest_path = bundle / "review-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["files"]["suggestions.jsonl"] = hashlib.sha256(suggestions.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert load_mapping_review(bundle)[0][0] == restored
