"""Live tests of the tagger against the Supabase database, with a fake model (no network calls).

Everything runs inside one transaction that is rolled back, so nothing is left behind.
"""
from __future__ import annotations

import os
import re
from dataclasses import replace
from pathlib import Path

import openpyxl
import psycopg
import pytest
from dotenv import load_dotenv

from scraper.store import normalize_db_url
from tagger.compare import compare
from tagger.config import Profile, load_settings
from tagger.derive import run_derive
from tagger.evaluate import evaluate
from tagger.facets import MODEL_POLICIES, facets_for_tagging
from tagger.golden import export, import_sheet
from tagger.products import load_product, quote_found
from tagger.providers import RateLimited
from tagger.providers import fake
from tagger.runs import Db, RateGate, finish_run, start_run
from tagger.steps import Context, products_to_tag, run_categorise, run_tag, tag_request

ROOT = Path(__file__).parents[1]
FAKE = Profile("fake", "fake", "fake-model-1", True, 1.0, 2.0, 0.1, 1.25, 0.5, 1.0)


@pytest.fixture(scope="module")
def conn():
    load_dotenv(ROOT / ".env")
    url = os.environ.get("SUPABASE_DB_URL")
    if not url:
        pytest.skip("SUPABASE_DB_URL not set")
    c = psycopg.connect(normalize_db_url(url), autocommit=False)
    if not c.execute("select to_regclass('public.tagging_calls')").fetchone()[0]:
        c.close()
        pytest.skip("run `catalog migrate` first")
    c.rollback()
    yield c
    c.close()


@pytest.fixture
def db(conn):
    conn.execute("select 1")             # open the transaction the test runs in
    yield Db(conn, commit=False)
    conn.rollback()


@pytest.fixture
def settings():
    s = load_settings(ROOT / "config")
    s.profiles["fake"] = FAKE
    s.concurrency = 2
    return s


def ctx(db, settings):
    return Context(db, settings, None, RateGate(), log=lambda *a: None)


REVIEW_ME: set[str] = set()


def fake_categorise(req):
    ids = req.schema["properties"]["category_id"]["enum"]
    title = req.text.split("TITLE:\n", 1)[1].split("\n", 1)[0]
    low = any(t in title for t in REVIEW_ME)
    # no runner-up for the low one, so the identical-facets rule cannot turn its review into 'flagged'
    return {"category_id": ids[0], "runner_up_id": ids[1] if len(ids) > 1 and not low else None,
            "confidence": 0.4 if low else 0.9, "reason": "fake"}


def fake_tag(req):
    title = req.text.split("TITLE:\n", 1)[1].split("\n", 1)[0]
    out = {}
    for fid, sch in req.schema["properties"].items():
        number = sch.get("$ref", "").endswith("number_answer")
        block = req.text.split(f"FACET: {fid}\n", 1)[1].split("\n\nFACET:", 1)[0]
        labels = re.findall(r"^  - (.+?)(?::|$)", block, re.M)
        vals = [labels[0]] if labels else [12.5] if number else ["x"]
        policy = re.search(rf"FACET: {fid}\nPolicy: (\w+)", req.text).group(1)
        ev = "image" if policy == "judge" else "text"
        out[fid] = {"state": "value", "values": vals, "confidence": 0.95, "evidence": ev,
                    "quote": title if ev == "text" else "", "reason": "fake"}
    return out


def responder(req):
    return fake_categorise(req) if req.stage == "categorise" else fake_tag(req)


@pytest.fixture
def pipeline(db, settings):
    """Categorise 4 golden products (one goes to review), tag the rest, derive."""
    fake.Adapter.responder = staticmethod(responder)
    fake.Adapter.calls = []
    pids = [r[0] for r in db.execute("select product_id from golden_products order by product_id limit 4")]
    if len(pids) < 4:
        pytest.skip("run `tagger golden sample` first")
    # start from a clean slate inside this rolled-back transaction: real golden labels and real runs on
    # these products would otherwise leak into the counts below
    db.execute("delete from golden_tags")
    db.execute("update golden_products set category_id = null, is_holdout = false")
    db.execute("delete from product_facet_tags where product_id = any(%s)", (pids,))
    db.execute("delete from catalog_products where product_id = any(%s)", (pids,))
    REVIEW_ME.clear()
    REVIEW_ME.add(load_product(db.conn, pids[-1]).name)
    c = ctx(db, settings)
    cat_run = run_categorise(c, FAKE, pids)
    finish_run(db, cat_run)
    auto = products_to_tag(db, None, pids, False)
    tag_run = run_tag(c, FAKE, auto)
    derive_report = run_derive(db, tag_run.run_id, settings, ROOT)
    finish_run(db, tag_run)
    return {"pids": pids, "auto": auto, "review": pids[-1], "cat_run": cat_run, "tag_run": tag_run,
            "derive": derive_report, "ctx": c}


def count(db, sql, params=()):
    return db.execute(sql, params).fetchone()[0]


def test_every_product_is_categorised_and_review_products_are_not_tagged(db, pipeline):
    p = pipeline
    assert count(db, "select count(*) from catalog_products where product_id = any(%s)", (p["pids"],)) == 4
    assert count(db, "select category_status from catalog_products where product_id = %s", (p["review"],)) == "review"
    assert p["review"] not in p["auto"] and len(p["auto"]) == 3
    assert count(db, "select count(*) from product_facet_tags where product_id = %s", (p["review"],)) == 0
    assert count(db, "select count(*) from product_facet_tags where tagging_run_id = %s",
                 (p["tag_run"].run_id,)) > 0


def test_saved_rows_obey_the_rules(db, pipeline):
    run = pipeline["tag_run"].run_id
    assert count(db, "select count(*) from v_invalid_tags where tagging_run_id = %s", (run,)) == 0
    assert count(db, """select count(*) from product_facet_tags t join facets f using (facet_id)
                        where t.tagging_run_id = %s and f.source_policy = 'text_only'
                          and t.evidence in ('image', 'both')""", (run,)) == 0
    for pid, quote in db.execute("""select product_id, evidence_quote from product_facet_tags
                                    where tagging_run_id = %s and status = 'auto' and evidence = 'text'""", (run,)):
        assert quote_found(quote, load_product(db.conn, pid).searchable())


def test_requests_only_carry_applicable_model_facing_facets(db, settings):
    for pattern in ("% / Dresses%", "% / Bags / %", "% / Shoes / %"):
        cat, path = db.execute("""select c.node_id, c.path from categories c
                                  where c.path like %s and c.is_leaf and exists
                                  (select 1 from category_facets cf where cf.category_id = c.node_id)
                                  order by c.node_id limit 1""", (pattern,)).fetchone()
        specs = facets_for_tagging(db.conn, cat, path, settings.function_categories)
        allowed = {r[0] for r in db.execute(
            """select cf.facet_id from category_facets cf join facets f using (facet_id)
               where cf.category_id = %s and f.status = 'active' and f.source_policy = any(%s)""",
            (cat, list(MODEL_POLICIES)))}
        assert specs and {s.facet_id for s in specs} <= allowed
        pid = db.execute("select product_id from products limit 1").fetchone()[0]
        req = tag_request(ctx(db, settings), start_run(db, "tag", FAKE, "x", None), load_product(db.conn, pid),
                          path, specs, "x")
        assert set(re.findall(r"^FACET: (\w+)$", req.text, re.M)) <= allowed
        assert set(req.schema["properties"]) <= allowed


def test_derive_rerun_leaves_row_count_unchanged(db, settings, pipeline):
    run = pipeline["tag_run"].run_id
    before = count(db, "select count(*) from product_facet_tags where tagging_run_id = %s", (run,))
    assert pipeline["derive"]["rows"] > 0
    again = run_derive(db, run, settings, ROOT)
    assert again["replaced"] == pipeline["derive"]["rows"]
    assert count(db, "select count(*) from product_facet_tags where tagging_run_id = %s", (run,)) == before


def test_resume_does_not_duplicate(db, settings, pipeline):
    run = pipeline["tag_run"]
    rows = count(db, "select count(*) from product_facet_tags where tagging_run_id = %s", (run.run_id,))
    calls = len(fake.Adapter.calls)
    # pretend the run stopped before finishing one product
    db.execute("delete from tagging_run_products where tagging_run_id = %s and product_id = %s",
               (run.run_id, pipeline["auto"][0]))
    run_tag(pipeline["ctx"], FAKE, pipeline["auto"], run)
    assert len(fake.Adapter.calls) == calls + 1                       # only the unfinished product
    run_derive(db, run.run_id, settings, ROOT)
    assert count(db, "select count(*) from product_facet_tags where tagging_run_id = %s", (run.run_id,)) == rows


def test_run_records_model_prices_and_costs(db, pipeline, settings):
    for run in (pipeline["cat_run"], pipeline["tag_run"]):
        prof, mid, ip, cost, calls = db.execute(
            """select model_profile, model_id, input_price, cost_usd,
                      (select sum(cost_usd) from tagging_calls c where c.tagging_run_id = r.tagging_run_id)
               from tagging_runs r where tagging_run_id = %s""", (run.run_id,)).fetchone()
        assert (prof, mid, float(ip)) == ("fake", "fake-model-1", 1.0) and cost == calls and cost > 0
    stored = count(db, "select cost_usd from tagging_runs where tagging_run_id = %s", (pipeline["tag_run"].run_id,))
    settings.profiles["fake"] = replace(FAKE, input_price=99.0)       # a later price edit
    finish_run(db, pipeline["tag_run"])
    assert count(db, "select cost_usd from tagging_runs where tagging_run_id = %s",
                 (pipeline["tag_run"].run_id,)) == stored
    stages = dict(db.execute("select tagging_run_id, stage from v_run_costs where tagging_run_id = any(%s)",
                             ([pipeline["cat_run"].run_id, pipeline["tag_run"].run_id],)).fetchall())
    assert sorted(stages.values()) == ["categorise", "tag"]
    out = compare(db, [pipeline["cat_run"].run_id, pipeline["tag_run"].run_id], stage="tag", project=3_000_000)
    assert "categorise" not in out and "projected cost for 3,000,000 products, batch (estimate)" in out


def test_retries_and_failures_are_logged(db, settings, pipeline):
    pid = pipeline["auto"][0]
    state = {"n": 0}

    def flaky(req):
        state["n"] += 1
        return RateLimited("slow down", retry_after=0.01) if state["n"] == 1 else fake_tag(req)
    fake.Adapter.responder = staticmethod(flaky)
    run = run_tag(pipeline["ctx"], FAKE, [pid])
    assert count(db, "select count(*) from tagging_calls where tagging_run_id = %s", (run.run_id,)) == 2
    fake.Adapter.responder = staticmethod(lambda req: {"nonsense": 1})          # never matches the schema
    run2 = run_tag(pipeline["ctx"], FAKE, [pid])
    s = finish_run(db, run2)
    assert count(db, "select count(*) from tagging_calls where tagging_run_id = %s", (run2.run_id,)) == 2
    assert (s["attempted"], s["succeeded"]) == (1, 0)


def test_golden_sheet_has_no_model_output_and_import_validates(db, pipeline, tmp_path):
    pid = pipeline["auto"][0]
    predicted = count(db, "select category_id from catalog_products where product_id = %s", (pid,))
    path = tmp_path / "sheet.xlsx"
    export(db, path)
    wb = openpyxl.load_workbook(path)
    cats = list(wb["Categories"].iter_rows(min_row=2, values_only=True))
    assert all(not r[4] for r in cats)                    # no predicted category is filled in
    db.execute("update golden_products set category_id = %s where product_id = %s", (predicted, pid))
    export(db, path)
    wb = openpyxl.load_workbook(path)
    facets = list(wb["Facets"].iter_rows(min_row=2, values_only=True))
    assert facets and all(r[9] in (None, "") and r[10] in (None, "") for r in facets)   # no predicted tags
    ws = wb["Facets"]
    occ = next(i for i, r in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2) if r[5] == "text_only")
    ws.cell(occ, 10, "value")
    ws.cell(occ, 11, "definitely-not-a-value")
    ws.cell(occ + 1, 10, "unknown")
    wb.save(path)
    rep = import_sheet(db, path)
    assert len(rep.invalid) == 1
    assert "not an allowed value" in rep.invalid[0] or "not a number" in rep.invalid[0]
    assert rep.facets_loaded == 1


def test_evaluate_perfect_then_one_wrong_value(db, pipeline):
    run = pipeline["tag_run"].run_id
    for pid in pipeline["auto"]:
        db.execute("""update golden_products set category_id = cp.category_id from catalog_products cp
                      where cp.product_id = golden_products.product_id and cp.product_id = %s""", (pid,))
    # golden labels = what the pipeline said, for every model-facing facet
    db.execute("""insert into golden_tags (product_id, facet_id, status, facet_value_id, value_number, value_text)
                  select t.product_id, t.facet_id, 'value', t.facet_value_id, t.value_number, t.value_text
                  from product_facet_tags t join facets f using (facet_id)
                  where t.tagging_run_id = %s and f.source_policy = any(%s)
                    and num_nonnulls(t.facet_value_id, t.value_number, t.value_text) = 1""",
               (run, list(MODEL_POLICIES)))
    ev = evaluate(db, run)
    m = {(x.scope, x.metric): x for x in ev["metrics"]}
    assert m[("overall", "accuracy")].value == 1.0 and m[("overall", "precision")].value == 1.0
    assert m[("category", "accuracy_exact")].value == 1.0
    facet_scores = [x.value for x in ev["metrics"] if x.metric == "accuracy" and x.scope not in ("overall",)]
    assert facet_scores and all(v == 1.0 for v in facet_scores)
    # one deliberately wrong value
    fid, tid, fvid = db.execute(
        """select g.facet_id, g.golden_tag_id, g.facet_value_id from golden_tags g join facets f using (facet_id)
           where g.product_id = %s and f.data_type = 'enum' and f.cardinality = 'single'
           order by g.facet_id limit 1""", (pipeline["auto"][0],)).fetchone()
    other = count(db, "select facet_value_id from facet_values where facet_id = %s and facet_value_id <> %s limit 1",
                  (fid, fvid))
    db.execute("update golden_tags set facet_value_id = %s where golden_tag_id = %s", (other, tid))
    ev = evaluate(db, run)
    m = {(x.scope, x.metric): x for x in ev["metrics"]}
    assert m[(fid, "accuracy")].value < 1.0 and m[(fid, "confusions")].detail["pairs"]


# ------------------------------------------------------------------ monitoring (PRD 9)

@pytest.fixture
def monitored(db, settings):
    """20 golden products tagged normally, then a no-design-details drill, both monitored."""
    from tagger.monitor import MonitorSettings, run_monitor
    with_colour = {r[0] for r in db.execute("select category_id from category_facets where facet_id = 'colour_primary'")}

    def categorise(req):
        ids = req.schema["properties"]["category_id"]["enum"]
        pick = next((i for i in ids if i in with_colour), ids[0])
        return {"category_id": pick, "runner_up_id": None, "confidence": 0.9, "reason": "fake"}

    def tag(req):
        out = fake_tag(req)
        if "DESIGN DETAILS:" not in req.text:            # the drill removed it: text-led facets go unknown
            for fid, ans in out.items():
                if re.search(rf"FACET: {fid}\nPolicy: text_(only|first)", req.text):
                    out[fid] = {"state": "unknown", "values": [], "confidence": 0, "evidence": "none",
                                "quote": "", "reason": "not stated"}
        return out
    fake.Adapter.responder = staticmethod(lambda req: categorise(req) if req.stage == "categorise" else tag(req))
    fake.Adapter.calls = []
    pids = [r[0] for r in db.execute("select product_id from golden_products order by product_id limit 20")]
    c = ctx(db, settings)
    finish_run(db, run_categorise(c, FAKE, pids))
    auto = products_to_tag(db, None, pids, False)
    normal = run_tag(c, FAKE, auto)
    run_derive(db, normal.run_id, settings, ROOT)
    finish_run(db, normal)
    drill = run_tag(c, FAKE, auto, drill="no-design-details")
    finish_run(db, drill)
    m = MonitorSettings.from_dict(settings.monitor)
    first = run_monitor(db, normal.run_id, m, baseline_id=normal.run_id)      # against itself
    result = run_monitor(db, drill.run_id, m, baseline_id=normal.run_id)
    return {"normal": normal.run_id, "drill": drill.run_id, "auto": auto, "self": first, "drill_result": result,
            "m": m}


def _stats_cover_every_tagged_facet_and_add_up(db, monitored):
    run = monitored["normal"]
    tagged = {r[0] for r in db.execute("select distinct facet_id from product_facet_tags where tagging_run_id = %s",
                                       (run,))}
    stats = {r[0] for r in db.execute("select facet_id from run_facet_stats where tagging_run_id = %s "
                                      "and segment_type = 'all'", (run,))}
    assert tagged and tagged <= stats
    assert count(db, """select count(*) from run_facet_stats where tagging_run_id = %s and products_applicable
                        <> auto_count + review_count + unknown_count + not_applicable_count""", (run,)) == 0
    assert count(db, """select count(*) from run_facet_stats where tagging_run_id = %s and segment_type <> 'all'
                        and products_applicable < 15""", (run,)) == 0


def _run_against_itself_and_rerun_are_stable(db, monitored):
    from tagger.monitor import run_monitor
    assert monitored["self"]["alerts"] == monitored["self"]["warnings"] == 0
    before = count(db, "select count(*) from monitor_alerts where tagging_run_id = %s", (monitored["drill"],))
    run_monitor(db, monitored["drill"], monitored["m"], baseline_id=monitored["normal"])
    assert count(db, "select count(*) from monitor_alerts where tagging_run_id = %s", (monitored["drill"],)) == before


def _drill_raises_text_led_alert_and_cannot_be_published(db, monitored):
    from tagger.steps import publish_run
    assert monitored["drill_result"]["alerts"] > 0
    assert count(db, """select count(*) from monitor_alerts a join facets f using (facet_id)
                        where a.tagging_run_id = %s and a.severity = 'alert'
                          and f.source_policy in ('text_only', 'text_first')
                          and a.check_name = 'unknown_rate'""", (monitored["drill"],)) > 0
    with pytest.raises(ValueError, match="drill"):
        publish_run(db, monitored["drill"])
    assert db.execute("select is_drill, drill_name from tagging_runs where tagging_run_id = %s",
                      (monitored["drill"],)).fetchone() == (True, "no-design-details")


def _drills_stay_out_of_views_and_history(db, monitored):
    from tagger.monitor import history
    drill = monitored["drill"]
    assert count(db, "select count(*) from v_run_costs where tagging_run_id = %s", (drill,)) == 0
    assert count(db, "select count(*) from v_monitor_latest where tagging_run_id = %s", (drill,)) == 0
    assert count(db, "select count(*) from v_monitor_latest where tagging_run_id = %s", (monitored["normal"],)) > 0
    runs = [r["run_id"] for r in history(db, "colour_primary")]
    assert monitored["normal"] in runs and drill not in runs and runs == sorted(runs)
    assert count(db, "select count(*) from v_open_alerts where tagging_run_id = %s", (drill,)) > 0


def test_monitoring_and_drills(db, monitored):
    """One setup (20 products, a normal run and a drill), then every PRD 9.8 check that needs the database."""
    _stats_cover_every_tagged_facet_and_add_up(db, monitored)
    _run_against_itself_and_rerun_are_stable(db, monitored)
    _drill_raises_text_led_alert_and_cannot_be_published(db, monitored)
    _drills_stay_out_of_views_and_history(db, monitored)
