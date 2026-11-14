"""The store's catalogue and rules, read from the shared database into memory (engineering PRD 1, 2).

Only approved products: `v_catalog_products` (published) with their tags from `v_product_facets`. Never the raw
scraped table. Re-read when the approved set or the rule tables change, so a product approved in the tagger app
appears on the next request with no restart.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict

import psycopg

from .engine import Product, Rules

FACETS = ("occasion", "formality", "season", "aesthetic", "colour_primary", "sleeve_length", "length",
          "sheerness", "brand_tier")

_lock = threading.Lock()
_cache: dict = {}
CHECK_SECONDS = 5.0          # how often to ask the database whether approvals or rules changed (a 0.3 s round trip)


def _stamp(conn) -> tuple:
    return conn.execute("""select (select count(*) || ':' || coalesce(max(approved_at)::text, '') || ':'
                                       || coalesce(max(updated_at)::text, '')
                                     from catalog_products where is_published),
                                  (select count(*) from store_outfit_roles where rule = 'owner'),
                                  (select string_agg(occasion || formality_min || formality_max || active::text, ',')
                                     from store_occasion_profiles),
                                  (select sum(weight) from store_score_weights),
                                  (select count(*) || ':' || sum(score) from store_colour_pairings)""").fetchone()


def load(conn: psycopg.Connection) -> tuple[list[Product], Rules]:
    """(approved products, rules), cached until the data stamp changes. The stamp is checked at most every
    CHECK_SECONDS, so a request stays well under 300 ms; a newly approved product appears within that time."""
    with _lock:
        if _cache and time.time() - _cache.get("checked", 0) < CHECK_SECONDS:
            return _cache["products"], _cache["rules"]
    stamp = _stamp(conn)
    with _lock:
        if _cache.get("stamp") == stamp:
            _cache["checked"] = time.time()
            return _cache["products"], _cache["rules"]
    tags: dict[str, dict] = defaultdict(lambda: defaultdict(list))
    confs: dict[str, dict] = defaultdict(lambda: defaultdict(list))
    for pid, fid, val, conf in conn.execute(
            """select product_id, facet_id, value, confidence from v_product_facets
                where facet_id = any(%s) and value is not null
                order by product_id, facet_id, value_sort_order""", (list(FACETS),)):
        tags[pid][fid].append(val)
        if conf is not None:
            confs[pid][fid].append(float(conf))
    images: dict[str, list[str]] = defaultdict(list)
    for pid, path in conn.execute("""select i.product_id, i.storage_path from product_images i
                                      join catalog_products cp on cp.product_id = i.product_id and cp.is_published
                                     where i.storage_path is not null order by i.product_id, i.position"""):
        images[pid].append(path)
    products = []
    for pid, name, brand, price, audience, role, stock, source, path, launch in conn.execute(
            """select p.product_id, p.name, p.brand, p.price, p.audience, coalesce(o.role, 'none'),
                      cp.in_stock, cp.source, p.category_path, coalesce(p.launch_date::text, '')
                 from v_catalog_products p join catalog_products cp using (product_id)
                 left join store_outfit_roles o on o.category_node_id = p.category_id
                order by p.product_id"""):
        parts = (path or "").split(" / ")
        products.append(Product(
            id=pid, name=name, brand=brand, price=float(price or 0), audience=audience or "", role=role,
            tags={k: tuple(v) for k, v in tags[pid].items()},
            confidence={k: sum(v) / len(v) for k, v in confs[pid].items() if v},
            in_stock=bool(stock), sample=source == "sample", category=parts[2] if len(parts) > 2 else (parts[-1] if parts else ""),
            path=path or "", launch=launch, images=tuple(images.get(pid, ()))))
    rules = load_rules(conn)
    rules.labels = {(f, v): l for f, v, l in conn.execute(
        "select facet_id, value, label from facet_values where facet_id = any(%s) order by facet_id, sort_order",
        (list(FACETS),))}
    with _lock:
        _cache.update(stamp=stamp, products=products, rules=rules, checked=time.time())
    return products, rules


def load_rules(conn) -> Rules:
    order = [r[0] for r in conn.execute("select value from facet_values where facet_id = 'formality' "
                                        "and is_active order by sort_order")]
    occasions = {r[0]: {"occasion": r[0], "display_name": r[1], "formality_min": r[2], "formality_max": r[3],
                        "seasons": list(r[4]), "hero_roles": list(r[5]), "active": r[6]}
                 for r in conn.execute("""select occasion, display_name, formality_min, formality_max, seasons,
                                                 hero_roles, active from store_occasion_profiles""")}
    templates: dict[str, list[str]] = defaultdict(list)
    for anchor, slot in conn.execute("select anchor_role, slot_role from store_look_templates "
                                     "order by anchor_role, position"):
        templates[anchor].append(slot)
    pairings = {(a, b): float(s) for a, b, s in conn.execute("select colour_a, colour_b, score from store_colour_pairings")}
    neutrals = frozenset(a for a, b, s, r in conn.execute(
        "select colour_a, colour_b, score, rule from store_colour_pairings where rule = 'neutral' and colour_a = colour_b"))
    weights: dict[str, dict[str, float]] = defaultdict(dict)
    for feature, signal, w in conn.execute("select feature, signal, weight from store_score_weights"):
        weights[feature][signal] = float(w)
    return Rules(order, occasions, dict(templates), pairings, dict(weights), neutrals)


def clear() -> None:
    with _lock:
        _cache.clear()
