from pathlib import Path

from urban_agent.schema import Plan

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample" / "fuxing_demo.geojson"


def test_load_sample():
    plan = Plan.from_geojson(SAMPLE)
    assert len(plan.parcels) == 10
    assert plan.total_area == 106000
    assert plan.parcel("P01").landuse == "M"
    assert plan.parcel("P10").flag("historic")


def test_copy_bumps_version():
    plan = Plan.from_geojson(SAMPLE)
    new = plan.copy()
    assert new.version == plan.version + 1
    new.parcel("P01").far = 9.9
    assert plan.parcel("P01").far != 9.9  # 深拷贝互不影响


def test_roundtrip(tmp_path):
    plan = Plan.from_geojson(SAMPLE)
    out = tmp_path / "out.geojson"
    plan.to_geojson(out)
    again = Plan.from_geojson(out)
    assert again.total_area == plan.total_area
    assert again.parcel("P05").facilities == ["clinic"]
