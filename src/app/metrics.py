"""Metrics tab (Build PRD 11, with the product owner's decisions of 14 Nov 2026): a read-only report.

Two scopes:
- Live (the default): review, cost, health and the auto-accepted / review-rate tiles cover every tagged
  product, each at its latest tagger answer (catalog_products.ai_run_id).
- Quality: the golden benchmark comes from the most recent tag run that covered the golden set.
Picking a run narrows everything to that one run.

One set of definitions: golden figures come from `tagger.evaluate` (the same code as `tagger evaluate`),
and the live precision / recall / coverage run through the same `score_tags`, with the reviewer's approved
answers in place of the golden ones. Health reads what `tagger monitor` stored. Nothing is written.

Speed (the database is a 0.25 s round trip away): one snapshot of everything the page counts is read in a
few large queries and every figure is computed from it in Python. The last computed page is served at once
while a fresh one is calculated in the background whenever the data changed.

Every share is {value, num, den} or {value: None, reason}: never 0 for "nothing to count".
"""
from __future__ import annotations

import statistics
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field

from tagger.config import load_settings
from tagger.evaluate import SMALL_SAMPLE, GoldAnswer, PredRow, _key, evaluate, facet_scorecard, score_tags
from tagger.monitor import MonitorSettings, Stat, stability_index

from .db import ROOT, end, get_db, read

EVIDENCE = {"text": "text", "image": "photo", "both": "both"}
CAL_BANDS = ["0.0-0.7", "0.7-0.8", "0.8-0.9", "0.9-1.0"]
BATCH_MIN = 10            # a batch run; live tag makes one-product runs
LIVE_MIN = 30             # approved products needed before a benchmark difference gets a status
FACET_LIVE_MIN = 20       # live answers a facet needs before its drift gets a status
NOTHING_LIVE = "no product approved from its page yet"


class MetricsError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status


def share(num, den, reason: str = "nothing to count") -> dict:
    if not den:
        return {"value": None, "num": num or 0, "den": 0, "reason": reason}
    return {"value": num / den, "num": num, "den": den}


def _from_metric(m, reason: str = "nothing to count") -> dict:
    """A share from an evaluation Metric, with the counts it carries."""
    if m is None:
        return {"value": None, "num": 0, "den": 0, "reason": reason}
    d = m.detail or {}
    if m.value is None:
        return {"value": None, "num": d.get("num", 0), "den": d.get("den", 0), "reason": reason}
    return {"value": float(m.value), "num": d.get("num"), "den": d.get("den")}


def _monitor_settings() -> MonitorSettings:
    return MonitorSettings.from_dict(load_settings(ROOT / "config").monitor)


def _status(diff_points: float | None, kind: str, ms: MonitorSettings) -> str | None:
    if diff_points is None:
        return None
    warn, alert = ((ms.coverage_warning_points, ms.coverage_alert_points) if kind == "coverage"
                   else (ms.rate_warning_points, ms.rate_alert_points))
    d = abs(diff_points)
    return "alert" if d >= alert else "watch" if d >= warn else "stable"


# ------------------------------------------------------------------ the snapshot

TAG_COLS = ("pid", "run", "facet", "status", "evidence", "fv", "num", "txt", "conf", "reason")


@dataclass
class Snap:
    runs: list[dict]
    run_rows: dict[int, dict]              # every tag and review run: stage, parent, finished
    names: dict[str, str]                  # active facets
    golden: dict[str, bool]                # product -> is_holdout
    paths: dict[str, str]                  # category node -> path
    size: int
    cp: dict[str, dict]                    # catalog products with their review state
    trp: dict[tuple, dict]                 # (product, tag run) -> category, status, category run
    tags: dict[tuple, list] = field(default_factory=dict)      # (product, run) -> rows (active facets)
    outcomes: list[tuple] = field(default_factory=list)

    def under(self, node: str, cat: str | None) -> bool:
        if not node:
            return True
        n, c = self.paths.get(node), self.paths.get(cat or "")
        return bool(n and c and (c == n or c.startswith(n + " / ")))


def _load() -> Snap:
    """Everything the page counts, in six queries."""
    meta = read("""
        select (select coalesce(json_object_agg(facet_id, name), '{}') from facets where status = 'active'),
               (select coalesce(json_object_agg(product_id, is_holdout), '{}') from golden_products),
               (select coalesce(json_object_agg(node_id, path), '{}') from categories),
               (select projection_catalog_size from app_settings),
               (select coalesce(json_agg(json_build_object(
                    'id', r.tagging_run_id, 'stage', r.stage, 'parent', r.parent_run_id, 'drill', r.is_drill,
                    'finished', r.finished_at is not null, 'date', r.started_at, 'model', r.model_profile,
                    'version', r.taxonomy_version, 'products', r.products_succeeded)), '[]')
                  from tagging_runs r where r.stage in ('tag', 'review') and r.purpose = 'tagger')""")[0]
    names, golden, paths, size, run_list = meta
    run_rows = {r["id"]: r for r in run_list}
    trp_rows = read("""select p.product_id, p.tagging_run_id, p.category_id, p.category_status, p.category_run_id,
                              c.model_profile
                         from tagging_run_products p join tagging_runs r on r.tagging_run_id = p.tagging_run_id
                         left join tagging_runs c on c.tagging_run_id = p.category_run_id
                        where r.stage = 'tag' and not r.is_drill and r.purpose = 'tagger' and p.status = 'done'""")
    trp = {(p, r): {"cat": c, "status": st, "catRun": cr, "catModel": cm} for p, r, c, st, cr, cm in trp_rows}
    cp = {r[0]: dict(zip(("pid", "ai", "current", "cat", "published", "via", "approvedAt", "openedAt", "state",
                          "flagged"), r)) for r in read("""
        select cp.product_id, cp.ai_run_id, cp.current_run_id, cp.category_id, cp.is_published, cp.approved_via,
               cp.approved_at, cp.review_opened_at, s.state, s.category_flagged
          from catalog_products cp left join v_product_review_state s using (product_id)
         where cp.source <> 'sample'""")}
    tags: dict[tuple, list] = defaultdict(list)
    for row in read("""
            select t.product_id, t.tagging_run_id, t.facet_id, t.status, t.evidence, t.facet_value_id, t.value_number,
                   t.value_text, t.confidence, t.review_reason
              from product_facet_tags t join tagging_runs r using (tagging_run_id) join facets f using (facet_id)
             where f.status = 'active' and r.purpose = 'tagger'
               and ((r.stage = 'tag' and not r.is_drill) or r.stage = 'review')"""):
        tags[(row[0], row[1])].append(row)
    outcomes = read("select product_id, facet_id, outcome, was_flagged, ai_run_id, approved_via "
                    "from v_review_outcome_rows where approved_via is distinct from 'sample'")
    rs = []
    gold_by_run: dict[int, int] = defaultdict(int)
    cat_models: dict[int, set] = defaultdict(set)
    for (p, r), v in trp.items():
        if p in golden:
            gold_by_run[r] += 1
        if v["catModel"]:
            cat_models[r].add(v["catModel"])
    for r in sorted(run_list, key=lambda x: -x["id"]):
        if r["stage"] == "tag" and not r["drill"] and (r["products"] or 0) > 0:
            rs.append({"id": r["id"], "date": r["date"], "tagModel": r["model"],
                       "categoriseModel": ", ".join(sorted(cat_models[r["id"]])) or None,
                       "definitionsVersion": r["version"], "products": r["products"] or 0,
                       "goldenProducts": gold_by_run[r["id"]]})
    return Snap(rs, run_rows, names, golden, paths, int(size), cp, trp, tags, outcomes)


def runs() -> list[dict]:
    """Tag runs for the picker (review runs never), newest first."""
    return _load().runs


def benchmark_run(s: Snap) -> int | None:
    """The most recent tag run that included the golden products: at least half of the golden set.
    (A live-tag run of one golden product does not count.)"""
    total = len(s.golden)
    for r in s.runs:
        if total and r["goldenProducts"] * 2 >= total:
            return r["id"]
    best = max(s.runs, key=lambda r: (r["goldenProducts"], r["id"]), default=None)
    return best["id"] if best and best["goldenProducts"] else None


def _pairs(s: Snap, run: int | None, category: str = "", golden: str | None = None) -> list[tuple[str, int]]:
    """(product, tag run) pairs in scope. run None: every tagged product at its latest tagger answer.
    golden: None (all) | 'exclude' | 'tuned' | 'holdout'. category: a node; it and everything beneath it."""
    if run is None:
        src = [(p, c["ai"], c["cat"]) for p, c in s.cp.items() if c["ai"] is not None]
    else:
        src = [(p, r, v["cat"]) for (p, r), v in s.trp.items() if r == run]
    out = []
    for p, r, cat in src:
        g = s.golden.get(p)
        if golden == "exclude" and g is not None or golden == "tuned" and g is not False \
                or golden == "holdout" and g is not True:
            continue
        if category and not s.under(category, cat):
            continue
        out.append((p, r))
    return out


def _counted(row) -> bool:
    """A tag answer: model-written (not a rule) and not 'not applicable'."""
    return row[3] != "not_applicable" and row[4] != "rule"


def _answers(s: Snap, pairs) -> dict:
    """Auto-accepted = accepted / tag answers; review rate = in review / tag answers."""
    auto = rev = n = 0
    with_review = set()
    for p, r in pairs:
        for row in s.tags.get((p, r), ()):
            if _counted(row):
                n += 1
                auto += row[3] == "auto"
                if row[3] == "review":
                    rev += 1
                    with_review.add(p)
    return {"autoAccepted": share(auto, n, "no tag answers"), "reviewRate": share(rev, n, "no tag answers"),
            "productsWithReview": len(with_review), "products": len({p for p, _ in pairs})}


def _review_by_facet(s: Snap, pairs) -> dict[str, dict]:
    c: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for p, r in pairs:
        for row in s.tags.get((p, r), ()):
            if _counted(row):
                c[row[2]][1] += 1
                c[row[2]][0] += row[3] == "review"
    return {f: share(rv, n, "no tag answers") for f, (rv, n) in c.items()}


# ------------------------------------------------------------------ evaluation (golden), cached

_ev_cache: dict = {}
_ev_lock = threading.Lock()


def _golden_stamp() -> tuple:
    return read("select (select count(*) from golden_products), (select count(*) from golden_tags), "
                "(select count(*) filter (where is_holdout) from golden_products)")[0]


def _evaluate(run_id: int, products: set | None, facet: str | None, gstamp: tuple) -> dict:
    """`tagger.evaluate` for a run; a run's golden scoring changes only when the golden set does."""
    key = (run_id, tuple(sorted(products)) if products is not None else None, facet, gstamp)
    with _ev_lock:
        if key in _ev_cache:
            return _ev_cache[key]
    db = get_db()
    try:
        ev = evaluate(db, run_id, save=False, products=products, facet=facet)
    finally:
        end(db)
    with _ev_lock:
        _ev_cache[key] = ev
    return ev


# ------------------------------------------------------------------ live quality

def live_quality(category: str = "", s: Snap | None = None) -> dict:
    """Precision, recall and coverage of the tagger's original answers, scored by `score_tags` against the
    reviewer's approved answers: non-golden products approved through the detail page only. Answers nobody
    checked (accepted and left as they were) count as correct."""
    s = s or _load()
    prods = []
    for p, c in s.cp.items():
        rr = s.run_rows.get(c["current"]) if c["current"] else None
        if (c["published"] and c["via"] == "detail" and p not in s.golden and rr and rr["stage"] == "review"
                and rr["parent"] == c["ai"] and (not category or s.under(category, c["cat"]))):
            prods.append(p)
    golden: dict[str, dict[str, GoldAnswer]] = defaultdict(dict)
    pred: dict[str, dict[str, list[PredRow]]] = defaultdict(lambda: defaultdict(list))
    for p in prods:
        c = s.cp[p]
        for row in s.tags.get((p, c["current"]), ()):          # the approved answer
            _, _, f, st, _, fv, num, txt, _, _ = row
            k = _key(fv, num, txt)
            g = golden[p].setdefault(f, GoldAnswer("unknown"))
            if k is not None:
                g.state = "value"
                g.values.add(k)
            elif st == "not_applicable" and g.state != "value":
                g.state = "not_applicable"
        for row in s.tags.get((p, c["ai"]), ()):               # the tagger's original answer
            _, _, f, st, ev, fv, num, txt, conf, _ = row
            pred[p][f].append(PredRow(st, _key(fv, num, txt), float(conf) if conf is not None else None, ev))
    scored = score_tags(golden, pred)
    m = {x.metric: x for x in scored if x.scope == "overall"}
    per: dict[str, dict] = defaultdict(dict)
    for x in scored:
        if x.scope != "overall" and ":" not in x.scope and x.metric in ("precision", "recall", "coverage"):
            per[x.scope][x.metric] = _from_metric(x)
            per[x.scope]["answers"] = x.product_count            # facet answers scored live
    return {"products": len(prods), **{k: _from_metric(m.get(k), NOTHING_LIVE)
                                       for k in ("precision", "recall", "coverage")}, "perFacet": dict(per)}


def _benchmark(s: Snap, qrun: int, ev: dict, category: str, live: dict) -> dict:
    """Golden benchmark (tuned and holdout apart) against live, five rows, the same definitions in both."""
    m = {(x.scope, x.metric): x for x in ev["metrics"]}
    ms = _monitor_settings()
    scored = m.get(("overall", "products_scored"))
    h_scored = m.get(("holdout:overall", "products_scored"))
    n_tuned, n_hold = int(scored.value) if scored else 0, int(h_scored.value) if h_scored else 0
    live_ans = _answers(s, _pairs(s, None, category, "exclude"))
    tuned_ans = _answers(s, _pairs(s, qrun, category, "tuned"))
    hold_ans = _answers(s, _pairs(s, qrun, category, "holdout"))
    rows = []
    for key, label, kind in (("precision", "Precision", "rate"), ("recall", "Recall", "rate"),
                             ("coverage", "Coverage", "coverage"), ("autoAccepted", "Auto-accepted", "coverage"),
                             ("reviewRate", "Review rate", "rate")):
        if key in ("precision", "recall", "coverage"):
            t = {**_from_metric(m.get(("overall", key)), "no tuned product scored"), "products": n_tuned}
            h = {**_from_metric(m.get(("holdout:overall", key)), "no holdout product scored"), "products": n_hold}
            lv = {**live[key], "products": live["products"]}
            live_n = live["products"]
        else:
            t = {**tuned_ans[key], "products": tuned_ans["products"]}
            h = {**hold_ans[key], "products": hold_ans["products"]}
            lv = {**live_ans[key], "products": live_ans["products"]}
            live_n = live_ans["products"]
        bench = h if h["value"] is not None else t
        diff = (100 * (lv["value"] - bench["value"])) if lv["value"] is not None and bench["value"] is not None \
            else None
        enough = live_n >= LIVE_MIN
        rows.append({"key": key, "label": label, "tuned": t, "holdout": h, "live": lv,
                     "against": "holdout" if bench is h else "tuned", "diffPoints": diff,
                     "status": _status(diff, kind, ms) if enough else None,
                     "statusNote": None if enough or diff is None else "too few to compare"})
    return {"rows": rows, "liveMin": LIVE_MIN,
            "limits": {"rate": [ms.rate_warning_points, ms.rate_alert_points],
                       "coverage": [ms.coverage_warning_points, ms.coverage_alert_points]}}


# ------------------------------------------------------------------ block 1

def _block1(s: Snap, qrun: int, category: str, facet: str | None, gstamp: tuple) -> dict:
    products = {p for p, _ in _pairs(s, qrun, category)} if category else None
    ev = _evaluate(qrun, products, None, gstamp)
    m = {(x.scope, x.metric): x for x in ev["metrics"]}
    scored = m.get(("overall", "products_scored"))
    h_scored = m.get(("holdout:overall", "products_scored"))
    live = live_quality(category, s)
    gold_review = _review_by_facet(s, _pairs(s, qrun, category, "tuned"))
    live_review = _review_by_facet(s, _pairs(s, None, category, "exclude"))
    ms = _monitor_settings()
    card = []
    for r in facet_scorecard(ev):
        fid = r["facet"]
        if fid not in s.names or (facet and fid != facet):
            continue
        fm = lambda k: _from_metric(m.get((fid, k)))
        lv = live["perFacet"].get(fid, {})
        lp = lv.get("precision", share(0, 0, NOTHING_LIVE))
        gp = fm("precision")
        diff = 100 * (lp["value"] - gp["value"]) if lp["value"] is not None and gp["value"] is not None else None
        live_answers = lv.get("answers", 0)
        enough = live_answers >= FACET_LIVE_MIN
        card.append({"facet": fid, "name": s.names[fid], "answers": r["answers"],
                     "confidenceMean": r["confidence_mean"],
                     "golden": {"precision": gp, "recall": fm("recall"), "coverage": fm("coverage"),
                                "reviewRate": gold_review.get(fid, share(0, 0, "no tag answers"))},
                     "live": {"precision": lp, "recall": lv.get("recall", share(0, 0, NOTHING_LIVE)),
                              "coverage": lv.get("coverage", share(0, 0, NOTHING_LIVE)),
                              "reviewRate": live_review.get(fid, share(0, 0, "no tag answers")),
                              "answers": live_answers},
                     "precisionDiff": diff,
                     "drift": _status(diff, "rate", ms) if enough else None,
                     "driftNote": None if enough else "too few to compare",
                     "smallSample": r["answers"] < SMALL_SAMPLE})

    cal_ev = _evaluate(qrun, products, facet, gstamp) if facet else ev
    cal: dict = {}
    for x in cal_ev["metrics"]:
        if x.scope.startswith("evidence:") and x.metric.startswith("calibration_"):
            ev_type = x.scope.split(":", 1)[1]
            d = x.detail or {}
            cal.setdefault(EVIDENCE.get(ev_type, ev_type), {})[x.metric[len("calibration_"):]] = {
                "value": x.value, "num": d.get("num"), "den": d.get("den", x.product_count),
                "meanConfidence": d.get("mean_confidence")}
    diag = {}
    for b in CAL_BANDS:          # perfect calibration: each band's average stated confidence, all evidence types
        cells = [c[b] for c in cal.values() if b in c and c[b]["meanConfidence"] is not None]
        n = sum(c["den"] for c in cells)
        diag[b] = sum(c["meanConfidence"] * c["den"] for c in cells) / n if n else None

    quoted = bad = 0             # invented quotes: the pipeline's own check (review_reason), all products in the run
    for p, r in _pairs(s, qrun, category):
        for row in s.tags.get((p, r), ()):
            if row[4] in ("text", "both") and _key(row[5], row[6], row[7]) is not None:
                quoted += 1
                bad += row[9] == "quote_not_found"
    sizes = (sum(not h for h in s.golden.values()), sum(bool(h) for h in s.golden.values()))
    return {
        "benchmark": _benchmark(s, qrun, ev, category, live),
        "facetLiveMin": FACET_LIVE_MIN,
        "correctlyLeftUnknown": _from_metric(m.get(("overall", "unknown_agreement")), "no golden unknown answers"),
        "inventedQuote": share(bad, quoted, "no answers that cite a quote"),
        "scored": {"tuned": int(scored.value) if scored else 0, "holdout": int(h_scored.value) if h_scored else 0,
                   "excludedWrongCategory": (scored.detail or {}).get("excluded_wrong_category", 0) if scored else 0},
        "goldenSizes": {"tuned": sizes[0], "holdout": sizes[1]},
        "scorecard": card, "calibration": cal, "calibrationBands": CAL_BANDS, "calibrationDiagonal": diag,
        "precision": {"tuned": _from_metric(m.get(("overall", "precision")), "no tuned product scored"),
                      "holdout": _from_metric(m.get(("holdout:overall", "precision")), "no holdout product scored")},
        "categoryAccuracy": _from_metric(m.get(("category", "accuracy_accepted")), "no golden product in this run"),
    }


# ------------------------------------------------------------------ block 2

def _block2(s: Snap, pairs, facet: str | None, answers: dict) -> dict:
    inscope = set(pairs)
    needing = 0
    for p, r in pairs:
        v = s.trp.get((p, r), {})
        if v.get("status") not in (None, "auto") or any(row[3] == "review" for row in s.tags.get((p, r), ())):
            needing += 1
    prog = {"ready": 0, "needs_review": 0, "approved": 0}
    detail_pairs = []
    times = []
    for p, r in pairs:
        c = s.cp.get(p)
        if not c or c["ai"] != r:
            continue
        if c["state"] in prog:
            prog[c["state"]] += 1
        if c["published"] and c["via"] == "detail":
            detail_pairs.append((p, r))
            if c["openedAt"] and c["approvedAt"] and c["approvedAt"] >= c["openedAt"]:
                times.append((c["approvedAt"] - c["openedAt"]).total_seconds())
    rows = [(f, o, w, v) for p, f, o, w, ai, v in s.outcomes if (p, ai) in inscope]
    tags = [r for r in rows if r[0] != "category"]
    reviewed = [r for r in tags if r[2] and r[1] != "not_reviewed"]
    unchanged = sum(r[1] == "approved_unchanged" for r in reviewed)
    changed = sum(r[1] in ("changed", "set_unknown") for r in reviewed)
    cats = [r for r in rows if r[0] == "category" and r[2] and r[1] != "not_reviewed"]
    detail = [r for r in tags if r[3] == "detail"]
    accepted_on_detail = sum(1 for p, r in detail_pairs for row in s.tags.get((p, r), ())
                             if row[3] == "auto" and row[4] != "rule")
    per: dict[str, list[int]] = {}
    for fid, outcome, *_ in reviewed:
        if fid in s.names and (not facet or fid == facet):
            c = per.setdefault(fid, [0, 0])
            c[0 if outcome == "approved_unchanged" else 1] += 1
    # where the reviews came from: the app (detail page, list, bulk) or a review sheet (`tagger review import`)
    by_source = {}
    for src, keep in (("app", lambda v: v not in ("sheet", "auto")), ("sheet", lambda v: v == "sheet"),
                      ("auto", lambda v: v == "auto")):
        rv = [r for r in reviewed if keep(r[3])]
        by_source[src] = {"reviewed": len(rv), "unchanged": sum(r[1] == "approved_unchanged" for r in rv),
                          "changed": sum(r[1] in ("changed", "set_unknown") for r in rv),
                          "products": len({p for p, f, o, w, ai, v in s.outcomes if (p, ai) in inscope and keep(v)})}
    return {
        "bySource": by_source,
        "reviewRate": answers["reviewRate"],
        "productsNeedingReview": share(needing, len(pairs), "no products tagged"),
        "progress": {"ready": prog["ready"], "needsReview": prog["needs_review"], "approved": prog["approved"]},
        "reviewedTags": len(reviewed),
        "approvedUnchanged": share(unchanged, len(reviewed), "no reviews yet"),
        "changedOnReview": share(changed, len(reviewed), "no reviews yet"),
        "acceptedCorrected": share(sum(not r[2] and r[1] in ("changed", "set_unknown") for r in detail),
                                   accepted_on_detail, NOTHING_LIVE),
        "categoryOverrides": share(sum(r[1] in ("changed", "set_unknown") for r in cats), len(cats),
                                   "no flagged or held category reviewed yet"),
        "perFacet": sorted(({"facet": f, "name": s.names[f], "unchanged": a, "changed": b, "reviewed": a + b}
                            for f, (a, b) in per.items()), key=lambda r: (-r["reviewed"], r["name"])),
        "reviewTime": {"medianSeconds": statistics.median(times) if times else None, "products": len(times),
                       **({} if times else {"reason": NOTHING_LIVE})},
    }


# ------------------------------------------------------------------ block 3

_frozen: dict = {}          # figures of finished runs, which never change: kept for the life of the server


def _load_run(s: Snap, run_id: int) -> dict:
    from tagger.compare import load_run
    if run_id in _frozen:
        return _frozen[run_id]
    db = get_db()
    try:
        with db.lock:
            r = load_run(db, run_id)
    finally:
        end(db)
    if s.run_rows.get(run_id, {}).get("finished"):
        _frozen[run_id] = r
    return r


def _block3(s: Snap, run: int | None, qrun: int | None, gstamp: tuple) -> dict:
    """Tag step only. Live: every tag run (re-tags and live tags included) added up; a run: that run."""
    ids = [run] if run is not None else [r["id"] for r in s.runs]
    loaded = [_load_run(s, i) for i in ids]
    cost = sum(r["cost"] for r in loaded)
    n = sum(r["succeeded"] for r in loaded)
    secs = sum(r["seconds"] for r in loaded)
    per = cost / n if n else None
    batch_known = all(r["batch_per_product"] is not None for r in loaded if r["succeeded"])
    batch = (sum(r["batch_per_product"] * r["succeeded"] for r in loaded if r["succeeded"]) / n
             if n and batch_known else (per / 2 if per else None))
    models = sorted({r["profile"] for r in loaded if r["profile"]})
    comparison = []
    base = qrun if run is None else run
    if base is not None:
        b = _load_run(s, base)
        in_run = {p for (p, r) in s.trp if r == base}
        others = []
        for r in s.runs:          # other models run on the same products
            if r["id"] != base and r["tagModel"] != b["profile"] and in_run and \
                    in_run <= {p for (p, x) in s.trp if x == r["id"]}:
                others.append((r["id"], r["tagModel"]))
        if others:
            seen = set()
            for rid, prof in [(base, b["profile"])] + others:
                if prof in seen:
                    continue
                seen.add(prof)
                ev = _evaluate(rid, in_run, None, gstamp)
                pm = next((x for x in ev["metrics"] if (x.scope, x.metric) == ("overall", "precision")), None)
                comparison.append({"run": rid, "model": prof, "costPerProduct": _load_run(s, rid)["per_product"],
                                   "precision": _from_metric(pm, "no golden product scored")})
    return {
        "scope": "run" if run is not None else "all", "runs": len(loaded), "totalCost": cost, "products": n,
        "models": models, "costPerProduct": per, "secondsPerProduct": secs / n if n else None,
        "catalogSize": s.size,
        "projection": {"standard": per * s.size if per else None, "batch": batch * s.size if batch else None,
                       "batchFromStoredPrices": bool(n and batch_known)},
        "comparison": comparison if len(comparison) > 1 else [],
    }


# ------------------------------------------------------------------ block 4

STAT_FIELDS = ("facet_id", "segment_type", "segment_value", "products_applicable", "auto_count", "review_count",
               "unknown_count", "not_applicable_count", "unknown_rate", "review_rate", "coverage",
               "values_with_confidence", "band_lt_50", "band_50_70", "band_70_80", "band_80_90", "band_90_100",
               "mean_confidence", "median_confidence")


def monitor_table(mrun: int | None, names: dict | None = None) -> dict:
    """The monitor's own figures for one run against its own stored baseline, read in two queries. A facet's
    status is the most severe alert the monitor stored for it (segment 'all'), else stable; the confidence
    shift is `monitor.stability_index` on the stored confidence bands."""
    out = {"run": mrun, "monitored": False, "baselineRunId": None, "facets": []}
    if not mrun:
        return out
    names = names if names is not None else dict(read("select facet_id, name from facets where status = 'active'"))
    row = read("""select m.baseline_run_id,
                         (select coalesce(json_agg(json_build_object('f', a.facet_id, 'check', a.check_name,
                                                                     'sev', a.severity)), '[]')
                            from monitor_alerts a where a.tagging_run_id = m.tagging_run_id
                             and a.baseline_run_id = m.baseline_run_id and a.segment_type = 'all')
                    from monitor_runs m where m.tagging_run_id = %s""", (mrun,))
    if not row:
        return out
    out["monitored"], out["baselineRunId"] = True, row[0][0]
    if not out["baselineRunId"]:
        return out
    stored = defaultdict(list)
    for a in row[0][1]:
        stored[a["f"]].append((a["check"], a["sev"]))
    stats: dict[int, dict[str, Stat]] = defaultdict(dict)
    for r in read(f"""select tagging_run_id, {', '.join(STAT_FIELDS)} from run_facet_stats
                       where tagging_run_id in (%s, %s) and segment_type = 'all'""", (mrun, out["baselineRunId"])):
        st = Stat(*r[1:])
        for k in ("unknown_rate", "review_rate", "coverage", "mean_confidence", "median_confidence"):
            v = getattr(st, k)
            setattr(st, k, float(v) if v is not None else None)
        stats[r[0]][st.facet_id] = st
    cur, base = stats[mrun], stats[out["baselineRunId"]]
    for fid, sc in cur.items():
        b = base.get(fid)
        if b is None or fid not in names:
            continue
        sevs = [sev for _, sev in stored.get(fid, [])]
        status = "alert" if "alert" in sevs else "watch" if "warning" in sevs else "stable"
        psi = stability_index(b.bands(), sc.bands()) if b.values_with_confidence and sc.values_with_confidence \
            else None
        out["facets"].append({
            "facet": fid, "name": names[fid],
            "unknownBaseline": b.unknown_rate, "unknownNow": sc.unknown_rate,
            "unknownChange": (sc.unknown_rate - b.unknown_rate)
            if sc.unknown_rate is not None and b.unknown_rate is not None else None,
            "confidenceShift": psi, "status": status,
            "checks": [{"check": c, "severity": sev} for c, sev in stored.get(fid, [])]})
    out["facets"].sort(key=lambda r: (-{"alert": 2, "watch": 1}.get(r["status"], 0),
                                      -abs(r["unknownChange"] or 0), -(r["confidenceShift"] or 0)))
    return out


def _block4(s: Snap, run: int | None, live_answers: dict) -> dict:
    head = read("""select (select max(m.tagging_run_id) from monitor_runs m join tagging_runs r using (tagging_run_id)
                            where r.stage = 'tag' and not r.is_drill and r.purpose = 'tagger'),
                          (select coalesce(json_agg(json_build_object(
                                 'id', a.alert_id, 'run', a.tagging_run_id, 'facet', coalesce(f.name, a.facet_id),
                                 'segType', a.segment_type, 'segValue', a.segment_value, 'check', a.check_name,
                                 'baseline', a.baseline_value, 'current', a.current_value, 'change', a.change,
                                 'severity', a.severity, 'at', a.created_at)
                                 order by a.severity = 'alert' desc, a.created_at desc), '[]')
                             from v_open_alerts a left join facets f on f.facet_id = a.facet_id
                            where not a.is_drill and (a.facet_id = 'category' or f.status = 'active'))""")[0]
    mrun = run if run is not None else head[0]
    out = monitor_table(mrun, s.names)
    ms = _monitor_settings()
    out["limits"] = {"stabilityWatch": ms.stability_warning, "stabilityAlert": ms.stability_alert}
    batch = [r for r in sorted(s.runs, key=lambda r: r["id"])
             if r["products"] >= BATCH_MIN and (run is None or r["id"] <= run)]
    if run is not None and run not in [r["id"] for r in batch]:
        batch += [r for r in s.runs if r["id"] == run]
    points = []
    for r in batch:          # each batch run counted as the tiles count, then "now" = the live tiles
        a = live_answers if (run is not None and r["id"] == run) else _answers(s, _pairs(s, r["id"]))
        points.append({"label": f"Run {r['id']}", "run": r["id"], "date": r["date"],
                       "autoAccepted": a["autoAccepted"], "reviewRate": a["reviewRate"]})
    if run is None:
        points.append({"label": "Now", "run": None, "date": None,
                       "autoAccepted": live_answers["autoAccepted"], "reviewRate": live_answers["reviewRate"]})
    out["overTime"] = points
    num = lambda v: float(v) if v is not None else None
    out["alerts"] = [{"id": a["id"], "run": a["run"], "facet": a["facet"],
                      "segment": "all" if a["segType"] == "all" else f"{a['segType']}: {a['segValue']}",
                      "check": a["check"], "baseline": num(a["baseline"]), "current": num(a["current"]),
                      "change": num(a["change"]), "severity": a["severity"], "at": a["at"]} for a in head[1]]
    return out


# ------------------------------------------------------------------ the page: served stale while refreshing

def _stamp() -> tuple:
    """Changes whenever anything the page shows can change: a new run, an approval or undo, a review saved,
    a monitor check, a golden import, a setting."""
    return read("""select (select max(tagging_run_id) from tagging_runs),
                          (select count(*) || ':' || coalesce(max(approved_at)::text, '') || ':'
                                  || coalesce(max(category_reviewed_at)::text, '') || ':'
                                  || coalesce(max(review_opened_at)::text, '') || ':' || count(ai_run_id)
                                  || ':' || count(*) filter (where is_published) from catalog_products),
                          (select max(checked_at) from monitor_runs),
                          (select count(*) || ':' || count(*) filter (where is_holdout) from golden_products),
                          (select count(*) from golden_tags),
                          (select count(*) from monitor_alerts where status = 'open'),
                          (select projection_catalog_size || ':' || default_review_threshold || ':'
                                  || category_review_threshold from app_settings),
                          (select string_agg(facet_id || '=' || review_threshold, ',' order by facet_id)
                             from facets where review_threshold is not null)""")[0]


_pages: dict = {}           # (run, category, facet) -> {"stamp", "data", "at"}
_refreshing: set = set()
_pages_lock = threading.Lock()


def clear_cache() -> None:
    with _pages_lock:
        _pages.clear()
    with _ev_lock:
        _ev_cache.clear()


def _compute(key: tuple, stamp: tuple) -> dict:
    data = _metrics(*key, gstamp=stamp[3:5])
    with _pages_lock:
        _pages[key] = {"stamp": stamp, "data": data, "at": time.time()}
    return data


def _refresh(key: tuple, stamp: tuple) -> None:
    with _pages_lock:
        if key in _refreshing:
            return
        _refreshing.add(key)

    def run():
        try:
            _compute(key, stamp)
        except Exception:  # noqa: BLE001 - the stale page stays; the next request tries again
            import traceback
            traceback.print_exc()
        finally:
            with _pages_lock:
                _refreshing.discard(key)
    threading.Thread(target=run, name="metrics-refresh", daemon=True).start()


def metrics(run: int | None = None, category: str = "", facet: str = "", wait: bool = False) -> dict:
    """The whole page. Fresh when nothing changed; otherwise the last computed page at once, marked
    `stale`, while a fresh one is calculated in the background (`wait=True` computes it now instead)."""
    key = (run, category, facet)
    stamp = _stamp()
    with _pages_lock:
        hit = _pages.get(key)
    if hit and hit["stamp"] == stamp:
        return {**hit["data"], "stale": False}
    if hit and not wait:
        _refresh(key, stamp)
        return {**hit["data"], "stale": True}
    return {**_compute(key, stamp), "stale": False}


def warm() -> None:
    """Compute the default page in the background (server start), so the first visit is quick."""
    try:
        _refresh((None, "", ""), _stamp())
    except Exception:  # noqa: BLE001 - warming is best effort
        pass


def _metrics(run: int | None, category: str, facet: str, gstamp: tuple) -> dict:
    s = _load()
    if not s.runs:
        raise MetricsError("No tag run yet. Tag some products first.", 404)
    info = None
    if run is not None:
        info = next((r for r in s.runs if r["id"] == run), None)
        if info is None:
            raise MetricsError("That is not a tag run.", 404)
    if facet and facet not in s.names:
        raise MetricsError("Unknown facet.", 422)
    qrun = run if run is not None else benchmark_run(s)
    qinfo = next((r for r in s.runs if r["id"] == qrun), None)
    pairs = _pairs(s, run, category)
    answers = _answers(s, pairs)
    b1 = _block1(s, qrun, category, facet or None, gstamp) if qrun else None
    b3 = _block3(s, run, qrun, gstamp)
    flagged = held = 0
    if qrun:
        cat_runs = {v["catRun"] for (p, r), v in s.trp.items() if r == qrun and v["catRun"]}
        for f, h in read("""select count(*) filter (where category_status = 'flagged'),
                                   count(*) filter (where category_status = 'review')
                              from tagging_run_products where tagging_run_id = any(%s) and status = 'done'""",
                         (list(cat_runs),)):
            flagged, held = f, h
    tuned_p = b1["precision"]["tuned"] if b1 else share(0, 0, "no golden run")
    hold_p = b1["precision"]["holdout"] if b1 else share(0, 0, "no golden run")
    top = {
        "precision": {"main": hold_p if hold_p["value"] is not None else tuned_p,
                      "isTuned": hold_p["value"] is None, "tuned": tuned_p},
        "autoAccepted": answers["autoAccepted"],
        "reviewRate": {**answers["reviewRate"], "products": answers["productsWithReview"]},
        "categoryAccuracy": {**(b1["categoryAccuracy"] if b1 else share(0, 0, "no golden run")),
                             "flagged": flagged, "held": held},
        "costPerProduct": {"value": b3["costPerProduct"], "projection": b3["projection"]["standard"],
                           "catalogSize": b3["catalogSize"]},
    }
    tag_models = ", ".join(sorted({s.run_rows[c["ai"]]["model"] for c in s.cp.values()
                                   if c["ai"] in s.run_rows and s.run_rows[c["ai"]]["model"]}))
    return {"scope": {"mode": "run" if run is not None else "live", "products": answers["products"],
                      "tagModels": tag_models or None},
            "run": info, "qualityRun": qinfo, "runs": s.runs, "filters": {"category": category, "facet": facet},
            "top": top, "quality": b1, "review": _block2(s, pairs, facet or None, answers),
            "cost": b3, "health": _block4(s, run, answers)}
