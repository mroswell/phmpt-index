/* Pharmacovigilance Findings — browse page.
 *
 * Loads data/pharmacovigilance.json (the term-match catalog) and
 * docs/data/index.json (for zip_url fallback when neither individual_url
 * nor ican_url is set on the pharmacovigilance record).
 *
 * Joins on the stable doc id. Renders a filterable, sortable table.
 *
 * Link priority for the filename cell:
 *   individual_url  →  ican_url  →  zip_url (last resort, downloads whole ZIP)
 */

(() => {
  const $ = (id) => document.getElementById(id);

  // ---- state ----
  const state = {
    rows: [],          // joined+enriched per-file records
    filteredRows: [],
    activeTerms: new Set(),  // empty = all terms
    sortKey: "total_hits",
    sortDir: "desc",
  };

  // ---- helpers ----
  function fmtNum(n) { return n == null ? "" : n.toLocaleString(); }

  function pagesCompact(pages, max = 12) {
    const arr = [...new Set(pages)].sort((a, b) => a - b);
    if (arr.length <= max) return arr.join(", ");
    return arr.slice(0, max).join(", ") + ` … (+${arr.length - max})`;
  }

  function sourceFor(row) {
    if (row.individual_url) return { kind: "individual", url: row.individual_url, label: "individual" };
    if (row.ican_url)       return { kind: "ican",       url: row.ican_url,       label: "ICAN" };
    if (row.zip_url)        return { kind: "zip",        url: row.zip_url,        label: "ZIP" };
    return { kind: null, url: null, label: "" };
  }

  // Escape regex special chars so we can highlight a term inside context strings
  function escapeRegExp(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

  function makeContextHTML(matches) {
    // matches: [{page, term, context}, ...]
    // Group by term, show first ~5 per term so things stay bounded
    const byTerm = new Map();
    for (const m of matches) {
      if (!byTerm.has(m.term)) byTerm.set(m.term, []);
      byTerm.get(m.term).push(m);
    }
    const parts = [];
    for (const [term, hits] of byTerm) {
      parts.push(`<div style="margin-bottom:8px"><strong>${term}</strong> (${hits.length} hit${hits.length === 1 ? "" : "s"}):</div>`);
      const pattern = new RegExp(escapeRegExp(term), "gi");
      hits.slice(0, 5).forEach((h) => {
        const safe = h.context.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        const highlighted = safe.replace(pattern, (m) => `<mark>${m}</mark>`);
        parts.push(`<div>p${h.page} — …${highlighted}…</div>`);
      });
      if (hits.length > 5) {
        parts.push(`<div style="color:var(--muted)">…and ${hits.length - 5} more.</div>`);
      }
    }
    return parts.join("");
  }

  // ---- rendering ----
  function renderTermChips(termCounts) {
    const strip = $("term-strip");
    strip.innerHTML = "";

    // "All" chip
    const all = document.createElement("span");
    all.className = "term-chip" + (state.activeTerms.size === 0 ? " active" : "");
    all.textContent = "All terms";
    all.onclick = () => { state.activeTerms.clear(); rerender(); };
    strip.appendChild(all);

    for (const [term, count] of termCounts) {
      const chip = document.createElement("span");
      chip.className = "term-chip" + (state.activeTerms.has(term) ? " active" : "");
      chip.innerHTML = `${term}<span class="count">${count.toLocaleString()}</span>`;
      chip.onclick = () => {
        if (state.activeTerms.has(term)) state.activeTerms.delete(term);
        else state.activeTerms.add(term);
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
    const src = $("f-source").value;

    state.filteredRows = state.rows.filter((r) => {
      if (state.activeTerms.size > 0) {
        const has = Object.keys(r.by_term || {}).some((t) => state.activeTerms.has(t));
        if (!has) return false;
      }
      if (nameQ && !r.filename.toLowerCase().includes(nameQ)) return false;
      if (mod && r.moduleLabel !== mod) return false;
      if (co && r.company !== co) return false;
      if (lic && r.license !== lic) return false;
      if (src && r.source.kind !== src) return false;
      return true;
    });

    // Sort
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

    // Recompute hits-given-active-term-filter for the count column
    const totalHits = state.filteredRows.reduce((s, r) => {
      if (state.activeTerms.size === 0) return s + r.total_hits;
      let n = 0;
      for (const [t, c] of Object.entries(r.by_term)) {
        if (state.activeTerms.has(t)) n += c;
      }
      return s + n;
    }, 0);

    $("count").textContent =
      `${state.filteredRows.length.toLocaleString()} files · ${totalHits.toLocaleString()} hit${totalHits === 1 ? "" : "s"} shown`;

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
      // Context toggle
      const toggle = document.createElement("a");
      toggle.className = "ctx-toggle";
      toggle.href = "#";
      toggle.textContent = " · show context";
      const panel = document.createElement("div");
      panel.className = "ctx-panel";
      let loaded = false;
      toggle.onclick = (e) => {
        e.preventDefault();
        if (!loaded) {
          panel.innerHTML = makeContextHTML(r.matches);
          loaded = true;
        }
        panel.classList.toggle("open");
        toggle.textContent = panel.classList.contains("open") ? " · hide context" : " · show context";
      };
      tdFile.appendChild(toggle);
      tdFile.appendChild(panel);
      tr.appendChild(tdFile);

      // Module / Company / License
      const tdMod = document.createElement("td"); tdMod.textContent = r.moduleLabel || ""; tr.appendChild(tdMod);
      const tdCo = document.createElement("td"); tdCo.textContent = r.company || ""; tr.appendChild(tdCo);
      const tdLic = document.createElement("td"); tdLic.textContent = r.license || ""; tr.appendChild(tdLic);

      // Pages
      const tdPages = document.createElement("td");
      tdPages.className = "num";
      tdPages.textContent = fmtNum(r.total_pages);
      tr.appendChild(tdPages);

      // Hits (under active filter if any)
      const tdHits = document.createElement("td");
      tdHits.className = "hits-num";
      let displayedHits = r.total_hits;
      if (state.activeTerms.size > 0) {
        displayedHits = 0;
        for (const [t, c] of Object.entries(r.by_term)) {
          if (state.activeTerms.has(t)) displayedHits += c;
        }
      }
      tdHits.textContent = fmtNum(displayedHits);
      tr.appendChild(tdHits);

      // Terms breakdown
      const tdTerms = document.createElement("td");
      tdTerms.className = "terms-cell";
      const termEntries = Object.entries(r.by_term)
        .filter(([t]) => state.activeTerms.size === 0 || state.activeTerms.has(t))
        .sort((a, b) => b[1] - a[1]);
      for (const [t, c] of termEntries) {
        const line = document.createElement("span");
        line.className = "term-line";
        line.textContent = `${t} × ${c}`;
        tdTerms.appendChild(line);
      }
      tr.appendChild(tdTerms);

      // Pages w/ hits (compact list, considering term filter)
      const tdPgs = document.createElement("td");
      tdPgs.className = "pages-cell";
      const matchedPages = r.matches
        .filter((m) => state.activeTerms.size === 0 || state.activeTerms.has(m.term))
        .map((m) => m.page);
      tdPgs.textContent = pagesCompact(matchedPages, 12);
      tr.appendChild(tdPgs);

      tbody.appendChild(tr);
    }
  }

  function rerender() {
    applyFilters();
    renderRows();
    // Refresh term chips' active state (count text doesn't change with filters,
    // it's the global per-term hit count)
    Array.from(document.querySelectorAll(".term-chip")).forEach((el) => {
      const isActive =
        (el.textContent.startsWith("All terms") && state.activeTerms.size === 0)
        || state.activeTerms.has(el.firstChild?.textContent);
      el.classList.toggle("active", isActive);
    });
  }

  // ---- bootstrap ----
  async function init() {
    const [pvDoc, indexDoc] = await Promise.all([
      fetch("data/pharmacovigilance.json").then((r) => r.json()),
      fetch("data/index.json").then((r) => r.json()),
    ]);

    // Build id → index-record map for URL fallback (zip_url etc.)
    const byId = new Map();
    for (const r of indexDoc) byId.set(r.id, r);

    // Enrich each pharmacovigilance file record
    state.rows = (pvDoc.files || [])
      .filter((f) => !f.error && (f.matches || []).length > 0)
      .map((f) => {
        const idxRow = byId.get(f.id) || {};
        const by_term = {};
        for (const m of f.matches) {
          by_term[m.term] = (by_term[m.term] || 0) + 1;
        }
        const total_hits = f.matches.length;
        const row = {
          id: f.id,
          filename: f.filename,
          module: f.module,
          moduleLabel: f.module || "Individual",
          batch_code: f.batch_code,
          company: f.company || idxRow.company,
          license: f.license || idxRow.license,
          total_pages: f.total_pages,
          total_hits,
          by_term,
          matches: f.matches,
          individual_url: f.individual_url || idxRow.individual_url,
          ican_url: f.ican_url || idxRow.ican_url,
          zip_url: idxRow.zip_url,
        };
        row.source = sourceFor(row);
        return row;
      });

    // Compute global term counts (across all files, ignoring filters)
    const globalTermCounts = new Map();
    for (const r of state.rows) {
      for (const [t, c] of Object.entries(r.by_term)) {
        globalTermCounts.set(t, (globalTermCounts.get(t) || 0) + c);
      }
    }
    const sortedTermCounts = [...globalTermCounts.entries()].sort((a, b) => b[1] - a[1]);

    // Summary line
    const totalFiles = state.rows.length;
    const totalHits = state.rows.reduce((s, r) => s + r.total_hits, 0);
    const haveIndividual = state.rows.filter((r) => r.source.kind === "individual").length;
    const haveIcan = state.rows.filter((r) => r.source.kind === "ican").length;
    const onlyZip = state.rows.filter((r) => r.source.kind === "zip").length;
    const noLink = state.rows.filter((r) => r.source.kind === null).length;

    $("summary").innerHTML =
      `<span class="stat"><span class="num">${totalFiles.toLocaleString()}</span> files with at least one term hit</span>` +
      `<span class="stat"><span class="num">${totalHits.toLocaleString()}</span> total hits</span>` +
      `<span class="stat"><span class="num">${haveIndividual.toLocaleString()}</span> with individual link</span>` +
      `<span class="stat"><span class="num">${haveIcan.toLocaleString()}</span> via ICAN</span>` +
      `<span class="stat"><span class="num">${onlyZip.toLocaleString()}</span> ZIP-only fallback</span>` +
      (noLink > 0 ? `<span class="stat"><span class="num">${noLink.toLocaleString()}</span> no link</span>` : "");

    renderTermChips(sortedTermCounts);

    // Filter event wiring
    ["f-name", "f-module", "f-company", "f-license", "f-source"].forEach((id) => {
      $(id).addEventListener(id === "f-name" ? "input" : "change", rerender);
    });
    $("reset").addEventListener("click", () => {
      ["f-name", "f-module", "f-company", "f-license", "f-source"].forEach((id) => {
        $(id).value = "";
      });
      state.activeTerms.clear();
      rerender();
    });

    // Sortable headers
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
