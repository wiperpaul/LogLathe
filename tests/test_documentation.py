from __future__ import annotations

import re
import subprocess
from pathlib import Path
from urllib.parse import unquote

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ROOT_DOCUMENTS = (
    REPOSITORY_ROOT / "README.md",
    REPOSITORY_ROOT / "ROADMAP.md",
    REPOSITORY_ROOT / "CONTRIBUTING.md",
)
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+[^)]*)?\)")
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")


def _documents() -> list[Path]:
    return [*ROOT_DOCUMENTS, *sorted((REPOSITORY_ROOT / "docs").rglob("*.md"))]


def _link_target(raw_target: str, source: Path) -> tuple[Path, str | None] | None:
    target = raw_target.strip("<>")
    if target.startswith(("http://", "https://", "mailto:")):
        return None
    path_text, separator, fragment = target.partition("#")
    path = source if not path_text else (source.parent / unquote(path_text)).resolve()
    return path, unquote(fragment) if separator else None


def _heading_slug(heading: str) -> str:
    # This covers the repository's heading subset and follows GitHub's basic slug shape.
    without_markup = re.sub(r"[`*_~]", "", heading).casefold()
    without_punctuation = re.sub(r"[^\w\- ]", "", without_markup)
    return without_punctuation.replace(" ", "-")


def _unescaped_pipe_count(line: str) -> int:
    return len(re.findall(r"(?<!\\)\|", line))


def test_relative_documentation_links_resolve_and_are_publishable() -> None:
    failures: list[str] = []
    local_targets: set[Path] = set()

    for source in _documents():
        text = source.read_text(encoding="utf-8")
        for match in MARKDOWN_LINK.finditer(text):
            resolved = _link_target(match.group(1), source)
            if resolved is None:
                continue
            target, _ = resolved
            if not target.exists():
                failures.append(f"{source.relative_to(REPOSITORY_ROOT)} -> {match.group(1)}")
                continue
            local_targets.add(target)

    relative_targets = sorted(
        str(target.relative_to(REPOSITORY_ROOT)).replace("\\", "/")
        for target in local_targets
        if target.is_relative_to(REPOSITORY_ROOT)
    )
    ignored = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        cwd=REPOSITORY_ROOT,
        input="\n".join(relative_targets),
        capture_output=True,
        text=True,
        check=False,
    ).stdout.splitlines()

    assert not failures, "Broken relative Markdown links:\n" + "\n".join(failures)
    assert not ignored, "Documentation links to ignored files:\n" + "\n".join(ignored)


def test_documentation_fragments_match_headings() -> None:
    failures: list[str] = []

    for source in _documents():
        text = source.read_text(encoding="utf-8")
        for match in MARKDOWN_LINK.finditer(text):
            resolved = _link_target(match.group(1), source)
            if resolved is None:
                continue
            target, fragment = resolved
            if not fragment or target.suffix.casefold() != ".md" or not target.exists():
                continue
            headings = {
                _heading_slug(heading)
                for heading in HEADING.findall(target.read_text(encoding="utf-8"))
            }
            if fragment.casefold() not in headings:
                failures.append(f"{source.relative_to(REPOSITORY_ROOT)} -> {match.group(1)}")

    assert not failures, "Markdown fragments without matching headings:\n" + "\n".join(failures)


def test_documentation_code_fences_are_balanced() -> None:
    failures = []
    for source in _documents():
        fence_count = sum(
            line.lstrip().startswith("```")
            for line in source.read_text(encoding="utf-8").splitlines()
        )
        if fence_count % 2:
            failures.append(str(source.relative_to(REPOSITORY_ROOT)))

    assert not failures, "Unbalanced Markdown code fences:\n" + "\n".join(failures)


def test_documentation_tables_have_consistent_columns() -> None:
    failures: list[str] = []

    for source in _documents():
        lines = source.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if not TABLE_SEPARATOR.fullmatch(line):
                continue
            expected = _unescaped_pipe_count(line)
            table_start = index - 1
            table_end = index + 1
            while table_end < len(lines) and lines[table_end].lstrip().startswith("|"):
                table_end += 1
            for row_index in range(table_start, table_end):
                actual = _unescaped_pipe_count(lines[row_index])
                if actual != expected:
                    failures.append(
                        f"{source.relative_to(REPOSITORY_ROOT)}:{row_index + 1} "
                        f"has {actual - 1} columns; expected {expected - 1}"
                    )

    assert not failures, "Malformed Markdown tables:\n" + "\n".join(failures)
