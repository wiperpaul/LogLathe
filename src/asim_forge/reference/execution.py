"""Native Kusto execution and replayable, input-bound result captures."""

from __future__ import annotations

import ipaddress
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .contracts import EventOutput, NativeCapture, QueryOutput, ReferenceEvent, ReferenceFixture
from .fixtures import canonical, digest, event_fingerprint, source_query


class KustoClient:
    """Query an explicitly configured local engine; no cloud credentials or deployment."""

    def __init__(self, endpoint: str, database: str) -> None:
        parsed = urlsplit(endpoint)
        try:
            local = ipaddress.ip_address(parsed.hostname or "").is_loopback
        except ValueError:
            local = parsed.hostname == "localhost"
        if (
            not local
            or parsed.scheme != "http"
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Native reference execution requires an HTTP loopback endpoint")
        if not database.strip():
            raise ValueError("Kusto database must be specified")
        self.endpoint = endpoint.rstrip("/")
        self.database = database

    def _post(self, resource: str, query: str) -> Any:
        data = canonical(
            {
                "db": self.database,
                "csl": query,
                "properties": json.dumps({"Options": {"servertimeout": "00:00:45"}}),
            }
        )
        request = Request(
            self.endpoint + resource,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "x-ms-readonly": "true",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=55) as response:
                return json.loads(response.read())
        except (OSError, ValueError) as error:
            raise ValueError(f"Native Kusto request failed: {error}") from error

    def engine_version(self) -> str:
        payload = self._post("/v1/rest/mgmt", ".show version")
        if not isinstance(payload, dict) or payload.get("error") or not payload.get("Tables"):
            raise ValueError("Kusto did not return engine version evidence")
        return json.dumps(payload["Tables"][0], sort_keys=True)

    def query(self, query: str) -> QueryOutput:
        return parse_response(self._post("/v2/rest/query", query))


def parse_response(payload: Any) -> QueryOutput:
    """Reject partial results, server errors, and ambiguous result sets."""
    if not isinstance(payload, list):
        raise ValueError("Expected a Kusto v2 response frame array")
    if any(not isinstance(frame, dict) for frame in payload):
        raise ValueError("Malformed Kusto response frame")
    for frame in payload:
        if frame.get("HasErrors") or frame.get("Cancelled") or frame.get("error"):
            raise ValueError("Kusto returned an error or incomplete result")
        if (
            frame.get("FrameType") == "DataTable"
            and frame.get("TableKind") == "QueryCompletionInformation"
        ):
            # Completion information includes severity in its Level column.
            names = [column["ColumnName"] for column in frame.get("Columns", [])]
            for values in frame.get("Rows", []):
                row = dict(zip(names, values, strict=True))
                if row.get("Level") in ("Error", "Fatal"):
                    raise ValueError("Kusto query completion reported an error")
    completions = [frame for frame in payload if frame.get("FrameType") == "DataSetCompletion"]
    if (
        len(completions) != 1
        or completions[0].get("HasErrors") is not False
        or completions[0].get("Cancelled") is not False
    ):
        raise ValueError("Kusto result is missing its completion frame")
    tables = [frame for frame in payload if frame.get("TableKind") == "PrimaryResult"]
    if len(tables) != 1:
        raise ValueError("Expected exactly one complete primary Kusto result table")
    table = tables[0]
    if table.get("FrameType") != "DataTable":
        raise ValueError("Progressive Kusto responses are not supported")
    names = [column["ColumnName"] for column in table["Columns"]]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate Kusto output columns")
    columns = {column["ColumnName"]: column["ColumnType"] for column in table["Columns"]}
    return QueryOutput(
        columns=columns,
        rows=[dict(zip(names, values, strict=True)) for values in table["Rows"]],
    )


def capture(
    fixture: ReferenceFixture,
    fixture_hash: str,
    events: list[ReferenceEvent],
    program: str,
    client: KustoClient,
    *,
    kind: Literal["reference", "candidate"],
    runtime_identity: str,
) -> NativeCapture:
    engine = client.engine_version()
    outputs = []
    for event in events:
        query = source_query(fixture, event, program)
        outputs.append(
            EventOutput(
                event_id=event.event_id,
                query_sha256=digest(query.encode()),
                output=client.query(query),
            )
        )
    return NativeCapture(
        fixture_sha256=fixture_hash,
        events_sha256=event_fingerprint(events),
        kind=kind,
        program_sha256=digest(program.encode()),
        engine=engine,
        runtime_identity=runtime_identity,
        captured_at=datetime.now(UTC).isoformat(),
        outputs=outputs,
    )


def verify_capture(
    saved: NativeCapture,
    fixture: ReferenceFixture,
    fixture_hash: str,
    events: list[ReferenceEvent],
    program: str,
    *,
    kind: Literal["reference", "candidate"],
) -> None:
    if (
        saved.kind != kind
        or saved.fixture_sha256 != fixture_hash
        or saved.events_sha256 != event_fingerprint(events)
        or saved.program_sha256 != digest(program.encode())
    ):
        raise ValueError("Capture does not match the exact fixture, events, program, and kind")
    by_id = {item.event_id: item for item in saved.outputs}
    if set(by_id) != {event.event_id for event in events}:
        raise ValueError("Capture must cover every fixture event exactly once")
    for event in events:
        query_hash = digest(source_query(fixture, event, program).encode())
        if by_id[event.event_id].query_sha256 != query_hash:
            raise ValueError(f"Capture query checksum mismatch: {event.event_id}")


def write_capture(path: Path, saved: NativeCapture) -> None:
    content = canonical(saved.model_dump(mode="json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.with_suffix(path.suffix + ".sha256").write_text(digest(content) + "\n", encoding="ascii")


def load_capture(path: Path) -> NativeCapture:
    content = path.read_bytes()
    checksum = path.with_suffix(path.suffix + ".sha256").read_text(encoding="ascii").strip()
    if digest(content) != checksum:
        raise ValueError("Native capture checksum mismatch")
    return NativeCapture.model_validate_json(content)
