"""Reading a scraped product: its labelled text, its images and its category candidates."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
import psycopg

from scraper.store import BUCKET

from .providers import Image

# content_tabs labels the scraper stores, grouped into the parts the skill files name.
EDITOR_TABS = ("Editor's advice",)
DETAIL_TABS = ("Design details", "Details and Care", "Key Details")
# Extra factual tabs sent with tag requests only: measurements and ingredients live here.
EXTRA_TABS = ("Size & Fit", "Dimensions", "Ingredients")
# The scraper's breadcrumb department words, mapped to spine departments (fallback only).
CRUMB_DEPARTMENTS = {"Jewellery & Watches": ("Jewellery", "Watches"), "Grooming": ("Beauty",)}


@dataclass
class Product:
    product_id: str
    name: str
    brand: str
    description: str
    details: list[str]
    tabs: dict[str, str]
    audience: str
    spine_node_id: str | None
    breadcrumb: list[str]
    price: float
    original_price: float | None
    currency: str
    url: str
    size_and_fit: list
    image_paths: list[str] = field(default_factory=list)

    def parts(self, extras: bool = False) -> list[tuple[str, str]]:
        """(label, text) pairs in the order the skill files list them; empty parts are left out."""
        editor = "\n".join(self.tabs[t] for t in EDITOR_TABS if self.tabs.get(t))
        details = "\n".join(self.details) or "\n".join(self.tabs[t] for t in DETAIL_TABS if self.tabs.get(t))
        out = [("Title", self.name), ("Brand", self.brand), ("Description", self.description)]
        if editor and _norm(editor) != _norm(self.description):
            out.append(("Editor's Notes", editor))
        out.append(("Design Details", details))
        if extras:
            for t in EXTRA_TABS:
                if self.tabs.get(t):
                    out.append((t, self.tabs[t]))
        return [(label, text) for label, text in out if text and text.strip()]

    def text_block(self, extras: bool = False) -> str:
        return "\n\n".join(f"{label.upper()}:\n{text}" for label, text in self.parts(extras))

    def searchable(self) -> str:
        """All product text, normalised, for checking that a quote appears in it."""
        return _norm(" ".join(t for _, t in self.parts(extras=True)) + " " + " ".join(self.tabs.values()))


def _norm(s: str) -> str:
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s.lower()).strip()


def quote_found(quote: str | None, haystack_normalised: str) -> bool:
    """PRD 4.4: compare after lower-casing and collapsing whitespace. Surrounding quote marks are ignored."""
    if not quote or not quote.strip():
        return False
    q = _norm(quote).strip(" \"'")
    return bool(q) and q in haystack_normalised


PRODUCT_SQL = """select product_id, name, brand, description, details, content_tabs, audience, spine_node_id,
                        site_breadcrumb, price, original_price, currency, url, size_and_fit
                 from products where product_id = %s"""


def load_product(conn: psycopg.Connection, product_id: str) -> Product:
    row = conn.execute(PRODUCT_SQL, (product_id,)).fetchone()
    if not row:
        raise LookupError(f"product {product_id} is not in products")
    (pid, name, brand, desc, details, tabs, audience, spine, crumb, price, orig, cur, url, saf) = row
    tab_text = {}
    for t in tabs or []:
        label = t.get("label")
        if label and t.get("text"):
            tab_text[label] = t["text"]
    paths = [r[0] for r in conn.execute(
        "select storage_path from product_images where product_id = %s and storage_path is not null "
        "order by position", (pid,))]
    return Product(pid, name, brand, desc or "", [str(d) for d in (details or [])], tab_text, audience or "",
                   spine, list(crumb or []), float(price), float(orig) if orig is not None else None, cur,
                   url, saf or [], paths)


# ------------------------------------------------------------------ candidates

@dataclass
class Candidates:
    nodes: list[dict]
    note: str | None = None

    @property
    def ids(self) -> list[str]:
        return [n["node_id"] for n in self.nodes]

    def text_block(self) -> str:
        lines = []
        for n in self.nodes:
            line = f"- {n['node_id']}: {n['path']}"
            if n.get("is_parent"):
                line += " (parent: choose only if no sub-category below fits)"
            if n.get("definition"):
                line += f"\n  Definition: {n['definition']}"
            if n.get("aliases"):
                line += f"\n  Aliases: {'; '.join(n['aliases'][:8])}"
            lines.append(line)
        return "CANDIDATE CATEGORIES:\n" + "\n".join(lines)


# Leaves, plus parent nodes that carry their own facets in the dictionary: a parent is the answer when no
# sub-category fits (skill rule 2). Holding nodes ("Other ...") are never offered.
CANDIDATE_SQL = """select c.node_id, c.path, c.definition, c.aliases, c.is_leaf from categories c
                   where c.level in ('L3', 'L4', 'L5') and c.audience = %s and c.department = any(%s)
                     and c.name not ilike 'other %%'
                     and (c.is_leaf or exists (select 1 from category_facets cf
                                               where cf.category_id = c.node_id and cf.inherited_from is null))
                   order by c.path"""

# The same rule as SQL, for checks elsewhere (golden import, sampling).
ASSIGNABLE_SQL = """select c.node_id from categories c where c.name not ilike 'other %%'
                      and (c.is_leaf or exists (select 1 from category_facets cf
                                                where cf.category_id = c.node_id and cf.inherited_from is null))"""


def candidates_for(conn: psycopg.Connection, product: Product) -> Candidates:
    """Leaf nodes under the product's audience and department, plus parent nodes with their own facets,
    without holding ('Other ...') nodes. A parent is for products no sub-category fits. When the scraper
    stored no spine node, the audience and department come from the site breadcrumb instead.
    """
    note = None
    row = None
    if product.spine_node_id:
        row = conn.execute("select audience, department from categories where node_id = %s",
                           (product.spine_node_id,)).fetchone()
    if row and row[1]:
        audience, departments = row[0], [row[1]]
    else:
        crumb = product.breadcrumb
        audience = (crumb[0] if crumb else product.audience).title()
        dept = crumb[1] if len(crumb) > 1 else ""
        departments = list(CRUMB_DEPARTMENTS.get(dept, (dept,)))
        note = (f"no spine node stored; candidates from the site breadcrumb "
                f"({' / '.join(crumb[:2]) or 'none'})")
    nodes = _fetch(conn, audience, departments)
    if not nodes:   # breadcrumb department not in the spine (e.g. "Edits"): every node for the audience
        all_depts = [r[0] for r in conn.execute(
            "select distinct department from categories where audience = %s and department <> ''", (audience,))]
        nodes = _fetch(conn, audience, all_depts)
        note = (note or "") + f"; department not found, offered every {audience} node"
    return Candidates(nodes, note)


def _fetch(conn, audience, departments):
    return [{"node_id": r[0], "path": r[1], "definition": r[2], "aliases": r[3] or [], "is_parent": not r[4]}
            for r in conn.execute(CANDIDATE_SQL, (audience, departments))]


# ------------------------------------------------------------------ images

class ImageStore:
    """Reads product images from Supabase Storage with the service key (the bucket is private)."""

    def __init__(self):
        url = os.environ.get("SUPABASE_URL", "").strip()
        key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
        if not url or not key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY are needed to read product images")
        parts = urlsplit(url)
        headers = {"apikey": key}
        if not key.startswith("sb_"):
            headers["Authorization"] = f"Bearer {key}"
        self._http = httpx.Client(base_url=f"{parts.scheme}://{parts.netloc}/storage/v1", headers=headers,
                                  timeout=60.0)

    def get(self, path: str) -> Image:
        r = self._http.get(f"/object/authenticated/{BUCKET}/{path}")
        r.raise_for_status()
        return Image(r.content, media_type(r.content), path)

    def close(self):
        self._http.close()


def media_type(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/jpeg"
