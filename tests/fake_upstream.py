"""An in-memory Azure-Sentinel commit served through the GitHub tree and raw URLs."""

from __future__ import annotations

import hashlib
import json
from urllib.parse import unquote

from asim_forge.azure_sentinel import git_blob_sha

REVISION = "0123456789abcdef0123456789abcdef01234567"
_API = "https://api.github.com/repos/Azure/Azure-Sentinel"
_RAW = "https://raw.githubusercontent.com/Azure/Azure-Sentinel/"


class FakeUpstream:
    def __init__(self, files: dict[str, bytes], revision: str = REVISION) -> None:
        self.files = files
        self.revision = revision
        self.served: dict[str, bytes] = {}
        self.truncated = False
        self.requests: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.requests.append(url)
        if url.startswith(f"{_API}/commits/"):
            return json.dumps({"sha": self.revision}).encode()
        if url.startswith(f"{_API}/git/trees/"):
            tree_sha, _, query = url.removeprefix(f"{_API}/git/trees/").partition("?")
            return self._tree(self._directories()[tree_sha], recursive=query == "recursive=1")
        if url.startswith(f"{_RAW}{self.revision}/"):
            path = unquote(url.removeprefix(f"{_RAW}{self.revision}/"))
            return self.served.get(path, self.files[path])
        raise AssertionError(f"Unexpected request: {url}")

    def raw_requests(self) -> list[str]:
        prefix = f"{_RAW}{self.revision}/"
        return [
            unquote(url.removeprefix(prefix)) for url in self.requests if url.startswith(prefix)
        ]

    def _directories(self) -> dict[str, str]:
        directories = {""}
        for path in self.files:
            parts = path.split("/")[:-1]
            directories.update("/".join(parts[: index + 1]) for index in range(len(parts)))
        return {self._tree_sha(directory): directory for directory in directories}

    def _tree_sha(self, directory: str) -> str:
        if not directory:
            return self.revision
        return hashlib.sha1(directory.encode(), usedforsecurity=False).hexdigest()

    def _tree(self, directory: str, *, recursive: bool) -> bytes:
        prefix = f"{directory}/" if directory else ""
        entries: dict[str, dict[str, object]] = {}
        for path, content in self.files.items():
            if not path.startswith(prefix):
                continue
            relative = path.removeprefix(prefix)
            parts = relative.split("/")
            for depth in range(1, len(parts)):
                if not recursive and depth > 1:
                    break
                name = "/".join(parts[:depth])
                entries[name] = {
                    "path": name,
                    "type": "tree",
                    "sha": self._tree_sha(prefix + name),
                }
            if recursive or len(parts) == 1:
                entries[relative] = {
                    "path": relative,
                    "type": "blob",
                    "sha": git_blob_sha(content),
                    "size": len(content),
                }
        payload: dict[str, object] = {
            "sha": self._tree_sha(directory),
            "tree": list(entries.values()),
        }
        if recursive:
            payload["truncated"] = self.truncated
        return json.dumps(payload).encode()
