"""Compile approved cluster reviews into typed specifications and deterministic KQL."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from .models import (
    ClusterRecord,
    CompileManifest,
    FieldMapping,
    ParserSource,
    ParserSpecification,
    ReviewDecision,
)
from .reviews import ReviewError, load_review_decisions

_PLACEHOLDER = re.compile(r"<VAR:[A-Za-z0-9_]+>")
_REGEX_META = re.compile(r"([\\.^$|?*+()\[\]{}])")


def compile_reviews(
    clusters_path: Path,
    reviews_path: Path,
    output_dir: Path,
    *,
    mapping_bundle: Path | None = None,
) -> CompileManifest:
    clusters = _load_clusters(clusters_path)
    if mapping_bundle is None:
        decisions = load_review_decisions(reviews_path)
    else:
        from .mapping_review import load_mapping_decisions

        decisions = load_mapping_decisions(mapping_bundle, reviews_path)
    _reject_duplicate_reviews(decisions)
    cluster_by_id = {cluster.cluster_id: cluster for cluster in clusters}
    output_dir.mkdir(parents=True, exist_ok=True)

    compiled: list[ParserSpecification] = []
    skipped: Counter[str] = Counter()
    outputs: list[str] = []
    cluster_file_hash = hashlib.sha256(clusters_path.read_bytes()).hexdigest()
    for decision in decisions:
        if decision.mapping_review is not None and (
            cluster_file_hash != decision.mapping_review.cluster_file_sha256
        ):
            raise ReviewError("Mapping review refers to a different or changed cluster file")
        if decision.status != "approved":
            skipped[decision.status] += 1
            continue
        cluster = cluster_by_id.get(decision.cluster_id)
        if cluster is None:
            raise ReviewError(f"Approved review references unknown cluster: {decision.cluster_id}")
        if not _has_parser_review(decision):
            skipped[
                decision.mapping_review.status if decision.mapping_review else "awaiting_mapping"
            ] += 1
            continue
        specification = _to_specification(cluster, decision)
        compiled.append(specification)
        stem = specification.parser_name
        spec_path = output_dir / f"{stem}.parser-spec.json"
        kql_path = output_dir / f"{stem}.kql"
        spec_path.write_text(
            json.dumps(specification.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        kql_path.write_text(compile_kql(specification), encoding="utf-8")
        outputs.extend([spec_path.name, kql_path.name])

    manifest = CompileManifest(
        cluster_count=len(clusters),
        review_count=len(decisions),
        compiled_count=len(compiled),
        skipped_reviews=dict(sorted(skipped.items())),
        outputs=sorted(outputs),
    )
    (output_dir / "compile-manifest.json").write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def _has_parser_review(decision: ReviewDecision) -> bool:
    if decision.mapping_review is not None and decision.mapping_review.status != "approved":
        return False
    return all(
        value is not None
        for value in (
            decision.schema_name,
            decision.parser_name,
            decision.vendor,
            decision.product,
        )
    )


def compile_kql(specification: ParserSpecification) -> str:
    regex, capture_groups = _capture_regex(specification)
    source = specification.source
    lines = [
        f"// Generated from approved review of {specification.cluster_id}.",
        f"let {specification.parser_name} = (disabled: bool = false) {{",
        f"    {source.table}",
        "    | where not(disabled)",
        f'    | where {source.message_field} matches regex @"{_verbatim(regex)}"',
    ]
    if specification.mapping_review and specification.mapping_review.time_generated == "ingestion":
        lines.insert(
            1,
            "// Setup assumes TimeGenerated is supplied at ingestion "
            "and exists in the queried table.",
        )
    source_columns = dict.fromkeys(
        mapping.source_field
        for mapping in specification.field_mappings
        if mapping.source_field is not None
    )
    aliases = {name: f"_asim_forge_source_{index}" for index, name in enumerate(source_columns, 1)}
    temporary_fields = list(aliases.values())
    for name, alias in aliases.items():
        lines.append(f"    | extend {alias} = {name}")
    for mapping in specification.field_mappings:
        if mapping.slot_id is not None:
            capture = capture_groups[mapping.slot_id]
            expression = f"_asim_forge_{mapping.slot_id}"
            temporary_fields.append(expression)
            lines.append(
                f'    | extend {expression} = extract(@"{_verbatim(regex)}", '
                f"{capture}, {source.message_field})"
            )
        elif mapping.source_field is not None:
            expression = aliases[mapping.source_field]
        else:
            value = mapping.constant_value
            expression = (
                _kql_string(value) if isinstance(value, str) else json.dumps(value, allow_nan=False)
            )
        lines.append(f"    | extend {mapping.asim_field} = {_transform(mapping, expression)}")
    lines.extend(
        [
            "    | extend",
            f"        EventSchema = {_kql_string(specification.schema_name)},",
            f"        EventVendor = {_kql_string(source.vendor)},",
            f"        EventProduct = {_kql_string(source.product)}",
        ]
    )
    if specification.schema_version is not None:
        lines.append(
            f"    | extend EventSchemaVersion = {_kql_string(specification.schema_version)}"
        )
    if temporary_fields:
        lines.append(f"    | project-away {', '.join(temporary_fields)}")
    lines.extend(["};", f"{specification.parser_name}", ""])
    return "\n".join(lines)


def _load_clusters(path: Path) -> list[ClusterRecord]:
    if not path.is_file():
        raise ReviewError(f"Cluster file does not exist: {path}")
    clusters = [
        ClusterRecord.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not clusters:
        raise ReviewError(f"No cluster records found in {path}")
    return clusters


def _reject_duplicate_reviews(decisions: list[ReviewDecision]) -> None:
    counts = Counter(decision.cluster_id for decision in decisions)
    duplicates = sorted(cluster_id for cluster_id, count in counts.items() if count > 1)
    if duplicates:
        raise ReviewError(
            "Milestone 1 requires one authoritative decision per cluster; duplicates: "
            + ", ".join(duplicates)
        )


def _to_specification(
    cluster: ClusterRecord,
    decision: ReviewDecision,
) -> ParserSpecification:
    schema_name = decision.schema_name
    parser_name = decision.parser_name
    vendor = decision.vendor
    product = decision.product
    required = {
        "schema_name": schema_name,
        "parser_name": parser_name,
        "vendor": vendor,
        "product": product,
    }
    missing = sorted(name for name, value in required.items() if value is None)
    if missing:
        raise ReviewError(f"Approved review {cluster.cluster_id} is missing: {', '.join(missing)}")
    assert schema_name is not None
    assert parser_name is not None
    assert vendor is not None
    assert product is not None

    available_slots = {slot.slot_id for slot in cluster.parameter_slots}
    mapped_slots = [
        mapping.slot_id for mapping in decision.field_mappings if mapping.slot_id is not None
    ]
    unknown_slots = sorted(set(mapped_slots) - available_slots)
    if unknown_slots:
        raise ReviewError(
            f"Review {cluster.cluster_id} maps unknown slots: {', '.join(unknown_slots)}"
        )
    duplicate_slots = _duplicates(mapped_slots)
    if duplicate_slots:
        raise ReviewError(
            f"Review {cluster.cluster_id} maps slots more than once: {', '.join(duplicate_slots)}"
        )
    duplicate_fields = _duplicates(
        [mapping.asim_field.casefold() for mapping in decision.field_mappings]
    )
    reserved = {"eventschema", "eventvendor", "eventproduct"}
    if decision.schema_version is not None:
        reserved.add("eventschemaversion")
    if any(mapping.asim_field.casefold() in reserved for mapping in decision.field_mappings):
        raise ReviewError("Set schema, vendor, and product through the parser metadata")
    if duplicate_fields:
        raise ReviewError(
            f"Review {cluster.cluster_id} maps ASIM fields more than once: "
            + ", ".join(duplicate_fields)
        )

    return ParserSpecification(
        parser_name=parser_name,
        cluster_id=cluster.cluster_id,
        schema_name=schema_name,
        schema_version=decision.schema_version,
        template=cluster.template,
        source=ParserSource(
            vendor=vendor,
            product=product,
            table=decision.source_table,
            message_field=decision.message_field,
        ),
        field_mappings=decision.field_mappings,
        reviewer=decision.reviewer,
        review_notes=decision.notes,
        mapping_review=decision.mapping_review,
    )


def _capture_regex(specification: ParserSpecification) -> tuple[str, dict[str, int]]:
    mapped = {mapping.slot_id for mapping in specification.field_mappings}
    matches = list(_PLACEHOLDER.finditer(specification.template))
    parts = ["^"]
    captures: dict[str, int] = {}
    cursor = 0
    capture_index = 0
    for slot_index, match in enumerate(matches, start=1):
        parts.append(_regex_literal(specification.template[cursor : match.start()]))
        slot_id = f"p{slot_index}"
        if slot_id in mapped:
            capture_index += 1
            captures[slot_id] = capture_index
            parts.append("(.+?)")
        else:
            parts.append(".*?")
        cursor = match.end()
    parts.extend([_regex_literal(specification.template[cursor:]), "$"])
    return "".join(parts), captures


def _transform(mapping: FieldMapping, temporary: str) -> str:
    function = {
        "string": "tostring",
        "int": "toint",
        "long": "tolong",
        "real": "toreal",
        "datetime": "todatetime",
        "bool": "tobool",
    }[mapping.transform]
    return f"{function}({temporary})"


def _duplicates(values: list[str]) -> list[str]:
    counts = Counter(values)
    return sorted(value for value, count in counts.items() if count > 1)


def _regex_literal(value: str) -> str:
    return _REGEX_META.sub(r"\\\1", value)


def _verbatim(value: str) -> str:
    return value.replace('"', '""')


def _kql_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)
