from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from asim_forge.cli import main
from asim_forge.compiler import compile_reviews
from asim_forge.models import AsimCatalog, AsimCatalogField, AsimCatalogManifest
from asim_forge.reference.comparison import compare_captures, compare_outputs, conformance
from asim_forge.reference.contracts import QueryOutput
from asim_forge.reference.execution import (
    KustoClient,
    load_capture,
    parse_response,
    verify_capture,
    write_capture,
)
from asim_forge.reference.fixtures import (
    canonical,
    controlled_variants,
    load_fixture,
    reference_program,
    source_events,
    source_query,
)
from asim_forge.reference.workflow import load_bundle, prepare
from asim_forge.reviews import load_review_decisions
from asim_forge.semantic_annotation.artifacts import load_semantic_annotation_tasks
from asim_forge.semantic_annotation.queue import prepare_semantic_annotation_queue

FIXTURE = Path(__file__).resolve().parents[1] / "evaluation/reference/openssh"


def _catalog() -> AsimCatalog:
    fixture, _ = load_fixture(FIXTURE)
    fields = [
        AsimCatalogField(
            name="EventCount", kql_type="int", field_class="Mandatory", schema_name="Authentication"
        ),
        AsimCatalogField(
            name="EventResult",
            kql_type="string",
            field_class="Mandatory",
            schema_name="Authentication",
            allowed_values=["Success", "Failure"],
        ),
        AsimCatalogField(
            name="SrcIpAddr",
            kql_type="string",
            field_class="Recommended",
            schema_name="Authentication",
            logical_type="IP Address",
        ),
    ]
    return AsimCatalog(
        manifest=AsimCatalogManifest(
            source_repository="test",
            source_path="test",
            requested_revision=fixture.catalogue_revision,
            resolved_revision=fixture.catalogue_revision,
            content_sha256="0" * 64,
            schema_count=1,
            field_count=len(fields),
            schemas=["Authentication"],
        ),
        fields=fields,
    )


def _events():
    fixture, _ = load_fixture(FIXTURE)
    seeds = source_events(FIXTURE, fixture)
    return seeds + controlled_variants(seeds)


def test_pinned_native_capture_covers_every_sample_and_variant() -> None:
    fixture, fingerprint = load_fixture(FIXTURE)
    events = _events()
    saved = load_capture(FIXTURE / "reference-output.json")
    verify_capture(
        saved, fixture, fingerprint, events, reference_program(FIXTURE, fixture), kind="reference"
    )
    assert len(events) == 30
    assert sum(event.origin == "public-sample" for event in events) == 20
    assert {event.group_id for event in events} == {fixture.group_id}
    assert all(event.parent_event_id for event in events if event.origin == "controlled-variant")
    assert events[0].values["TimeGenerated"] == "2025-11-22T10:15:30+00:00"
    outputs = {item.event_id: item.output for item in saved.outputs}
    assert len(outputs["sample-001"].rows) == 1
    assert outputs["variant-unrelated-process"].rows == []
    assert outputs["variant-preauth-only"].rows == []


def test_upstream_bytes_cannot_change_unnoticed(tmp_path: Path) -> None:
    path = tmp_path / "fixture"
    shutil.copytree(FIXTURE, path)
    with (path / "parser.yaml").open("ab") as handle:
        handle.write(b"\n# changed\n")
    with pytest.raises(ValueError, match="checksum mismatch"):
        load_fixture(path)


def test_missing_or_path_traversing_resource_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "fixture"
    shutil.copytree(FIXTURE, path)
    manifest = json.loads((path / "manifest.json").read_bytes())
    manifest["helpers"].append("../../secret.yaml")
    (path / "manifest.json").write_bytes(canonical(manifest))
    with pytest.raises(ValueError, match="hash-pinned"):
        load_fixture(path)


@pytest.mark.parametrize("changed", ["input", "program", "missing-event", "query-hash", "kind"])
def test_replay_rejects_stale_or_partial_evidence(changed: str) -> None:
    fixture, fingerprint = load_fixture(FIXTURE)
    events = _events()
    program = reference_program(FIXTURE, fixture)
    saved = load_capture(FIXTURE / "reference-output.json").model_copy(deep=True)
    if changed == "input":
        events[0].values["SyslogMessage"] = "changed"
    elif changed == "program":
        program += "\n// changed"
    elif changed == "missing-event":
        saved.outputs.pop()
    elif changed == "query-hash":
        saved.outputs[0].query_sha256 = "0" * 64
    else:
        saved.kind = "candidate"
    with pytest.raises(ValueError):
        verify_capture(saved, fixture, fingerprint, events, program, kind="reference")


def test_capture_checksum_detects_changed_values(tmp_path: Path) -> None:
    path = tmp_path / "capture.json"
    saved = load_capture(FIXTURE / "reference-output.json")
    write_capture(path, saved)
    content = json.loads(path.read_bytes())
    content["outputs"][0]["output"]["rows"][0]["SrcIpAddr"] = "192.0.2.9"
    path.write_bytes(canonical(content))
    with pytest.raises(ValueError, match="checksum"):
        load_capture(path)


def test_kql_input_escapes_untrusted_source_strings() -> None:
    fixture, _ = load_fixture(FIXTURE)
    event = _events()[0]
    event.values["SyslogMessage"] = 'quote"; .drop table X; \\ newline\n</script>'
    query = source_query(fixture, event, "Syslog | count")
    assert json.dumps(event.values["SyslogMessage"]) in query
    assert query.endswith("Syslog | count")
    assert "datetime(2025-11-22T10:15:30+00:00)" in query


def test_native_output_rows_must_match_their_columns() -> None:
    with pytest.raises(ValidationError, match="declared result columns"):
        QueryOutput(columns={"a": "int"}, rows=[{"other": 1}])


def _response():
    return [
        {
            "FrameType": "DataTable",
            "TableKind": "PrimaryResult",
            "Columns": [{"ColumnName": "SrcIpAddr", "ColumnType": "string"}],
            "Rows": [["192.0.2.1"]],
        },
        {"FrameType": "DataSetCompletion", "HasErrors": False, "Cancelled": False},
    ]


def test_native_response_requires_complete_successful_output() -> None:
    assert parse_response(_response()).rows == [{"SrcIpAddr": "192.0.2.1"}]
    for payload in (
        {"error": "failed"},
        _response()[:1],
        [*_response()[:-1], {"FrameType": "DataSetCompletion", "HasErrors": True}],
        [*_response()[:-1], {"FrameType": "DataSetCompletion"}],
        [*_response(), _response()[0]],
    ):
        with pytest.raises(ValueError):
            parse_response(payload)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://example.com",
        "http://10.0.0.1:8080",
        "http://localhost:8080/remote",
        "http://user:password@127.0.0.1:8080",
        "http://127.0.0.1:8080?remote=true",
    ],
)
def test_runtime_is_explicitly_local(endpoint: str) -> None:
    with pytest.raises(ValueError, match="loopback"):
        KustoClient(endpoint, "fixture")


def test_value_comparison_detects_direction_swap_type_and_row_multiplicity() -> None:
    columns = {"SrcIpAddr": "string", "DstIpAddr": "string", "EventCount": "int"}
    row = {"SrcIpAddr": "192.0.2.1", "DstIpAddr": "192.0.2.2", "EventCount": 1}
    expected = QueryOutput(columns=columns, rows=[row])
    swapped = QueryOutput(
        columns=columns,
        rows=[{**row, "SrcIpAddr": row["DstIpAddr"], "DstIpAddr": row["SrcIpAddr"]}],
    )
    differences = compare_outputs("event", expected, swapped)
    assert {diff.field for diff in differences if diff.kind == "value"} == {
        "SrcIpAddr",
        "DstIpAddr",
    }
    wrong_type = QueryOutput(
        columns={**columns, "EventCount": "bool"}, rows=[{**row, "EventCount": True}]
    )
    assert {diff.kind for diff in compare_outputs("event", expected, wrong_type)} == {
        "type",
        "value",
    }
    duplicate = QueryOutput(columns=columns, rows=[row, row])
    assert any(diff.kind == "row-count" for diff in compare_outputs("event", expected, duplicate))
    reordered = QueryOutput(columns=columns, rows=[row, swapped.rows[0]])
    assert not compare_outputs(
        "event", reordered, reordered.model_copy(update={"rows": reordered.rows[::-1]})
    )


def test_missing_column_and_dropped_event_remain_visible() -> None:
    expected = QueryOutput(columns={"EventCount": "int"}, rows=[{"EventCount": 1}])
    actual = QueryOutput(columns={}, rows=[])
    assert {diff.kind for diff in compare_outputs("event", expected, actual)} == {
        "row-count",
        "missing-field",
    }


def test_conformance_does_not_assume_reference_is_correct() -> None:
    fixture, _ = load_fixture(FIXTURE)
    output = QueryOutput(
        columns={"EventResult": "string", "SrcIpAddr": "string"},
        rows=[{"EventResult": "made-up", "SrcIpAddr": "not-an-ip"}],
    )
    issues = conformance("event", output, fixture, _catalog())
    assert {issue.kind for issue in issues} == {"enum", "logical-type", "missing-mandatory"}


def test_native_replay_self_comparison_is_exact_but_keeps_conformance_findings() -> None:
    fixture, _ = load_fixture(FIXTURE)
    saved = load_capture(FIXTURE / "reference-output.json")
    report = compare_captures(
        saved, saved.model_copy(update={"kind": "candidate"}), fixture, _events(), _catalog()
    )
    assert not report.differences
    assert report.slices["public-sample"]["exact_outputs"] == 20
    assert report.slices["controlled-variant"]["exact_outputs"] == 10
    assert report.reference_issues == report.candidate_issues


@pytest.fixture
def review_bundle(tmp_path: Path):
    path = tmp_path / "review"
    bundle = prepare(FIXTURE, path)
    return path, bundle


def test_prepare_uses_source_only_potato_bundle_and_preserves_existing_work(review_bundle) -> None:
    path, bundle = review_bundle
    loaded, events = load_bundle(path)
    assert loaded == bundle and len(events) == 30
    items = [
        json.loads(line) for line in (path / "build/potato/items.jsonl").read_bytes().splitlines()
    ]
    config = yaml.safe_load((path / "build/potato/config.yaml").read_text(encoding="utf-8"))
    assert len(items) == bundle.cluster_count
    assert config["data_files"] == ["items.jsonl"]
    assert config["annotation_schemes"][0]["name"] == "cluster_decision"
    assert "schema_suggestion" not in json.dumps(items)
    assert "EventResult" not in json.dumps(items)
    assert not (path / "review.html").exists()
    assert not list(path.rglob("*reviews.jsonl"))
    with pytest.raises(ValueError, match="empty output directory"):
        prepare(FIXTURE, path)


def test_potato_state_feeds_existing_annotation_queue_and_compile_without_conversion(
    review_bundle, tmp_path: Path
) -> None:
    path, bundle = review_bundle
    items = [
        json.loads(line) for line in (path / "build/potato/items.jsonl").read_bytes().splitlines()
    ]
    statuses = ["approved", "rejected", "needs_split", "insufficient_evidence"]
    state = {
        "user_id": "test reviewer",
        "instance_id_to_label_to_value": {
            item["id"]: [
                [{"schema": "cluster_decision", "name": status}, True],
                [{"schema": "review_notes", "name": "text_box"}, "Test-only review"],
            ]
            for item, status in zip(items, statuses)
        },
    }
    reviews = tmp_path / "user_state.json"
    reviews.write_bytes(canonical(state))
    decisions = load_review_decisions(reviews)
    assert decisions[0].schema_name is None
    assert decisions[0].field_mappings == []
    manifest = compile_reviews(path / "build/clusters.jsonl", reviews, tmp_path / "compiled")
    assert manifest.compiled_count == 0
    assert manifest.skipped_reviews == {
        "awaiting_mapping": 1,
        "rejected": 1,
        "needs_split": 1,
        "insufficient_evidence": 1,
    }
    queue_dir = tmp_path / "queue"
    queue = prepare_semantic_annotation_queue(
        path / "build",
        reviews,
        queue_dir,
        _catalog(),
        group_id="openbsd-openssh",
        group_strategy="source-family",
    )
    tasks = load_semantic_annotation_tasks(queue_dir / "tasks.jsonl")
    assert queue.task_count == 1
    assert queue.unreviewed_cluster_count == bundle.cluster_count - len(statuses)
    assert tasks[0].case_id == items[0]["id"]
    assert tasks[0].provenance.reviewer_ref == "test reviewer"
    assert tasks[0].provenance.cluster_file_sha256 == bundle.files["build/clusters.jsonl"]
    assert "schema_suggestion" not in tasks[0].model_dump_json()


@pytest.mark.parametrize(
    "changed", ["input/source.log", "build/potato/items.jsonl", "build/potato/config.yaml"]
)
def test_reference_evidence_pins_the_standard_review_inputs(review_bundle, changed: str) -> None:
    path, _ = review_bundle
    (path / changed).write_text("modified", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum mismatch"):
        load_bundle(path)


def test_cli_prepare_points_to_existing_potato_workflow(tmp_path: Path, capsys) -> None:
    path = tmp_path / "cli-review"
    main(["reference", "prepare", str(FIXTURE), "--output", str(path)])
    output = capsys.readouterr().out
    assert "uv run potato start" in output
    assert str((path / "build/potato/config.yaml").resolve()) in output
    assert "user_state.json directly with evaluation queue or compile" in output
