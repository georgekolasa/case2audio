from case2audio.cleaner import clean_markdown
from case2audio.furniture import strip_margin_furniture
from case2audio.reading_order import TextBlock, order_for_narration, render_markdown


def block(text, page=1, top=600, bottom=580, label="text"):
    return TextBlock(text, label, page, 50, top, 550, bottom, 612, 792)


def test_footer_cannot_swallow_the_next_pages_body_text():
    blocks = [
        block("The framework draws on many", top=110, bottom=90),
        block("Page 5 | Example Case BY SOME AUTHOR†", top=70, bottom=44),
        block("academics. This theory shares elements of value investing.", page=2),
        block("Next section", page=2, top=550, bottom=530, label="section_header"),
    ]
    stripped, count = strip_margin_furniture(blocks)
    main, sidebars = order_for_narration(stripped)
    text = clean_markdown(render_markdown(main, sidebars))
    assert count == 1
    assert "many academics. This theory shares elements of value investing." in text
    assert "Page 5" not in text


def test_split_margin_titles_and_authors_are_removed_but_cover_and_notes_survive():
    blocks = [
        block("BY SOME AUTHOR†"),
        block("A useful explanation near the bottom.", top=80, bottom=60, label="footnote"),
        block("Example Case | Page 1 BY SOME AUTHOR†", top=80, bottom=44),
        block("Example Case | Page 2", page=2, top=80, bottom=65),
        block("BY SOME", page=2, top=51, bottom=44),
        block("AUTHOR†", page=2, top=40, bottom=34),
        block("Real body text near the bottom.", page=2, top=85, bottom=72),
    ]
    kept, count = strip_margin_furniture(blocks)
    assert count == 4
    assert [b.text for b in kept] == [blocks[0].text, blocks[1].text, blocks[-1].text]


def test_repeated_prose_outside_the_margin_is_not_removed():
    blocks = [block("An important repeated qualification.", page=p) for p in [1, 2]]
    assert strip_margin_furniture(blocks) == (blocks, 0)


def test_body_block_crossing_the_margin_is_not_cropped():
    blocks = [block("Real prose ending near the page edge.", top=120, bottom=50)]
    assert strip_margin_furniture(blocks) == (blocks, 0)
