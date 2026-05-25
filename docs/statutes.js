/* Statutes & Regulations browse page.
 *
 * Loads data/statutes.json (produced by scripts/scan_statute_references.py)
 * plus data/index.json for the URL fallback.
 *
 * Two chip strips at the top:
 *   - Family (USC / CFR / Section-of-Act / Named act / Public Law /
 *     Court case / International) — click to filter
 *   - Top 30 individual statutes — click to filter to files mentioning
 *     that specific citation
 *
 * Same filename-link priority pattern as the other pages:
 *   individual_url  →  ican_url  →  zip_url
 */

(() => {
  const $ = (id) => document.getElementById(id);

  const state = {
    rows: [],
    filteredRows: [],
    activeFamilies: new Set(),
    activeStatutes: new Set(),
    sortKey: "total_hits",
    sortDir: "desc",
  };

  function fmtNum(n) { return n == null ? "" : n.toLocaleString(); }

  function sourceFor(row) {
    if (row.individual_url) return { kind: "individual", url: row.individual_url, label: "individual" };
    if (row.ican_url)       return { kind: "ican",       url: row.ican_url,       label: "ICAN" };
    if (row.zip_url)        return { kind: "zip",        url: row.zip_url,        label: "ZIP" };
    return { kind: null, url: null, label: "" };
  }

  function escapeRegExp(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

  function makeContextHTML(matches) {
    const byStatute = new Map();
    for (const m of matches) {
      if (!byStatute.has(m.normalized)) byStatute.set(m.normalized, []);
      byStatute.get(m.normalized).push(m);
    }
    const parts = [];
    for (const [stat, hits] of byStatute) {
      parts.push(`<div style="margin-bottom:8px"><strong>${stat}</strong> (${hits.length}):</div>`);
      // Highlight whatever the raw match was (might differ slightly from normalized)
      hits.slice(0, 5).forEach((h) => {
        const safe = h.context.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        const pattern = new RegExp(escapeRegExp(h.raw), "g");
        const highlighted = safe.replace(pattern, (m) => `<mark>${m}</mark>`);
        parts.push(`<div>p${h.page} — …${highlighted}…</div>`);
      });
      if (hits.length > 5) {
        parts.push(`<div style="color:var(--muted)">…and ${hits.length - 5} more.</div>`);
      }
    }
    return parts.join("");
  }

  function renderFamilyChips(familyCounts) {
    const strip = $("family-strip");
    strip.innerHTML = "";

    const all = document.createElement("span");
    all.className = "family-chip" + (state.activeFamilies.size === 0 ? " active" : "");
    all.textContent = "All families";
    all.onclick = () => { state.activeFamilies.clear(); rerender(); };
    strip.appendChild(all);

    for (const [family, count] of familyCounts) {
      const chip = document.createElement("span");
      chip.className = "family-chip" + (state.activeFamilies.has(family) ? " active" : "");
      chip.innerHTML = `${family}<span class="count">${count.toLocaleString()}</span>`;
      chip.dataset.family = family;
      chip.onclick = () => {
        if (state.activeFamilies.has(family)) state.activeFamilies.delete(family);
        else state.activeFamilies.add(family);
        rerender();
      };
      strip.appendChild(chip);
    }
  }

  function renderStatuteChips(statuteCounts) {
    const strip = $("statute-strip");
    strip.innerHTML = "";

    for (const [stat, count] of statuteCounts) {
      const chip = document.createElement("span");
      chip.className = "statute-chip" + (state.activeStatutes.has(stat) ? " active" : "");
      chip.innerHTML = `${stat}<span class="count">${count.toLocaleString()}</span>`;
      chip.dataset.statute = stat;
      chip.onclick = () => {
        if (state.activeStatutes.has(stat)) state.activeStatutes.delete(stat);
        else state.activeStatutes.add(stat);
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
      if (state.activeFamilies.size > 0) {
        const has = (r.matches || []).some((m) => state.activeFamilies.has(m.family));
        if (!has) return false;
      }
      if (state.activeStatutes.size > 0) {
        const has = (r.matches || []).some((m) => state.activeStatutes.has(m.normalized));
        if (!has) return false;
      }
      if (nameQ && !r.filename.toLowerCase().includes(nameQ)) return false;
      if (mod && r.moduleLabel !== mod) return false;
      if (co && r.company !== co) return false;
      if (lic && r.license !== lic) return false;
      if (src && r.source.kind !== src) return false;
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

    const totalHits = state.filteredRows.reduce((s, r) => {
      if (state.activeFamilies.size === 0 && state.activeStatutes.size === 0) {
        return s + r.total_hits;
      }
      const n = (r.matches || []).filter((m) =>
        (state.activeFamilies.size === 0 || state.activeFamilies.has(m.family))
        && (state.activeStatutes.size === 0 || state.activeStatutes.has(m.normalized))
      ).length;
      return s + n;
    }, 0);

    $("count").textContent =
      `${state.filteredRows.length.toLocaleString()} files · ${totalHits.toLocaleString()} citation${totalHits === 1 ? "" : "s"} shown`;

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
          // Respect active filters: only show contexts that match
          const filtered = (r.matches || []).filter((m) =>
            (state.activeFamilies.size === 0 || state.activeFamilies.has(m.family))
            && (state.activeStatutes.size === 0 || state.activeStatutes.has(m.normalized))
          );
          panel.innerHTML = makeContextHTML(filtered);
          loaded = true;
        }
        panel.classList.toggle("open");
        toggle.textContent = panel.classList.contains("open") ? " · hide context" : " · show context";
      };
      tdFile.appendChild(toggle);
      tdFile.appendChild(panel);
      tr.appendChild(tdFile);

      const tdMod = document.createElement("td"); tdMod.textContent = r.moduleLabel || ""; tr.appendChild(tdMod);
      const tdCo = document.createElement("td"); tdCo.textContent = r.company || ""; tr.appendChild(tdCo);
      const tdLic = document.createElement("td"); tdLic.textContent = r.license || ""; tr.appendChild(tdLic);

      const tdPages = document.createElement("td");
      tdPages.className = "num";
      tdPages.textContent = fmtNum(r.total_pages);
      tr.appendChild(tdPages);

      // Citations count (respecting active filters)
      let displayedHits = r.total_hits;
      if (state.activeFamilies.size > 0 || state.activeStatutes.size > 0) {
        displayedHits = (r.matches || []).filter((m) =>
          (state.activeFamilies.size === 0 || state.activeFamilies.has(m.family))
          && (state.activeStatutes.size === 0 || state.activeStatutes.has(m.normalized))
        ).length;
      }
      const tdHits = document.createElement("td");
      tdHits.className = "hits-num";
      tdHits.textContent = fmtNum(displayedHits);
      tr.appendChild(tdHits);

      // Top statutes (per-file breakdown, respecting active filters)
      const tdTop = document.createElement("td");
      tdTop.className = "statutes-cell";
      const perStatute = new Map();
      for (const m of (r.matches || [])) {
        if (state.activeFamilies.size > 0 && !state.activeFamilies.has(m.family)) continue;
        if (state.activeStatutes.size > 0 && !state.activeStatutes.has(m.normalized)) continue;
        perStatute.set(m.normalized, (perStatute.get(m.normalized) || 0) + 1);
      }
      const sorted = [...perStatute.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8);
      for (const [stat, n] of sorted) {
        const line = document.createElement("span");
        line.className = "statute-line";
        line.textContent = `${stat} × ${n}`;
        tdTop.appendChild(line);
      }
      tr.appendChild(tdTop);

      tbody.appendChild(tr);
    }
  }

  function rerender() {
    applyFilters();
    renderRows();
    Array.from(document.querySelectorAll(".family-chip")).forEach((el) => {
      const fam = el.dataset.family;
      const isAll = el.textContent.startsWith("All families");
      const active = isAll ? state.activeFamilies.size === 0 : state.activeFamilies.has(fam);
      el.classList.toggle("active", !!active);
    });
    Array.from(document.querySelectorAll(".statute-chip")).forEach((el) => {
      el.classList.toggle("active", state.activeStatutes.has(el.dataset.statute));
    });
  }

  async function init() {
    const [statDoc, indexDoc] = await Promise.all([
      fetch("data/statutes.json").then((r) => r.json()),
      fetch("data/index.json").then((r) => r.json()),
    ]);

    const byId = new Map();
    for (const r of indexDoc) byId.set(r.id, r);

    state.rows = (statDoc.files || [])
      .filter((f) => !f.error && (f.matches || []).length > 0)
      .map((f) => {
        const idxRow = byId.get(f.id) || {};
        const row = {
          id: f.id,
          filename: f.filename,
          module: f.module,
          moduleLabel: f.module || "Individual",
          company: f.company || idxRow.company,
          license: f.license || idxRow.license,
          total_pages: f.total_pages,
          total_hits: f.matches.length,
          matches: f.matches,
          individual_url: f.individual_url || idxRow.individual_url,
          ican_url: f.ican_url || idxRow.ican_url,
          zip_url: idxRow.zip_url,
        };
        row.source = sourceFor(row);
        return row;
      });

    const familyCounts = Object.entries(statDoc.summary.by_family || {})
      .sort((a, b) => b[1] - a[1]);
    const topStatutes = Object.entries(statDoc.summary.top_30_statutes || {})
      .sort((a, b) => b[1] - a[1]);

    const totalFiles = state.rows.length;
    const totalCit = state.rows.reduce((s, r) => s + r.total_hits, 0);
    const haveIndividual = state.rows.filter((r) => r.source.kind === "individual").length;
    const haveIcan = state.rows.filter((r) => r.source.kind === "ican").length;
    const onlyZip = state.rows.filter((r) => r.source.kind === "zip").length;

    $("summary").innerHTML =
      `<span class="stat"><span class="num">${totalFiles.toLocaleString()}</span> files with at least one citation</span>` +
      `<span class="stat"><span class="num">${totalCit.toLocaleString()}</span> total citations</span>` +
      `<span class="stat"><span class="num">${haveIndividual.toLocaleString()}</span> individual link</span>` +
      `<span class="stat"><span class="num">${haveIcan.toLocaleString()}</span> via ICAN</span>` +
      `<span class="stat"><span class="num">${onlyZip.toLocaleString()}</span> ZIP-only</span>`;

    renderFamilyChips(familyCounts);
    renderStatuteChips(topStatutes);

    ["f-name", "f-module", "f-company", "f-license", "f-source"].forEach((id) => {
      $(id).addEventListener(id === "f-name" ? "input" : "change", rerender);
    });
    $("reset").addEventListener("click", () => {
      ["f-name", "f-module", "f-company", "f-license", "f-source"].forEach((id) => {
        $(id).value = "";
      });
      state.activeFamilies.clear();
      state.activeStatutes.clear();
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
