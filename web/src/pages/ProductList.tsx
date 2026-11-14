import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent } from "react";
import { api, errorInfo, PRODUCTS_CHANGED } from "../api";
import { Chip } from "../components/Chip";
import { useConfirm } from "../components/ConfirmDialog";
import { ConfidenceBadge } from "../components/ConfidenceBadge";
import { StatusBadge } from "../components/StatusBadge";
import { useToast } from "../components/Toast";
import { TopBar } from "../components/TopBar";
import { navigate, setParams, useRoute } from "../router";
import "./ProductList.css";

// Products and Approved tabs (Design PRD 5). One table, one row per product; filters live in the
// query string so a filtered view survives a reload and the back button.

export type Summary = {
  ready: number;
  needsReview: number;
  approved: number;
  tagged: number;
  scraped: number;
  defaultLevel: number | null;
  categoryLevel: number | null;
  categoriseModel: string | null;
  tagModel: string | null;
  latestRunId: string | number | null;
};

type Row = {
  id: string;
  title: string;
  brand: string;
  categoryShort: string;
  categoryFlagged: boolean;
  state: "ready" | "needs_review" | "approved";
  nFacets: number;
  nFlagged: number;
  nResolved?: number;
  flaggedNames: string[];
  lowestConfidence: number | null;
  lowestIsLow: boolean;
  approvedAt: string | null;
  url: string;
  thumb: string | null;
};
type Page = { rows: Row[]; total: number; page: number; pageSize: number };

type Facet = {
  id: string;
  name: string;
  type: "enum" | "ordinal" | "boolean" | "number" | "text";
  values: { id: number | string; label: string }[];
};
type CatNode = { id: string; path: string; name: string; depth: number; count: number };

const FILTER_KEYS = ["state", "q", "category", "facet", "value", "flagged", "ids"] as const;
const SKELETON_ROWS = 6;
const FADE_MS = 150; // --motion: the approved row fades out, then leaves the table
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// The facet list and category tree change rarely: load them once per page load.
let facetsOnce: Promise<Facet[]> | null = null;
let treeOnce: Promise<CatNode[]> | null = null;
function loadFacets() {
  facetsOnce ??= api.get<{ facets: Facet[] }>("/api/facets").then((r) => r.facets);
  facetsOnce.catch(() => (facetsOnce = null));
  return facetsOnce;
}
function loadTree() {
  treeOnce ??= api.get<{ nodes: CatNode[] }>("/api/categories/tree").then((r) => r.nodes);
  treeOnce.catch(() => (treeOnce = null));
  return treeOnce;
}

function fmtDate(iso: string | null) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
}

function categoryLabel(path: string) {
  const parts = path.split(" / ");
  if (parts.length > 1 && parts[0] === "All") parts.shift();
  return parts.join(" › ");
}

/** Condition options for a facet (Design PRD 5): its values, or has a value / unknown, then needs review. */
function conditionOptions(f: Facet | undefined): { v: string; l: string }[] {
  if (!f) return [];
  const opts =
    f.values.length > 0 && f.type !== "number" && f.type !== "text"
      ? f.values.map((o) => ({ v: "v:" + o.id, l: "is " + o.label }))
      : [
          { v: "has", l: "has a value" },
          { v: "unknown", l: "unknown" },
        ];
  return [...opts, { v: "review", l: "needs review" }];
}

function openProduct(id: string, isProducts = true) {
  navigate((isProducts ? "/products/" : "/approved/") + encodeURIComponent(id));
}

export function ProductList({
  tab,
  onLoaded,
  onChanged,
}: {
  tab: "products" | "approved";
  onLoaded?: () => void;
  /** after an approve or undo: refresh the rail counts */
  onChanged?: () => void;
}) {
  const { params } = useRoute();
  const isProducts = tab === "products";

  const state = isProducts ? params.get("state") ?? "" : "";
  const q = params.get("q") ?? "";
  const category = params.get("category") ?? "";
  const facet = params.get("facet") ?? "";
  const value = params.get("value") ?? "";
  const flagged = params.get("flagged") === "true";
  const ids = params.get("ids") ?? "";
  const sort = params.get("sort") === "brand" ? "brand" : "review";
  const page = Math.max(1, parseInt(params.get("page") ?? "1", 10) || 1);

  // what the server sees: the filters shared by both calls, and the full list query
  const filterQs = useMemo(() => {
    const p = new URLSearchParams();
    if (q) p.set("q", q);
    if (category) p.set("category", category);
    if (facet && (value || flagged)) {
      p.set("facet", facet);
      if (value) p.set("value", value);
      if (flagged) p.set("flagged", "true");
    } else if (flagged) p.set("flagged", "true");
    if (ids) p.set("ids", ids);
    return p.toString();
  }, [q, category, facet, value, flagged, ids]);
  const listQs = useMemo(() => {
    const p = new URLSearchParams(filterQs);
    p.set("tab", tab);
    if (state === "ready" || state === "review") p.set("state", state);
    p.set("sort", sort);
    p.set("page", String(page));
    return p.toString();
  }, [filterQs, tab, state, sort, page]);

  // The detail page's back link and previous / next follow this list's filter and sort.
  useEffect(() => {
    try {
      sessionStorage.setItem("tagger:listQuery", params.toString());
    } catch {
      /* storage blocked: the detail page falls back to the unfiltered list */
    }
  }, [params]);

  // ---- scroll position: kept per list and query, restored on coming back from a product (Design PRD 14)
  const scrollRef = useRef<HTMLDivElement>(null);
  const scrollKey = "tagger:listScroll:" + tab + "?" + listQs;
  const restored = useRef<string | null>(null);
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const save = () => {
      try { sessionStorage.setItem(scrollKey, String(el.scrollTop)); } catch { /* storage blocked */ }
    };
    el.addEventListener("scroll", save, { passive: true });
    return () => el.removeEventListener("scroll", save);
  }, [scrollKey]);

  // ---- data
  const [data, setData] = useState<Page | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [facets, setFacets] = useState<Facet[]>([]);
  const [tree, setTree] = useState<CatNode[]>([]);
  const [reloadSeq, setReloadSeq] = useState(0);
  const onLoadedRef = useRef(onLoaded);
  onLoadedRef.current = onLoaded;
  const toast = useToast();
  const confirm = useConfirm();

  useEffect(() => {
    const again = () => setReloadSeq((n) => n + 1);
    window.addEventListener(PRODUCTS_CHANGED, again);
    return () => window.removeEventListener(PRODUCTS_CHANGED, again);
  }, []);

  useEffect(() => {
    loadFacets().then(setFacets).catch((e: Error) => setError(e.message));
    loadTree().then(setTree).catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    let live = true;
    setLoading(true);
    setError(null);
    api
      .get<Page>("/api/products?" + listQs)
      .then((r) => {
        if (!live) return;
        setData(r);
        onLoadedRef.current?.();
        if (restored.current !== scrollKey) {
          restored.current = scrollKey;
          let top = 0;
          try { top = Number(sessionStorage.getItem(scrollKey) || 0); } catch { /* storage blocked */ }
          requestAnimationFrame(() => scrollRef.current?.scrollTo({ top }));
        }
      })
      .catch((e: Error) => live && setError(e.message))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [listQs, reloadSeq]);

  useEffect(() => {
    let live = true;
    api
      .get<Summary>("/api/summary" + (filterQs ? "?" + filterQs : ""))
      .then((s) => live && setSummary(s))
      .catch(() => live && setSummary(null));
    return () => {
      live = false;
    };
  }, [filterQs, reloadSeq]);

  // ---- search: typed text updates `q` after a pause, replacing the history entry
  const [searchText, setSearchText] = useState(q);
  const pushedQ = useRef(q);
  useEffect(() => {
    if (q !== pushedQ.current) {
      pushedQ.current = q;
      setSearchText(q);
    }
  }, [q]);
  useEffect(() => {
    const next = searchText.trim();
    if (next === pushedQ.current) return;
    const t = window.setTimeout(() => {
      pushedQ.current = next;
      setParams({ q: next, page: null }, { replace: true });
    }, 250);
    return () => window.clearTimeout(t);
  }, [searchText]);

  // ---- filters
  const setFilter = (patch: Record<string, string | null>) => setParams({ ...patch, page: null });
  const currentFacet = facets.find((f) => f.id === facet);
  const condOpts = conditionOptions(currentFacet);
  const condValue = flagged ? "review" : value === "has" || value === "unknown" ? value : value ? "v:" + value : "";
  const setCondition = (v: string) => {
    if (v === "review") setFilter({ flagged: "true", value: null });
    else if (v.startsWith("v:")) setFilter({ value: v.slice(2), flagged: null });
    else setFilter({ value: v || null, flagged: null });
  };

  const chips: { key: string; label: string; remove: () => void }[] = [];
  if (isProducts && (state === "ready" || state === "review"))
    chips.push({ key: "state", label: state === "ready" ? "Ready" : "Needs review", remove: () => setFilter({ state: null }) });
  if (ids) chips.push({ key: "ids", label: "Selected products", remove: () => setFilter({ ids: null }) });
  if (category) {
    const node = tree.find((n) => n.id === category);
    chips.push({ key: "category", label: node ? categoryLabel(node.path) : "Category", remove: () => setFilter({ category: null }) });
  }
  if (facet && (value || flagged)) {
    const cond = condOpts.find((o) => o.v === condValue);
    const name = currentFacet?.name ?? "Facet";
    chips.push({
      key: "facet",
      label: `${name} ${cond ? cond.l : flagged ? "needs review" : ""}`.trim(),
      remove: () => setFilter({ facet: null, value: null, flagged: null }),
    });
  } else if (flagged) {
    chips.push({ key: "flagged", label: "Any facet needs review", remove: () => setFilter({ flagged: null }) });
  }
  if (q) chips.push({ key: "q", label: `“${q}”`, remove: () => setFilter({ q: null }) });
  const anyFilter = chips.length > 0;
  const clearAll = () => {
    const patch: Record<string, null> = { page: null };
    FILTER_KEYS.forEach((k) => (patch[k] = null));
    pushedQ.current = "";
    setSearchText("");
    setParams(patch);
  };

  // ---- approve from the list (Design PRD 5 "After approving from the list")
  const [fading, setFading] = useState<Set<string>>(new Set());
  const [approving, setApproving] = useState<Set<string>>(new Set());
  const [approvingAll, setApprovingAll] = useState(false);
  const [failed, setFailed] = useState<{ id: string; message: string }[] | null>(null);
  const reload = () => setReloadSeq((n) => n + 1);
  const mark = (set: typeof setFading, id: string, on: boolean) =>
    set((s) => {
      const next = new Set(s);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });

  const undo = async (ids: string[]) => {
    try {
      for (const id of ids) await api.post("/api/products/" + encodeURIComponent(id) + "/unapprove");
    } catch (e) {
      toast({ message: errorInfo(e).message });
    }
    reload();
    onChanged?.();
  };

  const approveRow = async (r: Row) => {
    mark(setApproving, r.id, true);
    try {
      await api.post("/api/products/" + encodeURIComponent(r.id) + "/approve", {});
    } catch (e) {
      mark(setApproving, r.id, false);
      const { message, problems } = errorInfo(e);
      // say what blocks it (e.g. "Season: all-season cannot be combined with other values")
      toast({ message: [message, ...problems].join(" ") });
      return;
    }
    mark(setFading, r.id, true);
    onChanged?.();
    window.setTimeout(() => {
      setData((d) => (d ? { ...d, rows: d.rows.filter((x) => x.id !== r.id), total: Math.max(0, d.total - 1) } : d));
      mark(setFading, r.id, false);
      mark(setApproving, r.id, false);
      setSummary((s) => (s ? { ...s, ready: Math.max(0, s.ready - 1), approved: s.approved + 1 } : s));
    }, FADE_MS);
    toast({
      message: "Approved and published",
      actions: [
        { label: "View on website", onClick: () => window.open(r.url, "_blank", "noopener,noreferrer") },
        { label: "Undo", onClick: () => void undo([r.id]) },
      ],
    });
  };

  const approveAll = async () => {
    const n = readyCount;
    const ok = await confirm({
      title: `Approve and publish ${n} ${n === 1 ? "product" : "products"}?`,
      body: "Every Ready product in the current view goes live on the website straight away. You can undo for 8 seconds.",
      ok: `Approve ${n}`,
    });
    if (!ok) return;
    setApprovingAll(true);
    setFailed(null);
    // the current list filters, as the server's list reads them
    const f = new URLSearchParams(filterQs);
    const body = {
      state: state === "ready" || state === "review" ? state : "",
      q: f.get("q") ?? "",
      category: f.get("category") ?? "",
      facet: f.get("facet") ?? "",
      value: f.get("value") ?? "",
      flagged: f.get("flagged") === "true",
      ids: f.get("ids") ?? "",
    };
    try {
      const r = await api.post<{ approved: string[]; failed: { id: string; message: string }[] }>(
        "/api/products/approve-ready",
        body,
      );
      if (r.failed.length > 0) setFailed(r.failed);
      if (r.approved.length > 0) {
        const ids = r.approved;
        toast({
          message: `${ids.length} ${ids.length === 1 ? "product" : "products"} approved and published`,
          actions: [{ label: "Undo", onClick: () => void undo(ids) }],
        });
      }
    } catch (e) {
      const { message, problems } = errorInfo(e);
      setFailed([{ id: "", message: [message, ...problems].join(" ") }]);
    } finally {
      setApprovingAll(false);
      reload();
      onChanged?.();
    }
  };

  // ---- top bar
  const title = isProducts ? "Products" : "Approved";
  const summaryText = summary
    ? isProducts
      ? `${summary.ready} ready · ${summary.needsReview} need review · ${summary.tagged} of ${summary.scraped} tagged`
      : `${summary.approved} approved · live on the website`
    : "";
  // a Ready product is never in the "Needs review" view
  const readyCount = state === "review" ? 0 : summary?.ready ?? 0;

  const rows = data?.rows ?? [];
  const total = data?.total ?? 0;
  const pageSize = data?.pageSize ?? 50;
  const showSkeleton = loading && !error;

  return (
    <>
      <TopBar
        left={
          <>
            <h1 className="title">{title}</h1>
            <span className="list-summary">{summaryText}</span>
          </>
        }
        right={
          <>
            <input
              className="list-search"
              type="search"
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              placeholder="Search title, brand or ID"
              aria-label="Search title, brand or ID"
            />
            {isProducts && (
              <button
                type="button"
                className="btn btn-secondary"
                disabled={readyCount === 0 || approvingAll}
                title={readyCount ? "Approve and publish every Ready product in this view" : "No Ready products in this view"}
                onClick={() => void approveAll()}
              >
                {approvingAll ? "Saving…" : `Approve all ready (${readyCount})`}
              </button>
            )}
          </>
        }
      />

      <div className="list-filters">
        {isProducts && (
          <div className="list-segment" role="group" aria-label="State">
            {[
              ["", "All"],
              ["ready", "Ready"],
              ["review", "Needs review"],
            ].map(([k, l]) => (
              <button
                key={k}
                type="button"
                className={"list-segment-item" + (state === k ? " is-active" : "")}
                aria-pressed={state === k}
                onClick={() => setFilter({ state: k || null })}
              >
                {l}
              </button>
            ))}
          </div>
        )}
        <select
          className="list-select list-select-category"
          value={category}
          onChange={(e) => setFilter({ category: e.target.value || null })}
          title="Category"
          aria-label="Category"
        >
          <option value="">All categories</option>
          {tree
            .filter((n) => !(n.depth === 0 && n.path === "All"))
            .map((n) => (
              <option key={n.id} value={n.id}>
                {"   ".repeat(Math.max(0, n.depth - 1)) + n.name + ` (${n.count})`}
              </option>
            ))}
        </select>
        <select
          className="list-select"
          value={facet}
          onChange={(e) => setFilter({ facet: e.target.value || null, value: null, flagged: null })}
          title="Facet"
          aria-label="Facet"
        >
          <option value="">Any facet</option>
          {facets.map((f) => (
            <option key={f.id} value={f.id}>
              {f.name}
            </option>
          ))}
        </select>
        {currentFacet && (
          <select
            className="list-select"
            value={condValue}
            onChange={(e) => setCondition(e.target.value)}
            aria-label={`Condition for ${currentFacet.name}`}
          >
            <option value="">Choose…</option>
            {condOpts.map((o) => (
              <option key={o.v} value={o.v}>
                {o.l}
              </option>
            ))}
          </select>
        )}
        <label className="list-sort">
          Sort
          <select
            className="list-select"
            value={sort}
            onChange={(e) => setParams({ sort: e.target.value === "brand" ? "brand" : null, page: null })}
          >
            <option value="review">Needs review first</option>
            <option value="brand">Brand A to Z</option>
          </select>
        </label>
      </div>

      {anyFilter && (
        <div className="list-chips">
          {chips.map((c) => (
            <Chip key={c.key} onRemove={c.remove}>
              {c.label}
            </Chip>
          ))}
          <button type="button" className="list-linkbtn" onClick={clearAll}>
            Clear all
          </button>
        </div>
      )}

      <div className="shell-scroll" ref={scrollRef}>
        <div className="list-page">
          {error && (
            <div className="list-error" role="alert">
              {error}
            </div>
          )}
          {failed && failed.length > 0 && (
            <div className="error-banner list-banner" role="alert">
              <div className="error-banner-text">
                {failed.some((x) => x.id) ? `${failed.length} could not be approved` : failed[0].message}
                {failed.some((x) => x.id) && (
                  <ul className="error-banner-list">
                    {failed.map((x) => (
                      <li key={x.id}>
                        {x.id}: {x.message}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <button type="button" className="btn btn-quiet btn-sm list-banner-close" aria-label="Dismiss" onClick={() => setFailed(null)}>
                ✕
              </button>
            </div>
          )}
          {!error && (
            <div className="list-table">
              <div className="list-grid list-head label">
                <span>Photo</span>
                <span>Product</span>
                <span>Category</span>
                <span>{isProducts ? "Facets" : "Approved"}</span>
                <span>Lowest confidence</span>
                <span className="list-right">{isProducts ? "Action" : "Website"}</span>
              </div>

              {showSkeleton &&
                Array.from({ length: SKELETON_ROWS }, (_, i) => (
                  <div key={i} className="list-grid list-row" aria-hidden="true">
                    <div className="list-thumb" />
                    <div className="list-stack">
                      <span className="skeleton list-sk list-sk-lg" />
                      <span className="skeleton list-sk list-sk-sm" />
                    </div>
                    <span className="skeleton list-sk list-sk-md" />
                    <span className="skeleton list-sk list-sk-md" />
                    <span className="skeleton list-sk list-sk-sm" />
                    <div className="list-right">
                      <span className="skeleton list-sk-btn" />
                    </div>
                  </div>
                ))}

              {!showSkeleton &&
                rows.map((r) => (
                  <ProductRow
                    key={r.id}
                    row={r}
                    isProducts={isProducts}
                    fading={fading.has(r.id)}
                    busy={approving.has(r.id)}
                    onApprove={() => void approveRow(r)}
                  />
                ))}

              {!showSkeleton && rows.length === 0 && (
                <div className="list-empty">
                  <span className="list-empty-title">
                    {anyFilter || page > 1
                      ? "No products match these filters"
                      : isProducts
                        ? "Everything tagged so far is approved"
                        : "Nothing approved yet"}
                  </span>
                  {(anyFilter || page > 1) && (
                    <button type="button" className="list-linkbtn" onClick={clearAll}>
                      Clear all filters
                    </button>
                  )}
                  {!anyFilter && page === 1 && isProducts && (
                    <button type="button" className="list-linkbtn" onClick={() => navigate("/approved")}>
                      Go to Approved
                    </button>
                  )}
                </div>
              )}
            </div>
          )}

          {!error && total > pageSize && (
            <div className="list-pager">
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                disabled={page <= 1 || loading}
                onClick={() => setParams({ page: page - 1 > 1 ? String(page - 1) : null })}
              >
                Previous
              </button>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                disabled={page * pageSize >= total || loading}
                onClick={() => setParams({ page: String(page + 1) })}
              >
                Next
              </button>
              <span className="muted small">
                {(page - 1) * pageSize + 1}–{Math.min(page * pageSize, total)} of {total}
              </span>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

function ProductRow({
  row: r,
  isProducts,
  fading,
  busy,
  onApprove,
}: {
  row: Row;
  isProducts: boolean;
  fading: boolean;
  busy: boolean;
  onApprove: () => void;
}) {
  const [photoFailed, setPhotoFailed] = useState(false);
  const open = () => openProduct(r.id, isProducts);
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      open();
    }
  };
  const stop = (e: MouseEvent) => e.stopPropagation();

  return (
    <div
      className={"list-grid list-row is-clickable" + (fading ? " is-fading" : "")}
      role="button" tabIndex={0} onClick={open} onKeyDown={onKey}>
      <div className="list-thumb">
        {r.thumb && !photoFailed && (
          <img src={r.thumb} alt={r.title} loading="lazy" onError={() => setPhotoFailed(true)} />
        )}
      </div>
      <div className="list-stack">
        <span className="list-title">{r.title}</span>
        <span className="list-sub">
          {r.brand} · {r.id}
        </span>
      </div>
      <div className="list-stack list-category">
        <span className="list-ellipsis">{r.categoryShort}</span>
        {r.categoryFlagged && r.state !== "approved" && <StatusBadge status="category-review" />}
      </div>
      {isProducts ? <FacetsCell row={r} /> : <span>{fmtDate(r.approvedAt)}</span>}
      <div>
        <ConfidenceBadge value={r.lowestConfidence} low={r.lowestIsLow} />
      </div>
      <div className="list-right">
        {isProducts && r.state === "ready" && (
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy}
            onClick={(e) => {
              e.stopPropagation();
              onApprove();
            }}
          >
            {busy && !fading ? "Saving…" : "Approve"}
          </button>
        )}
        {isProducts && r.state !== "ready" && (
          <button
            type="button"
            className="btn btn-review"
            onClick={(e) => {
              e.stopPropagation();
              open();
            }}
          >
            Review
          </button>
        )}
        {!isProducts && (
          <a className="list-site" href={r.url} target="_blank" rel="noopener noreferrer" onClick={stop}>
            View on website ↗
          </a>
        )}
      </div>
    </div>
  );
}

function FacetsCell({ row: r }: { row: Row }) {
  if (r.state === "ready" || (r.nFlagged === 0 && r.lowestConfidence !== null)) {
    const done = r.nResolved ?? 0;
    return (
      <span className="list-facets-text muted">
        {done > 0 ? `${done} reviewed · ${r.nFacets - done} confident` : `${r.nFacets} of ${r.nFacets} confident`}
      </span>
    );
  }
  if (r.nFlagged === 0) return <span className="list-facets-text muted">Not tagged yet</span>;
  const shown = r.flaggedNames.slice(0, 3);
  const more = Math.max(r.nFlagged, r.flaggedNames.length) - shown.length;
  return (
    <div className="list-facets">
      <span className="list-facets-text">
        {`${r.nFlagged} of ${r.nFacets} need review`}
        {(r.nResolved ?? 0) > 0 && <span className="muted">{` · ${r.nResolved} reviewed`}</span>}
      </span>
      {shown.map((n) => (
        <Chip key={n} tone="flag">
          {n}
        </Chip>
      ))}
      {more > 0 && <span className="list-more">+{more}</span>}
    </div>
  );
}
