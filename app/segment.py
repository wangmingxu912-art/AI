from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .image_ops import BBox


@dataclass(frozen=True)
class SegmentConfig:
    # downscale target width for analysis (speed + stability)
    analysis_width: int = 1200
    # a row is considered "ink" if ink_pixels / row_pixels >= this
    ink_row_ratio: float = 0.0035
    # minimum consecutive whitespace rows to split segments
    whitespace_gap_rows: int = 22
    # ignore tiny segments
    min_segment_height_px: int = 80
    min_segment_width_px: int = 140
    # padding around detected bbox (in pixels of original image)
    pad_x: int = 20
    pad_y: int = 14
    # treat "dark" pixels as ink if gray < threshold
    # if None, compute heuristically
    ink_threshold: int | None = None


def _compute_ink_threshold(gray: Image.Image) -> int:
    """
    Heuristic threshold for scanned exam pages:
    background is near-white, ink is darker.
    """
    # sample down a bit for histogram stability
    g = gray.resize((max(200, gray.width // 6), max(200, gray.height // 6)))
    hist = g.histogram()  # 256 bins
    total = sum(hist) or 1
    # find intensity at 10th percentile (captures ink + darker artifacts)
    cum = 0
    p10 = 0
    for i, c in enumerate(hist):
        cum += c
        if cum / total >= 0.10:
            p10 = i
            break
    # find intensity at 90th percentile (background-ish)
    cum = 0
    p90 = 255
    for i, c in enumerate(hist):
        cum += c
        if cum / total >= 0.90:
            p90 = i
            break

    # choose threshold between ink-ish and background-ish
    thr = int((p10 * 0.6) + (p90 * 0.4))
    # clamp to reasonable range
    return max(110, min(215, thr))


def detect_answer_blocks(page_png: Path, *, config: SegmentConfig | None = None) -> list[BBox]:
    """
    Detects contiguous handwritten/printed "answer blocks" by splitting on horizontal whitespace gaps.
    Returns bboxes in ORIGINAL image pixel coordinates.

    Assumption (matches your "连续的"):
    - answers are written one after another, separated by noticeable blank horizontal gaps.
    """
    cfg = config or SegmentConfig()

    with Image.open(page_png) as im0:
        gray0 = im0.convert("L")
        ow, oh = gray0.width, gray0.height

        scale = min(1.0, cfg.analysis_width / float(ow))
        aw = int(ow * scale)
        ah = int(oh * scale)
        gray = gray0.resize((aw, ah))

        thr = cfg.ink_threshold if cfg.ink_threshold is not None else _compute_ink_threshold(gray)

        # Precompute ink mask rows (as ratios)
        # For speed we use getdata row-by-row via crop; Pillow is ok at this scale.
        ink_rows: list[bool] = []
        for y in range(ah):
            row = gray.crop((0, y, aw, y + 1))
            pixels = row.getdata()
            # count "ink" pixels (dark)
            ink = 0
            for p in pixels:
                if p < thr:
                    ink += 1
            ink_rows.append((ink / aw) >= cfg.ink_row_ratio)

        # Find runs of ink rows separated by whitespace gap
        segments_y: list[tuple[int, int]] = []
        y = 0
        while y < ah:
            # skip whitespace
            while y < ah and not ink_rows[y]:
                y += 1
            if y >= ah:
                break
            start = y
            # run until a sufficient whitespace gap is found
            whitespace_run = 0
            y += 1
            while y < ah:
                if ink_rows[y]:
                    whitespace_run = 0
                else:
                    whitespace_run += 1
                    if whitespace_run >= cfg.whitespace_gap_rows:
                        end = y - whitespace_run
                        segments_y.append((start, end))
                        break
                y += 1
            else:
                # ended at page bottom
                segments_y.append((start, ah - 1))

        bboxes: list[BBox] = []
        for (y0, y1) in segments_y:
            if y1 <= y0:
                continue

            # Determine x extents by scanning columns within [y0, y1]
            band = gray.crop((0, y0, aw, y1 + 1))
            # Create per-column ink counts
            col_ink = [0] * aw
            # Iterate pixels; band.getdata returns row-major
            data = list(band.getdata())
            bw = aw
            for idx, p in enumerate(data):
                if p < thr:
                    col_ink[idx % bw] += 1
            # Find first/last column with ink above a tiny threshold
            min_col_ink = max(3, int((y1 - y0 + 1) * 0.01))
            xs = [i for i, c in enumerate(col_ink) if c >= min_col_ink]
            if not xs:
                continue
            x0 = min(xs)
            x1 = max(xs)

            # Map back to original coords and pad
            inv = 1.0 / scale if scale > 0 else 1.0
            ox0 = int(x0 * inv) - cfg.pad_x
            oy0 = int(y0 * inv) - cfg.pad_y
            ox1 = int((x1 + 1) * inv) + cfg.pad_x
            oy1 = int((y1 + 1) * inv) + cfg.pad_y

            ox0 = max(0, min(ox0, ow - 1))
            oy0 = max(0, min(oy0, oh - 1))
            ox1 = max(1, min(ox1, ow))
            oy1 = max(1, min(oy1, oh))
            w = max(1, ox1 - ox0)
            h = max(1, oy1 - oy0)

            if h < cfg.min_segment_height_px or w < cfg.min_segment_width_px:
                continue

            bboxes.append(BBox(x=ox0, y=oy0, w=w, h=h))

        # Sort top-to-bottom (then left-to-right) for stable ordering
        bboxes.sort(key=lambda b: (b.y, b.x))
        return bboxes

