from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .grader import grade_with_gpt52
from .image_ops import BBox, crop_image, is_blank_image
from .pdf_render import render_pdf_to_png_pages
from .segment import detect_answer_blocks
from .segment_ocr import detect_question_bboxes_by_ocr
from .segment_pdftext import detect_main_question_starts_from_pdf_text
from .schemas import (
    AutoQuestion,
    AutoQuestionsResponse,
    GradeQuestionOut,
    GradeRequest,
    GradeResponse,
    UploadResponse,
    UploadResponsePage,
)
from .storage import ensure_exam_dirs, get_exam_paths, new_exam_paths


PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = Path(os.environ.get("APP_WEB_DIR", str(PROJECT_ROOT / "web"))).resolve()

app = FastAPI(title="AQA A-Level Handwritten PDF Grader")


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    index_path = WEB_DIR / "index.html"
    if not index_path.exists():
        return HTMLResponse("<h1>Missing web/index.html</h1>", status_code=500)
    return HTMLResponse(index_path.read_text(encoding="utf-8"))


app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.post("/api/exams", response_model=UploadResponse)
async def upload_exam(pdf: UploadFile = File(...)) -> UploadResponse:
    if not pdf.filename or not pdf.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a .pdf file")

    paths = new_exam_paths()
    ensure_exam_dirs(paths)

    data = await pdf.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")

    paths.pdf_path.write_bytes(data)

    rendered = render_pdf_to_png_pages(paths.pdf_path, paths.pages_dir, dpi=200)
    pages: list[UploadResponsePage] = []
    for p in rendered:
        pages.append(
            UploadResponsePage(
                page_index=p.page_index,
                width=p.width,
                height=p.height,
                url=f"/api/exams/{paths.exam_id}/pages/{p.page_index}.png",
            )
        )

    return UploadResponse(exam_id=paths.exam_id, pages=pages)


@app.get("/api/exams/{exam_id}/pages/{page_index}.png")
def get_page_png(exam_id: str, page_index: int) -> FileResponse:
    paths = get_exam_paths(exam_id)
    png_path = paths.pages_dir / f"page_{page_index:04d}.png"
    if not png_path.exists():
        raise HTTPException(status_code=404, detail="Page not found")
    return FileResponse(str(png_path), media_type="image/png")


@app.get("/api/exams/{exam_id}/crops/{crop_name}.png")
def get_crop_png(exam_id: str, crop_name: str) -> FileResponse:
    paths = get_exam_paths(exam_id)
    png_path = paths.crops_dir / f"{crop_name}.png"
    if not png_path.exists():
        raise HTTPException(status_code=404, detail="Crop not found")
    return FileResponse(str(png_path), media_type="image/png")


@app.post("/api/exams/{exam_id}/auto_questions", response_model=AutoQuestionsResponse)
async def auto_questions(exam_id: str) -> AutoQuestionsResponse:
    """
    Auto-detect contiguous answer blocks across all pages, assign Q1..Qn in order.
    No manual bbox selection required.
    """
    paths = get_exam_paths(exam_id)
    if not paths.pdf_path.exists():
        raise HTTPException(status_code=404, detail="Exam not found")

    # Make sure pages are rendered (if user calls this directly)
    if not paths.pages_dir.exists() or not any(paths.pages_dir.glob("*.png")):
        render_pdf_to_png_pages(paths.pdf_path, paths.pages_dir, dpi=200)

    questions: list[AutoQuestion] = []
    qn = 1

    # Iterate in page index order
    page_paths = sorted(paths.pages_dir.glob("page_*.png"))

    # Prefer PDF text-layer based segmentation (most reliable for AQA printed papers).
    starts = detect_main_question_starts_from_pdf_text(paths.pdf_path)
    if starts:
        # Build per-question segments between successive starts (may span pages).
        # For now, we crop per-page segments and assign them under the main Q id on the starting page.
        # If a question spans multiple pages, it will still appear as multiple entries (Qn on different pages).
        # This avoids breaking the current single-image question schema.
        by_page: dict[int, list[tuple[int, int]]] = {}  # page -> list of (y0, y1) segments with assigned qnum in parallel list
        # We'll create segments per start and crop until next start (or page end).
        # Materialize page sizes from rendered images.
        page_sizes: dict[int, tuple[int, int, Path]] = {}
        for page_path in page_paths:
            try:
                pi = int(page_path.stem.split("_")[1])
            except Exception:
                continue
            # Read size from filename by opening is expensive; rely on previously rendered response sizes not stored.
            # We'll open once per page when cropping anyway, so just store path.
            page_sizes[pi] = (0, 0, page_path)

        for idx, st in enumerate(starts):
            qid = f"Q{st.num}"
            start_page = st.page_index
            start_y = st.y_px
            # Determine next start (page, y)
            if idx == len(starts) - 1:
                next_page, next_y = None, None
            else:
                nst = starts[idx + 1]
                next_page, next_y = nst.page_index, nst.y_px

            # Crop segments page by page
            p = start_page
            while True:
                page_path = page_sizes.get(p, (0, 0, None))[2]
                if page_path is None:
                    break
                # open to get height/width
                from PIL import Image

                with Image.open(page_path) as im:
                    pw, ph = im.width, im.height
                y0 = start_y if p == start_page else 0
                if next_page is None:
                    y1 = ph
                elif p < next_page:
                    y1 = ph
                else:
                    # same page as next start
                    y1 = max(0, min(ph, int(next_y)))
                if y1 > y0 + 10:
                    bbox = BBox(x=0, y=max(0, y0 - 8), w=pw, h=min(ph, y1 + 8) - max(0, y0 - 8))
                    crop_name = f"auto_{qid}__p{p}"
                    crop_path = paths.crops_dir / f"{crop_name}.png"
                    cw, ch = crop_image(page_path, bbox, crop_path)
                    questions.append(
                        AutoQuestion(
                            id=qid,
                            page_index=p,
                            bbox={"x": bbox.x, "y": bbox.y, "w": bbox.w, "h": bbox.h},
                            cropped_image_url=f"/api/exams/{exam_id}/crops/{crop_name}.png",
                            cropped_width=cw,
                            cropped_height=ch,
                        )
                    )
                if next_page is None or p >= next_page:
                    break
                p += 1

        return AutoQuestionsResponse(exam_id=exam_id, questions=questions)

    for page_path in page_paths:
        # parse page index from filename: page_0000.png
        try:
            page_index = int(page_path.stem.split("_")[1])
        except Exception:
            continue

        # Prefer OCR-based detection of clear question labels.
        labeled = detect_question_bboxes_by_ocr(page_path)
        if labeled:
            for qid, bbox in labeled:
                crop_name = f"auto_{qid}__p{page_index}"
                crop_path = paths.crops_dir / f"{crop_name}.png"
                cw, ch = crop_image(page_path, bbox, crop_path)
                questions.append(
                    AutoQuestion(
                        id=qid,
                        page_index=page_index,
                        bbox={"x": bbox.x, "y": bbox.y, "w": bbox.w, "h": bbox.h},
                        cropped_image_url=f"/api/exams/{exam_id}/crops/{crop_name}.png",
                        cropped_width=cw,
                        cropped_height=ch,
                    )
                )
        else:
            # Fallback: whitespace-based segmentation (best effort).
            bboxes = detect_answer_blocks(page_path)
            for bbox in bboxes:
                qid = f"Q{qn}"
                crop_name = f"auto_{qid}__p{page_index}"
                crop_path = paths.crops_dir / f"{crop_name}.png"
                cw, ch = crop_image(page_path, bbox, crop_path)
                questions.append(
                    AutoQuestion(
                        id=qid,
                        page_index=page_index,
                        bbox={"x": bbox.x, "y": bbox.y, "w": bbox.w, "h": bbox.h},
                        cropped_image_url=f"/api/exams/{exam_id}/crops/{crop_name}.png",
                        cropped_width=cw,
                        cropped_height=ch,
                    )
                )
                qn += 1

    return AutoQuestionsResponse(exam_id=exam_id, questions=questions)


@app.post("/api/exams/{exam_id}/grade", response_model=GradeResponse)
async def grade_exam(exam_id: str, req: GradeRequest) -> GradeResponse:
    if not req.questions:
        raise HTTPException(status_code=400, detail="questions is empty")

    paths = get_exam_paths(exam_id)
    if not paths.pdf_path.exists():
        raise HTTPException(status_code=404, detail="Exam not found")

    out_questions: list[GradeQuestionOut] = []
    total_score = 0.0
    total_max = 0.0
    raw_dump: dict[str, object] = {"questions": []}

    for q in req.questions:
        total_max += float(q.max_marks)

        page_png = paths.pages_dir / f"page_{q.page_index:04d}.png"
        if not page_png.exists():
            raise HTTPException(status_code=400, detail=f"Missing rendered page {q.page_index} for question {q.id}")

        crop_name = f"{q.id}__p{q.page_index}"
        crop_path = paths.crops_dir / f"{crop_name}.png"

        bbox = BBox(x=q.bbox.x, y=q.bbox.y, w=q.bbox.w, h=q.bbox.h)
        cw, ch = crop_image(page_png, bbox, crop_path)

        blank = is_blank_image(crop_path)
        if blank:
            score = 0.0
            feedback = "Blank answer region detected → 0 marks."
            mistakes = []
            extracted_text = ""
            raw_q = {"id": q.id, "blank": True}
        else:
            res = grade_with_gpt52(
                image_path=crop_path,
                question_id=q.id,
                subject=q.subject,
                question_text=q.question_text,
                mark_scheme=q.mark_scheme,
                max_marks=q.max_marks,
            )
            score = float(res.score)
            feedback = res.feedback
            mistakes = res.mistakes
            extracted_text = res.extracted_text
            raw_q = {"id": q.id, "blank": False, "raw": res.raw}

        total_score += score

        out_questions.append(
            GradeQuestionOut(
                id=q.id,
                score=score,
                max_marks=float(q.max_marks),
                is_blank=blank,
                feedback=feedback,
                mistakes=mistakes,
                cropped_image_url=f"/api/exams/{exam_id}/crops/{crop_name}.png",
                cropped_width=cw,
                cropped_height=ch,
                extracted_text=extracted_text,
            )
        )
        raw_dump["questions"].append(raw_q)

    # Persist last grade result for debugging / reproducibility
    (paths.root / "last_grade.json").write_text(json.dumps(raw_dump, ensure_ascii=False, indent=2), encoding="utf-8")

    return GradeResponse(
        exam_id=exam_id,
        total_score=total_score,
        total_max=total_max,
        questions=out_questions,
        raw=raw_dump,
    )

