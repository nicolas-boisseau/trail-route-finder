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
import math
from dataclasses import dataclass, field
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
    # Maps climb.id → total ascents in the route. Defaults to 1 per climb in
    # `climbs_used`. Populated when hill-repeats boost or manual mode adds reps.
    reps_per_climb: dict = field(default_factory=dict)

    def reps_for(self, climb: seg_mod.Climb) -> int:
        return self.reps_per_climb.get(climb.id, 1)


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


def _build_waypoints(
    start_lat: float, start_lon: float,
    ordered_climbs: list[seg_mod.Climb],
    reps_by_id: Optional[dict] = None,
) -> list[tuple[float, float]]:
    """Build the (lat, lon) waypoint list for a route through ordered climbs.

    Each climb is traversed `reps_by_id[climb.id]` times (default 1).
    Reps > 1 means: bot → top → bot → top → ... (N ascents, N-1 descents in between).
    """
    waypoints = [(start_lat, start_lon)]
    for c in ordered_climbs:
        bot, top = _segment_endpoints(c)
        n = (reps_by_id or {}).get(c.id, 1)
        for _ in range(n):
            waypoints.append((bot[1], bot[0]))
            waypoints.append((top[1], top[0]))
    waypoints.append((start_lat, start_lon))
    return waypoints


def _boost_with_repeats(
    base: ChainResult,
    start_lat: float, start_lon: float,
    target_dplus_m: float,
    dist_max_km: float,
    profile: str,
) -> Optional[ChainResult]:
    """Attempt to lift base.dplus_m to >= target_dplus_m by repeating one of its climbs.

    Strategy: pick the most "D+-efficient" climb (highest D+ per meter of
    repeat-distance), compute reps needed to close the gap, re-route via BRouter,
    re-evaluate IGN D+. Returns None if no climb can close the gap within budget.
    """
    gap = target_dplus_m - base.dplus_m
    if gap <= 0:
        return None

    budget_km = dist_max_km * 1.15
    current_km = base.track.length_km

    # Steepest first: each extra ascent gives `dplus_m` D+ at the cost of 2×length_m of
    # extra distance (one descent back + one new ascent), so dplus/length is the ranker.
    by_efficiency = sorted(
        base.climbs_used,
        key=lambda c: -c.dplus_m / max(c.length_m, 1.0),
    )

    for boost_climb in by_efficiency:
        k_extra = math.ceil(gap / boost_climb.dplus_m)
        extra_km = 2 * k_extra * boost_climb.length_m / 1000.0
        if current_km + extra_km > budget_km:
            # Not enough room; try the max number of extra reps that does fit
            k_max = int((budget_km - current_km) // (2 * boost_climb.length_m / 1000.0))
            if k_max < 1:
                continue
            k_extra = k_max

        reps_by_id = {c.id: 1 for c in base.climbs_used}
        reps_by_id[boost_climb.id] = 1 + k_extra

        waypoints = _build_waypoints(start_lat, start_lon, base.climbs_used, reps_by_id)
        t = brouter.multi_route(waypoints, profile=profile)
        if t is None or t.length_km > budget_km:
            continue

        points = gpx_utils.resample_polyline(t.coordinates, step_m=25.0)
        elevs = ign_altimetry.get_elevations(points)
        dplus = ign_altimetry.compute_dplus(elevs, smoothing_threshold_m=5.0)
        if dplus < target_dplus_m:
            # The boost didn't quite get there (BRouter routed via different paths than
            # the linear estimate assumed). Try the next climb anyway, but remember this
            # as a fallback if nothing better is found — actually keep it simple and
            # only return boosts that fully meet the target.
            log.debug(
                "  ↑ boost %s +%d reps yielded D+ %.0f m, still under target %d",
                (boost_climb.way_name or "?")[:25], k_extra, dplus, int(target_dplus_m),
            )
            continue

        log.info(
            "  ↑ boost via %s ×%d: %.1f→%.1f km, D+ %.0f→%.0f m",
            (boost_climb.way_name or "?")[:25], 1 + k_extra,
            base.track.length_km, t.length_km, base.dplus_m, dplus,
        )
        return ChainResult(
            track=t, climbs_used=list(base.climbs_used),
            dplus_m=dplus, elevations=elevs,
            reps_per_climb=reps_by_id,
        )
    return None


def find_chained_routes(
    start_lat: float, start_lon: float,
    climbs_all: list[seg_mod.Climb],
    dist_min_km: float, dist_max_km: float,
    dplus_min_m: float = 0.0,
    n_candidates: int = 5,
    max_combo_size: int = 8,
    top_segments_to_consider: int = 30,
    profile: str = "trail-hilly",
    max_brouter_calls: int = 250,
    auto_hill_repeats: bool = True,
) -> list[ChainResult]:
    """Generate candidate loops by chaining climbing segments.

    When `auto_hill_repeats` is True and `dplus_min_m > 0`, base loops that
    fall short of the D+ target get an automatic hill-repeats boost on their
    steepest climb. This unlocks ratios above the long-loop plateau (~22 m/km
    in Bordeaux viticole) by densifying climbs already in the chain.
    """
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
            if dist_min_km * 0.70 <= est <= dist_max_km * 1.25:
                est_dplus = sum(c.dplus_m for c in ordered)
                survivors.append((ordered, est_dplus))

    survivors.sort(key=lambda x: -x[1])
    survivors = survivors[:max_brouter_calls]
    log.info("Survived distance pre-filter: %d combos (capped to %d)", len(survivors), max_brouter_calls)

    # Route + IGN + (optional) boost per survivor.
    results: list[ChainResult] = []
    boost_threshold = dplus_min_m * 0.50  # don't boost routes way below target
    for i, (ordered, est_dplus) in enumerate(survivors):
        waypoints = _build_waypoints(start_lat, start_lon, ordered)
        t = brouter.multi_route(waypoints, profile=profile)
        if t is None:
            continue
        if not (dist_min_km * 0.85 <= t.length_km <= dist_max_km * 1.15):
            log.debug("  [%d] OOB %.1f km — skip", i + 1, t.length_km)
            continue

        points = gpx_utils.resample_polyline(t.coordinates, step_m=25.0)
        elevs = ign_altimetry.get_elevations(points)
        dplus = ign_altimetry.compute_dplus(elevs, smoothing_threshold_m=5.0)

        names = " → ".join((c.way_name or "?")[:18] for c in ordered)
        if dplus >= dplus_min_m:
            log.info("  [%d] %s: %.1f km · D+ %.0f m · KEEP", i + 1, names, t.length_km, dplus)
            results.append(ChainResult(track=t, climbs_used=ordered, dplus_m=dplus, elevations=elevs))
        elif auto_hill_repeats and dplus_min_m > 0 and dplus >= boost_threshold:
            log.info("  [%d] %s: %.1f km · D+ %.0f m · trying boost…", i + 1, names, t.length_km, dplus)
            base = ChainResult(track=t, climbs_used=ordered, dplus_m=dplus, elevations=elevs)
            boosted = _boost_with_repeats(base, start_lat, start_lon, dplus_min_m, dist_max_km, profile)
            if boosted is not None:
                results.append(boosted)
        else:
            log.debug("  [%d] %s: D+ %.0f below target — skip", i + 1, names, dplus)

    results.sort(key=lambda r: -r.dplus_m)

    # Dedupe by Jaccard overlap on raw track geometry. Threshold 0.40 keeps
    # variants that share the marquee climbs but differ in approach/return.
    kept: list[ChainResult] = []
    for r in results:
        if any(gpx_utils.jaccard_overlap(r.track.coordinates, k.track.coordinates) >= 0.40 for k in kept):
            continue
        kept.append(r)
        if len(kept) >= n_candidates:
            break
    return kept


def match_climb_by_name(name_pattern: str, climbs: list[seg_mod.Climb]) -> Optional[seg_mod.Climb]:
    """Substring match (case-insensitive); among ties, pick the highest D+."""
    pat = name_pattern.lower().strip()
    if not pat:
        return None
    matches = [c for c in climbs if pat in (c.way_name or "").lower()]
    if not matches:
        return None
    return max(matches, key=lambda c: c.dplus_m)


def find_manual_repeat_route(
    start_lat: float, start_lon: float,
    name_to_reps: dict,
    climbs_all: list[seg_mod.Climb],
    profile: str = "trail-hilly",
) -> Optional[ChainResult]:
    """Build a single route doing N ascents on each named climb.

    `name_to_reps` is {"<substring of way_name>": <total ascents (≥1)>}.
    The system geocodes each name to the best-matching climb in the index,
    orders them greedily by proximity from the start, and routes.
    """
    start_ll = (start_lon, start_lat)
    matched: list[tuple[seg_mod.Climb, int]] = []
    for name, reps in name_to_reps.items():
        if reps < 1:
            continue
        c = match_climb_by_name(name, climbs_all)
        if c is None:
            log.warning("No climb matching '%s' — skipping", name)
            continue
        matched.append((c, reps))
        log.info(
            "Matched '%s' → %s (D+%dm, %dm, %.1f%%) × %d ascents",
            name, c.way_name or "?", int(c.dplus_m), int(c.length_m), c.avg_slope_pct, reps,
        )

    if not matched:
        return None

    ordered = _greedy_order(start_ll, [c for c, _ in matched])
    reps_by_id = {c.id: r for c, r in matched}

    waypoints = _build_waypoints(start_lat, start_lon, ordered, reps_by_id)
    t = brouter.multi_route(waypoints, profile=profile)
    if t is None:
        return None

    points = gpx_utils.resample_polyline(t.coordinates, step_m=25.0)
    elevs = ign_altimetry.get_elevations(points)
    dplus = ign_altimetry.compute_dplus(elevs, smoothing_threshold_m=5.0)
    return ChainResult(
        track=t, climbs_used=ordered, dplus_m=dplus, elevations=elevs,
        reps_per_climb=reps_by_id,
    )
