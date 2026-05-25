/* FOIA Exemption Findings browse page.
 *
 * Loads three JSON artifacts:
 *   data/exemptions.json         — slim per-file rollups (the canonical
 *                                  aggregate of all per-module + individual
 *                                  scans)
 *   data/exemptions_report.json  — cross-tab counts by exemption × module,
 *                                  company, license, age_group, etc.
 *   data/rare_exemptions.json    — per-file page-level detail for the rare
 *                                  exemption types ((b)(1), (b)(2), (b)(3),
 *                                  (b)(5), (b)(7)(A), (b)(7)(C), (b)(7)(E),
 *                                  (b)(9), (b)(1)(B), (b)(1)(C))
 *   data/index.json              — for individual_url, ican_url, zip_url
 *                                  to render the filename links
 *
 * Renders:
 *   - dashboard summary
 *   - collapsible cross-tab tables
 *   - collapsible rare-exemption section
 *   - filterable / sortable per-file table at bottom
 */

(() => {
  const $ = (id) => document.getElementById(id);

  const state = {
    rows: [],            // joined per-file rollups
    filteredRows: [],
    activeExemptions: new Set(),
    sortKey: "total_markers",
    sortDir: "desc",
  };

  // ---- helpers ----
  function fmtNum(n) {
    if (n == null) return "—";
    if (typeof n === "number" && !Number.isInteger(n)) return n.toFixed(1);
    return n.toLocaleString();
  }

  function sourceFor(row) {
    if (row.individual_url) return { kind: "individual", url: row.individual_url, label: "individual" };
    if (row.ican_url)       return { kind: "ican",       url: row.ican_url,       label: "ICAN" };
    if (row.zip_url)        return { kind: "zip",        url: row.zip_url,        label: "ZIP" };
    return { kind: null, url: null, label: "" };
  }

  function pagesCompact(pages, max = 12) {
    const arr = [...new Set(pages)].sort((a, b) => a - b);
    if (arr.length <= max) return arr.join(", ");
    return arr.slice(0, max).join(", ") + ` … (+${arr.length - max})`;
  }

  // ---- cross-tab rendering ----
  function renderXtabs(report) {
    const root = $("xtab-body");
    root.innerHTML = "";

    // The four one-dimensional rollups
    const oneDims = [
      ["exemption_by_module", "Exemption × Module"],
      ["exemption_by_company", "Exemption × Company"],
      ["exemption_by_license", "Exemption × License"],
      ["exemption_by_age_group", "Exemption × Age Group"],
    ];

    const grid = document.createElement("div");
    grid.className = "xtab-grid";

    for (const [key, label] of oneDims) {
      const section = report[key];
      if (!section) continue;
      const dimVals = Object.keys(section.file_counts).sort();
      // Collect exemptions ordered by total count desc
      const exemptionTotals = {};
      for (const v of dimVals) {
        const inner = section.markers[v] || {};
        for (const [e, c] of Object.entries(inner)) {
          exemptionTotals[e] = (exemptionTotals[e] || 0) + c;
        }
      }
      const exemptions = Object.entries(exemptionTotals)
        .sort((a, b) => b[1] - a[1])
        .map(([e]) => e);

      const wrap = document.createElement("div");
      wrap.className = "xtab";
      wrap.innerHTML = `<h3>${label}</h3>`;

      const table = document.createElement("table");
      let html = "<thead><tr><th>Exemption</th>";
      for (const v of dimVals) html += `<th>${v}</th>`;
      html += "<th>Total</th></tr></thead><tbody>";

      for (const e of exemptions) {
        html += `<tr><td><code>${e}</code></td>`;
        let rowTotal = 0;
        for (const v of dimVals) {
          const c = (section.markers[v] || {})[e] || 0;
          rowTotal += c;
          html += `<td>${fmtNum(c)}</td>`;
        }
        html += `<td><strong>${fmtNum(rowTotal)}</strong></td></tr>`;
      }
      // Totals + files + rate rows
      const colMarkerTotals = dimVals.map((v) =>
        Object.values(section.markers[v] || {}).reduce((s, n) => s + n, 0)
      );
      const colFileTotals = dimVals.map((v) => section.file_counts[v] || 0);
      const grand = colMarkerTotals.reduce((s, n) => s + n, 0);
      const grandFiles = colFileTotals.reduce((s, n) => s + n, 0);

      html += `<tr><td><strong>Markers total</strong></td>`;
      for (const v of colMarkerTotals) html += `<td><strong>${fmtNum(v)}</strong></td>`;
      html += `<td><strong>${fmtNum(grand)}</strong></td></tr>`;

      html += `</tbody><tfoot>`;
      html += `<tr><td>Files</td>`;
      for (const v of colFileTotals) html += `<td>${fmtNum(v)}</td>`;
      html += `<td>${fmtNum(grandFiles)}</td></tr>`;

      html += `<tr><td>Markers / file</td>`;
      for (let i = 0; i < dimVals.length; i++) {
        const rate = colFileTotals[i] > 0 ? colMarkerTotals[i] / colFileTotals[i] : 0;
        html += `<td>${fmtNum(rate)}</td>`;
      }
      html += `<td>${fmtNum(grandFiles > 0 ? grand / grandFiles : 0)}</td></tr>`;
      html += `</tfoot>`;

      table.innerHTML = html;
      wrap.appendChild(table);
      grid.appendChild(wrap);
    }

    root.appendChild(grid);
  }

  // ---- rare exemption rendering ----
  function renderRare(rareDoc, phmptUrlMap) {
    const root = $("rare-body");
    root.innerHTML = "";

    const order = Object.entries(rareDoc.summary.by_marker)
      .sort((a, b) => b[1] - a[1]);
    if (order.length === 0) {
      root.textContent = "No rare exemption hits.";
      return;
    }

    const intro = document.createElement("p");
    intro.style.fontSize = "13px";
    intro.style.color = "var(--muted)";
    intro.textContent =
      `${rareDoc.summary.total_occurrences.toLocaleString()} occurrences across ` +
      `${rareDoc.summary.file_marker_combos} file × marker combos. ` +
      `A single file may appear under multiple rare types.`;
    root.appendChild(intro);

    for (const [marker, total] of order) {
      const rows = rareDoc.by_marker[marker] || [];
      const desc = rareDoc.summary.descriptions[marker] || "";

      const wrap = document.createElement("div");
      wrap.className = "rare-marker";
      wrap.innerHTML = `
        <h3><code>${marker}</code> — ${desc}</h3>
        <p class="desc">${total.toLocaleString()} occurrence(s) across ${rows.length} file(s).</p>`;

      const table = document.createElement("table");
      let html = "<thead><tr><th>File</th><th>Module</th><th>Co.</th><th>Lic.</th><th>Hits</th><th>Pages</th><th>Link</th></tr></thead><tbody>";
      for (const r of rows) {
        const pagesStr = r.pages.map((p) => p.page).join(", ");
        const totalHits = r.total_hits;
        // Build link cell: prefer ican_url (no Cloudflare friction) when present
        const url = r.phmpt_url || r.ican_url;
        const filename = r.filename;
        const fileCell = url
          ? `<a href="${url}" target="_blank" rel="noopener">${filename}</a>`
          : filename;
        let linkParts = [];
        if (r.phmpt_url) linkParts.push(`<a href="${r.phmpt_url}" target="_blank" rel="noopener">PHMPT</a>`);
        if (r.ican_url)  linkParts.push(`<a href="${r.ican_url}"  target="_blank" rel="noopener">ICAN</a>`);
        html += `<tr>
          <td class="filename-cell"><code>${filename}</code></td>
          <td>${r.module || ""}</td>
          <td>${r.company || ""}</td>
          <td>${r.license || ""}</td>
          <td>${totalHits}</td>
          <td class="pages-cell">${pagesStr}</td>
          <td>${linkParts.join(" · ") || "—"}</td>
        </tr>`;
      }
      html += "</tbody>";
      table.innerHTML = html;
      wrap.appendChild(table);
      root.appendChild(wrap);
    }
  }

  // ---- per-file table ----
  function applyFilters() {
    const nameQ = $("f-name").value.trim().toLowerCase();
    const mod = $("f-module").value;
    const co = $("f-company").value;
    const lic = $("f-license").value;
    const src = $("f-source").value;
    const minMarkers = parseInt($("f-min").value, 10);
    const hasMinFilter = !Number.isNaN(minMarkers);
    const pMin = parseInt($("f-pages-min").value, 10);
    const pMax = parseInt($("f-pages-max").value, 10);
    const hasPMin = !Number.isNaN(pMin);
    const hasPMax = !Number.isNaN(pMax);
    // Segmented control (radio group): "" = all files, "exclude-crf" = hide CRFs
    const crfFilter = document.querySelector('input[name="crf-filter"]:checked')?.value || "";

    state.filteredRows = state.rows.filter((r) => {
      if (state.activeExemptions.size > 0) {
        const has = Object.keys(r.by_marker || {}).some((e) => state.activeExemptions.has(e));
        if (!has) return false;
      }
      if (nameQ && !r.filename.toLowerCase().includes(nameQ)) return false;
      if (mod && r.moduleLabel !== mod) return false;
      if (co && r.company !== co) return false;
      if (lic && r.license !== lic) return false;
      if (src && r.source.kind !== src) return false;
      if (hasMinFilter && r.total_markers < minMarkers) return false;
      if (hasPMin && (r.total_pages == null || r.total_pages < pMin)) return false;
      if (hasPMax && (r.total_pages == null || r.total_pages > pMax)) return false;
      // CRFs (Case Report Forms) carry per-patient identifying info
      // that's heavily (b)(6)-redacted by design; hiding them lets you
      // see the rest of the corpus clearly.
      if (crfFilter === "exclude-crf" && r.isCRF) return false;
      return true;
    });

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

  function renderRows() {
    const tbody = $("rows");
    tbody.innerHTML = "";

    // Sum displayed markers honoring active-exemption chip filter
    const totalMarkers = state.filteredRows.reduce((s, r) => {
      if (state.activeExemptions.size === 0) return s + r.total_markers;
      let n = 0;
      for (const [e, c] of Object.entries(r.by_marker || {})) {
        if (state.activeExemptions.has(e)) n += c;
      }
      return s + n;
    }, 0);

    $("count").textContent =
      `${state.filteredRows.length.toLocaleString()} files · ${totalMarkers.toLocaleString()} markers shown`;

    for (const r of state.filteredRows) {
      const tr = document.createElement("tr");

      // File cell
      const tdFile = document.createElement("td");
      tdFile.className = "files-cell";
      const linkPart = document.createElement("a");
      linkPart.href = r.source.url || "#";
      linkPart.textContent = r.filename;
      if (r.source.url) {
        linkPart.target = "_blank";
        linkPart.rel = "noopener";
      } else {
        linkPart.style.color = "var(--muted)";
        linkPart.style.cursor = "default";
        linkPart.onclick = (e) => e.preventDefault();
      }
      tdFile.appendChild(linkPart);
      if (r.source.kind) {
        const tag = document.createElement("span");
        tag.className = "src-tag " + r.source.kind;
        tag.textContent = r.source.label;
        tdFile.appendChild(tag);
      }
      tr.appendChild(tdFile);

      const tdMod = document.createElement("td"); tdMod.textContent = r.moduleLabel || ""; tr.appendChild(tdMod);
      const tdCo = document.createElement("td"); tdCo.textContent = r.company || ""; tr.appendChild(tdCo);
      const tdLic = document.createElement("td"); tdLic.textContent = r.license || ""; tr.appendChild(tdLic);

      const tdPages = document.createElement("td");
      tdPages.className = "num";
      tdPages.textContent = fmtNum(r.total_pages);
      tr.appendChild(tdPages);

      // Markers (respecting active exemption filter)
      let displayedMarkers = r.total_markers;
      if (state.activeExemptions.size > 0) {
        displayedMarkers = 0;
        for (const [e, c] of Object.entries(r.by_marker || {})) {
          if (state.activeExemptions.has(e)) displayedMarkers += c;
        }
      }
      const tdMarkers = document.createElement("td");
      tdMarkers.className = "hits-num";
      tdMarkers.textContent = fmtNum(displayedMarkers);
      tr.appendChild(tdMarkers);

      // Markers per page
      const rate = r.total_pages > 0 ? displayedMarkers / r.total_pages : null;
      const tdRate = document.createElement("td");
      tdRate.className = "num";
      tdRate.textContent = rate == null ? "—" : rate.toFixed(2);
      tr.appendChild(tdRate);

      // Breakdown
      const tdBreak = document.createElement("td");
      tdBreak.className = "markers-cell";
      const entries = Object.entries(r.by_marker || {})
        .filter(([e]) => state.activeExemptions.size === 0 || state.activeExemptions.has(e))
        .sort((a, b) => b[1] - a[1]);
      for (const [e, c] of entries) {
        const line = document.createElement("span");
        line.className = "marker-line";
        line.textContent = `${e} × ${c.toLocaleString()}`;
        tdBreak.appendChild(line);
      }
      tr.appendChild(tdBreak);

      tbody.appendChild(tr);
    }
  }

  function renderExemptionChips(globalCounts) {
    const strip = $("exemption-strip");
    strip.innerHTML = "";

    const all = document.createElement("span");
    all.className = "ex-chip" + (state.activeExemptions.size === 0 ? " active" : "");
    all.textContent = "All exemptions";
    all.onclick = () => { state.activeExemptions.clear(); rerender(); };
    strip.appendChild(all);

    for (const [e, c] of globalCounts) {
      const chip = document.createElement("span");
      chip.className = "ex-chip" + (state.activeExemptions.has(e) ? " active" : "");
      chip.innerHTML = `<code>${e}</code><span class="count">${c.toLocaleString()}</span>`;
      chip.onclick = () => {
        if (state.activeExemptions.has(e)) state.activeExemptions.delete(e);
        else state.activeExemptions.add(e);
        rerender();
      };
      strip.appendChild(chip);
    }
  }

  function rerender() {
    applyFilters();
    renderRows();
    Array.from(document.querySelectorAll(".ex-chip")).forEach((el) => {
      const isAll = el.textContent.startsWith("All exemptions");
      let isActive;
      if (isAll) {
        isActive = state.activeExemptions.size === 0;
      } else {
        const code = el.querySelector("code")?.textContent;
        isActive = code && state.activeExemptions.has(code);
      }
      el.classList.toggle("active", !!isActive);
    });
  }

  // ---- bootstrap ----
  async function init() {
    const [slim, report, rareDoc, indexDoc] = await Promise.all([
      fetch("data/exemptions.json").then((r) => r.json()),
      fetch("data/exemptions_report.json").then((r) => r.json()),
      fetch("data/rare_exemptions.json").then((r) => r.json()),
      fetch("data/index.json").then((r) => r.json()),
    ]);

    // index lookup by id, for URL fallbacks
    const byId = new Map();
    for (const r of indexDoc) byId.set(r.id, r);

    // Build phmpt URL map (filename → individual_url or zip_url) for the
    // rare-exemption table's link column
    const phmptUrlMap = {};
    for (const r of indexDoc) {
      const f = r.filename;
      if (!f) continue;
      if (!phmptUrlMap[f] || (!phmptUrlMap[f].individual_url && r.individual_url)) {
        phmptUrlMap[f] = {
          individual_url: r.individual_url,
          zip_url: r.zip_url,
        };
      }
    }

    // Enrich slim records with URL data + computed fields
    state.rows = (slim.files || []).filter((f) => f.total_markers > 0).map((f) => {
      const idxRow = byId.get(f.id) || {};
      const row = {
        id: f.id,
        filename: f.filename,
        module: f.module,
        moduleLabel: f.module || "Individual",
        company: f.company || idxRow.company,
        license: f.license || idxRow.license,
        total_pages: f.total_pages || idxRow.page_count,
        total_markers: f.total_markers || 0,
        by_marker: f.by_marker || {},
        individual_url: idxRow.individual_url,
        ican_url: idxRow.ican_url,
        zip_url: idxRow.zip_url,
      };
      row.markers_per_page = row.total_pages > 0 ? row.total_markers / row.total_pages : null;
      row.source = sourceFor(row);
      // Flag Case Report Forms by filename. Convention: "_CRF_" appears
      // in the canonical eCTD section in the filename, but the substring
      // "CRF" alone (case-insensitive) is unique enough — it never shows
      // up in non-CRF FOIA filenames in this corpus.
      row.isCRF = /CRF/i.test(row.filename);
      return row;
    });

    // Compute global per-exemption counts for the chip strip
    const globalEx = new Map();
    for (const r of state.rows) {
      for (const [e, c] of Object.entries(r.by_marker)) {
        globalEx.set(e, (globalEx.get(e) || 0) + c);
      }
    }
    const sortedEx = [...globalEx.entries()].sort((a, b) => b[1] - a[1]);

    // Summary
    const totalFiles = state.rows.length;
    const totalMarkers = state.rows.reduce((s, r) => s + r.total_markers, 0);
    const crfFiles = state.rows.filter((r) => r.isCRF).length;
    const crfMarkers = state.rows.filter((r) => r.isCRF).reduce((s, r) => s + r.total_markers, 0);
    const haveIndividual = state.rows.filter((r) => r.source.kind === "individual").length;
    const haveIcan = state.rows.filter((r) => r.source.kind === "ican").length;
    const onlyZip = state.rows.filter((r) => r.source.kind === "zip").length;
    $("summary").innerHTML =
      `<span class="stat"><span class="num">${totalFiles.toLocaleString()}</span> files with at least one redaction marker</span>` +
      `<span class="stat"><span class="num">${totalMarkers.toLocaleString()}</span> total markers</span>` +
      `<span class="stat" title="CRFs carry per-patient identifying data that's heavily (b)(6)-redacted by design — use the Category filter to hide them.">` +
        `<span class="num">${crfFiles.toLocaleString()}</span> are CRFs ` +
        `(${((crfFiles / totalFiles) * 100).toFixed(0)}% of files, ` +
        `${((crfMarkers / totalMarkers) * 100).toFixed(0)}% of markers)` +
      `</span>` +
      `<span class="stat"><span class="num">${haveIndividual.toLocaleString()}</span> individual link</span>` +
      `<span class="stat"><span class="num">${haveIcan.toLocaleString()}</span> via ICAN</span>` +
      `<span class="stat"><span class="num">${onlyZip.toLocaleString()}</span> ZIP-only</span>`;

    // Cross-tabs (closed by default; user expands)
    renderXtabs(report);

    // Rare panel
    renderRare(rareDoc, phmptUrlMap);

    renderExemptionChips(sortedEx);

    const filterIds = ["f-name", "f-module", "f-company", "f-license", "f-source", "f-min", "f-pages-min", "f-pages-max"];
    filterIds.forEach((id) => {
      const ev = (id === "f-name" || id === "f-min" || id === "f-pages-min" || id === "f-pages-max") ? "input" : "change";
      $(id).addEventListener(ev, rerender);
    });
    // Segmented control radios — re-render whenever the selected segment changes
    document.querySelectorAll('input[name="crf-filter"]').forEach((el) => {
      el.addEventListener("change", rerender);
    });
    $("reset").addEventListener("click", () => {
      filterIds.forEach((id) => { $(id).value = ""; });
      // Reset segmented control to the first option ("All files")
      const firstRadio = document.querySelector('input[name="crf-filter"][value=""]');
      if (firstRadio) firstRadio.checked = true;
      state.activeExemptions.clear();
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
          state.sortDir = (k === "filename" || k === "moduleLabel" || k === "company" || k === "license") ? "asc" : "desc";
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
