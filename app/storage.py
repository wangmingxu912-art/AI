from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExamPaths:
    exam_id: str
    root: Path
    pdf_path: Path
    pages_dir: Path
    crops_dir: Path


def _project_root() -> Path:
    # /.../app/storage.py -> project root
    return Path(__file__).resolve().parent.parent


def get_data_root() -> Path:
    default_dir = _project_root() / "data"
    return Path(os.environ.get("APP_DATA_DIR", str(default_dir))).resolve()


def new_exam_paths() -> ExamPaths:
    exam_id = uuid.uuid4().hex
    return get_exam_paths(exam_id)


def get_exam_paths(exam_id: str) -> ExamPaths:
    root = (get_data_root() / "exams" / exam_id).resolve()
    pdf_path = root / "input.pdf"
    pages_dir = root / "pages"
    crops_dir = root / "crops"
    return ExamPaths(
        exam_id=exam_id,
        root=root,
        pdf_path=pdf_path,
        pages_dir=pages_dir,
        crops_dir=crops_dir,
    )


def ensure_exam_dirs(paths: ExamPaths) -> None:
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.pages_dir.mkdir(parents=True, exist_ok=True)
    paths.crops_dir.mkdir(parents=True, exist_ok=True)

