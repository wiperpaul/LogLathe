"""Execution-free analysis of upstream ASIM parsers, sample tables, and tester output.

Every derived fact (source tables, vendor literals, schema hints) comes from an
auditable regular expression over pinned upstream text. The rules are heuristics
for research triage, not a KQL parser; callers record which rule produced a claim.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal

import yaml

FileKind = Literal["ingested-csv", "raw", "table-schema", "tester-result", "other"]

# Columns that hold an unparsed vendor message, and semi-structured residue columns.
RAW_TEXT_COLUMNS = ("SyslogMessage", "Message", "RawData")
TEXT_COLUMNS = (*RAW_TEXT_COLUMNS, "EventData", "AdditionalExtensions")
# Normalized ASIM fields that a connector or ingest-time rule may already populate.
LEAKAGE_COLUMNS = (
    "EventSchema",
    "EventSchemaVersion",
    "EventType",
    "EventSubType",
    "EventResult",
    "EventResultDetails",
    "EventVendor",
    "EventProduct",
    "EventProductVersion",
    "EventSeverity",
    "DvcAction",
)
# Ingestion bookkeeping that differs between otherwise identical exports.
VOLATILE_COLUMNS = frozenset(
    {
        "TenantId",
        "_ResourceId",
        "_ItemId",
        "_SubscriptionId",
        "TimeGenerated",
        "TimeCollected",
        "ReceiptTime",
        "_TimeReceived",
        "MG",
        "ManagementGroupName",
        "SourceSystem",
    }
)
TABLE_NAME_HINTS = ("auth", "login", "logon", "signin", "audit", "network", "flow", "session")

_LEXICAL = re.compile(
    r"(?P<comment>//[^\n]*)"
    r"|(?P<string>```[\s\S]*?```|@\"(?:[^\"]|\"\")*\"|@'(?:[^']|'')*'"
    r"|\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*')"
)
_LET = re.compile(r"\blet\s+([A-Za-z_]\w*)\s*=")
_TABULAR_PARAMETER = re.compile(r"[(,]\s*([A-Za-z_]\w*)\s*:\s*\(")
_LET_TABLE = re.compile(r"\blet\s+\w+\s*=\s*(?:\(\s*)?([A-Za-z_]\w*)\s*(?=\|)")
_BLOCK_TABLE = re.compile(r"[({]\s*([A-Za-z_]\w*)\s*(?=\|)")
_STATEMENT_TABLE = re.compile(r"^\s*\(?\s*([A-Za-z_]\w*)\s*(?=\|)")
_UNION = re.compile(r"\bunion\b((?:\s+\w+\s*=\s*\w+)*)\s+([^|;]+)")
_IDENTIFIER = re.compile(r"[A-Za-z_]\w*")
_NOT_TABLES = frozenset(
    {"disabled", "pack", "isfuzzy", "true", "false", "kind", "withsource", "inner", "outer"}
)
_HELPER = re.compile(r"\b((?i:_asim_)\w+|_GetWatchlist\w*)\b")
_VENDOR = re.compile(r"\bEventVendor\s*=\s*@?(?:\"([^\"]*)\"|'([^']*)')")
_PRODUCT = re.compile(r"\bEventProduct\s*=\s*@?(?:\"([^\"]*)\"|'([^']*)')")
_SEVERITY = re.compile(r"^\((\d)\)\s*(?:Error|Warning|Info)\b\s*:?\s*(.*)$", re.S)
_SCHEMA_SUFFIX = re.compile(r"\s*\(Schema:\w+\)\s*$")
_SHORT_SCHEMA_ALIASES = {"auth": "Authentication", "network": "NetworkSession"}


@dataclass(frozen=True)
class LexedQuery:
    code: str
    uncommented: str
    literals: frozenset[str]


@dataclass(frozen=True)
class ParserFacts:
    name: str
    schema_name: str | None
    schema_version: str | None
    product: str | None
    builtin: str | None
    parameters: tuple[str, ...]
    vendors: tuple[str, ...]
    products: tuple[str, ...]
    tables: tuple[str, ...]
    helpers: tuple[str, ...]
    literals: frozenset[str]
    code: str


@dataclass(frozen=True)
class CsvProfile:
    columns: tuple[str, ...]
    rows: int
    source_tables: dict[str, int]
    fill: dict[str, float]
    device_identities: tuple[tuple[str, str], ...]
    fingerprint_columns: tuple[str, ...]
    fingerprints: Counter[str]


@dataclass(frozen=True)
class TesterSummary:
    counts: dict[str, int]
    errors: tuple[str, ...]


def lex_query(query: str) -> LexedQuery:
    """Remove comments and blank string literals so code patterns ignore quoted text."""
    code: list[str] = []
    uncommented: list[str] = []
    literals: set[str] = set()
    position = 0
    for match in _LEXICAL.finditer(query):
        code.append(query[position : match.start()])
        uncommented.append(query[position : match.start()])
        token = match.group("string")
        if token is not None:
            code.append('""')
            uncommented.append(token)
            literals.add(_unquote(token))
        position = match.end()
    code.append(query[position:])
    uncommented.append(query[position:])
    return LexedQuery("".join(code), "".join(uncommented), frozenset(literals))


def query_tables(code: str) -> tuple[str, ...]:
    """Source tables read by a lexed query: pipeline heads and union operands."""
    bound = set(_LET.findall(code)) | set(_TABULAR_PARAMETER.findall(code))
    found = set(_LET_TABLE.findall(code)) | set(_BLOCK_TABLE.findall(code))
    for statement in code.split(";"):
        match = _STATEMENT_TABLE.match(statement)
        if match:
            found.add(match.group(1))
    for match in _UNION.finditer(code):
        found.update(_IDENTIFIER.findall(match.group(2)))
    return tuple(sorted(found - bound - _NOT_TABLES))


def parser_facts(content: bytes) -> ParserFacts:
    data: Any = yaml.safe_load(content.decode("utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("Parser YAML is not a mapping")
    name = data.get("ParserName")
    query = data.get("ParserQuery")
    if not isinstance(name, str) or not isinstance(query, str):
        raise ValueError("Parser YAML lacks ParserName or ParserQuery")
    normalization = data.get("Normalization")
    normalization = normalization if isinstance(normalization, dict) else {}
    product = data.get("Product")
    product = product if isinstance(product, dict) else {}
    parameters = data.get("ParserParams")
    lexed = lex_query(query)
    return ParserFacts(
        name=name,
        schema_name=_text(normalization.get("Schema")),
        schema_version=_text(normalization.get("Version")),
        product=_text(product.get("Name")),
        builtin=_text(data.get("EquivalentBuiltInParser")),
        parameters=tuple(
            str(item["Name"])
            for item in (parameters if isinstance(parameters, list) else [])
            if isinstance(item, dict) and "Name" in item
        ),
        vendors=_literal_values(_VENDOR, lexed.uncommented),
        products=_literal_values(_PRODUCT, lexed.uncommented),
        tables=query_tables(lexed.code),
        helpers=tuple(sorted(set(_HELPER.findall(lexed.code)))),
        literals=frozenset(value.casefold() for value in lexed.literals),
        code=lexed.code,
    )


def references_column(code: str, column: str) -> bool:
    return re.search(rf"\b{re.escape(column)}\b", code) is not None


def column_name(header: str) -> str:
    """Strip export decorations such as `TimeGenerated [UTC]`."""
    return header.split(" [", 1)[0].strip()


def profile_csv(content: bytes) -> CsvProfile:
    with _large_csv_fields():
        reader = csv.reader(io.StringIO(content.decode("utf-8-sig"), newline=""))
        header = next(reader, None) or []
        rows = [row for row in reader if any(cell.strip() for cell in row)]
    columns = tuple(column_name(item) for item in header)
    width = len(columns)
    values = [[row[index] if index < len(row) else "" for index in range(width)] for row in rows]
    position = {name: index for index, name in enumerate(columns)}
    fill = {
        name: round(sum(1 for row in values if row[index].strip()) / len(values), 4)
        if values
        else 0.0
        for name, index in position.items()
    }
    source_tables: Counter[str] = Counter()
    if "Type" in position:
        source_tables.update(row[position["Type"]].strip() for row in values)
    identities: set[tuple[str, str]] = set()
    if "DeviceVendor" in position and "DeviceProduct" in position:
        for row in values:
            vendor = row[position["DeviceVendor"]].strip()
            if vendor:
                identities.add((vendor, row[position["DeviceProduct"]].strip()))
    kept = sorted((name, index) for name, index in position.items() if name not in VOLATILE_COLUMNS)
    fingerprints = Counter(
        hashlib.sha256(json.dumps([row[index] for _, index in kept]).encode()).hexdigest()
        for row in values
    )
    return CsvProfile(
        columns=columns,
        rows=len(values),
        source_tables=dict(sorted(source_tables.items())),
        fill=fill,
        device_identities=tuple(sorted(identities)),
        fingerprint_columns=tuple(name for name, _ in kept),
        fingerprints=fingerprints,
    )


def content_similarity(left: Counter[str], right: Counter[str]) -> float:
    """Multiset Jaccard similarity of row fingerprints."""
    union = sum((left | right).values())
    return sum((left & right).values()) / union if union else 0.0


def is_table_schema_header(columns: Sequence[str]) -> bool:
    """Whether a CSV header describes table columns rather than holding log rows."""
    names = set(columns)
    return (
        "ColumnName" in names and bool({"ColumnType", "DataType"} & names) and "Type" not in names
    )


def table_schema_columns(content: bytes) -> dict[str, str]:
    """Columns and types from an upstream table description.

    Upstream uses `ColumnName,ColumnOrdinal,DataType,ColumnType` exports and shorter
    `ColumnName,ColumnType` files; `ColumnType` holds the Kusto type when present.
    """
    with _large_csv_fields():
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig"), newline=""))
        fields = set(reader.fieldnames or [])
        type_column = next((name for name in ("ColumnType", "DataType") if name in fields), None)
        if "ColumnName" not in fields or type_column is None:
            raise ValueError("Table schema file lacks ColumnName and ColumnType or DataType")
        return {
            row["ColumnName"].strip(): (row[type_column] or "").strip()
            for row in reader
            if (row["ColumnName"] or "").strip()
        }


def raw_event_count(path: str, content: bytes) -> int:
    text = content.decode("utf-8-sig", errors="replace")
    if path.lower().endswith(".json"):
        try:
            payload: Any = json.loads(text)
        except json.JSONDecodeError:
            pass
        else:
            return len(payload) if isinstance(payload, list) else 1
    return sum(1 for line in text.splitlines() if line.strip())


def tester_summary(content: bytes) -> TesterSummary:
    """Severity counts from ASimSchemaTester/ASimDataTester output exported as CSV."""
    counts: Counter[str] = Counter()
    errors: list[str] = []
    with _large_csv_fields():
        reader = csv.reader(io.StringIO(content.decode("utf-8-sig"), newline=""))
        for row in reader:
            cell = row[0].strip() if row else ""
            if not cell or cell == "Result":
                continue
            match = _SEVERITY.match(cell)
            level = (
                {"0": "error", "1": "warning", "2": "info"}.get(match.group(1), "unparsed")
                if match
                else "unparsed"
            )
            counts[level] += 1
            if level == "error" and match:
                errors.append(_SCHEMA_SUFFIX.sub("", match.group(2)).strip())
    return TesterSummary(dict(sorted(counts.items())), tuple(errors))


def sample_file_kind(path: str) -> FileKind:
    name = path.rsplit("/", 1)[-1].lower()
    if re.search(r"_schema_?test(?:er)?\b", name) or "datatest" in name:
        return "tester-result"
    if re.search(r"_schema\.csv$", name):
        return "table-schema"
    if re.search(r"_rawlogs\.(?:txt|json)$", name):
        return "raw"
    if name.endswith(".csv"):
        return "ingested-csv"
    return "other"


def schema_hint(path: str, schemas: Sequence[str]) -> str | None:
    """The requested schema named by an upstream file name, if any."""
    name = path.rsplit("/", 1)[-1]
    for schema in sorted(schemas, key=len, reverse=True):
        if re.search(rf"(?:^|[_ ]|asim|vim){re.escape(schema)}", name, re.I):
            return schema
    tokens = re.split(r"[_.\s]+", name.lower())
    if "asim" in tokens:
        for alias, schema in _SHORT_SCHEMA_ALIASES.items():
            if alias in tokens and schema in schemas:
                return schema
    return None


def raw_companion_key(path: str) -> str | None:
    """The ingested-CSV path that a `_RawLogs` file accompanies."""
    match = re.match(r"^(.*)_RawLogs\.(?:txt|json)$", path, re.I)
    return f"{match.group(1)}_IngestedLogs.csv".casefold() if match else None


def table_name_hints(tables: Sequence[str]) -> list[str]:
    lowered = [table.casefold() for table in tables]
    return [hint for hint in TABLE_NAME_HINTS if any(hint in table for table in lowered)]


def normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def tester_variant(path: str) -> Literal["ASim", "vim"]:
    name = path.rsplit("/", 1)[-1]
    return "vim" if re.search(r"(?:^|_)vim", name, re.I) else "ASim"


def tester_kind(path: str) -> Literal["DataTest", "SchemaTest"] | None:
    name = path.rsplit("/", 1)[-1].lower()
    if "datatest" in name:
        return "DataTest"
    if "schematest" in name:
        return "SchemaTest"
    return None


def tester_key(path: str, schema: str) -> str:
    """Normalized vendor/product identity from an irregular tester file name."""
    name = re.sub(r"(?:\s*\(\d+\))?(?:\.csv)+$", "", path.rsplit("/", 1)[-1], flags=re.I)
    key = normalized_key(name)
    for token in (schema.casefold(), "schematester", "datatester", "schematest", "datatest"):
        key = key.replace(token, "")
    return key.replace("asim", "").replace("vim", "")


def _literal_values(pattern: re.Pattern[str], text: str) -> tuple[str, ...]:
    return tuple(sorted({first or second for first, second in pattern.findall(text)} - {""}))


def _unquote(token: str) -> str:
    if token.startswith("```"):
        return token[3:-3]
    if token.startswith("@"):
        quote = token[1]
        return token[2:-1].replace(quote * 2, quote)
    return re.sub(r"\\(.)", r"\1", token[1:-1])


def _text(value: Any) -> str | None:
    return None if value is None else str(value).strip() or None


@contextmanager
def _large_csv_fields() -> Iterator[None]:
    previous = csv.field_size_limit(2**31 - 1)
    try:
        yield
    finally:
        csv.field_size_limit(previous)
