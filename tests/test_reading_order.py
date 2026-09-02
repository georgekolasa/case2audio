from case2audio.reading_order import TextBlock, order_for_narration, render_markdown


def block(text, *, label="text", page=1, left=50, top=600, right=290, bottom=500):
    return TextBlock(
        text=text,
        label=label,
        page=page,
        left=left,
        top=top,
        right=right,
        bottom=bottom,
        page_width=612,
        page_height=792,
    )


def test_sidebar_is_deferred_and_main_column_continuation_is_joined() -> None:
    blocks = [
        block("A sale based only on price would lead to an", top=620),
        block("THE USEFUL SIDEBAR", label="section_header", top=470, right=220),
        block("Sidebar details.", top=440, right=280),
        block("impasse for both parties.", left=320, right=560, top=620),
    ]

    main, sidebars = order_for_narration(blocks)
    rendered = render_markdown(main, sidebars)

    assert "lead to an impasse" in rendered
    assert rendered.index("impasse") < rendered.index("THE USEFUL SIDEBAR")


def test_embedded_right_column_suffix_is_recovered_from_sidebar() -> None:
    blocks = [
        block("market share against up-and-", top=620),
        block("ARE YOU A PERSPECTIVE TAKER?", label="section_header", top=290, right=250),
        block("Sidebar citation.) - comers gained ground.", top=100, right=280),
        block("The next paragraph.", left=320, right=560, top=620),
    ]

    main, sidebars = order_for_narration(blocks)
    rendered = render_markdown(main, sidebars)

    assert "up-and-comers gained ground." in rendered
    assert "Sidebar citation.)" in rendered
    assert "citation.) - comers" not in rendered


def test_cross_page_continuation_skips_running_continued_header() -> None:
    blocks = [
        block("This idea is the same as", page=1),
        block("Perspective Taking (continued)", label="section_header", page=2),
        block("feeling sympathy.", page=2),
    ]

    main, _ = order_for_narration(blocks)

    assert [item.text for item in main] == ["This idea is the same as feeling sympathy."]
