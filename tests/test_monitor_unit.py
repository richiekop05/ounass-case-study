"""Offline tests for monitoring (PRD 9): statistics, the shift checks and the stability index."""
from __future__ import annotations

import math

import pytest

from tagger.monitor import MonitorSettings, Obs, compare_runs, compare_stat, compute_stats, stability_index, \
    status_of, summarise

M = MonitorSettings()


def obs(n_auto, n_unknown, n_review=0, n_na=0, conf=0.9, facet="colour_primary", brand="B"):
    out = []
    for i, (st, k) in enumerate([("auto", n_auto), ("unknown", n_unknown), ("review", n_review),
                                 ("not_applicable", n_na)]):
        out += [Obs(f"{st}{j}", facet, st, [conf] if st in ("auto", "review") else [],
                    {"department": "Women / Bags", "category": "Women / Bags / Totes", "brand": brand})
                for j in range(k)]
    return out


def test_counts_add_up_and_rates_exclude_not_applicable():
    s = summarise("f", "all", "", obs(6, 2, 1, 1))
    assert s.products_applicable == 10 == s.auto_count + s.unknown_count + s.review_count + s.not_applicable_count
    assert s.unknown_rate == pytest.approx(2 / 9) and s.coverage == pytest.approx(6 / 9)
    assert s.values_with_confidence == 7 and s.band_90_100 == 7 and s.mean_confidence == pytest.approx(0.9)


def test_review_without_value_counts_as_review_and_no_rows_as_unknown():
    assert status_of([("review", False)]) == "review"
    assert status_of([("auto", True), ("review", True)]) == "auto"
    assert status_of([("abstained", False)]) == "unknown" and status_of([]) == "unknown"
    assert status_of([("not_applicable", False)]) == "not_applicable"


def test_small_segments_get_no_row():
    stats = compute_stats(obs(10, 4, brand="Small") + obs(16, 0, brand="Big"), 15)
    segs = {(s.segment_type, s.segment_value) for s in stats}
    assert ("all", "") in segs and ("brand", "Big") in segs and ("brand", "Small") not in segs
    assert ("department", "Women / Bags") in segs


def run_with_unknown_rate(points, n=50):
    unknown = round(n * points / 100)
    return summarise("colour_primary", "all", "", obs(n - unknown, unknown))


def test_unknown_rate_rise_12_points_is_a_warning_and_25_an_alert():
    base = run_with_unknown_rate(30)
    got = {f.check_name: f.severity for f in compare_stat(base, run_with_unknown_rate(42), M)}
    assert got.get("unknown_rate") == "warning"
    got = {f.check_name: f.severity for f in compare_stat(base, run_with_unknown_rate(56), M)}
    assert got.get("unknown_rate") == "alert"


def test_fewer_than_15_applicable_products_flag_nothing():
    base = summarise("f", "all", "", obs(10, 0))
    cur = summarise("f", "all", "", obs(0, 10))                   # 100 points worse, but only 10 products
    assert compare_stat(base, cur, M) == []


def test_doubling_with_ten_points_is_an_alert():
    base = summarise("f", "all", "", obs(92, 8))                   # 8%
    cur = summarise("f", "all", "", obs(82, 18))                   # 18%: +10 points and more than double
    assert {f.check_name: f.severity for f in compare_stat(base, cur, M)}["unknown_rate"] == "alert"


def test_a_fall_in_unknown_rate_or_a_rise_in_confidence_is_not_flagged():
    base = summarise("f", "all", "", obs(20, 30, conf=0.6))
    cur = summarise("f", "all", "", obs(50, 0, conf=0.95))
    assert {f.check_name for f in compare_stat(base, cur, M)} <= {"stability_index"}


def test_coverage_and_mean_confidence_drops():
    base = summarise("f", "all", "", obs(40, 0, n_review=0, conf=0.9))
    cur = summarise("f", "all", "", obs(28, 0, n_review=12, conf=0.78))
    got = {f.check_name: f.severity for f in compare_stat(base, cur, M)}
    assert got["coverage"] == "alert" and got["review_rate"] == "alert" and got["mean_confidence"] == "alert"


def test_stability_index_hand_calculated_with_an_empty_band():
    base, cur = [10, 10, 10, 10, 10], [0, 0, 0, 0, 50]
    b, c = 10.5 / 52.5, [0.5 / 52.5] * 4 + [50.5 / 52.5]
    expected = sum((ci - b) * math.log(ci / b) for ci in c)
    assert stability_index(base, cur) == pytest.approx(expected) == pytest.approx(3.5163, abs=1e-4)
    assert stability_index(base, base) == 0
    assert stability_index([0, 0, 0, 0, 0], [0, 0, 0, 0, 0]) == 0


def test_a_run_against_itself_raises_nothing():
    stats = compute_stats(obs(30, 10, 5, conf=0.75) + obs(20, 20, facet="neckline", conf=0.85), 15)
    assert compare_runs(stats, stats, M) == []


def test_settings_from_config_ignore_unknown_keys():
    m = MonitorSettings.from_dict({"rate_warning_points": 5, "something_else": 1})
    assert m.rate_warning_points == 5 and m.rate_alert_points == 20
