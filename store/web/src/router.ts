import { useEffect, useState } from "react";

// A small router: the page lives in the path (/women, /p/{id}) and PLP filters in the query string,
// so a filtered view survives a reload and the back button.

export type Route = { path: string; params: URLSearchParams };

function current(): Route {
  return { path: window.location.pathname, params: new URLSearchParams(window.location.search) };
}

const listeners = new Set<() => void>();

export function navigate(
  to: string,
  opts: { replace?: boolean; keepScroll?: boolean; state?: Record<string, unknown> } = {},
) {
  if (to === window.location.pathname + window.location.search) return;
  const state = opts.state ?? null;
  if (opts.replace) window.history.replaceState(state, "", to);
  else window.history.pushState(state, "", to);
  listeners.forEach((fn) => fn());
  if (!opts.keepScroll) window.scrollTo(0, 0);
}

/** Use on <a href> so links stay real links (open in new tab works) but route in-page on a plain click. */
export function linkClick(e: React.MouseEvent<HTMLAnchorElement>) {
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  const href = e.currentTarget.getAttribute("href");
  if (!href || !href.startsWith("/")) return;
  e.preventDefault();
  navigate(href);
}

/** The surface that opened this page ("look", …), carried in history state; "grid" when unknown. */
export function openedFrom(): string {
  const s = window.history.state as { surface?: unknown } | null;
  return typeof s?.surface === "string" ? s.surface : "grid";
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
