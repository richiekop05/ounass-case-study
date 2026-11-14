"""Persona histories and the coverage report (engineering PRD 4).

Store sample products come from Live tag's "Store sample" mode (owner's decision, 14 Nov 2026), not from a file;
`personas.json` holds only the personas and their starting histories. Every product a history names must be
approved and in the store; the load stops with the list of missing IDs otherwise.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import psycopg

from .rules import ROOT

PERSONAS = ROOT / "store" / "sample_data" / "personas.json"
SEED_TYPES = {"view", "save", "purchase", "add_to_bag", "size_select", "search_referral", "entry"}


class LoadError(ValueError):
    pass


def load_personas(conn: psycopg.Connection, path: Path = PERSONAS) -> dict:
    """Write each persona's starting history to store_events (is_seed), replacing the previous seed events."""
    data = json.loads(path.read_text())["personas"]
    store = {r[0] for r in conn.execute("select product_id from v_catalog_products")}
    missing, bad = [], []
    for p in data:
        for e in p.get("events", []) + p.get("session_start", []):
            if e["type"] not in SEED_TYPES:
                bad.append(f"{p['id']}: event type '{e['type']}'")
            if e.get("product_id") and e["product_id"] not in store:
                missing.append(f"{p['id']}: {e['product_id']}")
    if missing or bad:
        raise LoadError("Persona histories refer to products not approved in the store, or unknown events; "
                        "nothing was loaded:\n  " + "\n  ".join(bad + missing))
    counts = {}
    with conn.transaction():
        for p in data:
            conn.execute("delete from store_events where persona_id = %s and is_seed", (p["id"],))
            rows = [(p["id"], "seed", e["type"], e.get("product_id"), json.dumps(e.get("context") or {}))
                    for e in p.get("events", [])]
            with conn.cursor() as cur:
                cur.executemany("""insert into store_events (persona_id, session_id, type, product_id, context, is_seed)
                                   values (%s, %s, %s, %s, %s, true)""", rows)
            counts[p["id"]] = len(rows)
    return counts


# ------------------------------------------------------------------ drafting starting histories

def _catalog(conn) -> list[dict]:
    rows = conn.execute("""select p.product_id, p.name, p.brand, p.audience, c.path, o.role
                             from v_catalog_products p join categories c on c.node_id = p.category_id
                             left join store_outfit_roles o on o.category_node_id = p.category_id
                            order by p.product_id""").fetchall()
    tags = defaultdict(lambda: defaultdict(list))
    for pid, fid, val in conn.execute("""select product_id, facet_id, value from v_product_facets
                                          where facet_id in ('aesthetic', 'colour_primary', 'formality', 'occasion')"""):
        tags[pid][fid].append(val)
    return [{"id": r[0], "name": r[1], "brand": r[2], "audience": r[3], "path": r[4], "role": r[5],
             **{k: v for k, v in tags[r[0]].items()}} for r in rows]


def draft_histories(conn) -> dict:
    """A first proposal of starting histories from the approved catalogue, for the owner to edit (PRD open item).
    Layla: about 15 events on minimalist or classic pieces in neutral colours. Noor: one past order and two views
    of formal dresses. James: nothing. Sara: three views of women's bags, and a session that starts from a search
    for one bag with a size selected."""
    cat = _catalog(conn)
    women = [p for p in cat if p["audience"] == "Women" and p["role"] not in (None, "none")]   # worn pieces only
    women.sort(key=lambda p: (p["role"] not in ("one_piece", "top", "bottom", "outerwear"), p["id"]))   # clothes first
    neutral = {"black", "white", "cream", "beige", "grey", "neutral", "brown"}
    layla = [p for p in women if set(p.get("aesthetic", [])) & {"minimalist", "classic"}
             and set(p.get("colour_primary", [])) & neutral]
    noor = [p for p in women if p["role"] == "one_piece"
            and set(p.get("formality", [])) & {"formal", "black_tie"}]
    bags = [p for p in women if p["role"] == "bag" and "Top-Handle" in p["path"]] or \
           [p for p in women if p["role"] == "bag"]
    plan = (["purchase"] * 3 + ["save"] * 4 + ["view"] * 8)
    ev = lambda t, p, why: {"type": t, "product_id": p["id"], "note": f"{p['brand']} · {p['name']} ({why})"}
    out = {
        "layla": [ev(t, p, "minimalist or classic, neutral") for t, p in zip(plan, layla)],
        "noor": [ev("purchase", noor[0], "formal one-piece")] + [ev("view", p, "formal one-piece") for p in noor[1:3]]
                if noor else [],
        "james": [],
        "sara": [ev("view", p, "women's bag") for p in bags[:3]],
        "sara_session_start": ([{"type": "search_referral", "product_id": bags[3]["id"] if len(bags) > 3 else bags[0]["id"],
                                 "note": "arrived from a search for this bag"},
                                {"type": "size_select", "context": {"size": "one size"}, "note": "selected a size"}]
                               if bags else []),
    }
    return out


def write_draft(conn, path: Path = PERSONAS) -> dict:
    data = json.loads(path.read_text())
    d = draft_histories(conn)
    for p in data["personas"]:
        p["events"] = d.get(p["id"], [])
        if p["id"] == "sara":
            p["session_start"] = d["sara_session_start"]
    data["_note"] = ("Starting histories DRAFTED from the approved catalogue on load; the owner chooses the final "
                     "ones (engineering PRD 9). 'note' fields are for reading only.")
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    return {k: len(v) for k, v in d.items()}


# ------------------------------------------------------------------ coverage

def coverage(conn) -> list[tuple]:
    """(occasion, audience, role, products): approved products per outfit role for each active occasion and
    audience. Hero roles count products tagged with the occasion; slot roles (shoes, bags, jewellery) count every
    product of the role, since looks match them on formality and occasion overlap."""
    profiles = conn.execute("select occasion, hero_roles from store_occasion_profiles where active order by occasion").fetchall()
    cat = _catalog(conn)
    rows = []
    for occ, heroes in profiles:
        for aud in ("Women", "Men"):
            mine = [p for p in cat if p["audience"] == aud]
            for role in ("one_piece", "top", "bottom", "outerwear", "shoes", "bag", "jewellery", "accessory"):
                if role in heroes:
                    n = sum(1 for p in mine if p["role"] == role and occ in p.get("occasion", []))
                    kind = "hero"
                else:
                    n = sum(1 for p in mine if p["role"] == role and (not p.get("occasion") or occ in p["occasion"]))
                    kind = "slot"
                rows.append((occ, aud, role, kind, n))
    return rows
