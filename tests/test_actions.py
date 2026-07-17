from pathlib import Path

import pytest

from urban_agent.actions import ActionError, apply_action
from urban_agent.schema import Plan

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample" / "fuxing_demo.geojson"


@pytest.fixture
def plan():
    return Plan.from_geojson(SAMPLE)


def test_set_landuse_ok(plan):
    apply_action(plan, {"name": "set_landuse",
                        "params": {"parcel_id": "P01", "landuse": "T"}})
    assert plan.parcel("P01").landuse == "T"
    assert plan.history[-1]["action"] == "set_landuse"


def test_invalid_landuse_rejected(plan):
    with pytest.raises(ActionError):
        apply_action(plan, {"name": "set_landuse",
                            "params": {"parcel_id": "P01", "landuse": "X"}})


def test_historic_parcel_frozen(plan):
    with pytest.raises(ActionError, match="历史风貌"):
        apply_action(plan, {"name": "set_landuse",
                            "params": {"parcel_id": "P10", "landuse": "C"}})


def test_far_out_of_range_rejected(plan):
    with pytest.raises(ActionError):
        apply_action(plan, {"name": "adjust_far",
                            "params": {"parcel_id": "P04", "delta": 2.0}})  # 3.0+2.0 > 3.5
    assert plan.parcel("P04").far == 3.0  # 拒绝后原值不变


def test_unknown_action_rejected(plan):
    with pytest.raises(ActionError):
        apply_action(plan, {"name": "demolish_everything", "params": {}})
