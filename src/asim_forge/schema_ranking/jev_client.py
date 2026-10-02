"""Opt-in transport and request-addressed replay for the Jev experiment."""

from __future__ import annotations

import json
import time
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import ValidationError

from .jev import ENDPOINT, SPEC_VERSION, DecisionSpec, JevRequest, JevResponse, request_hash


class JevError(ValueError):
    """An operational failure, never a semantic Unsupported decision."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Do not forward the bearer token to a redirected endpoint.
        return None


def send_request(request: JevRequest, api_key: str) -> JevResponse:
    """Use the documented REST API with bounded timeout and overload retries."""
    if not api_key.strip():
        raise JevError("Set TYPESAFE_API_KEY in the environment before using --live")
    wire = Request(
        ENDPOINT,
        data=request.model_dump_json().encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    opener = build_opener(_NoRedirect())
    for attempt in range(3):
        try:
            with opener.open(wire, timeout=45) as response:
                body = response.read(1_000_001)
            if len(body) > 1_000_000:
                raise JevError("Jev response exceeded the size limit")
            return JevResponse.model_validate_json(body).validate_for(request)
        except HTTPError as error:
            status = error.code
            error.close()
            if status in (429, 529) and attempt < 2:
                time.sleep(2**attempt)
                continue
            # Provider bodies and exception reprs may echo submitted events or credentials.
            raise JevError(f"Jev request failed with HTTP {status}") from None
        except (URLError, TimeoutError, OSError, HTTPException):
            raise JevError("Jev request failed due to a connection error or timeout") from None
        except (ValidationError, ValueError) as error:
            if isinstance(error, JevError):
                raise
            raise JevError("Jev returned an invalid or mismatched response") from None
    raise AssertionError("Unreachable retry state")


def cached_response(
    request: JevRequest,
    catalogue_revision: str,
    cache_dir: Path,
    *,
    live: bool = False,
    api_key: str = "",
    spec_version: DecisionSpec = SPEC_VERSION,
) -> tuple[JevResponse, bool]:
    """Return (response, cache_hit). Replay never falls through to the network."""
    digest = request_hash(request, catalogue_revision, spec_version=spec_version)
    path = cache_dir / f"{digest}.json"
    if path.exists():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if cached["request_hash"] != digest:
                raise ValueError("Wrong cache identity")
            response = JevResponse.model_validate(cached["response"]).validate_for(request)
            return response, True
        except (ValueError, KeyError, TypeError):
            raise JevError(
                "Invalid Jev cache entry; inspect or remove it before retrying"
            ) from None
    if not live:
        raise JevError("No cached Jev response for this request; replay makes no network calls")
    response = send_request(request, api_key)
    cache_dir.mkdir(parents=True, exist_ok=True)
    # Atomic replacement avoids exposing a partially written response on interruption.
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"request_hash": digest, "response": response.model_dump(mode="json")}) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return response, False
