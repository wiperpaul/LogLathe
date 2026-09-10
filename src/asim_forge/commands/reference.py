"""CLI for local functional parser fixtures and the first human review."""

from __future__ import annotations

import argparse
from pathlib import Path

from ..catalog import load_catalog
from ..reference.comparison import compare_captures
from ..reference.execution import KustoClient, capture, load_capture, verify_capture, write_capture
from ..reference.fixtures import canonical, load_fixture, reference_program
from ..reference.workflow import load_bundle, prepare


def register_reference_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    reference = subparsers.add_parser(
        "reference", help="Prepare and replay ASIM parser functional fixtures"
    )
    commands = reference.add_subparsers(dest="reference_command", required=True)
    prepare_parser = commands.add_parser(
        "prepare", help="Prepare public samples and a blinded source review"
    )
    prepare_parser.add_argument("fixture", type=Path)
    prepare_parser.add_argument("--output", type=Path, required=True)
    run = commands.add_parser(
        "capture", help="Execute the pinned reference or a candidate on a local Kusto engine"
    )
    run.add_argument("fixture", type=Path)
    run.add_argument("bundle", type=Path)
    run.add_argument(
        "--candidate",
        type=Path,
        help="Candidate KQL; omit to capture the pinned upstream reference",
    )
    run.add_argument("--endpoint", default="http://127.0.0.1:18080")
    run.add_argument("--database", default="LogLatheReference")
    run.add_argument(
        "--runtime-identity",
        required=True,
        help="Container image digest or equivalent immutable engine identity",
    )
    run.add_argument("--output", type=Path, required=True)
    compare = commands.add_parser(
        "compare", help="Compare complete native captures offline, retaining conformance failures"
    )
    compare.add_argument("fixture", type=Path)
    compare.add_argument("bundle", type=Path)
    compare.add_argument("--reference", type=Path, required=True)
    compare.add_argument(
        "--candidate",
        type=Path,
        required=True,
        help="Exact candidate KQL that produced the candidate capture",
    )
    compare.add_argument("--candidate-capture", type=Path, required=True)
    compare.add_argument("--catalog", type=Path, required=True)
    compare.add_argument("--output", type=Path, required=True)


def run_reference_command(args: argparse.Namespace) -> None:
    if args.reference_command == "prepare":
        bundle = prepare(args.fixture, args.output)
        print(
            f"Prepared {bundle.public_sample_count} public samples, "
            f"{bundle.controlled_variant_count} controlled variants, "
            f"and {bundle.cluster_count} clusters."
        )
        config = (args.output / "build/potato/config.yaml").resolve()
        print(f'Cluster review: uv run potato start "{config}" -p 8000')
        print(
            "Use Potato's user_state.json directly with evaluation queue or compile; "
            "cluster approval alone still awaits mapping."
        )
        return
    fixture, fixture_hash = load_fixture(args.fixture)
    bundle, events = load_bundle(args.bundle)
    if bundle.fixture_sha256 != fixture_hash:
        raise ValueError("Prepared bundle refers to a different fixture revision")
    upstream = reference_program(args.fixture, fixture)
    if (args.bundle / "reference.kql").read_text(encoding="utf-8") != upstream:
        raise ValueError("Prepared reference program differs from the pinned upstream bodies")
    candidate = args.candidate.read_text(encoding="utf-8") if args.candidate else None
    if args.reference_command == "capture":
        if args.output.exists():
            raise ValueError("Choose a new output path to preserve existing native evidence")
        saved = capture(
            fixture,
            fixture_hash,
            events,
            candidate if candidate is not None else upstream,
            KustoClient(args.endpoint, args.database),
            kind="candidate" if candidate is not None else "reference",
            runtime_identity=args.runtime_identity,
        )
        write_capture(args.output, saved)
        print(f"Captured native Kusto outputs for {len(saved.outputs)} events in {args.output}.")
        return
    assert candidate is not None
    reference_capture = load_capture(args.reference)
    candidate_capture = load_capture(args.candidate_capture)
    verify_capture(reference_capture, fixture, fixture_hash, events, upstream, kind="reference")
    verify_capture(candidate_capture, fixture, fixture_hash, events, candidate, kind="candidate")
    report = compare_captures(
        reference_capture, candidate_capture, fixture, events, load_catalog(args.catalog)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(report.model_dump(mode="json")))
    for name, counts in report.slices.items():
        print(
            f"{name}: {counts['exact_outputs']}/{counts['events']} exact outputs; "
            f"dropped rows={counts['dropped_rows']}; extra rows={counts['extra_rows']}"
        )
    print(
        f"Conformance issues: reference={len(report.reference_issues)}, "
        f"candidate={len(report.candidate_issues)}"
    )
    print(
        f"Functional comparison written to {args.output}; "
        "no semantic-gold or production-quality claim."
    )
