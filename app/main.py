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
from .schemas import (
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

