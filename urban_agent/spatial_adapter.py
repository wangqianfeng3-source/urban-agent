"""Small, renderer-neutral adapters for spatial-scope input data.

The spatial classifier only needs a boundary and identified parcel geometries.
This module normalizes those inputs while preserving arbitrary parcel properties
for downstream 2D/3D renderers, LLM tools, or planning actions.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable


GeoJSONInput = Mapping[str, Any] | str | Path


class SpatialDataError(ValueError):
    """Raised when external spatial data cannot be normalized safely."""


@dataclass(frozen=True)
class SpatialDataset:
    """Canonical, application-independent input for spatial classification."""

    boundary_geometry: dict
    parcel_geometries: dict[str, dict]
    parcel_properties: dict[str, dict] = field(default_factory=dict)
    source: str = "memory"
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.parcel_geometries:
            raise SpatialDataError("空间数据至少需要一个地块")
        if set(self.parcel_properties) - set(self.parcel_geometries):
            raise SpatialDataError("存在没有对应地块几何的属性记录")

    def feature_collection(self, parcel_ids=None) -> dict:
        """Return selected parcels as GeoJSON without losing renderer attributes."""
        selected = (
            set(self.parcel_geometries)
            if parcel_ids is None
            else {str(parcel_id) for parcel_id in parcel_ids}
        )
        unknown = selected - set(self.parcel_geometries)
        if unknown:
            raise SpatialDataError(f"未知地块 ID：{sorted(unknown)}")
        return {
            "type": "FeatureCollection",
            "metadata": dict(self.metadata),
            "features": [
                {
                    "type": "Feature",
                    "id": parcel_id,
                    "geometry": self.parcel_geometries[parcel_id],
                    "properties": dict(self.parcel_properties.get(parcel_id, {})),
                }
                for parcel_id in self.parcel_geometries
                if parcel_id in selected
            ],
        }


@runtime_checkable
class SpatialDataAdapter(Protocol):
    """Extension point for GeoJSON, databases, web services, or custom models."""

    def load(self) -> SpatialDataset:
        """Normalize a source into a canonical spatial dataset."""
        ...


def _read_geojson(source: GeoJSONInput) -> tuple[dict, str]:
    if isinstance(source, Mapping):
        return dict(source), "memory"
    path = Path(source)
    if not path.exists():
        raise SpatialDataError(f"找不到空间数据：{path}")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig")), str(path)
    except (OSError, json.JSONDecodeError) as exc:
        raise SpatialDataError(f"无法读取 GeoJSON：{path}") from exc


def _boundary_geometry(raw: dict) -> dict:
    from shapely.geometry import mapping, shape
    from shapely.ops import unary_union

    data_type = raw.get("type")
    if data_type == "FeatureCollection":
        features = raw.get("features", [])
        if not features:
            raise SpatialDataError("边界 FeatureCollection 为空")
        geometry = unary_union([shape(feature["geometry"]) for feature in features])
    elif data_type == "Feature":
        geometry = shape(raw["geometry"])
    else:
        geometry = shape(raw)
    if geometry.is_empty or geometry.geom_type not in {"Polygon", "MultiPolygon"}:
        raise SpatialDataError("边界必须是非空 Polygon 或 MultiPolygon")
    if not geometry.is_valid:
        raise SpatialDataError("边界几何无效，请先修复 GeoJSON")
    return mapping(geometry)


def _feature_id(feature: dict, id_property: str | None) -> str:
    properties = feature.get("properties") or {}
    candidates = (
        [properties.get(id_property)]
        if id_property
        else [feature.get("id"), properties.get("parcel_id"), properties.get("id")]
    )
    value = next((candidate for candidate in candidates if candidate is not None), None)
    if value is None or str(value).strip() == "":
        hint = f"properties.{id_property}" if id_property else "Feature.id"
        raise SpatialDataError(f"地块缺少唯一标识；期望字段：{hint}")
    return str(value)


@dataclass(frozen=True)
class GeoJSONSpatialAdapter:
    """Load a boundary and parcels from paths or in-memory GeoJSON mappings."""

    boundary: GeoJSONInput
    parcels: GeoJSONInput
    parcel_id_property: str | None = None
    source_name: str | None = None

    def load(self) -> SpatialDataset:
        from shapely.geometry import shape

        boundary_raw, boundary_source = _read_geojson(self.boundary)
        parcels_raw, parcel_source = _read_geojson(self.parcels)
        if parcels_raw.get("type") != "FeatureCollection":
            raise SpatialDataError("地块数据必须是 GeoJSON FeatureCollection")

        geometries: dict[str, dict] = {}
        properties: dict[str, dict] = {}
        for feature in parcels_raw.get("features", []):
            parcel_id = _feature_id(feature, self.parcel_id_property)
            if parcel_id in geometries:
                raise SpatialDataError(f"地块 ID 重复：{parcel_id}")
            geometry = feature.get("geometry")
            if not geometry:
                raise SpatialDataError(f"地块 {parcel_id} 缺少几何")
            parsed = shape(geometry)
            if parsed.is_empty or parsed.geom_type not in {"Polygon", "MultiPolygon"}:
                raise SpatialDataError(f"地块 {parcel_id} 必须是面几何")
            if not parsed.is_valid:
                raise SpatialDataError(f"地块 {parcel_id} 的几何无效")
            geometries[parcel_id] = dict(geometry)
            properties[parcel_id] = dict(feature.get("properties") or {})

        source = self.source_name or f"boundary={boundary_source}; parcels={parcel_source}"
        metadata = dict(parcels_raw.get("metadata") or {})
        metadata["boundary_source"] = boundary_source
        metadata["parcel_source"] = parcel_source
        return SpatialDataset(
            boundary_geometry=_boundary_geometry(boundary_raw),
            parcel_geometries=geometries,
            parcel_properties=properties,
            source=source,
            metadata=metadata,
        )


@dataclass(frozen=True)
class PlanSpatialAdapter:
    """Backward-compatible adapter for the project's existing ``Plan`` model."""

    plan: Any
    boundary: GeoJSONInput
    source_name: str | None = None

    def load(self) -> SpatialDataset:
        return GeoJSONSpatialAdapter(
            boundary=self.boundary,
            parcels=self.plan.to_dict(),
            source_name=self.source_name,
        ).load()
