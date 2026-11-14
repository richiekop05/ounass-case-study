"""Golden set: sample products, export a labelling sheet, import the labels (PRD 6.1).

The sheet is built only from scraped data and the taxonomy. It never reads catalog_products or
product_facet_tags, so no predicted category or tag can leak into it.
"""
from __future__ import annotations

import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font
from openpyxl.worksheet.datavalidation import DataValidation

from .facets import MODEL_POLICIES, FacetSpec, facets_for_category
from .products import CRUMB_DEPARTMENTS, candidates_for, load_product
from .runs import Db

SAMPLE_SEED = 20261113
MIN_PER_DEPARTMENT = 3
STATES = {"value": "value", "unknown": "unknown", "not applicable": "not_applicable",
          "not_applicable": "not_applicable", "n/a": "not_applicable"}
CATEGORY_HEADERS = ["product_id", "title", "brand", "product_url", "category_id", "candidate_nodes"]
FACET_HEADERS = ["product_id", "title", "product_url", "category_id", "facet_id", "policy", "takes", "definition",
                 "allowed_values", "state", "answer", "notes"]


# ------------------------------------------------------------------ sample

def product_departments(db: Db) -> dict[str, str]:
    """Department per scraped product: from its spine node, else from the site breadcrumb."""
    out = {}
    for pid, dept, crumb in db.execute(
            """select p.product_id, c.department, p.site_breadcrumb from products p
               left join categories c on c.node_id = p.spine_node_id"""):
        if dept:
            out[pid] = dept
        else:
            word = crumb[1] if crumb and len(crumb) > 1 else "Unknown"
            out[pid] = CRUMB_DEPARTMENTS.get(word, (word,))[0]
    return out


def allocate(counts: dict[str, int], n: int, minimum: int = MIN_PER_DEPARTMENT) -> dict[str, int]:
    """At least `minimum` per department (or all it has), the rest in proportion (largest remainder)."""
    alloc = {d: min(minimum, c) for d, c in counts.items()}
    left = n - sum(alloc.values())
    if left <= 0:
        return alloc
    total = sum(counts.values())
    shares = {d: n * c / total - alloc[d] for d, c in counts.items()}
    room = {d: counts[d] - alloc[d] for d in counts}
    for d in sorted(counts, key=lambda d: -shares[d]):
        take = max(0, min(room[d], int(shares[d]), left))
        alloc[d] += take
        left -= take
    while left > 0 and any(counts[d] > alloc[d] for d in counts):
        for d in sorted(counts, key=lambda d: -(n * counts[d] / total - alloc[d])):
            if left and counts[d] > alloc[d]:
                alloc[d] += 1
                left -= 1
    return alloc


def sample(db: Db, n: int, seed: int = SAMPLE_SEED) -> dict[str, int]:
    with db.lock:
        existing = db.conn.execute("select count(*) from golden_products").fetchone()[0]
        if existing:
            raise ValueError(f"golden_products already has {existing} products; the golden set is picked once")
        depts = product_departments(db)
        by_dept = defaultdict(list)
        for pid, d in sorted(depts.items()):
            by_dept[d].append(pid)
        alloc = allocate({d: len(v) for d, v in by_dept.items()}, n)
        rng = random.Random(seed)
        picked = []
        for d in sorted(by_dept):
            picked += rng.sample(by_dept[d], alloc[d])
        with db.conn.transaction():
            with db.conn.cursor() as cur:
                cur.executemany("insert into golden_products (product_id, notes) values (%s, %s)",
                                [(p, f"sampled seed {seed}, department {depts[p]}") for p in picked])
        db.commit()
    return {d: alloc[d] for d in sorted(alloc)}


# ------------------------------------------------------------------ export

def model_facing(specs: dict[str, FacetSpec]) -> list[FacetSpec]:
    """Facets a person labels: the model-facing ones. Derived facets are checked by tests instead."""
    return sorted((s for s in specs.values() if s.source_policy in MODEL_POLICIES),
                  key=lambda s: (s.sort_order if s.sort_order is not None else 10**6, s.facet_id))


def export(db: Db, path: Path) -> dict:
    with db.lock:
        golden = db.conn.execute("""select g.product_id, g.category_id, p.name, p.brand, p.url
                                    from golden_products g join products p using (product_id)
                                    order by g.product_id""").fetchall()
        if not golden:
            raise ValueError("golden_products is empty: run `tagger golden sample` first")
        labels = defaultdict(dict)
        for pid, fid, status, label, num, txt in db.conn.execute(
                """select t.product_id, t.facet_id, t.status, fv.label, t.value_number, t.value_text
                   from golden_tags t left join facet_values fv on fv.facet_value_id = t.facet_value_id
                   order by t.golden_tag_id"""):
            entry = labels[pid].setdefault(fid, {"state": status, "values": []})
            v = label if label is not None else (f"{float(num):g}" if num is not None else txt)
            if v is not None:
                entry["values"].append(v)

        wb = openpyxl.Workbook()
        cats = wb.active
        cats.title = "Categories"
        cats.append(CATEGORY_HEADERS)
        facets_ws = wb.create_sheet("Facets")
        facets_ws.append(FACET_HEADERS)
        n_facet_rows, unlabelled = 0, 0
        spec_cache: dict[str, dict] = {}
        for pid, category_id, name, brand, url in golden:
            product = load_product(db.conn, pid)
            cands = candidates_for(db.conn, product)
            cats.append([pid, name, brand, url, category_id or "",
                         "\n".join(f"{n['node_id']}: {n['path']}" for n in cands.nodes)])
            if not category_id:
                unlabelled += 1
                continue
            if category_id not in spec_cache:
                spec_cache[category_id] = facets_for_category(db.conn, category_id)
            for s in model_facing(spec_cache[category_id]):
                have = labels.get(pid, {}).get(s.facet_id, {})
                state = {"not_applicable": "not applicable"}.get(have.get("state"), have.get("state", ""))
                facets_ws.append([pid, name, url, category_id, s.facet_id, s.source_policy, s.limit_text(),
                                  s.definition or "",
                                  "\n".join(f"{v.label}: {v.definition}" if v.definition else v.label
                                            for v in s.values),
                                  state, "; ".join(have.get("values", [])), ""])
                n_facet_rows += 1
    _format(cats, {"A": 16, "B": 40, "C": 20, "D": 40, "E": 16, "F": 70})
    _format(facets_ws, {"A": 16, "B": 32, "C": 30, "D": 14, "E": 20, "F": 11, "G": 22, "H": 60, "I": 50,
                        "J": 16, "K": 30, "L": 30})
    dv = DataValidation(type="list", formula1='"value,unknown,not applicable"', allow_blank=True)
    facets_ws.add_data_validation(dv)
    dv.add(f"J2:J{max(2, facets_ws.max_row)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return {"products": len(golden), "without_category": unlabelled, "facet_rows": n_facet_rows}


def _format(ws, widths):
    for col, w in widths.items():
        ws.column_dimensions[col].width = w
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A2"


# ------------------------------------------------------------------ import

@dataclass
class ImportReport:
    categories_set: int = 0
    tags_loaded: int = 0
    facets_loaded: int = 0
    skipped_blank: int = 0
    invalid: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"categories set: {self.categories_set}; facet answers loaded: {self.facets_loaded} "
                 f"({self.tags_loaded} rows); blank rows skipped: {self.skipped_blank}; "
                 f"invalid rows: {len(self.invalid)}"]
        lines += [f"  {m}" for m in self.invalid]
        return "\n".join(lines)


def parse_answer(spec: FacetSpec, state_text, answer_text) -> tuple[str, list, str | None]:
    """Validate one facet answer. Returns (state, values, error)."""
    state = STATES.get(str(state_text or "").strip().lower())
    if state is None:
        return "", [], f"state {state_text!r} is not value, unknown or not applicable"
    # values are separated by ";" or a new line; for listed values a comma works too (none contains one)
    seps = r"[;,\n]" if spec.has_value_list else r"[;\n]"
    parts = [a.strip() for a in re.split(seps, str(answer_text or "")) if a.strip()]
    if state != "value":
        return (state, [], None) if not parts else (state, [], f"state {state} but an answer is given")
    if not parts:
        return state, [], "state value but no answer"
    values = []
    if spec.has_value_list:
        for a in parts:
            fv = spec.lookup(a)
            if not fv:
                return state, [], f"{a!r} is not an allowed value of {spec.facet_id}"
            if fv not in values:
                values.append(fv)
        exclusive = [v for v in values if v.is_exclusive]
        if exclusive and len(values) > 1:
            return state, [], f'"{exclusive[0].label}" cannot sit beside another value'
    elif spec.data_type == "number":
        try:
            x = float(parts[0].replace(",", ""))
        except ValueError:
            return state, [], f"{parts[0]!r} is not a number"
        if x <= 0 or len(parts) > 1:
            return state, [], "expects one positive number"
        values = [x]
    else:
        values = [" ".join(parts)]
    if spec.max_values and len(values) > spec.max_values:
        return state, [], f"{len(values)} values; {spec.facet_id} takes at most {spec.max_values}"
    return state, values, None


def import_sheet(db: Db, path: Path) -> ImportReport:
    rep = ImportReport()
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    for sheet, headers in (("Categories", CATEGORY_HEADERS), ("Facets", FACET_HEADERS)):
        if sheet not in wb.sheetnames:
            raise ValueError(f"{path.name} has no {sheet} sheet")
        got = [c.value for c in next(wb[sheet].iter_rows(max_row=1))]
        missing = [h for h in headers if h not in got]
        if missing:
            raise ValueError(f"{sheet} sheet is missing columns {missing}")

    def rows(sheet):
        it = wb[sheet].iter_rows(values_only=True)
        head = list(next(it))
        for i, r in enumerate(it, start=2):
            yield i, dict(zip(head, r))

    with db.lock, db.conn.transaction():
        conn = db.conn
        golden = {r[0]: r[1] for r in conn.execute("select product_id, category_id from golden_products")}
        from .products import ASSIGNABLE_SQL
        leaves = {r[0] for r in conn.execute(ASSIGNABLE_SQL)}
        for i, r in rows("Categories"):
            pid, cat = str(r.get("product_id") or "").strip(), str(r.get("category_id") or "").strip()
            if not pid:
                continue
            if pid not in golden:
                rep.invalid.append(f"Categories row {i}: {pid} is not in golden_products")
                continue
            if not cat:
                continue
            if cat not in leaves:
                rep.invalid.append(f"Categories row {i}: {cat!r} is not a leaf category")
                continue
            if golden[pid] != cat:
                conn.execute("update golden_products set category_id = %s where product_id = %s", (cat, pid))
                # labels for facets that do not apply to the new category are dropped
                conn.execute("""delete from golden_tags t where t.product_id = %s and not exists
                                (select 1 from category_facets cf where cf.category_id = %s
                                 and cf.facet_id = t.facet_id)""", (pid, cat))
                golden[pid] = cat
            rep.categories_set += 1

        spec_cache: dict[str, dict] = {}
        for i, r in rows("Facets"):
            pid, fid = str(r.get("product_id") or "").strip(), str(r.get("facet_id") or "").strip()
            if not pid or not fid:
                continue
            if r.get("state") in (None, "") and r.get("answer") in (None, ""):
                rep.skipped_blank += 1
                continue
            cat = golden.get(pid)
            if not cat:
                rep.invalid.append(f"Facets row {i}: {pid} has no golden category yet")
                continue
            if cat not in spec_cache:
                spec_cache[cat] = facets_for_category(conn, cat)
            spec = spec_cache[cat].get(fid)
            if spec is None or spec.source_policy not in MODEL_POLICIES:
                rep.invalid.append(f"Facets row {i}: {fid} is not a labelled facet for {cat}")
                continue
            state, values, err = parse_answer(spec, r.get("state"), r.get("answer"))
            if err:
                rep.invalid.append(f"Facets row {i} ({pid}, {fid}): {err}")
                continue
            conn.execute("delete from golden_tags where product_id = %s and facet_id = %s", (pid, fid))
            if state != "value":
                conn.execute("insert into golden_tags (product_id, facet_id, status) values (%s, %s, %s)",
                             (pid, fid, state))
                rep.tags_loaded += 1
            for v in values:
                conn.execute("""insert into golden_tags (product_id, facet_id, status, facet_value_id, value_number,
                                    value_text) values (%s, %s, 'value', %s, %s, %s)""",
                             (pid, fid, getattr(v, "facet_value_id", None),
                              v if isinstance(v, float) else None, v if isinstance(v, str) else None))
                rep.tags_loaded += 1
            rep.facets_loaded += 1
    db.commit()
    return rep


def golden_counts(db: Db) -> Counter:
    c = Counter()
    for (cat,) in db.execute("select category_id from golden_products"):
        c["labelled" if cat else "unlabelled"] += 1
    return c


# ------------------------------------------------------------------ extending the sample

def parse_add(spec: str) -> dict[str, int]:
    """'clothing=10,beauty=7' -> {'clothing': 10, 'beauty': 7} (department names lower-cased)."""
    out = {}
    for part in (spec or "").split(","):
        if not part.strip():
            continue
        name, sep, n = part.partition("=")
        if not sep or not n.strip().isdigit() or not name.strip():
            raise ValueError(f"--add expects department=number pairs, e.g. clothing=10,beauty=7 (got {part!r})")
        out[name.strip().lower()] = out.get(name.strip().lower(), 0) + int(n)
    if not out:
        raise ValueError("--add is empty")
    return out


def pick_spread(candidates: list[tuple[str, str, str]], existing: list[tuple[str, str]], n: int,
                rng: random.Random) -> list[str]:
    """Pick n of (product_id, category, brand), never a second product of a brand and category while an
    unrepresented pair remains; then the least-represented category, then brand. Ties go by a seeded shuffle."""
    pool = sorted(candidates)
    rng.shuffle(pool)
    order = {c[0]: i for i, c in enumerate(pool)}
    pairs = Counter(existing)
    cats = Counter(c for c, _ in existing)
    brands = Counter(b for _, b in existing)
    picked = []
    while pool and len(picked) < n:
        best = min(pool, key=lambda c: (pairs[(c[1], c[2])], cats[c[1]], brands[c[2]], order[c[0]]))
        pool.remove(best)
        picked.append(best[0])
        pairs[(best[1], best[2])] += 1
        cats[best[1]] += 1
        brands[best[2]] += 1
    return picked


def golden_make_up(db: Db) -> dict[str, int]:
    depts = product_departments(db)
    return dict(sorted(Counter(depts.get(p, "Unknown") for (p,) in db.execute(
        "select product_id from golden_products")).items()))


def sample_add(db: Db, spec: str, seed: int = SAMPLE_SEED) -> dict:
    """PRD golden extension 2: add products per department to the existing golden set.
    Existing products are never removed, replaced or picked again; a repeat of the same request adds nothing."""
    wanted = parse_add(spec)
    tag = "add " + ",".join(f"{k}={v}" for k, v in sorted(wanted.items()))
    with db.lock:
        conn = db.conn
        before = golden_make_up(db)
        if conn.execute("select count(*) from golden_products where notes like %s",
                        (f"%{tag} %",)).fetchone()[0]:
            return {"before": before, "after": before, "added": {}, "shortfall": {}, "already": True, "tag": tag}
        depts = product_departments(db)
        golden = {r[0] for r in conn.execute("select product_id from golden_products")}
        info = {pid: (cat or " / ".join((crumb or [])[:3]), brand) for pid, cat, crumb, brand in conn.execute(
            "select product_id, spine_node_id, site_breadcrumb, brand from products")}
        names = {d.lower(): d for d in set(depts.values())}
        rng = random.Random(seed)
        added, shortfall, rows = {}, {}, []
        for key in sorted(wanted):
            dept = names.get(key)
            n = wanted[key]
            if dept is None:
                shortfall[key] = n
                continue
            cands = [(p, *info[p]) for p, d in depts.items() if d == dept and p not in golden]
            existing = [info[p] for p in golden if depts.get(p) == dept]
            picked = pick_spread(cands, existing, n, rng)
            added[dept] = len(picked)
            if len(picked) < n:
                shortfall[dept] = n - len(picked)
            rows += [(p, f"{tag} (seed {seed}), department {dept}") for p in picked]
            golden.update(picked)
        with conn.transaction():
            with conn.cursor() as cur:
                cur.executemany("insert into golden_products (product_id, notes) values (%s, %s) "
                                "on conflict (product_id) do nothing", rows)
        db.commit()
        after = golden_make_up(db)
    return {"before": before, "after": after, "added": added, "shortfall": shortfall, "already": False, "tag": tag}
