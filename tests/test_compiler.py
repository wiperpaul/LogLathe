import json
from pathlib import Path

import pytest

from asim_forge.compiler import compile_kql, compile_reviews
from asim_forge.models import (
    ClusterRecord,
    FieldMapping,
    ParameterSlot,
    ParserSource,
    ParserSpecification,
    SchemaScore,
    SchemaSuggestion,
    SourceEvent,
    order_field_mappings,
)
from asim_forge.reviews import ReviewError


def _write_cluster(path: Path) -> ClusterRecord:
    cluster = ClusterRecord(
        cluster_id="cluster-auth",
        engine_cluster_id=1,
        template="Login user <VAR:TEXT> from <VAR:IPV4>",
        event_count=2,
        representative_events=[
            SourceEvent(
                source_file="auth.log",
                line_number=1,
                text="Login user alice from 10.0.0.1",
            )
        ],
        parameter_slots=[
            ParameterSlot(
                slot_id="p1",
                label="TEXT",
                placeholder="<VAR:TEXT>",
                occurrence=1,
                examples=["alice", "bob"],
            ),
            ParameterSlot(
                slot_id="p2",
                label="IPV4",
                placeholder="<VAR:IPV4>",
                occurrence=1,
                examples=["10.0.0.1", "10.0.0.2"],
            ),
        ],
        schema_suggestion=SchemaSuggestion(
            schema_name="Authentication",
            confidence=1.0,
            ranked_scores=[SchemaScore(schema_name="Authentication", score=1, evidence=["login"])],
        ),
    )
    path.write_text(cluster.model_dump_json() + "\n", encoding="utf-8")
    return cluster


def _review(cluster_id: str, *, slot_id: str = "p2") -> dict[str, object]:
    return {
        "cluster_id": cluster_id,
        "reviewer": "alice",
        "status": "approved",
        "schema_name": "Authentication",
        "parser_name": "vimDemoAuth",
        "vendor": "Demo",
        "product": "Gateway",
        "source_table": "Syslog",
        "message_field": "SyslogMessage",
        "field_mappings": [
            {"slot_id": "p1", "asim_field": "TargetUsername", "transform": "string"},
            {"slot_id": slot_id, "asim_field": "SrcIpAddr", "transform": "string"},
        ],
        "notes": "approved",
    }


def test_compiles_only_approved_review_to_spec_and_kql(tmp_path: Path) -> None:
    clusters_path = tmp_path / "clusters.jsonl"
    reviews_path = tmp_path / "reviews.jsonl"
    output_dir = tmp_path / "compiled"
    cluster = _write_cluster(clusters_path)
    reviews_path.write_text(json.dumps(_review(cluster.cluster_id)) + "\n", encoding="utf-8")

    manifest = compile_reviews(clusters_path, reviews_path, output_dir)

    assert manifest.compiled_count == 1
    specification = json.loads((output_dir / "vimDemoAuth.parser-spec.json").read_text("utf-8"))
    assert specification["reviewer"] == "alice"
    kql = (output_dir / "vimDemoAuth.kql").read_text("utf-8")
    assert "let vimDemoAuth" in kql
    assert "TargetUsername = tostring(_asim_forge_p1)" in kql
    assert "SrcIpAddr = tostring(_asim_forge_p2)" in kql
    assert 'EventSchema = "Authentication"' in kql
    assert r"Login user (.+?) from (.+?)" in kql
    assert r"Login\ user" not in kql


def test_rejects_mapping_to_unknown_parameter_slot(tmp_path: Path) -> None:
    clusters_path = tmp_path / "clusters.jsonl"
    reviews_path = tmp_path / "reviews.jsonl"
    cluster = _write_cluster(clusters_path)
    reviews_path.write_text(
        json.dumps(_review(cluster.cluster_id, slot_id="p9")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ReviewError, match="unknown slots: p9"):
        compile_reviews(clusters_path, reviews_path, tmp_path / "compiled")


def test_reports_stage_one_approval_as_awaiting_mapping(tmp_path: Path) -> None:
    clusters_path = tmp_path / "clusters.jsonl"
    reviews_path = tmp_path / "reviews.jsonl"
    output_dir = tmp_path / "compiled"
    cluster = _write_cluster(clusters_path)
    reviews_path.write_text(
        json.dumps(
            {
                "cluster_id": cluster.cluster_id,
                "reviewer": "cluster-reviewer",
                "status": "approved",
                "notes": "The examples form one coherent pattern.",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    manifest = compile_reviews(clusters_path, reviews_path, output_dir)

    assert manifest.compiled_count == 0
    assert manifest.skipped_reviews == {"awaiting_mapping": 1}
    assert json.loads((output_dir / "compile-manifest.json").read_text("utf-8"))[
        "skipped_reviews"
    ] == {"awaiting_mapping": 1}
    assert list(output_dir.glob("*.kql")) == []


def test_source_columns_are_captured_before_targets_overwrite_them(tmp_path: Path) -> None:
    clusters = tmp_path / "clusters.jsonl"
    cluster = _write_cluster(clusters)
    review = _review(cluster.cluster_id)
    review["field_mappings"] = [
        {"source_field": "SrcIpAddr", "asim_field": "DstIpAddr"},
        {"source_field": "DstIpAddr", "asim_field": "SrcIpAddr"},
        {"source_field": "TimeGenerated", "asim_field": "EventStartTime", "transform": "datetime"},
        {"constant_value": 'quote" backslash\\ newline\n雪', "asim_field": "EventMessage"},
        {"constant_value": False, "asim_field": "Flag", "transform": "bool"},
        {"constant_value": 0, "asim_field": "EventCount", "transform": "int"},
    ]
    state = tmp_path / "reviews.jsonl"
    state.write_text(json.dumps(review) + "\n", encoding="utf-8")
    output = tmp_path / "compiled"
    compile_reviews(clusters, state, output)
    kql = (output / "vimDemoAuth.kql").read_text("utf-8")
    assert kql.index("_asim_forge_source_2 = DstIpAddr") < kql.index("DstIpAddr = tostring(")
    assert "SrcIpAddr = tostring(_asim_forge_source_2)" in kql
    assert "EventStartTime = todatetime(_asim_forge_source_3)" in kql
    assert 'EventMessage = tostring("quote\\" backslash\\\\ newline\\n雪")' in kql
    assert "Flag = tobool(false)" in kql
    assert "EventCount = toint(0)" in kql
    assert "project-away _asim_forge_source_1, _asim_forge_source_2, _asim_forge_source_3" in kql


def _specification(mappings: list[FieldMapping]) -> ParserSpecification:
    return ParserSpecification(
        parser_name="vimDemoAuth",
        cluster_id="cluster-auth",
        schema_name="Authentication",
        template="Login at <VAR:TIMESTAMP>",
        source=ParserSource(
            vendor="Demo", product="Gateway", table="Syslog", message_field="SyslogMessage"
        ),
        field_mappings=mappings,
        reviewer="alice",
    )


@pytest.mark.parametrize(
    ("start_source", "expected_expression"),
    [
        ({"source_field": "TimeGenerated"}, "_asim_forge_source_1"),
        ({"source_field": "SourceTimestamp"}, "_asim_forge_source_1"),
        ({"slot_id": "p1"}, "_asim_forge_p1"),
        ({"constant_value": "2026-09-20T12:00:00Z"}, '"2026-09-20T12:00:00Z"'),
    ],
)
def test_end_time_uses_normalized_start_time_after_its_mapping(
    start_source: dict[str, str], expected_expression: str
) -> None:
    specification = _specification(
        [
            FieldMapping(
                output_field="eventstarttime", asim_field="EventEndTime", transform="datetime"
            ),
            FieldMapping(constant_value=1, asim_field="EventCount", transform="int"),
            FieldMapping.model_validate(
                {**start_source, "asim_field": "EventStartTime", "transform": "datetime"}
            ),
            FieldMapping(
                source_field="EventStartTime", asim_field="OriginalStartTime", transform="datetime"
            ),
        ]
    )

    kql = compile_kql(specification)

    start_assignment = f"EventStartTime = todatetime({expected_expression})"
    end_assignment = "EventEndTime = todatetime(EventStartTime)"
    assert start_assignment in kql
    assert kql.index(start_assignment) < kql.index(end_assignment)
    source_snapshot = kql.index(" = EventStartTime\n")
    assert source_snapshot < kql.index(start_assignment)
    assert "OriginalStartTime = todatetime(_asim_forge_source_" in kql
    assert "EventCount = toint(1)" in kql
    assert specification.field_mappings[0].asim_field == "EventEndTime"


def test_orders_output_mapping_chains_stably_without_changing_input() -> None:
    mappings = [
        FieldMapping(output_field="Middle", asim_field="Last"),
        FieldMapping(constant_value="independent", asim_field="Independent"),
        FieldMapping(output_field="First", asim_field="Middle"),
        FieldMapping(source_field="Input", asim_field="First"),
    ]

    ordered = order_field_mappings(mappings)

    assert [mapping.asim_field for mapping in ordered] == ["Independent", "First", "Middle", "Last"]
    assert mappings[0].asim_field == "Last"
    assert ordered[0] is mappings[1]
    kql = compile_kql(_specification(mappings))
    assert kql.index("First = tostring(") < kql.index("Middle = tostring(First)")
    assert kql.index("Middle = tostring(First)") < kql.index("Last = tostring(Middle)")


@pytest.mark.parametrize(
    ("mappings", "error"),
    [
        ([FieldMapping(output_field="Missing", asim_field="EventEndTime")], "unmapped output"),
        ([FieldMapping(output_field="EVENTENDTIME", asim_field="EventEndTime")], "own output"),
        (
            [
                FieldMapping(output_field="EventStartTime", asim_field="EventEndTime"),
                FieldMapping(output_field="EventEndTime", asim_field="EventStartTime"),
            ],
            "dependency cycle",
        ),
        (
            [
                FieldMapping(constant_value=1, asim_field="EventCount"),
                FieldMapping(constant_value=2, asim_field="eventcount"),
            ],
            "more than once",
        ),
    ],
)
def test_output_dependencies_are_validated_for_direct_compilation(
    mappings: list[FieldMapping], error: str
) -> None:
    with pytest.raises(ValueError, match=error):
        order_field_mappings(mappings)
    with pytest.raises(ReviewError, match=error):
        compile_kql(_specification(mappings))


def test_invalid_output_dependency_is_rejected_before_writing_a_specification(
    tmp_path: Path,
) -> None:
    clusters = tmp_path / "clusters.jsonl"
    cluster = _write_cluster(clusters)
    review = _review(cluster.cluster_id)
    review["field_mappings"] = [
        {"output_field": "EventStartTime", "asim_field": "EventEndTime", "transform": "datetime"}
    ]
    state = tmp_path / "reviews.jsonl"
    state.write_text(json.dumps(review) + "\n", encoding="utf-8")
    output = tmp_path / "compiled"

    with pytest.raises(ReviewError, match="unmapped output: EventStartTime"):
        compile_reviews(clusters, state, output)

    assert list(output.iterdir()) == []


@pytest.mark.parametrize(
    "extra_source",
    [{"slot_id": "p1"}, {"source_field": "TimeGenerated"}, {"constant_value": 1}],
)
def test_output_mapping_is_an_exclusive_source(extra_source: dict[str, str | int]) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        FieldMapping.model_validate(
            {**extra_source, "output_field": "EventStartTime", "asim_field": "EventEndTime"}
        )


def test_output_mapping_name_must_be_an_identifier() -> None:
    with pytest.raises(ValueError):
        FieldMapping(output_field="EventStartTime | take 1", asim_field="EventEndTime")
