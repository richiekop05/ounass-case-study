"""The golden answer key as sheets (13 Nov 2026).

- Master answer key: `export_master` writes the golden tables exactly as they are; `import_master` puts a
  (possibly edited) master back. Corrections made directly in the database live in the master from the
  moment it is exported, so re-importing it can never undo them.
- Disagreement review: `export_disagreements` lists every golden answer a tag run disagrees with;
  `import_disagreements` applies the reviewer's verdicts: "tagger right" takes the tagger's value,
  "neither" takes corrected_value, "key right" (or blank) changes nothing.

Nothing here calls a model. Only golden_products.category_id and golden_tags are written.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from .evaluate import PredRow, _key, pred_answer
from .facets import MODEL_POLICIES, FacetSpec, facets_for_category
from .golden import parse_answer
from .products import ASSIGNABLE_SQL

MASTER_PRODUCTS = ["product_id", "title", "brand", "product_url", "category_id", "category_path", "labelled_by",
                   "notes"]
MASTER_LABELS = ["product_id", "title", "facet_id", "state", "values", "allowed_values"]
DISAGREE_HEADERS = ["product_id", "product_name", "product_url", "first_image_url", "facet", "golden_value",
                    "tagger_value", "confidence", "status", "quote", "reason", "verdict", "corrected_value"]
VERDICTS = ("key right", "tagger right", "neither")
HEAD = PatternFill("solid", fgColor="DDDDDD")
EDIT = PatternFill("solid", fgColor="FFF2CC")


def _style(ws, widths: dict[str, int], editable: tuple[str, ...] = ()):
    heads = [c.value for c in ws[1]]
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = EDIT if c.value in editable else HEAD
    for i, h in enumerate(heads, start=1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = widths.get(h, 16)
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = ws.dimensions


def _answer_text(state: str, labels: list[str]) -> str:
    return "; ".join(labels) if state == "value" else state.replace("_", " ")


def _allowed(spec: FacetSpec | None) -> str:
    if spec is None:
        return ""
    if spec.has_value_list:
        return "; ".join(v.label for v in spec.values)
    return f"a positive number{f' ({spec.unit})' if spec.unit else ''}" if spec.data_type == "number" else "free text"


def _golden_answers(conn, pids=None) -> dict[tuple[str, str], tuple[str, list[str], list]]:
    """(product, facet) -> (state, labels, keys) from golden_tags."""
    sql = """select t.product_id, t.facet_id, t.status, t.facet_value_id, t.value_number, t.value_text, fv.label
             from golden_tags t left join facet_values fv using (facet_value_id)"""
    rows = conn.execute(sql + (" where t.product_id = any(%s)" if pids is not None else "") +
                        " order by t.product_id, t.facet_id, fv.sort_order nulls last",
                        (list(pids),) if pids is not None else ()).fetchall()
    out: dict = {}
    for p, f, st, fv, num, txt, label in rows:
        state, labels, keys = out.get((p, f), (st, [], []))
        k = _key(fv, num, txt)
        if k is not None:
            state = "value"
            labels.append(label if label is not None else (f"{float(num):g}" if num is not None else txt))
            keys.append(k)
        out[(p, f)] = (state, labels, keys)
    return out


# ------------------------------------------------------------------ master answer key

def export_master(conn, path: Path) -> dict:
    products = conn.execute(
        """select g.product_id, p.name, p.brand, p.url, g.category_id, c.path, g.labelled_by, g.notes
           from golden_products g join products p using (product_id) left join categories c on c.node_id = g.category_id
           order by g.product_id""").fetchall()
    answers = _golden_answers(conn)
    names = {r[0]: r[1] for r in products}
    cats = {r[0]: r[4] for r in products}
    specs_cache: dict[str, dict[str, FacetSpec]] = {}
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Products"
    ws.append(MASTER_PRODUCTS)
    for r in products:
        ws.append(list(r))
    _style(ws, {"title": 40, "product_url": 40, "category_path": 50, "notes": 40})
    ws2 = wb.create_sheet("Labels")
    ws2.append(MASTER_LABELS)
    for (p, f), (state, labels, _) in sorted(answers.items()):
        cat = cats.get(p)
        if cat and cat not in specs_cache:
            specs_cache[cat] = facets_for_category(conn, cat)
        ws2.append([p, names.get(p), f, state.replace("_", " "), "; ".join(labels) if state == "value" else "",
                    _allowed(specs_cache.get(cat, {}).get(f))])
    _style(ws2, {"title": 40, "facet_id": 22, "values": 30, "allowed_values": 60}, editable=("state", "values"))
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return {"products": len(products), "labelled_products": sum(1 for r in products if r[4]),
            "answers": len(answers), "path": str(path)}


def is_master_sheet(path: Path) -> bool:
    wb = openpyxl.load_workbook(path, read_only=True)
    return "Labels" in wb.sheetnames and "Products" in wb.sheetnames


@dataclass
class KeyImport:
    categories_saved: int = 0
    answers_saved: int = 0
    rows_saved: int = 0
    unchanged: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"categories saved: {self.categories_saved}; answers saved: {self.answers_saved} "
                 f"({self.rows_saved} rows); unchanged: {self.unchanged}; problems: {len(self.problems)}"]
        lines += [f"  skipped: {n} rows ({why})" for why, n in sorted(self.skipped.items())]
        lines += [f"  problem: {p}" for p in self.problems]
        return "\n".join(lines)


def _write_answer(conn, pid: str, fid: str, state: str, values: list) -> int:
    conn.execute("delete from golden_tags where product_id = %s and facet_id = %s", (pid, fid))
    if state != "value":
        conn.execute("insert into golden_tags (product_id, facet_id, status) values (%s, %s, %s)", (pid, fid, state))
        return 1
    for v in values:
        conn.execute("""insert into golden_tags (product_id, facet_id, status, facet_value_id, value_number, value_text)
                        values (%s, %s, 'value', %s, %s, %s)""",
                     (pid, fid, getattr(v, "facet_value_id", None), v if isinstance(v, float) else None,
                      v if isinstance(v, str) else None))
    return len(values)


def _specs(conn, cache, cat) -> dict[str, FacetSpec]:
    if cat not in cache:
        cache[cat] = {k: s for k, s in facets_for_category(conn, cat).items() if s.source_policy in MODEL_POLICIES}
    return cache[cat]


def _rows(wb, sheet):
    it = wb[sheet].iter_rows(values_only=True)
    head = [str(h or "").strip() for h in next(it)]
    for i, r in enumerate(it, start=2):
        if any(v not in (None, "") for v in r):
            yield i, dict(zip(head, r))


def _txt(v) -> str:
    return "" if v is None else str(v).strip()


def import_master(db, path: Path) -> KeyImport:
    """Make the golden tables match the master sheet for every product and answer it lists."""
    rep = KeyImport()
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    with db.lock:
        conn = db.conn
        golden = dict(conn.execute("select product_id, category_id from golden_products"))
        valid = {r[0] for r in conn.execute(ASSIGNABLE_SQL)}
        current = _golden_answers(conn)
        retired = {r[0] for r in conn.execute("select facet_id from facets where status <> 'active'")}
        cats: dict[str, str] = {}
        cache: dict = {}
        with conn.transaction():
            for i, r in _rows(wb, "Products"):
                pid, cat = _txt(r.get("product_id")), _txt(r.get("category_id"))
                if pid not in golden:
                    rep.problems.append(f"Products row {i}: {pid} is not in golden_products")
                    continue
                if not cat:
                    continue
                if cat not in valid:
                    rep.problems.append(f"Products row {i} ({pid}): {cat} is not a category a product can sit on")
                    continue
                cats[pid] = cat
                if golden[pid] != cat:
                    conn.execute("update golden_products set category_id = %s where product_id = %s", (cat, pid))
                    rep.categories_saved += 1
            for i, r in _rows(wb, "Labels"):
                pid, fid = _txt(r.get("product_id")), _txt(r.get("facet_id"))
                if fid in retired:         # archived answers stay as they are in the database
                    rep.skipped["facet retired"] = rep.skipped.get("facet retired", 0) + 1
                    continue
                cat = cats.get(pid) or golden.get(pid)
                if not cat:
                    rep.skipped["no category for the product"] = rep.skipped.get("no category for the product", 0) + 1
                    continue
                spec = _specs(conn, cache, cat).get(fid)
                if spec is None:
                    rep.problems.append(f"Labels row {i} ({pid}, {fid}): facet does not apply to category {cat}")
                    continue
                state, values, err = parse_answer(spec, r.get("state"), r.get("values"))
                if err:
                    rep.problems.append(f"Labels row {i} ({pid}, {fid}): {err}")
                    continue
                cur = current.get((pid, fid))
                new_keys = sorted(_key(getattr(v, "facet_value_id", None), v if isinstance(v, float) else None,
                                       v if isinstance(v, str) else None) for v in values) if state == "value" else []
                if cur and cur[0] == state and sorted(cur[2]) == new_keys:
                    rep.unchanged += 1
                    continue
                rep.rows_saved += _write_answer(conn, pid, fid, state, values)
                rep.answers_saved += 1
        db.commit()
    return rep


# ------------------------------------------------------------------ disagreements

CATEGORY_HEADERS = ["product_id", "product_name", "product_url", "first_image_url", "golden_category_id",
                    "golden_category_path", "tagger_category_id", "tagger_category_path", "runner_up_path",
                    "confidence", "status", "reason", "verdict", "corrected_category_id"]


def export_disagreements(conn, run_id: int, path: Path, holdout: bool | None = None) -> dict:
    """Every golden answer the run disagrees with, for products whose category matched (as scored), plus a
    Categories sheet for products whose category differs. holdout=True: holdout products only; False: the
    reviewed products only; None: all."""
    golden_cat = dict(conn.execute("select product_id, category_id from golden_products" +
                                   ("" if holdout is None else f" where is_holdout = {str(bool(holdout)).lower()}")))
    tagged = [(p, c) for p, c in conn.execute("""select product_id, category_id from tagging_run_products
                             where tagging_run_id = %s and status = 'done'""", (run_id,)) if p in golden_cat]
    eligible = [p for p, c in tagged if golden_cat.get(p) and golden_cat[p] == c]
    wrong_category = sum(1 for p, c in tagged if golden_cat.get(p) and golden_cat[p] != c)
    active = {r[0] for r in conn.execute("select facet_id from facets where status = 'active'")}
    gold = {k: v for k, v in _golden_answers(conn, eligible).items() if k[1] in active}   # retired: archived
    labels = {int(i): l for i, l in conn.execute("select facet_value_id, label from facet_values")}
    pred: dict = defaultdict(list)
    detail: dict = defaultdict(lambda: {"conf": None, "quote": None, "reason": None, "status": set()})
    for p, f, st, fv, num, txt, conf, ev, quote, reason in conn.execute(
            """select product_id, facet_id, status, facet_value_id, value_number, value_text, confidence, evidence,
                      evidence_quote, reason from product_facet_tags where tagging_run_id = %s
                 and product_id = any(%s)""", (run_id, eligible)):
        k = _key(fv, num, txt)
        pred[(p, f)].append(PredRow(st, k, float(conf) if conf is not None else None, ev))
        d = detail[(p, f)]
        if k is not None and conf is not None:
            d["conf"] = max(d["conf"] or 0, float(conf))
        d["quote"] = d["quote"] or quote
        d["reason"] = d["reason"] or reason
        d["status"].add(st)
    info = {p: r for p, *r in conn.execute(
        """select p.product_id, p.name, p.url,
                  (select source_url from product_images i where i.product_id = p.product_id
                   order by position limit 1)
           from products p where p.product_id = any(%s)""", (eligible,))}
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Disagreements"
    ws.append(DISAGREE_HEADERS)
    n = 0
    for (p, f), (gstate, glabels, gkeys) in sorted(gold.items()):
        state, answer = pred_answer(pred.get((p, f), []))
        G = set(gkeys) if gstate == "value" else set()
        if (state == "value" and gstate == "value" and answer == G) or (gstate != "value" and state == gstate):
            continue
        tagger = _answer_text(state, sorted(labels.get(k, str(k)) if isinstance(k, int) else
                                            (f"{k:g}" if isinstance(k, float) else str(k)) for k in answer))
        d = detail.get((p, f), {"conf": None, "quote": None, "reason": None, "status": set()})
        status = "/".join(sorted(s for s in d["status"] if s in ("auto", "review"))) or "-"
        name, url, image = info[p]
        ws.append([p, name, url, image, f, _answer_text(gstate, glabels), tagger, d["conf"], status,
                   d["quote"], d["reason"], None, None])
        n += 1
    dv = DataValidation(type="list", formula1='"' + ",".join(VERDICTS) + '"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"L2:L{max(n + 1, 2)}")
    _style(ws, {"product_name": 36, "product_url": 30, "first_image_url": 30, "facet": 18, "golden_value": 24,
                "tagger_value": 24, "quote": 36, "reason": 50, "verdict": 14, "corrected_value": 24},
           editable=("verdict", "corrected_value"))
    # categories: the tagger's category run for this tag run (or the run itself for a categorise run)
    cat_run = conn.execute("""select coalesce(max(category_run_id), %s) from tagging_run_products
                              where tagging_run_id = %s""", (run_id, run_id)).fetchone()[0]
    path_of = dict(conn.execute("select node_id, path from categories"))
    short = lambda n: (path_of.get(n) or "").split(" / ", 1)[-1] if n else None
    wc = wb.create_sheet("Categories")
    wc.append(CATEGORY_HEADERS)
    nc = 0
    for p, tcat, ru, conf, st in conn.execute(
            """select product_id, category_id, category_runner_up_id, category_confidence, category_status
               from tagging_run_products where tagging_run_id = %s and status = 'done'
               order by product_id""", (cat_run,)):
        g = golden_cat.get(p)
        if not g or g == tcat:
            continue
        name, url, image = conn.execute(
            """select p.name, p.url, (select source_url from product_images i where i.product_id = p.product_id
                                      order by position limit 1) from products p where p.product_id = %s""",
            (p,)).fetchone()
        reason = conn.execute("""select category_reason from catalog_products where product_id = %s""",
                              (p,)).fetchone()
        wc.append([p, name, url, image, g, short(g), tcat, short(tcat), short(ru),
                   float(conf) if conf is not None else None, st, reason[0] if reason else None, None, None])
        nc += 1
    dvc = DataValidation(type="list", formula1='"' + ",".join(VERDICTS) + '"', allow_blank=True)
    wc.add_data_validation(dvc)
    dvc.add(f"M2:M{max(nc + 1, 2)}")
    _style(wc, {"product_name": 36, "product_url": 30, "first_image_url": 30, "golden_category_path": 40,
                "tagger_category_path": 40, "runner_up_path": 36, "reason": 50, "verdict": 14,
                "corrected_category_id": 22}, editable=("verdict", "corrected_category_id"))
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return {"run_id": run_id, "rows": n, "category_rows": nc, "products_scored": len(eligible),
            "excluded_wrong_category": wrong_category, "path": str(path)}


def _parse_text_answer(spec: FacetSpec, text) -> tuple[str, list, str | None]:
    t = _txt(text)
    low = t.lower()
    if low in ("unknown", "not applicable", "not_applicable", "n/a"):
        return parse_answer(spec, low, None)
    return parse_answer(spec, "value", t)


def import_disagreements(db, path: Path) -> KeyImport:
    rep = KeyImport()
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    if "Disagreements" not in wb.sheetnames:
        raise ValueError(f"{path.name} has no Disagreements sheet")
    with db.lock:
        conn = db.conn
        golden = dict(conn.execute("select product_id, category_id from golden_products"))
        valid = {r[0] for r in conn.execute(ASSIGNABLE_SQL)}
        cache: dict = {}
        with conn.transaction():
            if "Categories" in wb.sheetnames:
                for i, r in _rows(wb, "Categories"):
                    pid = _txt(r.get("product_id"))
                    verdict = _txt(r.get("verdict")).lower()
                    if verdict in ("", "key right"):
                        k = f"category: {verdict or 'no verdict'}"
                        rep.skipped[k] = rep.skipped.get(k, 0) + 1
                        continue
                    if verdict not in VERDICTS or pid not in golden:
                        rep.problems.append(f"Categories row {i} ({pid}): verdict {verdict!r} or product not valid")
                        continue
                    cat = _txt(r.get("tagger_category_id") if verdict == "tagger right"
                               else r.get("corrected_category_id"))
                    if cat not in valid:
                        rep.problems.append(f"Categories row {i} ({pid}): {cat!r} is not a category a product "
                                            f"can sit on")
                        continue
                    conn.execute("update golden_products set category_id = %s where product_id = %s", (cat, pid))
                    # answers for facets the new category does not have are dropped
                    conn.execute("""delete from golden_tags t where t.product_id = %s and not exists
                                    (select 1 from category_facets cf where cf.category_id = %s
                                     and cf.facet_id = t.facet_id)""", (pid, cat))
                    golden[pid] = cat
                    rep.categories_saved += 1
            for i, r in _rows(wb, "Disagreements"):
                pid, fid = _txt(r.get("product_id")), _txt(r.get("facet"))
                verdict = _txt(r.get("verdict")).lower()
                if verdict in ("", "key right"):
                    rep.skipped[verdict or "no verdict"] = rep.skipped.get(verdict or "no verdict", 0) + 1
                    continue
                if verdict not in VERDICTS:
                    rep.problems.append(f"row {i} ({pid}, {fid}): verdict {verdict!r} is not one of {', '.join(VERDICTS)}")
                    continue
                cat = golden.get(pid)
                spec = _specs(conn, cache, cat).get(fid) if cat else None
                if spec is None:
                    rep.problems.append(f"row {i} ({pid}, {fid}): not a golden product, or the facet does not apply")
                    continue
                source = r.get("tagger_value") if verdict == "tagger right" else r.get("corrected_value")
                if verdict == "neither" and not _txt(source):
                    rep.problems.append(f"row {i} ({pid}, {fid}): verdict neither without a corrected_value")
                    continue
                state, values, err = _parse_text_answer(spec, source)
                if err:
                    rep.problems.append(f"row {i} ({pid}, {fid}): {err}")
                    continue
                rep.rows_saved += _write_answer(conn, pid, fid, state, values)
                rep.answers_saved += 1
        db.commit()
    return rep


__all__ = ["export_master", "import_master", "is_master_sheet", "export_disagreements", "import_disagreements"]
