"""Small image-backed repairs for full-page OCR, never guesses from corrupt PDF text."""

import re
from collections import Counter


def polish_ocr_narration(text: str, *, citation_markers: frozenset[str] = frozenset()) -> str:
    """Repair conservative OCR forms that are clear from repetition or punctuation."""
    text = re.sub(r"\b(\d{1,3})year\b", r"\1-year", text, flags=re.I)
    text = re.sub(r"\bChamps\s+-\s+Elys[ée]es\b", "Champs-Élysées", text, flags=re.I)
    # Full-page OCR often renders prose dashes inconsistently or glues them to discourse words.
    text = re.sub(r"\s*[–—]\s*", " - ", text)
    text = re.sub(
        r"(?<=[A-Za-z0-9])-\s*(?=(?:say|therefore|an\s+age-old)\b)",
        " - ",
        text,
        flags=re.I,
    )
    # Currency plus M is unambiguously millions and sounds clearer when expanded.
    text = re.sub(r"([€£$])\s*(\d+(?:\.\d+)?)\s*M\b\.?", r"\1\2 million", text)

    # OCR flattens raised footnotes into prose, e.g. "1991.1 The" or "claim).4.".
    # Keep the original sentence mark, but remove only markers attached to a strong boundary.
    # Decimal values such as 1.2 do not match because their period follows a digit.
    def remove_ocr_citation(match: re.Match[str]) -> str:
        anchor = match.group("anchor")
        # OCR can turn the sentence stop before a footnote into a comma ("1995,14.").
        if anchor.endswith("%,"):
            return anchor[:-1]
        if anchor.endswith(","):
            return anchor[:-1] + "."
        return anchor

    citation_end = r"(?=(?:[ \t]+(?=[A-Z\"'])|\n{2,}|$))"
    text = re.sub(
        r"(?P<anchor>\b(?:18|19|20)\d{2}[.,]|(?<!\d\.\d)[.!?][\"')\]]?)"
        rf"[ \t]*\d{{1,3}}[.,;:]?{citation_end}",
        remove_ocr_citation,
        text,
    )
    # A marker directly after a percentage has no separating space, unlike real prose values.
    text = re.sub(
        r"(?P<anchor>%[,]?)\d{1,3}[.,;:]?(?=[ \t]+[A-Za-z])",
        remove_ocr_citation,
        text,
    )
    if citation_markers:
        # A detached citation page confirms this OCR run actually contains footnotes.
        # OCR can insert a normal space before a raised marker after a financial value.
        text = re.sub(
            r"(?P<anchor>\b(?:million|billion|trillion|acres))[ \t]*\d{1,3}[.,;:]?"
            r"(?=[ \t]+[A-Z])",
            remove_ocr_citation,
            text,
            flags=re.I,
        )
        # A comma followed by a known note number and a new sentence is never spoken prose.
        text = re.sub(
            r"(?P<anchor>(?<=[A-Za-z)]),)[ \t]*\d{1,3}[.,;:]?"
            r"(?=[ \t]+[A-Z])",
            remove_ocr_citation,
            text,
        )

        # OCR sometimes reads raised 40 and 60 as 4º and 6°. At a sentence boundary
        # before fresh prose, those are citations, not degrees.
        text = re.sub(
            r"(?P<anchor>[.!?][\"')\]]?)[ \t]*\d{1,2}[º°](?=[ \t]+[A-Z])",
            remove_ocr_citation,
            text,
        )

    # A full stop followed by a question mark is a common OCR rendering of a lost footnote.
    text = re.sub(r"\.\?(?=(?:[ \t]+[A-Z]|\n{2,}|$))", ".", text)

    # Once a trailing marker is removed, OCR's original sentence dot can be duplicated.
    text = re.sub(r"\.{2,}(?=(?:[ \t]+|\n|$))", ".", text)

    # Recover a one-letter-damaged possessive only when the full acronym dominates the document.
    acronyms = Counter(re.findall(r"\b[A-Z]{3,5}\b", text))
    for acronym, count in acronyms.items():
        if count < 15:
            continue
        for shorter in {acronym[:index] + acronym[index + 1 :] for index in range(len(acronym))}:
            pattern = rf"\b{re.escape(shorter)}(['’]s)\b"
            if len(re.findall(pattern, text)) == 1:
                text = re.sub(pattern, lambda match, full=acronym: full + match.group(1), text)

    paragraphs = text.split("\n\n")
    for index, paragraph in enumerate(paragraphs):
        # A long OCR prose block ending in a bare word almost certainly lost its final period.
        if len(paragraph) >= 100 and re.search(r"[A-Za-z0-9)]$", paragraph):
            paragraphs[index] = paragraph + "."
    return "\n\n".join(paragraphs)


def recover_currency_suffixes(document, path) -> int:
    import pypdfium2 as pdfium
    from ocrmac import ocrmac

    repaired = 0
    with pdfium.PdfDocument(path) as pdf:
        for item in document.texts:
            if not item.prov or not re.search(r"[€£$]\s*\d[\d,.]*$", item.text.strip()):
                continue
            prov = item.prov[-1]
            box = prov.bbox
            page = pdf[prov.page_no - 1]
            height = page.get_height()
            # Vision sometimes misses an isolated M. on the next line. Re-read only that
            # small area; accept a literal unit, never infer millions from surrounding prose.
            top = height - box.b + 1
            image = page.render(scale=3).to_pil()
            crop = image.crop(
                (
                    max(0, box.l - 3) * 3,
                    top * 3,
                    min(page.get_width(), box.l + 90) * 3,
                    min(height, top + 22) * 3,
                )
            )
            cells = ocrmac.OCR(crop, language_preference=["en-US"]).recognize()
            if len(cells) != 1:
                continue
            text, confidence, _ = cells[0]
            if confidence >= 0.95 and re.fullmatch(r"[MKB]\.", text.strip()):
                item.text = item.text.rstrip() + " " + text.strip()
                repaired += 1
    return repaired
