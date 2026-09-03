from case2audio.pdf_safety import _coverage, scrub_hidden_text, scrub_hidden_values


def test_coverage_detects_text_fully_under_a_redaction_box() -> None:
    text = (168.0, 566.0, 244.0, 576.0)
    black_box = (167.0, 562.0, 245.0, 580.0)

    assert _coverage(text, black_box) == 1.0
    assert _coverage(text, (300.0, 300.0, 400.0, 400.0)) == 0.0


def test_hidden_text_is_scrubbed_from_narration_and_nested_debug_data() -> None:
    hidden = ("Private Person",)

    assert scrub_hidden_text("Employee: Private   Person", hidden) == "Employee: [redacted]"
    assert scrub_hidden_values(
        {"texts": [{"text": "Employee: Private Person"}]},
        hidden,
    ) == {"texts": [{"text": "Employee: [redacted]"}]}
