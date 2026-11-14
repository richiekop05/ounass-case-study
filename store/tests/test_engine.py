"""Richie's engine (engineering PRD 9, tests): filter rules exclude what they should, scoring is deterministic,
weights rescale when a signal is dropped, variety caps hold. Pure: no database."""
from __future__ import annotations

import random

import pytest

from richies import engine as E

ORDER = ["casual", "smart_casual", "smart", "formal", "black_tie"]
RULES = E.Rules(ORDER, {}, {}, {("black", "pink"): 1.0, ("blue", "pink"): 0.7, ("pink", "pink"): 0.8,
                               ("green", "pink"): 0.3}, {})


def P(id, brand="A", price=1000.0, role="one_piece", **tags):
    conf = tags.pop("conf", {})
    sample = tags.pop("sample", False)
    stock = tags.pop("stock", True)
    return E.Product(id, f"Item {id}", brand, price, "Women", role,
                     {k: tuple(v) if isinstance(v, (list, tuple)) else (v,) for k, v in tags.items()},
                     conf, in_stock=stock, sample=sample)


# ------------------------------------------------------------------ filter

def test_each_filter_rule_excludes_what_it_should_and_says_which():
    ps = [P("a", occasion="evening", formality="formal"), P("b", occasion="work", formality="smart"),
          P("c", occasion="evening", formality="casual"), P("d", occasion="evening", formality="formal", stock=False)]
    rules = [("in stock", lambda p: p.in_stock),
             ("occasion", lambda p: "evening" in p.values("occasion")),
             ("formality", lambda p: E.formality_in_range(RULES, p, "smart", "black_tie"))]
    kept, why = E.filter(ps, rules)
    assert [p.id for p in kept] == ["a"]
    assert why == {"b": "occasion", "c": "formality", "d": "in stock"}


def test_seasons_all_season_is_compatible_with_everything():
    assert E.seasons_compatible(["all_season"], ["cold"])
    assert E.seasons_compatible(["warm"], ["all_season"])
    assert E.seasons_compatible(["warm", "transitional"], ["transitional"])
    assert not E.seasons_compatible(["warm"], ["cold"])
    assert E.seasons_compatible([], ["cold"])                 # no season tag: not excluded


def test_more_covered_needs_long_sleeves_midi_or_longer_and_not_sheer_skipping_facets_that_do_not_apply():
    assert E.more_covered(P("a", sleeve_length="long", length="maxi", sheerness="opaque"))
    assert not E.more_covered(P("b", sleeve_length="short", length="maxi"))
    assert not E.more_covered(P("c", sleeve_length="long", length="mini"))
    assert not E.more_covered(P("d", sleeve_length="long", length="midi", sheerness="sheer"))
    assert E.more_covered(P("e", role="bag"))                 # none of the facets apply


# ------------------------------------------------------------------ score

def test_scores_are_deterministic_whatever_the_input_order():
    ps = [P(str(i), occasion=["evening"] if i % 2 else ["work", "evening"], formality=ORDER[i % 5])
          for i in range(20)]
    sig = {"fit": lambda p: E.occasion_fit(p, "evening"),
           "centre": lambda p: E.formality_centre(RULES, p, "smart", "black_tie")}
    w = {"fit": 0.6, "centre": 0.4}
    a = [(r.product.id, r.score) for r in E.score(ps, sig, w)]
    shuffled = ps[:]
    random.Random(7).shuffle(shuffled)
    assert [(r.product.id, r.score) for r in E.score(shuffled, sig, w)] == a
    ties = [x for x in a if x[1] == a[0][1]]
    assert ties == sorted(ties, key=lambda x: x[0])           # ties break on the ID


def test_a_dropped_signal_rescales_the_other_weights():
    p = P("a", occasion="evening")
    sig = {"fit": lambda p: 1.0, "taste": lambda p: None, "centre": lambda p: 0.5}
    r = E.score([p], sig, {"fit": 0.4, "taste": 0.35, "centre": 0.25})[0]
    assert "taste" not in r.signals
    assert r.score == pytest.approx((0.4 * 1.0 + 0.25 * 0.5) / (0.4 + 0.25))
    full = E.score([p], {"fit": lambda p: 1.0, "taste": lambda p: 1.0, "centre": lambda p: 0.5},
                   {"fit": 0.4, "taste": 0.35, "centre": 0.25})[0]
    assert full.score == pytest.approx(0.4 + 0.35 + 0.125)


def test_signals_measure_what_the_prd_says():
    assert E.occasion_fit(P("a", occasion=["evening", "work"]), "evening") == 1.0
    assert E.occasion_fit(P("b", occasion=["work", "evening"]), "evening") == 0.7
    lo, hi = "smart", "black_tie"                                   # levels 2..4, midpoint 3 (formal)
    assert E.formality_centre(RULES, P("c", formality="formal"), lo, hi) == 1.0
    assert E.formality_centre(RULES, P("d", formality="smart"), lo, hi) == 0.5
    assert E.formality_centre(RULES, P("e"), lo, hi) is None      # no formality tag: signal dropped
    assert E.tag_confidence(P("f", conf={"occasion": 0.8, "formality": 0.6})) == pytest.approx(0.7)
    assert E.tag_confidence(P("g", sample=True, conf={"occasion": 0.2})) == 1.0
    anchor = P("h", price=1000)
    assert E.price_fit(anchor, P("i", price=900)) == 1.0
    assert E.price_fit(anchor, P("j", price=2000)) == pytest.approx(0.5)
    assert E.price_fit(anchor, P("k", price=3000)) == 0.0
    assert RULES.colour_score("pink", "black") == 1.0 and RULES.colour_score("pink", "blue") == 0.7
    assert RULES.colour_score("pink", "purple") == 0.3                # not listed: everything else
    assert E.occasion_overlap(P("l", occasion=["evening", "work"]), P("m", occasion=["work"])) == 0.5


# ------------------------------------------------------------------ diversify

def test_variety_at_most_two_per_brand_and_per_colour_and_stops_at_the_limit():
    ps = [P(f"{i:02d}", brand="X" if i < 5 else f"B{i}", colour_primary="black" if i % 2 else "pink")
          for i in range(20)]
    ranked = E.score(ps, {"s": lambda p: 1 - int(p.id) / 100}, {"s": 1})
    caps = {"brand": (lambda p: p.brand, 2), "colour": (lambda p: p.first("colour_primary"), 2)}
    out = E.diversify(ranked, 8, caps)
    brands = [r.product.brand for r in out]
    colours = [r.product.first("colour_primary") for r in out]
    assert brands.count("X") <= 2 and colours.count("black") <= 2 and colours.count("pink") <= 2
    assert len(out) <= 8
    assert out == [r for r in ranked if r in out]             # order kept from the ranking
