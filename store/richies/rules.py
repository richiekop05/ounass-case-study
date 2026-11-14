"""Rule tables for Richie's (engineering PRD 3): seeded from `store/sample_data/rules/` and the category spine,
validated against the facet vocabulary in the database. An unknown value stops the load with a clear message.

Rows the owner has reviewed (`store_occasion_profiles.reviewed`, `store_outfit_roles.rule = 'owner'`) are never
overwritten by a re-seed.
"""
from __future__ import annotations

import json
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[2]
RULES = ROOT / "store" / "sample_data" / "rules"
ROLES = ("one_piece", "top", "bottom", "outerwear", "shoes", "bag", "jewellery", "accessory", "none")

# outfit role per spine node, by the third level of the path ("Women / Clothing / Dresses"); the second level
# (department) decides the rest. Anything not listed maps to 'none' and is flagged for review when it might
# be wearable.
L3_ROLE = {
    "Dresses": "one_piece", "Jumpsuits": "one_piece", "Kaftans": "one_piece", "Abayas": "one_piece",
    "Co-ord Sets": "one_piece",
    "Tops": "top", "Knitwear": "top", "Sweatshirts": "top",
    "Pants": "bottom", "Jeans": "bottom", "Shorts": "bottom", "Skirts": "bottom",
    "Coats & Jackets": "outerwear", "Blazers & Suits": "outerwear",
    "Belts": "accessory", "Eyewear": "accessory", "Hats": "accessory", "Scarves & Soft Accessories": "accessory",
    "Hair Accessories": "accessory", "Wallets & Small Leather Goods": "accessory", "Watches": "accessory",
}
DEPT_ROLE = {"Shoes": "shoes", "Bags": "bag", "Jewellery": "jewellery"}
NOT_WORN = {"Shoe Accessories", "Washbags", "Lunch Bags", "Changing Bags", "Luggage", "Jewellery Boxes",
            "Key Rings", "Watch Accessories", "Underwear & Socks", "Lingerie", "Shapewear", "Sleepwear",
            "Cosmetic Accessories", "Bag Accessories", "Gift Sets", "Tech", "Lifestyle", "Other Accessories",
            "Baby Essentials", "Baby Accessories"}
ASK = {"Swimwear": "one_piece? (resort)", "Activewear": "top/bottom? (sport)", "Luggage": "bag? (travel)",
       "Co-ord Sets": "one_piece", "Blazers & Suits": "outerwear (suits are a full outfit)",
       "Wallets & Small Leather Goods": "accessory", "Watches": "accessory", "Skiwear": "outerwear?"}


class RuleError(ValueError):
    pass


def _vocab(conn, facet: str) -> set[str]:
    return {r[0] for r in conn.execute(
        "select value from facet_values where facet_id = %s and is_active", (facet,))}


def _check(values, allowed: set[str], what: str, problems: list[str]):
    for v in values:
        if v not in allowed:
            problems.append(f"{what}: '{v}' is not in the vocabulary ({', '.join(sorted(allowed))})")


def role_for(path: str) -> tuple[str, str, bool]:
    """(role, rule, needs_review) for a spine path."""
    parts = path.split(" / ")
    audience = parts[0]
    dept = parts[1] if len(parts) > 1 else None
    l3 = parts[2] if len(parts) > 2 else None
    if audience in ("All",) or dept in ("Beauty", "Home", "Food") or (l3 and "Toy" in l3):
        return "none", f"{dept or audience}: not worn", False
    if l3 is None:
        return "none", "department node: not a product type", False
    if l3 in NOT_WORN:
        return "none", f"{l3}: not worn as part of an outfit", l3 in ASK
    if l3 in L3_ROLE:
        return L3_ROLE[l3], f"{l3}", l3 in ASK
    if dept in DEPT_ROLE:
        return DEPT_ROLE[dept], f"{dept}", False
    return "none", f"{l3}: not mapped", True


def seed(conn: psycopg.Connection) -> dict:
    """Validate the seed files and the spine, then upsert every rule table. Nothing is written on a problem."""
    problems: list[str] = []
    occ, form, season, colour = (_vocab(conn, f) for f in ("occasion", "formality", "season", "colour_primary"))
    profiles = json.loads((RULES / "occasion_profiles.json").read_text())["profiles"]
    for p in profiles:
        _check([p["occasion"]], occ, "occasion profile", problems)
        _check([p["formality_min"], p["formality_max"]], form, f"{p['occasion']} formality", problems)
        _check(p["seasons"], season, f"{p['occasion']} seasons", problems)
        _check(p["hero_roles"], set(ROLES) - {"none"}, f"{p['occasion']} hero roles", problems)
    templates = json.loads((RULES / "look_templates.json").read_text())["templates"]
    for anchor, slots in templates.items():
        _check([anchor, *slots], set(ROLES) - {"none"}, "look template", problems)
    col = json.loads((RULES / "colours.json").read_text())
    _check(col["neutrals"], colour, "neutral colours", problems)
    for a, b in col["complementary"]:
        _check([a, b], colour, "complementary pair", problems)
    weights = json.loads((RULES / "score_weights.json").read_text())
    personas = json.loads((ROOT / "store" / "sample_data" / "personas.json").read_text())["personas"]
    _check([p["entry_occasion"] for p in personas if p.get("entry_occasion")], occ, "persona entry occasion",
           problems)
    if problems:
        raise RuleError("The rule files use values the database does not have; nothing was loaded:\n  "
                        + "\n  ".join(problems))

    nodes = conn.execute("select node_id, path from categories").fetchall()
    with conn.transaction():
        for p in profiles:
            conn.execute(
                """insert into store_occasion_profiles (occasion, display_name, formality_min, formality_max, seasons,
                                                       hero_roles, active, note)
                   values (%s, %s, %s, %s, %s, %s, true, %s)
                   on conflict (occasion) do update set display_name = excluded.display_name,
                       formality_min = excluded.formality_min, formality_max = excluded.formality_max,
                       seasons = excluded.seasons, hero_roles = excluded.hero_roles, note = excluded.note
                   where not store_occasion_profiles.reviewed""",
                (p["occasion"], p["display_name"], p["formality_min"], p["formality_max"], p["seasons"],
                 p["hero_roles"], p.get("note")))
        flagged: dict[str, tuple[str, int]] = {}
        rows = []
        for node, path in nodes:
            role, rule, ask = role_for(path)
            if ask:                       # listed once per type ("Women / Clothing / Swimwear"), with its node count
                key = " / ".join(path.split(" / ")[:3])
                flagged[key] = (f"{role} ({ASK.get(path.split(' / ')[2], 'not mapped')})",
                                flagged.get(key, ("", 0))[1] + 1)
            rows.append((node, role, rule, ask))
        with conn.cursor() as cur:        # one batch, not a round trip per node
            cur.executemany(
                """insert into store_outfit_roles (category_node_id, role, rule, needs_review) values (%s, %s, %s, %s)
                   on conflict (category_node_id) do update set role = excluded.role, rule = excluded.rule,
                       needs_review = excluded.needs_review
                   where store_outfit_roles.rule is distinct from 'owner'""", rows)
        conn.execute("delete from store_look_templates")
        for anchor, slots in templates.items():
            for i, slot in enumerate(slots, start=1):
                conn.execute("""insert into store_look_templates (anchor_role, slot_role, position, required)
                                values (%s, %s, %s, false)""", (anchor, slot, i))
        conn.execute("delete from store_colour_pairings")
        pairs = []
        neutrals, comp, sc = set(col["neutrals"]), {tuple(sorted(x)) for x in col["complementary"]}, col["scores"]
        for a in sorted(colour):
            for b in sorted(colour):
                if a > b:
                    continue
                if a in neutrals or b in neutrals:
                    score, rule = sc["neutral"], "neutral"
                elif a == b:
                    score, rule = sc["same"], "same colour"
                elif (a, b) in comp:
                    score, rule = sc["complementary"], "complementary"
                else:
                    score, rule = sc["other"], "other"
                pairs.append((a, b, score, rule))
        with conn.cursor() as cur:
            cur.executemany("insert into store_colour_pairings (colour_a, colour_b, score, rule) values (%s, %s, %s, %s)",
                            pairs)
        conn.execute("delete from store_score_weights")
        for feature in ("shortlist", "look", "profile"):
            for signal, w in weights[feature].items():
                conn.execute("insert into store_score_weights (feature, signal, weight) values (%s, %s, %s)",
                             (feature, signal, w))
        for p in personas:
            conn.execute(
                """insert into store_personas (id, name, full_name, archetype, description, department_default,
                                              starting_intent, entry_source, entry_occasion, starting_signals, sort_order)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   on conflict (id) do update set name = excluded.name, full_name = excluded.full_name,
                       archetype = excluded.archetype, description = excluded.description,
                       department_default = excluded.department_default, starting_intent = excluded.starting_intent,
                       entry_source = excluded.entry_source, entry_occasion = excluded.entry_occasion,
                       starting_signals = excluded.starting_signals, sort_order = excluded.sort_order""",
                (p["id"], p["name"], p["full_name"], p.get("archetype"), p.get("description"), p["department_default"],
                 p["starting_intent"], p.get("entry_source"), p.get("entry_occasion"),
                 json.dumps(p.get("starting_signals") or []), p["sort_order"]))
    roles = dict(conn.execute("select role, count(*) from store_outfit_roles group by 1").fetchall())
    return {"occasions": len(profiles), "roles": roles,
            "flagged": [f"{k}: {v[0]}, {v[1]} nodes" for k, v in sorted(flagged.items())],
            "pairs": conn.execute("select count(*) from store_colour_pairings").fetchone()[0],
            "personas": len(personas)}
