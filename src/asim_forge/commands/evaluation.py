"""Parser registration and dispatch for evaluation workflows."""

from __future__ import annotations

import argparse
from pathlib import Path

from ..benchmarking import run_benchmarks
from ..catalog import load_catalog
from ..controlled import run_robustness, write_robustness_report
from ..controlled.robustness import ROBUSTNESS_APPROACH_NAMES
from ..evaluation import EvaluationError, SemanticMappingCase, load_semantic_mapping_cases
from ..evaluation_splits import (
    SemanticDatasetSplit,
    load_semantic_dataset_split,
    validate_semantic_case_groups,
    validate_semantic_dataset_split,
)
from ..semantic_annotation import (
    prepare_semantic_annotation_queue,
    promote_semantic_annotations,
    validate_semantic_promotion_artifacts,
)
from ..semantic_mapping import APPROACH_NAMES
from ..semantic_mapping.comparison import (
    ORACLE_CONDITIONS,
    ComparisonReport,
    compare_approaches,
    compare_split_approaches,
    write_comparison_report,
)
from ..semantic_mapping.context_views import CONTEXT_VIEWS, VIEW_DESCRIPTIONS
from ..semantic_mapping.statistics import DEFAULT_RESAMPLES


def register_evaluation_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register the evaluation command tree on the root parser."""
    evaluation = subparsers.add_parser(
        "evaluation",
        help="Manage provider-neutral semantic mapping evaluation cases",
    )
    evaluation_subparsers = evaluation.add_subparsers(
        dest="evaluation_command",
        required=True,
    )
    evaluation_validate = evaluation_subparsers.add_parser(
        "validate",
        help="Validate canonical semantic mapping case JSONL",
    )
    evaluation_validate.add_argument("cases", type=Path, help="Semantic mapping case JSONL")
    evaluation_validate.add_argument(
        "--split",
        type=Path,
        help="Optional grouped train/validation/test split manifest",
    )
    evaluation_validate.add_argument(
        "--case-groups",
        type=Path,
        help="Pre-label case-groups.jsonl from semantic promotion",
    )
    evaluation_validate.add_argument(
        "--promotion-manifest",
        type=Path,
        help="Promotion manifest that authenticates the cases and case groups",
    )
    evaluation_queue = evaluation_subparsers.add_parser(
        "queue",
        help="Export approved clusters as a blinded semantic annotation queue",
    )
    evaluation_queue.add_argument(
        "build_dir",
        type=Path,
        help="Build directory containing manifest.json and clusters.jsonl",
    )
    evaluation_queue.add_argument(
        "reviews",
        type=Path,
        help="Canonical cluster-review JSONL or a Potato user_state.json",
    )
    evaluation_queue.add_argument(
        "--catalog",
        type=Path,
        required=True,
        help="Directory containing a pinned ASIM catalogue snapshot",
    )
    evaluation_queue.add_argument("--group-id", required=True)
    evaluation_queue.add_argument(
        "--group-strategy",
        required=True,
        choices=("source", "source-family"),
        help="Build-level leakage-control grouping assigned before annotation",
    )
    evaluation_queue.add_argument(
        "--system",
        help="Optional neutral annotation identifier replacing the build system name",
    )
    evaluation_queue.add_argument("--vendor")
    evaluation_queue.add_argument("--product")
    evaluation_queue.add_argument("--source-table")
    evaluation_queue.add_argument("--message-field")
    evaluation_queue.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/semantic-annotation"),
    )
    evaluation_promote = evaluation_subparsers.add_parser(
        "promote",
        help="Promote completed semantic annotations into provider-neutral gold cases",
    )
    evaluation_promote.add_argument(
        "queue_dir",
        type=Path,
        help="Hash-verified queue directory containing queue-manifest.json",
    )
    evaluation_promote.add_argument("decisions", type=Path, help="Typed decision JSONL")
    evaluation_promote.add_argument(
        "--catalog",
        type=Path,
        required=True,
        help="The same pinned ASIM catalogue used to create the queue",
    )
    evaluation_promote.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/semantic-gold"),
    )
    evaluation_promote.add_argument(
        "--allow-single-review",
        action="store_true",
        help="Permit provisional human-review cases without adjudication",
    )
    evaluation_compare = evaluation_subparsers.add_parser(
        "compare",
        help="Compare separated semantic mapping approaches against the same cases",
    )
    evaluation_compare.add_argument("cases", type=Path, help="Semantic mapping case JSONL")
    evaluation_compare.add_argument(
        "--catalog",
        type=Path,
        required=True,
        help="Directory containing a pinned ASIM catalogue snapshot",
    )
    evaluation_compare.add_argument(
        "--approach",
        action="append",
        choices=APPROACH_NAMES,
        dest="approaches",
        help="Approach to compare; repeat to select several (default: all)",
    )
    evaluation_compare.add_argument(
        "--output",
        type=Path,
        help="Optional path for the complete JSON comparison report",
    )
    evaluation_compare.add_argument(
        "--split",
        type=Path,
        help="Grouped split manifest; retrieval references come only from its reference partitions",
    )
    evaluation_compare.add_argument(
        "--partition",
        choices=("validation", "test"),
        default="test",
        help="Held-out partition to evaluate when --split is supplied (default: test)",
    )
    evaluation_compare.add_argument(
        "--oracle",
        choices=ORACLE_CONDITIONS,
        default="none",
        help="Diagnostic condition supplying gold schema or source roles to isolate error",
    )
    evaluation_compare.add_argument(
        "--context-view",
        choices=CONTEXT_VIEWS,
        default="full",
        dest="context_view",
        help="Physically withhold evidence to measure what each context layer is worth",
    )
    evaluation_compare.add_argument(
        "--resamples",
        type=int,
        default=DEFAULT_RESAMPLES,
        help=(
            "Bootstrap and permutation resamples over source families "
            f"(default: {DEFAULT_RESAMPLES})"
        ),
    )
    evaluation_compare.add_argument(
        "--baseline-approach",
        choices=APPROACH_NAMES,
        help="Approach every other approach is tested against (default: the first prior)",
    )
    evaluation_compare.add_argument(
        "--case-groups",
        type=Path,
        help="Pre-label case-groups.jsonl from semantic promotion",
    )
    evaluation_compare.add_argument(
        "--promotion-manifest",
        type=Path,
        help="Promotion manifest that authenticates the cases and case groups",
    )
    evaluation_robustness = evaluation_subparsers.add_parser(
        "robustness",
        help="Score approaches against controlled perturbations of the seed cases",
    )
    evaluation_robustness.add_argument("cases", type=Path, help="Seed semantic mapping cases")
    evaluation_robustness.add_argument(
        "--catalog", type=Path, required=True, help="Pinned ASIM catalogue directory"
    )
    evaluation_robustness.add_argument(
        "--approach",
        action="append",
        choices=ROBUSTNESS_APPROACH_NAMES,
        dest="approaches",
        help="Approach to test; repeat to select several (default: all event-reading approaches)",
    )
    evaluation_robustness.add_argument(
        "--output",
        type=Path,
        help="Optional path for the complete JSON robustness report",
    )
    evaluation_benchmark = evaluation_subparsers.add_parser(
        "benchmark",
        help="Run the registered parsing, security-format, and ASIM evaluation corpora",
    )
    evaluation_benchmark.add_argument(
        "registry", type=Path, help="Directory containing corpus manifest folders"
    )
    evaluation_benchmark.add_argument(
        "--catalog",
        type=Path,
        help="Pinned ASIM catalogue (required when semantic corpora are present)",
    )
    evaluation_benchmark.add_argument("--output", type=Path, default=Path("artifacts/evaluation"))
    evaluation_benchmark.add_argument("--cache", type=Path, help="Optional shared download cache")
    evaluation_benchmark.add_argument(
        "--revision", default="unknown", help="Source revision recorded in the report"
    )
    evaluation_benchmark.add_argument(
        "--baseline", type=Path, help="Previous benchmark-report.json for comparable deltas"
    )


def run_evaluation_command(args: argparse.Namespace) -> None:
    """Dispatch one parsed evaluation subcommand."""
    if args.evaluation_command == "queue":
        queue_manifest = prepare_semantic_annotation_queue(
            args.build_dir,
            args.reviews,
            args.output,
            load_catalog(args.catalog),
            group_id=args.group_id,
            group_strategy=args.group_strategy,
            system=args.system,
            vendor=args.vendor,
            product=args.product,
            source_table=args.source_table,
            message_field=args.message_field,
        )
        excluded = queue_manifest.review_count - queue_manifest.task_count
        print(
            f"Prepared {queue_manifest.task_count} blinded semantic annotation task(s) "
            f"in {args.output}; excluded reviews={excluded}, "
            f"unreviewed clusters={queue_manifest.unreviewed_cluster_count}"
        )
    elif args.evaluation_command == "promote":
        promotion_manifest = promote_semantic_annotations(
            args.queue_dir,
            args.decisions,
            args.output,
            load_catalog(args.catalog),
            allow_single_review=args.allow_single_review,
        )
        skipped = ", ".join(
            f"{reason}={count}" for reason, count in promotion_manifest.skipped_tasks.items()
        )
        message = (
            f"Promoted {promotion_manifest.promoted_count} semantic mapping case(s) "
            f"into {args.output}"
        )
        if skipped:
            message += f"; not promoted: {skipped}"
        print(message)
    elif args.evaluation_command == "validate":
        cases = load_semantic_mapping_cases(args.cases)
        message = f"Validated {len(cases)} provider-neutral semantic mapping case(s)"
        if args.split is None and (
            args.case_groups is not None or args.promotion_manifest is not None
        ):
            raise EvaluationError("--case-groups and --promotion-manifest require --split")
        if args.split is not None:
            split = load_semantic_dataset_split(args.split)
            _validate_promoted_split(
                cases,
                split,
                args.case_groups,
                args.promotion_manifest,
                args.cases,
            )
            counts = validate_semantic_dataset_split(cases, split)
            partitions = ", ".join(f"{partition}={count}" for partition, count in counts.items())
            message += f" with grouped split {split.split_id} ({partitions})"
        print(message)
    elif args.evaluation_command == "compare":
        cases = load_semantic_mapping_cases(args.cases)
        catalog = load_catalog(args.catalog)
        if args.split is None and (
            args.case_groups is not None or args.promotion_manifest is not None
        ):
            raise EvaluationError("--case-groups and --promotion-manifest require --split")
        if args.split is None:
            report = compare_approaches(
                cases,
                catalog,
                args.approaches,
                oracle=args.oracle,
                context_view=args.context_view,
                resamples=args.resamples,
                baseline_approach=args.baseline_approach,
            )
        else:
            split = load_semantic_dataset_split(args.split)
            _validate_promoted_split(
                cases,
                split,
                args.case_groups,
                args.promotion_manifest,
                args.cases,
            )
            report = compare_split_approaches(
                cases,
                catalog,
                split,
                args.partition,
                args.approaches,
                oracle=args.oracle,
                context_view=args.context_view,
                resamples=args.resamples,
                baseline_approach=args.baseline_approach,
            )
        if args.output is not None:
            write_comparison_report(args.output, report)
        if report.split_id is not None:
            print(
                f"split={report.split_id} partition={report.evaluation_partition} "
                f"references={report.reference_case_count} cases={report.case_count}"
            )
        if report.oracle != "none":
            print(f"oracle={report.oracle} (diagnostic condition, not approach accuracy)")
        if report.context_view != "full":
            print(f"context-view={report.context_view} ({VIEW_DESCRIPTIONS[report.context_view]})")
        if report.sample is not None:
            print(
                f"cases={report.sample.case_count} "
                f"{report.sample.grouping}-groups={report.sample.group_count} "
                f"resolvable-difference>={report.sample.minimum_detectable_effect:.3f}"
            )
        print(
            "approach              schema@1  schema@3  field-f1  field-mrr  "
            "cand@1  cand@5  cut  tie  role-f1  exact  coverage  sel-auc  edits"
        )
        for evaluation in report.approaches:
            metrics = evaluation.metrics
            selective = (
                evaluation.risk_coverage.area_under_curve
                if evaluation.risk_coverage is not None
                else 0.0
            )
            print(
                f"{evaluation.approach.name:<21} "
                f"{metrics.schema_top1_accuracy:>8.3f}  "
                f"{metrics.schema_top3_hit_rate:>8.3f}  "
                f"{metrics.field_micro_f1:>8.3f}  "
                f"{metrics.field_mrr:>9.3f}  "
                f"{metrics.candidate_recall_at_1:>6.3f}  "
                f"{metrics.candidate_recall_at_5:>6.3f}  "
                f"{metrics.candidate_reduction_ratio:>3.2f}  "
                f"{metrics.field_top1_tie_rate:>3.2f}  "
                f"{metrics.source_micro_f1:>7.3f}  "
                f"{metrics.mapping_exact_match:>5.3f}  "
                f"{metrics.coverage:>8.3f}  "
                f"{selective:>7.3f}  "
                f"{metrics.mean_mapping_edits:>5.2f}"
            )
            for warning in evaluation.warnings:
                print(f"  warning: {warning}")
        _print_intervals(report)
        _print_paired_tests(report)
        for warning in report.warnings:
            print(f"warning: {warning}")
    elif args.evaluation_command == "robustness":
        cases = load_semantic_mapping_cases(args.cases)
        catalog = load_catalog(args.catalog)
        robustness = run_robustness(cases, catalog, args.approaches)
        if args.output is not None:
            write_robustness_report(args.output, robustness)
        print(
            "approach              perturbation       family             "
            "base-f1  pert-f1   delta  stable  verdict"
        )
        for row in robustness.rows:
            print(
                f"{row.approach:<21} {row.perturbation:<18} {row.family:<18} "
                f"{row.baseline_field_micro_f1:>7.3f}  {row.perturbed_field_micro_f1:>7.3f}  "
                f"{row.field_micro_f1_delta:>+6.3f}  {row.prediction_stability:>6.3f}  "
                f"{'pass' if row.passed else 'FAIL'}"
            )
            for note in row.notes:
                print(f"  note: {note}")
        for warning in robustness.warnings:
            print(f"warning: {warning}")
        print(
            "\nControlled robustness slices only. These are generated variants and "
            "must not be averaged into real-case results."
        )
    else:
        report = run_benchmarks(
            args.registry,
            args.output,
            catalog_dir=args.catalog,
            cache_dir=args.cache,
            revision=args.revision,
            baseline_path=args.baseline,
        )
        print(
            f"Evaluated {len(report.corpora)} corpora and wrote "
            f"{len(report.results)} result row(s) to {args.output}"
        )


def _print_intervals(report: ComparisonReport) -> None:
    intervals = [
        (evaluation.approach.name, interval)
        for evaluation in report.approaches
        for interval in evaluation.intervals
    ]
    if not intervals:
        return
    level = intervals[0][1].confidence_level
    resamples = intervals[0][1].resamples
    print(f"\nsource-family bootstrap, {resamples} resamples, {level:.0%} percentile interval")
    print("approach              metric                     point           interval")
    for name, interval in intervals:
        print(
            f"{name:<21} {interval.metric:<24} {interval.point:>7.3f}   "
            f"[{interval.lower:>6.3f}, {interval.upper:>6.3f}]"
        )


def _print_paired_tests(report: ComparisonReport) -> None:
    if not report.paired_tests:
        return
    baseline = report.paired_tests[0].baseline
    metric = report.paired_tests[0].metric
    print(f"\npaired permutation test against {baseline} on {metric}")
    print("approach                  value      diff        p  resolvable  verdict")
    for test in report.paired_tests:
        verdict = "distinguishable" if test.significant else "not distinguishable"
        print(
            f"{test.candidate:<21} {test.candidate_value:>8.3f}  "
            f"{test.difference:>+8.3f}  {test.p_value:>7.3f}  "
            f"{test.minimum_detectable_effect:>10.3f}  {verdict}"
        )


def _validate_promoted_split(
    cases: list[SemanticMappingCase],
    split: SemanticDatasetSplit,
    case_groups_path: Path | None,
    promotion_manifest_path: Path | None,
    cases_path: Path,
) -> None:
    if case_groups_path is None or promotion_manifest_path is None:
        raise EvaluationError(
            "Grouped evaluation requires --case-groups and --promotion-manifest "
            "to verify pre-label group assignments"
        )
    groups = validate_semantic_promotion_artifacts(
        cases_path,
        case_groups_path,
        promotion_manifest_path,
    )
    validate_semantic_case_groups(cases, split, groups)
