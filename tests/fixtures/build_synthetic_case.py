"""Rebuild the committed PDF used by the portable end-to-end extraction test."""

from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

OUTPUT = Path(__file__).with_name("synthetic-case.pdf")
WIDTH, HEIGHT = letter
LEFT = 72


def draw_lines(canvas: Canvas, lines: list[str], *, y: float, leading: float = 16) -> float:
    """Draw pre-wrapped lines so fixture geometry stays intentional and reviewable."""

    canvas.setFont("Helvetica", 11)
    for line in lines:
        canvas.drawString(LEFT, y, line)
        y -= leading
    return y


def draw_cited_sentence(canvas: Canvas, text: str, marker: str, *, y: float) -> None:
    """Use a real raised marker so citation detection exercises PDF geometry."""

    canvas.setFont("Helvetica", 11)
    canvas.drawString(LEFT, y, text)
    marker_x = LEFT + stringWidth(text, "Helvetica", 11) + 2
    canvas.setFont("Helvetica", 7)
    canvas.drawString(marker_x, y + 5, marker)


def draw_footer(canvas: Canvas, page: int) -> None:
    """Repeat furniture on every page so the extractor must remove it."""

    canvas.setFont("Helvetica", 8)
    canvas.drawString(LEFT, 32, f"Synthetic Retail Case | Page {page}")
    canvas.drawRightString(WIDTH - LEFT, 32, "BY TEST AUTHORS")


def build() -> None:
    # Invariant output avoids timestamp-only binary diffs when the fixture is rebuilt.
    canvas = Canvas(str(OUTPUT), pagesize=letter, invariant=1)
    canvas.setTitle("Synthetic Retail Case")
    canvas.setAuthor("case2audio test suite")

    canvas.setFont("Helvetica-Bold", 10)
    canvas.drawRightString(WIDTH - LEFT, HEIGHT - 72, "THE EXAMPLE CASE SERIES")
    canvas.setFont("Helvetica-Bold", 20)
    canvas.drawString(LEFT, HEIGHT - 110, "Synthetic Retail Case")
    canvas.setFont("Helvetica", 10)
    canvas.drawString(LEFT, HEIGHT - 132, "BY TEST AUTHORS")

    y = draw_lines(
        canvas,
        [
            "The company generated $1.4 trillion in sales while maintaining a 70% margin.",
            "Its managers used source evidence instead of guessing at damaged words or numbers.",
        ],
        y=HEIGHT - 175,
    )
    draw_cited_sentence(canvas, "Customers supported the measured expansion.", "1", y=y - 8)
    draw_cited_sentence(
        canvas,
        "Employees asked management to preserve the original meaning.",
        "2",
        y=y - 32,
    )
    draw_cited_sentence(canvas, "Investors requested a clear explanation.", "3", y=y - 56)
    draw_lines(
        canvas,
        [
            "The committee reviewed the evidence and compared the proposal with",
        ],
        y=90,
    )
    draw_footer(canvas, 1)
    canvas.showPage()

    y = draw_lines(
        canvas,
        [
            "Atlas Partners before approving the plan.",
            "The final recommendation kept useful prose while removing publication furniture.",
            "A listener could therefore understand the decision without seeing the page.",
        ],
        y=HEIGHT - 72,
    )
    canvas.setFont("Helvetica-Bold", 14)
    canvas.drawString(LEFT, y - 18, "Guiding Questions")
    draw_lines(
        canvas,
        [
            "Should the company expand while maintaining its operating discipline?",
            "Which stakeholders should management prioritize?",
        ],
        y=y - 42,
    )
    draw_footer(canvas, 2)
    canvas.showPage()

    canvas.setFont("Helvetica-Bold", 14)
    canvas.drawString(LEFT, HEIGHT - 72, "Endnotes")
    draw_lines(
        canvas,
        [
            "1. Synthetic source describing customer demand.",
            "2. Synthetic source describing employee feedback.",
            "3. Synthetic source describing investor expectations.",
        ],
        y=HEIGHT - 100,
    )
    draw_footer(canvas, 3)
    canvas.save()


if __name__ == "__main__":
    build()
