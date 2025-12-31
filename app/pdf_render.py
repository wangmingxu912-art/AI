from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF


@dataclass(frozen=True)
class RenderedPage:
    page_index: int
    png_path: Path
    width: int
    height: int


def render_pdf_to_png_pages(
    pdf_path: Path,
    out_dir: Path,
    *,
    dpi: int = 200,
) -> list[RenderedPage]:
    """
    Renders each PDF page into a PNG file. Uses PyMuPDF so we don't require poppler.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(str(pdf_path))
    pages: list[RenderedPage] = []
    # PyMuPDF uses 72 dpi base. Scale factor = dpi/72.
    scale = dpi / 72.0
    matrix = fitz.Matrix(scale, scale)

    for i in range(doc.page_count):
        page = doc.load_page(i)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        png_path = out_dir / f"page_{i:04d}.png"
        pix.save(str(png_path))
        pages.append(
            RenderedPage(
                page_index=i,
                png_path=png_path,
                width=pix.width,
                height=pix.height,
            )
        )

    return pages

