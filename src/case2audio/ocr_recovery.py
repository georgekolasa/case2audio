"""Small image-backed repairs for full-page OCR, never guesses from corrupt PDF text."""

import re
from collections import Counter


def polish_ocr_narration(text: str) -> str:
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
