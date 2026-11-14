"""`scraper discover`: fetch robots.txt, one listing and one product page; write docs/discovery.md."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .client import SITE, Client
from .listing import parse_listing
from .product import parse_product
from .state import extract_initial_state

TEMPLATE = Path(__file__).with_name("discovery_template.md")


def _trim(obj, max_list=2, max_str=160):
    if isinstance(obj, dict):
        return {k: _trim(v, max_list, max_str) for k, v in obj.items()}
    if isinstance(obj, list):
        out = [_trim(v, max_list, max_str) for v in obj[:max_list]]
        if len(obj) > max_list:
            out.append(f"... {len(obj) - max_list} more")
        return out
    if isinstance(obj, str) and len(obj) > max_str:
        return obj[:max_str] + "..."
    return obj


def run_discover(client: Client, listing_path: str, out: Path) -> Path:
    robots = client.get(SITE + "/robots.txt", expect_page=False).text
    rules = client.robots_for(SITE)
    listing_url = SITE + listing_path
    listing_html = client.get(listing_url).text
    listing = parse_listing(listing_html)
    first = next(i for i in listing.items if not i.out_of_stock)
    product_html = client.get(first.url).text
    product = parse_product(product_html, url=first.url)
    pdp = extract_initial_state(product_html)["pdp"]
    plp = extract_initial_state(listing_html)["plp"]

    probes = {
        listing_url + "?fh_start_index=24": rules.allowed(listing_url + "?fh_start_index=24"),
        listing_url + "?ajax=true": rules.allowed(listing_url + "?ajax=true"),
        listing_url: rules.allowed(listing_url),
        first.url: rules.allowed(first.url),
    }
    evidence = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "user_agent": client.user_agent,
        "robots_txt": robots.strip(),
        "robots_probes": "\n".join(f"| `{u.replace(SITE, '')}` | {'allowed' if ok else 'disallowed'} |"
                                   for u, ok in probes.items()),
        "crawl_delay": rules.crawl_delay or "none for `User-agent: *`",
        "listing_url": listing_url,
        "listing_summary": f"{len(listing.items)} products on the page, {listing.total_items} in the category, "
                           f"breadcrumb {listing.breadcrumb}",
        "listing_pagination": json.dumps(plp.get("pagination"), indent=2, ensure_ascii=False),
        "listing_item": json.dumps(_trim(plp["styleColors"][0], 3), indent=2, ensure_ascii=False),
        "product_url": first.url,
        "tab_labels": "\n".join(f"| `{t.get('tabId')}` | {t.get('title')} | {len(t.get('html') or '')} chars |"
                                for t in pdp.get("contentTabs") or [])
                      + f"\n| `deliveryDetails` | {(pdp.get('deliveryDetails') or {}).get('title')} | structured |",
        "product_raw": json.dumps(_trim({k: pdp[k] for k in (
            "styleColorId", "parentSku", "name", "designerCategoryName", "price", "color", "gender",
            "breadcrumbs", "badge", "media", "sizes", "contentTabs") if k in pdp}, 3, 300),
            indent=2, ensure_ascii=False),
        "product_parsed": json.dumps(_trim({k: v for k, v in product.items() if k != "raw"}, 4, 300),
                                     indent=2, ensure_ascii=False),
    }
    text = TEMPLATE.read_text(encoding="utf-8")
    for k, v in evidence.items():
        text = text.replace("{{" + k + "}}", str(v))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return out
