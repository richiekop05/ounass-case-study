"""Monitoring: unknown rate and confidence shift per facet, against a baseline run (PRD section 9).

Needs no golden set: it works on every product in a run. The statistics and checks are pure
functions; the database functions load a run, write run_facet_stats, pick the baseline, and write
monitor_alerts.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field, fields

from .runs import Db

BANDS = ("band_lt_50", "band_50_70", "band_70_80", "band_80_90", "band_90_100")
SEGMENTS = ("department", "category", "brand")
CHECKS = ("unknown_rate", "review_rate", "coverage", "mean_confidence", "stability_index")


@dataclass
class MonitorSettings:
    min_segment_products: int = 15
    min_applicable: int = 15
    min_values: int = 30
    rate_warning_points: float = 10
    rate_alert_points: float = 20
    rate_double_min_points: float = 10
    coverage_warning_points: float = 10
    coverage_alert_points: float = 20
    mean_confidence_warning: float = 0.05
    mean_confidence_alert: float = 0.10
    stability_warning: float = 0.10
    stability_alert: float = 0.25

    @classmethod
    def from_dict(cls, d: dict | None) -> "MonitorSettings":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (d or {}).items() if k in known})


# ------------------------------------------------------------------ statistics

@dataclass
class Obs:
    """One product and one facet in a run."""
    product_id: str
    facet_id: str
    status: str                                     # auto | review | unknown | not_applicable
    confidences: list[float] = field(default_factory=list)
    segments: dict[str, str] = field(default_factory=dict)


@dataclass
class Stat:
    facet_id: str
    segment_type: str
    segment_value: str
    products_applicable: int = 0
    auto_count: int = 0
    review_count: int = 0
    unknown_count: int = 0
    not_applicable_count: int = 0
    unknown_rate: float | None = None
    review_rate: float | None = None
    coverage: float | None = None
    values_with_confidence: int = 0
    band_lt_50: int = 0
    band_50_70: int = 0
    band_70_80: int = 0
    band_80_90: int = 0
    band_90_100: int = 0
    mean_confidence: float | None = None
    median_confidence: float | None = None

    @property
    def key(self):
        return self.facet_id, self.segment_type, self.segment_value

    def bands(self) -> list[int]:
        return [getattr(self, b) for b in BANDS]


def band_of(c: float) -> str:
    return ("band_lt_50" if c < 0.5 else "band_50_70" if c < 0.7 else "band_70_80" if c < 0.8
            else "band_80_90" if c < 0.9 else "band_90_100")


def status_of(rows: list[tuple[str, bool]]) -> str:
    """Saved rows (status, has_value) for one product and facet -> one status.
    A review row with no value counts as review; no rows at all counts as unknown."""
    statuses = {s for s, _ in rows}
    if any(s == "auto" and v for s, v in rows):
        return "auto"
    if "review" in statuses:
        return "review"
    if "not_applicable" in statuses:
        return "not_applicable"
    return "unknown"


def summarise(facet_id: str, seg_type: str, seg_value: str, obs: list[Obs]) -> Stat:
    s = Stat(facet_id, seg_type, seg_value, products_applicable=len(obs))
    confs = []
    for o in obs:
        setattr(s, f"{o.status}_count", getattr(s, f"{o.status}_count") + 1)
        confs += o.confidences
    denom = s.products_applicable - s.not_applicable_count
    if denom:
        s.unknown_rate, s.review_rate, s.coverage = (s.unknown_count / denom, s.review_count / denom,
                                                     s.auto_count / denom)
    s.values_with_confidence = len(confs)
    for c in confs:
        setattr(s, band_of(c), getattr(s, band_of(c)) + 1)
    if confs:
        s.mean_confidence = sum(confs) / len(confs)
        s.median_confidence = statistics.median(confs)
    return s


def compute_stats(obs: list[Obs], min_segment_products: int) -> list[Stat]:
    """`all` always; a department, category or brand only with at least `min_segment_products`."""
    by_facet: dict[str, list[Obs]] = defaultdict(list)
    for o in obs:
        by_facet[o.facet_id].append(o)
    out = []
    for fid in sorted(by_facet):
        items = by_facet[fid]
        out.append(summarise(fid, "all", "", items))
        for seg in SEGMENTS:
            groups: dict[str, list[Obs]] = defaultdict(list)
            for o in items:
                if o.segments.get(seg):
                    groups[o.segments[seg]].append(o)
            for value in sorted(groups):
                if len(groups[value]) >= min_segment_products:
                    out.append(summarise(fid, seg, value, groups[value]))
    return out


# ------------------------------------------------------------------ checks

def stability_index(base: list[int], cur: list[int]) -> float:
    """Sum over bands of (cur share - base share) * ln(cur share / base share), with half a count
    added to every band in both runs so an empty band cannot divide by zero."""
    b = [x + 0.5 for x in base]
    c = [x + 0.5 for x in cur]
    bt, ct = sum(b), sum(c)
    return sum((ci / ct - bi / bt) * math.log((ci / ct) / (bi / bt)) for bi, ci in zip(b, c))


@dataclass
class Finding:
    facet_id: str
    segment_type: str
    segment_value: str
    check_name: str
    baseline_value: float
    current_value: float
    change: float
    severity: str

    def size(self) -> float:
        """For ranking: points for rates, hundredths for confidence and stability."""
        return abs(self.change) * 100


def _rise(check, base, cur, warn, alert, double_min, applicable_ok) -> Finding | None:
    if not applicable_ok or base is None or cur is None:
        return None
    pts = (cur - base) * 100
    if pts >= alert or (base > 0 and cur > 2 * base and pts >= double_min):
        sev = "alert"
    elif pts >= warn:
        sev = "warning"
    else:
        return None
    return check, base, cur, cur - base, sev


def compare_stat(base: Stat, cur: Stat, m: MonitorSettings) -> list[Finding]:
    """The checks in PRD 9.3. A fall in unknown rate or a rise in confidence is never flagged."""
    out = []
    enough = base.products_applicable >= m.min_applicable and cur.products_applicable >= m.min_applicable
    for check in ("unknown_rate", "review_rate"):
        r = _rise(check, getattr(base, check), getattr(cur, check), m.rate_warning_points, m.rate_alert_points,
                  m.rate_double_min_points, enough)
        if r:
            out.append(r)
    if enough and base.coverage is not None and cur.coverage is not None:
        drop = (base.coverage - cur.coverage) * 100
        sev = "alert" if drop >= m.coverage_alert_points else "warning" if drop >= m.coverage_warning_points else None
        if sev:
            out.append(("coverage", base.coverage, cur.coverage, cur.coverage - base.coverage, sev))
    values_ok = base.values_with_confidence >= m.min_values and cur.values_with_confidence >= m.min_values
    if values_ok and base.mean_confidence is not None and cur.mean_confidence is not None:
        drop = base.mean_confidence - cur.mean_confidence
        sev = ("alert" if drop >= m.mean_confidence_alert - 1e-12 else
               "warning" if drop >= m.mean_confidence_warning - 1e-12 else None)
        if sev:
            out.append(("mean_confidence", base.mean_confidence, cur.mean_confidence, -drop, sev))
    if values_ok:
        psi = stability_index(base.bands(), cur.bands())
        sev = "alert" if psi >= m.stability_alert else "warning" if psi >= m.stability_warning else None
        if sev:
            out.append(("stability_index", 0.0, psi, psi, sev))
    return [Finding(cur.facet_id, cur.segment_type, cur.segment_value, *f) for f in out]


def compare_runs(base: list[Stat], cur: list[Stat], m: MonitorSettings) -> list[Finding]:
    """Only segments present in both runs are compared; rates are compared as rates."""
    base_by = {s.key: s for s in base}
    out = []
    for s in cur:
        if s.key in base_by:
            out += compare_stat(base_by[s.key], s, m)
    return out


# ------------------------------------------------------------------ database

def l3_path(path: str) -> str:
    return " / ".join(path.split(" / ")[:3])


def load_observations(db: Db, run_id: int) -> tuple[str, list[Obs]]:
    conn = db.conn
    stage, = conn.execute("select stage from tagging_runs where tagging_run_id = %s", (run_id,)).fetchone()
    products = conn.execute(
        """select rp.product_id, rp.category_id, rp.category_status, rp.category_confidence, c.audience,
                  c.department, c.path, p.brand
           from tagging_run_products rp join products p using (product_id)
           left join categories c on c.node_id = rp.category_id
           where rp.tagging_run_id = %s and rp.status = 'done'""", (run_id,)).fetchall()

    def segs(aud, dept, path, brand):
        return {"department": f"{aud} / {dept}" if aud and dept else "", "category": l3_path(path or ""),
                "brand": brand or ""}

    obs = []
    if stage == "categorise":
        for pid, cat, st, conf, aud, dept, path, brand in products:
            st = "review" if st == "flagged" else st      # a flagged category is waiting for review too
            obs.append(Obs(pid, "category", st, [float(conf)] if conf is not None else [],
                           segs(aud, dept, path, brand)))
        return stage, obs

    rows: dict[tuple, list] = defaultdict(list)
    confs: dict[tuple, list] = defaultdict(list)
    for pid, fid, st, has_value, conf in conn.execute(
            """select product_id, facet_id, status, num_nonnulls(facet_value_id, value_number, value_text) > 0,
                      confidence from product_facet_tags where tagging_run_id = %s""", (run_id,)):
        rows[(pid, fid)].append((st, has_value))
        if conf is not None:
            confs[(pid, fid)].append(float(conf))
    run_facets = {fid for _, fid in rows}
    applicable: dict[str, set] = defaultdict(set)
    for cat, fid in conn.execute(
            """select cf.category_id, cf.facet_id from category_facets cf join facets f using (facet_id)
               where f.status = 'active' and cf.category_id = any(%s)""",
            (list({p[1] for p in products if p[1]}),)):
        if fid in run_facets:
            applicable[cat].add(fid)
    for pid, cat, _, _, aud, dept, path, brand in products:
        sg = segs(aud, dept, path, brand)
        for fid in sorted(applicable.get(cat, ())):
            obs.append(Obs(pid, fid, status_of(rows.get((pid, fid), [])), confs.get((pid, fid), []), sg))
    return stage, obs


STAT_COLS = [f.name for f in fields(Stat)]


def save_stats(db: Db, run_id: int, stats: list[Stat]) -> None:
    conn = db.conn
    conn.execute("delete from run_facet_stats where tagging_run_id = %s", (run_id,))
    with conn.cursor() as cur:
        cur.executemany(
            f"insert into run_facet_stats (tagging_run_id, {', '.join(STAT_COLS)}) "
            f"values (%s, {', '.join(['%s'] * len(STAT_COLS))})",
            [(run_id, *[getattr(s, c) for c in STAT_COLS]) for s in stats])


def load_stats(db: Db, run_id: int) -> list[Stat]:
    rows = db.conn.execute(f"select {', '.join(STAT_COLS)} from run_facet_stats where tagging_run_id = %s",
                           (run_id,)).fetchall()
    out = []
    for r in rows:
        s = Stat(*r)
        for k in ("unknown_rate", "review_rate", "coverage", "mean_confidence", "median_confidence"):
            v = getattr(s, k)
            setattr(s, k, float(v) if v is not None else None)
        out.append(s)
    return out


def pick_baseline(db: Db, run_id: int, stage: str) -> int | None:
    """The pinned run for the stage; else the most recent earlier non-drill run of the same stage
    that covered at least half as many products."""
    conn = db.conn
    pinned = conn.execute("select tagging_run_id from monitor_baseline where stage = %s", (stage,)).fetchone()
    if pinned and pinned[0] != run_id:
        return pinned[0]
    n = conn.execute("select count(*) from tagging_run_products where tagging_run_id = %s and status = 'done'",
                     (run_id,)).fetchone()[0]
    row = conn.execute(
        """select r.tagging_run_id from tagging_runs r
           where r.stage = %s and not r.is_drill and r.purpose = 'tagger' and r.tagging_run_id < %s
             and (select count(*) from tagging_run_products p where p.tagging_run_id = r.tagging_run_id
                    and p.status = 'done') * 2 >= %s
           order by r.started_at desc, r.tagging_run_id desc limit 1""", (stage, run_id, n)).fetchone()
    return row[0] if row else None


def run_monitor(db: Db, run_id: int, settings: MonitorSettings, baseline_id: int | None = None) -> dict:
    with db.lock:
        conn = db.conn
        row = conn.execute("select stage, products_succeeded from tagging_runs where tagging_run_id = %s",
                           (run_id,)).fetchone()
        if not row:
            raise LookupError(f"run {run_id} not found")
        stage, succeeded = row
        if not succeeded:
            return {"run_id": run_id, "skipped": "no succeeded products"}
        with conn.transaction():
            _, obs = load_observations(db, run_id)
            stats = compute_stats(obs, settings.min_segment_products)
            save_stats(db, run_id, stats)
            if baseline_id is None:
                baseline_id = pick_baseline(db, run_id, stage)
            elif conn.execute("select stage from tagging_runs where tagging_run_id = %s",
                              (baseline_id,)).fetchone() != (stage,):
                raise ValueError(f"baseline {baseline_id} is not a {stage} run")
            findings = []
            if baseline_id is not None:
                base = load_stats(db, baseline_id)
                if not base:       # an older run never monitored: compute its stats first
                    _, bobs = load_observations(db, baseline_id)
                    base = compute_stats(bobs, settings.min_segment_products)
                    save_stats(db, baseline_id, base)
                findings = compare_runs(base, stats, settings)
                kept = {(a, b, c, d): (st, note, at) for a, b, c, d, st, note, at in conn.execute(
                    """select facet_id, segment_type, segment_value, check_name, status, note, acknowledged_at
                       from monitor_alerts where tagging_run_id = %s and baseline_run_id = %s""",
                    (run_id, baseline_id))}
                conn.execute("delete from monitor_alerts where tagging_run_id = %s and baseline_run_id = %s",
                             (run_id, baseline_id))
                for f in findings:
                    st, note, at = kept.get((f.facet_id, f.segment_type, f.segment_value, f.check_name),
                                            ("open", None, None))
                    conn.execute(
                        """insert into monitor_alerts (tagging_run_id, baseline_run_id, facet_id, segment_type,
                               segment_value, check_name, baseline_value, current_value, change, severity, status,
                               note, acknowledged_at)
                           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (run_id, baseline_id, f.facet_id, f.segment_type, f.segment_value, f.check_name,
                         f.baseline_value, f.current_value, f.change, f.severity, st, note, at))
            facets_checked = len({s.facet_id for s in stats})
            warnings = sum(f.severity == "warning" for f in findings)
            alerts = sum(f.severity == "alert" for f in findings)
            conn.execute(
                """insert into monitor_runs (tagging_run_id, baseline_run_id, facets_checked, warnings, alerts)
                   values (%s, %s, %s, %s, %s)
                   on conflict (tagging_run_id) do update set baseline_run_id = excluded.baseline_run_id,
                       facets_checked = excluded.facets_checked, warnings = excluded.warnings,
                       alerts = excluded.alerts, checked_at = now()""",
                (run_id, baseline_id, facets_checked, warnings, alerts))
        db.commit()
    return {"run_id": run_id, "stage": stage, "baseline_run_id": baseline_id, "facets_checked": facets_checked,
            "stats_rows": len(stats), "warnings": warnings, "alerts": alerts,
            "largest": sorted(findings, key=lambda f: (f.severity != "alert", -f.size()))[:10]}


def render(r: dict) -> str:
    if r.get("skipped"):
        return f"monitor run {r['run_id']}: skipped ({r['skipped']})"
    base = r["baseline_run_id"]
    lines = [f"monitor run {r['run_id']} ({r['stage']}) vs "
             + (f"baseline {base}" if base else "no baseline yet (stats recorded only)")
             + f": {r['facets_checked']} facets checked, {r['warnings']} warnings, {r['alerts']} alerts"]
    for f in r["largest"]:
        seg = "all" if f.segment_type == "all" else f"{f.segment_type}={f.segment_value}"
        if f.check_name in ("unknown_rate", "review_rate", "coverage"):
            vals = f"{100 * f.baseline_value:.0f}% -> {100 * f.current_value:.0f}% ({100 * f.change:+.0f} pts)"
        elif f.check_name == "stability_index":
            vals = f"index {f.current_value:.3f}"
        else:
            vals = f"{f.baseline_value:.3f} -> {f.current_value:.3f} ({f.change:+.3f})"
        lines.append(f"  {f.severity:<7} {f.facet_id:<22} {seg:<34} {f.check_name:<16} {vals}")
    return "\n".join(lines)


def pin_baseline(db: Db, run_id: int) -> dict:
    with db.lock:
        row = db.conn.execute("select stage, is_drill from tagging_runs where tagging_run_id = %s",
                              (run_id,)).fetchone()
        if not row:
            raise LookupError(f"run {run_id} not found")
        if row[1]:
            raise ValueError(f"run {run_id} is a drill run and cannot be a baseline")
        db.conn.execute("""insert into monitor_baseline (stage, tagging_run_id) values (%s, %s)
                           on conflict (stage) do update set tagging_run_id = excluded.tagging_run_id,
                               pinned_at = now()""", (row[0], run_id))
        db.commit()
    return {"stage": row[0], "baseline_run_id": run_id}


def history(db: Db, facet_id: str, segment: str | None = None) -> list[dict]:
    seg_type, seg_value = ("all", "")
    if segment:
        if "=" not in segment:
            raise ValueError("--segment looks like brand=NAME, department=NAME or category=PATH")
        seg_type, seg_value = segment.split("=", 1)
    rows = db.execute(
        """select r.tagging_run_id, r.stage, r.started_at, r.model_profile, s.products_applicable, s.unknown_rate,
                  s.review_rate, s.coverage, s.mean_confidence
           from run_facet_stats s join tagging_runs r using (tagging_run_id)
           where s.facet_id = %s and s.segment_type = %s and s.segment_value = %s and not r.is_drill
             and r.purpose = 'tagger'
           order by r.started_at, r.tagging_run_id""", (facet_id, seg_type, seg_value)).fetchall()
    keys = ("run_id", "stage", "started_at", "model_profile", "products_applicable", "unknown_rate", "review_rate",
            "coverage", "mean_confidence")
    return [dict(zip(keys, r)) for r in rows]


def acknowledge(db: Db, alert_id: int, note: str | None) -> dict:
    with db.lock:
        n = db.conn.execute("""update monitor_alerts set status = 'acknowledged', acknowledged_at = now(),
                                   note = coalesce(%s, note) where alert_id = %s""", (note, alert_id)).rowcount
        db.commit()
    if not n:
        raise LookupError(f"alert {alert_id} not found")
    return {"alert_id": alert_id, "status": "acknowledged"}
