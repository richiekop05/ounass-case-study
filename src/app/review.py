"""Review and approval (Build PRD 5). Every action is one database transaction.

A reviewer's decisions are written as tag rows under a run with stage `review` (one per product,
parent_run_id = the tagger run it started from). The tagger's own runs and rows are never edited.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from tagger.facets import MODEL_POLICIES

from .db import commit, end, get_db
from .listing import Filters

DECISIONS = ("approved", "changed", "unknown")
OUTCOME = {"approved": "approved_unchanged", "changed": "changed", "unknown": "set_unknown"}


class ReviewError(Exception):
    """Refused, with a message the page shows as written; `problems` lists one line per facet."""

    def __init__(self, message: str, status: int = 400, problems: list[str] | None = None):
        super().__init__(message)
        self.message, self.status, self.problems = message, status, problems or []


@dataclass
class Facet:
    id: str
    name: str
    data_type: str
    multi: bool
    max_values: int | None
    rule: bool
    required: bool      # a value is needed before approval: required here AND on_missing review_if_required
    allowed: dict[int, str] = field(default_factory=dict)
    exclusive: set[int] = field(default_factory=set)      # values that cannot be combined with others


@dataclass
class Ctx:
    product_id: str
    state: str
    published: bool
    ai_run: int | None
    current_run: int | None
    category_id: str
    category_flagged: bool
    category_reviewed: bool
    facets: dict[str, Facet]
    tagger_rows: dict[str, list[tuple]]       # facet -> rows (see ROW_COLS)
    flagged: set[str]
    review_run: int | None


ROW_COLS = ("facet_value_id", "value_number", "value_text", "status", "confidence", "evidence",
            "evidence_quote", "reason", "review_reason")


def _load(conn, pid: str) -> Ctx:
    st = conn.execute("""select s.state, s.is_published, s.ai_run_id, s.current_run_id, s.category_id,
                                s.category_flagged, cp.category_reviewed_at is not null
                           from v_product_review_state_all s join catalog_products cp using (product_id)
                          where s.product_id = %s""", (pid,)).fetchone()
    if not st:
        raise ReviewError(f"Product {pid} is not categorised, so it cannot be reviewed.", 404)
    state, published, ai_run, cur_run, cat, cat_flag, cat_reviewed = st
    facets: dict[str, Facet] = {}
    for fid, name, dtype, card, maxv, policy, req in conn.execute(
            """select f.facet_id, f.name, f.data_type, f.cardinality, f.max_values, f.source_policy,
                      cf.is_required and f.on_missing = 'review_if_required'
                 from category_facets cf join facets f on f.facet_id = cf.facet_id
                where cf.category_id = %s and f.status = 'active' and f.source_policy <> 'not_tagged'""", (cat,)):
        facets[fid] = Facet(fid, name, dtype, card != "single" and (maxv or 2) > 1, maxv,
                            policy not in MODEL_POLICIES, bool(req))
    for fid, vid, label, excl in conn.execute("""select facet_id, facet_value_id, label, is_exclusive
                                                 from facet_values where facet_id = any(%s) and is_active""",
                                              (list(facets),)):
        facets[fid].allowed[vid] = label
        if excl:
            facets[fid].exclusive.add(vid)
    rows: dict[str, list[tuple]] = {}
    flagged: set[str] = set()
    if ai_run:
        for r in conn.execute(f"""select facet_id, {', '.join(ROW_COLS)} from product_facet_tags
                                  where product_id = %s and tagging_run_id = %s""", (pid, ai_run)):
            rows.setdefault(r[0], []).append(r[1:])
        flagged = {r[0] for r in conn.execute("""select facet_id from v_tag_flags where product_id = %s
                                                 and tagging_run_id = %s and flagged""", (pid, ai_run))}
    rr = conn.execute("""select tagging_run_id from tagging_runs where stage = 'review' and product_id = %s
                         order by tagging_run_id desc limit 1""", (pid,)).fetchone()
    return Ctx(pid, state, bool(published), ai_run, cur_run, cat, bool(cat_flag), bool(cat_reviewed),
               facets, rows, flagged, rr[0] if rr else None)


def _has_value(r: tuple) -> bool:
    return r[0] is not None or r[1] is not None or r[2] is not None


def _labels(f: Facet, rows: list[tuple]) -> str:
    out = []
    for r in rows:
        if r[0] is not None:
            out.append(f.allowed.get(r[0]) or str(r[0]))
        elif r[1] is not None:
            out.append(f"{float(r[1]):g}")
        elif r[2] is not None:
            out.append(str(r[2]))
    return "; ".join(out) or "unknown"


# ------------------------------------------------------------------ decisions -> rows

def _decision_rows(c: Ctx, fid: str, d: dict, problems: list[str], allow_missing: bool = False) -> list[tuple] | None:
    f = c.facets.get(fid)
    if f is None:
        problems.append(f"{fid}: does not apply to this product's category")
        return None
    if f.rule:
        problems.append(f"{f.name}: is set by a rule and cannot be changed")
        return None
    kind = d.get("decision")
    if kind not in DECISIONS:
        problems.append(f"{f.name}: the decision must be approved, changed or unknown")
        return None
    old = c.tagger_rows.get(fid, [])
    if kind == "approved":
        if not any(_has_value(r) for r in old):
            if f.required and not allow_missing:      # store samples (owner): a required facet may stay empty
                problems.append(f"{f.name}: a value is required; choose one with Change")
                return None
            return [(None, None, None, "abstained", None, "human", None, "Approved as unknown by reviewer", None)]
        return [(r[0], r[1], r[2], "auto", r[4], r[5], r[6], r[7], None) for r in old if _has_value(r)]
    if kind == "unknown":
        if f.required and not allow_missing:
            problems.append(f"{f.name}: is required, so it cannot be set to unknown")
            return None
        return [(None, None, None, "abstained", None, "human", None, "Set to unknown by reviewer", None)]
    vals = d.get("values") or []
    if not vals:
        problems.append(f"{f.name}: choose a value")
        return None
    reason = f"Changed by reviewer (was: {_labels(f, old)})"
    if f.data_type in ("enum", "ordinal", "boolean"):
        ids = []
        for v in vals:
            try:
                vid = int(v)
            except (TypeError, ValueError):
                vid = None
            if vid not in f.allowed:
                problems.append(f"{f.name}: '{v}' is not an allowed value")
                return None
            if vid not in ids:
                ids.append(vid)
        limit = f.max_values if f.multi else 1
        if limit and len(ids) > limit:
            problems.append(f"{f.name}: choose up to {limit}")
            return None
        lone = [v for v in ids if v in f.exclusive]
        if lone and len(ids) > 1:          # the catalog rule exclusive_with_other_values, checked before saving
            problems.append(f"{f.name}: {f.allowed[lone[0]]} cannot be combined with other values")
            return None
        return [(vid, None, None, "auto", None, "human", None, reason, None) for vid in ids]
    if f.data_type == "number":
        try:
            x = float(vals[0])
        except (TypeError, ValueError):
            problems.append(f"{f.name}: enter a number")
            return None
        if x <= 0 or len(vals) > 1:
            problems.append(f"{f.name}: enter one positive number")
            return None
        return [(None, x, None, "auto", None, "human", None, reason, None)]
    text = " ".join(str(v).strip() for v in vals if str(v).strip())
    if not text:
        problems.append(f"{f.name}: enter a value")
        return None
    return [(None, None, text, "auto", None, "human", None, reason, None)]


def _saved(conn, c: Ctx) -> dict[str, dict]:
    """Decisions already stored in the product's review run."""
    if not c.review_run:
        return {}
    out: dict[str, dict] = {}
    for fid, vid, num, txt, status, ev, outcome in conn.execute(
            """select facet_id, facet_value_id, value_number, value_text, status, evidence, review_outcome
                 from product_facet_tags where tagging_run_id = %s and product_id = %s""",
            (c.review_run, c.product_id)):
        if outcome == "not_reviewed":
            continue
        d = out.setdefault(fid, {"decision": "approved", "values": []})
        if ev == "human":
            if status == "abstained":
                d["decision"] = "unknown"
            else:
                d["decision"] = "changed"
                d["values"].append(vid if vid is not None else float(num) if num is not None else txt)
    return out


def saved_decisions(product_id: str) -> dict | None:
    db = get_db()
    with db.lock:
        try:
            c = _load(db.conn, product_id)
            s = _saved(db.conn, c)
            dec = db.conn.execute("select category_review_outcome from catalog_products where product_id = %s",
                                  (product_id,)).fetchone()[0] if c.category_reviewed else None
            return ({"facets": s, "categoryReviewed": c.category_reviewed,
                     "categoryDecision": {"approved_unchanged": "approved", "changed": "changed"}.get(dec)}
                    if (s or c.category_reviewed) else None)
        finally:
            end(db)


# ------------------------------------------------------------------ runs

def _review_run(conn, c: Ctx, purpose: str = "tagger") -> int:
    if c.review_run:
        conn.execute("update tagging_runs set parent_run_id = %s where tagging_run_id = %s",
                     (c.ai_run, c.review_run))
        return c.review_run
    return conn.execute(
        """insert into tagging_runs (tagger_version, model, stage, product_id, parent_run_id, started_at,
                                     model_profile, taxonomy_version)
           values ('app', 'reviewer', 'review', %s, %s, now(), 'reviewer',
                   (select taxonomy_version from app_settings)) returning tagging_run_id""",
        (c.product_id, c.ai_run)).fetchone()[0] if purpose == "tagger" else conn.execute(
        """insert into tagging_runs (tagger_version, model, stage, product_id, parent_run_id, started_at,
                                     model_profile, taxonomy_version, purpose)
           values ('app', 'sample-loader', 'review', %s, %s, now(), 'sample-loader',
                   (select taxonomy_version from app_settings), %s) returning tagging_run_id""",
        (c.product_id, c.ai_run, purpose)).fetchone()[0]


INSERT = """insert into product_facet_tags (product_id, tagging_run_id, facet_id, facet_value_id, value_number,
                value_text, status, confidence, evidence, evidence_quote, reason, review_reason, review_outcome,
                review_was_flagged)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""


def _write(conn, pid: str, run: int, rows: list[tuple]):
    conn.execute("delete from product_facet_tags where tagging_run_id = %s and product_id = %s", (run, pid))
    with conn.cursor() as cur:
        cur.executemany(INSERT, rows)


def _check_ready_to_edit(c: Ctx):
    if not c.ai_run:
        raise ReviewError("This product has no tags yet: decide its category first.", 409)
    if c.category_flagged:
        raise ReviewError("Decide the category first. Facets are locked until it is approved or changed.", 409)


def save(product_id: str, decisions: dict) -> dict:
    """Save: store the decisions so far under the product's review run. Nothing is published."""
    db = get_db()
    with db.lock:
        conn = db.conn
        try:
            c = _load(conn, product_id)
            _check_ready_to_edit(c)
            problems: list[str] = []
            rows = []
            for fid, d in (decisions or {}).items():
                rs = _decision_rows(c, fid, d, problems)
                if rs:
                    rows += [(product_id, None, fid, *r, None, None) for r in rs]
            if problems:
                raise ReviewError("Some decisions could not be saved.", 422, problems)
            with conn.transaction():
                run = _review_run(conn, c)
                if c.published:   # an approved product: saving changes re-approves with the new rows
                    pass
                _write(conn, product_id, run, [(r[0], run, *r[2:]) for r in rows])
            commit(db)
        except Exception:
            end(db)
            raise
    if c.published:
        return approve(product_id, decisions, via=None)
    return {"saved": len(decisions or {}), "reviewRunId": run}


def approve(product_id: str, decisions: dict | None, via: str | None = "detail", require_ready: bool = False,
            who: str = "reviewer", unreviewed: set | None = None, allow_missing: bool = False,
            purpose: str = "tagger") -> dict:
    """Approve and publish: the decisions (else the saved ones) plus every untouched facet as the tagger left it."""
    db = get_db()
    with db.lock:
        conn = db.conn
        try:
            c = _load(conn, product_id)
            if require_ready and c.state != "ready":
                raise ReviewError("This product needs review, so it cannot be approved from the list. "
                                  "Open it to review the flagged facets.", 409)
            _check_ready_to_edit(c)
            merged = {**_saved(conn, c), **(decisions or {})}
            problems: list[str] = []
            for fid in sorted(c.flagged):
                if fid in c.facets and fid not in merged:
                    problems.append(f"{c.facets[fid].name}: needs a decision (approve or change it)")
            final = []
            for fid, f in sorted(c.facets.items()):
                if fid in merged:
                    rs = _decision_rows(c, fid, merged[fid], problems, allow_missing)
                    if rs is None:
                        continue
                    # accepted without a person looking (auto-approve): recorded as not reviewed
                    outcome = "not_reviewed" if unreviewed and fid in unreviewed else OUTCOME[merged[fid]["decision"]]
                else:
                    rs = []
                    for r in c.tagger_rows.get(fid, []):
                        status = r[3]
                        if status == "review" and _has_value(r) and fid not in c.flagged:
                            status = "auto"      # accepted at today's level
                        rs.append((r[0], r[1], r[2], status, r[4], r[5], r[6], r[7], r[8]))
                    outcome = "not_reviewed"
                has_value = any(_has_value(r) and r[3] == "auto" for r in rs)
                n_a = any(r[3] == "not_applicable" for r in rs)
                undecided_flag = fid in c.flagged and fid not in merged    # already reported above
                if f.required and not f.rule and not has_value and not n_a and not undecided_flag and not allow_missing:
                    problems.append(f"{f.name}: a value is required")
                final += [(product_id, None, fid, *r, outcome, fid in c.flagged) for r in rs]
            if problems:
                raise ReviewError("This product cannot be approved yet.", 422, problems)
            with conn.transaction():
                conn.execute("""update catalog_products set category_status = 'auto' where product_id = %s
                                and category_status <> 'auto'""", (product_id,))
                run = _review_run(conn, c, purpose)
                _write(conn, product_id, run, [(r[0], run, *r[2:]) for r in final])
                if not c.category_reviewed:
                    conn.execute("""update catalog_products set category_review_outcome = 'not_reviewed',
                                    category_was_flagged = false where product_id = %s""", (product_id,))
                conn.execute(
                    """update catalog_products set current_run_id = %s, is_published = true,
                              approved_at = case when is_published then approved_at else now() end,
                              approved_by = %s, approved_via = coalesce(%s, approved_via, 'detail')
                        where product_id = %s""", (run, who, via, product_id))
                bad = conn.execute("""select facet_id, rule, detail from v_invalid_tags
                                      where product_id = %s and tagging_run_id = %s""", (product_id, run)).fetchall()
                if bad:
                    raise ReviewError("Approval would store tags that break the catalog rules.", 422,
                                      [f"{c.facets[f].name if f in c.facets else f}: {d}" for f, _r, d in bad])
            commit(db)
        except Exception:
            end(db)
            raise
    return {"approved": product_id, "reviewRunId": run}


def unapprove(product_id: str) -> dict:
    """Undo, or Send back to review: unpublish and return to the tagger run; the decisions are kept."""
    db = get_db()
    with db.lock:
        conn = db.conn
        try:
            c = _load(conn, product_id)
            if not c.published:
                raise ReviewError("This product is not approved.", 409)
            with conn.transaction():
                parent = conn.execute("select parent_run_id from tagging_runs where tagging_run_id = %s",
                                      (c.current_run,)).fetchone()
                back = (parent[0] if parent and parent[0] else None) or c.ai_run
                if c.review_run:   # keep only the reviewer's own decisions, so they reappear as decisions
                    conn.execute("""delete from product_facet_tags where tagging_run_id = %s and product_id = %s
                                    and review_outcome = 'not_reviewed'""", (c.review_run, product_id))
                    conn.execute("""update product_facet_tags set review_outcome = null, review_was_flagged = null
                                    where tagging_run_id = %s and product_id = %s""", (c.review_run, product_id))
                conn.execute("""update catalog_products set is_published = false, current_run_id = %s,
                                approved_at = null, approved_via = null where product_id = %s""", (back, product_id))
            commit(db)
        except Exception:
            end(db)
            raise
    return {"unapproved": product_id}


def approve_ready(f: Filters) -> dict:
    """Approve all ready: every Ready product in the filters; one failing does not stop the others."""
    from .db import read
    from .listing import _where
    f = Filters(**{**f.__dict__, "tab": "products", "state": "ready", "page": 1})
    where, p = _where(f)
    ids = [r[0] for r in read(f"select s.product_id from v_product_review_state s where {where}", p)]
    done, failed = [], []
    for pid in ids:
        try:
            approve(pid, None, via="bulk", require_ready=True)
            done.append(pid)
        except ReviewError as e:
            failed.append({"id": pid, "message": "; ".join(e.problems) or e.message})
    return {"approved": done, "failed": failed}


# ------------------------------------------------------------------ category

def decide_category(product_id: str, category_id: str | None) -> dict:
    """Approve the category as shown (category_id None) or change it. Returns whether a re-tag is needed."""
    from tagger.products import ASSIGNABLE_SQL
    db = get_db()
    with db.lock:
        conn = db.conn
        try:
            c = _load(conn, product_id)
            if c.published:
                raise ReviewError("This product is approved. Send it back to review before changing its category.",
                                  409)
            with conn.transaction():
                if category_id is None or category_id == c.category_id:
                    conn.execute("""update catalog_products set category_status = 'auto', category_reviewed_at = now(),
                                    category_review_outcome = 'approved_unchanged', category_was_flagged = %s
                                    where product_id = %s""", (c.category_flagged, product_id))
                    retag = c.ai_run is None
                else:
                    ok = conn.execute(f"select 1 from ({ASSIGNABLE_SQL}) a where a.node_id = %s",
                                      (category_id,)).fetchone()
                    if not ok:
                        raise ReviewError("That category cannot hold products (unknown, or a holding node).", 422)
                    runs = [r[0] for r in conn.execute("""select tagging_run_id from tagging_runs
                                                          where stage = 'review' and product_id = %s""", (product_id,))]
                    conn.execute("""update catalog_products set category_id = %s, category_status = 'auto',
                                    category_reviewed_at = now(), category_review_outcome = 'changed',
                                    category_was_flagged = %s, ai_run_id = null, current_run_id = null
                                    where product_id = %s""", (category_id, c.category_flagged, product_id))
                    if runs:      # decisions made under the old category no longer apply
                        conn.execute("delete from product_facet_tags where tagging_run_id = any(%s)", (runs,))
                        conn.execute("delete from tagging_runs where tagging_run_id = any(%s)", (runs,))
                    retag = True
            commit(db)
        except Exception:
            end(db)
            raise
    return {"retag": retag}


def auto_approve(product_id: str) -> dict:
    """Approve and publish with the tagger's answers as they stand (owner, 14 Nov 2026). Every flagged answer
    without a saved decision is accepted as shown and recorded as `not_reviewed`; decisions a person already
    saved are used and counted as reviewed. Refused, like any approval, when the app would refuse it (a held
    category with no tags, a required facet without a value, a catalog rule broken)."""
    db = get_db()
    with db.lock:
        try:
            c = _load(db.conn, product_id)
            saved = _saved(db.conn, c)
        finally:
            end(db)
    if c.published:
        raise ReviewError("Already approved.", 409)
    if c.ai_run is None:
        raise ReviewError("Not tagged yet: its category is held for a person to decide.", 409)
    auto = {fid for fid in c.flagged if fid in c.facets and fid not in saved}
    before = None
    if c.category_flagged:
        # the category as the tagger gave it, accepted without a person: decided, but not counted as reviewed
        with db.lock:
            try:
                before = db.conn.execute("""select category_status, category_reviewed_at, category_review_outcome,
                                                   category_was_flagged from catalog_products where product_id = %s""",
                                         (product_id,)).fetchone()
                db.conn.execute("""update catalog_products set category_status = 'auto', category_reviewed_at = now(),
                                          category_review_outcome = 'not_reviewed', category_was_flagged = true
                                    where product_id = %s""", (product_id,))
                commit(db)
            except Exception:
                end(db)
                raise
    try:
        return approve(product_id, {f: {"decision": "approved"} for f in auto}, via="auto", who="auto",
                       unreviewed=auto)
    except ReviewError:
        if before is not None:     # not approved after all: the category goes back to waiting for a person
            with db.lock:
                try:
                    db.conn.execute("""update catalog_products set category_status = %s, category_reviewed_at = %s,
                                              category_review_outcome = %s, category_was_flagged = %s
                                        where product_id = %s""", (*before, product_id))
                    commit(db)
                except Exception:
                    end(db)
                    raise
        raise


def publish_sample(product_id: str) -> dict:
    """Richie's store sample (owner, 14 Nov 2026): publish for the store with the tagger's answers as they stand,
    no review. The category is accepted even when it was held or flagged; a required facet left empty stays
    empty; every answer is recorded as not reviewed. The product is `source = 'sample'`, so the tagger app never
    shows it. Catalog rules (v_invalid_tags) still hold."""
    db = get_db()
    with db.lock:
        try:
            db.conn.execute("""update catalog_products set source = 'sample', category_status = 'auto',
                                      category_reviewed_at = coalesce(category_reviewed_at, now()),
                                      category_review_outcome = 'not_reviewed', category_was_flagged =
                                          coalesce(category_was_flagged, category_status <> 'auto')
                                where product_id = %s""", (product_id,))
            commit(db)
            c = _load(db.conn, product_id)
        finally:
            end(db)
    if c.ai_run is None:
        raise ReviewError("Not tagged.", 409)
    flags = {fid for fid in c.flagged if fid in c.facets}
    return approve(product_id, {f: {"decision": "approved"} for f in flags}, via="sample", who="sample-loader",
                   unreviewed=flags, allow_missing=True, purpose="store_sample")
