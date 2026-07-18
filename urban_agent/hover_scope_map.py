"""Reusable hover-lift map component for resolved spatial scopes.

The component keeps hover animation entirely in the browser, so moving the
pointer never reruns Streamlit or recomputes spatial classification.
"""
from __future__ import annotations

import json
from collections.abc import Iterable

import streamlit as st
from shapely.geometry import mapping, shape
from shapely.ops import unary_union


_COMPONENT_HTML = """
<div class="hover-map-shell" data-testid="hover-scope-map" data-hover-active="false">
  <div class="hover-map-canvas"></div>
  <div class="hover-map-hint">悬停蓝色范围内任一地块，整组范围将高亮并抬升</div>
  <div class="hover-map-attribution">© CARTO · © OpenStreetMap contributors</div>
  <div class="hover-map-error" hidden></div>
</div>
"""

_COMPONENT_CSS = """
.hover-map-shell {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 520px;
  overflow: hidden;
  border: 1px solid var(--st-border-color, rgba(49, 51, 63, 0.18));
  border-radius: var(--st-base-radius, 0.5rem);
  background: var(--st-secondary-background-color, #f4f6f8);
}
.hover-map-canvas {
  position: absolute;
  inset: 0;
}
.hover-map-hint,
.hover-map-attribution,
.hover-map-error {
  position: absolute;
  z-index: 2;
  padding: 5px 8px;
  border-radius: 5px;
  color: #2c3440;
  background: rgba(255, 255, 255, 0.88);
  box-shadow: 0 1px 5px rgba(0, 0, 0, 0.12);
  pointer-events: none;
}
.hover-map-hint {
  top: 10px;
  left: 10px;
  font-size: 12px;
}
.hover-map-attribution {
  right: 7px;
  bottom: 6px;
  font-size: 10px;
}
.hover-map-error {
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  max-width: 80%;
  color: #b42318;
  font-size: 13px;
  pointer-events: auto;
}
"""

_COMPONENT_JS = """
const DECK_GL_URL = "https://cdn.jsdelivr.net/npm/deck.gl@9.1.14/dist.min.js"
const deckInstances = new WeakMap()
let deckLibraryPromise = null

function loadDeckLibrary() {
  if (globalThis.deck && globalThis.deck.Deck) return Promise.resolve(globalThis.deck)
  if (deckLibraryPromise) return deckLibraryPromise

  deckLibraryPromise = new Promise((resolve, reject) => {
    const existing = document.querySelector(`script[src="${DECK_GL_URL}"]`)
    if (existing) {
      existing.addEventListener("load", () => resolve(globalThis.deck), {once: true})
      existing.addEventListener("error", () => reject(new Error("deck.gl 加载失败")), {once: true})
      return
    }
    const script = document.createElement("script")
    script.src = DECK_GL_URL
    script.async = true
    script.crossOrigin = "anonymous"
    script.onload = () => resolve(globalThis.deck)
    script.onerror = () => reject(new Error("无法从 CDN 加载 deck.gl"))
    document.head.appendChild(script)
  })
  return deckLibraryPromise
}

function makeTileLayer(deckLib) {
  return new deckLib.TileLayer({
    id: "carto-light-basemap",
    data: "https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png",
    minZoom: 0,
    maxZoom: 20,
    tileSize: 256,
    refinementStrategy: "best-available",
    renderSubLayers: props => {
      const {west, south, east, north} = props.tile.bbox
      return new deckLib.BitmapLayer(props, {
        data: null,
        image: props.data,
        bounds: [west, south, east, north]
      })
    }
  })
}

function featureId(feature) {
  return String(feature?.id ?? feature?.properties?.parcel_id ?? "")
}

function addElevation(geometry, elevation) {
  const liftCoordinates = coordinates => {
    if (!Array.isArray(coordinates)) return coordinates
    if (coordinates.length >= 2 &&
        typeof coordinates[0] === "number" &&
        typeof coordinates[1] === "number") {
      return [coordinates[0], coordinates[1], elevation]
    }
    return coordinates.map(liftCoordinates)
  }
  if (geometry?.coordinates) {
    return {...geometry, coordinates: liftCoordinates(geometry.coordinates)}
  }
  if (geometry?.geometries) {
    return {
      ...geometry,
      geometries: geometry.geometries.map(item => addElevation(item, elevation))
    }
  }
  return geometry
}

function makeLayers(deckLib, data, scopeHovered, onHover) {
  const liftMeters = Number(data.lift_meters ?? 8)
  const transitionMs = Number(data.transition_ms ?? 180)
  const layers = [makeTileLayer(deckLib)]

  layers.push(new deckLib.GeoJsonLayer({
    id: "parcel-base",
    data: data.parcels,
    pickable: false,
    filled: true,
    stroked: true,
    getFillColor: [190, 198, 210, 20],
    getLineColor: [135, 145, 160, 105],
    getLineWidth: 1,
    lineWidthUnits: "pixels"
  }))

  if (data.scope_geometry) {
    layers.push(new deckLib.GeoJsonLayer({
      id: "scope-zone",
      data: {
        type: "Feature",
        id: "scope-preview",
        geometry: addElevation(data.scope_geometry, scopeHovered ? liftMeters : 0),
        properties: {scope_member: true, parcel_id: "当前方位范围"}
      },
      pickable: true,
      autoHighlight: false,
      filled: true,
      stroked: true,
      extruded: false,
      getFillColor: scopeHovered ? [72, 149, 239, 72] : [72, 149, 239, 42],
      getLineColor: scopeHovered ? [70, 230, 255, 255] : [45, 126, 247, 210],
      getLineWidth: scopeHovered ? 4 : 2.5,
      lineWidthUnits: "pixels",
      lineJointRounded: true,
      lineCapRounded: true,
      onHover
    }))
  }

  layers.push(new deckLib.GeoJsonLayer({
    id: "scope-parcels",
    data: data.selected_parcels,
    pickable: true,
    autoHighlight: false,
    filled: true,
    stroked: true,
    extruded: true,
    wireframe: true,
    material: {
      ambient: 0.42,
      diffuse: 0.62,
      shininess: 22,
      specularColor: [90, 95, 105]
    },
    getFillColor: [72, 149, 239, 52],
    getLineColor: [15, 70, 180, 255],
    getLineWidth: 2.5,
    lineWidthUnits: "pixels",
    lineJointRounded: true,
    lineCapRounded: true,
    getElevation: feature => {
      return scopeHovered ? liftMeters : 0
    },
    updateTriggers: {getElevation: [scopeHovered]},
    transitions: {getElevation: {duration: transitionMs}},
    onHover
  }))

  if (data.scope_outline) {
    layers.push(new deckLib.GeoJsonLayer({
      id: "scope-outline",
      data: {
        type: "Feature",
        geometry: addElevation(
          data.scope_outline,
          scopeHovered ? liftMeters + 0.5 : 0
        ),
        properties: {}
      },
      pickable: false,
      stroked: true,
      filled: false,
      getLineColor: scopeHovered ? [70, 230, 255, 255] : [15, 70, 180, 245],
      getLineWidth: scopeHovered ? 6 : 4,
      lineWidthUnits: "pixels",
      lineJointRounded: true,
      lineCapRounded: true
    }))
  }

  if (data.guides?.cross_sections?.length) {
    layers.push(new deckLib.GeoJsonLayer({
      id: "centerline-cross-sections",
      data: {
        type: "FeatureCollection",
        features: data.guides.cross_sections.map((geometry, index) => ({
          type: "Feature",
          id: `cross-${index}`,
          geometry
        }))
      },
      pickable: false,
      stroked: true,
      filled: false,
      getLineColor: [0, 145, 170, 190],
      lineWidthMinPixels: 2
    }))
  }

  if (data.guides?.centerline) {
    layers.push(new deckLib.GeoJsonLayer({
      id: "curved-centerline",
      data: {type: "Feature", geometry: data.guides.centerline, properties: {}},
      pickable: false,
      stroked: true,
      filled: false,
      getLineColor: [139, 61, 190, 245],
      lineWidthMinPixels: 4
    }))
  }

  if (data.boundary) {
    layers.push(new deckLib.GeoJsonLayer({
      id: "island-boundary",
      data: data.boundary,
      pickable: false,
      stroked: true,
      filled: false,
      getLineColor: [220, 38, 38, 255],
      lineWidthMinPixels: 3
    }))
  }
  return layers
}

export default function(component) {
  const {data, parentElement} = component
  const canvas = parentElement.querySelector(".hover-map-canvas")
  const shell = parentElement.querySelector(".hover-map-shell")
  const errorBox = parentElement.querySelector(".hover-map-error")
  if (!canvas || !shell || !errorBox) return

  const previous = deckInstances.get(parentElement)
  if (previous) {
    previous.finalize()
    deckInstances.delete(parentElement)
  }
  canvas.replaceChildren()
  errorBox.hidden = true
  let disposed = false
  let deckInstance = null
  let hoveredId = null
  let scopeHovered = false
  let handlePointerLeave = null
  let resetHoverOutsideMap = null

  loadDeckLibrary().then(deckLib => {
    if (disposed) return

    const redrawForHover = info => {
      const feature = info?.object
      const nextId = feature?.properties?.scope_member ? featureId(feature) : null
      const nextScopeHovered = Boolean(nextId)
      if (nextId === hoveredId && nextScopeHovered === scopeHovered) return
      hoveredId = nextId
      scopeHovered = nextScopeHovered
      shell.dataset.hoveredParcelId = hoveredId ?? ""
      shell.dataset.hoverActive = scopeHovered ? "true" : "false"
      deckInstance.setProps({
        layers: makeLayers(deckLib, data, scopeHovered, redrawForHover),
        getCursor: () => scopeHovered ? "pointer" : "grab"
      })
    }

    handlePointerLeave = event => {
      const related = event?.relatedTarget
      if (!related || !shell.contains(related)) redrawForHover({object: null})
    }
    shell.addEventListener("pointerleave", handlePointerLeave)
    shell.addEventListener("mouseleave", handlePointerLeave)
    shell.addEventListener("pointerout", handlePointerLeave)
    shell.addEventListener("mouseout", handlePointerLeave)
    resetHoverOutsideMap = event => {
      const rect = shell.getBoundingClientRect()
      const outside = event.clientX < rect.left || event.clientX > rect.right ||
        event.clientY < rect.top || event.clientY > rect.bottom
      if (outside) redrawForHover({object: null})
    }
    document.addEventListener("pointermove", resetHoverOutsideMap, true)
    document.addEventListener("mousemove", resetHoverOutsideMap, true)

    deckInstance = new deckLib.Deck({
      parent: canvas,
      width: "100%",
      height: "100%",
      controller: true,
      initialViewState: {
        longitude: data.view_state.longitude,
        latitude: data.view_state.latitude,
        zoom: data.view_state.zoom ?? 13.7,
        pitch: data.view_state.pitch ?? 32,
        bearing: data.view_state.bearing ?? 0,
        minPitch: 0,
        maxPitch: 60
      },
      layers: makeLayers(deckLib, data, scopeHovered, redrawForHover),
      getCursor: () => scopeHovered ? "pointer" : "grab",
      getTooltip: info => {
        if (!info?.object) return null
        const id = featureId(info.object)
        const belongs = Boolean(info.object.properties?.scope_member)
        return {
          text: belongs ? `${id} · 属于当前方位范围` : id,
          style: {fontSize: "12px"}
        }
      }
    })
    deckInstances.set(parentElement, deckInstance)
  }).catch(error => {
    if (disposed) return
    errorBox.hidden = false
    errorBox.textContent = `交互地图加载失败：${error.message}`
  })

  return () => {
    disposed = true
    if (handlePointerLeave) {
      shell.removeEventListener("pointerleave", handlePointerLeave)
      shell.removeEventListener("mouseleave", handlePointerLeave)
      shell.removeEventListener("pointerout", handlePointerLeave)
      shell.removeEventListener("mouseout", handlePointerLeave)
    }
    if (resetHoverOutsideMap) {
      document.removeEventListener("pointermove", resetHoverOutsideMap, true)
      document.removeEventListener("mousemove", resetHoverOutsideMap, true)
    }
    if (deckInstance) deckInstance.finalize()
    deckInstances.delete(parentElement)
  }
}
"""


_HOVER_SCOPE_MAP = st.components.v2.component(
    "urban_agent_hover_scope_map_v9",
    html=_COMPONENT_HTML,
    css=_COMPONENT_CSS,
    js=_COMPONENT_JS,
)


def _geometry_points(geometry: dict) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []

    def walk(node) -> None:
        if (
            isinstance(node, (list, tuple))
            and len(node) >= 2
            and isinstance(node[0], (int, float))
            and isinstance(node[1], (int, float))
        ):
            points.append((float(node[0]), float(node[1])))
        elif isinstance(node, (list, tuple)):
            for child in node:
                walk(child)

    walk(geometry.get("coordinates", []))
    return points


def _scope_outline_geometry(features: list[dict], target_ids: set[str]) -> dict | None:
    selected_geometries = [
        shape(feature["geometry"])
        for feature in features
        if str(feature.get("id")) in target_ids and feature.get("geometry")
    ]
    selected_geometries = [geometry for geometry in selected_geometries if not geometry.is_empty]
    if not selected_geometries:
        return None
    outline = mapping(unary_union(selected_geometries).boundary)
    return json.loads(json.dumps(outline))


def render_hover_scope_map(
    *,
    parcels: dict,
    target_parcel_ids: Iterable[str],
    boundary: dict,
    scope_geometry: dict | None = None,
    guides: dict | None = None,
    height: int = 620,
    lift_meters: float = 8.0,
    transition_ms: int = 180,
    key: str = "spatial-scope-hover-map",
):
    """Render neutral parcels and lift only hovered members of the active scope."""
    if parcels.get("type") != "FeatureCollection":
        raise ValueError("parcels 必须是 GeoJSON FeatureCollection")
    if not 1 <= lift_meters <= 50:
        raise ValueError("lift_meters 必须在 1-50 米之间")
    if not 0 <= transition_ms <= 2_000:
        raise ValueError("transition_ms 必须在 0-2000 毫秒之间")

    target_ids = {str(parcel_id) for parcel_id in target_parcel_ids}
    parcel_data = json.loads(json.dumps(parcels))
    features = parcel_data.get("features", [])
    selected_features = []
    for feature in features:
        parcel_id = str(feature.get("id"))
        properties = feature.setdefault("properties", {})
        belongs = parcel_id in target_ids
        properties["scope_member"] = belongs
        properties["parcel_id"] = parcel_id
        properties["base_fill"] = (
            [45, 126, 247, 55] if belongs else [190, 198, 210, 65]
        )
        properties["base_line"] = (
            [15, 70, 180, 255] if belongs else [135, 145, 160, 110]
        )
        if belongs:
            selected_features.append(feature)

    if scope_geometry:
        scope_outline = json.loads(
            json.dumps(mapping(shape(scope_geometry).boundary))
        )
    else:
        scope_outline = _scope_outline_geometry(features, target_ids)
    selected_parcels = {
        "type": "FeatureCollection",
        "features": selected_features,
    }

    boundary_points = [
        point
        for feature in boundary.get("features", [])
        for point in _geometry_points(feature.get("geometry", {}))
    ]
    if not boundary_points:
        raise ValueError("boundary 中没有可用于定位地图的坐标")
    longitude = sum(point[0] for point in boundary_points) / len(boundary_points)
    latitude = sum(point[1] for point in boundary_points) / len(boundary_points)

    return _HOVER_SCOPE_MAP(
        key=key,
        data={
            "parcels": parcel_data,
            "selected_parcels": selected_parcels,
            "scope_geometry": json.loads(json.dumps(scope_geometry)) if scope_geometry else None,
            "scope_outline": scope_outline,
            "boundary": boundary,
            "guides": guides,
            "lift_meters": float(lift_meters),
            "transition_ms": int(transition_ms),
            "view_state": {
                "longitude": longitude,
                "latitude": latitude,
                "zoom": 13.7,
                "pitch": 32,
                "bearing": 0,
            },
        },
        width="stretch",
        height=height,
    )
