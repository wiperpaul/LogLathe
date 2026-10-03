"""Export a local classifier replay page from existing experiment artifacts."""

from __future__ import annotations

import argparse
from pathlib import Path

from ..schema_ranking.dashboard import export_dashboard


def _run(value: str) -> tuple[str, Path]:
    label, separator, path = value.partition("=")
    if not separator or not label.strip() or not path.strip():
        raise argparse.ArgumentTypeError("Use --run 'Label=path/to/recorded-run'")
    return label.strip(), Path(path)


def register_schema_view_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser("schema-view", help="Export an offline classifier replay page")
    parser.add_argument("input", type=Path, help="Frozen evidence used by the recorded runs")
    parser.add_argument("--input-kind", choices=("cases", "queue", "experiment"), default="cases")
    parser.add_argument("--run", action="append", type=_run, default=[], metavar="LABEL=DIRECTORY")
    parser.add_argument(
        "--comparison",
        action="append",
        type=Path,
        default=[],
        help="Import every approach from an existing evaluation compare report",
    )
    parser.add_argument("--output", required=True, type=Path, help="New standalone HTML file")


def run_schema_view_command(args: argparse.Namespace) -> None:
    data = export_dashboard(
        args.input,
        kind=args.input_kind,
        runs=args.run,
        comparisons=args.comparison,
        output=args.output,
    )
    print(f"Exported {len(data['cases'])} templates and {len(data['classifiers'])} classifiers")
    print(f"Open {args.output.resolve()} in your browser. Recorded replay; no API requests.")
