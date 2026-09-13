"""Rebuild the committed PDF used by the portable end-to-end extraction test."""

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
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


def draw_fragments(
    canvas: Canvas,
    fragments: list[tuple[str, float, float, float]],
    *,
    y: float,
) -> None:
    """Draw one visual line as separate PDF objects to reproduce parser spacing damage."""

    x = LEFT
    for text, font_size, rise, gap_after in fragments:
        canvas.setFont("Helvetica", font_size)
        canvas.drawString(x, y + rise, text)
        x += stringWidth(text, "Helvetica", font_size) + gap_after


def draw_table(
    canvas: Canvas,
    rows: list[list[str]],
    *,
    x: float,
    top: float,
    column_widths: list[float],
    row_height: float = 20,
) -> None:
    """Draw real cell borders so Docling must classify the table instead of plain prose."""

    width = sum(column_widths)
    height = len(rows) * row_height
    canvas.setLineWidth(0.6)
    canvas.rect(x, top - height, width, height)
    for row_index in range(1, len(rows)):
        y = top - row_index * row_height
        canvas.line(x, y, x + width, y)
    cursor = x
    for column_width in column_widths[:-1]:
        cursor += column_width
        canvas.line(cursor, top, cursor, top - height)
    canvas.setFont("Helvetica", 8)
    for row_index, row in enumerate(rows):
        cursor = x
        for column_index, cell in enumerate(row):
            canvas.drawString(cursor + 4, top - (row_index + 0.7) * row_height, cell)
            cursor += column_widths[column_index]


def fixture_figure() -> ImageReader:
    """Return a simple raster so figure handling is tested without external assets."""

    image = Image.new("RGB", (600, 200), "white")
    drawing = ImageDraw.Draw(image)
    drawing.rectangle((20, 35, 250, 165), fill="#d9e8fb", outline="#3b73b9", width=5)
    drawing.rectangle((350, 35, 580, 165), fill="#dff3e4", outline="#438b52", width=5)
    drawing.line((260, 100, 340, 100), fill="#555555", width=8)
    drawing.polygon([(340, 100), (315, 85), (315, 115)], fill="#555555")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return ImageReader(buffer)


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

    # The first lines look normal but use odd object boundaries seen in troublesome cases.
    # PDFium retains the clean source while Docling adds spaces that cleanup must repair.
    draw_fragments(
        canvas,
        [
            ("The company generated $1.", 11, 0, 0.5),
            ("4", 7, 3, 0.5),
            (" trillion in sales while maintaining a 7", 11, 0, 1.5),
            ("0% margin.", 11, -1, 0),
        ],
        y=HEIGHT - 175,
    )
    draw_fragments(
        canvas,
        [
            ("Factories fa", 11, 0, 1.5),
            ("rther from headquarters used source evidence instead of guessing.", 11, -1, 0),
        ],
        y=HEIGHT - 191,
    )
    y = HEIGHT - 207
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

    canvas.setFont("Helvetica-Bold", 8)
    canvas.drawString(330, 250, "Copyright information")
    canvas.setFont("Helvetica", 7)
    canvas.drawString(330, 237, "© 2026 by Example University. All rights reserved.")
    canvas.drawString(330, 224, "This document is authorized for use only by Test Student.")
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

    canvas.setFont("Helvetica-Bold", 10)
    canvas.drawString(LEFT, 565, "Exhibit 1. Decision Options")
    draw_table(
        canvas,
        [
            ["Decision", "Meaning"],
            ["Expand", "Open two stores"],
            ["Hold", "Keep the current footprint"],
        ],
        x=LEFT,
        top=545,
        column_widths=[140, 290],
    )

    canvas.setFont("Helvetica-Bold", 10)
    canvas.drawString(LEFT, 445, "Exhibit 2. Five-Year Financial Summary")
    draw_table(
        canvas,
        [
            ["Metric", "2022", "2023", "2024", "2025"],
            ["Revenue", "105", "118", "132", "149"],
            ["Costs", "81", "90", "101", "114"],
            ["Margin", "24", "28", "31", "35"],
        ],
        x=LEFT,
        top=425,
        column_widths=[110, 80, 80, 80, 80],
    )

    canvas.setFont("Helvetica-Bold", 10)
    canvas.drawString(LEFT, 315, "Figure 1. Distribution Paths")
    figure_x, figure_y, figure_width, figure_height = LEFT, 105, 430, 185
    canvas.drawImage(
        fixture_figure(),
        figure_x,
        figure_y,
        width=figure_width,
        height=figure_height,
        mask="auto",
    )
    # Separate vector labels mimic the loose text Docling finds inside real diagrams.
    canvas.setFont("Helvetica-Bold", 10)
    canvas.drawCentredString(figure_x + 95, figure_y + 87, "Legacy channel")
    canvas.drawCentredString(figure_x + 335, figure_y + 87, "Future channel")
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
