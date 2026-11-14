"""Load facets, facet_values, category_facets and brands.

Upserts on the natural keys, so a second run changes nothing. Rows that disappeared from the
files are removed, unless a tag or golden tag still points at them: then the seed stops, lists
those rows and changes nothing.
"""
from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import psycopg

from .taxonomy import Taxonomy

# PRD v3 section 6 counts, updated for the policy file of 13 Nov 2026 (adds colour_secondary as text_only and
# is_metallic as derived; retires the 8 type facets the Register already retires), so the report can explain
# any difference.
PRD_EXPECTED = {"active": 91, "retired": 19,
                "policy": {"text_only": 40, "judge": 33, "text_first": 9, "derived": 7, "not_tagged": 2}}
BRAND_TIERS = ("contemporary", "designer", "luxury", "ultra-luxury")
BRAND_COLUMNS = ("brand", "brand_tier", "is_local_designer")


class SeedError(RuntimeError):
    pass


@dataclass
class SeedReport:
    by_status: dict[str, int] = field(default_factory=dict)
    by_policy: dict[str, int] = field(default_factory=dict)          # active facets only
    by_on_missing: dict[str, int] = field(default_factory=dict)      # active facets only
    values_per_facet: dict[str, int] = field(default_factory=dict)
    facets_without_values: list[str] = field(default_factory=list)
    definitions: str = ""
    category_facet_rows: int = 0
    nodes_covered: int = 0
    nodes_by_level: dict[str, int] = field(default_factory=dict)
    nodes_without_facets_by_level: dict[str, int] = field(default_factory=dict)
    inherited_nodes_by_level: dict[str, int] = field(default_factory=dict)
    inherited_rows: int = 0
    no_facets_no_ancestor: list[tuple[str, str]] = field(default_factory=list)
    skipped_rows: list[tuple[str, str, str]] = field(default_factory=list)
    skipped_rows_file: str | None = None
    unlisted_facets: list[tuple[str, str]] = field(default_factory=list)
    removed: dict[str, int] = field(default_factory=dict)
    kept_edited: list[str] = field(default_factory=list)      # facets edited in the review app, left as they are
    product_nodes_without_facets: list[tuple[str, str, int]] = field(default_factory=list)
    products_without_node: int = 0
    brands: str = ""
    prd_notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def render(self, source: str) -> str:
        lines = [f"Seeded taxonomy from {source} + facet_policy.csv",
                 f"  facets: {self.by_status}",
                 f"  active by source policy: {self.by_policy}",
                 f"  active by on_missing: {self.by_on_missing}",
                 f"  values: {sum(self.values_per_facet.values())} across {len(self.values_per_facet)} facets",
                 f"  definitions: {self.definitions}",
                 f"  category_facets rows: {self.category_facet_rows}   nodes covered: {self.nodes_covered} "
                 f"{dict(sorted(self.nodes_by_level.items()))}",
                 f"  inherited from nearest ancestor: {sum(self.inherited_nodes_by_level.values())} nodes "
                 f"{dict(sorted(self.inherited_nodes_by_level.items()))}, {self.inherited_rows} rows",
                 f"  nodes with no facets (no ancestor has any): "
                 f"{dict(sorted(self.nodes_without_facets_by_level.items()))}",
                 f"  brands: {self.brands}"]
        lines.append("  values per facet:")
        items = sorted(self.values_per_facet.items())
        for i in range(0, len(items), 6):
            lines.append("    " + "  ".join(f"{k}={v}" for k, v in items[i:i + 6]))
        lines.append(f"  facets with no value list (number / text / descriptive cell): "
                     f"{', '.join(self.facets_without_values)}")
        if self.unlisted_facets:
            lines.append("  Register facets left out (no row in facet_policy.csv): "
                         + ", ".join(f"{fid} ({st})" for fid, st in self.unlisted_facets))
        if self.skipped_rows:
            per = Counter((f, why) for _, f, why in self.skipped_rows)
            lines.append(f"  Matrix_long rows skipped: {len(self.skipped_rows)} "
                         f"(every row listed in {self.skipped_rows_file})")
            lines += [f"    {f}: {n} rows, {why}" for (f, why), n in sorted(per.items())]
        else:
            lines.append("  Matrix_long rows skipped: none")
        if self.kept_edited:
            lines.append(f"  facets edited in the app, left as they are (rerun with --force to overwrite): "
                         f"{', '.join(sorted(self.kept_edited))}")
        if any(self.removed.values()):
            lines.append(f"  removed (no longer in the files): {self.removed}")
        if self.no_facets_no_ancestor:
            lines.append("  L3+ nodes still with no facets (no ancestor has any):")
            lines += [f"    {nid} {path}" for nid, path in self.no_facets_no_ancestor]
        if self.product_nodes_without_facets:
            lines.append("  nodes with products but no facets:")
            lines += [f"    {nid} {path}: {n} products" for nid, path, n in self.product_nodes_without_facets]
        else:
            lines.append("  every node with products has facets")
        if self.products_without_node:
            lines.append(f"  products with no spine node (not catalog-ready until placed): {self.products_without_node}")
        if self.prd_notes:
            lines.append("  compared with PRD v3 and the policy file of 13 Nov 2026:")
            lines += [f"    {n}" for n in self.prd_notes]
        lines += [f"  WARNING: {w}" for w in self.warnings]
        return "\n".join(lines)


INHERIT_SQL = """
with recursive own as (
    select distinct category_id from category_facets where inherited_from is null
), walk (node_id, ancestor, depth) as (
    select c.node_id, c.parent_id, 1 from categories c
    where c.parent_id is not null and c.node_id not in (select category_id from own)
    union all
    select w.node_id, c.parent_id, w.depth + 1 from walk w join categories c on c.node_id = w.ancestor
    where c.parent_id is not null and w.ancestor not in (select category_id from own)
), nearest as (
    select distinct on (node_id) node_id, ancestor from walk
    where ancestor in (select category_id from own)
    order by node_id, depth
)
insert into category_facets (category_id, facet_id, is_required, inherited_from)
select n.node_id, cf.facet_id, cf.is_required, n.ancestor
from nearest n join category_facets cf on cf.category_id = n.ancestor and cf.inherited_from is null
"""


def inherit_facets(cur, rep: "SeedReport") -> None:
    """Every node with no dictionary rows gets a copy of its nearest listed ancestor's rows
    (with the required flag), marked with inherited_from. Listed nodes are never changed."""
    cur.execute(INHERIT_SQL)
    rep.inherited_rows = cur.rowcount
    cur.execute("""select c.level, count(distinct cf.category_id) from category_facets cf
                   join categories c on c.node_id = cf.category_id
                   where cf.inherited_from is not null group by 1""")
    rep.inherited_nodes_by_level = dict(cur.fetchall())
    cur.execute("""select c.node_id, c.path from categories c where not exists
                   (select 1 from category_facets cf where cf.category_id = c.node_id)
                   and c.level not in ('L1', 'L2') order by c.path""")
    rep.no_facets_no_ancestor = cur.fetchall()


def check_nodes(conn: psycopg.Connection, tax: Taxonomy) -> list[str]:
    """Matrix_long node_ids that are not in categories."""
    with conn.cursor() as cur:
        cur.execute("select node_id from categories")
        cats = {r[0] for r in cur.fetchall()}
    return sorted({n for n, _, _ in tax.applicability if n not in cats})


def _blocked_by_tags(cur, gone_values: list[tuple[str, str]], gone_facets: list[str]) -> list[tuple]:
    out: list[tuple] = []
    if gone_values:
        cur.execute(
            """with gone(facet_id, value) as (select * from unnest(%s::text[], %s::text[]))
               select 'product_facet_tags', t.product_id, t.tagging_run_id, fv.facet_id, fv.value
                 from product_facet_tags t join facet_values fv using (facet_value_id)
                 join gone g on g.facet_id = fv.facet_id and g.value = fv.value
               union all
               select 'golden_tags', t.product_id, null, fv.facet_id, fv.value
                 from golden_tags t join facet_values fv using (facet_value_id)
                 join gone g on g.facet_id = fv.facet_id and g.value = fv.value""",
            ([f for f, _ in gone_values], [v for _, v in gone_values]))
        out += cur.fetchall()
    if gone_facets:
        cur.execute("select 'product_facet_tags', product_id, tagging_run_id, facet_id, null "
                    "from product_facet_tags where facet_id = any(%s) union all "
                    "select 'golden_tags', product_id, null, facet_id, null "
                    "from golden_tags where facet_id = any(%s)", (gone_facets, gone_facets))
        out += cur.fetchall()
    return out


# ---------------------------------------------------------------- brands

def read_brands_file(path: Path) -> dict[str, tuple[str | None, bool]]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in BRAND_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise SeedError(f"{path.name} is missing columns: {missing}")
        out: dict[str, tuple[str | None, bool]] = {}
        for r in reader:
            brand = (r["brand"] or "").strip()
            if not brand:
                continue
            tier = (r["brand_tier"] or "").strip().lower() or None
            if tier is not None and tier not in BRAND_TIERS:
                raise SeedError(f"{path.name}: {brand} has brand_tier {tier!r}; use one of {BRAND_TIERS} or leave empty")
            out[brand] = (tier, (r["is_local_designer"] or "").strip().lower() in ("true", "yes", "1", "y"))
    return out


def seed_brands(cur, path: Path | None, rep: SeedReport) -> None:
    """One row per distinct brand in products. Loads tier / local flag from brands.csv when it exists,
    and (re)writes the file when it is missing or lacks a brand, keeping what a person entered."""
    cur.execute("insert into brands (brand) select distinct brand from products on conflict (brand) do nothing")
    filled: dict[str, tuple[str | None, bool]] = {}
    if path is not None and path.exists():
        filled = read_brands_file(path)
        cur.execute("select distinct brand from products")
        known = {r[0] for r in cur.fetchall()}
        unknown = sorted(set(filled) - known)
        if unknown:
            rep.warnings.append(f"{path.name}: {len(unknown)} brands are not in products and were ignored: "
                                f"{unknown[:10]}")
        rows = [(b, t, loc) for b, (t, loc) in filled.items() if b in known]
        cur.executemany("""update brands set brand_tier = %s, is_local_designer = %s
                           where brand = %s and (brand_tier is distinct from %s
                                                 or is_local_designer is distinct from %s)""",
                        [(t, loc, b, t, loc) for b, t, loc in rows])
    cur.execute("select brand, brand_tier, is_local_designer from brands order by brand")
    current = cur.fetchall()
    if path is not None and (not path.exists() or any(b not in filled for b, _, _ in current)):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(BRAND_COLUMNS)
            w.writerows([(b, t or "", "true" if loc else "false") for b, t, loc in current])
        rep.warnings.append(f"wrote {path.name} ({len(current)} brands); fill in brand_tier and is_local_designer")
    tiered = sum(1 for _, t, _ in current if t)
    rep.brands = f"{len(current)} rows, {tiered} with a tier, {sum(1 for *_, loc in current if loc)} local designers"


# ---------------------------------------------------------------- taxonomy

def seed_taxonomy(conn: psycopg.Connection, tax: Taxonomy, skip_missing_nodes: bool = False,
                  brands_file: Path | None = None, skipped_rows_file: Path | None = None,
                  commit: bool = True, force: bool = False) -> SeedReport:
    """`force`: also overwrite facets edited in the review app (Build PRD 3: the database is the only
    source for facet rules; the seed refuses to overwrite an app edit unless forced)."""
    rep = SeedReport(warnings=list(tax.warnings), unlisted_facets=list(tax.unlisted_facets),
                     facets_without_values=[f.facet_id for f in tax.facets if not f.values])
    missing_nodes = check_nodes(conn, tax)
    if missing_nodes and not skip_missing_nodes:
        raise SeedError(f"{len(missing_nodes)} Matrix_long node_ids are not in categories "
                        f"(pass --skip-missing-nodes to skip them): {missing_nodes}")
    missing = set(missing_nodes)
    rep.skipped_rows = list(tax.skipped_rows) + [(n, f, "node not in categories")
                                                 for n, f, _ in tax.applicability if n in missing]
    applicability = [a for a in tax.applicability if a[0] not in missing]

    with conn.cursor() as cur:
        cur.execute("select facet_id from facets where edited_in_app")
        edited = set() if force else {r[0] for r in cur.fetchall()}
        rep.kept_edited = sorted(edited & {f.facet_id for f in tax.facets})
        if edited:      # their rules, values (also any added in the app) and required flags stay as edited
            tax.facets = [f for f in tax.facets if f.facet_id not in edited]
        file_facets = {f.facet_id for f in tax.facets} | edited
        file_values = {(f.facet_id, v.value) for f in tax.facets for v in f.values}
        cur.execute("select facet_id, value from facet_values")
        gone_values = [fv for fv in cur.fetchall() if fv not in file_values and fv[0] not in edited]
        cur.execute("select facet_id from facets")
        gone_facets = [r[0] for r in cur.fetchall() if r[0] not in file_facets]
        blocked = _blocked_by_tags(cur, gone_values, gone_facets)
        if blocked:
            listing = "\n".join(f"  {t}: product {p} run {r} {f}={v}" for t, p, r, f, v in blocked[:50])
            raise SeedError(f"The files drop {len(blocked)} facet values or facets that tags still use; "
                            f"nothing was changed:\n{listing}")

        cur.executemany(
            """insert into facets (facet_id, name, facet_group, definition, definition_version, data_type, unit,
                                   cardinality, min_values, max_values, none_is_exclusive, kind, source_policy,
                                   on_missing, default_value, source_rule, unknown_policy, auto_threshold,
                                   status, sort_order)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               on conflict (facet_id) do update set
                 name=excluded.name, facet_group=excluded.facet_group, definition=excluded.definition,
                 definition_version=excluded.definition_version, data_type=excluded.data_type,
                 unit=excluded.unit, cardinality=excluded.cardinality, min_values=excluded.min_values,
                 max_values=excluded.max_values, none_is_exclusive=excluded.none_is_exclusive,
                 kind=excluded.kind, source_policy=excluded.source_policy, on_missing=excluded.on_missing,
                 default_value=excluded.default_value, source_rule=excluded.source_rule,
                 unknown_policy=excluded.unknown_policy, auto_threshold=excluded.auto_threshold,
                 status=excluded.status, sort_order=excluded.sort_order""",
            [(f.facet_id, f.name, f.facet_group, f.definition, f.definition_version, f.data_type, f.unit,
              f.cardinality, f.min_values, f.max_values, f.none_is_exclusive, f.kind, f.source_policy,
              f.on_missing, f.default_value, f.source_rule, f.unknown_policy, f.auto_threshold, f.status,
              f.sort_order) for f in tax.facets])

        cur.executemany(
            """insert into facet_values (facet_id, value, label, definition, definition_version, sort_order,
                                         is_exclusive)
               values (%s,%s,%s,%s,%s,%s,%s)
               on conflict (facet_id, value) do update set
                 label=excluded.label, definition=excluded.definition,
                 definition_version=excluded.definition_version, sort_order=excluded.sort_order,
                 is_exclusive=excluded.is_exclusive""",
            [(f.facet_id, v.value, v.label, v.definition, v.definition_version, v.sort_order, v.is_exclusive)
             for f in tax.facets for v in f.values])

        # category_facets: one row per Matrix_long row for an active facet (the sheet is already expanded).
        cur.execute("create temp table _cf (category_id text, facet_id text, is_required boolean) on commit drop")
        with cur.copy("copy _cf (category_id, facet_id, is_required) from stdin") as cp:
            for row in applicability:
                cp.write_row(row)
        # Inherited rows are rebuilt below, so clear them first: a node the dictionary now lists
        # then gets only its own rows.
        cur.execute("delete from category_facets where inherited_from is not null")
        cur.execute("""insert into category_facets (category_id, facet_id, is_required)
                       select category_id, facet_id, is_required from _cf
                       on conflict (category_id, facet_id) do update set is_required = excluded.is_required
                       where category_facets.is_required is distinct from excluded.is_required
                         and category_facets.facet_id <> all(%s)""", (list(edited),))
        cur.execute("""delete from category_facets cf where not exists
                       (select 1 from _cf where _cf.category_id = cf.category_id and _cf.facet_id = cf.facet_id)""")
        rep.removed["category_facets"] = cur.rowcount
        cur.execute("drop table _cf")
        inherit_facets(cur, rep)

        cur.execute("delete from facet_values where (facet_id, value) in "
                    "(select * from unnest(%s::text[], %s::text[]))",
                    ([f for f, _ in gone_values], [v for _, v in gone_values]))
        rep.removed["facet_values"] = cur.rowcount
        cur.execute("delete from facets where facet_id = any(%s)", (gone_facets,))
        rep.removed["facets"] = cur.rowcount

        seed_brands(cur, brands_file, rep)

        # ---- report
        cur.execute("select status, count(*) from facets group by 1")
        rep.by_status = dict(cur.fetchall())
        cur.execute("select source_policy, count(*) from facets where status = 'active' group by 1")
        rep.by_policy = dict(cur.fetchall())
        cur.execute("select on_missing, count(*) from facets where status = 'active' group by 1")
        rep.by_on_missing = dict(cur.fetchall())
        cur.execute("select facet_id, count(*) from facet_values group by 1")
        rep.values_per_facet = dict(cur.fetchall())
        cur.execute("select count(*), count(distinct category_id) from category_facets")
        rep.category_facet_rows, rep.nodes_covered = cur.fetchone()
        cur.execute("""select c.level,
                              count(*) filter (where exists (select 1 from category_facets cf
                                                             where cf.category_id = c.node_id)),
                              count(*) filter (where not exists (select 1 from category_facets cf
                                                                 where cf.category_id = c.node_id))
                       from categories c group by 1""")
        for level, covered, uncovered in cur.fetchall():
            if covered:
                rep.nodes_by_level[level] = covered
            if uncovered:
                rep.nodes_without_facets_by_level[level] = uncovered
        cur.execute("""select p.spine_node_id, c.path, count(*) from products p
                       join categories c on c.node_id = p.spine_node_id
                       where not exists (select 1 from category_facets cf where cf.category_id = p.spine_node_id)
                       group by 1, 2 order by 1""")
        rep.product_nodes_without_facets = cur.fetchall()
        cur.execute("select count(*) from products where spine_node_id is null")
        rep.products_without_node = cur.fetchone()[0]

    if tax.definitions_loaded:
        rep.definitions = (f"{tax.definitions_loaded['facets']} facets, {tax.definitions_loaded['values']} values, "
                           f"version {tax.definitions_version}; unmatched rows: {len(tax.definitions_unmatched)}")
        if tax.definitions_unmatched:
            rep.warnings.append("definitions with no matching facet or value: "
                                + ", ".join(f"{f}/{v or '-'}" for f, v in tax.definitions_unmatched[:30]))
    else:
        rep.definitions = "none loaded (facet_definitions.csv not supplied yet)"
    if skipped_rows_file is not None and rep.skipped_rows:
        skipped_rows_file.parent.mkdir(parents=True, exist_ok=True)
        with skipped_rows_file.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(("node_id", "facet_id", "reason"))
            w.writerows(rep.skipped_rows)
        rep.skipped_rows_file = str(skipped_rows_file)
    rep.prd_notes = _compare_with_prd(rep)
    if commit:
        conn.commit()
    return rep


def _compare_with_prd(rep: SeedReport) -> list[str]:
    notes: list[str] = []
    for key in ("active", "retired"):
        got = rep.by_status.get(key, 0)
        if got != PRD_EXPECTED[key]:
            notes.append(f"{key} facets {got}, expected {PRD_EXPECTED[key]}")
    for policy, want in PRD_EXPECTED["policy"].items():
        got = rep.by_policy.get(policy, 0)
        if got != want:
            notes.append(f"{policy}: {got}, expected {want}")
    if rep.unlisted_facets:
        notes.append("left out because facet_policy.csv has no row for them: "
                     + ", ".join(fid for fid, _ in rep.unlisted_facets))
    if not notes:
        notes.append("matches: 91 active, 19 retired; 40 text_only, 33 judge, 9 text_first, 7 derived, 2 not_tagged")
    return notes
