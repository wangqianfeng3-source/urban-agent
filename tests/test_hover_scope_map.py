import json
from pathlib import Path

import urban_agent.hover_scope_map as hover_map


ROOT = Path(__file__).resolve().parents[1]
REAL_BOUNDARY = ROOT / "data" / "real" / "fuxing_geojson" / "boundary.geojson"
REAL_PLAN = ROOT / "data" / "real" / "fuxing_building_parcel_plan.geojson"


def test_component_uses_client_side_hover_elevation_without_legacy_api():
    script = hover_map._COMPONENT_JS
    assert "onHover" in script
    assert "getElevation" in script
    assert "updateTriggers" in script
    assert "transitions" in script
    assert 'addEventListener("pointerleave"' in script
    assert 'removeEventListener("pointerleave"' in script
    assert 'addEventListener("mouseleave"' in script
    assert 'removeEventListener("mouseleave"' in script
    assert 'document.addEventListener("pointermove"' in script
    assert 'document.removeEventListener("pointermove"' in script
    assert 'addEventListener("pointerout"' in script
    assert 'removeEventListener("pointerout"' in script
    assert 'data-testid="hover-scope-map"' in hover_map._COMPONENT_HTML
    assert "setComponentValue" not in script
    assert "window.Streamlit" not in script


def test_renderer_tags_scope_members_without_mutating_source(monkeypatch):
    boundary = json.loads(REAL_BOUNDARY.read_text(encoding="utf-8-sig"))
    parcels = json.loads(REAL_PLAN.read_text(encoding="utf-8-sig"))
    first_id = str(parcels["features"][0]["id"])
    second_id = str(parcels["features"][1]["id"])
    original_first_properties = dict(parcels["features"][0]["properties"])
    captured = {}

    def fake_component(**kwargs):
        captured.update(kwargs)
        return "mounted"

    monkeypatch.setattr(hover_map, "_HOVER_SCOPE_MAP", fake_component)
    result = hover_map.render_hover_scope_map(
        parcels=parcels,
        target_parcel_ids=[first_id],
        boundary=boundary,
        lift_meters=8,
        transition_ms=180,
    )

    assert result == "mounted"
    features = captured["data"]["parcels"]["features"]
    by_id = {str(feature["id"]): feature for feature in features}
    assert by_id[first_id]["properties"]["scope_member"] is True
    assert by_id[second_id]["properties"]["scope_member"] is False
    assert parcels["features"][0]["properties"] == original_first_properties
    assert captured["data"]["view_state"]["pitch"] > 0
    assert captured["height"] >= 520


def test_renderer_rejects_invalid_lift_settings(monkeypatch):
    boundary = json.loads(REAL_BOUNDARY.read_text(encoding="utf-8-sig"))
    parcels = json.loads(REAL_PLAN.read_text(encoding="utf-8-sig"))
    monkeypatch.setattr(hover_map, "_HOVER_SCOPE_MAP", lambda **kwargs: kwargs)

    try:
        hover_map.render_hover_scope_map(
            parcels=parcels,
            target_parcel_ids=[],
            boundary=boundary,
            lift_meters=0,
        )
    except ValueError as exc:
        assert "1-50" in str(exc)
    else:
        raise AssertionError("lift_meters=0 should be rejected")
