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

# A TAXONOMY OF STRATEGIES

# A FRAGMENTING MARKET
"""

    result = clean_markdown(markdown)

    assert "WHEN the case" in result
    assert "ARE YOU A PERSPECTIVE TAKER?" in result
    assert "A TAXONOMY OF STRATEGIES" in result
    assert "A FRAGMENTING MARKET" in result
    assert "ATAXONOMY" not in result


def test_contact_email_is_removed_without_joining_the_next_section() -> None:
    markdown = """The author teaches negotiation. They can be reached at author@example.edu.

# Sidebars
"""

    result = clean_markdown(markdown)

    assert result == "The author teaches negotiation.\n\nSidebars\n"


def test_cleaner_removes_misclassified_footer_and_repeated_byline() -> None:
    markdown = """# TechPulse Labs Case

BY MODUPE AKINOLA AND ADAM GALINSKY

This opening paragraph is deliberately longer than one hundred and eighty characters so the
cleaner knows the cover introduction has ended and later repeated furniture is not real prose.

# Page 9 | TechPulse Labs Case

BY MODUPE AKINOLA AND ADAM GALINSKY

Useful exhibit prose.
"""

    result = clean_markdown(markdown)

    assert "Page 9" not in result
    assert result.count("BY MODUPE") == 1
    assert result.endswith("Useful exhibit prose.\n")


def test_cleaner_repairs_high_confidence_pdf_word_damage() -> None:
    markdown = """After becoming roduct lead, she inherited a marketdriven plan.

The end-ofyear review arrived after several deadlines-name a setback.

An up-and-comer considered the underlying-and often benevolent-intentions.

- -Have you made progress?
"""

    result = clean_markdown(markdown)

    assert "product lead" in result
    assert "market-driven" in result
    assert "end-of-year" in result
    assert "deadlines - name" in result
    assert "up-and-comer" in result
    assert "underlying - and often benevolent-intentions" in result
    assert "\n\nHave you made progress?" in result


def test_repairs_known_splits_and_compounds_without_general_word_joining():
    text = clean_markdown(
        "We wou ld expa nd onethird of our front-ofhouse space. "
        "Figures are inflationadjusted; offpremise sales use Valuebased pricing. "
        "Standard and P oor's. They walked in to see us. Firm 1 earned 20% in 2024."
    )
    assert "would expand one-third of our front-of-house" in text
    assert "inflation-adjusted" in text
    assert "off-premise" in text
    assert "Value-based" in text
    assert "Standard and Poor's" in text
    assert "in to see us. Firm 1 earned 20% in 2024." in text


def test_audio_punctuation_and_unambiguous_source_typos_are_repaired():
    text = clean_markdown(
        "Nvidia VIDIA said Apple 's customers ' WTP uses a socalled no - name model . "
        "Profit is price mins cost, and the latestgeneration GPU s out competes rivals. "
        "It was second -largest from 2013 - 2023, beside the I ♥ NY logo."
    )
    assert text == (
        "Nvidia said Apple's customers' WTP uses a so-called no-name model. "
        "Profit is price minus cost, and the latest-generation GPUs outcompetes rivals. "
        "It was second-largest from 2013 to 2023, beside the I Love New York logo.\n"
    )


def test_cover_ids_and_publication_dates_are_not_narrated():
    assert clean_markdown("ID#080407\n\nPUBLISHED ON AUGUST 29, 2024\n\nUseful title") == (
        "Useful title\n"
    )


def test_publication_date_does_not_consume_adjacent_case_prose():
    text = clean_markdown("PUBLISHED ON AUGUST 27, 2024 resources were spread too thin.")
    assert text == "resources were spread too thin.\n"


def test_cheer_and_contractions_keep_their_spoken_words():
    text = clean_markdown(
        "BY ALICE * AND BOB †\n\nGive me an\n\nL\n\n!\n\n"
        "I ' ve said we' ve paid; don ' t break the retailer' s promise."
    )
    assert text == (
        "BY ALICE AND BOB\n\nGive me an L!\n\n"
        "I've said we've paid; don't break the retailer's promise.\n"
    )
