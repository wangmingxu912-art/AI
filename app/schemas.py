from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class UploadResponsePage(BaseModel):
    page_index: int
    width: int
    height: int
    url: str


class UploadResponse(BaseModel):
    exam_id: str
    pages: list[UploadResponsePage]


class BBoxModel(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(gt=0)
    h: int = Field(gt=0)


class GradeQuestionIn(BaseModel):
    id: str = Field(min_length=1)
    page_index: int = Field(ge=0)
    bbox: BBoxModel
    max_marks: float = Field(gt=0)
    question_text: str = ""
    mark_scheme: str = ""
    subject: str = "AQA A-Level"


class GradeRequest(BaseModel):
    questions: list[GradeQuestionIn] = Field(default_factory=list)


class MistakeBBox(BaseModel):
    x: int
    y: int
    w: int
    h: int


class Mistake(BaseModel):
    bbox: MistakeBBox
    label: str = ""
    severity: Literal["minor", "major"] = "major"


class GradeQuestionOut(BaseModel):
    id: str
    score: float
    max_marks: float
    is_blank: bool
    feedback: str
    mistakes: list[Mistake] = Field(default_factory=list)
    cropped_image_url: str
    cropped_width: int
    cropped_height: int
    extracted_text: str = ""


class GradeResponse(BaseModel):
    exam_id: str
    total_score: float
    total_max: float
    questions: list[GradeQuestionOut]
    raw: dict[str, Any] = Field(default_factory=dict)

