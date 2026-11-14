import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, errorInfo, PRODUCTS_CHANGED } from "../api";
import { useConfirm } from "../components/ConfirmDialog";
import { useToast } from "../components/Toast";
import { TopBar } from "../components/TopBar";
import { navigate, setParams, useRoute } from "../router";
import "./Confidence.css";

// Confidence tab (Design PRD 8, Build PRD 6): the level each facet's answers must reach to be trusted, how
// much of the catalog reaches it, and a preview of what a change moves before it is applied. Lowering the
// category level tags the held products that now clear it (decided 14 Nov 2026), shown in the preview.

type Share = { value: number | null; num: number; den: number };
type Reviewed = { reviewed: number; unchanged: number; changed: number; changedConfident: number };
type Row = {
  id: string; name: string; definition: string | null; rule: boolean; required: boolean;
  level: number | null; isDefault: boolean; confident: Share | null; meanConfidence: number | null;
  unknown: Share; needsReview: number | null; reviewed: Reviewed;
};
type Data = {
  default: number;
  category: { level: number; confident: Share; meanConfidence: number | null; needsReview: number; reviewed: Reviewed };
  facets: Row[];
  outcomes: Reviewed;
  products: { ready: number; needsReview: number };
};
type Preview = {
  facets: { id: string; name: string; from: number; to: number; confidentFrom: Share; confidentTo: Share }[];
  category: { from: number; to: number; confidentFrom: Share; confidentTo: Share } | null;
  toReady: number; toReview: number; heldToTag: number; tagCostUsd: number;
};
type Previous = { levels: Record<string, number | null>; default: number; category: number };
type Body = { levels?: Record<string, number | null>; default?: number; category?: number };
type CatNode = { id: string; path: string; name: string; depth: number };

const CAT = "__category";
const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${Math.round(v * 100)}%`);
const lvl = (v: number) => v.toFixed(2);
const plural = (n: number, one: string, many = one + "s") => `${n} ${n === 1 ? one : many}`;

let treeOnce: Promise<CatNode[]> | null = null;
function loadTree() {
  treeOnce ??= api.get<{ nodes: CatNode[] }>("/api/categories/tree").then((r) => r.nodes);
  treeOnce.catch(() => (treeOnce = null));
  return treeOnce;
}

function bodyOf(drafts: Record<string, number>, dflt: number | null): Body {
  const levels: Record<string, number> = {};
  let category: number | undefined;
  for (const [k, v] of Object.entries(drafts)) {
    if (k === CAT) category = v;
    else levels[k] = v;
  }
  return { ...(Object.keys(levels).length ? { levels } : {}), ...(category !== undefined ? { category } : {}),
           ...(dflt !== null ? { default: dflt } : {}) };
}

function moveText(p: Preview) {
  const parts: string[] = [];
  if (p.toReady) parts.push(`${plural(p.toReady, "product")} move from Needs review to Ready`);
  if (p.toReview) parts.push(`${plural(p.toReview, "product")} move from Ready to Needs review`);
  if (p.heldToTag) parts.push(`${plural(p.heldToTag, "held product")} will be tagged now (about $${p.tagCostUsd.toFixed(2)})`);
  return parts.length ? parts.join(". ") + "." : "No product changes state.";
}

function previewLine(p: Preview, id: string) {
  const f = id === CAT ? p.category : p.facets.find((x) => x.id === id);
  if (!f) return moveText(p);
  const a = f.confidentFrom.value, b = f.confidentTo.value;
  const conf = a === b ? `Confident stays at ${pct(a)}` : `Confident ${b !== null && a !== null && b > a ? "rises" : "falls"} from ${pct(a)} to ${pct(b)}`;
  return `${lvl(f.from)} → ${lvl(f.to)}: ${conf}. ${moveText(p)}`;
}

function ReviewedCell({ r }: { r: Reviewed }) {
  if (!r.reviewed && !r.changedConfident) return <span className="muted">—</span>;
  return (
    <span className="cf-reviewed">
      <span>{r.unchanged} approved as shown · {r.changed} changed</span>
      <span className="muted small">
        {r.reviewed ? `${Math.round((100 * r.changed) / r.reviewed)}% changed` : ""}
        {r.changedConfident ? `${r.reviewed ? " · " : ""}${r.changedConfident} changed though confident` : ""}
      </span>
    </span>
  );
}

function ConfidentCell({ s }: { s: Share | null }) {
  if (!s) return <span className="muted">—</span>;
  return (
    <span className="cf-confident">
      <span className="cf-conf-top">
        <span className="num">{pct(s.value)}</span>
        <span className="cf-bar" aria-hidden="true"><span style={{ width: `${Math.round((s.value ?? 0) * 100)}%` }} /></span>
      </span>
      <span className="muted small">{s.num} of {plural(s.den, "product")}</span>
    </span>
  );
}

export function Confidence({ onChanged }: { onChanged?: () => void }) {
  const { params } = useRoute();
  const category = params.get("category") ?? "";
  const focus = params.get("facet") ?? "";
  const toast = useToast();
  const confirm = useConfirm();
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tree, setTree] = useState<CatNode[]>([]);
  const [search, setSearch] = useState("");
  const [drafts, setDrafts] = useState<Record<string, number>>({});
  const [dfltDraft, setDfltDraft] = useState<string>("");
  const [rowPreview, setRowPreview] = useState<Record<string, Preview | string>>({});
  const [allPreview, setAllPreview] = useState<Preview | string | null>(null);
  const [busy, setBusy] = useState(false);
  const focused = useRef(false);

  const load = useCallback(() => {
    const q = category ? `?category=${encodeURIComponent(category)}` : "";
    api.get<Data>(`/api/confidence${q}`)
      .then((d) => { setData(d); setDfltDraft(lvl(d.default)); setError(null); })
      .catch((e) => setError(errorInfo(e).message));
  }, [category]);
  useEffect(load, [load]);
  useEffect(() => { loadTree().then(setTree).catch(() => undefined); }, []);

  // a facet opened from the Metrics tab is scrolled to once
  useEffect(() => {
    if (!data || !focus || focused.current) return;
    focused.current = true;
    document.getElementById(`cf-row-${focus}`)?.scrollIntoView({ block: "center" });
  }, [data, focus]);

  const previewOf = useCallback(async (body: Body): Promise<Preview | string> => {
    const q = category ? `?category=${encodeURIComponent(category)}` : "";
    try { return await api.post<Preview>(`/api/confidence/preview${q}`, body); }
    catch (e) { const { message, problems } = errorInfo(e); return [message, ...problems].join(" "); }
  }, [category]);

  // the preview for each edited row on its own, and for all of them together
  useEffect(() => {
    let live = true;
    const ids = Object.keys(drafts);
    Promise.all(ids.map(async (id) => [id, await previewOf(bodyOf({ [id]: drafts[id] }, null))] as const))
      .then((pairs) => live && setRowPreview(Object.fromEntries(pairs)));
    if (ids.length > 1) previewOf(bodyOf(drafts, null)).then((p) => live && setAllPreview(p));
    else setAllPreview(null);
    return () => { live = false; };
  }, [drafts, previewOf]);

  const original = useCallback((id: string) => (id === CAT ? data?.category.level : data?.facets.find((f) => f.id === id)?.level) ?? null, [data]);

  const setDraft = (id: string, raw: string) => {
    const v = Number(raw);
    setDrafts((d) => {
      const next = { ...d };
      if (raw === "" || Number.isNaN(v) || Math.abs(v - (original(id) ?? -1)) < 1e-9) delete next[id];
      else next[id] = Math.min(1, Math.max(0, Math.round(v * 100) / 100));
      return next;
    });
  };

  const applyBody = async (body: Body, label: string) => {
    const p = await previewOf(body);
    if (typeof p !== "string" && p.heldToTag > 0) {
      const ok = await confirm({
        title: `Tag ${plural(p.heldToTag, "held product")}?`,
        body: `At this level their category is accepted, so they are tagged now, about $${p.tagCostUsd.toFixed(2)}. Undo restores the level; products already tagged keep their tags.`,
        ok: "Apply and tag",
      });
      if (!ok) return false;
    }
    setBusy(true);
    try {
      const r = await api.put<{ previous: Previous; heldTagged: number; jobId: number | null }>("/api/confidence", body);
      const prev: Body = {
        ...(body.levels ? { levels: Object.fromEntries(Object.keys(body.levels).map((k) => [k, r.previous.levels[k] ?? null])) } : {}),
        ...(body.default !== undefined ? { default: r.previous.default } : {}),
        ...(body.category !== undefined ? { category: r.previous.category } : {}),
      };
      toast({
        message: `${label} applied.${r.heldTagged ? ` Tagging ${plural(r.heldTagged, "product")}${r.jobId ? "" : " could not start; use Tag now on each"}.` : ""}`,
        actions: [{ label: "Undo", onClick: () => { void api.put("/api/confidence", prev).then(() => { load(); onChanged?.(); window.dispatchEvent(new Event(PRODUCTS_CHANGED)); }); } }],
      });
      load();
      onChanged?.();
      window.dispatchEvent(new Event(PRODUCTS_CHANGED));
      return true;
    } catch (e) {
      const { message, problems } = errorInfo(e);
      toast({ message: [message, ...problems].join(" ") });
      return false;
    } finally {
      setBusy(false);
    }
  };

  const applyRow = async (id: string) => {
    if (await applyBody(bodyOf({ [id]: drafts[id] }, null), id === CAT ? "Category level" : "Level")) {
      setDrafts((d) => { const n = { ...d }; delete n[id]; return n; });
    }
  };
  const applyAll = async () => {
    if (await applyBody(bodyOf(drafts, null), `${Object.keys(drafts).length} levels`)) setDrafts({});
  };
  const dfltValue = Number(dfltDraft);
  const dfltChanged = data !== null && dfltDraft !== "" && !Number.isNaN(dfltValue) && Math.abs(dfltValue - data.default) > 1e-9;
  const applyDefault = async () => {
    const v = Math.min(1, Math.max(0, Math.round(dfltValue * 100) / 100));
    const p = await previewOf({ default: v });
    const ok = await confirm({
      title: `Default level ${lvl(data!.default)} → ${lvl(v)}?`,
      body: typeof p === "string" ? p : `Every facet without its own level follows it. ${moveText(p)}`,
      ok: "Apply",
    });
    if (ok) await applyBody({ default: v }, "Default level");
  };
  const resetAll = async () => {
    const own = (data?.facets ?? []).filter((f) => !f.rule && !f.isDefault);
    if (own.length === 0) { toast({ message: "Every facet already follows the default level.", duration: 4000 }); return; }
    const levels = Object.fromEntries(own.map((f) => [f.id, null]));
    const p = await previewOf({ levels });
    const ok = await confirm({
      title: `Reset ${plural(own.length, "facet")} to the default level?`,
      body: typeof p === "string" ? p : `${own.map((f) => f.name).join(", ")} will follow the default (${lvl(data!.default)}). ${moveText(p)}`,
      ok: "Reset",
    });
    if (ok) await applyBody({ levels }, "Reset to default");
  };

  const rows = useMemo(() => {
    const s = search.trim().toLowerCase();
    return (data?.facets ?? []).filter((f) => !s || f.name.toLowerCase().includes(s) || f.id.includes(s));
  }, [data, search]);
  const o = data?.outcomes;

  const levelInput = (id: string, value: number) => (
    <input
      className="cf-level" type="number" min={0} max={1} step={0.05} aria-label="Confidence level"
      value={drafts[id] !== undefined ? drafts[id] : lvl(value)}
      onChange={(e) => setDraft(id, e.target.value)}
    />
  );
  const previewRow = (id: string) =>
    drafts[id] === undefined ? null : (
      <div className="cf-preview" role="status">
        <span>{rowPreview[id] === undefined ? "Working out the effect…" : typeof rowPreview[id] === "string" ? rowPreview[id] as string : previewLine(rowPreview[id] as Preview, id)}</span>
        <button className="btn btn-primary btn-sm" disabled={busy || typeof rowPreview[id] === "string"} onClick={() => void applyRow(id)}>{busy ? "Applying…" : "Apply"}</button>
        <button className="btn btn-secondary btn-sm" onClick={() => setDrafts((d) => { const n = { ...d }; delete n[id]; return n; })}>Cancel</button>
      </div>
    );

  return (
    <>
      <TopBar
        left={<h1 className="title">Confidence</h1>}
        right={
          <div className="cf-controls">
            <label className="cf-default">Default level
              <input className="cf-level" type="number" min={0} max={1} step={0.05} value={dfltDraft} onChange={(e) => setDfltDraft(e.target.value)} />
            </label>
            {dfltChanged && <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => void applyDefault()}>Apply</button>}
            <select className="list-select" value={category} aria-label="Category" onChange={(e) => setParams({ category: e.target.value }, { replace: true })}>
              <option value="">All categories</option>
              {tree.filter((n) => !(n.depth === 0 && n.path === "All")).map((n) => (
                <option key={n.id} value={n.id}>{"   ".repeat(Math.max(0, n.depth - 1)) + n.name}</option>
              ))}
            </select>
            <input className="cf-search" placeholder="Search facets" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search facets" />
            <button className="btn btn-quiet btn-sm" onClick={() => void resetAll()}>Reset all to default</button>
          </div>
        }
      />
      <div className="shell-scroll">
        <div className="cf">
          {error && <div className="error-banner" role="alert"><span className="error-banner-text">{error}</span><button className="btn btn-secondary btn-sm" onClick={load}>Try again</button></div>}
          {!data && !error && <div aria-busy="true" className="cf-table">{[0, 1, 2, 3, 4, 5].map((i) => <div key={i} className="cf-tr"><span className="skeleton cf-sk" /></div>)}</div>}
          {data && (
            <>
              <p className="cf-summary">
                {o && o.reviewed + o.changedConfident > 0
                  ? `${o.reviewed} answers reviewed · ${o.unchanged} approved as shown · ${o.changed} changed (${o.reviewed ? Math.round((100 * o.changed) / o.reviewed) : 0}%) · ${o.changedConfident} changed though confident`
                  : "No answers reviewed yet."}
                <span className="muted"> · {data.products.ready} Ready · {data.products.needsReview} Needs review</span>
              </p>
              {data.facets.length === 0 && data.category.confident.den === 0 ? (
                <p className="muted">No tagged products yet.</p>
              ) : null}
              <div className="cf-table" role="table">
                <div className="cf-tr cf-th" role="row">
                  <span>Facet</span><span>Confidence level</span><span>Confident</span><span className="num">Average confidence</span>
                  <span className="num">Unknown</span><span className="num">Needs review</span><span>Reviewed so far</span>
                </div>
                <div id={`cf-row-${CAT}`} className={"cf-row" + (drafts[CAT] !== undefined ? " is-editing" : "")} role="row">
                  <div className="cf-tr">
                    <span className="cf-name" title="The categorising step: a category below this level waits for a person">Category</span>
                    <span className="cf-levelcell">{levelInput(CAT, data.category.level)}</span>
                    <ConfidentCell s={data.category.confident} />
                    <span className="num">{data.category.meanConfidence === null ? "—" : data.category.meanConfidence.toFixed(2)}</span>
                    <span className="num muted">—</span>
                    <span className="num">{data.category.needsReview}</span>
                    <ReviewedCell r={data.category.reviewed} />
                  </div>
                  {previewRow(CAT)}
                </div>
                {rows.map((f) => (
                  <div key={f.id} id={`cf-row-${f.id}`} className={"cf-row" + (f.rule ? " is-rule" : "") + (drafts[f.id] !== undefined ? " is-editing" : "") + (f.id === focus ? " is-focus" : "")} role="row">
                    <div className="cf-tr">
                      <span className="cf-name" title={f.definition ?? undefined}>
                        {f.name}
                        {f.required && <span className="cf-badge">Required</span>}
                      </span>
                      <span className="cf-levelcell">
                        {f.rule ? <span className="muted">Rule</span> : (
                          <>
                            {levelInput(f.id, f.level as number)}
                            {f.isDefault && drafts[f.id] === undefined && <span className="cf-default-tag">default</span>}
                          </>
                        )}
                      </span>
                      <ConfidentCell s={f.confident} />
                      <span className="num">{f.meanConfidence === null ? "—" : f.meanConfidence.toFixed(2)}</span>
                      <span className="num" title={`${f.unknown.num} of ${f.unknown.den} products`}>{pct(f.unknown.value)}</span>
                      <span className="num">
                        {f.needsReview === null ? <span className="muted">—</span> : f.needsReview > 0 ? (
                          <button className="cf-link" title="Open these products" onClick={() => navigate(`/products?facet=${encodeURIComponent(f.id)}&flagged=true${category ? `&category=${encodeURIComponent(category)}` : ""}`)}>
                            {f.needsReview}
                          </button>
                        ) : "0"}
                      </span>
                      <ReviewedCell r={f.reviewed} />
                    </div>
                    {previewRow(f.id)}
                  </div>
                ))}
              </div>
              <p className="muted small">
                Figures cover every tagged product at its latest tagging, approved or not. A new level applies to products
                waiting for approval and to everything tagged from now on; approved products stay approved. Lowering a level
                never clears a flag raised for another reason, such as a missing required value or a quote not found.
              </p>
            </>
          )}
        </div>
      </div>
      {Object.keys(drafts).length > 1 && (
        <div className="cf-bar-bottom" role="region" aria-label="Pending level changes">
          <span>
            {Object.keys(drafts).length} levels changed ·{" "}
            {allPreview === null ? "working out the combined effect…" : typeof allPreview === "string" ? allPreview : moveText(allPreview)}
          </span>
          <button className="btn btn-secondary" onClick={() => setDrafts({})}>Discard</button>
          <button className="btn btn-primary" disabled={busy || typeof allPreview === "string"} onClick={() => void applyAll()}>{busy ? "Applying…" : "Apply all"}</button>
        </div>
      )}
    </>
  );
}
