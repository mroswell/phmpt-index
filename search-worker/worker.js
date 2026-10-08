// Read-only search proxy for the FOIA document index.
// The browser calls this Worker; the Worker forwards to the Bonsai OpenSearch
// cluster using the BONSAI_URL secret (which embeds the credentials), so the
// search key never reaches the browser. Only _search is exposed.

const INDEX = "foia_pages_v3";
const ALLOW_ORIGINS = new Set([
  "https://foia.coviddocuments.com",
  "https://coviddocuments.com",
  "https://mroswell.github.io",
  "http://localhost:8765",
  "http://localhost:8791",
]);

export default {
  async fetch(req, env) {
    const origin = req.headers.get("Origin") || "";
    const cors = {
      "Access-Control-Allow-Origin": ALLOW_ORIGINS.has(origin) ? origin : "https://foia.coviddocuments.com",
      "Access-Control-Allow-Methods": "GET, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
      "Vary": "Origin",
    };
    if (req.method === "OPTIONS") return new Response(null, { headers: cors });
    if (req.method !== "GET") return json({ error: "GET only" }, 405, cors);
    if (!env.BONSAI_URL) return json({ error: "search not configured" }, 503, cors);

    const u = new URL(req.url);
    const q = (u.searchParams.get("q") || "").slice(0, 200).trim();
    if (!q) return json({ total: 0, hits: [] }, 200, cors);

    // A query wrapped in double quotes means exact-phrase: all words must
    // appear adjacent and in order (match_phrase), not OR'd as separate terms.
    const isPhrase = q.length >= 2 && q.startsWith('"') && q.endsWith('"');
    const queryText = isPhrase ? q.slice(1, -1).trim() : q;
    if (!queryText) return json({ total: 0, hits: [] }, 200, cors);

    const from = Math.min(Math.max(parseInt(u.searchParams.get("from") || "0", 10) || 0, 0), 1000);
    const size = Math.min(Math.max(parseInt(u.searchParams.get("size") || "20", 10) || 20, 1), 200);
    // group=doc consolidates page-level hits into one entry per document.
    const group = u.searchParams.get("group") === "doc";

    const filter = [];
    for (const f of ["company", "license", "module", "age_group", "study", "batch_code"]) {
      const raw = u.searchParams.get(f);
      if (!raw) continue;
      // Comma-split so multi-select filters (e.g. module) OR their values;
      // a single value behaves exactly like the old `term` filter.
      const vals = raw.split(",").map((s) => s.trim()).filter(Boolean);
      if (vals.length) filter.push({ terms: { [f]: vals } });
    }

    const body = {
      from, size, track_total_hits: 10000,
      query: {
        bool: {
          must: [{
            multi_match: {
              query: queryText,
              fields: ["text", "text.exact^2", "bates_text", "filename^1.5"],
              type: isPhrase ? "phrase" : "best_fields",
            },
          }],
          filter,
        },
      },
      highlight: { fields: { text: { fragment_size: 180, number_of_fragments: 1 } } },
      _source: ["doc_id", "filename", "page", "total_pages", "company", "license",
                "age_group", "module", "study", "url", "bates_start", "bates_end"],
    };

    if (group) {
      // Collapse to one hit per document; inner_hits carries the matching
      // pages (sorted), and the cardinality agg gives the document count so
      // the client can paginate over documents rather than pages.
      body.collapse = {
        field: "doc_id",
        inner_hits: {
          name: "pages",
          size: 50,
          _source: ["page"],
          sort: [{ page: { order: "asc" } }],
        },
      };
      body.aggs = { doc_count: { cardinality: { field: "doc_id" } } };
    }

    // Workers' fetch ignores userinfo in the URL, so convert embedded
    // credentials into an explicit Authorization header.
    const bu = new URL(env.BONSAI_URL);
    const auth = "Basic " + btoa(`${decodeURIComponent(bu.username)}:${decodeURIComponent(bu.password)}`);
    bu.username = ""; bu.password = "";
    const target = `${bu.origin}/${INDEX}/_search`;

    let resp;
    try {
      resp = await fetch(target, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Authorization": auth },
        body: JSON.stringify(body),
      });
    } catch (e) {
      return json({ error: "search backend unreachable" }, 502, cors);
    }
    if (!resp.ok) {
      const detail = await resp.text().catch(() => "");
      return json({ error: "search backend error", status: resp.status, detail: detail.slice(0, 200) }, 502, cors);
    }

    const data = await resp.json();

    if (group) {
      const docs = (data.hits?.hits || []).map((h) => {
        const inner = h.inner_hits?.pages?.hits;
        const pages = (inner?.hits || [])
          .map((p) => p._source.page)
          .sort((a, b) => a - b);
        return {
          ...h._source,
          score: h._score,
          snippet: (h.highlight?.text || [])[0] || "",
          pages,
          pages_total: inner?.total?.value ?? pages.length,
        };
      });
      return json({
        total: data.aggregations?.doc_count?.value ?? docs.length, // distinct documents
        pages_matched: data.hits?.total?.value ?? 0,
        took: data.took,
        hits: docs,
      }, 200, cors);
    }

    const hits = (data.hits?.hits || []).map((h) => ({
      ...h._source,
      score: h._score,
      snippet: (h.highlight?.text || [])[0] || "",
    }));
    return json({ total: data.hits?.total?.value ?? 0, took: data.took, hits }, 200, cors);
  },
};

function json(obj, status, cors) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { ...cors, "Content-Type": "application/json", "Cache-Control": "public, max-age=60" },
  });
}
