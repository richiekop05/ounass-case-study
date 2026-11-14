"""AI-assisted golden labelling (PRD "Golden set extension and AI-assisted labelling").

`prefill` drafts a category and facet answers for every golden product with a model that is not
under test, using the pipeline's own requests, and writes a review sheet. It reads only scraped data
and the taxonomy (`products`, `product_images`, `categories`, `facets`, `facet_values`,
`category_facets`, `golden_products`) and writes nothing to the database: no run, no tags, no call
log. It never reads `catalog_products` or `product_facet_tags`, so no tagging output can reach the sheet.

`import_reviewed` reads the reviewed sheet, decides each final answer, and stores only that in
`golden_products` / `golden_tags`.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from .config import Profile
from .facets import MODEL_POLICIES, FacetSpec, facets_for_category, facets_for_tagging
from .golden import STATES, parse_answer
from .products import Candidates, candidates_for, load_product
from .runs import CallFailed, RateGate, call_with_retries, make_provider
from .steps import CATEGORISING_SKILL, TAGGING_SKILL, build_tag_request, categorise_request, read_skill
from .tagging import check_answer

PRODUCT_HEADERS = ["product_id", "title", "brand", "product_url", "prefilled_category_id",
                   "prefilled_category_path", "category_note", "category_verdict", "corrected_category_id",
                   "all_facets_reviewed", "candidate_nodes"]
FACET_HEADERS = ["product_id", "title", "facet_id", "is_required", "prefilled_state", "prefilled_values",
                 "evidence", "certainty", "allowed_values", "verdict", "corrected_state", "corrected_values"]
REVIEW_COLUMNS = {"Products": ("category_verdict", "corrected_category_id", "all_facets_reviewed"),
                  "Facets": ("verdict", "corrected_state", "corrected_values")}
PERSON_FILL = PatternFill("solid", fgColor="FFF4CC")


def certainty(confidence: float | None, state: str, changed: bool) -> str:
    """high at 0.90 or above, medium from 0.75 up to 0.90, low below; unknown or changed by a check: low."""
    if changed or state == "unknown" or confidence is None:
        return "low"
    return "high" if confidence >= 0.90 else "medium" if confidence >= 0.75 else "low"


# ------------------------------------------------------------------ drafting

@dataclass
class Draft:
    product_id: str
    title: str = ""
    brand: str = ""
    url: str = ""
    category_id: str | None = None
    category_path: str | None = None
    category_note: str = ""
    candidates: list[dict] = field(default_factory=list)
    facets: list[dict] = field(default_factory=list)
    error: str | None = None
    cost_usd: float = 0.0


class Drafter:
    """Runs the pipeline's two requests for one product, with no gate and no thresholds."""

    def __init__(self, conn, profile: Profile, images, max_images: int, skills_dir: Path,
                 function_categories: dict[str, str] | None = None):
        self.conn = conn                      # read only; shared, so every query holds the lock
        self.lock = threading.RLock()
        self.profile = profile
        self.provider = make_provider(profile)
        self.images = images
        self.max_images = max_images
        self.function_categories = function_categories or {}
        self.gate = RateGate()
        self.cat_skill = read_skill(CATEGORISING_SKILL, skills_dir)
        self.tag_skill = read_skill(TAGGING_SKILL, skills_dir)

    def _call(self, request, draft: Draft):
        def count(attempt, result, error):
            if result is not None:
                draft.cost_usd += self.profile.cost(result.input_tokens, result.output_tokens,
                                                    result.cached_input_tokens, result.cache_write_tokens)
        return call_with_retries(self.provider, request, self.gate, count)

    def _images(self, paths):
        if not (self.profile.accepts_images and self.images):
            return []
        return [self.images.get(p) for p in paths]

    def draft(self, product_id: str, category_id: str | None = None, note: str | None = None) -> Draft:
        d = Draft(product_id)
        try:
            with self.lock:
                product = load_product(self.conn, product_id)
                cands = candidates_for(self.conn, product)
            d.title, d.brand, d.url, d.candidates = product.name, product.brand, product.url, cands.nodes
            if category_id is None:
                if not cands.nodes:
                    raise ValueError("no candidate categories")
                req = categorise_request(product, cands, self._images(product.image_paths[:1]), self.cat_skill)
                reply = self._call(req, d).parsed
                category_id = reply["category_id"]
                d.category_note = _category_note(reply, cands)
            else:
                d.category_note = note or ""
            with self.lock:
                path = self.conn.execute("select path from categories where node_id = %s",
                                         (category_id,)).fetchone()[0]
                specs = facets_for_tagging(self.conn, category_id, path, self.function_categories)
            d.category_id, d.category_path = category_id, path
            if specs:
                req = build_tag_request(product, path, specs, self.tag_skill,
                                        self._images(product.image_paths[:self.max_images]))
                reply = self._call(req, d).parsed
                searchable = product.searchable()
                d.facets = [_facet_row(s, reply.get(s.facet_id), searchable, product.name) for s in specs]
        except (CallFailed, LookupError, ValueError, KeyError) as e:
            d.error = f"{type(e).__name__}: {e}"
        return d


def _category_note(reply: dict, cands: Candidates) -> str:
    paths = {n["node_id"]: n["path"] for n in cands.nodes}
    note = f"{reply.get('reason') or ''} (confidence {float(reply.get('confidence') or 0):.2f})".strip()
    ru = reply.get("runner_up_id")
    if ru and ru != reply.get("category_id") and ru in paths:
        note += f" Runner-up: {ru} {paths[ru]}."
    return note


def _facet_row(spec: FacetSpec, raw, searchable: str, title: str) -> dict:
    a = check_answer(spec, raw, searchable)
    changed = bool(a.notes) or a.force_review
    if a.values and hasattr(a.values[0], "label"):
        values = "; ".join(v.label for v in a.values)
    else:
        values = "; ".join(f"{v:g}" if isinstance(v, float) else str(v) for v in a.values)
    evidence = ""
    if a.state == "value" and a.evidence:
        evidence = a.evidence + (f': "{a.quote}"' if a.evidence in ("text", "both") and a.quote else "")
    why = "; ".join([a.reason] + a.notes if a.reason else a.notes)
    if why:
        evidence = f"{evidence} - {why}" if evidence else why
    if spec.has_value_list:
        allowed = "; ".join(v.label for v in spec.values)
    elif spec.data_type == "number":
        allowed = f"a positive number{f' ({spec.unit})' if spec.unit else ''}"
    else:
        allowed = "free text"
    return {"product_id": None, "title": title, "facet_id": spec.facet_id,
            "is_required": "yes" if spec.is_required else "no",
            "prefilled_state": a.state, "prefilled_values": values, "evidence": evidence,
            "certainty": certainty(a.confidence, a.state, changed), "allowed_values": allowed}


# ------------------------------------------------------------------ the sheet

def sheet_is_reviewed(path: Path) -> bool:
    """True when any column a person fills in has an entry."""
    if not path.exists():
        return False
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    for sheet, cols in REVIEW_COLUMNS.items():
        if sheet not in wb.sheetnames:
            continue
        it = wb[sheet].iter_rows(values_only=True)
        head = list(next(it, []))
        idx = [head.index(c) for c in cols if c in head]
        for row in it:
            if any(row[i] not in (None, "") for i in idx if i < len(row)):
                return True
    return False


def write_sheet(drafts: list[Draft], path: Path) -> None:
    wb = openpyxl.Workbook()
    ps = wb.active
    ps.title = "Products"
    ps.append(PRODUCT_HEADERS)
    fs = wb.create_sheet("Facets")
    fs.append(FACET_HEADERS)
    for d in sorted(drafts, key=lambda d: d.product_id):
        note = d.category_note if not d.error else f"PRE-FILL FAILED: {d.error}"
        ps.append([d.product_id, d.title, d.brand, d.url, d.category_id or "", d.category_path or "", note,
                   "", "", "", "\n".join(f"{n['node_id']}: {n['path']}" for n in d.candidates)])
        for r in d.facets:          # each product's facet rows stay together, in facet order
            fs.append([d.product_id, r["title"], r["facet_id"], r["is_required"], r["prefilled_state"],
                       r["prefilled_values"], r["evidence"], r["certainty"], r["allowed_values"], "", "", ""])
    _style(ps, {"A": 16, "B": 36, "C": 18, "D": 34, "E": 16, "F": 40, "G": 60, "H": 12, "I": 18, "J": 12, "K": 60},
           ["H", "I", "J"])
    _style(fs, {"A": 16, "B": 30, "C": 20, "D": 9, "E": 13, "F": 26, "G": 60, "H": 10, "I": 50, "J": 9, "K": 14,
                "L": 26}, ["J", "K", "L"])
    for ws, col, options in ((ps, "H", "ok,change"), (ps, "J", "yes"), (fs, "J", "ok,change"),
                             (fs, "K", "value,unknown,not_applicable")):
        dv = DataValidation(type="list", formula1=f'"{options}"', allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(f"{col}2:{col}{max(2, ws.max_row)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.xlsx")
    wb.save(tmp)
    tmp.replace(path)


def _style(ws, widths, person_cols):
    for col, w in widths.items():
        ws.column_dimensions[col].width = w
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    for col in person_cols:
        for cell in ws[col][1:]:
            cell.fill = PERSON_FILL
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


# ------------------------------------------------------------------ commands

MAIN_SHEET = Path(__file__).resolve().parents[2] / "golden" / "prefilled_labels.xlsx"

def prefill(conn, profile: Profile, images, out: Path, settings, skills_dir: Path, force: bool = False,
            only: dict[str, tuple[str, str]] | None = None, log=print) -> dict:
    """Draft every golden product (or, with `only`, the given products under the given categories) and
    write the sheet. Nothing is written to the database."""
    if only is not None and Path(out).resolve() == MAIN_SHEET.resolve():
        raise ValueError(f"a partial pre-fill never writes {MAIN_SHEET.name}; pass another --out")
    if sheet_is_reviewed(out) and not force:
        raise ValueError(f"{out} has review entries; pass --force to overwrite it")
    drafter = Drafter(conn, profile, images, settings.max_images, skills_dir, settings.function_categories)
    if only is None:
        pids = [r[0] for r in conn.execute("select product_id from golden_products order by product_id")]
        if not pids:
            raise ValueError("golden_products is empty: run `tagger golden sample` first")
        jobs = [(p, None, None) for p in pids]
    else:
        jobs = [(p, cat, note) for p, (cat, note) in sorted(only.items())]
    log(f"pre-filling {len(jobs)} golden products with model {profile.name} ({profile.model_id})")
    started = time.monotonic()
    done = []

    def one(job):
        d = drafter.draft(*job)
        log(f"  {d.product_id}: " + (f"FAILED {d.error}" if d.error else
                                     f"{d.category_id}, {len(d.facets)} facets, ${d.cost_usd:.4f}"))
        return d
    with ThreadPoolExecutor(max_workers=max(1, settings.concurrency)) as pool:
        done = list(pool.map(one, jobs))
    write_sheet(done, out)
    failed = [d.product_id for d in done if d.error]
    return {"model": profile.name, "model_id": profile.model_id, "drafted": len(done) - len(failed),
            "failed": failed, "cost_usd": round(sum(d.cost_usd for d in done), 4),
            "seconds": round(time.monotonic() - started, 1), "facet_rows": sum(len(d.facets) for d in done),
            "path": str(out)}


def recategorised(conn, reviewed: Path) -> dict[str, tuple[str, str]]:
    """Products whose category verdict is `change`, with their corrected (valid) category."""
    wb = openpyxl.load_workbook(reviewed, read_only=True, data_only=True)
    if "Products" not in wb.sheetnames:
        raise ValueError(f"{reviewed.name} has no Products sheet")
    it = wb["Products"].iter_rows(values_only=True)
    head = list(next(it))
    out = {}
    valid = _valid_categories(conn)
    for row in it:
        r = dict(zip(head, row))
        if _txt(r.get("category_verdict")) == "change":
            cat = _txt(r.get("corrected_category_id"), lower=False)
            if cat in valid:
                out[_txt(r["product_id"], lower=False)] = (
                    cat, f"Category corrected in review (pre-filled {r.get('prefilled_category_id') or '-'}).")
    return out


def chosen_categories(conn, product_ids: list[str], sheet: Path) -> dict[str, tuple[str | None, str]]:
    """For a partial pre-fill: each product with the category pre-filled for it in `sheet` (opened read
    only, never written). A product missing there, or whose category is no longer a valid leaf, is
    categorised afresh (None)."""
    golden = {r[0] for r in conn.execute("select product_id from golden_products where product_id = any(%s)",
                                         (product_ids,))}
    unknown = [p for p in product_ids if p not in golden]
    if unknown:
        raise ValueError(f"not in golden_products: {', '.join(unknown)}")
    found = {}
    if Path(sheet).exists():
        wb = openpyxl.load_workbook(sheet, read_only=True, data_only=True)
        if "Products" in wb.sheetnames:
            it = wb["Products"].iter_rows(values_only=True)
            head = list(next(it))
            for row in it:
                r = dict(zip(head, row))
                found[_txt(r.get("product_id"), lower=False)] = _txt(r.get("prefilled_category_id"), lower=False)
        wb.close()
    valid = _valid_categories(conn)
    out = {}
    for p in product_ids:
        cat = found.get(p)
        out[p] = ((cat, f"Category kept from {Path(sheet).name}.") if cat in valid
                  else (None, ""))
    return out


def _txt(v, lower=True) -> str:
    s = "" if v is None else str(v).strip()
    return s.lower() if lower else s


def _valid_categories(conn) -> set[str]:
    """Leaves and parents with their own facets, never holding ('Other ...') nodes (products.ASSIGNABLE_SQL)."""
    from .products import ASSIGNABLE_SQL
    return {r[0] for r in conn.execute(ASSIGNABLE_SQL)}


# ------------------------------------------------------------------ import

@dataclass
class ReviewImport:
    categories_saved: int = 0
    facets_loaded: int = 0
    rows_loaded: int = 0
    second_pass: list[str] = field(default_factory=list)
    unreviewed_products: list[str] = field(default_factory=list)
    skipped_no_verdict: dict[str, int] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"categories saved: {self.categories_saved}; facet answers loaded: {self.facets_loaded} "
                 f"({self.rows_loaded} rows); problems: {len(self.problems)}"]
        if self.second_pass:
            lines.append(f"category changed, facets not imported (second pass needed): {', '.join(self.second_pass)}")
            lines.append("  next: uv run tagger golden prefill --only-recategorised <this sheet>")
        if self.unreviewed_products:
            lines.append(f"not reviewed (no category verdict, all_facets_reviewed not yes): "
                         f"{', '.join(self.unreviewed_products)}")
        for pid, n in sorted(self.skipped_no_verdict.items()):
            lines.append(f"  {pid}: {n} facet rows skipped (no verdict, product not marked all_facets_reviewed)")
        lines += [f"  problem: {p}" for p in self.problems]
        return "\n".join(lines)


def import_reviewed(db, path: Path, verdicts_only: bool = False) -> ReviewImport:
    """Load a reviewed sheet. With `verdicts_only`, a facet row is imported only when it has a verdict
    (all_facets_reviewed does not turn blanks into ok), and a blank category verdict counts as ok for a
    product that has at least one facet verdict."""
    rep = ReviewImport()
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    for sheet, headers in (("Products", PRODUCT_HEADERS), ("Facets", FACET_HEADERS)):
        if sheet not in wb.sheetnames:
            raise ValueError(f"{path.name} has no {sheet} sheet")
        got = [c.value for c in next(wb[sheet].iter_rows(max_row=1))]
        missing = [h for h in headers if h not in got and h != "candidate_nodes"]
        if missing:
            raise ValueError(f"{sheet} sheet is missing columns {missing}")

    def rows(sheet):
        it = wb[sheet].iter_rows(values_only=True)
        head = list(next(it))
        for i, r in enumerate(it, start=2):
            yield i, dict(zip(head, r))

    with db.lock:
        conn = db.conn
        golden = {r[0]: r[1] for r in conn.execute("select product_id, category_id from golden_products")}
        valid = _valid_categories(conn)
        final: dict[str, str] = {}               # product -> final category, facets importable
        reviewed: set[str] = set()
        new_category: dict[str, str] = {}
        facet_verdicts = {_txt(r.get("product_id"), lower=False) for _, r in rows("Facets")
                          if _txt(r.get("verdict"))} if verdicts_only else set()
        # ---- pass 1: products
        for i, r in rows("Products"):
            pid = _txt(r.get("product_id"), lower=False)
            if not pid:
                continue
            if pid not in golden:
                rep.problems.append(f"Products row {i}: {pid} is not in golden_products")
                continue
            verdict = _txt(r.get("category_verdict"))
            if _txt(r.get("all_facets_reviewed")) == "yes":
                reviewed.add(pid)
            if verdicts_only:
                reviewed.discard(pid)
                if verdict == "" and pid in facet_verdicts:
                    verdict = "ok"
            prefilled = _txt(r.get("prefilled_category_id"), lower=False)
            if verdict not in ("", "ok", "change"):
                rep.problems.append(f"Products row {i} ({pid}): category_verdict {verdict!r} is not ok or change")
                continue
            if verdict == "change":
                corrected = _txt(r.get("corrected_category_id"), lower=False)
                if not corrected:
                    rep.problems.append(f"Products row {i} ({pid}): verdict change without a corrected_category_id")
                elif corrected not in valid:
                    rep.problems.append(f"Products row {i} ({pid}): category {corrected!r} is not a leaf category "
                                        f"(unknown ID or holding node)")
                else:
                    new_category[pid] = corrected
                    rep.second_pass.append(pid)
                continue
            if verdict == "" and pid not in reviewed:
                rep.unreviewed_products.append(pid)
                continue
            if not prefilled:
                rep.problems.append(f"Products row {i} ({pid}): no pre-filled category to accept")
            elif prefilled not in valid:
                rep.problems.append(f"Products row {i} ({pid}): category {prefilled!r} is not a category a product can sit on")
            else:
                final[pid] = prefilled
                new_category[pid] = prefilled
        # ---- pass 2: facets
        specs_cache: dict[str, dict[str, FacetSpec]] = {}
        answers: dict[tuple[str, str], tuple[str, list]] = {}
        for i, r in rows("Facets"):
            pid, fid = _txt(r.get("product_id"), lower=False), _txt(r.get("facet_id"), lower=False)
            if not pid or not fid or pid not in final:
                continue
            cat = final[pid]
            if cat not in specs_cache:
                specs_cache[cat] = {k: s for k, s in facets_for_category(conn, cat).items()
                                    if s.source_policy in MODEL_POLICIES}
            spec = specs_cache[cat].get(fid)
            if spec is None:
                rep.problems.append(f"Facets row {i} ({pid}, {fid}): facet does not apply to category {cat}")
                continue
            verdict = _txt(r.get("verdict"))
            if verdict not in ("", "ok", "change"):
                rep.problems.append(f"Facets row {i} ({pid}, {fid}): verdict {verdict!r} is not ok or change")
                continue
            if verdict == "":
                if pid not in reviewed:
                    rep.skipped_no_verdict[pid] = rep.skipped_no_verdict.get(pid, 0) + 1
                    continue
                verdict = "ok"
            if verdict == "ok":
                state, values = r.get("prefilled_state"), r.get("prefilled_values")
            else:
                state, values = r.get("corrected_state"), r.get("corrected_values")
                if _txt(state) == "" and _txt(values) == "":
                    rep.problems.append(f"Facets row {i} ({pid}, {fid}): verdict change without a corrected answer")
                    continue
                if _txt(state) == "" and _txt(values):
                    state = "value"
            parsed_state, parsed_values, err = parse_answer(spec, state, values)
            if err:
                rep.problems.append(f"Facets row {i} ({pid}, {fid}): {err}")
                continue
            answers[(pid, fid)] = (parsed_state, parsed_values)
        # ---- write: only final answers
        with conn.transaction():
            for pid, cat in new_category.items():
                if golden.get(pid) != cat:
                    conn.execute("update golden_products set category_id = %s where product_id = %s", (cat, pid))
                    conn.execute("""delete from golden_tags t where t.product_id = %s and not exists
                                    (select 1 from category_facets cf where cf.category_id = %s
                                     and cf.facet_id = t.facet_id)""", (pid, cat))
                rep.categories_saved += 1
            for pid in rep.second_pass:      # labels drawn up for the wrong category are dropped
                conn.execute("delete from golden_tags where product_id = %s", (pid,))
            for (pid, fid), (state, values) in answers.items():
                conn.execute("delete from golden_tags where product_id = %s and facet_id = %s", (pid, fid))
                if state != "value":
                    conn.execute("insert into golden_tags (product_id, facet_id, status) values (%s, %s, %s)",
                                 (pid, fid, state))
                    rep.rows_loaded += 1
                for v in values:
                    conn.execute(
                        """insert into golden_tags (product_id, facet_id, status, facet_value_id, value_number,
                               value_text) values (%s, %s, 'value', %s, %s, %s)""",
                        (pid, fid, getattr(v, "facet_value_id", None), v if isinstance(v, float) else None,
                         v if isinstance(v, str) else None))
                    rep.rows_loaded += 1
                rep.facets_loaded += 1
        db.commit()
    return rep


def is_review_sheet(path: Path) -> bool:
    return "Products" in openpyxl.load_workbook(path, read_only=True).sheetnames


__all__ = ["prefill", "recategorised", "import_reviewed", "is_review_sheet", "certainty", "STATES"]
