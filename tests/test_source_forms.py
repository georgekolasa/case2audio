from case2audio.reading_order import TextBlock
from case2audio.source_forms import punctuation_forms, repair_source_forms


def block(text, page=1):
    return TextBlock(text, "text", page, 50, 650, 550, 500, 612, 792)


def test_source_dashes_and_line_wrap_compounds_are_recovered():
    forms = punctuation_forms(
        "growth plans—by 2024; landscape—attracted buyers; delivery—one-hour delivery; "
        "low\ufffe\r\nmargin; store-by-store; mid-2024",
        1,
    )
    text = (
        "plansby 2024; landscapeattracted buyers; deliveryone-hour delivery; "
        "lowmargin; store-bystore; mid2024"
    )
    result, count = repair_source_forms(block(text), forms)
    assert result == (
        "plans - by 2024; landscape - attracted buyers; delivery - one-hour delivery; "
        "low-margin; store-by-store; mid-2024"
    )
    assert count == 6


def test_source_forms_do_not_guess_other_pages_or_ambiguous_spacing():
    forms = punctuation_forms("low-margin and one-third", 1)
    for text, page in [("lowmargin", 2), ("lowmargin and lowmargin", 1), ("one third", 1)]:
        assert repair_source_forms(block(text, page), forms) == (text, 0)


def test_suspended_compounds_do_not_turn_into_prose_dashes():
    text = "health- and moderation-conscious customers"
    forms = punctuation_forms(text, 1)
    assert repair_source_forms(block(text), forms) == (text, 0)
