"""Prepare assisted mapping review inside the existing Potato workflow."""

from __future__ import annotations

import argparse
from pathlib import Path

from ..mapping_review import load_mapping_setup, prepare_mapping_review


def register_review_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    review = subparsers.add_parser(
        "review", help="Continue approved clusters into Potato mapping review"
    )
    commands = review.add_subparsers(dest="review_command", required=True)
    prepare = commands.add_parser(
        "prepare", help="Prepare editable mapping suggestions from an existing annotation queue"
    )
    prepare.add_argument("queue", type=Path)
    prepare.add_argument("--catalog", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument(
        "--setup", type=Path, help="JSON setup with schema_versions and time_generated policy"
    )
    prepare.add_argument(
        "--approach",
        choices=("semantic-frame", "direct-lexical", "matcher-ensemble"),
        default="semantic-frame",
    )
    prepare.add_argument("--reference-fixture", type=Path)
    prepare.add_argument("--reference-bundle", type=Path)
    prepare.add_argument("--reference-capture", type=Path)
    prepare.add_argument(
        "--cluster-reviews",
        type=Path,
        help="Include notes from the queue's original cluster-review state",
    )


def run_review_command(args: argparse.Namespace) -> None:
    manifest = prepare_mapping_review(
        args.queue,
        args.catalog,
        args.output,
        approach_name=args.approach,
        fixture_dir=args.reference_fixture,
        reference_bundle=args.reference_bundle,
        reference_capture=args.reference_capture,
        cluster_reviews=args.cluster_reviews,
        setup=load_mapping_setup(args.setup) if args.setup else None,
    )
    config = (args.output / "potato/config.yaml").resolve()
    print(f"Prepared {manifest.task_count} assisted mapping tasks; no mapping approvals created.")
    print(f'Continue in Potato: uv run potato start "{config}" -p 8000')
    print("Compile the saved Potato state with --mapping-bundle pointing to this review directory.")
