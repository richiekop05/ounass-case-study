"""Complete the Look (engineering PRD 6 and 9): slots per anchor role, the anchor never in its own look, the
colour rule, no look without an outfit role, alternates, high intent. Pure: synthetic products."""
from __future__ import annotations

from richies import engine as E
from richies.features import build_look

ORDER = ["casual", "smart_casual", "smart", "formal", "black_tie"]
TEMPLATES = {"one_piece": ["shoes", "bag", "jewellery"], "top": ["bottom", "shoes", "bag"],
             "bottom": ["top", "shoes", "bag"], "outerwear": ["one_piece", "shoes", "bag"],
             "shoes": ["one_piece", "bag", "jewellery"], "bag": ["one_piece", "shoes", "jewellery"],
             "jewellery": ["one_piece", "shoes", "bag"], "accessory": ["one_piece", "shoes", "bag"]}
NEUTRAL = frozenset({"black", "white", "gold"})
COLOURS = ["black", "white", "gold", "red", "blue", "green", "pink"]
PAIRS = {(min(a, b), max(a, b)): (1.0 if a in NEUTRAL or b in NEUTRAL else 0.8 if a == b else 0.3)
         for a in COLOURS for b in COLOURS}
WEIGHTS = {"look": {"colour": .30, "formality": .20, "occasion": .15, "aesthetic": .15, "taste": .15, "price": .05}}
RULES = E.Rules(ORDER, {}, TEMPLATES, PAIRS, WEIGHTS, NEUTRAL)


def P(id, role, colour="black", formality="formal", occasion=("evening",), aesthetic="minimalist", price=1000,
      audience="Women", stock=True, season=("all_season",)):
    return E.Product(id, id, f"brand-{id}", price, audience, role,
                     {"colour_primary": (colour,), "formality": (formality,), "occasion": tuple(occasion),
                      "aesthetic": (aesthetic,), "season": tuple(season)}, {}, in_stock=stock)


def catalogue():
    ps = []
    for role in ("one_piece", "top", "bottom", "outerwear", "shoes", "bag", "jewellery", "accessory"):
        for i, colour in enumerate(COLOURS):
            ps.append(P(f"{role}-{i}", role, colour=colour))
    return ps


def test_every_anchor_role_gets_its_template_slots_and_never_itself():
    ps = catalogue()
    for role, slots in TEMPLATES.items():
        anchor = next(p for p in ps if p.role == role)
        look = build_look(anchor, ps, RULES)
        assert [s["slot"] for s in look["slots"]] == slots, role
        ids = {it["product"].id for s in look["slots"] for it in s["items"]}
        assert anchor.id not in ids
        assert all(len(s["items"]) <= 5 for s in look["slots"])          # chosen + up to four alternates


def test_no_outfit_role_no_look():
    ps = catalogue()
    assert build_look(P("perfume", "none"), ps, RULES)["slots"] == []


def test_at_most_two_non_neutral_colour_families_across_the_look():
    # anchor red; candidates in every slot prefer distinct strong colours, so the rule must skip some
    ps = [P("a", "one_piece", colour="red")]
    for slot, colours in (("shoes", ["blue", "white"]), ("bag", ["green", "black"]), ("jewellery", ["pink", "gold"])):
        for i, c in enumerate(colours):
            ps.append(P(f"{slot}-{c}", slot, colour=c, price=500 if i == 0 else 900))
    look = build_look(ps[0], ps, RULES)
    chosen = [s["items"][0]["product"].first("colour_primary") for s in look["slots"]]
    strong = {"red"} | {c for c in chosen if c not in NEUTRAL}
    assert len(strong) <= 2


def test_filters_department_formality_stock_and_occasion():
    anchor = P("a", "one_piece", formality="formal", occasion=("evening",))
    ps = [anchor,
          P("ok", "shoes", formality="smart"),                              # one step: kept
          P("far", "shoes", formality="casual"),                            # three steps: out
          P("men", "shoes", audience="Men"),                                # other department: out
          P("gone", "shoes", stock=False),                                  # out of stock: out
          P("day", "shoes", occasion=("everyday",)),                        # no shared occasion: out
          P("cold", "shoes", season=("cold",))]                             # anchor is all-season: kept
    look = build_look(anchor, ps, RULES)
    ids = {it["product"].id for s in look["slots"] for it in s["items"]}
    assert ids == {"ok", "cold"}
    carried = build_look(anchor, ps + [P("work", "shoes", occasion=("work", "evening"))], RULES, occasion="work")
    assert {it["product"].id for s in carried["slots"] for it in s["items"]} == {"work"}


def test_only_filled_slots_and_high_intent_has_no_alternates_or_reasons():
    anchor = P("a", "one_piece")
    ps = [anchor, P("s1", "shoes"), P("s2", "shoes", colour="white")]
    look = build_look(anchor, ps, RULES)
    assert look["filled"] == 1 and look["slots"][0]["slot"] == "shoes" and len(look["slots"][0]["items"]) == 2
    hi = build_look(anchor, ps, RULES, high_intent=True)
    assert len(hi["slots"][0]["items"]) == 1 and hi["slots"][0]["items"][0]["reason"] is None


def test_deterministic():
    ps = catalogue()
    a = next(p for p in ps if p.role == "top")
    one = build_look(a, ps, RULES)
    two = build_look(a, list(reversed(ps)), RULES)
    ids = lambda l: [[it["product"].id for it in s["items"]] for s in l["slots"]]
    assert ids(one) == ids(two)
