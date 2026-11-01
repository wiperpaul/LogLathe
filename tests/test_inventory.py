from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml
from fake_upstream import REVISION, FakeUpstream

from asim_forge import azure_sentinel
from asim_forge.cli import main
from asim_forge.reference import static_analysis as static
from asim_forge.reference.contracts import ReferenceFixture, Resource
from asim_forge.reference.inventory import AsimInventory, build_inventory
from asim_forge.reference.inventory_report import render_inventory

_SAMPLES = "Sample Data/ASIM"
_PARSERS = "Parsers/ASimAuthentication/Parsers"
_OLD_REVISION = "fedcba9876543210fedcba9876543210fedcba98"
_REMOVED_SAMPLE = f"{_SAMPLES}/Contoso_Gateway_ASimAuthentication_Removed.csv"
_UNION_PARSER = f"{_PARSERS}/ASimAuthentication.yaml"
_CONTOSO_SCHEMA = f"{_SAMPLES}/ContosoGateway_CL_Schema.csv"


def _parser(
    name: str,
    query: str,
    *,
    params: tuple[str, ...] = ("disabled",),
    builtin: str | None = None,
    schema: str = "Authentication",
) -> bytes:
    document = {
        "Parser": {"Title": name, "Version": "0.1.0"},
        "Product": {"Name": name},
        "Normalization": {"Schema": schema, "Version": "0.1.3"},
        "ParserName": name,
        "EquivalentBuiltInParser": builtin or f"_{name}",
        "ParserParams": [{"Name": param, "Type": "bool", "Default": False} for param in params],
        "ParserQuery": query,
    }
    return yaml.safe_dump(document, sort_keys=False).encode()


def _csv(header: str, *rows: str) -> bytes:
    return ("\n".join([header, *rows]) + "\n").encode()


_CONTOSO_ROWS = (
    '2024-01-01,ContosoGateway_CL,"user=alice action=login result=ok"',
    '2024-01-01,ContosoGateway_CL,"user=bob action=login result=fail"',
)
_FILES = {
    f"{_SAMPLES}/Contoso_Gateway_ASimAuthentication_IngestedLogs.csv": _csv(
        "TimeGenerated [UTC],Type,Message", *_CONTOSO_ROWS
    ),
    f"{_SAMPLES}/Contoso_Gateway_ASimAuthentication_RawLogs.txt": (
        b"user=alice action=login result=ok\nuser=bob action=login result=fail\n"
    ),
    f"{_SAMPLES}/Contoso_Gateway_ASimAuthentication_ContosoGateway_CL_IngestedLogs.csv": _csv(
        "TimeGenerated [UTC],Type,Message",
        *(row.replace("2024-01-01", "2025-06-30") for row in _CONTOSO_ROWS),
    ),
    f"{_SAMPLES}/ContosoGateway_CL_Schema.csv": _csv(
        "ColumnName,ColumnType", "TimeGenerated,datetime", "Message,string"
    ),
    f"{_SAMPLES}/Contoso_Gateway_Authentication_SchemaTest.csv": _csv(
        "Result", '"(2) Info: Schema is valid (Schema:Authentication)"'
    ),
    f"{_SAMPLES}/Contoso_Gateway_Dns_IngestedLogs.csv": _csv("Type", "ContosoDns_CL"),
    f"{_SAMPLES}/Fabrikam_Edge_Authentication_IngestedLogs.csv": _csv(
        "Type,Computer,SyslogMessage", "Syslog,edge-1,login accepted for carol"
    ),
    f"{_SAMPLES}/Syslog_Authentication_IngestedLogs.csv": _csv(
        "Type,Computer,SyslogMessage", "Syslog,host-9,session opened for dave"
    ),
    f"{_SAMPLES}/Northwind_Portal_Authentication_IngestedLogs.csv": _csv(
        "Type,UserName_s", "NorthwindAudit_CL,erin"
    ),
    f"{_SAMPLES}/VPN_Authentication_IngestedLogs.csv": _csv(
        "Type,DeviceVendor,DeviceProduct,SourceUserName",
        "CommonSecurityLog,Tailspin,Toys VPN,frank",
    ),
    f"{_SAMPLES}/Wingtip CEF/Wingtip_SSO_Authentication_IngestedLogs.csv": _csv(
        "DeviceVendor,DeviceProduct,SourceUserName", "Wingtip,SSO Gateway,grace"
    ),
    f"{_SAMPLES}/Microsoft_NativeTable_Authentication_IngestedLogs.csv": _csv(
        "Type,EventSchema,EventType,TargetUsername",
        "ASimAuthenticationEventLogs,Authentication,Logon,heidi",
    ),
    f"{_SAMPLES}/Cynerio_Cynerio_Authentication_IngestedLogs.csv": _csv(
        "Type,user_s", "CynerioEvent_CL,ivan"
    ),
    f"{_SAMPLES}/Orphan_Authentication_RawLogs.txt": b"orphan\n",
    f"{_PARSERS}/ASimAuthenticationContosoGateway.yaml": _parser(
        "ASimAuthenticationContosoGateway",
        "let parser = (disabled: bool = false) {\n"
        "  ContosoGateway_CL\n"
        "  | where not(disabled)\n"
        '  | parse Message with "user=" TargetUsername " " *\n'
        '  | extend EventVendor = "Contoso", EventProduct = "Gateway"\n'
        "};\n"
        "parser(disabled = disabled)\n",
        builtin="_ASim_Authentication_ContosoGateway",
    ),
    f"{_PARSERS}/vimAuthenticationContosoGateway.yaml": _parser(
        "vimAuthenticationContosoGateway",
        "ContosoGateway_CL | where true\n",
        params=("disabled", "starttime"),
        builtin="_Im_Authentication_ContosoGateway",
    ),
    f"{_PARSERS}/ASimAuthenticationFabrikamEdge.yaml": _parser(
        "ASimAuthenticationFabrikamEdge",
        "let parser = (disabled: bool = false) {\n"
        "  Syslog\n"
        '  | where not(disabled) and ProcessName == "fabrikam-edge"\n'
        '  | where Computer in (_ASIM_GetSourceBySourceType("FabrikamEdge"))\n'
        '  | extend EventVendor = "Fabrikam", EventProduct = "Edge"\n'
        "};\n"
        "parser(disabled = disabled)\n",
    ),
    f"{_PARSERS}/ASimAuthenticationNorthwindPortal.yaml": _parser(
        "ASimAuthenticationNorthwindPortal",
        "let parser = (disabled: bool = false) {\n"
        "  union isfuzzy=true\n"
        '    (Syslog | where ProcessName == "northwind"),\n'
        "    (NorthwindAudit_CL | where not(disabled))\n"
        '  | extend EventVendor = "Northwind", EventProduct = "Portal"\n'
        "};\n"
        "parser(disabled = disabled)\n",
    ),
    f"{_PARSERS}/ASimAuthenticationTailspinVPN.yaml": _parser(
        "ASimAuthenticationTailspinVPN",
        "let parser = (disabled: bool = false, pack: bool = false) {\n"
        "  // FakeCommentTable | where false\n"
        "  CommonSecurityLog\n"
        '  | where DeviceVendor == "Tailspin" and DeviceProduct == "Toys VPN"\n'
        '  | extend EventVendor = "Tailspin", EventProduct = "VPN"\n'
        "};\n"
        "parser(disabled = disabled, pack = pack)\n",
        params=("disabled", "pack"),
        builtin="_ASim_Authentication_TailspinVPN",
    ),
    f"{_PARSERS}/vimAuthenticationTailspinVPN.yaml": _parser(
        "vimAuthenticationTailspnVPN",
        "CommonSecurityLog | where true\n",
        builtin="_Im_Authentication_TailspinVPN",
    ),
    f"{_PARSERS}/ASimAuthenticationWingtipSSO.yaml": _parser(
        "ASimAuthenticationWingtipSSO",
        "let parser = (disabled: bool = false) {\n"
        "  CommonSecurityLog\n"
        '  | where DeviceVendor == "Wingtip" and DeviceProduct == "SSO Gateway"\n'
        '  | extend EventVendor = "Wingtip", EventProduct = "SSO"\n'
        "};\n"
        "parser(disabled = disabled)\n",
    ),
    f"{_PARSERS}/ASimAuthenticationNative.yaml": _parser(
        "ASimAuthenticationNative",
        "let parser = (disabled: bool = false) {\n"
        "  ASimAuthenticationEventLogs\n"
        "  | where not(disabled)\n"
        "};\n"
        "parser(disabled = disabled)\n",
    ),
    f"{_PARSERS}/ASimAuthentication.yaml": _parser(
        "ASimAuthentication",
        "union isfuzzy=true vimAuthenticationEmpty, ASimAuthenticationNative(false)\n",
    ),
    f"{_PARSERS}/imAuthentication.yaml": _parser(
        "imAuthentication", "union isfuzzy=true vimAuthenticationEmpty\n"
    ),
    f"{_PARSERS}/vimAuthenticationEmpty.yaml": _parser(
        "vimAuthenticationEmpty", "datatable(EventType: string)[]\n"
    ),
    "Parsers/ASimAuthentication/Tests/Contoso_Gateway_ASimAuthentication_DataTest.csv": _csv(
        "Result",
        '"(0) Error: 1 invalid value(s) for field [EventProduct] of type [Enumerated]: '
        '[""Gateway""] (Schema:Authentication)"',
        '"(2) Info: 2 records checked (Schema:Authentication)"',
    ),
    "Parsers/ASimAuthentication/test/Northwind/Northwind_Portal_vimAuthentication_SchemaTest (1)"
    ".csv.csv": _csv("Result", '"(1) Warning: Missing recommended field [Dvc]"'),
}
_CONTOSO_CSV = f"{_SAMPLES}/Contoso_Gateway_ASimAuthentication_IngestedLogs.csv"
_CONTOSO_RAW = f"{_SAMPLES}/Contoso_Gateway_ASimAuthentication_RawLogs.txt"
_CONTOSO_PARSER = f"{_PARSERS}/ASimAuthenticationContosoGateway.yaml"


def _sha256(path: str) -> str:
    return hashlib.sha256(_FILES[path]).hexdigest()


def _registries(tmp_path: Path) -> tuple[Path, Path]:
    fixtures = tmp_path / "fixtures"
    (fixtures / "contoso").mkdir(parents=True, exist_ok=True)
    fixture = ReferenceFixture(
        fixture_id="contoso-gateway-authentication-v1",
        group_id="contoso-gateway",
        source_revision=_OLD_REVISION,
        catalogue_revision=_OLD_REVISION,
        schema_name="Authentication",
        schema_version="0.1.3",
        source_table="ContosoGateway_CL",
        message_field="Message",
        vendor="Contoso",
        product="Gateway",
        csv_file="sample.csv",
        parser_file="parser.yaml",
        helpers=[],
        columns={"TimeGenerated": "datetime", "Message": "string"},
        datetime_format="%Y-%m-%d",
        resources=[
            Resource(file="sample.csv", source_path=_CONTOSO_CSV, sha256=_sha256(_CONTOSO_CSV)),
            Resource(file="parser.yaml", source_path=_CONTOSO_PARSER, sha256="0" * 64),
            Resource(file="removed.csv", source_path=_REMOVED_SAMPLE, sha256="1" * 64),
            Resource(file="union.yaml", source_path=_UNION_PARSER, sha256="2" * 64),
            Resource(file="schema.csv", source_path=_CONTOSO_SCHEMA, sha256="4" * 64),
            Resource(file="LICENSE", source_path="LICENSE", sha256="3" * 64),
        ],
        redistribution="content",
        attribution="Synthetic test fixture.",
        mutation_suite="none",
    )
    (fixtures / "contoso" / "manifest.json").write_text(fixture.model_dump_json(), "utf-8")
    corpora = tmp_path / "corpora"
    (corpora / "contoso-raw").mkdir(parents=True, exist_ok=True)
    corpus = {
        "corpus_id": "contoso-raw",
        "title": "Contoso raw logs",
        "track": "schema-hint",
        "objective": "Synthetic schema hint",
        "system": "contoso",
        "interpretation": "Synthetic.",
        "source": {"project": "Synthetic", "url": "https://example.test", "terms": "Test."},
        "resources": [
            {
                "role": "input",
                "url": "https://raw.githubusercontent.com/Azure/Azure-Sentinel/"
                f"{_OLD_REVISION}/Sample%20Data/ASIM/"
                "Contoso_Gateway_ASimAuthentication_RawLogs.txt",
                "sha256": _sha256(_CONTOSO_RAW),
            }
        ],
        "schema_hint": "Authentication",
    }
    (corpora / "contoso-raw" / "manifest.json").write_text(json.dumps(corpus), "utf-8")
    return fixtures, corpora


def _build(tmp_path: Path, upstream: FakeUpstream | None = None) -> AsimInventory:
    fixtures, corpora = _registries(tmp_path)
    return build_inventory(
        revision="master",
        cache_dir=tmp_path / "cache",
        schemas=["Authentication"],
        fixtures_dir=fixtures,
        corpora_dir=corpora,
        fetch=upstream or FakeUpstream(_FILES),
    )


def test_inventory_pairs_samples_with_parsers_by_table_and_identity(tmp_path: Path) -> None:
    upstream = FakeUpstream(_FILES)
    inventory = _build(tmp_path, upstream)

    assert inventory.revision == REVISION
    assert inventory.requested_revision == "master"
    pairings = {(item.parser, item.sample.rsplit("/", 1)[-1]): item for item in inventory.pairings}
    assert {key: item.basis for key, item in pairings.items()} == {
        (
            "ASimAuthenticationContosoGateway",
            "Contoso_Gateway_ASimAuthentication_ContosoGateway_CL_IngestedLogs.csv",
        ): ["source-table", "exclusive-table", "vendor-product-name"],
        (
            "ASimAuthenticationContosoGateway",
            "Contoso_Gateway_ASimAuthentication_IngestedLogs.csv",
        ): ["source-table", "exclusive-table", "vendor-product-name"],
        ("ASimAuthenticationFabrikamEdge", "Fabrikam_Edge_Authentication_IngestedLogs.csv"): [
            "source-table",
            "shared-table",
            "vendor-product-name",
        ],
        ("ASimAuthenticationNative", "Microsoft_NativeTable_Authentication_IngestedLogs.csv"): [
            "source-table",
            "exclusive-table",
        ],
        (
            "ASimAuthenticationNorthwindPortal",
            "Northwind_Portal_Authentication_IngestedLogs.csv",
        ): ["source-table", "exclusive-table", "vendor-product-name"],
        ("ASimAuthenticationTailspinVPN", "VPN_Authentication_IngestedLogs.csv"): [
            "source-table",
            "shared-table",
            "device-literals",
        ],
        ("ASimAuthenticationWingtipSSO", "Wingtip_SSO_Authentication_IngestedLogs.csv"): [
            "unknown-source-table",
            "vendor-product-name",
            "device-literals",
        ],
    }
    contoso = pairings[
        ("ASimAuthenticationContosoGateway", "Contoso_Gateway_ASimAuthentication_IngestedLogs.csv")
    ]
    assert (contoso.status, contoso.blockers) == ("ready", [])
    assert contoso.parsed_text_columns == ["Message"]
    assert contoso.fixtures == ["contoso-gateway-authentication-v1"]
    expected = {
        "Contoso_Gateway_ASimAuthentication_ContosoGateway_CL_IngestedLogs.csv": (
            "excluded",
            ["duplicate-sample"],
        ),
        "Fabrikam_Edge_Authentication_IngestedLogs.csv": (
            "needs-setup",
            ["source-setup", "column-types"],
        ),
        "Microsoft_NativeTable_Authentication_IngestedLogs.csv": (
            "excluded",
            ["normalized-input", "column-types"],
        ),
        "Northwind_Portal_Authentication_IngestedLogs.csv": (
            "needs-setup",
            ["missing-tables", "column-types"],
        ),
        "VPN_Authentication_IngestedLogs.csv": (
            "needs-setup",
            ["unsupported-parameters", "column-types"],
        ),
        "Wingtip_SSO_Authentication_IngestedLogs.csv": ("needs-setup", ["unknown-source-table"]),
    }
    for (_, sample), item in pairings.items():
        if sample in expected:
            assert (item.status, item.blockers) == expected[sample], sample
    northwind = pairings[
        ("ASimAuthenticationNorthwindPortal", "Northwind_Portal_Authentication_IngestedLogs.csv")
    ]
    assert northwind.missing_tables == ["Syslog"]
    tailspin = next(p for p in inventory.parsers if p.name == "ASimAuthenticationTailspinVPN")
    assert tailspin.tables == ["CommonSecurityLog"]
    assert tailspin.filtering_variant == "vimAuthenticationTailspnVPN"
    assert "Contoso_Gateway_Dns_IngestedLogs.csv" not in " ".join(upstream.raw_requests())


def test_inventory_records_unpaired_duplicate_and_leaking_samples(tmp_path: Path) -> None:
    inventory = _build(tmp_path)
    samples = {sample.file.path.rsplit("/", 1)[-1]: sample for sample in inventory.samples}

    assert {name: sample.unpaired_reason for name, sample in samples.items()} == {
        "Contoso_Gateway_ASimAuthentication_ContosoGateway_CL_IngestedLogs.csv": None,
        "Contoso_Gateway_ASimAuthentication_IngestedLogs.csv": None,
        "Cynerio_Cynerio_Authentication_IngestedLogs.csv": "no-parser-reads-tables",
        "Fabrikam_Edge_Authentication_IngestedLogs.csv": None,
        "Microsoft_NativeTable_Authentication_IngestedLogs.csv": None,
        "Northwind_Portal_Authentication_IngestedLogs.csv": None,
        "Syslog_Authentication_IngestedLogs.csv": "shared-table-without-identity-evidence",
        "VPN_Authentication_IngestedLogs.csv": None,
        "Wingtip_SSO_Authentication_IngestedLogs.csv": None,
    }
    duplicate = samples["Contoso_Gateway_ASimAuthentication_ContosoGateway_CL_IngestedLogs.csv"]
    assert duplicate.duplicate_of == _CONTOSO_CSV
    assert duplicate.duplicate_similarity == 1.0
    contoso = samples["Contoso_Gateway_ASimAuthentication_IngestedLogs.csv"]
    assert contoso.input_shape == "raw-text"
    assert contoso.raw_companion is not None
    assert (contoso.raw_companion.path, contoso.raw_events) == (_CONTOSO_RAW, 2)
    assert contoso.table_schema_files == [f"{_SAMPLES}/ContosoGateway_CL_Schema.csv"]
    native = samples["Microsoft_NativeTable_Authentication_IngestedLogs.csv"]
    assert native.input_shape == "normalized-input"
    assert native.leaked_fields == {"EventSchema": 1.0, "EventType": 1.0}
    assert native.table_name_hints == ["auth"]
    assert samples["VPN_Authentication_IngestedLogs.csv"].device_identities == [
        ("Tailspin", "Toys VPN")
    ]
    assert samples["Northwind_Portal_Authentication_IngestedLogs.csv"].input_shape == "structured"


def test_inventory_tracks_existing_uses_testers_and_skips(tmp_path: Path) -> None:
    inventory = _build(tmp_path)

    uses = {
        (use.kind, use.path): use.upstream
        for item in [*inventory.parsers, *inventory.samples, *inventory.table_schemas]
        for use in item.existing_uses
    }
    assert uses == {
        ("reference-fixture", _CONTOSO_PARSER): "changed",
        ("reference-fixture", _CONTOSO_CSV): "unchanged",
        ("reference-fixture", _CONTOSO_SCHEMA): "changed",
        ("corpus", _CONTOSO_RAW): "unchanged",
    }
    assert [(use.path, use.upstream) for use in inventory.unattached_uses] == [
        (_UNION_PARSER, "not-inventoried"),
        (_REMOVED_SAMPLE, "missing"),
    ]
    testers = {
        tester.file.path.rsplit("/", 1)[-1]: (tester.kind, tester.variant, tester.parsers)
        for tester in inventory.tester_results
    }
    assert testers == {
        "Contoso_Gateway_ASimAuthentication_DataTest.csv": (
            "DataTest",
            "ASim",
            ["ASimAuthenticationContosoGateway"],
        ),
        "Contoso_Gateway_Authentication_SchemaTest.csv": (
            "SchemaTest",
            "ASim",
            ["ASimAuthenticationContosoGateway"],
        ),
        "Northwind_Portal_vimAuthentication_SchemaTest (1).csv.csv": (
            "SchemaTest",
            "vim",
            ["ASimAuthenticationNorthwindPortal"],
        ),
    }
    data_test = next(t for t in inventory.tester_results if t.kind == "DataTest")
    assert data_test.counts == {"error": 1, "info": 1}
    assert data_test.errors == [
        '1 invalid value(s) for field [EventProduct] of type [Enumerated]: ["Gateway"]'
    ]
    assert {(item.path.rsplit("/", 1)[-1], item.reason) for item in inventory.skipped} == {
        ("ASimAuthentication.yaml", "schema-union"),
        ("imAuthentication.yaml", "schema-union"),
        ("vimAuthenticationEmpty.yaml", "empty-parser"),
    }
    assert inventory.listed_files == {
        _SAMPLES: 14,
        _PARSERS: 11,
        "Parsers/ASimAuthentication/Tests": 1,
        "Parsers/ASimAuthentication/test": 1,
    }
    assert any("vimAuthenticationTailspnVPN does not mirror" in w for w in inventory.warnings)
    assert any("Orphan_Authentication_RawLogs.txt" in w for w in inventory.warnings)


def test_inventory_reuses_the_verified_cache(tmp_path: Path) -> None:
    first = FakeUpstream(_FILES)
    _build(tmp_path, first)
    second = FakeUpstream(_FILES)
    _build(tmp_path, second)

    assert first.raw_requests()
    assert second.raw_requests() == []


def test_inventory_reports_unreadable_upstream_files(tmp_path: Path) -> None:
    files = {
        **_FILES,
        f"{_PARSERS}/ASimAuthenticationBroken.yaml": b"ParserName: [unclosed\n",
        f"{_SAMPLES}/Broken_Schema.csv": b"Name,Type\nMessage,string\n",
        f"{_SAMPLES}/Litware_Authentication_IngestedLogs.csv": _csv("Type", "Litware_CL"),
        f"{_SAMPLES}/Litware_Authentication_RawLogs.json": b'[{"a": 1}, {"a": 2}]',
    }

    inventory = _build(tmp_path, FakeUpstream(files))

    skipped = {item.path: item.reason for item in inventory.skipped}
    assert skipped[f"{_PARSERS}/ASimAuthenticationBroken.yaml"].startswith("unreadable: ")
    assert any(
        w.startswith(f"Could not read table schema {_SAMPLES}/Broken_Schema.csv")
        for w in inventory.warnings
    )
    litware = next(
        s
        for s in inventory.samples
        if s.file.path.endswith("Litware_Authentication_IngestedLogs.csv")
    )
    assert litware.raw_events == 2
    assert litware.unpaired_reason == "no-parser-reads-tables"


def test_inventory_reads_sample_directory_column_exports_as_table_schemas(tmp_path: Path) -> None:
    export = f"{_SAMPLES}/CommonSecurityLog_Authentication.csv"
    files = {
        **_FILES,
        export: _csv(
            "ColumnName,ColumnOrdinal,DataType,ColumnType",
            "DeviceVendor,0,System.String,string",
            "DeviceProduct,1,System.String,string",
        ),
    }

    inventory = _build(tmp_path, FakeUpstream(files))

    assert export not in {sample.file.path for sample in inventory.samples}
    schema = next(item for item in inventory.table_schemas if item.file.path == export)
    assert (schema.table, schema.columns) == ("CommonSecurityLog", 2)
    tailspin = next(p for p in inventory.pairings if p.parser == "ASimAuthenticationTailspinVPN")
    assert tailspin.blockers == ["unsupported-parameters"]


def test_inventory_excludes_samples_whose_cells_violate_declared_types(tmp_path: Path) -> None:
    sample = f"{_SAMPLES}/VPN_Authentication_IngestedLogs.csv"
    export = f"{_SAMPLES}/CommonSecurityLog_Authentication.csv"
    files = {
        **_FILES,
        sample: _csv(
            "Type,DeviceVendor,DeviceProduct,SourceUserName,SentBytes,DeviceCustomNumber1",
            "CommonSecurityLog,Tailspin,Toys VPN,frank,test,7",
            "CommonSecurityLog,Tailspin,Toys VPN,grace,,8",
        ),
        export: _csv(
            "ColumnName,ColumnType",
            "SourceUserName,string",
            "SentBytes,long",
            "DeviceCustomNumber1,int",
        ),
    }

    inventory = _build(tmp_path, FakeUpstream(files))

    record = next(item for item in inventory.samples if item.file.path == sample)
    assert record.type_violations == ["SentBytes"]
    tailspin = next(p for p in inventory.pairings if p.parser == "ASimAuthenticationTailspinVPN")
    assert (tailspin.status, tailspin.blockers) == (
        "excluded",
        ["unsupported-parameters", "type-violations"],
    )
    assert "unsupported-parameters, type-violations (SentBytes)" in render_inventory(inventory)

    # A column is only a violation when every covering schema types it strictly.
    files[f"{_SAMPLES}/CommonSecurityLog_Schema.csv"] = _csv(
        "ColumnName,ColumnType", "SentBytes,string"
    )
    inventory = _build(tmp_path / "lenient", FakeUpstream(files))
    record = next(item for item in inventory.samples if item.file.path == sample)
    assert record.type_violations == []


def test_inventory_rejects_invalid_schema_names(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="ASIM schema names"):
        build_inventory(
            revision=REVISION, cache_dir=tmp_path, schemas=["../etc"], fetch=FakeUpstream({})
        )


def test_report_summarises_pairings_and_escapes_table_cells(tmp_path: Path) -> None:
    inventory = _build(tmp_path)
    data_test = next(t for t in inventory.tester_results if t.kind == "DataTest")
    data_test.errors[0] = "left | right"

    report = render_inventory(inventory)

    assert "| Authentication | 6 | 6 | 9 | 7 | 1 | 1 | 4 | 2 |" in report
    assert "missing-tables (Syslog), column-types" in report
    assert "ready (contoso-gateway-authentication-v1)" in report
    assert "| `fedcba9` | changed |" in report
    assert f"| {_REMOVED_SAMPLE} | `fedcba9` | missing |" in report
    assert "| 1 | 0 | left \\| right |" in report


def test_cli_inventory_writes_json_and_markdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fixtures, corpora = _registries(tmp_path)
    monkeypatch.setattr(azure_sentinel, "fetch_bytes", FakeUpstream(_FILES))
    output = tmp_path / "out"

    main(
        [
            "reference",
            "inventory",
            "--revision",
            REVISION,
            "--schema",
            "Authentication",
            "--cache",
            str(tmp_path / "cache"),
            "--fixtures",
            str(fixtures),
            "--corpora",
            str(corpora),
            "--output",
            str(output),
        ]
    )

    printed = capsys.readouterr().out
    assert "7 pairings, 1 ready" in printed
    loaded = AsimInventory.model_validate_json((output / "inventory.json").read_bytes())
    assert loaded.revision == REVISION
    assert (output / "inventory.md").read_text("utf-8").startswith("# ASIM sample and parser")


def test_query_tables_ignores_comments_strings_and_bound_names() -> None:
    lexed = static.lex_query(
        "// CommentTable | where true\n"
        "let helper = (T: (Message: string)) { T | where Message has 'x' };\n"
        'let filtered = RealTable | where Message has @"C:\\path | QuotedTable";\n'
        "union isfuzzy=true filtered, OtherTable, (ThirdTable | where true)\n"
        "| where Message has ```FencedTable | where```\n"
    )

    assert static.query_tables(lexed.code) == ("OtherTable", "RealTable", "ThirdTable")
    assert "c:\\path | quotedtable" in {literal.casefold() for literal in lexed.literals}


def test_table_schema_columns_accepts_both_upstream_layouts() -> None:
    assert static.table_schema_columns(
        b"ColumnName,ColumnOrdinal,DataType,ColumnType\nMessage,0,System.String,string\n"
    ) == {"Message": "string"}
    assert static.table_schema_columns(b"ColumnName,DataType\nMessage,System.String\n") == {
        "Message": "System.String"
    }
    with pytest.raises(ValueError, match="lacks ColumnName"):
        static.table_schema_columns(b"Name,Type\nMessage,string\n")


def test_type_violations_mirror_fixture_cell_parsing() -> None:
    profile = static.profile_csv(
        _csv(
            "Count,Score,Flag,Ratio,Seen [UTC],Note",
            "3,1.5,True,nan,not a date,x",
            ",inf,yes,2,,",
        )
    )
    types = {
        "Count": "long",
        "Score": "real",
        "Flag": "bool",
        "Ratio": "Double",
        "Seen": "datetime",
        "Note": "int",
    }

    assert static.type_violations(profile, [types]) == ["Score", "Flag", "Ratio", "Note"]
    assert static.type_violations(profile, [types, {"Score": "string"}]) == [
        "Flag",
        "Ratio",
        "Note",
    ]
    assert static.type_violations(profile, []) == []


@pytest.mark.parametrize(
    ("path", "schema", "key"),
    [
        ("Okta_Auth0_ASimAuthentication_DataTest.csv", "Authentication", "oktaauth0"),
        ("Microsoft_Event_ASimAuditEvent_DataTest (1).csv", "AuditEvent", "microsoftevent"),
        ("Infoblox_vimAuditEvent_SchemaTester.csv.csv", "AuditEvent", "infoblox"),
    ],
)
def test_tester_key_strips_schema_tool_and_copy_markers(path: str, schema: str, key: str) -> None:
    assert static.tester_key(path, schema) == key
