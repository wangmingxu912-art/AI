/* Minimal UI: upload PDF -> draw bbox -> add questions -> grade -> overlay mistake boxes */

let state = {
  examId: null,
  pages: [], // {page_index, width, height, url}
  selections: new Map(), // key=page_index -> {bbox, displayScale}
  questions: [], // {id, page_index, bbox, max_marks, question_text, mark_scheme}
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
  state.selections = new Map();
  state.questions = [];
  renderPages();
  renderQuestions();
  setStatus("uploadStatus", `渲染完成：exam_id=${state.examId}，共 ${state.pages.length} 页`);
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

    attachCanvasBehavior(canvas, p);
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

function drawSelection(canvas) {
  const pageIndex = Number(canvas.dataset.pageIndex);
  const sel = state.selections.get(pageIndex);
  if (!sel) return;

  const ctx = canvas.getContext("2d");
  // Redraw base image by reloading (cheap enough)
  // But to avoid flicker, we just draw overlay on current image.
  const s = sel.displayScale;
  const r = sel.bbox;
  ctx.save();
  ctx.lineWidth = 2;
  ctx.strokeStyle = "#6aa6ff";
  ctx.fillStyle = "rgba(106,166,255,0.16)";
  ctx.strokeRect(r.x * s, r.y * s, r.w * s, r.h * s);
  ctx.fillRect(r.x * s, r.y * s, r.w * s, r.h * s);
  ctx.restore();
}

function attachCanvasBehavior(canvas, page) {
  let dragging = false;
  let start = null;

  function canvasToImageXY(e) {
    const rect = canvas.getBoundingClientRect();
    const cx = e.clientX - rect.left;
    const cy = e.clientY - rect.top;
    const scale = canvas.width / page.width; // display pixels per image pixel
    const ix = Math.max(0, Math.min(page.width, Math.round(cx / scale)));
    const iy = Math.max(0, Math.min(page.height, Math.round(cy / scale)));
    return { ix, iy, scale };
  }

  canvas.addEventListener("mousedown", (e) => {
    if (!state.examId) return;
    dragging = true;
    const { ix, iy, scale } = canvasToImageXY(e);
    start = { ix, iy, scale };
    state.selections.set(page.page_index, {
      bbox: { x: ix, y: iy, w: 1, h: 1 },
      displayScale: scale,
    });
    setStatus("selInfo", `已在 Page ${page.page_index + 1} 开始框选：起点 (${ix}, ${iy})`);
  });

  canvas.addEventListener("mousemove", (e) => {
    if (!dragging || !start) return;
    const { ix, iy } = canvasToImageXY(e);
    const x = Math.min(start.ix, ix);
    const y = Math.min(start.iy, iy);
    const w = Math.max(1, Math.abs(ix - start.ix));
    const h = Math.max(1, Math.abs(iy - start.iy));
    state.selections.set(page.page_index, {
      bbox: { x, y, w, h },
      displayScale: start.scale,
    });
    // redraw by reloading image and drawing selection after load
    loadPageIntoCanvas(canvas, page);
  });

  window.addEventListener("mouseup", () => {
    if (!dragging || !start) return;
    dragging = false;
    const sel = state.selections.get(page.page_index);
    if (sel) {
      const r = sel.bbox;
      setStatus(
        "selInfo",
        `当前框选：Page ${page.page_index + 1} bbox = x:${r.x} y:${r.y} w:${r.w} h:${r.h}`
      );
    }
    start = null;
  });
}

function addQuestion() {
  if (!state.examId) {
    setStatus("selInfo", "请先上传 PDF", true);
    return;
  }
  const id = el("qId").value.trim();
  const max = Number(el("qMax").value);
  if (!id) {
    setStatus("selInfo", "请输入题号（例如 Q1）", true);
    return;
  }
  if (!Number.isFinite(max) || max <= 0) {
    setStatus("selInfo", "满分必须是 > 0 的数字", true);
    return;
  }

  // Find the most recent selection (highest page index with a selection)
  let chosen = null;
  for (const p of state.pages) {
    const sel = state.selections.get(p.page_index);
    if (sel) chosen = { page_index: p.page_index, sel };
  }
  if (!chosen) {
    setStatus("selInfo", "请先在某一页上拖拽框选答案区域", true);
    return;
  }

  state.questions.push({
    id,
    page_index: chosen.page_index,
    bbox: chosen.sel.bbox,
    max_marks: max,
    question_text: el("qText").value || "",
    mark_scheme: el("qMs").value || "",
    subject: "AQA A-Level",
  });
  renderQuestions();
  setStatus("selInfo", `已添加题目 ${id}（Page ${chosen.page_index + 1}）`);
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
      <div class="meta">满分：${q.max_marks}</div>
    `;
    div.querySelector("button[data-remove]")?.addEventListener("click", () => {
      state.questions.splice(idx, 1);
      renderQuestions();
    });
    root.appendChild(div);
  });
}

function clearSelection() {
  state.selections = new Map();
  renderPages();
  setStatus("selInfo", "已清除所有框选");
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
el("addQuestionBtn").addEventListener("click", () => addQuestion());
el("clearSelBtn").addEventListener("click", () => clearSelection());
el("gradeBtn").addEventListener("click", () => grade().catch((e) => setStatus("gradeStatus", String(e), true)));

