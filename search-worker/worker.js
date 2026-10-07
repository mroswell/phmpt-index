// Read-only search proxy for the FOIA document index.
// The browser calls this Worker; the Worker forwards to the Bonsai OpenSearch
// cluster using the BONSAI_URL secret (which embeds the credentials), so the
// search key never reaches the browser. Only _search is exposed.

const INDEX = "foia_pages";
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

    const from = Math.min(Math.max(parseInt(u.searchParams.get("from") || "0", 10) || 0, 0), 1000);
    const size = Math.min(Math.max(parseInt(u.searchParams.get("size") || "20", 10) || 20, 1), 50);

    const filter = [];
    for (const f of ["company", "license", "module", "age_group", "study", "batch_code"]) {
      const v = u.searchParams.get(f);
      if (v) filter.push({ term: { [f]: v } });
    }

    const body = {
      from, size, track_total_hits: 10000,
      query: {
        bool: {
          must: [{
            multi_match: {
              query: q,
              fields: ["text", "text.exact^2", "bates_text", "filename^1.5"],
              type: "best_fields",
            },
          }],
          filter,
        },
      },
      highlight: { fields: { text: { fragment_size: 180, number_of_fragments: 1 } } },
      _source: ["doc_id", "filename", "page", "total_pages", "company", "license",
                "age_group", "module", "study", "url", "bates_start", "bates_end"],
    };

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
