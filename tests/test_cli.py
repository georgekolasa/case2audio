import pytest

from case2audio.cli import _enforce_quality, build_parser
from case2audio.errors import Case2AudioError
from case2audio.quality import ExtractionSignals, assess_narration


def test_extract_defaults_are_narration_friendly() -> None:
    args = build_parser().parse_args(["extract", "case.pdf"])

    assert args.table_mode == "smart"
    assert args.no_ocr is False


def test_polly_defaults_to_requested_generative_matthew_voice() -> None:
    args = build_parser().parse_args(["speak", "case.txt", "--bucket", "example"])

    assert args.engine == "generative"
    assert args.voice == "Matthew"


def test_pre_polly_gate_rejects_blocking_quality_findings() -> None:
    report = assess_narration(
        "Page 9 | Example Case\n",
        signals=ExtractionSignals(),
    )

    with pytest.raises(Case2AudioError, match="no synthesis task submitted"):
        _enforce_quality(report)
