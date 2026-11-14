"""Review app, step 3 (Build PRD 9): review runs, approval, undo, outcomes.

Runs against the live database inside one transaction that is always rolled back (the app's shared
connection is replaced by a Db with commit=False), so nothing is left behind. No model calls.
"""
from __future__ import annotations

import hashlib
import os

import psycopg
import pytest
from dotenv import load_dotenv

import app.db as appdb
from app.review import ReviewError, approve, saved_decisions, save, unapprove
from scraper.store import normalize_db_url
from tagger.runs import Db

load_dotenv()
URL = os.environ.get("SUPABASE_DB_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="SUPABASE_DB_URL not set")


@pytest.fixture
def db():
    conn = psycopg.connect(normalize_db_url(URL), autocommit=False)
    test_db = Db(conn, commit=False)
    appdb._db = test_db
    yield test_db
    conn.rollback()
    conn.close()
    appdb._db = None


def one(db, sql, params=()):
    return db.conn.execute(sql, params).fetchone()


def snapshot(db, pid, run):
    rows = db.conn.execute("select * from product_facet_tags where product_id = %s and tagging_run_id = %s "
                           "order by tag_id", (pid, run)).fetchall()
    return hashlib.md5(repr(rows).encode()).hexdigest()


def state(db, pid):
    return one(db, "select state from v_product_review_state where product_id = %s", (pid,))[0]


def a_product(db, where: str):
    row = one(db, f"select product_id, ai_run_id from v_product_review_state s where {where} "
                  f"order by product_id limit 1")
    if not row and "'ready'" in where:
        # every Ready product may have been approved by the reviewer; send approved ones back inside this
        # rolled-back transaction until one matches (the live data is untouched)
        for (pid,) in db.conn.execute("select product_id from catalog_products where is_published "
                                      "order by product_id").fetchall():
            unapprove(pid)
            row = one(db, f"select product_id, ai_run_id from v_product_review_state s where {where} "
                          f"order by product_id limit 1")
            if row:
                break
    if not row:
        pytest.skip(f"no product matching {where}")
    return row


def flagged(db, pid, run):
    return [r[0] for r in db.conn.execute(
        """select g.facet_id from v_tag_flags g join facets f using (facet_id)
            where g.product_id = %s and g.tagging_run_id = %s and g.flagged order by g.facet_id""", (pid, run))]


def other_value(db, pid, run, fid):
    have = {r[0] for r in db.conn.execute("""select facet_value_id from product_facet_tags where product_id = %s
                                             and tagging_run_id = %s and facet_id = %s""", (pid, run, fid))}
    for (vid,) in db.conn.execute("select facet_value_id from facet_values where facet_id = %s and is_active "
                                  "order by sort_order", (fid,)):
        if vid not in have:
            return vid
    pytest.skip(f"{fid} has no other value")


def test_approve_ready_from_list_publishes_and_undo_returns(db):
    pid, ai = a_product(db, "s.n_resolved = 0 and s.state = 'ready'")
    before = snapshot(db, pid, ai)
    r = approve(pid, None, via="list", require_ready=True)
    run = r["reviewRunId"]
    cur, pub, at, via = one(db, "select current_run_id, is_published, approved_at, approved_via from catalog_products "
                                "where product_id = %s", (pid,))
    assert (cur, pub, via) == (run, True, "list") and at is not None
    assert state(db, pid) == "approved"
    assert one(db, "select stage, parent_run_id from tagging_runs where tagging_run_id = %s", (run,)) == ("review", ai)
    # the review run holds the tagger's answers, the tagger's rows are unchanged
    ai_vals = db.conn.execute("""select facet_id, facet_value_id, value_number, value_text from product_facet_tags
                                 where product_id = %s and tagging_run_id = %s order by 1, 2, 3, 4""", (pid, ai)).fetchall()
    rv_vals = db.conn.execute("""select facet_id, facet_value_id, value_number, value_text from product_facet_tags
                                 where product_id = %s and tagging_run_id = %s order by 1, 2, 3, 4""", (pid, run)).fetchall()
    assert ai_vals == rv_vals and snapshot(db, pid, ai) == before
    assert one(db, "select count(*) from v_catalog_products where product_id = %s", (pid,))[0] == 1
    assert one(db, "select count(*) from v_invalid_tags")[0] == 0
    unapprove(pid)
    assert one(db, "select current_run_id, is_published, approved_at from catalog_products where product_id = %s",
               (pid,)) == (ai, False, None)
    assert state(db, pid) == "ready" and one(db, "select count(*) from v_catalog_products where product_id = %s",
                                             (pid,))[0] == 0


def test_needs_review_is_refused_from_the_list_and_without_decisions(db):
    pid, ai = a_product(db, "s.state = 'needs_review' and s.n_flagged > 0 and not s.category_flagged")
    n = one(db, "select count(*) from tagging_runs where stage = 'review'")[0]
    with pytest.raises(ReviewError) as e:
        approve(pid, None, via="list", require_ready=True)
    assert e.value.status == 409
    with pytest.raises(ReviewError) as e:
        approve(pid, {}, via="detail")
    assert e.value.problems and all("needs a decision" in p for p in e.value.problems)
    assert one(db, "select count(*) from tagging_runs where stage = 'review'")[0] == n      # nothing written
    assert one(db, "select is_published from catalog_products where product_id = %s", (pid,))[0] is False


def test_changed_value_is_human_and_saved_decisions_return_after_undo(db):
    # two flags or more and nothing saved yet, so one saved decision leaves the product in Needs review
    pid, ai = a_product(db, "s.state = 'needs_review' and s.n_flagged > 1 and s.n_resolved = 0 "
                            "and not s.category_flagged")
    before = snapshot(db, pid, ai)
    flags = flagged(db, pid, ai)
    fid = next((f for f in flags if one(db, "select data_type from facets where facet_id = %s", (f,))[0]
                in ("enum", "ordinal", "boolean")), None)
    if fid is None:
        pytest.skip("no flagged list facet")
    new = other_value(db, pid, ai, fid)
    save(pid, {fid: {"decision": "changed", "values": [new]}})
    assert state(db, pid) == "needs_review"                     # saving does not publish
    rest = {f: {"decision": "approved"} for f in flags if f != fid}
    try:
        r = approve(pid, rest, via="detail")
    except ReviewError as e:
        pytest.skip(f"product needs more than flag decisions: {e.problems}")
    row = one(db, """select evidence, status, confidence, review_outcome, review_was_flagged, reason
                     from product_facet_tags where tagging_run_id = %s and facet_id = %s""", (r["reviewRunId"], fid))
    assert row[:5] == ("human", "auto", None, "changed", True) and row[5].startswith("Changed by reviewer (was:")
    assert snapshot(db, pid, ai) == before
    assert one(db, "select count(*) from v_invalid_tags")[0] == 0
    unapprove(pid)
    saved = saved_decisions(pid)["facets"]
    assert saved[fid] == {"decision": "changed", "values": [new]}


def test_outcomes_count_reviewed_and_changes(db):
    pid = ai = flags = lists = None
    for cand, run in db.conn.execute("""select product_id, ai_run_id from v_product_review_state
                                        where state = 'needs_review' and n_flagged >= 3 and not category_flagged
                                        order by product_id""").fetchall():
        fl = flagged(db, cand, run)
        ls = [f for f in fl if one(db, """select f.data_type in ('enum','ordinal','boolean')
                                          and not (cf.is_required and f.on_missing = 'review_if_required')
                                          and exists (select 1 from product_facet_tags t where t.product_id = %s
                                                      and t.tagging_run_id = %s and t.facet_id = f.facet_id
                                                      and t.facet_value_id is not null)
                                          from facets f join category_facets cf on cf.facet_id = f.facet_id
                                          join catalog_products cp on cp.category_id = cf.category_id
                                          where f.facet_id = %s and cp.product_id = %s""", (cand, run, f, cand))[0]]
        if len(ls) == len(fl) and len(ls) >= 3:
            pid, ai, flags, lists = cand, run, fl, ls
            break
    if pid is None:
        pytest.skip("needs a product whose 3+ flagged facets are optional list facets with a value")
    keep, change = lists[0], lists[1]
    dec = {f: {"decision": "approved"} for f in flags}
    dec[change] = {"decision": "changed", "values": [other_value(db, pid, ai, change)]}
    if len(lists) >= 3:
        dec[lists[2]] = {"decision": "unknown"}
    totals = lambda: one(db, """select coalesce(sum(reviewed), 0), coalesce(sum(approved_unchanged), 0),
                                       coalesce(sum(changed), 0) from v_review_outcomes
                                where facet_id is null and model_profile is null""")
    r0, u0, c0 = totals()          # the reviewer's own approvals are already in the live data
    try:
        approve(pid, dec, via="detail")
    except ReviewError as e:
        pytest.skip(f"product needs more than flag decisions: {e.problems}")
    r1, u1, c1 = totals()
    reviewed, unchanged, changed = r1 - r0, u1 - u0, c1 - c0
    assert reviewed == len(flags) and changed == (2 if len(lists) >= 3 else 1)
    assert unchanged == reviewed - changed
    assert one(db, """select count(*) from product_facet_tags t join catalog_products cp
                      on cp.current_run_id = t.tagging_run_id and cp.product_id = t.product_id
                      where cp.product_id = %s and t.review_outcome = 'not_reviewed'""", (pid,))[0] > 0
    unapprove(pid)
    assert totals() == (r0, u0, c0)


def test_tagger_publish_no_longer_publishes(db):
    from tagger.steps import publish_run
    published = lambda: sorted(r[0] for r in db.conn.execute(
        "select product_id from catalog_products where is_published"))
    before = published()
    run = one(db, "select max(tagging_run_id) from tagging_runs where stage = 'tag' and not is_drill")[0]
    publish_run(db, run)
    assert published() == before          # only approval in the app publishes


def test_evaluate_is_unchanged_by_approval(db):
    from tagger.evaluate import evaluate
    pid, ai = a_product(db, "s.n_resolved = 0 and s.state = 'ready' and s.product_id in (select product_id from golden_products)")
    key = lambda ev: sorted((m.scope, m.metric, m.value) for m in ev["metrics"]
                            if m.metric in ("precision", "recall", "coverage") and m.value is not None)
    before = key(evaluate(db, ai, save=False))
    approve(pid, None, via="list", require_ready=True)
    assert key(evaluate(db, ai, save=False)) == before


def test_saving_every_flag_moves_the_product_to_ready_and_list_approval_uses_the_decisions(db):
    """User decision of 14 Nov 2026: saved decisions resolve flags; the product becomes Ready."""
    pid, ai = a_product(db, "s.state = 'needs_review' and s.n_flagged > 0 and not s.category_flagged")
    flags = flagged(db, pid, ai)
    fid = next((f for f in flags if one(db, "select data_type from facets where facet_id = %s", (f,))[0]
                in ("enum", "ordinal", "boolean")), None)
    if fid is None:
        pytest.skip("no flagged list facet")
    new = other_value(db, pid, ai, fid)
    dec = {f: {"decision": "approved"} for f in flags}
    dec[fid] = {"decision": "changed", "values": [new]}
    first = {k: dec[k] for k in list(dec)[:1]}
    save(pid, first)
    if len(flags) > 1:
        assert state(db, pid) == "needs_review"          # one flag still open
    try:
        save(pid, dec)
    except ReviewError as e:
        pytest.skip(f"cannot save these decisions: {e.problems}")
    if one(db, "select state from v_product_review_state where product_id = %s", (pid,))[0] != "ready":
        pytest.skip("product still has a required value missing")
    assert one(db, "select n_flagged, n_resolved from v_product_review_state where product_id = %s",
               (pid,)) == (0, len(flags))
    r = approve(pid, None, via="list", require_ready=True)          # from the list, no decisions sent
    assert one(db, """select facet_value_id, evidence from product_facet_tags where tagging_run_id = %s
                      and facet_id = %s""", (r["reviewRunId"], fid)) == (new, "human")
    assert state(db, pid) == "approved"


def test_an_exclusive_value_cannot_be_saved_with_another_value(db):
    """e.g. Season "all-season" plus "transitional": refused at Save, with the reason, and nothing written."""
    row = one(db, """select s.product_id, fv.facet_id, fv.facet_value_id
                       from v_product_review_state s
                       join catalog_products cp using (product_id)
                       join category_facets cf on cf.category_id = cp.category_id
                       join facets f on f.facet_id = cf.facet_id and f.status = 'active'
                                    and f.source_policy in ('text_only', 'text_first', 'judge')
                                    and f.cardinality <> 'single'
                       join facet_values fv on fv.facet_id = f.facet_id and fv.is_exclusive and fv.is_active
                      where s.state <> 'approved' and s.ai_run_id is not null
                      order by s.product_id limit 1""")
    if not row:
        pytest.skip("no product with a multi-value facet that has an exclusive value")
    pid, fid, excl = row
    other = one(db, """select facet_value_id from facet_values where facet_id = %s and is_active
                        and not is_exclusive order by sort_order limit 1""", (fid,))[0]
    n = one(db, "select count(*) from product_facet_tags where product_id = %s", (pid,))[0]
    with pytest.raises(ReviewError) as e:
        save(pid, {fid: {"decision": "changed", "values": [excl, other]}})
    assert e.value.status == 422 and any("cannot be combined" in p for p in e.value.problems)
    assert one(db, "select count(*) from product_facet_tags where product_id = %s", (pid,))[0] == n
