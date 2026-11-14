"""Product page -> product fields, including every content tab.

The product page embeds `initialState.pdp`. All tabs are present in that JSON
(`contentTabs` as HTML fragments, plus `deliveryDetails`), not only the open one.
Wording is copied as shown. The only change is dropping the leading bullet glyph
("• ") that the site uses to draw list items.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from selectolax.lexbor import LexborHTMLParser

from .client import SITE
from .state import canonical_url, extract_initial_state

IMAGE_WIDTH = 1000
MAX_IMAGES = 4

_BULLET = re.compile(r"^[•·●–\-\*]\s*")
_BLOCKS = "p, li, h1, h2, h3, h4, h5, h6"
_ATTR = re.compile(r"^\s*([^:]{1,40}?)\s*:\s*(\S.*)$")


# ---------------------------------------------------------------- HTML fragments


def fragment_blocks(html: str) -> list[str]:
    """Split an HTML fragment into its block texts (paragraphs / list items), in order.
    `<br>` inside a block becomes a line break inside that block."""
    if not html or not html.strip():
        return []
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    tree = LexborHTMLParser(f"<div id='__root'>{html}</div>")
    root = tree.css_first("#__root")
    nodes = root.css(_BLOCKS)
    block_tags = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6"}
    out: list[str] = []
    if nodes:
        for n in nodes:
            parent, nested = n.parent, False
            while parent is not None and parent is not root:
                if parent.tag in block_tags:
                    nested = True
                    break
                parent = parent.parent
            if nested:
                continue
            text = n.text(deep=True).strip()
            if text:
                out.append(text)
        # Text sitting outside any block element (rare) is kept too.
        loose = _loose_text(root, block_tags)
        if loose:
            out.extend(loose)
    else:
        out = [t.strip() for t in root.text(deep=True).split("\n") if t.strip()]
    return out


def _loose_text(root, block_tags: set[str]) -> list[str]:
    texts = []
    child = root.child
    while child is not None:
        if child.tag == "-text":
            t = (child.text_content or "").strip()
            if t:
                texts.append(t)
        child = child.next
    return texts


def fragment_lines(html: str) -> list[str]:
    """One entry per visible line, bullets dropped. Used for list-style tabs."""
    lines = []
    for block in fragment_blocks(html):
        for line in block.split("\n"):
            line = _BULLET.sub("", line.strip()).strip()
            if line:
                lines.append(line)
    return lines


def lines_to_attributes(lines: list[str]) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for line in lines:
        m = _ATTR.match(line)
        if m and not m.group(2).startswith("//"):  # "See https://..." is not a label
            label, value = m.group(1).strip(), m.group(2).strip()
            attrs.setdefault(label, value)
    return attrs


# ---------------------------------------------------------------- tabs


def _tab_kind(tab: dict) -> str | None:
    tab_id = (tab.get("tabId") or "").lower()
    title = (tab.get("title") or "").lower()
    if tab_id == "description" or "editor" in title:
        return "description"
    # Beauty pages have "Key Details" in place of "Design details": same role, product attributes.
    if tab_id in ("designdetails", "keydetails") or "design detail" in title or title in ("details", "key details"):
        return "details"
    if tab_id == "sizeandfit" or "size & fit" in title or "size and fit" in title:
        return "size_and_fit"
    return None


def _delivery_tab(dd: dict) -> dict | None:
    if not dd or not isinstance(dd, dict):
        return None
    lines: list[str] = []
    for region in dd.get("tabs") or []:
        lines.append(region.get("title") or "")
        delivery = region.get("delivery") or {}
        if delivery.get("title"):
            lines.append(delivery["title"])
        for ship in delivery.get("shipments") or []:
            if ship.get("sourceDescription"):
                lines.append(ship["sourceDescription"])
            for m in ship.get("methods") or []:
                lines.append(f"{m.get('title', '')}: {m.get('description', '')}".strip(": "))
        lines.extend(delivery.get("footnotes") or [])
        ret = region.get("returns") or {}
        for k in ("title", "description"):
            if ret.get(k):
                lines.append(ret[k])
    lines = [line for line in lines if line]
    return {"label": dd.get("title") or "Delivery", "text": "\n".join(lines), "items": lines}


# ---------------------------------------------------------------- product


def _audience(pdp: dict, breadcrumb: list[str]) -> str | None:
    probe = " ".join([str(pdp.get("gender") or ""), breadcrumb[0] if breadcrumb else ""]).lower()
    if "unisex" in probe:
        return "unisex"
    if "women" in probe or "womens" in probe:
        return "women"
    if re.search(r"\bmens?\b", probe) or "men" == probe.strip():
        return "men"
    if any(k in probe for k in ("kid", "girl", "boy", "baby")):
        return "kids"
    return None


def _image_urls(state: dict, pdp: dict) -> list[str]:
    host = "ounass-ae.atgcdn.ae"
    base_hint = (state.get("cdnBaseUrlPathByKey") or {}).get("productZoom") or ""
    m = re.match(r"^(?:https?:)?//([^/]+)/", base_hint)
    if m:
        host = m.group(1)
    urls = []
    for item in pdp.get("media") or []:
        src = (item or {}).get("src") or ""
        if not src or src.lower().endswith((".mp4", ".mov", ".webm")):
            continue
        path = src.split("?", 1)[0]
        if not path.startswith("/"):
            path = "/" + path
        urls.append(f"https://{host}/small_light(dw={IMAGE_WIDTH},of=jpg)/pub/media/catalog/product{path}")
    if not urls:  # fall back to the gallery's own largest URLs
        for img in pdp.get("images") or []:
            u = (img or {}).get("twoX") or (img or {}).get("oneX")
            if u:
                urls.append("https:" + u if u.startswith("//") else u)
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def parse_product(html: str, url: str | None = None, scraped_at: datetime | None = None) -> dict:
    """Return product fields (no spine fields, no storage paths) plus `raw`.

    Works the same for a fetched page and for a page saved by hand from a browser."""
    state = extract_initial_state(html)
    pdp = state.get("pdp")
    if not isinstance(pdp, dict) or not pdp:
        raise ValueError("page has no product data (initialState.pdp missing)")

    crumbs_src = pdp.get("breadcrumbs") or state.get("breadcrumbs") or []
    product_id = str(pdp.get("styleColorId") or "")
    breadcrumb = [
        b.get("name", "") for b in crumbs_src
        if str(b.get("categoryId")) != product_id and not str(b.get("urlPath", "")).endswith(".html")
    ]

    canonical = canonical_url(html)
    if not canonical:
        path = pdp.get("slug") and f"/{pdp['slug']}.html"
        canonical = url or (SITE + path if path else "")

    description_paras: list[str] = []
    details: list[str] = []
    size_and_fit: list[str] = []
    content_tabs: list[dict] = []
    for tab in pdp.get("contentTabs") or []:
        html_frag = tab.get("html") or ""
        kind = _tab_kind(tab)
        blocks = fragment_blocks(html_frag)
        items = fragment_lines(html_frag)
        if kind == "description":
            description_paras = blocks
            text = "\n\n".join(blocks)
        else:
            text = "\n".join(items)
        content_tabs.append({"label": tab.get("title") or tab.get("tabId") or "", "text": text, "items": items})
        if kind == "details":
            details = items
        elif kind == "size_and_fit":
            size_and_fit = items
    delivery = _delivery_tab(pdp.get("deliveryDetails"))
    if delivery:
        content_tabs.append(delivery)

    description = "\n\n".join(description_paras)
    if not description and pdp.get("descriptionText"):
        description = str(pdp["descriptionText"]).strip()

    sizes = []
    for s in pdp.get("sizes") or []:
        label = s.get("sizeCode") or s.get("label") or ""
        stock = s.get("stock")
        in_stock = (stock is None or stock > 0) and not s.get("disabled", False)
        sizes.append({"label": str(label), "in_stock": bool(in_stock)})
    in_stock = any(s["in_stock"] for s in sizes) if sizes else not pdp.get("outOfStock", False)

    price = pdp.get("price")
    slashed = pdp.get("slashedPrice") or pdp.get("oldPrice")
    original_price = slashed if (slashed and price and slashed > price) else None

    badge = pdp.get("badge")
    badges = [badge["value"]] if isinstance(badge, dict) and badge.get("value") else []

    images = [{"source_url": u, "position": i + 1} for i, u in enumerate(_image_urls(state, pdp)[:MAX_IMAGES])]

    return {
        "product_id": product_id,
        "url": canonical,
        "name": pdp.get("name") or "",
        "brand": pdp.get("designerCategoryName") or "",
        "price": price,
        "original_price": original_price,
        "currency": state.get("currency") or "",
        "site_breadcrumb": breadcrumb,
        "audience": _audience(pdp, breadcrumb),
        "description": description,
        "details": details,
        "attributes": lines_to_attributes(details),
        "content_tabs": content_tabs,
        "size_and_fit": size_and_fit,
        "colour": pdp.get("color") or pdp.get("colorInEnglish") or None,
        "sizes": sizes,
        "in_stock": in_stock,
        "badges": badges,
        "images": images,
        "scraped_at": (scraped_at or datetime.now(timezone.utc)).isoformat(),
        "source": "embedded",
        "parent_sku": str(pdp.get("parentSku") or ""),
        "raw": {"pdp": pdp, "breadcrumbs": state.get("breadcrumbs"), "currency": state.get("currency"),
                "canonical": canonical_url(html)},
    }


def parse_product_file(path: Path) -> dict:
    """Fallback path from the PRD: parse a product page saved by hand in a browser."""
    return parse_product(path.read_text(encoding="utf-8", errors="replace"))
