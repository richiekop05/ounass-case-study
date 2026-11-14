"""Looks for you eligibility, the PLP lead module and the persona signals (engineering PRD V5, 14 Nov).

The rule tests are pure; the persona tests read the persona file and the approved catalogue.
"""
from __future__ import annotations

import json
import os

import pytest
from dotenv import load_dotenv

from richies.lead import (check_signal, does_line, eligibility, lead_module, occasion_in_play, positive_count,
                          starting_events)
from richies.profile import Event, intent
from richies.rules import ROOT

load_dotenv()
PERSONAS = json.loads((ROOT / "store" / "sample_data" / "personas.json").read_text())["personas"]


def ev(t, pid="p1", seed=False, **ctx):
    return Event(t, pid, ctx, seed)


# ------------------------------------------------------------------ eligibility

def test_a_session_with_no_events_is_not_eligible_and_becomes_eligible_after_three_positive_events():
    assert eligibility("low", [])["eligible"] is False
    two = [ev("view"), ev("save")]
    assert eligibility("low", two) | {} == eligibility("low", two)
    assert eligibility("low", two)["reason"] == "2 of 3 positive signals this session"
    three = two + [ev("swap_in")]
    e = eligibility("low", three)
    assert e["eligible"] and e["code"] == "taste_profile" and e["count"] == 3


def test_negative_and_non_product_events_do_not_count():
    evs = [ev("swap_out"), ev("size_select", None), ev("view", None), ev("refine_apply", None), ev("view")]
    assert positive_count(evs) == 1


def test_a_high_intent_session_is_never_eligible():
    many = [ev("purchase", seed=True)] * 10
    e = eligibility("high", many)
    assert e["eligible"] is False and e["never"] and e["code"] == "high_intent"


def test_one_seed_purchase_and_an_occasion_in_play_is_eligible_with_occasion_led_looks():
    evs = [ev("purchase", seed=True), ev("entry", None, source="notification", occasion="evening")]
    e = eligibility("low", evs)
    assert e["eligible"] and e["code"] == "occasion_in_play" and e["occasion"] == "evening"
    # a chosen occasion overrides the one the shopper arrived with
    assert occasion_in_play(evs + [ev("occasion_select", None, occasion="work")]) == ("work", "chosen")


def test_the_threshold_is_a_setting():
    evs = [ev("view")] * 4
    assert eligibility("low", evs, threshold=5)["eligible"] is False
    assert eligibility("low", evs, threshold=4)["eligible"] is True


# ------------------------------------------------------------------ lead module

def test_lead_module_rules_in_order():
    assert lead_module("high", [ev("view")] * 9)["module"] == "none"
    entry = ev("entry", None, source="notification", occasion="evening")
    lead = lead_module("low", [ev("view")] * 9 + [entry])
    assert lead["module"] == "occasion_picker" and lead["suggestedOccasion"] == "evening"
    assert lead_module("low", [ev("view")] * 3)["module"] == "looks"
    first = lead_module("low", [], department_known=False)
    assert first["module"] == "occasion_picker" and first["showOpener"] and first["suggestedOccasion"] is None
    assert lead_module("low", [], department_known=True)["showOpener"] is False


def test_never_a_top_looks_row_and_a_full_picker_together():
    for state in ("low", "high"):
        for evs in ([], [ev("view")] * 3, [ev("entry", None, occasion="evening")]):
            for known in (True, False):
                lead = lead_module(state, evs, department_known=known)
                assert lead["module"] in ("none", "looks", "occasion_picker")      # exactly one


def test_james_moves_from_the_picker_with_the_opener_to_looks_after_three_positive_events():
    james = next(p for p in PERSONAS if p["id"] == "james")
    evs = starting_events(james["events"], james["session_start"])
    lead = lead_module(intent([e for e in evs if not e.is_seed])[0], evs, department_known=False)
    assert lead["module"] == "occasion_picker" and lead["showOpener"]
    evs += [ev("view", "a"), ev("save", "b"), ev("swap_in", "c")]
    assert lead_module("low", evs, department_known=False)["module"] == "looks"


# ------------------------------------------------------------------ personas: computed, not typed

EXPECTED = {"layla": ("low", "looks"), "noor": ("low", "occasion_picker"), "james": ("low", "occasion_picker"),
            "sara": ("high", "none")}


@pytest.mark.parametrize("p", PERSONAS, ids=lambda p: p["id"])
def test_each_personas_computed_starting_intent_and_lead_match_the_design(p):
    evs = starting_events(p["events"], p.get("session_start", []))
    state = intent([e for e in evs if not e.is_seed])[0]
    lead = lead_module(state, evs, department_known=p["department_default"] != "all")
    assert (state, lead["module"]) == EXPECTED[p["id"]]
    assert state == p["starting_intent"]          # the stored column agrees with the rule
    assert does_line(lead)


def test_no_age_appears_anywhere_in_the_persona_data():
    import re
    for p in PERSONAS:
        shown = [p["name"], p["full_name"], p.get("archetype") or "", p.get("description") or ""] + \
                [s["text"] for s in p["starting_signals"]]
        assert not any(re.search(r"\b\d{2}\b", t) for t in shown), p["id"]
        assert "age" not in p


@pytest.mark.skipif(not os.environ.get("SUPABASE_DB_URL"), reason="SUPABASE_DB_URL not set")
@pytest.mark.parametrize("p", PERSONAS, ids=lambda p: p["id"])
def test_every_displayed_signal_has_matching_starting_events(p):
    """A persona whose stored signals describe a behaviour with no matching event fails, so the switcher's lines
    can never drift from the data."""
    import psycopg
    from scraper.store import normalize_db_url
    with psycopg.connect(normalize_db_url(os.environ["SUPABASE_DB_URL"])) as conn:
        roles = dict(conn.execute("""select p.product_id, o.role from v_catalog_products p
                                       left join store_outfit_roles o on o.category_node_id = p.category_id"""))
        occ: dict[str, list[str]] = {}
        for pid, v in conn.execute("select product_id, value from v_product_facets where facet_id = 'occasion'"):
            occ.setdefault(pid, []).append(v)
    evs = starting_events(p["events"], p.get("session_start", []))
    assert len(p["starting_signals"]) in (3, 4)
    for s in p["starting_signals"]:
        assert check_signal(s["check"], evs, roles.get, lambda pid: tuple(occ.get(pid, ()))), \
            f"{p['id']}: '{s['text']}' has no matching starting event ({s['check']})"
