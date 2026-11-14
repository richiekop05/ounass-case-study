"""Confidence and Models tabs (Build PRD 9): levels, previews, applying, model choice.

Every test runs inside one transaction that is rolled back (the app's shared connection is replaced by a
Db with commit=False), so no level or model choice is really changed. No model call is made: tagging the
held products is replaced by a stub.
"""
from __future__ import annotations

import hashlib
import os
import tomllib
from decimal import Decimal
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


def tags_hash(db):
    rows = db.conn.execute("select tag_id, status, confidence, facet_value_id, review_reason, tagging_run_id "
                           "from product_facet_tags order by tag_id").fetchall()
    return hashlib.md5(repr(rows).encode()).hexdigest()


def flagged(db, pid, run, fid):
    r = one(db, "select flagged from v_tag_flags where product_id = %s and tagging_run_id = %s and facet_id = %s",
            (pid, run, fid))
    return r[0] if r else None


def a_low_confidence_tag(db):
    """A model-written value below 1.0, flagged for nothing but its confidence, on the tagger's latest run."""
    return one(db, """
        select t.product_id, t.tagging_run_id, t.facet_id, min(t.confidence)
          from product_facet_tags t join catalog_products cp on cp.product_id = t.product_id
                                                             and cp.ai_run_id = t.tagging_run_id
          join facets f on f.facet_id = t.facet_id and f.status = 'active'
                       and f.source_policy in ('text_only', 'text_first', 'judge') and f.default_value is null
         where t.confidence between 0.3 and 0.95 and t.evidence is distinct from 'rule'
           and num_nonnulls(t.facet_value_id, t.value_number, t.value_text) > 0
         group by t.product_id, t.tagging_run_id, t.facet_id
        having bool_and(t.status <> 'review' or coalesce(t.review_reason, 'low_confidence') = 'low_confidence')
         order by 1, 3 limit 1""")


# ------------------------------------------------------------------ levels and flags

def test_a_tag_is_flagged_below_its_level_and_not_above(db):
    from app.settings_tabs import apply
    row = a_low_confidence_tag(db)
    if not row:
        pytest.skip("no suitable tag")
    pid, run, fid, conf = row
    apply({"levels": {fid: float(conf) + 0.05}})
    assert flagged(db, pid, run, fid) is True
    apply({"levels": {fid: float(conf) - 0.05}})
    assert flagged(db, pid, run, fid) is False


def test_a_tag_in_review_for_another_reason_is_flagged_at_any_level(db):
    from app.settings_tabs import apply
    row = one(db, """select t.product_id, t.tagging_run_id, t.facet_id from product_facet_tags t
                       join facets f on f.facet_id = t.facet_id and f.status = 'active'
                                    and f.source_policy in ('text_only', 'text_first', 'judge')
                      where t.status = 'review' and t.review_reason in ('missing_required', 'quote_not_found',
                                                                          'text_policy')
                        and t.evidence is distinct from 'rule' limit 1""")
    if not row:
        pytest.skip("no tag in review for a reason other than confidence")
    pid, run, fid = row
    apply({"levels": {fid: 0.0}})
    assert flagged(db, pid, run, fid) is True


def test_a_facet_without_its_own_level_follows_the_default_and_one_with_a_level_does_not(db):
    from app.settings_tabs import apply
    follow = one(db, """select facet_id from facets where status = 'active' and review_threshold is null
                          and source_policy in ('text_only', 'text_first', 'judge') limit 1""")[0]
    own = one(db, """select facet_id, review_threshold from facets where status = 'active'
                       and review_threshold is not null limit 1""")
    if not own:
        db.conn.execute("update facets set review_threshold = 0.7 where facet_id = 'occasion'")
        own = ("occasion", Decimal("0.70"))
    apply({"default": 0.55})
    level = lambda f: one(db, "select coalesce(review_threshold, (select default_review_threshold from app_settings)) "
                              "from facets where facet_id = %s", (f,))[0]
    assert level(follow) == Decimal("0.55")
    assert level(own[0]) == own[1]


def test_applying_levels_changes_no_tag_and_no_approved_product_and_no_stored_outcome(db):
    from app.settings_tabs import apply
    before = tags_hash(db)
    pub = db.conn.execute("select product_id, current_run_id, approved_at from catalog_products "
                          "where is_published order by 1").fetchall()
    outcomes = db.conn.execute("select * from v_review_outcome_rows order by 1, 2").fetchall()
    states = dict(db.conn.execute("select product_id, state from v_product_review_state where state = 'approved'"))
    apply({"default": 0.3, "category": 0.95, "levels": {"occasion": 0.99}})
    assert tags_hash(db) == before
    assert db.conn.execute("select product_id, current_run_id, approved_at from catalog_products "
                           "where is_published order by 1").fetchall() == pub
    assert db.conn.execute("select * from v_review_outcome_rows order by 1, 2").fetchall() == outcomes
    assert dict(db.conn.execute("select product_id, state from v_product_review_state "
                                "where state = 'approved'")) == states


def test_the_pipeline_reads_a_changed_level_on_its_next_run(db):
    """facets_for_tagging reads the level from the database each time: no restart, no config edit."""
    from app.settings_tabs import apply
    from tagger.config import load_settings
    from tagger.facets import facets_for_tagging
    cat, path, fid = one(db, """select c.node_id, c.path, cf.facet_id from category_facets cf
                                 join categories c on c.node_id = cf.category_id
                                 join facets f on f.facet_id = cf.facet_id and f.status = 'active'
                                              and f.source_policy in ('text_only', 'text_first', 'judge')
                                where exists (select 1 from catalog_products cp where cp.category_id = c.node_id)
                                limit 1""")
    fc = load_settings(ROOT / "config").function_categories
    apply({"levels": {fid: 0.42}})
    spec = next(s for s in facets_for_tagging(db.conn, cat, path, fc) if s.facet_id == fid)
    assert spec.auto_threshold == pytest.approx(0.42)


# ------------------------------------------------------------------ figures and previews

def test_figures_agree_with_the_live_state_view(db):
    from app.settings_tabs import confidence
    c = confidence()
    ready, review = one(db, """select count(*) filter (where state = 'ready'),
                                      count(*) filter (where state = 'needs_review') from v_product_review_state""")
    assert c["products"] == {"ready": ready, "needsReview": review}
    flags = dict(db.conn.execute("""select f, count(*) from v_product_review_state s, unnest(s.flagged_facets) f
                                     where s.state <> 'approved' group by f"""))
    for r in c["facets"]:
        if not r["rule"]:
            assert r["needsReview"] == flags.get(r["id"], 0), r["id"]


def test_a_category_filter_gives_the_figures_worked_out_by_hand(db):
    from app.settings_tabs import confidence
    node = one(db, """select n.node_id from categories n
                       where (select count(*) from catalog_products cp join categories c on c.node_id = cp.category_id
                               where cp.ai_run_id is not null and (c.path = n.path or c.path like n.path || ' / %%'))
                             between 3 and 15 order by n.path limit 1""")
    if not node:
        pytest.skip("no small category")
    node = node[0]
    c = confidence(node)
    prods = db.conn.execute("""select cp.product_id, cp.ai_run_id, cp.category_id from catalog_products cp
                                join categories c on c.node_id = cp.category_id
                                join categories n on n.node_id = %s
                               where cp.ai_run_id is not null and (c.path = n.path or c.path like n.path || ' / %%')""",
                            (node,)).fetchall()
    dflt = float(one(db, "select default_review_threshold from app_settings")[0])
    for r in c["facets"]:
        if r["rule"]:
            continue
        own = one(db, "select review_threshold, default_value from facets where facet_id = %s", (r["id"],))
        level = float(own[0]) if own[0] is not None else dflt
        applicable = conf = unknown = 0
        for pid, run, cat in prods:
            if not one(db, "select 1 from category_facets where category_id = %s and facet_id = %s", (cat, r["id"])):
                continue
            applicable += 1
            rows = db.conn.execute("""select t.confidence, num_nonnulls(t.facet_value_id, t.value_number, t.value_text) > 0,
                                             t.evidence, fv.value, fv.label
                                        from product_facet_tags t left join facet_values fv using (facet_value_id)
                                       where t.product_id = %s and t.tagging_run_id = %s and t.facet_id = %s""",
                                   (pid, run, r["id"])).fetchall()
            values = [x for x in rows if x[1]]
            if not values:
                unknown += 1
                continue
            scored = [float(x[0]) for x in values if x[0] is not None and x[2] != "rule"
                      and not (own[1] is not None and own[1] in (x[3], x[4]))]
            conf += (not scored) or min(scored) >= level
        assert r["confident"]["den"] == applicable and r["confident"]["num"] == conf, r["id"]
        assert r["unknown"]["num"] == unknown, r["id"]


def test_a_preview_writes_nothing_and_predicts_the_moves(db):
    from app.settings_tabs import apply, preview
    before = tags_hash(db)
    settings = one(db, "select * from app_settings")
    p = preview({"default": 0.6})
    assert tags_hash(db) == before and one(db, "select * from app_settings") == settings
    old = dict(db.conn.execute("select product_id, state from v_product_review_state where state <> 'approved'"))
    apply({"default": 0.6})
    new = dict(db.conn.execute("select product_id, state from v_product_review_state where state <> 'approved'"))
    assert p["toReady"] == sum(old[k] == "needs_review" and new.get(k) == "ready" for k in old)
    assert p["toReview"] == sum(old[k] == "ready" and new.get(k) == "needs_review" for k in old)


def test_lowering_the_category_level_accepts_and_tags_the_held_products_that_clear_it(db, monkeypatch):
    import app.settings_tabs as S
    pid = one(db, "select product_id from catalog_products order by is_published, product_id limit 1")[0]
    db.conn.execute("update catalog_products set is_published = false, approved_at = null where product_id = %s",
                    (pid,))                                  # every product may be approved: send one back here
    db.conn.execute("""update catalog_products set category_status = 'review', ai_run_id = null, current_run_id = null,
                         category_confidence = 0.62, category_reviewed_at = null where product_id = %s""", (pid,))
    tagged = []
    monkeypatch.setattr(S, "_tag_held", lambda pids: tagged.extend(pids) or 99)
    monkeypatch.setattr(S.jobs, "busy", lambda: False)
    p = S.preview({"category": 0.6})
    assert p["heldToTag"] >= 1 and p["tagCostUsd"] > 0
    r = S.apply({"category": 0.6})
    assert pid in tagged and r["heldTagged"] == len(tagged) and r["jobId"] == 99
    assert one(db, "select category_status from catalog_products where product_id = %s", (pid,))[0] == "auto"
    assert r["previous"]["category"] == pytest.approx(0.75, abs=0.5)
    # raising it holds nothing new and tags nothing
    tagged.clear()
    S.apply({"category": 0.9})
    assert tagged == []


def test_bad_levels_are_refused_with_the_reason(db):
    from app.settings_tabs import SettingsError, apply
    rule = one(db, "select facet_id from facets where status = 'active' and source_policy = 'derived' limit 1")
    with pytest.raises(SettingsError) as e:
        apply({"levels": {"nope": 0.5, **({rule[0]: 0.5} if rule else {})}, "default": 1.5})
    assert e.value.status == 422


# ------------------------------------------------------------------ models

def test_models_match_the_profiles_file_and_contain_no_secret(db):
    from app.settings_tabs import models
    m = models()
    with open(ROOT / "config" / "models.toml", "rb") as f:
        profiles = tomllib.load(f)["profiles"]
    assert [p["name"] for p in m["profiles"]] == list(profiles)
    text = repr(m).lower()
    assert "api_key" not in text and "base_url" not in text
    for k, v in os.environ.items():
        if "KEY" in k and len(v) > 8:
            assert v not in repr(m)


def test_a_saved_model_is_used_by_the_next_run_and_an_explicit_one_still_wins(db):
    from app.pipeline import settings_for
    from app.settings_tabs import SettingsError, save_models
    other = next(n for n in ("opus", "haiku", "sonnet")
                 if n != one(db, "select tag_model from app_settings")[0])
    save_models({"tagModel": other})
    s = settings_for(db)                     # read at the start of every run and live tag job
    assert s.tag_model == other
    explicit = "sonnet" if other != "sonnet" else "haiku"
    assert (explicit or s.tag_model) == explicit          # the CLI's `model or settings.tag_model`
    with pytest.raises(SettingsError):
        save_models({"tagModel": "no-such-model"})
