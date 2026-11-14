"""Richie's step 1: rule tables, vocabulary validation, and store samples kept out of every tagger figure.

Database tests run inside one transaction that is rolled back.
"""
from __future__ import annotations

import json
import os
import shutil

import psycopg
import pytest
from dotenv import load_dotenv

import app.db as appdb
from richies import rules
from scraper.store import normalize_db_url
from tagger.runs import Db

load_dotenv()
URL = os.environ.get("SUPABASE_DB_URL", "")
needs_db = pytest.mark.skipif(not URL, reason="SUPABASE_DB_URL not set")


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


@pytest.mark.parametrize("path,role", [
    ("Women / Clothing / Dresses / Midi Dresses", "one_piece"), ("Women / Clothing / Abayas", "one_piece"),
    ("Men / Clothing / Tops / Shirts", "top"), ("Women / Clothing / Skirts", "bottom"),
    ("Men / Clothing / Coats & Jackets", "outerwear"), ("Women / Shoes / Pumps", "shoes"),
    ("Women / Bags / Clutches", "bag"), ("Women / Jewellery / Earrings", "jewellery"),
    ("Women / Accessories / Belts", "accessory"), ("Women / Beauty / Fragrance", "none"),
    ("All / Home / Dining", "none"), ("Women / Bags / Washbags", "none"), ("Women / Clothing", "none")])
def test_outfit_roles_follow_the_spine(path, role):
    assert rules.role_for(path)[0] == role


@needs_db
def test_an_unknown_value_stops_the_load_and_writes_nothing(db, tmp_path, monkeypatch):
    bad = tmp_path / "rules"
    shutil.copytree(rules.RULES, bad)
    data = json.loads((bad / "occasion_profiles.json").read_text())
    data["profiles"][0]["occasion"] = "engagement"           # not in the occasion vocabulary
    data["profiles"][1]["formality_min"] = "very formal"
    (bad / "occasion_profiles.json").write_text(json.dumps(data))
    monkeypatch.setattr(rules, "RULES", bad)
    before = one(db, "select count(*) from store_occasion_profiles")[0]
    with pytest.raises(rules.RuleError) as e:
        rules.seed(db.conn)
    assert "engagement" in str(e.value) and "very formal" in str(e.value)
    assert one(db, "select count(*) from store_occasion_profiles")[0] == before


@needs_db
def test_rule_tables_are_seeded_from_the_vocabulary(db):
    vocab = lambda f: {r[0] for r in db.conn.execute(
        "select value from facet_values where facet_id = %s and is_active", (f,))}
    occ = vocab("occasion")
    rows = db.conn.execute("select occasion, formality_min, formality_max, seasons from store_occasion_profiles").fetchall()
    assert rows and all(r[0] in occ and {r[1], r[2]} <= vocab("formality") and set(r[3]) <= vocab("season")
                        for r in rows)
    n = len(vocab("colour_primary"))
    assert one(db, "select count(*) from store_colour_pairings")[0] == n * (n + 1) // 2
    assert one(db, "select score from store_colour_pairings where colour_a = 'black' and colour_b = 'pink'")[0] == 1
    assert one(db, "select count(*) from store_outfit_roles")[0] == one(db, "select count(*) from categories")[0]
    assert {r[0] for r in db.conn.execute("select id from store_personas")} == {"layla", "noor", "james", "sara"}


def _make_sample(db) -> str:
    """A store sample copied from an approved product: raw row, catalog row (source sample, published) and its
    tags in a store-sample tag run, as the Live tag "Store sample" mode will write them."""
    c = db.conn
    src = one(db, "select product_id, current_run_id from catalog_products where is_published "
                  "and source = 'tagged' order by product_id limit 1")
    pid, run = src[0], src[1]
    new = "sample_test_" + pid
    cols = [r[0] for r in c.execute("select column_name from information_schema.columns where table_name = 'products' "
                                    "order by ordinal_position")]
    c.execute(f"insert into products ({', '.join(cols)}) select %s, {', '.join(cols[1:])} from products "
              f"where product_id = %s", (new, pid))
    sample_run = c.execute("""insert into tagging_runs (tagger_version, model, instructions_hash, started_at, finished_at,
                                  stage, model_profile, model_id, input_price, output_price, products_attempted,
                                  products_succeeded, cost_usd, purpose)
                              select tagger_version, model, instructions_hash, now(), now(), 'tag', model_profile, model_id,
                                  input_price, output_price, 1, 1, 0.05, 'store_sample'
                                from tagging_runs where tagging_run_id = (select ai_run_id from catalog_products
                                                                           where product_id = %s)
                              returning tagging_run_id""", (pid,)).fetchone()[0]
    ccols = [r[0] for r in c.execute("""select column_name from information_schema.columns
                                         where table_name = 'catalog_products' and column_name not in
                                         ('product_id', 'source', 'current_run_id', 'ai_run_id', 'approved_via')
                                         order by ordinal_position""")]
    c.execute(f"""insert into catalog_products (product_id, source, approved_via, {', '.join(ccols)})
                  select %s, 'sample', 'sample', {', '.join(ccols)} from catalog_products where product_id = %s""",
              (new, pid))
    tcols = [r[0] for r in c.execute("""select column_name from information_schema.columns
                                         where table_name = 'product_facet_tags' and column_name not in
                                         ('tag_id', 'product_id', 'tagging_run_id', 'review_outcome', 'review_was_flagged')
                                         order by ordinal_position""")]
    c.execute(f"""insert into product_facet_tags (product_id, tagging_run_id, {', '.join(tcols)})
                  select %s, %s, {', '.join(tcols)} from product_facet_tags
                   where product_id = %s and tagging_run_id = %s and status in ('auto', 'not_applicable')""",
              (new, sample_run, pid, run))
    c.execute("insert into tagging_run_products (tagging_run_id, product_id, status, category_id) "
              "select %s, %s, 'done', category_id from catalog_products where product_id = %s", (sample_run, new, pid))
    c.execute("update catalog_products set current_run_id = %s, ai_run_id = %s, is_published = true "
              "where product_id = %s", (sample_run, sample_run, new))
    return new


@needs_db
def test_a_store_sample_changes_no_tagger_figure_but_reaches_the_store(db):
    from app import metrics as M
    from app.listing import Filters, rows, summary
    from app.settings_tabs import confidence, models

    def figures():
        M.clear_cache()
        m = M.metrics(wait=True)
        return {
            "summary": summary(Filters()), "products": rows(Filters())["total"],
            "approved": rows(Filters(tab="approved"))["total"], "confidence": confidence(), "models": models(),
            "queue": one(db, "select count(*) from v_review_queue")[0],
            "costs": one(db, "select count(*), sum(cost_usd) from v_run_costs"),
            "metrics": {k: m[k] for k in ("top", "review", "cost", "scope")},
            "monitor": one(db, "select max(r.tagging_run_id) from tagging_runs r where r.stage = 'tag' "
                               "and not r.is_drill and r.purpose = 'tagger'")[0],
        }
    before = figures()
    new = _make_sample(db)
    assert one(db, "select count(*) from v_catalog_products where product_id = %s", (new,))[0] == 1   # the store sees it
    assert one(db, "select count(*) from v_product_review_state where product_id = %s", (new,))[0] == 0
    after = figures()
    for k in before:
        assert after[k] == before[k], k
    from tagger.steps import products_to_tag
    assert new not in products_to_tag(db, None, None, False)       # a tagger batch never re-tags it
