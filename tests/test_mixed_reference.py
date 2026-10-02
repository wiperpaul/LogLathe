import csv
import json
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from asim_forge.evaluation_splits import load_semantic_dataset_split
from asim_forge.ingestion import read_events
from asim_forge.reference.contracts import ReferenceFixture
from asim_forge.reference.execution import load_capture, verify_capture
from asim_forge.reference.fixtures import (
    load_fixture,
    reference_program,
    source_events,
    source_query,
)
from asim_forge.reference.workflow import prepare

ROOT = Path(__file__).resolve().parents[1] / "evaluation/reference"
COUNTS = {
    "cisco-meraki-authentication": (18, 18),
    "cisco-meraki-networksession": (13, 13),
    "cisco-meraki-auditevent": (21, 21),
    "barracuda-waf-authentication": (15, 15),
    "barracuda-waf-networksession": (15, 15),
    "barracuda-waf-auditevent": (15, 15),
    "carbonblack-cloud-authentication": (16, 16),
    "carbonblack-cloud-networksession": (32, 32),
    "carbonblack-cloud-auditevent": (66, 66),
    "cisco-asa-authentication": (217, 217),
    "cisco-asa-networksession": (30, 27),
}


@pytest.mark.parametrize("name", COUNTS)
def test_mixed_native_evidence_covers_every_selected_fixture_input(name):
    path = ROOT / name
    fixture, fingerprint = load_fixture(path)
    events = source_events(path, fixture)
    saved = load_capture(path / "reference-output.json")
    verify_capture(
        saved, fixture, fingerprint, events, reference_program(path, fixture), kind="reference"
    )
    inputs, selected = COUNTS[name]
    assert len(events) == len(saved.outputs) == inputs
    assert sum(bool(item.output.rows) for item in saved.outputs) == selected
    assert all(event.origin == "public-sample" for event in events)
    assert fixture.mutation_suite == "none"
    # Check actual emitted schemas, rather than treating sample filenames as labels.
    assert all(
        row["EventSchema"] == fixture.schema_name
        for item in saved.outputs
        for row in item.output.rows
    )
    if name == "barracuda-waf-networksession":
        assert all(row["EventUid"] == "" for item in saved.outputs for row in item.output.rows)


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("mixed-split.json", {"train": 1, "validation": 3, "test": 3}),
        ("expanded-split.json", {"train": 1, "validation": 6, "test": 5}),
    ],
)
def test_fixture_split_covers_every_source_once_and_keeps_product_schemas_together(
    filename, expected
):
    split = load_semantic_dataset_split(ROOT / filename)
    fixtures = [load_fixture(p.parent)[0] for p in ROOT.glob("*/manifest.json")]
    by_id = {fixture.fixture_id: fixture for fixture in fixtures}
    selected = {entry.case_id for entry in split.entries}
    assert selected <= set(by_id)
    if filename == "expanded-split.json":
        assert set(by_id) == selected
    counts = Counter()
    for entry in split.entries:
        fixture = by_id[entry.case_id]
        assert entry.group_id == fixture.group_id
        assert fixture.catalogue_revision == split.catalogue_revision
        counts[entry.partition] += 1
    assert counts == expected
    assert {entry.partition for entry in split.entries if entry.group_id == "barracuda-waf"} == {
        "test" if filename == "mixed-split.json" else "validation"
    }
    changed = split.model_dump()
    changed["entries"][0]["partition"] = "train"
    with pytest.raises(ValidationError, match="groups cannot cross"):
        type(split).model_validate(changed)


def test_export_headers_numeric_nulls_and_explicit_setup(tmp_path):
    path = ROOT / "barracuda-waf-networksession"
    fixture, _ = load_fixture(path)
    event = source_events(path, fixture)[0]
    assert event.values["TimeGenerated"] == "2023-06-11T17:40:00+00:00"
    assert event.values["EventID_d"] is None
    assert type(event.values["SourcePort_d"]) is float
    query = source_query(fixture, event, reference_program(path, fixture))
    assert "real(null)" in query
    assert 'let _ItemId = "";' in query
    assert "TimeGenerated [UTC]" not in query
    bundle = prepare(path, tmp_path / "prepared")
    assert bundle.event_count == bundle.public_sample_count == 15
    assert bundle.controlled_variant_count == 0


def test_aliases_and_setup_cannot_bypass_manifest_validation():
    data = json.loads((ROOT / "barracuda-waf-networksession/manifest.json").read_text())
    with pytest.raises(ValidationError, match="version 2"):
        ReferenceFixture.model_validate({**data, "format_version": "1"})
    with pytest.raises(ValidationError, match="hash-pinned"):
        ReferenceFixture.model_validate({**data, "setup_file": "untracked.kql"})
    with pytest.raises(ValidationError, match="unique"):
        ReferenceFixture.model_validate({**data, "csv_column_names": {"Computer": "TenantId"}})


@pytest.mark.parametrize("schema", ["authentication", "networksession", "auditevent"])
def test_carbonblack_adapter_only_adds_empty_loganalytics_item_id(schema):
    path = ROOT / f"carbonblack-cloud-{schema}"
    fixture, _ = load_fixture(path)
    with (path / "source.csv").open(encoding="utf-8-sig", newline="") as handle:
        originals = list(csv.DictReader(handle))
    with (path / fixture.csv_file).open(encoding="utf-8", newline="") as handle:
        ingested = list(csv.DictReader(handle))
    assert ingested == [{**row, "_ItemId": ""} for row in originals]
    assert all(
        row["EventUid"] == ""
        for item in load_capture(path / "reference-output.json").outputs
        for row in item.output.rows
    )


def test_blank_messages_keep_source_row_identity_and_native_coverage(tmp_path):
    path = ROOT / "carbonblack-cloud-networksession"
    fixture, _ = load_fixture(path)
    bundle = prepare(path, tmp_path / "prepared")
    events, _ = read_events(tmp_path / "prepared/input")
    originals = {e.source_row: e for e in source_events(path, fixture)}
    build = json.loads((tmp_path / "prepared/build/manifest.json").read_text())
    assert bundle.event_count == bundle.public_sample_count == 32
    assert build["event_count"] == len(events) == 16
    assert [e.line_number for e in events] == list(range(17, 33))
    assert all(e.text == originals[e.line_number].values[fixture.message_field] for e in events)
    clusters = [
        json.loads(line)
        for line in (tmp_path / "prepared/build/clusters.jsonl").read_text().splitlines()
    ]
    assert sum(c["event_count"] for c in clusters) == 16
    assert all(
        sample["text"] == originals[sample["line_number"]].values[fixture.message_field]
        for cluster in clusters
        for sample in cluster["representative_events"]
    )


def test_boolean_cells_and_nullable_datetimes_preserve_kusto_types(tmp_path):
    path = ROOT / "carbonblack-cloud-authentication"
    fixture, _ = load_fixture(path)
    original = source_events(path, fixture)[0]
    assert original.values["flagged_b"] is False
    assert "false" in source_query(fixture, original, "CarbonBlackAuditLogs_CL")
    original.values["flagged_b"] = None
    assert "bool(null)" in source_query(fixture, original, "CarbonBlackAuditLogs_CL")
    for invalid in ("false", 0, 1):
        original.values["flagged_b"] = invalid
        with pytest.raises(ValueError, match="Expected boolean"):
            source_query(fixture, original, "CarbonBlackAuditLogs_CL")
    with (path / fixture.csv_file).open(encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    edited = fixture.model_copy(update={"csv_file": "source.csv"})
    for value, expected in (("TRUE", True), ("false", False), ("", None), ("0", "invalid")):
        row["flagged_b"] = value
        row["TimeGenerated [UTC]"] = "8/8/2023, 6:45:30 AM"
        with (tmp_path / "source.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)
        if expected == "invalid":
            with pytest.raises(ValueError, match="Invalid boolean.*row 1: flagged_b"):
                source_events(tmp_path, edited)
        else:
            event = source_events(tmp_path, edited)[0]
            assert event.values["flagged_b"] is expected
            assert event.values["TimeGenerated"] == "2023-08-08T06:45:30+00:00"
    asa, _ = load_fixture(ROOT / "cisco-asa-authentication")
    event = source_events(ROOT / "cisco-asa-authentication", asa)[0]
    assert event.values["EndTime"] is None
    assert "datetime(null)" in source_query(asa, event, "CommonSecurityLog")
