"""Join verified native reference output to already reviewed source examples."""

from __future__ import annotations

from pathlib import Path

from pydantic import JsonValue

from ..models import AsimCatalog, StrictModel
from ..semantic_annotation.contracts import SemanticAnnotationTask
from .comparison import conformance
from .contracts import ConformanceIssue, QueryOutput
from .execution import load_capture, verify_capture
from .fixtures import digest, load_fixture, reference_program
from .workflow import load_bundle


class ReferenceReviewExample(StrictModel):
    event_id: str
    source_values: dict[str, JsonValue]
    output: QueryOutput


class ReferenceReviewEvidence(StrictModel):
    capture_sha256: str
    schema_name: str
    schema_version: str
    source_columns: dict[str, str]
    examples: list[ReferenceReviewExample]
    issues: list[ConformanceIssue]


def reference_review_evidence(
    fixture_dir: Path,
    bundle_dir: Path,
    capture_path: Path,
    tasks: list[SemanticAnnotationTask],
    catalog: AsimCatalog,
) -> dict[str, ReferenceReviewEvidence]:
    """Keep source row identity and the upstream disagreements visible to engineering."""
    fixture, fingerprint = load_fixture(fixture_dir)
    bundle, events = load_bundle(bundle_dir)
    if (
        bundle.fixture_sha256 != fingerprint
        or catalog.manifest.resolved_revision != fixture.catalogue_revision
    ):
        raise ValueError("Reference review inputs must use the same fixture and catalogue revision")
    capture = load_capture(capture_path)
    verify_capture(
        capture,
        fixture,
        fingerprint,
        events,
        reference_program(fixture_dir, fixture),
        kind="reference",
    )
    seeds = {event.source_row: event for event in events if event.origin == "public-sample"}
    outputs = {item.event_id: item.output for item in capture.outputs}
    evidence = {}
    for task in tasks:
        if (
            task.provenance.cluster_file_sha256 != bundle.files["build/clusters.jsonl"]
            or task.provenance.build_manifest_sha256 != bundle.files["build/manifest.json"]
        ):
            raise ValueError("Annotation queue and native reference must share the same build")
        examples, issues = [], []
        for sample in task.input.representative_events:
            event = seeds.get(sample.line_number)
            if event is None or event.values[fixture.message_field] != sample.text:
                raise ValueError("Reference source row does not match the reviewed example")
            output = outputs[event.event_id]
            examples.append(
                ReferenceReviewExample(
                    event_id=event.event_id, source_values=event.values, output=output
                )
            )
            issues.extend(conformance(event.event_id, output, fixture, catalog))
        evidence[task.case_id] = ReferenceReviewEvidence(
            capture_sha256=digest(capture_path.read_bytes()),
            schema_name=fixture.schema_name,
            schema_version=fixture.schema_version,
            source_columns=fixture.columns,
            examples=examples,
            issues=issues,
        )
    return evidence
