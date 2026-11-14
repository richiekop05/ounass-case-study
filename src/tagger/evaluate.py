"""Scoring a run against the golden set (PRD 6.2). The scoring itself is pure; the database parts
load inputs and save results to evaluation_results."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from psycopg.types.json import Jsonb

from .runs import Db, run_summary

BANDS = [(0.0, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0001)]     # the lowest band is 'below 0.7': nothing dropped
BAND_LABELS = {"0.0-0.7": "below 0.7"}
LABELS = {"unknown_agreement": "correctly left unknown"}


@dataclass
class GoldAnswer:
    state: str                        # value | unknown | not_applicable
    values: set = field(default_factory=set)


@dataclass
class PredRow:
    status: str                       # auto | review | abstained | not_applicable
    key: object = None                # facet_value_id, rounded number or normalised text; None = no value
    confidence: float | None = None
    evidence: str | None = None


@dataclass
class Metric:
    scope: str
    metric: str
    value: float | None
    product_count: int
    detail: dict | None = None


def _share(a, b):
    return a / b if b else None


def _nd(num, den) -> dict:
    """The counts behind a share, kept in the metric's detail."""
    return {"num": num, "den": den}


def score_categories(golden_cat: dict[str, str], results: dict[str, dict]) -> list[Metric]:
    pids = [p for p in golden_cat if golden_cat[p] and p in results]
    n = len(pids)
    exact = sum(results[p]["category_id"] == golden_cat[p] for p in pids)
    either = sum(golden_cat[p] in (results[p]["category_id"], results[p].get("category_runner_up_id")) for p in pids)
    review = [p for p in pids if results[p].get("category_status") == "review"]
    flagged = [p for p in pids if results[p].get("category_status") == "flagged"]
    auto = [p for p in pids if p not in review and p not in flagged]
    auto_ok = sum(results[p]["category_id"] == golden_cat[p] for p in auto)
    flagged_ok = sum(results[p]["category_id"] == golden_cat[p] for p in flagged)
    wrong = Counter((golden_cat[p], results[p]["category_id"]) for p in pids
                    if results[p]["category_id"] != golden_cat[p])
    return [Metric("category", "accuracy_exact", _share(exact, n), n, _nd(exact, n)),
            Metric("category", "accuracy_match_or_runner_up", _share(either, n), n, _nd(either, n)),
            Metric("category", "review_rate", _share(len(review), n), n, _nd(len(review), n)),
            Metric("category", "tagged_category_flagged_rate", _share(len(flagged), n), n, _nd(len(flagged), n)),
            Metric("category", "accepted_rate", _share(len(auto), n), n, _nd(len(auto), n)),
            Metric("category", "accuracy_accepted", _share(auto_ok, len(auto)), len(auto), _nd(auto_ok, len(auto))),
            Metric("category", "accuracy_flagged", _share(flagged_ok, len(flagged)), len(flagged),
                   _nd(flagged_ok, len(flagged))),
            Metric("category", "confusions", None, n,
                   {"pairs": [[g, pr, c] for (g, pr), c in wrong.most_common(5)]})]


def pred_answer(rows: list[PredRow]) -> tuple[str, set]:
    """What the pipeline answered: its auto values, else its review values, else a state."""
    auto = {r.key for r in rows if r.status == "auto" and r.key is not None}
    if auto:
        return "value", auto
    review = {r.key for r in rows if r.status == "review" and r.key is not None}
    if review:
        return "value", review
    if any(r.status == "not_applicable" for r in rows):
        return "not_applicable", set()
    return "unknown", set()


SCORECARD_BANDS = (("below 0.7", 0.0, 0.7), ("0.7-0.8", 0.7, 0.8), ("0.8-0.9", 0.8, 0.9), ("0.9-1.0", 0.9, 1.01))
SMALL_SAMPLE = 20


def confidence_bands(values: list[float]) -> dict[str, float | None]:
    """Share of answers in each confidence band (answers that carry a confidence)."""
    return {name: (sum(lo <= v < hi for v in values) / len(values) if values else None)
            for name, lo, hi in SCORECARD_BANDS}


def facet_scorecard(ev: dict) -> list[dict]:
    """One row per facet with at least one golden answer (PRD follow-up, 13 Nov 2026)."""
    m = {(x.scope, x.metric): x for x in ev["metrics"]}
    facets = sorted({x.scope for x in ev["metrics"] if x.metric == "precision" and x.scope != "overall"
                     and ":" not in x.scope})
    rows = []
    for fid in facets:
        n = m[(fid, "precision")].product_count
        conf = m.get((fid, "confidence_mean"))
        d = (conf.detail or {}) if conf else {}
        bands = d.get("bands") or {}
        rows.append({"facet": fid, "answers": n, "answers_with_confidence": conf.product_count if conf else 0,
                     "confidence_mean": conf.value if conf else None, "confidence_min": d.get("min"),
                     "confidence_max": d.get("max"),
                     **{f"share {name}": bands.get(name) for name, _, _ in SCORECARD_BANDS},
                     **{k: m[(fid, k)].value for k in ("precision", "recall", "coverage", "review_rate")},
                     "sample": "small sample" if n < SMALL_SAMPLE else ""})
    rows.sort(key=lambda r: (-r["answers"], r["facet"]))
    return rows


def write_scorecard(rows: list[dict], path) -> None:
    import openpyxl
    from openpyxl.styles import Font
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Facet scorecard"
    heads = list(rows[0]) if rows else ["facet"]
    ws.append(heads)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in rows:
        ws.append([r[h] for h in heads])
    for col in ws.iter_cols(min_row=2):
        name = col[0].column_letter
        h = ws[f"{name}1"].value
        fmt = "0.0%" if h.startswith("share") or h in ("precision", "recall", "coverage", "review_rate") else \
              "0.00" if h.startswith("confidence") else None
        if fmt:
            for cell in col:
                cell.number_format = fmt
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = ws.dimensions
    ws.column_dimensions["A"].width = 24
    wb.save(path)


def score_tags(golden: dict[str, dict[str, GoldAnswer]], pred: dict[str, dict[str, list[PredRow]]],
               label=lambda fid, key: str(key)) -> list[Metric]:
    per = defaultdict(lambda: Counter())
    confusions = defaultdict(Counter)
    calib = defaultdict(lambda: defaultdict(lambda: [0, 0, 0.0]))     # answers, right, sum of confidence
    confs = defaultdict(list)          # one confidence per tagger answer (the highest of its value rows)
    for pid, facets in golden.items():
        for fid, g in facets.items():
            rows = pred.get(pid, {}).get(fid, [])
            c = per[fid]
            c["n"] += 1
            answer_confs = [r.confidence for r in rows if r.confidence is not None and r.key is not None
                            and r.status in ("auto", "review")]
            if answer_confs:
                confs[fid].append(max(answer_confs))
            G = g.values if g.state == "value" else set()
            auto = {r.key for r in rows if r.status == "auto" and r.key is not None}
            c["tp"] += len(auto & G)
            c["pred_pos"] += len(auto)
            c["gold_pos"] += len(G)
            # coverage (decided 14 Nov 2026): golden values whose facet got an accepted value, over golden values.
            # Each value is one answer, as for recall, so recall can never exceed coverage.
            c["covered"] += len(G) if auto else 0
            c["review"] += any(r.status == "review" for r in rows)
            state, answer = pred_answer(rows)
            if g.state == "value":
                correct = state == "value" and answer == G
            else:
                c["gold_unknown"] += 1
                correct = state == g.state
                c["unknown_agree"] += correct
            c["correct"] += correct
            if not correct:
                gtxt = "; ".join(sorted(label(fid, k) for k in G)) if G else g.state
                ptxt = "; ".join(sorted(label(fid, k) for k in answer)) if answer else state
                confusions[fid][(gtxt, ptxt)] += 1
            for r in rows:
                if r.key is None or r.confidence is None or r.status not in ("auto", "review"):
                    continue
                for lo, hi in BANDS:
                    if lo <= r.confidence < hi:
                        cell = calib[r.evidence or "none"][f"{lo:.1f}-{min(hi, 1.0):.1f}"]
                        cell[0] += 1
                        cell[1] += r.key in G
                        cell[2] += r.confidence
    out = []
    tot = Counter()
    for fid in sorted(per):
        c = per[fid]
        tot.update(c)
        out += [Metric(fid, "accuracy", _share(c["correct"], c["n"]), c["n"], _nd(c["correct"], c["n"])),
                Metric(fid, "precision", _share(c["tp"], c["pred_pos"]), c["n"], _nd(c["tp"], c["pred_pos"])),
                Metric(fid, "recall", _share(c["tp"], c["gold_pos"]), c["n"], _nd(c["tp"], c["gold_pos"])),
                Metric(fid, "coverage", _share(c["covered"], c["gold_pos"]), c["n"], _nd(c["covered"], c["gold_pos"])),
                Metric(fid, "review_rate", _share(c["review"], c["n"]), c["n"], _nd(c["review"], c["n"])),
                Metric(fid, "unknown_agreement", _share(c["unknown_agree"], c["gold_unknown"]), c["gold_unknown"],
                       _nd(c["unknown_agree"], c["gold_unknown"])),
                Metric(fid, "confidence_mean", sum(confs[fid]) / len(confs[fid]) if confs[fid] else None,
                       len(confs[fid]), {"min": min(confs[fid], default=None), "max": max(confs[fid], default=None),
                                         "bands": confidence_bands(confs[fid])}),
                Metric(fid, "confusions", None, c["n"],
                       {"pairs": [[g, p, k] for (g, p), k in confusions[fid].most_common(5)]})]
    n = tot["n"]
    out += [Metric("overall", "accuracy", _share(tot["correct"], n), n, _nd(tot["correct"], n)),
            Metric("overall", "precision", _share(tot["tp"], tot["pred_pos"]), n, _nd(tot["tp"], tot["pred_pos"])),
            Metric("overall", "recall", _share(tot["tp"], tot["gold_pos"]), n, _nd(tot["tp"], tot["gold_pos"])),
            Metric("overall", "coverage", _share(tot["covered"], tot["gold_pos"]), n,
                   _nd(tot["covered"], tot["gold_pos"])),
            Metric("overall", "review_rate", _share(tot["review"], n), n, _nd(tot["review"], n)),
            Metric("overall", "unknown_agreement", _share(tot["unknown_agree"], tot["gold_unknown"]),
                   tot["gold_unknown"], _nd(tot["unknown_agree"], tot["gold_unknown"]))]
    for ev, bands in sorted(calib.items()):
        for band, (count, right, conf) in sorted(bands.items()):
            out.append(Metric(f"evidence:{ev}", f"calibration_{band}", _share(right, count), count,
                              {**_nd(right, count), "mean_confidence": conf / count if count else None}))
    return out


# ------------------------------------------------------------------ database

def _key(fvid, num, txt):
    if fvid is not None:
        return int(fvid)
    if num is not None:
        return round(float(num), 2)
    if txt is not None:
        return " ".join(str(txt).lower().split())
    return None


def evaluate(db: Db, run_id: int, save: bool = True, products=None, facet: str | None = None) -> dict:
    """Score a run. `products` (a set of product IDs) and `facet` narrow the scoring for the review app's
    filters; a narrowed result is never saved."""
    if (products is not None or facet) and save:
        raise ValueError("a filtered evaluation cannot be saved")
    keep = (lambda p: True) if products is None else (lambda p: p in products)
    conn = db.conn
    with db.lock:
        stage = conn.execute("select stage from tagging_runs where tagging_run_id = %s", (run_id,)).fetchone()
        if not stage:
            raise LookupError(f"run {run_id} not found")
        stage = stage[0]
        rows = conn.execute("select product_id, category_id, is_holdout from golden_products").fetchall()
        golden_cat = {p: c for p, c, h in rows if not h and keep(p)}       # the reviewed set
        holdout_cat = {p: c for p, c, h in rows if h and keep(p)}          # scored apart, only once it has a key
        if not any(golden_cat.values()) and products is None:
            raise ValueError("no golden product has a category yet: label and import the sheet first")
        if stage == "tag":
            tag_products = {p: (c, cr) for p, c, cr in conn.execute(
                """select product_id, category_id, category_run_id from tagging_run_products
                   where tagging_run_id = %s and status = 'done'""", (run_id,)) if keep(p)}
            cat_runs = sorted({cr for _, cr in tag_products.values() if cr})
        else:
            tag_products, cat_runs = {}, [run_id]
        results = {}
        for p, c, ru, st in conn.execute(
                """select distinct on (product_id) product_id, category_id, category_runner_up_id, category_status
                   from tagging_run_products where tagging_run_id = any(%s) and status = 'done'
                   order by product_id, tagging_run_id desc""", (cat_runs,)):
            results[p] = {"category_id": c, "category_runner_up_id": ru, "category_status": st}
        labels = {int(i): l for i, l in conn.execute("select facet_value_id, label from facet_values")}
        label = lambda fid, k: labels.get(k, str(k)) if isinstance(k, int) else str(k)

        def tag_metrics(cats: dict[str, str]) -> tuple[list[Metric], int, int]:
            eligible = [p for p, (c, _) in tag_products.items() if cats.get(p) and cats[p] == c]
            wrong = sum(1 for p, (c, _) in tag_products.items() if cats.get(p) and cats[p] != c)
            golden: dict[str, dict[str, GoldAnswer]] = defaultdict(dict)
            for p, f, st, fv, num, txt in conn.execute(
                    """select t.product_id, t.facet_id, t.status, t.facet_value_id, t.value_number, t.value_text
                       from golden_tags t join facets f using (facet_id)
                       where t.product_id = any(%s) and f.status = 'active'
                         and (%s::text is null or t.facet_id = %s)""", (eligible, facet, facet)):   # retired: archived
                g = golden[p].setdefault(f, GoldAnswer(st))
                k = _key(fv, num, txt)
                if k is not None:
                    g.values.add(k)
            pred: dict[str, dict[str, list[PredRow]]] = defaultdict(lambda: defaultdict(list))
            for p, f, st, fv, num, txt, conf, ev in conn.execute(
                    """select product_id, facet_id, status, facet_value_id, value_number, value_text, confidence,
                              evidence from product_facet_tags where tagging_run_id = %s and product_id = any(%s)""",
                    (run_id, eligible)):
                pred[p][f].append(PredRow(st, _key(fv, num, txt), float(conf) if conf is not None else None, ev))
            return score_tags(golden, pred, label), len(golden), wrong

        metrics = score_categories(golden_cat, results)
        excluded = 0
        if stage == "tag":
            tm, scored, excluded = tag_metrics(golden_cat)
            metrics += tm
            metrics.append(Metric("overall", "products_scored", scored, scored, {"excluded_wrong_category": excluded}))
        # holdout: reported apart, overall figures only, and only for holdout products that have a key
        if any(holdout_cat.values()):
            for m in score_categories(holdout_cat, results):
                if m.metric != "confusions":                       # no holdout answers shown
                    metrics.append(Metric("holdout:category", m.metric, m.value, m.product_count, m.detail))
            if stage == "tag":
                tm, scored, wrong = tag_metrics(holdout_cat)
                metrics += [Metric("holdout:overall", m.metric, m.value, m.product_count, m.detail)
                            for m in tm if m.scope == "overall"]
                metrics.append(Metric("holdout:overall", "products_scored", scored, scored,
                                      {"excluded_wrong_category": wrong}))
        runs = [run_summary(db, r) for r in [run_id] + [c for c in cat_runs if c != run_id]]
        for s in runs:
            scope = "run" if s["run_id"] == run_id else f"run:{s['run_id']}"
            metrics += [Metric(scope, "cost_usd", s["cost_usd"], s["succeeded"] or 0, {"stage": s["stage"]}),
                        Metric(scope, "cost_per_product", s["cost_per_product"], s["succeeded"] or 0),
                        Metric(scope, "seconds", s["seconds"], s["succeeded"] or 0),
                        Metric(scope, "seconds_per_product", s["seconds_per_product"], s["succeeded"] or 0)]
        if save:
            with conn.transaction():
                conn.execute("delete from evaluation_results where tagging_run_id = %s and kind = 'evaluate'",
                             (run_id,))
                with conn.cursor() as cur:
                    cur.executemany(
                        """insert into evaluation_results (tagging_run_id, kind, scope, metric, value, product_count,
                               detail) values (%s, 'evaluate', %s, %s, %s, %s, %s)""",
                        [(run_id, m.scope, m.metric, m.value, m.product_count,
                          Jsonb(m.detail) if m.detail is not None else None) for m in metrics])
            db.commit()
    return {"run_id": run_id, "stage": stage, "metrics": metrics, "excluded": excluded, "runs": runs}


def _pct(v):
    return "  -  " if v is None else f"{100 * v:5.1f}%"


def render(ev: dict, other: dict | None = None) -> str:
    """Summary table; with `other`, the two runs side by side and the difference."""
    def index(e):
        return {(m.scope, m.metric): m for m in e["metrics"]}
    a, b = index(ev), index(other) if other else {}
    lines = [f"run {ev['run_id']} ({ev['stage']})" + (f" vs run {other['run_id']}" if other else "")]

    def row(name, key):
        ma, mb = a.get(key), b.get(key)
        line = f"  {LABELS.get(name, name):<34}{_pct(ma.value if ma else None)}"
        if other:
            line += f"  {_pct(mb.value if mb else None)}"
            if ma and mb and ma.value is not None and mb.value is not None:
                line += f"  {100 * (mb.value - ma.value):+5.1f} pts"
        n = ma.product_count if ma else (mb.product_count if mb else 0)
        return line + f"   n={n}"

    lines.append("Category")
    for m in ("accuracy_exact", "accuracy_match_or_runner_up", "accepted_rate", "accuracy_accepted",
              "tagged_category_flagged_rate", "accuracy_flagged", "review_rate"):
        lines.append(row(m, ("category", m)))
    conf = a.get(("category", "confusions"))
    if conf and conf.detail and conf.detail["pairs"]:
        lines.append("  most common category confusions (golden -> predicted): "
                     + "; ".join(f"{g} -> {p} x{c}" for g, p, c in conf.detail["pairs"]))
    if ev["stage"] == "tag":
        lines.append(f"Tags (golden products whose category matched; excluded for a wrong category: "
                     f"{ev['excluded']})")
        for m in ("accuracy", "precision", "recall", "coverage", "review_rate", "unknown_agreement"):
            lines.append(row(m, ("overall", m)))
        facets = sorted({m.scope for m in ev["metrics"]} - {"category", "overall", "run"}
                        - {s for s in {m.scope for m in ev["metrics"]} if ":" in s})
        if facets:
            lines.append(f"  {'facet':<24}{'acc':>7}{'prec':>7}{'rec':>7}{'cov':>7}{'rev':>7}{'unk':>7}   n")
            for f in facets:
                vals = [a.get((f, m)) for m in ("accuracy", "precision", "recall", "coverage", "review_rate",
                                                "unknown_agreement")]
                cells = "".join(f"{(100 * v.value):6.0f}%" if v and v.value is not None else "      -"
                                for v in vals)
                lines.append(f"  {f:<24}{cells}   {vals[0].product_count if vals[0] else 0}")
            for f in facets:
                c = a.get((f, "confusions"))
                if c and c.detail and c.detail["pairs"]:
                    lines.append(f"  confusions {f}: " + "; ".join(f"{g} -> {p} x{k}" for g, p, k in c.detail["pairs"]))
        cal = sorted({m.scope for m in ev["metrics"] if m.scope.startswith("evidence:")})
        for scope in cal:
            cells = [f"{BAND_LABELS.get(m.metric.split('_', 1)[1], m.metric.split('_', 1)[1])}: "
                     f"{_pct(m.value).strip()} of {m.product_count}"
                     for m in ev["metrics"] if m.scope == scope]
            lines.append(f"  calibration {scope.split(':')[1]}: " + "; ".join(cells))
    if any(m.scope.startswith("holdout:") for m in ev["metrics"]):
        hs = a.get(("holdout:overall", "products_scored"))
        lines.append("Holdout (scored apart from the reviewed products; no answers shown)")
        for scope, m in (("holdout:category", "accuracy_exact"), ("holdout:category", "review_rate"),
                         ("holdout:overall", "precision"), ("holdout:overall", "recall"),
                         ("holdout:overall", "coverage"), ("holdout:overall", "review_rate")):
            lines.append(row(f"{scope.split(':')[1]} {m}", (scope, m)))
        if hs and hs.detail:
            lines.append(f"  holdout products scored for tags: {int(hs.value)} "
                         f"(excluded for a wrong category: {hs.detail['excluded_wrong_category']})")
    for s in ev["runs"]:
        per = f"${s['cost_per_product']:.5f}" if s["cost_per_product"] is not None else "n/a"
        sec = f"{s['seconds_per_product']:.1f}s" if s["seconds_per_product"] is not None else "n/a"
        lines.append(f"Cost run {s['run_id']} ({s['stage']}): ${s['cost_usd']:.4f} total, {per} per product; "
                     f"{s['seconds']:.0f}s total, {sec} per product")
    return "\n".join(lines)
