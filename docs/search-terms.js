"use strict";

// Search-terms page. Loads docs/data/tags.json (scraped + curated from
// coviddocuments.com's tag dropdown), renders an A–Z grouped, filterable list.
// Each tag links to the main index's full-text search via #search=<phrase>.

const LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ#".split("");

let ALL = [];           // all tag objects
const $ = (id) => document.getElementById(id);

function tagHref(t) {
  // t.search already quotes multi-word phrases for exact-phrase matching.
  return "index.html#search=" + encodeURIComponent(t.search);
}

function render(filterText) {
  const q = (filterText || "").trim().toLowerCase();
  const shown = q ? ALL.filter((t) => t.name.toLowerCase().includes(q)) : ALL;

  // group by letter
  const byLetter = new Map(LETTERS.map((l) => [l, []]));
  for (const t of shown) (byLetter.get(t.letter) || byLetter.get("#")).push(t);

  // alphabet bar — letters with matches are links, others greyed
  const bar = $("alpha-bar");
  bar.innerHTML = "";
  for (const l of LETTERS) {
    const has = byLetter.get(l).length > 0;
    const a = document.createElement("a");
    a.textContent = l === "#" ? "0–9" : l;
    if (has) { a.href = "#letter-" + l; }
    else { a.className = "empty"; a.setAttribute("aria-disabled", "true"); }
    bar.appendChild(a);
  }

  // list
  const list = $("tag-list");
  list.innerHTML = "";
  if (!shown.length) {
    const d = document.createElement("div");
    d.className = "tg-empty";
    d.textContent = `No tags match “${filterText}”.`;
    list.appendChild(d);
    return;
  }
  const frag = document.createDocumentFragment();
  for (const l of LETTERS) {
    const group = byLetter.get(l);
    if (!group.length) continue;
    const sec = document.createElement("section");
    sec.className = "tg-group";
    const h = document.createElement("h2");
    h.id = "letter-" + l;
    h.textContent = l === "#" ? "0–9" : l;
    sec.appendChild(h);
    const wrap = document.createElement("div");
    wrap.className = "tg-list";
    for (const t of group) {
      const a = document.createElement("a");
      a.className = "tg";
      a.href = tagHref(t);
      a.title = `Search this corpus for ${t.search}`;
      a.textContent = t.name;
      wrap.appendChild(a);
    }
    sec.appendChild(wrap);
    frag.appendChild(sec);
  }
  list.appendChild(frag);
}

function updateSummary(shownCount) {
  const total = ALL.length;
  const txt = shownCount === total
    ? `<span class="num">${total.toLocaleString()}</span> tags`
    : `<span class="num">${shownCount.toLocaleString()}</span> of ${total.toLocaleString()} tags`;
  $("summary").innerHTML =
    txt +
    ` &middot; <span class="tg-note">pre-built searches harvested from coviddocuments.com — ` +
    `a tag is the companion site's label, not a guarantee the exact word appears in a document.</span>`;
}

function debounce(fn, ms) {
  let h;
  return (...a) => { clearTimeout(h); h = setTimeout(() => fn(...a), ms); };
}

async function load() {
  const r = await fetch("data/tags.json");
  const data = await r.json();
  ALL = data.tags || [];

  // native autocomplete over all tag names
  const dl = $("tag-options");
  const frag = document.createDocumentFragment();
  for (const t of ALL) {
    const o = document.createElement("option");
    o.value = t.name;
    frag.appendChild(o);
  }
  dl.appendChild(frag);

  render("");
  updateSummary(ALL.length);

  const input = $("tag-filter");
  input.addEventListener("input", debounce(() => {
    const q = input.value;
    render(q);
    const shown = q.trim()
      ? ALL.filter((t) => t.name.toLowerCase().includes(q.trim().toLowerCase())).length
      : ALL.length;
    updateSummary(shown);
  }, 120));
}

load().catch((e) => {
  $("summary").textContent = "Failed to load tags: " + e;
});
