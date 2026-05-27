"""Find trail-running loop candidates around a starting point with a D+ target.

Strategy:
 1. geocode the address (BAN)
 2. sweep (direction × roundtrip-radius) → call BRouter round-trip per combo
 3. coarse filter on BRouter's own SRTM-based D+ estimate
 4. recompute precise D+ via IGN RGE ALTI 1m on survivors
 5. dedupe geometrically, keep top N by D+
 6. write GPX + index.html
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

import _paths
import brouter
import gpx_utils
import ign_altimetry
import visualize
from geocode import geocode


log = logging.getLogger("find_routes")

# directions sampled around the start point
DIRECTIONS_DEG = list(range(0, 360, 30))  # 12 directions

# Empirical: BRouter loop perimeter ≈ 7 × roundTripDistance, so radius ≈ 0.14 × target.
# (Direction matters: same radius gives ±50% loop length depending on path network.)
RADIUS_FACTOR = 0.14

# Sample three radii within the user-supplied distance range.
N_RADII = 3


def sweep_candidates(
    lat: float,
    lon: float,
    dist_min_km: float,
    dist_max_km: float,
    profile: str,
) -> list[brouter.BRouterTrack]:
    """Call BRouter across the (direction × radius) grid."""
    if N_RADII == 1:
        radii_km = [(dist_min_km + dist_max_km) / 2]
    else:
        step = (dist_max_km - dist_min_km) / (N_RADII - 1)
        radii_km = [dist_min_km + i * step for i in range(N_RADII)]

    out: list[brouter.BRouterTrack] = []
    total = len(DIRECTIONS_DEG) * len(radii_km)
    done = 0
    for radius_km in radii_km:
        radius_m = int(radius_km * 1000 * RADIUS_FACTOR)
        for direction in DIRECTIONS_DEG:
            done += 1
            track = brouter.round_trip(lat, lon, direction, radius_m, profile=profile)
            if track is None:
                log.info("[%d/%d] dir=%3d radius=%dm → no route", done, total, direction, radius_m)
                continue
            log.info(
                "[%d/%d] dir=%3d radius=%dm → %.1f km, D+ ~%dm (SRTM)",
                done, total, direction, radius_m, track.length_km, track.ascend_filtered_m,
            )
            out.append(track)
    return out


def precise_dplus(track: brouter.BRouterTrack, sample_step_m: float = 25.0) -> tuple[float, list[float]]:
    """Resample the polyline at ~25m and query IGN altimetry for accurate D+."""
    points = gpx_utils.resample_polyline(track.coordinates, step_m=sample_step_m)
    elevs = ign_altimetry.get_elevations(points)
    dplus = ign_altimetry.compute_dplus(elevs, smoothing_threshold_m=5.0)
    return dplus, elevs


def dedupe(candidates: list[tuple[brouter.BRouterTrack, float, list[float]]], threshold: float = 0.55):
    """Keep only geometrically distinct candidates (Jaccard < threshold against kept set)."""
    # candidates are pre-sorted; iterate and keep if dissimilar enough
    kept: list[tuple[brouter.BRouterTrack, float, list[float]]] = []
    for cand in candidates:
        track = cand[0]
        if any(gpx_utils.jaccard_overlap(track.coordinates, k[0].coordinates) >= threshold for k in kept):
            continue
        kept.append(cand)
    return kept


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--address", required=True, help="Free-form address or 'lat,lon'")
    ap.add_argument("--dplus-min", type=float, required=True, help="Minimum D+ in meters")
    ap.add_argument("--dist-min", type=float, required=True, help="Minimum loop distance in km")
    ap.add_argument("--dist-max", type=float, required=True, help="Maximum loop distance in km")
    ap.add_argument("--n-candidates", type=int, default=5, help="How many distinct candidates to return")
    ap.add_argument("--profile", default="trail-hilly")
    ap.add_argument("--out-dir", default=None, help="Output directory (default: ../output/run_<timestamp>)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(message)s",
    )

    if not brouter.ping():
        print("BRouter not reachable on http://localhost:17777", file=sys.stderr)
        print("Run setup.sh once, then start: cd <skill>/docker && docker compose up -d", file=sys.stderr)
        sys.exit(2)

    loc = geocode(args.address)
    log.info("Start: %.5f, %.5f — %s", loc.lat, loc.lon, loc.label)

    raw = sweep_candidates(loc.lat, loc.lon, args.dist_min, args.dist_max, args.profile)
    if not raw:
        print("BRouter returned no loop in any direction. Try a wider distance range or different start.", file=sys.stderr)
        sys.exit(3)

    # 1) keep only those within distance range (BRouter sometimes overshoots)
    in_range = [
        t for t in raw
        if args.dist_min * 0.85 <= t.length_km <= args.dist_max * 1.15
    ]
    log.info("In distance range: %d / %d", len(in_range), len(raw))

    # 2) coarse filter on BRouter SRTM D+ — keep candidates that BRouter thinks have at least 70% of target
    coarse_threshold = args.dplus_min * 0.70
    coarse = [t for t in in_range if t.ascend_filtered_m >= coarse_threshold]
    coarse.sort(key=lambda t: t.ascend_filtered_m, reverse=True)
    log.info("Above coarse D+ threshold (%.0f m): %d", coarse_threshold, len(coarse))

    # cap the IGN-precise pass — we want top ~3x candidates to dedupe from
    coarse = coarse[: max(args.n_candidates * 3, 10)]

    # 3) precise D+ via IGN
    enriched: list[tuple[brouter.BRouterTrack, float, list[float]]] = []
    for i, t in enumerate(coarse):
        log.info("[IGN %d/%d] %.1f km, computing precise D+...", i + 1, len(coarse), t.length_km)
        try:
            dplus, elevs = precise_dplus(t)
        except Exception as e:
            log.warning("IGN failed: %s", e)
            continue
        log.info("  → D+ %d m (was %d m via SRTM)", dplus, t.ascend_filtered_m)
        if dplus >= args.dplus_min:
            enriched.append((t, dplus, elevs))

    if not enriched:
        print(f"No candidate met D+ ≥ {args.dplus_min:.0f} m. Try a higher distance range.", file=sys.stderr)
        sys.exit(4)

    # 4) sort by precise D+ desc and dedupe
    enriched.sort(key=lambda x: x[1], reverse=True)
    kept = dedupe(enriched)[: args.n_candidates]
    log.info("Final candidates after dedupe: %d", len(kept))

    # 5) outputs
    if args.out_dir:
        out_dir = Path(args.out_dir)
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = _paths.output_dir() / f"run_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    candidates = []
    for i, (t, dplus, elevs) in enumerate(kept):
        gpx_name = f"route_{i + 1}.gpx"
        gpx_path = out_dir / gpx_name
        gpx_utils.write_gpx(
            t.coordinates, gpx_path,
            name=f"Trail D+{int(dplus)}m {t.length_km:.1f}km",
            description=f"Generated by trail-route-finder from {loc.label}",
        )
        candidates.append(visualize.Candidate(
            idx=i,
            coords=t.coordinates,
            length_km=t.length_km,
            dplus_m=dplus,
            elevations=elevs,
            gpx_filename=gpx_name,
        ))

    html_path = visualize.build_html(loc.lat, loc.lon, loc.label, candidates, out_dir / "index.html")

    print(f"\nOK — {len(candidates)} candidates written to {out_dir}/")
    for c in candidates:
        print(f"  #{c.idx + 1}: {c.length_km:.1f} km · D+ {c.dplus_m:.0f} m · {c.dplus_m / c.length_km:.0f} m/km → {c.gpx_filename}")
    print(f"\nReview: xdg-open {html_path}")
    print(f"Push to Garmin: python3 {Path(__file__).parent}/push_to_garmin.py {out_dir}/route_<N>.gpx")


if __name__ == "__main__":
    main()
