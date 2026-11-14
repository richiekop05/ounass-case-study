// Wording shared across pages: prices, departments, plain-words event lines.

import type { Dept, StoreEvent } from "./types";

export function price(n: number, currency = "AED"): string {
  return `${currency} ${Math.round(n).toLocaleString("en-US")}`;
}

export const DEPT_LABEL: Record<Dept, string> = { women: "Women", men: "Men", all: "Shop all" };

function humanise(v: unknown): string {
  const s = String(v ?? "").replace(/_/g, " ");
  return s.slice(0, 1).toUpperCase() + s.slice(1);
}

export const REFINE_WORD: Record<string, string> = {
  budget: "under AED 3,000",
  neutral: "neutrals",
  covered: "more covered",
};

const FACET_WORD: Record<string, string> = {
  category: "category",
  colour_primary: "colour",
  occasion: "occasion",
  formality: "formality",
  season: "season",
  aesthetic: "aesthetic",
  sleeve_length: "sleeve length",
  length: "length",
};

/** "An evening event" -> "an evening event"; "Work" -> "work". For "for …" and "Styling for: …". */
export function occasionPhrase(label: string): string {
  return /^an? /i.test(label) ? label.charAt(0).toLowerCase() + label.slice(1) : label.toLowerCase();
}

const PLACEMENT_WORD: Record<string, string> = { top_band: "top of the page", grid: "in the grid", product_page: "product page" };

/** One plain-words line for an event in the behind-the-scenes drawer. */
export function eventLine(e: StoreEvent): string {
  const p = e.product;
  const c = e.context || {};
  switch (e.type) {
    case "view":
      return p ? `Viewed ${p}` : "Viewed a piece";
    case "save":
      return p ? `Saved ${p}` : "Saved a piece";
    case "add_to_bag":
      return p ? `Added ${p} to bag` : "Added a piece to bag";
    case "add_look_to_bag": {
      const n = Array.isArray(c.items) ? c.items.length : 0;
      return n ? `Added a look to bag, ${n} pieces` : "Added a look to bag";
    }
    case "occasion_select":
      return `Chose an occasion: ${humanise(c.occasion)}`;
    case "shortlist_view":
      return `Saw a shortlist for ${humanise(c.occasion).toLowerCase()}${c.count !== undefined ? `, ${String(c.count)} pieces` : ""}`;
    case "refine_apply":
      return `${c.on === false ? "Removed" : "Refined by"} ${REFINE_WORD[String(c.refine)] ?? humanise(c.refine)}`;
    case "look_view":
      return p ? `Saw the look for ${p}` : "Saw a look";
    case "swap_out":
      return p ? `Swapped out ${p}` : "Swapped out a piece";
    case "swap_in":
      return p ? `Swapped in ${p}` : "Swapped in a piece";
    case "purchase":
      return p ? `Bought ${p}` : "Bought a piece";
    case "size_select":
      return "Selected a size";
    case "search_referral":
      return p ? `Arrived from a search: ${p}` : "Arrived from a search";
    case "filter_apply": {
      const what = `${FACET_WORD[String(c.facet)] ?? humanise(c.facet).toLowerCase()}: ${humanise(c.value)}`;
      return c.surface === "tag" ? `Tapped a tag, ${what}` : `Filtered by ${what}`;
    }
    case "entry":
      return (
        {
          direct: "Opened the app directly",
          notification: `Arrived from ${c.occasion ? `an ${humanise(c.occasion).toLowerCase()}-edit` : "a"} notification`,
          web_search: "Arrived from a general web search",
          product_search: "Arrived from a product search",
        } as Record<string, string>
      )[String(c.source)] ?? "Arrived";
    case "looks_eligible":
      return "Became eligible for Looks for you";
    case "look_card_view":
      return `Saw a look card (${PLACEMENT_WORD[String(c.placement)] ?? humanise(c.placement)})` + (p ? `: ${p}` : "");
    case "look_card_tap":
      return `Opened a look card (${PLACEMENT_WORD[String(c.placement)] ?? humanise(c.placement)})` + (p ? `: ${p}` : "");
    default:
      return humanise(e.type) + (p ? ` · ${p}` : "");
  }
}

export function eventMeta(e: StoreEvent): string {
  const seed = e.context && (e.context as Record<string, unknown>).sessionStart ? "session start" : null;
  const history = e.seed && !seed ? "history" : null;
  const t = new Date(e.at);
  const time = isNaN(t.getTime()) ? "" : t.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  const c = (e.context || {}) as Record<string, unknown>;
  const where = c.slot ? `${String(c.slot).replace(/_/g, " ")}${c.position ? " #" + String(c.position) : ""}` : null;
  return [e.type, where, seed ?? history, time].filter(Boolean).join(" · ");
}
