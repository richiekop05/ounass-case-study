"""On-demand scraping for the review app's Live tag (Build PRD 7).

Same rules as a normal scrape: requests go through the scraper's Client (robots.txt, polite delay,
retries, disk cache); a product is parsed, validated and stored exactly as `run_scrape` stores it.
Nothing from a listing tile is kept except the link and its position.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from pydantic import ValidationError

from .client import Client, FetchError
from .listing import parse_listing
from .models import Product
from .product import parse_product
from .scrape import _store_images

HOST_RE = re.compile(r"^(www\.)?ounass\.(ae|com|sa|kw|qa|bh|om)$", re.I)
PRODUCT_RE = re.compile(r"^/shop-[a-z0-9-]+-(\d+_\d+)\.html$", re.I)
NOT_LISTING = re.compile(r"^/(cart|checkout|account|customer|wishlist|login|register|help|faq|stores?)(/|$)", re.I)


def classify(url: str) -> dict:
    """{'kind': 'product'|'listing'|'neither', 'productId'?}. Decided from the URL alone, no request."""
    u = (url or "").strip()
    if u and "://" not in u:
        u = "https://" + u                      # a pasted link without the scheme
    try:
        parts = urlsplit(u)
    except ValueError:
        return {"kind": "neither"}
    if parts.scheme not in ("http", "https") or not HOST_RE.match(parts.netloc or ""):
        return {"kind": "neither"}
    path = re.sub(r"/+$", "", parts.path or "")
    m = PRODUCT_RE.match(path)
    if m:
        return {"kind": "product", "productId": m.group(1)}
    if path.endswith(".html") or NOT_LISTING.match(path):
        return {"kind": "neither"}
    if path and path != "/":
        return {"kind": "listing"}
    if "q=" in (parts.query or ""):              # a search results page
        return {"kind": "listing"}
    return {"kind": "neither"}


def listing_links(client: Client, url: str, limit: int) -> tuple[list[dict], int]:
    """The first `limit` product links on a listing page as first loaded, in page order, without duplicates.
    Returns (items, number of products the page showed). Pagination is never followed."""
    try:
        page = parse_listing(client.get(url).text)
    except ValueError as e:
        raise ValueError("The listing page could not be read: its products are loaded by scripts the "
                         f"scraper cannot see ({e}).") from e
    seen, items = set(), []
    for it in page.items:
        path = urlsplit(it.url).path if it.url.startswith("http") else it.url
        m = PRODUCT_RE.match(re.sub(r"/+$", "", path))
        pid = m.group(1) if m else None
        if not pid or pid in seen:
            continue
        seen.add(pid)
        full = it.url if it.url.startswith("http") else f"https://{urlsplit(url).netloc}{it.url}"
        items.append({"position": len(items) + 1, "productId": pid, "url": full})
    if not items:
        raise ValueError("No product links were found on that listing page as first loaded.")
    return items[:limit], len(items)


def scrape_one(client: Client, sink, url: str, area: str = "live tag") -> dict:
    """Fetch, parse, store the photos and save one product page, as run_scrape does for each product."""
    try:
        resp = client.get(url)
    except FetchError as e:
        raise RuntimeError(f"The page could not be fetched: {e}") from e
    try:
        parsed = parse_product(resp.text, url=url)
    except ValueError as e:
        raise RuntimeError(f"The page is not a product page the scraper can read: {e}") from e
    parsed["spine_node_id"] = None            # no plan row: the categoriser places it from the breadcrumb
    parsed["spine_path"] = None
    parsed["placement_level"] = None
    parsed["needs_placement"] = True
    parsed["sample_area"] = area
    parsed["listing_url"] = None
    _store_images(client, sink, parsed, no_images=False)
    raw = parsed.pop("raw")
    try:
        Product.model_validate(parsed)
    except ValidationError as e:
        raise RuntimeError(f"The product page is missing required fields: {e.errors()[0].get('msg')}") from e
    parsed["raw"] = raw
    sink.upsert_product(parsed)
    return {k: v for k, v in parsed.items() if k != "raw"}
