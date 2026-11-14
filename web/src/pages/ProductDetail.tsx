import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, errorInfo, PRODUCTS_CHANGED } from "../api";
import { CategoryCard } from "../components/CategoryCard";
import { useConfirm } from "../components/ConfirmDialog";
import { EvidencePanel } from "../components/EvidencePanel";
import { FacetRow } from "../components/FacetRow";
import { StatusBadge, type Status } from "../components/StatusBadge";
import { TOAST_SHORT, useToast } from "../components/Toast";
import { TopBar } from "../components/TopBar";
import type { EditorResult } from "../components/ValueEditor";
import {
  decisionsFromSaved,
  decisionsKey,
  decisionsToApi,
  hasValue,
  sameValues,
  shownValue,
  type CategoryDecision,
  type Facet,
  type FacetDecision,
  type ProductDetailData,
} from "../productTypes";
import { navigate, setLeaveGuard } from "../router";
import "./ProductDetail.css";

// Product detail page (Design PRD 6). Evidence on the left (42%), the category card and the facet
// rows on the right (58%), each column scrolling on its own, and a footer fixed under the right
// column. Decisions are held on the page until Save or Approve and publish sends them (Build PRD 5);
// saved decisions are loaded back when the page opens.
//
// The two columns and all the review wiring live in `ProductReview`, which the detail page (with its
// top bar and previous / next) and the Live tag tab (embedded under the tracker, Design PRD 7) share.

type Base = "products" | "approved";
type ListPage = { rows: { id: string }[]; total: number; page: number; pageSize: number };

const LIST_KEY = "tagger:listQuery";
const LEAVE_MSG = "You have decisions that are not saved. Leave this page and lose them?";

function readListQuery() {
  try {
    return sessionStorage.getItem(LIST_KEY) ?? "";
  } catch {
    return "";
  }
}

/** The list's query string as the server reads it (the list page adds tab and the defaults). */
function serverListQuery(qs: string, base: Base) {
  const p = new URLSearchParams(qs);
  p.set("tab", base);
  const state = p.get("state");
  if (base === "approved" || (state !== "ready" && state !== "review")) p.delete("state");
  if (!p.get("sort")) p.set("sort", "review");
  if (!p.get("page")) p.set("page", "1");
  return p.toString();
}

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);
const enc = encodeURIComponent;

const announce = () => window.dispatchEvent(new Event(PRODUCTS_CHANGED));

type Busy = "save" | "approve" | "unapprove" | "category" | null;
type Banner = { message: string; problems: string[]; retry: () => void };
type Retag = { decision: CategoryDecision; jobId: number | null; seconds: number; error: string | null };
type Job = { status: "running" | "done" | "failed"; error: string | null; seconds?: number };

export function ProductDetail({ id, base, onChanged }: { id: string; base: Base; onChanged?: () => void }) {
  return <ProductReview productId={id} base={base} onChanged={onChanged} />;
}

export type ProductReviewProps = {
  productId: string;
  /** Detail page: which list the top bar returns to and steps through. */
  base?: Base;
  /** Live tag: no top bar or previous / next; a title line and a fixed-height frame instead, and
   *  approving stays on the page. */
  embedded?: boolean;
  /** After an approve, save or undo: refresh the rail counts. */
  onChanged?: () => void;
  /** Embedded: called after "Approve and publish" succeeds. */
  onApproved?: () => void;
  /** Embedded: bump to load the product again (e.g. when tagging finishes). */
  reloadKey?: number;
  /** Embedded: tagging is still running, so the facet rows show as loading placeholders. */
  facetsPending?: boolean;
  /** A category decision started a re-tag job (Live tag follows it in its tracker). */
  onCategoryJob?: (jobId: number) => void;
};

export function ProductReview({
  productId: id,
  base = "products",
  embedded = false,
  onChanged,
  onApproved,
  reloadKey = 0,
  facetsPending = false,
  onCategoryJob,
}: ProductReviewProps) {
  const [data, setData] = useState<ProductDetailData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [facetDec, setFacetDec] = useState<Record<string, FacetDecision>>({});
  const [catDec, setCatDec] = useState<CategoryDecision | undefined>(undefined);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState<string | null>(null);
  const [quote, setQuote] = useState<{ text: string; seq: number } | null>(null);
  const [nav, setNav] = useState<{ ids: string[]; offset: number; total: number } | null>(null);
  const lockRef = useRef<HTMLDivElement | null>(null);
  const [baseline, setBaseline] = useState(decisionsKey({}));
  const [busy, setBusy] = useState<Busy>(null);
  const [banner, setBanner] = useState<Banner | null>(null);
  const [retag, setRetag] = useState<Retag | null>(null);
  const [reloadSeq, setReloadSeq] = useState(0);
  const keepEdits = useRef(false); // the next load keeps the decisions on the page
  const lastCat = useRef<CategoryDecision | null>(null); // the category decision sent this visit
  const toast = useToast();
  const confirm = useConfirm();
  const onChangedRef = useRef(onChanged);
  onChangedRef.current = onChanged;
  const onApprovedRef = useRef(onApproved);
  onApprovedRef.current = onApproved;
  const changed = useCallback(() => {
    onChangedRef.current?.();
    announce();
  }, []);

  const listQuery = useMemo(readListQuery, []);
  const backHref = "/" + base + (listQuery ? "?" + listQuery : "");
  const backLabel = base === "approved" ? "Approved" : "Products";

  // ---- data: the product, with its saved decisions loaded into the page
  useEffect(() => {
    let live = true;
    api
      .get<ProductDetailData>("/api/products/" + enc(id))
      .then((d) => {
        if (!live) return;
        const saved = decisionsFromSaved(d.facets, d.saved);
        setData(d);
        setBaseline(decisionsKey(saved));
        if (!keepEdits.current) setFacetDec(saved);
        keepEdits.current = false;
        setCatDec(d.saved?.categoryReviewed ? (lastCat.current ?? { kind: "approved" }) : undefined);
      })
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [id, reloadSeq, reloadKey]);

  const reload = useCallback((keep: boolean) => {
    keepEdits.current = keep;
    setReloadSeq((n) => n + 1);
  }, []);
  // a toast's Undo can outlive the page: it reloads only while the page is still open
  const reloadRef = useRef<((keep: boolean) => void) | null>(reload);
  useEffect(() => {
    reloadRef.current = reload;
    return () => {
      reloadRef.current = null;
    };
  }, [reload]);

  // previous / next follow the list's filter and sort: the ordered IDs of the list page it was on
  useEffect(() => {
    if (embedded) return;
    let live = true;
    api
      .get<ListPage>("/api/products?" + serverListQuery(listQuery, base))
      .then((r) => live && setNav({ ids: r.rows.map((x) => x.id), offset: (r.page - 1) * r.pageSize, total: r.total }))
      .catch(() => live && setNav(null));
    return () => {
      live = false;
    };
  }, [listQuery, base, embedded]);

  // ---- derived
  const dirty = decisionsKey(facetDec) !== baseline;
  const facets = data?.facets ?? [];
  const reviewRows = facets.filter((f) => f.flagged);
  const confidentRows = [...facets.filter((f) => !f.flagged && !f.rule), ...facets.filter((f) => !f.flagged && f.rule)];
  const ordered = [...reviewRows, ...confidentRows];

  const catPending = !!data && data.category.flagged && !catDec && !data.isPublished;
  const retagging = !!retag && retag.error === null;
  const locked = catPending || retagging || busy === "category";
  const isMissing = (f: Facet) => {
    const v = shownValue(f, facetDec[f.id]);
    return f.required && !f.rule && v.state !== "not_applicable" && !hasValue(v);
  };
  const undecided = ordered.filter((f) => f.flagged && !f.rule && !facetDec[f.id]);
  const missing = ordered.filter(isMissing);
  const remaining = undecided.length + missing.filter((f) => !undecided.includes(f)).length + (catPending ? 1 : 0);

  const whatIsLeft = [
    catPending && "Decide the category.",
    undecided.length > 0 &&
      `Approve or change ${undecided.length} flagged ${plural(undecided.length, "facet", "facets")}.`,
    missing.length > 0 && `Give ${missing.length} required ${plural(missing.length, "facet", "facets")} a value.`,
  ]
    .filter(Boolean)
    .join(" ");

  // ---- leaving with decisions asks first
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    // leaving inside the app (rail, back link, another product) asks with the app's own dialog
    setLeaveGuard(() => confirm({ title: "Leave without saving?", body: LEAVE_MSG, ok: "Leave", cancel: "Stay" }));
    return () => {
      window.removeEventListener("beforeunload", warn);
      setLeaveGuard(null);
    };
  }, [dirty, confirm]);

  const leave = useCallback(
    (to: string) => {
      navigate(to);
    },
    [dirty],
  );

  // locked facet rows cannot be reached by mouse or keyboard
  useEffect(() => {
    lockRef.current?.toggleAttribute("inert", locked);
  }, [locked, data]);

  // ---- decisions
  const decide = (f: Facet, d: FacetDecision | null) =>
    setFacetDec((all) => {
      const next = { ...all };
      if (d) next[f.id] = d;
      else delete next[f.id];
      return next;
    });

  const applyResult = (f: Facet, r: EditorResult) => {
    const a = f.answer;
    const same = !!a && a.state === r.state && sameValues(a.values, r.values);
    // choosing the tagger's own value counts as approving it
    if (same) decide(f, f.flagged ? { kind: "approved" } : null);
    else decide(f, { kind: "changed", state: r.state, values: r.values });
    setEditing(null);
  };

  const toggle = (f: Facet) =>
    setExpanded((s) => {
      const next = new Set(s);
      if (next.has(f.id)) next.delete(f.id);
      else next.add(f.id);
      return next;
    });

  const scrollToFirstOpen = () => {
    if (catPending) {
      document.getElementById("category-card")?.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    const first = undecided[0] ?? missing[0];
    if (!first) return;
    if (!first.flagged) setExpanded((s) => new Set(s).add(first.id));
    window.requestAnimationFrame(() =>
      document.getElementById("facet-" + first.id)?.scrollIntoView({ behavior: "smooth", block: "center" }),
    );
  };

  // ---- top bar
  const idx = nav ? nav.ids.indexOf(id) : -1;
  const prevId = idx > 0 ? nav!.ids[idx - 1] : null;
  const nextId = idx >= 0 && idx < nav!.ids.length - 1 ? nav!.ids[idx + 1] : null;
  const go = (other: string | null) => other && leave(`/${base}/${encodeURIComponent(other)}`);

  // ---- save and approve (Design PRD 6 "Save and approve", Build PRD 5)
  const apiBody = () => ({ facets: decisionsToApi(facets, facetDec) });
  const fail = (e: unknown, retry: () => void) => setBanner({ ...errorInfo(e), retry });
  // "Try again" runs the action with the decisions on the page at that moment
  const actions = useRef({ save: () => {}, approve: () => {}, sendBack: () => {} });

  const save = async () => {
    if (!data || busy) return;
    const sent = decisionsKey(facetDec);
    setBusy("save");
    setBanner(null);
    try {
      await api.put("/api/products/" + enc(id) + "/review", apiBody());
    } catch (e) {
      fail(e, () => actions.current.save());
      setBusy(null);
      return;
    }
    setBaseline(sent);
    setBusy(null);
    if (data.isPublished) {
      toast({ message: "Saved · the website now shows these values", duration: TOAST_SHORT });
      changed();
      reload(true);
    } else {
      toast({ message: "Saved", duration: TOAST_SHORT });
    }
  };

  const approve = async () => {
    if (!data || busy) return;
    setBusy("approve");
    setBanner(null);
    try {
      await api.post("/api/products/" + enc(id) + "/approve-detail", apiBody());
    } catch (e) {
      fail(e, () => actions.current.approve());
      setBusy(null);
      return;
    }
    setBaseline(decisionsKey(facetDec));
    changed();
    const pid = id;
    const url = data.url;
    toast({
      message: "Approved and published",
      actions: [
        { label: "View on website", onClick: () => window.open(url, "_blank", "noopener,noreferrer") },
        {
          label: "Undo",
          onClick: () =>
            void api
              .post("/api/products/" + enc(pid) + "/unapprove")
              .catch((e) => toast({ message: errorInfo(e).message }))
              .finally(() => {
                changed();
                reloadRef.current?.(true);
              }),
        },
      ],
    });
    if (embedded) {
      // Live tag: stay on the page, now showing the approved product
      reload(true);
      onApprovedRef.current?.();
      return;
    }
    // on to the next product in the list, as the next button would; none left: back to the list.
    // Everything was just approved, so nothing is unsaved: no "leave without saving?" question.
    setLeaveGuard(null);
    navigate(nextId ? `/${base}/${enc(nextId)}` : backHref);
  };

  const sendBack = async () => {
    if (!data || busy) return;
    const restore = apiBody();
    const pid = id;
    setBusy("unapprove");
    setBanner(null);
    try {
      await api.post("/api/products/" + enc(pid) + "/unapprove");
    } catch (e) {
      fail(e, () => actions.current.sendBack());
      setBusy(null);
      return;
    }
    setBusy(null);
    changed();
    reload(true);
    toast({
      message: "Sent back to review · removed from the website",
      actions: [
        {
          label: "Undo",
          onClick: () =>
            void api
              .post("/api/products/" + enc(pid) + "/approve-detail", restore)
              .catch((e) => {
                const { message, problems } = errorInfo(e);
                toast({ message: [message, ...problems].join(" ") });
              })
              .finally(() => {
                changed();
                reloadRef.current?.(true);
              }),
        },
      ],
    });
  };
  actions.current = { save, approve, sendBack };

  // ---- category: approve as shown, or change it (which re-tags the product)
  const runCategory = async (d: CategoryDecision) => {
    setBusy("category");
    setRetag(null);
    let r: { jobId: number | null };
    try {
      r = await api.put<{ jobId: number | null }>(
        "/api/products/" + enc(id) + "/category",
        d.kind === "changed" ? { categoryId: d.id } : {},
      );
    } catch (e) {
      const { message, problems } = errorInfo(e);
      setRetag({ decision: d, jobId: null, seconds: 0, error: [message, ...problems].join(" ") });
      setBusy(null);
      return;
    }
    lastCat.current = d;
    if (r.jobId === null) {
      setBusy(null);
      changed();
      reload(true);
      return;
    }
    setRetag({ decision: d, jobId: r.jobId, seconds: 0, error: null });
    onCategoryJob?.(r.jobId);
  };

  const decideCategory = async (d: CategoryDecision) => {
    if (d.kind === "changed") {
      const ok = await confirm({
        title: "Change the category?",
        body: "Changing the category re-tags this product. This takes about 30 seconds.",
        ok: "Change category",
      });
      if (!ok) return;
    }
    void runCategory(d);
  };

  // the re-tag job: poll once a second until it is done or failed
  const jobId = retag && retag.error === null ? retag.jobId : null;
  useEffect(() => {
    if (jobId === null) return;
    let live = true;
    const started = Date.now();
    const tick = () =>
      api
        .get<Job>("/api/jobs/" + jobId)
        .then((j) => {
          if (!live) return;
          const seconds = Math.round(j.seconds ?? (Date.now() - started) / 1000);
          if (j.status === "running") {
            setRetag((r) => (r && r.jobId === jobId ? { ...r, seconds } : r));
            return;
          }
          live = false;
          window.clearInterval(t);
          setBusy(null);
          changed();
          if (j.status === "done") {
            setRetag(null);
            reload(false);
          } else {
            setRetag((r) => (r ? { ...r, error: j.error || "Re-tagging failed." } : r));
          }
        })
        .catch((e) => {
          if (!live) return;
          live = false;
          window.clearInterval(t);
          setBusy(null);
          setRetag((r) => (r ? { ...r, error: errorInfo(e).message } : r));
        });
    const t = window.setInterval(tick, 1000);
    return () => {
      live = false;
      window.clearInterval(t);
    };
  }, [jobId, changed, reload]);

  const status: Status | null = !data
    ? null
    : data.isPublished || data.state === "approved"
      ? "approved"
      : data.state === "ready"
        ? "ready"
        : "needs-review";

  const topBar = embedded ? (
    <div className="review-head">
      {data ? (
        <>
          <h2 className="title review-head-title">{data.title}</h2>
          <span className="muted review-head-brand">
            {data.brand} · {data.id}
          </span>
          {status && <StatusBadge status={status} />}
        </>
      ) : (
        <div className="skeleton review-head-sk" />
      )}
    </div>
  ) : (
    <TopBar
      left={
        <div className="detail-top-left">
          <button type="button" className="btn btn-quiet btn-sm" onClick={() => leave(backHref)}>
            ← {backLabel}
          </button>
          <span className="detail-top-divider" aria-hidden="true" />
          {data && (
            <div className="detail-top-title">
              <h1 className="title detail-title">{data.title}</h1>
              <span className="muted detail-brand">{data.brand}</span>
            </div>
          )}
          {status && <StatusBadge status={status} />}
        </div>
      }
      right={
        <div className="detail-top-right">
          {idx >= 0 && (
            <span className="small muted">
              {nav!.offset + idx + 1} of {nav!.total}
            </span>
          )}
          <button
            type="button"
            className="btn btn-secondary detail-step"
            title="Previous product"
            aria-label="Previous product"
            disabled={!prevId}
            onClick={() => go(prevId)}
          >
            ←
          </button>
          <button
            type="button"
            className="btn btn-secondary detail-step"
            title="Next product"
            aria-label="Next product"
            disabled={!nextId}
            onClick={() => go(nextId)}
          >
            →
          </button>
        </div>
      }
    />
  );

  // ---- error and loading
  if (error && embedded) {
    return (
      <div className="error-banner review-error" role="alert">
        <span className="error-banner-text">{error}</span>
      </div>
    );
  }
  const frame = "detail" + (embedded ? " is-embedded" : "");

  if (error) {
    return (
      <>
        {topBar}
        <div className="shell-scroll">
          <div className="detail-error" role="alert">
            <span>{error}</span>
            <a
              href={backHref}
              onClick={(e) => {
                e.preventDefault();
                navigate(backHref);
              }}
            >
              Back to {backLabel}
            </a>
          </div>
        </div>
      </>
    );
  }

  if (!data) {
    return (
      <>
        {topBar}
        <div className={frame} aria-busy="true">
          <div className="detail-left">
            <div className="skeleton detail-sk-photo" />
            <div className="skeleton detail-sk-line" />
            <div className="skeleton detail-sk-block" />
            <div className="skeleton detail-sk-block" />
          </div>
          <div className="detail-right">
            <div className="detail-facets">
              <div className="skeleton detail-sk-card" />
              <div className="skeleton detail-sk-open" />
              <div className="skeleton detail-sk-row" />
              <div className="skeleton detail-sk-row" />
              <div className="skeleton detail-sk-row" />
            </div>
            <div className="detail-footer" />
          </div>
        </div>
      </>
    );
  }

  // ---- page
  const renderRow = (f: Facet) => (
    <FacetRow
      key={f.id}
      facet={f}
      decision={facetDec[f.id]}
      open={f.flagged || expanded.has(f.id) || editing === f.id}
      editing={editing === f.id}
      onToggle={() => toggle(f)}
      onApprove={() => decide(f, { kind: "approved" })}
      onEdit={() => setEditing(f.id)}
      onEditDone={(r) => applyResult(f, r)}
      onEditCancel={() => setEditing(null)}
      onUnknown={() => applyResult(f, { state: "unknown", values: [] })}
      onQuote={(text) => setQuote((q) => ({ text, seq: (q?.seq ?? 0) + 1 }))}
      problem={banner?.problems.find((p) => p.startsWith(f.name + ":"))?.slice(f.name.length + 1).trim()}
    />
  );

  const footerMsg = facetsPending
    ? "Tagging…"
    : remaining > 0
      ? `${remaining} ${plural(remaining, "facet still needs", "facets still need")} a decision`
      : dirty
        ? "Unsaved changes"
        : "All facets decided";
  const canPublish = remaining === 0 && !locked && !facetsPending;
  const working = busy !== null || retagging || facetsPending;

  return (
    <>
      {topBar}
      <div className={frame}>
        <div className="detail-left">
          <EvidencePanel
            title={data.title}
            url={data.url}
            photos={data.photos}
            text={data.text}
            highlight={quote?.text ?? null}
            highlightSeq={quote?.seq ?? 0}
          />
        </div>

        <div className="detail-right">
          {banner && (
            <div className="error-banner detail-banner" role="alert">
              <div className="error-banner-text">
                {banner.message}
                {banner.problems.length > 0 && (
                  <ul className="error-banner-list">
                    {banner.problems.map((p) => (
                      <li key={p}>{p}</li>
                    ))}
                  </ul>
                )}
              </div>
              <button type="button" className="btn btn-secondary btn-sm" disabled={busy !== null} onClick={banner.retry}>
                Try again
              </button>
            </div>
          )}
          <div className="detail-facets">
            <CategoryCard
              category={data.category}
              decision={catDec}
              onDecide={(d) => void decideCategory(d)}
              busy={busy !== null || retagging}
              readOnly={data.isPublished}
            />
            {retag && retag.error === null && (
              <div className="detail-retag small" role="status">
                {retag.decision.kind === "changed" ? "Re-tagging with the new category…" : "Tagging with the approved category…"}{" "}
                <span className="muted">{retag.seconds} s</span>
              </div>
            )}
            {retag && retag.error !== null && (
              <div className="error-banner small" role="alert">
                <span className="error-banner-text">{retag.error}</span>
                <button type="button" className="btn btn-secondary btn-sm" onClick={() => void runCategory(retag.decision)}>
                  Try again
                </button>
              </div>
            )}
            {catPending && !retag && (
              <div className="detail-locked small">
                Decide the category first. Facets are locked until it is approved or changed.
              </div>
            )}
            {facetsPending && (
              <div className="detail-groups" aria-busy="true">
                <div className="skeleton detail-sk-open" />
                <div className="skeleton detail-sk-row" />
                <div className="skeleton detail-sk-row" />
                <div className="skeleton detail-sk-row" />
              </div>
            )}
            <div
              ref={lockRef}
              className={"detail-groups" + (locked ? " is-locked" : "")}
              aria-disabled={locked}
              hidden={facetsPending}
            >
              {reviewRows.length > 0 && (
                <>
                  <GroupHeading label="Needs review" count={reviewRows.length} first />
                  {reviewRows.map(renderRow)}
                </>
              )}
              {confidentRows.length > 0 && (
                <>
                  <GroupHeading label="Confident" count={confidentRows.length} first={reviewRows.length === 0} />
                  {confidentRows.map(renderRow)}
                </>
              )}
            </div>
          </div>

          <footer className="detail-footer">
            <div className="detail-footer-msg">
              {remaining > 0 && !facetsPending ? (
                <button type="button" className="detail-footer-link" onClick={scrollToFirstOpen}>
                  {footerMsg}
                </button>
              ) : (
                <span className={dirty ? "" : "muted"}>{footerMsg}</span>
              )}
              {remaining > 0 && dirty && <span className="muted"> · Unsaved changes</span>}
            </div>
            <div className="detail-footer-buttons">
              {data.isPublished ? (
                <>
                  <button type="button" className="btn btn-secondary" disabled={working} onClick={() => void sendBack()}>
                    {busy === "unapprove" ? "Saving…" : "Send back to review"}
                  </button>
                  <button
                    type="button"
                    className="btn btn-primary"
                    disabled={!dirty || !canPublish || working}
                    title={!dirty ? "Nothing has changed yet." : !canPublish ? whatIsLeft : undefined}
                    onClick={() => void save()}
                  >
                    {busy === "save" ? "Saving…" : "Save changes"}
                  </button>
                </>
              ) : (
                <>
                  <button
                    type="button"
                    className="btn btn-secondary"
                    disabled={!dirty || working}
                    title={!dirty ? "Nothing to save yet." : undefined}
                    onClick={() => void save()}
                  >
                    {busy === "save" ? "Saving…" : "Save"}
                  </button>
                  <button
                    type="button"
                    className="btn btn-primary"
                    disabled={!canPublish || working}
                    title={!canPublish ? whatIsLeft : undefined}
                    onClick={() => void approve()}
                  >
                    {busy === "approve" ? "Saving…" : "Approve and publish"}
                  </button>
                </>
              )}
            </div>
          </footer>
        </div>
      </div>
    </>
  );
}

function GroupHeading({ label, count, first }: { label: string; count: number; first?: boolean }) {
  return (
    <div className={"detail-group-head" + (first ? " is-first" : "")}>
      <span className="detail-group-label">{label}</span>
      <span className="detail-group-count">{count}</span>
    </div>
  );
}
