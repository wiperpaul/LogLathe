"""Static inventory of upstream ASIM samples and parsers for research triage.

The inventory pairs Microsoft's public sample tables with the source parsers that
read them and lists what native execution would still need. Nothing is executed and
no parser output is treated as correct. Upstream bytes stay in the local cache and
records point back to them by Git blob SHA and SHA-256.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import unquote

import yaml
from pydantic import Field

from .. import azure_sentinel
from ..azure_sentinel import BlobCache, FetchBytes, TreeEntry, UpstreamTree
from ..benchmarking import load_corpus_manifests
from ..models import StrictModel
from . import static_analysis as static
from .contracts import ReferenceFixture

DEFAULT_SCHEMAS = ("Authentication", "NetworkSession", "AuditEvent")
SAMPLE_ROOT = "Sample Data/ASIM"
DUPLICATE_THRESHOLD = 0.9
RAW_TEXT_FILL = 0.5
_SETUP_HELPERS = frozenset(
    {"_getwatchlist", "_asim_getwatchlistraw", "_asim_getsourcebysourcetype"}
)
_NORMALIZED_TABLE = re.compile(r"^ASim\w+Logs$")
_RAW_GITHUB = re.compile(
    rf"^https://raw\.githubusercontent\.com/{re.escape(azure_sentinel.REPOSITORY)}"
    r"/([0-9a-f]{40})/(.+)$"
)

InputShape = Literal["normalized-input", "raw-text", "raw-companion", "structured"]
Blocker = Literal[
    "normalized-input",
    "duplicate-sample",
    "unknown-source-table",
    "ambiguous-parser",
    "unsupported-parameters",
    "source-setup",
    "missing-tables",
    "column-types",
]
EXCLUDING_BLOCKERS = frozenset({"normalized-input", "duplicate-sample"})


class UpstreamFile(StrictModel):
    path: str
    git_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)


class ExistingUse(StrictModel):
    kind: Literal["reference-fixture", "corpus"]
    id: str
    path: str
    revision: str
    current: bool


class TableSchemaFile(StrictModel):
    file: UpstreamFile
    table: str
    columns: int = Field(ge=0)


class SampleRecord(StrictModel):
    file: UpstreamFile
    schema_name: str
    rows: int = Field(ge=0)
    columns: list[str]
    source_tables: dict[str, int]
    text_fill: dict[str, float]
    raw_companion: UpstreamFile | None = None
    raw_events: int | None = None
    input_shape: InputShape
    leaked_fields: dict[str, float]
    table_name_hints: list[str]
    table_schema_files: list[str]
    device_identities: list[tuple[str, str]]
    duplicate_of: str | None = None
    duplicate_similarity: float | None = None
    unpaired_reason: str | None = None
    existing_uses: list[ExistingUse] = Field(default_factory=list)


class ParserRecord(StrictModel):
    file: UpstreamFile
    name: str
    schema_name: str
    schema_version: str | None
    product: str | None
    builtin: str | None
    filtering_variant: str | None
    parameters: list[str]
    vendors: list[str]
    products: list[str]
    tables: list[str]
    helpers: list[str]
    existing_uses: list[ExistingUse] = Field(default_factory=list)


class TesterResult(StrictModel):
    file: UpstreamFile
    schema_name: str
    kind: Literal["DataTest", "SchemaTest"]
    variant: Literal["ASim", "vim"]
    parsers: list[str]
    counts: dict[str, int]
    errors: list[str]


class Pairing(StrictModel):
    schema_name: str
    parser: str
    sample: str
    basis: list[str]
    parsed_text_columns: list[str]
    missing_tables: list[str]
    fixtures: list[str]
    blockers: list[Blocker]
    status: Literal["ready", "needs-setup", "excluded"]


class SkippedFile(StrictModel):
    path: str
    reason: str


class AsimInventory(StrictModel):
    format_version: Literal["1"] = "1"
    repository: str = azure_sentinel.REPOSITORY_URL
    requested_revision: str
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    schemas: list[str]
    listed_files: dict[str, int]
    parsers: list[ParserRecord]
    samples: list[SampleRecord]
    table_schemas: list[TableSchemaFile]
    tester_results: list[TesterResult]
    pairings: list[Pairing]
    skipped: list[SkippedFile]
    warnings: list[str]


@dataclass(frozen=True)
class _Use:
    kind: Literal["reference-fixture", "corpus"]
    id: str
    path: str
    revision: str
    sha256: str
    paths: frozenset[str]


def build_inventory(
    *,
    revision: str,
    cache_dir: Path,
    schemas: Sequence[str] = DEFAULT_SCHEMAS,
    fixtures_dir: Path | None = None,
    corpora_dir: Path | None = None,
    fetch: FetchBytes | None = None,
) -> AsimInventory:
    """List, verify, and statically analyse upstream samples and parsers at one commit."""
    fetch = fetch or azure_sentinel.fetch_bytes
    schemas = list(dict.fromkeys(schemas))
    if not schemas or any(not re.fullmatch(r"[A-Z][A-Za-z]+", schema) for schema in schemas):
        raise ValueError("Schemas must be ASIM schema names such as Authentication")
    requested = azure_sentinel.validate_revision(revision)
    resolved = azure_sentinel.resolve_revision(requested, fetch)
    tree = UpstreamTree(resolved, fetch)
    cache = BlobCache(cache_dir, resolved, fetch)
    builder = _Builder(schemas)
    try:
        sample_entries = tree.files(SAMPLE_ROOT)
        builder.listed[SAMPLE_ROOT] = len(sample_entries)
        builder.add_sample_directory(sample_entries, cache)
        for schema in schemas:
            for folder in ("Parsers", "Tests", "test"):
                directory = f"Parsers/ASim{schema}/{folder}"
                entries = tree.files(directory)
                builder.listed[directory] = len(entries)
                if folder == "Parsers":
                    builder.add_parsers(schema, entries, cache)
                else:
                    builder.add_testers(schema, entries, cache)
    finally:
        cache.save()
    return builder.finish(requested, resolved, _existing_uses(fixtures_dir, corpora_dir))


def _upstream_file(entry: TreeEntry, sha256: str) -> UpstreamFile:
    return UpstreamFile(path=entry.path, git_sha=entry.git_sha, sha256=sha256, size=entry.size)


@dataclass
class _Sample:
    file: UpstreamFile
    schema: str
    profile: static.CsvProfile
    raw: UpstreamFile | None = None
    raw_events: int | None = None


class _Builder:
    def __init__(self, schemas: list[str]) -> None:
        self.schemas = schemas
        self.listed: dict[str, int] = {}
        self.samples: list[_Sample] = []
        self.table_schemas: list[TableSchemaFile] = []
        self.parsers: list[tuple[ParserRecord, static.ParserFacts]] = []
        self.testers: list[TesterResult] = []
        self.skipped: list[SkippedFile] = []
        self.warnings: list[str] = []

    def add_sample_directory(self, entries: list[TreeEntry], cache: BlobCache) -> None:
        kinds = {entry.path: static.sample_file_kind(entry.path) for entry in entries}
        hints = {entry.path: static.schema_hint(entry.path, self.schemas) for entry in entries}
        wanted = [
            entry
            for entry in entries
            if kinds[entry.path] == "table-schema"
            or (hints[entry.path] and kinds[entry.path] in {"ingested-csv", "raw", "tester-result"})
        ]
        contents = cache.read_all(wanted)
        raws: dict[str, TreeEntry] = {}
        for entry in wanted:
            kind = kinds[entry.path]
            content, sha256 = contents[entry.path]
            if kind == "raw":
                key = static.raw_companion_key(entry.path)
                if key:
                    raws[key] = entry
            elif kind == "table-schema":
                self._add_table_schema(entry, content, sha256)
            elif kind == "tester-result":
                schema = hints[entry.path]
                assert schema is not None
                self._add_tester(schema, entry, content, sha256)
        for entry in wanted:
            schema = hints[entry.path]
            if kinds[entry.path] != "ingested-csv" or schema is None:
                continue
            content, sha256 = contents[entry.path]
            try:
                profile = static.profile_csv(content)
            except (UnicodeError, ValueError) as error:
                self.warnings.append(f"Could not profile {entry.path}: {error}")
                continue
            if static.is_table_schema_header(profile.columns):
                self._add_table_schema(entry, content, sha256, schema)
                continue
            sample = _Sample(_upstream_file(entry, sha256), schema, profile)
            raw = raws.pop(entry.path.casefold(), None)
            if raw is not None:
                raw_content, raw_sha256 = contents[raw.path]
                sample.raw = _upstream_file(raw, raw_sha256)
                sample.raw_events = static.raw_event_count(raw.path, raw_content)
            self.samples.append(sample)
        for raw in raws.values():
            self.warnings.append(f"Raw log file has no ingested CSV companion: {raw.path}")

    def _add_table_schema(
        self, entry: TreeEntry, content: bytes, sha256: str, schema: str | None = None
    ) -> None:
        try:
            columns = static.table_schema_columns(content)
        except (UnicodeError, ValueError) as error:
            self.warnings.append(f"Could not read table schema {entry.path}: {error}")
            return
        table = re.sub(r"(?:_schema)?\.csv$", "", entry.path.rsplit("/", 1)[-1], flags=re.I)
        if schema is not None:
            # Files such as CommonSecurityLog_NetworkSession.csv name the table, then the schema.
            table = re.sub(rf"_(?:asim|vim)?{re.escape(schema)}$", "", table, flags=re.I)
        self.table_schemas.append(
            TableSchemaFile(file=_upstream_file(entry, sha256), table=table, columns=len(columns))
        )

    def add_parsers(self, schema: str, entries: list[TreeEntry], cache: BlobCache) -> None:
        wanted = [entry for entry in entries if entry.path.lower().endswith((".yaml", ".yml"))]
        contents = cache.read_all(wanted)
        sources: dict[str, tuple[ParserRecord, static.ParserFacts]] = {}
        variants: dict[str, static.ParserFacts] = {}
        variant_paths: dict[str, str] = {}
        for entry in wanted:
            content, sha256 = contents[entry.path]
            try:
                facts = static.parser_facts(content)
            except (UnicodeError, ValueError, yaml.YAMLError) as error:
                self.skipped.append(SkippedFile(path=entry.path, reason=f"unreadable: {error}"))
                continue
            reason = _parser_skip_reason(facts, schema)
            if reason == "filtering-variant":
                variants[facts.name] = facts
                variant_paths[facts.name] = entry.path
                continue
            if reason is None and facts.name in sources:
                reason = "duplicate-parser-name"
            if reason is not None:
                self.skipped.append(SkippedFile(path=entry.path, reason=reason))
                continue
            record = ParserRecord(
                file=_upstream_file(entry, sha256),
                name=facts.name,
                schema_name=schema,
                schema_version=facts.schema_version,
                product=facts.product,
                builtin=facts.builtin,
                filtering_variant=None,
                parameters=list(facts.parameters),
                vendors=list(facts.vendors),
                products=list(facts.products),
                tables=list(facts.tables),
                helpers=list(facts.helpers),
            )
            sources[facts.name] = (record, facts)
        for name, (record, facts) in sources.items():
            variant = _filtering_variant(facts, variants)
            if variant is not None:
                del variants[variant.name]
                record.filtering_variant = variant.name
                if variant.name != f"vim{name.removeprefix('ASim')}":
                    self.warnings.append(
                        f"Filtering variant {variant.name} does not mirror {name}; "
                        "matched by EquivalentBuiltInParser"
                    )
            if not record.tables:
                self.warnings.append(f"No source table recognised in parser {name}")
            self.parsers.append((record, facts))
        for name in sorted(variants):
            self.skipped.append(
                SkippedFile(path=variant_paths[name], reason="filtering-variant-without-source")
            )

    def add_testers(self, schema: str, entries: list[TreeEntry], cache: BlobCache) -> None:
        wanted = [
            entry
            for entry in entries
            if entry.path.lower().endswith(".csv") and static.tester_kind(entry.path)
        ]
        contents = cache.read_all(wanted)
        for entry in wanted:
            content, sha256 = contents[entry.path]
            self._add_tester(schema, entry, content, sha256)

    def _add_tester(self, schema: str, entry: TreeEntry, content: bytes, sha256: str) -> None:
        kind = static.tester_kind(entry.path)
        if kind is None:
            return
        try:
            summary = static.tester_summary(content)
        except (UnicodeError, ValueError) as error:
            self.warnings.append(f"Could not read tester output {entry.path}: {error}")
            return
        self.testers.append(
            TesterResult(
                file=_upstream_file(entry, sha256),
                schema_name=schema,
                kind=kind,
                variant=static.tester_variant(entry.path),
                parsers=[],
                counts=summary.counts,
                errors=list(summary.errors[:10]),
            )
        )

    def finish(self, requested: str, resolved: str, uses: list[_Use]) -> AsimInventory:
        order = {schema: index for index, schema in enumerate(self.schemas)}
        self.parsers.sort(key=lambda item: (order[item[0].schema_name], item[0].name))
        self.samples.sort(key=lambda sample: sample.file.path)
        current = {
            item.file.path: item.file.sha256
            for item in [*(record for record, _ in self.parsers), *self.table_schemas]
        }
        for sample in self.samples:
            current[sample.file.path] = sample.file.sha256
            if sample.raw is not None:
                current[sample.raw.path] = sample.raw.sha256

        def uses_of(*paths: str) -> list[ExistingUse]:
            return [
                ExistingUse(
                    kind=use.kind,
                    id=use.id,
                    path=use.path,
                    revision=use.revision,
                    current=current.get(use.path) == use.sha256,
                )
                for use in uses
                if use.path in paths
            ]

        for record, _ in self.parsers:
            record.existing_uses = uses_of(record.file.path)
        candidates = _candidates(self.samples, self.parsers)
        duplicates = _duplicates(
            self.samples,
            used={use.path for use in uses},
            paired={
                sample.file.path
                for sample, found in zip(self.samples, candidates, strict=True)
                if found
            },
        )
        records = [
            self._sample_record(sample, duplicates.get(sample.file.path), uses_of)
            for sample in self.samples
        ]
        pairings = _pairings(
            records, candidates, self.parsers, self.table_schemas, uses, self.schemas
        )
        for tester in self.testers:
            tester.parsers = _tester_parsers(tester, [record for record, _ in self.parsers])
        table_schemas = sorted(self.table_schemas, key=_file_path)
        testers = sorted(self.testers, key=_file_path)
        skipped = sorted(self.skipped, key=lambda item: item.path)
        return AsimInventory(
            requested_revision=requested,
            revision=resolved,
            schemas=self.schemas,
            listed_files=self.listed,
            parsers=[record for record, _ in self.parsers],
            samples=records,
            table_schemas=table_schemas,
            tester_results=testers,
            pairings=pairings,
            skipped=skipped,
            warnings=self.warnings,
        )

    def _sample_record(
        self,
        sample: _Sample,
        duplicate: tuple[str, float] | None,
        uses_of: Callable[..., list[ExistingUse]],
    ) -> SampleRecord:
        profile = sample.profile
        tables = [table for table in profile.source_tables if table]
        covering = [
            item.file.path
            for item in self.table_schemas
            if any(_covers(item.table, table) for table in tables)
        ]
        paths = [sample.file.path, *([sample.raw.path] if sample.raw else [])]
        return SampleRecord(
            file=sample.file,
            schema_name=sample.schema,
            rows=profile.rows,
            columns=list(profile.columns),
            source_tables=profile.source_tables,
            text_fill={
                column: profile.fill[column]
                for column in static.TEXT_COLUMNS
                if column in profile.fill
            },
            raw_companion=sample.raw,
            raw_events=sample.raw_events,
            input_shape=_input_shape(sample, tables),
            leaked_fields={
                column: profile.fill[column]
                for column in static.LEAKAGE_COLUMNS
                if profile.fill.get(column, 0.0) > 0
            },
            table_name_hints=static.table_name_hints(tables),
            table_schema_files=sorted(covering),
            device_identities=list(profile.device_identities),
            duplicate_of=duplicate[0] if duplicate else None,
            duplicate_similarity=duplicate[1] if duplicate else None,
            existing_uses=uses_of(*paths),
        )


def _file_path(item: TableSchemaFile | TesterResult) -> str:
    return item.file.path


def _filtering_variant(
    source: static.ParserFacts, variants: dict[str, static.ParserFacts]
) -> static.ParserFacts | None:
    """The `vim` twin by mirrored name, else by mirrored `EquivalentBuiltInParser`."""
    mirrored = variants.get(f"vim{source.name.removeprefix('ASim')}")
    if mirrored is not None or not source.builtin:
        return mirrored
    builtin = re.sub(r"^_ASim_", "_Im_", source.builtin, flags=re.I).casefold()
    return next(
        (
            variant
            for variant in variants.values()
            if variant.builtin and variant.builtin.casefold() == builtin
        ),
        None,
    )


def _parser_skip_reason(facts: static.ParserFacts, schema: str) -> str | None:
    if facts.schema_name is not None and facts.schema_name != schema:
        return f"other-schema: {facts.schema_name}"
    if facts.name in {f"ASim{schema}", f"im{schema}"} or facts.name.startswith("im"):
        return "schema-union"
    if facts.name.casefold().endswith("empty"):
        return "empty-parser"
    if facts.name.startswith("vim"):
        return "filtering-variant"
    if not facts.name.startswith("ASim"):
        return "unrecognised-name"
    return None


def _input_shape(sample: _Sample, tables: list[str]) -> InputShape:
    fill = sample.profile.fill
    if any(_NORMALIZED_TABLE.match(table) for table in tables) or fill.get("EventSchema", 0) > 0.5:
        return "normalized-input"
    if any(fill.get(column, 0.0) >= RAW_TEXT_FILL for column in static.RAW_TEXT_COLUMNS):
        return "raw-text"
    if sample.raw is not None:
        return "raw-companion"
    return "structured"


def _covers(schema_file_table: str, table: str) -> bool:
    stem = schema_file_table.casefold()
    return stem == table.casefold() or stem.endswith(f"_{table.casefold()}")


def _duplicates(
    samples: list[_Sample], *, used: set[str], paired: set[str]
) -> dict[str, tuple[str, float]]:
    """Map each redundant sample to the retained sample whose rows it repeats.

    The retained copy is the one an existing fixture or corpus already uses, then one a
    parser pairs with, then the larger, then the shorter path.
    """
    parent = {sample.file.path: sample.file.path for sample in samples}

    def root(path: str) -> str:
        while parent[path] != path:
            parent[path] = parent[parent[path]]
            path = parent[path]
        return path

    by_columns: dict[tuple[str, ...], list[_Sample]] = defaultdict(list)
    for sample in samples:
        if sample.profile.rows:
            by_columns[sample.profile.fingerprint_columns].append(sample)
    for group in by_columns.values():
        for index, left in enumerate(group):
            for right in group[index + 1 :]:
                similarity = static.content_similarity(
                    left.profile.fingerprints, right.profile.fingerprints
                )
                if similarity >= DUPLICATE_THRESHOLD:
                    parent[root(left.file.path)] = root(right.file.path)
    clusters: dict[str, list[_Sample]] = defaultdict(list)
    for sample in samples:
        clusters[root(sample.file.path)].append(sample)
    result: dict[str, tuple[str, float]] = {}
    for members in clusters.values():
        if len(members) < 2:
            continue
        kept = min(
            members,
            key=lambda sample: (
                sample.file.path not in used,
                sample.file.path not in paired,
                -sample.profile.rows,
                len(sample.file.path),
                sample.file.path,
            ),
        )
        for sample in members:
            if sample is not kept:
                similarity = static.content_similarity(
                    sample.profile.fingerprints, kept.profile.fingerprints
                )
                result[sample.file.path] = (kept.file.path, round(similarity, 4))
    return result


_Candidate = tuple[ParserRecord, static.ParserFacts, list[str]]


def _candidates(
    samples: list[_Sample], parsers: list[tuple[ParserRecord, static.ParserFacts]]
) -> list[list[_Candidate]]:
    """Parsers with static evidence of reading each sample, in sample order."""
    readers = Counter(
        (record.schema_name, table) for record, _ in parsers for table in record.tables
    )
    result: list[list[_Candidate]] = []
    for sample in samples:
        tables = {table for table in sample.profile.source_tables if table}
        found: list[_Candidate] = []
        for parser, facts in parsers:
            if parser.schema_name != sample.schema:
                continue
            basis = _pairing_basis(sample, parser, facts, tables, readers)
            if basis:
                found.append((parser, facts, basis))
        result.append(found)
    return result


def _pairings(
    records: list[SampleRecord],
    candidates: list[list[_Candidate]],
    parsers: list[tuple[ParserRecord, static.ParserFacts]],
    table_schemas: list[TableSchemaFile],
    uses: list[_Use],
    schemas: list[str],
) -> list[Pairing]:
    fixture_paths: dict[str, set[str]] = defaultdict(set)
    for use in uses:
        if use.kind == "reference-fixture":
            fixture_paths[use.id].update(use.paths)
    pairings: list[Pairing] = []
    for record, found in zip(records, candidates, strict=True):
        tables = {table for table in record.source_tables if table}
        if not found:
            same_schema = [
                parser for parser, _ in parsers if parser.schema_name == record.schema_name
            ]
            if not tables:
                record.unpaired_reason = "unknown-source-table"
            elif not any(tables <= set(parser.tables) for parser in same_schema):
                record.unpaired_reason = "no-parser-reads-tables"
            else:
                record.unpaired_reason = "shared-table-without-identity-evidence"
            continue
        for parser, facts, basis in found:
            missing = sorted(set(parser.tables) - tables) if tables else []
            blockers: list[Blocker] = []
            if record.input_shape == "normalized-input":
                blockers.append("normalized-input")
            if record.duplicate_of is not None:
                blockers.append("duplicate-sample")
            if not tables:
                blockers.append("unknown-source-table")
            if len(found) > 1:
                blockers.append("ambiguous-parser")
            if set(parser.parameters) - {"disabled"}:
                blockers.append("unsupported-parameters")
            if any(helper.casefold() in _SETUP_HELPERS for helper in parser.helpers):
                blockers.append("source-setup")
            if missing:
                blockers.append("missing-tables")
            if tables and not all(
                any(_covers(item.table, table) for item in table_schemas) for table in tables
            ):
                blockers.append("column-types")
            status: Literal["ready", "needs-setup", "excluded"] = (
                "excluded"
                if EXCLUDING_BLOCKERS.intersection(blockers)
                else "needs-setup"
                if blockers
                else "ready"
            )
            pairings.append(
                Pairing(
                    schema_name=record.schema_name,
                    parser=parser.name,
                    sample=record.file.path,
                    basis=basis,
                    parsed_text_columns=[
                        column
                        for column in static.RAW_TEXT_COLUMNS
                        if record.text_fill.get(column, 0.0) >= RAW_TEXT_FILL
                        and static.references_column(facts.code, column)
                    ],
                    missing_tables=missing,
                    fixtures=sorted(
                        fixture
                        for fixture, paths in fixture_paths.items()
                        if {record.file.path, parser.file.path} <= paths
                    ),
                    blockers=blockers,
                    status=status,
                )
            )
    order = {schema: index for index, schema in enumerate(schemas)}
    return sorted(pairings, key=lambda item: (order[item.schema_name], item.parser, item.sample))


def _pairing_basis(
    sample: _Sample,
    parser: ParserRecord,
    facts: static.ParserFacts,
    tables: set[str],
    readers: Counter[tuple[str, str]],
) -> list[str]:
    """Evidence that `parser` reads `sample`; empty when the pairing is unsupported."""
    if tables and not tables <= set(parser.tables):
        return []
    shared = not tables or any(readers[(parser.schema_name, table)] > 1 for table in tables)
    location = static.normalized_key(sample.file.path.removeprefix(f"{SAMPLE_ROOT}/"))
    vendors = [key for key in map(static.normalized_key, parser.vendors) if key]
    products = [key for key in map(static.normalized_key, parser.products) if key]
    named = any(key in location for key in vendors) and any(key in location for key in products)
    identities = sample.profile.device_identities
    device = bool(identities) and all(
        vendor.casefold() in facts.literals
        and (not product or product.casefold() in facts.literals)
        for vendor, product in identities
    )
    if shared and not (named or device):
        return []
    basis = ["source-table", "shared-table" if shared else "exclusive-table"]
    if not tables:
        basis = ["unknown-source-table"]
    if named:
        basis.append("vendor-product-name")
    if device:
        basis.append("device-literals")
    return basis


def _tester_parsers(tester: TesterResult, parsers: list[ParserRecord]) -> list[str]:
    """Best-effort parser identity for an irregularly named upstream tester export.

    A parser identity contained in the file name scores its length; failing that, a file
    name of at least six characters contained in an identity (`SecurityEvent_Firewall` in
    `MicrosoftSecurityEventFirewall`) scores the file name length. The longest wins, then
    containment in the file name, then a parser-name match over a vendor and product match,
    so `CiscoMeraki` is preferred to `CiscoMerakiSyslog`.
    """
    key = static.tester_key(tester.file.path, tester.schema_name)
    best: dict[str, tuple[int, bool, bool]] = {}
    for parser in parsers:
        if parser.schema_name != tester.schema_name:
            continue
        own = static.normalized_key(parser.name.removeprefix(f"ASim{parser.schema_name}"))
        identities = {(own, True)}
        identities.update(
            (static.normalized_key(vendor) + static.normalized_key(product), False)
            for vendor in parser.vendors
            for product in parser.products
        )
        scores = [
            (len(identity), True, by_name)
            for identity, by_name in identities
            if len(identity) >= 4 and identity in key
        ]
        scores += [
            (len(key), False, by_name)
            for identity, by_name in identities
            if len(key) >= 6 and key in identity
        ]
        if scores:
            best[parser.name] = max(scores)
    if not best:
        return []
    top = max(best.values())
    return sorted(name for name, score in best.items() if score == top)


def _existing_uses(fixtures_dir: Path | None, corpora_dir: Path | None) -> list[_Use]:
    """Upstream paths already pinned by reference fixtures or registered corpora."""
    uses: list[_Use] = []
    if fixtures_dir is not None and fixtures_dir.is_dir():
        for manifest in sorted(fixtures_dir.glob("*/manifest.json")):
            fixture = ReferenceFixture.model_validate_json(manifest.read_bytes())
            paths = frozenset(resource.source_path for resource in fixture.resources)
            uses.extend(
                _Use(
                    "reference-fixture",
                    fixture.fixture_id,
                    resource.source_path,
                    fixture.source_revision,
                    resource.sha256,
                    paths,
                )
                for resource in fixture.resources
            )
    if corpora_dir is not None and corpora_dir.is_dir():
        for _, corpus, _ in load_corpus_manifests(corpora_dir):
            for resource in corpus.resources:
                match = _RAW_GITHUB.match(resource.url)
                if match:
                    path = unquote(match.group(2))
                    uses.append(
                        _Use(
                            "corpus",
                            corpus.corpus_id,
                            path,
                            match.group(1),
                            resource.sha256,
                            frozenset({path}),
                        )
                    )
    return uses
