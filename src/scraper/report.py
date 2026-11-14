"""Run report and the acceptance checks shared by `scrape` and `validate`."""
from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import ValidationError

from .models import Product
from .sampling import NEW_TYPE_DEPTS, NEW_TYPE_L3, HOLDING_NAMES, PlanRow

FILL_FIELDS = list(Product.model_fields)


def _filled(v: Any) -> bool:
    return v is not None and v != "" and v != [] and v != {}


def fill_rates(products: list[dict]) -> dict[str, float]:
    if not products:
        return {}
    n = len(products)
    return {f: round(100 * sum(_filled(p.get(f)) or isinstance(p.get(f), bool) for p in products) / n, 1)
            for f in FILL_FIELDS}


def area_counts(products: list[dict], plan: list[PlanRow]) -> dict[str, dict]:
    quotas: Counter = Counter()
    for r in plan:
        quotas[r.area] += r.quota
    got = Counter(p.get("sample_area") for p in products)
    out = {}
    for area, q in quotas.items():
        g = got.get(area, 0)
        out[area] = {"quota": q, "got": g, "within_10pct": abs(g - q) <= 0.1 * q}
    return out


def node_counts(products: list[dict], plan: list[PlanRow]) -> dict[str, dict]:
    got = Counter(p.get("listing_url") for p in products)
    return {r.listing_url: {"spine_node_id": r.spine_node_id, "area": r.area, "quota": r.quota,
                            "got": got.get(r.listing_url, 0)} for r in plan}


def is_new_type_l4(node: dict, cats: dict[str, dict]) -> bool:
    if node["level"] != "L4":
        return False
    parent = cats.get(node.get("parent_id") or "")
    return (node.get("department") or "").lower() in NEW_TYPE_DEPTS or (
        parent is not None and parent["name"].lower() in NEW_TYPE_L3)


def checks(products: list[dict], cats: dict[str, dict], plan: list[PlanRow] | None = None,
           typed: list[tuple] | None = None) -> list[tuple[str, bool, str]]:
    """Acceptance checks from PRD section 9 that can be computed from the data."""
    out: list[tuple[str, bool, str]] = []
    n = len(products)
    out.append(("at least 240 valid rows", n >= 240, f"{n} rows"))

    invalid = []
    for p in products:
        try:
            Product.model_validate(p)
        except ValidationError as e:
            invalid.append(f"{p.get('product_id')}: {e.errors()[0]['loc']} {e.errors()[0]['msg']}")
    out.append(("every row passes the schema", not invalid, "; ".join(invalid[:5]) or "ok"))

    rates = fill_rates(products)
    out.append(("details on >= 95% of rows", rates.get("details", 0) >= 95, f"{rates.get('details', 0)}%"))

    no_img = [p["product_id"] for p in products if not p.get("images")]
    out.append(("every product has an image row", not no_img, f"{len(no_img)} without"))
    not_stored = [p["product_id"] for p in products if not any(i.get("storage_path") for i in p.get("images", []))]
    out.append(("every product has a stored image", not not_stored, f"{len(not_stored)} without"))

    unknown = [p["product_id"] for p in products if p.get("spine_node_id") and p["spine_node_id"] not in cats]
    out.append(("every spine_node_id exists in categories", not unknown, f"{len(unknown)} unknown"))

    nodes = {p["spine_node_id"] for p in products if p.get("spine_node_id") in cats}
    special = {nid for nid in nodes if is_new_type_l4(cats[nid], cats)}
    out.append(("at least 60 nodes covered", len(nodes) >= 60, f"{len(nodes)} nodes"))
    out.append(("10+ new-type L4 nodes (Jewellery/Eyewear/Watches/Hats)", len(special) >= 10,
                f"{len(special)} nodes"))

    bad_flag = []
    for p in products:
        node = cats.get(p.get("spine_node_id") or "")
        expected = node is None or not node["is_leaf"]
        if bool(p.get("needs_placement")) != expected:
            bad_flag.append(p["product_id"])
    out.append(("needs_placement true exactly when node empty or not a leaf", not bad_flag,
                f"{len(bad_flag)} wrong"))

    holding = [p["product_id"] for p in products
               if (c := cats.get(p.get("spine_node_id") or "")) and
               ("holding" in (c.get("review_flag") or "").lower() or c["name"].lower() in HOLDING_NAMES)]
    out.append(("no product on a holding node", not holding, f"{len(holding)} on holding nodes"))

    if typed is not None:
        wrong = [pid for pid, nid, ptype in typed
                 if (nid in cats and cats[nid]["is_leaf"]) != bool(ptype)]
        out.append(("products_with_type: type exactly on leaf nodes", not wrong, f"{len(wrong)} wrong"))

    brands = {p.get("brand") for p in products}
    out.append(("30+ brands", len(brands) >= 30, f"{len(brands)} brands"))

    by_node_brand = Counter((p.get("listing_url"), p.get("brand")) for p in products)
    over = [k for k, v in by_node_brand.items() if v > 3]
    out.append(("max 3 products per brand per node", not over, f"{len(over)} node/brand pairs over"))

    if plan:
        areas = area_counts(products, plan)
        off = [f"{a}: {v['got']}/{v['quota']}" for a, v in areas.items() if not v["within_10pct"]]
        out.append(("each area within 10% of quota", not off, "; ".join(off) or "ok"))
    return out


def render(report: dict, check_rows: list[tuple[str, bool, str]] | None = None) -> str:
    lines = [f"Run {report.get('run_id', '-')}: {report['status']}",
             f"  products written: {report['products_written']}   rejects: {report['rejects']}",
             f"  requests: {report['requests']} {report['requests_by_host']}   cache hits: {report['cache_hits']}"]
    if report.get("area_counts"):
        lines.append("  areas (got/quota):")
        for a, v in report["area_counts"].items():
            mark = "ok " if v["within_10pct"] else "LOW"
            lines.append(f"    {mark} {a:<28} {v['got']:>3}/{v['quota']}")
    # Only rows the run actually worked through (a --limit run never reaches most rows).
    reasons = report.get("shortfall_reasons") or {}
    short = [(u, v) for u, v in (report.get("node_counts") or {}).items()
             if v["got"] < v["quota"] and u in reasons]
    if short:
        lines.append("  plan rows under quota:")
        for u, v in short:
            reason = (report.get("shortfall_reasons") or {}).get(u, "")
            lines.append(f"    {v['got']}/{v['quota']} {v['spine_node_id'] or '(placement test)'} {u} {reason}")
    if report.get("field_fill_rates"):
        low = {k: v for k, v in report["field_fill_rates"].items() if v < 100}
        lines.append(f"  fields below 100% fill: {low or 'none'}")
    if report.get("errors"):
        lines.append(f"  errors ({len(report['errors'])}):")
        lines += [f"    {e}" for e in report["errors"][:15]]
    if report.get("notes"):
        lines.append(f"  note: {report['notes']}")
    if check_rows:
        lines.append("  checks:")
        lines += [f"    [{'x' if ok else ' '}] {name}: {detail}" for name, ok, detail in check_rows]
    return "\n".join(lines)
