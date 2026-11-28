"use strict";
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const ico = (id, cls = "") => `<svg class="i ${cls}"><use href="#i-${id}"/></svg>`;

let LANG = localStorage.getItem("lang") || null;
let STATE = { docs: [] };
let FILTER = "";              // station filter for the inbox ("" = all)
const selected = new Set();
const native = () => (window.pywebview && window.pywebview.api) || null;

function t(key, vars) {
  let s = (I18N[LANG] || I18N.zh)[key] ?? I18N.en[key] ?? key;
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.replace(`{${k}}`, v);
  return s;
}

function applyLang() {
  document.documentElement.lang = LANG;
  $$("[data-i18n]").forEach((el) => (el.textContent = t(el.dataset.i18n)));
  $$("[data-i18n-title]").forEach((el) => (el.title = t(el.dataset.i18nTitle)));
  $$("[data-i18n-aria]").forEach((el) => el.setAttribute("aria-label", t(el.dataset.i18nAria)));
  $("#btnLang").textContent = LANG === "zh" ? "EN" : "中文";
  renderChips();
  renderMap.last = renderDocs.last = null;
  renderAll();
  if (RV.doc) renderReview();
}

function toast(msg, ms = 3500, action) {
  const el = $("#toast");
  el.innerHTML = esc(msg);
  if (action) {
    const b = document.createElement("button");
    b.className = "btn small";
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

// ---------------- stations ----------------
// Where a document is on the line. Read = waiting or OCR, Build = AI table.
function stationOf(d) {
  if (d.status === "queued" || d.status === "ocr") return "read";
  if (d.status === "ai") return "build";
  if (d.status === "error") return "errors";
  if (d.confirmed) return "confirmed";
  return "review";
}
const erpDone = (d) => d.erp_state === "sent" || d.erp_state === "already";
const isBusy = (d) => ["queued", "ocr", "ai"].includes(d.status);
const toReview = () => (STATE.docs || []).filter((d) => d.status === "ready" && !d.confirmed);

// ---------------- state polling ----------------
let qrUrl = "";
async function refresh() {
  try {
    STATE = await api("/api/state");
  } catch (e) {
    return;
  }
  if (!LANG) { LANG = STATE.ui_lang || "zh"; applyLang(); }
  const urls = STATE.phone_urls || [];
  for (const sel of [$("#phoneUrl"), $("#phoneUrlBig")]) {
    if (sel.options.length !== urls.length || [...sel.options].some((o, i) => o.value !== urls[i])) {
      const cur = sel.value || qrUrl;
      sel.innerHTML = urls.map((u) => `<option value="${esc(u)}">${esc(u.replace(/^http:\/\//, "").replace(/\/m\/.*/, ""))}</option>`).join("");
      if (urls.includes(cur)) sel.value = cur;
    }
  }
  const want = urls.includes(qrUrl) ? qrUrl : (urls[0] || "");
  if (want && want !== $("#qr").dataset.u) {
    qrUrl = want;
    for (const img of [$("#qr"), $("#qrBig")]) {
      img.src = keyed("/api/qr.png?u=" + encodeURIComponent(qrUrl));
      img.title = qrUrl;
      img.dataset.u = qrUrl;
    }
    $("#phoneUrl").value = $("#phoneUrlBig").value = qrUrl;
  }
  const pc = $("#phoneCount");
  pc.textContent = STATE.phone_uploads ? t("phoneCount", { n: STATE.phone_uploads }) : "";
  pc.classList.toggle("hidden", !STATE.phone_uploads);
  $("#watchPath").textContent = $("#emptyWatch").textContent = STATE.watch_dir || "—";
  renderChips();
  if (RV.doc) {
    const s = STATE.docs.find((d) => d.id === RV.doc.id);
    if (!s) { RV.doc = null; }
    else if ((s.status !== RV.doc.status || s.img_ver !== RV.doc.img_ver) && !RV.editing) {
      await openReview(RV.doc.id, true);
    }
  }
  if (!RV.doc && !RV.loading) {
    const next = toReview()[0];
    if (next) await openReview(next.id);
  }
  renderAll();
}

function renderAll() {
  renderMap();
  renderDocs();
  renderWork();
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

// The small text beside each picker says what is actually in use right now.
function renderChips() {
  if (!STATE.ai_mode) return;
  renderModePickers();
  const ai = $("#chipAi");
  if (!STATE.llm_ready) { ai.textContent = t("engNoKey"); ai.className = "off"; }
  else if (STATE.ai_mode === "online") { ai.textContent = STATE.llm_model || "LLM"; ai.className = ""; }
  else { ai.textContent = t("engRules"); ai.className = ""; }
  $("#chipOcr").textContent = STATE.ocr_engine === "vision" ? t("engVision") : t("engLocal");
}

// ---------------- the line map ----------------
function renderMap() {
  const docs = STATE.docs || [];
  const n = (st) => docs.filter((d) => stationOf(d) === st).length;
  const stations = [["", t("stInbox"), docs.length], ["read", t("stRead"), n("read")], ["build", t("stBuild"), n("build")],
    ["review", t("stReview"), n("review")], ["confirmed", t("stConfirmed"), n("confirmed")]];
  if (n("errors")) stations.splice(4, 0, ["errors", t("stErrors"), n("errors")]);
  const hot = n("review") ? "review" : null;
  const erp = docs.filter(erpDone).length;
  const html = `<div class="stations">${stations.map(([k, label, c]) =>
    `<button class="station${k === hot ? " hot" : ""}${k === "errors" ? " err" : ""}${FILTER === k && k ? " sel" : ""}" data-st="${k}" title="${esc(t("filterHint"))}">
      <span class="ring"></span><span class="lab">${esc(label)} <span class="n">${c}</span></span></button>`).join("")}</div>
    <div class="branches" aria-hidden="true">
      <svg width="54" height="44" viewBox="0 0 54 44"><path d="M2 10 L14 -2 H48" stroke="#1f8a4c" stroke-width="4" fill="none" stroke-linejoin="round" stroke-linecap="round"/>
      <path d="M2 10 H48" stroke="#e9a300" stroke-width="4" stroke-linecap="round"/><path d="M2 10 L14 22 H48" stroke="#2457c5" stroke-width="4" fill="none" stroke-linejoin="round" stroke-linecap="round"/></svg>
      <span class="br excel">${esc(t("brExcel"))}</span><span class="br dm">${esc(t("brDm"))}</span>
      <button class="br erp" data-st="erp">${esc(t("brErp"))}${erp ? " · " + erp : ""}</button></div>`;
  if (html !== renderMap.last) { $("#map").innerHTML = html; renderMap.last = html; }
}
$("#map").addEventListener("click", (e) => {
  const b = e.target.closest("[data-st]");
  if (!b) return;
  setFilter(FILTER === b.dataset.st ? "" : b.dataset.st);
});
function setFilter(st) {
  FILTER = st;
  $("#filterChip").classList.toggle("hidden", !st);
  $("#filterName").textContent = st ? ({ erp: t("brErp") }[st] || t("st" + st[0].toUpperCase() + st.slice(1))) : "";
  renderMap.last = renderDocs.last = null;
  renderAll();
}
$("#filterChip").onclick = () => setFilter("");

// ---------------- inbox ----------------
const SRC = (s) => t("src_" + s);
function docMarker(d) {
  if (isBusy(d)) return "busy";
  if (d.status === "error") return "err";
  if (d.confirmed && erpDone(d)) return "erp";
  if (d.confirmed) return "ok";
  return "";
}
function docMeta(d) {
  if (isBusy(d)) return { cls: "", text: t("st_" + d.status) + (d.progress ? " · " + d.progress : "") };
  if (d.status === "error") return { cls: "err", text: d.error || t("st_error") };
  const parts = [d.ref, t("rows", { n: d.n_rows }), SRC(d.source), d.enhanced && d.enhanced.length ? t("enhancedTag") : ""];
  if (d.confirmed) parts.splice(1, 0, t("confirmed"));
  return { cls: d.warning ? "warn" : "", text: parts.filter(Boolean).join(" · ") };
}
function erpTag(d) {
  if (!d.erp_state || d.erp_state === "sent" || d.erp_state === "already") {
    return d.erp_state ? `<span class="erp-tag" title="${esc(d.erp_message || "")}">${esc(t("erp_" + d.erp_state))}</span>` : "";
  }
  return `<span class="erp-tag ${d.erp_state === "failed" ? "failed" : ""}" title="${esc(d.erp_message || "")}">${esc(t("erp_" + d.erp_state))}</span>`;
}
function renderDocs() {
  const all = STATE.docs || [];
  for (const id of [...selected]) if (!all.some((d) => d.id === id && d.status === "ready")) selected.delete(id);
  const docs = FILTER === "erp" ? all.filter(erpDone) : FILTER ? all.filter((d) => stationOf(d) === FILTER) : all;
  const html = docs.map((d) => {
    const m = docMeta(d);
    const title = d.status === "ready" || d.confirmed ? (d.title || d.name) : d.name;
    return `<div class="doc${RV.doc && RV.doc.id === d.id ? " open" : ""}" data-id="${d.id}" role="option" aria-selected="${RV.doc && RV.doc.id === d.id}">
      <input type="checkbox" class="sel" ${selected.has(d.id) ? "checked" : ""} ${d.status !== "ready" ? "disabled" : ""} aria-label="${esc(t("selectOne"))}">
      <i class="mk ${docMarker(d)}"></i>
      <div class="doc-main"><div class="doc-title">${esc(title)}</div><div class="doc-meta ${m.cls}">${esc(m.text)}</div></div>
      <div class="doc-side">${d.total ? `<span class="amt">${esc(d.total)}</span>` : ""}${erpTag(d)}</div>
      <button class="del" data-act="del" title="${esc(t("del"))}">${ico("trash")}</button>
    </div>`;
  }).join("");
  // Only touch the DOM when something changed, so polling never eats a click.
  if (html !== renderDocs.last) { $("#docList").innerHTML = html; renderDocs.last = html; }
  const ready = all.filter((d) => d.status === "ready");
  $("#selAll").checked = ready.length > 0 && ready.every((d) => selected.has(d.id));
  $("#selAll").disabled = !ready.length;
  $("#inboxCount").textContent = all.length ? t("docsCount", { n: all.length }) : "";
  const emptyMsg = $("#inboxEmpty");
  emptyMsg.classList.toggle("hidden", docs.length > 0);
  emptyMsg.textContent = all.length ? t("inboxEmptyFilter") : t("inboxEmpty");
  renderSelBar();
}
function renderSelBar() {
  const n = selected.size;
  $("#selBar").classList.toggle("hidden", !n);
  $("#selCount").textContent = t("selCount", { n });
  $("#btnErpAll").disabled = !STATE.erp_ready;
  $("#btnErpAll").title = STATE.erp_ready ? "" : t("erpNotSet");
  $("#btnDm").disabled = !native();
}

$("#docList").addEventListener("click", async (e) => {
  const row = e.target.closest(".doc");
  if (!row) return;
  const id = row.dataset.id;
  if (e.target.classList.contains("sel")) {
    e.target.checked ? selected.add(id) : selected.delete(id);
    renderSelBar();
    return;
  }
  if (e.target.closest("[data-act=del]")) {
    if (!confirm(t("deleteAsk"))) return;
    await api(`/api/doc/${id}`, { method: "DELETE" });
    selected.delete(id);
    if (RV.doc && RV.doc.id === id) { RV.doc = null; RV.editing = false; }
    refresh();
    return;
  }
  const d = STATE.docs.find((x) => x.id === id);
  if (!d) return;
  if (d.status === "ready" || d.status === "error") {
    if (RV.editing && RV.undo.length && !confirm(t("unsaved"))) return;
    await openReview(id);
    renderAll();
  } else toast(t("stillReading"));
});

$("#selAll").addEventListener("change", (e) => {
  for (const d of STATE.docs) if (d.status === "ready") e.target.checked ? selected.add(d.id) : selected.delete(d.id);
  renderDocs.last = null;
  renderDocs();
});

// ---------------- work area: empty / idle / review ----------------
function renderWork() {
  const docs = STATE.docs || [];
  const view = !docs.length ? "empty" : RV.doc ? "review" : "idle";
  $("#empty").classList.toggle("hidden", view !== "empty");
  $("#idle").classList.toggle("hidden", view !== "idle");
  $("#review").classList.toggle("hidden", view !== "review");
  if (view === "idle") {
    const busy = docs.filter(isBusy).length;
    $("#idleMsg").textContent = busy ? t("idleBusy", { n: busy }) : t("idleDone");
  }
  if (view === "review") renderNext();
}

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
$("#emptyFiles").onclick = () => $("#btnFiles").click();

$("#btnScan").onclick = async () => {
  try {
    const r = await api("/api/scan", { method: "POST", body: "{}" });
    if (r.ok) { toast(t("uploaded", { n: 1 })); refresh(); }
  } catch (e) {
    toast(t("scanErr", { e: e.data?.error || e.message }), 6000);
  }
};
$("#emptyScan").onclick = () => $("#btnScan").click();

$("#btnWatchOpen").onclick = () => native()?.open_path(STATE.watch_dir);
$("#btnWatchPick").onclick = async () => {
  const p = await native()?.pick_folder();
  if (p) { await api("/api/settings", { method: "POST", body: JSON.stringify({ WATCH_DIR: p }) }); refresh(); }
};
$("#emptyWatchPick").onclick = () => $("#btnWatchPick").click();

// Phone QR popover: grows from the Phone button; Esc or a click outside closes it.
function setPhonePop(open) {
  $("#phonePop").classList.toggle("hidden", !open);
  $("#btnPhone").classList.toggle("on", open);
  $("#btnPhone").setAttribute("aria-expanded", open);
}
$("#btnPhone").onclick = (e) => { e.stopPropagation(); setPhonePop($("#phonePop").classList.contains("hidden")); };
$("#phonePopClose").onclick = () => setPhonePop(false);
document.addEventListener("click", (e) => {
  if (!$("#phonePop").classList.contains("hidden") && !e.target.closest("#phonePop, #btnPhone")) setPhonePop(false);
  if (!$("#rerunMenu").classList.contains("hidden") && !e.target.closest("#rerunMenu, #rvRerun")) setRerun(false);
});
for (const sel of [$("#phoneUrl"), $("#phoneUrlBig")]) {
  sel.onchange = () => { qrUrl = sel.value; $("#qr").dataset.u = ""; refresh(); };
}

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

// ---------------- export ----------------
const selectedReady = () => STATE.docs.filter((d) => selected.has(d.id) && d.status === "ready").map((d) => d.id);
$("#btnExport").onclick = async () => {
  const ids = selectedReady();
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
  const ids = selectedReady();
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
  if (!native()) return toast(t("needDesktop"));
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
const RV = { doc: null, draft: null, undo: [], editing: false, page: 0, zoom: 1, loading: false };
const clone = (o) => JSON.parse(JSON.stringify(o));

async function openReview(id, keepPage) {
  RV.loading = true;
  try {
    const doc = await api(`/api/doc/${id}`);
    const sameDoc = RV.doc && RV.doc.id === id;
    RV.doc = doc;
    RV.draft = clone(doc.table || { title: "", fields: [], columns: ["Text"], rows: [] });
    RV.undo = [];
    RV.editing = false;
    if (!keepPage || !sameDoc) { RV.page = 0; RV.zoom = 1; }
    renderReview();
  } finally {
    RV.loading = false;
  }
}

function renderReview() {
  const d = RV.doc;
  if (!d) return;
  const busy = isBusy(d);
  $("#tTitle").value = RV.draft.title || "";
  $("#tTitle").placeholder = d.name;
  $("#tTitle").readOnly = !RV.editing;
  $("#rvMeta").textContent = [d.name, SRC(d.source), d.engine && t("eng_" + d.engine), d.mode && t("mode_" + d.mode),
    d.confirmed ? t("confirmed") : ""].filter(Boolean).join("  ·  ");
  $("#rvBadge").classList.toggle("hidden", !(d.enhanced && d.enhanced.length));
  const warn = busy ? t("rereading") : (d.error || d.warning || "");
  $("#rvWarn").textContent = warn;
  $("#rvWarnBox").classList.toggle("hidden", !warn);
  $("#rvWarnBox").classList.toggle("err", !!d.error && !busy);
  const n = d.pages || 1;
  $("#rvPages").innerHTML = n > 1 ? Array.from({ length: n }, (_, i) => `<button class="${i === RV.page ? "active" : ""}" data-p="${i}">${i + 1}</button>`).join("") : "";
  // v= changes when the page image changes (rotation / re-read), so the browser reloads it
  const src = keyed(`/api/doc/${d.id}/page/${RV.page}.jpg?v=${d.img_ver || 0}-${d.rotate || 0}`);
  if ($("#rvImg").getAttribute("src") !== src) $("#rvImg").src = src;
  applyZoom();
  $("#ocrText").textContent = d.text || "";
  $("#review").classList.toggle("editing", RV.editing);
  $("#rvUndo").disabled = !RV.undo.length;
  $("#rvEdit").disabled = busy;
  $("#rvRerun").disabled = busy;
  $("#rotLeft").disabled = $("#rotRight").disabled = busy || RV.editing;
  $("#rvConfirm").disabled = d.status !== "ready";
  $("#rvConfirmLabel").textContent = RV.editing ? t("saveConfirm") : t("confirmNext");
  $("#rvErp").disabled = !STATE.erp_ready;
  $("#rvErp").title = STATE.erp_ready ? "" : t("erpNotSet");
  renderGrids();
  renderCheck();
  renderNext();
}

// The bill that "Confirm & next" will open.
function nextDoc() {
  const list = toReview().filter((d) => !RV.doc || d.id !== RV.doc.id);
  if (!RV.doc) return list[0];
  const order = (STATE.docs || []).map((d) => d.id);
  const here = order.indexOf(RV.doc.id);
  return list.find((d) => order.indexOf(d.id) > here) || list[0];
}
function renderNext() {
  const nx = nextDoc();
  $("#rvNext").innerHTML = nx
    ? `${esc(t("nextLabel"))}<b>${esc(nx.title || nx.name)}</b>`
    : `${esc(t("nextLabel"))}<b>${esc(t("nextNone"))}</b>`;
}

const NUM = /^[+-]?(RM\s*)?[\d,]*\.?\d+$/i;
const toNum = (s) => parseFloat(String(s).replace(/RM|,|\s/gi, ""));
const TOTAL_KEY = /total|amount\s*due|jumlah|合计|总计|总额/i, SUB_KEY = /sub\s*-?\s*total|小计|tax|gst|sst|discount/i;
const AMT_COL = /^(total|amount|amt|jumlah|nett?\s*amount|金额|合计|小计)\b/i;
// Proof line: do the line totals add up to the bill's total?
function renderCheck() {
  const tb = RV.draft, box = $("#rvCheck");
  let total = null;
  for (const f of tb.fields) if (TOTAL_KEY.test(f.key) && !SUB_KEY.test(f.key) && NUM.test(String(f.value).trim())) total = toNum(f.value);
  let col = -1;
  tb.columns.forEach((c, j) => { if (AMT_COL.test(String(c).trim())) col = j; });
  const vals = col < 0 ? [] : tb.rows.map((r) => String(r[col] ?? "").trim()).filter((v) => v && NUM.test(v)).map(toNum);
  if (total === null || !vals.length) { box.classList.add("hidden"); return; }
  const sum = vals.reduce((a, b) => a + b, 0);
  // Tax, service charge, discount and rounding sit between the line totals and the bill total.
  let adj = 0, subtotal = null;
  for (const f of tb.fields) {
    const v = String(f.value).trim();
    if (!NUM.test(v)) continue;
    if (/sub\s*-?\s*total|小计/i.test(f.key)) subtotal = toNum(v);
    else if (/tax|gst|sst|service|charge|税|服务费/i.test(f.key)) adj += toNum(v);
    else if (/discount|rebate|less|折扣/i.test(f.key)) adj -= Math.abs(toNum(v));
    else if (/round/i.test(f.key)) adj += toNum(v);
  }
  const near = (a, b) => Math.abs(a - b) < 0.015;
  const ok = near(sum, total) || near(sum + adj, total) || (subtotal !== null && near(sum, subtotal));
  box.classList.remove("hidden");
  box.classList.toggle("bad", !ok);
  box.querySelector("use").setAttribute("href", ok ? "#i-check" : "#i-alert");
  box.querySelector("span").textContent = ok ? t("checkOk", { t: total.toFixed(2) }) : t("checkBad", { s: sum.toFixed(2), t: total.toFixed(2) });
}

function renderGrids() {
  const tb = RV.draft, ed = RV.editing;
  // Read-only (not disabled) outside edit mode, so text can still be selected and copied.
  const ro = ed ? "" : "readonly";
  $("#tTitle").readOnly = !ed;
  $("#fieldsGrid").innerHTML = tb.fields.map((f, i) => `<div class="fcell">
      <input class="k" data-f="${i}" data-k="key" value="${esc(f.key)}" ${ro} aria-label="${esc(t("key"))}">
      <input class="v" data-f="${i}" data-k="value" value="${esc(f.value)}" ${ro} aria-label="${esc(f.key || t("value"))}">
      <button class="x edit-only" data-delf="${i}" title="${esc(t("del"))}">${ico("x")}</button></div>`).join("");
  $("#fieldsGrid").classList.toggle("hidden", !tb.fields.length && !ed);
  const numeric = tb.columns.map((_, j) => {
    const v = tb.rows.map((r) => String(r[j] ?? "").trim()).filter(Boolean);
    return v.length > 0 && v.filter((x) => NUM.test(x)).length / v.length > 0.7;
  });
  const width = (j) => {
    const vis = (s) => [...String(s || "")].reduce((n, ch) => n + (ch.charCodeAt(0) > 0x2e80 ? 2 : 1), 0);
    const m = Math.max(vis(tb.columns[j]), ...tb.rows.map((r) => vis(r[j])));
    return Math.min(42, Math.max(numeric[j] ? 5 : 6, m + 2));
  };
  $("#itemsGrid").innerHTML =
    `<tr><th class="rn">#</th>${tb.columns.map((c, j) => `<th style="min-width:${width(j)}ch"><div class="th"><input data-col="${j}" value="${esc(c)}" ${ro}><button class="x edit-only" data-delc="${j}" title="${esc(t("del"))}">${ico("x")}</button></div></th>`).join("")}<th class="edit-only"></th></tr>` +
    tb.rows.map((r, i) => `<tr><td class="rn">${i + 1}</td>${r.map((v, j) => `<td class="${numeric[j] ? "num" : ""}"><input data-r="${i}" data-c="${j}" value="${esc(v)}" ${ro}></td>`).join("")}<td class="edit-only"><button class="x" data-delr="${i}" title="${esc(t("del"))}">${ico("x")}</button></td></tr>`).join("");
}

function snapshot() {
  RV.undo.push(clone(RV.draft));
  if (RV.undo.length > 200) RV.undo.shift();
  $("#rvUndo").disabled = false;
}

// One undo step per field: the first change after a field gets focus saves the table as it was.
let lastEdited = null;
$("#tabTable").addEventListener("focusin", () => { lastEdited = null; });
$("#tabTable").addEventListener("input", (e) => {
  if (!RV.editing) return;
  const el = e.target;
  if (el !== lastEdited) { snapshot(); lastEdited = el; }
  const tb = RV.draft;
  if (el.id === "tTitle") tb.title = el.value;
  else if (el.dataset.f !== undefined) tb.fields[+el.dataset.f][el.dataset.k] = el.value;
  else if (el.dataset.col !== undefined) tb.columns[+el.dataset.col] = el.value;
  else if (el.dataset.r !== undefined) tb.rows[+el.dataset.r][+el.dataset.c] = el.value;
  renderCheck();
});
$("#tabTable").addEventListener("click", (e) => {
  if (!RV.editing) return;
  const b = e.target.closest("button.x");
  if (!b) return;
  const tb = RV.draft;
  if (b.dataset.delf !== undefined) { snapshot(); tb.fields.splice(+b.dataset.delf, 1); }
  else if (b.dataset.delr !== undefined) { snapshot(); tb.rows.splice(+b.dataset.delr, 1); }
  else if (b.dataset.delc !== undefined) {
    if (tb.columns.length <= 1) return;
    snapshot(); const j = +b.dataset.delc; tb.columns.splice(j, 1); tb.rows.forEach((r) => r.splice(j, 1));
  } else return;
  renderGrids();
  renderCheck();
});
// Enter moves down a row in the item grid, like a spreadsheet.
$("#itemsGrid").addEventListener("keydown", (e) => {
  if (e.key !== "Enter" || e.ctrlKey || e.metaKey || e.target.dataset.r === undefined) return;
  e.preventDefault();
  const r = +e.target.dataset.r + (e.shiftKey ? -1 : 1), c = e.target.dataset.c;
  $(`#itemsGrid input[data-r="${r}"][data-c="${c}"]`)?.focus();
});

$("#addField").onclick = () => { snapshot(); RV.draft.fields.push({ key: "", value: "" }); renderGrids(); $(`#fieldsGrid input[data-f="${RV.draft.fields.length - 1}"]`)?.focus(); };
$("#addRow").onclick = () => { snapshot(); RV.draft.rows.push(RV.draft.columns.map(() => "")); renderGrids(); $(`#itemsGrid input[data-r="${RV.draft.rows.length - 1}"][data-c="0"]`)?.focus(); };
$("#addCol").onclick = () => { snapshot(); RV.draft.columns.push(t("newColumn")); RV.draft.rows.forEach((r) => r.push("")); renderGrids(); };

$("#rvEdit").onclick = () => { RV.editing = true; renderReview(); $("#tTitle").focus(); };
$("#rvUndo").onclick = () => { if (RV.undo.length) { RV.draft = RV.undo.pop(); lastEdited = null; renderReview(); } };
$("#rvCancel").onclick = () => { RV.draft = clone(RV.doc.table || RV.draft); RV.undo = []; RV.editing = false; renderReview(); };
// Confirm (saving any edits), then open the next bill waiting for review.
$("#rvConfirm").onclick = async () => {
  if (!RV.doc || RV.doc.status !== "ready") return;
  const btn = $("#rvConfirm");
  btn.disabled = true;
  try {
    const r = await api(`/api/doc/${RV.doc.id}/table`, { method: "PUT", body: JSON.stringify({ table: RV.draft, confirmed: true }) });
    RV.doc.table = r.table; RV.doc.confirmed = true;
    RV.draft = clone(r.table); RV.undo = []; RV.editing = false;
    selected.add(RV.doc.id);
    const nx = nextDoc();
    if (nx) {
      toast(t("confirmedNextMsg"));
      await openReview(nx.id);
    } else {
      toast(t("allReviewed"), 5000);
      renderReview();
    }
  } finally {
    btn.disabled = false;
  }
  refresh();
};
function setRerun(open) {
  $("#rerunMenu").classList.toggle("hidden", !open);
  $("#rvRerun").setAttribute("aria-expanded", open);
}
$("#rvRerun").onclick = (e) => { e.stopPropagation(); setRerun($("#rerunMenu").classList.contains("hidden")); };
async function redo(kind) {
  setRerun(false);
  await api(`/api/doc/${RV.doc.id}/redo`, { method: "POST", body: JSON.stringify({ kind }) });
  toast(t("rereading"), 4000);
  await refresh();
}
$("#rvRedoAi").onclick = () => redo("ai");
$("#rvRedoOcr").onclick = () => redo("full");

const anyModalOpen = () => !$("#settings").classList.contains("hidden") || !$("#dmResult").classList.contains("hidden");
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (!$("#rerunMenu").classList.contains("hidden")) return setRerun(false);
    if (!$("#phonePop").classList.contains("hidden")) return setPhonePop(false);
    if (!$("#dmResult").classList.contains("hidden")) return $("#dmResult").classList.add("hidden");
    if (!$("#settings").classList.contains("hidden")) return $("#settings").classList.add("hidden");
    return;
  }
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && RV.doc && !anyModalOpen() && !$("#review").classList.contains("hidden")) {
    e.preventDefault();
    $("#rvConfirm").click();
    return;
  }
  if ((e.ctrlKey || e.metaKey) && e.key === "z" && RV.editing && document.activeElement?.tagName !== "INPUT") { e.preventDefault(); $("#rvUndo").click(); }
});
$("#rvPages").addEventListener("click", (e) => { const p = e.target.dataset.p; if (p !== undefined) { RV.page = +p; renderReview(); } });
async function rotateDoc(deg) {
  const d = RV.doc;
  if (!d || RV.editing) return;
  if ((d.confirmed || JSON.stringify(RV.draft) !== JSON.stringify(d.table || RV.draft)) && !confirm(t("rotateConfirm"))) return;
  try {
    await api(`/api/doc/${d.id}/rotate`, { method: "POST", body: JSON.stringify({ deg }) });
  } catch (e) {
    return toast(String(e.message || e), 5000);
  }
  toast(t("rotating"), 4000);
  await openReview(d.id, true);   // shows the turned image now; the table follows when re-read
  refresh();
}
$("#rotLeft").onclick = () => rotateDoc(-90);
$("#rotRight").onclick = () => rotateDoc(90);

// Divider between photo and result: drag (or arrow keys) to change the split, double-click to reset.
(() => {
  const split = $("#splitter"), left = $("#review .pane.original"), body = $("#review");
  const KEY = "d2x.split";
  const apply = (pct) => { left.style.width = Math.min(80, Math.max(20, pct)) + "%"; };
  const save = () => { try { localStorage.setItem(KEY, parseFloat(left.style.width)); } catch {} };
  try { const v = parseFloat(localStorage.getItem(KEY)); if (v) apply(v); } catch {}
  split.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    split.setPointerCapture(e.pointerId);
    split.classList.add("dragging");
    body.classList.add("resizing");
    const r = body.getBoundingClientRect();
    const move = (ev) => apply(((ev.clientX - r.left) / r.width) * 100);
    const up = () => {
      split.removeEventListener("pointermove", move);
      split.classList.remove("dragging");
      body.classList.remove("resizing");
      save();
    };
    split.addEventListener("pointermove", move);
    split.addEventListener("pointerup", up, { once: true });
    split.addEventListener("pointercancel", up, { once: true });
  });
  split.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const cur = parseFloat(left.style.width) || 44;
    apply(cur + (e.key === "ArrowLeft" ? -2 : 2));
    save();
  });
  split.addEventListener("dblclick", () => { left.style.width = ""; try { localStorage.removeItem(KEY); } catch {} });
})();

function applyZoom() {
  $("#rvImg").style.width = RV.zoom * 100 + "%";
  $("#zoomLevel").textContent = Math.round(RV.zoom * 100) + "%";
}
$("#zoomIn").onclick = () => { RV.zoom = Math.min(4, RV.zoom * 1.25); applyZoom(); };
$("#zoomOut").onclick = () => { RV.zoom = Math.max(0.3, RV.zoom / 1.25); applyZoom(); };

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

$$(".tab").forEach((b) => b.onclick = () => {
  $$(".tab").forEach((x) => { x.classList.toggle("active", x === b); x.setAttribute("aria-selected", x === b); });
  $("#tabTable").classList.toggle("hidden", b.dataset.tab !== "table");
  $("#tabText").classList.toggle("hidden", b.dataset.tab !== "text");
});

// ---------------- settings ----------------
function showSection(sec) {
  $$(".set-item").forEach((b) => b.classList.toggle("active", b.dataset.sec === sec));
  $$(".set-sec").forEach((s) => s.classList.toggle("hidden", s.dataset.sec !== sec));
}
$$(".set-item").forEach((b) => b.onclick = () => showSection(b.dataset.sec));
function sectionStates(s) {
  const put = (id, text, cls) => { const el = $(id); el.textContent = text; el.className = cls || ""; };
  put("#secAiState", STATE.llm_ready ? t("setOn", { m: STATE.llm_model || "LLM" }) : t("setNoKey"), STATE.llm_ready ? "ok" : "warn");
  put("#secVisionState", STATE.vision_ready ? t("setOn", { m: s.VISION_MODEL || "" }) : t("setVisionOff"), STATE.vision_ready ? "ok" : "");
  put("#secPhotoState", s.PHOTO_ENHANCE === "off" ? t("optEnhanceOffShort") : t("optEnhanceAutoShort"));
  put("#secErpState", STATE.erp_ready ? t("setErpOn") : t("setErpOff"), STATE.erp_ready ? "ok" : "warn");
  put("#secDmState", s.DYNAMOD_WORKBOOK ? t("setDmOn") : t("setDmOff"), s.DYNAMOD_WORKBOOK ? "ok" : "");
  put("#secVerState", STATE.version || "");
}
$("#btnSettings").onclick = async () => {
  const s = await api("/api/settings");
  const dlg = $("#settings");
  dlg.querySelectorAll("[name]").forEach((el) => {
    if (el.type === "password") {
      el.value = "";
      el.placeholder = s[el.name + "_SET"] ? t("keyKeep") : t("keyNotSet");
    } else el.value = s[el.name] ?? "";
  });
  $("#envPath").textContent = s.ENV_PATH;
  const kn = $("#keyNote");
  kn.dataset.i18n = s.KEY_STORAGE === "env" ? "keyNoteEnv" : "keyNote";
  kn.textContent = t(kn.dataset.i18n);
  $("#verCurrent").textContent = STATE.version || "?";
  $("#checkResult").textContent = "";
  $("#erpTestResult").textContent = "";
  sectionStates(s);
  showSection("ai");
  dlg.classList.remove("hidden");
};
$("#setCancel").onclick = () => $("#settings").classList.add("hidden");
$("#settings").addEventListener("click", (e) => { if (e.target.id === "settings") $("#settings").classList.add("hidden"); });
$("#dmResult").addEventListener("click", (e) => { if (e.target.id === "dmResult") $("#dmResult").classList.add("hidden"); });
$("#setSave").onclick = async () => {
  const body = {};
  $("#settings").querySelectorAll("[name]").forEach((el) => (body[el.name] = el.value));
  try {
    await api("/api/settings", { method: "POST", body: JSON.stringify(body) });
  } catch (e) {
    if (e.data?.error === "key_required" || e.data?.error === "bad_model") {
      toast(t(e.data.error === "bad_model" ? "badModel" : "keyRequired"), 9000);
      const field = $(`#settings [name="${e.data.field}"]`);
      if (field) { showSection(field.closest(".set-sec").dataset.sec); field.focus(); }
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
  out.className = "hint";
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
