// The only way the page talks to the server. Errors carry the server's `message`, shown as written.

export class ApiError extends Error {
  status: number;
  /** One line per problem (e.g. per facet) when the server refuses with a list. */
  problems: string[];
  /** Problems keyed by field ("name", "values.2.label"), when the server says which field. */
  fields: Record<string, string>;
  constructor(message: string, status: number, problems: string[] = [], fields: Record<string, string> = {}) {
    super(message);
    this.status = status;
    this.problems = problems;
    this.fields = fields;
  }
}

/** The message and the problems list of any error, for banners and toasts. */
export function errorInfo(e: unknown): { message: string; problems: string[] } {
  if (e instanceof ApiError) return { message: e.message, problems: e.problems };
  return { message: e instanceof Error ? e.message : String(e), problems: [] };
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError("The app server is not reachable. Is `tagger app` running?", 0);
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const problems = Array.isArray(data?.problems) ? data.problems.map(String) : [];
    const fields = data && typeof data.fields === "object" && data.fields ? data.fields : {};
    throw new ApiError((data && data.message) || `Request failed (${res.status})`, res.status, problems, fields);
  }
  return data as T;
}

export const api = {
  get: <T,>(url: string) => request<T>("GET", url),
  post: <T,>(url: string, body?: unknown) => request<T>("POST", url, body ?? {}),
  put: <T,>(url: string, body?: unknown) => request<T>("PUT", url, body ?? {}),
};

/** Fired on window when products were approved or sent back from somewhere other than the open list
 *  (an Undo in a toast after the page moved on), so an open list reloads. */
export const PRODUCTS_CHANGED = "tagger:products-changed";
