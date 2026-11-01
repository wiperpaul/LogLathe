"""Write and summarise a static ASIM sample and parser inventory."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .inventory import SAMPLE_ROOT, AsimInventory

SHAPES = ("raw-text", "raw-companion", "structured", "normalized-input")


def write_inventory(inventory: AsimInventory, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    data_path = output_dir / "inventory.json"
    report_path = output_dir / "inventory.md"
    data_path.write_text(
        json.dumps(inventory.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(render_inventory(inventory), encoding="utf-8")
    return data_path, report_path


def render_inventory(inventory: AsimInventory) -> str:
    samples = {sample.file.path: sample for sample in inventory.samples}
    paired_parsers = {pairing.parser for pairing in inventory.pairings}
    paired_samples = {pairing.sample for pairing in inventory.pairings}
    lines = [
        "# ASIM sample and parser inventory",
        "",
        f"Upstream: {inventory.repository} at `{inventory.revision}` "
        f"(requested `{inventory.requested_revision}`).",
        "",
        "Static analysis only. No parser was executed, and a pairing means the sample's "
        "source table and identity evidence match what the parser reads; it is not a claim "
        "that the parser output is correct. Samples, file names, and table names are "
        "silver research evidence and must not reach blind schema-selection inputs.",
        "",
        "## Summary",
        "",
        "| Schema | Source parsers | Parsers with a sample | Samples | Paired samples "
        "| Ready | Ready, raw text | Needs setup | Excluded |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for schema in inventory.schemas:
        parsers = [parser for parser in inventory.parsers if parser.schema_name == schema]
        schema_samples = [sample for sample in inventory.samples if sample.schema_name == schema]
        pairings = [pairing for pairing in inventory.pairings if pairing.schema_name == schema]
        statuses = Counter(pairing.status for pairing in pairings)
        raw_text = sum(
            pairing.status == "ready" and samples[pairing.sample].input_shape == "raw-text"
            for pairing in pairings
        )
        lines.append(
            f"| {schema} | {len(parsers)} "
            f"| {sum(parser.name in paired_parsers for parser in parsers)} "
            f"| {len(schema_samples)} "
            f"| {sum(sample.file.path in paired_samples for sample in schema_samples)} "
            f"| {statuses['ready']} | {raw_text} "
            f"| {statuses['needs-setup']} | {statuses['excluded']} |"
        )
    lines += [
        "",
        "Status counts are pairings; a sample paired with two parsers counts twice. "
        "Raw-text pairings carry the original message in one column, which is what "
        "reference preparation turns into schema-selection inputs today.",
        "",
        "## Input shapes",
        "",
        "| Schema | " + " | ".join(SHAPES) + " |",
        "| --- |" + " ---: |" * len(SHAPES),
    ]
    for schema in inventory.schemas:
        shapes = Counter(
            sample.input_shape for sample in inventory.samples if sample.schema_name == schema
        )
        lines.append(f"| {schema} | " + " | ".join(str(shapes[shape]) for shape in SHAPES) + " |")
    blockers = Counter(blocker for pairing in inventory.pairings for blocker in pairing.blockers)
    lines += ["", "## Readiness blockers", ""]
    if blockers:
        lines += ["| Blocker | Pairings |", "| --- | ---: |"]
        lines += [f"| `{name}` | {count} |" for name, count in sorted(blockers.items())]
    else:
        lines.append("None.")
    for schema in inventory.schemas:
        pairings = [pairing for pairing in inventory.pairings if pairing.schema_name == schema]
        lines += ["", f"## {schema} pairings", ""]
        if not pairings:
            lines.append("None.")
            continue
        lines += [
            "| Parser | Sample | Basis | Input | Parsed text | Status | Blockers |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for pairing in pairings:
            sample = samples[pairing.sample]
            blocker_text = ", ".join(
                f"{blocker} ({', '.join(pairing.missing_tables)})"
                if blocker == "missing-tables"
                else blocker
                for blocker in pairing.blockers
            )
            lines.append(
                _row(
                    pairing.parser,
                    _short(pairing.sample),
                    ", ".join(pairing.basis),
                    sample.input_shape,
                    ", ".join(pairing.parsed_text_columns),
                    pairing.status
                    + (f" ({', '.join(pairing.fixtures)})" if pairing.fixtures else ""),
                    blocker_text,
                )
            )
    unpaired = [sample for sample in inventory.samples if sample.unpaired_reason]
    lines += ["", "## Samples without a parser", ""]
    if unpaired:
        lines += ["| Sample | Schema | Source tables | Reason |", "| --- | --- | --- | --- |"]
        lines += [
            _row(
                _short(sample.file.path),
                sample.schema_name,
                ", ".join(table or "(empty)" for table in sample.source_tables) or "(no Type)",
                sample.unpaired_reason or "",
            )
            for sample in unpaired
        ]
    else:
        lines.append("None.")
    lines += ["", "## Parsers without a sample", ""]
    for schema in inventory.schemas:
        names = [
            parser.name
            for parser in inventory.parsers
            if parser.schema_name == schema and parser.name not in paired_parsers
        ]
        lines.append(f"- {schema} ({len(names)}): {', '.join(names) or 'none'}")
    duplicates = [sample for sample in inventory.samples if sample.duplicate_of]
    lines += ["", "## Duplicate samples", ""]
    if duplicates:
        lines += ["| Sample | Repeats | Row similarity |", "| --- | --- | ---: |"]
        lines += [
            _row(
                _short(sample.file.path),
                _short(sample.duplicate_of or ""),
                f"{sample.duplicate_similarity:.3f}",
            )
            for sample in duplicates
        ]
    else:
        lines.append("None.")
    leaking = [sample for sample in inventory.samples if sample.leaked_fields]
    hinted = [sample for sample in inventory.samples if sample.table_name_hints]
    lines += [
        "",
        "## Label leakage",
        "",
        "Populated normalized fields in the source table, and schema words in table names. "
        "Both would reveal the answer to a blind schema or mapping decision.",
        "",
    ]
    if leaking or hinted:
        lines += [
            "| Sample | Populated normalized fields | Table-name hints |",
            "| --- | --- | --- |",
        ]
        for sample in sorted(
            {item.file.path: item for item in [*leaking, *hinted]}.values(),
            key=lambda item: item.file.path,
        ):
            lines.append(
                _row(
                    _short(sample.file.path),
                    ", ".join(f"{name} {fill:.0%}" for name, fill in sample.leaked_fields.items()),
                    ", ".join(sample.table_name_hints),
                )
            )
    else:
        lines.append("None.")
    uses = [
        *(
            use
            for item in [*inventory.parsers, *inventory.samples, *inventory.table_schemas]
            for use in item.existing_uses
        ),
        *inventory.unattached_uses,
    ]
    lines += [
        "",
        "## Existing fixtures and corpora",
        "",
        "Pinned files in the listed directories, compared with this revision: `changed` "
        "means different bytes, `missing` means the path is gone, and `not-inventoried` "
        "means the file exists but no parser or sample record holds it.",
        "",
    ]
    if uses:
        lines += [
            "| Use | Kind | Upstream path | Pinned revision | Upstream here |",
            "| --- | --- | --- | --- | --- |",
        ]
        lines += [
            _row(use.id, use.kind, use.path, f"`{use.revision[:7]}`", use.upstream)
            for use in sorted(uses, key=lambda use: (use.id, use.path))
        ]
    else:
        lines.append("None.")
    failing = [
        tester
        for tester in inventory.tester_results
        if tester.counts.get("error") and tester.variant == "ASim"
    ]
    lines += [
        "",
        "## Upstream tester errors",
        "",
        "Contributor-run ASimSchemaTester/ASimDataTester exports. They describe the "
        "contributor's workspace, not these sample bytes; parser matches are best effort.",
        "",
    ]
    if failing:
        lines += [
            "| Tester export | Parsers | Errors | Warnings | First error |",
            "| --- | --- | ---: | ---: | --- |",
        ]
        lines += [
            _row(
                tester.file.path.rsplit("/", 1)[-1],
                ", ".join(tester.parsers) or "(unmatched)",
                str(tester.counts.get("error", 0)),
                str(tester.counts.get("warning", 0)),
                tester.errors[0][:160] if tester.errors else "",
            )
            for tester in failing
        ]
    else:
        lines.append("None.")
    reasons = Counter(item.reason.split(":", 1)[0] for item in inventory.skipped)
    lines += ["", "## Skipped parser files", ""]
    lines += [f"- {reason}: {count}" for reason, count in sorted(reasons.items())] or ["None."]
    lines += ["", "## Warnings", ""]
    lines += [f"- {warning}" for warning in inventory.warnings] or ["None."]
    return "\n".join(lines) + "\n"


def _short(path: str) -> str:
    return path.removeprefix(f"{SAMPLE_ROOT}/")


def _row(*cells: str) -> str:
    return "| " + " | ".join(cell.replace("|", "\\|").replace("\n", " ") for cell in cells) + " |"
