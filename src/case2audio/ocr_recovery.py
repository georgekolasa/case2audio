"""Small image-backed repairs for full-page OCR, never guesses from corrupt PDF text."""

import re


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
