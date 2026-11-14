"""Looks for you eligibility, the PLP lead module, and the persona-signal checks (engineering PRD V5, 14 Nov).

Pure: everything is read from events, so the same rules run for a live session and for a persona's simulated
starting session. Both decisions are made when a page loads (like intent), so a change shows on the next page.
"""
from __future__ import annotations

from typing import Callable

from .profile import Event, intent as intent_rule

POSITIVE = ("view", "save", "add_to_bag", "swap_in", "purchase")     # the event types with a positive weight
DEFAULT_THRESHOLD = 3


def positive_count(events: list[Event]) -> int:
    """Seed and session events with a positive weight, on a product."""
    return sum(1 for e in events if e.type in POSITIVE and e.product_id)


def occasion_in_play(events: list[Event]) -> tuple[str | None, str | None]:
    """(occasion, where it came from): the latest occasion chosen this session, else the occasion the shopper
    arrived with (the entry event, e.g. Noor's evening-edit notification)."""
    for e in reversed(events):
        if e.type == "occasion_select" and not e.is_seed and (e.context or {}).get("occasion"):
            return e.context["occasion"], "chosen"
    for e in reversed(events):
        if e.type == "entry" and (e.context or {}).get("occasion"):
            return e.context["occasion"], "entry"
    return None, None


def eligibility(intent_state: str, events: list[Event], threshold: int = DEFAULT_THRESHOLD,
                label: Callable[[str], str] = lambda o: o) -> dict:
    """Looks for you: never for a high-intent session; otherwise eligible with an occasion in play (the looks are
    then built for it) or with a taste profile (at least `threshold` positive events)."""
    n = positive_count(events)
    occ, src = occasion_in_play(events)
    base = {"count": n, "threshold": threshold, "occasion": occ}
    if intent_state == "high":
        return {**base, "eligible": False, "never": True, "code": "high_intent",
                "reason": "Never shown to a high-intent shopper"}
    if occ:                                       # an occasion in play leads the looks, whatever the history
        how = "arrived with" if src == "entry" else "chose"
        return {**base, "eligible": True, "never": False, "code": "occasion_in_play",
                "reason": f"Looks are built for the occasion they {how}: {label(occ)}"}
    if n >= threshold:
        return {**base, "eligible": True, "never": False, "code": "taste_profile",
                "reason": f"{n} positive signals (threshold {threshold})"}
    return {**base, "eligible": False, "never": False, "code": "not_enough_signals",
            "reason": f"{n} of {threshold} positive signals this session"}


def lead_module(intent_state: str, events: list[Event], threshold: int = DEFAULT_THRESHOLD,
                department_known: bool = True) -> dict:
    """The one module at the top of the PLP: show when the store knows, ask when it does not. First match wins."""
    if intent_state == "high":
        return {"module": "none", "suggestedOccasion": None, "occasionSource": None, "showOpener": False,
                "occasionToggle": False, "code": "high_intent",
                "reason": "High intent: the feature stays out of the way"}
    occ, src = occasion_in_play(events)
    if occ:
        # a chosen occasion (on the listing or the product page) reopens its shortlist; an arrival one is suggested
        return {"module": "occasion_picker", "suggestedOccasion": occ, "occasionSource": src, "showOpener": False,
                "occasionToggle": False, "code": "occasion_in_play",
                "reason": "An occasion is in play (" + ("arrived with it" if src == "entry" else "chosen") + ")"}
    if positive_count(events) >= threshold:
        # README V7: a shopper whose taste came from their history (Layla) gets the looks row alone and reaches
        # occasions on the product page; one whose taste was built this session (James) keeps the one-line toggle
        built_here = positive_count([e for e in events if e.is_seed]) < threshold
        return {"module": "looks", "suggestedOccasion": None, "occasionSource": None, "showOpener": False,
                "occasionToggle": built_here, "code": "taste_profile",
                "reason": "Taste known, no occasion: Looks for you leads"}
    return {"module": "occasion_picker", "suggestedOccasion": None, "occasionSource": None,
            "showOpener": not department_known, "occasionToggle": False,
            "code": "nothing_known", "reason": "Nothing known yet: ask" + (" who it is for" if not department_known else "")}


def does_line(lead: dict, label: Callable[[str], str] = lambda o: o) -> str:
    """The switcher's "what the store does" line, computed from the lead module."""
    m = lead["module"]
    if m == "none":
        return "Feature stays out of the way"
    if m == "looks":
        return "Leads with Looks for you"
    if lead.get("suggestedOccasion"):
        return f"Leads with the occasion picker, {label(lead['suggestedOccasion']).lower()} suggested"
    if lead.get("showOpener"):
        return 'Leads with the occasion picker and "Who is it for?"'
    return "Leads with the occasion picker"


def starting_events(seed: list[dict], session_start: list[dict]) -> list[Event]:
    """A persona's starting state as events: the seed history plus the simulated starting session."""
    return ([Event(e["type"], e.get("product_id"), e.get("context") or {}, True) for e in seed]
            + [Event(e["type"], e.get("product_id"), e.get("context") or {}, False) for e in session_start])


def check_signal(check: str, events: list[Event], role_of: Callable[[str], str | None] = lambda pid: None,
                 occasions_of: Callable[[str], tuple] = lambda pid: ()) -> bool:
    """Whether a persona's starting events show the behaviour a switcher line describes. Each stored signal names
    one of these checks, so the displayed lines cannot drift from the data."""
    kind, _, arg = check.partition(":")
    seed = [e for e in events if e.is_seed]
    sess = [e for e in events if not e.is_seed]
    views = [e for e in sess if e.type == "view"]
    entry = next((e for e in sess if e.type == "entry"), None)
    if kind == "history":
        return positive_count(seed) >= int(arg)
    if kind == "entry":
        return bool(entry and entry.context.get("source") == arg)
    if kind == "no_search_or_filter":
        return not any(e.type in ("search_referral", "filter_apply") for e in sess)
    if kind == "quick_views":
        return sum(1 for e in views if e.context.get("dwell") == "seconds") >= int(arg)
    if kind == "orders":
        return sum(1 for e in seed if e.type == "purchase") == int(arg)
    if kind == "orders_in":
        bought = [e for e in seed if e.type == "purchase"]
        return bool(bought) and all(role_of(e.product_id) == arg for e in bought)
    if kind == "browsing":
        return any(arg in occasions_of(e.product_id) for e in views)
    if kind == "first_visit":
        return not seed
    if kind == "pages":
        return entry is not None and len(views) == int(arg) - 1
    if kind == "low_intent":
        return intent_rule(sess)[0] == "low"
    if kind in ("search_referral", "size_select"):
        return any(e.type == kind for e in sess)
    raise ValueError(f"unknown signal check '{check}'")
