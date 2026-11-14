"""Offline: parsing the facet dictionary, the supplied policy file and the definitions file."""
from collections import Counter
from pathlib import Path

import pytest

from catalog.taxonomy import (TaxonomyError, apply_definitions, apply_policy, is_description, load_taxonomy,
                              parse_default, parse_unit, parse_values)

CONFIG = Path(__file__).parents[1] / "config"
WORKBOOK = CONFIG / "ounass_facet_dictionary_v3_1_corrected.xlsx"
POLICY = CONFIG / "facet_policy.csv"


def fresh(skip_unlisted=False, policy=POLICY):
    if not (WORKBOOK.exists() and POLICY.exists()):
        pytest.skip("facet dictionary or policy file not in config/")
    tax = load_taxonomy(WORKBOOK)
    apply_policy(tax, policy, skip_unlisted=skip_unlisted)
    return tax


@pytest.fixture(scope="module")
def tax():
    return fresh()


def test_policy_file_drives_status_and_policy(tax):
    assert Counter(f.status for f in tax.facets) == {"active": 91, "retired": 19}
    active = [f for f in tax.facets if f.status == "active"]
    assert Counter(f.source_policy for f in active) == {
        "text_only": 40, "judge": 33, "text_first": 9, "derived": 7, "not_tagged": 2}
    retired = {"hue_temperature", "modesty", "silhouette", "fabric_weight", "structure", "jewellery_scale",
               "product_type", "material_composition", "accessory_type", "bag_type", "eyewear_type",
               "food_type", "garment_type", "hat_style", "piece_style", "slg_type"}
    assert retired <= {f.facet_id for f in tax.facets if f.status == "retired"}
    assert tax.unlisted_facets == []                                  # every Register facet has a row


def _policy_without(tmp_path, *facet_ids):
    lines = [l for l in POLICY.read_text().splitlines(keepends=True) if l.split(",")[0] not in facet_ids]
    path = tmp_path / "facet_policy.csv"
    path.write_text("".join(lines))
    return path


def test_register_facet_without_policy_row_stops(tmp_path):
    with pytest.raises(TaxonomyError, match="colour_secondary"):
        fresh(policy=_policy_without(tmp_path, "colour_secondary"))


def test_unlisted_facets_are_skipped_with_the_option(tmp_path):
    tax = fresh(skip_unlisted=True, policy=_policy_without(tmp_path, "colour_secondary", "is_metallic"))
    assert {"colour_secondary", "is_metallic"}.isdisjoint(f.facet_id for f in tax.facets)
    assert {fid for fid, _ in tax.unlisted_facets} == {"colour_secondary", "is_metallic"}
    assert Counter(why for _, _, why in tax.skipped_rows)["facet has no row in the policy file"] > 0


def test_retired_rows_are_skipped(tax):
    assert {why for _, _, why in tax.skipped_rows} == {"facet is retired"}
    active = {f.facet_id for f in tax.facets if f.status == "active"}
    assert all(fid in active for _, fid, _ in tax.applicability)
    assert len(tax.applicability) + len(tax.skipped_rows) == 17516


def test_values_defaults_and_limits(tax):
    by_id = {f.facet_id: f for f in tax.facets}
    assert [v.value for v in by_id["price_band"].values] == ["entry", "mid", "high"]
    season = by_id["season"].values                                   # all-season added by the seed override
    assert [v.label for v in season] == ["warm", "transitional", "cold", "all-season"]
    assert [v.is_exclusive for v in season] == [False, False, False, True] and season[-1].sort_order == 4
    assert all(v.is_exclusive == (v.value == "none") for v in by_id["stone"].values)
    colours = by_id["colour_primary"].values
    assert [c.label for c in colours[-3:]] == ["cream", "beige", "mud grey"] and len(colours) == 22
    assert not any(c.is_exclusive for c in colours)
    assert [c.label for c in by_id["colour_secondary"].values[-3:]] == ["cream", "beige", "mud grey"] and by_id["finish"].values[-1].label == "mesh"
    assert by_id["occasion"].max_values == 3 and by_id["stone"].none_is_exclusive
    assert by_id["pattern"].auto_threshold == 0.8 and by_id["aesthetic"].auto_threshold == 0.7 and by_id["colour_primary"].auto_threshold is None
    assert {f.facet_id: f.default_value for f in tax.facets if f.on_missing == "default"} == {
        "giftable": "false", "exclusivity": "none", "is_set": "false"}
    assert by_id["bag_width_cm"].unit == "cm" and by_id["bag_width_cm"].values == []
    assert by_id["colour_secondary"].source_policy == "text_first" and by_id["colour_secondary"].auto_threshold == 0.8 and by_id["is_metallic"].source_policy == "derived"
    for f in tax.facets:
        assert f.definition is None and all(v.definition is None for v in f.values)


def test_value_parsing_rules():
    formality = parse_values("1 casual; 2 smart-casual; 5 black-tie", "ordinal")
    assert [(v.value, v.label, v.sort_order) for v in formality] == [
        ("casual", "casual", 1), ("smart_casual", "smart-casual", 2), ("black_tie", "black-tie", 5)]
    necklace = parse_values("choker 35-40cm; long 90+", "enum")
    assert [(v.value, v.label) for v in necklace] == [("choker", "choker 35-40cm"), ("long", "long 90+")]
    assert parse_values("rose gold; Mother's Day", "enum")[1].value == "mothers_day"
    with pytest.raises(TaxonomyError):
        parse_values("logo/monogram; logo monogram", "enum")
    assert is_description("slug of the deepest node (L4 where one exists; L5 for Beauty) e.g. hoops")
    assert parse_default("defaults none") == "none" and parse_default("unknown permitted") is None
    assert parse_unit("positive number; cm") == "cm" and parse_unit("SPF value") is None


def test_policy_file_errors(tmp_path):
    text = POLICY.read_text()
    bad = tmp_path / "facet_policy.csv"
    bad.write_text(text.replace("pattern,active,text_first", "pattern,active,extract_only"))
    with pytest.raises(TaxonomyError, match="source_policy"):
        apply_policy(load_taxonomy(WORKBOOK), bad)
    bad.write_text(text.replace(",on_missing,", ",missing_rule,"))
    with pytest.raises(TaxonomyError, match="missing columns"):
        apply_policy(load_taxonomy(WORKBOOK), bad)
    with pytest.raises(TaxonomyError, match="not generated"):
        apply_policy(load_taxonomy(WORKBOOK), tmp_path / "nope.csv")


def test_definitions_file(tmp_path):
    tax = fresh()
    path = tmp_path / "facet_definitions.csv"
    path.write_text("facet_id,value,definition,version\n"
                    "occasion,,Where the piece would be worn.,r1\n"
                    "occasion,wedding_guest,Dressed for a wedding as a guest.,r1\n"
                    "occasion,Ramadan/Eid,Suits Ramadan and Eid gatherings.,r1\n"
                    "occasion,disco,No such value.,r1\n"
                    "nonsense,,No such facet.,r1\n")
    apply_definitions(tax, path)
    occ = next(f for f in tax.facets if f.facet_id == "occasion")
    assert occ.definition == "Where the piece would be worn." and occ.definition_version == "r1"
    defined = {v.value: v.definition for v in occ.values if v.definition}
    assert set(defined) == {"wedding_guest", "ramadan_eid"}           # matched by slug or by label
    assert tax.definitions_loaded == {"facets": 1, "values": 2}
    assert sorted(tax.definitions_unmatched) == [("nonsense", ""), ("occasion", "disco")]
    assert tax.definitions_version == "r1"


def test_definitions_file_is_optional(tmp_path):
    tax = fresh()
    apply_definitions(tax, tmp_path / "missing.csv")
    assert tax.definitions_loaded == {} and any("not found" in w for w in tax.warnings)


def test_supplied_definitions_file_matches_everything():
    path = CONFIG / "facet_definitions.csv"
    if not path.exists():
        pytest.skip("definitions file not supplied")
    tax = fresh()
    apply_definitions(tax, path)
    assert tax.definitions_unmatched == []
    formality = next(f for f in tax.facets if f.facet_id == "formality")
    assert all(v.definition for v in formality.values)              # "1 casual" matched to casual
    by_id = {f.facet_id: f for f in tax.facets}
    assert by_id["colour_secondary"].definition and all(v.definition for v in by_id["is_metallic"].values)
