# "Individual download" filter

## Context

The Individual column is gone; the filename cell is now the link to
phmpt.org's individual page when `individual_url` is populated. About
**5,059 of 8,069 rows (63%)** have a link; the other **3,010** are
bundled in zips but were never published as standalone downloads.

Right now the only way to *see* which rows are which is to look at
whether the filename has the little ↗ icon — there's no filter for it.
This adds a three-state dropdown so you can narrow to "has individual
download" or "zip only" in one click, and the choice round-trips through
the URL hash for shareable permalinks.

## What it looks like

A new select in the filter bar, matching the existing Company / License /
Age controls visually and functionally:

```
Individual download
[ All                       ▾ ]
  All
  Has individual download
  Zip only
```

## Files to touch

| File | Change |
| --- | --- |
| `docs/index.html` | Add the new `<label class="filter">` block after the existing Age dropdown |
| `docs/app.js` | Wire up filter state, predicate, URL serialization, reset |

## `docs/index.html`

After the existing **Age group** filter (`#f-age`), add:

```html
<label class="filter">
  <span>Individual download</span>
  <select id="f-individual">
    <option value="">All</option>
    <option value="yes">Has individual download</option>
    <option value="no">Zip only</option>
  </select>
</label>
```

## `docs/app.js`

Five small touches, all mirroring patterns already used for the existing
Company / License / Age filters:

1. **`FIELD_PARAMS`** — add `"f-individual": "indiv"` so the choice
   round-trips through the URL hash as `&indiv=yes` or `&indiv=no`.
2. **`applyFilters`** — read `const individual = $("f-individual").value;`
   alongside the other reads, then add the predicate inside the filter
   loop:
   ```js
   if (individual === "yes" && !r.individual_url) return false;
   if (individual === "no" && r.individual_url) return false;
   ```
3. **`init`** — add `"f-individual"` to the list of IDs whose `change`
   event re-runs `applyFilters`.
4. **`resetAllFilters`** — add `"f-individual"` to the list of IDs whose
   `.value` is cleared on reset.
5. No `applyParamsToControls` change needed — that function already
   iterates `FIELD_PARAMS` to restore values from the URL, so adding the
   entry in (1) covers loading from URL and saved searches automatically.

## Verification

- Open `http://localhost:8765/`, hard-refresh.
- New "Individual download" dropdown appears between Age group and the
  Date inputs.
- Default state ("All") shows 8,069 files; "Has individual download"
  narrows to 5,059; "Zip only" narrows to 3,010.
- Save a search with the filter set to "Zip only"; reset; re-apply —
  filter restores.
- Set "Zip only" and click **Copy link**; open the URL in a new tab —
  filter restores from the hash.
- Combine with another filter (e.g., M3 module + Zip only) and confirm
  counts compose sensibly.
