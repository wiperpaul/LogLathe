"""Load pinned upstream files and make explicit source-table fixtures."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from .contracts import ReferenceEvent, ReferenceFixture


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode()


def load_fixture(path: Path) -> tuple[ReferenceFixture, str]:
    manifest_path = path / "manifest.json"
    fixture = ReferenceFixture.model_validate_json(manifest_path.read_bytes())
    for resource in fixture.resources:
        target = (path / resource.file).resolve()
        if not target.is_relative_to(path.resolve()):
            raise ValueError("Fixture resource escapes its directory")
        if digest(target.read_bytes()) != resource.sha256:
            raise ValueError(f"Fixture resource checksum mismatch: {resource.file}")
    return fixture, digest(canonical(fixture.model_dump(mode="json")))


def source_events(path: Path, fixture: ReferenceFixture) -> list[ReferenceEvent]:
    with (path / fixture.csv_file).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if set(reader.fieldnames or []) != set(fixture.columns):
            raise ValueError("CSV columns differ from the declared source-table shape")
        events = []
        for number, row in enumerate(reader, 1):
            values: dict[str, Any] = {}
            for name, kind in fixture.columns.items():
                value = row[name]
                if value is None:
                    raise ValueError(f"Missing CSV cell at row {number}: {name}")
                if kind == "datetime":
                    values[name] = (
                        datetime.strptime(value, fixture.datetime_format)
                        .replace(tzinfo=UTC)
                        .isoformat()
                    )
                elif kind in ("int", "long"):
                    values[name] = int(value) if value else None
                elif kind == "string":
                    values[name] = value
                else:
                    raise ValueError(f"Unsupported CSV source type: {kind}")
            events.append(
                ReferenceEvent(
                    event_id=f"sample-{number:03}",
                    group_id=fixture.group_id,
                    origin="public-sample",
                    source_row=number,
                    values=values,
                )
            )
    if not events:
        raise ValueError("Reference fixture must contain public sample rows")
    return events


def controlled_variants(seeds: list[ReferenceEvent]) -> list[ReferenceEvent]:
    """Ten explicit probes; expectations come from KQL, not from these mutations."""
    by_id = {event.event_id: event for event in seeds}
    changes = [
        (
            "ipv6",
            1,
            {"SyslogMessage": "Accepted publickey for adminuser from 2001:db8::10 port 52234 ssh2"},
        ),
        ("same-source-observer-ip", 1, {"HostIP": "192.168.1.100"}),
        (
            "unknown-method",
            1,
            {
                "SyslogMessage": "Accepted futuremethod for adminuser "
                "from 192.168.1.100 port 52234 ssh2"
            },
        ),
        ("missing-port", 5, {"SyslogMessage": "Invalid user hacker from 192.168.1.104"}),
        ("empty-user", 5, {"SyslogMessage": "Invalid user  from 192.168.1.104 port 52238"}),
        (
            "invalid-port",
            1,
            {"SyslogMessage": "Accepted publickey for adminuser from 192.168.1.100 port bad ssh2"},
        ),
        ("unrelated-process", 1, {"ProcessName": "example-service"}),
        (
            "preauth-only",
            3,
            {
                "SyslogMessage": "Connection closed by authenticating user adminuser "
                "192.168.1.100 port 52234 [preauth]"
            },
        ),
        (
            "escaped-username",
            2,
            {
                "SyslogMessage": 'Accepted password for domain\\test"user '
                "from 192.168.1.101 port 52235 ssh2"
            },
        ),
        (
            "failure-instead-of-success",
            2,
            {"SyslogMessage": "Failed password for testuser from 192.168.1.101 port 52235 ssh2"},
        ),
    ]
    variants = []
    for name, row, changed in changes:
        parent = by_id[f"sample-{row:03}"]
        variants.append(
            parent.model_copy(
                update={
                    "event_id": f"variant-{name}",
                    "origin": "controlled-variant",
                    "parent_event_id": parent.event_id,
                    "mutation": name,
                    "values": {**parent.values, **changed},
                }
            )
        )
    return variants


def event_fingerprint(events: list[ReferenceEvent]) -> str:
    return digest(canonical([event.model_dump(mode="json") for event in events]))


def reference_program(path: Path, fixture: ReferenceFixture) -> str:
    """Wrap exact upstream bodies as query-local functions; install nothing remotely."""
    parts = []
    for name in fixture.helpers:
        helper = yaml.safe_load((path / name).read_text(encoding="utf-8"))
        parameters = []
        for parameter in helper["FunctionParams"]:
            kind = parameter["Type"].removeprefix("table:")
            parameters.append(f"{parameter['Name']}: {kind}")
        parts.append(
            f"let {helper['EquivalentBuiltInFunction']} = ({', '.join(parameters)}) {{\n"
            + helper["FunctionQuery"].rstrip()
            + "\n};"
        )
    parser = yaml.safe_load((path / fixture.parser_file).read_text(encoding="utf-8"))
    if parser["Normalization"] != {
        "Schema": fixture.schema_name,
        "Version": fixture.schema_version,
    }:
        raise ValueError("Pinned parser normalization metadata does not match the fixture")
    if parser["ParserParams"] != [{"Name": "disabled", "Type": "bool", "Default": False}]:
        raise ValueError("Only the unfiltered disabled-only parser signature is supported")
    parts.append("let disabled = false;\n" + parser["ParserQuery"].rstrip())
    return "\n".join(parts) + "\n"


def source_query(fixture: ReferenceFixture, event: ReferenceEvent, program: str) -> str:
    """One row per query preserves identity even when upstream projects IDs away."""
    fields, cells = [], []
    if set(event.values) != set(fixture.columns):
        raise ValueError("Event columns differ from the fixture source-table shape")
    for name, kind in fixture.columns.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError(f"Unsafe source column identifier: {name}")
        fields.append(f"{name}: {kind}")
        value = event.values[name]
        if value is None:
            cells.append('""' if kind == "string" else f"{kind}(null)")
        elif kind == "string":
            cells.append(json.dumps(value, ensure_ascii=True))
        elif kind == "datetime":
            parsed = datetime.fromisoformat(str(value))
            cells.append(f"datetime({parsed.isoformat()})")
        elif kind in ("int", "long"):
            if type(value) is not int:
                raise ValueError(f"Expected integer source value for {name}")
            cells.append(f"{kind}({value})")
        else:
            raise ValueError(f"Unsupported fixture literal type: {kind}")
    return (
        f"let {fixture.source_table} = datatable({', '.join(fields)})\n"
        f"[{', '.join(cells)}];\n{program}"
    )
