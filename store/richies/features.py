"""The two features, as entry points into the engine (engineering PRD 5 and 6). They take products, rules, a
context and a taste function, and return plain data; the server turns reason codes into words.

`taste(product) -> float | None` comes from the shopper's profile (step 6); None means no profile, so the taste
signal is dropped and the other weights rescaled.
"""
from __future__ import annotations

from typing import Callable

from . import engine as E

Taste = Callable[[E.Product], "float | None"]
NO_TASTE: Taste = lambda p: None          # noqa: E731
ALTERNATES = 4


def _no_look(reason: str) -> dict:
    return {"slots": [], "filled": 0, "reason": reason}


def build_look(anchor: E.Product, products: list[E.Product], rules: E.Rules, occasion: str | None = None,
               taste: Taste = NO_TASTE, high_intent: bool = False) -> dict:
    """One chosen item per slot plus up to four alternates per slot, each with the signals behind it.
    Slots with no candidate are left out; `filled` says how many remain."""
    slots = rules.templates.get(anchor.role) if anchor.role != "none" else None
    if not slots:
        return _no_look("no outfit role")
    weights = rules.weights.get("look", {})
    anchor_lvl = rules.level(anchor.first("formality"))

    def rules_for(slot: str) -> list[E.Rule]:
        rs: list[E.Rule] = [
            ("in stock", lambda p: p.in_stock and p.approved),
            ("slot role", lambda p: p.role == slot),
            ("not the anchor", lambda p: p.id != anchor.id),
            ("department", lambda p: p.audience == anchor.audience),
            ("formality", lambda p: (lambda d: d is None or d <= 1)(E.formality_steps(rules, anchor, p))),
            ("season", lambda p: E.seasons_compatible(anchor.values("season"), p.values("season"))),
        ]
        if occasion:
            rs.append(("occasion", lambda p: not p.values("occasion") or occasion in p.values("occasion")))
        else:
            rs.append(("occasion", lambda p: not p.values("occasion") or not anchor.values("occasion")
                       or bool(set(p.values("occasion")) & set(anchor.values("occasion")))))
        return rs

    def formality_close(p: E.Product):
        d = E.formality_steps(rules, anchor, p)
        return None if d is None else (1.0 if d == 0 else 0.6)

    def aesthetic(p: E.Product):
        a, b = set(anchor.values("aesthetic")), set(p.values("aesthetic"))
        return None if not a or not b else (1.0 if a & b else 0.4)

    signals = {
        "colour": lambda p: rules.colour_score(anchor.first("colour_primary"), p.first("colour_primary")),
        "formality": formality_close,
        "occasion": lambda p: E.occasion_overlap(anchor, p),
        "aesthetic": aesthetic,
        "taste": taste,
        "price": lambda p: E.price_fit(anchor, p),
    }
    neutral = lambda c: c is None or c in rules.neutrals          # noqa: E731
    families = {anchor.first("colour_primary")} - {None} if not neutral(anchor.first("colour_primary")) else set()
    out = []
    for slot in slots:
        kept, _ = E.filter(products, rules_for(slot))
        ranked = E.score(kept, signals, weights)
        if not ranked:
            continue
        # look-level rule: at most two non-neutral colour families across the anchor and the chosen items
        chosen = next((r for r in ranked
                       if neutral(r.product.first("colour_primary"))
                       or len(families | {r.product.first("colour_primary")}) <= 2), None)
        if chosen is None:
            continue
        if not neutral(chosen.product.first("colour_primary")):
            families.add(chosen.product.first("colour_primary"))
        rest = [r for r in ranked if r is not chosen][:0 if high_intent else ALTERNATES]
        out.append({"slot": slot, "items": [_item(r, high_intent) for r in [chosen, *rest]]})
    return {"slots": out, "filled": len(out), "reason": None if out else "no candidates"}


def _item(r: E.Ranked, high_intent: bool) -> dict:
    return {"product": r.product, "score": r.score, "signals": r.signals,
            "reason": None if high_intent else r.strongest(prefer=("taste", "occasion"))}


# ------------------------------------------------------------------ occasion assistant (engineering PRD 5)

BUDGET = 3000.0          # "Under AED 3,000"
SHORTLIST = 8


def hero_rules(rules: E.Rules, occasion: str, audiences: set[str] | None, refine: dict | None = None) -> list[E.Rule]:
    prof = rules.occasions[occasion]
    refine = refine or {}
    rs: list[E.Rule] = [
        ("in stock", lambda p: p.in_stock and p.approved),
        ("department", lambda p: audiences is None or p.audience in audiences),
        ("hero role", lambda p: p.role in prof["hero_roles"]),
        ("occasion", lambda p: occasion in p.values("occasion")),
        ("formality", lambda p: E.formality_in_range(rules, p, prof["formality_min"], prof["formality_max"])),
        ("season", lambda p: E.seasons_compatible(p.values("season"), prof["seasons"])),
    ]
    if refine.get("budget"):
        rs.append(("budget", lambda p: p.price < BUDGET))
    if refine.get("neutral"):
        rs.append(("colour family", lambda p: p.first("colour_primary") in rules.neutrals))
    if refine.get("covered"):
        rs.append(("more covered", E.more_covered))
    return rs


def eligible(products, rules, occasion, audiences, refine=None) -> list[E.Product]:
    kept, _ = E.filter(products, hero_rules(rules, occasion, audiences, refine))
    return kept


def available_occasions(products, rules, audiences, minimum: int = 4) -> list[str]:
    """Active occasions with at least `minimum` eligible hero pieces for the department."""
    return [o for o, prof in sorted(rules.occasions.items()) if prof["active"]
            and len(eligible(products, rules, o, audiences)) >= minimum]


def occasion_shortlist(occasion: str, products, rules: E.Rules, audiences: set[str] | None,
                       refine: dict | None = None, taste: Taste = NO_TASTE) -> list[dict]:
    """Up to eight hero pieces, ranked, at most two per brand and per primary colour, with up to three reasons:
    the occasion, the strongest other matching tag (formality or season), and taste when it is above 0.6."""
    prof = rules.occasions[occasion]
    kept = eligible(products, rules, occasion, audiences, refine)
    signals = {
        "occasion_fit": lambda p: E.occasion_fit(p, occasion),
        "taste": taste,
        "formality_centre": lambda p: E.formality_centre(rules, p, prof["formality_min"], prof["formality_max"]),
        "tag_confidence": E.tag_confidence,
    }
    ranked = E.score(kept, signals, rules.weights.get("shortlist", {}))
    picked = E.diversify(ranked, SHORTLIST, {"brand": (lambda p: p.brand, 2),
                                            "colour": (lambda p: p.first("colour_primary"), 2)})
    out = []
    for r in picked:
        p = r.product
        reasons = [{"code": "occasion", "value": occasion}]
        centre = r.signals.get("formality_centre")
        season = [s for s in p.values("season") if s in prof["seasons"] or s == "all_season"]
        if centre is not None and centre >= 0.75 and p.first("formality"):
            reasons.append({"code": "formality", "value": p.first("formality")})
        elif season and season[0] != "all_season":
            reasons.append({"code": "season", "value": season[0]})
        elif p.first("formality"):
            reasons.append({"code": "formality", "value": p.first("formality")})
        if (r.signals.get("taste") or 0) > 0.6:
            reasons.append({"code": "taste", "value": p.first("aesthetic")})
        out.append({"product": p, "score": r.score, "signals": r.signals, "reasons": reasons[:3]})
    return out


def looks_for_you(products, rules: E.Rules, audiences, taste: Taste, count: int = 3, exclude: str | None = None,
                  occasion: str | None = None) -> list[dict]:
    """Looks for an eligible shopper (engineering PRD V5): each a hero piece, a look built for it, and the occasion.

    With an occasion in play, the heroes are the top of that occasion's shortlist. Otherwise, with a taste
    profile, the occasions with the highest mean taste match each give their top hero; once every occasion has
    given one, the next heroes follow in the same order. No hero repeats; `exclude` (the product on the page)
    is never a hero or a look item. A look needs at least two pieces besides the hero."""
    def look_for(hero, occ):
        if hero.id == exclude:
            return None
        lk = build_look(hero, products, rules, occ, taste)
        items = [s["items"][0]["product"].id for s in lk["slots"]]
        if lk["filled"] < 2 or exclude in items:
            return None
        return {"occasion": occ, "hero": hero, "look": lk}

    out, used = [], set()
    if occasion and occasion in rules.occasions:
        for x in occasion_shortlist(occasion, products, rules, audiences, taste=taste):
            if len(out) == count:
                break
            if x["product"].id not in used and (lk := look_for(x["product"], occasion)):
                used.add(x["product"].id)
                out.append(lk)
        if out:
            return out
    if not any(taste(p) is not None for p in products[:50]) and not any(taste(p) is not None for p in products):
        return out
    scored = []
    for o in available_occasions(products, rules, audiences):
        vals = [v for v in (taste(p) for p in eligible(products, rules, o, audiences)) if v is not None]
        if vals:
            scored.append((sum(vals) / len(vals), o))
    queues = [(o, [x["product"] for x in occasion_shortlist(o, products, rules, audiences, taste=taste)])
              for _, o in sorted(scored, key=lambda x: (-x[0], x[1]))]
    while len(out) < count and any(q for _, q in queues):
        for o, q in queues:                       # one hero per occasion per round
            while q:
                hero = q.pop(0)
                if hero.id not in used and (lk := look_for(hero, o)):
                    used.add(hero.id)
                    out.append(lk)
                    break
            if len(out) == count:
                break
    return out
