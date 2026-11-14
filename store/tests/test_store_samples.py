"""Richie's step 2: store samples from Live tag, persona histories, coverage.

Database tests run inside one transaction that is rolled back; the site and the model are stubbed.
"""
from __future__ import annotations

import json
import os
import time

import psycopg
import pytest
from dotenv import load_dotenv

import app.db as appdb
from scraper.store import normalize_db_url
from tagger.runs import Db

load_dotenv()
URL = os.environ.get("SUPABASE_DB_URL", "")
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


def test_publish_sample_puts_a_product_in_the_store_and_nowhere_in_the_tagger(db):
    from app.review import publish_sample
    pid = one(db, """select product_id from catalog_products cp where source = 'tagged' and ai_run_id is not null
                      and category_status in ('auto', 'flagged') order by is_published, product_id limit 1""")[0]
    # a new store sample has no review run yet: detach this product's earlier ones (rolled back)
    db.conn.execute("update tagging_runs set product_id = null where stage = 'review' and product_id = %s", (pid,))
    # as a sample would be before publishing: tagged, unpublished, category held below the gate
    db.conn.execute("""update catalog_products set is_published = false, current_run_id = ai_run_id, approved_at = null,
                              approved_via = null, category_status = 'flagged', category_reviewed_at = null
                        where product_id = %s""", (pid,))
    publish_sample(pid)
    cp = one(db, "select is_published, source, approved_via, approved_by, category_status, current_run_id "
                 "from catalog_products where product_id = %s", (pid,))
    assert cp[:5] == (True, "sample", "sample", "sample-loader", "auto")
    run = one(db, "select stage, purpose from tagging_runs where tagging_run_id = %s", (cp[5],))
    assert run == ("review", "store_sample")
    assert one(db, "select count(*) from v_catalog_products where product_id = %s", (pid,))[0] == 1
    assert one(db, "select count(*) from v_product_review_state where product_id = %s", (pid,))[0] == 0
    assert one(db, "select count(*) from v_review_queue where product_id = %s", (pid,))[0] == 0
    assert one(db, """select count(*) from product_facet_tags where tagging_run_id = %s
                       and review_outcome <> 'not_reviewed'""", (cp[5],))[0] == 0
    assert one(db, "select count(*) from v_invalid_tags")[0] == 0


class _Dummy:
    def close(self):
        pass


def test_a_store_sample_job_marks_its_runs_skips_known_products_and_publishes(monkeypatch):
    import app.live as live
    from app import jobs, pipeline
    import app.review as review
    calls = []
    items = [{"position": i + 1, "productId": f"98888888{i}_9", "url": f"https://www.ounass.ae/shop-x-98888888{i}_9.html"}
             for i in range(3)]
    known = {items[2]["productId"]}                          # already in the catalog: left as it is
    monkeypatch.setattr(live, "_client", lambda: _Dummy())
    monkeypatch.setattr(live, "_store", lambda: _Dummy())
    monkeypatch.setattr(live, "listing_links", lambda c, url, limit: (items[:limit], 3))
    monkeypatch.setattr(live, "_known", lambda pid: pid in known)
    monkeypatch.setattr(live, "_categorised", lambda pid: pid in known)
    monkeypatch.setattr(live, "scrape_one", lambda c, s, url: calls.append(("fetch", url)) or {"name": "X", "brand": "Y"})
    monkeypatch.setattr(pipeline, "categorise",
                        lambda pid, job, purpose="tagger": calls.append(("categorise", pid, purpose)) or
                        {"status": "review" if pid.endswith("0_9") else "auto"})
    monkeypatch.setattr(pipeline, "tag", lambda pid, job, purpose="tagger": calls.append(("tag", pid, purpose)) or {})
    monkeypatch.setattr(live, "_mark_sample", lambda pid, held: calls.append(("mark", pid, held)))
    monkeypatch.setattr(review, "publish_sample", lambda pid: calls.append(("publish", pid)))
    r = live.start_listing("https://www.ounass.ae/women/clothing/dresses", 3, sample=True)
    t0 = time.time()
    while jobs.get(r["jobId"]).status == "running" and time.time() - t0 < 20:
        time.sleep(0.05)
    job = jobs.get(r["jobId"])
    assert job.kind == "sample" and job.status == "done"
    by = {it["position"]: it for it in job.items}
    assert by[3]["already"] and not any(c[1] == items[2]["productId"] for c in calls if c[0] != "fetch")
    for p in (1, 2):
        pid = items[p - 1]["productId"]
        assert by[p]["state"] == "in_store"
        assert ("categorise", pid, "store_sample") in calls and ("tag", pid, "store_sample") in calls
        assert ("publish", pid) in calls
    # held below the gate: accepted and tagged anyway (owner's decision)
    assert ("mark", items[0]["productId"], True) in calls and ("tag", items[0]["productId"], "store_sample") in calls


def test_persona_histories_load_once_and_refuse_unknown_products(db, tmp_path):
    from richies.loader import LoadError, load_personas
    path = tmp_path / "personas.json"
    pid = one(db, "select product_id from v_catalog_products order by product_id limit 1")[0]
    data = {"personas": [{"id": "layla", "events": [{"type": "view", "product_id": pid},
                                                     {"type": "purchase", "product_id": pid}]}]}
    path.write_text(json.dumps(data))
    assert load_personas(db.conn, path)["layla"] == 2
    load_personas(db.conn, path)                              # reloading replaces, never duplicates
    assert one(db, "select count(*) from store_events where persona_id = 'layla' and is_seed")[0] == 2
    data["personas"][0]["events"].append({"type": "view", "product_id": "no_such_product"})
    path.write_text(json.dumps(data))
    with pytest.raises(LoadError) as e:
        load_personas(db.conn, path)
    assert "no_such_product" in str(e.value)
    assert one(db, "select count(*) from store_events where persona_id = 'layla' and is_seed")[0] == 2


def test_coverage_counts_per_occasion_department_and_role(db):
    from richies.loader import coverage
    rows = coverage(db.conn)
    occ = {r[0] for r in db.conn.execute("select occasion from store_occasion_profiles where active")}
    assert {r[0] for r in rows} == occ and {r[1] for r in rows} == {"Women", "Men"}
    assert all(isinstance(r[4], int) and r[4] >= 0 for r in rows)
