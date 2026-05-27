"""Discover climbing segments by combining OSM trail geometries with IGN altimetry.

A "climb" = a contiguous ascending portion of an OSM way, with minimum total D+,
minimum length, and minimum average slope. Both directions of each way are scanned.

Output is cacheable per zone: re-running on the same area returns instantly.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Iterable

import requests

import _paths
import gpx_utils
import ign_altimetry


log = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "trail-route-finder/0.1 (https://github.com/nicolas-boisseau/trail-route-finder)"

# Trail-runnable highways. We deliberately skip motorways/primary roads.
# `service` is excluded (driveways, parking aisles) but `residential` is kept
# in case the climb passes through a village — common in viticole areas.
_TRAIL_QUERY = """
[out:json][timeout:120];
(
  way["highway"="path"](around:{r},{lat},{lon});
  way["highway"="track"](around:{r},{lat},{lon});
  way["highway"="footway"](around:{r},{lat},{lon});
  way["highway"="bridleway"](around:{r},{lat},{lon});
  way["highway"="cycleway"](around:{r},{lat},{lon});
  way["highway"="unclassified"](around:{r},{lat},{lon});
  way["highway"="tertiary"](around:{r},{lat},{lon});
);
out geom;
"""

# Sub-segment thresholds (tuned for low-relief viticole terrain).
MIN_DPLUS_M = 25.0       # at least 25 m climb to count as a segment
MIN_LENGTH_M = 150.0     # at least 150 m long
MIN_AVG_SLOPE_PCT = 4.0  # at least 4% average slope (so ~50m climb in 1.25km is too gentle)
SAMPLE_STEP_M = 50.0     # IGN sampling resolution


@dataclass
class Climb:
    id: str                       # stable hash
    way_id: int
    way_name: str
    way_type: str                 # OSM highway value
    direction: str                # "fwd" or "bwd"
    geometry: list                # list of [lon, lat, ele] along the climb
    length_m: float
    dplus_m: float
    avg_slope_pct: float
    max_slope_pct: float
    start_elev_m: float
    top_elev_m: float

    @classmethod
    def make(cls, way_id, way_name, way_type, direction, geom_with_ele):
        if len(geom_with_ele) < 2:
            return None
        length = 0.0
        for (lon1, lat1, _), (lon2, lat2, _) in zip(geom_with_ele, geom_with_ele[1:]):
            length += gpx_utils.haversine_m((lon1, lat1), (lon2, lat2))
        elevs = [p[2] for p in geom_with_ele]
        dplus = elevs[-1] - elevs[0]
        avg_slope = (dplus / length * 100) if length > 0 else 0.0
        # max sustained slope over any 50m window
        max_slope = 0.0
        for i in range(len(geom_with_ele) - 1):
            d = gpx_utils.haversine_m(
                (geom_with_ele[i][0], geom_with_ele[i][1]),
                (geom_with_ele[i + 1][0], geom_with_ele[i + 1][1]),
            )
            if d > 0:
                s = (elevs[i + 1] - elevs[i]) / d * 100
                if s > max_slope:
                    max_slope = s
        sid = hashlib.md5(f"{way_id}/{direction}/{int(geom_with_ele[0][0]*1e5)}/{int(geom_with_ele[0][1]*1e5)}".encode()).hexdigest()[:10]
        return cls(
            id=sid, way_id=way_id, way_name=way_name, way_type=way_type, direction=direction,
            geometry=[[p[0], p[1], p[2]] for p in geom_with_ele],
            length_m=length, dplus_m=dplus, avg_slope_pct=avg_slope, max_slope_pct=max_slope,
            start_elev_m=elevs[0], top_elev_m=elevs[-1],
        )


def _zone_key(lat: float, lon: float, radius_m: int) -> str:
    return hashlib.md5(f"{round(lat, 3)}/{round(lon, 3)}/{radius_m}".encode()).hexdigest()[:12]


def _cache_path(lat: float, lon: float, radius_m: int) -> Path:
    return _paths.data_dir() / "segments_cache" / f"{_zone_key(lat, lon, radius_m)}.json"


def _overpass(query: str, attempts: int = 3) -> dict:
    last = None
    for i in range(attempts):
        try:
            r = requests.post(
                OVERPASS_URL,
                headers={"User-Agent": USER_AGENT},
                data={"data": query},
                timeout=180,
            )
            if r.status_code in (504, 503, 429):
                last = f"HTTP {r.status_code}"
                log.warning("Overpass %s — retry %d/%d", r.status_code, i + 1, attempts)
                time.sleep(3 + i * 5)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            last = str(e)
            log.warning("Overpass error %s — retry %d/%d", e, i + 1, attempts)
            time.sleep(3 + i * 5)
    raise RuntimeError(f"Overpass failed after {attempts} attempts: {last}")


def _detect_climbs_in_profile(geom: list, elevs: list, way_id: int, way_name: str, way_type: str) -> Iterable[Climb]:
    """Scan a profile in both directions; emit climbs meeting the thresholds."""
    if len(geom) != len(elevs) or len(geom) < 2:
        return
    for direction, idx_seq in (("fwd", range(len(geom))), ("bwd", range(len(geom) - 1, -1, -1))):
        idx = list(idx_seq)
        cur_start = 0
        # Walk through, accumulate ascending sub-runs; emit when descent breaks the run
        i = 0
        while i < len(idx) - 1:
            j = i
            cum_up = 0.0
            cum_dist = 0.0
            top_elev = elevs[idx[j]]
            while j < len(idx) - 1:
                e1 = elevs[idx[j]]
                e2 = elevs[idx[j + 1]]
                lon1, lat1 = geom[idx[j]]
                lon2, lat2 = geom[idx[j + 1]]
                d = gpx_utils.haversine_m((lon1, lat1), (lon2, lat2))
                if e2 < e1 - 2.0:  # 2m drop tolerance to filter noise
                    break
                cum_up += max(0.0, e2 - e1)
                cum_dist += d
                top_elev = max(top_elev, e2)
                j += 1
            if (cum_up >= MIN_DPLUS_M and cum_dist >= MIN_LENGTH_M
                    and cum_up / cum_dist * 100 >= MIN_AVG_SLOPE_PCT):
                geom_with_ele = [(geom[idx[k]][0], geom[idx[k]][1], elevs[idx[k]]) for k in range(i, j + 1)]
                c = Climb.make(way_id, way_name, way_type, direction, geom_with_ele)
                if c:
                    yield c
            i = max(j, i + 1)


def discover_zone(lat: float, lon: float, radius_m: int = 8000, force_refresh: bool = False) -> list[Climb]:
    """Build (or load from cache) the list of climbing segments in a zone."""
    cache = _cache_path(lat, lon, radius_m)
    if cache.exists() and not force_refresh:
        log.info("Using cached segments at %s", cache)
        data = json.loads(cache.read_text())
        return [Climb(**c) for c in data["climbs"]]

    log.info("Discovering segments in (%.4f, %.4f) radius %d m", lat, lon, radius_m)
    log.info("  Step 1/3: Overpass query for trail ways...")
    ways = _overpass(_TRAIL_QUERY.format(r=radius_m, lat=lat, lon=lon)).get("elements", [])
    log.info("    → %d OSM ways", len(ways))

    log.info("  Step 2/3: resampling each way at %d m + batched IGN altimetry...", int(SAMPLE_STEP_M))
    # Resample each way's geometry to ~50m spacing.
    way_samples: list[tuple[int, str, str, list[tuple[float, float]]]] = []
    for w in ways:
        geom = [(p["lon"], p["lat"]) for p in (w.get("geometry") or [])]
        if len(geom) < 2:
            continue
        # Convert to (lon, lat, 0) for resample_polyline
        coords3 = [(g[0], g[1], 0.0) for g in geom]
        resampled = gpx_utils.resample_polyline(coords3, step_m=SAMPLE_STEP_M)
        if len(resampled) < 2:
            continue
        way_samples.append((
            w["id"],
            (w.get("tags") or {}).get("name", "?"),
            (w.get("tags") or {}).get("highway", "?"),
            resampled,
        ))
    log.info("    → %d ways after resampling", len(way_samples))

    # Concatenate all sample points for batched IGN query
    all_points: list[tuple[float, float]] = []
    way_slices: list[tuple[int, int]] = []
    for _, _, _, samp in way_samples:
        start = len(all_points)
        all_points.extend(samp)
        way_slices.append((start, len(all_points)))
    log.info("    → %d total points to query IGN", len(all_points))

    elevs = ign_altimetry.get_elevations(all_points)
    log.info("    → IGN done")

    log.info("  Step 3/3: detecting climbs...")
    climbs: list[Climb] = []
    for (way_id, way_name, way_type, samp), (a, b) in zip(way_samples, way_slices):
        way_elevs = elevs[a:b]
        if any(e != e for e in way_elevs):  # NaN — skip
            continue
        for c in _detect_climbs_in_profile(samp, way_elevs, way_id, way_name, way_type):
            climbs.append(c)

    # Dedupe: keep best per (way_id, direction)
    by_key: dict = {}
    for c in climbs:
        key = (c.way_id, c.direction)
        prev = by_key.get(key)
        if prev is None or c.dplus_m > prev.dplus_m:
            by_key[key] = c
    climbs = sorted(by_key.values(), key=lambda c: -c.dplus_m)

    log.info("  → %d unique climbing segments", len(climbs))

    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(
        {
            "lat": lat, "lon": lon, "radius_m": radius_m,
            "discovered_at": time.time(),
            "climbs": [asdict(c) for c in climbs],
        },
        ensure_ascii=False, indent=1,
    ))
    log.info("Cached to %s", cache)
    return climbs
