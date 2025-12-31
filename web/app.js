/* Minimal UI: upload PDF -> draw bbox -> add questions -> grade -> overlay mistake boxes */

let state = {
  examId: null,
  pages: [], // {page_index, width, height, url}
  questions: [], // {id, page_index, bbox, max_marks, question_text, mark_scheme, subject}
};

const el = (id) => document.getElementById(id);

function setStatus(target, msg, isError = false) {
  const node = el(target);
  node.textContent = msg || "";
  node.style.color = isError ? "#ff6a7a" : "";
}

async function uploadPdf() {
  const f = el("pdfFile").files?.[0];
  if (!f) {
    setStatus("uploadStatus", "请选择一个 PDF 文件", true);
    return;
  }
  setStatus("uploadStatus", "上传中…");

  const fd = new FormData();
  fd.append("pdf", f);
  const resp = await fetch("/api/exams", { method: "POST", body: fd });
  if (!resp.ok) {
    const t = await resp.text();
    setStatus("uploadStatus", `上传失败: ${t}`, true);
    return;
  }
  const data = await resp.json();
  state.examId = data.exam_id;
  state.pages = data.pages;
  state.questions = [];
  renderPages();
  renderQuestions();
  setStatus("uploadStatus", `渲染完成：exam_id=${state.examId}，共 ${state.pages.length} 页。下一步点击“自动识别题目”。`);
}

function renderPages() {
  const root = el("pagesRoot");
  root.innerHTML = "";

  state.pages.forEach((p) => {
    const card = document.createElement("div");
    card.className = "pageCard";

    const header = document.createElement("div");
    header.className = "pageHeader";
    header.innerHTML = `<span>Page ${p.page_index + 1}</span><span class="mono">${p.width}×${p.height}</span>`;

    const wrap = document.createElement("div");
    wrap.className = "canvasWrap";

    const canvas = document.createElement("canvas");
    canvas.dataset.pageIndex = String(p.page_index);
    wrap.appendChild(canvas);

    card.appendChild(header);
    card.appendChild(wrap);
    root.appendChild(card);

    loadPageIntoCanvas(canvas, p);
  });
}

function loadPageIntoCanvas(canvas, page) {
  const img = new Image();
  img.onload = () => {
    // Fit to max width for usability
    const maxW = 820;
    const scale = Math.min(1, maxW / img.width);
    canvas.width = Math.floor(img.width * scale);
    canvas.height = Math.floor(img.height * scale);
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    drawSelection(canvas);
  };
  img.src = page.url;
}

async function autoDetectQuestions() {
  if (!state.examId) {
    setStatus("selInfo", "请先上传 PDF", true);
    return;
  }
  setStatus("selInfo", "自动识别题目中…（按空白行切分连续答案块）");

  const resp = await fetch(`/api/exams/${state.examId}/auto_questions`, { method: "POST" });
  if (!resp.ok) {
    const t = await resp.text();
    setStatus("selInfo", `自动识别失败: ${t}`, true);
    return;
  }
  const data = await resp.json();

  const defaultMax = Number(el("defaultMax").value) || 1;
  const globalQText = el("globalQText").value || "";
  const globalMs = el("globalMs").value || "";

  state.questions = (data.questions || []).map((q) => ({
    id: q.id,
    page_index: q.page_index,
    bbox: q.bbox,
    max_marks: defaultMax,
    question_text: globalQText,
    mark_scheme: globalMs,
    subject: "AQA A-Level",
    _preview: q.cropped_image_url,
  }));

  renderQuestions();
  setStatus("selInfo", `已识别 ${state.questions.length} 题（Q1..）。你可以逐题调整满分/评分标准，然后开始评分。`);
}

function renderQuestions() {
  const root = el("questionsList");
  root.innerHTML = "";
  state.questions.forEach((q, idx) => {
    const div = document.createElement("div");
    div.className = "item";
    const r = q.bbox;
    div.innerHTML = `
      <div class="row space">
        <div><strong>${q.id}</strong> <span class="pill">Page ${q.page_index + 1}</span></div>
        <button class="btn ghost" data-remove="${idx}">移除</button>
      </div>
      <div class="meta mono">bbox x:${r.x} y:${r.y} w:${r.w} h:${r.h}</div>
      <div class="row" style="margin-top:8px">
        <label style="margin:0; flex: 1">
          满分
          <input data-max="${idx}" type="number" step="0.5" min="0" value="${q.max_marks}" />
        </label>
      </div>
      <div class="row" style="margin-top:8px">
        <div class="canvasWrap" style="width: 100%">
          <img src="${q._preview || ""}" alt="preview" style="max-width:100%; display:block" />
        </div>
      </div>
      <label style="margin-top:10px">
        题干（可选）
        <textarea data-qtext="${idx}" rows="3" placeholder="question text...">${(q.question_text || "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")}</textarea>
      </label>
      <label>
        mark scheme（可选）
        <textarea data-ms="${idx}" rows="4" placeholder="mark scheme...">${(q.mark_scheme || "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")}</textarea>
      </label>
    `;
    div.querySelector("button[data-remove]")?.addEventListener("click", () => {
      state.questions.splice(idx, 1);
      renderQuestions();
    });
    div.querySelector(`input[data-max="${idx}"]`)?.addEventListener("input", (e) => {
      const v = Number(e.target.value);
      if (Number.isFinite(v) && v >= 0) state.questions[idx].max_marks = v;
    });
    div.querySelector(`textarea[data-qtext="${idx}"]`)?.addEventListener("input", (e) => {
      state.questions[idx].question_text = e.target.value;
    });
    div.querySelector(`textarea[data-ms="${idx}"]`)?.addEventListener("input", (e) => {
      state.questions[idx].mark_scheme = e.target.value;
    });
    root.appendChild(div);
  });
}

async function grade() {
  if (!state.examId) {
    setStatus("gradeStatus", "请先上传 PDF", true);
    return;
  }
  if (!state.questions.length) {
    setStatus("gradeStatus", "请至少添加一道题目", true);
    return;
  }
  setStatus("gradeStatus", "评分中…（非空白题会调用 GPT-5.2，可能需要一点时间）");
  el("gradeSummary").innerHTML = "";
  el("gradeResults").innerHTML = "";

  const resp = await fetch(`/api/exams/${state.examId}/grade`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ questions: state.questions }),
  });
  if (!resp.ok) {
    const t = await resp.text();
    setStatus("gradeStatus", `评分失败: ${t}`, true);
    return;
  }
  const data = await resp.json();
  setStatus("gradeStatus", "评分完成");
  renderGrade(data);
}

function renderGrade(data) {
  el("gradeSummary").innerHTML = `
    <div class="row">
      <span class="pill ok">总分：${data.total_score} / ${data.total_max}</span>
      <span class="pill">exam_id: <span class="mono">${data.exam_id}</span></span>
    </div>
  `;

  const root = el("gradeResults");
  root.innerHTML = "";

  data.questions.forEach((q) => {
    const card = document.createElement("div");
    card.className = "item";

    const blankPill = q.is_blank
      ? `<span class="pill bad">空白题：0 分</span>`
      : `<span class="pill ok">得分：${q.score} / ${q.max_marks}</span>`;

    const header = document.createElement("div");
    header.className = "row space";
    header.innerHTML = `<div><strong>${q.id}</strong></div><div class="row">${blankPill}</div>`;

    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = q.feedback || "";

    const extracted = document.createElement("div");
    extracted.className = "meta mono";
    extracted.textContent = q.extracted_text ? `extracted_text: ${q.extracted_text}` : "";

    const wrap = document.createElement("div");
    wrap.className = "canvasWrap";
    const canvas = document.createElement("canvas");
    wrap.appendChild(canvas);

    card.appendChild(header);
    card.appendChild(meta);
    if (q.extracted_text) card.appendChild(extracted);
    card.appendChild(wrap);
    root.appendChild(card);

    drawMistakesCanvas(canvas, q.cropped_image_url, q.mistakes || []);
  });
}

function drawMistakesCanvas(canvas, imgUrl, mistakes) {
  const img = new Image();
  img.onload = () => {
    const maxW = 520;
    const scale = Math.min(1, maxW / img.width);
    canvas.width = Math.floor(img.width * scale);
    canvas.height = Math.floor(img.height * scale);
    const ctx = canvas.getContext("2d");
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);

    // overlay mistakes
    for (const m of mistakes) {
      const b = m.bbox || {};
      if (![b.x, b.y, b.w, b.h].every((v) => Number.isFinite(v))) continue;
      const x = b.x * scale;
      const y = b.y * scale;
      const w = b.w * scale;
      const h = b.h * scale;
      ctx.save();
      ctx.lineWidth = 2;
      ctx.strokeStyle = m.severity === "minor" ? "#9bffb8" : "#ff6a7a";
      ctx.fillStyle = m.severity === "minor" ? "rgba(155,255,184,0.14)" : "rgba(255,106,122,0.12)";
      ctx.strokeRect(x, y, w, h);
      ctx.fillRect(x, y, w, h);
      if (m.label) {
        ctx.font = "12px ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, Liberation Mono, Courier New";
        ctx.fillStyle = "rgba(0,0,0,0.55)";
        ctx.fillRect(x, Math.max(0, y - 18), Math.min(w, canvas.width - x), 18);
        ctx.fillStyle = "#ffffff";
        ctx.fillText(String(m.label).slice(0, 60), x + 4, Math.max(12, y - 5));
      }
      ctx.restore();
    }
  };
  img.src = imgUrl;
}

el("uploadBtn").addEventListener("click", () => uploadPdf().catch((e) => setStatus("uploadStatus", String(e), true)));
el("autoBtn").addEventListener("click", () => autoDetectQuestions().catch((e) => setStatus("selInfo", String(e), true)));
el("gradeBtn").addEventListener("click", () => grade().catch((e) => setStatus("gradeStatus", String(e), true)));

