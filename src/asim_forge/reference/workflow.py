"""Adapt pinned source-table fixtures to the existing build and Potato workflow."""

from __future__ import annotations

from pathlib import Path

from ..pipeline import build_review_bundle
from .contracts import PreparedBundle, ReferenceEvent
from .fixtures import (
    canonical,
    controlled_variants,
    digest,
    event_fingerprint,
    load_fixture,
    reference_program,
    source_events,
)

_EVIDENCE_FILES = (
    "events.jsonl",
    "reference.kql",
    "input/source.log",
    "build/clusters.jsonl",
    "build/manifest.json",
    "build/schema-rankings.jsonl",
    "build/potato/items.jsonl",
    "build/potato/config.yaml",
)


def prepare(fixture_dir: Path, output_dir: Path) -> PreparedBundle:
    fixture, fixture_hash = load_fixture(fixture_dir)
    seeds = source_events(fixture_dir, fixture)
    events = seeds + controlled_variants(seeds)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("Use an empty output directory to preserve existing review evidence")
    input_dir = output_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "source.log").write_text(
        "\n".join(str(event.values[fixture.message_field]) for event in seeds) + "\n",
        encoding="utf-8",
    )
    build = build_review_bundle(input_dir, output_dir / "build", system=fixture.group_id)
    (output_dir / "events.jsonl").write_bytes(
        b"".join(canonical(event.model_dump(mode="json")) for event in events)
    )
    (output_dir / "reference.kql").write_text(
        reference_program(fixture_dir, fixture), encoding="utf-8"
    )
    bundle = PreparedBundle(
        fixture_sha256=fixture_hash,
        events_sha256=event_fingerprint(events),
        event_count=len(events),
        public_sample_count=len(seeds),
        controlled_variant_count=len(events) - len(seeds),
        cluster_count=build.cluster_count,
        files={name: digest((output_dir / name).read_bytes()) for name in _EVIDENCE_FILES},
    )
    (output_dir / "prepared-manifest.json").write_bytes(canonical(bundle.model_dump(mode="json")))
    return bundle


def load_bundle(path: Path) -> tuple[PreparedBundle, list[ReferenceEvent]]:
    bundle = PreparedBundle.model_validate_json((path / "prepared-manifest.json").read_bytes())
    if set(bundle.files) != set(_EVIDENCE_FILES):
        raise ValueError("Prepared bundle has missing or unexpected evidence files")
    for name, expected in bundle.files.items():
        target = (path / name).resolve()
        if not target.is_relative_to(path.resolve()) or digest(target.read_bytes()) != expected:
            raise ValueError(f"Prepared bundle checksum mismatch: {name}")
    events = [
        ReferenceEvent.model_validate_json(line)
        for line in (path / "events.jsonl").read_bytes().splitlines()
    ]
    if (
        len(events) != bundle.event_count
        or event_fingerprint(events) != bundle.events_sha256
        or len({event.event_id for event in events}) != len(events)
    ):
        raise ValueError("Prepared event evidence does not match its manifest")
    return bundle, events
