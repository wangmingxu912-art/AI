from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageStat


@dataclass(frozen=True)
class BBox:
    x: int
    y: int
    w: int
    h: int

    def clamp(self, *, max_w: int, max_h: int) -> "BBox":
        x = max(0, min(self.x, max_w))
        y = max(0, min(self.y, max_h))
        w = max(1, min(self.w, max_w - x))
        h = max(1, min(self.h, max_h - y))
        return BBox(x=x, y=y, w=w, h=h)

    def to_pil_box(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.x + self.w, self.y + self.h)


def crop_image(page_png: Path, bbox: BBox, out_path: Path) -> tuple[int, int]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(page_png) as im:
        bbox2 = bbox.clamp(max_w=im.width, max_h=im.height)
        cropped = im.crop(bbox2.to_pil_box())
        cropped.save(out_path, format="PNG")
        return cropped.width, cropped.height


def is_blank_image(png_path: Path, *, white_threshold: int = 245, blank_ratio: float = 0.992) -> bool:
    """
    Heuristic blank detector: if almost all pixels are near-white, treat as blank.
    """
    with Image.open(png_path) as im:
        gray = im.convert("L")
        # Downsample for speed and stability.
        gray = gray.resize((max(64, gray.width // 6), max(64, gray.height // 6)))
        stat = ImageStat.Stat(gray)
        # stat.extrema gives (min, max) for L
        # But we want ratio of pixels above threshold.
        pixels = list(gray.getdata())
        if not pixels:
            return True
        whiteish = sum(1 for p in pixels if p >= white_threshold)
        ratio = whiteish / len(pixels)
        # Guard against "all white" scans
        return ratio >= blank_ratio

