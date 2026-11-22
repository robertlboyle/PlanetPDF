from __future__ import annotations

import pytest

from planetpdf.core.pagerange import PageRangeError, format_page_ranges, parse_page_ranges


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1", [0]),
        ("1,5,8-12", [0, 4, 7, 8, 9, 10, 11]),
        (" 1 , 5 , 8 - 12 ", [0, 4, 7, 8, 9, 10, 11]),
        ("12-10", [11, 10, 9]),
        ("18-", [17, 18, 19]),
        ("-3", [0, 1, 2]),
        ("3,1,2", [2, 0, 1]),  # typed order kept
        ("1-3,2,3-4", [0, 1, 2, 3]),  # duplicates dropped
        ("ALL", list(range(20))),
        ("1,,2,", [0, 1]),  # stray commas tolerated
    ],
)
def test_parse(text, expected):
    assert parse_page_ranges(text, 20) == expected


@pytest.mark.parametrize("text", ["", "  ", "0", "21", "1-21", "a", "1-2-3", "-", "5x", ","])
def test_parse_errors(text):
    with pytest.raises(PageRangeError):
        parse_page_ranges(text, 20)


def test_error_message_names_the_page():
    with pytest.raises(PageRangeError, match="Page 25"):
        parse_page_ranges("1,25", 20)


@pytest.mark.parametrize(
    ("indices", "text"),
    [([0], "1"), ([0, 4, 7, 8, 9], "1,5,8-10"), ([3, 2, 1], "2-4"), ([], "")],
)
def test_format(indices, text):
    assert format_page_ranges(indices) == text


def test_format_round_trips():
    indices = [0, 1, 2, 5, 9, 10]
    assert parse_page_ranges(format_page_ranges(indices), 20) == indices
