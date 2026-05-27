"""Minimal GPX writer + simple geometry helpers."""
from __future__ import annotations

import math
from pathlib import Path

import gpxpy
import gpxpy.gpx


def write_gpx(
    coords: list[tuple[float, float, float]],
    out_path: Path,
    name: str,
    description: str = "",
) -> Path:
    """Write coordinates [(lon, lat, ele), ...] as a single-track GPX."""
    gpx = gpxpy.gpx.GPX()
    gpx.name = name
    gpx.description = description
    track = gpxpy.gpx.GPXTrack(name=name)
    gpx.tracks.append(track)
    seg = gpxpy.gpx.GPXTrackSegment()
    track.segments.append(seg)
    for lon, lat, ele in coords:
        seg.points.append(gpxpy.gpx.GPXTrackPoint(latitude=lat, longitude=lon, elevation=ele))
    out_path.write_text(gpx.to_xml(), encoding="utf-8")
    return out_path


_EARTH_R = 6_371_008.8


def haversine_m(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    """Great-circle distance between (lon, lat) points in meters."""
    lon1, lat1 = math.radians(p1[0]), math.radians(p1[1])
    lon2, lat2 = math.radians(p2[0]), math.radians(p2[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_R * math.asin(math.sqrt(a))


def resample_polyline(coords: list[tuple[float, float, float]], step_m: float = 50.0) -> list[tuple[float, float]]:
    """Resample a polyline to roughly uniform spacing in meters.

    Returns (lon, lat) only — used to feed elevation API at a controlled density.
    """
    if not coords:
        return []
    out: list[tuple[float, float]] = [(coords[0][0], coords[0][1])]
    carry = 0.0
    for (lon1, lat1, _), (lon2, lat2, _) in zip(coords, coords[1:]):
        seg = haversine_m((lon1, lat1), (lon2, lat2))
        if seg == 0:
            continue
        carry += seg
        while carry >= step_m:
            t = 1.0 - (carry - step_m) / seg
            out.append((lon1 + t * (lon2 - lon1), lat1 + t * (lat2 - lat1)))
            carry -= step_m
    if (coords[-1][0], coords[-1][1]) != out[-1]:
        out.append((coords[-1][0], coords[-1][1]))
    return out


def bbox(coords: list[tuple[float, float, float]]) -> tuple[float, float, float, float]:
    """Return (min_lon, min_lat, max_lon, max_lat)."""
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return min(lons), min(lats), max(lons), max(lats)


def jaccard_overlap(a: list[tuple[float, float, float]], b: list[tuple[float, float, float]], cell_m: float = 100.0) -> float:
    """Approximate Jaccard overlap by rasterizing each track to a ~100m grid.

    Used to dedupe candidate loops that share most of their geometry.
    Cheap, no shapely needed for this.
    """
    def cells(track):
        s = set()
        for lon, lat, _ in track:
            # crude meters-per-degree at this latitude
            mlat = 111_320.0
            mlon = 111_320.0 * math.cos(math.radians(lat))
            s.add((round(lon * mlon / cell_m), round(lat * mlat / cell_m)))
        return s

    ca, cb = cells(a), cells(b)
    if not ca or not cb:
        return 0.0
    return len(ca & cb) / len(ca | cb)
