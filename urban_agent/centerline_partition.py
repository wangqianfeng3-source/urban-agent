"""Curvilinear directional partitioning for long, irregular island geometries.

The module is intentionally independent from requirement parsing and planning
actions.  It converts one projected boundary polygon into a reusable local
coordinate frame: ``s`` runs from south to north along the centerline and
``q`` runs from the west shore to the east shore on each cross-section.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from shapely import make_valid
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union


CENTERLINE_INTERVALS = 100
SMOOTHING_PASSES = 2
CORRECTION_PASSES = 1
ALGORITHM_ID = "centerline-v1"


class CenterlinePartitionError(ValueError):
    """Raised when a stable centerline frame cannot be built."""


@dataclass(frozen=True)
class CenterlineStation:
    s: float
    center: tuple[float, float]
    west: tuple[float, float]
    east: tuple[float, float]


@dataclass(frozen=True)
class CenterlineFrame:
    boundary: object
    stations: tuple[CenterlineStation, ...]
    length_m: float

    @property
    def centerline(self) -> LineString:
        return LineString([station.center for station in self.stations])

    def sampled_cross_sections(self, step: int = 10) -> tuple[LineString, ...]:
        indexes = list(range(0, len(self.stations), max(1, step)))
        if indexes[-1] != len(self.stations) - 1:
            indexes.append(len(self.stations) - 1)
        return tuple(
            LineString([self.stations[index].west, self.stations[index].east])
            for index in indexes
            if self.stations[index].west != self.stations[index].east
        )


@dataclass(frozen=True)
class ScopeWindow:
    s0: float = 0.0
    s1: float = 1.0
    q0: float = 0.0
    q1: float = 1.0


def _line_parts(geometry) -> list[LineString]:
    if geometry.is_empty:
        return []
    if geometry.geom_type == "LineString":
        return [geometry]
    if geometry.geom_type in {"MultiLineString", "GeometryCollection"}:
        parts: list[LineString] = []
        for item in geometry.geoms:
            parts.extend(_line_parts(item))
        return parts
    return []


def _extreme_coordinate(boundary: Polygon, *, north: bool) -> tuple[float, float]:
    coordinates = list(boundary.exterior.coords)
    target_y = max(point[1] for point in coordinates) if north else min(
        point[1] for point in coordinates
    )
    tolerance = max(boundary.bounds[3] - boundary.bounds[1], 1.0) * 1e-10
    candidates = [point for point in coordinates if abs(point[1] - target_y) <= tolerance]
    center_x = boundary.centroid.x
    point = min(candidates, key=lambda item: abs(item[0] - center_x))
    return float(point[0]), float(point[1])


def _slice_segment(boundary: Polygon, line: LineString, reference) -> LineString:
    parts = _line_parts(boundary.intersection(line))
    parts = [part for part in parts if part.length > 1e-7]
    if not parts:
        raise CenterlinePartitionError("岛屿边界与中心线截面没有形成有效线段")
    if reference is None:
        return max(parts, key=lambda part: part.length)
    point = Point(reference)
    containing = [part for part in parts if part.distance(point) <= 1e-6]
    return max(containing, key=lambda part: part.length) if containing else min(
        parts, key=lambda part: part.distance(point)
    )


def _ordered_endpoints(
    segment: LineString,
    direction: tuple[float, float],
) -> tuple[tuple[float, float], tuple[float, float]]:
    coordinates = list(segment.coords)
    dx, dy = direction
    ordered = sorted(coordinates, key=lambda point: point[0] * dx + point[1] * dy)
    return tuple(ordered[0][:2]), tuple(ordered[-1][:2])


def _smooth(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    result = list(points)
    for _ in range(SMOOTHING_PASSES):
        updated = [result[0]]
        for index in range(1, len(result) - 1):
            previous, current, following = result[index - 1:index + 2]
            updated.append((
                (previous[0] + 2 * current[0] + following[0]) / 4,
                (previous[1] + 2 * current[1] + following[1]) / 4,
            ))
        updated.append(result[-1])
        result = updated
    return result


def build_centerline_frame(boundary) -> CenterlineFrame:
    """Build the fixed 101-station, once-corrected curvilinear frame."""
    if boundary.is_empty or boundary.geom_type != "Polygon" or not boundary.is_valid:
        raise CenterlinePartitionError("中心线模式需要单一且有效的岛屿 Polygon 边界")

    min_x, min_y, max_x, max_y = boundary.bounds
    span_x, span_y = max_x - min_x, max_y - min_y
    if span_x <= 0 or span_y <= 0:
        raise CenterlinePartitionError("岛屿边界尺寸无效，无法生成中心线")
    line_margin = math.hypot(span_x, span_y) * 2

    south = _extreme_coordinate(boundary, north=False)
    north = _extreme_coordinate(boundary, north=True)
    initial = [south]
    previous = south
    for index in range(1, CENTERLINE_INTERVALS):
        y = min_y + span_y * index / CENTERLINE_INTERVALS
        horizontal = LineString([(min_x - line_margin, y), (max_x + line_margin, y)])
        segment = _slice_segment(boundary, horizontal, previous)
        west, east = _ordered_endpoints(segment, (1.0, 0.0))
        previous = ((west[0] + east[0]) / 2, (west[1] + east[1]) / 2)
        initial.append(previous)
    initial.append(north)

    smoothed = _smooth(initial)
    corrected: list[tuple[tuple[float, float], tuple[float, float], tuple[float, float]]] = []
    for index, current in enumerate(smoothed):
        if index == 0:
            tangent = (
                smoothed[1][0] - current[0],
                smoothed[1][1] - current[1],
            )
        elif index == len(smoothed) - 1:
            tangent = (
                current[0] - smoothed[index - 1][0],
                current[1] - smoothed[index - 1][1],
            )
        else:
            tangent = (
                smoothed[index + 1][0] - smoothed[index - 1][0],
                smoothed[index + 1][1] - smoothed[index - 1][1],
            )
        magnitude = math.hypot(*tangent)
        if magnitude <= 1e-9:
            raise CenterlinePartitionError(f"中心线第 {index} 个点无法确定切线方向")
        normal = (-tangent[1] / magnitude, tangent[0] / magnitude)
        if normal[0] < 0:
            normal = (-normal[0], -normal[1])

        cross_line = LineString([
            (current[0] - normal[0] * line_margin, current[1] - normal[1] * line_margin),
            (current[0] + normal[0] * line_margin, current[1] + normal[1] * line_margin),
        ])
        parts = _line_parts(boundary.intersection(cross_line))
        parts = [part for part in parts if part.length > 1e-7]
        if not parts:
            if index in {0, len(smoothed) - 1}:
                corrected.append((current, current, current))
                continue
            raise CenterlinePartitionError(f"中心线第 {index} 个垂直截面没有穿过岛屿")
        point = Point(current)
        containing = [part for part in parts if part.distance(point) <= 1e-6]
        segment = max(containing, key=lambda part: part.length) if containing else min(
            parts, key=lambda part: part.distance(point)
        )
        west, east = _ordered_endpoints(segment, normal)
        center = ((west[0] + east[0]) / 2, (west[1] + east[1]) / 2)
        corrected.append((center, west, east))

    corrected[0] = (south, south, south)
    corrected[-1] = (north, north, north)
    distances = [0.0]
    for index in range(1, len(corrected)):
        previous_center = corrected[index - 1][0]
        center = corrected[index][0]
        distances.append(distances[-1] + math.dist(previous_center, center))
    total_length = distances[-1]
    if total_length <= 0:
        raise CenterlinePartitionError("中心线长度为零")

    stations = tuple(
        CenterlineStation(
            s=distance / total_length,
            center=center,
            west=west,
            east=east,
        )
        for distance, (center, west, east) in zip(distances, corrected)
    )
    if any(not boundary.covers(Point(station.center)) for station in stations):
        raise CenterlinePartitionError("校正后的中心线存在岛屿范围外点")
    return CenterlineFrame(boundary=boundary, stations=stations, length_m=total_length)


def _interpolate_station(frame: CenterlineFrame, s: float) -> CenterlineStation:
    s = min(1.0, max(0.0, s))
    stations = frame.stations
    if s <= 0:
        return stations[0]
    if s >= 1:
        return stations[-1]
    for index in range(1, len(stations)):
        right = stations[index]
        if right.s >= s:
            left = stations[index - 1]
            span = right.s - left.s
            ratio = 0.0 if span <= 1e-12 else (s - left.s) / span

            def interpolate(a, b):
                return (a[0] + (b[0] - a[0]) * ratio, a[1] + (b[1] - a[1]) * ratio)

            return CenterlineStation(
                s=s,
                center=interpolate(left.center, right.center),
                west=interpolate(left.west, right.west),
                east=interpolate(left.east, right.east),
            )
    return stations[-1]


def _point_at_q(station: CenterlineStation, q: float) -> tuple[float, float]:
    return (
        station.west[0] + (station.east[0] - station.west[0]) * q,
        station.west[1] + (station.east[1] - station.west[1]) * q,
    )


def window_geometry(frame: CenterlineFrame, window: ScopeWindow):
    """Render an ``s/q`` window as clipped adjacent cross-section cells."""
    stations = [_interpolate_station(frame, window.s0)]
    stations.extend(
        station for station in frame.stations if window.s0 < station.s < window.s1
    )
    stations.append(_interpolate_station(frame, window.s1))
    q0 = -0.5 if window.q0 <= 1e-12 else window.q0
    q1 = 1.5 if window.q1 >= 1 - 1e-12 else window.q1
    cells = []
    for left, right in zip(stations, stations[1:]):
        cell = Polygon([
            _point_at_q(left, q0),
            _point_at_q(right, q0),
            _point_at_q(right, q1),
            _point_at_q(left, q1),
        ])
        if not cell.is_valid:
            cell = make_valid(cell)
        if not cell.is_empty and cell.area > 1e-7:
            cells.append(cell)
    if not cells:
        return Polygon()
    merged = unary_union(cells)
    if not merged.is_valid:
        merged = make_valid(merged)
    return merged.intersection(frame.boundary)


def nested_window(parent: ScopeWindow, term: str, ratios: dict[str, float]) -> ScopeWindow:
    """Map one directional term into a parent window using the same frame."""
    ds, dq = parent.s1 - parent.s0, parent.q1 - parent.q0
    if term == "north":
        return ScopeWindow(parent.s1 - ratios["north"] * ds, parent.s1, parent.q0, parent.q1)
    if term == "south":
        return ScopeWindow(parent.s0, parent.s0 + ratios["south"] * ds, parent.q0, parent.q1)
    if term == "east":
        return ScopeWindow(parent.s0, parent.s1, parent.q1 - ratios["east"] * dq, parent.q1)
    if term == "west":
        return ScopeWindow(parent.s0, parent.s1, parent.q0, parent.q0 + ratios["west"] * dq)
    if term == "middle":
        return ScopeWindow(
            parent.s0 + ratios["south"] * ds,
            parent.s1 - ratios["north"] * ds,
            parent.q0 + ratios["west"] * dq,
            parent.q1 - ratios["east"] * dq,
        )
    if term == "north_tip":
        return ScopeWindow(
            parent.s1 - ratios["north_tip"] * ds,
            parent.s1,
            parent.q0,
            parent.q1,
        )
    if term == "south_tip":
        return ScopeWindow(
            parent.s0,
            parent.s0 + ratios["south_tip"] * ds,
            parent.q0,
            parent.q1,
        )
    raise CenterlinePartitionError(f"中心线模式不支持方向词：{term}")


def resolve_directional_terms(
    frame: CenterlineFrame,
    terms: tuple[str, ...],
    ratios: dict[str, float],
):
    window = ScopeWindow()
    for term in terms:
        window = nested_window(window, term, ratios)
    return window_geometry(frame, window)
