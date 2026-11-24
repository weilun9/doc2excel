"use strict";
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let LANG = localStorage.getItem("lang") || null;
let STATE = { docs: [] };
const selected = new Set();
const native = () => (window.pywebview && window.pywebview.api) || null;

function t(key, vars) {
  let s = (I18N[LANG] || I18N.zh)[key] ?? I18N.en[key] ?? key;
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.replace(`{${k}}`, v);
  return s;
}

function applyLang() {
  document.documentElement.lang = LANG;
  document.querySelectorAll("[data-i18n]").forEach((el) => (el.textContent = t(el.dataset.i18n)));
  $("#btnLang").textContent = LANG === "zh" ? "EN" : "中文";
  renderChips();
  renderDocs();
  if (RV.doc) renderReview();
}

function toast(msg, ms = 3500, action) {
  const el = $("#toast");
  el.innerHTML = esc(msg);
  if (action) {
    const b = document.createElement("button");
    b.className = "small";
    b.textContent = action.label;
    b.onclick = action.fn;
    el.appendChild(b);
  }
  el.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add("hidden"), ms);
}

// Per-launch key injected by the server; required on every /api call.
const D2X_KEY = document.querySelector('meta[name="d2x-key"]')?.content || "";
const keyed = (url) => url + (url.includes("?") ? "&" : "?") + "k=" + encodeURIComponent(D2X_KEY);
function apiHeaders(json) {
  const h = { "X-D2X-Key": D2X_KEY };
  if (json) h["Content-Type"] = "application/json";
  return h;
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: apiHeaders(opts.body && !(opts.body instanceof FormData)),
  });
  const ct = res.headers.get("content-type") || "";
  const data = ct.includes("json") ? await res.json() : res;
  if (!res.ok && ct.includes("json")) throw Object.assign(new Error(data.error || res.status), { data });
  return data;
}

// ---------------- state polling ----------------
let qrUrl = "";
async function refresh() {
  try {
    STATE = await api("/api/state");
  } catch (e) {
    return;
  }
  if (!LANG) { LANG = STATE.ui_lang || "zh"; applyLang(); }
  const sel = $("#phoneUrl");
  const urls = STATE.phone_urls || [];
  if (sel.options.length !== urls.length || [...sel.options].some((o, i) => o.value !== urls[i])) {
    const cur = sel.value;
    sel.innerHTML = urls.map((u) => `<option value="${esc(u)}">${esc(u.replace(/^http:\/\//, "").replace(/\/m\/.*/, ""))}</option>`).join("");
    if (urls.includes(cur)) sel.value = cur;
  }
  if (sel.value && sel.value !== qrUrl) {
    qrUrl = sel.value;
    $("#qr").src = keyed("/api/qr.png?u=" + encodeURIComponent(qrUrl));
    $("#qr").title = qrUrl;
  }
  $("#phoneCount").textContent = STATE.phone_uploads ? t("phoneCount", { n: STATE.phone_uploads }) : "";
  $("#watchPath").textContent = STATE.watch_dir || "—";
  renderChips();
  renderDocs();
  if (RV.doc) {
    const s = STATE.docs.find((d) => d.id === RV.doc.id);
    if (s && s.status !== RV.doc.status && !RV.editing) openReview(RV.doc.id, true);
  }
}

function renderModePickers() {
  const so = $("#selOcr"), sa = $("#selAi");
  if (document.activeElement !== so && STATE.ocr_mode) so.value = STATE.ocr_mode;
  if (document.activeElement !== sa && STATE.ai_mode_setting) sa.value = STATE.ai_mode_setting;
  so.querySelector('[value="vision"]').textContent = t(STATE.vision_ready ? "ocrOptVision" : "ocrOptVisionNo");
  sa.querySelector('[value="online"]').textContent = t("aiOptOnline").replace("DeepSeek", STATE.llm_model || "DeepSeek");
}
async function saveMode(key, value) {
  await api("/api/settings", { method: "POST", body: JSON.stringify({ [key]: value }) });
  toast(key === "OCR_MODE" && value === "vision" && !STATE.vision_ready ? t("visionNeedKey") : t("modeSaved"), 5000);
  refresh();
}
$("#selOcr").onchange = (e) => saveMode("OCR_MODE", e.target.value);
$("#selAi").onchange = (e) => saveMode("AI_MODE", e.target.value);

function renderChips() {
  if (!STATE.ai_mode) return;
  renderModePickers();
  const ai = $("#chipAi");
  if (!STATE.llm_ready) { ai.textContent = t("aiNoKey"); ai.className = "chip off"; }
  else if (STATE.ai_mode === "online") { ai.textContent = t("aiOnline", { m: STATE.llm_model || "LLM" }); ai.className = "chip on"; }
  else { ai.textContent = t("aiOffline"); ai.className = "chip off"; }
  const ocr = $("#chipOcr");
  ocr.textContent = STATE.ocr_engine === "vision" ? t("ocrVision") : t("ocrLocal");
  ocr.className = "chip " + (STATE.ocr_engine === "vision" ? "on" : "");
}

const SRC_ICON = { phone: "📱", local: "💻", folder: "📁", scanner: "🖨️" };
function renderDocs() {
  const list = $("#docList");
  const docs = STATE.docs || [];
  $("#empty").style.display = docs.length ? "none" : "";
  for (const id of [...selected]) if (!docs.some((d) => d.id === id)) selected.delete(id);
  const html = docs.map((d) => {
    const busy = ["queued", "ocr", "ai"].includes(d.status);
    const status = d.confirmed ? `<span class="badge ok">✓ ${t("confirmed")}</span>`
      : `<span class="badge ${d.status}">${busy ? '<i class="spin"></i>' : ""}${t("st_" + d.status)}${d.progress ? " " + esc(d.progress) : ""}</span>`;
    const meta = [
      `${SRC_ICON[d.source] || ""} ${t("src_" + d.source)}`,
      d.engine ? t("eng_" + d.engine) : "",
      d.mode ? t("mode_" + d.mode) : "",
      d.status === "ready" || d.confirmed ? t("rows", { n: d.n_rows }) : "",
    ].filter(Boolean).join(" · ");
    return `<div class="doc ${d.confirmed ? "is-ok" : ""}" data-id="${d.id}">
      <input type="checkbox" class="sel" ${selected.has(d.id) ? "checked" : ""} ${d.status !== "ready" ? "disabled" : ""}>
      <div class="doc-main" data-act="view">
        <div class="doc-title">${esc(d.title || d.name)}</div>
        <div class="doc-meta">${esc(d.name)} · ${esc(meta)}</div>
        ${d.error ? `<div class="doc-err">${esc(d.error)}</div>` : ""}
        ${d.warning ? `<div class="doc-warn">${esc(d.warning)}</div>` : ""}
      </div>
      ${status}
      <button class="small" data-act="view" ${d.status === "ready" || d.status === "error" ? "" : "disabled"}>${t("view")}</button>
      ${erpChip(d)}
      <button class="small" data-act="erp" ${STATE.erp_ready ? "" : "disabled"} title="${esc(STATE.erp_ready ? "" : t("erpNotSet"))}">${t("erpSend")}</button>
      <button class="small ghost" data-act="del">${t("del")}</button>
    </div>`;
  }).join("");
  // Only touch the DOM when something changed, so polling never eats a click.
  if (html !== renderDocs.last) { list.innerHTML = html; renderDocs.last = html; }
  const ready = docs.filter((d) => d.status === "ready");
  $("#selAll").checked = ready.length > 0 && ready.every((d) => selected.has(d.id));
}

$("#docList").addEventListener("click", async (e) => {
  const row = e.target.closest(".doc");
  if (!row) return;
  const id = row.dataset.id;
  if (e.target.classList.contains("sel")) {
    e.target.checked ? selected.add(id) : selected.delete(id);
    return;
  }
  const act = e.target.closest("[data-act]")?.dataset.act;
  if (act === "view") {
    const d = STATE.docs.find((x) => x.id === id);
    if (d && (d.status === "ready" || d.status === "error")) openReview(id);
  } else if (act === "erp") {
    await sendToErp([id]);
  } else if (act === "del") {
    if (!confirm(t("deleteAsk"))) return;
    await api(`/api/doc/${id}`, { method: "DELETE" });
    selected.delete(id);
    refresh();
  }
});

$("#selAll").addEventListener("change", (e) => {
  for (const d of STATE.docs) if (d.status === "ready") e.target.checked ? selected.add(d.id) : selected.delete(d.id);
  renderDocs();
});

// ---------------- adding files ----------------
async function uploadFiles(files) {
  if (!files.length) return;
  const fd = new FormData();
  for (const f of files) fd.append("files", f, f.name);
  const res = await api("/api/upload", { method: "POST", body: fd });
  if (res.added.length) toast(t("uploaded", { n: res.added.length }));
  if (res.rejected.length) toast(t("rejected", { f: res.rejected.join(", ") }), 5000);
  refresh();
}

$("#btnFiles").onclick = async () => {
  if (native()) {
    const paths = await native().open_files();
    if (paths && paths.length) {
      const r = await api("/api/add-paths", { method: "POST", body: JSON.stringify({ paths }) });
      toast(t("uploaded", { n: r.added.length }));
      refresh();
    }
  } else {
    const inp = document.createElement("input");
    inp.type = "file"; inp.multiple = true; inp.accept = "image/*,.pdf,.heic";
    inp.onchange = () => uploadFiles([...inp.files]);
    inp.click();
  }
};

$("#btnScan").onclick = async () => {
  try {
    const r = await api("/api/scan", { method: "POST", body: "{}" });
    if (r.ok) { toast(t("uploaded", { n: 1 })); refresh(); }
  } catch (e) {
    toast(t("scanErr", { e: e.data?.error || e.message }), 6000);
  }
};

$("#btnWatchOpen").onclick = () => native()?.open_path(STATE.watch_dir);
$("#btnWatchPick").onclick = async () => {
  const p = await native()?.pick_folder();
  if (p) { await api("/api/settings", { method: "POST", body: JSON.stringify({ WATCH_DIR: p }) }); refresh(); }
};

let dragDepth = 0;
window.addEventListener("dragenter", (e) => { e.preventDefault(); dragDepth++; $("#drop").classList.remove("hidden"); });
window.addEventListener("dragleave", (e) => { e.preventDefault(); if (--dragDepth <= 0) { dragDepth = 0; $("#drop").classList.add("hidden"); } });
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => {
  e.preventDefault();
  dragDepth = 0;
  $("#drop").classList.add("hidden");
  uploadFiles([...(e.dataTransfer?.files || [])]);
});

// Ctrl+V: paste images (e.g. copied in WeChat, screenshots) or files copied in Explorer.
document.addEventListener("paste", (e) => {
  const tag = e.target?.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return; // normal text paste
  const cd = e.clipboardData;
  if (!cd) return;
  let files = [...(cd.files || [])];
  if (!files.length) {
    files = [...(cd.items || [])].filter((it) => it.kind === "file").map((it) => it.getAsFile()).filter(Boolean);
  }
  if (!files.length) return;
  e.preventDefault();
  const stamp = new Date().toTimeString().slice(0, 8).replace(/:/g, "");
  files = files.map((f, i) => {
    // Clipboard bitmaps arrive as "image.png"; give them a useful name.
    if (!f.name || /^image\.(png|jpe?g|bmp|gif)$/i.test(f.name)) {
      const ext = (f.type.split("/")[1] || "png").replace("jpeg", "jpg");
      return new File([f], `paste_${stamp}${files.length > 1 ? "_" + (i + 1) : ""}.${ext}`, { type: f.type });
    }
    return f;
  });
  uploadFiles(files);
});

$("#phoneUrl").onchange = refresh;

// ---------------- export ----------------
$("#btnExport").onclick = async () => {
  const ids = STATE.docs.filter((d) => selected.has(d.id) && d.status === "ready").map((d) => d.id);
  if (!ids.length) return toast(t("noneSelected"));
  const mode = $("#exportMode").value;
  const stamp = new Date().toISOString().slice(0, 16).replace(/[-:T]/g, "");
  const name = `Doc2Excel_${stamp}.xlsx`;
  if (native()) {
    const path = await native().save_dialog(name);
    if (!path) return;
    const r = await api("/api/export", { method: "POST", body: JSON.stringify({ ids, mode, path }) });
    toast(t("exported", { p: r.path }), 8000, { label: t("openFile"), fn: () => native().open_path(r.path) });
  } else {
    const res = await fetch("/api/export", { method: "POST", headers: apiHeaders(true), body: JSON.stringify({ ids, mode }) });
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = name; a.click();
  }
};

// ---------------- SmartAcc DynaMod ----------------
let dmLast = { workbook: "", out_dir: "" };

function showDmResult(r) {
  dmLast = { workbook: r.workbook || "", out_dir: r.out_dir || "" };
  const rows = (r.results || []).map((x) => {
    const status = x.ok
      ? `<td class="ok">${esc(t("dmOk", { n: x.lines }))}</td>`
      : `<td class="bad">${esc(t("dmBad"))}</td>`;
    const why = x.ok ? esc(x.path) : esc((x.problems || []).join("；"));
    return `<tr><td><b>${esc(x.doc_id)}</b><div class="why">${why}</div></td>${status}</tr>`;
  });
  $("#dmTable").innerHTML = rows.join("");
  const notes = [...(r.notes || [])];
  if ((r.results || []).some((x) => !x.ok) && r.workbook) notes.push(t("dmCheckInWb"));
  $("#dmNotes").textContent = notes.join(" ");
  $("#dmOpenWb").classList.toggle("hidden", !r.workbook || !native());
  $("#dmOpenDir").classList.toggle("hidden", !native());
  $("#dmResult").classList.remove("hidden");
}

function dmError(e) {
  const code = e.data?.error;
  if (code === "dm_workbook_missing") return toast(t("dmNeedWb"), 7000);
  if (code === "dm_not_workbook") return toast(t("dmNotWb"), 7000);
  if (code === "no_ready_docs") return toast(t("noneSelected"));
  toast(String(e.message || e), 7000);
}

$("#btnDm").onclick = async () => {
  const ids = STATE.docs.filter((d) => selected.has(d.id) && d.status === "ready").map((d) => d.id);
  if (!ids.length) return toast(t("noneSelected"));
  if (!native()) return;
  const stamp = new Date().toISOString().slice(0, 16).replace(/[-:T]/g, "");
  const path = await native().save_dialog(`进单整理_${stamp}.xlsx`);
  if (!path) return;
  try {
    showDmResult(await api("/api/dynamod/export", { method: "POST", body: JSON.stringify({ ids, path }) }));
  } catch (e) { dmError(e); }
};

$("#btnDmFromWb").onclick = async () => {
  if (!native()) return;
  const path = await native().open_xlsx();
  if (!path) return;
  try {
    showDmResult(await api("/api/dynamod/from_workbook", { method: "POST", body: JSON.stringify({ path }) }));
  } catch (e) { dmError(e); }
};

$("#dmClose").onclick = () => $("#dmResult").classList.add("hidden");
$("#dmOpenWb").onclick = () => dmLast.workbook && native()?.open_path(dmLast.workbook);
$("#dmOpenDir").onclick = () => dmLast.out_dir && native()?.open_path(dmLast.out_dir);
$("#dmPickWb").onclick = async () => {
  const p = await native()?.open_xlsx();
  if (p) $('#settings [name="DYNAMOD_WORKBOOK"]').value = p;
};
$("#dmPickOut").onclick = async () => {
  const p = await native()?.pick_folder();
  if (p) $('#settings [name="DYNAMOD_OUT_DIR"]').value = p;
};

// ---------------- review / edit ----------------
const RV = { doc: null, draft: null, undo: [], editing: false, page: 0, zoom: 1 };
const clone = (o) => JSON.parse(JSON.stringify(o));

async function openReview(id, keepPage) {
  const doc = await api(`/api/doc/${id}`);
  RV.doc = doc;
  RV.draft = clone(doc.table || { title: "", fields: [], columns: ["Text"], rows: [] });
  RV.undo = [];
  RV.editing = false;
  if (!keepPage) { RV.page = 0; RV.zoom = 1; }
  $("#review").classList.remove("hidden");
  renderReview();
}

function renderReview() {
  const d = RV.doc;
  $("#rvName").textContent = d.table?.title || d.name;
  $("#rvMeta").textContent = [d.name, d.engine && t("eng_" + d.engine), d.mode && t("mode_" + d.mode), d.confirmed ? "✓ " + t("confirmed") : ""].filter(Boolean).join(" · ");
  $("#rvWarn").textContent = d.warning || d.error || "";
  const n = d.pages || 1;
  $("#rvPages").innerHTML = n > 1 ? Array.from({ length: n }, (_, i) => `<button class="small ${i === RV.page ? "active" : ""}" data-p="${i}">${i + 1}</button>`).join("") : "";
  $("#rvImg").src = keyed(`/api/doc/${d.id}/page/${RV.page}.jpg`);
  $("#rvImg").style.width = (RV.zoom * 100) + "%";
  $("#ocrText").textContent = d.text || "";
  $("#review").classList.toggle("editing", RV.editing);
  $("#rvEdit").disabled = RV.editing;
  $("#rvUndo").disabled = !RV.editing || !RV.undo.length;
  $("#rvCancel").disabled = !RV.editing;
  renderGrids();
}

function renderGrids() {
  const tb = RV.draft, ed = RV.editing;
  // Read-only (not disabled) outside edit mode, so text can still be selected and copied.
  const dis = ed ? "" : "readonly";
  $("#tTitle").value = tb.title || "";
  $("#tTitle").readOnly = !ed;
  $("#fieldsGrid").innerHTML = `<tr><th>${t("key")}</th><th>${t("value")}</th><th class="edit-only"></th></tr>` +
    tb.fields.map((f, i) => `<tr>
      <td><input data-f="${i}" data-k="key" value="${esc(f.key)}" ${dis}></td>
      <td><input data-f="${i}" data-k="value" value="${esc(f.value)}" ${dis}></td>
      <td class="edit-only"><button class="x" data-delf="${i}">✕</button></td></tr>`).join("");
  const width = (j) => {
    const vis = (s) => [...String(s || "")].reduce((n, ch) => n + (ch.charCodeAt(0) > 0x2e80 ? 2 : 1), 0);
    const m = Math.max(vis(tb.columns[j]), ...tb.rows.map((r) => vis(r[j])));
    return Math.min(42, Math.max(6, m + 2));
  };
  $("#itemsGrid").innerHTML =
    `<tr><th class="rn">#</th>${tb.columns.map((c, j) => `<th style="min-width:${width(j)}ch"><div class="th"><input data-col="${j}" value="${esc(c)}" ${dis}><button class="x edit-only" data-delc="${j}">✕</button></div></th>`).join("")}<th class="edit-only"></th></tr>` +
    tb.rows.map((r, i) => `<tr><td class="rn">${i + 1}</td>${r.map((v, j) => `<td><input data-r="${i}" data-c="${j}" value="${esc(v)}" ${dis}></td>`).join("")}<td class="edit-only"><button class="x" data-delr="${i}">✕</button></td></tr>`).join("");
}

function snapshot() {
  RV.undo.push(clone(RV.draft));
  if (RV.undo.length > 200) RV.undo.shift();
  $("#rvUndo").disabled = false;
}

let focusSnap = null;
$("#tabTable").addEventListener("focusin", (e) => { if (e.target.tagName === "INPUT" && RV.editing) focusSnap = clone(RV.draft); });
$("#tabTable").addEventListener("input", (e) => {
  if (!RV.editing) return;
  const el = e.target;
  if (focusSnap) { RV.undo.push(focusSnap); focusSnap = null; $("#rvUndo").disabled = false; }
  const tb = RV.draft;
  if (el.id === "tTitle") tb.title = el.value;
  else if (el.dataset.f !== undefined) tb.fields[+el.dataset.f][el.dataset.k] = el.value;
  else if (el.dataset.col !== undefined) tb.columns[+el.dataset.col] = el.value;
  else if (el.dataset.r !== undefined) tb.rows[+el.dataset.r][+el.dataset.c] = el.value;
});
$("#tabTable").addEventListener("click", (e) => {
  if (!RV.editing) return;
  const b = e.target.closest("button");
  if (!b) return;
  const tb = RV.draft;
  if (b.dataset.delf !== undefined) { snapshot(); tb.fields.splice(+b.dataset.delf, 1); }
  else if (b.dataset.delr !== undefined) { snapshot(); tb.rows.splice(+b.dataset.delr, 1); }
  else if (b.dataset.delc !== undefined) {
    if (tb.columns.length <= 1) return;
    snapshot(); const j = +b.dataset.delc; tb.columns.splice(j, 1); tb.rows.forEach((r) => r.splice(j, 1));
  } else return;
  renderGrids();
});
// Enter moves down a row in the item grid, like a spreadsheet.
$("#itemsGrid").addEventListener("keydown", (e) => {
  if (e.key !== "Enter" || e.target.dataset.r === undefined) return;
  e.preventDefault();
  const r = +e.target.dataset.r + (e.shiftKey ? -1 : 1), c = e.target.dataset.c;
  $(`#itemsGrid input[data-r="${r}"][data-c="${c}"]`)?.focus();
});

$("#addField").onclick = () => { snapshot(); RV.draft.fields.push({ key: "", value: "" }); renderGrids(); $(`#fieldsGrid input[data-f="${RV.draft.fields.length - 1}"]`)?.focus(); };
$("#addRow").onclick = () => { snapshot(); RV.draft.rows.push(RV.draft.columns.map(() => "")); renderGrids(); $(`#itemsGrid input[data-r="${RV.draft.rows.length - 1}"][data-c="0"]`)?.focus(); };
$("#addCol").onclick = () => { snapshot(); RV.draft.columns.push(LANG === "zh" ? "新列" : "New column"); RV.draft.rows.forEach((r) => r.push("")); renderGrids(); };

$("#rvEdit").onclick = () => { RV.editing = true; renderReview(); };
$("#rvUndo").onclick = () => { if (RV.undo.length) { RV.draft = RV.undo.pop(); renderReview(); } };
$("#rvCancel").onclick = () => { RV.draft = clone(RV.doc.table); RV.undo = []; RV.editing = false; renderReview(); };
$("#rvConfirm").onclick = async () => {
  const r = await api(`/api/doc/${RV.doc.id}/table`, { method: "PUT", body: JSON.stringify({ table: RV.draft, confirmed: true }) });
  RV.doc.table = r.table; RV.doc.confirmed = true;
  RV.draft = clone(r.table); RV.undo = []; RV.editing = false;
  selected.add(RV.doc.id);
  toast(t("confirmedMsg"));
  renderReview();
  refresh();
};
async function redo(kind) {
  await api(`/api/doc/${RV.doc.id}/redo`, { method: "POST", body: JSON.stringify({ kind }) });
  closeReview(true);
  refresh();
}
$("#rvRedoAi").onclick = () => redo("ai");
$("#rvRedoOcr").onclick = () => redo("full");
function closeReview(force) {
  if (!force && RV.editing && RV.undo.length && !confirm(t("unsaved"))) return;
  RV.doc = null; RV.editing = false;
  $("#review").classList.add("hidden");
}
$("#rvClose").onclick = () => closeReview();
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && RV.doc) closeReview();
  if ((e.ctrlKey || e.metaKey) && e.key === "z" && RV.editing && document.activeElement?.tagName !== "INPUT") { e.preventDefault(); $("#rvUndo").click(); }
});
$("#rvPages").addEventListener("click", (e) => { const p = e.target.dataset.p; if (p !== undefined) { RV.page = +p; renderReview(); } });
$("#zoomIn").onclick = () => { RV.zoom = Math.min(4, RV.zoom * 1.25); $("#rvImg").style.width = RV.zoom * 100 + "%"; };
$("#zoomOut").onclick = () => { RV.zoom = Math.max(0.3, RV.zoom / 1.25); $("#rvImg").style.width = RV.zoom * 100 + "%"; };
// ---- copy / save: OCR text tab -> plain text; table tab -> spreadsheet text ----
const activeTab = () => document.querySelector(".tab.active")?.dataset.tab || "table";
function cellText(v) {
  let s = String(v ?? "");
  // CSV/clipboard formula injection: text from a scan like "=HYPERLINK(...)" must
  // not run when pasted or opened in Excel. Plain numbers like -12.50 are kept.
  if (/^[=+\-@\t\r]/.test(s) && !/^[+-]?[\d,]*\.?\d+%?$/.test(s)) s = "'" + s;
  return /[\t\n"]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}
function tableAsDelimited(sep) {
  const tb = RV.draft, lines = [];
  if (tb.title) lines.push(cellText(tb.title));
  for (const f of tb.fields) lines.push([f.key, f.value].map(cellText).join(sep));
  if (lines.length) lines.push("");
  lines.push(tb.columns.map(cellText).join(sep));
  for (const r of tb.rows) lines.push(r.map(cellText).join(sep));
  return lines.join("\r\n");
}
function baseName() {
  const n = (RV.draft.title || RV.doc.name || "document").replace(/\.[a-z0-9]+$/i, "");
  return n.replace(/[\\/:*?"<>|]+/g, "_").slice(0, 80).trim() || "document";
}
async function copyText(text) {
  if (native()?.copy_text) return native().copy_text(text);
  await navigator.clipboard.writeText(text);
  return true;
}
// Text the user selected in the current tab (OCR text, or inside one table cell).
function selectedText() {
  const el = document.activeElement;
  if (el && el.tagName === "INPUT" && el.closest("#tabTable") && el.selectionEnd > el.selectionStart) {
    return el.value.slice(el.selectionStart, el.selectionEnd);
  }
  const sel = window.getSelection();
  const tab = activeTab() === "text" ? $("#tabText") : $("#tabTable");
  if (sel && !sel.isCollapsed && tab.contains(sel.anchorNode) && tab.contains(sel.focusNode)) return sel.toString();
  return "";
}
// Keep focus (and the selection inside a cell) when the Copy button is pressed.
$("#rvCopy").addEventListener("mousedown", (e) => e.preventDefault());
$("#rvCopy").onclick = async () => {
  const isText = activeTab() === "text";
  const part = selectedText();
  try {
    const ok = await copyText(part || (isText ? (RV.doc.text || "") : tableAsDelimited("\t")));
    if (ok === false) throw new Error("clipboard busy");
    toast(t(part ? "copiedSel" : isText ? "copiedText" : "copiedTable"));
  } catch (e) {
    toast(t("copyFail", { e: e.message || e }), 5000);
  }
};
$("#rvSave").onclick = async () => {
  const isText = activeTab() === "text";
  const content = isText ? (RV.doc.text || "") : tableAsDelimited(",");
  const name = baseName() + (isText ? ".txt" : ".csv");
  if (native()?.save_text) {
    const path = await native().save_text(content, name);
    if (path) toast(t("savedTo", { p: path }), 8000, { label: t("openFile"), fn: () => native().open_path(path) });
  } else {
    // UTF-8 BOM so Excel shows Chinese in the CSV correctly.
    const blob = new Blob(["﻿" + content], { type: isText ? "text/plain" : "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = name; a.click();
  }
};

document.querySelectorAll(".tab").forEach((b) => b.onclick = () => {
  document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === b));
  $("#tabTable").classList.toggle("hidden", b.dataset.tab !== "table");
  $("#tabText").classList.toggle("hidden", b.dataset.tab !== "text");
});

// ---------------- settings ----------------
$("#btnSettings").onclick = async () => {
  const s = await api("/api/settings");
  const dlg = $("#settings");
  dlg.querySelectorAll("[name]").forEach((el) => {
    if (el.type === "password") {
      el.value = "";
      el.placeholder = s[el.name + "_SET"] ? "••••••••  (set)" : "(not set)";
    } else el.value = s[el.name] ?? "";
  });
  $("#envPath").textContent = s.ENV_PATH;
  $("#verCurrent").textContent = STATE.version || "?";
  $("#checkResult").textContent = "";
  dlg.classList.remove("hidden");
};
$("#setCancel").onclick = () => $("#settings").classList.add("hidden");
$("#setSave").onclick = async () => {
  const body = {};
  $("#settings").querySelectorAll("[name]").forEach((el) => (body[el.name] = el.value));
  try {
    await api("/api/settings", { method: "POST", body: JSON.stringify(body) });
  } catch (e) {
    if (e.data?.error === "key_required") {
      toast(t("keyRequired"), 7000);
      $(`#settings [name="${e.data.field}"]`)?.focus();
      return;
    }
    throw e;
  }
  $("#settings").classList.add("hidden");
  toast(t("saved"));
  refresh();
};

$("#btnLang").onclick = async () => {
  LANG = LANG === "zh" ? "en" : "zh";
  localStorage.setItem("lang", LANG);
  applyLang();
  api("/api/settings", { method: "POST", body: JSON.stringify({ UI_LANG: LANG }) }).catch(() => {});
};

if (LANG) applyLang();
refresh().then(() => checkUpdate(false));
setInterval(refresh, 1500);

// ---------------- self-update (GitHub Releases) ----------------
const UPD = { latest: "", busy: false, closed: false };
function showUpdateBar(msg, withButton) {
  $("#updateMsg").textContent = msg;
  $("#btnUpdate").classList.toggle("hidden", !withButton);
  $("#updateClose").classList.toggle("hidden", UPD.busy);
  $("#updateBar").classList.remove("hidden");
}
async function checkUpdate(manual) {
  const out = $("#checkResult");
  if (manual) { out.className = "hint"; out.textContent = t("checking"); }
  let r;
  try { r = await api("/api/update/check"); } catch (e) { r = { ok: false, error: e.message }; }
  if (!r.ok) {
    if (manual) { out.textContent = t("updateCheckFail", { e: r.error }); out.className = "hint err-text"; }
    return;  // at startup a failed check (offline) stays silent
  }
  if (r.dev) { if (manual) out.textContent = t("updateDev"); return; }
  if (!r.update) { if (manual) { out.textContent = t("upToDate"); out.className = "hint ok-text"; } return; }
  UPD.latest = r.latest;
  if (manual) out.textContent = t("updateAvail", { v: r.latest });
  if (manual || !UPD.closed) showUpdateBar(t("updateAvail", { v: r.latest }), true);
}
$("#btnCheckUpdate").onclick = () => checkUpdate(true);
$("#updateClose").onclick = () => { UPD.closed = true; $("#updateBar").classList.add("hidden"); };
$("#btnUpdate").onclick = async () => {
  if (UPD.busy) return;
  try {
    await api("/api/update/install", { method: "POST", body: "{}" });
  } catch (e) {
    showUpdateBar(t("updateFail", { e: e.data?.error || e.message }), true);
    return;
  }
  UPD.busy = true;
  $("#settings").classList.add("hidden");
  showUpdateBar(t("updateDownloading", { v: UPD.latest, p: 0 }), false);
  const poll = setInterval(async () => {
    let s;
    try { s = await api("/api/update/status"); } catch { return; }  // app is closing
    if (s.state === "downloading") {
      const p = s.total ? Math.floor((s.done / s.total) * 100) : 0;
      showUpdateBar(t("updateDownloading", { v: UPD.latest, p }), false);
    } else if (s.state === "installing") {
      showUpdateBar(t("updateInstalling"), false);
    } else if (s.state === "error") {
      clearInterval(poll);
      UPD.busy = false;
      showUpdateBar(t("updateFail", { e: s.error }), true);
    }
  }, 500);
};


// ---------------- AI ERP: send original files to the ERP scan queue ----------------
function erpChip(d) {
  if (!d.erp_state) return "";
  const cls = { sent: "ok", already: "ok", failed: "error", sending: "" }[d.erp_state] || "";
  const label = t("erp_" + d.erp_state);
  return `<span class="badge ${cls}" title="${esc(d.erp_message || "")}">${d.erp_state === "sending" ? '<i class="spin"></i>' : ""}${esc(label)}</span>`;
}
async function sendToErp(ids) {
  if (!STATE.erp_ready) { toast(t("erpNotSet"), 5000); return; }
  let sent = 0, already = 0, failed = 0, lastErr = "";
  for (const id of ids) {
    const d = STATE.docs.find((x) => x.id === id);
    if (d) { d.erp_state = "sending"; renderDocs(); }
    try {
      const r = await api(`/api/doc/${id}/erp`, { method: "POST", body: "{}" });
      if (r.state === "sent") sent++; else if (r.state === "already") already++; else { failed++; lastErr = r.error; }
      if (r.code === "unauthorized" || r.code === "unreachable" || r.code === "not_configured") {
        failed += ids.length - (sent + already + failed); break;  // same error for every file
      }
    } catch (e) { failed++; lastErr = e.message; }
  }
  await refresh();
  const msg = [sent && t("erpSentN", { n: sent }), already && t("erpAlreadyN", { n: already }),
               failed && t("erpFailedN", { n: failed, e: lastErr })].filter(Boolean).join(" · ");
  toast(msg, failed ? 9000 : 4000);
}
$("#btnErpAll").onclick = () => {
  const ids = STATE.docs.filter((d) => selected.has(d.id)).map((d) => d.id);
  if (!ids.length) return toast(t("noneSelected"));
  sendToErp(ids);
};
$("#rvErp").onclick = () => RV.doc && sendToErp([RV.doc.id]);
$("#erpTest").onclick = async () => {
  const out = $("#erpTestResult");
  const url = $('#settings [name="ERP_URL"]').value.trim(), tok = $('#settings [name="ERP_TOKEN"]').value;
  out.textContent = t("erpTesting");
  try {  // save first so the test uses what is typed
    await api("/api/settings", { method: "POST", body: JSON.stringify({ ERP_URL: url, ERP_TOKEN: tok }) });
  } catch (e) {
    out.textContent = e.data?.error === "key_required" ? t("keyRequired") : e.message;
    return;
  }
  const r = await api("/api/erp/ping", { method: "POST", body: "{}" });
  out.textContent = r.ok ? t("erpOk", { u: r.user || "?", q: r.queue ?? 0 }) : "✕ " + r.error;
  out.className = "hint " + (r.ok ? "ok-text" : "err-text");
  refresh();
};
