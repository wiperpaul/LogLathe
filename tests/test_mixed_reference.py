import json
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from asim_forge.evaluation_splits import load_semantic_dataset_split
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
    "cisco-ise-authentication": (30, 0),
    "cisco-ise-networksession": (20, 1),
    "cisco-ise-auditevent": (20, 20),
    "barracuda-waf-authentication": (15, 15),
    "barracuda-waf-networksession": (15, 15),
    "barracuda-waf-auditevent": (15, 15),
}


@pytest.mark.parametrize("name", COUNTS)
def test_mixed_native_evidence_is_complete_even_for_dropped_inputs(name):
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
    # An empty capture is retained, not turned into Unsupported or a filename label.
    assert all(
        row["EventSchema"] == fixture.schema_name
        for item in saved.outputs
        for row in item.output.rows
    )
    if name == "barracuda-waf-networksession":
        assert all(row["EventUid"] == "" for item in saved.outputs for row in item.output.rows)


def test_fixture_split_covers_every_source_once_and_keeps_product_schemas_together():
    split = load_semantic_dataset_split(ROOT / "mixed-split.json")
    fixtures = [load_fixture(p.parent)[0] for p in ROOT.glob("*/manifest.json")]
    by_id = {fixture.fixture_id: fixture for fixture in fixtures}
    assert set(by_id) == {entry.case_id for entry in split.entries}
    counts = Counter()
    for entry in split.entries:
        fixture = by_id[entry.case_id]
        assert entry.group_id == fixture.group_id
        assert fixture.catalogue_revision == split.catalogue_revision
        counts[entry.partition] += 1
    assert counts == {"train": 1, "validation": 3, "test": 3}
    assert {entry.partition for entry in split.entries if entry.group_id == "barracuda-waf"} == {
        "test"
    }
    changed = split.model_dump()
    changed["entries"][0]["partition"] = "validation"
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
