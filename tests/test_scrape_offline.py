"""The whole scrape loop against a fake site built from the fixtures. No network, no database."""
import json
import re

import httpx

from scraper.client import Client
from scraper.models import Product
from scraper.sampling import PlanRow
from scraper.scrape import DryRunSink, run_scrape

UA = "OunassCaseStudyScraper/0.1 (test; contact: test@test.invalid)"
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100


def fake_site(fixture_text):
    listing = fixture_text("listing_women_dresses.html")
    product = fixture_text("product_zimmermann_dress.html")
    calls = []

    def route(req: httpx.Request):
        calls.append(str(req.url))
        if req.url.path == "/robots.txt":
            if req.url.host == "www.ounass.ae":
                return httpx.Response(200, text=fixture_text("robots.txt"))
            return httpx.Response(404)
        if req.url.host.endswith("atgcdn.ae"):
            return httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})
        if req.url.path.endswith(".html"):
            # Give each product page its own id, taken from the URL, so picks are distinct.
            sid = re.search(r"-(\d+_\d+)\.html$", req.url.path).group(1)
            style, colour = sid.split("_")
            page = product.replace("220163574_195", sid).replace("220163574", style)
            return httpx.Response(200, text=page, headers={"content-type": "text/html"})
        return httpx.Response(200, text=listing, headers={"content-type": "text/html"})
    return route, calls


CATS = {"W101002": {"node_id": "W101002", "parent_id": "W101", "level": "L3", "name": "Dresses",
                    "slug": "dresses", "path": "Women / Clothing / Dresses", "is_leaf": True,
                    "review_flag": None, "department": "Clothing"}}
PLAN = [
    PlanRow("W101002", "Women / Clothing / Dresses", "https://www.ounass.ae/women/clothing/dresses", 3,
            "Women: clothing", "L3"),
    PlanRow("", "", "https://www.ounass.ae/women/clothing/beachwear", 3, "Women: clothing", ""),
]


def _client(tmp_path, route):
    t = {"now": 0.0}

    def sleep(s):
        t["now"] += s
    return Client(UA, cache_dir=tmp_path / "cache", transport=httpx.MockTransport(route),
                  clock=lambda: t["now"], sleep=sleep)


def test_scrape_end_to_end_and_second_run_makes_no_requests(tmp_path, fixture_text):
    route, calls = fake_site(fixture_text)
    sink = DryRunSink(tmp_path / "out.jsonl")
    state = run_scrape(_client(tmp_path, route), sink, PLAN, CATS, log=lambda *_: None)

    assert len(state.accepted) == 6 and state.rejects == 0
    rows = [json.loads(line) for line in (tmp_path / "out.jsonl").read_text().splitlines()]
    assert len({r["product_id"] for r in rows}) == 6
    for r in rows:
        Product.model_validate(r)
        assert r["images"] and all(i["storage_path"] for i in r["images"])
    on_node = [r for r in rows if r["spine_node_id"] == "W101002"]
    assert len(on_node) == 3 and not any(r["needs_placement"] for r in on_node)
    placement = [r for r in rows if not r["spine_node_id"]]
    assert len(placement) == 3 and all(r["needs_placement"] for r in placement)
    assert len({r["parent_sku"] for r in rows}) == 6      # one colour per style
    assert not any("?" in u for u in calls if "ounass.ae/" in u and "atgcdn" not in u)

    # Resume / second run: same plan, same cache, zero network requests.
    calls.clear()
    c2 = _client(tmp_path, route)
    state2 = run_scrape(c2, DryRunSink(tmp_path / "out2.jsonl"), PLAN, CATS, log=lambda *_: None)
    assert calls == [] and c2.stats.requests == 0
    assert [p["product_id"] for p in state2.accepted] == [p["product_id"] for p in state.accepted]


def test_limit_stops_early(tmp_path, fixture_text):
    route, _ = fake_site(fixture_text)
    state = run_scrape(_client(tmp_path, route), DryRunSink(tmp_path / "o.jsonl"), PLAN, CATS,
                       limit=2, no_images=True, log=lambda *_: None)
    assert len(state.accepted) == 2
