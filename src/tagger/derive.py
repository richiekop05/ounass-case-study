"""Step 3: facets worked out by code (PRD section 5). No model calls, no cost.

Rows go under the tag run with evidence 'rule', status 'auto', no confidence, and a reason that
starts with "derive:" so a re-run can replace exactly these rows and nothing else.
"""
from __future__ import annotations

import bisect
import csv
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .config import DeriveSettings
from .facets import FacetSpec, all_facets, facets_for_category, function_category
from .products import load_product
from .runs import Db
from .tagging import TagRow

REASON_PREFIX = "derive:"


# ------------------------------------------------------------------ rule files

@dataclass
class GiftRules:
    giftable: list[tuple[str, bool]] = field(default_factory=list)            # (category path, giftable)
    occasions: list[tuple[str, str, int, list[str]]] = field(default_factory=list)  # (occasion, path, rank, keywords)


def load_gift_rules(giftable_file: Path, occasion_file: Path) -> GiftRules:
    rules = GiftRules()
    if giftable_file.exists():
        with giftable_file.open(newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                rules.giftable.append((r["path"].strip(), r["giftable"].strip().lower() == "true"))
    if occasion_file.exists():
        with occasion_file.open(newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                kws = [k.strip().lower() for k in (r.get("keywords") or "").split(";") if k.strip()]
                rules.occasions.append((r["occasion"].strip(), (r.get("path") or "").strip(), int(r["rank"]), kws))
    return rules


def under(path: str, ancestor: str) -> bool:
    """True when `path` is `ancestor` or below it ('' matches everything)."""
    return not ancestor or path == ancestor or path.startswith(ancestor + " / ")


# ------------------------------------------------------------------ inputs

@dataclass
class ProductFacts:
    product_id: str
    category_id: str
    category_path: str
    department: tuple[str, str]           # (audience, department) of the category
    price: float | None
    launch_date: date | None
    brand_tier: str | None
    is_local_designer: bool
    text: str                              # normalised product text
    facets: dict[str, FacetSpec]           # every active facet mapped to the category
    accepted: dict[str, list]              # this run's auto values per facet: labels / numbers


def contains_word(text: str, word: str) -> bool:
    return bool(word) and re.search(rf"\b{re.escape(word.lower())}\w*\b", text) is not None


# ------------------------------------------------------------------ rules

def band(price: float, prices: list[float]) -> str:
    """Thirds by rank: the share of prices strictly below this one."""
    share = bisect.bisect_left(prices, price) / len(prices)
    return "entry" if share < 1 / 3 else "mid" if share < 2 / 3 else "high"


def price_band(p: ProductFacts, dept_prices: dict[tuple, list[float]], all_prices: list[float],
               s: DeriveSettings) -> tuple[str, str] | None:
    if not p.price:
        return None
    pool = dept_prices.get(p.department, [])
    scope = "department"
    if len(pool) < s.price_band_min_department:
        pool, scope = all_prices, "catalog"
    if not pool:
        return None
    return band(p.price, pool), f"price {p.price:g} ranked in {scope} ({len(pool)} priced products)"


def recency(p: ProductFacts, today: date, s: DeriveSettings) -> tuple[str, str] | None:
    if not p.launch_date:
        return None
    days = (today - p.launch_date).days
    label = ("new-in" if days <= s.recency_new_in_days else
             "current season" if days <= s.recency_current_days else "carried over")
    return label, f"launched {days} days ago"


def is_metallic(p: ProductFacts, s: DeriveSettings) -> tuple[str, str] | None:
    colours = p.accepted.get("colour_primary") or []
    if not colours:
        return None
    metallic = str(colours[0]).lower() in {c.lower() for c in s.metallic_colours}
    return ("true" if metallic else "false"), f"colour_primary is {colours[0]}"


def case_size_bucket(p: ProductFacts, s: DeriveSettings) -> tuple[str, str] | None:
    sizes = p.accepted.get("case_size_mm") or []
    if not sizes:
        return None
    mm = float(sizes[0])
    slug = "small" if mm < s.case_size_small_below_mm else "large" if mm > s.case_size_large_above_mm else "medium"
    return slug, f"case_size_mm is {mm:g}"


def giftable(p: ProductFacts, rules: GiftRules, s: DeriveSettings) -> tuple[str, str]:
    listed = [(path, g) for path, g in rules.giftable if under(p.category_path, path)]
    if listed:
        path, g = max(listed, key=lambda x: len(x[0]))       # the most specific row wins
        if not g:
            return "false", f"category under {path} is never giftable"
    if str((p.accepted.get("is_set") or [""])[0]).lower() == "true":
        return "true", "is_set is true"
    if contains_word(p.text, s.gift_text_keyword):
        return "true", f"text mentions '{s.gift_text_keyword}'"
    if listed:
        return "true", f"gift-friendly category ({path})"
    return "false", "not a set, no gift wording, category not gift-friendly"


def gift_occasions(p: ProductFacts, rules: GiftRules, max_values: int | None) -> list[tuple[str, str]]:
    hits: dict[str, tuple[int, str]] = {}
    for occasion, path, rank, kws in rules.occasions:
        if not under(p.category_path, path):
            continue
        if kws and not any(contains_word(p.text, k) for k in kws):
            continue
        why = f"rank {rank}: " + (f"text mentions {kws}" if kws else f"category under {path or 'any'}")
        if occasion not in hits or rank < hits[occasion][0]:
            hits[occasion] = (rank, why)
    ordered = sorted(hits.items(), key=lambda kv: (kv[1][0], kv[0]))
    return [(occ, why) for occ, (_, why) in ordered[:max_values or 3]]


def derive_for(p: ProductFacts, dept_prices, all_prices, rules: GiftRules, s: DeriveSettings, today: date,
               function_categories: dict[str, str]) -> tuple[list[TagRow], list[str], list[str]]:
    """All rule rows for one product, plus 'none' rows to remove, plus warnings.
    Only facets mapped to the product's category are written."""
    out: list[tuple[str, str, str]] = []           # (facet_id, value label or slug, why)

    def add(fid, result):
        if result and fid in p.facets:
            out.append((fid, result[0], f"{fid}: {result[1]}"))

    add("price_band", price_band(p, dept_prices, all_prices, s))
    add("recency", recency(p, today, s))
    add("is_metallic", is_metallic(p, s))
    add("case_size_bucket", case_size_bucket(p, s))
    if p.brand_tier:
        add("brand_tier", (p.brand_tier, "brand table"))
    gift = giftable(p, rules, s) if "giftable" in p.facets else None
    add("giftable", gift)
    if gift and gift[0] == "true" and "gift_occasion" in p.facets:
        for occ, why in gift_occasions(p, rules, p.facets["gift_occasion"].max_values):
            out.append(("gift_occasion", occ, f"gift_occasion: {why}"))
    remove_none = []
    if p.is_local_designer and "exclusivity" in p.facets:
        out.append(("exclusivity", "local designer", "exclusivity: brand flagged local designer"))
        remove_none.append("exclusivity")
    fn = function_category(p.category_path, function_categories)
    if fn and "function" in p.facets:
        out.append(("function", fn, f"function: category {p.category_path}"))

    rows, warnings = [], []
    for fid, label, why in out:
        fv = p.facets[fid].lookup(label)
        if not fv:
            warnings.append(f"{p.product_id}: {fid} has no value {label!r}")
            continue
        rows.append(TagRow(fid, "auto", facet_value_id=fv.facet_value_id, evidence="rule",
                           reason=f"{REASON_PREFIX}{why}", label=fv.label))
    return rows, remove_none, warnings


# ------------------------------------------------------------------ database

def load_facts(db: Db, run_id: int, product_ids: list[str]) -> tuple[list[ProductFacts], dict, list[float]]:
    conn = db.conn
    facets = all_facets(conn)
    accepted: dict[str, dict[str, list]] = {}
    for pid, fid, label, num, txt in conn.execute(
            """select t.product_id, t.facet_id, fv.label, t.value_number, t.value_text
               from product_facet_tags t left join facet_values fv on fv.facet_value_id = t.facet_value_id
               where t.tagging_run_id = %s and t.status = 'auto' and t.product_id = any(%s)
               order by t.tag_id""", (run_id, product_ids)):
        v = label if label is not None else (float(num) if num is not None else txt)
        accepted.setdefault(pid, {}).setdefault(fid, []).append(v)
    facts = []
    rows = conn.execute(
        """select cp.product_id, cp.category_id, c.path, c.audience, c.department, cp.price, cp.launch_date,
                  b.brand_tier, coalesce(b.is_local_designer, false)
           from catalog_products cp join categories c on c.node_id = cp.category_id
           left join brands b on b.brand = cp.brand
           where cp.product_id = any(%s) and cp.category_status in ('auto', 'flagged')""", (product_ids,)).fetchall()
    cat_facets: dict[str, dict] = {}
    for pid, cat, path, aud, dept, price, launch, tier, local in rows:
        if cat not in cat_facets:
            cat_facets[cat] = facets_for_category(conn, cat)
        product = load_product(conn, pid)
        facts.append(ProductFacts(pid, cat, path, (aud, dept), float(price) if price else None, launch, tier,
                                  bool(local), product.searchable(), cat_facets[cat], accepted.get(pid, {})))
    dept_prices: dict[tuple, list[float]] = {}
    all_prices = []
    for aud, dept, price in conn.execute(
            """select c.audience, c.department, cp.price from catalog_products cp
               join categories c on c.node_id = cp.category_id where cp.price > 0"""):
        dept_prices.setdefault((aud, dept), []).append(float(price))
        all_prices.append(float(price))
    for v in dept_prices.values():
        v.sort()
    all_prices.sort()
    del facets
    return facts, dept_prices, all_prices


def run_derive(db: Db, run_id: int, settings, root: Path = Path.cwd(), today: date | None = None,
               log=print) -> dict:
    today = today or date.today()
    s = settings.derive
    with db.lock:
        stage = db.conn.execute("select stage from tagging_runs where tagging_run_id = %s", (run_id,)).fetchone()
        if not stage or stage[0] != "tag":
            raise ValueError(f"run {run_id} is not a tag run")
        pids = [r[0] for r in db.conn.execute(
            "select product_id from tagging_run_products where tagging_run_id = %s and status = 'done'", (run_id,))]
        rules = load_gift_rules(root / s.giftable_categories_file, root / s.gift_occasion_rules_file)
        facts, dept_prices, all_prices = load_facts(db, run_id, pids)
        all_rows, warnings, removed = [], [], 0
        with db.conn.transaction():
            deleted = db.conn.execute(
                "delete from product_facet_tags where tagging_run_id = %s and evidence = 'rule' and reason like %s",
                (run_id, REASON_PREFIX + "%")).rowcount
            for p in facts:
                rows, remove_none, warn = derive_for(p, dept_prices, all_prices, rules, s, today,
                                                     settings.function_categories)
                warnings += warn
                for fid in remove_none:
                    removed += db.conn.execute(
                        """delete from product_facet_tags t using facet_values fv
                           where fv.facet_value_id = t.facet_value_id and t.tagging_run_id = %s
                             and t.product_id = %s and t.facet_id = %s and fv.value = 'none'""",
                        (run_id, p.product_id, fid)).rowcount
                with db.conn.cursor() as cur:
                    cur.executemany(
                        """insert into product_facet_tags (product_id, tagging_run_id, facet_id, facet_value_id,
                               status, evidence, reason) values (%s, %s, %s, %s, 'auto', 'rule', %s)""",
                        [(p.product_id, run_id, r.facet_id, r.facet_value_id, r.reason) for r in rows])
                all_rows += rows
        db.commit()
    counts: dict[str, int] = {}
    for r in all_rows:
        counts[r.facet_id] = counts.get(r.facet_id, 0) + 1
    for w in warnings[:20]:
        log(f"  warning: {w}")
    if not rules.giftable and not rules.occasions:
        log("  warning: no gift rule files found; giftable uses sets and gift wording only")
    return {"products": len(facts), "rows": len(all_rows), "replaced": deleted, "none_removed": removed,
            "by_facet": counts, "warnings": len(warnings)}
