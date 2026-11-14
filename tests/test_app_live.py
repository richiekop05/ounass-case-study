"""Live tag (Build PRD 7 and 9): link detection, listing links, refusals before any network call.

The detection tests use real links from the database; no request is made to the Ounass site.
"""
from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv

from scraper.live import classify, listing_links
from scraper.listing import parse_listing
from scraper.store import normalize_db_url

load_dotenv()
FIXTURES = Path(__file__).parent / "fixtures"


class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeClient:
    def __init__(self, html):
        self.html, self.calls = html, []

    def get(self, url, **kw):
        self.calls.append(url)
        return FakeResponse(self.html)


@pytest.fixture(scope="module")
def real_links():
    url = os.environ.get("SUPABASE_DB_URL", "")
    if not url:
        pytest.skip("SUPABASE_DB_URL not set")
    with psycopg.connect(normalize_db_url(url)) as c:
        prods = [r[0] for r in c.execute("select url from products order by product_id limit 10")]
        lists = [r[0] for r in c.execute("""select distinct listing_url from products where listing_url is not null
                                            order by 1 limit 10""")]
    return prods, lists


def test_ten_real_product_and_ten_real_listing_links_are_detected(real_links):
    prods, lists = real_links
    assert len(prods) == 10 and len(lists) == 10
    for u in prods:
        c = classify(u)
        assert c["kind"] == "product" and u.rstrip("/").endswith(c["productId"] + ".html")
    assert all(classify(u)["kind"] == "listing" for u in lists)


@pytest.mark.parametrize("url", ["https://example.com/shop-x-1_2.html", "https://www.ounass.ae/", "not a link",
                                 "https://www.ounass.ae/cart", "ftp://www.ounass.ae/women/clothing"])
def test_other_links_are_neither(url):
    assert classify(url)["kind"] == "neither"


def test_search_and_scheme_less_links():
    assert classify("https://www.ounass.ae/search?q=dress")["kind"] == "listing"
    assert classify("www.ounass.ae/women/clothing/dresses")["kind"] == "listing"


def test_listing_links_keep_page_order_without_duplicates_and_stop_at_x():
    html = (FIXTURES / "listing_women_dresses.html").read_text()
    page = parse_listing(html)
    client = FakeClient(html)
    items, found = listing_links(client, "https://www.ounass.ae/women/clothing/dresses", 3)
    assert len(client.calls) == 1 and len(items) == 3
    assert [it["position"] for it in items] == [1, 2, 3]
    ids = [it.style_color_id for it in page.items]
    assert [it["productId"] for it in items] == list(dict.fromkeys(ids))[:3]
    assert all(it["url"].startswith("https://www.ounass.ae/shop-") for it in items)
    all_items, found2 = listing_links(FakeClient(html), "https://www.ounass.ae/women/clothing/dresses", 999)
    assert found == found2 == len(all_items) == len(set(it["productId"] for it in all_items))


def test_non_ounass_and_mismatched_links_are_refused_before_any_request(monkeypatch):
    import app.live as live

    def no_network(*a, **k):
        raise AssertionError("no request may be made")
    monkeypatch.setattr(live, "_client", no_network)
    monkeypatch.setattr(live, "_store", no_network)
    for fn, args in ((live.start_product, ("https://example.com/x",)),
                     (live.start_product, ("https://www.ounass.ae/men/clothing/jeans",)),
                     (live.start_listing, ("https://www.ounass.ae/shop-a-b-1_2.html", 3)),
                     (live.start_listing, ("https://www.ounass.ae/men/clothing/jeans", 0)),
                     (live.start_listing, ("https://www.ounass.ae/men/clothing/jeans", 26))):
        with pytest.raises(live.LiveError):
            fn(*args)
