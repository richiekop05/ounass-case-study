import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { CardPlaceholder, ProductCard } from "../components/ProductCard";
import { FilterDrawer } from "../components/FilterDrawer";
import { FeatureBand } from "../components/FeatureBand";
import { GridLook } from "../components/LookCards";
import { FACETS, SHORTLIST_PARAM, filtersFrom, filtersKey, filtersQuery } from "../filters";
import { navigate } from "../router";
import { useStore } from "../store";
import type { Card, Dept, Facet, Intent, LookForYou } from "../types";

const LOOK_EVERY = 8; // one look card after every eighth product, at most three per page (Design PRD 5)
const GRID_LOOKS = 3;

/** `intent` is the state in force for this page load; null until the load has been told to the server. */
export function PLP({ dept, params, intent }: { dept: Dept; params: URLSearchParams; intent: Intent | null }) {
  const { logEvent, setProduct, state } = useStore();
  const filters = useMemo(() => filtersFrom(params), [params]);
  const key = filtersKey(filters);
  const shortlist = params.get(SHORTLIST_PARAM) || null;

  const [facets, setFacets] = useState<Facet[] | null>(null);
  const [items, setItems] = useState<{ key: string; cards: Card[]; count: number; order: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openFacet, setOpenFacet] = useState<string | null>(null);
  const [looks, setLooks] = useState<LookForYou[] | null>(null);

  useEffect(() => setProduct(null), [setProduct]);

  useEffect(() => {
    if (!intent) return;
    let live = true;
    setFacets(null);
    api.facets(dept).then(
      (r) => live && setFacets(r.facets),
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [dept, intent]);

  useEffect(() => {
    if (!intent) return;
    let live = true;
    setItems(null);
    setError(null);
    api.products(dept, filtersFrom(new URLSearchParams(key))).then(
      (r) => live && setItems({ key, cards: r.products, count: r.count, order: r.order }),
      (e: Error) => live && setError(e.message),
    );
    return () => {
      live = false;
    };
  }, [dept, key, intent]);

  // Looks for you: eligibility was decided when this page loaded; one call serves the top band and the grid.
  useEffect(() => {
    if (!intent) return;
    let live = true;
    setLooks(null);
    if (intent === "high") {
      setLooks([]);
      return;
    }
    api.looksForYou(dept, 6).then(
      (r) => live && setLooks(r.looks.filter((l) => l.hero)),
      () => live && setLooks([]),
    );
    return () => {
      live = false;
    };
  }, [dept, intent]);
  const leadsWithLooks = state?.lead.module === "looks" && !shortlist;
  const topLooks = looks === null ? null : leadsWithLooks ? looks.slice(0, 3) : [];
  const gridLooks = (looks ?? []).slice(leadsWithLooks ? 3 : 0).slice(0, GRID_LOOKS);

  // Same path: filters and the shortlist change the query only, which is not a new page load.
  const go = (f: typeof filters, sl: string | null = shortlist) => {
    const q = new URLSearchParams(filtersQuery(f).slice(1));
    if (sl) q.set(SHORTLIST_PARAM, sl);
    const qs = q.toString();
    navigate(window.location.pathname + (qs ? "?" + qs : ""), { keepScroll: true });
  };

  const setOccasion = (o: string | null) => {
    if (o) logEvent("occasion_select", null, { occasion: o, surface: "plp" });
    go(filters, o);
  };

  // An occasion chosen earlier (here or on a product page) opens on its shortlist when a listing page loads
  // (engineering PRD V7). Once per load, so "Change" still returns to the chips.
  const reopened = useRef<string | null>(null);
  const chosen = state?.lead.occasionSource === "chosen" ? state.lead.suggestedOccasion : null;
  useEffect(() => {
    if (!intent || !chosen || shortlist || reopened.current === dept + chosen) return;
    reopened.current = dept + chosen;
    const q = new URLSearchParams(window.location.search);
    q.set(SHORTLIST_PARAM, chosen);
    navigate(window.location.pathname + "?" + q.toString(), { keepScroll: true, replace: true });
  }, [intent, chosen, dept]); // eslint-disable-line react-hooks/exhaustive-deps

  const apply = (facetId: string, values: string[]) => {
    const before = new Set(filters[facetId] || []);
    values.filter((v) => !before.has(v)).forEach((v) => logEvent("filter_apply", null, { facet: facetId, value: v }));
    const next = { ...filters, [facetId]: values };
    if (!values.length) delete next[facetId];
    setOpenFacet(null);
    go(next);
  };

  const remove = (facetId: string, value: string) => {
    const next = { ...filters, [facetId]: (filters[facetId] || []).filter((v) => v !== value) };
    if (!next[facetId].length) delete next[facetId];
    go(next);
  };

  const labelOf = (facetId: string, value: string) =>
    facets?.find((f) => f.id === facetId)?.values.find((v) => v.value === value)?.label ??
    value.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

  const pills = FACETS.flatMap((f) => (filters[f.id] || []).map((v) => ({ facet: f.id, value: v })));
  const drawerFacet = openFacet ? facets?.find((f) => f.id === openFacet) : undefined;
  const closeDrawer = useCallback(() => setOpenFacet(null), []);

  return (
    <div className="container plp">
      {intent === "low" && (
        <FeatureBand dept={dept} occasion={shortlist} setOccasion={setOccasion} topLooks={topLooks} />
      )}

      <div className="filter-bar" role="toolbar" aria-label="Filters">
        {FACETS.map((f) => {
          const n = (filters[f.id] || []).length;
          return (
            <button
              key={f.id}
              className={"filter-button" + (n ? " is-active" : "")}
              onClick={() => setOpenFacet(f.id)}
              disabled={!facets}
            >
              {f.label}
              {n ? ` · ${n}` : ""}
            </button>
          );
        })}
      </div>

      {pills.length > 0 && (
        <div className="applied">
          {pills.map((p) => (
            <button key={p.facet + p.value} className="pill pill--applied" onClick={() => remove(p.facet, p.value)}>
              {labelOf(p.facet, p.value)} <span aria-hidden="true">×</span>
              <span className="visually-hidden">Remove</span>
            </button>
          ))}
          <button className="link-button" onClick={() => go({})}>
            Clear all
          </button>
        </div>
      )}

      <div className="grid-head">
        <h1 className="grid-head__title">{items ? (items.order === "taste" ? "Selected for you" : "New in") : "\u00a0"}</h1>
        <span className="grid-head__count">
          {items ? `${items.count} ${items.count === 1 ? "piece" : "pieces"}` : ""}
        </span>
      </div>

      {error ? (
        <p className="message">{error}</p>
      ) : items && items.cards.length === 0 ? (
        <div className="message">
          <p>Nothing matches that yet.</p>
          <button className="link-button link-button--accent" onClick={() => go({})}>
            Clear all filters
          </button>
        </div>
      ) : (
        <div className="grid">
          {items
            ? items.cards.flatMap((c, i) => {
                const out = [<ProductCard key={c.id} card={c} />];
                const n = (i + 1) / LOOK_EVERY;
                const look = Number.isInteger(n) ? gridLooks[n - 1] : undefined;
                if (look) out.push(<GridLook key={"look-" + (look.hero?.id ?? n)} look={look} position={n} />);
                return out;
              })
            : Array.from({ length: 8 }, (_, i) => <CardPlaceholder key={i} />)}
        </div>
      )}

      {drawerFacet && (
        <FilterDrawer
          facet={drawerFacet}
          dept={dept}
          filters={filters}
          onApply={(vs) => apply(drawerFacet.id, vs)}
          onClose={closeDrawer}
        />
      )}
    </div>
  );
}
