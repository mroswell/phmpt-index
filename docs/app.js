"use strict";

const PAGE_SIZE = 100;
const SAVED_KEY = "phmpt-saved-searches-v1";

// URL hash <-> state field names. Compact param names keep links short.
const FIELD_PARAMS = {
  "f-name":       "q",
  "f-company":    "co",
  "f-license":    "li",
  "f-age":        "ag",
  "f-individual": "indiv",
  "f-date-from":  "df",
  "f-date-to":    "dt",
  "f-pages-min":  "pmin",
  "f-pages-max":  "pmax",
  "f-bates":      "bates",
};

const state = {
  rows: [],
  filtered: [],
  page: 0,
  sortKey: "modified",
  sortDir: -1,
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
  const name = $("f-name").value.trim().toLowerCase();
  const company = $("f-company").value;
  const license = $("f-license").value;
  const age = $("f-age").value;
  const individual = $("f-individual").value;
  const dateFrom = $("f-date-from").value;
  const dateTo = $("f-date-to").value;
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
    if (individual === "both"  && !(r.zip_source && r.individual_url)) return false;
    if (individual === "zip"   && !(r.zip_source && !r.individual_url)) return false;
    if (individual === "indiv" && !(!r.zip_source && r.individual_url)) return false;
    if (dateFrom && (!r.modified || r.modified.slice(0, 10) < dateFrom)) return false;
    if (dateTo && (!r.modified || r.modified.slice(0, 10) > dateTo)) return false;
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
  $("count").textContent = total === state.rows.length
    ? `${total.toLocaleString()} files`
    : `${total.toLocaleString()} of ${state.rows.length.toLocaleString()} files`;

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  if (state.page >= totalPages) state.page = totalPages - 1;
  const start = state.page * PAGE_SIZE;
  const end = Math.min(start + PAGE_SIZE, total);

  const slice = state.filtered.slice(start, end);
  const frag = document.createDocumentFragment();
  for (const r of slice) frag.appendChild(rowEl(r));
  tbody.appendChild(frag);

  $("pager-info").textContent = total === 0
    ? "no results"
    : `${(start + 1).toLocaleString()}–${end.toLocaleString()}  (page ${state.page + 1} / ${totalPages})`;
  $("prev").disabled = state.page === 0;
  $("next").disabled = state.page >= totalPages - 1;

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
  tdName.title = r.filename;
  if (r.individual_url) {
    const a = document.createElement("a");
    a.href = r.individual_url;
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = r.filename;
    a.appendChild(externalIcon());
    tdName.appendChild(a);
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

  const tdDate = document.createElement("td");
  tdDate.textContent = fmtDate(r.modified);
  tr.appendChild(tdDate);

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
  for (const [id, key] of Object.entries(FIELD_PARAMS)) {
    const v = $(id).value;
    if (v) params.set(key, v);
  }
  if (state.extSelected.size) params.set("ext", [...state.extSelected].sort().join(","));
  if (state.modSelected.size) params.set("mod", [...state.modSelected].sort().join(","));
  if (state.sortKey !== "modified" || state.sortDir !== -1) {
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
    for (const v of (params.get("ext") || "").split(",").filter(Boolean)) state.extSelected.add(v);
    state.modSelected.clear();
    for (const v of (params.get("mod") || "").split(",").filter(Boolean)) state.modSelected.add(v);
    // Re-sync the existing checkbox UIs.
    for (const cb of document.querySelectorAll("#f-ext input")) cb.checked = state.extSelected.has(cb.value);
    for (const cb of document.querySelectorAll("#f-module input")) cb.checked = state.modSelected.has(cb.value);
    state.sortKey = params.get("sort") || "modified";
    state.sortDir = params.get("dir") === "asc" ? 1 : -1;
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
  const name = (prompt("Name this search:") || "").trim();
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
    li.textContent = "No saved searches yet.";
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
      if (!confirm(`Delete saved search "${s.name}"?`)) return;
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
  for (const id of ["f-name", "f-date-from", "f-date-to", "f-pages-min", "f-pages-max", "f-bates"]) $(id).value = "";
  for (const id of ["f-company", "f-license", "f-age", "f-individual"]) $(id).value = "";
  state.extSelected.clear();
  state.modSelected.clear();
  for (const cb of document.querySelectorAll("#f-ext input, #f-module input")) cb.checked = false;
  state.sortKey = "modified";
  state.sortDir = -1;
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
  for (const id of ["f-name", "f-date-from", "f-date-to", "f-pages-min", "f-pages-max", "f-bates"]) {
    $(id).addEventListener("input", debounced);
  }
  for (const id of ["f-company", "f-license", "f-age", "f-individual"]) {
    $(id).addEventListener("change", applyFilters);
  }

  $("reset").addEventListener("click", resetAllFilters);

  $("prev").addEventListener("click", () => { state.page = Math.max(0, state.page - 1); render(); });
  $("next").addEventListener("click", () => { state.page++; render(); });

  for (const th of document.querySelectorAll("th[data-sort]")) {
    th.addEventListener("click", () => {
      const key = th.dataset.sort;
      if (state.sortKey === key) state.sortDir = -state.sortDir;
      else { state.sortKey = key; state.sortDir = key === "modified" ? -1 : 1; }
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

  // Back/forward navigation: re-apply state from the URL.
  window.addEventListener("hashchange", () => {
    readUrlIntoState();
    applyFilters();
  });
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
  // If the page was opened with a hash, restore that state before first render.
  if (location.hash) readUrlIntoState();
  applyFilters();
}

load().catch((e) => {
  $("rows").innerHTML = `<tr><td colspan="12" style="padding:24px;color:#900">load failed: ${e}</td></tr>`;
});
