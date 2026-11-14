"""The scrape run: plan rows -> listing -> picked product pages -> validated rows."""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from .client import Client, FetchError
from .listing import parse_listing
from .models import Product
from .product import parse_product
from .report import area_counts, fill_rates, node_counts
from .sampling import PlanRow, Picker, interleave_by_area


class Sink(Protocol):
    """Where accepted products go: the database, or a local file for a dry run."""
    def stored_images(self, product_id: str) -> dict[int, tuple[str, str | None]]: ...
    def upload_image(self, path: str, data: bytes, content_type: str = ...) -> None: ...
    def upsert_product(self, row: dict) -> None: ...
    def add_reject(self, run_id, product_id, url, reason, payload=None) -> None: ...


class DryRunSink:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
        self.rejects: list[dict] = []

    def stored_images(self, product_id):
        return {}

    def upload_image(self, path, data, content_type="image/jpeg"):
        pass

    def upsert_product(self, row):
        slim = {k: v for k, v in row.items() if k != "raw"}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(slim, ensure_ascii=False, default=str) + "\n")

    def add_reject(self, run_id, product_id, url, reason, payload=None):
        self.rejects.append({"product_id": product_id, "url": url, "reason": reason})


@dataclass
class RunState:
    accepted: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    rejects: int = 0
    shortfall: dict[str, str] = field(default_factory=dict)


def _attach_spine(parsed: dict, row: PlanRow, cats: dict[str, dict]) -> dict:
    node = cats.get(row.spine_node_id) if row.spine_node_id else None
    parsed["spine_node_id"] = node["node_id"] if node else None
    parsed["spine_path"] = node["path"] if node else None
    parsed["placement_level"] = node["level"] if node else None
    # Never guess a deeper node: empty or non-leaf means the tagger must place it.
    parsed["needs_placement"] = node is None or not node["is_leaf"]
    parsed["sample_area"] = row.area
    parsed["listing_url"] = row.listing_url
    return parsed


def _store_images(client: Client, sink: Sink, parsed: dict, no_images: bool) -> None:
    pid = parsed["product_id"]
    if no_images:
        return
    already = sink.stored_images(pid)
    kept = []
    for im in parsed["images"]:
        path = f"{pid}/{im['position']}.jpg"
        prev = already.get(im["position"])
        if prev and prev[0] == im["source_url"] and prev[1]:
            im["storage_path"] = prev[1]  # stored by an earlier run: no request, no upload
            kept.append(im)
            continue
        try:
            resp = client.get(im["source_url"], expect_page=False)
        except FetchError:
            continue
        if not resp.content_type.startswith("image/"):
            continue
        sink.upload_image(path, resp.content, resp.content_type.split(";")[0])
        im["storage_path"] = path
        kept.append(im)
    parsed["images"] = kept


def run_scrape(client: Client, sink: Sink, plan: list[PlanRow], cats: dict[str, dict], *,
               run_id: int | None = None, limit: int | None = None, category: str | None = None,
               no_images: bool = False, log=print, state: RunState | None = None) -> RunState:
    """Pass `state` in to keep partial results when the run stops early (StopScrape)."""
    state = state if state is not None else RunState()
    picker = Picker()
    rows = interleave_by_area(plan)
    if category:
        rows = [r for r in rows if r.spine_node_id == category]
        if not rows:
            raise ValueError(f"no plan row for node {category}")

    for row in rows:
        if limit and len(state.accepted) >= limit:
            break
        try:
            listing = parse_listing(client.get(row.listing_url).text)
        except FetchError as e:
            state.errors.append(f"listing {e}")
            state.shortfall[row.listing_url] = f"listing failed: {e}"
            continue
        except ValueError as e:
            state.errors.append(f"listing parse {row.listing_url}: {e}")
            state.shortfall[row.listing_url] = "listing could not be parsed"
            continue

        taken, brands_here, why = 0, Counter(), Counter()
        for item in picker.candidates(listing.items):
            if taken >= row.quota or (limit and len(state.accepted) >= limit):
                break
            if brands_here[item.brand] >= picker.per_node_brand_cap:
                why["brand cap"] += 1
                continue
            try:
                resp = client.get(item.url)
            except FetchError as e:
                state.errors.append(f"product {e}")
                why["fetch failed"] += 1
                continue
            try:
                parsed = parse_product(resp.text, url=item.url)
            except ValueError as e:
                state.rejects += 1
                sink.add_reject(run_id, item.style_color_id, item.url, f"parse: {e}")
                why["parse failed"] += 1
                continue
            if not parsed["in_stock"]:
                why["out of stock on product page"] += 1
                continue
            if parsed["product_id"] in picker.ids_taken:  # page resolved to a product already taken
                picker.ids_taken.add(item.style_color_id)
                why["duplicate"] += 1
                continue
            _attach_spine(parsed, row, cats)
            _store_images(client, sink, parsed, no_images)
            raw = parsed.pop("raw")
            try:
                Product.model_validate(parsed)
            except ValidationError as e:
                state.rejects += 1
                sink.add_reject(run_id, parsed.get("product_id"), item.url, str(e),
                                {k: v for k, v in parsed.items() if k != "content_tabs"})
                why["failed validation"] += 1
                continue
            parsed["raw"] = raw
            sink.upsert_product(parsed)
            picker.accept(item)
            picker.ids_taken.add(parsed["product_id"])
            brands_here[item.brand] += 1
            taken += 1
            state.accepted.append({k: v for k, v in parsed.items() if k != "raw"})
            log(f"  [{len(state.accepted):>3}] {row.area:<26} {parsed['brand'][:22]:<22} {parsed['name'][:50]}")

        if taken < row.quota and not (limit and len(state.accepted) >= limit):
            detail = ", ".join(f"{k}: {v}" for k, v in why.items())
            state.shortfall[row.listing_url] = (f"only {len(listing.items)} products on the page "
                                                f"(pagination is robots-disallowed){'; ' + detail if detail else ''}")
    return state


def build_report(state: RunState, client: Client, plan: list[PlanRow], started_at: str, status: str,
                 notes: str | None = None) -> dict:
    return {
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "requests": client.stats.requests,
        "requests_by_host": client.stats.by_host,
        "cache_hits": client.stats.cache_hits,
        "products_written": len(state.accepted),
        "rejects": state.rejects,
        "node_counts": node_counts(state.accepted, plan),
        "area_counts": area_counts(state.accepted, plan),
        "field_fill_rates": fill_rates(state.accepted),
        "errors": state.errors,
        "shortfall_reasons": state.shortfall,
        "notes": notes,
    }
