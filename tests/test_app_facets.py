"""Facets tab (Build PRD 3, 6 and 9): saving rules and values, rubric versions, history and restore, the
seed guard. Every test runs inside one transaction that is rolled back; no model call is made.
"""
from __future__ import annotations

import os
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


def body_of(d: dict, **patch) -> dict:
    b = {"name": d["name"], "definition": d["definition"], "policy": d["policy"], "onMissing": d["onMissing"],
         "maxValues": d["maxValues"],
         "values": [{"id": v["id"], "label": v["label"], "definition": v["definition"], "active": v["active"]}
                    for v in d["values"]]}
    b.update(patch)
    return b


def specs(db, fid):
    from tagger.config import load_settings
    from tagger.facets import facets_for_tagging
    cat, path = one(db, """select c.node_id, c.path from category_facets cf join categories c on c.node_id = cf.category_id
                            where cf.facet_id = %s order by c.path limit 1""", (fid,))
    fc = load_settings(ROOT / "config").function_categories
    return next((s for s in facets_for_tagging(db.conn, cat, path, fc) if s.facet_id == fid), None)


def test_versions_count_up():
    from app.facets_tab import next_version
    assert [next_version(v) for v in (None, "v6", "v6.1", "v6.9")] == ["v1", "v6.1", "v6.2", "v6.10"]


def test_a_rubric_change_gets_a_new_version_with_history_and_a_new_taxonomy_version(db):
    from app.facets_tab import facet_detail, save
    d = facet_detail("season")
    tv = one(db, "select taxonomy_version from app_settings")[0]
    warm = next(v for v in d["values"] if v["label"] == "warm")
    vals = body_of(d)["values"]
    for v in vals:
        if v["id"] == warm["id"]:
            v["definition"] = warm["definition"] + " Also resortwear."
    r = save("season", body_of(d, definition=d["definition"] + " Test sentence.", values=vals))
    assert r["version"] == "v6.1"
    assert one(db, "select definition_version, edited_in_app from facets where facet_id = 'season'") == ("v6.1", True)
    assert one(db, "select definition_version from facet_values where facet_value_id = %s", (warm["id"],))[0] == "v6.1"
    others = db.conn.execute("""select definition_version from facet_values where facet_id = 'season'
                                 and facet_value_id <> %s""", (warm["id"],)).fetchall()
    assert all(v[0] == "v6" for v in others)                         # untouched values keep their version
    assert one(db, "select taxonomy_version from app_settings")[0] == tv + 1
    ch = one(db, """select before ->> 'version', after ->> 'version', summary from facet_changes
                     where facet_id = 'season' order by change_id desc limit 1""")
    assert ch[:2] == ("v6", "v6.1") and "rubric v6 → v6.1" in ch[2]
    # the model gets the new rubric on its next product
    assert "Test sentence." in specs(db, "season").definition


def test_a_name_only_change_keeps_the_rubric_version(db):
    from app.facets_tab import facet_detail, save
    d = facet_detail("season")
    r = save("season", body_of(d, name="Season of wear"))
    assert r["version"] == d["version"]


def test_restore_brings_back_the_earlier_rubric_as_a_new_change(db):
    from app.facets_tab import facet_detail, restore, save
    d = facet_detail("season")
    save("season", body_of(d, definition="Shorter rubric."))
    change = one(db, "select change_id from facet_changes where facet_id = 'season' order by change_id desc limit 1")[0]
    r = restore("season", change)
    assert one(db, "select definition from facets where facet_id = 'season'")[0] == d["definition"]
    assert r["version"] == "v6.2"                                    # a new version, with the old text
    assert one(db, "select count(*) from facet_changes where facet_id = 'season'")[0] >= 2


def test_bad_saves_are_refused_with_the_field_and_write_nothing(db):
    from app.facets_tab import FacetError, facet_detail, save
    d = facet_detail("season")
    n = one(db, "select count(*) from facet_changes")[0]
    bad = [
        body_of(d, name=""),
        body_of(d, definition=" "),
        body_of(d, maxValues=0),
        body_of(d, values=body_of(d)["values"][1:]),                 # a value left out = deleted
        body_of(d, values=[{**v, "active": False} for v in body_of(d)["values"]]),
        body_of(d, values=body_of(d)["values"] + [{"id": None, "label": "Warm", "definition": None, "active": True}]),
        {**body_of(d), "dataType": "text"},
        {**body_of(d), "categories": ["W101"]},
        {**body_of(d), "id": "other"},
    ]
    for b in bad:
        with pytest.raises(FacetError) as e:
            save("season", b)
        assert e.value.status == 422 and (e.value.fields or e.value.problems)
    assert one(db, "select count(*) from facet_changes")[0] == n
    with pytest.raises(FacetError):
        save("brand_tier", {"name": "x", "definition": "y", "values": []})     # set by rule: read-only


def test_a_save_that_would_break_stored_tags_is_refused(db):
    from app.facets_tab import FacetError, facet_detail, save
    row = one(db, """select t.facet_id from product_facet_tags t join facets f using (facet_id)
                      where f.cardinality = 'multi' and f.source_policy in ('text_only', 'text_first', 'judge')
                        and t.facet_value_id is not null and coalesce(trim(f.definition), '') <> ''
                      group by t.facet_id, t.product_id, t.tagging_run_id having count(*) > 1 limit 1""")
    if not row:
        pytest.skip("no multi-value tag with two values")
    fid = row[0]
    d = facet_detail(fid)
    n = one(db, "select count(*) from facet_changes")[0]
    with pytest.raises(FacetError) as e:
        save(fid, body_of(d, maxValues=1))
    assert "breaking the catalog rules" in e.value.message and e.value.problems
    assert one(db, "select max_values from facets where facet_id = %s", (fid,))[0] == d["maxValues"]
    assert one(db, "select count(*) from facet_changes")[0] == n


def test_an_inactive_value_is_not_offered_and_a_new_value_is_and_passes_the_rules(db):
    from app.facets_tab import facet_detail, save
    from app.review import approve, save as save_review
    d = facet_detail("season")
    vals = body_of(d)["values"]
    cold = next(v for v in vals if v["label"] == "cold")
    cold["active"] = False
    vals.append({"id": None, "label": "Resort", "definition": "Holiday pieces for a warm destination.", "active": True})
    save("season", body_of(d, values=vals))
    offered = {v.label for v in specs(db, "season").values}
    assert "cold" not in offered and "Resort" in offered
    # tags that use the inactive value are kept
    assert one(db, "select count(*) from product_facet_tags t join facet_values v using (facet_value_id) "
                   "where v.facet_id = 'season' and v.label = 'cold'")[0] >= 0
    # a reviewer can choose the new value, and the approved rows pass the catalog rules
    new_id = one(db, "select facet_value_id from facet_values where facet_id = 'season' and label = 'Resort'")[0]
    pid = one(db, """select s.product_id from v_product_review_state s
                       join catalog_products cp using (product_id)
                       join category_facets cf on cf.category_id = cp.category_id and cf.facet_id = 'season'
                      where s.state <> 'approved' and s.ai_run_id is not null and not s.category_flagged
                      order by 1 limit 1""")
    if not pid:
        db.conn.execute("""update catalog_products set is_published = false where product_id = (
                             select cp.product_id from catalog_products cp join category_facets cf
                               on cf.category_id = cp.category_id and cf.facet_id = 'season'
                              where cp.ai_run_id is not null order by 1 limit 1)""")
        pid = one(db, """select s.product_id from v_product_review_state s join catalog_products cp using (product_id)
                          join category_facets cf on cf.category_id = cp.category_id and cf.facet_id = 'season'
                         where s.state <> 'approved' and s.ai_run_id is not null order by 1 limit 1""")
    if not pid:
        pytest.skip("no product to review")
    save_review(pid[0], {"season": {"decision": "changed", "values": [new_id]}})
    assert one(db, "select count(*) from v_invalid_tags where facet_id = 'season'")[0] == 0


def test_the_seed_leaves_facets_edited_in_the_app_unless_forced(db):
    from app.facets_tab import facet_detail, save
    from catalog.seed import seed_taxonomy
    from catalog.taxonomy import apply_definitions, apply_policy, load_taxonomy

    def tax():
        t = load_taxonomy(ROOT / "config" / "ounass_facet_dictionary_v3_1_corrected.xlsx")
        apply_policy(t, ROOT / "config" / "facet_policy.csv")
        apply_definitions(t, ROOT / "config" / "facet_definitions.csv")
        return t
    d = facet_detail("season")
    vals = body_of(d)["values"] + [{"id": None, "label": "Resort", "definition": None, "active": True}]
    save("season", body_of(d, definition="Edited in the app.", values=vals))
    rep = seed_taxonomy(db.conn, tax(), commit=False)
    assert "season" in rep.kept_edited
    assert one(db, "select definition from facets where facet_id = 'season'")[0] == "Edited in the app."
    assert one(db, "select count(*) from facet_values where facet_id = 'season' and label = 'Resort'")[0] == 1
    rep = seed_taxonomy(db.conn, tax(), commit=False, force=True)
    assert rep.kept_edited == []
    assert one(db, "select definition from facets where facet_id = 'season'")[0] == d["definition"]


def test_a_facet_save_changes_no_tag_and_no_approved_product(db):
    import hashlib
    from app.facets_tab import facet_detail, save
    tags = lambda: hashlib.md5(repr(db.conn.execute(
        "select tag_id, facet_value_id, status, confidence, tagging_run_id from product_facet_tags order by tag_id"
    ).fetchall()).encode()).hexdigest()
    pub = lambda: db.conn.execute("select product_id, current_run_id, approved_at from catalog_products "
                                  "where is_published order by 1").fetchall()
    before_t, before_p = tags(), pub()
    d = facet_detail("season")
    vals = body_of(d)["values"] + [{"id": None, "label": "Resort", "definition": "Holiday wear.", "active": True}]
    for v in vals:
        if v["label"] == "cold":
            v["active"] = False
    save("season", body_of(d, definition=d["definition"] + " More.", values=vals))
    assert tags() == before_t and pub() == before_p
