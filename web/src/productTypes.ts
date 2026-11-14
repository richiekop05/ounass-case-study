// Shapes returned by GET /api/products/{id}, shared by the detail page and its components (and
// later the Live tag page). Also the small helpers that turn them into on-screen wording.

export type AnswerValue = { id: number; label: string } | { number: number } | { text: string };

export type Answer = {
  state: "value" | "unknown" | "not_applicable";
  values: AnswerValue[];
  status: string;
  confidence: number | null;
  evidence: "text" | "image" | "both" | "rule" | null;
  quote: string | null;
  reason: string | null;
  reviewReason: string | null;
};

/** `exclusive`: a value like "all-season" that cannot be combined with any other value of the facet. */
export type FacetValue = { id: number; label: string; definition: string | null; exclusive?: boolean };

export type Facet = {
  id: string;
  name: string;
  definition: string | null;
  type: "enum" | "ordinal" | "boolean" | "number" | "text";
  multi: boolean;
  maxValues: number | null;
  unit: string | null;
  rule: boolean;
  policy: string;
  required: boolean;
  level: number | null;
  values: FacetValue[];
  answer: Answer | null;
  flagged: boolean;
  flagReason: "low_confidence" | "quote_not_found" | "missing_required" | "text_policy" | null;
  quoteFound: boolean;
};

export type CategoryInfo = {
  id: string;
  path: string[];
  confidence: number | null;
  level: number;
  status: string;
  flagged: boolean;
  runnerUp: { id: string; path: string[] } | null;
};

export type TextBlock = { label: string; text: string };
export type Photo = { n: number; url: string | null };

export type ProductDetailData = {
  id: string;
  title: string;
  brand: string;
  url: string;
  state: "ready" | "needs_review" | "approved";
  isPublished: boolean;
  approvedAt: string | null;
  aiRunId: number | null;
  currentRunId: number | null;
  nFlagged: number;
  text: TextBlock[];
  photos: Photo[];
  category: CategoryInfo;
  facets: Facet[];
  saved: SavedReview | null;
};

/** What the reviewer chose for a facet: keep the tagger's answer, or a new one. */
export type FacetDecision =
  | { kind: "approved" }
  | { kind: "changed"; state: "value" | "unknown"; values: AnswerValue[] };

/** The reviewer's decisions as the server stores and accepts them (PUT review, approve-detail). */
export type ApiDecision = { decision: "approved" | "changed" | "unknown"; values?: (number | string)[] };
export type SavedReview = { facets: Record<string, ApiDecision>; categoryReviewed: boolean };

/** Saved decisions (GET /api/products/{id} `saved`) as the page's decision state. */
export function decisionsFromSaved(facets: Facet[], saved: SavedReview | null): Record<string, FacetDecision> {
  const out: Record<string, FacetDecision> = {};
  if (!saved) return out;
  for (const [fid, d] of Object.entries(saved.facets)) {
    const f = facets.find((x) => x.id === fid);
    if (!f || f.rule) continue;
    const taggerHasValue = !!f.answer && f.answer.state === "value" && f.answer.values.length > 0;
    if (d.decision === "approved") out[fid] = { kind: "approved" };
    else if (d.decision === "unknown")
      // approving an empty answer is stored as unknown: show it as approved again
      out[fid] = taggerHasValue ? { kind: "changed", state: "unknown", values: [] } : { kind: "approved" };
    else {
      const values: AnswerValue[] = [];
      for (const v of d.values ?? []) {
        if (f.type === "number") values.push({ number: Number(v) });
        else if (f.type === "text") values.push({ text: String(v) });
        else {
          const o = f.values.find((x) => x.id === Number(v));
          if (o) values.push({ id: o.id, label: o.label });
        }
      }
      out[fid] = { kind: "changed", state: "value", values };
    }
  }
  return out;
}

/** The page's decision state as the API body's `facets`. */
export function decisionsToApi(facets: Facet[], dec: Record<string, FacetDecision>): Record<string, ApiDecision> {
  const out: Record<string, ApiDecision> = {};
  for (const [fid, d] of Object.entries(dec)) {
    const f = facets.find((x) => x.id === fid);
    if (!f) continue;
    if (d.kind === "approved") out[fid] = { decision: "approved" };
    else if (d.state === "unknown" || d.values.length === 0) out[fid] = { decision: "unknown" };
    else
      out[fid] = {
        decision: "changed",
        values: d.values.flatMap((v): (number | string)[] => {
          if ("number" in v) return [v.number];
          if ("text" in v) return [v.text];
          // the facet's own list id (an answer's id can differ from the list's: match by label too)
          const o = f.values.find((x) => x.id === v.id) ?? f.values.find((x) => x.label === v.label);
          return o ? [o.id] : [v.id];
        }),
      };
  }
  return out;
}

/** A stable text form of the decisions, to tell whether anything changed since the last save. */
export function decisionsKey(dec: Record<string, FacetDecision>) {
  return JSON.stringify(
    Object.keys(dec)
      .sort()
      .map((k) => {
        const d = dec[k];
        return d.kind === "approved" ? [k, "a"] : [k, d.state, d.values.map(valueKey).sort()];
      }),
  );
}

export type CategoryDecision = { kind: "approved" } | { kind: "changed"; id: string; path: string[] };

/** The value the page currently shows for a facet: the reviewer's change, else the tagger's answer. */
export type ShownValue = { state: "value" | "unknown" | "not_applicable" | "none"; values: AnswerValue[] };

export function shownValue(f: Facet, d: FacetDecision | undefined): ShownValue {
  if (d?.kind === "changed") return { state: d.state, values: d.values };
  if (!f.answer) return { state: "none", values: [] };
  return { state: f.answer.state, values: f.answer.values };
}

export function hasValue(v: ShownValue) {
  return v.state === "value" && v.values.length > 0;
}

export function valueKey(v: AnswerValue) {
  // by label: labels are unique within a facet, and an answer's value id can differ from the list's
  if ("id" in v) return "l" + v.label;
  if ("number" in v) return "n" + v.number;
  return "t" + v.text;
}

export function sameValues(a: AnswerValue[], b: AnswerValue[]) {
  if (a.length !== b.length) return false;
  const ka = a.map(valueKey).sort();
  const kb = b.map(valueKey).sort();
  return ka.every((k, i) => k === kb[i]);
}

export function valueText(v: AnswerValue, unit: string | null) {
  if ("id" in v) return v.label;
  if ("number" in v) return unit ? `${v.number} ${unit}` : String(v.number);
  return v.text;
}

/** One line of text for a value: labels joined, or "Unknown" / "Not applicable". */
export function shownText(v: ShownValue, unit: string | null) {
  if (v.state === "not_applicable") return "Not applicable";
  if (!hasValue(v)) return "Unknown";
  return v.values.map((x) => valueText(x, unit)).join(", ");
}

export const EVIDENCE_LABEL: Record<string, string> = { text: "Text", image: "Image", both: "Both" };

/** The tagger's reason, without internal prefixes such as "derive:price_band:" and without the
 *  "quote not found" suffix (the row shows its own warning for that). */
export function plainReason(reason: string | null) {
  if (!reason) return "";
  return reason
    .replace(/^derive:[^:]*:\s*/, "")
    .replace(/;?\s*quote not found in text\.?$/i, "")
    .trim();
}

/** Why a facet is in review, in plain words, for the reasons the row does not already show. */
export const FLAG_REASON: Record<string, string> = {
  missing_required: "Required, but the tagger found no value.",
  text_policy: "This facet must come from the product text, but the answer relied on the photos.",
};

export function isLow(confidence: number | null, level: number | null) {
  return confidence !== null && level !== null && confidence < level;
}
