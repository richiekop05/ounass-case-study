"""Metrics tab (Build PRD 11 and the decisions of 14 Nov 2026): the page gives the same figures as
`tagger evaluate`, live figures use each product's latest tagging, and the health block agrees with what the
monitor stored.

Read-only against the real database (evaluate runs with save=False). The one test that approves a product
does it inside a transaction that is rolled back.
"""
from __future__ import annotations

import os

import psycopg
import pytest
from dotenv import load_dotenv

load_dotenv()
URL = os.environ.get("SUPABASE_DB_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="SUPABASE_DB_URL not set")


@pytest.fixture(scope="module")
def page():
    from app import metrics as M
    M.clear_cache()
    p = M.metrics(wait=True)
    if not p["quality"]:
        pytest.skip("no tag run covered the golden set")
    return p


@pytest.fixture(scope="module")
def ev(page):
    from app.db import end, get_db
    from tagger.evaluate import evaluate
    db = get_db()
    try:
        return evaluate(db, page["qualityRun"]["id"], save=False)
    finally:
        end(db)


def _m(ev, scope, metric):
    return next((x for x in ev["metrics"] if (x.scope, x.metric) == (scope, metric)), None)


def _rows(page):
    return {r["key"]: r for r in page["quality"]["benchmark"]["rows"]}


def test_golden_benchmark_matches_tagger_evaluate_and_the_scorecard(page, ev):
    from tagger.evaluate import facet_scorecard
    rows = _rows(page)
    for name in ("precision", "recall", "coverage"):
        assert rows[name]["tuned"]["value"] == pytest.approx(_m(ev, "overall", name).value)
        h = _m(ev, "holdout:overall", name)
        assert rows[name]["holdout"]["value"] == (pytest.approx(h.value) if h and h.value is not None else None)
    q = page["quality"]
    assert q["correctlyLeftUnknown"]["value"] == pytest.approx(_m(ev, "overall", "unknown_agreement").value)
    card = {r["facet"]: r for r in facet_scorecard(ev)}
    assert q["scorecard"], "the scorecard is empty"
    for row in q["scorecard"]:
        c = card[row["facet"]]
        assert row["answers"] == c["answers"]
        for k in ("precision", "recall", "coverage"):     # review rate: counted as the table counts (own test)
            assert row["golden"][k]["value"] == (pytest.approx(c[k]) if c[k] is not None else None)
    top = page["top"]["precision"]
    assert top["main"]["value"] == (rows["precision"]["tuned"] if top["isTuned"] else rows["precision"]["holdout"])["value"]


def test_quality_comes_from_the_latest_run_that_covered_the_golden_set(page):
    from app.db import read
    total = read("select count(*) from golden_products")[0][0]
    q = page["qualityRun"]
    assert q["goldenProducts"] * 2 >= total
    later = [r for r in page["runs"] if r["id"] > q["id"] and r["goldenProducts"] * 2 >= total]
    assert not later


def test_every_share_carries_its_counts_and_nothing_to_count_is_never_zero(page):
    for r in page["quality"]["benchmark"]["rows"]:
        for side in ("tuned", "holdout", "live"):
            s = r[side]
            assert "products" in s
            if s["value"] is None:
                assert s["reason"] and not s["den"]
            else:
                assert s["den"] and s["value"] == pytest.approx(s["num"] / s["den"])
        if r["diffPoints"] is not None and r["live"]["products"] < page["quality"]["benchmark"]["liveMin"]:
            assert r["status"] is None and r["statusNote"] == "too few to compare"


def test_tuned_holdout_and_live_are_disjoint_and_never_combined(page):
    from app.db import read
    q = page["qualityRun"]["id"]
    rows = read("""select g.product_id, g.is_holdout from golden_products g
                     join tagging_run_products p on p.product_id = g.product_id
                    where p.tagging_run_id = %s and p.status = 'done'""", (q,))
    assert not {p for p, h in rows if not h} & {p for p, h in rows if h}
    for r in page["quality"]["benchmark"]["rows"]:
        assert set(r) >= {"tuned", "holdout", "live"} and "combined" not in r and "overall" not in r
    from app.metrics import _load, _pairs
    golden = {p for p, _ in rows}
    assert not {p for p, _ in _pairs(_load(), None, "", "exclude")} & golden


def test_wrong_category_products_leave_tag_metrics_but_count_for_category_accuracy(page, ev):
    from app.db import read
    run = page["qualityRun"]["id"]
    rows = read("""select g.product_id, g.category_id = p.category_id from golden_products g
                     join tagging_run_products p on p.product_id = g.product_id
                    where p.tagging_run_id = %s and p.status = 'done' and not g.is_holdout
                      and g.category_id is not null""", (run,))
    wrong = sum(not ok for _, ok in rows)
    assert page["quality"]["scored"]["excludedWrongCategory"] == wrong
    assert page["quality"]["scored"]["tuned"] == len(rows) - wrong
    assert _m(ev, "category", "accuracy_exact").product_count >= len(rows)


def test_calibration_drops_no_answer_and_cells_sum_to_the_answers_with_a_value(page):
    from app.db import read
    run = page["qualityRun"]["id"]
    cells = [c for bands in page["quality"]["calibration"].values() for c in bands.values()]
    assert all(c["den"] for c in cells)
    n = read("""
        select count(*) from product_facet_tags t join facets f using (facet_id)
          join tagging_run_products p on p.product_id = t.product_id and p.tagging_run_id = t.tagging_run_id
          join golden_products g on g.product_id = t.product_id and not g.is_holdout and g.category_id = p.category_id
         where t.tagging_run_id = %s and f.status = 'active' and t.status in ('auto', 'review')
           and t.confidence is not null and num_nonnulls(t.facet_value_id, t.value_number, t.value_text) > 0
           and exists (select 1 from golden_tags gt where gt.product_id = t.product_id and gt.facet_id = t.facet_id)""",
             (run,))[0][0]
    assert sum(c["den"] for c in cells) == n
    assert page["quality"]["calibrationBands"][0] == "0.0-0.7"


def test_a_facet_filter_narrows_the_scorecard_and_calibration_only(page):
    from app import metrics as M
    fid = page["quality"]["scorecard"][0]["facet"]
    one = M.metrics(None, "", fid, wait=True)
    assert [r["facet"] for r in one["quality"]["scorecard"]] == [fid]
    assert one["quality"]["benchmark"] == page["quality"]["benchmark"]
    assert sum(c["den"] for b in one["quality"]["calibration"].values() for c in b.values()) <= \
        sum(c["den"] for b in page["quality"]["calibration"].values() for c in b.values())


def test_the_latest_point_over_time_equals_the_tiles(page):
    from app import metrics as M
    last = page["health"]["overTime"][-1]
    assert last["run"] is None
    assert last["autoAccepted"] == page["top"]["autoAccepted"]
    assert {k: last["reviewRate"][k] for k in ("value", "num", "den")} == \
        {k: page["top"]["reviewRate"][k] for k in ("value", "num", "den")}
    one = M.metrics(page["qualityRun"]["id"], wait=True)
    assert one["health"]["overTime"][-1]["run"] == page["qualityRun"]["id"]
    assert one["health"]["overTime"][-1]["autoAccepted"] == one["top"]["autoAccepted"]


def test_live_tiles_cover_every_tagged_product_at_its_latest_tagging(page):
    from app.db import read
    n = read("select count(*) from catalog_products where ai_run_id is not null")[0][0]
    assert page["scope"]["mode"] == "live" and page["scope"]["products"] == n


def test_confidence_shift_uses_the_monitors_baseline_limits_and_stored_alerts(page):
    from app.db import end, get_db, read
    from app.metrics import _monitor_settings, monitor_table
    from tagger.monitor import compare_stat, load_stats
    h = page["health"]
    if not h["run"]:
        pytest.skip("no monitored tag run")
    assert h["baselineRunId"] == read("select baseline_run_id from monitor_runs where tagging_run_id = %s",
                                      (h["run"],))[0][0]
    t = monitor_table(h["run"])
    stored = {}
    for f, check, sev, cur in read("""select facet_id, check_name, severity, current_value from monitor_alerts
                                       where tagging_run_id = %s and baseline_run_id = %s and segment_type = 'all'""",
                                   (h["run"], h["baselineRunId"])):
        stored.setdefault(f, []).append((check, sev, float(cur) if cur is not None else None))
    db = get_db()
    try:
        with db.lock:
            cur = {s.facet_id: s for s in load_stats(db, h["run"]) if s.segment_type == "all"}
            base = {s.facet_id: s for s in load_stats(db, h["baselineRunId"]) if s.segment_type == "all"}
    finally:
        end(db)
    ms = _monitor_settings()
    sev = {"alert": "alert", "warning": "watch"}
    for row in t["facets"]:
        f = row["facet"]
        kept = [s for _, s, _ in stored.get(f, [])]
        want = "alert" if "alert" in kept else "watch" if "warning" in kept else "stable"
        assert row["status"] == want, f
        # the monitor's own limits give the same status today
        found = [x.severity for x in compare_stat(base[f], cur[f], ms)]
        assert ("alert" if "alert" in found else "watch" if "warning" in found else "stable") == want, f
        for check, _, value in stored.get(f, []):
            if check == "stability_index" and value is not None:
                assert row["confidenceShift"] == pytest.approx(value), f
        assert row["status"] in ("stable",) + tuple(sev.values())


def test_review_runs_are_never_offered_and_unknown_runs_are_refused():
    from app import metrics as M
    from app.db import read
    review = read("select tagging_run_id from tagging_runs where stage = 'review' limit 1")
    if review:
        assert review[0][0] not in {r["id"] for r in M.runs()}
        with pytest.raises(M.MetricsError):
            M.metrics(review[0][0])


@pytest.fixture
def db():
    import app.db as appdb
    from scraper.store import normalize_db_url
    from tagger.runs import Db
    conn = psycopg.connect(normalize_db_url(URL), autocommit=False)
    test_db = Db(conn, commit=False)
    old = appdb._db
    appdb._db = test_db
    yield test_db
    conn.rollback()
    conn.close()
    appdb._db = old


def test_live_quality_scores_original_answers_against_approved_ones_detail_page_only(db):
    """Approve one non-golden product from its page with one confident value changed (rolled back): live
    precision gains that product's accepted values, all right but the changed one; a list approval adds nothing."""
    from app.metrics import live_quality
    from app.review import ReviewError, approve
    c = db.conn
    candidates = c.execute("""
        select s.product_id, s.ai_run_id from v_product_review_state s
         where s.state <> 'approved' and not s.category_flagged and s.ai_run_id is not null
           and not exists (select 1 from golden_products g where g.product_id = s.product_id)
         order by (s.state = 'ready') desc, s.product_id""").fetchall()
    before = live_quality()
    for pid, ai in candidates:
        pick = c.execute("""select t.facet_id, t.facet_value_id from product_facet_tags t join facets f using (facet_id)
                             where t.product_id = %s and t.tagging_run_id = %s and t.status = 'auto'
                               and t.evidence is distinct from 'rule' and f.source_policy in ('text_only', 'text_first', 'judge')
                               and t.facet_value_id is not null and f.data_type in ('enum', 'ordinal')
                               and f.status = 'active' and f.max_values = 1
                             order by t.facet_id limit 1""", (pid, ai)).fetchone()
        if not pick:
            continue
        fid, old = pick
        new = c.execute("""select facet_value_id from facet_values where facet_id = %s and is_active
                            and facet_value_id <> %s order by sort_order limit 1""", (fid, old)).fetchone()
        if not new:
            continue
        flags = [r[0] for r in c.execute("""select facet_id from v_tag_flags where product_id = %s
                                             and tagging_run_id = %s and flagged""", (pid, ai))]
        decisions = {f: {"decision": "approved"} for f in flags if f != fid}
        decisions[fid] = {"decision": "changed", "values": [new[0]]}
        try:
            approve(pid, decisions, via="detail")
            break
        except ReviewError:                    # refused (e.g. a required value missing): nothing was written
            continue
    else:
        pytest.skip("no non-golden product could be approved")
    after = live_quality()
    assert after["products"] == before["products"] + 1
    n_auto = c.execute("""select count(*) from product_facet_tags t join facets f using (facet_id)
                           where t.product_id = %s and t.tagging_run_id = %s and t.status = 'auto' and f.status = 'active'
                             and num_nonnulls(t.facet_value_id, t.value_number, t.value_text) > 0""",
                       (pid, ai)).fetchone()[0]
    p0, p1 = before["precision"], after["precision"]
    assert p1["den"] - p0["den"] == n_auto
    assert (p1["den"] - p1["num"]) - (p0["den"] - p0["num"]) == 1           # only the changed value is wrong


def test_facet_scorecard_puts_golden_and_live_side_by_side_with_the_tables_definitions(page, ev):
    """Golden precision / recall / coverage per facet = `tagger evaluate`; golden and live review rate are counted as
    the benchmark table counts; live figures come from the same live sources; drift needs 20 live answers."""
    from app.metrics import FACET_LIVE_MIN, _load, _pairs, _review_by_facet, live_quality
    q = page["quality"]
    snap = _load()
    live = live_quality("", snap)
    gold_rr = _review_by_facet(snap, _pairs(snap, page["qualityRun"]["id"], "", "tuned"))
    live_rr = _review_by_facet(snap, _pairs(snap, None, "", "exclude"))
    for r in q["scorecard"]:
        f = r["facet"]
        for k in ("precision", "recall", "coverage"):
            want = _m(ev, f, k).value
            assert r["golden"][k]["value"] == (pytest.approx(want) if want is not None else None)
            lv = live["perFacet"].get(f, {}).get(k)
            assert r["live"][k]["value"] == (lv["value"] if lv else None)
        assert r["golden"]["reviewRate"]["value"] == (gold_rr[f]["value"] if f in gold_rr else None)
        assert r["live"]["reviewRate"]["value"] == (live_rr[f]["value"] if f in live_rr else None)
        if r["live"]["answers"] < FACET_LIVE_MIN:
            assert r["drift"] is None and r["driftNote"] == "too few to compare"
        if r["precisionDiff"] is not None:
            assert r["precisionDiff"] == pytest.approx(100 * (r["live"]["precision"]["value"] - r["golden"]["precision"]["value"]))
        assert r["smallSample"] == (r["answers"] < 20)
    # the per-facet live figures add up to the table's live figures
    rows = _rows(page)
    tot = sum(x.get("precision", {}).get("den") or 0 for x in live["perFacet"].values())
    assert tot == (rows["precision"]["live"]["den"] or 0)


def test_recall_never_exceeds_coverage(page, ev):
    for x in ev["metrics"]:
        if x.metric == "coverage" and x.value is not None:
            r = _m(ev, x.scope, "recall")
            assert r is None or r.value is None or r.value <= x.value + 1e-12, x.scope
    rows = _rows(page)
    for side in ("tuned", "holdout"):
        if rows["coverage"][side]["value"] is not None:
            assert rows["recall"][side]["value"] <= rows["coverage"][side]["value"]


def test_a_changed_stamp_serves_the_last_page_at_once_and_refreshes_in_the_background(page, monkeypatch):
    import time as _t
    from app import metrics as M
    first = M.metrics()                                   # cached, same data: fresh
    assert first["stale"] is False
    real = M._stamp
    changed = real()[:-1] + ("changed",)
    monkeypatch.setattr(M, "_stamp", lambda: changed)
    t0 = _t.time()
    second = M.metrics()
    assert second["stale"] is True and _t.time() - t0 < 2      # served at once
    for _ in range(120):                                        # the background refresh lands
        if M._pages[(None, "", "")]["stamp"] == changed:
            break
        _t.sleep(0.5)
    assert M.metrics()["stale"] is False
