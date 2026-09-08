from types import SimpleNamespace

from case2audio.inline_refs import (
    RaisedReference,
    WordJoin,
    raised_reference,
    reference_sequence_markers,
    repair_source_word_joins,
    scan_pdf_text_evidence,
    strip_inline_references,
    touching_word_fragments,
)
from case2audio.reading_order import TextBlock


def block(text, page=1):
    return TextBlock(text, "text", page, 50, 650, 550, 500, 612, 792)


def test_only_small_raised_punctuated_tokens_are_candidates():
    previous = ("all under one roof.”", (50, 600, 400, 612))
    assert raised_reference(previous, ("1", (401, 606, 404, 611))) == "1"
    assert raised_reference(previous, ("1", (401, 600, 407, 612))) is None
    assert raised_reference(previous, ("1", (440, 606, 444, 611))) is None
    assert raised_reference(("meters", (50, 600, 400, 612)), ("2", (401, 606, 404, 611))) is None


def test_confirmed_marker_is_removed_but_numbers_and_quotes_survive():
    text = "Firm 1 said 'under one roof.' 1 We earned 20% in 2024. See Exhibit 1."
    ref = RaisedReference(1, "1", "under one roof.”", 400, 600)
    result, count = strip_inline_references(block(text), [ref], {"1"})
    assert count == 1
    assert result == "Firm 1 said 'under one roof.' We earned 20% in 2024. See Exhibit 1."


def test_marker_needs_matching_note_page_and_geometry():
    text = "The claim. 2"
    ref = RaisedReference(1, "2", "The claim.", 400, 600)
    assert strip_inline_references(block(text), [ref], set()) == (text, 0)
    assert strip_inline_references(block(text, page=2), [ref], {"2"}) == (text, 0)
    elsewhere = RaisedReference(1, "2", "The claim.", 400, 400)
    assert strip_inline_references(block(text), [elsewhere], {"2"}) == (text, 0)


def test_mid_sentence_percent_citation_and_roman_marker():
    text = "Less than 20% 1 of firms. No value. i A new thought."
    refs = [
        RaisedReference(1, "1", "Less than 20%", 300, 600),
        RaisedReference(1, "i", "No value.", 400, 600),
    ]
    result, count = strip_inline_references(block(text), refs, {"1", "i"})
    assert count == 2
    assert result == "Less than 20% of firms. No value. A new thought."


def test_digit_prefix_and_normal_numbers_are_not_removed():
    ref = RaisedReference(1, "1", "We earned.", 400, 600)
    for text in ["We earned. 12", "We earned. 1.5 million", "Firm 1 sold 20% in 2024."]:
        assert strip_inline_references(block(text), [ref], {"1"}) == (text, 0)


def test_nested_quotes_and_italic_spacing_still_allow_confirmed_markers():
    previous = ("the idea in Brooklyn.’”", (50, 600, 400, 612))
    assert raised_reference(previous, ("4", (401, 606, 404, 611))) == "4"
    ref = RaisedReference(1, "22", "use Intel Inside.”", 400, 600)
    result, count = strip_inline_references(block("use Intel Inside .' 22"), [ref], {"22"})
    assert count == 1
    assert result == "use Intel Inside .'"


def test_sequence_evidence_needs_several_consecutive_raised_numbers():
    def refs(numbers):
        return [RaisedReference(1, str(n), "A claim.", 400, 600) for n in numbers]

    assert reference_sequence_markers(refs([1, 3])) == set()
    assert reference_sequence_markers(refs([1, 1, 1])) == set()
    assert reference_sequence_markers(refs([1, 2, 3, 5])) == {"1", "2", "3", "5"}


def test_source_join_requires_no_encoded_or_visible_space_and_same_line():
    previous = ("where to expa", (50, 600, 400, 612))
    assert touching_word_fragments(previous, ("nd,", (401, 600, 410, 612))) == ("expa", "nd")
    assert touching_word_fragments(previous, (" nd,", (401, 600, 410, 612))) is None
    assert touching_word_fragments(previous, ("nd,", (405, 600, 410, 612))) is None
    assert touching_word_fragments(previous, ("nd,", (50, 580, 65, 592))) is None


def test_source_join_is_local_and_does_not_change_other_word_boundaries():
    join = WordJoin(1, "expa", "nd", 400, 600)
    text = "We could expa nd here, or walk in to see it."
    repaired, count = repair_source_word_joins(block(text), [join])
    assert repaired == "We could expand here, or walk in to see it."
    assert count == 1
    assert repair_source_word_joins(block(text, page=2), [join]) == (text, 0)


def test_source_join_does_not_pick_the_wrong_occurrence_in_a_paragraph():
    join = WordJoin(1, "in", "to", 400, 600)
    text = "They walked in to see us, then went into the office."
    assert repair_source_word_joins(block(text), [join]) == (text, 0)


def test_pdf_scanner_handles_separate_periods_spaces_and_releases_pages(monkeypatch, tmp_path):
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    closed = []
    # Synthetic objects exercise the real scan loop without distributing a licensed case PDF.
    specs = [
        ("The claim", (50, 600, 100, 612)),
        (".", (101, 600, 102, 602)),
        ("1", (103, 606, 106, 611)),
        ("expa", (50, 580, 100, 592)),
        ("nd", (101, 580, 111, 592)),
        ("in", (50, 560, 100, 572)),
        (" ", (100, 560, 101, 572)),
        ("to", (101, 560, 111, 572)),
    ]
    objects = [
        SimpleNamespace(type=raw.FPDF_PAGEOBJ_TEXT, extract=lambda t=t: t, get_bounds=lambda b=b: b)
        for t, b in specs
    ]
    page = SimpleNamespace(
        get_textpage=lambda: SimpleNamespace(close=lambda: closed.append("text")),
        get_objects=lambda **_: iter(objects),
        close=lambda: closed.append("page"),
    )

    class Document:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            closed.append("document")

        def __len__(self):
            return 1

        def __getitem__(self, index):
            return page

    monkeypatch.setattr(pdfium, "PdfDocument", lambda _: Document())
    evidence = scan_pdf_text_evidence(tmp_path / "synthetic.pdf")
    assert [r.marker for r in evidence.references] == ["1"]
    assert evidence.references[0].anchor == "The claim."
    assert [(j.left_word, j.right_word) for j in evidence.word_joins] == [("expa", "nd")]
    assert closed == ["text", "page", "document"]
