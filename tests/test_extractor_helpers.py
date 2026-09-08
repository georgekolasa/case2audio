from types import SimpleNamespace

from case2audio.cleaner import clean_markdown
from case2audio.extractor import (
    _build_narration_markdown,
    _content_pictures,
    _is_generic_table_title,
    _is_text_table,
    _linearize_table,
    _place_referenced_footnotes,
    _strip_front_matter,
    _strip_low_value_table_notes,
    _strip_visual_labels,
    _table_blocks,
    _table_title,
    _text_blocks_from_item,
    _visual_blocks,
)
from case2audio.inline_refs import RaisedReference
from case2audio.reading_order import TextBlock


def _table(rows):
    grid = [[SimpleNamespace(text=cell) for cell in row] for row in rows]
    return SimpleNamespace(
        data=SimpleNamespace(
            grid=grid,
            num_rows=len(grid),
            num_cols=max(len(row) for row in grid),
        )
    )


def _block(text, label="text", page=1, left=50, top=600, right=550, bottom=500):
    return TextBlock(text, label, page, left, top, right, bottom, 612, 792)


def test_smart_mode_recognizes_and_narrates_compact_text_table() -> None:
    table = _table(
        [
            ["1.", "Understands the firm's goals", "____"],
            ["2.", "Works on priority items", "____"],
        ]
    )

    assert _is_text_table(table)
    assert _linearize_table(table) == (
        "Table contents. 1. Understands the firm's goals. 2. Works on priority items."
    )


def test_dense_table_uses_its_merged_first_cell_as_title() -> None:
    table = _table(
        [
            ["Ten-Year Financial Summary"] * 5,
            ["Sales", "1", "2", "3", "4"],
        ]
    )

    assert not _is_text_table(table)
    assert _table_title(table) == "Ten-Year Financial Summary"
    assert not _is_generic_table_title(_table_title(table))
    assert _is_generic_table_title("Rank")
    assert _is_generic_table_title("AB InBev 2016")
    assert _is_generic_table_title("Business model")
    assert _is_generic_table_title("Supplier")


def test_small_numeric_tables_are_not_misclassified_as_text():
    assert not _is_text_table(
        _table(
            [
                ["Package", "Total %", "On-premise %", "Off-premise %"],
                ["1/2 barrel", "36", "68", "5"],
            ]
        )
    )
    assert not _is_text_table(
        _table(
            [
                ["Rank", "2014", "2025"],
                ["1", "Alpha Brewery", "Beta Brewery"],
            ]
        )
    )


def test_unit_conversion_table_remains_useful_and_speakable():
    table = _table([["Measure", "Equivalent"], ["1 barrel", "31 gallons"]])
    assert _is_text_table(table)
    assert _linearize_table(table) == "Table contents. 1 barrel equals 31 gallons."


def test_multpage_provenance_preserves_text_and_each_pages_geometry():
    def provenance(page, span):
        return SimpleNamespace(
            page_no=page, charspan=span, bbox=SimpleNamespace(l=50, t=600, r=550, b=500)
        )

    document = SimpleNamespace(
        pages={n: SimpleNamespace(size=SimpleNamespace(width=612, height=792)) for n in [1, 2]}
    )
    item = SimpleNamespace(
        text="First page. Next page. 43", prov=[provenance(1, (0, 11)), provenance(2, (12, 25))]
    )
    blocks = _text_blocks_from_item(document, item, "text")
    assert [b.page for b in blocks] == [1, 2]
    assert " ".join(b.text for b in blocks) == item.text
    # An unaccounted-for real word must never vanish because metadata is incomplete.
    item.prov[1].charspan = (17, 25)
    blocks = _text_blocks_from_item(document, item, "text")
    assert len(blocks) == 1
    assert blocks[0].text == item.text


def test_front_matter_stops_at_the_next_page_without_a_heading():
    blocks = [
        _block("Author affiliation", "section_header"),
        _block("Professor of Strategy"),
        _block("Acknowledgments", "section_header"),
        _block("Research assistance from A. Person."),
        _block("Real opening prose.", page=2),
    ]
    kept, removed = _strip_front_matter(blocks)
    assert [block.text for block in kept] == ["Real opening prose."]
    assert removed == 4


def test_cover_metadata_cannot_swallow_page_two_intro():
    def item(text, page, top=600, label="text"):
        return SimpleNamespace(
            text=text,
            content_layer="body",
            label=SimpleNamespace(value=label),
            prov=[
                SimpleNamespace(page_no=page, bbox=SimpleNamespace(l=50, r=550, t=top, b=top - 30))
            ],
        )

    document = SimpleNamespace(
        texts=[
            item("The warning was that too many projects spread the organization's", 1),
            item("PUBLISHED ON AUGUST 27, 2024", 1, top=300),
            item("resources too thin. Were the new brands compatible?", 2),
            item("Humble Beginnings", 2, top=500, label="section_header"),
        ],
        pages={n: SimpleNamespace(size=SimpleNamespace(width=612, height=792)) for n in (1, 2)},
        tables=[],
        pictures=[],
    )
    md, _ = _build_narration_markdown(document, "body", "smart")
    text = clean_markdown(md)
    assert "organization's resources too thin. Were the new brands compatible?" in text
    assert "PUBLISHED" not in text


def test_table_footer_is_dropped_but_body_table_is_kept():
    document = SimpleNamespace(
        pages={1: SimpleNamespace(size=SimpleNamespace(width=612, height=792))}
    )
    footer = _table([["Case title | Page 1"], ["BY ALICE AND BOB"]])
    body = _table([["Year", "2024", "2025"], ["Sales", "10", "20"]])
    for table, top in [(footer, 70), (body, 500)]:
        table.prov = [
            SimpleNamespace(page_no=1, bbox=SimpleNamespace(l=50, r=550, t=top, b=top - 20))
        ]
    document.tables = [footer, body]
    blocks, narrated, omitted = _table_blocks(document, "smart")
    assert len(blocks) == 1
    assert (narrated, omitted) == (0, 1)
    assert blocks[0].top == 500


def test_only_inventory_style_table_notes_are_removed():
    blocks = [
        _block("Note: Lager is Brooklyn Lager. Seasonal beers include several labels."),
        _block("Note: Total sales were 53%, which changes the interpretation."),
    ]
    kept, removed = _strip_low_value_table_notes(blocks)
    assert [block.text for block in kept] == [blocks[1].text]
    assert removed == 1


def test_footnote_moves_beside_its_raised_reference():
    blocks = [
        _block("The model is strict. i A later sentence."),
        _block("Next page prose.", page=2),
        _block("i This model assumes identical firms.", "footnote"),
    ]
    ref = RaisedReference(1, "i", "The model is strict.", 160, 550)
    reordered = _place_referenced_footnotes(blocks, [ref])
    assert [block.text for block in reordered] == [blocks[0].text, blocks[2].text, blocks[1].text]


def test_short_footnote_anchor_does_not_attach_to_an_earlier_page():
    blocks = [
        _block("The first discussion creates value.", page=1),
        _block("A later model creates value. i", page=2),
        _block("i This model assumes identical firms.", "footnote", page=2),
    ]
    ref = RaisedReference(2, "i", "value.", 160, 550)
    reordered = _place_referenced_footnotes(blocks, [ref])
    assert [block.text for block in reordered] == [blocks[0].text, blocks[1].text, blocks[2].text]


def test_small_body_diagram_is_marked_and_its_loose_labels_are_removed():
    bbox = SimpleNamespace(l=190, t=313, r=409, b=211)
    provenance = SimpleNamespace(page_no=1, bbox=bbox)
    picture = SimpleNamespace(prov=[provenance])
    document = SimpleNamespace(
        pictures=[picture],
        pages={1: SimpleNamespace(size=SimpleNamespace(width=612, height=792))},
    )
    pictures = _content_pictures(document)
    assert pictures == [picture]
    blocks = [
        _block("FIGURE 9. STRATEGIES", "caption", top=330, bottom=315),
        _block("Firm", left=220, top=290, right=270, bottom=270),
        _block("", left=250, top=260, right=270, bottom=240),
        _block("Body prose below.", top=180, bottom=150),
    ]
    kept, removed = _strip_visual_labels(blocks, pictures)
    assert [block.text for block in kept] == ["FIGURE 9. STRATEGIES", "Body prose below."]
    assert removed == 2
    notes, count = _visual_blocks(document, pictures)
    assert count == 1
    assert notes[0].text == "Figure omitted. See PDF page 1."


def test_small_margin_logo_is_not_treated_as_content():
    picture = SimpleNamespace(
        prov=[SimpleNamespace(page_no=1, bbox=SimpleNamespace(l=450, t=72, r=527, b=44))]
    )
    document = SimpleNamespace(
        pictures=[picture],
        pages={1: SimpleNamespace(size=SimpleNamespace(width=612, height=792))},
    )
    assert _content_pictures(document) == []
