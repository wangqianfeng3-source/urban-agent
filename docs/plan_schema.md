# Plan Schema 规范（v1.0）

> 建议：**五天营期内尽量沿用现版**。schema 一动所有组联动，性价比通常不高；
> 真有非改不可的字段，建议 Day 1 晚提出、当晚定案，并同步更新本文档与 `schema.py`。

## 1. 为什么需要它

方案的结构化表示是整个系统的地基：编辑模块改它、评估模块读它、可视化画它。
schema 一变，所有组都要跟着改 —— 所以建议保持稳定，其他一切在它之上生长。

## 2. 文件格式

一个方案 = 一个 GeoJSON `FeatureCollection`，坐标系 WGS84（EPSG:4326）。

```json
{
  "type": "FeatureCollection",
  "metadata": {"site": "复兴岛", "version": 3},
  "features": [
    {
      "type": "Feature",
      "id": "P01",
      "geometry": {"type": "Polygon", "coordinates": [[...]]},
      "properties": {
        "landuse": "T",
        "far": 1.5,
        "height": 24,
        "green_ratio": 0.10,
        "area": 10000,
        "facilities": ["clinic"],
        "zone_flags": {"zero_carbon": true, "historic": false}
      }
    }
  ]
}
```

## 3. 地块字段（v1.0）

| 字段 | 类型 | 单位 | 约束 | 说明 |
|---|---|---|---|---|
| `id` | string | — | 片区内唯一 | 地块编号 |
| `geometry` | Polygon | 经纬度 | WGS84 | 地块边界；**编辑动作不得修改几何** |
| `landuse` | string | — | 见下方代码表 | 用地类型 |
| `far` | float | — | 0 – 3.5 | 容积率 |
| `height` | float | 米 | ≥0 | 限高 |
| `green_ratio` | float | — | 0 – 0.9 | 绿地率 |
| `area` | float | m² | >0 | 用地面积（入库时预计算，避免运行时投影换算） |
| `facilities` | list[str] | — | ≤5 项 | 配套设施 |
| `zone_flags` | dict | — | bool 值 | 特殊分区标记，见下 |

**用地代码**：`R` 居住 · `C` 商业 · `M` 工业 · `T` 科创研发 · `A` 公共服务设施 · `G` 绿地

**分区标记**：`zero_carbon` 零碳示范区（禁工业、容积率≤2.5）· `historic` 历史风貌保护（用地性质冻结）

## 4. 版本与历史

- `Plan.copy()` 产生新版本（version+1），每轮编辑必须在副本上进行；
- 每个动作自动写入 `Plan.history`（谁、改了什么、为什么）—— 这是可审计性的来源；
- 循环运行时每版方案落盘为 `plan_v{n}.geojson`。

## 5. 已知的扩展候选（建议留到营后 v2）

- 建筑年代/质量字段（碳维度建筑改造评估需要）
- 地块级人口/岗位数（人和社会维度需要）
- 道路要素作为独立 Feature 类型（交通维度接入时需要）

有新的扩展想法：在仓库 Issues 打 `schema-proposal` 标签，营后统一讨论。
