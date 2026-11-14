from pathlib import Path

import pytest

from scraper.sampling import check_plan, draft_plan, load_spine

ROOT = Path(__file__).parents[1]
SPINE = ROOT / "ounass_spine_v5_nodes.csv"
if not SPINE.exists():
    SPINE = ROOT / "config" / "spine_v5_nodes_from_xlsx.csv"


@pytest.fixture(scope="module")
def nodes():
    if not SPINE.exists():
        pytest.skip("spine CSV not generated")
    return load_spine(SPINE)


@pytest.fixture(scope="module")
def site_paths():
    text = (Path(__file__).parent / "fixtures" / "category_paths.txt").read_text()
    return [line.strip().replace("https://www.ounass.ae", "") for line in text.splitlines() if line.strip()]


def test_spine_shape(nodes):
    by_level = {}
    for n in nodes:
        by_level[n.level] = by_level.get(n.level, 0) + 1
    assert by_level["L3"] == 262 and by_level["L4"] == 484
    ids = {n.node_id for n in nodes}
    assert all(n.parent_id in ids for n in nodes if n.parent_id)


def test_holding_nodes_detected(nodes):
    holding = {n.name for n in nodes if n.is_holding}
    assert holding == {"Heels", "Other Shoes", "Other Accessories", "Other Bags"}


def test_draft_plan_meets_prd_rules(nodes, site_paths):
    rows, notes = draft_plan(nodes, site_paths, 250)
    assert sum(r.quota for r in rows) == 250
    assert all(r.quota >= 3 for r in rows)
    assert check_plan(rows, nodes) == []  # no holding nodes, every node exists
    covered = {r.spine_node_id for r in rows if r.spine_node_id}
    assert len(covered) >= 60
    placement = [r for r in rows if not r.spine_node_id]
    assert sum(r.quota for r in placement) == 10
    assert {r.listing_url.rsplit("/", 1)[-1] for r in placement} == {"beachwear", "loungewear"}
    assert not any("Below" in n for n in notes), notes
    assert len({r.listing_url for r in rows}) == len(rows)  # no page used twice
