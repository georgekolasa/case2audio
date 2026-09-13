"""Opt-in checks against locally extracted cases; licensed PDFs stay outside the repo."""

import os
import re
from pathlib import Path

import pytest


@pytest.fixture
def extracted():
    location = os.environ.get("CASE2AUDIO_REGRESSION_DIR")
    if not location:
        pytest.skip("Set CASE2AUDIO_REGRESSION_DIR to freshly extracted case folders")
    return Path(location)


def test_walmart_retains_prose_and_explanations(extracted):
    text = (extracted / "Walmart/narration.txt").read_text()
    assert "organization's resources too thin." in text
    assert "Were low prices and better brands truly compatible?" in text
    assert "by pursuing too many opportunities at once?" in text
    assert "Explanatory note: Retailers controlled both hourly pay" in text
    assert "$18 for Walmart and $26 for Costco." in text
    assert "Explanatory note: Neither Walmart nor Amazon disclosed membership numbers" in text
    assert "sharing a room - and walked instead of taking taxis." in text
    assert "any bigger than the catalog business" in text
    assert "Give me an L!" in text
    assert "plans - by 2024" in text
    assert "landscape - attracted" in text
    assert "delivery - one-hour delivery" in text
    assert not re.search(r"\b(?:plansby|landscapeattracted|merch andise|don' t)\b", text)
    assert not re.search(r"[.!?]['\" ]*\s+(?:7|12|24|28|66)\b", text)
    assert not re.search(r"Source\s*:|Econometrica|Harvard Business Publishing|Stores, Inc.", text)
    for page in (2, 4, 6, 9, 11):
        assert f"Table omitted. See PDF page {page}." not in text
    report = (extracted / "Walmart/debug/quality-report.txt").read_text()
    assert "DAMAGED_SENTENCE" in report  # The source itself is incomplete; do not invent words.
    assert "POSSIBLE_INLINE_CITATIONS" not in report


def test_bb_keeps_useful_case_content(extracted):
    text = (extracted / "bb/narration.txt").read_text()
    assert "A FRAGMENTING MARKET" in text
    assert "I Love New York logo" in text
    assert "inflation-adjusted" in text
    assert "health- and moderation-conscious" in text
    assert "One barrel equals 31 US gallons" in text
    assert "Countries are listed" not in text
    assert "Copyright information" not in text


def test_sfn_keeps_definitions_and_page_continuations(extracted):
    text = (extracted / "sfn/narration.txt").read_text()
    assert "ROIC is measured as net income" in text
    assert "Explanatory note: This model of competition" in text
    assert "price of its products" in text
    assert "other carriers by relying" in text
    assert "Figure omitted. See PDF page 11." in text
    assert "A TAXONOMY OF VALUE-BASED STRATEGIES" in text
    assert not re.search(r"[\ue000-\uf8ff]|Nvidia VIDIA", text)
