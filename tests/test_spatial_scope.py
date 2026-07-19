import json
from pathlib import Path

import pytest

from urban_agent.schema import Plan
from urban_agent.spatial_scope import (
    SpatialScope,
    SpatialScopeError,
    centerline_guide_geometry,
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
def centerline_rules():
    return override_scope_rules(load_scope_rules(), partition_mode="centerline")


@pytest.fixture(scope="module")
def centerline_resolved(plan, centerline_rules):
    scopes = {
        "north": ("single", ("north",)),
        "south": ("single", ("south",)),
        "east": ("single", ("east",)),
        "west": ("single", ("west",)),
        "middle": ("single", ("middle",)),
        "north_tip": ("single", ("north_tip",)),
        "south_tip": ("single", ("south_tip",)),
        "waterfront": ("single", ("waterfront",)),
        "east_waterfront": ("intersection", ("east", "waterfront")),
        "middle_west": ("nested", ("middle", "west")),
        "west_middle": ("nested", ("west", "middle")),
        "southeast": ("nested", ("east", "south")),
    }
    return {
        name: resolve_scope_with_rules(
            plan,
            SpatialScope(mode, terms, name),
            centerline_rules,
        )
        for name, (mode, terms) in scopes.items()
    }


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


def test_legacy_rules_default_to_bbox_and_preserve_real_counts(plan):
    rules = load_scope_rules()
    assert len(plan.parcels) == 820
    assert rules.partition_mode == "bbox"
    expected = {
        "north": 61,
        "south": 425,
        "east": 148,
        "west": 84,
        "middle": 248,
        "north_tip": 5,
        "south_tip": 71,
        "waterfront": 153,
    }
    for term, count in expected.items():
        result = resolve_scope_with_rules(
            plan,
            SpatialScope("single", (term,), term),
            rules,
        )
        assert len(result.target_parcel_ids) == count


def test_partition_mode_round_trips_through_rules_file(tmp_path, centerline_rules):
    target = tmp_path / "centerline-rules.json"
    save_scope_rules(centerline_rules, target)
    raw = json.loads(target.read_text(encoding="utf-8"))
    assert raw["partition_mode"] == "centerline"
    assert load_scope_rules(target).partition_mode == "centerline"


def test_centerline_has_fixed_stations_and_auditable_metadata(centerline_rules):
    from shapely.geometry import Point, shape
    from shapely.ops import unary_union

    guides = centerline_guide_geometry(centerline_rules)
    assert guides["algorithm"] == "centerline-v1"
    assert guides["station_count"] == 101
    assert guides["correction_passes"] == 1
    assert guides["centerline_length_m"] > 0
    assert guides["centerline"]["type"] == "LineString"
    assert guides["cross_sections"]
    raw = json.loads(centerline_rules.boundary_path.read_text(encoding="utf-8-sig"))
    boundary = unary_union([shape(feature["geometry"]) for feature in raw["features"]])
    coordinates = guides["centerline"]["coordinates"]
    boundary_y = [point[1] for point in boundary.exterior.coords]
    assert coordinates[0][1] == pytest.approx(min(boundary_y))
    assert coordinates[-1][1] == pytest.approx(max(boundary_y))
    assert all(boundary.covers(Point(point)) for point in coordinates)


def test_centerline_east_west_ratios_use_the_full_local_width(centerline_rules):
    from urban_agent.centerline_partition import ScopeWindow, nested_window

    root = ScopeWindow()
    west = nested_window(root, "west", centerline_rules.ratios)
    east = nested_window(root, "east", centerline_rules.ratios)
    assert west.q0 == 0
    assert west.q1 == centerline_rules.ratios["west"]
    assert east.q0 == 1 - centerline_rules.ratios["east"]
    assert east.q1 == 1


def test_centerline_regions_are_valid_and_middle_is_remaining(centerline_resolved):
    from shapely.geometry import shape

    for result in centerline_resolved.values():
        assert shape(result.geometry).is_valid
        assert result.geometry["type"] in {"Polygon", "MultiPolygon"}
        assert result.rule_parameters["partition_algorithm"] == "centerline-v1"
        assert result.rule_parameters["centerline_intervals"] == 100
    middle = set(centerline_resolved["middle"].target_parcel_ids)
    edges = set().union(*(
        set(centerline_resolved[name].target_parcel_ids)
        for name in ("north", "south", "east", "west")
    ))
    assert middle
    assert middle.isdisjoint(edges)


def test_centerline_tips_intersections_and_nested_scopes(centerline_resolved):
    assert set(centerline_resolved["north_tip"].target_parcel_ids) <= set(
        centerline_resolved["north"].target_parcel_ids
    )
    assert set(centerline_resolved["south_tip"].target_parcel_ids) <= set(
        centerline_resolved["south"].target_parcel_ids
    )
    assert set(centerline_resolved["east_waterfront"].target_parcel_ids) == (
        set(centerline_resolved["east"].target_parcel_ids)
        & set(centerline_resolved["waterfront"].target_parcel_ids)
    )
    assert set(centerline_resolved["middle_west"].target_parcel_ids) <= set(
        centerline_resolved["middle"].target_parcel_ids
    )
    assert set(centerline_resolved["west_middle"].target_parcel_ids) <= set(
        centerline_resolved["west"].target_parcel_ids
    )
    assert set(centerline_resolved["southeast"].target_parcel_ids) <= set(
        centerline_resolved["east"].target_parcel_ids
    )


def test_waterfront_geometry_is_identical_between_partition_modes(
    plan,
    centerline_rules,
):
    from shapely.geometry import shape

    scope = SpatialScope("single", ("waterfront",), "waterfront")
    bbox = resolve_scope_with_rules(plan, scope, load_scope_rules())
    centerline = resolve_scope_with_rules(plan, scope, centerline_rules)
    assert shape(bbox.geometry).equals(shape(centerline.geometry))
    assert bbox.target_parcel_ids == centerline.target_parcel_ids
