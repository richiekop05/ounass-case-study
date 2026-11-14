"""`tagger review export / import` (owner, 14 Nov 2026): the sheet writes what the app writes.

Runs inside one transaction that is rolled back; no model call (re-tagging is switched off).
"""
from __future__ import annotations

import hashlib
import os

import openpyxl
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


def latest_run(db):
    r = one(db, """select r.tagging_run_id from tagging_runs r
                    where r.stage = 'tag' and not r.is_drill and exists (
                          select 1 from tagging_run_products p where p.tagging_run_id = r.tagging_run_id
                             and not exists (select 1 from golden_products g where g.product_id = p.product_id))
                    order by r.products_succeeded desc, r.tagging_run_id desc limit 1""")
    if not r:
        pytest.skip("no tag run with non-golden products")
    return r[0]


def snapshot(db, pid, run):
    rows = db.conn.execute("select * from product_facet_tags where product_id = %s and tagging_run_id = %s "
                           "order by tag_id", (pid, run)).fetchall()
    return hashlib.md5(repr(rows).encode()).hexdigest()


def fill(path, verdicts):
    """verdicts: {(sheet, product_id, facet_id or None): (verdict, corrected)}"""
    wb = openpyxl.load_workbook(path)
    for ws in wb.worksheets:
        head = [c.value for c in ws[1]]
        for row in ws.iter_rows(min_row=2):
            d = dict(zip(head, [c.value for c in row]))
            key = (ws.title, d["product_id"], d.get("facet_id"))
            if key in verdicts:
                v, corr = verdicts[key]
                row[head.index("verdict")].value = v
                row[head.index("corrected_category_id" if ws.title == "Categories" else "corrected_value")].value = corr
    wb.save(path)


def test_export_has_only_review_rows_no_golden_and_sorted_by_product(db, tmp_path):
    from tagger.review_sheet import export
    run = latest_run(db)
    r = export(db.conn, run, tmp_path)
    wb = openpyxl.load_workbook(r["path"])
    assert wb.sheetnames == ["Categories", "Facets"]
    golden = {x[0] for x in db.conn.execute("select product_id from golden_products")}
    for ws in wb.worksheets:
        ids = [row[0] for row in ws.iter_rows(min_row=2, values_only=True)]
        assert ids == sorted(ids) and not set(ids) & golden
    flagged = one(db, """select count(*) from v_tag_flags g join catalog_products cp on cp.product_id = g.product_id
                           and cp.ai_run_id = g.tagging_run_id
                          where g.flagged and g.tagging_run_id = %s and not cp.is_published
                            and not exists (select 1 from golden_products x where x.product_id = g.product_id)""",
                  (run,))[0]
    assert r["facets"] == flagged                         # one row per answer in review, nothing accepted


def test_import_writes_what_the_app_writes(db, tmp_path):
    from tagger.review_sheet import export, import_sheet
    run = latest_run(db)
    r = export(db.conn, run, tmp_path)
    wb = openpyxl.load_workbook(r["path"])
    facet_rows = list(wb["Facets"].iter_rows(min_row=2, values_only=True))
    cat_ids = {row[0] for row in wb["Categories"].iter_rows(min_row=2, values_only=True)}
    by = {}
    for row in facet_rows:
        by.setdefault(row[0], []).append(row)
    # products whose answers in review all carry a value (approving "unknown" on a required facet is refused,
    # in the sheet as in the app)
    cands = [p for p, rows in by.items() if p not in cat_ids and len(rows) >= 2
             and all(r[6] and r[6] != "unknown" for r in rows)]
    if len(cands) < 2:
        pytest.skip("needs two products with two answers in review and an accepted category")
    full, partial = cands[0], cands[1]
    before = snapshot(db, full, run)
    verdicts = {("Facets", full, row[5]): ("approve", None) for row in by[full]}
    # one change to an allowed value on the fully reviewed product
    fid = by[full][0][5]
    allowed = [v.strip() for v in (by[full][0][11] or "").split("|") if v.strip()]
    current = {v.strip() for v in (by[full][0][6] or "").split(";")}
    other = next((a for a in allowed if a not in current), None)
    if other:
        verdicts[("Facets", full, fid)] = ("change", other)
    # the other product: one verdict blank, one value not allowed
    verdicts[("Facets", partial, by[partial][0][5])] = ("change", "not-a-real-value")
    fill(r["path"], verdicts)
    rep = import_sheet(type(tmp_path)(r["path"]), retag=False)
    assert full in rep.approved, rep.render()
    assert partial in rep.saved_partial and any("not-a-real-value" in x for x in rep.rejected)
    cp = one(db, "select is_published, approved_via, current_run_id, ai_run_id from catalog_products "
                 "where product_id = %s", (full,))
    assert cp[0] is True and cp[1] == "sheet" and cp[2] != cp[3]
    assert one(db, "select stage, parent_run_id from tagging_runs where tagging_run_id = %s", (cp[2],)) == ("review", run)
    assert snapshot(db, full, run) == before                      # the tagger's answers are kept as they were
    assert one(db, "select count(*) from v_catalog_products where product_id = %s", (full,))[0] == 1
    assert one(db, "select is_published from catalog_products where product_id = %s", (partial,))[0] is False
    assert one(db, "select count(*) from v_invalid_tags")[0] == 0
    outcomes = db.conn.execute("select outcome, approved_via from v_review_outcome_rows where product_id = %s "
                               "and was_flagged", (full,)).fetchall()
    assert outcomes and all(v == "sheet" for _, v in outcomes)
    if other:
        assert ("changed", "sheet") in outcomes
    # live precision leaves sheet reviews out
    from app.metrics import live_quality
    assert full not in str(live_quality())


def test_auto_approve_publishes_with_the_taggers_answers_and_counts_nothing_as_reviewed(db):
    from app.review import ReviewError, auto_approve
    run = latest_run(db)
    rows = db.conn.execute("""select s.product_id from v_product_review_state s
                               join catalog_products cp using (product_id)
                              where s.state = 'needs_review' and cp.ai_run_id = %s
                                and not exists (select 1 from golden_products g where g.product_id = s.product_id)
                              order by (s.category_flagged) desc, s.product_id limit 12""", (run,)).fetchall()
    done = None
    for (pid,) in rows:
        before = snapshot(db, pid, run)
        try:
            auto_approve(pid)
            done = pid
            break
        except ReviewError:
            continue
    if not done:
        pytest.skip("no product the app would approve")
    cp = one(db, "select is_published, approved_via, category_review_outcome from catalog_products "
                 "where product_id = %s", (done,))
    assert cp[0] is True and cp[1] == "auto" and cp[2] in (None, "not_reviewed")
    assert snapshot(db, done, run) == before
    assert one(db, """select count(*) from v_review_outcome_rows where product_id = %s
                       and outcome <> 'not_reviewed'""", (done,))[0] == 0
    assert one(db, "select count(*) from v_invalid_tags")[0] == 0
    held = one(db, """select product_id from catalog_products where ai_run_id is null and not is_published limit 1""")
    if held:
        with pytest.raises(ReviewError):
            auto_approve(held[0])
