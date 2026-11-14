"""Taste profile, taste match and intent (engineering PRD 7). Computed from events on every request; nothing about
a persona's taste is typed in by hand.

Profile: per facet value for aesthetic, primary colour, formality, sleeve length, length, sheerness and brand.
Each event adds its weight to the product's value for each facet (session events count double), each tally is
clamped at zero, and each facet is divided by its total so its values sum to 1.
"""
from __future__ import annotations

from dataclasses import dataclass

from .engine import Product

PROFILE_FACETS = ("aesthetic", "colour_primary", "formality", "sleeve_length", "length", "sheerness", "brand")
SETTINGS = ("session_multiplier", "looks_threshold")    # profile rows that are settings, not event weights
DEFAULT_WEIGHTS = {"purchase": 3, "add_to_bag": 3, "save": 2, "swap_in": 1.5, "view": 1, "swap_out": -1.5}


@dataclass
class Event:
    type: str
    product_id: str | None
    context: dict
    is_seed: bool
    at: str = ""


def _values(p: Product, facet: str) -> tuple:
    return (p.brand,) if facet == "brand" else p.values(facet)


def profile(events: list[Event], products: dict[str, Product], weights: dict[str, float] | None = None
            ) -> dict[str, dict[str, float]] | None:
    """{facet: {value: share}} or None when no event carries a weight (no profile: the taste signal is dropped)."""
    w = {**DEFAULT_WEIGHTS, **{k: v for k, v in (weights or {}).items() if k not in SETTINGS}}
    mult = (weights or {}).get("session_multiplier", 2)
    tally: dict[str, dict[str, float]] = {f: {} for f in PROFILE_FACETS}
    any_weight = False
    for e in events:
        if e.type not in w or not e.product_id or e.product_id not in products:
            continue
        p = products[e.product_id]
        amount = w[e.type] * (1 if e.is_seed else mult)
        any_weight = True
        for f in PROFILE_FACETS:
            for v in _values(p, f):
                tally[f][v] = tally[f].get(v, 0.0) + amount
    if not any_weight:
        return None
    out = {}
    for f, vals in tally.items():
        vals = {k: max(0.0, v) for k, v in vals.items()}            # neutral, never negative
        total = sum(vals.values())
        out[f] = {k: v / total for k, v in vals.items() if v > 0} if total else {}
    return out


def taste_match(prof: dict | None, p: Product) -> float | None:
    """Mean over the profile facets that apply to the product of (its value's weight / the facet's largest
    weight), so the best-liked value scores 1. None without a profile."""
    if not prof:
        return None
    scores = []
    for f in PROFILE_FACETS:
        vals = _values(p, f)
        facet = prof.get(f) or {}
        if not vals or not facet:
            continue
        top = max(facet.values())
        scores.append(max(facet.get(v, 0.0) for v in vals) / top)
    return sum(scores) / len(scores) if scores else None


def effect(e: Event, products: dict[str, Product], labels: dict, weights: dict | None = None) -> str | None:
    """How one event moved the profile, in words, e.g. "minimalist aesthetic up · black up"."""
    w = {**DEFAULT_WEIGHTS, **{k: v for k, v in (weights or {}).items() if k not in SETTINGS}}
    if e.type not in w or e.product_id not in products:
        return {"size_select": "size chosen · intent high on the next page",
                "filter_apply": "filter used · intent high on the next page",
                "search_referral": "arrived from a search · intent high",
                "refine_apply": "refinement, not a filter · intent unchanged"}.get(e.type)
    p = products[e.product_id]
    way = "up" if w[e.type] > 0 else "down"
    parts = []
    for f, suffix in (("aesthetic", " aesthetic"), ("colour_primary", ""), ("formality", "")):
        for v in _values(p, f)[:1]:
            lab = labels.get((f, v), v.replace("_", " ")).lower()
            parts.append(f"{lab}{suffix} {way}")
    return " · ".join(parts) or None


def intent(session_events: list[Event]) -> tuple[str, str]:
    """High if, in this session, the shopper arrived on a product page from a search, selected a size or applied a
    PLP filter (refinement chips do not count); low otherwise. One function, so a scored version can replace it."""
    for e in session_events:
        if e.is_seed:
            continue
        if e.type == "search_referral":
            return "high", "Arrived on a product page from a search"
        if e.type == "size_select":
            return "high", "Selected a size"
        if e.type == "filter_apply":
            return "high", "Used a filter" + (" (from a product tag)" if (e.context or {}).get("surface") == "tag"
                                               else " on the listing page")
    return "low", "No filter, size or search signal this session"
