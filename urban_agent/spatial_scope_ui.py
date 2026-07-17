"""Independent Streamlit UI for calibrating spatial-scope rules."""
from __future__ import annotations

import json
from pathlib import Path

import pydeck as pdk
import streamlit as st

from .schema import LANDUSE_CODES, Plan
from .spatial_scope import (
    DEFAULT_RULES_PATH,
    SpatialScopeError,
    load_scope_rules,
    override_scope_rules,
    parse_spatial_scope,
    resolve_scope_with_rules,
    save_scope_rules,
    scope_rules_to_dict,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PLAN_PATH = ROOT / "data" / "real" / "fuxing_building_parcel_plan.geojson"


@st.cache_data(show_spinner=False)
def _load_plan(path: str, modified_ns: int) -> Plan:
    del modified_ns
    return Plan.from_geojson(path)


def _geometry_points(geometry: dict) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []

    def walk(node):
        if (
            isinstance(node, (list, tuple))
            and len(node) >= 2
            and isinstance(node[0], (int, float))
            and isinstance(node[1], (int, float))
        ):
            points.append((float(node[0]), float(node[1])))
            return
        if isinstance(node, (list, tuple)):
            for child in node:
                walk(child)

    walk(geometry.get("coordinates", []))
    return points


def _map_layers(plan: Plan, resolution, boundary_raw: dict):
    selected_ids = set(resolution.target_parcel_ids)
    background_features = []
    selected_features = []
    for feature in plan.to_dict()["features"]:
        props = feature.setdefault("properties", {})
        parcel_id = str(feature["id"])
        props["label"] = (
            f"{parcel_id} · {LANDUSE_CODES.get(props.get('landuse'), props.get('landuse'))}"
        )
        if parcel_id in selected_ids:
            props["fill"] = [255, 132, 32, 205]
            props["line"] = [180, 72, 0, 255]
            selected_features.append(feature)
        else:
            props["fill"] = [190, 198, 210, 65]
            props["line"] = [135, 145, 160, 110]
            background_features.append(feature)

    zone_feature = {
        "type": "Feature",
        "id": "scope-preview",
        "geometry": resolution.geometry,
        "properties": {
            "label": "当前空间范围",
            "fill": [35, 123, 255, 70],
            "line": [20, 85, 205, 245],
        },
    }
    boundary_features = json.loads(json.dumps(boundary_raw)).get("features", [])
    for feature in boundary_features:
        props = feature.setdefault("properties", {})
        props["label"] = "复兴岛边界"
        props["line"] = [220, 38, 38, 255]

    return [
        pdk.Layer(
            "GeoJsonLayer",
            {"type": "FeatureCollection", "features": background_features},
            pickable=True,
            stroked=True,
            filled=True,
            get_fill_color="properties.fill",
            get_line_color="properties.line",
            line_width_min_pixels=1,
        ),
        pdk.Layer(
            "GeoJsonLayer",
            {"type": "FeatureCollection", "features": [zone_feature]},
            pickable=True,
            stroked=True,
            filled=True,
            get_fill_color="properties.fill",
            get_line_color="properties.line",
            line_width_min_pixels=3,
        ),
        pdk.Layer(
            "GeoJsonLayer",
            {"type": "FeatureCollection", "features": selected_features},
            pickable=True,
            stroked=True,
            filled=True,
            get_fill_color="properties.fill",
            get_line_color="properties.line",
            line_width_min_pixels=2,
        ),
        pdk.Layer(
            "GeoJsonLayer",
            {"type": "FeatureCollection", "features": boundary_features},
            pickable=True,
            stroked=True,
            filled=False,
            get_line_color="properties.line",
            line_width_min_pixels=3,
        ),
    ]


def _render_map(plan: Plan, resolution, boundary_raw: dict) -> None:
    boundary_points = [
        point
        for feature in boundary_raw.get("features", [])
        for point in _geometry_points(feature["geometry"])
    ]
    longitude = sum(point[0] for point in boundary_points) / len(boundary_points)
    latitude = sum(point[1] for point in boundary_points) / len(boundary_points)
    deck = pdk.Deck(
        map_style=None,
        initial_view_state=pdk.ViewState(
            longitude=longitude,
            latitude=latitude,
            zoom=13.7,
            pitch=0,
        ),
        layers=_map_layers(plan, resolution, boundary_raw),
        tooltip={"text": "{label}"},
    )
    st.pydeck_chart(deck, use_container_width=True)


def _ratio_controls(saved_rules):
    ratios = saved_rules.ratios
    st.sidebar.subheader("方向分区比例")
    north = st.sidebar.slider("北部（占父区域高度）", 5, 49, round(ratios["north"] * 100), 1)
    south = st.sidebar.slider("南部（占父区域高度）", 5, 49, round(ratios["south"] * 100), 1)
    east = st.sidebar.slider("东部（占父区域宽度）", 5, 49, round(ratios["east"] * 100), 1)
    west = st.sidebar.slider("西部（占父区域宽度）", 5, 49, round(ratios["west"] * 100), 1)
    north_tip = st.sidebar.slider(
        "北岛尖（包含于北部）",
        1,
        49,
        round(ratios["north_tip"] * 100),
        1,
    )
    south_tip = st.sidebar.slider(
        "南岛尖（包含于南部）",
        1,
        49,
        round(ratios["south_tip"] * 100),
        1,
    )
    return {
        "north": north / 100,
        "south": south / 100,
        "east": east / 100,
        "west": west / 100,
        "north_tip": north_tip / 100,
        "south_tip": south_tip / 100,
    }


def render_spatial_scope_calibration() -> None:
    st.title("空间范围标定")
    st.caption(
        "独立预览页面：蓝色是解析范围，橙色是命中地块，灰色是未命中地块。"
        "滑块只改变当前预览，点击保存后才会写入规则 JSON。"
    )

    saved_rules = load_scope_rules()
    plan_path = st.sidebar.text_input("地块数据", str(DEFAULT_PLAN_PATH))
    plan_file = Path(plan_path)
    if not plan_file.exists():
        st.error(f"找不到地块数据：{plan_file}")
        return
    plan = _load_plan(str(plan_file), plan_file.stat().st_mtime_ns)

    ratios = _ratio_controls(saved_rules)
    waterfront_distance = st.sidebar.slider(
        "滨水带向内距离（米）",
        1,
        100,
        round(saved_rules.waterfront_distance_m),
        1,
    )
    version = st.sidebar.text_input("规则版本", saved_rules.version)

    try:
        preview_rules = override_scope_rules(
            saved_rules,
            ratios=ratios,
            waterfront_distance_m=waterfront_distance,
            version=version.strip() or saved_rules.version,
        )
    except SpatialScopeError as exc:
        st.error(f"参数不合法：{exc}")
        return

    preview_changed = scope_rules_to_dict(preview_rules) != scope_rules_to_dict(saved_rules)
    if preview_changed:
        st.sidebar.warning("当前为未保存预览")
    else:
        st.sidebar.success("当前参数与已保存规则一致")
    if st.sidebar.button("保存当前参数到规则文件", type="primary"):
        target = save_scope_rules(preview_rules, DEFAULT_RULES_PATH)
        st.sidebar.success(f"已保存：{target.name}")

    expression = st.text_input(
        "测试方位描述",
        "东部滨水",
        help="例如：北岛尖、中部的西部、西部的中部、东南部、东部滨水",
    )
    try:
        scope = parse_spatial_scope(expression)
        if scope is None:
            st.info("请输入一个可识别的方位描述。")
            return
        resolution = resolve_scope_with_rules(plan, scope, preview_rules)
    except SpatialScopeError as exc:
        st.error(f"无法解析：{exc}")
        return

    mode_labels = {"single": "单一区域", "intersection": "交集", "nested": "递进"}
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("总地块", len(plan.parcels))
    col2.metric("命中地块", len(resolution.target_parcel_ids))
    col3.metric("解析方式", mode_labels[scope.mode])
    col4.metric("规则版本", preview_rules.version)
    st.code(
        json.dumps(
            {"mode": scope.mode, "terms": list(scope.terms)},
            ensure_ascii=False,
        ),
        language="json",
    )

    boundary_raw = json.loads(preview_rules.boundary_path.read_text(encoding="utf-8-sig"))
    _render_map(plan, resolution, boundary_raw)
    st.caption("图例：橙色＝命中地块　蓝色＝空间范围　灰色＝其他地块　红线＝复兴岛边界")

    download_col, trace_col = st.columns(2)
    scope_geojson = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "id": "scope-preview",
            "geometry": resolution.geometry,
            "properties": {
                "raw_text": expression,
                "mode": scope.mode,
                "terms": list(scope.terms),
                "rules_version": preview_rules.version,
            },
        }],
    }
    download_col.download_button(
        "下载当前范围 GeoJSON",
        json.dumps(scope_geojson, ensure_ascii=False, indent=2),
        file_name="spatial_scope_preview.geojson",
        mime="application/geo+json",
        use_container_width=True,
    )
    trace_col.download_button(
        "下载解析记录 JSON",
        json.dumps(resolution.to_dict(), ensure_ascii=False, indent=2),
        file_name="spatial_scope_trace.json",
        mime="application/json",
        use_container_width=True,
    )

    with st.expander("查看命中地块与追溯记录"):
        st.dataframe(
            [{"地块 ID": parcel_id} for parcel_id in resolution.target_parcel_ids],
            use_container_width=True,
            hide_index=True,
        )
        st.json(resolution.to_dict(), expanded=False)
