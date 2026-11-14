"""Listing page -> product candidates.

Listing pages embed `initialState.plp` with the first 24 products ("styleColors"),
the breadcrumb and pagination. Further pages are only reachable with
`?fh_start_index=`, which robots.txt disallows, so one listing URL yields at most 24.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .client import SITE
from .state import extract_initial_state


@dataclass
class ListingItem:
    style_color_id: str
    parent_sku: str
    url: str
    name: str
    brand: str
    price: float | None
    out_of_stock: bool
    category_url: str
    colour: str | None
    badge: str | None


@dataclass
class ListingPage:
    url_path: str
    breadcrumb: list[str]
    total_items: int | None
    items: list[ListingItem] = field(default_factory=list)


def parse_listing(html: str) -> ListingPage:
    state = extract_initial_state(html)
    plp = state.get("plp") or {}
    items = []
    for sc in plp.get("styleColors") or []:
        url = sc.get("url") or ""
        if url and not url.startswith("http"):
            url = SITE + url
        badge = sc.get("badge") or {}
        items.append(ListingItem(
            style_color_id=str(sc.get("styleColorId") or ""),
            parent_sku=str(sc.get("parentSku") or ""),
            url=url,
            name=sc.get("name") or "",
            brand=sc.get("designerCategoryName") or "",
            price=sc.get("price"),
            out_of_stock=bool(sc.get("outOfStock")),
            category_url=sc.get("categoryUrl") or "",
            colour=sc.get("colorInEnglish"),
            badge=badge.get("value") if isinstance(badge, dict) else None,
        ))
    return ListingPage(
        url_path=state.get("currentPath") or "",
        breadcrumb=[b.get("name", "") for b in plp.get("breadcrumbs") or []],
        total_items=(plp.get("pagination") or {}).get("totalItems"),
        items=[i for i in items if i.style_color_id and i.url],
    )
