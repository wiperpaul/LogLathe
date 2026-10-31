import re

import pytest

from asim_forge.clustering import DeepParseClusterer, _parameter_slots
from asim_forge.compiler import _capture_regex, compile_kql
from asim_forge.models import FieldMapping, ParserSource, ParserSpecification, SourceEvent
from asim_forge.potato_bundle import example_literal_spans, example_slot_spans


@pytest.mark.parametrize(
    "text",
    [
        "Login  user alice from 10.0.0.1",
        "\tLogin\tuser\talice\tfrom\t10.0.0.1\t",
        "  Login user alice   from 10.0.0.1  ",
    ],
)
def test_clustering_review_spans_and_parser_match_original_whitespace(text: str) -> None:
    event = SourceEvent(source_file="test.log", line_number=1, text=text)
    cluster = DeepParseClusterer(system="test").cluster([event]).clusters[0]

    assert cluster.representative_events == [event]
    assert cluster.parameter_slots[0].examples == ["10.0.0.1"]
    specification = ParserSpecification(
        parser_name="vimTest",
        cluster_id=cluster.cluster_id,
        schema_name="Authentication",
        template=cluster.template,
        source=ParserSource(
            vendor="Demo", product="Demo", table="Syslog", message_field="SyslogMessage"
        ),
        field_mappings=[FieldMapping(slot_id="p1", asim_field="SrcIpAddr")],
        reviewer="test",
    )
    regex, groups = _capture_regex(specification)
    found = re.fullmatch(regex, text)
    assert found is not None
    assert found.group(groups["p1"]) == "10.0.0.1"
    assert regex in compile_kql(specification)
    spans = example_slot_spans(cluster.template, [event], cluster.parameter_slots)[0]
    start = text.index("10.0.0.1")
    assert spans == [{"slot_id": "p1", "start": start, "end": start + 8, "text": "10.0.0.1"}]
    for span in example_literal_spans(cluster.template, [event], cluster.parameter_slots)[0]:
        assert text[span["start"] : span["end"]] == span["text"]


@pytest.mark.parametrize(
    ("template", "text", "value"),
    [
        ("User <VAR:TEXT> end", "  User\t Alice  van Smith \tend  ", "Alice  van Smith"),
        ('value="<VAR:TEXT>"', 'value="  Alice  "', "  Alice  "),
        ("<VAR:TEXT>!", "  Alice  !  ", "Alice  "),
        ("[<VAR:TEXT>", "  [  Alice  ", "  Alice"),
        ("<VAR:TEXT>", "  Alice van Smith  ", "Alice van Smith"),
    ],
)
def test_whitespace_separators_do_not_change_captured_values(template, text, value) -> None:
    event = SourceEvent(source_file="test.log", line_number=1, text=text)
    slots = _parameter_slots(template, [event])

    assert slots[0].examples == [value]
    spans = example_slot_spans(template, [event], slots)[0]
    assert len(spans) == 1
    assert spans[0]["text"] == value
    assert text[spans[0]["start"] : spans[0]["end"]] == value
