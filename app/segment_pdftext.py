from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF


@dataclass(frozen=True)
class PdfTextSegmentConfig:
    # Consider text that starts in left margin as a question label candidate
    left_margin_frac: float = 0.12
    # Ignore footer area (page numbers etc.)
    ignore_bottom_frac: float = 0.10
    # Minimum font size to consider as a main question number
    min_font_size: float = 11.0
    # Render dpi used for page PNGs (must match render_pdf_to_png_pages)
    render_dpi: int = 200


_DIGITS_ONLY = re.compile(r"^\d{1,3}$")
_MAIN_LABEL_LINE = re.compile(r"(?ix)^(?:question\s*)?(?:q\s*)?(?P<num>\d{1,3})\s*$")


@dataclass(frozen=True)
class QuestionStart:
    page_index: int
    num: int
    y_px: int


def detect_main_question_starts_from_pdf_text(
    pdf_path: Path, *, cfg: PdfTextSegmentConfig | None = None
) -> list[QuestionStart]:
    """
    Use PDF text layer (not OCR) to find main question numbers (1,2,3...) in left margin.
    Returns starts ordered top-to-bottom across pages with monotonic increasing numbers.
    """
    c = cfg or PdfTextSegmentConfig()
    scale = c.render_dpi / 72.0

    doc = fitz.open(str(pdf_path))
    starts: list[QuestionStart] = []

    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)
        pr = page.rect
        left_margin_x = pr.width * c.left_margin_frac
        max_y = pr.height * (1.0 - c.ignore_bottom_frac)

        d = page.get_text("dict")
        candidates: list[tuple[float, float, int]] = []  # y0, font_size, num

        for block in d.get("blocks", []) or []:
            for line in block.get("lines", []) or []:
                for span in line.get("spans", []) or []:
                    txt = (span.get("text") or "").strip()
                    if not txt:
                        continue
                    # Main question number is often a standalone "1" in the left column.
                    if not _DIGITS_ONLY.match(txt):
                        continue

                    x0, y0, x1, y1 = span.get("bbox", (0, 0, 0, 0))
                    if x0 > left_margin_x:
                        continue
                    if y0 > max_y:
                        continue
                    size = float(span.get("size") or 0.0)
                    if size < c.min_font_size:
                        continue

                    m = _MAIN_LABEL_LINE.match(txt)
                    if not m:
                        continue
                    num = int(m.group("num"))
                    candidates.append((float(y0), size, num))

        if not candidates:
            continue

        # Keep only first occurrence per num on the page, preferring earlier y and larger font
        candidates.sort(key=lambda t: (t[0], -t[1]))
        seen: set[int] = set()
        for y0, size, num in candidates:
            if num in seen:
                continue
            seen.add(num)
            starts.append(QuestionStart(page_index=page_index, num=num, y_px=int(y0 * scale)))

    # Sort across pages
    starts.sort(key=lambda s: (s.page_index, s.y_px))

    # Keep monotonic increasing sequence, drop duplicates and out-of-order noise
    filtered: list[QuestionStart] = []
    last_num = 0
    for s in starts:
        if s.num < last_num:
            continue
        if s.num == last_num:
            # keep first occurrence only
            continue
        filtered.append(s)
        last_num = s.num

    return filtered

