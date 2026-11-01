"""Pinned, verifiable access to the public Azure-Sentinel repository.

Upstream content is addressed by an immutable commit. Directory listings come from
the Git tree API and every file is checked against its Git blob SHA before use, so a
listing pins content without copying it. Verified bytes are stored once in the
shared `<sha256>.blob` corpus-cache layout used by the benchmark runner.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from time import sleep
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

REPOSITORY = "Azure/Azure-Sentinel"
REPOSITORY_URL = f"https://github.com/{REPOSITORY}"
_API = f"https://api.github.com/repos/{REPOSITORY}"
_COMMIT_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
_RETRY_STATUS = {429, 500, 502, 503, 504}

FetchBytes = Callable[[str], bytes]


class UpstreamError(ValueError):
    """Raised when upstream content cannot be resolved or trusted."""


@dataclass(frozen=True)
class TreeEntry:
    path: str
    git_sha: str
    size: int


def validate_revision(revision: str) -> str:
    revision = revision.strip()
    if not revision or any(character.isspace() for character in revision):
        raise UpstreamError("Azure-Sentinel revision cannot be empty or contain whitespace")
    return revision


def resolve_revision(revision: str, fetch: FetchBytes) -> str:
    """Resolve a branch or tag to a full commit SHA; full SHAs need no request."""
    revision = validate_revision(revision)
    if _COMMIT_SHA.fullmatch(revision):
        return revision.lower()
    payload = _json(fetch(f"{_API}/commits/{quote(revision, safe='')}"), revision)
    resolved = payload.get("sha") if isinstance(payload, dict) else None
    if not isinstance(resolved, str) or not _COMMIT_SHA.fullmatch(resolved):
        raise UpstreamError(f"Azure-Sentinel revision {revision!r} did not resolve to a commit SHA")
    return resolved.lower()


def raw_url(revision: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{REPOSITORY}/{revision}/{quote(path)}"


def fetch_bytes(url: str) -> bytes:
    """Retrieve a trusted GitHub URL with bounded retries for transient failures."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "ASIM-Forge"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers)
    for attempt in range(3):
        try:
            with urlopen(request, timeout=60) as response:  # noqa: S310 - fixed trusted hosts
                return bytes(response.read())
        except HTTPError as error:
            if error.code not in _RETRY_STATUS or attempt == 2:
                raise UpstreamError(f"Could not retrieve {url}: {error}") from error
        except OSError as error:
            if attempt == 2:
                raise UpstreamError(f"Could not retrieve {url}: {error}") from error
        sleep(2**attempt)
    raise AssertionError("unreachable")


def git_blob_sha(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode()
    return hashlib.sha1(header + content, usedforsecurity=False).hexdigest()


class UpstreamTree:
    """Blob listings beneath directories of one immutable commit."""

    def __init__(self, revision: str, fetch: FetchBytes) -> None:
        if not _COMMIT_SHA.fullmatch(revision):
            raise UpstreamError("Upstream trees must be read at a full commit SHA")
        self.revision = revision.lower()
        self._fetch = fetch
        self._children: dict[str, list[dict[str, Any]]] = {}

    def files(self, directory: str) -> list[TreeEntry]:
        """Every blob beneath `directory`, or an empty list when it does not exist."""
        tree_sha = self.revision
        for part in directory.split("/"):
            match = next(
                (
                    item
                    for item in self._entries(tree_sha)
                    if item.get("path") == part and item.get("type") == "tree"
                ),
                None,
            )
            if match is None:
                return []
            tree_sha = str(match["sha"])
        payload = _json(self._fetch(f"{_API}/git/trees/{tree_sha}?recursive=1"), directory)
        if not isinstance(payload, dict) or payload.get("truncated") is not False:
            raise UpstreamError(f"Upstream tree listing for {directory!r} is incomplete")
        return sorted(
            (
                TreeEntry(f"{directory}/{item['path']}", str(item["sha"]), int(item["size"]))
                for item in payload.get("tree", [])
                if item.get("type") == "blob"
            ),
            key=lambda entry: entry.path,
        )

    def _entries(self, tree_sha: str) -> list[dict[str, Any]]:
        if tree_sha not in self._children:
            payload = _json(self._fetch(f"{_API}/git/trees/{tree_sha}"), tree_sha)
            if not isinstance(payload, dict) or not isinstance(payload.get("tree"), list):
                raise UpstreamError(f"Unexpected upstream tree response for {tree_sha}")
            self._children[tree_sha] = payload["tree"]
        return self._children[tree_sha]


class BlobCache:
    """Upstream files stored once by SHA-256 and verified against their Git blob SHA."""

    INDEX = "azure-sentinel-git-blobs.json"

    def __init__(self, directory: Path, revision: str, fetch: FetchBytes) -> None:
        self.directory = directory
        self.revision = revision
        self._fetch = fetch
        index = directory / self.INDEX
        loaded: Any = json.loads(index.read_text(encoding="utf-8")) if index.is_file() else {}
        self._index: dict[str, str] = loaded if isinstance(loaded, dict) else {}
        self._changed = False

    def read(self, entry: TreeEntry) -> tuple[bytes, str]:
        """Return verified bytes and their SHA-256, fetching only when not cached."""
        cached = self._index.get(entry.git_sha)
        if cached is not None:
            path = self.directory / f"{cached}.blob"
            if path.is_file():
                content = path.read_bytes()
                if git_blob_sha(content) == entry.git_sha:
                    return content, hashlib.sha256(content).hexdigest()
        content = self._fetch(raw_url(self.revision, entry.path))
        if git_blob_sha(content) != entry.git_sha:
            raise UpstreamError(f"Upstream bytes do not match the pinned Git blob: {entry.path}")
        digest = hashlib.sha256(content).hexdigest()
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / f"{digest}.blob"
        if not target.is_file() or target.read_bytes() != content:
            target.write_bytes(content)
        self._index[entry.git_sha] = digest
        self._changed = True
        return content, digest

    def read_all(
        self, entries: Sequence[TreeEntry], *, workers: int = 8
    ) -> dict[str, tuple[bytes, str]]:
        """Read many entries concurrently; identical blobs are fetched and written once."""
        unique: dict[str, TreeEntry] = {}
        for entry in entries:
            unique.setdefault(entry.git_sha, entry)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = dict(zip(unique, pool.map(self.read, unique.values()), strict=True))
        return {entry.path: results[entry.git_sha] for entry in entries}

    def save(self) -> None:
        if self._changed:
            self.directory.mkdir(parents=True, exist_ok=True)
            (self.directory / self.INDEX).write_text(
                json.dumps(self._index, indent=1, sort_keys=True) + "\n", encoding="utf-8"
            )
            self._changed = False


def _json(content: bytes, subject: str) -> Any:
    try:
        return json.loads(content.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeError) as error:
        raise UpstreamError(f"Invalid upstream response for {subject!r}: {error}") from error
