"""Independent Streamlit UI for calibrating spatial-scope rules."""
from __future__ import annotations

import json
from pathlib import Path

import pydeck as pdk
import streamlit as st

from .hover_scope_map import render_hover_scope_map
from .schema import LANDUSE_CODES, Plan
from .spatial_scope import (
    DEFAULT_RULES_PATH,
    SpatialScopeError,
    centerline_guide_geometry,
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


def _map_layers(plan: Plan, resolution, boundary_raw: dict, guides: dict | None = None):
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
            props["fill"] = [45, 126, 247, 55]
            props["line"] = [15, 70, 180, 255]
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

    layers = [
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
    ]
    if guides:
        cross_section_features = [
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {"label": "中心线校正截面", "line": [0, 145, 170, 210]},
            }
            for geometry in guides["cross_sections"]
        ]
        centerline_feature = {
            "type": "Feature",
            "geometry": guides["centerline"],
            "properties": {"label": "岛体曲线中心线", "line": [139, 61, 190, 255]},
        }
        layers.extend([
            pdk.Layer(
                "GeoJsonLayer",
                {"type": "FeatureCollection", "features": cross_section_features},
                pickable=True,
                stroked=True,
                filled=False,
                get_line_color="properties.line",
                line_width_min_pixels=2,
            ),
            pdk.Layer(
                "GeoJsonLayer",
                {"type": "FeatureCollection", "features": [centerline_feature]},
                pickable=True,
                stroked=True,
                filled=False,
                get_line_color="properties.line",
                line_width_min_pixels=4,
            ),
        ])
    layers.append(
        pdk.Layer(
            "GeoJsonLayer",
            {"type": "FeatureCollection", "features": boundary_features},
            pickable=True,
            stroked=True,
            filled=False,
            get_line_color="properties.line",
            line_width_min_pixels=3,
        )
    )
    return layers


def _render_map(
    plan: Plan,
    resolution,
    boundary_raw: dict,
    guides: dict | None = None,
) -> None:
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
        layers=_map_layers(plan, resolution, boundary_raw, guides),
        tooltip={"text": "{label}"},
    )
    st.pydeck_chart(deck, width="stretch")


def _ratio_controls(saved_rules, partition_mode: str):
    ratios = saved_rules.ratios
    st.sidebar.subheader("方向分区比例")
    if partition_mode == "centerline":
        north_label = "北部（占父范围中心线长度）"
        south_label = "南部（占父范围中心线长度）"
        east_label = "东部（占局部东西截面宽度）"
        west_label = "西部（占局部东西截面宽度）"
    else:
        north_label = "北部（占父区域高度）"
        south_label = "南部（占父区域高度）"
        east_label = "东部（占父区域宽度）"
        west_label = "西部（占父区域宽度）"
    north = st.sidebar.slider(north_label, 5, 49, round(ratios["north"] * 100), 1)
    south = st.sidebar.slider(south_label, 5, 49, round(ratios["south"] * 100), 1)
    east = st.sidebar.slider(east_label, 5, 49, round(ratios["east"] * 100), 1)
    west = st.sidebar.slider(west_label, 5, 49, round(ratios["west"] * 100), 1)
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
        "独立预览页面：方位范围作为地块属性保存，不持续改变地块外观；"
        "命中地块使用淡蓝色透明填充和深蓝色完整描边，与橙色建筑区分；"
        "鼠标悬停时整组范围会高亮并临时抬升。"
        "滑块只改变当前预览，点击保存后才会写入规则 JSON。"
    )

    saved_rules = load_scope_rules()
    plan_path = st.sidebar.text_input("地块数据", str(DEFAULT_PLAN_PATH))
    plan_file = Path(plan_path)
    if not plan_file.exists():
        st.error(f"找不到地块数据：{plan_file}")
        return
    plan = _load_plan(str(plan_file), plan_file.stat().st_mtime_ns)

    partition_labels = {
        "bbox": "经纬度外包框",
        "centerline": "岛体曲线中心线",
    }
    partition_mode = st.sidebar.segmented_control(
        "方位划分方式",
        options=list(partition_labels),
        default=saved_rules.partition_mode,
        format_func=partition_labels.get,
        required=True,
        key="spatial_partition_mode",
        width="stretch",
    )
    ratios = _ratio_controls(saved_rules, partition_mode)
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
            partition_mode=partition_mode,
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
        guides = (
            centerline_guide_geometry(preview_rules)
            if preview_rules.partition_mode == "centerline"
            else None
        )
    except SpatialScopeError as exc:
        st.error(f"无法解析：{exc}")
        return

    mode_labels = {"single": "单一区域", "intersection": "交集", "nested": "递进"}
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("总地块", len(plan.parcels))
    col2.metric("命中地块", len(resolution.target_parcel_ids))
    col3.metric("解析方式", mode_labels[scope.mode])
    col4.metric("划分方式", partition_labels[preview_rules.partition_mode])
    st.code(
        json.dumps(
            {"mode": scope.mode, "terms": list(scope.terms)},
            ensure_ascii=False,
        ),
        language="json",
    )

    boundary_raw = json.loads(preview_rules.boundary_path.read_text(encoding="utf-8-sig"))
    render_hover_scope_map(
        parcels=plan.to_dict(),
        target_parcel_ids=resolution.target_parcel_ids,
        boundary=boundary_raw,
        scope_geometry=resolution.geometry,
        guides=guides,
    )
    legend = (
        "图例：淡蓝色填充＋深蓝描边＝命中地块　橙色＝建筑　"
        "灰色＝其他地块　红线＝复兴岛边界　"
        "悬停蓝色地块＝整组边缘高亮并抬升"
    )
    if guides:
        legend += "　紫线＝中心线　青线＝抽样截面"
    st.caption(legend)
    if guides:
        st.info(
            f"当前算法：{guides['algorithm']}；"
            f"中心线 {guides['station_count']} 个点，长度约 "
            f"{guides['centerline_length_m']:.1f} 米；固定执行一次垂直截面校正。"
        )

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
        width="stretch",
    )
    trace_col.download_button(
        "下载解析记录 JSON",
        json.dumps(resolution.to_dict(), ensure_ascii=False, indent=2),
        file_name="spatial_scope_trace.json",
        mime="application/json",
        width="stretch",
    )

    with st.expander("查看命中地块与追溯记录"):
        st.dataframe(
            [{"地块 ID": parcel_id} for parcel_id in resolution.target_parcel_ids],
            width="stretch",
            hide_index=True,
        )
        st.json(resolution.to_dict(), expanded=False)
