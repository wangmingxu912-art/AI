from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytesseract
from PIL import Image

from .image_ops import BBox


@dataclass(frozen=True)
class OCRSegmentConfig:
    analysis_width: int = 1800
    # Only consider labels near left margin (fraction of width)
    left_margin_frac: float = 0.28
    # Minimum confidence for a word to be used
    min_conf: int = 70
    # Pad around computed question bbox
    pad_x: int = 18
    pad_y: int = 10
    # Prefer printed question numbers: ignore extremely small OCR boxes
    min_word_height: int = 10


_MAIN_LABEL_RE = re.compile(
    r"""(?ix)
    ^
    (?:question\s*)?
    (?:q\s*)?
    (?P<num>\d{1,3})
    (?:\s*[\.\)]\s*)?
    (?:
      \(\s*[a-z]\s*\)
    )?
    $
    """
)

_LINE_START_RE = re.compile(
    r"""(?ix)
    ^
    (?:question\s*)?
    (?:q\s*)?
    (?P<num>\d{1,3})
    (?:\s*[\.\)]\s*)?
    (?P<rest>.*)
    $
    """
)


def _normalize_label(num: str) -> str:
    n = str(int(num))  # strip leading zeros
    return f"Q{n}"


def detect_question_bboxes_by_ocr(page_png: Path, *, cfg: OCRSegmentConfig | None = None) -> list[tuple[str, BBox]]:
    """
    Detect question blocks by finding question labels (1, 2(a), Q3, 4b...) via OCR.
    Then segment the page vertically between successive labels.
    """
    c = cfg or OCRSegmentConfig()

    with Image.open(page_png) as im0:
        gray0 = im0.convert("L")
        ow, oh = gray0.width, gray0.height
        scale = min(1.0, c.analysis_width / float(ow))
        aw, ah = int(ow * scale), int(oh * scale)
        im = gray0.resize((aw, ah))

    # Use word-level OCR boxes.
    # If tesseract is not installed, gracefully disable OCR and let callers fallback.
    try:
        data = pytesseract.image_to_data(im, output_type=pytesseract.Output.DICT, lang="eng", config="--psm 6")
    except Exception as e:
        # pytesseract raises TesseractNotFoundError, but keep this broad to avoid hard failures in prod.
        msg = str(e).lower()
        if "tesseract is not installed" in msg or "tesseractnotfounderror" in msg:
            return []
        return []

    # Build line-level text from OCR words across the whole line.
    # Important: we must include the right-side printed stem text; otherwise the top line becomes just "1".
    words: list[tuple[int, int, int, int, int, int, int]] = []  # block,par,line,x,y,w,h,idx
    n = len(data.get("text", []))
    for i in range(n):
        txt = (data["text"][i] or "").strip()
        if not txt:
            continue
        try:
            conf = int(float(data["conf"][i]))
        except Exception:
            conf = 0
        if conf < c.min_conf:
            continue
        x = int(data["left"][i])
        y = int(data["top"][i])
        w = int(data["width"][i])
        h = int(data["height"][i])
        if h < c.min_word_height:
            continue

        block = int(data.get("block_num", [0] * n)[i] or 0)
        par = int(data.get("par_num", [0] * n)[i] or 0)
        line = int(data.get("line_num", [0] * n)[i] or 0)
        words.append((block, par, line, x, y, w, h, i))

    if not words:
        return []

    # Map word index -> text for quick lookup
    texts = data["text"]

    # group by line
    groups: dict[tuple[int, int, int], list[tuple[int, int, int, int, int]]] = {}
    for block, par, line, x, y, w, h, idx in words:
        key = (block, par, line)
        groups.setdefault(key, []).append((x, y, w, h, idx))

    line_candidates: list[tuple[int, int, str]] = []  # y_top, x_left, label(Qn)
    for key, items in groups.items():
        items.sort(key=lambda t: t[0])
        x0 = min(t[0] for t in items)
        y0 = min(t[1] for t in items)
        # Join line text (entire line)
        line_text = " ".join((texts[t[4]] or "").strip() for t in items).strip()
        if not line_text:
            continue

        # Only consider lines whose FIRST token is near the left margin (question number column).
        # This filters out marks like "[2 marks]" elsewhere on the page.
        first_x = items[0][0]
        if first_x > int(aw * c.left_margin_frac):
            continue

        # Decide if the line *starts* with a main question label (Q1/Q2/Question 3/1./1)).
        # We only create one bbox per main question number (Q1, Q2...), not per (a)(b) subpart.
        m = _LINE_START_RE.match(line_text)
        if not m:
            continue
        num = m.group("num")
        rest = (m.group("rest") or "").strip()
        # Filter out likely "step numbers": if there's no rest and line is basically just a number, ignore.
        # For true questions, the line usually has stem text (like your example) so rest will be non-empty.
        # But OCR might split punctuation; allow if line has multiple tokens even if rest looks empty.
        if not rest and len(items) <= 1:
            continue

        # Validate the label itself (e.g., "1", "Q2", "Question 3")
        # Create a normalized main question id: Q{num}
        label = _normalize_label(num)
        line_candidates.append((y0, x0, label))

    if not line_candidates:
        return []

    # Sort top-to-bottom, then keep a monotonic increasing main-question sequence to avoid picking "steps".
    line_candidates.sort(key=lambda t: (t[0], t[1]))
    filtered_lines: list[tuple[int, int, str]] = []
    last_num = 0
    for y0, x0, label in line_candidates:
        try:
            num = int(label[1:])
        except Exception:
            continue
        if num < last_num:
            # skip out-of-order hits
            continue
        if num == last_num and filtered_lines:
            # duplicates on same question number: keep first occurrence only
            continue
        filtered_lines.append((y0, x0, label))
        last_num = num

    if not filtered_lines:
        return []

    # Convert y-stops into question blocks
    inv = 1.0 / scale if scale > 0 else 1.0
    results: list[tuple[str, BBox]] = []
    for idx, (y, x, label) in enumerate(filtered_lines):
        y0 = max(0, int(y * inv) - c.pad_y)
        y1 = oh if idx == len(filtered_lines) - 1 else max(0, int(filtered_lines[idx + 1][0] * inv) - c.pad_y)
        if y1 <= y0:
            continue
        # full width (safer for now)
        x0 = 0
        x1 = ow
        # pad horizontally a bit (clamped)
        x0 = max(0, x0 - c.pad_x)
        x1 = min(ow, x1 + c.pad_x)
        results.append((label, BBox(x=x0, y=y0, w=max(1, x1 - x0), h=max(1, y1 - y0))))

    # If labels are not strictly increasing (e.g., step numbers inside answers), filter aggressively:
    # keep only the first occurrence of each label.
    seen: set[str] = set()
    filtered: list[tuple[str, BBox]] = []
    for label, bbox in results:
        if label in seen:
            continue
        seen.add(label)
        filtered.append((label, bbox))
    return filtered

