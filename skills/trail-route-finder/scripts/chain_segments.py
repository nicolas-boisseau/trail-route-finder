"""Build trail loops by chaining nearby climbing segments.

Phase 3 of the segments-first approach:
 1. Given a start point, take all segments within reach.
 2. Try combinations of 2-5 segments (ordered greedily nearest-neighbor from
    the start) and route start → seg1.bottom → seg1.top → seg2.bottom → ... → start.
 3. Filter by total distance and IGN-precise D+, dedupe, return top N.
"""
from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass
from typing import Optional

import brouter
import gpx_utils
import ign_altimetry
import segments as seg_mod


log = logging.getLogger(__name__)


@dataclass
class ChainResult:
    track: brouter.BRouterTrack
    climbs_used: list[seg_mod.Climb]
    dplus_m: float
    elevations: list[float]


def _dist(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    """Haversine between (lon, lat) points."""
    return gpx_utils.haversine_m(p1, p2)


def _segment_endpoints(c: seg_mod.Climb) -> tuple[tuple[float, float], tuple[float, float]]:
    """(bottom_lonlat, top_lonlat) — direction-aware."""
    first = (c.geometry[0][0], c.geometry[0][1])
    last = (c.geometry[-1][0], c.geometry[-1][1])
    # Since each Climb is already direction-encoded (low → high), first=bottom, last=top.
    return first, last


def _candidates_near(
    start_lat: float, start_lon: float,
    climbs: list[seg_mod.Climb],
    max_approach_km: float = 4.0,
) -> list[seg_mod.Climb]:
    """Keep segments whose entry (bottom) is within walking detour of start."""
    sl = (start_lon, start_lat)
    out = []
    for c in climbs:
        bot, _ = _segment_endpoints(c)
        if _dist(sl, bot) / 1000.0 <= max_approach_km:
            out.append(c)
    out.sort(key=lambda c: -c.dplus_m)
    return out


def _greedy_order(start_lonlat: tuple[float, float], climbs: list[seg_mod.Climb]) -> list[seg_mod.Climb]:
    """Order so each next climb starts near where the previous ended."""
    remaining = list(climbs)
    ordered: list[seg_mod.Climb] = []
    cur = start_lonlat
    while remaining:
        best = min(remaining, key=lambda c: _dist(cur, _segment_endpoints(c)[0]))
        ordered.append(best)
        cur = _segment_endpoints(best)[1]
        remaining.remove(best)
    return ordered


def _quick_distance_estimate(
    start_lonlat: tuple[float, float], ordered: list[seg_mod.Climb],
) -> float:
    """Crude perimeter estimate: connectors as crow-flies + segment own lengths.

    BRouter routes between waypoints are longer than the straight line. We
    factor ×1.35 for the connectors to keep the prefilter forgiving.
    """
    total = 0.0
    cur = start_lonlat
    for c in ordered:
        bot, top = _segment_endpoints(c)
        total += _dist(cur, bot) * 1.35
        total += c.length_m
        cur = top
    total += _dist(cur, start_lonlat) * 1.35
    return total


def find_chained_routes(
    start_lat: float, start_lon: float,
    climbs_all: list[seg_mod.Climb],
    dist_min_km: float, dist_max_km: float,
    dplus_min_m: float = 0.0,
    n_candidates: int = 5,
    max_combo_size: int = 6,
    top_segments_to_consider: int = 22,
    profile: str = "trail-hilly",
    max_brouter_calls: int = 120,
) -> list[ChainResult]:
    """Generate candidate loops by chaining climbing segments."""
    start_ll = (start_lon, start_lat)

    pool = _candidates_near(start_lat, start_lon, climbs_all, max_approach_km=dist_max_km * 0.45)
    pool = pool[:top_segments_to_consider]
    log.info("Pool: %d candidate segments (top D+ within %.1f km)", len(pool), dist_max_km * 0.45)
    if not pool:
        return []

    # Generate combos, pre-order by greedy NN, prefilter by quick distance estimate.
    seen_keys = set()
    survivors: list[tuple[list[seg_mod.Climb], float]] = []
    for k in range(2, min(max_combo_size, len(pool)) + 1):
        for combo in itertools.combinations(pool, k):
            key = tuple(sorted(c.id for c in combo))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            ordered = _greedy_order(start_ll, list(combo))
            est = _quick_distance_estimate(start_ll, ordered) / 1000.0
            # forgiving window before paying for BRouter
            if dist_min_km * 0.70 <= est <= dist_max_km * 1.25:
                # quick D+ estimate from segments alone (lower bound)
                est_dplus = sum(c.dplus_m for c in ordered)
                survivors.append((ordered, est_dplus))

    # Rank prefilter survivors by expected D+
    survivors.sort(key=lambda x: -x[1])
    survivors = survivors[:max_brouter_calls]
    log.info("Survived distance pre-filter: %d combos (capped to %d)", len(survivors), max_brouter_calls)

    # Route + IGN-precise D+ each survivor
    results: list[ChainResult] = []
    for i, (ordered, est_dplus) in enumerate(survivors):
        waypoints = [(start_lat, start_lon)]
        for c in ordered:
            bot, top = _segment_endpoints(c)
            waypoints.append((bot[1], bot[0]))
            waypoints.append((top[1], top[0]))
        waypoints.append((start_lat, start_lon))

        t = brouter.multi_route(waypoints, profile=profile)
        if t is None:
            continue
        if not (dist_min_km * 0.85 <= t.length_km <= dist_max_km * 1.15):
            log.debug("  [%d] OOB %.1f km — skip", i + 1, t.length_km)
            continue
        # IGN-precise D+
        points = gpx_utils.resample_polyline(t.coordinates, step_m=25.0)
        elevs = ign_altimetry.get_elevations(points)
        dplus = ign_altimetry.compute_dplus(elevs, smoothing_threshold_m=5.0)
        log.info(
            "  [%d] %s: %.1f km · D+ %.0f m (est %.0f) · %s",
            i + 1,
            " → ".join((c.way_name or "?")[:18] for c in ordered),
            t.length_km, dplus, est_dplus,
            "KEEP" if dplus >= dplus_min_m else "below target",
        )
        if dplus >= dplus_min_m:
            results.append(ChainResult(track=t, climbs_used=ordered, dplus_m=dplus, elevations=elevs))

    results.sort(key=lambda r: -r.dplus_m)

    # Dedupe by Jaccard overlap on raw track geometry
    kept: list[ChainResult] = []
    for r in results:
        if any(gpx_utils.jaccard_overlap(r.track.coordinates, k.track.coordinates) >= 0.55 for k in kept):
            continue
        kept.append(r)
        if len(kept) >= n_candidates:
            break
    return kept
