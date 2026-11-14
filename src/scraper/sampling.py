"""Spine loading, plan drafting, and the picking rules used during a scrape."""
from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .client import SITE
from .listing import ListingItem

HOLDING_NAMES = {"heels", "other shoes", "other accessories", "other bags"}
NEW_TYPE_DEPTS = {"jewellery", "watches"}          # L4s that gained product types in v5
NEW_TYPE_L3 = {"eyewear", "hats", "watches"}       # ...plus these L3 parents
PLACEMENT_TEST_PAGES = ["beachwear", "loungewear"]  # site pages kept with needs_placement

# --------------------------------------------------------------------------- spine

# Accepted header spellings, normalised (lowercase, spaces -> _). First match wins.
COLUMN_ALIASES = {
    "node_id": ["node_id", "id", "category_id", "spine_node_id"],
    "parent_id": ["parent_id", "parent", "parent_node_id"],
    "level": ["level", "depth", "node_level"],
    "audience": ["audience", "gender"],
    "department": ["department", "dept"],
    "name": ["name", "label", "category", "node_name", "title"],
    "slug": ["slug", "node_slug"],
    "path": ["path", "full_path", "spine_path", "node_path"],
    "is_leaf": ["is_leaf", "leaf"],
    "definition": ["definition", "description"],
    "aliases": ["aliases", "alias", "source_aliases", "old_csv_classes_merged_here", "old_names"],
    "review_flag": ["review_flag", "flag", "flags", "review", "holding", "status"],
    "source_count": ["source_count", "illustrative_source_count"],
}


@dataclass
class Node:
    node_id: str
    parent_id: str | None
    level: str
    audience: str
    department: str
    name: str
    slug: str
    path: str
    is_leaf: bool
    definition: str = ""
    aliases: list[str] = field(default_factory=list)
    review_flag: str = ""
    source_count: float = 0.0

    @property
    def is_holding(self) -> bool:
        # v5 marks them in the definition ("a holding node pending SKU-level typing").
        return ("holding" in self.review_flag.lower() or "holding node" in self.definition.lower()
                or self.name.strip().lower() in HOLDING_NAMES)


def _norm(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", h.strip().lower()).strip("_")


def slugify(s: str) -> str:
    s = s.lower().replace("&", "and").replace("'", "")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def _truthy(v: str) -> bool:
    return str(v).strip().lower() in ("true", "t", "1", "yes", "y")


def load_spine(path: Path) -> list[Node]:
    """Read the spine CSV, mapping whatever column names it uses onto ours."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = {_norm(h): h for h in reader.fieldnames or []}
        colmap = {}
        for ours, options in COLUMN_ALIASES.items():
            for opt in options:
                if opt in headers:
                    colmap[ours] = headers[opt]
                    break
        missing = [c for c in ("node_id", "parent_id", "name") if c not in colmap]
        if missing:
            raise ValueError(f"spine CSV {path} lacks columns for {missing}; headers are {reader.fieldnames}")
        raw_rows = list(reader)

    def g(row, key, default=""):
        col = colmap.get(key)
        return (row.get(col) or default).strip() if col else default

    by_id = {g(r, "node_id"): r for r in raw_rows}
    children = Counter(g(r, "parent_id") for r in raw_rows if g(r, "parent_id"))

    def depth(nid: str) -> int:
        d, seen = 1, set()
        while (p := g(by_id[nid], "parent_id")) and p in by_id and p not in seen:
            seen.add(p)
            nid, d = p, d + 1
        return d

    nodes: dict[str, Node] = {}
    for r in raw_rows:
        nid = g(r, "node_id")
        level = g(r, "level") or f"L{depth(nid)}"
        if re.fullmatch(r"\d+", level):
            level = f"L{level}"
        name = g(r, "name")
        leaf_raw = g(r, "is_leaf")
        nodes[nid] = Node(
            node_id=nid,
            parent_id=g(r, "parent_id") or None,
            level=level.upper(),
            audience=g(r, "audience"),
            department=g(r, "department"),
            name=name,
            slug=g(r, "slug") or slugify(name),
            path=g(r, "path"),
            is_leaf=_truthy(leaf_raw) if leaf_raw else children[nid] == 0,
            definition=g(r, "definition"),
            aliases=[a.strip() for a in re.split(r"[;|]", g(r, "aliases")) if a.strip()],
            review_flag=g(r, "review_flag"),
            source_count=float(g(r, "source_count") or 0 or 0),
        )
    for n in nodes.values():  # fill missing paths from the parent chain
        if not n.path:
            parts, cur = [], n
            while cur:
                parts.append(cur.name)
                cur = nodes.get(cur.parent_id) if cur.parent_id else None
            n.path = " / ".join(reversed(parts))
    return list(nodes.values())


# --------------------------------------------------------------------------- areas

@dataclass(frozen=True)
class Area:
    name: str
    quota: int
    audiences: tuple[str, ...]      # spine Audience values; () = any
    departments: tuple[str, ...]    # spine Department values


AREAS = [
    Area("Women: clothing", 70, ("Women",), ("Clothing",)),
    Area("Women: shoes", 30, ("Women",), ("Shoes",)),
    Area("Women: bags", 25, ("Women",), ("Bags",)),
    Area("Women: accessories", 15, ("Women",), ("Accessories",)),
    Area("Women: jewellery", 15, ("Women",), ("Jewellery",)),
    Area("Men: clothing", 40, ("Men",), ("Clothing",)),
    Area("Men: shoes", 15, ("Men",), ("Shoes",)),
    Area("Men: bags and accessories", 15, ("Men",), ("Bags", "Accessories", "Jewellery")),
    Area("Watches", 5, ("Women", "Men"), ("Watches",)),
    Area("Beauty", 10, ("Women", "Men"), ("Beauty",)),
    Area("Home", 5, ("All",), ("Home",)),
    Area("Kids", 5, ("Kids",), ("Clothing", "Shoes", "Accessories")),
]
PLACEMENT_TEST_QUOTA = 10  # taken from "Women: clothing"

# Where each audience/department lives on the site (URL prefixes to search under).
SITE_ROOTS = {
    ("Women", "Clothing"): ["/women/clothing"],
    ("Women", "Shoes"): ["/women/shoes"],
    ("Women", "Bags"): ["/women/bags"],
    ("Women", "Accessories"): ["/women/accessories"],
    ("Women", "Jewellery"): ["/women/jewellery"],
    ("Women", "Watches"): ["/women/jewellery/fine-watches", "/women/watches", "/women/accessories/watches"],
    ("Women", "Beauty"): ["/women/beauty"],
    ("Men", "Clothing"): ["/men/clothing"],
    ("Men", "Shoes"): ["/men/shoes"],
    ("Men", "Bags"): ["/men/bags", "/men/accessories/bags"],
    ("Men", "Accessories"): ["/men/accessories"],
    ("Men", "Jewellery"): ["/men/accessories/jewellery", "/men/jewellery"],
    ("Men", "Watches"): ["/men/watches", "/men/accessories/watches"],
    ("Men", "Beauty"): ["/men/grooming", "/men/beauty"],
    ("All", "Home"): ["/women/home"],
    ("Kids", "Clothing"): ["/kids/girl/clothing", "/kids/boy/clothing"],
    ("Kids", "Shoes"): ["/kids/girl/shoes", "/kids/boy/shoes"],
    ("Kids", "Accessories"): ["/kids/girl/accessories", "/kids/boy/accessories"],
}
EXCLUDE_PATH = re.compile(r"/(sale|edits?|designers|shop-by|private-sale|cyber-sale|gifts?)(/|$)|staff-sale")

# Site wording that differs from the spine name.
EXTRA_SYNONYMS = {
    "pants": ["trousers"], "coats & jackets": ["coats-jackets"], "eyewear": ["sunglasses"],
    "sunglasses": ["sunglasses"], "optical glasses": ["optical", "optical-frames"],
    "earrings": ["earrings"], "watches": ["fine-watches", "watches"], "sneakers": ["trainers"],
    "flats": ["flat-shoes"], "chains": ["chain"], "pendants": ["pendant"], "bands": ["band"],
    "chain bracelets": ["chain"], "cocktail rings": ["cocktail"], "studs": ["studs"],
    "drop earrings": ["drop"], "hoops": ["hoop", "hoops"], "single earrings": ["singles"],
    "bangles": ["bangles"], "makeup": ["make-up"], "make-up": ["make-up"], "skincare": ["skin-care"],
    "skin care": ["skin-care"], "haircare": ["hair-care"], "candles & home fragrance": ["candles"],
    "dining": ["tableware"], "decor": ["home-decor", "decorative-objects"],
}


def _key_variants(text: str, synonyms: bool = True) -> set[str]:
    base = slugify(text)
    out = {base, base.replace("-and-", "-")}
    for b in list(out):
        if b.endswith("ies"):
            out.add(b[:-3] + "y")
        if b.endswith("es"):
            out.add(b[:-2])
        if b.endswith("s"):
            out.add(b[:-1])
        else:
            out.add(b + "s")
    if synonyms:
        out |= {slugify(s) for s in EXTRA_SYNONYMS.get(text.strip().lower(), [])}
    return {o for o in out if o}


@dataclass
class PlanRow:
    spine_node_id: str
    spine_path: str
    listing_url: str
    quota: int
    area: str
    placement_level: str
    note: str = ""

    FIELDS = ["spine_node_id", "spine_path", "listing_url", "quota", "area", "placement_level", "note"]


def match_nodes_to_pages(nodes: list[Node], site_paths: list[str]) -> dict[str, str]:
    """node_id -> best listing path. Name first; an alias only when unique in its dept."""
    paths = [p for p in site_paths if not EXCLUDE_PATH.search(p)]
    by_id = {n.node_id: n for n in nodes}
    alias_owners: Counter = Counter()
    for n in nodes:
        for a in n.aliases:
            alias_owners[(n.audience, n.department, slugify(a))] += 1

    l3_keys: dict[tuple[str, str], set[str]] = defaultdict(set)
    for n in nodes:
        if n.level == "L3":
            l3_keys[(n.audience, n.department)] |= _key_variants(n.name, synonyms=False)

    out: dict[str, str] = {}
    for n in nodes:
        if n.level not in ("L3", "L4", "L5") or n.is_holding:
            continue
        roots = SITE_ROOTS.get((n.audience, n.department))
        if not roots:
            continue
        # Pages strictly below a department root, never the root itself, and never the
        # Beachwear/Loungewear pages (those feed the placement test set only).
        under = [p for p in paths if any(p.startswith(r + "/") for r in roots)
                 and not any(seg in PLACEMENT_TEST_PAGES for seg in p.split("/"))]
        name_keys = _key_variants(n.name)
        alias_keys = set()
        for a in n.aliases:
            if alias_owners[(n.audience, n.department, slugify(a))] == 1 and slugify(a) not in PLACEMENT_TEST_PAGES:
                alias_keys |= _key_variants(a)
        parent = by_id.get(n.parent_id or "")
        parent_keys = _key_variants(parent.name) if parent and n.level != "L3" else set()
        # An L4 may take a page directly under the department root (Sunglasses under
        # /women/accessories) unless an L3 has that name (Activewear/Tops vs Tops).
        root_ok = not (name_keys & l3_keys[(n.audience, n.department)])

        def candidates(keys: set[str]) -> list[str]:
            hits = [p for p in under if p.rsplit("/", 1)[-1] in keys]
            if parent_keys:  # an L4 sits below a page matching its L3, or directly under the root
                hits = [p for p in hits if any(seg in parent_keys for seg in p.split("/")[:-1])
                        or (root_ok and p.rsplit("/", 1)[0] in roots)]
            return sorted(hits, key=lambda p: (p.count("/"), p))

        hits = candidates(name_keys) or candidates(alias_keys)
        if hits:
            out[n.node_id] = hits[0]
    return out


def _is_new_type_l4(n: Node, by_id: dict[str, Node]) -> bool:
    if n.level != "L4":
        return False
    parent = by_id.get(n.parent_id or "")
    return n.department.lower() in NEW_TYPE_DEPTS or (parent is not None and parent.name.lower() in NEW_TYPE_L3)


def draft_plan(nodes: list[Node], site_paths: list[str], target: int = 250) -> tuple[list[PlanRow], list[str]]:
    """Draft the sample plan. Returns rows and human-readable notes on gaps."""
    scale = target / sum(a.quota for a in AREAS)
    by_id = {n.node_id: n for n in nodes}
    matched = match_nodes_to_pages(nodes, site_paths)
    rows: list[PlanRow] = []
    notes: list[str] = []

    for area in AREAS:
        quota = max(3, round(area.quota * scale))
        if area.name == "Women: clothing":
            pt = max(3, round(PLACEMENT_TEST_QUOTA * scale))
            per = [pt - pt // 2, pt // 2]
            for page, q in zip(PLACEMENT_TEST_PAGES, per):
                url = f"/women/clothing/{page}"
                if url in site_paths:
                    rows.append(PlanRow("", "", SITE + url, q, area.name, "",
                                        f"{page.title()} placement test set: saved with needs_placement"))
                    quota -= q
                else:
                    notes.append(f"No {url} page found for the placement test set.")

        in_area = [n for n in nodes if n.node_id in matched
                   and (not area.audiences or n.audience in area.audiences)
                   and n.department in area.departments]
        # Deepest page wins: drop an L3 when one of its children matched.
        matched_parents = {n.parent_id for n in in_area if n.level in ("L4", "L5")}
        in_area = [n for n in in_area if n.node_id not in matched_parents]
        # Avoid two nodes pointing at the same page.
        seen_urls, uniq = set(), []
        for n in sorted(in_area, key=lambda n: (-_is_new_type_l4(n, by_id), -n.source_count)):
            if matched[n.node_id] not in seen_urls:
                seen_urls.add(matched[n.node_id])
                uniq.append(n)
        # Round-robin across L3 parents so one L3 cannot take the whole area.
        groups: dict[str, list[Node]] = defaultdict(list)
        for n in uniq:
            groups[n.node_id if n.level == "L3" else (n.parent_id or "")].append(n)
        ordered, i = [], 0
        group_lists = sorted(groups.values(), key=lambda g: (-_is_new_type_l4(g[0], by_id), -g[0].source_count))
        while any(i < len(g) for g in group_lists):
            ordered += [g[i] for g in group_lists if i < len(g)]
            i += 1
        if set(area.departments) <= {"Jewellery", "Watches"}:
            # v5 gave these L4s their product types; they are the point of sampling here.
            ordered.sort(key=lambda n: not _is_new_type_l4(n, by_id))

        n_nodes = min(len(ordered), max(1, quota // 3))
        if n_nodes == 0:
            notes.append(f"{area.name}: no spine node matched a site listing page; quota {quota} unplanned.")
            continue
        chosen = ordered[:n_nodes]
        base, extra = divmod(quota, n_nodes)
        for k, n in enumerate(chosen):
            q = base + (1 if k < extra else 0)
            note = "v5 new-type L4" if _is_new_type_l4(n, by_id) else ""
            if not n.is_leaf:
                note = (note + "; " if note else "") + "not a leaf: products saved with needs_placement"
            rows.append(PlanRow(n.node_id, n.path, SITE + matched[n.node_id], q, area.name, n.level, note))
        if len(ordered) * 3 < quota:
            notes.append(f"{area.name}: only {len(ordered)} matched nodes for quota {quota}.")

    nodes_cov = len({r.spine_node_id for r in rows if r.spine_node_id})
    special = sum(1 for r in rows if r.spine_node_id and _is_new_type_l4(by_id[r.spine_node_id], by_id))
    notes.append(f"Plan covers {nodes_cov} nodes ({special} new-type L4s in Jewellery/Eyewear/Watches/Hats), "
                 f"{sum(r.quota for r in rows)} products.")
    if nodes_cov < 60:
        notes.append("Below the 60-node target: add rows by hand.")
    if special < 10:
        notes.append("Below 10 new-type L4 nodes: add Jewellery/Eyewear/Watches/Hats L4 rows by hand.")
    return rows, notes


def write_plan(rows: list[PlanRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=PlanRow.FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: getattr(r, k) for k in PlanRow.FIELDS})


def read_plan(path: Path) -> list[PlanRow]:
    with open(path, newline="", encoding="utf-8") as f:
        return [PlanRow(r["spine_node_id"].strip(), r["spine_path"].strip(), r["listing_url"].strip(),
                        int(r["quota"]), r.get("area", "").strip(), r.get("placement_level", "").strip(),
                        r.get("note", "")) for r in csv.DictReader(f) if r.get("listing_url", "").strip()]


def check_plan(rows: list[PlanRow], nodes: list[Node]) -> list[str]:
    by_id = {n.node_id: n for n in nodes}
    problems = []
    for r in rows:
        if r.spine_node_id:
            n = by_id.get(r.spine_node_id)
            if n is None:
                problems.append(f"{r.spine_node_id}: not in the spine")
            elif n.is_holding:
                problems.append(f"{r.spine_node_id} ({n.path}): holding node, do not target")
        if r.quota < 3:
            problems.append(f"{r.listing_url}: quota {r.quota} is below 3 per node")
    return problems


# --------------------------------------------------------------------------- picking


def interleave_by_area(rows: list[PlanRow]) -> list[PlanRow]:
    """Order rows round-robin across areas so a partial (--limit) run spans areas."""
    groups: dict[str, list[PlanRow]] = defaultdict(list)
    for r in rows:
        groups[r.area].append(r)
    out, i = [], 0
    lists = list(groups.values())
    while any(i < len(g) for g in lists):
        out += [g[i] for g in lists if i < len(g)]
        i += 1
    return out


def stride_order(n: int, stride: int = 3) -> list[int]:
    """0, 3, 6, ... then 1, 4, 7, ... so picks spread across the page, not the top rows."""
    return [i for offset in range(stride) for i in range(offset, n, stride)]


@dataclass
class Picker:
    """Keeps the cross-node rules: one colour per style, brand caps."""
    per_node_brand_cap: int = 3
    global_brand_soft_cap: int = 8
    styles_taken: set[str] = field(default_factory=set)
    ids_taken: set[str] = field(default_factory=set)
    brand_counts: Counter = field(default_factory=Counter)

    def candidates(self, items: list[ListingItem]) -> list[ListingItem]:
        ordered = [items[i] for i in stride_order(len(items))]
        fresh = [it for it in ordered if not it.out_of_stock and it.style_color_id not in self.ids_taken]
        first = [it for it in fresh if it.parent_sku not in self.styles_taken
                 and self.brand_counts[it.brand] < self.global_brand_soft_cap]
        # Fallbacks only used when the quota cannot be met otherwise.
        second = [it for it in fresh if it.parent_sku not in self.styles_taken and it not in first]
        third = [it for it in fresh if it not in first and it not in second]
        return first + second + third

    def accept(self, item: ListingItem) -> None:
        self.ids_taken.add(item.style_color_id)
        self.styles_taken.add(item.parent_sku)
        self.brand_counts[item.brand] += 1
