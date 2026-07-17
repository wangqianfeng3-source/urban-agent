"""交互式人机共创 demo（D 组）—— streamlit run app/streamlit_app.py

不是静态展示：现场可以
  1. 手动编辑方案（走与 AI 相同的受限动作空间，非法修改被当场拦截）；
  2. 让 AI 提修改建议 → 规划师逐条勾选拍板后才执行（"AI 出建议、人拍板"）；
  3. 一键自动迭代至达标，逐轮实时看到得分爬升；
  4. 随时导出给规划师的迭代报告。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pydeck as pdk
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from urban_agent.actions import ActionError, apply_action          # noqa: E402
from urban_agent.editor import LLMEditor, MockEditor               # noqa: E402
from urban_agent.evaluators import default_evaluators              # noqa: E402
from urban_agent.evaluators.base import aggregate                  # noqa: E402
from urban_agent.intake import parse_requirement_text              # noqa: E402
from urban_agent.loop import LoopResult                            # noqa: E402
from urban_agent.portfolio import run_portfolio                    # noqa: E402
from urban_agent.report import report_markdown                     # noqa: E402
from urban_agent.schema import LANDUSE_CODES, Plan                 # noqa: E402
from urban_agent.strategies import STRATEGIES                      # noqa: E402

SAMPLE = ROOT / "data" / "real" / "fuxing_building_parcel_plan.geojson"
if not SAMPLE.exists():
    SAMPLE = ROOT / "data" / "sample" / "fuxing_demo.geojson"
REAL_BOUNDARY = ROOT / "data" / "real" / "fuxing_geojson" / "boundary.geojson"
REAL_BUILDINGS = ROOT / "data" / "real" / "fuxing_geojson" / "buildings.geojson"
REAL_ROADS = ROOT / "data" / "real" / "fuxing_geojson" / "roads.geojson"
REAL_PARK = ROOT / "data" / "real" / "fuxing_geojson" / "fuxing_park.geojson"
LANDUSE_COLOR = {
    # 参考国内控规常见表达：居住黄、商业红、工业紫、公共服务橙、绿地绿。
    "R": [255, 230, 128],
    "C": [230, 0, 18],
    "M": [163, 73, 164],
    "T": [0, 112, 192],
    "A": [255, 153, 51],
    "G": [0, 176, 80],
}
LANDUSE_LEGEND = {
    "R": "居住",
    "C": "商业",
    "M": "工业",
    "T": "科创研发",
    "A": "公共服务设施",
    "G": "绿地",
}
FACILITY_OPTIONS = ["community_center", "elderly_care", "library", "gym", "clinic"]

st.set_page_config(page_title="城市更新智能体 · 人机共创", layout="wide")


# ---------------- 状态管理 ----------------

def init_state(data_path: str):
    st.session_state.initial = Plan.from_geojson(data_path)
    st.session_state.plan = Plan.from_geojson(data_path)
    st.session_state.evaluators = default_evaluators(baseline=st.session_state.initial)
    st.session_state.mock_editor = MockEditor()
    st.session_state.history = []      # 与 loop.py 同格式的迭代记录
    st.session_state.pending = []      # AI 已提出、待人拍板的动作


if "plan" not in st.session_state:
    init_state(str(SAMPLE))


def evaluate_now():
    results = [e.evaluate(st.session_state.plan) for e in st.session_state.evaluators]
    return results, aggregate(results)


def record_round(results, agg, action_logs):
    """把'评估 + 本轮执行的动作'记为一轮，与 loop.py 的 history 同构。"""
    st.session_state.history.append({
        "iteration": len(st.session_state.history) + 1,
        "plan_version": st.session_state.plan.version,
        "total": agg["total"],
        "hard_pass": agg["hard_pass"],
        "scores": agg["scores"],
        "violations": [
            {"rule": v.rule_id, "parcel": v.parcel_id, "message": v.message}
            for r in results for v in r.violations
        ],
        "evidence": {r.dimension: r.soft.evidence for r in results},
        "actions": action_logs,
    })


def execute_actions(actions: list[dict], results, agg) -> list[dict]:
    """在方案副本上执行一批动作并记录一轮。返回执行日志。"""
    logs = []
    st.session_state.plan = st.session_state.plan.copy()
    for a in actions:
        try:
            desc = apply_action(st.session_state.plan, a)
            logs.append({"ok": True, "desc": desc, "reason": a.get("reason", "")})
        except ActionError as e:
            logs.append({"ok": False, "desc": str(e), "reason": a.get("reason", "")})
    record_round(results, agg, logs)
    return logs


def make_editor(kind: str):
    strategy = STRATEGIES.get(st.session_state.get("strategy_key", "street_friendly"))
    if kind == "llm":
        try:
            return LLMEditor(strategy=strategy)
        except RuntimeError as e:
            st.error(str(e))
            return None
    return MockEditor(strategy=strategy)


def _geometry_points(geometry: dict) -> list:
    """Return lon/lat points from common GeoJSON geometries for map fitting."""
    coords = geometry.get("coordinates", [])
    gtype = geometry.get("type")
    if gtype == "Point":
        return [coords]
    if gtype == "LineString":
        return coords
    if gtype == "Polygon":
        return [pt for ring in coords for pt in ring]
    if gtype == "MultiPolygon":
        return [pt for polygon in coords for ring in polygon for pt in ring]
    return []


def load_geojson(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _rgba_css(color: list[int]) -> str:
    alpha = color[3] / 255 if len(color) > 3 else 1
    return f"rgba({color[0]}, {color[1]}, {color[2]}, {alpha:.2f})"


def _swatch(color: list[int], label: str) -> str:
    return (
        "<span style='display:inline-flex;align-items:center;margin-right:14px;margin-bottom:4px;'>"
        f"<span style='width:12px;height:12px;background:{_rgba_css(color)};"
        "border:1px solid rgba(0,0,0,.25);display:inline-block;margin-right:5px;'></span>"
        f"{label}</span>"
    )


def _road_feature_id(feature: dict, index: int) -> str:
    props = feature.get("properties", {})
    return f"road-{props.get('OBJECTID', index)}"


def _road_profiles(plan_obj: Plan) -> dict:
    profiles = {}
    for p in plan_obj.parcels.values():
        flags = p.zone_flags
        road_id = flags.get("nearest_road_id")
        if not road_id:
            continue
        current = profiles.setdefault(str(road_id), {
            "road_width": 0.0,
            "sidewalk_width": 0.0,
            "bike_lane_width": 0.0,
            "frontage_retreat": 0.0,
            "count": 0,
        })
        current["road_width"] = max(current["road_width"], float(flags.get("road_width_m") or 0))
        current["sidewalk_width"] = max(current["sidewalk_width"], float(flags.get("sidewalk_width_m") or 0))
        current["bike_lane_width"] = max(current["bike_lane_width"], float(flags.get("bike_lane_width_m") or 0))
        current["frontage_retreat"] = max(current["frontage_retreat"], float(flags.get("frontage_retreat_m") or 0))
        current["count"] += 1
    return profiles


def _style_plan_geojson(plan_obj: Plan) -> dict:
    gj = plan_obj.to_dict()
    for f in gj["features"]:
        props = f["properties"]
        flags = props.get("zone_flags", {})
        props["fill"] = LANDUSE_COLOR[props["landuse"]]
        road_note = ""
        if flags.get("building_as_parcel"):
            road_note = (f" · 路宽{flags.get('road_width_m', '-')}m"
                         f" · 人行道{flags.get('sidewalk_width_m', '-')}m"
                         f" · 退界{flags.get('frontage_retreat_m', 0)}m")
        props["label"] = (f"{f['id']} {LANDUSE_CODES[props['landuse']]} · "
                          f"容积率{props['far']} · 绿地率{props['green_ratio']}{road_note}")
    return gj


def _render_plan_map(plan_obj: Plan, *, include_caption: bool = True, zoom: float = 14.5):
    gj = _style_plan_geojson(plan_obj)
    real_boundary = load_geojson(REAL_BOUNDARY)
    building_plan = any(p.zone_flags.get("building_as_parcel") for p in plan_obj.parcels.values())
    real_buildings = None if building_plan else load_geojson(REAL_BUILDINGS)
    real_roads = load_geojson(REAL_ROADS)
    real_park = load_geojson(REAL_PARK)
    context_points = []
    layers = []

    if real_boundary:
        for f in real_boundary["features"]:
            props = f.setdefault("properties", {})
            props["line"] = [210, 30, 30]
            props["fill"] = [210, 30, 30]
            props["label"] = props.get("name") or "复兴岛边界"
            context_points.extend(_geometry_points(f["geometry"]))
        layers.append(pdk.Layer(
            "GeoJsonLayer", real_boundary, pickable=True, stroked=True, filled=True,
            get_fill_color="properties.fill", get_line_color="properties.line",
            line_width_min_pixels=3, opacity=0.35))

    if real_buildings:
        for f in real_buildings["features"]:
            props = f.setdefault("properties", {})
            height = float(props.get("height") or 8)
            props["fill"] = [110, 110, 110]
            props["line"] = [80, 80, 80]
            props["label"] = f"现状建筑 · 高度{height:g}m"
        layers.append(pdk.Layer(
            "GeoJsonLayer", real_buildings, pickable=True, stroked=True, filled=True,
            get_fill_color="properties.fill", get_line_color="properties.line",
            line_width_min_pixels=1, opacity=0.35))

    if real_roads:
        for idx, f in enumerate(real_roads["features"], 1):
            props = f.setdefault("properties", {})
            props["line"] = [245, 130, 32]
            props["label"] = props.get("NAME") or f"{_road_feature_id(f, idx)} 现状道路"
            context_points.extend(_geometry_points(f["geometry"]))
        layers.append(pdk.Layer(
            "GeoJsonLayer", real_roads, pickable=True, stroked=True, filled=False,
            get_line_color="properties.line", line_width_min_pixels=2, opacity=0.75))

        profiles = _road_profiles(plan_obj)
        if profiles:
            profile_roads = json.loads(json.dumps(real_roads))
            width_buckets = {4: [], 7: [], 11: [], 15: []}
            for idx, f in enumerate(profile_roads["features"], 1):
                rid = _road_feature_id(f, idx)
                if rid not in profiles:
                    continue
                profile = profiles[rid]
                total_width = profile["road_width"] + 2 * profile["sidewalk_width"] + 2 * profile["bike_lane_width"]
                props = f.setdefault("properties", {})
                props["line"] = [0, 92, 175] if profile["frontage_retreat"] else [31, 132, 205]
                props["label"] = (
                    f"{rid} 规划断面 · 车行道{profile['road_width']:.1f}m · "
                    f"人行道{profile['sidewalk_width']:.1f}m · 骑行{profile['bike_lane_width']:.1f}m"
                )
                if total_width < 9:
                    width_buckets[4].append(f)
                elif total_width < 13:
                    width_buckets[7].append(f)
                elif total_width < 18:
                    width_buckets[11].append(f)
                else:
                    width_buckets[15].append(f)
            for width_px, features in width_buckets.items():
                if not features:
                    continue
                bucket = {"type": "FeatureCollection", "features": features}
                layers.append(pdk.Layer(
                    "GeoJsonLayer", bucket, pickable=True, stroked=True, filled=False,
                    get_line_color="properties.line",
                    line_width_min_pixels=width_px, opacity=0.85))

    if real_park:
        for f in real_park["features"]:
            props = f.setdefault("properties", {})
            props["fill"] = [0, 176, 80]
            props["line"] = [0, 120, 60]
            props["label"] = props.get("name", "复兴岛公园")
            context_points.extend(_geometry_points(f["geometry"]))
        layers.append(pdk.Layer(
            "GeoJsonLayer", real_park, pickable=True, stroked=True, filled=True,
            get_fill_color="properties.fill", get_line_color="properties.line",
            line_width_min_pixels=2, opacity=0.45))

    plan_points = [pt for f in gj["features"] for pt in _geometry_points(f["geometry"])]
    all_points = context_points or plan_points
    lons = [c[0] for c in all_points]
    lats = [c[1] for c in all_points]
    layers.append(pdk.Layer(
        "GeoJsonLayer", gj, pickable=True, stroked=True, filled=True,
        get_fill_color="properties.fill", get_line_color=[255, 255, 255],
        line_width_min_pixels=1, opacity=0.82))
    st.pydeck_chart(pdk.Deck(
        map_style=None,
        initial_view_state=pdk.ViewState(
            longitude=sum(lons) / len(lons), latitude=sum(lats) / len(lats),
            zoom=zoom),
        layers=layers,
        tooltip={"text": "{label}"},
    ))
    if include_caption:
        legend = "".join(_swatch(LANDUSE_COLOR[k], v) for k, v in LANDUSE_LEGEND.items())
        legend += _swatch([210, 30, 30, 240], "真实边界")
        legend += _swatch([245, 130, 32, 175], "现状道路")
        legend += _swatch([0, 92, 175, 225], "规划道路断面（线越粗表示断面越宽）")
        legend += _swatch([0, 176, 80, 95], "复兴岛公园")
        st.markdown(legend, unsafe_allow_html=True)


def _plan_delta_summary(before: Plan, after: Plan) -> dict:
    shared = [pid for pid in before.parcels if pid in after.parcels]
    changed = []
    retreat = 0.0
    area_loss = 0.0
    for pid in shared:
        b = before.parcel(pid)
        a = after.parcel(pid)
        diff = b.area - a.area
        if abs(diff) > 0.1 or b.geometry != a.geometry:
            changed.append(pid)
            area_loss += max(0.0, diff)
        retreat += float(a.zone_flags.get("frontage_retreat_m") or 0)
    return {
        "changed_count": len(changed),
        "area_loss": area_loss,
        "retreat_sum": retreat,
    }


# ---------------- 侧边栏 ----------------

with st.sidebar:
    st.header("⚙️ 运行设置")
    data_path = st.text_input("方案数据文件", str(SAMPLE))
    editor_kind = st.radio("编辑器", ["mock", "llm"], horizontal=True,
                           help="mock=规则式（无需 API key）；llm=大模型（需 LLM_API_KEY）")
    strategy_options = list(STRATEGIES)
    default_strategy = strategy_options.index("street_friendly") if "street_friendly" in strategy_options else 0
    strategy_key = st.selectbox(
        "本轮策略",
        strategy_options,
        index=default_strategy,
        format_func=lambda k: STRATEGIES[k].name,
        help="影响 AI/规则编辑器优先采纳哪一类软优化建议，不改变评估和执行 loop",
    )
    st.session_state.strategy_key = strategy_key
    target = st.slider("达标线（综合得分）", 50, 95, 75)
    max_actions = st.slider("每轮最多动作数", 1, 4, 2,
                            help="'局部修改'原则：每轮少改几处，方案不跑偏")
    max_iters = st.slider("自动迭代最大轮数", 1, 12, 8)
    if st.button("🔄 重置为初始方案", use_container_width=True):
        init_state(data_path)
        st.rerun()

results, agg = evaluate_now()
plan: Plan = st.session_state.plan

# ---------------- 顶部指标 ----------------

st.title("城市数实空间智能体 · 人机共创工作台")
c1, c2, c3, c4 = st.columns(4)
c1.metric("方案版本", f"v{plan.version}")
c2.metric("综合得分", f"{agg['total']:.1f}" if agg["hard_pass"] else "0（否决）",
          help="任一硬违规存在时一票否决")
c3.metric("硬违规", agg["violation_count"])
c4.metric("已迭代轮数", len(st.session_state.history))
if agg["hard_pass"] and agg["total"] >= target:
    st.success(f"🎉 方案已达标（≥{target} 分且无硬违规），可导出报告交规划师确认")

tab_edit, tab_ai, tab_pf, tab_history, tab_compare = st.tabs(
    ["🗺 方案与人工编辑", "🤖 AI 建议 · 人拍板", "🏆 Top 5 方案比选", "📈 迭代历史与报告", "🆚 方案对比"])

# ---------------- Tab 1：地图 + 人工编辑 ----------------

with tab_edit:
    left, right = st.columns([3, 2])

    with left:
        _render_plan_map(plan)

    with right:
        st.subheader("当前评估")
        for r in results:
            st.caption(f"**{r.dimension}**（{r.soft.score:.1f}/100）：{r.soft.evidence}")
        if agg["violation_count"]:
            st.markdown("**硬违规（一票否决项）**")
            for r in results:
                for v in r.violations:
                    st.error(f"`{v.rule_id}` {v.message}")
        else:
            st.success("无硬违规")

        st.divider()
        st.subheader("人工编辑")
        st.caption("与 AI 走同一受限动作空间：非法修改会被当场拦截并说明原因")
        pid = st.selectbox("选择地块", list(plan.parcels), format_func=lambda i: (
            f"{i} · {LANDUSE_CODES[plan.parcel(i).landuse]}"
            + (" · 历史保护" if plan.parcel(i).flag("historic") else "")
            + (" · 零碳区" if plan.parcel(i).flag("zero_carbon") else "")))
        act_type = st.selectbox("动作", ["调整用地性质", "增减容积率", "设置绿地率", "新增设施", "道路断面退界", "整条道路断面退界"])

        if act_type == "调整用地性质":
            lu = st.selectbox("目标用地", list(LANDUSE_CODES),
                              format_func=lambda k: f"{k} {LANDUSE_CODES[k]}")
            action = {"name": "set_landuse", "params": {"parcel_id": pid, "landuse": lu}}
        elif act_type == "增减容积率":
            delta = st.number_input("容积率增量", -1.5, 1.5, -0.5, 0.1)
            action = {"name": "adjust_far", "params": {"parcel_id": pid, "delta": delta}}
        elif act_type == "设置绿地率":
            ratio = st.slider("绿地率", 0.0, 0.9, float(plan.parcel(pid).green_ratio), 0.05)
            action = {"name": "set_green_ratio", "params": {"parcel_id": pid, "ratio": ratio}}
        elif act_type == "新增设施":
            fac = st.selectbox("设施类型", FACILITY_OPTIONS)
            action = {"name": "add_facility", "params": {"parcel_id": pid, "facility": fac}}
        elif act_type == "道路断面退界":
            flags = plan.parcel(pid).zone_flags
            current_road = float(flags.get("road_width_m") or 8.0)
            current_sidewalk = float(flags.get("sidewalk_width_m") or 1.5)
            current_bike = float(flags.get("bike_lane_width_m") or 0.0)
            road_width = st.number_input("目标车行道宽度（m）", 4.0, 24.0, max(8.0, current_road), 0.5)
            sidewalk_width = st.number_input("目标人行道宽度（m）", 1.5, 6.0, max(3.0, current_sidewalk), 0.5)
            bike_lane_width = st.number_input("目标骑行空间宽度（m）", 0.0, 2.5, max(1.5, current_bike), 0.5)
            action = {
                "name": "set_street_profile",
                "params": {
                    "parcel_id": pid,
                    "road_width": road_width,
                    "sidewalk_width": sidewalk_width,
                    "bike_lane_width": bike_lane_width,
                },
            }
        else:
            flags = plan.parcel(pid).zone_flags
            default_road_id = str(flags.get("nearest_road_id") or "road-1")
            road_id = st.text_input("道路编号", default_road_id)
            road_width = st.number_input("目标车行道宽度（m）", 4.0, 24.0, 8.0, 0.5, key="road_profile_width")
            sidewalk_width = st.number_input("目标人行道宽度（m）", 1.5, 6.0, 3.0, 0.5, key="road_profile_sidewalk")
            bike_lane_width = st.number_input("目标骑行空间宽度（m）", 0.0, 2.5, 1.5, 0.5, key="road_profile_bike")
            max_parcels = st.slider("本次最多影响建筑数", 1, 30, 12)
            action = {
                "name": "set_road_profile",
                "params": {
                    "road_id": road_id,
                    "road_width": road_width,
                    "sidewalk_width": sidewalk_width,
                    "bike_lane_width": bike_lane_width,
                    "max_parcels": max_parcels,
                },
            }

        if st.button("执行修改", type="primary", use_container_width=True):
            action["reason"] = "规划师人工修改"
            logs = execute_actions([action], results, agg)
            (st.toast if logs[0]["ok"] else st.error)(
                ("✓ " if logs[0]["ok"] else "✗ 已拦截：") + logs[0]["desc"])
            if logs[0]["ok"]:
                st.rerun()

# ---------------- Tab 2：AI 建议 → 人拍板 / 自动迭代 ----------------

with tab_ai:
    col_a, col_b = st.columns(2)

    with col_a:
        st.subheader("单步：AI 出建议，规划师拍板")
        if st.button("💡 让 AI 提本轮修改建议", use_container_width=True):
            editor = make_editor(editor_kind)
            if editor:
                with st.spinner("AI 正在分析评估反馈…"):
                    st.session_state.pending = editor.propose(plan, results, max_actions)
                if not st.session_state.pending:
                    st.info("AI 没有新建议（可能评分已停滞，建议人工介入）")

        if st.session_state.pending:
            st.markdown("**AI 建议以下修改（勾选后采纳）：**")
            picked = []
            for i, a in enumerate(st.session_state.pending):
                label = f"`{a['name']}` {a['params']}"
                if a.get("reason"):
                    label += f"\n\n　↳ 理由：{a['reason']}"
                if st.checkbox(label, value=True, key=f"pending_{i}"):
                    picked.append(a)
            ok_col, no_col = st.columns(2)
            if ok_col.button("✅ 采纳勾选项并执行", type="primary", use_container_width=True):
                execute_actions(picked, results, agg)
                st.session_state.pending = []
                st.rerun()
            if no_col.button("🗑 全部否掉", use_container_width=True):
                st.session_state.pending = []
                st.rerun()

    with col_b:
        st.subheader("自动：一键迭代至达标")
        st.caption("演示'AI 承担改—评—改循环'；每轮结果实时显示，随时可切回单步模式")
        if st.button("▶ 自动运行", type="primary", use_container_width=True):
            editor = make_editor(editor_kind)
            if editor:
                box = st.status("自动迭代中…", expanded=True)
                for _ in range(max_iters):
                    r_now, a_now = evaluate_now()
                    if a_now["hard_pass"] and a_now["total"] >= target:
                        box.update(label="✅ 达标", state="complete")
                        break
                    actions = editor.propose(st.session_state.plan, r_now, max_actions)
                    if not actions:
                        box.update(label="⏸ 评分停滞，已停止", state="error")
                        break
                    logs = execute_actions(actions, r_now, a_now)
                    total = f"{a_now['total']:.1f}" if a_now["hard_pass"] else "0(否决)"
                    box.write(f"第 {len(st.session_state.history)} 轮 · 得分 {total} · "
                              + "；".join(l["desc"] for l in logs))
                else:
                    box.update(label="⚠️ 达到最大轮数", state="error")
                st.rerun()

# ---------------- Tab 3：Top 5 方案比选 ----------------

with tab_pf:
    st.subheader("不同规划策略，各自迭代，一起摆上桌——请规划师选择")
    st.caption("每张策略卡（低碳优先/活力优先/绿色宜居/均衡改良/保守渐进）独立跑一遍"
               "改—评—改闭环；排序只看（是否合规，综合得分）。此处用规则式编辑器以保证速度。")
    require = st.text_input("更新需求（可选，自然语言，Phase 0 会解析出候选策略）",
                            placeholder="如：打造零碳示范街区，同时提升街区活力")
    if st.button("🏆 生成 Top 5 方案比选", type="primary", use_container_width=True):
        strategies = list(STRATEGIES.values())
        if require.strip():
            obj = parse_requirement_text(require)
            if obj.strategy_keys:
                picked = [STRATEGIES[k] for k in obj.strategy_keys]
                strategies = picked + [s for s in strategies if s.key not in obj.strategy_keys]
                st.info("需求解析 → 候选策略：" + "、".join(s.name for s in picked))
        with st.spinner("五条策略轨迹各自迭代中…"):
            items = run_portfolio(data_path, ROOT / "runs" / "portfolio_app",
                                  editor_kind="mock", target=target, max_iters=max_iters,
                                  strategies=strategies)
        st.session_state.portfolio = [{
            "rank": it.rank, "name": it.strategy.name, "desc": it.strategy.description,
            "hard_pass": it.hard_pass, "total": it.total, "scores": it.scores,
            "iterations": it.iterations, "plan": it.plan_path,
        } for it in items]

    if st.session_state.get("portfolio"):
        pf = st.session_state.portfolio
        st.dataframe([{
            "排名": p["rank"], "策略": p["name"],
            "合规": "✓" if p["hard_pass"] else "✗ 有硬违规",
            **{d: f"{v:.1f}" for d, v in p["scores"].items()},
            "综合得分": f"{p['total']:.1f}" if p["hard_pass"] else "0",
            "轮数": p["iterations"], "适用情形": p["desc"],
        } for p in pf], use_container_width=True, hide_index=True)

        labels = [f"第 {p['rank']} 名 · {p['name']}"
                  f"（{p['total']:.1f} 分）" for p in pf]
        chosen = st.selectbox("选择一个方案", labels)
        if st.button("📥 载入所选方案到工作台，继续人机共创", use_container_width=True):
            sel = pf[labels.index(chosen)]
            st.session_state.plan = Plan.from_geojson(sel["plan"])
            st.session_state.pending = []
            st.toast(f"已载入「{sel['name']}」方案 —— 切到“方案与人工编辑”查看")
            st.rerun()
        report_file = ROOT / "runs" / "portfolio_app" / "report.md"
        if report_file.exists():
            with st.expander("📄 查看完整比选报告（Markdown）"):
                st.markdown(report_file.read_text(encoding="utf-8"))

# ---------------- Tab 4：历史与报告 ----------------

with tab_history:
    if not st.session_state.history:
        st.info("还没有迭代记录。去左边两个标签页改改方案，或点'自动运行'。")
    else:
        hist = st.session_state.history
        st.line_chart({"综合得分": [h["total"] for h in hist],
                       **{d: [h["scores"][d] for h in hist] for d in hist[0]["scores"]}})
        st.dataframe([{
            "轮次": h["iteration"], "版本": f"v{h['plan_version']}",
            "硬违规": len(h["violations"]),
            "综合得分": h["total"] if h["hard_pass"] else 0,
            "修改": "；".join(("✓" if a["ok"] else "✗") + a["desc"] for a in h["actions"]) or "—",
        } for h in hist], use_container_width=True, hide_index=True)

        status = ("passed" if agg["hard_pass"] and agg["total"] >= target
                  else "in_progress")
        result = LoopResult(status=status, final_plan=plan, history=hist)
        st.download_button(
            "📄 导出迭代报告（Markdown，给规划师确认）",
            report_markdown(st.session_state.initial, result),
            file_name="report.md", use_container_width=True)


# ---------------- Tab 5：原始方案 / 优化后方案对比 ----------------

with tab_compare:
    st.subheader("原始方案与当前方案对比")
    st.caption("用于检查优化是否真的改变了空间形态：道路断面变宽时，沿路建筑多边形会退界，图上道路蓝线也会随断面宽度变粗。")

    initial_results = [e.evaluate(st.session_state.initial) for e in st.session_state.evaluators]
    initial_agg = aggregate(initial_results)
    delta = _plan_delta_summary(st.session_state.initial, plan)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("初始综合得分", f"{initial_agg['total']:.1f}" if initial_agg["hard_pass"] else "0")
    m2.metric("当前综合得分", f"{agg['total']:.1f}" if agg["hard_pass"] else "0")
    m3.metric("边界变化建筑", delta["changed_count"])
    m4.metric("累计退界", f"{delta['retreat_sum']:.1f} m")
    st.caption(f"建筑 footprint 面积净减少约 {delta['area_loss']:.0f} m²；面积减少主要来自道路断面拓宽后的沿路退让。")

    before_col, after_col = st.columns(2)
    with before_col:
        st.markdown("**优化前：初始方案**")
        _render_plan_map(st.session_state.initial, include_caption=False, zoom=14.4)
    with after_col:
        st.markdown("**优化后：当前方案**")
        _render_plan_map(plan, include_caption=False, zoom=14.4)

    legend = "".join(_swatch(LANDUSE_COLOR[k], v) for k, v in LANDUSE_LEGEND.items())
    legend += _swatch([245, 130, 32, 175], "现状道路")
    legend += _swatch([0, 92, 175, 225], "规划道路断面（越粗表示越宽）")
    st.markdown(legend, unsafe_allow_html=True)
