"""Offline tests for the tagger: PRD 4.4 checks, the 4.5 status table, the section 5 rules, scoring,
schemas and config. No network and no database."""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from tagger.config import ConfigError, DeriveSettings, load_settings, price_tokens
from tagger.derive import GiftRules, ProductFacts, derive_for, gift_occasions, giftable
from tagger.evaluate import GoldAnswer, PredRow, score_categories, score_tags
from tagger.facets import FacetSpec, FacetValue, function_category
from tagger.golden import allocate, parse_answer
from tagger.products import quote_found
from tagger.providers import SchemaError
from tagger.providers.base import api_schema
from tagger.steps import decide_category
from tagger.tagging import check_answer, reply_schema, rows_for, validate_reply

ROOT = Path(__file__).parents[1]
TEXT = quote_found.__globals__["_norm"]("Title: Silk Dress. Colour: Emerald. Midi length. Heel height 85 mm. A gift set.")


def spec(fid="occasion", policy="judge", values=("everyday", "work", "evening", "none"), max_values=3,
         threshold=0.75, data_type="enum", on_missing="unknown", default=None, required=False, none_excl=True):
    s = FacetSpec(fid, fid, None, policy, data_type, "multi" if (max_values or 2) > 1 else "single", None, 1,
                  max_values, none_excl, threshold, on_missing, default, 1, is_required=required)
    s.values = [FacetValue(i + 1, v.replace(" ", "_").replace("-", "_"), v, None,
                           is_exclusive=(v == "none" and none_excl) or v == "all-season")
                for i, v in enumerate(values)]
    return s


def ans(state="value", values=("evening",), conf=0.9, evidence="image", quote="", reason="r"):
    return {"state": state, "values": list(values), "confidence": conf, "evidence": evidence, "quote": quote,
            "reason": reason}


# ------------------------------------------------------------------ 4.4 checks

def test_missing_facet_is_unknown():
    a = check_answer(spec(), None, TEXT)
    assert a.state == "unknown" and "no answer returned" in a.notes


def test_value_state_without_values_and_the_reverse():
    assert check_answer(spec(), ans(values=()), TEXT).state == "unknown"
    assert check_answer(spec(), ans(state="unknown", values=("work",)), TEXT).state == "unknown"
    assert check_answer(spec(), ans(state="not_applicable", values=()), TEXT).state == "not_applicable"


def test_values_not_allowed_are_dropped():
    a = check_answer(spec(), ans(values=("disco", "work")), TEXT)
    assert [v.value for v in a.values] == ["work"] and any("not allowed" in n for n in a.notes)
    assert check_answer(spec(), ans(values=("disco",)), TEXT).state == "unknown"


def test_too_many_values_and_none_beside_others():
    a = check_answer(spec(max_values=2), ans(values=("everyday", "work", "evening")), TEXT)
    assert [v.value for v in a.values] == ["everyday", "work"]
    a = check_answer(spec(), ans(values=("none", "work")), TEXT)
    assert [v.value for v in a.values] == ["work"]


def test_exclusive_value_other_than_none_is_kept_alone():
    season = spec("season", "judge", ("warm", "transitional", "cold", "all-season"), 3, 0.8, none_excl=False)
    a = check_answer(season, ans(values=("warm", "all-season", "cold")), TEXT)
    assert [v.label for v in a.values] == ["all-season"] and any("all-season" in n for n in a.notes)
    a = check_answer(season, ans(values=("warm", "cold")), TEXT)
    assert [v.label for v in a.values] == ["warm", "cold"] and not a.notes
    assert '"all-season" cannot sit beside another value.' in season.limit_text()
    assert parse_answer(season, "value", "all-season")[2] is None
    assert "all-season" in parse_answer(season, "value", "warm; all-season")[2]


def test_photo_on_text_only_facet_is_unknown():
    a = check_answer(spec("colour_primary", "text_only", ("green", "red"), 1, None), ans(values=("green",)), TEXT)
    assert a.state == "unknown" and "photo used on a text-only facet" in a.notes
    rows = rows_for(spec("colour_primary", "text_only", ("green", "red"), 1, None), a)
    assert rows[0].status == "abstained" and rows[0].evidence is None


def test_quote_not_found_forces_review():
    s = spec("length", "text_first", ("midi", "maxi"), 1, 0.85)
    a = check_answer(s, ans(values=("midi",), evidence="text", quote="Ankle length"), TEXT)
    assert a.force_review and rows_for(s, a)[0].status == "review"
    assert "quote not found in text" in rows_for(s, a)[0].reason
    ok = check_answer(s, ans(values=("midi",), evidence="text", quote="midi   LENGTH"), TEXT)
    assert not ok.force_review and rows_for(s, ok)[0].status == "auto"


def test_number_must_be_positive():
    s = spec("bag_width_cm", "text_only", (), 1, None, data_type="number")
    assert check_answer(s, ans(values=(0,), evidence="text", quote="Midi length"), TEXT).state == "unknown"
    assert check_answer(s, ans(values=(-3,), evidence="text", quote="Midi length"), TEXT).state == "unknown"
    a = check_answer(s, ans(values=(24,), evidence="text", quote="Midi length"), TEXT)
    assert a.values == [24.0] and rows_for(s, a)[0].value_number == 24.0


# ------------------------------------------------------------------ 4.5 statuses

def status(s, raw):
    return [r.status for r in rows_for(s, check_answer(s, raw, TEXT))]


def test_levels_apply_to_text_answers_too():
    """Decision of 14 Nov 2026: every model-written value answer is compared with its level."""
    from tagger.tagging import review_reason, check_answer as ca
    s = spec("colour_primary", "text_only", ("green",), 1, 0.8)
    assert status(s, ans(values=("green",), evidence="text", quote="Colour: Emerald", conf=0.9)) == ["auto"]
    assert status(s, ans(values=("green",), evidence="text", quote="Colour: Emerald", conf=0.5)) == ["review"]
    a = ca(s, ans(values=("green",), evidence="text", quote="Colour: Emerald", conf=0.5), TEXT)
    assert review_reason(s, a) == "low_confidence"
    a = ca(s, ans(values=("green",), evidence="text", quote="not in the text", conf=0.95), TEXT)
    assert review_reason(s, a) == "quote_not_found"
    s = spec("length", "text_first", ("midi",), 1, 0.85)
    assert status(s, ans(values=("midi",), evidence="text", quote="Midi length", conf=0.9)) == ["auto"]
    assert status(s, ans(values=("midi",), evidence="text", quote="Midi length", conf=0.5)) == ["review"]


def test_text_first_from_photo_uses_threshold():
    s = spec("length", "text_first", ("midi",), 1, 0.85)
    assert status(s, ans(values=("midi",), evidence="image", conf=0.85)) == ["auto"]
    assert status(s, ans(values=("midi",), evidence="image", conf=0.84)) == ["review"]
    assert status(s, ans(values=("midi",), evidence="both", quote="Midi length", conf=0.9)) == ["auto"]


def test_judge_uses_threshold_and_no_threshold_means_review():
    assert status(spec(), ans(values=("evening", "work"), conf=0.75)) == ["auto", "auto"]
    assert status(spec(), ans(conf=0.74)) == ["review"]
    assert status(spec(threshold=None), ans(conf=0.99)) == ["review"]


def test_unknown_routes_by_on_missing():
    s = spec("metal", "text_only", ("gold",), 1, None, on_missing="review_if_required", required=True)
    rows = rows_for(s, check_answer(s, ans(state="unknown", values=(), conf=0, evidence="none"), TEXT))
    assert [r.status for r in rows] == ["review"] and rows[0].facet_value_id is None and rows[0].confidence is None
    s.is_required = False
    assert status(s, ans(state="unknown", values=())) == ["abstained"]
    d = spec("exclusivity", "text_only", ("limited edition", "none"), None, None, on_missing="default", default="none")
    rows = rows_for(d, check_answer(d, ans(state="unknown", values=()), TEXT))
    assert [(r.status, r.evidence, r.label, r.confidence) for r in rows] == [("auto", "rule", "none", None)]
    assert status(spec(), ans(state="unknown", values=())) == ["abstained"]


def test_not_applicable():
    rows = rows_for(spec(), check_answer(spec(), ans(state="not_applicable", values=(), conf=0, evidence="none"), TEXT))
    assert [(r.status, r.facet_value_id, r.evidence) for r in rows] == [("not_applicable", None, None)]


# ------------------------------------------------------------------ schema

def test_reply_schema_rejects_value_outside_allowed_values():
    schema = reply_schema([spec()])
    validate_reply(schema, {"occasion": ans()})
    with pytest.raises(SchemaError, match="disco"):
        validate_reply(schema, {"occasion": ans(values=("disco",))})
    with pytest.raises(SchemaError):
        validate_reply(schema, {})                                  # every facet is required
    validate_reply(schema, {"occasion": ans(values=("everyday", "work", "evening", "none"))})  # trimmed later
    validate_reply(schema, {"occasion": ans(state="unknown", values=(), conf=0, evidence="none")})
    with pytest.raises(SchemaError):
        validate_reply(schema, {"occasion": ans(conf=None)})          # no nullable fields (API union limit)
    assert "anyOf" not in str(schema)


def test_api_schema_drops_unsupported_keywords():
    sent = api_schema(reply_schema([spec(max_values=2)]))
    assert "maxItems" not in str(sent) and "enum" in str(sent)


def test_quote_matching_is_case_and_space_insensitive():
    assert quote_found('"Colour:  emerald"', TEXT) and not quote_found("", TEXT) and not quote_found(None, TEXT)


def test_category_gate_and_runner_up():
    ids = ["A", "B"]
    d = decide_category({"category_id": "A", "runner_up_id": "A", "confidence": 0.8, "reason": "x"}, ids, 0.8)
    assert d["category_status"] == "auto" and d["category_runner_up_id"] is None and d["notes"]
    assert decide_category({"category_id": "A", "runner_up_id": "B", "confidence": 0.79}, ids, 0.8)[
        "category_status"] == "review"
    with pytest.raises(ValueError):
        decide_category({"category_id": "Z", "runner_up_id": None, "confidence": 1}, ids, 0.8)


# ------------------------------------------------------------------ section 5 rules

def facts(**kw):
    facets = {f: spec(f, "derived", vals, mx, None) for f, vals, mx in [
        ("price_band", ("entry", "mid", "high"), 1),
        ("recency", ("new-in", "current season", "carried over"), 1),
        ("is_metallic", ("true", "false"), 1),
        ("case_size_bucket", ("small <34", "medium 34-40", "large >40"), 1),
        ("brand_tier", ("contemporary", "designer", "luxury", "ultra-luxury"), 1),
        ("giftable", ("true", "false"), 1),
        ("gift_occasion", ("Eid", "birthday", "Christmas", "wedding"), 3),
        ("exclusivity", ("limited edition", "local designer", "none"), None),
        ("function", ("active", "swim"), None)]}
    facets["case_size_bucket"].values = [FacetValue(40 + i, s, l, None) for i, (s, l) in
                                         enumerate([("small", "small <34"), ("medium", "medium 34-40"),
                                                    ("large", "large >40")])]
    base = dict(product_id="p1", category_id="C", category_path="Women / Bags / Tote Bags",
                department=("Women", "Bags"), price=500.0, launch_date=None, brand_tier=None,
                is_local_designer=False, text="a leather tote", facets=facets, accepted={})
    base.update(kw)
    return ProductFacts(**base)


S = DeriveSettings(metallic_colours=["gold", "silver", "rose gold", "metallic"])
TODAY = date(2026, 11, 13)


def derived(p, rules=GiftRules(), dept=None, allp=None, fc=None):
    rows, remove, warn = derive_for(p, dept or {}, allp or [], rules, S, TODAY, fc or {"Swimwear": "swim"})
    assert not warn
    return {(r.facet_id, r.label) for r in rows}, remove, rows


def test_price_band_thirds_and_small_department_fallback():
    dept = {("Women", "Bags"): sorted([100.0 * i for i in range(1, 10)])}
    assert ("price_band", "entry") in derived(facts(price=200), dept=dept)[0]
    assert ("price_band", "mid") in derived(facts(price=500), dept=dept)[0]
    assert ("price_band", "high") in derived(facts(price=900), dept=dept)[0]
    small = {("Women", "Bags"): [100.0, 900.0]}                 # under 9 priced products: whole catalog
    got = derived(facts(price=100), dept=small, allp=sorted([1.0, 2.0, 3.0, 100.0, 900.0, 1000.0]))[0]
    assert ("price_band", "mid") in got


def test_recency_boundaries():
    for days, label in ((0, "new-in"), (30, "new-in"), (31, "current season"), (180, "current season"),
                        (181, "carried over")):
        assert ("recency", label) in derived(facts(launch_date=TODAY - timedelta(days=days)))[0]
    assert not any(f == "recency" for f, _ in derived(facts())[0])


def test_is_metallic_and_case_size_bucket():
    assert ("is_metallic", "true") in derived(facts(accepted={"colour_primary": ["rose gold"]}))[0]
    assert ("is_metallic", "false") in derived(facts(accepted={"colour_primary": ["black"]}))[0]
    assert not any(f == "is_metallic" for f, _ in derived(facts())[0])
    for mm, label in ((33.9, "small <34"), (34, "medium 34-40"), (40, "medium 34-40"), (41, "large >40")):
        assert ("case_size_bucket", label) in derived(facts(accepted={"case_size_mm": [mm]}))[0]


def test_brand_tier_from_brand_table():
    assert ("brand_tier", "ultra-luxury") in derived(facts(brand_tier="ultra-luxury"))[0]
    assert not any(f == "brand_tier" for f, _ in derived(facts(brand_tier=None))[0])


def test_giftable_rules():
    rules = GiftRules(giftable=[("Women / Bags", True), ("Women / Clothing", False)])
    assert giftable(facts(), rules, S)[0] == "true"                                   # gift-friendly category
    dress = facts(category_path="Women / Clothing / Dresses", accepted={"is_set": ["true"]}, text="a gift")
    assert giftable(dress, rules, S)[0] == "false"                                     # clothing never
    other = facts(category_path="All / Home / Furniture")
    assert giftable(other, rules, S)[0] == "false"
    assert giftable(facts(category_path="All / Home / Furniture", text="the perfect gifting idea"), rules, S)[0] == "true"
    assert giftable(facts(category_path="All / Home / Furniture", accepted={"is_set": ["true"]}), rules, S)[0] == "true"


def test_gift_occasions_rank_keywords_and_limit():
    rules = GiftRules(occasions=[("birthday", "Women / Bags", 3, []), ("Eid", "Women / Bags", 2, []),
                                 ("wedding", "Women", 4, []), ("Christmas", "", 1, ["christmas", "advent"])])
    assert [o for o, _ in gift_occasions(facts(), rules, 3)] == ["Eid", "birthday", "wedding"]
    assert [o for o, _ in gift_occasions(facts(text="christmas edit"), rules, 3)] == ["Christmas", "Eid", "birthday"]
    got, _, _ = derived(facts(text="a christmas gift"), GiftRules(giftable=[("Women / Bags", True)],
                                                                  occasions=rules.occasions))
    assert ("giftable", "true") in got and ("gift_occasion", "Christmas") in got
    got, _, _ = derived(facts(), GiftRules(giftable=[("Women / Bags", False)], occasions=rules.occasions))
    assert ("giftable", "false") in got and not any(f == "gift_occasion" for f, _ in got)


def test_exclusivity_and_function_additions():
    got, remove, rows = derived(facts(is_local_designer=True))
    assert ("exclusivity", "local designer") in got and remove == ["exclusivity"]
    assert all(r.evidence == "rule" and r.status == "auto" and r.confidence is None
               and r.reason.startswith("derive:") for r in rows)
    got, _, _ = derived(facts(category_path="Women / Clothing / Swimwear / Bikinis"))
    assert ("function", "swim") in got


def test_only_facets_mapped_to_the_category_are_written():
    p = facts(launch_date=TODAY, brand_tier="luxury")
    for f in ("recency", "brand_tier"):
        del p.facets[f]
    got = {f for f, _ in derived(p)[0]}
    assert "recency" not in got and "brand_tier" not in got


def test_function_category_matches_a_path_segment():
    fc = {"Swimwear": "swim", "Activewear": "active"}
    assert function_category("Women / Clothing / Activewear / Leggings", fc) == "active"
    assert function_category("Women / Clothing / Dresses", fc) is None


# ------------------------------------------------------------------ scoring

def test_perfect_predictions_score_100_and_a_wrong_value_shows_as_confusion():
    golden = {"p1": {"occasion": GoldAnswer("value", {1, 2}), "length": GoldAnswer("value", {7}),
                     "strap": GoldAnswer("not_applicable"), "metal": GoldAnswer("unknown")},
              "p2": {"length": GoldAnswer("value", {8})}}
    pred = {"p1": {"occasion": [PredRow("auto", 1, 0.9, "image"), PredRow("auto", 2, 0.8, "image")],
                   "length": [PredRow("auto", 7, 0.95, "text")], "strap": [PredRow("not_applicable")],
                   "metal": [PredRow("abstained")]},
            "p2": {"length": [PredRow("auto", 8, 0.92, "image")]}}
    m = {(x.scope, x.metric): x.value for x in score_tags(golden, pred)}
    for f in ("occasion", "length", "strap", "metal"):
        assert m[(f, "accuracy")] == 1.0
    assert m[("overall", "accuracy")] == m[("overall", "precision")] == m[("overall", "recall")] == 1.0
    assert m[("overall", "unknown_agreement")] == 1.0

    pred["p2"]["length"] = [PredRow("auto", 9, 0.92, "image")]
    out = score_tags(golden, pred, lambda f, k: f"v{k}")
    m = {(x.scope, x.metric): x for x in out}
    assert m[("length", "accuracy")].value == 0.5
    assert m[("length", "confusions")].detail["pairs"] == [["v8", "v9", 1]]
    assert m[("evidence:image", "calibration_0.9-1.0")].value == 0.5        # one right (0.90), one wrong (0.92)


def test_category_scores():
    g = {"a": "X", "b": "Y", "c": "Z", "d": None}
    r = {"a": {"category_id": "X", "category_status": "auto"},
         "b": {"category_id": "Q", "category_runner_up_id": "Y", "category_status": "review"},
         "c": {"category_id": "Z", "category_status": "auto"}}
    m = {x.metric: x.value for x in score_categories(g, r)}
    assert m["accuracy_exact"] == pytest.approx(2 / 3) and m["accuracy_match_or_runner_up"] == 1.0
    assert m["review_rate"] == pytest.approx(1 / 3) and m["accuracy_accepted"] == 1.0
    r["c"]["category_status"] = "flagged"             # tagged, category flagged: counted apart from accepted
    m = {x.metric: x.value for x in score_categories(g, r)}
    assert m["accepted_rate"] == pytest.approx(1 / 3) and m["tagged_category_flagged_rate"] == pytest.approx(1 / 3)
    assert m["accuracy_flagged"] == 1.0 and m["review_rate"] == pytest.approx(1 / 3)


def test_gate_flags_a_top_pick_whose_runner_up_has_the_same_facets():
    ids = ["A", "B", "C"]
    same = lambda a, b: {a, b} == {"A", "B"}
    reply = lambda ru: {"category_id": "A", "runner_up_id": ru, "confidence": 0.6, "reason": "x"}
    assert decide_category(reply("B"), ids, 0.7, same)["category_status"] == "flagged"
    assert decide_category(reply("C"), ids, 0.7, same)["category_status"] == "review"
    assert decide_category(reply(None), ids, 0.7, same)["category_status"] == "review"
    assert decide_category({**reply("C"), "confidence": 0.7}, ids, 0.7, same)["category_status"] == "auto"


# ------------------------------------------------------------------ golden, config

def test_golden_answers_are_validated():
    s = spec()
    assert parse_answer(s, "value", "work; evening")[2] is None
    assert "not an allowed value" in parse_answer(s, "value", "disco")[2]
    assert "none" in parse_answer(s, "value", "none; work")[2]
    assert parse_answer(s, "not applicable", "")[0] == "not_applicable"
    assert parse_answer(s, "unknown", "work")[2]
    assert "at most" in parse_answer(spec(max_values=1), "value", "work; evening")[2]


def test_allocation_has_a_floor_per_department():
    alloc = allocate({"Clothing": 100, "Shoes": 45, "Bags": 25, "Watches": 5, "Home": 2}, 50)
    assert sum(alloc.values()) == 50 and alloc["Watches"] >= 3 and alloc["Home"] == 2
    assert alloc["Clothing"] > alloc["Shoes"] > alloc["Bags"]


def test_profiles_and_unknown_profile_name():
    s = load_settings(ROOT / "config")
    assert set(s.profiles) >= {"haiku", "sonnet", "opus"}
    assert s.categorise_model == "haiku" and s.tag_model == "sonnet"
    with pytest.raises(ConfigError, match="valid names: haiku, opus, sonnet"):
        s.profile("gpt")
    assert price_tokens(1_000_000, 1_000_000, 0, 0, 2, 10, None, None) == 12


def test_no_model_name_or_price_in_code():
    code = "\n".join(p.read_text() for p in (ROOT / "src" / "tagger").rglob("*.py"))
    assert not re.search(r"claude-(haiku|sonnet|opus|fable)", code)
    assert not re.search(r"(model_id|_price)\s*=\s*['\"\d]", code)     # ids and prices come from models.toml


def test_default_value_needs_no_quote_and_is_accepted():
    excl = spec("exclusivity", "text_only", ("Ounass-exclusive", "limited edition", "none"), None, None,
                on_missing="default", default="none")
    for evidence, quote in (("none", ""), ("text", "not in the text at all")):
        a = check_answer(excl, ans(values=("none",), evidence=evidence, quote=quote, conf=0.6), TEXT)
        assert a.state == "value" and not a.force_review and "quote not found in text" not in a.notes
        assert {r.status for r in rows_for(excl, a)} == {"auto"}
    # a non-default value on the same facet still needs its quote
    a = check_answer(excl, ans(values=("limited edition",), evidence="text", quote="numbered"), TEXT)
    assert {r.status for r in rows_for(excl, a)} == {"review"}


def test_facet_scorecard_rows_and_bands():
    from tagger.evaluate import confidence_bands, facet_scorecard
    assert confidence_bands([0.5, 0.75, 0.85, 0.95]) == {"below 0.7": 0.25, "0.7-0.8": 0.25, "0.8-0.9": 0.25,
                                                         "0.9-1.0": 0.25}
    golden = {f"p{i}": {"colour": GoldAnswer("value", {1})} for i in range(25)}
    golden["p0"]["fit"] = GoldAnswer("value", {7})
    pred = {f"p{i}": {"colour": [PredRow("auto", 1, 0.95, "text")]} for i in range(25)}
    pred["p0"]["fit"] = [PredRow("review", 7, 0.72, "image")]
    rows = facet_scorecard({"metrics": score_tags(golden, pred)})
    by = {r["facet"]: r for r in rows}
    assert by["colour"]["answers"] == 25 and by["colour"]["sample"] == "" and by["colour"]["precision"] == 1.0
    assert by["colour"]["share 0.9-1.0"] == 1.0 and by["colour"]["confidence_min"] == 0.95
    assert by["fit"]["sample"] == "small sample" and by["fit"]["review_rate"] == 1.0
    assert by["fit"]["share 0.7-0.8"] == 1.0 and [r["facet"] for r in rows] == ["colour", "fit"]


def test_coverage_counts_golden_values_and_recall_never_exceeds_it():
    """Coverage (14 Nov 2026): golden values whose facet got an accepted value / golden values that exist."""
    golden = {"p1": {"a": GoldAnswer("value", {1, 2, 3}), "b": GoldAnswer("value", {9}),
                     "c": GoldAnswer("unknown")}}
    pred = {"p1": {"a": [PredRow("auto", 1, 0.9), PredRow("auto", 2, 0.9), PredRow("auto", 3, 0.9)],
                   "b": [PredRow("review", 9, 0.6)],
                   "c": [PredRow("auto", 5, 0.95)]}}
    m = {(x.scope, x.metric): x for x in score_tags(golden, pred)}
    assert m[("overall", "coverage")].value == 3 / 4          # a's three values covered; b in review; c not counted
    assert m[("overall", "recall")].value == 3 / 4
    assert m[("overall", "recall")].value <= m[("overall", "coverage")].value
    assert m[("c", "coverage")].value is None                 # no golden value: nothing to count
