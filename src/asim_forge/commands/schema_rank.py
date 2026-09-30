"""CLI for opt-in Jev schema experiments using existing case and queue contracts."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from ..catalog import load_catalog
from ..evaluation import EvaluationError, load_semantic_mapping_cases
from ..evaluation_splits import (
    load_semantic_dataset_split,
    select_semantic_split,
    validate_semantic_case_groups,
)
from ..schema_ranking.jev import DEFAULT_MODEL, SCHEMA_DEFINITIONS
from ..schema_ranking.jev_experiment import (
    SchemaExperimentInput,
    from_labelled_case,
    run_schema_experiment,
)
from ..semantic_annotation import validate_semantic_promotion_artifacts
from ..semantic_annotation.artifacts import load_semantic_annotation_queue


def register_schema_rank_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "schema-rank", help="Prepare, run, or replay an advisory Jev schema experiment"
    )
    parser.add_argument(
        "input", type=Path, help="Existing cases JSONL or annotation queue directory"
    )
    parser.add_argument("--input-kind", choices=("cases", "queue"), default="cases")
    parser.add_argument("--catalog", required=True, type=Path, help="Existing pinned catalogue")
    parser.add_argument("--output", required=True, type=Path, help="New experiment directory")
    parser.add_argument("--cache", type=Path, default=Path("artifacts/jev-cache"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="Send source evidence to TypeSafe's API")
    mode.add_argument(
        "--replay", action="store_true", help="Read cached responses only; no network"
    )
    parser.add_argument("--context", choices=("template", "enriched"), default="enriched")
    parser.add_argument("--nouls", action="store_true", help="Add independent primary-event probes")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Pinned Jev model version")
    parser.add_argument("--limit", type=int, help="Use only the first N selected inputs")
    parser.add_argument("--split", type=Path, help="Existing grouped split manifest (cases only)")
    parser.add_argument(
        "--partition", choices=("validation", "test"), help="Default with --split: test"
    )
    parser.add_argument("--case-groups", type=Path)
    parser.add_argument("--promotion-manifest", type=Path)


def run_schema_rank_command(args: argparse.Namespace) -> None:
    catalog = load_catalog(args.catalog)
    revision = catalog.manifest.resolved_revision
    if not set(SCHEMA_DEFINITIONS).issubset(catalog.manifest.schemas):
        raise EvaluationError("Catalogue must contain all three Jev experiment candidate schemas")
    if args.limit is not None and args.limit < 1:
        raise EvaluationError("--limit must be positive")
    if args.split is None and (args.case_groups or args.promotion_manifest):
        raise EvaluationError("--case-groups and --promotion-manifest require --split")
    if args.partition is not None and args.split is None:
        raise EvaluationError("--partition requires --split")
    split_provenance = None
    if args.input_kind == "queue":
        if args.split:
            raise EvaluationError("Grouped splits require labelled cases, not an annotation queue")
        manifest, tasks = load_semantic_annotation_queue(args.input)
        if manifest.catalogue_revision != revision:
            raise EvaluationError("Queue and catalogue revisions differ")
        inputs = [SchemaExperimentInput(case_id=task.case_id, source=task.input) for task in tasks]
    else:
        cases = load_semantic_mapping_cases(args.input)
        if any(case.catalogue_revision != revision for case in cases):
            raise EvaluationError("Cases and catalogue revisions differ")
        if args.split:
            if not args.case_groups or not args.promotion_manifest:
                raise EvaluationError(
                    "Grouped evaluation requires --case-groups and --promotion-manifest"
                )
            split = load_semantic_dataset_split(args.split)
            groups = validate_semantic_promotion_artifacts(
                args.input, args.case_groups, args.promotion_manifest
            )
            validate_semantic_case_groups(cases, split, groups)
            partition = args.partition or "test"
            selection = select_semantic_split(cases, split, partition)
            cases = selection.evaluation_cases
            split_provenance = {"split_id": selection.split_id, "partition": partition}
        inputs = [from_labelled_case(case) for case in cases]
    if args.limit is not None:
        inputs = inputs[: args.limit]
    report = run_schema_experiment(
        inputs,
        catalogue_revision=revision,
        output=args.output,
        cache=args.cache,
        mode="live" if args.live else "replay" if args.replay else "prepare",
        context=args.context,
        nouls=args.nouls,
        model=args.model,
        api_key=os.environ.get("TYPESAFE_API_KEY", "") if args.live else "",
        split_provenance=split_provenance,
    )
    print(
        f"Jev {report['mode']}: {len(inputs)} input(s), {report['completed']} completed, "
        f"{report['cache_hits']} cache hit(s). Artifacts: {args.output}"
    )
