import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, errorInfo } from "../api";
import { useConfirm } from "../components/ConfirmDialog";
import { useToast } from "../components/Toast";
import { TopBar } from "../components/TopBar";
import { navigate, setLeaveGuard, setParams, useRoute } from "../router";
import "./Facets.css";

// Facets tab (Design PRD 9): each facet's rules and values, edited in the database and used by the next
// product tagged. The definitions are the rubric sent to the model; a change to them gets a new rubric version
// (decided 14 Nov 2026). "Test on a product" re-tags one product and shows this facet before and after.

type ListItem = { id: string; name: string; group: "ai" | "rule" | "retired"; values: number; allValues: number; edited: boolean };
type Value = { id: number | null; value?: string; label: string; definition: string | null; version?: string | null; active: boolean; exclusive?: boolean; uses?: number };
type Change = { id: number; at: string; by: string; summary: string; versionBefore: string | null; versionAfter: string | null };
type Detail = {
  id: string; name: string; definition: string | null; version: string | null; policy: string; onMissing: string;
  maxValues: number | null; cardinality: string; dataType: string; status: string; required: boolean; requiredIn: number;
  categoriesCount: number; values: Value[]; group: string; policyText: string; rule: string | null; unknownPolicy: string | null;
  edited: boolean; updatedAt: string | null; level: number; levelIsDefault: boolean;
  categories: { id: string; path: string; required: boolean }[]; history: Change[]; taggedProducts: number;
};
type Draft = { name: string; definition: string; policy: string; onMissing: string; maxValues: number | null; required: boolean | null; values: Value[] };
type CatNode = { id: string; path: string; name: string; depth: number };
type JobAnswer = { run: number; rows: { value: string | null; status: string; confidence: number | null; evidence: string | null; reason: string | null; quote: string | null; reviewReason: string | null }[] } | null;
type Job = {
  id: number; status: string; error: string | null; steps: { name: string; state: string; seconds: number | null }[];
  costUsd: number; result: { productId?: string; before?: JobAnswer; after?: JobAnswer; name?: string; brand?: string; photo?: string | null };
};

const GROUPS: [ListItem["group"], string][] = [["ai", "Tagged by AI"], ["rule", "Set by rule"], ["retired", "Retired"]];
const POLICIES: [string, string][] = [["text_only", "Product text only"], ["text_first", "Product text first, then photos"], ["judge", "Judge from text and photos"]];
const TYPE_TEXT: Record<string, string> = { enum: "from a list", ordinal: "from an ordered list", boolean: "yes or no", number: "a number", text: "free text" };
const STATUS_TEXT: Record<string, string> = { auto: "Accepted", review: "In review", abstained: "Left unknown", not_applicable: "Not applicable" };
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const when = (iso: string) => { const d = new Date(iso); return `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}, ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`; };

let treeOnce: Promise<CatNode[]> | null = null;
function loadTree() {
  treeOnce ??= api.get<{ nodes: CatNode[] }>("/api/categories/tree").then((r) => r.nodes);
  treeOnce.catch(() => (treeOnce = null));
  return treeOnce;
}

function draftOf(d: Detail): Draft {
  return { name: d.name, definition: d.definition ?? "", policy: d.policy, onMissing: d.onMissing, maxValues: d.maxValues,
           required: null, values: d.values.map((v) => ({ ...v })) };
}

function changed(d: Detail, x: Draft) {
  return x.name !== d.name || x.definition !== (d.definition ?? "") || x.policy !== d.policy || x.onMissing !== d.onMissing
    || x.maxValues !== d.maxValues || x.required !== null || x.values.length !== d.values.length
    || x.values.some((v, i) => v.label !== d.values[i]?.label || (v.definition ?? "") !== (d.values[i]?.definition ?? "") || v.active !== d.values[i]?.active);
}

function rubricChanged(d: Detail, x: Draft) {
  return x.definition !== (d.definition ?? "") || x.values.some((v, i) => v.id === null ? !!v.definition : (v.definition ?? "") !== (d.values[i]?.definition ?? ""));
}

function nextVersion(v: string | null) {
  if (!v) return "v1";
  const m = /^(.*?)(?:\.(\d+))?$/.exec(v)!;
  return m[2] ? `${m[1]}.${Number(m[2]) + 1}` : `${v}.1`;
}

function Answer({ a, title }: { a: JobAnswer | undefined; title: string }) {
  return (
    <div className="fx-answer">
      <span className="fx-answer-title">{title}</span>
      {!a ? <span className="muted">Not tagged before</span> : a.rows.length === 0 ? <span className="muted">No answer (unknown)</span> : a.rows.map((r, i) => (
        <div key={i} className="fx-answer-row">
          <strong>{r.value ?? (r.status === "not_applicable" ? "Not applicable" : "Unknown")}</strong>
          <span className="muted small">
            {STATUS_TEXT[r.status] ?? "Answer"}
            {r.confidence !== null ? ` · ${r.confidence.toFixed(2)}` : ""}{r.evidence ? ` · ${r.evidence === "image" ? "photo" : r.evidence}` : ""}
          </span>
          {r.quote && <span className="small">“{r.quote}”</span>}
          {r.reason && <span className="muted small">{r.reason}</span>}
        </div>
      ))}
    </div>
  );
}

function TestPanel({ facet, onDone }: { facet: Detail; onDone: () => void }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<{ id: string; name: string; brand: string }[]>([]);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number>();

  useEffect(() => {
    if (q.trim().length < 2 || q.includes("ounass.")) { setHits([]); return; }
    const t = window.setTimeout(() => {
      api.get<{ products: typeof hits }>(`/api/product-search?q=${encodeURIComponent(q)}`).then((r) => setHits(r.products)).catch(() => setHits([]));
    }, 250);
    return () => window.clearTimeout(t);
  }, [q]);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const poll = (id: number) => {
    api.get<Job>(`/api/jobs/${id}`).then((j) => {
      setJob(j);
      if (j.status === "running") timer.current = window.setTimeout(() => poll(id), 1000);
      else if (j.status === "done") onDone();
    }).catch((e) => setError(errorInfo(e).message));
  };
  const start = async (product: string) => {
    setError(null); setJob(null); setHits([]);
    try {
      const r = await api.post<{ jobId: number }>(`/api/facets/${encodeURIComponent(facet.id)}/test`, { product });
      poll(r.jobId);
    } catch (e) { setError(errorInfo(e).message); }
  };
  const res = job?.result;
  return (
    <div className="fx-card fx-test">
      <h3 className="fx-h3">Test on a product</h3>
      <p className="muted small">Re-tags one product with the saved rules (one paid tagging call, about 5¢) and shows this facet before and after. The product then appears in the Products tab like any other; an approved product stays approved.</p>
      <div className="fx-test-pick">
        <input className="fx-input" placeholder="Search by name, brand or ID, or paste an Ounass product link" value={q} onChange={(e) => setQ(e.target.value)}
               onKeyDown={(e) => { if (e.key === "Enter" && q.trim()) void start(q.trim()); }} />
        <button className="btn btn-primary" disabled={!q.trim() || job?.status === "running"} onClick={() => void start(q.trim())}>Test</button>
      </div>
      {hits.length > 0 && (
        <ul className="fx-hits">
          {hits.map((h) => (
            <li key={h.id}><button onClick={() => { setQ(h.id); void start(h.id); }}>{h.name} <span className="muted">· {h.brand} · {h.id}</span></button></li>
          ))}
        </ul>
      )}
      {error && <div className="error-banner" role="alert"><span className="error-banner-text">{error}</span></div>}
      {job && (
        <div className="fx-test-run" aria-live="polite">
          <span className="muted small">
            {job.steps.map((s) => `${s.name}${s.state === "done" ? ` ✓ ${s.seconds ?? ""}s` : s.state === "running" ? "…" : s.state === "failed" ? " ✕" : ""}`).join(" · ")}
            {job.costUsd ? ` · $${job.costUsd.toFixed(3)}` : ""}
          </span>
          {job.status === "failed" && <div className="error-banner" role="alert"><span className="error-banner-text">{job.error}</span></div>}
          {job.status === "done" && res && (
            <div className="fx-compare">
              {res.photo ? <img className="fx-photo" src={res.photo} alt="" /> : <div className="fx-photo" />}
              <div className="fx-compare-main">
                <button className="fx-link" onClick={() => navigate(`/products/${encodeURIComponent(res.productId!)}`)}>{res.name} <span className="muted">· {res.brand}</span></button>
                <div className="fx-compare-cols">
                  <Answer a={res.before} title="Before" />
                  <Answer a={res.after} title="After" />
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function Facets() {
  const { params } = useRoute();
  const selected = params.get("facet") ?? "";
  const category = params.get("category") ?? "";
  const toast = useToast();
  const confirm = useConfirm();
  const [list, setList] = useState<ListItem[] | null>(null);
  const [q, setQ] = useState("");
  const [tree, setTree] = useState<CatNode[]>([]);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<{ tagged: number; version: string | null } | null>(null);
  const [showTest, setShowTest] = useState(false);
  const [view, setView] = useState<number | null>(null);

  const loadList = useCallback(() => {
    const p = new URLSearchParams();
    if (category) p.set("category", category);
    if (q.trim()) p.set("q", q.trim());
    api.get<{ facets: ListItem[] }>(`/api/facet-list?${p}`).then((r) => setList(r.facets)).catch(() => setList([]));
  }, [category, q]);
  useEffect(() => { const t = window.setTimeout(loadList, 200); return () => window.clearTimeout(t); }, [loadList]);
  useEffect(() => { loadTree().then(setTree).catch(() => undefined); }, []);

  const loadDetail = useCallback((id: string) => {
    if (!id) { setDetail(null); setDraft(null); return; }
    api.get<Detail>(`/api/facets/${encodeURIComponent(id)}`).then((d) => { setDetail(d); setDraft(draftOf(d)); setFieldErrors({}); })
      .catch((e) => toast({ message: errorInfo(e).message }));
  }, [toast]);
  useEffect(() => { loadDetail(selected); setSaved(null); setShowTest(false); setView(null); }, [selected, loadDetail]);

  const dirty = !!detail && !!draft && changed(detail, draft);
  useEffect(() => {
    if (!dirty) return;
    const h = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", h);
    setLeaveGuard(() => confirm({ title: "Leave without saving?", body: "Your edits to this facet will be lost.", ok: "Leave", cancel: "Stay" }));
    return () => { window.removeEventListener("beforeunload", h); setLeaveGuard(null); };
  }, [dirty, confirm]);

  const pick = async (id: string) => {
    if (id === selected) return;
    if (dirty && !(await confirm({ title: "Leave without saving?", body: "Your edits to this facet will be lost.", ok: "Leave", cancel: "Stay" }))) return;
    setParams({ facet: id });
  };

  const save = async () => {
    if (!detail || !draft) return;
    setSaving(true); setFieldErrors({});
    try {
      const body = { name: draft.name, definition: draft.definition, policy: draft.policy, onMissing: draft.onMissing,
                     maxValues: draft.maxValues, ...(draft.required !== null ? { required: draft.required } : {}),
                     values: draft.values.map((v) => ({ id: v.id, label: v.label, definition: v.definition, active: v.active })) };
      const r = await api.put<{ taggedProducts: number; version: string | null; summary: string }>(`/api/facets/${encodeURIComponent(detail.id)}`, body);
      toast({ message: "Saved. This applies to products tagged from now on.", duration: 4000 });
      setSaved({ tagged: r.taggedProducts, version: r.version });
      loadDetail(detail.id); loadList();
    } catch (e) {
      const { message, problems } = errorInfo(e);
      const fields = e instanceof ApiError ? e.fields : {};
      // problems tied to a field are shown beside it; the rest (e.g. tags that would break) in the banner
      setFieldErrors(Object.keys(fields).length ? fields : Object.fromEntries(problems.map((p, i) => [`_${i}`, p])));
      toast({ message: [message, ...(Object.keys(fields).length ? [] : problems)].join(" ") });
    } finally { setSaving(false); }
  };

  const restore = async (c: Change) => {
    const ok = await confirm({ title: "Restore this version?", body: `The facet goes back to how it was before “${c.summary}”. This is saved as a new change${c.versionBefore ? ` (rubric ${c.versionBefore} text, as a new version)` : ""}.`, ok: "Restore" });
    if (!ok || !detail) return;
    try {
      await api.post(`/api/facets/${encodeURIComponent(detail.id)}/restore`, { changeId: c.id });
      toast({ message: "Restored. This applies to products tagged from now on.", duration: 4000 });
      loadDetail(detail.id); loadList();
    } catch (e) { const { message, problems } = errorInfo(e); toast({ message: [message, ...problems].join(" ") }); }
  };

  const groups = useMemo(() => GROUPS.map(([g, title]) => ({ g, title, items: (list ?? []).filter((f) => f.group === g) })), [list]);
  const editable = detail?.group === "ai";
  const setD = (patch: Partial<Draft>) => setDraft((d) => (d ? { ...d, ...patch } : d));
  const setV = (i: number, patch: Partial<Value>) => setDraft((d) => (d ? { ...d, values: d.values.map((v, j) => (j === i ? { ...v, ...patch } : v)) } : d));
  const histItem = detail?.history.find((h) => h.id === view);

  return (
    <>
      <TopBar left={<h1 className="title">Facets</h1>} right={list ? <span className="muted small">{list.filter((f) => f.group !== "retired").length} facets · {list.filter((f) => f.edited).length} edited in the app</span> : undefined} />
      <div className="fx">
        <aside className="fx-left">
          <input className="fx-input" placeholder="Search facets" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search facets" />
          <select className="list-select fx-cat" value={category} aria-label="Category" onChange={(e) => setParams({ category: e.target.value || null }, { replace: true })}>
            <option value="">All categories</option>
            {tree.filter((n) => !(n.depth === 0 && n.path === "All")).map((n) => (
              <option key={n.id} value={n.id}>{"   ".repeat(Math.max(0, n.depth - 1)) + `Facets used in ${n.name}`}</option>
            ))}
          </select>
          <nav className="fx-list" aria-label="Facets">
            {list === null ? [0, 1, 2, 3, 4, 5].map((i) => <div key={i} className="fx-skeleton" />) : groups.map(({ g, title, items }) => items.length > 0 && (
              <div key={g} className="fx-group">
                <span className="fx-group-title">{title}</span>
                {items.map((f) => (
                  <button key={f.id} className={"fx-item" + (f.id === selected ? " is-active" : "") + (g === "retired" ? " is-retired" : "")} onClick={() => void pick(f.id)}>
                    <span>{f.name}</span>
                    <span className="fx-item-meta">
                      {f.edited && <span className="fx-edited">Edited</span>}
                      <span className="muted small">{f.values}</span>
                    </span>
                  </button>
                ))}
              </div>
            ))}
          </nav>
        </aside>
        <main className="fx-right">
          {!detail || !draft ? (
            selected ? (
              <div className="fx-cards" aria-busy="true">{[0, 1, 2].map((i) => <div key={i} className="skeleton fx-sk-card" />)}</div>
            ) : <p className="muted fx-empty">Select a facet to see its rules</p>
          ) : (
            <div className="fx-cards">
              {saved && (
                <div className="fx-banner" role="status">
                  <span>{saved.tagged} products were tagged with the earlier version{saved.version ? `; the rubric is now ${saved.version}` : ""}.</span>
                  <button className="btn btn-secondary btn-sm" onClick={() => setShowTest(true)}>Test on a product</button>
                </div>
              )}
              <section className="fx-card">
                <h3 className="fx-h3">Basics</h3>
                <label className="fx-field">Display name
                  <input className={"fx-input" + (fieldErrors.name ? " is-error" : "")} value={draft.name} disabled={!editable} onChange={(e) => setD({ name: e.target.value })} />
                  {fieldErrors.name && <span className="fx-ferr">{fieldErrors.name}</span>}
                </label>
                <label className="fx-field">
                  <span>Rubric sent to the model <span className="fx-version">{detail.version ?? "no version"}{editable && rubricChanged(detail, draft) ? ` → ${nextVersion(detail.version)} on save` : ""}</span></span>
                  <textarea className={"fx-textarea" + (fieldErrors.definition ? " is-error" : "")} rows={6} value={draft.definition} disabled={!editable} onChange={(e) => setD({ definition: e.target.value })} />
                  {fieldErrors.definition && <span className="fx-ferr">{fieldErrors.definition}</span>}
                </label>
                <span className="muted small">{detail.id} · {detail.cardinality === "multi" ? "several values" : "one value"} · {TYPE_TEXT[detail.dataType] ?? "values"}{detail.updatedAt ? ` · last saved ${when(detail.updatedAt)}` : ""}</span>
              </section>

              <section className="fx-card">
                <h3 className="fx-h3">How it is judged</h3>
                {!editable ? (
                  <p>{detail.policyText}{detail.rule ? `: ${detail.rule}` : ""}. <span className="muted">Read-only.</span></p>
                ) : (
                  <>
                    <fieldset className="fx-radios">
                      <legend>Evidence source</legend>
                      {POLICIES.map(([v, l]) => (
                        <label key={v}><input type="radio" name="policy" checked={draft.policy === v} onChange={() => setD({ policy: v })} /> {l}</label>
                      ))}
                    </fieldset>
                    <label className="fx-check">
                      <input type="checkbox" checked={draft.required ?? detail.required}
                             ref={(el) => { if (el) el.indeterminate = draft.required === null && detail.requiredIn > 0 && !detail.required; }}
                             onChange={(e) => setD({ required: e.target.checked })} />
                      Required
                      <span className="muted small">
                        {draft.required === null ? `required in ${detail.requiredIn} of ${detail.categoriesCount} categories` : `will be ${draft.required ? "required" : "optional"} in all ${detail.categoriesCount} categories`}
                      </span>
                    </label>
                    <fieldset className="fx-radios">
                      <legend>When no answer is found</legend>
                      {detail.onMissing === "default" ? <span className="muted">Uses its default value (read-only)</span> : (
                        <>
                          <label><input type="radio" name="miss" checked={draft.onMissing === "unknown"} onChange={() => setD({ onMissing: "unknown" })} /> Leave unknown</label>
                          <label><input type="radio" name="miss" checked={draft.onMissing === "review_if_required"} onChange={() => setD({ onMissing: "review_if_required" })} /> Send to review (where required)</label>
                        </>
                      )}
                    </fieldset>
                    <label className="fx-field fx-inline">Maximum values
                      <input className="fx-input fx-num" type="number" min={1} disabled={detail.cardinality === "single"} value={draft.maxValues ?? ""} onChange={(e) => setD({ maxValues: e.target.value === "" ? null : Number(e.target.value) })} />
                      {detail.cardinality === "single" && <span className="muted small">single-value facet</span>}
                      {fieldErrors.maxValues && <span className="fx-ferr">{fieldErrors.maxValues}</span>}
                    </label>
                  </>
                )}
              </section>

              {(detail.values.length > 0 || editable) && detail.dataType !== "number" && detail.dataType !== "text" && (
                <section className="fx-card">
                  <h3 className="fx-h3">Values</h3>
                  <div className="fx-values" role="table">
                    <div className="fx-vrow fx-vhead" role="row"><span>Label</span><span>Definition (sent to the model)</span><span className="num">Products</span><span>Active</span></div>
                    {draft.values.map((v, i) => (
                      <div key={v.id ?? `new-${i}`} className={"fx-vrow" + (v.active ? "" : " is-inactive") + (fieldErrors[`values.${i}.label`] || fieldErrors[`values.${i}`] ? " is-error" : "")} role="row">
                        <span className="fx-vlabel">
                          <input className="fx-input" value={v.label} disabled={!editable} aria-label="Label" onChange={(e) => setV(i, { label: e.target.value })} />
                          {(fieldErrors[`values.${i}.label`] || fieldErrors[`values.${i}`]) && <span className="fx-ferr">{fieldErrors[`values.${i}.label`] ?? fieldErrors[`values.${i}`]}</span>}
                        </span>
                        <textarea className="fx-textarea fx-vdef" rows={2} value={v.definition ?? ""} disabled={!editable} aria-label="Definition" onChange={(e) => setV(i, { definition: e.target.value || null })} />
                        <span className="num">{v.id === null ? "new" : v.uses ?? 0}</span>
                        <label className="fx-switch"><input type="checkbox" checked={v.active} disabled={!editable} onChange={(e) => setV(i, { active: e.target.checked })} /> {v.exclusive ? <span className="muted small">stands alone</span> : null}</label>
                      </div>
                    ))}
                  </div>
                  {fieldErrors.values && <span className="fx-ferr">{fieldErrors.values}</span>}
                  {editable && <button className="btn btn-secondary btn-sm" onClick={() => setD({ values: [...draft.values, { id: null, label: "", definition: null, active: true }] })}>Add value</button>}
                  <span className="muted small">A value cannot be deleted. Switching it off stops the AI choosing it from now on; products already tagged with it keep it.</span>
                </section>
              )}

              <section className="fx-card">
                <h3 className="fx-h3">Applies to</h3>
                <div className="fx-chips">
                  {detail.categories.slice(0, 40).map((c) => <span key={c.id} className="fx-chip" title={c.path}>{c.path.split(" / ").slice(-2).join(" › ")}{c.required ? " · required" : ""}</span>)}
                  {detail.categories.length > 40 && <span className="muted small">and {detail.categories.length - 40} more</span>}
                </div>
              </section>

              <section className="fx-card fx-row">
                <h3 className="fx-h3">Confidence level</h3>
                <span>{detail.level.toFixed(2)}{detail.levelIsDefault ? " (default)" : ""}</span>
                <button className="fx-link" onClick={() => navigate(`/confidence?facet=${encodeURIComponent(detail.id)}`)}>Change on the Confidence tab</button>
              </section>

              <section className="fx-card">
                <h3 className="fx-h3">History</h3>
                {detail.history.length === 0 ? <p className="muted">No changes in the app yet.</p> : (
                  <ul className="fx-history">
                    {detail.history.map((h) => (
                      <li key={h.id}>
                        <span className="muted small">{when(h.at)}</span>
                        <span>{h.summary}</span>
                        <span className="fx-history-actions">
                          <button className="fx-link" onClick={() => setView(view === h.id ? null : h.id)}>{view === h.id ? "Hide" : "View"}</button>
                          {editable && <button className="fx-link" onClick={() => void restore(h)}>Restore</button>}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
                {histItem && <HistoryView facetId={detail.id} changeId={histItem.id} />}
              </section>

              {editable && (showTest ? <TestPanel facet={detail} onDone={() => loadDetail(detail.id)} /> : (
                <button className="btn btn-secondary fx-test-open" onClick={() => setShowTest(true)}>Test on a product</button>
              ))}
              {Object.keys(fieldErrors).some((k) => k.startsWith("_") || ["id", "dataType", "cardinality", "categories", "policy", "onMissing"].includes(k)) && (
                <div className="error-banner" role="alert">
                  <span className="error-banner-text">
                    Not saved. {Object.entries(fieldErrors).filter(([k]) => k.startsWith("_") || ["id", "dataType", "cardinality", "categories", "policy", "onMissing"].includes(k)).map(([, v]) => v).join(" ")}
                  </span>
                  <button className="btn btn-secondary btn-sm" onClick={() => void save()}>Try again</button>
                </div>
              )}
            </div>
          )}
        </main>
      </div>
      {editable && detail && (
        <div className="fx-footer">
          <span className="muted small">{dirty ? (rubricChanged(detail, draft!) ? `Unsaved changes · the rubric becomes ${nextVersion(detail.version)}` : "Unsaved changes") : "No changes"}</span>
          <button className="btn btn-secondary" disabled={!dirty || saving} onClick={() => { setDraft(draftOf(detail)); setFieldErrors({}); }}>Discard</button>
          <button className="btn btn-primary" disabled={!dirty || saving} onClick={() => void save()}>{saving ? "Saving…" : "Save changes"}</button>
        </div>
      )}
    </>
  );
}

function HistoryView({ facetId, changeId }: { facetId: string; changeId: number }) {
  const [data, setData] = useState<{ before: { definition: string | null; version: string | null; values: Value[] } } | null>(null);
  useEffect(() => {
    api.get<{ before: { definition: string | null; version: string | null; values: Value[] } }>(`/api/facets/${encodeURIComponent(facetId)}/changes/${changeId}`)
      .then(setData).catch(() => setData(null));
  }, [facetId, changeId]);
  if (!data) return <p className="muted small">Loading…</p>;
  return (
    <div className="fx-histview">
      <span className="muted small">Earlier version{data.before.version ? ` (rubric ${data.before.version})` : ""}</span>
      <p className="fx-pre">{data.before.definition}</p>
      <ul>{data.before.values.map((v) => <li key={String(v.id)}><strong>{v.label}</strong>{v.active ? "" : " (inactive)"}{v.definition ? `: ${v.definition}` : ""}</li>)}</ul>
    </div>
  );
}
