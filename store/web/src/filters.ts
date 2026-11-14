import type { Filters } from "./api";

// The eight PLP facets, in bar order. Ids match the query-string keys the server reads.
export const FACETS: { id: string; label: string }[] = [
  { id: "category", label: "Category" },
  { id: "colour_primary", label: "Colour" },
  { id: "occasion", label: "Occasion" },
  { id: "formality", label: "Formality" },
  { id: "season", label: "Season" },
  { id: "aesthetic", label: "Aesthetic" },
  { id: "sleeve_length", label: "Sleeve length" },
  { id: "length", label: "Length" },
];

export const FACET_IDS = new Set(FACETS.map((f) => f.id));

export function filtersFrom(params: URLSearchParams): Filters {
  const out: Filters = {};
  for (const { id } of FACETS) {
    const vs = params.getAll(id).filter(Boolean);
    if (vs.length) out[id] = [...new Set(vs)];
  }
  return out;
}

export function filtersQuery(f: Filters): string {
  const p = new URLSearchParams();
  for (const { id } of FACETS) (f[id] || []).forEach((v) => p.append(id, v));
  const s = p.toString();
  return s ? "?" + s : "";
}

export function filtersKey(f: Filters): string {
  return filtersQuery(f);
}

/** URL key for the PLP occasion shortlist. Not "occasion": that key is the Occasion facet filter. */
export const SHORTLIST_PARAM = "shortlist";
