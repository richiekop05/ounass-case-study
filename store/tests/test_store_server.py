"""Richie's step 4: the store server reads only approved products, filters by tag, keeps sessions per persona and
records events. Uses the real shared database; the events it writes are removed afterwards."""
from __future__ import annotations

import os

import pytest
from dotenv import load_dotenv

load_dotenv()
pytestmark = pytest.mark.skipif(not os.environ.get("SUPABASE_DB_URL"), reason="SUPABASE_DB_URL not set")


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from richies import server
    c = TestClient(server.app)
    sessions = []
    c.sessions = sessions
    yield c
    if sessions:
        server.q("delete from store_events where session_id = any(%s) and not is_seed", (sessions,))


def start(client, persona):
    r = client.post("/store/session", json={"persona": persona})
    assert r.status_code == 200
    client.sessions.append(r.json()["session"])
    return r.json()["session"]


def test_the_store_lists_only_approved_products(client):
    from richies import server
    ids = {p["id"] for p in client.get("/store/products?dept=all").json()["products"]}
    approved = {r[0] for r in server.q("select product_id from v_catalog_products")}
    assert ids == approved
    unapproved = server.q("select product_id from catalog_products where not is_published limit 1")
    if unapproved:
        assert client.get(f"/store/products/{unapproved[0][0]}").status_code == 404


def test_department_and_tag_filters_combine(client):
    from richies import server
    women = client.get("/store/products?dept=women").json()["products"]
    aud = dict(server.q("select product_id, audience from v_catalog_products"))
    assert women and all(aud[p["id"]] == "Women" for p in women)
    r = client.get("/store/products?dept=women&occasion=evening&colour_primary=black").json()["products"]
    tags = {}
    for pid, f, v in server.q("select product_id, facet_id, value from v_product_facets "
                              "where facet_id in ('occasion', 'colour_primary')"):
        tags.setdefault(pid, set()).add((f, v))
    for p in r:
        assert ("occasion", "evening") in tags[p["id"]] and ("colour_primary", "black") in tags[p["id"]]
    facets = {f["id"]: f for f in client.get("/store/facets?dept=women").json()["facets"]}
    assert set(facets) == {"category", "colour_primary", "occasion", "formality", "season", "aesthetic",
                           "sleeve_length", "length"}
    assert all(v["count"] > 0 for f in facets.values() for v in f["values"])


def test_a_product_page_has_tags_images_and_no_storage_paths(client):
    pid = client.get("/store/products?dept=women").json()["products"][0]["id"]
    d = client.get(f"/store/products/{pid}").json()
    assert d["tags"] and d["name"] and d["price"] > 0
    assert all(u.startswith("http") and "token=" in u for u in d["images"])


def test_sessions_belong_to_a_persona_and_sara_starts_high_intent_signals(client):
    from richies import server
    s1 = start(client, "james")
    assert client.get("/store/personas").json()["current"] == "james"
    s2 = start(client, "james")                      # reset: a new session id
    assert s1 != s2
    s3 = start(client, "sara")
    types = {r[0] for r in server.q("select type from store_events where session_id = %s", (s3,))}
    assert {"search_referral", "size_select"} <= types
    assert client.post("/store/session", json={"persona": "nobody"}).status_code == 422


def test_events_are_recorded_against_the_session(client):
    from richies import server
    s = start(client, "layla")
    pid = client.get("/store/products?dept=women").json()["products"][0]["id"]
    assert client.post("/store/events", json={"type": "view", "productId": pid, "context": {"surface": "grid"}}).status_code == 200
    assert client.post("/store/events", json={"type": "nonsense"}).status_code == 422
    rows = server.q("""select type, product_id, context->>'surface' from store_events where session_id = %s
                         and context->>'sessionStart' is null""", (s,))      # the simulated arrival is left out
    assert rows == [("view", pid, "grid")]
    ev = client.get("/store/state").json()["events"]
    assert ev[0]["type"] == "view"


def _pid_with_role(role, audience="Women"):
    from richies import server
    r = server.q("""select p.product_id from v_catalog_products p join store_outfit_roles o on o.category_node_id = p.category_id
                     where o.role = %s and p.audience = %s order by p.product_id limit 1""", (role, audience))
    return r[0][0] if r else None


def test_the_look_endpoint_follows_the_role_and_carries_the_occasion(client):
    none = _pid_with_role("none")
    if none:
        j = client.get(f"/store/products/{none}/look").json()
        assert j["filled"] == 0 and j["title"] is None and j["slots"] == []
    dress = _pid_with_role("one_piece")
    j = client.get(f"/store/products/{dress}/look").json()
    assert j["filled"] == len(j["slots"]) and {s["slot"] for s in j["slots"]} <= {"shoes", "bag", "jewellery"}
    assert all(i["id"] != dress for s in j["slots"] for i in s["items"])
    assert j["title"] == ("Complete the look" if j["filled"] > 1 else "Pairs well with" if j["filled"] else None)
    e = client.get(f"/store/products/{dress}/look?occasion=evening").json()
    assert e["occasion"] == "evening" and e["occasionLabel"]
    assert client.get(f"/store/products/{dress}/look?occasion=not-an-occasion").json()["occasion"] is None
    assert client.get("/store/products/no_such_product/look").status_code == 404


def test_occasions_shortlist_and_looks_for_you(client):
    from richies import server
    start(client, "layla")
    client.get("/store/state?load=1")
    occ = client.get("/store/occasions?dept=women").json()["occasions"]
    assert occ and all(o["value"] for o in occ)
    o = occ[0]["value"]
    sl = client.post("/store/shortlist", json={"occasion": o, "dept": "women"}).json()
    assert 0 < sl["count"] <= 8 and all(1 <= len(i["reasons"]) <= 3 for i in sl["items"])
    brands = [server.q("select brand from catalog_products where product_id = %s", (i["id"],))[0][0] for i in sl["items"]]
    assert max(brands.count(b) for b in brands) <= 2
    looks = client.get("/store/looks-for-you?dept=women").json()["looks"]
    assert 0 < len(looks) <= 6 and all(l["title"] and l["hero"]["id"] for l in looks)
    layla = [i["id"] for i in sl["items"]]
    start(client, "james")
    client.get("/store/state?load=1")
    james = [i["id"] for i in client.post("/store/shortlist", json={"occasion": o, "dept": "women"}).json()["items"]]
    assert layla != james                              # the personalisation demo: same occasion, different result
    assert client.get("/store/looks-for-you?dept=women").json()["looks"] == []     # no profile yet
    assert client.post("/store/shortlist", json={"occasion": "nope"}).status_code == 422


def test_a_size_selection_moves_the_next_page_to_high_intent_not_the_current_one(client):
    start(client, "layla")
    assert client.get("/store/state?load=1").json()["intent"]["state"] == "low"
    pid = client.get("/store/products?dept=women").json()["products"][0]["id"]
    client.post("/store/events", json={"type": "size_select", "productId": pid, "context": {"size": "M"}})
    st = client.get("/store/state").json()["intent"]
    assert st["state"] == "low" and st["pending"] == "high"            # this page stays as it was
    assert client.get("/store/state?load=1").json()["intent"]["state"] == "high"
    assert client.get("/store/products?dept=women").json()["order"] == "new"
    start(client, "layla")
    client.get("/store/state?load=1")
    client.post("/store/events", json={"type": "refine_apply", "context": {"refine": "budget"}})
    assert client.get("/store/state?load=1").json()["intent"]["state"] == "low"


# ------------------------------------------------------------------ engineering PRD V5 (14 Nov)

def test_personas_show_signals_and_computed_intent_and_lead_and_no_age(client):
    import re
    ps = {p["id"]: p for p in client.get("/store/personas").json()["personas"]}
    assert [ps[k]["startingIntent"] for k in ("layla", "noor", "james", "sara")] == ["low", "low", "low", "high"]
    assert [ps[k]["startingLead"] for k in ("layla", "noor", "james", "sara")] == \
        ["looks", "occasion_picker", "occasion_picker", "none"]
    assert ps["layla"]["does"] == "Leads with Looks for you"
    assert ps["noor"]["does"] == "Leads with the occasion picker, evening suggested"
    assert ps["sara"]["does"] == "Feature stays out of the way"
    for p in ps.values():
        assert 3 <= len(p["signals"]) <= 4 and "description" not in p
        assert not any(re.search(r"\b\d{2}\b", t) for t in [p["fullName"], *p["signals"]])


def test_lead_module_per_persona_and_james_moves_to_looks_on_the_next_page(client):
    def lead(persona, **q):
        start(client, persona)
        return client.get("/store/state?load=1").json()
    layla = lead("layla")["lead"]
    assert layla["module"] == "looks" and not layla["occasionToggle"]     # README V7: the looks row alone
    noor = lead("noor")
    assert noor["lead"]["module"] == "occasion_picker" and noor["lead"]["suggestedOccasion"] == "evening"
    assert client.get("/store/occasions?dept=women").json()["occasions"][0]["value"] == "evening"
    assert lead("sara")["lead"]["module"] == "none"
    assert lead("sara")["looks"]["state"] == "never"
    james = lead("james")
    assert james["lead"] == {**james["lead"], "module": "occasion_picker", "showOpener": True}
    assert james["looks"]["state"] == "not_yet"
    assert client.get("/store/looks-for-you?dept=women").json() | {"looks": []} == \
        {"eligible": False, "code": "not_enough_signals", "reason": james["looks"]["reason"], "looks": []}
    ids = [p["id"] for p in client.get("/store/products?dept=women").json()["products"][:3]]
    for pid in ids:
        client.post("/store/events", json={"type": "view", "productId": pid})
    same_page = client.get("/store/state").json()
    assert same_page["looks"]["state"] == "not_yet" and same_page["looks"]["pending"]
    assert same_page["lead"]["module"] == "occasion_picker"             # the current page does not change
    nxt = client.get("/store/state?load=1&audience=her").json()
    assert nxt["looks"]["state"] == "eligible" and nxt["lead"]["module"] == "looks"
    assert nxt["lead"]["occasionToggle"]                 # taste built this session: the one-line toggle stays
    assert nxt["looks"]["since"].startswith("Became eligible")
    assert any(e["type"] == "looks_eligible" for e in nxt["events"])


def test_looks_for_you_heroes_never_repeat_and_exclude_the_product_on_the_page(client):
    start(client, "layla")
    client.get("/store/state?load=1")
    looks = client.get("/store/looks-for-you?dept=women&count=6").json()["looks"]
    heroes = [l["hero"]["id"] for l in looks]
    assert len(heroes) == len(set(heroes)) and len(heroes) > 3          # top band 1-3, grid 4-6, no repeats
    pid = heroes[0]
    more = client.get(f"/store/looks-for-you?dept=women&count=3&exclude_product_id={pid}").json()["looks"]
    assert more and all(pid != l["hero"]["id"] and pid not in [t["id"] for t in l["thumbs"]] for l in more)


def test_noor_gets_looks_built_for_the_occasion_she_arrived_with(client):
    start(client, "noor")
    client.get("/store/state?load=1")
    r = client.get("/store/looks-for-you?dept=women").json()
    assert r["eligible"] and r["code"] == "occasion_in_play"
    assert r["looks"] and all(l["occasion"] == "evening" for l in r["looks"])


def test_look_card_events_are_recorded_with_their_placement(client):
    from richies import server
    sid = start(client, "layla")
    for t in ("look_card_view", "look_card_tap"):
        assert client.post("/store/events", json={"type": t, "context": {"placement": "grid"}}).status_code == 200
    rows = server.q("select type, context->>'placement' from store_events where session_id = %s and type like 'look_card%%'",
                    (sid,))
    assert sorted(rows) == [("look_card_tap", "grid"), ("look_card_view", "grid")]


# ------------------------------------------------------------------ engineering PRD V7: product-page occasion row

def _two_occasion_dress():
    from richies import server
    return server.q("""select p.product_id from v_catalog_products p
                        join store_outfit_roles o on o.category_node_id = p.category_id
                        join v_product_facets f on f.product_id = p.product_id and f.facet_id = 'occasion'
                       where o.role = 'one_piece' and p.audience = 'Women'
                       group by p.product_id having count(*) >= 2 and bool_or(f.value = 'evening')
                       order by p.product_id limit 1""")[0][0]


def test_occasion_options_are_the_products_own_and_each_fills_a_slot(client):
    from richies import server
    pid = _two_occasion_dress()
    own = [r[0] for r in server.q("select value from v_product_facets where product_id = %s and facet_id = 'occasion'",
                                  (pid,))]
    start(client, "james")
    client.get("/store/state?load=1")
    j = client.get(f"/store/products/{pid}/look").json()
    opts = [o["value"] for o in j["occasionOptions"]]
    assert opts and set(opts) <= set(own) and len(opts) <= 4 and j["occasionInPlay"] is None
    for o in opts:                                     # an occasion that would fill no slot is never offered
        assert client.get(f"/store/products/{pid}/look?occasion={o}").json()["filled"] >= 1
    none = _pid_with_role("none")
    if none:
        assert client.get(f"/store/products/{none}/look").json()["occasionOptions"] == []


def test_noor_options_start_with_evening_and_sara_has_none(client):
    pid = _two_occasion_dress()
    start(client, "noor")
    client.get("/store/state?load=1")
    assert client.get(f"/store/products/{pid}/look").json()["occasionOptions"][0]["value"] == "evening"
    start(client, "sara")
    client.get("/store/state?load=1")
    assert client.get(f"/store/products/{pid}/look").json()["occasionOptions"] == []


def test_an_occasion_chosen_on_the_product_page_leads_the_next_listing_page(client):
    pid = _two_occasion_dress()
    start(client, "layla")
    client.get("/store/state?load=1")
    o = client.get(f"/store/products/{pid}/look").json()["occasionOptions"][-1]["value"]
    client.post("/store/events", json={"type": "occasion_select", "productId": pid,
                                       "context": {"occasion": o, "surface": "product_page"}})
    assert client.get("/store/state").json()["lead"]["module"] == "looks"      # this page does not change
    lead = client.get("/store/state?load=1").json()["lead"]
    assert lead["module"] == "occasion_picker" and lead["suggestedOccasion"] == o and lead["occasionSource"] == "chosen"
    r = client.get("/store/looks-for-you?dept=women").json()
    assert r["code"] == "occasion_in_play" and all(l["occasion"] == o for l in r["looks"])
