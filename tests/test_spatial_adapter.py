import json
from pathlib import Path

import pytest

from urban_agent.schema import Plan
from urban_agent.spatial_adapter import (
    GeoJSONSpatialAdapter,
    PlanSpatialAdapter,
    SpatialDataAdapter,
    SpatialDataError,
)
from urban_agent.spatial_scope import (
    SpatialScope,
    load_scope_rules,
    override_scope_rules,
    resolve_scope_dataset,
    resolve_scope_with_rules,
)


ROOT = Path(__file__).resolve().parents[1]
REAL_BOUNDARY = ROOT / "data" / "real" / "fuxing_geojson" / "boundary.geojson"
REAL_PLAN = ROOT / "data" / "real" / "fuxing_building_parcel_plan.geojson"


@pytest.fixture(scope="module")
def real_dataset():
    adapter = GeoJSONSpatialAdapter(REAL_BOUNDARY, REAL_PLAN)
    assert isinstance(adapter, SpatialDataAdapter)
    return adapter.load()


def test_geojson_adapter_preserves_ids_and_renderer_properties(real_dataset):
    assert len(real_dataset.parcel_geometries) == 820
    assert "B001" in real_dataset.parcel_geometries
    assert "height" in real_dataset.parcel_properties["B001"]

    selected = real_dataset.feature_collection(["B001"])
    assert len(selected["features"]) == 1
    assert selected["features"][0]["id"] == "B001"
    assert selected["features"][0]["properties"]["height"] > 0


def test_existing_plan_can_also_use_the_public_adapter():
    dataset = PlanSpatialAdapter(
        Plan.from_geojson(REAL_PLAN),
        REAL_BOUNDARY,
        source_name="legacy-plan",
    ).load()
    assert len(dataset.parcel_geometries) == 820
    assert dataset.source == "legacy-plan"


@pytest.mark.parametrize("partition_mode", ["bbox", "centerline"])
@pytest.mark.parametrize(
    "scope",
    [
        SpatialScope("intersection", ("east", "waterfront"), "东部滨水"),
        SpatialScope("nested", ("middle", "west"), "中部的西部"),
    ],
)
def test_adapter_and_existing_plan_entry_points_match(
    real_dataset,
    partition_mode,
    scope,
):
    plan = Plan.from_geojson(REAL_PLAN)
    rules = override_scope_rules(load_scope_rules(), partition_mode=partition_mode)
    existing = resolve_scope_with_rules(plan, scope, rules)
    adapted = resolve_scope_dataset(real_dataset, scope, rules)
    assert adapted.target_parcel_ids == existing.target_parcel_ids
    assert adapted.scope == existing.scope
    assert adapted.rule_parameters == existing.rule_parameters


def test_custom_external_id_property_is_supported():
    boundary = json.loads(REAL_BOUNDARY.read_text(encoding="utf-8-sig"))
    parcels = json.loads(REAL_PLAN.read_text(encoding="utf-8-sig"))
    feature = parcels["features"][0]
    feature.pop("id")
    feature["properties"]["external_key"] = "external-001"
    parcels["features"] = [feature]

    dataset = GeoJSONSpatialAdapter(
        boundary,
        parcels,
        parcel_id_property="external_key",
        source_name="external-map-v2",
    ).load()
    assert tuple(dataset.parcel_geometries) == ("external-001",)
    assert dataset.source == "external-map-v2"


def test_adapter_rejects_duplicate_ids():
    boundary = json.loads(REAL_BOUNDARY.read_text(encoding="utf-8-sig"))
    parcels = json.loads(REAL_PLAN.read_text(encoding="utf-8-sig"))
    parcels["features"] = [parcels["features"][0], parcels["features"][0]]
    with pytest.raises(SpatialDataError, match="重复"):
        GeoJSONSpatialAdapter(boundary, parcels).load()
