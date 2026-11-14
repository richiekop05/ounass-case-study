// The only way the pages talk to the server. Cookies carry the persona and the session.

import type {
  Card,
  Dept,
  EventType,
  Facet,
  Look,
  LookForYou,
  Occasion,
  Persona,
  Product,
  Refine,
  Shortlist,
  StoreState,
} from "./types";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, {
      method,
      credentials: "same-origin",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError("The store is not reachable just now.", 0);
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError((data && data.message) || `Request failed (${res.status})`, res.status);
  return data as T;
}

export type Filters = Record<string, string[]>;

function filterQuery(dept: Dept, filters: Filters): string {
  const p = new URLSearchParams({ dept });
  for (const [k, vs] of Object.entries(filters)) vs.forEach((v) => p.append(k, v));
  return p.toString();
}

export const api = {
  personas: () => request<{ personas: Persona[]; current: string; session: string }>("GET", "/store/personas"),
  session: (persona: string) => request<{ persona: string; session: string }>("POST", "/store/session", { persona }),
  products: (dept: Dept, filters: Filters) =>
    request<{ products: Card[]; order: "taste" | "new"; count: number }>("GET", "/store/products?" + filterQuery(dept, filters)),
  facets: (dept: Dept) => request<{ facets: Facet[] }>("GET", "/store/facets?dept=" + dept),
  product: (id: string) => request<Product>("GET", "/store/products/" + encodeURIComponent(id)),
  look: (id: string, occasion: string | null) =>
    request<Look>(
      "GET",
      `/store/products/${encodeURIComponent(id)}/look` + (occasion ? "?occasion=" + encodeURIComponent(occasion) : ""),
    ),
  event: (type: EventType, productId: string | null, context: Record<string, unknown> = {}) =>
    request<{ ok: boolean }>("POST", "/store/events", { type, productId, context }),
  /** `load`: this is a page load, when intent changes take effect. Without it the server only reports. */
  state: (load = false, audience: string | null = null) =>
    request<StoreState>("GET", "/store/state" + (load ? "?load=1" + (audience ? "&audience=" + audience : "") : "")),
  occasions: (dept: Dept) =>
    request<{ occasions: Occasion[]; note: string | null }>("GET", "/store/occasions?dept=" + dept),
  shortlist: (occasion: string, dept: Dept, refine: Refine) =>
    request<Shortlist>("POST", "/store/shortlist", { occasion, dept, refine }),
  looksForYou: (dept: Dept, count = 6, exclude: string | null = null) =>
    request<{ eligible: boolean; code: string; reason: string; looks: LookForYou[] }>(
      "GET",
      `/store/looks-for-you?dept=${dept}&count=${count}` + (exclude ? "&exclude_product_id=" + encodeURIComponent(exclude) : ""),
    ),
};
