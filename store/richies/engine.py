"""The one module that chooses (engineering PRD 2): filter, score, diversify, plus the signals both features use.

No page concerns, no persona names: it takes products, rules and a taste function, and returns ranked products
with the signals each one scored, so the same code would run on real customers. Deterministic: ties break on
the product ID, and nothing is random.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable

# ------------------------------------------------------------------ data


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    brand: str
    price: float
    audience: str                      # Women | Men | Kids | All | Unisex
    role: str                          # outfit role from store_outfit_roles
    tags: dict = field(default_factory=dict)          # facet -> tuple of values (in tag order)
    confidence: dict = field(default_factory=dict)    # facet -> mean confidence (None for samples)
    in_stock: bool = True
    approved: bool = True
    sample: bool = False
    category: str = ""                 # product type, the third level of the spine ("Dresses")
    path: str = ""
    launch: str = ""                   # launch date, for "newest first"
    images: tuple = ()                 # storage paths in gallery order (signed by the server)

    def values(self, facet: str) -> tuple:
        return tuple(self.tags.get(facet, ()))

    def first(self, facet: str):
        v = self.values(facet)
        return v[0] if v else None


@dataclass
class Rules:
    """The rule tables, loaded once per request from the database (or built in tests)."""
    formality_order: list[str]                         # casual ... black_tie
    occasions: dict[str, dict]                         # occasion -> profile row
    templates: dict[str, list[str]]                    # anchor role -> slot roles in order
    pairings: dict[tuple[str, str], float]             # (a, b) with a <= b -> score
    weights: dict[str, dict[str, float]]               # feature -> signal -> weight
    neutrals: frozenset = frozenset()
    labels: dict = field(default_factory=dict)         # (facet, value) -> label, for the pages

    def level(self, formality: str | None) -> int | None:
        return self.formality_order.index(formality) if formality in self.formality_order else None

    def colour_score(self, a: str | None, b: str | None) -> float | None:
        if not a or not b:
            return None
        return self.pairings.get((min(a, b), max(a, b)), 0.3)


@dataclass
class Ranked:
    product: Product
    score: float
    signals: dict[str, float]          # the signals that applied, each 0 to 1 before weighting

    def strongest(self, prefer: Iterable[str] = ()) -> str | None:
        """The signal that contributed most (weighted), for the reason code."""
        return max(self.signals, key=lambda k: (self.signals[k], k in prefer), default=None)


# ------------------------------------------------------------------ the three engine functions

Rule = tuple[str, Callable[[Product], bool]]


def filter(candidates: Iterable[Product], rules: list[Rule]) -> tuple[list[Product], dict[str, str]]:
    """Keep products that pass every rule. Returns (kept, {product id: first rule it failed})."""
    kept, why = [], {}
    for p in candidates:
        failed = next((name for name, ok in rules if not ok(p)), None)
        if failed is None:
            kept.append(p)
        else:
            why[p.id] = failed
    return kept, why


Signal = Callable[[Product], "float | None"]


def score(candidates: Iterable[Product], signals: dict[str, Signal], weights: dict[str, float]) -> list[Ranked]:
    """Weighted sum of signals, each 0 to 1. A signal that returns None (does not apply to this product, or no
    profile yet) is dropped and the other weights are rescaled to sum to 1 for that product. Sorted by score,
    then by product ID, so the order is always the same."""
    out = []
    for p in candidates:
        vals = {}
        for name, fn in signals.items():
            if weights.get(name, 0) <= 0:
                continue
            v = fn(p)
            if v is not None:
                vals[name] = max(0.0, min(1.0, float(v)))
        total = sum(weights[k] for k in vals)
        s = sum(weights[k] * v for k, v in vals.items()) / total if total else 0.0
        out.append(Ranked(p, round(s, 6), vals))
    out.sort(key=lambda r: (-r.score, r.product.id))
    return out


def diversify(ranked: list[Ranked], limit: int, caps: dict[str, tuple[Callable[[Product], object], int]]) -> list[Ranked]:
    """Walk from the top; take a product only while each cap holds (e.g. at most two per brand). Stop at limit."""
    taken, counts = [], {name: {} for name in caps}
    for r in ranked:
        if len(taken) >= limit:
            break
        keys = {name: key(r.product) for name, (key, _) in caps.items()}
        if any(keys[n] is not None and counts[n].get(keys[n], 0) >= caps[n][1] for n in caps):
            continue
        taken.append(r)
        for n, k in keys.items():
            if k is not None:
                counts[n][k] = counts[n].get(k, 0) + 1
    return taken


# ------------------------------------------------------------------ shared rules and signals

ALL_SEASON = "all_season"


def seasons_compatible(a: Iterable[str], b: Iterable[str]) -> bool:
    """`all_season` is compatible with everything; a product with no season tag is not excluded."""
    a, b = set(a), set(b)
    if not a or not b or ALL_SEASON in a or ALL_SEASON in b:
        return True
    return bool(a & b)


def formality_centre(rules: Rules, p: Product, lo: str, hi: str) -> float | None:
    """1.0 at the midpoint of the occasion's range, falling linearly to 0.5 at its edges."""
    lv, a, b = rules.level(p.first("formality")), rules.level(lo), rules.level(hi)
    if lv is None or a is None or b is None:
        return None
    if a == b:
        return 1.0
    mid, half = (a + b) / 2, (b - a) / 2
    return max(0.0, 1.0 - 0.5 * abs(lv - mid) / half)


def formality_in_range(rules: Rules, p: Product, lo: str, hi: str) -> bool:
    lv = rules.level(p.first("formality"))
    return lv is None or rules.level(lo) <= lv <= rules.level(hi)


def formality_steps(rules: Rules, a: Product, b: Product) -> int | None:
    la, lb = rules.level(a.first("formality")), rules.level(b.first("formality"))
    return None if la is None or lb is None else abs(la - lb)


def occasion_fit(p: Product, occasion: str) -> float:
    """1.0 if the occasion is the product's only or first occasion value, 0.7 otherwise."""
    occ = p.values("occasion")
    return 1.0 if occ and occ[0] == occasion else 0.7


def tag_confidence(p: Product, facets=("occasion", "formality")) -> float:
    """Mean confidence of the given tags; 1.0 for sample products (tagged by hand) or tags without one."""
    if p.sample:
        return 1.0
    vals = [p.confidence[f] for f in facets if p.confidence.get(f) is not None]
    return sum(vals) / len(vals) if vals else 1.0


def price_fit(anchor: Product, c: Product) -> float:
    """1.0 if the candidate costs no more than the anchor, falling linearly to 0 at three times its price."""
    if not anchor.price or c.price <= anchor.price:
        return 1.0
    return max(0.0, 1.0 - (c.price - anchor.price) / (2 * anchor.price))


def occasion_overlap(anchor: Product, c: Product) -> float | None:
    a = set(anchor.values("occasion"))
    if not a or not c.values("occasion"):
        return None
    return len(a & set(c.values("occasion"))) / len(a)


def more_covered(p: Product) -> bool:
    """"More covered": long sleeves, a midi or longer length, and not sheer. A facet that does not apply to the
    product's category is skipped."""
    sleeve, length, sheer = p.values("sleeve_length"), p.values("length"), p.values("sheerness")
    ok = True
    if sleeve:
        ok &= "long" in sleeve
    if length:
        ok &= bool(set(length) & {"midi", "maxi", "floor"})
    if sheer:
        ok &= "sheer" not in sheer and "semi_sheer" not in sheer
    return ok
