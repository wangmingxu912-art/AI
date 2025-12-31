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
    min_conf: int = 55
    # Pad around computed question bbox
    pad_x: int = 18
    pad_y: int = 10


_LABEL_RE = re.compile(
    r"""
    ^
    (?:Q\s*)?
    (?P<num>\d{1,3})
    (?:
      [\.\)]?
      (?P<part>[a-z])
    )?
    $
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _normalize_label(num: str, part: str | None) -> str:
    n = str(int(num))  # strip leading zeros
    if part:
        return f"Q{n}{part.lower()}"
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

    # Use word-level OCR boxes
    data = pytesseract.image_to_data(im, output_type=pytesseract.Output.DICT, lang="eng", config="--psm 6")

    candidates: list[tuple[int, int, int, int, str]] = []  # x,y,w,h,label
    for i in range(len(data.get("text", []))):
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

        if x > int(aw * c.left_margin_frac):
            continue

        # normalize common punctuation around labels
        txt2 = txt.replace("(", "").replace(")", "").replace(":", "").replace("—", "-").strip()
        m = _LABEL_RE.match(txt2)
        if not m:
            continue
        label = _normalize_label(m.group("num"), m.group("part"))
        candidates.append((x, y, w, h, label))

    if not candidates:
        return []

    # Sort top-to-bottom; dedupe close-by repeated detections
    candidates.sort(key=lambda t: (t[1], t[0]))
    deduped: list[tuple[int, int, int, int, str]] = []
    for x, y, w, h, label in candidates:
        if deduped:
            px, py, pw, ph, pl = deduped[-1]
            if label == pl and abs(y - py) < 18:
                continue
        deduped.append((x, y, w, h, label))

    # Convert y-stops into question blocks
    inv = 1.0 / scale if scale > 0 else 1.0
    results: list[tuple[str, BBox]] = []
    for idx, (x, y, w, h, label) in enumerate(deduped):
        y0 = max(0, int(y * inv) - c.pad_y)
        y1 = oh if idx == len(deduped) - 1 else max(0, int(deduped[idx + 1][1] * inv) - c.pad_y)
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

