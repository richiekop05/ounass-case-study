"""All database and storage writes. Postgres via psycopg; images via the Storage REST API."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit

import httpx
import psycopg
from psycopg.types.json import Jsonb

from .sampling import Node

BUCKET = "product-images"
MIGRATIONS = Path(__file__).resolve().parents[2] / "db" / "migrations"

PRODUCT_COLUMNS = [
    "product_id", "url", "name", "brand", "price", "original_price", "currency", "site_breadcrumb",
    "spine_node_id", "spine_path", "placement_level", "needs_placement", "audience", "description",
    "details", "attributes", "content_tabs", "size_and_fit", "colour", "sizes", "in_stock", "badges",
    "scraped_at", "source", "parent_sku", "sample_area", "listing_url", "raw",
]
JSON_COLUMNS = {"site_breadcrumb", "details", "attributes", "content_tabs", "size_and_fit", "sizes",
                "badges", "raw"}


@dataclass
class Settings:
    supabase_url: str
    service_key: str
    db_url: str


def normalize_db_url(url: str) -> str:
    """Percent-encode the password so characters like @ : / # [ ] in it do not break the URL.
    Splits on the LAST '@' (hosts never contain one); already-encoded passwords stay as they are."""
    url = url.strip()
    scheme, sep, rest = url.partition("://")
    if not sep or "@" not in rest:
        return url
    userinfo, host = rest.rsplit("@", 1)
    user, colon, password = userinfo.partition(":")
    if not colon:
        return url
    password = password.strip("[]") if password.startswith("[") and password.endswith("]") else password
    return f"{scheme}://{user}:{quote(unquote(password), safe='')}@{host}"


def apply_migrations(conn: psycopg.Connection) -> list[str]:
    """Apply each db/migrations/*.sql file once, in name order, recording it in schema_migrations.
    Each file is itself safe to rerun; the ledger stops an older file (002) from undoing a newer
    one (003) on a later run. Returns the files applied this time."""
    applied = []
    with conn.cursor() as cur:
        cur.execute("""create table if not exists schema_migrations (
                           filename text primary key, applied_at timestamptz not null default now())""")
        cur.execute("alter table schema_migrations enable row level security")
        cur.execute("select filename from schema_migrations")
        done = {r[0] for r in cur.fetchall()}
        for f in sorted(MIGRATIONS.glob("*.sql")):
            if f.name in done:
                continue
            cur.execute(f.read_text())
            cur.execute("insert into schema_migrations (filename) values (%s)", (f.name,))
            applied.append(f.name)
    conn.commit()
    return applied


class Store:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.conn = psycopg.connect(normalize_db_url(settings.db_url), autocommit=False)
        # Accept the URL with or without a path (people often paste .../rest/v1/).
        parts = urlsplit(settings.supabase_url.strip())
        self.origin = f"{parts.scheme}://{parts.netloc}"
        key = settings.service_key.strip()
        headers = {"apikey": key}
        if not key.startswith("sb_"):  # legacy JWT keys also go in Authorization
            headers["Authorization"] = f"Bearer {key}"
        self._http = httpx.Client(base_url=self.origin + "/storage/v1", headers=headers, timeout=60.0)

    def close(self) -> None:
        self.conn.close()
        self._http.close()

    # ------------------------------------------------------------- schema

    def migrate(self) -> list[str]:
        return apply_migrations(self.conn)

    def load_categories(self, nodes: list[Node]) -> int:
        """Make `categories` match the spine file exactly (upsert, then drop rows not in the file)."""
        ids = [n.node_id for n in nodes]
        with self.conn.cursor() as cur:
            cur.execute("set constraints all deferred")
            cur.executemany(
                """insert into categories (node_id, parent_id, level, audience, department, name, slug, path,
                                           is_leaf, definition, aliases, review_flag)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   on conflict (node_id) do update set
                     parent_id=excluded.parent_id, level=excluded.level, audience=excluded.audience,
                     department=excluded.department, name=excluded.name, slug=excluded.slug,
                     path=excluded.path, is_leaf=excluded.is_leaf, definition=excluded.definition,
                     aliases=excluded.aliases, review_flag=excluded.review_flag""",
                [(n.node_id, n.parent_id, n.level, n.audience, n.department, n.name, n.slug, n.path,
                  n.is_leaf, n.definition or None, Jsonb(n.aliases), n.review_flag or None) for n in nodes],
            )
            cur.execute("delete from categories where not (node_id = any(%s))", (ids,))
            cur.execute("select count(*) from categories")
            count = cur.fetchone()[0]
        self.conn.commit()
        return count

    def categories(self) -> dict[str, dict]:
        with self.conn.cursor() as cur:
            cur.execute("select node_id, parent_id, level, name, slug, path, is_leaf, review_flag, department "
                        "from categories")
            cols = [d.name for d in cur.description]
            return {r[0]: dict(zip(cols, r)) for r in cur.fetchall()}

    # ------------------------------------------------------------- products

    def upsert_product(self, row: dict[str, Any]) -> None:
        values = [Jsonb(row.get(c)) if c in JSON_COLUMNS else row.get(c) for c in PRODUCT_COLUMNS]
        cols = ", ".join(PRODUCT_COLUMNS)
        sets = ", ".join(f"{c}=excluded.{c}" for c in PRODUCT_COLUMNS if c != "product_id")
        with self.conn.cursor() as cur:
            cur.execute(
                f"insert into products ({cols}) values ({', '.join(['%s'] * len(values))}) "
                f"on conflict (product_id) do update set {sets}",
                values,
            )
            cur.execute("delete from product_images where product_id=%s", (row["product_id"],))
            cur.executemany(
                "insert into product_images (product_id, position, source_url, storage_path) values (%s,%s,%s,%s)",
                [(row["product_id"], im["position"], im["source_url"], im.get("storage_path"))
                 for im in row["images"]],
            )
        self.conn.commit()

    def stored_images(self, product_id: str) -> dict[int, tuple[str, str | None]]:
        with self.conn.cursor() as cur:
            cur.execute("select position, source_url, storage_path from product_images where product_id=%s",
                        (product_id,))
            return {r[0]: (r[1], r[2]) for r in cur.fetchall()}

    def add_reject(self, run_id: int | None, product_id: str | None, url: str, reason: str,
                   payload: dict | None = None) -> None:
        with self.conn.cursor() as cur:
            cur.execute("insert into product_rejects (run_id, product_id, url, reason, payload) "
                        "values (%s,%s,%s,%s,%s)",
                        (run_id, product_id, url, reason[:2000], Jsonb(payload) if payload else None))
        self.conn.commit()

    def all_products(self, with_raw: bool = False) -> list[dict]:
        cols = [c for c in PRODUCT_COLUMNS if with_raw or c != "raw"]
        with self.conn.cursor() as cur:
            cur.execute(f"select {', '.join(cols)} from products order by product_id")
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            cur.execute("select product_id, position, source_url, storage_path from product_images "
                        "order by product_id, position")
            images: dict[str, list] = {}
            for pid, pos, src, sp in cur.fetchall():
                images.setdefault(pid, []).append({"source_url": src, "position": pos, "storage_path": sp})
        for r in rows:
            r["images"] = images.get(r["product_id"], [])
            for k in ("price", "original_price"):
                if r[k] is not None:
                    r[k] = float(r[k])
        return rows

    def products_with_type(self) -> list[tuple[str, str | None, str | None]]:
        with self.conn.cursor() as cur:
            cur.execute("select product_id, spine_node_id, product_type from products_with_type")
            return cur.fetchall()

    # ------------------------------------------------------------- runs

    def start_run(self, started_at: str, args: dict) -> int:
        with self.conn.cursor() as cur:
            cur.execute("insert into scrape_runs (started_at, status, args) values (%s,'running',%s) "
                        "returning run_id", (started_at, Jsonb(args)))
            run_id = cur.fetchone()[0]
        self.conn.commit()
        return run_id

    def finish_run(self, run_id: int, report: dict) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """update scrape_runs set finished_at=%s, status=%s, requests=%s, requests_by_host=%s,
                   cache_hits=%s, products_written=%s, rejects=%s, node_counts=%s, area_counts=%s,
                   field_fill_rates=%s, errors=%s, notes=%s where run_id=%s""",
                (report["finished_at"], report["status"], report["requests"], Jsonb(report["requests_by_host"]),
                 report["cache_hits"], report["products_written"], report["rejects"],
                 Jsonb(report["node_counts"]), Jsonb(report["area_counts"]), Jsonb(report["field_fill_rates"]),
                 Jsonb({"errors": report["errors"], "shortfall": report.get("shortfall_reasons", {})}),
                 report.get("notes"), run_id),
            )
        self.conn.commit()

    # ------------------------------------------------------------- storage

    def upload_image(self, path: str, data: bytes, content_type: str = "image/jpeg") -> None:
        r = self._http.post(f"/object/{BUCKET}/{path}", content=data,
                            headers={"Content-Type": content_type, "x-upsert": "true"})
        if r.status_code >= 300:
            raise RuntimeError(f"storage upload {path} failed: {r.status_code} {r.text[:200]}")

    def download_image(self, path: str) -> bytes:
        r = self._http.get(f"/object/authenticated/{BUCKET}/{path}")
        r.raise_for_status()
        return r.content

    def signed_url(self, path: str, expires_in: int = 3600) -> str:
        r = self._http.post(f"/object/sign/{BUCKET}/{path}", json={"expiresIn": expires_in})
        r.raise_for_status()
        return self.origin + "/storage/v1" + json.loads(r.text)["signedURL"]

    def delete_image(self, path: str) -> None:
        r = self._http.request("DELETE", f"/object/{BUCKET}", json={"prefixes": [path]})
        r.raise_for_status()
