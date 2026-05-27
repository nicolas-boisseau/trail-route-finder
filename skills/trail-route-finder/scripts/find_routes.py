"""Find trail-running loop candidates around a starting point with a D+ target.

Strategy:
 1. geocode the address (BAN)
 2. sweep (direction × roundtrip-radius) → call BRouter round-trip per combo
 3. coarse filter on BRouter's own SRTM-based D+ estimate
 4. recompute precise D+ via IGN RGE ALTI 1m on survivors
 5. dedupe geometrically, keep top N by D+
 6. write GPX + index.html

Supports --address (single start) or --preset <name> (zone with multiple starts,
auto-fallback to stretch starts if the local zone can't hit the target).
"""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import _paths
import brouter
import gpx_utils
import hilltop_mode
import ign_altimetry
import presets as presets_mod
import visualize
from geocode import Location, geocode


log = logging.getLogger("find_routes")

DIRECTIONS_DEG = list(range(0, 360, 30))  # 12 directions
N_RADII = 3
# Empirical: BRouter loop perimeter ≈ 7 × roundTripDistance, so radius ≈ 0.14 × target.
RADIUS_FACTOR = 0.14


def sweep_candidates(lat, lon, dist_min_km, dist_max_km, profile):
    step = (dist_max_km - dist_min_km) / (N_RADII - 1) if N_RADII > 1 else 0
    radii_km = [dist_min_km + i * step for i in range(N_RADII)] if N_RADII > 1 else [(dist_min_km + dist_max_km) / 2]

    out = []
    total = len(DIRECTIONS_DEG) * len(radii_km)
    done = 0
    for radius_km in radii_km:
        radius_m = int(radius_km * 1000 * RADIUS_FACTOR)
        for direction in DIRECTIONS_DEG:
            done += 1
            t = brouter.round_trip(lat, lon, direction, radius_m, profile=profile)
            if t is None:
                log.info("[%d/%d] dir=%3d radius=%dm → no route", done, total, direction, radius_m)
                continue
            log.info("[%d/%d] dir=%3d radius=%dm → %.1f km, D+ ~%dm (SRTM)",
                     done, total, direction, radius_m, t.length_km, t.ascend_filtered_m)
            out.append(t)
    return out


def precise_dplus(track, sample_step_m=25.0):
    points = gpx_utils.resample_polyline(track.coordinates, step_m=sample_step_m)
    elevs = ign_altimetry.get_elevations(points)
    return ign_altimetry.compute_dplus(elevs, smoothing_threshold_m=5.0), elevs


def dedupe(candidates, threshold=0.55):
    kept = []
    for cand in candidates:
        if any(gpx_utils.jaccard_overlap(cand[0].coordinates, k[0].coordinates) >= threshold for k in kept):
            continue
        kept.append(cand)
    return kept


@dataclass
class FindResult:
    loc: Location
    enriched: list  # list of (BRouterTrack, dplus, elevations)
    stats: dict
    best_dplus: float  # max D+ found (even if below target)


def find_from_location(loc, dplus_min, dist_min, dist_max, n_candidates, profile, mode="roundtrip"):
    """Run the full sweep+filter pipeline for a single start. Returns FindResult.

    `enriched` is sorted desc by D+ and deduped, capped at n_candidates.
    `best_dplus` reflects the top D+ we *saw* (even below target) for diagnostics.

    mode='roundtrip' uses BRouter's auto round-trip (good when terrain is uniformly hilly).
    mode='hilltop'   forces routes through OSM hilltop POIs (essential in low-relief areas
                     where round-trip places waypoints in valleys and misses the climbs).
    """
    target_km = (dist_min + dist_max) / 2
    if mode == "hilltop":
        raw = hilltop_mode.find_hilltop_loops(loc.lat, loc.lon, target_km, profile=profile)
    else:
        raw = sweep_candidates(loc.lat, loc.lon, dist_min, dist_max, profile)
    in_range = [t for t in raw if dist_min * 0.85 <= t.length_km <= dist_max * 1.15]
    log.info("In distance range: %d / %d", len(in_range), len(raw))

    coarse_threshold = dplus_min * 0.70
    coarse = [t for t in in_range if t.ascend_filtered_m >= coarse_threshold]
    log.info("Above coarse D+ threshold (%.0f m): %d", coarse_threshold, len(coarse))
    # Always include the top-3 by SRTM regardless of threshold so we can report
    # a meaningful best_dplus diagnostic even when the local terrain falls short.
    by_srtm = sorted(in_range, key=lambda t: t.ascend_filtered_m, reverse=True)
    for t in by_srtm[:3]:
        if t not in coarse:
            coarse.append(t)
    coarse.sort(key=lambda t: t.ascend_filtered_m, reverse=True)
    coarse = coarse[: max(n_candidates * 3, 10)]

    enriched = []
    best_dplus = 0.0
    for i, t in enumerate(coarse):
        log.info("[IGN %d/%d] %.1f km, computing precise D+...", i + 1, len(coarse), t.length_km)
        try:
            dplus, elevs = precise_dplus(t)
        except Exception as e:
            log.warning("IGN failed: %s", e)
            continue
        log.info("  → D+ %d m (was %d m via SRTM)", dplus, t.ascend_filtered_m)
        best_dplus = max(best_dplus, dplus)
        if dplus >= dplus_min:
            enriched.append((t, dplus, elevs))

    enriched.sort(key=lambda x: x[1], reverse=True)
    kept = dedupe(enriched)[:n_candidates]

    return FindResult(
        loc=loc,
        enriched=kept,
        stats={"raw": len(raw), "in_range": len(in_range), "coarse": len(coarse), "matched": len(enriched)},
        best_dplus=best_dplus,
    )


def write_outputs(result: FindResult, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    candidates = []
    for i, (t, dplus, elevs) in enumerate(result.enriched):
        gpx_name = f"route_{i + 1}.gpx"
        gpx_utils.write_gpx(
            t.coordinates, out_dir / gpx_name,
            name=f"Trail D+{int(dplus)}m {t.length_km:.1f}km",
            description=f"Generated by trail-route-finder from {result.loc.label}",
        )
        candidates.append(visualize.Candidate(
            idx=i, coords=t.coordinates, length_km=t.length_km,
            dplus_m=dplus, elevations=elevs, gpx_filename=gpx_name,
        ))
    return visualize.build_html(result.loc.lat, result.loc.lon, result.loc.label, candidates, out_dir / "index.html")


def print_summary(result: FindResult, html_path: Path):
    print(f"\n=== {result.loc.label} ===")
    if not result.enriched:
        print(f"  No candidate met the D+ target (best found: {result.best_dplus:.0f} m)")
        return
    for i, (t, dplus, _) in enumerate(result.enriched):
        print(f"  #{i + 1}: {t.length_km:.1f} km · D+ {dplus:.0f} m · {dplus / t.length_km:.0f} m/km → route_{i + 1}.gpx")
    print(f"  HTML: xdg-open {html_path}")


def run_preset(preset_name, dplus_min, dist_min, dist_max, n_candidates, profile, skip_stretch, mode):
    p = presets_mod.load(preset_name)
    print(f"Preset: {p.label}  (mode={mode})")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = _paths.output_dir() / f"preset_{p.name}_{ts}"
    base.mkdir(parents=True, exist_ok=True)

    def _safe(name):
        return "".join(c if c.isalnum() else "_" for c in name)

    all_results: list[FindResult] = []
    total_matched = 0

    for s in p.starts:
        print(f"\n→ Trying {s.label}")
        loc = geocode(s.address)
        r = find_from_location(loc, dplus_min, dist_min, dist_max, n_candidates, profile, mode)
        all_results.append(r)
        total_matched += len(r.enriched)
        html = write_outputs(r, base / _safe(s.address))
        print_summary(r, html)

    if total_matched == 0 and not skip_stretch and p.stretch_starts:
        print("\nNo local match — trying stretch zones (further drive):")
        for s in p.stretch_starts:
            print(f"\n→ Trying {s.label}")
            loc = geocode(s.address)
            r = find_from_location(loc, dplus_min, dist_min, dist_max, n_candidates, profile, mode)
            all_results.append(r)
            total_matched += len(r.enriched)
            html = write_outputs(r, base / _safe(s.address))
            print_summary(r, html)

    print(f"\n{'=' * 50}\nAggregate: {total_matched} matching candidate(s) across {len(all_results)} start(s)")
    print(f"Reports under: {base}/")
    if total_matched == 0:
        all_best = max((r.best_dplus for r in all_results), default=0)
        print(f"Best D+ seen anywhere: {all_best:.0f} m — try lowering --dplus-min or widening --dist-max.")


def main():
    ap = argparse.ArgumentParser()
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--address", help="Free-form address or 'lat,lon'")
    group.add_argument("--preset", help=f"Named preset zone. Available: {', '.join(presets_mod.list_available()) or '(none)'}")
    ap.add_argument("--dplus-min", type=float, required=True, help="Minimum D+ in meters")
    ap.add_argument("--dist-min", type=float, required=True, help="Minimum loop distance in km")
    ap.add_argument("--dist-max", type=float, required=True, help="Maximum loop distance in km")
    ap.add_argument("--n-candidates", type=int, default=5)
    ap.add_argument("--profile", default="trail-hilly")
    ap.add_argument(
        "--mode", choices=["roundtrip", "hilltop"], default="roundtrip",
        help="roundtrip = BRouter auto-loop (default). hilltop = force routes through "
             "OSM hilltop POIs — much better in low-relief areas (Gironde, Charente)."
    )
    ap.add_argument("--no-stretch", action="store_true", help="Don't auto-try stretch starts when preset locals fail")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s")

    if not brouter.ping():
        print("BRouter not reachable on http://localhost:17777", file=sys.stderr)
        print("Run setup.sh once, then start: cd <skill>/docker && docker compose --env-file <data>/.env up -d", file=sys.stderr)
        sys.exit(2)

    if args.preset:
        run_preset(args.preset, args.dplus_min, args.dist_min, args.dist_max,
                   args.n_candidates, args.profile, args.no_stretch, args.mode)
        return

    loc = geocode(args.address)
    r = find_from_location(loc, args.dplus_min, args.dist_min, args.dist_max, args.n_candidates, args.profile, args.mode)

    if not r.enriched:
        print(f"No candidate met D+ ≥ {args.dplus_min:.0f} m (best: {r.best_dplus:.0f} m).", file=sys.stderr)
        print("Try lowering --dplus-min, widening --dist-max, or use --preset to try nearby zones.", file=sys.stderr)
        sys.exit(4)

    out_dir = Path(args.out_dir) if args.out_dir else _paths.output_dir() / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    html = write_outputs(r, out_dir)
    print_summary(r, html)
    print(f"\nPush to Garmin: python3 {Path(__file__).parent}/push_to_garmin.py {out_dir}/route_<N>.gpx")


if __name__ == "__main__":
    main()
