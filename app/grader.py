from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import OpenAI


@dataclass(frozen=True)
class GradeResult:
    extracted_text: str
    score: float
    feedback: str
    mistakes: list[dict[str, Any]]
    raw: dict[str, Any]


def _b64_data_url(png_path: Path) -> str:
    data = png_path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _json_loads_lenient(s: str) -> dict[str, Any]:
    s2 = s.strip()
    # Common failure mode: model wraps JSON in code fences.
    if s2.startswith("```"):
        s2 = s2.strip("`")
        # If it contains a json tag line, drop it
        s2 = s2.replace("json\n", "", 1)
    return json.loads(s2)


def grade_with_gpt52(
    *,
    image_path: Path,
    question_id: str,
    subject: str,
    question_text: str,
    mark_scheme: str,
    max_marks: float,
) -> GradeResult:
    """
    Uses GPT-5.2 in high-reasoning mode to:
    1) transcribe handwriting
    2) grade using provided mark scheme / rubric
    3) return mistake bounding boxes on the *cropped* image
    """
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    client = OpenAI(api_key=api_key)

    # Ask for strict JSON so frontend can render.
    prompt = f"""
You are an AQA A-Level exam marker.

You will be given a cropped image of ONE question's student answer (handwritten), plus optional question text and mark scheme.

Rules:
- Score must be between 0 and {max_marks} inclusive.
- If the student wrote nothing / the answer region is blank, set score = 0 and extracted_text = "".
- Be strict and consistent with the mark scheme if provided; otherwise apply a reasonable AQA-style marking approach.
- Return mistake bounding boxes in PIXEL coordinates relative to the provided cropped image:
  origin (0,0) at top-left; x to the right, y downward.
- Each mistake bbox should tightly cover the incorrect part (as best as possible). If you cannot localize a mistake, omit it.
- The response MUST be valid JSON and NOTHING else.

Return JSON with this schema:
{{
  "extracted_text": string,
  "score": number,
  "feedback": string,
  "mistakes": [
    {{
      "bbox": {{"x": int, "y": int, "w": int, "h": int}},
      "label": string,
      "severity": "minor"|"major"
    }}
  ]
}}

Context:
- question_id: {question_id}
- subject: {subject}
- max_marks: {max_marks}
- question_text: {question_text or "(not provided)"}
- mark_scheme: {mark_scheme or "(not provided)"}
""".strip()

    image_url = _b64_data_url(image_path)

    def call_once(extra_instruction: str | None = None) -> dict[str, Any]:
        text = prompt if not extra_instruction else (prompt + "\n\n" + extra_instruction)
        resp = client.responses.create(
            model=os.environ.get("OPENAI_MODEL", "gpt-5.2"),
            reasoning={"effort": os.environ.get("OPENAI_REASONING_EFFORT", "high")},
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": text},
                        {"type": "input_image", "image_url": image_url},
                    ],
                }
            ],
        )
        # Prefer output_text when present
        out_text = getattr(resp, "output_text", None)
        if not out_text:
            # Fallback: concatenate any text parts
            chunks: list[str] = []
            for item in resp.output:
                for c in getattr(item, "content", []) or []:
                    if getattr(c, "type", None) in ("output_text", "text"):
                        chunks.append(getattr(c, "text", "") or "")
            out_text = "\n".join(chunks).strip()
        if not out_text:
            raise RuntimeError("Empty model response")
        return _json_loads_lenient(out_text)

    raw: dict[str, Any]
    try:
        raw = call_once()
    except Exception:
        # One retry with stricter instruction
        raw = call_once("IMPORTANT: Return ONLY raw JSON. No markdown, no backticks, no commentary.")

    extracted_text = str(raw.get("extracted_text", "") or "")
    score = float(raw.get("score", 0) or 0)
    feedback = str(raw.get("feedback", "") or "")
    mistakes = raw.get("mistakes", []) or []
    if not isinstance(mistakes, list):
        mistakes = []

    # Clamp score
    if score < 0:
        score = 0.0
    if score > float(max_marks):
        score = float(max_marks)

    return GradeResult(
        extracted_text=extracted_text,
        score=score,
        feedback=feedback,
        mistakes=mistakes,
        raw=raw,
    )

