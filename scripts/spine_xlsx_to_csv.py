"""Build a provisional spine nodes CSV from Main_ounass_new_spine_product_counts_v5.xlsx.

Stopgap until the real `ounass_spine_v5_nodes.csv` is supplied. The workbook has
L3 rows (sheet "Spine") and L4 rows (sheet "L4") only, so this script:
  - creates L1 (audience) and L2 (department) nodes from the ID prefixes
    (W101 -> parent W1), named from the Audience/Department columns;
  - has no L5 level and no definitions (left empty);
  - uses "Old CSV classes merged here" as aliases;
  - flags the holding nodes named in the PRD in `review_flag`.

Usage: python scripts/spine_xlsx_to_csv.py <workbook.xlsx> <out.csv>
"""
from __future__ import annotations

import csv
import re
import sys

import openpyxl

HOLDING = {"heels", "other shoes", "other accessories", "other bags"}
FIELDS = ["node_id", "parent_id", "level", "audience", "department", "name", "slug", "path",
          "is_leaf", "definition", "aliases", "review_flag", "source_count", "v5_status"]


def slugify(s: str) -> str:
    s = s.lower().replace("&", "and")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def main(xlsx: str, out: str) -> None:
    wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
    spine = list(wb["Spine"].iter_rows(values_only=True))
    l4 = list(wb["L4"].iter_rows(values_only=True))
    sh, lh = spine[0], l4[0]
    S = [dict(zip(sh, r)) for r in spine[1:] if r[0]]
    L = [dict(zip(lh, r)) for r in l4[1:] if r[0]]

    rows: dict[str, dict] = {}
    for r in S:
        l2_id = r["parent_id"]
        l1_id = l2_id[:2]
        aud, dept = r["Audience"], r["Department"]
        rows.setdefault(l1_id, dict(node_id=l1_id, parent_id="", level="L1", audience=aud, department="",
                                    name=aud, path=aud, is_leaf="FALSE"))
        rows.setdefault(l2_id, dict(node_id=l2_id, parent_id=l1_id, level="L2", audience=aud, department=dept,
                                    name=dept, path=f"{aud} / {dept}", is_leaf="FALSE"))
        name = r["Category"]
        rows[r["category_id"]] = dict(
            node_id=r["category_id"], parent_id=l2_id, level="L3", audience=aud, department=dept, name=name,
            path=f"{aud} / {dept} / {name}", is_leaf=str(r["is_leaf"]).upper(),
            aliases=r.get("Old CSV classes merged here") or "",
            review_flag="holding" if name.lower() in HOLDING else "",
            source_count=r.get("Illustrative source count") or "", v5_status=r.get("v5 status") or "")
    for r in L:
        parent = rows[r["category_id"]]
        name = r["Sub-category"]
        rows[r["subcategory_id"]] = dict(
            node_id=r["subcategory_id"], parent_id=r["category_id"], level="L4", audience=r["Audience"],
            department=r["Department"], name=name, path=f"{parent['path']} / {name}", is_leaf="TRUE",
            review_flag="holding" if name.lower() in HOLDING else "",
            source_count=r.get("Illustrative source count") or "", v5_status=r.get("v5 status") or "")

    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for row in rows.values():
            row.setdefault("slug", slugify(row["name"]))
            w.writerow({k: row.get(k, "") for k in FIELDS})
    levels = {}
    for row in rows.values():
        levels[row["level"]] = levels.get(row["level"], 0) + 1
    print(f"wrote {len(rows)} nodes to {out}: {levels}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
