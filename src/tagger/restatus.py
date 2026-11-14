"""Recalculate the saved status of a tag run's rows from their stored confidence and today's thresholds.

No model is called. Only rows that hold a model value (status auto or review, any evidence but rule) are recalculated, with
the same rule the tagger uses (PRD 4.5): an answer equal to the facet's default value is auto; text_only
is auto only on text evidence; text_first is auto on text evidence; otherwise auto when confidence >= the
facet's auto_threshold; a quote that was not found in the text stays in review. Rule rows (defaults,
derived facets), not_applicable, abstained and unknown-required review rows hold no model value and are
left alone.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .facets import all_facets
from .runs import Db
from .tagging import Answer, value_status

QUOTE_NOT_FOUND = "quote not found in text"


@dataclass
class Restatus:
    run_id: int
    rows_checked: int = 0
    unchanged: int = 0
    changes: Counter = field(default_factory=Counter)            # (facet_id, old, new) -> rows
    skipped: Counter = field(default_factory=Counter)            # reason -> rows

    @property
    def changed(self) -> int:
        return sum(self.changes.values())

    def render(self) -> str:
        to_auto = sum(n for (_, old, new), n in self.changes.items() if new == "auto")
        lines = [f"run {self.run_id}: {self.rows_checked} value rows recalculated, {self.changed} changed "
                 f"({to_auto} review -> auto, {self.changed - to_auto} auto -> review), {self.unchanged} unchanged"]
        for (fid, old, new), n in sorted(self.changes.items()):
            lines.append(f"  {fid}: {old} -> {new}: {n}")
        for why, n in sorted(self.skipped.items()):
            lines.append(f"  left alone: {n} rows ({why})")
        return "\n".join(lines)


def restatus(db: Db, run_id: int) -> Restatus:
    rep = Restatus(run_id)
    with db.lock:
        conn = db.conn
        row = conn.execute("select stage from tagging_runs where tagging_run_id = %s", (run_id,)).fetchone()
        if not row:
            raise LookupError(f"run {run_id} not found")
        if row[0] != "tag":
            raise ValueError(f"run {run_id} is a {row[0]} run; restatus works on tag runs")
        specs = all_facets(conn)
        rows = conn.execute(
            """select tag_id, facet_id, status, confidence, evidence, coalesce(reason, ''), facet_value_id,
                      (facet_value_id is not null or value_number is not null or value_text is not null)
               from product_facet_tags where tagging_run_id = %s""", (run_id,)).fetchall()
        updates = []
        for tag_id, fid, status, conf, evidence, reason, fvid, has_value in rows:
            if status not in ("auto", "review") or evidence == "rule" or not has_value:
                rep.skipped["no threshold applies: rule, not applicable, abstained or unknown"] += 1
                continue
            spec = specs.get(fid)
            if spec is None:
                rep.skipped["facet no longer active"] += 1
                continue
            rep.rows_checked += 1
            values = [v for v in spec.values if v.facet_value_id == fvid] if fvid is not None else [None]
            a = Answer(fid, "value", values=values, confidence=float(conf) if conf is not None else None,
                       evidence=evidence, force_review=QUOTE_NOT_FOUND in reason)
            new = value_status(spec, a)
            if new == status:
                rep.unchanged += 1
            else:
                rep.changes[(fid, status, new)] += 1
                updates.append((new, tag_id))
        with conn.transaction():
            for new, tag_id in updates:
                conn.execute("update product_facet_tags set status = %s where tag_id = %s",
                             (new, tag_id))
        db.commit()
    return rep
