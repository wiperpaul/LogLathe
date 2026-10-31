"""Regex fragments shared by raw-event extraction, review spans, and KQL."""

from __future__ import annotations

import re

_REGEX_META = re.compile(r"([\\.^$|?*+()\[\]{}])")


def literal_pattern(value: str) -> str:
    """Match the whitespace normalization used by DeepParse without changing values."""
    escaped = _REGEX_META.sub(r"\\\1", value)
    return re.sub(r"\s+", lambda _: r"\s+", escaped)


def slot_pattern(template: str, match: re.Match[str], *, greedy: bool = False) -> str:
    """Keep separator whitespace outside a capture, including at event boundaries."""
    left = match.start() == 0 or template[match.start() - 1].isspace()
    right = match.end() == len(template) or template[match.end()].isspace()
    middle = ".*" if greedy else ".*?"
    if left and right:
        return rf"\S(?:{middle}\S)?"
    if left:
        return rf"\S{middle}"
    if right:
        return rf"{middle}\S"
    return ".+" if greedy else ".+?"
