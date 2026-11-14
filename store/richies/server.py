"""Richie's store server (engineering PRD 8): `/store/*` JSON endpoints and the built pages.

Reads only approved products (through `catalog.load`), writes only `store_events`. The persona and session live
in cookies; every endpoint reads them. The pages render what the endpoints return.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from scraper.store import normalize_db_url

from . import catalog
from .engine import Product
from .rules import ROOT

STATIC = Path(__file__).resolve().parent / "static"
PERSONAS = ROOT / "store" / "sample_data" / "personas.json"
COOKIE_P, COOKIE_S = "richies_persona", "richies_session"
DEPT_AUDIENCE = {"women": {"Women"}, "men": {"Men"}}
FILTER_FACETS = ["category", "colour_primary", "occasion", "formality", "season", "aesthetic", "sleeve_length", "length"]
FILTER_LABELS = {"category": "Category", "colour_primary": "Colour", "occasion": "Occasion", "formality": "Formality",
                 "season": "Season", "aesthetic": "Aesthetic", "sleeve_length": "Sleeve length", "length": "Length"}
EVENT_TYPES = {"view", "save", "add_to_bag", "add_look_to_bag", "size_select", "filter_apply", "occasion_select",
               "refine_apply", "shortlist_view", "look_view", "swap_out", "swap_in", "look_card_view", "look_card_tap"}
LOOK_PLACEMENTS = ("top_band", "grid", "product_page")
log = logging.getLogger("richies")

app = FastAPI(title="Richie's", docs_url=None, redoc_url=None, openapi_url=None)

# ------------------------------------------------------------------ database: one connection, one lock

_db_lock = threading.RLock()
_conn: psycopg.Connection | None = None


def conn() -> psycopg.Connection:
    global _conn
    with _db_lock:
        if _conn is None or _conn.closed or _conn.broken:
            load_dotenv(ROOT / ".env")
            url = os.environ.get("SUPABASE_DB_URL", "").strip()
            if not url:
                raise RuntimeError("SUPABASE_DB_URL is not set. Copy .env.example to .env and fill it in.")
            _conn = psycopg.connect(normalize_db_url(url), autocommit=True)
        return _conn


def q(sql: str, params=()) -> list[tuple]:
    with _db_lock:
        cur = conn().execute(sql, params)
        return cur.fetchall() if cur.description else []


def products() -> tuple[list[Product], object]:
    with _db_lock:
        c = conn()
        c.autocommit = False
        try:
            return catalog.load(c)
        finally:
            c.rollback()
            c.autocommit = True


# ------------------------------------------------------------------ sessions

_personas: dict = {}


def personas() -> list[dict]:
    """The four personas with their starting signals. The intent badge and the "what the store does" line are
    computed by running the intent rule and the lead-module rule over each persona's starting events (seed history
    plus the simulated starting session); only the signal lines are stored text. They change only when
    `richies seed-rules` or `load-personas` runs, so they are kept for ten minutes."""
    import time
    if not _personas or time.time() - _personas["at"] > 600:
        from .lead import does_line, lead_module, starting_events
        _, rules = products()
        rows = []
        for i, n, arch, dept, signals in q("""select id, name, archetype, department_default, starting_signals
                                                from store_personas order by sort_order"""):
            evs = seed_events(i) + starting_events([], _session_start_events(i))
            state, why = intent_rule([e for e in evs if not e.is_seed])
            lead = lead_module(state, evs, looks_threshold(rules), department_known=dept != "all")
            rows.append({"id": i, "name": n, "archetype": arch, "fullName": f"{n} · {arch}" if arch else n,
                         "department": dept, "signals": [x["text"] for x in (signals or [])],
                         "startingIntent": state, "startingIntentReason": why, "startingLead": lead["module"],
                         "does": does_line(lead, lambda o: label(rules, "occasion", o))})
        _personas.update(at=time.time(), rows=rows)
    return _personas["rows"]


def _session_start_events(persona: str) -> list[dict]:
    try:
        data = json.loads(PERSONAS.read_text())["personas"]
    except (OSError, ValueError, KeyError):
        return []
    return next((p.get("session_start", []) for p in data if p["id"] == persona), [])


# ------------------------------------------------------------------ events in memory (the server writes them all)

from .profile import Event, effect as event_effect, intent as intent_rule, profile as build_profile, taste_match

_ev_lock = threading.Lock()
_session_events: dict[str, list[Event]] = {}     # session id -> its events, oldest first
_seed_events: dict[str, tuple[float, list[Event]]] = {}
_effective: dict[str, dict] = {}                  # session id -> intent, eligibility and lead in force since the
                                                  # last page load (all three are decided when a page loads)


def _rows_to_events(rows) -> list[Event]:
    return [Event(t, pid, c or {}, seed, at.isoformat() if at else "") for t, pid, c, seed, at in rows]


def seed_events(persona: str) -> list[Event]:
    import time
    with _ev_lock:
        hit = _seed_events.get(persona)
        if hit and time.time() - hit[0] < 60:
            return hit[1]
    ev = _rows_to_events(q("""select type, product_id, context, is_seed, created_at from store_events
                               where persona_id = %s and is_seed order by id""", (persona,)))
    with _ev_lock:
        _seed_events[persona] = (time.time(), ev)
    return ev


def session_events(persona: str, sid: str) -> list[Event]:
    with _ev_lock:
        if sid in _session_events:
            return list(_session_events[sid])
    ev = _rows_to_events(q("""select type, product_id, context, is_seed, created_at from store_events
                               where persona_id = %s and session_id = %s and not is_seed order by id""", (persona, sid)))
    with _ev_lock:
        _session_events.setdefault(sid, ev)
        return list(_session_events[sid])


def _remember(sid: str, e: Event):
    with _ev_lock:
        _session_events.setdefault(sid, []).append(e)


def looks_threshold(rules) -> int:
    """Positive events needed for a taste profile (Looks for you and the lead module); a settings row."""
    from .lead import DEFAULT_THRESHOLD
    return int((rules.weights.get("profile") or {}).get("looks_threshold", DEFAULT_THRESHOLD))


def effective(persona: str, sid: str) -> dict:
    """What is in force for this page: set when a page loads (PRD 7), so a change applies to the next page."""
    with _ev_lock:
        hit = _effective.get(sid)
    return hit or page_load(persona, sid)


def effective_intent(persona: str, sid: str) -> tuple[str, str]:
    return effective(persona, sid)["intent"]


def page_load(persona: str, sid: str, audience: str | None = None) -> dict:
    """Decide intent, Looks for you eligibility and the PLP lead module for the page being loaded (engineering
    PRD 5 and 7). The moment a session becomes eligible is logged as an event, with the reason."""
    from datetime import datetime, timezone
    from .lead import eligibility, lead_module
    _, rules = products()
    sess = session_events(persona, sid)
    evs = seed_events(persona) + sess
    state = intent_rule(sess)
    occ_label = lambda o: label(rules, "occasion", o)          # noqa: E731
    elig = eligibility(state[0], evs, looks_threshold(rules), occ_label)
    dept = next((x["department"] for x in personas() if x["id"] == persona), "women")
    lead = lead_module(state[0], evs, looks_threshold(rules), department_known=dept != "all" or bool(audience))
    with _ev_lock:
        prev = _effective.get(sid)
    since = None
    if elig["eligible"]:
        if prev and prev.get("looksSince"):
            since = prev["looksSince"]
        elif prev is None or not _first_page(sess):
            since = "Eligible from the first page"
        else:                                     # crossed the line during this session: log the moment and why
            since = (f"Became eligible after {elig['count']} positive signals" if elig["code"] == "taste_profile"
                     else "Became eligible when an occasion came into play")
            ctx = {"reason": elig["reason"], "code": elig["code"]}
            q("""insert into store_events (persona_id, session_id, type, product_id, context, is_seed)
                 values (%s, %s, 'looks_eligible', null, %s, false)""", (persona, sid, json.dumps(ctx)))
            _remember(sid, Event("looks_eligible", None, ctx, False, datetime.now(timezone.utc).isoformat()))
    out = {"intent": state, "looks": elig, "lead": lead, "looksSince": since}
    with _ev_lock:
        _effective[sid] = out
    return out


def _first_page(sess: list[Event]) -> bool:
    """True once the session has done anything beyond its simulated start."""
    return any(not (e.context or {}).get("sessionStart") for e in sess)


def new_session(persona: str) -> str:
    """A fresh session for the persona. Sara's starts from a search referral with a size selected (PRD 7)."""
    sid = uuid.uuid4().hex[:16]
    start = _session_start_events(persona)
    if start:                                     # one round trip for the whole simulated start
        q("insert into store_events (persona_id, session_id, type, product_id, context, is_seed) values "
          + ", ".join(["(%s, %s, %s, %s, %s, false)"] * len(start)),
          tuple(x for e in start for x in (persona, sid, e["type"], e.get("product_id"),
                                           json.dumps({**(e.get("context") or {}), "sessionStart": True}))))
    with _ev_lock:
        _session_events[sid] = []
    for e in _session_start_events(persona):
        _remember(sid, Event(e["type"], e.get("product_id"), {**(e.get("context") or {}), "sessionStart": True}, False))
    page_load(persona, sid)
    return sid


def session(request: Request, response: Response | None = None) -> tuple[str, str]:
    """(persona, session id) from the cookies; a first visit starts as Layla."""
    p, s = request.cookies.get(COOKIE_P), request.cookies.get(COOKIE_S)
    known = {x["id"] for x in personas()}
    if p not in known or not s:
        p = p if p in known else "layla"
        s = new_session(p)
        if response is not None:
            _set(response, p, s)
    return p, s


def _set(response: Response, p: str, s: str):
    response.set_cookie(COOKIE_P, p, samesite="lax", httponly=True)
    response.set_cookie(COOKIE_S, s, samesite="lax", httponly=True)


class SessionBody(BaseModel):
    persona: str


@app.get("/store/personas")
def api_personas(request: Request, response: Response):
    p, s = session(request, response)
    return {"personas": personas(), "current": p, "session": s}


@app.post("/store/session")
def api_session(body: SessionBody, response: Response):
    """Switch persona or reset: always a new session id."""
    if body.persona not in {x["id"] for x in personas()}:
        return JSONResponse({"message": "Unknown persona."}, status_code=422)
    s = new_session(body.persona)
    _set(response, body.persona, s)
    return {"persona": body.persona, "session": s}


# ------------------------------------------------------------------ catalogue

def _sign(paths: list[str]) -> dict:
    from app.photos import sign            # the tagger app's signer: short-lived links, never the key
    return sign([p for p in paths if p])


def label(rules, facet: str, value: str) -> str:
    l = rules.labels.get((facet, value), value.replace("_", " "))
    return l[:1].upper() + l[1:]


def _values(p: Product, facet: str) -> tuple:
    return (p.category,) if facet == "category" else p.values(facet)


def _in_dept(p: Product, dept: str) -> bool:
    aud = DEPT_AUDIENCE.get(dept)
    return aud is None or p.audience in aud


def card(p: Product, photos: dict) -> dict:
    return {"id": p.id, "brand": p.brand, "name": p.name, "price": p.price,
            "image": photos.get(p.images[0]) if p.images else None, "colour": p.first("colour_primary")}


def _filters(request: Request) -> dict[str, list[str]]:
    out = {}
    for f in FILTER_FACETS:
        vals = request.query_params.getlist(f)
        if vals:
            out[f] = vals
    return out


@app.get("/store/products")
def api_products(request: Request, response: Response, dept: str = "all"):
    """The PLP grid: by taste match for a low-intent shopper with a profile ("Selected for you"), otherwise
    newest first ("New in")."""
    persona, sid = session(request, response)
    ps, rules = products()
    f = _filters(request)
    items = [p for p in ps if _in_dept(p, dept) and all(set(_values(p, k)) & set(v) for k, v in f.items())]
    items.sort(key=lambda p: (p.launch, p.id), reverse=True)
    order = "new"
    if effective_intent(persona, sid)[0] == "low":
        taste = taste_for(persona, sid)
        scores = {p.id: taste(p) for p in items}
        if any(v is not None for v in scores.values()):
            order = "taste"
            items.sort(key=lambda p: -(scores[p.id] or 0))          # stable: newest first within a score
    photos = _sign([p.images[0] for p in items if p.images])
    return {"products": [card(p, photos) for p in items], "order": order, "count": len(items)}


@app.get("/store/facets")
def api_facets(request: Request, response: Response, dept: str = "all"):
    """Each filterable facet with the values that have products in the department (counts)."""
    session(request, response)
    ps, rules = products()
    mine = [p for p in ps if _in_dept(p, dept)]
    out = []
    for f in FILTER_FACETS:
        counts: dict[str, int] = {}
        for p in mine:
            for v in set(_values(p, f)):
                if v:
                    counts[v] = counts.get(v, 0) + 1
        if f == "category":
            vals = sorted(counts)
        else:
            order = {v: i for i, ((ff, v), _) in enumerate(rules.labels.items()) if ff == f}
            vals = sorted(counts, key=lambda v: order.get(v, 999))
        out.append({"id": f, "label": FILTER_LABELS[f],
                    "values": [{"value": v, "label": v if f == "category" else label(rules, f, v), "count": counts[v]}
                               for v in vals]})
    return {"facets": out}


@app.get("/store/products/{pid}")
def api_product(pid: str, request: Request, response: Response):
    session(request, response)
    ps, rules = products()
    p = next((x for x in ps if x.id == pid), None)
    if p is None:
        return JSONResponse({"message": "That piece is not in the store."}, status_code=404)
    row = q("""select description, details, size_and_fit, currency, category_path from v_catalog_products
                where product_id = %s""", (pid,))
    desc, details, size_fit, currency, path = row[0] if row else (None, None, None, "AED", p.path)
    photos = _sign(list(p.images))
    tags = []
    for facet in ("occasion", "formality", "season", "aesthetic", "colour_primary", "sleeve_length", "length",
                  "sheerness"):
        for v in p.values(facet):
            tags.append({"facet": facet, "facetLabel": FILTER_LABELS.get(facet, facet.replace("_", " ").capitalize()),
                         "value": v, "label": label(rules, facet, v),
                         "confidence": None if p.sample else p.confidence.get(facet)})
    sizes = _sizes(size_fit, p)
    return {"id": p.id, "brand": p.brand, "name": p.name, "price": p.price, "currency": currency or "AED",
            "images": [photos.get(i) for i in p.images if photos.get(i)], "description": desc,
            "details": details, "sizeAndFit": size_fit, "sizes": sizes, "category": p.category,
            "department": {"Women": "women", "Men": "men"}.get(p.audience, "all"), "role": p.role,
            "tags": tags, "sample": p.sample, "inStock": p.in_stock}


def _sizes(size_fit, p: Product) -> list[str]:
    """Sizes for the selector: from the product's size data when it lists them, else by product type."""
    if isinstance(size_fit, dict):
        for k in ("sizes", "available_sizes"):
            if isinstance(size_fit.get(k), list) and size_fit[k]:
                return [str(x) for x in size_fit[k]][:10]
    if p.role in ("one_piece", "top", "bottom", "outerwear"):
        return ["XS", "S", "M", "L", "XL"] if p.audience == "Women" else ["S", "M", "L", "XL"]
    if p.role == "shoes":
        return ["36", "37", "38", "39", "40", "41"] if p.audience == "Women" else ["41", "42", "43", "44", "45"]
    return []


# ------------------------------------------------------------------ Complete the Look (engineering PRD 6)


SLOT_LABEL = {"one_piece": "Dress", "top": "Top", "bottom": "Bottom", "outerwear": "Outerwear", "shoes": "Shoes",
              "bag": "Bag", "jewellery": "Jewellery", "accessory": "Accessory"}


def _noun(p: Product) -> str:
    n = (p.category or "piece").lower()
    if n in ("jeans", "pants", "shorts", "trousers", "leggings", "sunglasses"):   # plural-only words stay plural
        return n
    if n.endswith("sses"):                 # dresses -> dress
        return n[:-2]
    return n[:-1] if n.endswith("s") and not n.endswith("ss") else n


def reason_text(code: str | None, item: Product, anchor: Product, rules, occasion: str | None) -> str | None:
    """Customer wording for a reason code. The engine knows no personas; the wording follows the signal."""
    if code is None:
        return None
    if code == "colour":
        c = anchor.first("colour_primary")
        return f"Works with the {label(rules, 'colour_primary', c).lower()}" if c else "Colours that work together"
    if code == "formality":
        return "Same dress code"
    if code == "occasion":
        if occasion:
            prof = rules.occasions.get(occasion, {})
            return f"Right for {(prof.get('display_name') or label(rules, 'occasion', occasion)).lower()}"
        return f"Goes with the {_noun(anchor)} you chose"
    if code == "aesthetic":
        a = item.first("aesthetic")
        return f"Same {label(rules, 'aesthetic', a).lower()} feel" if a else None
    if code == "taste":
        a = item.first("aesthetic")
        return f"Matches your {label(rules, 'aesthetic', a).lower()} picks" if a else "In your taste"
    if code == "price":
        return f"Goes with the {_noun(anchor)} you chose"
    return None


def current_profile(persona: str, sid: str):
    ps, rules = products()
    by_id = {p.id: p for p in ps}
    return build_profile(seed_events(persona) + session_events(persona, sid), by_id, rules.weights.get("profile")), by_id


def taste_for(persona: str, session_id: str):
    """The shopper's taste match from seed and session events; None (signal dropped) without a profile."""
    prof, _ = current_profile(persona, session_id)
    return lambda p: taste_match(prof, p)


@app.get("/store/products/{pid}/look")
def api_look(pid: str, request: Request, response: Response, occasion: str | None = None):
    from .features import build_look
    persona, sid = session(request, response)
    ps, rules = products()
    anchor = next((x for x in ps if x.id == pid), None)
    if anchor is None:
        return JSONResponse({"message": "That piece is not in the store."}, status_code=404)
    if occasion and occasion not in rules.occasions:
        occasion = None
    high = effective_intent(persona, sid)[0] == "high"
    look = build_look(anchor, ps, rules, occasion, taste_for(persona, sid), high_intent=high)
    photos = _sign([it["product"].images[0] for s in look["slots"] for it in s["items"] if it["product"].images])
    slots = [{"slot": s["slot"], "label": SLOT_LABEL.get(s["slot"], s["slot"].title()),
              "items": [{**card(it["product"], photos), "reasonCode": it["reason"],
                         "reason": reason_text(it["reason"], it["product"], anchor, rules, occasion)}
                        for it in s["items"]]}
             for s in look["slots"]]
    filled = len(slots)
    prof = rules.occasions.get(occasion or "", {})
    return {"anchor": pid, "occasion": occasion, "occasionLabel": prof.get("display_name"),
            "occasionInPlay": occasion, "occasionOptions": occasion_options(persona, sid, anchor, high),
            "filled": filled, "highIntent": high,
            "title": ("Pairs well with" if high or filled == 1 else "Complete the look") if filled else None,
            "slots": slots}


def occasion_options(persona: str, sid: str, anchor: Product, high: bool) -> list[dict]:
    """The product-page occasion row (engineering PRD V7, 6): the product's own occasion tags that are active
    (offered on the listing page for its department) and would fill at least one slot, the session's hinted
    occasion first, then tag order; at most four. None for high intent or a product with no outfit role."""
    from .features import available_occasions, build_look
    from .lead import occasion_in_play
    if high or anchor.role == "none":
        return []
    ps, rules = products()
    active = set(available_occasions(ps, rules, {anchor.audience} if anchor.audience in ("Women", "Men") else None))
    own = [o for o in anchor.values("occasion") if o in active]
    hint, _ = occasion_in_play(seed_events(persona) + session_events(persona, sid))
    if hint in own:
        own = [hint] + [o for o in own if o != hint]
    keep = [o for o in own if build_look(anchor, ps, rules, o, high_intent=True)["filled"] >= 1][:4]
    return [{"value": o, "label": label(rules, "occasion", o),
             "phrase": _phrase(rules.occasions[o]["display_name"])} for o in keep]


# ------------------------------------------------------------------ occasion assistant (engineering PRD 5)

def _audiences(dept: str):
    return DEPT_AUDIENCE.get(dept)


def chip_text(code: str, value: str | None, rules) -> str | None:
    if not value:
        return None
    if code == "occasion":
        return label(rules, "occasion", value)
    if code == "formality":
        return label(rules, "formality", value)
    if code == "season":
        return f"{label(rules, 'season', value)} season"
    if code == "taste":
        return f"Matches your {label(rules, 'aesthetic', value).lower()} picks"
    return None


def _phrase(display: str) -> str:
    """'An evening event' -> 'an evening event'; 'Work' -> 'work'."""
    return display[:1].lower() + display[1:] if display.split(" ")[0] in ("A", "An") else display.lower()


@app.get("/store/occasions")
def api_occasions(request: Request, response: Response, dept: str = "women"):
    """Active occasions with at least four eligible hero pieces for the department. Occasions of the shopper's
    past purchases come first, marked as suggested."""
    from .features import available_occasions
    persona, sid = session(request, response)
    ps, rules = products()
    occ = available_occasions(ps, rules, _audiences(dept))
    by_id = {p.id: p for p in ps}
    # suggestions come from the most recent order only, and only its occasion-specific values: an everyday or
    # work piece says nothing about where the shopper is going (Design PRD 3: Noor's evening occasions first)
    purchases = [e for e in seed_events(persona) + session_events(persona, sid)
                 if e.type in ("purchase", "add_to_bag") and e.product_id in by_id]
    bought = []
    if purchases:
        last = by_id[purchases[-1].product_id]
        bought = [o for o in last.values("occasion") if o in occ and o not in ("everyday", "work", "travel")]
    # the occasion in play (arrived with, or chosen) leads, then the last order's occasions (engineering PRD V5)
    first = effective(persona, sid)["lead"].get("suggestedOccasion")
    if first in occ:
        bought = [first] + [o for o in bought if o != first]
    ordered = bought + [o for o in occ if o not in bought]
    return {"occasions": [{"value": o, "label": label(rules, "occasion", o),
                           "display": rules.occasions[o]["display_name"], "phrase": _phrase(rules.occasions[o]["display_name"]),
                           "suggested": o in bought} for o in ordered],
            "note": f"{label(rules, 'occasion', bought[0])} occasions first, from your recent order" if bought else None}


class ShortlistBody(BaseModel):
    occasion: str
    dept: str = "women"
    refine: dict = {}


@app.post("/store/shortlist")
def api_shortlist(body: ShortlistBody, request: Request, response: Response):
    from .features import occasion_shortlist
    persona, sid = session(request, response)
    ps, rules = products()
    if body.occasion not in rules.occasions:
        return JSONResponse({"message": "Unknown occasion."}, status_code=422)
    refine = {k: bool(v) for k, v in (body.refine or {}).items() if k in ("budget", "neutral", "covered")}
    taste = taste_for(persona, sid)
    sl = occasion_shortlist(body.occasion, ps, rules, _audiences(body.dept), refine, taste)
    photos = _sign([x["product"].images[0] for x in sl if x["product"].images])
    prof = rules.occasions[body.occasion]
    has_profile = any(taste(p) is not None for p in ps[:1] or ps)
    return {"occasion": body.occasion, "display": prof["display_name"], "phrase": _phrase(prof["display_name"]),
            "refine": refine, "count": len(sl), "tasteRanked": has_profile,
            "items": [{**card(x["product"], photos),
                       "reasons": [t for t in (chip_text(r["code"], r["value"], rules) for r in x["reasons"]) if t],
                       "reasonCodes": [r["code"] for r in x["reasons"]]} for x in sl]}


@app.get("/store/looks-for-you")
def api_looks_for_you(request: Request, response: Response, dept: str = "women", count: int = 6,
                      exclude_product_id: str | None = None):
    """Looks for an eligible shopper (engineering PRD V5): `eligible`, the reason, and up to `count` looks, each
    with a distinct hero. Eligibility is the one decided when the page loaded. With an occasion in play the looks
    are built for it; otherwise from the taste profile."""
    from .features import looks_for_you
    persona, sid = session(request, response)
    elig = effective(persona, sid)["looks"]
    if not elig["eligible"]:
        return {"eligible": False, "code": elig["code"], "reason": elig["reason"], "looks": []}
    ps, rules = products()
    if exclude_product_id:                        # the product page: its own department
        anchor = next((p for p in ps if p.id == exclude_product_id), None)
        if anchor and anchor.audience in ("Women", "Men"):
            dept = anchor.audience.lower()
    occ = elig["occasion"] if elig["code"] == "occasion_in_play" else None
    looks = looks_for_you(ps, rules, _audiences(dept), taste_for(persona, sid), count=max(1, min(count, 9)),
                          exclude=exclude_product_id, occasion=occ)
    paths = []
    for lk in looks:
        paths += list(lk["hero"].images[:1]) + [s["items"][0]["product"].images[0] for s in lk["look"]["slots"]
                                                 if s["items"][0]["product"].images]
    photos = _sign(paths)
    out = []
    for lk in looks:
        h = lk["hero"]
        aes = h.first("aesthetic")
        occ_label = rules.labels.get(("occasion", lk["occasion"]), lk["occasion"])     # keeps "Ramadan/Eid" capitalised
        title = f"{label(rules, 'aesthetic', aes) if aes else ''} {occ_label}".strip()
        slots = [s["items"][0]["product"] for s in lk["look"]["slots"]]
        out.append({"occasion": lk["occasion"], "title": title, "hero": card(h, photos),
                    "thumbs": [card(p, photos) for p in slots[:3]],
                    "sub": f"{h.brand} {h.name.lower()}, with {len(slots)} piece{'s' if len(slots) != 1 else ''}"})
    return {"eligible": True, "code": elig["code"], "reason": elig["reason"], "looks": out}


# ------------------------------------------------------------------ events and state

class EventBody(BaseModel):
    type: str
    productId: str | None = None
    context: dict = {}


@app.post("/store/events")
def api_event(body: EventBody, request: Request, response: Response):
    if body.type not in EVENT_TYPES:
        return JSONResponse({"message": f"Unknown event type {body.type}."}, status_code=422)
    p, s = session(request, response)
    pid = body.productId
    if pid and not q("select 1 from catalog_products where product_id = %s", (pid,)):
        pid = None
    q("""insert into store_events (persona_id, session_id, type, product_id, context, is_seed)
         values (%s, %s, %s, %s, %s, false)""", (p, s, body.type, pid, json.dumps(body.context or {})))
    from datetime import datetime, timezone
    _remember(s, Event(body.type, pid, body.context or {}, False, datetime.now(timezone.utc).isoformat()))
    return {"ok": True}


@app.get("/store/state")
def api_state(request: Request, response: Response, load: bool = False, audience: str | None = None):
    """Behind the scenes: intent in force (and the change pending for the next page), the taste profile's top
    values, and the last five events with their effect. `load=1`: the page calls it when it loads, which is when
    a change of intent takes effect (PRD 7)."""
    p, s = session(request, response)
    if load:
        page_load(p, s, audience)
    eff = effective(p, s)
    state, reason = eff["intent"]
    nxt, nxt_reason = intent_rule(session_events(p, s))
    prof, by_id = current_profile(p, s)
    _, rules = products()

    def top(facets, prefix=None):
        merged: dict[str, float] = {}
        for f in facets:
            for v, w in ((prof or {}).get(f) or {}).items():
                lab = label(rules, f, v)
                if f == "sleeve_length":
                    lab = f"{lab} sleeves"
                elif f == "length":
                    lab = f"{lab} length"
                merged[lab] = max(merged.get(lab, 0.0), w)
        return [{"label": k, "share": round(v, 3)} for k, v in sorted(merged.items(), key=lambda x: -x[1])[:3]]
    dims = [{"label": "Aesthetic", "rows": top(["aesthetic"])}, {"label": "Colour", "rows": top(["colour_primary"])},
            {"label": "Formality", "rows": top(["formality"])},
            {"label": "Coverage", "rows": top(["sleeve_length", "length", "sheerness"])}]
    # look card views are instrumentation for the funnel, not something the shopper did: kept out of the panel
    recent = [e for e in session_events(p, s)[::-1] + seed_events(p)[::-1] if e.type != "look_card_view"][:5]
    weights = rules.weights.get("profile")
    events = []
    for e in recent:
        prod = by_id.get(e.product_id) if e.product_id else None
        events.append({"type": e.type, "product": f"{prod.brand} {prod.name}" if prod else None, "context": e.context,
                       "at": e.at, "seed": e.is_seed, "effect": event_effect(e, by_id, rules.labels, weights)})
    return {"persona": p, "session": s,
            "intent": {"state": state, "reason": reason, "pending": nxt if nxt != state else None,
                       "pendingReason": nxt_reason if nxt != state else None},
            "profile": dims if prof else None, "events": events,
            "looks": _looks_state(p, s, eff, rules), "lead": eff["lead"]}


def _looks_state(p: str, s: str, eff: dict, rules) -> dict:
    """Looks for you for the panel: the state in force, why, the count against the threshold, and whether the
    threshold has been reached since the page loaded (it shows from the next page)."""
    from .lead import eligibility
    e = eff["looks"]
    now = eligibility(eff["intent"][0], seed_events(p) + session_events(p, s), looks_threshold(rules),
                      lambda o: label(rules, "occasion", o))
    return {"state": "never" if e["never"] else "eligible" if e["eligible"] else "not_yet",
            "code": e["code"], "reason": e["reason"], "count": now["count"], "threshold": e["threshold"],
            "pending": bool(now["eligible"] and not e["eligible"]), "since": eff.get("looksSince")}


# ------------------------------------------------------------------ warm-up

@app.on_event("startup")
def _warm():
    """Load the catalogue and the persona summary in the background, so the first visitor does not wait."""
    def run():
        try:
            products()
            personas()
        except Exception:                          # noqa: BLE001 - a cold start is slower, never broken
            log.exception("warm-up failed")
    threading.Thread(target=run, daemon=True).start()


# ------------------------------------------------------------------ pages

if STATIC.exists():
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")


@app.get("/{path:path}")
def spa(path: str):
    index = STATIC / "index.html"
    if path.startswith("store/"):
        return JSONResponse({"message": "Not found."}, status_code=404)
    if not index.exists():
        return JSONResponse({"message": "The store pages are not built. Run: cd store/web && npm install && npm run build"},
                            status_code=503)
    # never cached, so a rebuilt store shows on the next load (the assets it names carry a content hash)
    return FileResponse(index, headers={"Cache-Control": "no-cache"})
