"use strict";

const PAGE_SIZE = 100;
const SAVED_KEY = "phmpt-saved-searches-v1";

// URL hash <-> control id. Clear, self-describing param names.
const FIELD_PARAMS = {
  "f-name":       "name",
  "f-company":    "company",
  "f-license":    "license",
  "f-age":        "age",
  "f-individual": "availability",
  "f-pages-min":  "pages_min",
  "f-pages-max":  "pages_max",
  "f-bates":      "bates",
};

const state = {
  rows: [],
  filtered: [],
  mode: "filter",   // "filter" (table) | "search" (full-text results)
  searchQuery: "",  // active full-text query (search mode), for URL state
  page: 0,
  sortKey: "filename",
  sortDir: 1,
  extSelected: new Set(),
  modSelected: new Set(),
  // Set to true while we're loading state from URL/saved-search,
  // so applyFilters() doesn't echo back into history.
  applyingExternal: false,
};

const $ = (id) => document.getElementById(id);

function fmtBytes(n) {
  if (n == null) return "";
  if (n < 1024) return n + " B";
  const units = ["KB", "MB", "GB", "TB"];
  let i = -1;
  do { n /= 1024; i++; } while (n >= 1024 && i < units.length - 1);
  return n.toFixed(n >= 100 ? 0 : 1) + " " + units[i];
}

function fmtNum(n) {
  return n == null ? "" : n.toLocaleString();
}

function fmtDate(iso) {
  if (!iso) return "";
  return iso.slice(0, 10);
}

function tag(cls, text) {
  const el = document.createElement("span");
  el.className = "tag " + cls;
  el.textContent = text;
  return el;
}

function compare(a, b, key) {
  const av = a[key], bv = b[key];
  if (av == null && bv == null) return 0;
  if (av == null) return 1;
  if (bv == null) return -1;
  if (typeof av === "number" && typeof bv === "number") return av - bv;
  return String(av).localeCompare(String(bv));
}

function applyFilters() {
  // In search mode the table is hidden; a filter change should re-query the
  // search backend instead of refiltering the (hidden) table.
  if (state.mode === "search") { if (window.ftRerun) window.ftRerun(); return; }
  const name = $("f-name").value.trim().toLowerCase();
  const company = $("f-company").value;
  const license = $("f-license").value;
  const age = $("f-age").value;
  const individual = $("f-individual").value;
  const pMinV = $("f-pages-min").value;
  const pMaxV = $("f-pages-max").value;
  const pMin = pMinV === "" ? null : Number(pMinV);
  const pMax = pMaxV === "" ? null : Number(pMaxV);
  const batesV = $("f-bates").value;
  const bates = batesV === "" ? null : Number(batesV);
  const exts = state.extSelected;
  const mods = state.modSelected;

  state.filtered = state.rows.filter((r) => {
    if (name && !r.filename.toLowerCase().includes(name)) return false;
    if (company && r.company !== company) return false;
    if (license && r.license !== license) return false;
    if (age && r.age_group !== age) return false;
    if (individual === "both"  && !(r.zip_source && (r.individual_url || r.hosted_url))) return false;
    if (individual === "zip"   && !(r.zip_source && !(r.individual_url || r.hosted_url))) return false;
    if (individual === "indiv" && !(!r.zip_source && r.individual_url)) return false;
    if (pMin != null) {
      if (r.page_count == null || r.page_count < pMin) return false;
    }
    if (pMax != null) {
      if (r.page_count == null || r.page_count > pMax) return false;
    }
    if (exts.size > 0 && !exts.has(r.extension)) return false;
    if (mods.size > 0 && !mods.has(r.module)) return false;
    if (bates != null) {
      if (r.bates_start == null || r.bates_end == null) return false;
      if (bates < r.bates_start || bates > r.bates_end) return false;
    }
    return true;
  });

  state.filtered.sort((a, b) => state.sortDir * compare(a, b, state.sortKey));
  state.page = 0;
  render();
  if (!state.applyingExternal) {
    writeUrlFromState();
  }
  updateActiveName();
}

function updateActiveName() {
  const el = $("active-name");
  if (!el) return;
  const params = currentParams().toString();
  const match = params === "" ? null : loadSaved().find(s => s.hash === params);
  el.textContent = match ? match.name : "";
  // `hidden` is the most reliable way to keep the chip out of layout when
  // there's no exact saved-search match — it beats any conflicting CSS.
  el.hidden = !match;
}

function render() {
  const tbody = $("rows");
  tbody.innerHTML = "";
  const total = state.filtered.length;
  if (state.mode === "filter") {
    $("count").textContent = total === state.rows.length
      ? `${total.toLocaleString()} files`
      : `${total.toLocaleString()} of ${state.rows.length.toLocaleString()} files`;
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  if (state.page >= totalPages) state.page = totalPages - 1;
  const start = state.page * PAGE_SIZE;
  const end = Math.min(start + PAGE_SIZE, total);

  const slice = state.filtered.slice(start, end);
  const frag = document.createDocumentFragment();
  for (const r of slice) frag.appendChild(rowEl(r));
  tbody.appendChild(frag);

  if (state.mode === "filter") {
    $("pager-info").textContent = total === 0
      ? "no results"
      : `${(start + 1).toLocaleString()}–${end.toLocaleString()}  (page ${state.page + 1} / ${totalPages})`;
    $("prev").disabled = state.page === 0;
    $("next").disabled = state.page >= totalPages - 1;
  }

  for (const th of document.querySelectorAll("th[data-sort]")) {
    th.classList.remove("sorted-asc", "sorted-desc");
    if (th.dataset.sort === state.sortKey) {
      th.classList.add(state.sortDir > 0 ? "sorted-asc" : "sorted-desc");
    }
  }
}

function rowEl(r) {
  const tr = document.createElement("tr");

  const tdId = document.createElement("td");
  tdId.className = "num rownum";
  tdId.textContent = r.id != null ? String(r.id) : "";
  tr.appendChild(tdId);

  const tdName = document.createElement("td");
  tdName.className = "filename";
  // Link precedence: PHMPT individual (canonical) -> self-hosted R2 (fills the
  // gap for files PHMPT only ships zipped) -> ICAN -> grouped dataset .zip (xpt).
  const fileUrl = r.individual_url || r.hosted_url || r.ican_url || r.dataset_zip_url || null;
  let note = "";
  if (!r.individual_url && r.hosted_url) note = "\n(self-hosted copy)";
  else if (!r.individual_url && !r.hosted_url && r.ican_url) note = "\n(opening via ICAN — PHMPT has no individual link)";
  else if (fileUrl && r.dataset_zip_url && fileUrl === r.dataset_zip_url) note = "\n(grouped dataset bundle — .xpt files aren't viewable individually)";
  tdName.title = r.filename + note;
  if (fileUrl) {
    const a = document.createElement("a");
    a.href = fileUrl;
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = r.filename;
    a.appendChild(externalIcon());
    tdName.appendChild(a);
    if (fileUrl === r.dataset_zip_url) tdName.appendChild(tag("dataset", "dataset .zip"));
  } else {
    tdName.textContent = r.filename;
  }
  tr.appendChild(tdName);

  const tdExt = document.createElement("td");
  if (r.extension) tdExt.appendChild(tag("ext", r.extension));
  tr.appendChild(tdExt);

  const tdSize = document.createElement("td");
  tdSize.className = "num";
  tdSize.textContent = fmtBytes(r.size);
  tr.appendChild(tdSize);

  const tdPages = document.createElement("td");
  tdPages.className = "num";
  if (r.page_count == null && r.extension === "pdf") {
    tdPages.textContent = "—";
    tdPages.title = "Page count unavailable for individual PDFs not bundled in a multiple-file-downloads zip";
    tdPages.style.color = "var(--muted)";
  } else {
    tdPages.textContent = fmtNum(r.page_count);
  }
  tr.appendChild(tdPages);

  const tdCo = document.createElement("td");
  if (r.company) tdCo.appendChild(tag(r.company.toLowerCase(), r.company));
  tr.appendChild(tdCo);

  const tdLic = document.createElement("td");
  if (r.license) tdLic.appendChild(tag(r.license.toLowerCase(), r.license));
  tr.appendChild(tdLic);

  const tdAge = document.createElement("td");
  tdAge.textContent = r.age_group || "";
  tr.appendChild(tdAge);

  const tdMod = document.createElement("td");
  tdMod.className = "col-module";
  if (r.module) tdMod.appendChild(tag("ext", r.module));
  tr.appendChild(tdMod);

  const tdBates = document.createElement("td");
  tdBates.className = "num col-bates";
  if (r.bates_start != null) {
    tdBates.textContent = r.bates_start === r.bates_end
      ? String(r.bates_start)
      : `${r.bates_start}–${r.bates_end}`;
  }
  tr.appendChild(tdBates);

  const tdZip = document.createElement("td");
  tdZip.className = "zip";
  if (r.zip_url) {
    const a = document.createElement("a");
    a.href = r.zip_url;
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = r.zip_source;
    a.appendChild(externalIcon());
    tdZip.appendChild(a);
  } else {
    tdZip.textContent = r.zip_source || "";
  }
  tr.appendChild(tdZip);

  return tr;
}

function externalIcon() {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("class", "ext-icon");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  const a = document.createElementNS(NS, "path");
  a.setAttribute("d", "M14 4h6v6");
  const b = document.createElementNS(NS, "path");
  b.setAttribute("d", "M20 4L10 14");
  const c = document.createElementNS(NS, "path");
  c.setAttribute("d", "M19 13v6a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h6");
  svg.appendChild(a);
  svg.appendChild(b);
  svg.appendChild(c);
  return svg;
}

// ── URL state ──────────────────────────────────────────────────────

function currentParams() {
  const params = new URLSearchParams();
  // Search mode: the full-text query plus only the filters the search index
  // supports (company/license/age/module). File-only filters don't apply.
  if (state.mode === "search") {
    if (state.searchQuery) params.set("search", state.searchQuery);
    const co = $("f-company").value; if (co) params.set("company", co);
    const li = $("f-license").value; if (li) params.set("license", li);
    const ag = $("f-age").value;     if (ag) params.set("age", ag);
    if (state.modSelected.size) params.set("module", [...state.modSelected].sort().join(","));
    return params;
  }
  // Filter mode: every filter control plus sort.
  for (const [id, key] of Object.entries(FIELD_PARAMS)) {
    const v = $(id).value;
    if (v) params.set(key, v);
  }
  if (state.extSelected.size) params.set("filetype", [...state.extSelected].sort().join(","));
  if (state.modSelected.size) params.set("module", [...state.modSelected].sort().join(","));
  if (state.sortKey !== "filename" || state.sortDir !== 1) {
    params.set("sort", state.sortKey);
    params.set("dir", state.sortDir > 0 ? "asc" : "desc");
  }
  return params;
}

function writeUrlFromState() {
  const params = currentParams();
  const hash = params.toString() ? "#" + params.toString() : "";
  // replaceState (not push) so back-button isn't spammed by every keystroke.
  history.replaceState(null, "", location.pathname + location.search + hash);
}

function applyParamsToControls(params) {
  state.applyingExternal = true;
  try {
    for (const [id, key] of Object.entries(FIELD_PARAMS)) {
      $(id).value = params.get(key) || "";
    }
    // Mutate the existing Sets in place — the checkbox change handlers
    // captured a closure over these references, so replacing the Sets would
    // orphan the listeners.
    state.extSelected.clear();
    for (const v of (params.get("filetype") || "").split(",").filter(Boolean)) state.extSelected.add(v);
    state.modSelected.clear();
    for (const v of (params.get("module") || "").split(",").filter(Boolean)) state.modSelected.add(v);
    // Re-sync the existing checkbox UIs.
    for (const cb of document.querySelectorAll("#f-ext input")) cb.checked = state.extSelected.has(cb.value);
    for (const cb of document.querySelectorAll("#f-module input")) cb.checked = state.modSelected.has(cb.value);
    state.sortKey = params.get("sort") || "filename";
    state.sortDir = params.get("dir") === "desc" ? -1 : 1;
  } finally {
    state.applyingExternal = false;
  }
}

function readUrlIntoState() {
  const hash = location.hash.startsWith("#") ? location.hash.slice(1) : "";
  applyParamsToControls(new URLSearchParams(hash));
}

function fullLink(params = currentParams()) {
  const s = params.toString();
  return location.origin + location.pathname + location.search + (s ? "#" + s : "");
}

// ── saved searches ─────────────────────────────────────────────────

function loadSaved() {
  try {
    return JSON.parse(localStorage.getItem(SAVED_KEY) || "[]");
  } catch {
    return [];
  }
}

function persistSaved(list) {
  localStorage.setItem(SAVED_KEY, JSON.stringify(list));
}

function refreshSavedPicker() {
  const sel = $("saved-pick");
  sel.innerHTML = `
    <option value="" disabled hidden selected>—</option>
    <option value="__reset__">— pick —</option>
  `;
  const sorted = loadSaved().slice().sort((a, b) => a.name.localeCompare(b.name));
  for (const s of sorted) {
    const opt = document.createElement("option");
    opt.value = s.id;
    opt.textContent = s.name;
    sel.appendChild(opt);
  }
  // Picker is action-only: never preserve a selection. The active search
  // name is shown in #active-name instead. This guarantees re-picking the
  // same option always fires `change`.
  sel.value = "";
}

function handleSaveCurrent() {
  const name = (prompt("Name this filter set:") || "").trim();
  if (!name) return;
  const list = loadSaved();
  if (list.some(s => s.name === name)) {
    if (!confirm(`Overwrite the existing "${name}"?`)) return;
  }
  const hash = currentParams().toString();
  const newEntry = {
    id: String(Date.now()) + "-" + Math.random().toString(36).slice(2, 8),
    name,
    hash,
    created_at: new Date().toISOString(),
  };
  const filtered = list.filter(s => s.name !== name);
  filtered.push(newEntry);
  persistSaved(filtered);
  refreshSavedPicker();
  updateActiveName();
  showToast(`Saved "${name}"`);
}

function applySavedById(id) {
  const s = loadSaved().find(e => e.id === id);
  if (!s) return;
  applyParamsToControls(new URLSearchParams(s.hash));
  applyFilters();
  writeUrlFromState();
}

function openManageModal() {
  $("manage-modal").classList.add("open");
  renderSavedList();
}

function closeManageModal() {
  $("manage-modal").classList.remove("open");
}

function renderSavedList() {
  const ul = $("saved-list");
  const list = loadSaved();
  ul.innerHTML = "";
  if (list.length === 0) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = "No saved filter sets yet.";
    ul.appendChild(li);
    return;
  }
  list.sort((a, b) => a.name.localeCompare(b.name));
  for (const s of list) {
    const li = document.createElement("li");

    const name = document.createElement("span");
    name.className = "name";
    name.textContent = s.name;
    li.appendChild(name);

    const meta = document.createElement("span");
    meta.className = "meta";
    meta.textContent = s.created_at.slice(0, 10);
    li.appendChild(meta);

    const apply = document.createElement("button");
    apply.textContent = "Apply";
    apply.addEventListener("click", () => {
      applySavedById(s.id);
      closeManageModal();
    });
    li.appendChild(apply);

    const rename = document.createElement("button");
    rename.textContent = "Rename";
    rename.addEventListener("click", () => {
      const next = (prompt("New name:", s.name) || "").trim();
      if (!next || next === s.name) return;
      const all = loadSaved();
      const dupe = all.find(e => e.name === next && e.id !== s.id);
      if (dupe && !confirm(`"${next}" already exists. Replace it?`)) return;
      const updated = all
        .filter(e => !(dupe && e.id === dupe.id))
        .map(e => e.id === s.id ? { ...e, name: next } : e);
      persistSaved(updated);
      refreshSavedPicker();
      renderSavedList();
    });
    li.appendChild(rename);

    const copy = document.createElement("button");
    copy.textContent = "Copy link";
    copy.addEventListener("click", async () => {
      const url = location.origin + location.pathname + location.search + (s.hash ? "#" + s.hash : "");
      try { await navigator.clipboard.writeText(url); showToast("Link copied"); }
      catch { prompt("Copy this link:", url); }
    });
    li.appendChild(copy);

    const del = document.createElement("button");
    del.className = "danger";
    del.textContent = "Delete";
    del.addEventListener("click", () => {
      if (!confirm(`Delete saved filter set "${s.name}"?`)) return;
      persistSaved(loadSaved().filter(e => e.id !== s.id));
      refreshSavedPicker();
      renderSavedList();
    });
    li.appendChild(del);

    ul.appendChild(li);
  }
}

let toastTimer = null;
function showToast(msg) {
  const el = $("toast");
  el.textContent = msg;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.textContent = ""; }, 2200);
}

function resetAllFilters() {
  for (const id of ["f-name", "f-pages-min", "f-pages-max", "f-bates"]) $(id).value = "";
  for (const id of ["f-company", "f-license", "f-age", "f-individual"]) $(id).value = "";
  state.extSelected.clear();
  state.modSelected.clear();
  for (const cb of document.querySelectorAll("#f-ext input, #f-module input")) cb.checked = false;
  state.sortKey = "filename";
  state.sortDir = 1;
  applyFilters();
}

async function copyCurrentLink() {
  const url = fullLink();
  try {
    await navigator.clipboard.writeText(url);
    showToast("Link copied");
  } catch {
    prompt("Copy this link:", url);
  }
}

function buildCheckboxFilter(wrapId, valueKey, stateSet, orderFn) {
  const counts = new Map();
  for (const r of state.rows) {
    const v = r[valueKey];
    if (!v) continue;
    counts.set(v, (counts.get(v) || 0) + 1);
  }
  const entries = [...counts.entries()];
  entries.sort(orderFn || ((a, b) => b[1] - a[1]));
  const wrap = $(wrapId);
  wrap.innerHTML = "";
  for (const [val, n] of entries) {
    const lbl = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.value = val;
    cb.addEventListener("change", () => {
      if (cb.checked) stateSet.add(val);
      else stateSet.delete(val);
      applyFilters();
    });
    lbl.appendChild(cb);
    lbl.appendChild(document.createTextNode(`${val} (${n.toLocaleString()})`));
    wrap.appendChild(lbl);
  }
}

function init() {
  const debounced = debounce(applyFilters, 120);
  for (const id of ["f-name", "f-pages-min", "f-pages-max", "f-bates"]) {
    $(id).addEventListener("input", debounced);
  }
  for (const id of ["f-company", "f-license", "f-age", "f-individual"]) {
    $(id).addEventListener("change", applyFilters);
  }

  $("reset").addEventListener("click", resetAllFilters);

  $("prev").addEventListener("click", () => {
    if (state.mode === "search") { window.ftPage(-1); return; }
    state.page = Math.max(0, state.page - 1); render();
  });
  $("next").addEventListener("click", () => {
    if (state.mode === "search") { window.ftPage(1); return; }
    state.page++; render();
  });

  for (const th of document.querySelectorAll("th[data-sort]")) {
    th.addEventListener("click", () => {
      const key = th.dataset.sort;
      if (state.sortKey === key) state.sortDir = -state.sortDir;
      else { state.sortKey = key; state.sortDir = 1; }
      applyFilters();
    });
  }

  // Saved-search toolbar
  $("saved-pick").addEventListener("change", (e) => {
    const v = e.target.value;
    // Snap back to placeholder immediately so re-picking the same option
    // still fires `change` next time.
    e.target.value = "";
    if (v === "__reset__") resetAllFilters();
    else if (v) applySavedById(v);
  });
  $("saved-save").addEventListener("click", handleSaveCurrent);
  $("copy-link").addEventListener("click", copyCurrentLink);
  $("saved-manage").addEventListener("click", openManageModal);
  // Modal close handlers
  for (const el of document.querySelectorAll("#manage-modal [data-close]")) {
    el.addEventListener("click", closeManageModal);
  }
  $("manage-modal").addEventListener("click", (e) => {
    if (e.target === e.currentTarget) closeManageModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeManageModal();
  });
  // Note: the hashchange listener lives in load() (applyHashToState), which
  // handles both filter and search URLs. Don't add a second one here — two
  // listeners race and a filter-mode re-run can clobber a search URL.
}

function debounce(fn, ms) {
  let h;
  return (...args) => { clearTimeout(h); h = setTimeout(() => fn(...args), ms); };
}

async function load() {
  init();
  const r = await fetch("data/index.json");
  state.rows = await r.json();
  const totalPages = state.rows.reduce((s, r) => s + (r.page_count || 0), 0);
  $("stats").textContent =
    ` ${state.rows.length.toLocaleString()} files · ${totalPages.toLocaleString()} pages`;
  buildCheckboxFilter("f-ext", "extension", state.extSelected);
  // Modules sort by their numeric suffix so M1, M2, M3, M4, M5 line up.
  buildCheckboxFilter("f-module", "module", state.modSelected,
    (a, b) => a[0].localeCompare(b[0]));
  refreshSavedPicker();
  // Restore whatever state the opening URL encodes (filters and/or search).
  applyHashToState();
  // Re-apply on manual #hash edits / back-forward nav. (history.replaceState,
  // which we use to write the URL, does NOT fire hashchange, so no loop.)
  window.addEventListener("hashchange", applyHashToState);
}

// Apply the current #hash to the UI. A `search` param restores search mode;
// otherwise we land in (or return to) filter mode.
function applyHashToState() {
  if (!state.rows.length) return;   // data not loaded yet; load() calls us again
  readUrlIntoState();               // sync filter controls from the hash
  const searchQ = new URLSearchParams(location.hash.slice(1)).get("search");
  if (searchQ && window.ftRestore) {
    window.ftRestore(searchQ);      // enter search mode and run the query
  } else if (state.mode === "search" && window.ftExit) {
    window.ftExit();                // leaving a search URL → back to the table
  } else {
    applyFilters();
  }
}

load().catch((e) => {
  $("rows").innerHTML = `<tr><td colspan="11" style="padding:24px;color:#900">load failed: ${e}</td></tr>`;
});

/* ---- Full-text document search (OpenSearch via Cloudflare Worker) ----
   Results are consolidated one row per document (group=doc), listing the
   matching page numbers. They reuse the main table's display zone + pager:
   searching enters "search mode" (table hidden, hit-list shown, pager pages
   over documents); clearing the box or "Back to filters" returns to the
   table. */
(function () {
  const API = "https://search.coviddocuments.com/";
  const SIZE = 150;       // documents per page
  const FROM_CAP = 1000;  // the Worker clamps `from` to this
  const form = document.getElementById("ft-form");
  if (!form) return;
  const q = document.getElementById("ft-q");
  const clearBtn = document.getElementById("ft-clear");
  const statusEl = document.getElementById("ft-status");
  const out = document.getElementById("ft-results");
  const table = document.getElementById("results");
  const countEl = document.getElementById("count");
  const banner = document.getElementById("search-banner");
  const bannerText = banner.querySelector(".sb-text");
  const backBtn = document.getElementById("back-to-filters");
  const copyBtn = document.getElementById("ft-copy-link");

  let seq = 0;
  let curQuery = "";
  let curPage = 0;        // 0-based page over documents
  let docTotal = 0;
  let pagesMatched = 0;

  function syncClear() { if (clearBtn) clearBtn.hidden = !q.value; }
  q.addEventListener("input", syncClear);
  syncClear();

  function safeSnippet(s) {
    const esc = String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    return esc.replace(/&lt;em&gt;/g, "<em>").replace(/&lt;\/em&gt;/g, "</em>");
  }

  // "Pages 63, 423, … (+313 more) of 13,123" — singular "Page" for a single
  // match. Show at most PAGE_LIST_CAP page numbers; big safety reports can
  // match a term on hundreds of pages, so the rest roll into "(+N more)".
  const PAGE_LIST_CAP = 10;
  // Append "· Pages 63, 423 (+N more) of 13,123" to `meta`, with each page
  // number a link that opens the PDF at that page (native #page=N). Only link
  // when the hit URL is a PDF; otherwise render the numbers as plain text.
  function appendPages(meta, h) {
    const pages = h.pages || [];
    if (!pages.length) return;
    const total = h.pages_total || pages.length;
    const shown = pages.slice(0, PAGE_LIST_CAP);
    const canLink = !!h.url && /\.pdf\b/i.test(h.url);
    meta.appendChild(document.createTextNode(" · " + (total === 1 ? "Page " : "Pages ")));
    shown.forEach((p, i) => {
      if (i) meta.appendChild(document.createTextNode(", "));
      const label = p.toLocaleString();
      if (canLink) {
        const a = document.createElement("a");
        a.className = "ft-pg";
        a.href = h.url + "#page=" + p;
        a.target = "_blank"; a.rel = "noopener";
        a.title = "Open the PDF at page " + label;
        a.textContent = label;
        meta.appendChild(a);
      } else {
        meta.appendChild(document.createTextNode(label));
      }
    });
    if (total > shown.length)
      meta.appendChild(document.createTextNode(" (+" + (total - shown.length).toLocaleString() + " more)"));
    if (h.total_pages)
      meta.appendChild(document.createTextNode(" of " + h.total_pages.toLocaleString()));
  }

  function renderHits(hits) {
    out.innerHTML = "";
    const frag = document.createDocumentFragment();
    for (const h of hits) {
      const card = document.createElement("div"); card.className = "ft-hit";
      const head = document.createElement("div"); head.className = "ft-hit-head";
      if (h.url) {
        const a = document.createElement("a");
        a.href = h.url; a.target = "_blank"; a.rel = "noopener"; a.textContent = h.filename;
        head.appendChild(a);
      } else head.appendChild(document.createTextNode(h.filename));
      const meta = document.createElement("span"); meta.className = "ft-hit-meta";
      appendPages(meta, h);
      const extra = (h.company ? " · " + h.company : "") + (h.module ? " · " + h.module : "") +
                    (h.license ? " · " + h.license : "");
      if (extra) meta.appendChild(document.createTextNode(extra));
      head.appendChild(meta); card.appendChild(head);
      if (h.snippet) {
        const s = document.createElement("div"); s.className = "ft-snip";
        s.innerHTML = safeSnippet(h.snippet); card.appendChild(s);
      }
      frag.appendChild(card);
    }
    out.appendChild(frag);
  }

  function updatePager(shownCount) {
    const totalPages = Math.max(1, Math.ceil(docTotal / SIZE));
    const start = curPage * SIZE;
    const end = start + shownCount;
    const reachableNext = (curPage + 1) * SIZE < FROM_CAP;
    document.getElementById("pager-info").textContent = docTotal === 0
      ? "no results"
      : `${(start + 1).toLocaleString()}–${end.toLocaleString()}  (page ${curPage + 1} / ${totalPages})`;
    document.getElementById("prev").disabled = curPage === 0;
    document.getElementById("next").disabled = curPage >= totalPages - 1 || !reachableNext;
  }

  // Human-readable summary of the active search filters, e.g.
  // ["Company: Pfizer", "Module: M4, M5"].
  function activeFilterSummary() {
    const parts = [];
    const co = $("f-company").value; if (co) parts.push("Company: " + co);
    const li = $("f-license").value; if (li) parts.push("License: " + li);
    const ag = $("f-age").value;     if (ag) parts.push("Age: " + ag);
    const mods = [...state.modSelected]; if (mods.length) parts.push("Module: " + mods.join(", "));
    return parts;
  }

  function updateBanner() {
    // strip the user's surrounding exact-phrase quotes so the banner reads
    // match “adverse events”, not match “"adverse events"”
    const shownQ = curQuery.replace(/^"(.*)"$/, "$1");
    let t = `${docTotal.toLocaleString()} document${docTotal === 1 ? "" : "s"}` +
            ` (${pagesMatched.toLocaleString()} page${pagesMatched === 1 ? "" : "s"})` +
            ` match “${shownQ}”`;
    const filters = activeFilterSummary();
    if (filters.length) t += ` · filtered by ${filters.join(" · ")}`;
    if (docTotal > FROM_CAP) t += ` · first ${FROM_CAP.toLocaleString()} shown`;
    bannerText.textContent = t;
  }

  // The four filters the page-level search index supports (company, license,
  // age_group, module). Read live from the controls and map UI ids → index
  // field names; module is a multi-select, sent comma-joined.
  function searchFilterParams() {
    const p = [];
    const co = $("f-company").value; if (co) p.push("company=" + encodeURIComponent(co));
    const li = $("f-license").value; if (li) p.push("license=" + encodeURIComponent(li));
    const ag = $("f-age").value;     if (ag) p.push("age_group=" + encodeURIComponent(ag));
    const mods = [...state.modSelected]; if (mods.length) p.push("module=" + encodeURIComponent(mods.join(",")));
    return p.join("&");
  }

  function enterSearchMode() {
    state.mode = "search";
    document.body.classList.add("mode-search");
    table.hidden = true;
    out.hidden = false;
    countEl.hidden = true;
    banner.hidden = false;
  }

  function exitSearchMode() {
    seq++;                 // cancel any in-flight request
    state.mode = "filter";
    document.body.classList.remove("mode-search");
    table.hidden = false;
    out.hidden = true;
    out.innerHTML = "";
    countEl.hidden = false;
    banner.hidden = true;
    statusEl.textContent = "";
    applyFilters();        // restore the table, applying any filters changed during search
  }

  async function runSearch(page) {
    if (!curQuery) { exitSearchMode(); return; }
    curPage = Math.max(0, page);
    const mine = ++seq;
    enterSearchMode();
    state.searchQuery = curQuery;
    writeUrlFromState();   // reflect search + active filters in the #hash
    statusEl.textContent = "Searching…";
    try {
      const from = curPage * SIZE;
      const filters = searchFilterParams();
      const url = `${API}?q=${encodeURIComponent(curQuery)}&group=doc&size=${SIZE}&from=${from}` +
                  (filters ? "&" + filters : "");
      const r = await fetch(url);
      const d = await r.json();
      if (mine !== seq) return;
      if (d.error) { statusEl.textContent = "Search error: " + d.error; return; }
      docTotal = d.total || 0;
      pagesMatched = d.pages_matched || 0;
      const hits = d.hits || [];
      renderHits(hits);
      updateBanner();
      updatePager(hits.length);
      statusEl.textContent = "";
    } catch (err) {
      if (mine === seq) statusEl.textContent = "Search unavailable right now.";
    }
  }

  // Bridge so the shared Prev/Next pager (wired in setup) can drive search.
  window.ftPage = (delta) => runSearch(curPage + delta);
  // Bridge so a filter change in search mode re-queries from the first page.
  window.ftRerun = () => runSearch(0);
  // Bridge so load() can restore a search from the URL (controls are already set).
  window.ftRestore = (query) => { q.value = query; curQuery = query; syncClear(); runSearch(0); };
  // Bridge so a hashchange to a non-search URL can leave search mode.
  window.ftExit = () => { q.value = ""; curQuery = ""; syncClear(); exitSearchMode(); };

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const query = q.value.trim();
    if (!query) { curQuery = ""; exitSearchMode(); return; }
    curQuery = query;
    runSearch(0);
  });

  if (clearBtn) clearBtn.addEventListener("click", () => {
    q.value = ""; syncClear(); q.focus();
    curQuery = "";
    exitSearchMode();
  });

  backBtn.addEventListener("click", exitSearchMode);

  // Copy the current search URL. The savedbar's #toast is hidden in search
  // mode, so confirm inline by briefly flipping the button label.
  copyBtn.addEventListener("click", async () => {
    const url = fullLink();   // currentParams() returns the search branch in search mode
    try { await navigator.clipboard.writeText(url); }
    catch { prompt("Copy this link:", url); return; }
    const prev = copyBtn.textContent;
    copyBtn.textContent = "Copied!";
    setTimeout(() => { copyBtn.textContent = prev; }, 1500);
  });
})();
