"""`tagger review export / import`: review a tag run in a spreadsheet instead of the app (owner, 14 Nov 2026).

Export: review/review_run<ID>.xlsx for the products of a tag run (golden products left out), with two sheets:
Categories (each product whose category is flagged or held) and Facets (each facet answer in review, by the
same rule as the app). Accepted tags are not in the sheet.

Import: every verdict is written through the review app's own functions (`app.review`), so the result is
exactly what reviewing by hand writes: the same review run, the tagger's rows untouched, outcomes recorded,
and the product approved and published once every row for it has a verdict (`approved_via = 'sheet'`).
A blank verdict leaves that answer in review and the product unapproved; the verdicts given are saved as
decisions, as the app's Save does. A changed category (or an approved held category) re-tags that product.
A corrected value that is not an allowed value is rejected and listed.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

CAT_COLS = ["product_id", "name", "product_url", "image_url", "tagger_category", "category_id", "runner_up",
            "confidence", "status", "reason", "verdict", "corrected_category_id"]
FACET_COLS = ["product_id", "name", "product_url", "image_url", "facet", "facet_id", "tagger_value", "confidence",
              "evidence", "quote", "reason", "allowed_values", "verdict", "corrected_value"]
CAT_VERDICTS = ("approve", "change")
FACET_VERDICTS = ("approve", "change", "unknown")
EVIDENCE = {"text": "text", "image": "photo", "both": "text and photo"}


def _plain(reason: str | None) -> str | None:
    if not reason:
        return None
    r = re.sub(r"^derive:[a-z_]+:\s*", "", reason)
    r = re.sub(r";?\s*quote not found in text", "", r).strip(" ;")
    return r[:1].upper() + r[1:] if r else None


# ------------------------------------------------------------------ export

def scope(conn, run_id: int) -> tuple[list[str], list[str]]:
    """(products tagged in the run, held products of its categorise runs), golden products left out."""
    stage = conn.execute("select stage from tagging_runs where tagging_run_id = %s", (run_id,)).fetchone()
    if not stage or stage[0] != "tag":
        raise ValueError(f"run {run_id} is not a tag run")
    tagged = [r[0] for r in conn.execute(
        """select p.product_id from tagging_run_products p
            where p.tagging_run_id = %s and p.status = 'done'
              and not exists (select 1 from golden_products g where g.product_id = p.product_id)
            order by p.product_id""", (run_id,))]
    held = [r[0] for r in conn.execute(
        """select cp.product_id from catalog_products cp
            where cp.category_status = 'review' and cp.ai_run_id is null and cp.category_reviewed_at is null
              and cp.category_run_id in (select distinct category_run_id from tagging_run_products
                                          where tagging_run_id = %s and category_run_id is not null)
              and not exists (select 1 from golden_products g where g.product_id = cp.product_id)
            order by cp.product_id""", (run_id,))]
    return tagged, held


def rows_for(conn, run_id: int) -> tuple[list[list], list[list]]:
    tagged, held = scope(conn, run_id)
    pids = tagged + held
    info = {r[0]: r[1:] for r in conn.execute(
        """select p.product_id, p.name, p.url,
                  (select i.source_url from product_images i where i.product_id = p.product_id
                    order by i.position limit 1)
             from products p where p.product_id = any(%s)""", (pids,))}
    cats = []
    for pid, cpath, cid, rpath, conf, status, reason in conn.execute(
            """select cp.product_id, c.path, cp.category_id, r.path, cp.category_confidence, cp.category_status,
                      cp.category_reason
                 from catalog_products cp join categories c on c.node_id = cp.category_id
                 left join categories r on r.node_id = cp.category_runner_up_id
                 join v_product_review_state s on s.product_id = cp.product_id
                where cp.product_id = any(%s) and not cp.is_published
                  and (s.category_flagged or (cp.category_status = 'review' and cp.ai_run_id is null))
                order by cp.product_id""", (pids,)):
        name, url, img = info.get(pid, (None, None, None))
        cats.append([pid, name, url, img, cpath, cid, rpath, float(conf) if conf is not None else None,
                     "held (not tagged)" if status == "review" else "flagged (tagged)", _plain(reason), None, None])
    allowed = defaultdict(list)
    for fid, label in conn.execute("""select facet_id, label from facet_values where is_active
                                       order by facet_id, sort_order nulls last, facet_value_id"""):
        allowed[fid].append(label)
    facets = []
    for pid, fid, fname, ftype in conn.execute(
            """select g.product_id, g.facet_id, f.name, f.data_type
                 from v_tag_flags g join catalog_products cp on cp.product_id = g.product_id
                                                            and cp.ai_run_id = g.tagging_run_id
                 join facets f on f.facet_id = g.facet_id
                where g.flagged and g.tagging_run_id = %s and g.product_id = any(%s) and not cp.is_published
                order by g.product_id, f.sort_order nulls last, f.facet_id""", (run_id, tagged)):
        rows = conn.execute(
            """select fv.label, t.value_number, t.value_text, t.confidence, t.evidence, t.evidence_quote, t.reason,
                      t.status
                 from product_facet_tags t left join facet_values fv using (facet_value_id)
                where t.product_id = %s and t.tagging_run_id = %s and t.facet_id = %s
                order by t.confidence desc nulls last""", (pid, run_id, fid)).fetchall()
        vals = [r[0] if r[0] is not None else (f"{float(r[1]):g}" if r[1] is not None else r[2])
                for r in rows if any(x is not None for x in r[:3])]
        confs = [float(r[3]) for r in rows if r[3] is not None and any(x is not None for x in r[:3])]
        ev = sorted({EVIDENCE.get(r[4], r[4]) for r in rows if r[4] and r[4] != "rule"})
        quotes = [r[5] for r in rows if r[5]]
        reason = next((_plain(r[6]) for r in rows if r[6]), None)
        name, url, img = info.get(pid, (None, None, None))
        allowed_text = " | ".join(allowed[fid]) if ftype in ("enum", "ordinal", "boolean") else \
            ("a positive number" if ftype == "number" else "free text")
        facets.append([pid, name, url, img, fname, fid, "; ".join(vals) if vals else "unknown",
                       min(confs) if confs else None, ", ".join(ev) or None, " / ".join(quotes) or None, reason,
                       allowed_text, None, None])
    return cats, facets


def export(conn, run_id: int, out_dir: Path) -> dict:
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation
    cats, facets = rows_for(conn, run_id)
    wb = openpyxl.Workbook()
    specs = [("Categories", CAT_COLS, cats, CAT_VERDICTS,
              {"name": 34, "product_url": 28, "image_url": 28, "tagger_category": 44, "runner_up": 44,
               "reason": 60, "corrected_category_id": 22}),
             ("Facets", FACET_COLS, facets, FACET_VERDICTS,
              {"name": 34, "product_url": 28, "image_url": 28, "facet": 20, "tagger_value": 24, "quote": 40,
               "reason": 60, "allowed_values": 60, "corrected_value": 24})]
    fill = PatternFill("solid", fgColor="FFF4D6")       # the columns to fill in
    for i, (title, cols, rows, verdicts, widths) in enumerate(specs):
        ws = wb.active if i == 0 else wb.create_sheet()
        ws.title = title
        ws.append(cols)
        for c in ws[1]:
            c.font = Font(bold=True)
        for r in rows:
            ws.append(r)
        n = len(rows) + 1
        for col_i, name in enumerate(cols, start=1):
            letter = ws.cell(row=1, column=col_i).column_letter
            ws.column_dimensions[letter].width = widths.get(name, 14)
            if name in ("verdict", "corrected_category_id", "corrected_value"):
                for row in range(1, n + 1):
                    ws.cell(row=row, column=col_i).fill = fill
            if name in ("reason", "quote", "allowed_values", "tagger_category", "runner_up", "name"):
                for row in range(2, n + 1):
                    ws.cell(row=row, column=col_i).alignment = Alignment(wrap_text=True, vertical="top")
            if name in ("product_url", "image_url"):
                for row in range(2, n + 1):
                    c = ws.cell(row=row, column=col_i)
                    if c.value:
                        c.hyperlink = c.value
            if name == "confidence":
                for row in range(2, n + 1):
                    ws.cell(row=row, column=col_i).number_format = "0.00"
        v_col = ws.cell(row=1, column=cols.index("verdict") + 1).column_letter
        dv = DataValidation(type="list", formula1='"' + ",".join(verdicts) + '"', allow_blank=True)
        ws.add_data_validation(dv)
        if n > 1:
            dv.add(f"{v_col}2:{v_col}{n}")
        ws.freeze_panes = "B2"
        ws.auto_filter.ref = ws.dimensions
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"review_run{run_id}.xlsx"
    if path.exists():
        raise FileExistsError(f"{path} exists; it may hold verdicts already. Move it away first.")
    wb.save(path)
    return {"path": str(path), "categories": len(cats), "facets": len(facets),
            "products": len({r[0] for r in cats} | {r[0] for r in facets})}


# ------------------------------------------------------------------ import

@dataclass
class ImportReport:
    approved: list[str] = field(default_factory=list)
    saved_partial: list[str] = field(default_factory=list)       # some verdicts blank: decisions saved, not approved
    retagged: list[str] = field(default_factory=list)
    already: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)            # rows refused (bad verdict or value)
    errors: list[str] = field(default_factory=list)              # products the app refused, with the reason
    cost_usd: float = 0.0

    def render(self) -> str:
        lines = [f"approved and published: {len(self.approved)}",
                 f"saved, not approved (a verdict blank or rejected): {len(self.saved_partial)}",
                 f"re-tagged after a category decision: {len(self.retagged)}"
                 + (f" (${self.cost_usd:.4f})" if self.retagged else ""),
                 f"already approved, skipped: {len(self.already)}",
                 f"rejected rows: {len(self.rejected)}"]
        lines += [f"  {r}" for r in self.rejected]
        lines.append(f"refused by the app: {len(self.errors)}")
        lines += [f"  {e}" for e in self.errors]
        return "\n".join(lines)


def _read(path: Path) -> tuple[list[dict], list[dict]]:
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)

    def sheet(name, need):
        if name not in wb.sheetnames:
            raise ValueError(f"{path.name} has no sheet '{name}'")
        ws = wb[name]
        head = [str(c.value).strip() if c.value is not None else "" for c in ws[1]]
        missing = [c for c in need if c not in head]
        if missing:
            raise ValueError(f"sheet '{name}' is missing columns: {', '.join(missing)}")
        out = []
        for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            d = dict(zip(head, row))
            if not d.get("product_id"):
                continue
            d["_row"] = i
            out.append(d)
        return out
    return (sheet("Categories", ["product_id", "verdict", "corrected_category_id"]),
            sheet("Facets", ["product_id", "facet_id", "verdict", "corrected_value"]))


def _norm(v) -> str:
    return "" if v is None else str(v).strip()


def _value_ids(conn, fid: str, text: str) -> tuple[list | None, str | None]:
    """The decision values for a corrected value: allowed value IDs for list facets (several separated by ';'),
    a number, or text. (None, why) when it is not allowed."""
    dtype = conn.execute("select data_type from facets where facet_id = %s", (fid,)).fetchone()
    if not dtype:
        return None, "unknown facet"
    dtype = dtype[0]
    if dtype in ("enum", "ordinal", "boolean"):
        allowed = {}
        for vid, value, label in conn.execute("""select facet_value_id, value, label from facet_values
                                                  where facet_id = %s and is_active""", (fid,)):
            allowed[label.strip().lower()] = vid
            allowed[value.strip().lower()] = vid
        ids, bad = [], []
        for part in [p.strip() for p in text.split(";") if p.strip()]:
            vid = allowed.get(part.lower())
            (ids if vid is not None else bad).append(vid if vid is not None else part)
        if bad or not ids:
            return None, f"not an allowed value: {', '.join(bad) or '(empty)'}"
        return ids, None
    if dtype == "number":
        try:
            x = float(text)
        except ValueError:
            return None, "not a number"
        return ([x], None) if x > 0 else (None, "not a positive number")
    return ([text], None) if text else (None, "empty")


def import_sheet(path: Path, retag: bool = True) -> ImportReport:
    from app.db import end, get_db
    from app.jobs import Job
    from app.review import ReviewError, approve, decide_category, save
    rep = ImportReport()
    cat_rows, facet_rows = _read(path)
    by_cat = {}
    for r in cat_rows:
        by_cat[_norm(r["product_id"])] = r
    by_facets: dict[str, list[dict]] = defaultdict(list)
    for r in facet_rows:
        by_facets[_norm(r["product_id"])].append(r)
    db = get_db()
    conn = db.conn
    for pid in sorted(set(by_cat) | set(by_facets)):
        state = conn.execute("select state, ai_run_id from v_product_review_state where product_id = %s",
                             (pid,)).fetchone()
        end(db)
        if not state:
            rep.errors.append(f"{pid}: not a categorised product")
            continue
        if state[0] == "approved":
            rep.already.append(pid)
            continue
        complete = True
        # ---- the category, first
        cr = by_cat.get(pid)
        if cr is not None:
            verdict = _norm(cr["verdict"]).lower()
            if not verdict:
                complete = False
            elif verdict not in CAT_VERDICTS:
                rep.rejected.append(f"Categories row {cr['_row']} ({pid}): verdict '{cr['verdict']}' is not "
                                    f"approve or change")
                complete = False
            else:
                new = _norm(cr["corrected_category_id"]) if verdict == "change" else None
                if verdict == "change" and not new:
                    rep.rejected.append(f"Categories row {cr['_row']} ({pid}): change needs corrected_category_id")
                    complete = False
                else:
                    try:
                        r = decide_category(pid, new)
                    except ReviewError as e:
                        rep.rejected.append(f"Categories row {cr['_row']} ({pid}): {e.message}")
                        complete = False
                        r = None
                    if r and r.get("retag"):
                        # the facet rows were for the old category (or there were none): tag again and leave the
                        # product for review, as the app does after a category decision
                        if retag:
                            from app import pipeline
                            job = Job(0, "sheet", pid)
                            try:
                                pipeline.tag(pid, job)
                                rep.retagged.append(pid)
                                rep.cost_usd += job.cost_usd
                            except Exception as e:  # noqa: BLE001
                                rep.errors.append(f"{pid}: re-tagging failed: {e}")
                        else:
                            rep.errors.append(f"{pid}: category decided; tag it with `tagger run --product {pid}`")
                        continue
        # ---- the facets
        decisions = {}
        for fr in by_facets.get(pid, []):
            verdict = _norm(fr["verdict"]).lower()
            fid = _norm(fr["facet_id"])
            where = f"Facets row {fr['_row']} ({pid}, {fid})"
            if not verdict:
                complete = False
                continue
            if verdict not in FACET_VERDICTS:
                rep.rejected.append(f"{where}: verdict '{fr['verdict']}' is not approve, change or unknown")
                complete = False
                continue
            if verdict == "approve":
                decisions[fid] = {"decision": "approved"}
            elif verdict == "unknown":
                decisions[fid] = {"decision": "unknown"}
            else:
                vals, why = _value_ids(conn, fid, _norm(fr["corrected_value"]))
                end(db)
                if vals is None:
                    rep.rejected.append(f"{where}: corrected_value '{_norm(fr['corrected_value'])}': {why}")
                    complete = False
                    continue
                decisions[fid] = {"decision": "changed", "values": vals}
        try:
            if complete:
                approve(pid, decisions, via="sheet", who="sheet")
                rep.approved.append(pid)
            elif decisions:
                save(pid, decisions)
                rep.saved_partial.append(pid)
            else:
                rep.saved_partial.append(pid)
        except ReviewError as e:
            rep.errors.append(f"{pid}: {e.message} {' '.join(e.problems)}".strip())
            if complete and decisions:     # keep the verdicts that were given
                try:
                    save(pid, decisions)
                except ReviewError:
                    pass
    return rep


# ------------------------------------------------------------------ auto-approve

def auto_approve_run(conn_db, run_id: int) -> dict:
    """Approve every product of a tag run's batch that is waiting for review, with the tagger's answers as they
    stand (`app.review.auto_approve`). Golden products, already approved ones and Ready ones are left alone.
    Products the app would refuse are left for a person, with the reason."""
    from app.db import end
    from app.review import ReviewError, auto_approve
    conn = conn_db.conn
    tagged, held = scope(conn, run_id)
    pids = [r[0] for r in conn.execute("""select product_id from v_product_review_state
                                           where product_id = any(%s) and state = 'needs_review'
                                           order by product_id""", (tagged + held,))]
    end(conn_db)
    done, left = [], []
    for pid in pids:
        try:
            auto_approve(pid)
            done.append(pid)
        except ReviewError as e:
            left.append(f"{pid}: {e.message} {' '.join(e.problems)}".strip())
    return {"approved": done, "left": left}
