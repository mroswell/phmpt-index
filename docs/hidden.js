/* Redaction Topics — browse page.
 *
 * Loads:
 *   data/hidden_substance.json          — slim per-file rollup with top 3 example hits
 *   data/foia_followup_targets.json     — top-100 ranked targets (for the toggle)
 *   data/index.json                     — for individual_url / ican_url / zip_url
 *
 * Per-file detail (all redactions in a file) is fetched on-demand from
 * data/hidden_substance/{filename}.json when the user expands a row.
 *
 * (The on-disk filename retains "hidden_substance" for compatibility
 * with the data pipeline; only user-facing labels use the new term
 * "Redaction Topics".)
 *
 * Filters: topic chip, marker-type chip, filename, module, company,
 * license, bates substring, "likely in table only" toggle,
 * "top-100 targets only" toggle, "group identical-redaction submissions"
 * toggle.
 */

(() => {
  const $ = (id) => document.getElementById(id);

  const state = {
    rows: [],
    filteredRows: [],
    activeCategories: new Set(),
    activeMarkers: new Set(),
    top100Filenames: new Set(),
    sortKey: "total_kept",
    sortDir: "desc",
    detailCache: new Map(),
  };

  function fmtNum(n) { return n == null ? "" : n.toLocaleString(); }

  // Strip the submission token (_S<N>_) from a filename so we can group
  // successive monthly submissions of the same eCTD section together.
  // 125742_S53_M1_356h.pdf -> 125742_M1_356h.pdf
  // 125742-45_S211_M1_356h.pdf -> 125742-45_M1_356h.pdf
  function canonicalFilename(fn) {
    return fn.replace(/_S\d+_/, "_");
  }

  // Version signature: two submissions with the same canonical name AND
  // the same signature have effectively-identical redactions and should
  // be collapsed. Two submissions whose forms differ in length or
  // per-marker counts (e.g., the form expanded to add more rows) keep
  // separate rows.
  function versionSignature(row) {
    const markerKey = Object.entries(row.by_marker || {})
      .sort()
      .map(([k, v]) => `${k}:${v}`)
      .join(",");
    return `${row.total_kept}|${row.table_count || 0}|${markerKey}`;
  }

  // Extract submission number, e.g. "125742_S53_M1_356h.pdf" -> 53.
  // Returns null for filenames without _S<N>_.
  function submissionNum(fn) {
    const m = fn.match(/_S(\d+)_/);
    return m ? parseInt(m[1], 10) : null;
  }

  // Build groups for the "Group identical-redaction submissions" view.
  // Returns [{primary: row, members: [row, ...]}, ...]. A group of 1 is
  // just one row with itself as the only member (so the rest of the
  // render code can be uniform).
  function buildGroups(rows) {
    const groups = new Map();
    for (const r of rows) {
      const key = canonicalFilename(r.filename) + "||" + versionSignature(r);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(r);
    }
    const out = [];
    for (const members of groups.values()) {
      // Pick the highest submission number as the "primary" (most recent)
      members.sort((a, b) => {
        const sa = submissionNum(a.filename) ?? -1;
        const sb = submissionNum(b.filename) ?? -1;
        return sb - sa;
      });
      out.push({ primary: members[0], members });
    }
    return out;
  }

  function sourceFor(row) {
    if (row.individual_url) return { kind: "individual", url: row.individual_url, label: "individual" };
    if (row.ican_url)       return { kind: "ican",       url: row.ican_url,       label: "ICAN" };
    if (row.zip_url)        return { kind: "zip",        url: row.zip_url,        label: "ZIP" };
    return { kind: null, url: null, label: "" };
  }

  function escapeHTML(s) {
    return (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  // Wrap any (b)(N)(X?) marker occurrence in <mark>
  function highlightMarkers(text) {
    const escaped = escapeHTML(text);
    return escaped.replace(/\(b\)\s*\(\d+\)(?:\s*\([A-Z]\))?/g, (m) => `<mark>${m}</mark>`);
  }

  function renderCategoryChips(categoryCounts) {
    const strip = $("cat-strip");
    strip.innerHTML = "";

    const allChip = document.createElement("span");
    allChip.className = "cat-chip" + (state.activeCategories.size === 0 ? " active" : "");
    allChip.textContent = "All categories";
    allChip.onclick = () => { state.activeCategories.clear(); rerender(); };
    strip.appendChild(allChip);

    for (const [cat, count] of categoryCounts) {
      const chip = document.createElement("span");
      chip.className = "cat-chip" + (state.activeCategories.has(cat) ? " active" : "");
      chip.innerHTML = `${cat}<span class="count">${count.toLocaleString()}</span>`;
      chip.onclick = () => {
        if (state.activeCategories.has(cat)) state.activeCategories.delete(cat);
        else state.activeCategories.add(cat);
        rerender();
      };
      strip.appendChild(chip);
    }
  }

  function renderMarkerChips(markerCounts) {
    const strip = $("marker-strip");
    strip.innerHTML = "";

    const allChip = document.createElement("span");
    allChip.className = "marker-chip" + (state.activeMarkers.size === 0 ? " active" : "");
    allChip.textContent = "All markers";
    allChip.onclick = () => { state.activeMarkers.clear(); rerender(); };
    strip.appendChild(allChip);

    for (const [m, count] of markerCounts) {
      const chip = document.createElement("span");
      chip.className = "marker-chip" + (state.activeMarkers.has(m) ? " active" : "");
      chip.innerHTML = `<code>${m}</code><span class="count">${count.toLocaleString()}</span>`;
      chip.onclick = () => {
        if (state.activeMarkers.has(m)) state.activeMarkers.delete(m);
        else state.activeMarkers.add(m);
        rerender();
      };
      strip.appendChild(chip);
    }
  }

  function applyFilters() {
    const nameQ = $("f-name").value.trim().toLowerCase();
    const mod = $("f-module").value;
    const co = $("f-company").value;
    const lic = $("f-license").value;
    const batesQ = $("f-bates").value.trim().toLowerCase();
    const tableFilter = document.querySelector('input[name="table-filter"]:checked')?.value || "";
    const topFilter = document.querySelector('input[name="top-filter"]:checked')?.value || "";
    const groupFilter = document.querySelector('input[name="group-filter"]:checked')?.value || "";

    state.filteredRows = state.rows.filter((r) => {
      if (state.activeCategories.size > 0) {
        const has = Object.keys(r.by_category || {}).some((c) => state.activeCategories.has(c));
        if (!has) return false;
      }
      if (state.activeMarkers.size > 0) {
        const has = Object.keys(r.by_marker || {}).some((m) => state.activeMarkers.has(m));
        if (!has) return false;
      }
      if (nameQ && !r.filename.toLowerCase().includes(nameQ)) return false;
      if (mod && (r.moduleLabel || "") !== mod) return false;
      if (co && r.company !== co) return false;
      if (lic && r.license !== lic) return false;
      if (batesQ) {
        // Check if any of the top examples has a bates that matches
        const found = (r.top_examples || []).some((e) =>
          (e.bates || "").toLowerCase().includes(batesQ)
        );
        if (!found) return false;
      }
      if (tableFilter === "table" && (r.table_count || 0) === 0) return false;
      if (topFilter === "top100" && !state.top100Filenames.has(r.filename)) return false;
      return true;
    });

    // Grouping: collapse rows that share canonical filename + version signature
    if (groupFilter === "group") {
      const grouped = buildGroups(state.filteredRows);
      state.filteredRows = grouped.map(({ primary, members }) => ({
        ...primary,
        _groupMembers: members,
        _groupSize: members.length,
      }));
    } else {
      // Make sure stale group metadata doesn't linger when toggling off
      for (const r of state.filteredRows) {
        delete r._groupMembers;
        delete r._groupSize;
      }
    }

    const k = state.sortKey;
    const dir = state.sortDir === "desc" ? -1 : 1;
    state.filteredRows.sort((a, b) => {
      let av = a[k], bv = b[k];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === "string") return av.localeCompare(bv) * dir;
      return (av - bv) * dir;
    });
  }

  async function loadDetail(filename) {
    if (state.detailCache.has(filename)) return state.detailCache.get(filename);
    const encoded = encodeURIComponent(filename).replace(/%2F/g, "/"); // file-safe but allow slashes
    const resp = await fetch(`data/hidden_substance/${encoded}.json`);
    if (!resp.ok) throw new Error(`Failed to load ${filename}: ${resp.status}`);
    const doc = await resp.json();
    state.detailCache.set(filename, doc);
    return doc;
  }

  function renderHitInline(hit) {
    const meta = [];
    meta.push(`<span><strong>Page ${hit.page}</strong></span>`);
    meta.push(`<span>Marker: <code>${hit.marker_canonical || hit.marker}</code></span>`);
    if (hit.substance_category || hit.category) {
      meta.push(`<span>Topic: <strong>${hit.substance_category || hit.category}</strong></span>`);
    }
    if (hit.bates) meta.push(`<span>Bates: <code>${hit.bates}</code></span>`);
    if (hit.likely_table) {
      meta.push(`<span style="color:#8a5a00;font-weight:600">⊞ Likely in table (${hit.nearby_marker_count || "5+"} markers nearby)</span>`);
    }
    let sectionLine = "";
    if (hit.section_heading) {
      sectionLine = `<div class="ex-section">§ ${escapeHTML(hit.section_heading)}</div>`;
    }
    return `<div class="ex-hit">
      <div class="ex-meta">${meta.join("")}</div>
      ${sectionLine}
      <div class="ex-context">${highlightMarkers(hit.context || hit.context_preview || "")}</div>
    </div>`;
  }

  function renderRows() {
    const tbody = $("rows");
    tbody.innerHTML = "";

    const totalRedactions = state.filteredRows.reduce((s, r) => {
      if (state.activeCategories.size === 0 && state.activeMarkers.size === 0) {
        return s + (r.total_kept || 0);
      }
      // Count only the matched categories/markers
      let n = 0;
      if (state.activeCategories.size > 0) {
        for (const [c, cnt] of Object.entries(r.by_category || {})) {
          if (state.activeCategories.has(c)) n += cnt;
        }
      }
      if (state.activeMarkers.size > 0) {
        let m = 0;
        for (const [marker, cnt] of Object.entries(r.by_marker || {})) {
          if (state.activeMarkers.has(marker)) m += cnt;
        }
        // If both filters active, use the smaller (intersect estimate)
        n = state.activeCategories.size > 0 ? Math.min(n, m) : m;
      }
      return s + n;
    }, 0);

    const isGrouped = state.filteredRows.some((r) => r._groupSize && r._groupSize > 1);
    const collapsedSubmissions = state.filteredRows.reduce(
      (s, r) => s + (r._groupSize > 1 ? r._groupSize - 1 : 0),
      0,
    );
    $("count").textContent = isGrouped
      ? `${state.filteredRows.length.toLocaleString()} unique documents (${collapsedSubmissions.toLocaleString()} additional submissions collapsed) · ~${totalRedactions.toLocaleString()} redactions in filtered view`
      : `${state.filteredRows.length.toLocaleString()} files · ~${totalRedactions.toLocaleString()} redactions in filtered view`;

    for (const r of state.filteredRows) {
      const tr = document.createElement("tr");
      tr.dataset.filename = r.filename;

      // Expand toggle column
      const tdToggle = document.createElement("td");
      tdToggle.style.width = "20px";
      const tog = document.createElement("a");
      tog.href = "#";
      tog.className = "expand-toggle";
      tog.textContent = "▶";
      tog.title = "Show all redactions in this file";
      tog.onclick = async (e) => {
        e.preventDefault();
        const next = tr.nextSibling;
        if (next && next.classList?.contains("detail-row")) {
          next.remove();
          tog.textContent = "▶";
          return;
        }
        tog.textContent = "▼";
        const detailTr = document.createElement("tr");
        detailTr.className = "detail-row";
        const detailTd = document.createElement("td");
        detailTd.colSpan = 9;
        detailTd.className = "expand-cell";
        detailTd.textContent = "Loading…";
        detailTr.appendChild(detailTd);
        tr.after(detailTr);
        try {
          const doc = await loadDetail(r.filename);
          const sorted = [...(doc.hits || [])].sort((a, b) => {
            // Rare markers first, then table-cluster, then by page
            const rareA = a.marker_canonical === "(b)(4)" ? 1 : 0;
            const rareB = b.marker_canonical === "(b)(4)" ? 1 : 0;
            if (rareA !== rareB) return rareA - rareB;
            if (a.likely_table !== b.likely_table) return a.likely_table ? -1 : 1;
            return a.page - b.page;
          });
          let html = "";
          // Group-members section: list every collapsed sibling submission
          if (r._groupSize && r._groupSize > 1) {
            const members = [...r._groupMembers].sort((a, b) => {
              const sa = submissionNum(a.filename) ?? -1;
              const sb = submissionNum(b.filename) ?? -1;
              return sa - sb;
            });
            html += `<div style="margin-bottom:10px;padding:8px 12px;background:#eef4ff;border:1px solid #cfd8e6;border-radius:4px;font-size:12px;line-height:1.55">
              <div style="font-weight:600;color:var(--accent);margin-bottom:4px">
                Submissions collapsed into this row (${r._groupSize})
              </div>
              <div style="color:var(--muted);font-size:11px;margin-bottom:6px">
                All share an identical redaction signature
                (${r.total_kept} marker${r.total_kept === 1 ? "" : "s"},
                ${Object.entries(r.by_marker || {}).map(([k, v]) => `<code>${k}</code>×${v}`).join(", ")}).
                The detail below is from the most recent submission;
                Bates ranges differ across submissions but the redacted fields are the same.
              </div>
              <ul style="margin:0;padding-left:18px">`;
            for (const m of members) {
              const isPrimary = m.filename === r.filename;
              const url = m.source?.url;
              const link = url
                ? `<a href="${url}" target="_blank" rel="noopener">${m.filename}</a>`
                : `<span>${m.filename}</span>`;
              html += `<li>${link}${isPrimary ? " <span style='color:var(--muted)'>(detail shown below)</span>" : ""}</li>`;
            }
            html += `</ul></div>`;
          }
          html += `<div style="margin-bottom:8px;font-size:11px;color:var(--muted)">
            ${sorted.length} redaction${sorted.length === 1 ? "" : "s"} in this file (rare markers and table clusters shown first)
          </div>`;
          html += sorted.slice(0, 200).map(renderHitInline).join("");
          if (sorted.length > 200) {
            html += `<div style="color:var(--muted);font-size:11px;margin-top:4px">…and ${sorted.length - 200} more (truncated for display).</div>`;
          }
          detailTd.innerHTML = html;
        } catch (err) {
          detailTd.innerHTML = `<div style="color:#c8102e">Failed to load detail: ${err.message}</div>`;
        }
      };
      tdToggle.appendChild(tog);
      tr.appendChild(tdToggle);

      // File cell
      const tdFile = document.createElement("td");
      tdFile.className = "files-cell";
      const link = document.createElement("a");
      link.href = r.source.url || "#";
      link.textContent = r.filename;
      if (r.source.url) {
        link.target = "_blank";
        link.rel = "noopener";
      } else {
        link.style.color = "var(--muted)";
        link.style.cursor = "default";
        link.onclick = (e) => e.preventDefault();
      }
      tdFile.appendChild(link);
      if (r.source.kind) {
        const tag = document.createElement("span");
        tag.className = "src-tag " + r.source.kind;
        tag.textContent = r.source.label;
        tdFile.appendChild(tag);
      }
      if (state.top100Filenames.has(r.filename)) {
        const t = document.createElement("span");
        t.className = "target-tag";
        t.title = "This file contains at least one Top-100 follow-up target";
        t.textContent = "★ top-100";
        tdFile.appendChild(t);
      }
      if (r._groupSize && r._groupSize > 1) {
        const others = r._groupMembers
          .filter((m) => m.filename !== r.filename);
        const subs = others
          .map((m) => submissionNum(m.filename))
          .filter((n) => n != null)
          .sort((a, b) => a - b);
        const range = subs.length
          ? ` (S${subs[0]}${subs.length > 1 ? "–S" + subs[subs.length - 1] : ""})`
          : "";
        const g = document.createElement("span");
        g.className = "target-tag";
        g.style.background = "#e3eef7";
        g.style.color = "#1e5d8c";
        g.title =
          `Collapsed with ${others.length} other submission${others.length === 1 ? "" : "s"} ` +
          `of this document that share an identical redaction signature ` +
          `(same redaction count, same marker breakdown). Expand the row to see them all.`;
        g.textContent = `+${others.length} earlier${range}`;
        tdFile.appendChild(g);
      }
      tr.appendChild(tdFile);

      // Module / Company / License
      const tdMod = document.createElement("td");
      tdMod.textContent = r.moduleLabel || "";
      tr.appendChild(tdMod);
      const tdCo = document.createElement("td");
      tdCo.textContent = r.company || "";
      tr.appendChild(tdCo);
      const tdLic = document.createElement("td");
      tdLic.textContent = r.license || "";
      tr.appendChild(tdLic);

      // Total redactions
      const tdTotal = document.createElement("td");
      tdTotal.className = "hits-num";
      tdTotal.textContent = fmtNum(r.total_kept);
      tr.appendChild(tdTotal);

      // In tables
      const tdTab = document.createElement("td");
      tdTab.className = "hits-num";
      tdTab.textContent = r.table_count ? fmtNum(r.table_count) : "—";
      tr.appendChild(tdTab);

      // Categories
      const tdCat = document.createElement("td");
      tdCat.className = "cat-cell";
      const catEntries = Object.entries(r.by_category || {})
        .filter(([c]) => state.activeCategories.size === 0 || state.activeCategories.has(c))
        .sort((a, b) => b[1] - a[1]);
      for (const [c, n] of catEntries.slice(0, 4)) {
        const line = document.createElement("span");
        line.className = "line";
        line.textContent = `${c} × ${n.toLocaleString()}`;
        tdCat.appendChild(line);
      }
      if (catEntries.length > 4) {
        const more = document.createElement("span");
        more.className = "line";
        more.style.color = "var(--muted)";
        more.textContent = `…+${catEntries.length - 4} more`;
        tdCat.appendChild(more);
      }
      tr.appendChild(tdCat);

      // Marker breakdown
      const tdMark = document.createElement("td");
      tdMark.className = "marker-cell";
      const markerEntries = Object.entries(r.by_marker || {})
        .filter(([m]) => state.activeMarkers.size === 0 || state.activeMarkers.has(m))
        .sort((a, b) => b[1] - a[1]);
      for (const [m, n] of markerEntries) {
        const line = document.createElement("span");
        line.className = "line";
        line.innerHTML = `<code>${m}</code> × ${n.toLocaleString()}`;
        tdMark.appendChild(line);
      }
      tr.appendChild(tdMark);

      tbody.appendChild(tr);
    }
  }

  function rerender() {
    applyFilters();
    renderRows();
    Array.from(document.querySelectorAll(".cat-chip")).forEach((el) => {
      const isAll = el.textContent.startsWith("All categories");
      const cat = el.firstChild?.textContent;
      el.classList.toggle("active",
        (isAll && state.activeCategories.size === 0) ||
        state.activeCategories.has(cat));
    });
    Array.from(document.querySelectorAll(".marker-chip")).forEach((el) => {
      const isAll = el.textContent.startsWith("All markers");
      const code = el.querySelector("code")?.textContent;
      el.classList.toggle("active",
        (isAll && state.activeMarkers.size === 0) ||
        (code && state.activeMarkers.has(code)));
    });
  }

  async function init() {
    const [hsDoc, indexDoc, targetsDoc] = await Promise.all([
      fetch("data/hidden_substance.json").then((r) => r.json()),
      fetch("data/index.json").then((r) => r.json()),
      fetch("data/foia_followup_targets.json").then((r) => r.json()).catch(() => ({ targets: [] })),
    ]);

    // Build filename → index-record map for URL fallback
    const byFilename = new Map();
    for (const r of indexDoc) byFilename.set(r.filename, r);

    state.top100Filenames = new Set((targetsDoc.targets || []).map((t) => t.filename));

    state.rows = (hsDoc.files || []).map((f) => {
      const idxRow = byFilename.get(f.filename) || {};
      const moduleLabel = f.module || "Memo/Other";
      const row = {
        ...f,
        moduleLabel,
        individual_url: idxRow.individual_url,
        ican_url: idxRow.ican_url,
        zip_url: idxRow.zip_url,
      };
      row.source = sourceFor(row);
      return row;
    });

    // Summary line
    const s = hsDoc.summary || {};
    const totalFiles = state.rows.length;
    const totalKept = s.total_kept || 0;
    const totalTable = s.kept_likely_in_table || 0;

    $("summary").innerHTML =
      `<span class="stat"><span class="num">${totalKept.toLocaleString()}</span> redaction markers (after excluding CRFs and (b)(6))</span>` +
      `<span class="stat"><span class="num">${totalFiles.toLocaleString()}</span> source files</span>` +
      `<span class="stat"><span class="num">${totalTable.toLocaleString()}</span> likely inside tables (each may hide many values)</span>` +
      `<span class="stat"><span class="num">${state.top100Filenames.size.toLocaleString()}</span> files with top-100 follow-up targets</span>`;

    // Chip strips from summary
    const cats = Object.entries(s.by_substance_category || {});
    renderCategoryChips(cats);
    const markers = Object.entries(s.by_marker || {});
    renderMarkerChips(markers);

    // PDF cta visible if the PDF file exists
    try {
      const head = await fetch("data/foia_followup_targets.pdf", { method: "HEAD" });
      if (head.ok) $("pdf-cta").hidden = false;
    } catch (_) { /* ignore */ }

    // Filter event wiring
    ["f-name", "f-bates"].forEach((id) => $(id).addEventListener("input", rerender));
    ["f-module", "f-company", "f-license"].forEach((id) => $(id).addEventListener("change", rerender));
    document.querySelectorAll('input[name="table-filter"], input[name="top-filter"], input[name="group-filter"]').forEach((el) => {
      el.addEventListener("change", rerender);
    });
    $("reset").addEventListener("click", () => {
      ["f-name", "f-bates"].forEach((id) => ($(id).value = ""));
      ["f-module", "f-company", "f-license"].forEach((id) => ($(id).value = ""));
      document.querySelectorAll('input[name="table-filter"]').forEach((el) => (el.checked = el.value === ""));
      document.querySelectorAll('input[name="top-filter"]').forEach((el) => (el.checked = el.value === ""));
      document.querySelectorAll('input[name="group-filter"]').forEach((el) => (el.checked = el.value === ""));
      state.activeCategories.clear();
      state.activeMarkers.clear();
      rerender();
    });

    document.querySelectorAll("th[data-sort]").forEach((th) => {
      th.style.cursor = "pointer";
      th.title = "Click to sort";
      th.addEventListener("click", () => {
        const k = th.dataset.sort;
        if (state.sortKey === k) {
          state.sortDir = state.sortDir === "desc" ? "asc" : "desc";
        } else {
          state.sortKey = k;
          state.sortDir = (k === "filename" || k === "module" || k === "company" || k === "license") ? "asc" : "desc";
        }
        renderRows();
      });
    });

    rerender();
  }

  init().catch((err) => {
    console.error(err);
    $("summary").textContent = "Failed to load data: " + err.message;
  });
})();
