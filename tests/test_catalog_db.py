"""Live checks of the catalog migrations against the Supabase database (PRD v3 section 6).

Every test runs inside one transaction that is rolled back, so nothing is left behind.
Skipped when SUPABASE_DB_URL is not set or the catalog migrations and seed have not run.
"""
from __future__ import annotations

import csv
import os
from datetime import date, timedelta
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv

from scraper.store import MIGRATIONS, PRODUCT_COLUMNS, normalize_db_url

ROOT = Path(__file__).parents[1]
CONFIG = ROOT / "config"
NEW_TABLES = {"facets", "facet_values", "category_facets", "brands", "tagging_runs", "catalog_products",
              "product_facet_tags", "golden_products", "golden_tags"}
VIEWS = {"v_catalog_products", "v_product_facets", "v_review_queue", "v_invalid_tags"}
SCRAPER_COLUMNS = {
    "products": set(PRODUCT_COLUMNS),
    "product_images": {"product_id", "position", "source_url", "storage_path"},
    "categories": {"node_id", "parent_id", "level", "audience", "department", "name", "slug", "path",
                   "is_leaf", "definition", "aliases", "review_flag"},
    "product_rejects": {"id", "run_id", "product_id", "url", "reason", "payload", "rejected_at"},
    "scrape_runs": {"run_id", "started_at", "finished_at", "status", "args", "requests", "requests_by_host",
                    "cache_hits", "products_written", "rejects", "node_counts", "area_counts",
                    "field_fill_rates", "errors", "notes"},
}
SCRAPED_PRODUCTS = 241
# The test product's category must carry: occasion (judge, multi 1-3), bag_width_cm (text_only, number),
# price_band (derived), carry_style (judge, multi) and exclusivity (text_only, default none).
NEEDED = ("occasion", "bag_width_cm", "price_band", "carry_style", "exclusivity")


@pytest.fixture(scope="module")
def conn():
    load_dotenv(ROOT / ".env")
    url = os.environ.get("SUPABASE_DB_URL")
    if not url:
        pytest.skip("SUPABASE_DB_URL not set")
    c = psycopg.connect(normalize_db_url(url), autocommit=False)
    ready = c.execute("select count(*) from information_schema.columns where table_name = 'facets' "
                      "and column_name = 'on_missing'").fetchone()[0]
    if not ready or c.execute("select count(*) from facets").fetchone()[0] == 0:
        c.close()
        pytest.skip("run `catalog migrate` and `catalog seed-taxonomy` first")
    c.rollback()
    yield c
    c.close()


@pytest.fixture
def db(conn):
    yield conn
    conn.rollback()  # tests clean up after themselves


def one(db, sql, params=()):
    return db.execute(sql, params).fetchone()


def fails(db, sql, params=(), exc=psycopg.errors.IntegrityError, match=None):
    """Run sql in a savepoint and assert it is rejected."""
    with pytest.raises(exc, match=match):
        with db.transaction():
            db.execute(sql, params)


def add_catalog_row(db, pid, cat, current_run=None, cat_run=None, published=True):
    db.execute("""insert into catalog_products (product_id, category_id, category_confidence, category_status,
                     category_run_id, name, brand, price, currency, url, description, details, size_and_fit,
                     primary_image_path, current_run_id, is_published)
                  select product_id, %s, 0.95, 'auto', %s, name, brand, price, currency, url, description,
                         details, size_and_fit, product_id || '/1.jpg', %s, %s
                  from products where product_id = %s""", (cat, cat_run, current_run, published, pid))


def free(db, pids):
    """Make products uncategorised inside the test's rolled-back transaction: every scraped product may be
    categorised in the live data (all 244 were by 14 Nov 2026)."""
    db.execute("delete from tagging_run_products where product_id = any(%s)", (pids,))
    db.execute("delete from catalog_products where product_id = any(%s)", (pids,))


@pytest.fixture
def world(db):
    """One published product on a leaf, a categorise run and two tag runs."""
    pid, cat = one(db, f"""select p.product_id, p.spine_node_id from products p
                           join categories c on c.node_id = p.spine_node_id
                           where c.is_leaf and not exists (select 1 from golden_products gp where gp.product_id = p.product_id) and (select count(*) from category_facets cf
                                 where cf.category_id = c.node_id and cf.facet_id = any(%s)) = {len(NEEDED)}
                             
                           order by p.product_id limit 1""", (list(NEEDED),))
    free(db, [pid])
    run = lambda stage, v: one(db, "insert into tagging_runs (stage, tagger_version, model) "
                                   "values (%s,%s,'test') returning tagging_run_id", (stage, v))[0]
    cat_run, run1, run2 = run("categorise", "test-c1"), run("tag", "test-v1"), run("tag", "test-v2")
    add_catalog_row(db, pid, cat, current_run=run2, cat_run=cat_run)
    single = one(db, """select f.facet_id from category_facets cf join facets f using (facet_id)
                        where cf.category_id = %s and f.cardinality = 'single' and f.source_policy = 'judge'
                          and f.data_type <> 'number' order by f.sort_order limit 1""", (cat,))[0]
    not_applicable = one(db, """select facet_id from facets where status = 'active' and source_policy = 'judge'
                                and facet_id not in (select facet_id from category_facets where category_id = %s)
                                and exists (select 1 from facet_values v where v.facet_id = facets.facet_id)
                                order by sort_order limit 1""", (cat,))[0]

    def value(facet, n=0):
        return one(db, "select facet_value_id from facet_values where facet_id = %s "
                       "order by sort_order offset %s limit 1", (facet, n))[0]

    return dict(pid=pid, cat=cat, cat_run=cat_run, run1=run1, run2=run2, single=single,
                na=not_applicable, value=value)


def tag(db, w, facet, value_id=None, run=None, status="auto", confidence=0.9, number=None, text=None,
        evidence="image", quote=None, pid=None):
    db.execute("""insert into product_facet_tags (product_id, tagging_run_id, facet_id, facet_value_id,
                     value_number, value_text, confidence, evidence, evidence_quote, reason, status)
                  values (%s,%s,%s,%s,%s,%s,%s,%s,%s,'test',%s)""",
               (pid or w["pid"], run or w["run2"], facet, value_id, number, text,
                None if evidence == "rule" else confidence, evidence, quote, status))


INSERT_TAG = """insert into product_facet_tags (product_id, tagging_run_id, facet_id, facet_value_id,
                   value_number, value_text, evidence, status) values (%s,%s,%s,%s,%s,%s,%s,%s)"""


# ------------------------------------------------------------------ migration and seed

def test_migrations_create_objects_and_rerun(db):
    latest = sorted(MIGRATIONS.glob("*.sql"))[-1]
    db.execute(latest.read_text())                                 # rerun inside the test transaction
    tables = {r[0] for r in db.execute("select table_name from information_schema.tables "
                                       "where table_schema='public' and table_type='BASE TABLE'")}
    views = {r[0] for r in db.execute("select table_name from information_schema.views where table_schema='public'")}
    assert NEW_TABLES <= tables and VIEWS <= views
    rls = dict(db.execute("select relname, relrowsecurity from pg_class where relname = any(%s)",
                          (list(NEW_TABLES),)).fetchall())
    assert all(rls[t] for t in NEW_TABLES)
    applied = {r[0] for r in db.execute("select filename from schema_migrations")}
    assert {p.name for p in MIGRATIONS.glob("*.sql")} <= applied


def test_scraper_tables_unchanged(db):
    for table, expected in SCRAPER_COLUMNS.items():
        cols = {r[0] for r in db.execute("select column_name from information_schema.columns "
                                         "where table_schema='public' and table_name=%s", (table,))}
        assert cols == expected, table
    assert one(db, "select count(*) from products")[0] >= SCRAPED_PRODUCTS     # Live tag adds products


def _taxonomy():
    from catalog.taxonomy import apply_definitions, apply_policy, load_taxonomy
    tax = load_taxonomy(CONFIG / "ounass_facet_dictionary_v3_1_corrected.xlsx")
    apply_policy(tax, CONFIG / "facet_policy.csv")
    apply_definitions(tax, CONFIG / "facet_definitions.csv")
    return tax


def test_seed_is_idempotent_and_matches_prd(db):
    from catalog.seed import seed_taxonomy
    tax = _taxonomy()
    tables = ("facets", "facet_values", "category_facets", "brands")
    counts = lambda: [one(db, f"select count(*) from {t}")[0] for t in tables]
    before = counts()
    rep = seed_taxonomy(db, tax, commit=False)
    assert counts() == before
    assert rep.by_status == {"active": 91, "retired": 19}
    assert rep.by_policy == {"text_only": 40, "judge": 33, "text_first": 9, "derived": 7, "not_tagged": 2}
    assert rep.unlisted_facets == []
    # one dictionary row per Matrix_long row whose facet is active and node exists ...
    assert one(db, "select count(*) from category_facets where inherited_from is null")[0] == len(tax.applicability)
    # ... and nodes the dictionary does not list copy their nearest listed ancestor's rows
    assert one(db, """select count(*) from category_facets cf join categories c on c.node_id = cf.category_id
                      where cf.inherited_from is not null and c.level in ('L3', 'L4')""")[0] == 0
    assert one(db, """select count(*) from categories c where c.level not in ('L1', 'L2') and not exists
                      (select 1 from category_facets cf where cf.category_id = c.node_id)""")[0] == 0
    assert one(db, """select count(*) from category_facets cf where cf.inherited_from is not null and not exists
                      (select 1 from category_facets a where a.category_id = cf.inherited_from
                       and a.facet_id = cf.facet_id and a.is_required = cf.is_required
                       and a.inherited_from is null)""")[0] == 0
    assert one(db, "select count(*) from category_facets cf join facets f using (facet_id) "
                   "where f.status <> 'active'")[0] == 0
    assert one(db, "select count(*) from facet_values where facet_id = 'price_band'")[0] == 3
    assert one(db, """select count(*) from facets f where data_type in ('enum','ordinal','boolean')
                      and status = 'active'
                      and (select count(*) from facet_values v where v.facet_id = f.facet_id) < 2""")[0] == 0
    assert one(db, """select count(*) from products p where p.spine_node_id is not null and not exists
                      (select 1 from category_facets cf where cf.category_id = p.spine_node_id)""")[0] == 0


def test_brands_one_row_per_brand_and_exported(db):
    brands = {r[0] for r in db.execute("select brand from brands")}
    assert brands == {r[0] for r in db.execute("select distinct brand from products")}
    with (CONFIG / "brands.csv").open() as fh:
        assert {r["brand"] for r in csv.DictReader(fh)} == brands


def test_seed_refuses_to_drop_a_used_value(db, world):
    from catalog.seed import SeedError, seed_taxonomy
    tax = _taxonomy()
    facet = next(f for f in tax.facets if f.facet_id == world["single"])
    tag(db, world, facet.facet_id, world["value"](facet.facet_id, 0))
    facet.values = facet.values[1:]                     # the file "drops" the value just used
    with pytest.raises(SeedError, match="tags still use"):
        seed_taxonomy(db, tax, commit=False)


def test_launch_dates(db):
    from catalog.launch_dates import seed_launch_dates
    rows = db.execute("""select p.product_id, p.spine_node_id from products p
                         join categories c on c.node_id = p.spine_node_id where c.is_leaf and not exists (select 1 from golden_products gp where gp.product_id = p.product_id)
                           
                         order by p.product_id limit 60""").fetchall()
    free(db, [r[0] for r in rows])
    for pid, cat in rows:
        add_catalog_row(db, pid, cat, published=False)
    kept = rows[0][0]
    db.execute("update catalog_products set launch_date = '2020-01-01' where product_id = %s", (kept,))
    today = date(2026, 11, 13)
    first = seed_launch_dates(db, today=today, commit=False)
    assert first["set"] == len(rows) - 1 and first["in_last_30_days"] == 25
    assert one(db, "select count(*) from catalog_products where launch_date is null")[0] == 0
    assert one(db, "select launch_date, launch_date_is_dummy from catalog_products where product_id = %s",
               (kept,)) == (date(2020, 1, 1), False)
    oldest = one(db, "select min(launch_date) from catalog_products where launch_date_is_dummy")[0]
    assert oldest >= today - timedelta(days=400)
    snapshot = db.execute("select product_id, launch_date from catalog_products order by 1").fetchall()
    assert seed_launch_dates(db, today=today, commit=False)["set"] == 0
    assert db.execute("select product_id, launch_date from catalog_products order by 1").fetchall() == snapshot


# ------------------------------------------------------------------ constraints

def test_value_from_another_facet_fails(db, world):
    other = one(db, "select facet_value_id from facet_values where facet_id <> %s limit 1", (world["single"],))[0]
    fails(db, INSERT_TAG, (world["pid"], world["run2"], world["single"], other, None, None, "image", "auto"),
          psycopg.errors.ForeignKeyViolation)


def test_facet_not_applicable_to_category_fails(db, world):
    fails(db, INSERT_TAG, (world["pid"], world["run2"], world["na"], world["value"](world["na"]), None, None,
                           "image", "auto"), psycopg.errors.CheckViolation, match="does not apply")


def test_value_columns_follow_status(db, world):
    fails(db, INSERT_TAG, (world["pid"], world["run2"], world["single"], None, None, None, "image", "auto"),
          psycopg.errors.CheckViolation)                                  # auto with no value
    for status in ("abstained", "not_applicable"):
        fails(db, INSERT_TAG, (world["pid"], world["run2"], world["single"], world["value"](world["single"]),
                               None, None, "image", status), psycopg.errors.CheckViolation)
    tag(db, world, world["single"], None, status="review")                # review, no value: a person fills it
    tag(db, world, "occasion", None, status="abstained")
    tag(db, world, "carry_style", None, status="not_applicable")
    queue = one(db, "select needs_value from v_review_queue where product_id = %s and facet_id = %s",
                (world["pid"], world["single"]))
    assert queue == (True,)


def test_number_values(db, world):
    tag(db, world, "bag_width_cm", number=32.5, evidence="text", quote="Width: 32.5cm")
    fails(db, INSERT_TAG, (world["pid"], world["run1"], "bag_width_cm", None, 30, "30 cm", "text", "auto"),
          psycopg.errors.CheckViolation)                                  # two value columns
    fails(db, INSERT_TAG, (world["pid"], world["run1"], "occasion", None, 3, None, "image", "auto"),
          psycopg.errors.CheckViolation, match="wrong column")
    assert one(db, "select display_value from v_product_facets where product_id = %s "
                   "and facet_id = 'bag_width_cm'", (world["pid"],)) == ("32.5 cm",)


def test_who_may_write_what(db, world):
    for facet, reason in (("jewellery_tier", "not_tagged"), ("silhouette", "retired")):
        fails(db, INSERT_TAG, (world["pid"], world["run2"], facet, world["value"](facet), None, None, "image", "auto"),
              psycopg.errors.CheckViolation, match="not taggable")
    band = world["value"]("price_band")
    fails(db, INSERT_TAG, (world["pid"], world["run2"], "price_band", band, None, None, "image", "auto"),
          psycopg.errors.CheckViolation, match="derived")                 # derived without rule
    tag(db, world, "price_band", band, evidence="rule")                   # derived with rule: succeeds
    fails(db, INSERT_TAG, (world["pid"], world["run2"], "bag_width_cm", None, 30, None, "image", "auto"),
          psycopg.errors.CheckViolation, match="text_only")               # text_only from a photo
    # code also writes rule rows on non-derived facets: on_missing defaults and local_designer
    none = one(db, "select facet_value_id from facet_values where facet_id = 'exclusivity' and value = 'none'")[0]
    tag(db, world, "exclusivity", none, evidence="rule")


def test_confidence_and_status_ranges(db, world):
    v = world["value"](world["single"])
    for confidence, status in ((1.5, "auto"), (-0.1, "auto"), (0.5, "approved")):
        fails(db, """insert into product_facet_tags (product_id, tagging_run_id, facet_id, facet_value_id,
                        confidence, evidence, status) values (%s,%s,%s,%s,%s,'image',%s)""",
              (world["pid"], world["run2"], world["single"], v, confidence, status), psycopg.errors.CheckViolation)
    fails(db, "update catalog_products set category_status = 'maybe' where product_id = %s", (world["pid"],),
          psycopg.errors.CheckViolation)


def test_runs_have_the_right_stage(db, world):
    fails(db, INSERT_TAG, (world["pid"], world["cat_run"], world["single"], world["value"](world["single"]),
                           None, None, "image", "auto"), psycopg.errors.CheckViolation, match="not a tag or review run")
    fails(db, "update catalog_products set current_run_id = %s where product_id = %s",
          (world["cat_run"], world["pid"]), psycopg.errors.CheckViolation)
    fails(db, "update catalog_products set category_run_id = %s where product_id = %s",
          (world["run1"], world["pid"]), psycopg.errors.CheckViolation)


def test_no_tags_while_category_in_review(db, world):
    db.execute("update catalog_products set category_status = 'review' where product_id = %s", (world["pid"],))
    fails(db, INSERT_TAG, (world["pid"], world["run2"], world["single"], world["value"](world["single"]),
                           None, None, "image", "auto"), psycopg.errors.CheckViolation, match="category status review")
    db.execute("update catalog_products set category_status = 'flagged' where product_id = %s", (world["pid"],))
    with db.transaction():          # flagged: tagged, only the category waits for review (migration 013)
        db.execute(INSERT_TAG, (world["pid"], world["run2"], world["single"], world["value"](world["single"]),
                                None, None, "text", "auto"))
        assert one(db, "select count(*) from v_review_queue where item_type = 'category' and product_id = %s",
                   (world["pid"],))[0] == 1


def test_catalog_category_must_be_leaf_or_parent_with_facets_and_brand_known(db, world):
    bare = one(db, """select node_id from categories c where not is_leaf and not exists
                      (select 1 from category_facets cf where cf.category_id = c.node_id) limit 1""")[0]
    fails(db, "update catalog_products set category_id = %s where product_id = %s", (bare, world["pid"]),
          psycopg.errors.CheckViolation)
    parent = one(db, """select node_id from categories c where not is_leaf and name not ilike 'other %%'
                        and exists (select 1 from category_facets cf where cf.category_id = c.node_id
                                    and cf.inherited_from is null) limit 1""")[0]
    with db.transaction():          # a parent with its own facets is accepted (migration 012)
        db.execute("update catalog_products set category_id = %s where product_id = %s", (parent, world["pid"]))
        db.execute("update catalog_products set category_id = %s where product_id = %s", (world["cat"], world["pid"]))
    fails(db, "update catalog_products set brand = 'No Such Brand' where product_id = %s", (world["pid"],),
          psycopg.errors.ForeignKeyViolation)


def test_golden_tags_record_any_answer(db, world):
    db.execute("insert into golden_products (product_id, category_id, labelled_by) values (%s,%s,'test') "
               "on conflict (product_id) do update set category_id = excluded.category_id",   # may be sampled
               (world["pid"], world["cat"]))
    ins = ("insert into golden_tags (product_id, facet_id, facet_value_id, value_number, value_text, status) "
           "values (%s,%s,%s,%s,%s,%s)")
    db.execute(ins, (world["pid"], world["single"], world["value"](world["single"]), None, None, "value"))
    db.execute(ins, (world["pid"], "bag_width_cm", None, 32.5, None, "value"))
    db.execute(ins, (world["pid"], "carry_style", None, None, None, "unknown"))
    db.execute(ins, (world["pid"], "occasion", None, None, None, "not_applicable"))
    fails(db, ins, (world["pid"], "price_band", world["value"]("price_band"), None, None, "unknown"),
          psycopg.errors.CheckViolation)
    fails(db, ins, (world["pid"], world["single"], world["value"](world["single"]), None, None, "value"),
          psycopg.errors.UniqueViolation)


# ------------------------------------------------------------------ views

def test_product_facets_shows_current_run_auto_only(db, world):
    s, occ = world["single"], "occasion"
    tag(db, world, s, world["value"](s, 0), run=world["run1"])            # old run
    tag(db, world, s, world["value"](s, 1), run=world["run2"])            # current run, auto
    tag(db, world, occ, world["value"](occ, 0), run=world["run2"], status="review")
    tag(db, world, occ, None, run=world["run1"], status="abstained")
    rows = db.execute("select facet_id, value, tagging_run_id from v_product_facets where product_id = %s",
                      (world["pid"],)).fetchall()
    second = one(db, "select value from facet_values where facet_value_id = %s", (world["value"](s, 1),))[0]
    assert rows == [(s, second, world["run2"])]
    queue = db.execute("select item_type, facet_id from v_review_queue where product_id = %s",
                       (world["pid"],)).fetchall()
    assert queue == [("tag", occ)]
    db.execute("update catalog_products set current_run_id = %s where product_id = %s", (world["run1"], world["pid"]))
    assert [r[2] for r in db.execute("select facet_id, value, tagging_run_id from v_product_facets "
                                     "where product_id = %s", (world["pid"],))] == [world["run1"]]


def test_unpublished_product_is_in_no_website_view(db, world):
    tag(db, world, world["single"], world["value"](world["single"]))
    assert one(db, "select count(*) from v_catalog_products where product_id = %s", (world["pid"],))[0] == 1
    for change in ("is_published = false", "category_status = 'review', is_published = true"):
        with db.transaction(force_rollback=True):
            db.execute(f"update catalog_products set {change} where product_id = %s", (world["pid"],))
            for view in ("v_catalog_products", "v_product_facets"):
                assert one(db, f"select count(*) from {view} where product_id = %s", (world["pid"],))[0] == 0
    db.execute("update catalog_products set category_status = 'review' where product_id = %s", (world["pid"],))
    assert one(db, "select item_type from v_review_queue where product_id = %s", (world["pid"],))[0] == "category"


def _rules(db, pid):
    return {r[0] for r in db.execute("select rule from v_invalid_tags where product_id = %s", (pid,))}


def test_invalid_tags_catches_breaches_and_is_empty(db, world):
    s, occ = world["single"], "occasion"
    with db.transaction(force_rollback=True):
        tag(db, world, s, world["value"](s, 0))
        tag(db, world, s, world["value"](s, 1))
        assert "single_facet_multiple_auto" in _rules(db, world["pid"])
    with db.transaction(force_rollback=True):
        for n in range(4):                                                 # occasion allows 1-3 values
            tag(db, world, occ, world["value"](occ, n))
        assert _rules(db, world["pid"]) == {"too_many_values"}
    with db.transaction(force_rollback=True):
        tag(db, world, "bag_width_cm", number=30, evidence="text")        # text evidence, no quote
        assert _rules(db, world["pid"]) == {"text_without_quote"}
    with db.transaction(force_rollback=True):
        tag(db, world, occ, world["value"](occ, 0))
        new_cat = one(db, """select c.node_id from categories c where c.is_leaf and not exists
                             (select 1 from category_facets cf where cf.category_id = c.node_id
                              and cf.facet_id = 'occasion') limit 1""")[0]
        db.execute("update catalog_products set category_id = %s where product_id = %s", (new_cat, world["pid"]))
        assert _rules(db, world["pid"]) == {"facet_not_applicable"}
    tag(db, world, s, world["value"](s, 0))
    tag(db, world, occ, world["value"](occ, 0))
    tag(db, world, occ, world["value"](occ, 1))
    tag(db, world, "bag_width_cm", number=30, evidence="text", quote="Width 30cm")
    tag(db, world, "price_band", world["value"]("price_band"), evidence="rule")
    tag(db, world, "carry_style", None, status="review")
    assert one(db, "select count(*) from v_invalid_tags")[0] == 0


def test_none_exclusive(db):
    """`none` beside another value on a none-exclusive facet is listed (checked on stone)."""
    pid, cat = one(db, """select p.product_id, p.spine_node_id from products p
                          join categories c on c.node_id = p.spine_node_id
                          join category_facets cf on cf.category_id = c.node_id and cf.facet_id = 'stone'
                          where c.is_leaf and not exists (select 1 from golden_products gp where gp.product_id = p.product_id)
                            
                          limit 1""")
    free(db, [pid])
    run = one(db, "insert into tagging_runs (stage, tagger_version, model) values ('tag','t','t') "
                  "returning tagging_run_id")[0]
    add_catalog_row(db, pid, cat, current_run=run)
    for value in ("none", "diamond"):
        db.execute("""insert into product_facet_tags (product_id, tagging_run_id, facet_id, facet_value_id,
                         evidence, evidence_quote, status) select %s, %s, 'stone', facet_value_id, 'text', 'quote', 'auto'
                      from facet_values where facet_id = 'stone' and value = %s""", (pid, run, value))
    assert _rules(db, pid) == {"exclusive_with_other_values"}


def test_delete_catalog_row_cascades_tags_only(db, world):
    tag(db, world, world["single"], world["value"](world["single"]))
    db.execute("insert into golden_products (product_id, category_id, labelled_by) values (%s,%s,'test') "
               "on conflict (product_id) do update set category_id = excluded.category_id",   # may be sampled
               (world["pid"], world["cat"]))
    db.execute("insert into golden_tags (product_id, facet_id, facet_value_id, status) values (%s,%s,%s,'value')",
               (world["pid"], world["single"], world["value"](world["single"])))
    db.execute("delete from catalog_products where product_id = %s", (world["pid"],))
    assert one(db, "select count(*) from product_facet_tags where product_id = %s", (world["pid"],))[0] == 0
    assert one(db, "select count(*) from products where product_id = %s", (world["pid"],))[0] == 1
    assert one(db, "select count(*) from golden_tags where product_id = %s", (world["pid"],))[0] == 1


# ------------------------------------------------------------------ access

def test_public_key_reads_views_not_raw_or_golden(db, world):
    tag(db, world, world["single"], world["value"](world["single"]))
    db.execute("set local role anon")
    assert one(db, "select count(*) from v_catalog_products where product_id = %s", (world["pid"],))[0] == 1
    assert one(db, "select count(*) from v_product_facets where product_id = %s", (world["pid"],))[0] == 1
    assert one(db, "select count(*) from facet_values")[0] > 0
    assert one(db, "select count(*) from products")[0] == 0           # RLS: no policy for anon
    assert one(db, "select count(*) from product_images")[0] == 0
    for table in ("golden_tags", "golden_products", "tagging_runs", "brands"):
        fails(db, f"select * from {table} limit 1", exc=psycopg.errors.InsufficientPrivilege)
    fails(db, "insert into facets (facet_id, name, cardinality, kind, data_type, source_policy, on_missing) "
              "values ('x','x','single','objective','enum','judge','unknown')",
          exc=psycopg.errors.InsufficientPrivilege)
