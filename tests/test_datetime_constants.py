"""Datetime constants use one grammar in Python and the mapping review page."""

import json
import re
import shutil
import subprocess
from datetime import datetime
from importlib.resources import files

import pytest

from asim_forge.models import is_datetime_constant

ACCEPTED = [
    "2026-03-01",
    "2024-02-29",
    "2000-02-29",
    "0001-01-01",
    "9999-12-31",
    "2026-03-01T00:00",
    "2026-03-01T12:30:45",
    "2026-03-01T23:59:59.1",
    "2026-03-01T23:59:59.123456",
    "2026-03-01T23:59:59.1234567",
    "2026-03-01T12:00Z",
    "2026-03-01T12:00:00Z",
    "2026-03-01T12:00:00+05:30",
    "2026-03-01T12:00:00.5-23:59",
]

REJECTED = [
    "",
    "not-a-date",
    "2026-02-29",
    "1900-02-29",
    "2026-02-30",
    "2026-04-31",
    "2026-00-10",
    "2026-13-01",
    "2026-01-00",
    "2026-01-32",
    "0000-01-01",
    "2026-3-1",
    "20260301",
    "2026-W09-1",
    "2026-060",
    "2026-03-01T",
    "2026-03-01T12",
    "2026-03-01T1200",
    "2026-03-01 12:00",
    "2026-03-01t12:00",
    "2026-03-01X12:00",
    "2026-03-01T24:00",
    "2026-03-01T23:60",
    "2026-03-01T23:59:60",
    "2026-03-01T12:00:00.",
    "2026-03-01T12:00:00.12345678",
    "2026-03-01T12:00:00,5",
    "2026-03-01T12:00:00z",
    "2026-03-01T12:00:00+05",
    "2026-03-01T12:00:00+0530",
    "2026-03-01T12:00:00+24:00",
    "2026-03-01T12:00:00+05:30:15",
    " 2026-03-01",
    "2026-03-01 ",
    "2026-03-01\n",
    "\uff12\uff10\uff12\uff16-03-01",
    "\u0662\u0660\u0662\u0666-03-01",
]


def _calendar_grid() -> list[str]:
    years = ["0000", "0001", "1900", "2000", "2023", "2024", "9999"]
    return [
        f"{year}-{month:02d}-{day:02d}"
        for year in years
        for month in range(14)
        for day in range(33)
    ]


@pytest.mark.parametrize("value", ACCEPTED)
def test_iso_dates_and_timestamps_are_datetime_constants(value: str) -> None:
    assert is_datetime_constant(value)
    # The grammar narrows, never widens, what Python itself can parse.
    datetime.fromisoformat(value)


@pytest.mark.parametrize("value", REJECTED)
def test_ambiguous_or_impossible_values_are_not_datetime_constants(value: str) -> None:
    assert not is_datetime_constant(value)


def test_calendar_grid_matches_python_date_validation() -> None:
    for value in _calendar_grid():
        try:
            datetime.fromisoformat(value)
        except ValueError:
            expected = False
        else:
            expected = True
        assert is_datetime_constant(value) is expected, value


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_review_page_accepts_the_same_datetime_constants() -> None:
    page = files("asim_forge").joinpath("mapping_review.html").read_text(encoding="utf-8")
    function = re.search(r"    function isDateTimeConstant\(value\) \{\n.*?\n    \}\n", page, re.S)
    assert function is not None
    values = ACCEPTED + REJECTED + _calendar_grid()
    script = (
        function.group(0)
        + "const values = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n"
        + "process.stdout.write(JSON.stringify(values.map(isDateTimeConstant)));\n"
    )
    node = shutil.which("node")
    assert node is not None
    result = subprocess.run(
        [node, "-e", script],
        input=json.dumps(values),
        capture_output=True,
        check=True,
        encoding="utf-8",
    )
    browser = json.loads(result.stdout)
    mismatches = [
        value
        for value, accepted in zip(values, browser, strict=True)
        if accepted is not is_datetime_constant(value)
    ]
    assert mismatches == []
