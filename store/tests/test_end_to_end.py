"""End to end (engineering PRD 9): a product approved in the tagger app appears in the store's catalogue and returns
a look, with no restart. Runs inside one rolled-back transaction."""
from __future__ import annotations

import os

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


def test_a_product_approved_in_the_tagger_reaches_the_store_with_a_look(db):
    from app.review import auto_approve, unapprove
    from richies import catalog
    from richies.features import build_look
    pid = db.conn.execute("""select cp.product_id from catalog_products cp
                               join store_outfit_roles o on o.category_node_id = cp.category_id and o.role = 'one_piece'
                              where cp.is_published and cp.source = 'tagged' order by cp.product_id limit 1""").fetchone()[0]
    unapprove(pid)
    catalog.clear()
    ps, _ = catalog.load(db.conn)
    assert pid not in {p.id for p in ps}                 # sent back to review: gone from the store
    auto_approve(pid)                                    # approved again in the tagger app
    catalog.clear()                                      # the store re-reads when its stamp changes
    ps, rules = catalog.load(db.conn)
    anchor = next((p for p in ps if p.id == pid), None)
    assert anchor is not None
    look = build_look(anchor, ps, rules)
    assert look["filled"] >= 1
    catalog.clear()
