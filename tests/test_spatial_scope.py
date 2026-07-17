from pathlib import Path

import pytest

from urban_agent.schema import Plan
from urban_agent.spatial_scope import (
    SpatialScopeError,
    load_scope_rules,
    override_scope_rules,
    parse_spatial_scope,
    resolve_scope,
    resolve_scope_with_rules,
    save_scope_rules,
)


ROOT = Path(__file__).resolve().parents[1]
REAL_PLAN = ROOT / "data" / "real" / "fuxing_building_parcel_plan.geojson"


@pytest.fixture(scope="module")
def plan():
    return Plan.from_geojson(REAL_PLAN)


@pytest.fixture(scope="module")
def resolved(plan):
    phrases = {
        "north": "北部",
        "south": "南部",
        "east": "东部",
        "west": "西部",
        "middle": "中部",
        "north_tip": "北岛尖",
        "south_tip": "南岛尖",
        "waterfront": "滨水",
        "east_waterfront": "东部滨水",
        "middle_west": "中部地区的偏西部",
        "west_middle": "西部的中部",
        "southeast": "东南部",
    }
    return {
        key: resolve_scope(plan, parse_spatial_scope(phrase))
        for key, phrase in phrases.items()
    }


@pytest.mark.parametrize(
    ("text", "mode", "terms"),
    [
        ("东部", "single", ("east",)),
        ("北岛尖", "single", ("north_tip",)),
        ("东部滨水", "intersection", ("east", "waterfront")),
        ("北岛尖滨水", "intersection", ("north_tip", "waterfront")),
        ("中部地区的偏西部", "nested", ("middle", "west")),
        ("西部的中部", "nested", ("west", "middle")),
        ("东南部", "nested", ("east", "south")),
    ],
)
def test_parse_supported_phrases(text, mode, terms):
    scope = parse_spatial_scope(text)
    assert scope.mode == mode
    assert scope.terms == terms


def test_parse_rejects_more_than_two_terms():
    with pytest.raises(SpatialScopeError, match="超过两层"):
        parse_spatial_scope("东部滨水地区的南部")


def test_parse_rejects_ambiguous_direction_pair():
    with pytest.raises(SpatialScopeError, match="关系不明确"):
        parse_spatial_scope("东部和西部")


def test_north_and_south_include_their_tips(resolved):
    north = set(resolved["north"].target_parcel_ids)
    south = set(resolved["south"].target_parcel_ids)
    assert set(resolved["north_tip"].target_parcel_ids) <= north
    assert set(resolved["south_tip"].target_parcel_ids) <= south


def test_middle_is_remaining_region(resolved):
    middle = set(resolved["middle"].target_parcel_ids)
    edge_regions = set().union(*(
        set(resolved[name].target_parcel_ids)
        for name in ("north", "south", "east", "west")
    ))
    assert middle
    assert middle.isdisjoint(edge_regions)


def test_intersection_requires_both_memberships(resolved):
    expected = (
        set(resolved["east"].target_parcel_ids)
        & set(resolved["waterfront"].target_parcel_ids)
    )
    assert set(resolved["east_waterfront"].target_parcel_ids) == expected


def test_nested_scopes_stay_inside_their_parent(resolved):
    assert set(resolved["middle_west"].target_parcel_ids) <= set(
        resolved["middle"].target_parcel_ids
    )
    assert set(resolved["west_middle"].target_parcel_ids) <= set(
        resolved["west"].target_parcel_ids
    )
    assert set(resolved["southeast"].target_parcel_ids) <= set(
        resolved["east"].target_parcel_ids
    )


def test_resolution_is_auditable(resolved):
    resolution = resolved["middle_west"]
    payload = resolution.to_dict()
    assert payload["rules_version"] == "1.0"
    assert payload["scope"]["terms"] == ["middle", "west"]
    assert payload["membership"]["directional"] == "centroid_within"
    assert (
        payload["rule_parameters"]["waterfront_distance_m"]
        == load_scope_rules().waterfront_distance_m
    )
    assert len(payload["trace"]) == 2
    assert payload["target_parcel_ids"]


def test_in_memory_preview_changes_result_without_saving(plan):
    saved = load_scope_rules()
    preview_ratios = dict(saved.ratios)
    preview_ratios["east"] = 0.10
    preview = override_scope_rules(saved, ratios=preview_ratios)
    scope = parse_spatial_scope("东部")
    saved_result = resolve_scope_with_rules(plan, scope, saved)
    preview_result = resolve_scope_with_rules(plan, scope, preview)
    assert len(preview_result.target_parcel_ids) < len(saved_result.target_parcel_ids)


def test_rules_are_only_persisted_by_explicit_save(tmp_path):
    saved = load_scope_rules()
    preview = override_scope_rules(saved, waterfront_distance_m=20, version="preview")
    target = tmp_path / "rules.json"
    save_scope_rules(preview, target)
    reloaded = load_scope_rules(target)
    assert reloaded.version == "preview"
    assert reloaded.waterfront_distance_m == 20
