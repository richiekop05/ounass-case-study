import { useEffect, useMemo, useState } from "react";
import { api, errorInfo } from "../api";
import { TopBar } from "../components/TopBar";
import { navigate, setParams, useRoute } from "../router";
import "./Metrics.css";

// Metrics tab (Design PRD 11, Build PRD 11, decisions of 14 Nov): live figures for every tagged product at its
// latest tagging, quality from the latest run that covered the golden set; a picked run narrows it all. Every number comes from
// GET /api/metrics with its numerator and denominator; a share with nothing to count is a dash and a reason.

type Share = { value: number | null; num: number | null; den: number | null; reason?: string };
type Run = {
  id: number; date: string | null; tagModel: string | null; categoriseModel: string | null;
  definitionsVersion: number | null; products: number; goldenProducts: number;
};
type Pair = { tuned: Share; holdout: Share };
type CardRow = {
  facet: string; name: string; answers: number; confidenceMean: number | null; smallSample: boolean;
  golden: { precision: Share; recall: Share; coverage: Share; reviewRate: Share };
  live: { precision: Share; recall: Share; coverage: Share; reviewRate: Share; answers: number };
  precisionDiff: number | null; drift: "stable" | "watch" | "alert" | null; driftNote: string | null;
};
type HealthRow = {
  facet: string; name: string; unknownBaseline: number | null; unknownNow: number | null;
  unknownChange: number | null; confidenceShift: number | null; status: "stable" | "watch" | "alert";
};
type Alert = {
  id: number; run: number; facet: string; segment: string; check: string; baseline: number | null;
  current: number | null; change: number | null; severity: string; at: string | null;
};
type Bench = Share & { products: number };
type BenchRow = {
  key: string; label: string; tuned: Bench; holdout: Bench; live: Bench; against: "tuned" | "holdout";
  diffPoints: number | null; status: "stable" | "watch" | "alert" | null; statusNote: string | null;
};
type Metrics = {
  /** The last computed page, served while a fresh one is calculated in the background. */
  stale: boolean;
  scope: { mode: "live" | "run"; products: number; tagModels: string | null };
  run: Run | null;
  qualityRun: Run | null;
  runs: Run[];
  top: {
    precision: { main: Share; isTuned: boolean; tuned: Share };
    autoAccepted: Share;
    reviewRate: Share & { products: number };
    categoryAccuracy: Share & { flagged: number; held: number };
    costPerProduct: { value: number | null; projection: number | null; catalogSize: number };
  };
  quality: {
    benchmark: { rows: BenchRow[]; liveMin: number };
    facetLiveMin: number;
    precision: Pair;
    correctlyLeftUnknown: Share; inventedQuote: Share;
    scored: { tuned: number; holdout: number; excludedWrongCategory: number };
    goldenSizes: { tuned: number; holdout: number };
    scorecard: CardRow[];
    calibration: Record<string, Record<string, Share>>;
    calibrationBands: string[];
    calibrationDiagonal: Record<string, number | null>;
  } | null;
  review: {
    reviewRate: Share; productsNeedingReview: Share;
    progress: { ready: number; needsReview: number; approved: number };
    reviewedTags: number; approvedUnchanged: Share; changedOnReview: Share; acceptedCorrected: Share;
    categoryOverrides: Share;
    perFacet: { facet: string; name: string; unchanged: number; changed: number; reviewed: number }[];
    reviewTime: { medianSeconds: number | null; products: number; reason?: string };
    bySource: Record<"app" | "sheet" | "auto", { reviewed: number; unchanged: number; changed: number; products: number }>;
  };
  cost: {
    scope: "all" | "run"; runs: number; totalCost: number; products: number; models: string[];
    costPerProduct: number | null; secondsPerProduct: number | null; catalogSize: number;
    projection: { standard: number | null; batch: number | null; batchFromStoredPrices: boolean };
    comparison: { run: number; model: string; costPerProduct: number | null; precision: Share }[];
  };
  health: {
    run: number | null; monitored: boolean; baselineRunId: number | null; facets: HealthRow[];
    overTime: { label: string; run: number | null; date: string | null; autoAccepted: Share; reviewRate: Share }[];
    alerts: Alert[];
  };
};
type Facet = { id: string; name: string };
type CatNode = { id: string; path: string; name: string; depth: number; count: number };

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MODEL_NAMES: Record<string, string> = { haiku: "Haiku", sonnet: "Sonnet", opus: "Opus" };
const BLOCKS = [
  ["mx-b1", "Is it right?"],
  ["mx-b2", "Human work"],
  ["mx-b3", "Cost"],
  ["mx-b4", "Health"],
] as const;
const SERIES = [
  { key: "text", label: "Text", cls: "s1" },
  { key: "photo", label: "Photo", cls: "s2" },
  { key: "both", label: "Both", cls: "s3" },
];
const SMALL = 20;

// ------------------------------------------------------------------ formatting

const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${Math.round(v * 100)}%`);
const fmtDate = (iso: string | null) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
};
const model = (m: string | null) => (m ? MODEL_NAMES[m] ?? m : "—");
/** Two significant figures (Design PRD 11), with thousands separators for large amounts. */
function sig2(v: number): string {
  if (v === 0) return "0";
  const r = Number(v.toPrecision(2));
  return Math.abs(r) >= 1000 ? r.toLocaleString("en-US") : String(r);
}
const dollars = (v: number | null) => (v === null ? "—" : `$${sig2(v)}`);
const cents = (v: number | null) => (v === null ? "—" : `${sig2(v * 100)}¢`);
const points = (v: number | null) => (v === null ? "—" : `${v >= 0 ? "+" : "−"}${Math.abs(Math.round(v * 100))} pts`);
const of = (s: Share, noun = "") => (s.den ? `${s.num} of ${s.den}${noun ? " " + noun : ""}` : s.reason ?? "nothing to count");

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

// ------------------------------------------------------------------ small pieces

function Value({ s, big = false }: { s: Share; big?: boolean }) {
  return (
    <span className={big ? "mx-big" : "mx-stat"} title={s.reason}>
      {pct(s.value)}
    </span>
  );
}

function Stat({ label, s, sub, note }: { label: string; s: Share; sub?: string; note?: string }) {
  return (
    <div className="mx-statbox">
      <span className="mx-label">{label}</span>
      <Value s={s} />
      <span className="mx-sub">{s.value === null ? s.reason : sub ?? of(s)}</span>
      {note && <span className="mx-sub">{note}</span>}
    </div>
  );
}

function SplitBar({ a, b, aLabel, bLabel }: { a: number; b: number; aLabel: string; bLabel: string }) {
  const total = a + b;
  if (!total) return null;
  return (
    <div className="mx-split">
      <div className="mx-split-bar" role="img" aria-label={`${aLabel} ${a}, ${bLabel} ${b}`}>
        {a > 0 && <span className="mx-split-a" style={{ flexGrow: a }} title={`${aLabel}: ${a}`} />}
        {b > 0 && <span className="mx-split-b" style={{ flexGrow: b }} title={`${bLabel}: ${b}`} />}
      </div>
      <div className="mx-split-labels">
        <span>
          {aLabel} {Math.round((100 * a) / total)}%
        </span>
        <span>
          {bLabel} {100 - Math.round((100 * a) / total)}%
        </span>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ charts (inline SVG, Design PRD 11 "Charts")

const W = 640;
const H = 240;
const PAD = { l: 48, r: 72, t: 16, b: 36 };
const xAt = (i: number, n: number) => PAD.l + (n <= 1 ? (W - PAD.l - PAD.r) / 2 : (i * (W - PAD.l - PAD.r)) / (n - 1));
const yAt = (v: number) => PAD.t + (1 - v) * (H - PAD.t - PAD.b);

function Axes({ labels }: { labels: string[] }) {
  return (
    <g>
      {[0, 0.25, 0.5, 0.75, 1].map((v) => (
        <g key={v}>
          <line className="mx-grid" x1={PAD.l} x2={W - PAD.r} y1={yAt(v)} y2={yAt(v)} />
          <text className="mx-axis" x={PAD.l - 8} y={yAt(v) + 4} textAnchor="end">
            {Math.round(v * 100)}%
          </text>
        </g>
      ))}
      {labels.map((l, i) => (
        <text key={i} className="mx-axis" x={xAt(i, labels.length)} y={H - PAD.b + 20} textAnchor="middle">
          {l}
        </text>
      ))}
    </g>
  );
}

type Pt = { x: number; v: number; n: number; title: string };

/** Label heights for the line ends, nudged apart so no two labels overlap (at least 14 units). */
function spreadLabels(ends: (number | null)[]): (number | undefined)[] {
  const order = ends.map((y, i) => ({ y, i })).filter((e) => e.y !== null) as { y: number; i: number }[];
  order.sort((a, b) => a.y - b.y);
  const out: (number | undefined)[] = ends.map(() => undefined);
  let prev = -Infinity;
  for (const e of order) {
    const y = Math.max(e.y, prev + 14);
    out[e.i] = y;
    prev = y;
  }
  return out;
}

function Series({ pts, cls, label, labelY }: { pts: Pt[]; cls: string; label: string; labelY?: number }) {
  if (pts.length === 0) return null;
  const last = pts[pts.length - 1];
  return (
    <g className={`mx-series mx-${cls}`}>
      {pts.length > 1 && <polyline points={pts.map((p) => `${p.x},${yAt(p.v)}`).join(" ")} />}
      {pts.map((p, i) => (
        <circle key={i} cx={p.x} cy={yAt(p.v)} r={4} className={p.n < SMALL ? "is-hollow" : ""}>
          <title>{p.title}</title>
        </circle>
      ))}
      <text className="mx-line-label" x={last.x + 10} y={(labelY ?? yAt(last.v)) + 4}>
        {label}
      </text>
    </g>
  );
}

type Quality = NonNullable<Metrics["quality"]>;
const bandLabel = (b: string) => (b === "0.0-0.7" ? "below 0.7" : b.replace("-", "–"));

function CalibrationChart({ cal, bands, diag }: { cal: Quality["calibration"]; bands: string[]; diag: Quality["calibrationDiagonal"] }) {
  // perfect calibration: each band's average stated confidence (so "below 0.7" sits where its answers are)
  const mids = bands.map((b) => {
    if (diag[b] !== null && diag[b] !== undefined) return diag[b] as number;
    const [lo, hi] = b.split("-").map(Number);
    return (lo + hi) / 2;
  });
  const labels = bands.map(bandLabel);
  const drawn = SERIES.filter((s) => cal[s.key]);
  const other = Object.entries(cal)
    .filter(([k]) => !SERIES.some((s) => s.key === k))
    .reduce((n, [, cells]) => n + Object.values(cells).reduce((m, c) => m + (c.den ?? 0), 0), 0);
  if (drawn.length === 0) return <p className="mx-empty">— No answers with a confidence to compare</p>;
  const pts: Pt[][] = drawn.map((s) =>
    bands.flatMap((b, i) => {
      const c = cal[s.key][b];
      if (!c || c.value === null) return [];
      return [{ x: xAt(i, bands.length), v: c.value, n: c.den ?? 0,
                title: `${s.label}, confidence ${labels[i]}: ${pct(c.value)} right (${c.num} of ${c.den})` }];
    }),
  );
  // a label sits at the end of each line, and "perfect" at the end of the diagonal
  const labelYs = spreadLabels([
    ...pts.map((p) => (p.length ? yAt(p[p.length - 1].v) : null)),
    yAt(mids[mids.length - 1]),
  ]);
  return (
    <>
      <svg className="mx-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Calibration: stated confidence against actual accuracy">
        <Axes labels={labels} />
        <polyline className="mx-diagonal" points={mids.map((m, i) => `${xAt(i, bands.length)},${yAt(m)}`).join(" ")} />
        {drawn.map((s, k) => (
          <Series key={s.key} cls={s.cls} label={s.label} pts={pts[k]} labelY={labelYs[k]} />
        ))}
        <text className="mx-axis" x={xAt(bands.length - 1, bands.length) + 10} y={(labelYs[drawn.length] ?? 0) + 4}>
          perfect
        </text>
      </svg>
      <span className="mx-sub">
        Stated confidence (across) against the share of answers that match the golden answer (up). Hollow points
        rest on fewer than {SMALL} answers; hover a point for its count.
        {other > 0 && ` ${other} answer${other === 1 ? "" : "s"} without an evidence type are not drawn.`}
      </span>
    </>
  );
}

function TrendChart({ rows }: { rows: Metrics["health"]["overTime"] }) {
  if (rows.length === 0) return <p className="mx-empty">— No tag run yet</p>;
  const labels = rows.map((r) => r.label);
  const line = (k: "reviewRate" | "autoAccepted", name: string): Pt[] =>
    rows.flatMap((r, i) =>
      r[k].value === null ? [] : [{ x: xAt(i, rows.length), v: r[k].value as number, n: SMALL,
                                    title: `${name}, ${r.label}: ${pct(r[k].value)} (${r[k].num} of ${r[k].den} tags)` }],
    );
  const auto = line("autoAccepted", "Auto-accepted"), rev = line("reviewRate", "Review rate");
  const ys = spreadLabels([auto.length ? yAt(auto[auto.length - 1].v) : null, rev.length ? yAt(rev[rev.length - 1].v) : null]);
  return (
    <>
      <svg className="mx-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Auto-accepted and review rate per tag run">
        <Axes labels={labels} />
        <Series pts={auto} cls="s1" label="Auto-accepted" labelY={ys[0]} />
        <Series pts={rev} cls="s2" label="Review rate" labelY={ys[1]} />
      </svg>
      <span className="mx-sub">
        Each batch run's tags, counted as the tiles count them (model-written answers, rules and not-applicable left out).
        {rows.some((r) => r.run === null) && " “Now” is every tagged product at its latest tagging, the same as the tiles."}
        {rows.length === 1 && " Baseline run."}
      </span>
    </>
  );
}

// ------------------------------------------------------------------ blocks

type Metric4 = "precision" | "recall" | "coverage" | "reviewRate";
type SortKey = "name" | "answers" | "confidenceMean" | "diff" | "drift" | `g:${Metric4}` | `l:${Metric4}`;
const GROUPS: [Metric4, string][] = [["precision", "Precision"], ["recall", "Recall"], ["coverage", "Coverage"], ["reviewRate", "Review rate"]];
const DRIFT_RANK = { alert: 0, watch: 1, stable: 2 } as const;

function sortValue(r: CardRow, k: SortKey): number | string | null {
  if (k === "name") return r.name;
  if (k === "answers") return r.answers;
  if (k === "confidenceMean") return r.confidenceMean;
  if (k === "diff") return r.precisionDiff;
  if (k === "drift") return r.drift ? DRIFT_RANK[r.drift] : null;
  const [side, m] = k.split(":") as ["g" | "l", Metric4];
  return (side === "g" ? r.golden : r.live)[m].value;
}

/** Default order: alerts, then watch, then the lowest live precision; facets without a live figure last. */
function defaultOrder(a: CardRow, b: CardRow) {
  const da = a.drift ? DRIFT_RANK[a.drift] : 3, db = b.drift ? DRIFT_RANK[b.drift] : 3;
  const ra = Math.min(da, 2), rb = Math.min(db, 2);
  if (ra !== rb && (ra < 2 || rb < 2)) return ra - rb;
  const pa = a.live.precision.value, pb = b.live.precision.value;
  if (pa !== pb) return (pa ?? 2) - (pb ?? 2);
  return a.name.localeCompare(b.name);
}

function SortHead({ k, label, sort, onSort, cls = "num" }: {
  k: SortKey; label: string; sort: { key: SortKey | null; dir: 1 | -1 }; onSort: (k: SortKey) => void; cls?: string;
}) {
  return (
    <button role="columnheader" className={`mx-sort ${cls}`} onClick={() => onSort(k)}
            aria-sort={sort.key === k ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
      {label}{sort.key === k ? (sort.dir === 1 ? " ↑" : " ↓") : ""}
    </button>
  );
}

function LiveCell({ s }: { s: Share }) {
  return (
    <span className="mx-sc-cell" title={s.value === null ? s.reason : of(s, "answers")}>
      <span>{pct(s.value)}</span>
      <span className="mx-sc-n">{s.den ? `of ${s.den}` : "\u00a0"}</span>
    </span>
  );
}

function Scorecard({ rows, liveMin }: { rows: CardRow[]; liveMin: number }) {
  const [sort, setSort] = useState<{ key: SortKey | null; dir: 1 | -1 }>({ key: null, dir: 1 });
  const [showSmall, setShowSmall] = useState(false);
  const nSmall = rows.filter((r) => r.smallSample).length;
  const onSort = (k: SortKey) => setSort((s) => ({ key: k, dir: s.key === k ? (-s.dir as 1 | -1) : 1 }));
  const sorted = useMemo(() => {
    const out = [...rows];
    if (sort.key === null) out.sort(defaultOrder);
    else {
      const k = sort.key;
      out.sort((a, b) => {
        const x = sortValue(a, k), y = sortValue(b, k);
        if (x === null) return 1;
        if (y === null) return -1;
        return (typeof x === "string" ? x.localeCompare(y as string) : x - (y as number)) * sort.dir;
      });
    }
    return out;
  }, [rows, sort]);
  if (rows.length === 0) return <p className="mx-empty">— No golden answers for this selection</p>;
  return (
    <div className="mx-table mx-scorecard" role="table">
      <div className="mx-tr mx-th mx-th-group" role="row">
        <span /><span /><span />
        {GROUPS.map(([m, l]) => (
          <span key={m} className={`mx-group mx-group-${m}`}>{l}</span>
        ))}
        <span />
      </div>
      <div className="mx-tr mx-th" role="row">
        <SortHead k="name" label="Facet" sort={sort} onSort={onSort} cls="" />
        <SortHead k="answers" label="Golden answers" sort={sort} onSort={onSort} />
        <SortHead k="confidenceMean" label="Avg conf." sort={sort} onSort={onSort} />
        {GROUPS.map(([m]) => (
          <span key={m} className="mx-pairhead">
            <SortHead k={`g:${m}`} label="Golden" sort={sort} onSort={onSort} />
            <SortHead k={`l:${m}`} label="Live" sort={sort} onSort={onSort} />
            {m === "precision" && <SortHead k="diff" label="Diff." sort={sort} onSort={onSort} />}
          </span>
        ))}
        <SortHead k="drift" label="Drift" sort={sort} onSort={onSort} cls="" />
      </div>
      {sorted.filter((r) => showSmall || !r.smallSample || nSmall === rows.length).map((r) => (
        <div key={r.facet} className={"mx-tr" + (r.smallSample ? " is-small" : "")} role="row">
          <span className="mx-facet">
            {r.name}
            {r.smallSample && <span className="mx-badge">Small sample</span>}
          </span>
          <span className="num">{r.answers}</span>
          <span className="num">{r.confidenceMean === null ? "—" : r.confidenceMean.toFixed(2)}</span>
          {GROUPS.map(([m]) => (
            <span key={m} className="mx-pair-cells">
              <span className="mx-sc-cell" title={r.golden[m].value === null ? r.golden[m].reason : of(r.golden[m])}>
                <span>{pct(r.golden[m].value)}</span>
                <span className="mx-sc-n">{"\u00a0"}</span>
              </span>
              <LiveCell s={r.live[m]} />
              {m === "precision" && (
                <span className="mx-sc-cell">
                  <span>{r.precisionDiff === null ? "—" : `${r.precisionDiff >= 0 ? "+" : "−"}${Math.abs(Math.round(r.precisionDiff))}`}</span>
                  <span className="mx-sc-n">{r.precisionDiff === null ? "\u00a0" : "pts"}</span>
                </span>
              )}
            </span>
          ))}
          <span>
            {r.drift ? <span className={`mx-status is-${r.drift}`}>{STATUS_TEXT[r.drift]}</span>
              : <span className="mx-sc-n" title={`${r.driftNote ?? ""}: drift needs ${liveMin} live answers`}>Too few</span>}
          </span>
        </div>
      ))}
      {nSmall > 0 && nSmall < rows.length && (
        <button className="btn btn-quiet btn-sm mx-more" onClick={() => setShowSmall((v) => !v)}>
          {showSmall ? "Hide small samples" : `Show ${nSmall} small-sample facets (fewer than ${SMALL} golden answers)`}
        </button>
      )}
      <span className="mx-sub mx-sc-foot">
        Golden: the tuned set (the holdout is scored overall only). Live: as in the table above. Live counts are
        answers. Drift compares live with golden precision, using the monitoring limits, once a facet has at least
        {" "}{liveMin} live answers.
      </span>
    </div>
  );
}

const STATUS_TEXT = { stable: "Stable", watch: "Watch", alert: "Alert" } as const;

function BenchCell({ s }: { s: Bench }) {
  return (
    <span className="mx-bench-cell" title={s.value === null ? s.reason : of(s)}>
      <span className="mx-bench-value">{pct(s.value)}</span>
      <span className="mx-bench-n">{s.products} product{s.products === 1 ? "" : "s"}</span>
    </span>
  );
}

function Benchmark({ b }: { b: Quality["benchmark"] }) {
  return (
    <div className="mx-part">
      <span className="mx-part-title">Benchmark versus live</span>
      <div className="mx-table mx-bench" role="table">
        <div className="mx-tr mx-th" role="row">
          <span />
          <span className="num">Golden · tuned set</span>
          <span className="num">Golden · holdout</span>
          <span className="num">Live</span>
          <span className="num">Difference</span>
        </div>
        {b.rows.map((r) => (
          <div key={r.key} className="mx-tr" role="row">
            <span>{r.label}</span>
            <BenchCell s={r.tuned} />
            <BenchCell s={r.holdout} />
            <BenchCell s={r.live} />
            <span className="mx-bench-cell">
              <span className="mx-bench-value" title={`Live minus the ${r.against === "holdout" ? "holdout" : "tuned set"}`}>
                {r.diffPoints === null ? "—" : `${r.diffPoints >= 0 ? "+" : "−"}${Math.abs(Math.round(r.diffPoints))} pts`}
              </span>
              <span className="mx-bench-n">
                {r.status ? <span className={`mx-status is-${r.status}`}>{STATUS_TEXT[r.status]}</span>
                  : r.statusNote ?? `vs ${r.against === "holdout" ? "holdout" : "tuned set"}`}
              </span>
            </span>
          </div>
        ))}
      </div>
      <span className="mx-sub">
        Live precision and recall count unchecked confident answers as correct. Live precision, recall and coverage
        score the tagger's original answers against the reviewer's approved answers, on products that are not in the
        golden set and were approved from their own page (not from the list or in bulk). Live auto-accepted and review
        rate cover every tagged product outside the golden set, at its latest tagging. The difference is against the
        holdout, or the tuned set when no holdout product is scored; it gets a status from the monitoring limits once
        the live figure rests on at least {b.liveMin} products.
      </span>
    </div>
  );
}

function Quality({ q, run, facetOn }: { q: Quality; run: Run; facetOn: boolean }) {
  return (
    <section id="mx-b1" className="mx-card">
      <div className="mx-head">
        <h2 className="mx-h2">Is it right?</h2>
        <span className="mx-filter-note">Golden set scored on tag run {run.id} · {fmtDate(run.date)}</span>
      </div>
      <Benchmark b={q.benchmark} />
      <div className="mx-row mx-row-2">
        <Stat label="Correctly left unknown" s={q.correctlyLeftUnknown}
              sub={`${of(q.correctlyLeftUnknown, "golden unknowns")} · tuned set`}
              note="When there is no right answer, how often the tagger says unknown" />
        <Stat label="Invented quote rate" s={q.inventedQuote}
              sub={`${of(q.inventedQuote, "answers that cite a quote")} · all products in the run`} note="Lower is better" />
      </div>
      <div className="mx-part">
        <div className="mx-part-head">
          <span className="mx-part-title">Facet scorecard</span>
          <span className="mx-sub">Golden benchmark and live, side by side. Click a heading to sort.</span>
        </div>
        <Scorecard rows={q.scorecard} liveMin={q.facetLiveMin} />
      </div>
      <div className="mx-part">
        <div className="mx-part-head">
          <span className="mx-part-title">Calibration</span>
          <span className="mx-sub">Tuned set{facetOn ? ", this facet only" : ", all facets"}</span>
        </div>
        <CalibrationChart cal={q.calibration} bands={q.calibrationBands} diag={q.calibrationDiagonal} />
      </div>
      <span className="mx-def">
        Tuned set: the golden products the rules were adjusted on ({q.goldenSizes.tuned}). Holdout: golden products
        labelled without seeing the tagger's answers ({q.goldenSizes.holdout}). Scored in this run: {q.scored.tuned} tuned,
        {" "}{q.scored.holdout} holdout; only products whose category matched the golden category are scored for tags
        {q.scored.excludedWrongCategory ? ` (${q.scored.excludedWrongCategory} tuned left out for a wrong category)` : ""}.
      </span>
    </section>
  );
}

function Review({ r }: { r: Metrics["review"] }) {
  const p = r.progress;
  const total = p.ready + p.needsReview + p.approved;
  const prog = [
    { k: "approved", label: "Approved", n: p.approved },
    { k: "ready", label: "Ready", n: p.ready },
    { k: "review", label: "Needs review", n: p.needsReview },
  ];
  const t = r.reviewTime;
  const time = t.medianSeconds === null ? "—" : `${Math.floor(t.medianSeconds / 60)} min ${Math.round(t.medianSeconds % 60)} s`;
  return (
    <section id="mx-b2" className="mx-card">
      <h2 className="mx-h2">How much human work does it need?</h2>
      <div className="mx-row mx-row-2">
        <Stat label="Review rate" s={r.reviewRate} sub={of(r.reviewRate, "tags in review")} />
        <Stat label="Products needing review" s={r.productsNeedingReview} sub={of(r.productsNeedingReview, "products tagged")} />
      </div>
      <div className="mx-part">
        <span className="mx-part-title">Product progress</span>
        {total === 0 ? (
          <p className="mx-empty">— No product has this run as its latest tagging</p>
        ) : (
          <>
            <div className="mx-progress" role="img" aria-label={prog.map((g) => `${g.label} ${g.n}`).join(", ")}>
              {prog.map((g) => g.n > 0 && <span key={g.k} className={`mx-prog-${g.k}`} style={{ flexGrow: g.n }} title={`${g.label}: ${g.n}`} />)}
            </div>
            <div className="mx-legend">
              {prog.map((g) => (
                <span key={g.k}>
                  <span className={`mx-key mx-prog-${g.k}`} />
                  {g.label} <strong>{g.n}</strong> <span className="muted">{Math.round((100 * g.n) / total)}%</span>
                </span>
              ))}
            </div>
          </>
        )}
      </div>
      {r.reviewedTags === 0 ? (
        <p className="mx-empty">No reviews yet. These fill in as products are approved.</p>
      ) : (
        <>
          <div className="mx-part">
            <div className="mx-part-head">
              <span className="mx-part-title">Reviewed tags</span>
              <span className="mx-sub">{r.reviewedTags} reviewed</span>
            </div>
            <SplitBar a={r.approvedUnchanged.num ?? 0} b={r.changedOnReview.num ?? 0} aLabel="Approved without change" bLabel="Changed on review" />
            <span className="mx-sub">
              {(["app", "sheet"] as const).filter((k) => r.bySource[k].reviewed > 0).map((k) =>
                `${k === "app" ? "In the app" : "Source “sheet” (review sheets)"}: ${r.bySource[k].reviewed} reviewed · ${r.bySource[k].unchanged} unchanged · ${r.bySource[k].changed} changed`).join("  ·  ")}
              {r.bySource.sheet.reviewed > 0 && ". Sheet reviews are not in live precision, recall or coverage: the sheet does not show the accepted answers."}
              {r.bySource.auto.products > 0 && ` ${r.bySource.auto.products} products were approved automatically, without review; they are not counted here.`}
            </span>
          </div>
          <div className="mx-row mx-row-3">
            <Stat label="Accepted tags corrected" s={r.acceptedCorrected} note="Errors that got past the threshold" />
            <Stat label="Category overrides" s={r.categoryOverrides} note="Whether the category gate is in the right place" />
            <div className="mx-statbox">
              <span className="mx-label">Review time per product</span>
              <span className="mx-stat">{time}</span>
              <span className="mx-sub">{t.medianSeconds === null ? t.reason : `Median of ${t.products} product${t.products === 1 ? "" : "s"} approved from their page`}</span>
            </div>
          </div>
          <div className="mx-part">
            <span className="mx-part-title">Review outcomes per facet</span>
            {r.perFacet.length === 0 ? (
              <p className="mx-empty">— No reviewed tags for this facet</p>
            ) : (
              <div className="mx-table mx-outcomes" role="table">
                <div className="mx-tr mx-th" role="row">
                  <span>Facet</span>
                  <span>Approved without change · changed</span>
                  <span className="num">Reviewed</span>
                </div>
                {r.perFacet.map((f) => (
                  <div key={f.facet} className="mx-tr" role="row">
                    <button className="mx-link" title="Open on the Confidence tab" onClick={() => navigate(`/confidence?facet=${encodeURIComponent(f.facet)}`)}>
                      {f.name}
                    </button>
                    <SplitBar a={f.unchanged} b={f.changed} aLabel="Unchanged" bLabel="Changed" />
                    <span className="num">{f.reviewed}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </section>
  );
}

function Cost({ c, notes }: { c: Metrics["cost"]; notes: string[] }) {
  const all = c.scope === "all";
  return (
    <section id="mx-b3" className="mx-card">
      <div className="mx-head">
        <h2 className="mx-h2">What does tagging cost?</h2>
        {notes.map((n) => <span key={n} className="mx-filter-note">{n}</span>)}
      </div>
      <div className="mx-row mx-row-3">
        <div className="mx-statbox">
          <span className="mx-label">Tagging cost per product</span>
          <span className="mx-stat">{cents(c.costPerProduct)}</span>
          <span className="mx-sub">Tag step only; categorising is not included</span>
        </div>
        <div className="mx-statbox">
          <span className="mx-label">{all ? "Tagging cost, all runs" : "Tagging cost per run"}</span>
          <span className="mx-stat">{dollars(c.totalCost)}</span>
          <span className="mx-sub">
            {c.products} products tagged{all ? ` in ${c.runs} runs (re-tags and live tags included)` : ""} · {c.models.map(model).join(", ") || "—"}
          </span>
        </div>
        <div className="mx-statbox">
          <span className="mx-label">Tagging time per product</span>
          <span className="mx-stat">{c.secondsPerProduct === null ? "—" : `${sig2(c.secondsPerProduct)} s`}</span>
          <span className="mx-sub">Run duration ÷ products tagged</span>
        </div>
      </div>
      <div className="mx-part">
        <span className="mx-part-title">Tagging cost projection for the full catalog</span>
        <div className="mx-row mx-row-2">
          <div className="mx-statbox">
            <span className="mx-side-label">Standard pricing</span>
            <span className="mx-stat">{dollars(c.projection.standard)}</span>
          </div>
          <div className="mx-statbox">
            <span className="mx-side-label">Batch pricing</span>
            <span className="mx-stat">{dollars(c.projection.batch)}</span>
          </div>
        </div>
        <span className="mx-sub">For {c.catalogSize.toLocaleString("en-US")} products · {c.models.map(model).join(", ") || "—"}{c.projection.batchFromStoredPrices ? " · batch at the model's stored batch prices" : " · batch at half the standard rate"}</span>
      </div>
      {c.comparison.length > 1 && (
        <div className="mx-part">
          <span className="mx-part-title">Model comparison</span>
          <div className="mx-table mx-compare" role="table">
            <div className="mx-tr mx-th" role="row">
              <span>Model</span>
              <span className="num">Tagging cost per product</span>
              <span className="num">Precision</span>
            </div>
            {c.comparison.map((m) => (
              <div key={m.run} className="mx-tr" role="row">
                <span>{model(m.model)} <span className="muted">run {m.run}</span></span>
                <span className="num">{cents(m.costPerProduct)}</span>
                <span className="num" title={m.precision.reason ?? of(m.precision)}>{pct(m.precision.value)}</span>
              </div>
            ))}
          </div>
          <span className="mx-sub">On the same products as the golden run.</span>
        </div>
      )}
    </section>
  );
}

const CHECK_TEXT: Record<string, string> = {
  unknown_rate: "unknown rate", review_rate: "review rate", coverage: "coverage",
  mean_confidence: "average confidence", stability_index: "confidence shift",
};

function alertChange(a: Alert) {
  if (a.check === "stability_index") return a.current === null ? "—" : `index ${a.current.toFixed(2)}`;
  if (a.check === "mean_confidence")
    return `${a.baseline?.toFixed(2) ?? "—"} → ${a.current?.toFixed(2) ?? "—"}`;
  return `${pct(a.baseline)} → ${pct(a.current)} (${points(a.change)})`;
}

function Health({ h, notes }: { h: Metrics["health"]; notes: string[] }) {
  const [all, setAll] = useState(false);
  const rows = all ? h.facets : h.facets.slice(0, 10);
  return (
    <section id="mx-b4" className="mx-card">
      <div className="mx-head">
        <h2 className="mx-h2">Is it still healthy?</h2>
        {notes.map((n) => <span key={n} className="mx-filter-note">{n}</span>)}
      </div>
      <div className="mx-part">
        <div className="mx-part-head">
          <span className="mx-part-title">Facets that moved most</span>
          {h.baselineRunId && <span className="mx-sub">Monitor check of tag run {h.run} against baseline run {h.baselineRunId}, per product and facet</span>}
        </div>
        {!h.monitored ? (
          <p className="mx-empty">— The monitor has not checked this run (live-tag runs of one product are not monitored)</p>
        ) : h.baselineRunId === null ? (
          <p className="mx-empty">— This is the baseline run, so there is nothing to compare yet.</p>
        ) : (
          <div className="mx-table mx-health" role="table">
            <div className="mx-tr mx-th" role="row">
              <span>Facet</span>
              <span className="num">Unknown at baseline</span>
              <span className="num">Unknown now</span>
              <span className="num">Change</span>
              <span className="num">Confidence shift</span>
              <span>Status</span>
            </div>
            {rows.map((f) => (
              <div key={f.facet} className="mx-tr" role="row">
                <span>{f.name}</span>
                <span className="num">{pct(f.unknownBaseline)}</span>
                <span className="num">{pct(f.unknownNow)}</span>
                <span className="num">{points(f.unknownChange)}</span>
                <span className="num">{f.confidenceShift === null ? "—" : f.confidenceShift.toFixed(2)}</span>
                <span>
                  <span className={`mx-status is-${f.status}`}>{f.status === "stable" ? "Stable" : f.status === "watch" ? "Watch" : "Alert"}</span>
                </span>
              </div>
            ))}
            {h.facets.length > 10 && (
              <button className="btn btn-quiet btn-sm mx-more" onClick={() => setAll((v) => !v)}>
                {all ? "Show the top 10" : `Show all ${h.facets.length} facets`}
              </button>
            )}
          </div>
        )}
      </div>
      <div className="mx-part">
        <span className="mx-part-title">Auto-accepted and review rate over time</span>
        <TrendChart rows={h.overTime} />
      </div>
      <div className="mx-part">
        <span className="mx-part-title">Open alerts</span>
        {h.alerts.length === 0 ? (
          <p className="mx-empty">No open alerts</p>
        ) : (
          <ul className="mx-alerts">
            {h.alerts.map((a) => (
              <li key={a.id}>
                <span className={`mx-status is-${a.severity === "alert" ? "alert" : "watch"}`}>{a.severity === "alert" ? "Alert" : "Watch"}</span>
                <span>
                  <strong>{a.facet === "category" ? "Category" : a.facet}</strong>
                  {a.segment !== "all" && <span className="muted"> · {a.segment}</span>}
                  {" — "}{CHECK_TEXT[a.check] ?? a.check} {alertChange(a)}
                </span>
                <span className="muted">run {a.run} · {fmtDate(a.at)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

// ------------------------------------------------------------------ the page

export function MetricsPage() {
  const { params } = useRoute();
  const run = params.get("run") ?? "";
  const category = params.get("category") ?? "";
  const facet = params.get("facet") ?? "";
  const [data, setData] = useState<Metrics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [facets, setFacets] = useState<Facet[]>([]);
  const [tree, setTree] = useState<CatNode[]>([]);
  const [attempt, setAttempt] = useState(0);
  const [stale, setStale] = useState(false);

  useEffect(() => {
    loadFacets().then(setFacets).catch(() => undefined);
    loadTree().then(setTree).catch(() => undefined);
  }, []);

  useEffect(() => {
    let live = true;
    setLoading(true);
    setError(null);
    const q = new URLSearchParams();
    if (run) q.set("run", run);
    if (category) q.set("category", category);
    if (facet) q.set("facet", facet);
    let timer: number | undefined;
    let tries = 0;
    // a stale page is shown at once; ask again until the fresh one is ready
    const load = () =>
      api
        .get<Metrics>(`/api/metrics?${q}`)
        .then((d) => {
          if (!live) return;
          setData(d);
          setStale(d.stale);
          if (d.stale && tries++ < 30) timer = window.setTimeout(load, 2000);
        })
        .catch((e) => live && setError(errorInfo(e).message))
        .finally(() => live && setLoading(false));
    load();
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [run, category, facet, attempt]);

  const notes34 = [category && "category", facet && "facet"].filter(Boolean) as string[];
  const top = data?.top;
  const r = data?.run ?? null;
  const qr = data?.qualityRun ?? null;
  const tiles = top
    ? [
        { label: "Tag precision", badge: top.precision.isTuned ? "Tuned set" : null, s: top.precision.main,
          sub: top.precision.isTuned
            ? `${of(top.precision.tuned, "accepted tags scored")}; no holdout product scored`
            : `Holdout · tuned set ${pct(top.precision.tuned.value)} · ${top.precision.tuned.den ?? 0} answers scored` },
        { label: "Auto-accepted", s: top.autoAccepted, sub: of(top.autoAccepted, "tags") },
        { label: "Review rate", s: top.reviewRate,
          sub: `${top.reviewRate.products} product${top.reviewRate.products === 1 ? "" : "s"} with a tag in review` },
        { label: "Category accuracy", s: top.categoryAccuracy,
          sub: `${of(top.categoryAccuracy, "golden products")} · ${top.categoryAccuracy.flagged} flagged · ${top.categoryAccuracy.held} held` },
      ]
    : [];

  return (
    <>
      <TopBar left={<h1 className="title">Metrics</h1>} right={(loading && data) || stale ? <span className="muted small">Updating…</span> : undefined} />
      <div className="shell-scroll">
        <div className="mx">
          {error && (
            <div className="error-banner" role="alert">
              <span className="error-banner-text">{error}</span>
              <button className="btn btn-secondary btn-sm" onClick={() => setAttempt((a) => a + 1)}>Try again</button>
            </div>
          )}
          {!data && loading && (
            <div aria-busy="true" className="mx-loading">
              <div className="mx-tiles">{[0, 1, 2, 3, 4].map((i) => <div key={i} className="skeleton mx-sk-tile" />)}</div>
              <div className="skeleton mx-sk-card" />
              <span className="muted small">Working out the figures; the first load takes a few seconds.</span>
            </div>
          )}
          {data && top && (
            <>
              <div className="mx-tiles">
                {tiles.map((t) => (
                  <div key={t.label} className="mx-tile">
                    <div className="mx-tile-head">
                      <span className="mx-label">{t.label}</span>
                      {t.badge && <span className="mx-badge">{t.badge}</span>}
                    </div>
                    <Value s={t.s} big />
                    <span className="mx-sub">{t.s.value === null ? t.s.reason : t.sub}</span>
                  </div>
                ))}
                <div className="mx-tile">
                  <span className="mx-label">Tagging cost per product</span>
                  <span className="mx-big">{cents(top.costPerProduct.value)}</span>
                  <span className="mx-sub">
                    {dollars(top.costPerProduct.projection)} for {top.costPerProduct.catalogSize.toLocaleString("en-US")} products at standard pricing
                  </span>
                </div>
              </div>
              <span className="mx-runline">
                {r
                  ? `Tag run ${r.id} · ${model(r.categoriseModel)} categorises, ${model(r.tagModel)} tags · definitions ${r.definitionsVersion === null ? "version not recorded" : `version ${r.definitionsVersion}`} · ${fmtDate(r.date)}`
                  : `All ${data.scope.products} tagged products, each at its latest tagging · tagged by ${(data.scope.tagModels ?? "").split(", ").map(model).join(", ")}`}
                {qr && ` · quality from golden run ${qr.id} (${fmtDate(qr.date)})`}
                {data.quality && ` · golden set ${data.quality.goldenSizes.tuned} tuned + ${data.quality.goldenSizes.holdout} holdout`}
              </span>
              <div className="mx-filters">
                <label className="mx-filter">
                  Run
                  <select className="list-select" value={r ? String(r.id) : ""} onChange={(e) => setParams({ run: e.target.value }, { replace: true })}>
                    <option value="">All tagged products (latest tagging)</option>
                    {data.runs.map((o) => (
                      <option key={o.id} value={o.id}>
                        Run {o.id} · {fmtDate(o.date)} · {o.products} product{o.products === 1 ? "" : "s"}
                        {o.products === 1 ? " (live tag)" : ""}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="mx-filter">
                  Category
                  <select className="list-select" value={category} onChange={(e) => setParams({ category: e.target.value }, { replace: true })}>
                    <option value="">All categories</option>
                    {tree
                      .filter((n) => !(n.depth === 0 && n.path === "All"))
                      .map((n) => (
                        <option key={n.id} value={n.id}>
                          {"   ".repeat(Math.max(0, n.depth - 1)) + n.name}
                        </option>
                      ))}
                  </select>
                </label>
                <label className="mx-filter">
                  Facet
                  <select className="list-select" value={facet} onChange={(e) => setParams({ facet: e.target.value }, { replace: true })}>
                    <option value="">All facets</option>
                    {facets.map((f) => (
                      <option key={f.id} value={f.id}>{f.name}</option>
                    ))}
                  </select>
                </label>
                <nav className="mx-jumps" aria-label="Jump to a block">
                  {BLOCKS.map(([id, l]) => (
                    <button key={id} className="mx-jump" onClick={() => document.getElementById(id)?.scrollIntoView({ behavior: "smooth" })}>
                      {l}
                    </button>
                  ))}
                </nav>
              </div>
              <div className={"mx-blocks" + (loading ? " is-loading" : "")}>
                {data.quality && qr ? (
                  <Quality q={data.quality} run={qr} facetOn={!!facet} />
                ) : (
                  <section id="mx-b1" className="mx-card">
                    <h2 className="mx-h2">Is it right?</h2>
                    <p className="mx-empty">— No tag run has covered the golden set yet</p>
                  </section>
                )}
                <Review r={data.review} />
                <Cost c={data.cost} notes={notes34.length ? [`Not affected by the ${notes34.join(" or ")} filter`] : []} />
                <Health h={data.health} notes={notes34.length ? [`Not affected by the ${notes34.join(" or ")} filter`] : []} />
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}

