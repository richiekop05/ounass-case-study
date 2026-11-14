"""Golden set extension and AI-assisted labelling: sampling, pre-fill and import of the reviewed sheet.

Offline tests first; the database tests run inside a rolled-back transaction with a fake model.
"""
from __future__ import annotations

import os
import random
import re
from pathlib import Path

import openpyxl
import psycopg
import pytest
from dotenv import load_dotenv

from scraper.store import normalize_db_url
from tagger.config import Profile, load_settings
from tagger.golden import parse_add, pick_spread, sample_add
from tagger.golden_review import (MAIN_SHEET, certainty, import_reviewed, prefill, recategorised,
                                  sheet_is_reviewed)
from tagger.providers import fake
from tagger.runs import Db

ROOT = Path(__file__).parents[1]
FAKE = Profile("fake", "fake", "fake-opus", True, 4.0, 20.0)
GOLDEN_COLUMNS = {
    "golden_products": {"product_id", "category_id", "labelled_by", "notes", "created_at", "is_holdout"},
    "golden_tags": {"product_id", "facet_id", "facet_value_id", "created_at", "golden_tag_id", "value_number",
                    "value_text", "status"},
}


# ------------------------------------------------------------------ offline

def test_certainty_bands():
    assert certainty(0.95, "value", False) == "high" and certainty(0.90, "value", False) == "high"
    assert certainty(0.89, "value", False) == "medium" and certainty(0.75, "value", False) == "medium"
    assert certainty(0.74, "value", False) == "low"
    assert certainty(0.99, "unknown", False) == "low" and certainty(0.99, "value", True) == "low"


def test_partial_prefill_never_writes_the_main_sheet():
    with pytest.raises(ValueError, match="never writes prefilled_labels.xlsx"):
        prefill(None, FAKE, None, MAIN_SHEET, None, ROOT / "skills", only={"x": ("y", "")}, log=lambda *a: None)


def test_parse_answer_takes_commas_and_one_part_of_a_slash_label():
    from tagger.facets import FacetSpec, FacetValue
    from tagger.golden import parse_answer
    spec = FacetSpec("occasion", "Occasion", None, "judge", "enum", "multi", None, None, 3, False, None,
                     "unknown", None, None, values=[FacetValue(1, "work", "work"),
                                                    FacetValue(2, "special_occasion", "special occasion"),
                                                    FacetValue(3, "bold_statement", "bold/statement")])
    state, values, err = parse_answer(spec, "value", "work, special occasion")
    assert err is None and [v.label for v in values] == ["work", "special occasion"]
    assert [v.label for v in parse_answer(spec, "value", "bold")[1]] == ["bold/statement"]
    assert [v.label for v in parse_answer(spec, "value", "specialoccasion")[1]] == ["special occasion"]


def test_parse_add():
    assert parse_add("Clothing=10, beauty=7") == {"clothing": 10, "beauty": 7}
    for bad in ("clothing", "clothing=x", ""):
        with pytest.raises(ValueError):
            parse_add(bad)


def test_pick_spread_takes_unrepresented_pairs_first_and_repeats():
    cands = [(f"p{i}", cat, brand) for i, (cat, brand) in enumerate(
        [("Dresses", "A")] * 4 + [("Dresses", "B")] * 2 + [("Tops", "A")] * 2 + [("Skirts", "C")])]
    picked = pick_spread(cands, [("Dresses", "A")], 3, random.Random(1))
    pairs = [next((c[1], c[2]) for c in cands if c[0] == p) for p in picked]
    assert len(set(pairs)) == 3 and ("Dresses", "A") not in pairs          # existing pair is represented
    assert picked == pick_spread(cands, [("Dresses", "A")], 3, random.Random(1))
    assert len(pick_spread(cands, [], 50, random.Random(1))) == len(cands)  # shortfall: takes what exists


def test_prefill_code_never_names_tagging_tables():
    src = (ROOT / "src" / "tagger" / "golden_review.py").read_text()
    code = src.split('"""', 2)[2]          # skip the module docstring, which names them to say it never reads them
    assert "product_facet_tags" not in code and "catalog_products" not in code


# ------------------------------------------------------------------ database

@pytest.fixture(scope="module")
def conn():
    load_dotenv(ROOT / ".env")
    url = os.environ.get("SUPABASE_DB_URL")
    if not url:
        pytest.skip("SUPABASE_DB_URL not set")
    c = psycopg.connect(normalize_db_url(url), autocommit=False)
    c.rollback()
    yield c
    c.close()


@pytest.fixture
def db(conn):
    conn.execute("select 1")
    yield Db(conn, commit=False)
    conn.rollback()


class SpyConn:
    """Records every statement the pre-fill sends."""

    def __init__(self, real):
        self.real, self.sql = real, []

    def execute(self, sql, params=()):
        self.sql.append(str(sql))
        return self.real.execute(sql, params)


def table_counts(db) -> dict[str, int]:
    tables = [r[0] for r in db.execute("select table_name from information_schema.tables where table_schema = "
                                       "'public' and table_type = 'BASE TABLE' order by 1")]
    return {t: db.execute(f'select count(*) from "{t}"').fetchone()[0] for t in tables}


def test_sample_add_extends_and_is_repeatable(db):
    before = {r[0] for r in db.execute("select product_id from golden_products")}
    if len(before) != 50:
        pytest.skip("expects the original 50-product golden set")
    r = sample_add(db, "clothing=10,beauty=7,accessories=10,bags=8")
    after = {r[0] for r in db.execute("select product_id from golden_products")}
    assert len(after) == 85 and before <= after and sum(r["added"].values()) == 35
    assert r["before"] != r["after"] and not r["shortfall"]
    again = sample_add(db, "clothing=10,beauty=7,accessories=10,bags=8")
    assert again["already"] and db.execute("select count(*) from golden_products").fetchone()[0] == 85
    short = sample_add(db, "beauty=5,watches=1")                  # beauty has no unselected products left
    assert short["shortfall"]["Beauty"] == 5 and short["added"]["Beauty"] == 0 and short["added"]["Watches"] == 1


@pytest.fixture
def drafted(db, tmp_path):
    with_colour = {r[0] for r in db.execute("select category_id from category_facets where facet_id = 'colour_primary'")}

    def responder(req):
        if req.stage == "categorise":
            ids = req.schema["properties"]["category_id"]["enum"]
            pick = next((i for i in ids if i in with_colour), ids[0])
            return {"category_id": pick, "runner_up_id": ids[-1] if ids[-1] != pick else None, "confidence": 0.92,
                    "reason": "fake"}
        out = {}
        for fid, sch in req.schema["properties"].items():
            block = req.text.split(f"FACET: {fid}\n", 1)[1].split("\n\nFACET:", 1)[0]
            labels = re.findall(r"^  - (.+?)(?::|$)", block, re.M)
            number = sch.get("$ref", "").endswith("number_answer")
            vals = [labels[0]] if labels else [12.5] if number else ["x"]
            out[fid] = {"state": "value", "values": vals, "confidence": 0.8, "evidence": "image", "quote": "",
                        "reason": "fake"}
            if "Policy: text_only" in block:
                out[fid] = {"state": "unknown", "values": [], "confidence": 0, "evidence": "none", "quote": "",
                            "reason": "not stated"}
        return out
    fake.Adapter.responder = staticmethod(responder)
    settings = load_settings(ROOT / "config")
    settings.concurrency = 2
    counts = table_counts(db)
    spy = SpyConn(db.conn)
    out = tmp_path / "prefilled_labels.xlsx"
    result = prefill(spy, FAKE, None, out, settings, ROOT / "skills", log=lambda *a: None)
    return {"result": result, "path": out, "spy": spy, "counts": counts, "settings": settings,
            "responder": responder}


def _prefill_writes_the_sheet_and_nothing_else(db, drafted):
    assert table_counts(db) == drafted["counts"]
    assert not [s for s in drafted["spy"].sql if re.search(r"product_facet_tags|catalog_products", s)]
    assert not [s for s in drafted["spy"].sql if re.match(r"\s*(insert|update|delete)", s, re.I)]
    wb = openpyxl.load_workbook(drafted["path"])
    products = list(wb["Products"].iter_rows(min_row=2, values_only=True))
    golden = {r[0] for r in db.execute("select product_id from golden_products")}
    assert {p[0] for p in products} == golden and drafted["result"]["model"] == "fake"
    assert wb["Facets"].freeze_panes == "A2" and wb["Facets"].auto_filter.ref
    cat = {p[0]: p[4] for p in products}
    facets = list(wb["Facets"].iter_rows(min_row=2, values_only=True))
    assert facets
    seen_order = [f[0] for f in facets]
    assert seen_order == sorted(seen_order)                       # a product's facet rows stay together
    for pid, _, fid, _, state, values, _, cert, *_ in facets:
        applies = db.execute("select count(*) from category_facets where category_id = %s and facet_id = %s",
                             (cat[pid], fid)).fetchone()[0]
        assert applies == 1
        if state == "value" and values:
            for v in str(values).split("; "):
                labels = {r[0] for r in db.execute("select label from facet_values where facet_id = %s", (fid,))}
                assert not labels or v in labels
        if state == "unknown":
            assert cert == "low"


def _prefill_refuses_to_overwrite_a_reviewed_sheet(db, drafted):
    wb = openpyxl.load_workbook(drafted["path"])
    wb["Products"]["H2"] = "ok"
    wb.save(drafted["path"])
    assert sheet_is_reviewed(drafted["path"])
    with pytest.raises(ValueError, match="--force"):
        prefill(db.conn, FAKE, None, drafted["path"], drafted["settings"], ROOT / "skills", log=lambda *a: None)


def _col(ws, name):
    return [c.value for c in ws[1]].index(name) + 1


def _import_reviewed_sheet(db, drafted, tmp_path):
    # start from no labels: real golden labels are cleared inside this rolled-back transaction only
    db.execute("delete from golden_tags")
    db.execute("update golden_products set category_id = null, is_holdout = false")
    path = drafted["path"]
    wb = openpyxl.load_workbook(path)
    ps, fs = wb["Products"], wb["Facets"]
    prows = {ps.cell(r, 1).value: r for r in range(2, ps.max_row + 1)}
    frows: dict[str, list[int]] = {}
    for r in range(2, fs.max_row + 1):
        frows.setdefault(fs.cell(r, 1).value, []).append(r)
    pids = sorted(p for p in prows if len(frows.get(p, [])) >= 4)
    a, b, c_, d = pids[:4]
    pc = {n: _col(ps, n) for n in ("category_verdict", "corrected_category_id", "all_facets_reviewed",
                                   "prefilled_category_id")}
    fc = {n: _col(fs, n) for n in ("facet_id", "verdict", "corrected_state", "corrected_values", "prefilled_values",
                                   "prefilled_state", "allowed_values")}
    # A: reviewed; one invalid value, one change without a correction, one valid change, one ok
    ps.cell(prows[a], pc["category_verdict"], "ok")
    ps.cell(prows[a], pc["all_facets_reviewed"], "yes")
    enum_rows = [r for r in frows[a] if ";" in str(fs.cell(r, fc["allowed_values"]).value or "")]
    bad, nocorr, good, okrow = enum_rows[:4]
    fs.cell(bad, fc["verdict"], "change"); fs.cell(bad, fc["corrected_state"], "value")
    fs.cell(bad, fc["corrected_values"], "definitely-not-allowed")
    fs.cell(nocorr, fc["verdict"], "change")
    corrected = str(fs.cell(good, fc["allowed_values"]).value).split("; ")[-1]
    fs.cell(good, fc["verdict"], "change"); fs.cell(good, fc["corrected_state"], "value")
    fs.cell(good, fc["corrected_values"], corrected)
    fs.cell(okrow, fc["verdict"], "ok")
    # B: category ok, not marked reviewed; only one facet row has a verdict
    ps.cell(prows[b], pc["category_verdict"], "ok")
    fs.cell(frows[b][0], fc["verdict"], "ok")
    # C: category changed to an unknown ID
    ps.cell(prows[c_], pc["category_verdict"], "change"); ps.cell(prows[c_], pc["corrected_category_id"], "NOPE-1")
    # D: category changed to a valid leaf
    other = db.execute("""select c.node_id from categories c where c.is_leaf and c.name not ilike 'other %%'
                          and c.node_id <> %s and exists (select 1 from category_facets cf
                          where cf.category_id = c.node_id) order by c.node_id limit 1""",
                       (ps.cell(prows[d], pc["prefilled_category_id"]).value,)).fetchone()[0]
    ps.cell(prows[d], pc["category_verdict"], "change"); ps.cell(prows[d], pc["corrected_category_id"], other)
    wb.save(path)

    eval_rows = db.execute("select count(*) from evaluation_results").fetchone()[0]
    rep = import_reviewed(db, path)
    assert len(rep.problems) == 3, rep.problems
    joined = " | ".join(rep.problems)
    assert "not an allowed value" in joined and "without a corrected answer" in joined and "NOPE-1" in joined
    assert rep.second_pass == [d] and rep.skipped_no_verdict[b] == len(frows[b]) - 1

    def stored(pid, row):
        fid = fs.cell(row, fc["facet_id"]).value
        return [r[0] for r in db.execute("""select coalesce(fv.label, t.status) from golden_tags t
                                            left join facet_values fv using (facet_value_id)
                                            where t.product_id = %s and t.facet_id = %s""", (pid, fid))]
    assert stored(a, good) == [corrected]
    pre = fs.cell(okrow, fc["prefilled_values"]).value or fs.cell(okrow, fc["prefilled_state"]).value
    assert stored(a, okrow) == [x for x in str(pre).split("; ")]
    assert stored(a, bad) == [] and stored(a, nocorr) == []
    blank_rows_a = [r for r in frows[a] if r not in (bad, nocorr, good, okrow)]
    assert all(stored(a, r) for r in blank_rows_a)                     # blank verdict, product reviewed
    assert stored(b, frows[b][0]) and not any(stored(b, r) for r in frows[b][1:])
    assert db.execute("select category_id from golden_products where product_id = %s", (d,)).fetchone()[0] == other
    assert db.execute("select count(*) from golden_tags where product_id = %s", (d,)).fetchone()[0] == 0
    assert db.execute("select category_id from golden_products where product_id = %s", (c_,)).fetchone()[0] is None

    # re-import: no duplicates
    n = db.execute("select count(*) from golden_tags").fetchone()[0]
    import_reviewed(db, path)
    assert db.execute("select count(*) from golden_tags").fetchone()[0] == n

    # second pass: only D, under its corrected category
    only = recategorised(db.conn, path)
    assert only == {d: (other, only[d][1])}
    out2 = tmp_path / "prefilled_labels_pass2.xlsx"
    prefill(db.conn, FAKE, None, out2, drafted["settings"], ROOT / "skills", only=only, log=lambda *a: None)
    wb2 = openpyxl.load_workbook(out2)
    rows2 = list(wb2["Products"].iter_rows(min_row=2, values_only=True))
    assert [(r[0], r[4]) for r in rows2] == [(d, other)]

    # importing the small sheet after the main one touches only its own products
    others = sorted(db.execute("""select product_id, facet_id, status, facet_value_id, value_number, value_text
                                  from golden_tags where product_id <> %s""", (d,)).fetchall(), key=str)
    cats = db.execute("select product_id, category_id from golden_products where product_id <> %s order by 1",
                      (d,)).fetchall()
    ps2 = wb2["Products"]
    ps2.cell(2, _col(ps2, "category_verdict"), "ok"); ps2.cell(2, _col(ps2, "all_facets_reviewed"), "yes")
    wb2.save(out2)
    rep2 = import_reviewed(db, out2)
    assert rep2.problems == [] and rep2.categories_saved == 1
    assert db.execute("select count(*) from golden_tags where product_id = %s", (d,)).fetchone()[0] > 0
    assert sorted(db.execute("""select product_id, facet_id, status, facet_value_id, value_number, value_text
                                from golden_tags where product_id <> %s""", (d,)).fetchall(), key=str) == others
    assert db.execute("select product_id, category_id from golden_products where product_id <> %s order by 1",
                      (d,)).fetchall() == cats

    # nothing else changed: same golden schema, no evaluation rows
    for table, cols in GOLDEN_COLUMNS.items():
        got = {r[0] for r in db.execute("select column_name from information_schema.columns where table_name = %s",
                                        (table,))}
        assert got == cols
    assert db.execute("select count(*) from evaluation_results").fetchone()[0] == eval_rows


def test_prefill_and_import(db, drafted, tmp_path):
    """One pre-fill of the whole golden set (fake model), then every check that needs it."""
    _prefill_writes_the_sheet_and_nothing_else(db, drafted)
    _import_reviewed_sheet(db, drafted, tmp_path)
    _prefill_refuses_to_overwrite_a_reviewed_sheet(db, drafted)
