// docparse Studio - vanilla JS single-page app (no build step).
const $ = (s, el = document) => el.querySelector(s);
const main = $("#main");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const h = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; };
const color = (type) => `var(--t-${type}, var(--muted))`;
const FURN = new Set(["Header", "Footer", "PageBreak"]);
const TYPES = ["Title", "NarrativeText", "ListItem", "Table", "TableChunk", "Image", "FigureCaption", "CodeSnippet", "Text",
  "UncategorizedText", "Address", "EmailAddress", "Formula", "Header", "Footer"];
const ICON = {
  list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
  page: '<path d="M14 3H6a1 1 0 00-1 1v16a1 1 0 001 1h12a1 1 0 001-1V8z"/><path d="M14 3v5h5M8 13h8M8 17h5"/>',
  tree: '<path d="M4 5h6M4 5v14h6M4 12h6M14 5h6M14 12h6M14 19h6"/>',
  table: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M3 15h18M9 4v16M15 4v16"/>',
  chunk: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  check: '<path d="M9 12l2 2 4-4"/><path d="M12 3l7 4v5c0 4.5-3 8-7 9-4-1-7-4.5-7-9V7z"/>',
  compare: '<path d="M8 3v18M16 3v18M3 8h5M16 16h5M3 16h5M16 8h5"/>',
  download: '<path d="M12 4v12m0 0l-4-4m4 4l4-4M4 20h16"/>',
};
const TABS = [["elements", "Elements", "list"], ["pages", "Pages", "page"], ["outline", "Outline", "tree"], ["tables", "Tables", "table"],
  ["chunks", "Chunks", "chunk"], ["validate", "Validate", "check"], ["compare", "Compare", "compare"], ["export", "Export", "download"]];

let caps = { compare: false, ocr: false };
let doc = null;            // {meta, result, reviews}
let ui = { filter: new Set(), q: "", limit: 200, page: 1, showTypes: null, review: null };
let pollTimer = null;

// ------------------------------------------------------------------ api
async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return r.headers.get("content-type")?.includes("json") ? r.json() : r;
}
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), 2600);
}

// ------------------------------------------------------------------ theme
(function theme() {
  let saved = null;
  try { saved = localStorage.getItem("theme"); } catch {}
  if (saved) document.documentElement.dataset.theme = saved;
  $("#themeBtn").onclick = () => {
    const cur = document.documentElement.dataset.theme ||
      (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("theme", next); } catch {}
  };
})();

// ------------------------------------------------------------------ router
window.addEventListener("hashchange", route);
$("#backBtn").onclick = () => { location.hash = "#/"; };
async function route() {
  clearTimeout(pollTimer);
  closeSheet();
  const parts = location.hash.replace(/^#\/?/, "").split("/");
  if (parts[0] === "doc" && parts[1]) return showDoc(parts[1], parts[2] || "elements");
  return showHome();
}

// ------------------------------------------------------------------ home
async function showHome() {
  doc = null;
  $("#backBtn").hidden = true; $("#brand").hidden = false; $("#docTitle").hidden = true; $("#tabbar").hidden = true;
  main.replaceChildren($("#tpl-home").content.cloneNode(true));
  const drop = $("#drop"), input = $("#fileInput");
  $("#pickBtn").onclick = () => input.click();
  $("#camBtn").onclick = () => $("#cameraInput").click();
  input.onchange = () => input.files[0] && upload(input.files[0]);
  $("#cameraInput").onchange = (e) => e.target.files[0] && upload(e.target.files[0]);
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("drag"); }));
  drop.addEventListener("drop", (e) => e.dataTransfer.files[0] && upload(e.dataTransfer.files[0]));
  loadRecent();
}

async function loadRecent() {
  const box = $("#recent");
  if (!box) return;
  let docs = [];
  try { docs = await api("/api/docs"); } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  $("#recentCount").textContent = docs.length ? `${docs.length} document${docs.length > 1 ? "s" : ""}` : "";
  if (!docs.length) { box.innerHTML = `<div class="empty card">Nothing parsed yet. Upload a document to start.</div>`; return; }
  box.replaceChildren(...docs.map((d) => {
    const ext = (d.filename.split(".").pop() || d.kind || "").slice(0, 4);
    const right = d.status === "done" ? ring(d.score) : d.status === "error" ? '<span class="badge" style="--c:var(--fail)">failed</span>' : '<div class="spinner"></div>';
    const sub = d.status === "done" ? `${d.pages ?? "–"} pages · ${d.elements} elements · ${d.seconds}s` : esc(d.progress || d.status);
    const row = h(`<div class="card doc-row"><div class="doc-ico">${esc(ext)}</div><div class="doc-main"><div class="doc-name">${esc(d.filename)}</div>
      <div class="doc-sub">${sub}</div></div>${right}</div>`);
    row.onclick = () => { location.hash = `#/doc/${d.id}`; };
    return row;
  }));
  if (docs.some((d) => d.status === "queued" || d.status === "parsing")) pollTimer = setTimeout(loadRecent, 1500);
}

function ring(v, big = false) {
  const c = v >= 80 ? "var(--pass)" : v >= 55 ? "var(--warn)" : "var(--fail)";
  return `<div class="ring${big ? " big" : ""}" style="--v:${v};--c:${c}" data-v="${v}" title="Parse health ${v}/100"></div>`;
}

async function upload(file) {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("ocr", $("#optOcr")?.value || "auto");
  fd.append("tables", $("#optTables")?.checked ? "true" : "false");
  fd.append("languages", $("#optLang")?.value || "");
  toast(`Uploading ${file.name}…`);
  try {
    const m = await api("/api/docs", { method: "POST", body: fd });
    location.hash = `#/doc/${m.id}`;
  } catch (e) { toast(`Upload failed: ${e.message}`); }
}

// ------------------------------------------------------------------ document
async function showDoc(id, tab) {
  $("#backBtn").hidden = false; $("#brand").hidden = true; $("#docTitle").hidden = false;
  if (!doc || doc.meta.id !== id || doc.meta.status !== "done") {
    try { doc = await api(`/api/docs/${id}`); } catch (e) { main.innerHTML = `<div class="card err">${esc(e.message)}</div>`; return; }
    ui = { filter: new Set(), q: "", limit: 200, page: 1, showTypes: null, review: null };
  }
  $("#docTitle").textContent = doc.meta.filename;
  if (doc.meta.status !== "done") return showProcessing(id);
  renderTabs(id, tab);
  const view = { elements: viewElements, pages: viewPages, outline: viewOutline, tables: viewTables, chunks: viewChunks,
    validate: viewValidate, compare: viewCompare, export: viewExport }[tab] || viewElements;
  main.replaceChildren(docHeader());
  view();
  window.scrollTo({ top: 0 });
}

function showProcessing(id) {
  $("#tabbar").hidden = true;
  const m = doc.meta;
  if (m.status === "error") {
    main.innerHTML = `<div class="card err"><h3>Parsing failed</h3><p>${esc(m.error)}</p></div>`;
    return;
  }
  main.innerHTML = `<div class="processing"><div class="spinner"></div><h2>${esc(m.progress || "Working…")}</h2>
    <p class="muted">${m.kind === "image" ? "Running OCR on your photo." : "Large or scanned documents can take a minute."}</p></div>`;
  pollTimer = setTimeout(async () => { doc = null; showDoc(id, "elements"); }, 1200);
}

function renderTabs(id, active) {
  const bar = $("#tabbar");
  bar.hidden = false;
  bar.replaceChildren(...TABS.map(([k, label, ic]) => {
    const b = h(`<button class="tab${k === active ? " on" : ""}"><svg viewBox="0 0 24 24">${ICON[ic]}</svg>${label}</button>`);
    b.onclick = () => { location.hash = `#/doc/${id}/${k}`; };
    return b;
  }));
  bar.querySelector(".on")?.scrollIntoView({ inline: "center", block: "nearest" });
}

function docHeader() {
  const m = doc.meta, v = doc.result.validation;
  const reviewed = Object.keys(doc.reviews || {}).length;
  return h(`<div class="doc-head">${ring(v.score)}<div><div class="stats">
    <span class="stat"><b>${m.pages ?? "–"}</b> pages</span><span class="stat"><b>${m.elements}</b> elements</span>
    <span class="stat"><b>${m.seconds}</b>s</span>${reviewed ? `<span class="stat"><b>${reviewed}</b> reviewed</span>` : ""}
    ${m.kind === "image" ? '<span class="stat">OCR</span>' : ""}</div></div></div>`);
}

const els = () => doc.result.elements;
const byId = () => (doc._byId ||= Object.fromEntries(els().map((e) => [e.element_id, e])));

// ---------- Elements
function viewElements() {
  const all = els();
  const counts = {};
  all.forEach((e) => (counts[e.type] = (counts[e.type] || 0) + 1));
  const bar = h(`<div><div class="toolbar"><input type="search" placeholder="Search text…" value="${esc(ui.q)}" aria-label="Search"></div><div class="chips"></div></div>`);
  const chips = bar.querySelector(".chips");
  Object.entries(counts).sort((a, b) => b[1] - a[1]).forEach(([t, n]) => {
    const c = h(`<button class="chip${ui.filter.has(t) ? " on" : ""}" style="--c:${color(t)}"><span class="dot"></span>${t}<span class="count">${n}</span></button>`);
    c.onclick = () => { ui.filter.has(t) ? ui.filter.delete(t) : ui.filter.add(t); ui.limit = 200; c.classList.toggle("on"); draw(); };
    chips.append(c);
  });
  const list = h(`<div class="els"></div>`);
  const more = h(`<div class="more"></div>`);
  main.append(bar, list, more);
  const search = bar.querySelector("input");
  search.oninput = () => { ui.q = search.value; ui.limit = 200; draw(); };
  function draw() {
    const q = ui.q.trim().toLowerCase();
    const items = all.filter((e) => (!ui.filter.size || ui.filter.has(e.type)) && (!q || e.text.toLowerCase().includes(q)));
    list.replaceChildren(...items.slice(0, ui.limit).map(elCard));
    more.replaceChildren();
    if (!items.length) list.innerHTML = `<div class="empty">No matching elements.</div>`;
    if (items.length > ui.limit) {
      const b = h(`<button class="btn">Show more (${items.length - ui.limit} left)</button>`);
      b.onclick = () => { ui.limit += 400; draw(); };
      more.append(b);
    }
  }
  draw();
}

function elCard(e) {
  const md = e.metadata, r = doc.reviews?.[e.element_id];
  const depth = md.category_depth != null && e.type === "Title" ? ` · L${md.category_depth + 1}` : md.category_depth ? ` · depth ${md.category_depth}` : "";
  const text = e.type === "Image" ? (e.text || "[image]") : e.text;
  const card = h(`<div class="el${e.type === "Title" ? " title" : ""}" style="--c:${color(e.type)}">
    <div class="el-meta"><span class="badge">${e.type}</span>${md.page_number ? `<span>p.${md.page_number}</span>` : ""}<span>${depth.replace(/^ · /, "")}</span>
    ${r ? `<span class="rv ${r.verdict === "wrong" || r.corrected_type ? "wrong" : "ok"}">${r.verdict === "ok" && !r.corrected_type ? "✓" : "✗"}</span>` : ""}</div>
    <div class="el-text">${esc(text)}</div></div>`);
  card.onclick = () => openElement(e);
  return card;
}

// ---------- element sheet
function openElement(e) {
  const md = e.metadata, r = doc.reviews?.[e.element_id] || {};
  const parent = md.parent_id && byId()[md.parent_id];
  const body = $("#sheetBody");
  const typeOpts = TYPES.map((t) => `<option ${t === (r.corrected_type || e.type) ? "selected" : ""}>${t}</option>`).join("");
  body.innerHTML = `<div class="el-meta"><span class="badge" style="--c:${color(e.type)}">${e.type}</span>${md.page_number ? `<span>page ${md.page_number}</span>` : ""}</div>
    ${md.text_as_html ? `<div class="tbl-scroll card" style="padding:0;margin:8px 0">${md.text_as_html}</div>` : `<div class="full-text">${esc(e.text || "[image]")}</div>`}
    <h3 style="margin-top:16px">Is this right?</h3>
    <div class="btn-row"><button class="btn ok ${r.verdict === "ok" ? "on" : ""}" data-v="ok">✓ Correct</button><button class="btn bad ${r.verdict === "wrong" ? "on" : ""}" data-v="wrong">✗ Wrong</button>
    ${md.coordinates ? '<button class="btn" id="goPage">Show on page</button>' : ""}</div>
    <label class="row">Correct type <select id="relabel">${typeOpts}</select></label>
    <label class="row" style="align-items:flex-start">Note <textarea id="note" rows="2" style="flex:1" placeholder="What's wrong?">${esc(r.note || "")}</textarea></label>
    <details><summary class="muted">Metadata</summary><dl class="kv">
      <dt>element_id</dt><dd class="mono">${e.element_id}</dd>
      ${parent ? `<dt>parent</dt><dd><a href="#" id="goParent">${esc(parent.type)}: ${esc(parent.text.slice(0, 60))}</a></dd>` : ""}
      ${Object.entries(md).filter(([k]) => !["text_as_html", "coordinates", "orig_elements"].includes(k))
        .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(typeof v === "object" ? JSON.stringify(v) : v)}</dd>`).join("")}
    </dl></details>`;
  body.querySelectorAll("[data-v]").forEach((b) => (b.onclick = () => saveReview(e, { verdict: r.verdict === b.dataset.v ? null : b.dataset.v })));
  $("#relabel", body).onchange = (ev) => saveReview(e, { corrected_type: ev.target.value === e.type ? null : ev.target.value, verdict: ev.target.value === e.type ? r.verdict : "wrong" });
  $("#note", body).onchange = (ev) => saveReview(e, { note: ev.target.value });
  $("#goPage", body)?.addEventListener("click", () => { ui.page = md.page_number; ui.highlight = e.element_id; location.hash = `#/doc/${doc.meta.id}/pages`; });
  $("#goParent", body)?.addEventListener("click", (ev) => { ev.preventDefault(); openElement(parent); });
  openSheet();
}

async function saveReview(e, patch) {
  const cur = doc.reviews?.[e.element_id] || {};
  const next = { element_id: e.element_id, verdict: cur.verdict ?? null, corrected_type: cur.corrected_type ?? null, note: cur.note ?? null, ...patch };
  try {
    doc.reviews = await api(`/api/docs/${doc.meta.id}/reviews`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(next) });
    if (!ui.review) openElement(e);
    toast("Review saved");
  } catch (err) { toast(err.message); }
}

function openSheet() { $("#sheet").hidden = false; $("#sheetBackdrop").hidden = false; }
function closeSheet() { $("#sheet").hidden = true; $("#sheetBackdrop").hidden = true; }
$("#sheetBackdrop").onclick = closeSheet;
document.addEventListener("keydown", (e) => e.key === "Escape" && closeSheet());
(function swipeToClose() {
  let y0 = null; const s = $("#sheet");
  s.addEventListener("touchstart", (e) => { if ($("#sheetBody").scrollTop <= 0) y0 = e.touches[0].clientY; }, { passive: true });
  s.addEventListener("touchend", (e) => { if (y0 != null && e.changedTouches[0].clientY - y0 > 80) closeSheet(); y0 = null; });
})();

// ---------- Pages
function viewPages() {
  if (!doc.meta.is_pdf) {
    main.append(h(`<div class="card empty">Page view shows PDFs and photos. This ${esc(doc.meta.kind)} file has no fixed pages, so use the Elements and Outline tabs.</div>`));
    return;
  }
  const n = doc.meta.pages || 1;
  ui.page = Math.min(Math.max(1, ui.page || 1), n);
  const types = [...new Set(els().filter((e) => e.metadata.coordinates).map((e) => e.type))];
  ui.showTypes ||= new Set(types);
  const pager = h(`<div class="pager"><button class="btn small" id="prev">‹</button><select id="pageSel">${Array.from({ length: n }, (_, i) =>
    `<option value="${i + 1}" ${i + 1 === ui.page ? "selected" : ""}>Page ${i + 1} / ${n}</option>`).join("")}</select><button class="btn small" id="next">›</button></div>`);
  const legend = h(`<div class="legend"></div>`);
  types.forEach((t) => {
    const c = h(`<button class="chip${ui.showTypes.has(t) ? " on" : ""}" style="--c:${color(t)}"><span class="dot"></span>${t}</button>`);
    c.onclick = () => { ui.showTypes.has(t) ? ui.showTypes.delete(t) : ui.showTypes.add(t); c.classList.toggle("on"); draw(); };
    legend.append(c);
  });
  ui.zoom ||= 1; ui.labels ??= true;
  const tools = h(`<div class="pager"><button class="btn small" id="zout" aria-label="Zoom out">−</button><span class="muted" id="zv"></span>
    <button class="btn small" id="zin" aria-label="Zoom in">+</button><button class="chip${ui.labels ? " on" : ""}" id="lbl">Labels</button></div>`);
  const scroller = h(`<div class="page-scroll"></div>`);
  const wrap = h(`<div class="page-wrap${ui.labels ? "" : " nolabels"}"><img alt="Rendered page"></div>`);
  scroller.append(wrap);
  main.append(pager, legend, tools, scroller);
  const zoom = (z) => { ui.zoom = Math.min(4, Math.max(1, z)); wrap.style.width = ui.zoom * 100 + "%"; $("#zv").textContent = Math.round(ui.zoom * 100) + "%"; };
  $("#zin").onclick = () => zoom(ui.zoom + 0.5); $("#zout").onclick = () => zoom(ui.zoom - 0.5);
  $("#lbl").onclick = () => { ui.labels = !ui.labels; $("#lbl").classList.toggle("on", ui.labels); wrap.classList.toggle("nolabels", !ui.labels); };
  zoom(ui.zoom);
  const go = (p) => { ui.page = Math.min(Math.max(1, p), n); $("#pageSel").value = ui.page; draw(); };
  $("#prev").onclick = () => go(ui.page - 1); $("#next").onclick = () => go(ui.page + 1);
  $("#pageSel").onchange = (e) => go(+e.target.value);
  let x0 = null;
  wrap.addEventListener("touchstart", (e) => { if (e.touches.length === 1 && ui.zoom === 1) x0 = e.touches[0].clientX; }, { passive: true });
  wrap.addEventListener("touchend", (e) => { if (x0 == null) return; const dx = e.changedTouches[0].clientX - x0; if (Math.abs(dx) > 70) go(ui.page + (dx < 0 ? 1 : -1)); x0 = null; });
  function draw() {
    const img = wrap.querySelector("img");
    img.src = `/api/docs/${doc.meta.id}/pages/${ui.page}.png?scale=${devicePixelRatio > 1 ? 2 : 1.5}`;
    wrap.querySelectorAll(".box").forEach((b) => b.remove());
    const sz = doc.result.page_sizes?.[ui.page - 1];
    els().filter((e) => e.metadata.page_number === ui.page && e.metadata.coordinates && ui.showTypes.has(e.type)).forEach((e) => {
      const c = e.metadata.coordinates, W = c.layout_width || sz?.[0], H = c.layout_height || sz?.[1];
      if (!W || !H) return;
      const xs = c.points.map((p) => p[0]), ys = c.points.map((p) => p[1]);
      const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
      const b = h(`<div class="box${ui.highlight === e.element_id ? " hl" : ""}" style="--c:${color(e.type)};left:${(x0 / W) * 100}%;top:${(y0 / H) * 100}%;width:${((x1 - x0) / W) * 100}%;height:${((y1 - y0) / H) * 100}%"
        title="${esc(e.type)}"><span class="box-label">${e.type}</span></div>`);
      b.onclick = (ev) => { ev.stopPropagation(); openElement(e); };
      wrap.append(b);
    });
    if (ui.highlight) setTimeout(() => wrap.querySelector(".hl")?.scrollIntoView({ block: "center", behavior: "smooth" }), 250);
  }
  draw();
}

// ---------- Outline
function viewOutline() {
  const all = els().filter((e) => !FURN.has(e.type));
  const titles = all.filter((e) => e.type === "Title");
  if (!titles.length) { main.append(h(`<div class="card empty">No headings were detected, so there is no outline.</div>`)); return; }
  const kids = {}; const nChildren = {};
  all.forEach((e) => { const p = e.metadata.parent_id || "root"; nChildren[p] = (nChildren[p] || 0) + 1; if (e.type === "Title") (kids[p] ||= []).push(e); });
  const orphans = all.filter((e) => e.type === "Title" && e.metadata.parent_id && !byId()[e.metadata.parent_id]);
  const build = (list) => {
    const ul = h(`<ul class="tree"></ul>`);
    list.forEach((t) => {
      const li = h(`<li><div class="node"><span class="dot" style="--c:${color("Title")}"></span><span>${esc(t.text)}</span>
        <span class="n">${t.metadata.page_number ? "p." + t.metadata.page_number + " · " : ""}${nChildren[t.element_id] || 0}</span></div></li>`);
      li.firstChild.onclick = () => openElement(t);
      if (kids[t.element_id]) li.append(build(kids[t.element_id]));
      ul.append(li);
    });
    return ul;
  };
  const card = h(`<div class="card"></div>`);
  card.append(build([...(kids.root || []), ...orphans]));
  main.append(h(`<p class="muted">${titles.length} headings. Each number counts the elements directly under that heading.</p>`), card);
}

// ---------- Tables
function viewTables() {
  const tables = els().filter((e) => e.type === "Table" || e.type === "TableChunk");
  if (!tables.length) { main.append(h(`<div class="card empty">No tables detected.</div>`)); return; }
  tables.forEach((t, i) => {
    const html = t.metadata.text_as_html || `<table><tr><td>${esc(t.text)}</td></tr></table>`;
    const rows = (html.match(/<tr>/g) || []).length;
    const card = h(`<div class="card tbl-card"><div class="tbl-head"><b>Table ${i + 1}</b><span class="muted">${t.metadata.page_number ? "page " + t.metadata.page_number + " · " : ""}${rows} rows</span>
      <span><button class="btn small" data-a="csv">Copy CSV</button> <button class="btn small" data-a="open">Review</button></span></div><div class="tbl-scroll">${html}</div></div>`);
    card.querySelector('[data-a="csv"]').onclick = async () => {
      const grid = [...card.querySelectorAll("tr")].map((tr) => [...tr.children].map((td) => `"${td.textContent.replace(/"/g, '""')}"`).join(","));
      try { await navigator.clipboard.writeText(grid.join("\n")); toast("Copied as CSV"); } catch { toast("Clipboard not available"); }
    };
    card.querySelector('[data-a="open"]').onclick = () => openElement(t);
    main.append(card);
  });
}

// ---------- Chunks
const chunkCfg = { strategy: "by_title", max_characters: 1000, overlap: 0, combine_text_under_n_chars: 300 };
function viewChunks() {
  const panel = h(`<div class="card"><div class="seg" id="strat"><button data-s="by_title">By title</button><button data-s="basic">Basic</button></div>
    <div class="slider"><label>Max characters <b id="vMax"></b></label><input type="range" id="max" min="200" max="4000" step="50"></div>
    <div class="slider"><label>Overlap <b id="vOv"></b></label><input type="range" id="ov" min="0" max="400" step="10"></div>
    <div class="slider" id="combWrap"><label>Combine sections under <b id="vComb"></b></label><input type="range" id="comb" min="0" max="2000" step="50"></div>
    <div class="hist" id="hist"></div><div class="muted" id="cstats" style="font-size:13px"></div></div>`);
  const list = h(`<div style="margin-top:12px"></div>`);
  main.append(panel, list);
  const set = () => {
    panel.querySelectorAll("#strat button").forEach((b) => b.classList.toggle("on", b.dataset.s === chunkCfg.strategy));
    $("#max").value = chunkCfg.max_characters; $("#vMax").textContent = chunkCfg.max_characters;
    $("#ov").value = chunkCfg.overlap; $("#vOv").textContent = chunkCfg.overlap;
    $("#comb").value = chunkCfg.combine_text_under_n_chars; $("#vComb").textContent = chunkCfg.combine_text_under_n_chars;
    $("#combWrap").hidden = chunkCfg.strategy !== "by_title";
  };
  let timer;
  const run = () => { clearTimeout(timer); timer = setTimeout(load, 250); };
  panel.querySelectorAll("#strat button").forEach((b) => (b.onclick = () => { chunkCfg.strategy = b.dataset.s; set(); run(); }));
  $("#max").oninput = (e) => { chunkCfg.max_characters = +e.target.value; chunkCfg.overlap = Math.min(chunkCfg.overlap, chunkCfg.max_characters - 50); set(); run(); };
  $("#ov").oninput = (e) => { chunkCfg.overlap = Math.min(+e.target.value, chunkCfg.max_characters - 50); set(); run(); };
  $("#comb").oninput = (e) => { chunkCfg.combine_text_under_n_chars = +e.target.value; set(); run(); };
  async function load() {
    try {
      const body = { ...chunkCfg, combine_text_under_n_chars: Math.min(chunkCfg.combine_text_under_n_chars, chunkCfg.max_characters) };
      const r = await api(`/api/docs/${doc.meta.id}/chunks`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
      const bins = Array(24).fill(0);
      r.chunks.forEach((c) => bins[Math.min(23, Math.floor((c.text.length / chunkCfg.max_characters) * 24))]++);
      const mx = Math.max(1, ...bins);
      $("#hist").replaceChildren(...bins.map((b) => h(`<div style="height:${(b / mx) * 100}%" title="${b}"></div>`)));
      $("#cstats").textContent = `${r.stats.count} chunks · avg ${r.stats.avg} · min ${r.stats.min} · max ${r.stats.max} characters (size distribution, 0 → max)`;
      list.replaceChildren(...r.chunks.slice(0, 150).map((c, i) => h(`<div class="card chunk"><div class="chunk-head"><span>#${i + 1} · <span class="badge" style="--c:${color(c.type)}">${c.type}</span>${c.metadata.is_continuation ? " · continued" : ""}</span>
        <span>${c.text.length} chars${c.metadata.page_number ? " · p." + c.metadata.page_number : ""}</span></div><div class="chunk-text">${esc(c.text)}</div></div>`)));
      if (r.chunks.length > 150) list.append(h(`<p class="muted more">Showing the first 150 of ${r.chunks.length} chunks.</p>`));
    } catch (e) { toast(e.message); }
  }
  set(); load();
}

// ---------- Validate
function wilson(k, n, z = 1.96) {
  if (!n) return [0, 0];
  const p = k / n, d = 1 + (z * z) / n, c = p + (z * z) / (2 * n), m = z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n));
  return [Math.max(0, (c - m) / d), Math.min(1, (c + m) / d)];
}
function viewValidate() {
  const v = doc.result.validation, reviews = doc.reviews || {};
  const rv = Object.values(reviews).filter((r) => r.verdict);
  const ok = rv.filter((r) => r.verdict === "ok" && !r.corrected_type).length;
  const [lo, hi] = wilson(ok, rv.length);
  const hero = h(`<div class="card val-hero">${ring(v.score, true)}<div><h2>Parse health</h2>
    <p class="muted" style="margin:4px 0 0">Automatic checks that need no answer key. Below them is your own review sample.</p></div></div>`);
  const kp = h(`<div class="kpis">
    <div class="card kpi"><div class="v">${rv.length}</div><div class="l">elements reviewed by you</div></div>
    <div class="card kpi"><div class="v">${rv.length ? Math.round((ok / rv.length) * 100) + "%" : "–"}</div><div class="l">judged correct${rv.length ? ` (95% CI ${Math.round(lo * 100)}–${Math.round(hi * 100)}%)` : ""}</div></div>
    <div class="card kpi"><div class="v">${Object.values(reviews).filter((r) => r.corrected_type).length}</div><div class="l">type corrections</div></div></div>`);
  const start = h(`<div class="btn-row"><button class="btn primary" id="startReview">Review a random sample of 20</button>
    <a class="btn" href="/api/docs/${doc.meta.id}/export?fmt=reviews">Export reviews</a></div>`);
  const list = h(`<div class="checks"></div>`);
  const sym = { pass: "✓", warn: "!", fail: "✗", info: "i" };
  v.checks.forEach((c) => list.append(h(`<div class="card check"><div class="ci ${c.status}">${sym[c.status]}</div><div><h3>${esc(c.label)}</h3><p>${esc(c.detail)}</p></div></div>`)));
  const tc = v.type_counts, total = Object.values(tc).reduce((a, b) => a + b, 0);
  const dist = h(`<div class="card" style="margin-top:12px"><h3 style="font-size:15px;margin-bottom:8px">Element types</h3><div class="bar">${Object.entries(tc)
    .map(([t, n]) => `<div style="width:${(n / total) * 100}%;background:${color(t)}" title="${t}: ${n}"></div>`).join("")}</div>
    <div class="legend">${Object.entries(tc).map(([t, n]) => `<span class="chip" style="--c:${color(t)}"><span class="dot"></span>${t}<span class="count">${n}</span></span>`).join("")}</div></div>`);
  main.append(hero, kp, start, list, dist);
  $("#startReview").onclick = startReview;
}

function startReview() {
  const pool = els().filter((e) => !FURN.has(e.type) && !doc.reviews?.[e.element_id]?.verdict);
  if (!pool.length) { toast("Everything is already reviewed"); return; }
  const sample = [];
  const copy = [...pool];
  while (sample.length < Math.min(20, copy.length)) sample.push(copy.splice(Math.floor(Math.random() * copy.length), 1)[0]);
  ui.review = { items: sample, i: 0 };
  drawReview();
}
function drawReview() {
  const R = ui.review, e = R.items[R.i];
  if (!e) { ui.review = null; closeSheet(); toast("Sample done: thanks!"); viewValidateRefresh(); return; }
  const md = e.metadata;
  $("#sheetBody").innerHTML = `<div class="progress"><div style="width:${(R.i / R.items.length) * 100}%"></div></div>
    <p class="muted" style="font-size:13px">${R.i + 1} of ${R.items.length}${md.page_number ? " · page " + md.page_number : ""}</p>
    <div class="review-card"><div><span class="badge" style="--c:${color(e.type)};font-size:15px">${e.type}</span></div>
    ${md.text_as_html ? `<div class="tbl-scroll">${md.text_as_html}</div>` : `<div class="txt">${esc(e.text || "[image]")}</div>`}
    <p class="muted" style="margin:0;font-size:13px">Is the text complete, and is <b>${e.type}</b> the right type?</p>
    <div class="review-actions"><button class="btn bad" id="rvBad">✗ Wrong</button><button class="btn ok" id="rvOk">✓ Correct</button></div>
    <div class="btn-row" style="margin:0"><button class="btn small" id="rvSkip">Skip</button>${md.coordinates ? '<button class="btn small" id="rvPage">See on page</button>' : ""}<button class="btn small" id="rvStop">Stop</button></div></div>`;
  const next = () => { R.i++; drawReview(); };
  $("#rvOk").onclick = async () => { await saveReview(e, { verdict: "ok" }); next(); };
  $("#rvBad").onclick = async () => { await saveReview(e, { verdict: "wrong" }); next(); };
  $("#rvSkip").onclick = next;
  $("#rvStop").onclick = () => { ui.review = null; closeSheet(); viewValidateRefresh(); };
  $("#rvPage")?.addEventListener("click", () => { ui.review = null; ui.page = md.page_number; ui.highlight = e.element_id; location.hash = `#/doc/${doc.meta.id}/pages`; });
  openSheet();
}
function viewValidateRefresh() { if (location.hash.endsWith("/validate")) { main.replaceChildren(docHeader()); viewValidate(); } }

// ---------- Compare
function viewCompare() {
  if (!caps.compare) {
    main.append(h(`<div class="card empty">Side-by-side comparison with <b>unstructured</b> is not enabled on this server.<br>
      Start the server with <span class="mono">UNSTRUCTURED_PYTHON=/path/to/python</span> pointing at an interpreter that has unstructured installed.</div>`));
    return;
  }
  const box = h(`<div class="card"><h3>docparse vs. unstructured</h3><p class="muted">Runs the open-source <b>unstructured</b> library (fast/ocr_only strategy) on the same file and lines up the two outputs element by element.</p>
    <button class="btn primary" id="runCmp">Run comparison</button></div>`);
  const out = h(`<div></div>`);
  main.append(box, out);
  $("#runCmp").onclick = async () => {
    $("#runCmp").disabled = true; $("#runCmp").innerHTML = '<div class="spinner" style="width:18px;height:18px"></div> Running unstructured…';
    try { drawCmp(await api(`/api/docs/${doc.meta.id}/compare`, { method: "POST" })); box.remove(); }
    catch (e) { toast(e.message); $("#runCmp").disabled = false; $("#runCmp").textContent = "Run comparison"; }
  };
  function drawCmp(r) {
    const s = r.summary;
    const aligned = s.aligned_same_type + s.aligned_different_type;
    out.append(h(`<div class="kpis">
      <div class="card kpi"><div class="v">${Math.round(s.word_agreement * 100)}%</div><div class="l">word-level agreement</div></div>
      <div class="card kpi"><div class="v">${aligned ? Math.round((s.aligned_same_type / aligned) * 100) : 0}%</div><div class="l">same type on ${aligned} matched blocks</div></div>
      <div class="card kpi"><div class="v">${s.only_docparse} / ${s.only_unstructured}</div><div class="l">blocks only in docparse / only in unstructured</div></div>
      <div class="card kpi"><div class="v">${r.docparse_seconds}s / ${r.unstructured.seconds.toFixed(1)}s</div><div class="l">time: docparse / unstructured</div></div></div>`));
    const types = [...new Set([...Object.keys(s.types_docparse), ...Object.keys(s.types_unstructured)])];
    const mx = Math.max(1, ...types.map((t) => Math.max(s.types_docparse[t] || 0, s.types_unstructured[t] || 0)));
    out.append(h(`<div class="card"><h3 style="font-size:15px;margin-bottom:10px">Element types <span class="muted" style="font-weight:400">(top bar docparse, bottom bar unstructured)</span></h3><div class="cmp-bars">${types.map((t) =>
      `<div class="cmp-row"><span>${t}</span><div class="two"><div class="b" style="width:${((s.types_docparse[t] || 0) / mx) * 100}%;background:var(--accent)" title="docparse ${s.types_docparse[t] || 0}"></div>
       <div class="b" style="width:${((s.types_unstructured[t] || 0) / mx) * 100}%;background:var(--accent-2)" title="unstructured ${s.types_unstructured[t] || 0}"></div></div></div>`).join("")}</div></div>`));
    if (s.type_disagreements.length) {
      out.append(h(`<div class="card" style="margin-top:12px"><h3 style="font-size:15px">Where they disagree on type</h3>${s.type_disagreements.map((d) =>
        `<div style="padding:10px 0;border-bottom:1px solid var(--border)"><div class="el-meta"><span class="badge" style="--c:${color(d.docparse)}">docparse: ${d.docparse}</span><span class="badge" style="--c:${color(d.unstructured)}">unstructured: ${d.unstructured}</span></div><div class="el-text">${esc(d.text)}</div></div>`).join("")}</div>`));
    }
  }
}

// ---------- Export
function viewExport() {
  const id = doc.meta.id;
  const opts = [["json", "JSON", "unstructured-compatible element list"], ["jsonl", "JSONL", "one element per line"],
    ["csv", "CSV", "spreadsheet-friendly"], ["markdown", "Markdown", "headings, lists, tables"],
    ["bigquery", "BigQuery rows", "NDJSON matching the BigQuery schema"], ["reviews", "Your reviews", "labels as an answer key"]];
  const grid = h(`<div class="export-grid"></div>`);
  opts.forEach(([f, label, desc]) => grid.append(h(`<a class="btn" href="/api/docs/${id}/export?fmt=${f}"><b>${label}</b><small>${desc}</small></a>`)));
  const actions = h(`<div class="btn-row" style="margin-top:16px"><a class="btn" href="/api/docs/${id}/file">Original file</a>
    <button class="btn" id="share">Share link</button><button class="btn" id="reparse">Re-parse with OCR</button><button class="btn bad" id="del">Delete</button></div>`);
  main.append(grid, actions);
  $("#share").onclick = async () => {
    const url = location.href;
    try { if (navigator.share) await navigator.share({ title: doc.meta.filename, url }); else { await navigator.clipboard.writeText(url); toast("Link copied"); } } catch {}
  };
  $("#reparse").onclick = async () => {
    const fd = new FormData(); fd.append("ocr", "always"); fd.append("tables", "true");
    await api(`/api/docs/${id}/reparse`, { method: "POST", body: fd }); doc = null; route();
  };
  $("#del").onclick = async () => { if (confirm("Delete this document and its results?")) { await api(`/api/docs/${id}`, { method: "DELETE" }); location.hash = "#/"; } };
}

// ------------------------------------------------------------------ boot
api("/api/capabilities").then((c) => (caps = c)).catch(() => {});
if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
route();
