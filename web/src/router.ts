import { useEffect, useState } from "react";

// A small router: the page lives in the path (/products, /products/OU-1, /live …) and the list
// filters in the query string, so a filtered view survives a reload and the back button (PRD 5).

export type Route = { path: string; params: URLSearchParams };

function current(): Route {
  return { path: window.location.pathname, params: new URLSearchParams(window.location.search) };
}

const listeners = new Set<() => void>();

type Guard = () => Promise<boolean>;
let guard: Guard | null = null;

/** A page with unsaved edits sets a guard; leaving to another page asks it first (Design PRD 13). */
export function setLeaveGuard(g: Guard | null) {
  guard = g;
}

export function navigate(to: string, opts: { replace?: boolean } = {}) {
  if (to === window.location.pathname + window.location.search) return;
  const samePath = to.split("?")[0] === window.location.pathname;
  if (guard && !samePath) {
    const g = guard;
    void g().then((ok) => {
      if (ok) {
        guard = null;
        go(to, opts);
      }
    });
    return;
  }
  go(to, opts);
}

function go(to: string, opts: { replace?: boolean }) {
  if (opts.replace) window.history.replaceState(null, "", to);
  else window.history.pushState(null, "", to);
  listeners.forEach((fn) => fn());
}

/** Change some query parameters on the current path (empty values are removed). */
export function setParams(patch: Record<string, string | null | undefined>, opts: { replace?: boolean } = {}) {
  const p = new URLSearchParams(window.location.search);
  for (const [k, v] of Object.entries(patch)) {
    if (v === null || v === undefined || v === "") p.delete(k);
    else p.set(k, v);
  }
  const qs = p.toString();
  navigate(window.location.pathname + (qs ? "?" + qs : ""), opts);
}

export function useRoute(): Route {
  const [route, setRoute] = useState(current);
  useEffect(() => {
    const update = () => setRoute(current());
    listeners.add(update);
    window.addEventListener("popstate", update);
    return () => {
      listeners.delete(update);
      window.removeEventListener("popstate", update);
    };
  }, []);
  return route;
}
