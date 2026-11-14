import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorInfo, PRODUCTS_CHANGED } from "../api";
import { useConfirm } from "../components/ConfirmDialog";
import { StatusBadge } from "../components/StatusBadge";
import { useToast } from "../components/Toast";
import { TopBar } from "../components/TopBar";
import { navigate } from "../router";
import { ProductReview } from "./ProductDetail";
import "./ProductDetail.css";
import "./LiveTag.css";

// Live tag tab (Design PRD 7). Paste an Ounass link: a single product runs the four steps on the
// server and fills in the same review as the detail page under the tracker; a listing page tags its
// first X products and fills a table row by row. The link's type comes only from the server
// (POST /api/live-tag/check); the page never matches URL patterns itself.

type Kind = "product" | "listing";
type Check = {
  kind: Kind | "neither";
  productId?: string;
  existing?: boolean;
  estimate?: { seconds: number; costUsd: number; perProductSeconds: number };
};
type StepState = "waiting" | "running" | "done" | "failed" | "paused";
type Step = { name: string; state: StepState; seconds: number | null; note: string | null };
type ItemStep = "waiting" | "fetching" | "categorising" | "tagging" | "publishing" | "done" | "failed" | "cancelled";
type Item = {
  position: number;
  productId: string;
  url: string;
  step: ItemStep;
  state: "ready" | "needs_review" | "approved" | "in_store" | null;
  nFlagged: number | null;
  thumb?: string | null;
  already: boolean;
  title: string | null;
  brand: string | null;
  error: string | null;
  seconds: number | null;
  costUsd: number;
};
type Job = {
  id: number;
  kind: "live" | "listing" | "retag";
  productId: string | null;
  status: "running" | "done" | "failed" | "waiting_for_category" | "stopped";
  steps: Step[];
  error: string | null;
  errorStep: string | null;
  costUsd: number;
  models: { categorise?: string; tag?: string };
  items?: Item[];
  stopping: boolean;
  seconds: number;
};
type Run =
  | { kind: "product"; url: string; productId: string; jobId: number | null; existing: boolean; seq: number }
  | { kind: "listing"; url: string; jobId: number; limit: number; seq: number; sample?: boolean };

const STEPS = ["Fetching product", "Categorising", "Tagging", "Done"];
const STEP_LABEL: Record<ItemStep, string> = {
  waiting: "Waiting",
  fetching: "Fetching",
  categorising: "Categorising",
  tagging: "Tagging",
  publishing: "Adding to the store",
  done: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
};
const KIND_PAGE: Record<Kind, string> = { product: "product page", listing: "listing page" };
const KIND_OPTION: Record<Kind, string> = { product: "Single product", listing: "Listing page" };
const HELPER: Record<Kind, string> = {
  product: "Paste a product link from the Ounass website",
  listing: "Paste a category, brand or search page link",
};
const NOT_OUNASS = "This is not an Ounass product or listing link";
const RECENT_KEY = "tagger:liveRecent";
const MAX = 25;

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);
const enc = encodeURIComponent;

function fmtSecs(s: number | null | undefined) {
  if (s === null || s === undefined) return "";
  if (s < 60) return `${s.toFixed(1)} s`;
  const r = Math.round(s);
  return `${Math.floor(r / 60)} min ${r % 60} s`;
}
const fmtCost = (n: number) => "$" + (n > 0 && n < 0.1 ? n.toFixed(3) : n.toFixed(2));

function readRecent(): string[] {
  try {
    const v = JSON.parse(sessionStorage.getItem(RECENT_KEY) ?? "[]");
    return Array.isArray(v) ? v.filter((x) => typeof x === "string").slice(0, 5) : [];
  } catch {
    return [];
  }
}

/** Poll a job once a second while it runs. `seq` fetches it again (after an approve or a retry). */
function useJob(jobId: number | null, seq: number) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setJob((j) => (j && j.id === jobId ? j : null));
    setError(null);
    if (jobId === null) return;
    let live = true;
    let t: number | undefined;
    const tick = () =>
      api
        .get<Job>("/api/jobs/" + jobId)
        .then((j) => {
          if (!live) return;
          setJob(j);
          if (j.status === "running") t = window.setTimeout(tick, 1000);
        })
        .catch((e) => live && setError(errorInfo(e).message));
    tick();
    return () => {
      live = false;
      window.clearTimeout(t);
    };
  }, [jobId, seq]);
  return { job, error };
}

export function LiveTag({ onChanged }: { onChanged?: () => void }) {
  const [url, setUrl] = useState("");
  const [mode, setMode] = useState<Kind>("product");
  // Richie's store samples (owner, 14 Nov 2026): a listing, tagged as usual, published for the store only
  const [storeSample, setStoreSample] = useState(false);
  const [override, setOverride] = useState(false);
  const overrideRef = useRef(false);
  overrideRef.current = override;
  const [x, setX] = useState("5");
  const [check, setCheck] = useState<(Check & { url: string }) | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mismatch, setMismatch] = useState<Kind | null>(null);
  const [starting, setStarting] = useState(false);
  const [run, setRun] = useState<Run | null>(null);
  const [jobSeq, setJobSeq] = useState(0);
  const [retagJobId, setRetagJobId] = useState<number | null>(null);
  const [recent, setRecent] = useState<string[]>(readRecent);
  const [approving, setApproving] = useState<Set<number>>(new Set());
  const runSeq = useRef(0);
  const toast = useToast();
  const confirm = useConfirm();

  const xn = Number(x);
  const xValid = x.trim() !== "" && Number.isInteger(xn) && xn >= 1 && xn <= MAX;
  const listing = mode === "listing";

  const { job, error: pollError } = useJob(run?.jobId ?? null, jobSeq);
  const { job: retagJob } = useJob(retagJobId, 0);

  const busy =
    starting || job?.status === "running" || retagJob?.status === "running";

  // ---- the toggle sets itself from the server's reading of the link (debounced)
  const limitKey = listing && xValid ? xn : null;
  useEffect(() => {
    const u = url.trim();
    if (!u) {
      setCheck(null);
      return;
    }
    let live = true;
    const t = window.setTimeout(() => {
      api
        .post<Check>("/api/live-tag/check", { url: u, limit: limitKey ?? undefined })
        .then((c) => {
          if (!live) return;
          setCheck({ ...c, url: u });
          if (!overrideRef.current && !storeSampleRef.current && c.kind !== "neither") setMode(c.kind);
        })
        .catch(() => live && setCheck(null));
    }, 300);
    return () => {
      live = false;
      window.clearTimeout(t);
    };
  }, [url, limitKey]);

  const changeUrl = (v: string) => {
    setUrl(v);
    setOverride(false);
    setError(null);
    setMismatch(null);
  };

  const storeSampleRef = useRef(storeSample);
  storeSampleRef.current = storeSample;
  const chooseMode = (k: Kind, sample = false) => {
    setStoreSample(sample);
    if (k === mode) return;
    setMode(k);
    setOverride(true);
    setMismatch(null);
    setError(null);
  };

  const remember = (u: string) =>
    setRecent((r) => {
      const next = [u, ...r.filter((x) => x !== u)].slice(0, 5);
      try {
        sessionStorage.setItem(RECENT_KEY, JSON.stringify(next));
      } catch {
        /* private mode: the list simply is not kept */
      }
      return next;
    });

  // ---- start a run
  const start = async (opts: { url?: string; again?: boolean } = {}) => {
    const u = (opts.url ?? url).trim();
    if (!u || busy) return;
    setError(null);
    setMismatch(null);
    setStarting(true);
    try {
      const c = await api.post<Check>("/api/live-tag/check", { url: u, limit: xValid ? xn : undefined });
      if (c.kind === "neither") {
        setError(NOT_OUNASS);
        return;
      }
      let m = mode;
      if (storeSample) m = "listing";
      else if (opts.url !== undefined || !overrideRef.current) {
        m = c.kind;
        setMode(m);
      }
      if (c.kind !== m) {
        setMismatch(c.kind);
        return;
      }
      if (m === "listing" && !xValid) return;
      runSeq.current += 1;
      if (m === "product") {
        const r = await api.post<{ productId: string; existing: boolean; jobId: number | null }>("/api/live-tag", {
          url: u,
          mode: "product",
          again: !!opts.again,
        });
        setRetagJobId(null);
        setRun({ kind: "product", url: u, productId: r.productId, jobId: r.jobId, existing: r.existing, seq: runSeq.current });
      } else {
        const r = await api.post<{ jobId: number }>("/api/live-tag", { url: u, mode: storeSample ? "sample" : "listing", limit: xn });
        setRetagJobId(null);
        setRun({ kind: "listing", url: u, jobId: r.jobId, limit: xn, seq: runSeq.current, sample: storeSample });
      }
      remember(u);
    } catch (e) {
      setError(errorInfo(e).message);
    } finally {
      setStarting(false);
    }
  };

  const reset = () => {
    setUrl("");
    setMode("product");
    setStoreSample(false);
    setOverride(false);
    setX("5");
    setCheck(null);
    setError(null);
    setMismatch(null);
    setRun(null);
    setRetagJobId(null);
  };

  const refreshJob = useCallback(() => setJobSeq((n) => n + 1), []);
  const changed = useCallback(() => {
    onChanged?.();
    window.dispatchEvent(new Event(PRODUCTS_CHANGED));
  }, [onChanged]);

  // ---- single product: retry a failed step
  const retry = async () => {
    if (!run || run.kind !== "product" || run.jobId === null) return;
    setError(null);
    try {
      const r = await api.post<{ productId: string; jobId: number }>("/api/jobs/" + run.jobId + "/retry");
      setRetagJobId(null);
      setRun({ ...run, productId: r.productId, jobId: r.jobId, existing: false });
    } catch (e) {
      setError(errorInfo(e).message);
    }
  };

  // ---- listing: approve, undo, stop, retry an item
  const undo = async (ids: string[]) => {
    try {
      for (const id of ids) await api.post("/api/products/" + enc(id) + "/unapprove");
    } catch (e) {
      toast({ message: errorInfo(e).message });
    }
    changed();
    refreshJob();
  };

  const approveItem = async (it: Item) => {
    setApproving((s) => new Set(s).add(it.position));
    try {
      await api.post("/api/products/" + enc(it.productId) + "/approve", {});
    } catch (e) {
      toast({ message: errorInfo(e).message });
      return;
    } finally {
      setApproving((s) => {
        const next = new Set(s);
        next.delete(it.position);
        return next;
      });
    }
    changed();
    refreshJob();
    toast({
      message: "Approved and published",
      actions: [
        { label: "View on website", onClick: () => window.open(it.url, "_blank", "noopener,noreferrer") },
        { label: "Undo", onClick: () => void undo([it.productId]) },
      ],
    });
  };

  const [approvingAll, setApprovingAll] = useState(false);
  const approveAll = async (ids: string[]) => {
    const n = ids.length;
    const ok = await confirm({
      title: `Approve and publish ${n} ${plural(n, "product", "products")}?`,
      body: "Every Ready product from this listing goes live on the website straight away. You can undo for 8 seconds.",
      ok: `Approve ${n}`,
    });
    if (!ok) return;
    setApprovingAll(true);
    try {
      const r = await api.post<{ approved: string[]; failed: { id: string; message: string }[] }>(
        "/api/products/approve-ready",
        { state: "ready", q: "", category: "", facet: "", value: "", flagged: false, ids: ids.join(",") },
      );
      const done = r.approved;
      const failText = r.failed.length > 0 ? ` · ${r.failed.length} could not be approved` : "";
      toast({
        message: `${done.length} ${plural(done.length, "product", "products")} approved and published${failText}`,
        actions: done.length > 0 ? [{ label: "Undo", onClick: () => void undo(done) }] : [],
      });
    } catch (e) {
      const { message, problems } = errorInfo(e);
      toast({ message: [message, ...problems].join(" ") });
    } finally {
      setApprovingAll(false);
      changed();
      refreshJob();
    }
  };

  const stop = async () => {
    if (!run) return;
    try {
      await api.post("/api/jobs/" + run.jobId + "/stop");
    } catch (e) {
      toast({ message: errorInfo(e).message });
    }
  };

  const retryItem = async (it: Item) => {
    if (!run) return;
    try {
      await api.post("/api/jobs/" + run.jobId + "/items/" + it.position + "/retry");
    } catch (e) {
      toast({ message: errorInfo(e).message });
      return;
    }
    refreshJob();
  };

  // ---- what to show under the box
  const trimmed = url.trim();
  const detectedKind = check && check.url === trimmed && check.kind !== "neither" ? check.kind : null;
  const tracking = run?.kind === "product" && !run.existing;
  let detected: string | null = null;
  if (detectedKind && !error && !mismatch && !busy) {
    detected = `Detected: ${KIND_PAGE[detectedKind]}`;
    const est = check?.estimate;
    if (listing && detectedKind === "listing" && xValid && est) {
      const mins = Math.max(1, Math.round(est.seconds / 60));
      detected += ` · About ${mins} ${plural(mins, "minute", "minutes")} and $${est.costUsd.toFixed(2)}`;
    }
  }
  const showHelper = !error && !mismatch && !detected && !tracking;
  const buttonLabel = listing
    ? xValid
      ? `Tag ${xn} ${plural(xn, "product", "products")}`
      : "Tag products"
    : "Tag product";

  return (
    <>
      <TopBar
        left={<h1 className="title">Live tag</h1>}
        right={
          (run || url) && (
            <button type="button" className="btn btn-quiet" onClick={reset}>
              Tag another
            </button>
          )
        }
      />
      <div className="shell-scroll">
        <div className="live">
          <div className="live-toggle" role="radiogroup" aria-label="What the link is">
            {(["product", "listing"] as Kind[]).map((k) => (
              <button
                key={k}
                type="button"
                role="radio"
                aria-checked={mode === k && !storeSample}
                className={"live-toggle-item" + (mode === k && !storeSample ? " is-active" : "")}
                onClick={() => chooseMode(k)}
              >
                {KIND_OPTION[k]}
              </button>
            ))}
            <button
              type="button"
              role="radio"
              aria-checked={storeSample}
              className={"live-toggle-item" + (storeSample ? " is-active" : "")}
              onClick={() => chooseMode("listing", true)}
            >
              Store sample (listing)
            </button>
          </div>
          {storeSample && (
            <p className="small muted live-sample-note">
              For Richie's store only: each new product is tagged as usual and added to the store without review.
              Store samples never appear in this app's tabs or figures. Products already here are left as they are.
            </p>
          )}

          <form
            className="live-form"
            onSubmit={(e) => {
              e.preventDefault();
              void start();
            }}
          >
            <input
              className={"live-url" + (error ? " is-error" : "")}
              value={url}
              onChange={(e) => changeUrl(e.target.value)}
              placeholder="https://www.ounass.ae/…"
              aria-label="Ounass product or listing link"
              autoFocus
            />
            {listing && (
              <label className="live-number">
                First
                <input
                  type="number"
                  min={1}
                  max={MAX}
                  step={1}
                  value={x}
                  onChange={(e) => setX(e.target.value)}
                  aria-label="Number of products"
                  aria-invalid={!xValid}
                  className={xValid ? "" : "is-error"}
                />
                products
              </label>
            )}
            <button type="submit" className="btn btn-primary live-submit" disabled={busy}>
              {buttonLabel}
            </button>
          </form>

          {error && (
            <span className="small live-error" role="alert">
              {error}
            </span>
          )}
          {pollError && !error && (
            <span className="small live-error" role="alert">
              {pollError}
            </span>
          )}
          {mismatch && (
            <div className="small live-mismatch" role="alert">
              <span>
                This is a {KIND_PAGE[mismatch]} link, but “{KIND_OPTION[mode]}” is selected.
              </span>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => {
                  setMode(mismatch);
                  setOverride(false);
                  setMismatch(null);
                }}
              >
                Switch to {KIND_OPTION[mismatch]}
              </button>
            </div>
          )}
          {showHelper && <span className="small muted">{HELPER[mode]}</span>}
          {detected && <span className="small muted">{detected}</span>}
          {listing && !xValid && (
            <span className="small live-error">Choose a number from 1 to 25.</span>
          )}

          {run?.kind === "product" && !run.existing && (
            <Tracker job={job} retagJob={retagJob} onRetry={() => void retry()} />
          )}
          {run?.kind === "product" && run.existing && (
            <div className="live-existing">
              <span>Already tagged. Showing the existing result.</span>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                disabled={busy}
                onClick={() => void start({ url: run.url, again: true })}
              >
                Tag again
              </button>
            </div>
          )}

          {recent.length > 0 && (
            <div className="live-recent">
              <span className="label">Tagged this session</span>
              {recent.map((u) => (
                <button
                  key={u}
                  type="button"
                  className="live-recent-link"
                  disabled={busy}
                  onClick={() => changeUrl(u)}
                >
                  {u.replace(/^https?:\/\/(www\.)?/, "")}
                </button>
              ))}
            </div>
          )}
        </div>

        {run?.kind === "listing" && (
          <ListingTable
            job={job}
            limit={run.limit}
            approving={approving}
            approvingAll={approvingAll}
            onApprove={(it) => void approveItem(it)}
            onApproveAll={(ids) => void approveAll(ids)}
            onStop={() => void stop()}
            onRetry={(it) => void retryItem(it)}
            onRetryPage={() => void start({ url: run.url })}
            sample={!!run.sample}
          />
        )}

        {run?.kind === "product" && (
          <SingleResult key={run.seq} run={run} job={job} retagJob={retagJob} onChanged={changed} onCategoryJob={setRetagJobId} />
        )}
      </div>
    </>
  );
}

// ------------------------------------------------------------------ single product

function trackerSteps(job: Job | null, retagJob: Job | null): Step[] {
  const steps = STEPS.map(
    (n) => job?.steps.find((s) => s.name === n) ?? { name: n, state: "waiting" as StepState, seconds: null, note: null },
  );
  if (retagJob) {
    const t = retagJob.steps.find((s) => s.name === "Tagging");
    if (t) steps[2] = t;
    if (retagJob.status === "done") {
      steps[3] = { name: "Done", state: "done", seconds: (job?.seconds ?? 0) + retagJob.seconds, note: null };
    }
  }
  return steps;
}

function Tracker({ job, retagJob, onRetry }: { job: Job | null; retagJob: Job | null; onRetry: () => void }) {
  const steps = trackerSteps(job, retagJob);
  const models = { ...(job?.models ?? {}), ...(retagJob?.models ?? {}) };
  const cost = (job?.costUsd ?? 0) + (retagJob?.costUsd ?? 0);
  const paused = job?.status === "waiting_for_category" && (!retagJob || retagJob.status === "failed");

  const note = (s: Step) => {
    if (s.state === "running") return "Running…";
    if (s.state === "failed") return "Stopped";
    if (s.state === "paused") return "Waiting for category";
    if (s.state !== "done") return "";
    if (s.name === "Done") {
      const used = [models.categorise, models.tag].filter(Boolean).join(" and ");
      return [`Total ${fmtSecs(s.seconds)}`, fmtCost(cost), used].filter(Boolean).join(" · ");
    }
    return [s.note, fmtSecs(s.seconds)].filter(Boolean).join(" · ");
  };

  return (
    <>
      <ol className="live-tracker" aria-label="Progress">
        {steps.map((s, i) => (
          <li key={s.name} className={"live-step is-" + s.state}>
            <div className="live-step-head">
              <span className="live-step-dot" aria-hidden="true">
                {s.state === "done" ? "✓" : s.state === "failed" ? "!" : i + 1}
              </span>
              <span className="live-step-name">{s.name}</span>
            </div>
            <span className="small live-step-note">{note(s)}</span>
          </li>
        ))}
      </ol>
      {job?.status === "failed" && (
        <div className="error-banner" role="alert">
          <span className="error-banner-text">{job.error || "The run stopped."}</span>
          <button type="button" className="btn btn-secondary btn-sm" onClick={onRetry}>
            Try again
          </button>
        </div>
      )}
      {paused && (
        <div className="live-paused" role="status">
          Paused: the category is below its confidence level. Approve or change it below and tagging continues.
        </div>
      )}
    </>
  );
}

function SingleResult({
  run,
  job,
  retagJob,
  onChanged,
  onCategoryJob,
}: {
  run: Extract<Run, { kind: "product" }>;
  job: Job | null;
  retagJob: Job | null;
  onChanged: () => void;
  onCategoryJob: (id: number) => void;
}) {
  const steps = trackerSteps(job, null);
  const catDone = steps[1].state === "done";
  const tagDone = steps[2].state === "done";
  const retagDone = retagJob?.status === "done";
  const running = job?.status === "running";

  if (!run.existing && !catDone) {
    // stopped before the product was categorised: nothing to show (the tracker says why)
    if (job && !running) return null;
    // the product cannot be read until it is categorised: placeholders in the shape of the result
    return (
      <div className="live-result" aria-busy="true">
        <div className="review-head">
          <div className="skeleton review-head-sk" />
        </div>
        <div className="detail is-embedded">
          <div className="detail-left">
            <div className="skeleton detail-sk-photo" />
            <div className="skeleton detail-sk-line" />
            <div className="skeleton detail-sk-block" />
          </div>
          <div className="detail-right">
            <div className="detail-facets">
              <div className="skeleton detail-sk-card" />
              <div className="skeleton detail-sk-row" />
              <div className="skeleton detail-sk-row" />
            </div>
            <div className="detail-footer" />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="live-result">
      <ProductReview
        productId={run.productId}
        embedded
        reloadKey={(tagDone ? 1 : 0) + (retagDone ? 2 : 0)}
        facetsPending={!run.existing && running && !tagDone}
        onChanged={onChanged}
        onCategoryJob={onCategoryJob}
      />
    </div>
  );
}

// ------------------------------------------------------------------ listing

function ListingTable({
  job,
  limit,
  approving,
  approvingAll,
  onApprove,
  onApproveAll,
  onStop,
  onRetry,
  onRetryPage,
  sample = false,
}: {
  job: Job | null;
  limit: number;
  approving: Set<number>;
  approvingAll: boolean;
  onApprove: (it: Item) => void;
  onApproveAll: (ids: string[]) => void;
  onStop: () => void;
  onRetry: (it: Item) => void;
  /** Read the listing page again, after it failed before any product was found. */
  onRetryPage: () => void;
  /** A store-sample run: results go to the store, with no review actions here. */
  sample?: boolean;
}) {
  const items = job?.items ?? [];
  const known = items.length > 0;
  const finished = !!job && job.status !== "running";
  const reading = job?.steps.find((s) => s.name === "Reading the listing page");

  const fin = items.filter((it) => it.step === "done" || it.step === "failed" || it.step === "cancelled").length;
  const done = items.filter((it) => it.step === "done");
  const ready = done.filter((it) => it.state === "ready");
  const review = done.filter((it) => it.state === "needs_review");
  const approved = done.filter((it) => it.state === "approved");
  const failed = items.filter((it) => it.step === "failed").length;
  const cancelled = items.filter((it) => it.step === "cancelled").length;
  const totals = job ? `${fmtSecs(job.seconds)} · ${fmtCost(job.costUsd)}` : "";

  let line: string;
  if (!job || (!known && !finished)) line = "Reading the listing page…";
  else if (finished && job.status === "failed" && !known) line = "The listing page could not be read.";
  else if (finished && sample)
    line =
      `${done.filter((it) => it.state === "in_store").length} added to the store` +
      (done.some((it) => it.already) ? ` · ${done.filter((it) => it.already).length} already in the catalog` : "") +
      (failed ? ` · ${failed} failed` : "") +
      (cancelled ? ` · ${cancelled} cancelled` : "");
  else if (finished)
    line =
      `${done.length} tagged: ${ready.length} ready, ${review.length} ${plural(review.length, "needs", "need")} review` +
      (approved.length ? `, ${approved.length} approved` : "") +
      (failed ? ` · ${failed} failed` : "") +
      (cancelled ? ` · ${cancelled} cancelled` : "");
  else line = `${fin} of ${items.length} done · ${totals}`;

  const notes = [reading?.note, finished && known ? totals : null].filter(Boolean).join(" · ");
  const placeholders = Array.from({ length: limit }, (_, i) => i + 1);

  return (
    <div className="live live-batch">
      <div className="live-batch-head">
        <div className="live-batch-line">
          <span className="live-batch-title">{line}</span>
          {notes && <span className="small muted">{notes}</span>}
        </div>
        <div className="live-batch-buttons">
          {!finished && job && (
            <button
              type="button"
              className="btn btn-secondary"
              disabled={job.stopping}
              title="The product in progress finishes; the rest are cancelled"
              onClick={onStop}
            >
              {job.stopping ? "Stopping…" : "Stop"}
            </button>
          )}
          {finished && known && !sample && (
            <>
              <button
                type="button"
                className="btn btn-secondary"
                disabled={ready.length === 0 || approvingAll}
                title={ready.length ? undefined : "No Ready products from this listing"}
                onClick={() => onApproveAll(ready.map((it) => it.productId))}
              >
                {approvingAll ? "Saving…" : `Approve all ready (${ready.length})`}
              </button>
              <button
                type="button"
                className="btn btn-secondary"
                disabled={done.length === 0}
                onClick={() => navigate("/products?ids=" + enc(done.map((it) => it.productId).join(",")))}
              >
                Open in Products
              </button>
            </>
          )}
        </div>
      </div>
      {job?.status === "failed" && job.error && (
        <div className="error-banner small" role="alert">
          <span className="error-banner-text">{job.error}</span>
          {!known && <button className="btn btn-secondary btn-sm" onClick={onRetryPage}>Try again</button>}
        </div>
      )}

      <div className="live-table" role="table" aria-label="Products from the listing page">
        <div className="live-row live-row-head label" role="row">
          <span role="columnheader">#</span>
          <span role="columnheader">Product</span>
          <span role="columnheader">Step</span>
          <span role="columnheader">Result</span>
          <span role="columnheader" className="live-cell-action">
            Action
          </span>
        </div>
        {known
          ? items.map((it) => (
              <ItemRow
                key={it.position}
                it={it}
                approving={approving.has(it.position)}
                onApprove={() => onApprove(it)}
                onRetry={() => onRetry(it)}
              />
            ))
          : !(finished && job?.status === "failed") &&
            placeholders.map((n) => (
              <div key={n} className="live-row" role="row">
                <span className="muted">{n}</span>
                <ProductPlaceholder />
                <span className="muted">Waiting</span>
                <span />
                <span />
              </div>
            ))}
      </div>
    </div>
  );
}

function ProductPlaceholder() {
  return (
    <div className="live-product" aria-hidden="true">
      <div className="skeleton live-thumb" />
      <div className="live-sk-lines">
        <div className="skeleton live-sk-long" />
        <div className="skeleton live-sk-short" />
      </div>
    </div>
  );
}

function ItemRow({
  it,
  approving,
  onApprove,
  onRetry,
}: {
  it: Item;
  approving: boolean;
  onApprove: () => void;
  onRetry: () => void;
}) {
  const active = it.step === "fetching" || it.step === "categorising" || it.step === "tagging";
  const isDone = it.step === "done";
  return (
    <div className="live-row" role="row">
      <span className="muted">{it.position}</span>
      {it.title || it.brand ? (
        <div className="live-product">
          {it.thumb ? (
            <img className="live-thumb" src={it.thumb} alt={it.title ?? ""} />
          ) : (
            <div className="live-thumb live-thumb-stripe" title={it.title ?? undefined} />
          )}
          <div className="live-product-text">
            <span className="live-product-title">{it.title}</span>
            <span className="small muted">{it.brand}</span>
          </div>
        </div>
      ) : (
        <ProductPlaceholder />
      )}
      <div className="live-item-step">
        <span className={"live-item-step-label is-" + it.step + (active ? " is-active" : "")}>{STEP_LABEL[it.step]}</span>
        {it.step === "failed" && (
          <>
            {it.error && <span className="small live-error">{it.error}</span>}
            <button type="button" className="live-linkbtn small" onClick={onRetry}>
              Try again
            </button>
          </>
        )}
      </div>
      <div className="live-item-result">
        {isDone && it.already && <span className="pill pill-outline status-muted live-already">Already tagged</span>}
        {isDone && it.state === "in_store" && <span className="pill pill-outline live-already">In the store</span>}
        {isDone && it.state === "ready" && <StatusBadge status="ready" />}
        {isDone && it.state === "needs_review" && <StatusBadge status="needs-review" />}
        {isDone && it.state === "approved" && <StatusBadge status="approved" />}
        {isDone && it.state === "needs_review" && !!it.nFlagged && (
          <span className="small muted">{it.nFlagged} flagged</span>
        )}
      </div>
      <div className="live-cell-action">
        {isDone && it.state === "ready" && (
          <button type="button" className="btn btn-primary" disabled={approving} onClick={onApprove}>
            {approving ? "Saving…" : "Approve"}
          </button>
        )}
        {isDone && it.state === "needs_review" && (
          <button type="button" className="btn btn-review" onClick={() => navigate("/products/" + enc(it.productId))}>
            Review
          </button>
        )}
        {isDone && it.state === "approved" && (
          <button type="button" className="btn btn-secondary" onClick={() => navigate("/approved/" + enc(it.productId))}>
            Open
          </button>
        )}
      </div>
    </div>
  );
}
