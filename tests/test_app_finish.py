"""Step 5 (Build PRD 9): the remaining automated checks.

Database tests run inside one transaction that is rolled back. Job tests stub the site and the model, so
no request reaches Ounass and no model is called.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv

import app.db as appdb
from scraper.store import normalize_db_url
from tagger.runs import Db

load_dotenv()
URL = os.environ.get("SUPABASE_DB_URL", "")
ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not URL, reason="SUPABASE_DB_URL not set")


@pytest.fixture
def db():
    conn = psycopg.connect(normalize_db_url(URL), autocommit=False)
    test_db = Db(conn, commit=False)
    old = appdb._db
    appdb._db = test_db
    yield test_db
    conn.rollback()
    conn.close()
    appdb._db = old


def one(db, sql, params=()):
    return db.conn.execute(sql, params).fetchone()


def invalid(db):
    return one(db, "select count(*) from v_invalid_tags")[0]


# ------------------------------------------------------------------ state view, worked out independently

def test_every_products_state_follows_the_rules(db):
    """Build PRD 4, recomputed in Python from the rows: published -> approved; untagged, a category in review or
    below its level, or a flagged facet without a saved decision -> needs review; else ready."""
    dflt, clvl = one(db, "select default_review_threshold, category_review_threshold from app_settings")
    levels = {f: (l if l is not None else dflt) for f, l in db.conn.execute(
        "select facet_id, review_threshold from facets where status = 'active' "
        "and source_policy in ('text_only', 'text_first', 'judge')")}
    defaults = {(f, v) for f, v in db.conn.execute(
        """select f.facet_id, fv.facet_value_id from facets f join facet_values fv using (facet_id)
            where f.default_value is not null and f.default_value in (fv.value, fv.label)""")}
    for pid, pub, ai, cstat, cconf, creviewed, state in db.conn.execute(
            """select cp.product_id, cp.is_published, cp.ai_run_id, cp.category_status, cp.category_confidence,
                      cp.category_reviewed_at is not null, s.state
                 from catalog_products cp join v_product_review_state s using (product_id)""").fetchall():
        if pub:
            assert state == "approved", pid
            continue
        flagged = set()
        if ai:
            for fid, st, conf, reason, ev, fv, has in db.conn.execute(
                    """select facet_id, status, confidence, review_reason, evidence, facet_value_id,
                              num_nonnulls(facet_value_id, value_number, value_text) > 0
                         from product_facet_tags where product_id = %s and tagging_run_id = %s""", (pid, ai)):
                if fid not in levels or ev == "rule":
                    continue
                if st == "review" and (reason or "low_confidence") != "low_confidence":
                    flagged.add(fid)
                elif has and conf is not None and (fid, fv) not in defaults and conf < levels[fid]:
                    flagged.add(fid)
        rr = one(db, """select max(tagging_run_id) from tagging_runs where stage = 'review' and product_id = %s""",
                 (pid,))[0]
        decided = {r[0] for r in db.conn.execute(
            """select distinct facet_id from product_facet_tags where tagging_run_id = %s and product_id = %s
                and review_outcome is distinct from 'not_reviewed'""", (rr, pid))} if rr else set()
        cat_flag = not creviewed and (cstat == "review" or cconf < clvl)
        want = "needs_review" if (ai is None or cat_flag or flagged - decided) else "ready"
        assert state == want, pid


# ------------------------------------------------------------------ category change

def test_changing_the_category_discards_the_review_run_and_tags_only_the_new_categorys_facets(db):
    from app.review import decide_category, save
    from tagger.config import load_settings
    from tagger.facets import facets_for_tagging
    row = one(db, """select cp.product_id, cp.category_id from catalog_products cp
                      where cp.ai_run_id is not null order by cp.product_id limit 1""")
    pid, cat = row
    db.conn.execute("update catalog_products set is_published = false, current_run_id = ai_run_id, "
                    "approved_at = null, approved_via = null where product_id = %s", (pid,))
    flag = one(db, """select facet_id from product_facet_tags t join catalog_products cp using (product_id)
                       where t.product_id = %s and t.tagging_run_id = cp.ai_run_id and t.status = 'auto'
                         and t.evidence is distinct from 'rule' limit 1""", (pid,))
    if flag:
        try:
            save(pid, {flag[0]: {"decision": "approved"}})
        except Exception:  # noqa: BLE001 - a saved run is only needed to show it is discarded
            pass
    new_cat, new_path = one(db, """select c.node_id, c.path from categories c
                                    where c.node_id <> %s and exists (select 1 from category_facets cf
                                          where cf.category_id = c.node_id)
                                      and not exists (select 1 from categories k where k.parent_id = c.node_id)
                                    order by c.path limit 1""", (cat,))
    r = decide_category(pid, new_cat)
    assert r["retag"] is True
    cp = one(db, "select category_id, ai_run_id, current_run_id, category_review_outcome from catalog_products "
                 "where product_id = %s", (pid,))
    assert cp == (new_cat, None, None, "changed")
    assert one(db, """select count(*) from tagging_runs where stage = 'review' and product_id = %s""", (pid,))[0] == 0
    fc = load_settings(ROOT / "config").function_categories
    asked = {s.facet_id for s in facets_for_tagging(db.conn, new_cat, new_path, fc)}
    mapped = {r[0] for r in db.conn.execute("select facet_id from category_facets where category_id = %s", (new_cat,))}
    assert asked <= mapped                   # the re-tag asks only for facets of the new category
    assert invalid(db) == 0


# ------------------------------------------------------------------ outcomes

def _unapproved_with_flags(db, n_min: int):
    """An approved product sent back to review inside the test, with every level raised so it has flags."""
    from app.review import unapprove
    from app.settings_tabs import apply
    apply({"default": 0.99})
    for (pid,) in db.conn.execute("""select product_id from catalog_products where is_published
                                      and approved_via in ('list', 'bulk') order by product_id""").fetchall():
        unapprove(pid)
        flags = [r[0] for r in db.conn.execute(
            """select g.facet_id from v_tag_flags g join catalog_products cp on cp.product_id = g.product_id
                and cp.ai_run_id = g.tagging_run_id join facets f on f.facet_id = g.facet_id
                where g.product_id = %s and g.flagged and f.data_type in ('enum', 'ordinal')
                order by g.facet_id""", (pid,))]
        req = {r[0] for r in db.conn.execute(
            """select cf.facet_id from category_facets cf join catalog_products cp on cp.category_id = cf.category_id
                join facets f on f.facet_id = cf.facet_id
                where cp.product_id = %s and cf.is_required and f.on_missing = 'review_if_required'""", (pid,))}
        state = one(db, "select category_flagged from v_product_review_state where product_id = %s", (pid,))[0]
        if len(flags) >= n_min and not state and len([f for f in flags if f not in req]) >= 1:
            yield pid, flags, req


def test_three_outcomes_and_changed_though_confident_are_recorded_and_undo_clears_them(db):
    from app.review import ReviewError, approve, unapprove

    def other(pid, ai, fid):
        have = {r[0] for r in db.conn.execute("select facet_value_id from product_facet_tags where product_id = %s "
                                               "and tagging_run_id = %s and facet_id = %s", (pid, ai, fid))}
        r = one(db, """select facet_value_id from facet_values where facet_id = %s and is_active and not is_exclusive
                        and facet_value_id <> all(%s) order by sort_order limit 1""", (fid, list(have)))
        return r[0] if r else None
    for pid, flags, req in _unapproved_with_flags(db, 3):
        ai = one(db, "select ai_run_id from catalog_products where product_id = %s", (pid,))[0]
        unk = next(f for f in flags if f not in req)
        chg = next((f for f in flags if f != unk and other(pid, ai, f)), None)
        if chg is None:
            continue
        decisions = {f: {"decision": "approved"} for f in flags}
        decisions[unk] = {"decision": "unknown"}
        decisions[chg] = {"decision": "changed", "values": [other(pid, ai, chg)]}
        try:
            approve(pid, decisions, via="detail")
            break
        except ReviewError:          # refused for another reason (e.g. a required value): try the next one
            continue
    else:
        pytest.skip("no approved product to send back with enough flags")
    rows = db.conn.execute("select facet_id, outcome, was_flagged from v_review_outcome_rows where product_id = %s "
                           "and facet_id <> 'category'", (pid,)).fetchall()
    reviewed = [r for r in rows if r[2] and r[1] != "not_reviewed"]
    assert len(reviewed) == len(flags)
    assert sum(r[1] in ("changed", "set_unknown") for r in reviewed) == 2
    assert dict((f, o) for f, o, _ in rows)[unk] == "set_unknown"
    assert all(o == "not_reviewed" for f, o, w in rows if not w)
    before = db.conn.execute("select * from v_review_outcome_rows where product_id = %s order by 2", (pid,)).fetchall()
    unapprove(pid)
    assert one(db, "select count(*) from v_review_outcome_rows where product_id = %s", (pid,))[0] == 0
    approve(pid, decisions, via="detail")
    assert db.conn.execute("select * from v_review_outcome_rows where product_id = %s order by 2",
                           (pid,)).fetchall() == before
    assert invalid(db) == 0


def test_a_confident_answer_changed_counts_as_changed_though_confident_not_as_reviewed(db):
    from app.review import ReviewError, approve, unapprove
    for (pid,) in db.conn.execute("""select product_id from catalog_products where is_published
                                      and approved_via in ('list', 'bulk') order by product_id""").fetchall():
        unapprove(pid)
        ai = one(db, "select ai_run_id from catalog_products where product_id = %s", (pid,))[0]
        flags = {r[0] for r in db.conn.execute("""select facet_id from v_tag_flags where product_id = %s
                                                   and tagging_run_id = %s and flagged""", (pid, ai))}
        conf = one(db, """select t.facet_id, t.facet_value_id from product_facet_tags t join facets f using (facet_id)
                           where t.product_id = %s and t.tagging_run_id = %s and t.status = 'auto'
                             and f.data_type = 'enum' and f.cardinality = 'single' and t.evidence <> 'rule'
                             and f.source_policy in ('text_only', 'text_first', 'judge') and t.facet_id <> all(%s)
                           limit 1""", (pid, ai, list(flags)))
        if not conf:
            continue
        fid, old = conf
        new = one(db, "select facet_value_id from facet_values where facet_id = %s and is_active "
                      "and facet_value_id <> %s and not is_exclusive limit 1", (fid, old))
        if not new:
            continue
        decisions = {f: {"decision": "approved"} for f in flags}
        decisions[fid] = {"decision": "changed", "values": [new[0]]}
        try:
            approve(pid, decisions, via="detail")
            break
        except ReviewError:          # refused for another reason (e.g. a required value): try the next one
            continue
    else:
        pytest.skip("no product with a confident list facet that can be approved")
    r = one(db, "select outcome, was_flagged from v_review_outcome_rows where product_id = %s and facet_id = %s",
            (pid, fid))
    assert r == ("changed", False)
    tot = one(db, """select count(*) filter (where was_flagged and outcome <> 'not_reviewed'),
                            count(*) filter (where not was_flagged and outcome in ('changed', 'set_unknown'))
                       from v_review_outcome_rows where product_id = %s and facet_id <> 'category'""", (pid,))
    assert tot == (len(flags), 1)


# ------------------------------------------------------------------ live tag jobs (site and model stubbed)

def _wait(job_id, timeout=20):
    from app import jobs
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = jobs.get(job_id)
        if j and j.status != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


class _Dummy:
    def close(self):
        pass


@pytest.fixture
def stubbed(monkeypatch):
    """Live tag with the site and the model replaced: records the calls; `fail` names a step to raise in."""
    import app.live as live
    from app import pipeline
    calls, cfg = [], {"fail": None, "known": set(), "status": "auto"}

    def step(name):
        calls.append(name)
        if cfg["fail"] == name:
            raise RuntimeError(f"injected failure in {name}")
    monkeypatch.setattr(live, "_client", lambda: _Dummy())
    monkeypatch.setattr(live, "_store", lambda: _Dummy())
    monkeypatch.setattr(live, "scrape_one", lambda c, s, url: step("fetch") or {"name": "X", "brand": "Y"})
    monkeypatch.setattr(live, "_known", lambda pid: pid in cfg["known"])
    monkeypatch.setattr(live, "_categorised", lambda pid: False)
    monkeypatch.setattr(pipeline, "categorise", lambda pid, job, purpose="tagger": step("categorise") or {"status": cfg["status"]})
    monkeypatch.setattr(pipeline, "tag", lambda pid, job, purpose="tagger": step("tag") or {"runId": None})
    return calls, cfg


URL_P = "https://www.ounass.ae/shop-test-dress-999999999_99.html"


def test_a_live_tag_runs_every_step_in_order(stubbed):
    import app.live as live
    calls, cfg = stubbed
    r = live.start_product(URL_P)
    j = _wait(r["jobId"])
    assert j.status == "done" and calls == ["fetch", "categorise", "tag"]
    assert [s.state for s in j.steps] == ["done"] * 4


@pytest.mark.parametrize("step,name", [("fetch", "Fetching product"), ("categorise", "Categorising"),
                                       ("tag", "Tagging")])
def test_a_failure_at_each_step_is_reported_and_publishes_nothing(stubbed, step, name):
    import app.live as live
    from app.db import read
    calls, cfg = stubbed
    cfg["fail"] = step
    published = read("select count(*) from catalog_products where is_published")[0][0]
    j = _wait(live.start_product(URL_P)["jobId"])
    assert j.status == "failed" and j.error_step == name and "injected failure" in j.error
    assert read("select count(*) from catalog_products where is_published")[0][0] == published


def test_a_category_below_the_level_pauses_the_single_product_run(stubbed):
    import app.live as live
    calls, cfg = stubbed
    cfg["status"] = "review"
    j = _wait(live.start_product(URL_P)["jobId"])
    assert j.status == "waiting_for_category" and "tag" not in calls


def test_a_listing_job_runs_items_like_single_jobs_three_at_a_time_and_retries_a_failed_item(stubbed, monkeypatch):
    import app.live as live
    from app import pipeline
    calls, cfg = stubbed
    items = [{"position": i + 1, "productId": f"99999999{i}_9", "url": f"https://www.ounass.ae/shop-x-99999999{i}_9.html"}
             for i in range(5)]
    monkeypatch.setattr(live, "listing_links", lambda c, url, limit: (items[:limit], 5))
    cfg["known"] = {items[4]["productId"]}                 # already in the raw table
    active, peak, lock = [0], [0], threading.Lock()
    fail_once = {items[1]["productId"]}

    def tag(pid, job, purpose="tagger"):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.2)
        with lock:
            active[0] -= 1
        calls.append(("tag", pid))
        if pid in fail_once:
            fail_once.discard(pid)
            raise RuntimeError("injected failure on item 2")
        return {"runId": None}
    monkeypatch.setattr(pipeline, "tag", tag)
    j = _wait(live.start_listing("https://www.ounass.ae/women/clothing/dresses", 5)["jobId"])
    by = {it["position"]: it for it in j.items}
    assert by[2]["step"] == "failed" and by[2]["failedAt"] == "tagging"
    assert all(by[p]["step"] == "done" for p in (1, 3, 4))
    assert by[5]["already"] and ("tag", items[4]["productId"]) not in calls     # not scraped or tagged again
    assert peak[0] <= live.PARALLEL
    assert calls.count("fetch") == 4                         # one fetch per new product, through the lock
    live.retry_item(j.id, 2)
    t0 = time.time()
    while by[2]["step"] != "done" and time.time() - t0 < 10:
        time.sleep(0.05)
    assert by[2]["step"] == "done"


# ------------------------------------------------------------------ secrets, storage paths, skill files

def test_no_response_carries_a_key_or_an_unsigned_storage_path(db):
    from app.detail import product_detail
    from app.listing import Filters, rows, summary
    from app.metrics import metrics
    from app.settings_tabs import confidence, models
    pid = one(db, "select product_id from catalog_products where ai_run_id is not null order by 1 limit 1")[0]
    out = json.dumps([rows(Filters(tab="approved")), rows(Filters()), summary(Filters()), product_detail(pid),
                      models(), confidence(), metrics(wait=True)], default=str)
    for k, v in os.environ.items():
        if ("KEY" in k or "SECRET" in k or "PASSWORD" in k) and len(v) >= 12:
            assert v not in out, k
    paths = [r[0] for r in db.conn.execute("select storage_path from product_images where storage_path is not null "
                                           "limit 500")]
    strings = re.findall(r'"((?:[^"\\]|\\.)*)"', out)
    for s in strings:
        for p in paths:
            if p in s:
                assert s.startswith("http") and "token=" in s, s[:120]


def test_no_app_code_writes_to_the_skill_files():
    for f in (ROOT / "src" / "app").glob("*.py"):
        text = f.read_text()
        assert "skills" not in text or not re.search(r"open\([^)]*skills[^)]*['\"]w", text), f.name
        assert "write_text" not in text or "skills" not in text, f.name


# ------------------------------------------------------------------ runs and settings

def test_a_run_started_after_a_facet_save_records_the_new_taxonomy_version(db):
    from app.facets_tab import facet_detail, save
    from app.pipeline import settings_for
    from tagger.runs import start_run
    d = facet_detail("season")
    save("season", {"name": d["name"] + " (test)", "definition": d["definition"], "policy": d["policy"],
                    "onMissing": d["onMissing"], "maxValues": d["maxValues"],
                    "values": [{"id": v["id"], "label": v["label"], "definition": v["definition"],
                                "active": v["active"]} for v in d["values"]]})
    tv = one(db, "select taxonomy_version from app_settings")[0]
    s = settings_for(db)
    run = start_run(db, "tag", s.profiles[s.tag_model], "test", None, drill="test")
    assert one(db, "select taxonomy_version from tagging_runs where tagging_run_id = %s", (run.run_id,))[0] == tv


def test_a_stored_model_with_no_profile_stops_the_pipeline_with_a_clear_message(db):
    from app.pipeline import settings_for
    from tagger.config import ConfigError
    db.conn.execute("update app_settings set tag_model = 'ghost'")
    with pytest.raises(ConfigError) as e:
        settings_for(db)
    assert "ghost" in str(e.value) and "Models tab" in str(e.value)


def test_the_facet_filter_by_value_and_by_flag_and_the_category_filter_return_the_right_products(db):
    from app.listing import Filters, rows
    fid, vid = one(db, """select t.facet_id, t.facet_value_id from catalog_products cp
                           join product_facet_tags t on t.product_id = cp.product_id and t.tagging_run_id = cp.current_run_id
                          where cp.is_published and t.facet_value_id is not null
                          group by 1, 2 order by count(*) desc limit 1""")
    want = {r[0] for r in db.conn.execute(
        """select distinct cp.product_id from catalog_products cp join product_facet_tags t
             on t.product_id = cp.product_id and t.tagging_run_id = cp.current_run_id
            where cp.is_published and t.facet_id = %s and t.facet_value_id = %s""", (fid, vid))}
    got = rows(Filters(tab="approved", facet=fid, value=str(vid), page=1))
    assert got["total"] == len(want)
    node = one(db, "select node_id, path from categories where path = 'All / Women' or path like '%% / Women' limit 1")
    if node:
        want_c = {r[0] for r in db.conn.execute(
            """select cp.product_id from catalog_products cp join categories c on c.node_id = cp.category_id
                where cp.is_published and (c.path = %s or c.path like %s || ' / %%')""", (node[1], node[1]))}
        assert rows(Filters(tab="approved", category=node[0]))["total"] == len(want_c)
