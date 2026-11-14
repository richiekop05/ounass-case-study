"""Taste profile and intent (engineering PRD 7 and 9). Pure: synthetic products and events."""
from __future__ import annotations

import pytest

from richies import engine as E
from richies.profile import Event, intent, profile, taste_match


def P(id, aesthetic, colour, brand="B", formality="smart"):
    return E.Product(id, id, brand, 1000, "Women", "one_piece",
                     {"aesthetic": (aesthetic,), "colour_primary": (colour,), "formality": (formality,)}, {})


PRODUCTS = {p.id: p for p in [P("min-black", "minimalist", "black"), P("min-white", "minimalist", "white"),
                              P("bold-gold", "bold_statement", "gold", brand="C"), P("rom-pink", "romantic", "pink")]}


def ev(t, pid=None, seed=False, **ctx):
    return Event(t, pid, ctx, seed)


def test_no_events_means_no_profile_and_no_taste_signal():
    assert profile([], PRODUCTS) is None
    assert profile([ev("size_select")], PRODUCTS) is None            # an event without a weight builds nothing
    assert taste_match(None, PRODUCTS["min-black"]) is None


def test_values_sum_to_one_per_facet_and_session_events_count_double():
    prof = profile([ev("purchase", "min-black", seed=True), ev("view", "bold-gold")], PRODUCTS)
    assert sum(prof["aesthetic"].values()) == pytest.approx(1.0)
    # seed purchase 3 vs session view 1 x 2 = 2
    assert prof["aesthetic"]["minimalist"] == pytest.approx(3 / 5)
    assert prof["aesthetic"]["bold_statement"] == pytest.approx(2 / 5)


def test_a_swap_out_lowers_a_value_and_never_takes_it_below_zero():
    base = [ev("view", "bold-gold"), ev("view", "min-black")]
    before = profile(base, PRODUCTS)["aesthetic"]["bold_statement"]
    after = profile(base + [ev("swap_out", "bold-gold")], PRODUCTS)["aesthetic"]
    assert after.get("bold_statement", 0) < before
    many = profile(base + [ev("swap_out", "bold-gold")] * 5, PRODUCTS)["aesthetic"]
    assert "bold_statement" not in many and all(v >= 0 for v in many.values())


def test_taste_match_scores_the_best_liked_value_one():
    prof = profile([ev("purchase", "min-black", seed=True), ev("view", "rom-pink", seed=True)], PRODUCTS)
    assert taste_match(prof, PRODUCTS["min-black"]) == pytest.approx(1.0)
    assert taste_match(prof, PRODUCTS["rom-pink"]) < taste_match(prof, PRODUCTS["min-white"]) <= 1.0


def test_a_size_selection_or_a_plp_filter_makes_intent_high_a_refinement_does_not():
    assert intent([]) == ("low", "No filter, size or search signal this session")
    assert intent([ev("refine_apply", context={"refine": "budget"})])[0] == "low"
    assert intent([ev("occasion_select")])[0] == "low"
    assert intent([ev("size_select")]) == ("high", "Selected a size")
    assert intent([ev("filter_apply")])[0] == "high"
    assert intent([ev("search_referral", "min-black")])[0] == "high"
    assert intent([ev("size_select", seed=True)])[0] == "low"       # only this session counts
