"""Typed value, row-cardinality, and bounded catalogue conformance checks."""

from __future__ import annotations

import ipaddress
import json
from collections import Counter

from ..models import AsimCatalog
from .contracts import (
    ConformanceIssue,
    Difference,
    NativeCapture,
    OutputComparison,
    QueryOutput,
    ReferenceEvent,
    ReferenceFixture,
)
from .fixtures import canonical, digest


def compare_outputs(event_id: str, expected: QueryOutput, actual: QueryOutput) -> list[Difference]:
    differences = []
    if len(expected.rows) != len(actual.rows):
        differences.append(
            Difference(
                event_id=event_id,
                kind="row-count",
                expected=len(expected.rows),
                actual=len(actual.rows),
            )
        )
    # Empty results agree on event selection; a schema-only mismatch is still visible below.
    for field in sorted(set(expected.columns) | set(actual.columns)):
        if field not in actual.columns:
            differences.append(
                Difference(
                    event_id=event_id,
                    kind="missing-field",
                    field=field,
                    expected=expected.columns[field],
                )
            )
        elif field not in expected.columns:
            differences.append(
                Difference(
                    event_id=event_id, kind="extra-field", field=field, actual=actual.columns[field]
                )
            )
        elif expected.columns[field] != actual.columns[field]:
            differences.append(
                Difference(
                    event_id=event_id,
                    kind="type",
                    field=field,
                    expected=expected.columns[field],
                    actual=actual.columns[field],
                )
            )
    # Treat rows as a multiset, so reordering passes but duplicate/missing events do not.
    expected_rows = Counter(canonical(row) for row in expected.rows)
    actual_rows = Counter(canonical(row) for row in actual.rows)
    unmatched_expected = sorted((expected_rows - actual_rows).elements())
    unmatched_actual = sorted((actual_rows - expected_rows).elements())
    for left, right in zip(unmatched_expected, unmatched_actual):
        before, after = json.loads(left), json.loads(right)
        for field in sorted(set(before) & set(after)):
            if canonical(before[field]) != canonical(after[field]):
                differences.append(
                    Difference(
                        event_id=event_id,
                        kind="value",
                        field=field,
                        expected=before[field],
                        actual=after[field],
                    )
                )
    return differences


def conformance(
    event_id: str,
    output: QueryOutput,
    fixture: ReferenceFixture,
    catalog: AsimCatalog,
) -> list[ConformanceIssue]:
    issues = []
    for field in catalog.fields_for_schema(fixture.schema_name):
        name = field.name
        if name not in output.columns:
            if field.field_class == "Mandatory":
                issues.append(
                    ConformanceIssue(
                        event_id=event_id,
                        field=name,
                        kind="missing-mandatory",
                        detail="Mandatory output column is absent",
                    )
                )
            continue
        if output.columns[name] != field.kql_type:
            issues.append(
                ConformanceIssue(
                    event_id=event_id,
                    field=name,
                    kind="type",
                    detail=f"Expected {field.kql_type}; got {output.columns[name]}",
                )
            )
        for row in output.rows:
            value = row.get(name)
            if value is None or value == "":
                if field.field_class == "Mandatory":
                    issues.append(
                        ConformanceIssue(
                            event_id=event_id,
                            field=name,
                            kind="missing-mandatory",
                            detail="Mandatory value is empty",
                        )
                    )
                continue
            if field.allowed_values and str(value) not in field.allowed_values:
                issues.append(
                    ConformanceIssue(
                        event_id=event_id,
                        field=name,
                        kind="enum",
                        detail=f"{value!r} is outside the pinned enumeration",
                    )
                )
            if field.logical_type == "IP Address":
                try:
                    ipaddress.ip_address(str(value))
                except ValueError:
                    issues.append(
                        ConformanceIssue(
                            event_id=event_id,
                            field=name,
                            kind="logical-type",
                            detail="Value is not an IPv4 or IPv6 address",
                        )
                    )
    return issues


def compare_captures(
    reference: NativeCapture,
    candidate: NativeCapture,
    fixture: ReferenceFixture,
    events: list[ReferenceEvent],
    catalog: AsimCatalog,
) -> OutputComparison:
    if catalog.manifest.resolved_revision != fixture.catalogue_revision:
        raise ValueError("Comparison catalogue revision differs from the fixture")
    if reference.kind != "reference" or candidate.kind != "candidate":
        raise ValueError("Expected one reference capture and one candidate capture")
    if (
        reference.fixture_sha256 != candidate.fixture_sha256
        or reference.events_sha256 != candidate.events_sha256
    ):
        raise ValueError("Captures refer to different fixture inputs")
    before = {item.event_id: item.output for item in reference.outputs}
    after = {item.event_id: item.output for item in candidate.outputs}
    if set(before) != set(after) or set(before) != {event.event_id for event in events}:
        raise ValueError("Captures must cover the same complete fixture event set")
    differences, reference_issues, candidate_issues = [], [], []
    slices: dict[str, dict[str, int]] = {}
    # Ignore source-table pass-through fields unless the pinned catalogue defines them as targets.
    target_names = {field.name for field in catalog.fields_for_schema(fixture.schema_name)}

    def target_output(output: QueryOutput) -> QueryOutput:
        fields = set(output.columns) - (set(fixture.columns) - target_names)
        return QueryOutput(
            columns={key: value for key, value in output.columns.items() if key in fields},
            rows=[
                {key: value for key, value in row.items() if key in fields} for row in output.rows
            ],
        )

    for event in events:
        expected, actual = (
            target_output(before[event.event_id]),
            target_output(after[event.event_id]),
        )
        current = compare_outputs(event.event_id, expected, actual)
        differences.extend(current)
        reference_issues.extend(conformance(event.event_id, expected, fixture, catalog))
        candidate_issues.extend(conformance(event.event_id, actual, fixture, catalog))
        counts = slices.setdefault(
            event.origin,
            {
                "events": 0,
                "exact_outputs": 0,
                "selection_agreements": 0,
                "reference_rows": 0,
                "candidate_rows": 0,
                "dropped_rows": 0,
                "extra_rows": 0,
            },
        )
        counts["events"] += 1
        counts["exact_outputs"] += int(not current)
        counts["selection_agreements"] += int(bool(expected.rows) == bool(actual.rows))
        counts["reference_rows"] += len(expected.rows)
        counts["candidate_rows"] += len(actual.rows)
        counts["dropped_rows"] += max(0, len(expected.rows) - len(actual.rows))
        counts["extra_rows"] += max(0, len(actual.rows) - len(expected.rows))
    return OutputComparison(
        fixture_sha256=reference.fixture_sha256,
        reference_sha256=digest(canonical(reference.model_dump(mode="json"))),
        candidate_sha256=digest(canonical(candidate.model_dump(mode="json"))),
        catalogue_revision=fixture.catalogue_revision,
        slices=slices,
        differences=differences,
        reference_issues=reference_issues,
        candidate_issues=candidate_issues,
        warnings=[
            "Functional regression evidence against a pinned implementation; "
            "not semantic gold or production accuracy.",
            "Public samples and generated variants are separate slices from one source family.",
            "Catalogue checks cover mandatory fields, physical types, enumerations, "
            "and IP addresses; "
            "conditional requirements and other logical rules remain untested.",
            "Reference parser conformance failures are retained for human review, "
            "not silently copied as correctness labels.",
        ],
    )
