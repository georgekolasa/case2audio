from case2audio.cleaner import CleanerOptions, clean_markdown


def test_cleaner_removes_furniture_and_markdown() -> None:
    markdown = """# Useful title

This document is authorized for use only by Example Student.

    The nego-

    tiator had **two** choices and [one source](https://example.com).

3
"""

    result = clean_markdown(markdown)

    assert "authorized" not in result
    assert "Useful title" in result
    assert "negotiator had two choices and one source." in result
    assert result.strip().endswith("source.")


def test_cleaner_skips_tables_by_default() -> None:
    markdown = """Before.

| Metric | Value |
| --- | --- |
| Revenue | 42 |

After.
"""

    assert clean_markdown(markdown) == "Before.\n\nAfter.\n"


def test_cleaner_can_linearize_tables() -> None:
    markdown = """| Metric | Value |
| --- | --- |
| Revenue | 42 |
"""

    result = clean_markdown(markdown, CleanerOptions(table_mode="linearize"))

    assert result == "Metric; Value\nRevenue; 42\n"


def test_cleaner_applies_extra_drop_regex() -> None:
    markdown = "Keep me.\n\nCONFIDENTIAL COURSE COPY\n\nKeep me too."

    result = clean_markdown(
        markdown,
        CleanerOptions(extra_drop_patterns=(r"^confidential course copy$",)),
    )

    assert result == "Keep me.\n\nKeep me too.\n"


def test_drop_cap_fix_does_not_join_normal_heading_words() -> None:
    markdown = """W HEN the case starts, listen closely.

# ARE YOU A PERSPECTIVE TAKER?
"""

    result = clean_markdown(markdown)

    assert "WHEN the case" in result
    assert "ARE YOU A PERSPECTIVE TAKER?" in result


def test_contact_email_is_removed_without_joining_the_next_section() -> None:
    markdown = """The author teaches negotiation. They can be reached at author@example.edu.

# Sidebars
"""

    result = clean_markdown(markdown)

    assert result == "The author teaches negotiation.\n\nSidebars\n"
