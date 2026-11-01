from __future__ import annotations

import io
import json
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

import pytest
from fake_upstream import REVISION, FakeUpstream

from asim_forge import azure_sentinel
from asim_forge.azure_sentinel import (
    BlobCache,
    TreeEntry,
    UpstreamError,
    UpstreamTree,
    fetch_bytes,
    git_blob_sha,
    raw_url,
    resolve_revision,
)

_FILES = {
    "Sample Data/ASIM/a.csv": b"Type\nSyslog\n",
    "Sample Data/ASIM/Nested Dir/b.csv": b"Type\nSyslog\n",
    "Sample Data/Other/c.csv": b"other\n",
    "README.md": b"readme\n",
}


def _entry(path: str) -> TreeEntry:
    content = _FILES[path]
    return TreeEntry(path, git_blob_sha(content), len(content))


def _refuse(url: str) -> bytes:
    raise AssertionError(f"Unexpected request: {url}")


def test_git_blob_sha_matches_git_hash_object() -> None:
    assert git_blob_sha(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_resolve_revision_only_queries_symbolic_refs() -> None:
    upstream = FakeUpstream(_FILES)

    assert resolve_revision(REVISION.upper(), _refuse) == REVISION
    assert resolve_revision("master", upstream) == REVISION
    assert upstream.requests[0].endswith("/commits/master")
    with pytest.raises(UpstreamError, match="did not resolve"):
        resolve_revision("master", lambda url: json.dumps({"sha": "abc"}).encode())


def test_raw_url_quotes_paths_with_spaces() -> None:
    assert raw_url(REVISION, "Sample Data/ASIM/a b.csv").endswith(
        f"/{REVISION}/Sample%20Data/ASIM/a%20b.csv"
    )


def test_tree_lists_nested_blobs_beneath_a_directory() -> None:
    tree = UpstreamTree(REVISION, FakeUpstream(_FILES))

    assert tree.files("Sample Data/ASIM") == [
        _entry("Sample Data/ASIM/Nested Dir/b.csv"),
        _entry("Sample Data/ASIM/a.csv"),
    ]
    assert tree.files("Sample Data/Missing") == []


def test_tree_requires_a_commit_and_a_complete_listing() -> None:
    upstream = FakeUpstream(_FILES)
    upstream.truncated = True

    with pytest.raises(UpstreamError, match="full commit SHA"):
        UpstreamTree("master", upstream)
    with pytest.raises(UpstreamError, match="incomplete"):
        UpstreamTree(REVISION, upstream).files("Sample Data/ASIM")


def test_blob_cache_verifies_stores_and_reuses_upstream_bytes(tmp_path: Path) -> None:
    upstream = FakeUpstream(_FILES)
    entries = [_entry("Sample Data/ASIM/a.csv"), _entry("Sample Data/ASIM/Nested Dir/b.csv")]
    cache = BlobCache(tmp_path, REVISION, upstream)

    contents = cache.read_all(entries)
    cache.save()

    assert upstream.raw_requests() == ["Sample Data/ASIM/a.csv"]
    assert {path: content for path, (content, _) in contents.items()} == {
        "Sample Data/ASIM/a.csv": b"Type\nSyslog\n",
        "Sample Data/ASIM/Nested Dir/b.csv": b"Type\nSyslog\n",
    }
    digest = contents["Sample Data/ASIM/a.csv"][1]
    assert (tmp_path / f"{digest}.blob").read_bytes() == b"Type\nSyslog\n"
    assert BlobCache(tmp_path, REVISION, _refuse).read(entries[0]) == (b"Type\nSyslog\n", digest)


def test_blob_cache_rejects_bytes_that_do_not_match_the_listing(tmp_path: Path) -> None:
    upstream = FakeUpstream(_FILES)
    upstream.served["Sample Data/ASIM/a.csv"] = b"tampered\n"

    with pytest.raises(UpstreamError, match="pinned Git blob"):
        BlobCache(tmp_path, REVISION, upstream).read(_entry("Sample Data/ASIM/a.csv"))
    assert not list(tmp_path.glob("*.blob"))


def test_blob_cache_refetches_a_corrupted_cached_blob(tmp_path: Path) -> None:
    entry = _entry("Sample Data/ASIM/a.csv")
    cache = BlobCache(tmp_path, REVISION, FakeUpstream(_FILES))
    _, digest = cache.read(entry)
    cache.save()
    (tmp_path / f"{digest}.blob").write_bytes(b"corrupted\n")
    upstream = FakeUpstream(_FILES)

    assert BlobCache(tmp_path, REVISION, upstream).read(entry)[0] == b"Type\nSyslog\n"
    assert upstream.raw_requests() == ["Sample Data/ASIM/a.csv"]
    assert (tmp_path / f"{digest}.blob").read_bytes() == b"Type\nSyslog\n"


class _Urlopen:
    def __init__(self, *outcomes: bytes | Exception) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[Request] = []

    def __call__(self, request: Request, timeout: float) -> io.BytesIO:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return io.BytesIO(outcome)


def _http_error(code: int) -> HTTPError:
    return HTTPError("https://example.test", code, "status", Message(), None)


def _patch_network(monkeypatch: pytest.MonkeyPatch, opener: _Urlopen) -> list[float]:
    sleeps: list[float] = []
    monkeypatch.setattr(azure_sentinel, "urlopen", opener)
    monkeypatch.setattr(azure_sentinel, "sleep", sleeps.append)
    return sleeps


def test_fetch_bytes_retries_transient_failures_with_token(monkeypatch: pytest.MonkeyPatch) -> None:
    opener = _Urlopen(_http_error(503), OSError("reset"), b"payload")
    sleeps = _patch_network(monkeypatch, opener)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    assert fetch_bytes("https://api.github.com/x") == b"payload"
    assert sleeps == [1, 2]
    assert {request.get_header("Authorization") for request in opener.requests} == {
        "Bearer test-token"
    }


def test_fetch_bytes_fails_fast_on_permanent_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    opener = _Urlopen(_http_error(404))
    sleeps = _patch_network(monkeypatch, opener)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    with pytest.raises(UpstreamError, match="Could not retrieve"):
        fetch_bytes("https://api.github.com/x")
    assert sleeps == []
    assert opener.requests[0].get_header("Authorization") is None


def test_fetch_bytes_gives_up_after_three_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    opener = _Urlopen(OSError("a"), OSError("b"), OSError("c"))
    sleeps = _patch_network(monkeypatch, opener)

    with pytest.raises(UpstreamError, match="Could not retrieve"):
        fetch_bytes("https://api.github.com/x")
    assert (len(opener.requests), sleeps) == (3, [1, 2])
