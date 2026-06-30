"""Generate PDF versions of every Markdown sample policy in this folder.

Pure-Python implementation using markdown + fpdf2. No system libraries
required, so this runs anywhere Python runs (no pango / glib like
WeasyPrint, no Chromium like puppeteer).

Usage:
    uv run --with markdown --with fpdf2 python s3-data/mock-policies/build_pdfs.py

Or via the wrapper: ./s3-data/mock-policies/build-pdfs.sh
"""

from __future__ import annotations

import re
from pathlib import Path

import markdown
from fpdf import FPDF
from fpdf.enums import XPos, YPos


HERE = Path(__file__).resolve().parent


# --- Styling --------------------------------------------------------------
PAGE_FORMAT = "Letter"
MARGIN = 15  # mm

H1_SIZE = 18
H2_SIZE = 13
H3_SIZE = 11
BODY_SIZE = 10
CELL_SIZE = 9.5

ACCENT = (99, 102, 241)  # indigo
HEADING_COLOR = (15, 23, 42)
SUBHEADING_COLOR = (30, 58, 138)
TABLE_HEADER_BG = (238, 242, 255)
TABLE_BORDER = (203, 213, 225)
TEXT_COLOR = (31, 41, 55)
NOTICE_BG = (254, 243, 199)
NOTICE_BORDER = (245, 158, 11)
NOTICE_TEXT = (146, 64, 14)


# --- Markdown helpers -----------------------------------------------------
def _strip_inline(text: str) -> str:
    """Strip inline markdown so fpdf renders plain text. Also down-mapping
    a small set of common Unicode punctuation to Latin-1 equivalents,
    because the default fpdf2 Helvetica font is Latin-1 only and bundling
    a Unicode TTF inflates every PDF by ~500 KB. The LLM extractor reads
    these PDFs as images-of-text via Claude vision, so the visual fidelity
    matters more than character-perfect Unicode.
    """
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    replacements = {
        "—": "-",
        "–": "-",
        "•": "*",
        "→": "->",
        "←": "<-",
        "✅": "[done]",
        "❌": "[x]",
        "📄": "",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "…": "...",
        "©": "(c)",
        "®": "(R)",
        "™": "(TM)",
        "≥": ">=",
        "≤": "<=",
        "×": "x",
    }
    for unicode_char, ascii_replacement in replacements.items():
        text = text.replace(unicode_char, ascii_replacement)
    return text


def _parse_table(lines: list[str], start: int) -> tuple[list[list[str]], int]:
    """Parse a GFM pipe table starting at `lines[start]`.

    Returns (rows_including_header, end_index_exclusive).
    """
    rows: list[list[str]] = []
    i = start
    while i < len(lines):
        line = lines[i].rstrip()
        if not line.startswith("|"):
            break
        # Skip the separator line (---|---|---).
        if re.match(r"^\|[\s\-:|]+\|$", line):
            i += 1
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        rows.append([_strip_inline(c) for c in cells])
        i += 1
    return rows, i


# --- PDF rendering --------------------------------------------------------
class PolicyPDF(FPDF):
    def __init__(self):
        super().__init__(orientation="P", unit="mm", format=PAGE_FORMAT)
        self.set_margins(MARGIN, MARGIN, MARGIN)
        self.set_auto_page_break(auto=True, margin=MARGIN + 5)
        self.set_text_color(*TEXT_COLOR)

    def render(self, md_text: str) -> None:
        self.add_page()
        lines = md_text.splitlines()
        i = 0
        in_codeblock = False
        codeblock_lines: list[str] = []

        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            # ---- fenced code block ----
            if stripped.startswith("```"):
                if in_codeblock:
                    self._render_codeblock(codeblock_lines)
                    codeblock_lines = []
                    in_codeblock = False
                else:
                    in_codeblock = True
                i += 1
                continue
            if in_codeblock:
                codeblock_lines.append(line)
                i += 1
                continue

            # ---- horizontal rule ----
            if re.match(r"^-{3,}$|^_{3,}$", stripped):
                self.ln(2)
                self.set_draw_color(*TABLE_BORDER)
                self.set_line_width(0.2)
                self.line(MARGIN, self.get_y(), self.w - MARGIN, self.get_y())
                self.ln(3)
                i += 1
                continue

            # ---- table ----
            if stripped.startswith("|") and i + 1 < len(lines) and re.match(
                r"^\|[\s\-:|]+\|$", lines[i + 1].strip()
            ):
                rows, i = _parse_table(lines, i)
                self._render_table(rows)
                continue

            # ---- headings ----
            if stripped.startswith("# "):
                self._render_heading(stripped[2:].strip(), level=1)
                i += 1
                continue
            if stripped.startswith("## "):
                self._render_heading(stripped[3:].strip(), level=2)
                i += 1
                continue
            if stripped.startswith("### "):
                self._render_heading(stripped[4:].strip(), level=3)
                i += 1
                continue

            # ---- blockquote / notice ----
            if stripped.startswith(">"):
                quote_lines: list[str] = []
                while i < len(lines) and lines[i].strip().startswith(">"):
                    quote_lines.append(re.sub(r"^>\s?", "", lines[i].strip()))
                    i += 1
                self._render_quote("\n".join(quote_lines))
                continue

            # ---- bullet list ----
            if re.match(r"^\s*[-*]\s+", line):
                items: list[str] = []
                while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                    items.append(re.sub(r"^\s*[-*]\s+", "", lines[i]))
                    i += 1
                self._render_list(items)
                continue

            # ---- blank ----
            if not stripped:
                self.ln(1.5)
                i += 1
                continue

            # ---- paragraph ----
            self._render_paragraph(stripped)
            i += 1

    def _render_heading(self, text: str, level: int) -> None:
        text = _strip_inline(text)
        self.ln(2 if level > 1 else 1)
        if level == 1:
            self.set_font("Helvetica", "B", H1_SIZE)
            self.set_text_color(*HEADING_COLOR)
            self.cell(
                0,
                9,
                text,
                new_x=XPos.LMARGIN,
                new_y=YPos.NEXT,
            )
            self.set_draw_color(*ACCENT)
            self.set_line_width(0.5)
            y = self.get_y()
            self.line(MARGIN, y, self.w - MARGIN, y)
            self.ln(2)
        elif level == 2:
            self.ln(2)
            self.set_font("Helvetica", "B", H2_SIZE)
            self.set_text_color(*SUBHEADING_COLOR)
            self.cell(
                0, 7, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT
            )
            self.ln(0.5)
        else:
            self.ln(1)
            self.set_font("Helvetica", "B", H3_SIZE)
            self.set_text_color(*SUBHEADING_COLOR)
            self.cell(
                0, 6, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT
            )
        # restore body
        self.set_font("Helvetica", "", BODY_SIZE)
        self.set_text_color(*TEXT_COLOR)

    def _render_paragraph(self, text: str) -> None:
        self.set_font("Helvetica", "", BODY_SIZE)
        self.set_text_color(*TEXT_COLOR)
        self.multi_cell(0, 5, _strip_inline(text))
        self.ln(1)

    def _render_list(self, items: list[str]) -> None:
        self.set_font("Helvetica", "", BODY_SIZE)
        self.set_text_color(*TEXT_COLOR)
        for item in items:
            x = self.l_margin
            self.set_x(x)
            self.cell(4, 5, "*")
            self.set_x(x + 5)
            # Width = page minus left+right margins minus the bullet indent.
            usable = self.w - self.l_margin - self.r_margin - 5
            self.multi_cell(usable, 5, _strip_inline(item))
        self.ln(1)

    def _render_table(self, rows: list[list[str]]) -> None:
        if not rows:
            return
        col_count = max(len(r) for r in rows)
        usable_w = self.w - 2 * MARGIN
        col_w = usable_w / col_count

        self.set_font("Helvetica", "B", CELL_SIZE)
        self.set_fill_color(*TABLE_HEADER_BG)
        self.set_draw_color(*TABLE_BORDER)
        self.set_line_width(0.15)

        # header
        header = rows[0] + [""] * (col_count - len(rows[0]))
        self._render_table_row(header, col_w, fill=True, bold=True)
        # body
        self.set_font("Helvetica", "", CELL_SIZE)
        for row in rows[1:]:
            row = row + [""] * (col_count - len(row))
            self._render_table_row(row, col_w, fill=False, bold=False)
        self.ln(2)

    def _render_table_row(
        self, cells: list[str], col_w: float, fill: bool, bold: bool
    ) -> None:
        # Compute the row height by checking the wrapped height of each cell.
        max_h = 5.0
        wrapped: list[list[str]] = []
        for c in cells:
            text = _strip_inline(c)
            lines = self.multi_cell(
                col_w, 4.5, text, dry_run=True, output="LINES"
            )
            wrapped.append(lines)
            max_h = max(max_h, len(lines) * 4.5)

        x_start = self.get_x()
        y_start = self.get_y()

        # Page break check.
        if y_start + max_h > self.page_break_trigger:
            self.add_page()
            y_start = self.get_y()
            x_start = self.get_x()

        for i, c in enumerate(cells):
            self.set_xy(x_start + i * col_w, y_start)
            if bold and i == 0:
                self.set_font("Helvetica", "B", CELL_SIZE)
            elif bold:
                self.set_font("Helvetica", "B", CELL_SIZE)
            else:
                # Make the first column slightly emphasised (label column).
                if i == 0:
                    self.set_font("Helvetica", "B", CELL_SIZE)
                    self.set_text_color(51, 65, 85)
                else:
                    self.set_font("Helvetica", "", CELL_SIZE)
                    self.set_text_color(*TEXT_COLOR)
            self.multi_cell(
                col_w,
                4.5,
                _strip_inline(c),
                border=1,
                fill=fill,
                max_line_height=4.5,
            )
        self.set_xy(x_start, y_start + max_h)

    def _render_quote(self, text: str) -> None:
        self.ln(1)
        self.set_fill_color(*NOTICE_BG)
        self.set_draw_color(*NOTICE_BORDER)
        self.set_line_width(0.5)
        self.set_text_color(*NOTICE_TEXT)
        self.set_font("Helvetica", "I", 9)
        # Coloured left border.
        x = self.get_x()
        y = self.get_y()
        self.rect(x, y, 1.2, 12, "F")  # accent bar (rough; resized after)
        self.set_x(x + 4)
        # Print the text with a fill so it looks like a callout box.
        height_before = self.get_y()
        self.multi_cell(
            self.w - 2 * MARGIN - 4, 4.5, _strip_inline(text), fill=True
        )
        # Redraw the accent bar to match the actual rendered height.
        accent_h = self.get_y() - height_before
        self.set_fill_color(*NOTICE_BORDER)
        self.rect(x, height_before, 1.2, accent_h, "F")
        self.ln(1)
        self.set_text_color(*TEXT_COLOR)
        self.set_font("Helvetica", "", BODY_SIZE)

    def _render_codeblock(self, lines: list[str]) -> None:
        self.ln(1)
        self.set_font("Courier", "", 9)
        self.set_fill_color(241, 245, 249)
        self.set_text_color(*TEXT_COLOR)
        for line in lines:
            self.cell(
                0,
                4.5,
                _strip_inline(line),
                border=0,
                fill=True,
                new_x=XPos.LMARGIN,
                new_y=YPos.NEXT,
            )
        self.set_font("Helvetica", "", BODY_SIZE)
        self.ln(1)


def main() -> int:
    md_files = sorted(HERE.glob("*.md"))
    if not md_files:
        print("No .md files found.")
        return 1
    for md_path in md_files:
        out_path = md_path.with_suffix(".pdf")
        text = md_path.read_text(encoding="utf-8")
        # We don't actually need the markdown library to produce HTML — we
        # parse the relevant subset directly. The import is kept to fail
        # fast if the user runs this without the right deps.
        _ = markdown
        pdf = PolicyPDF()
        pdf.render(text)
        pdf.output(str(out_path))
        size_kb = out_path.stat().st_size / 1024
        print(f"📄 {md_path.name}  →  {out_path.name}  ({size_kb:.1f} KB)")
    print(f"✅ Generated {len(md_files)} PDF(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
