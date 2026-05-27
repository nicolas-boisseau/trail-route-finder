"""Hilltop mode: force routes through known high POIs (peaks, châteaux, viewpoints).

BRouter's round-trip places auto-waypoints to minimize cost — which puts them
in valleys, not on tops. By forcing waypoints at OSM hilltop POIs (validated
by IGN altimetry), we get loops that DO climb each hill.

This is the mode trail runners actually want in low-relief areas like the
Gironde viticole, where the absolute hills are small (40-70m) but ridable D+
comes from stringing 3-4 of them together.
"""
from __future__ import annotations

import itertools
import logging
import math
from typing import Optional

import brouter
import pois as pois_mod


log = logging.getLogger(__name__)


def _bearing_deg(from_lat: float, from_lon: float, to_lat: float, to_lon: float) -> float:
    """Bearing from one point to another, 0=N, 90=E, ..."""
    dy = to_lat - from_lat
    dx = (to_lon - from_lon) * math.cos(math.radians((from_lat + to_lat) / 2))
    return (math.degrees(math.atan2(dx, dy)) + 360) % 360


def _bucket_by_sector(start_lat: float, start_lon: float, hpois: list[pois_mod.HilltopPOI], n_sectors: int = 8):
    """Group hilltops by direction sector from the start."""
    buckets: dict[int, list[pois_mod.HilltopPOI]] = {i: [] for i in range(n_sectors)}
    width = 360 / n_sectors
    for p in hpois:
        b = _bearing_deg(start_lat, start_lon, p.lat, p.lon)
        buckets[int(b // width)].append(p)
    for v in buckets.values():
        v.sort(key=lambda p: -p.rel_height_m)
    return buckets


def _gen_combos(
    start_lat: float, start_lon: float,
    hpois: list[pois_mod.HilltopPOI],
    hills_per_combo: int,
):
    """Yield all distinct unordered subsets of size `hills_per_combo`, each
    ordered clockwise by bearing from start (so the route forms a convex-ish
    polygon without crisscrossing).

    No angular-spread filter — let the distance filter downstream reject loops
    that come out too long or too short.
    """
    for combo in itertools.combinations(hpois, hills_per_combo):
        ordered = sorted(combo, key=lambda p: _bearing_deg(start_lat, start_lon, p.lat, p.lon))
        yield list(ordered)


def find_hilltop_loops(
    start_lat: float,
    start_lon: float,
    target_dist_km: float,
    profile: str = "trail-hilly",
    overpass_radius_m: Optional[int] = None,
) -> list[brouter.BRouterTrack]:
    """Generate candidate loops through hilltops around the start.

    Returns BRouterTracks (no D+ filter — caller does precise filtering downstream).
    """
    if overpass_radius_m is None:
        # POIs up to ~half the perimeter away from start
        overpass_radius_m = int(target_dist_km * 1000 * 0.45)

    hpois = pois_mod.query_high_pois(
        start_lat, start_lon, overpass_radius_m,
        min_rel_height_m=10.0, keep_top=16,
    )
    if not hpois:
        log.warning("No high POIs found within %d m", overpass_radius_m)
        return []
    log.info("Top hilltops found:")
    for p in hpois[:8]:
        log.info("  +%5.1f m  %-10s %s  (%.4f, %.4f)", p.rel_height_m, p.kind, p.name, p.lat, p.lon)

    tracks: list[brouter.BRouterTrack] = []
    for hills_per_combo in (2, 3, 4):
        if len(hpois) < hills_per_combo:
            continue
        for combo in _gen_combos(start_lat, start_lon, hpois, hills_per_combo):
            waypoints = [(start_lat, start_lon)] + [(p.lat, p.lon) for p in combo] + [(start_lat, start_lon)]
            t = brouter.multi_route(waypoints, profile=profile)
            if t is None:
                continue
            names = " → ".join(p.name[:15] for p in combo)
            log.info("  [%dh] %s: %.1f km, D+ ~%dm (SRTM)", hills_per_combo, names, t.length_km, t.ascend_filtered_m)
            tracks.append(t)
    return tracks
