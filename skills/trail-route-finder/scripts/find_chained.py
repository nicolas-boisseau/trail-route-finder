"""CLI: build loops that chain together pre-discovered climbing segments.

Two modes:
  Auto chain — default. Tries combos of nearby climbs, hits a distance/D+ target.
                Falls back to hill-repeats on the steepest in-chain climb when a
                base loop comes close but doesn't reach the D+ target.
  Manual repeats — pass `--repeat-segments "<name>:N,<name>:N"` to skip the combo
                search and just route N ascents on each named climb.

Workflow:
  1. Ensure segment cache exists for the zone (or run find_segments.py first).
  2. Pick the right mode based on flags.
  3. Route via BRouter, recompute D+ with IGN, dedupe, write GPX + HTML.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

import _paths
import brouter
import chain_segments
import gpx_utils
import segments as seg_mod
import visualize
from geocode import geocode


def _parse_repeat_spec(spec: str) -> dict:
    """Parse '<name>:N,<name>:N' into {name: N}. Names may contain spaces but
    not commas. Reps must be int ≥ 1. Whitespace-trimmed."""
    out: dict = {}
    for piece in spec.split(","):
        piece = piece.strip()
        if not piece or ":" not in piece:
            continue
        name, n = piece.rsplit(":", 1)
        try:
            n_int = int(n.strip())
        except ValueError:
            print(f"Bad reps count in '{piece}' — skipping", file=sys.stderr)
            continue
        if n_int < 1:
            continue
        out[name.strip()] = n_int
    return out


def _format_climbs(r: chain_segments.ChainResult) -> str:
    parts = []
    for c in r.climbs_used:
        n = r.reps_for(c)
        name = (c.way_name or "?")[:20]
        parts.append(f"{name} ×{n}" if n > 1 else name)
    return " → ".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--address", required=True)
    ap.add_argument("--dist-min", type=float, default=0.0,
                    help="Minimum loop distance in km. Ignored in --repeat-segments mode.")
    ap.add_argument("--dist-max", type=float, required=True,
                    help="Maximum loop distance in km (also the budget for auto hill-repeats).")
    ap.add_argument("--dplus-min", type=float, default=0.0,
                    help="Target minimum D+ in m. Triggers auto hill-repeats when set.")
    ap.add_argument("--zone-address", help="Address used for the segment cache (default: --address)")
    ap.add_argument("--zone-radius-km", type=float, default=8.0)
    ap.add_argument("--n-candidates", type=int, default=5)
    ap.add_argument("--profile", default="trail-hilly")
    ap.add_argument(
        "--no-hill-repeats", action="store_true",
        help="Disable the automatic hill-repeats boost when a base loop falls short of --dplus-min.",
    )
    ap.add_argument(
        "--repeat-segments", type=str, default=None,
        help='Manual mode: comma-separated "<climb-name>:N" pairs (e.g. '
             '"Route de l\'Église:3,Chemin de Larsis:2"). Skips the combo search '
             'and builds a single route doing N ascents on each named climb.',
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s")

    if not brouter.ping():
        print("BRouter unreachable on http://localhost:17777", flush=True)
        return 2

    loc = geocode(args.address)
    print(f"Start: {loc.label} ({loc.lat:.4f}, {loc.lon:.4f})")

    zone = geocode(args.zone_address) if args.zone_address else loc
    climbs = seg_mod.discover_zone(zone.lat, zone.lon, int(args.zone_radius_km * 1000))
    print(f"Zone: {zone.label} — {len(climbs)} segments in index")

    if args.repeat_segments:
        name_to_reps = _parse_repeat_spec(args.repeat_segments)
        if not name_to_reps:
            print("No valid '<name>:N' pairs parsed from --repeat-segments.", file=sys.stderr)
            return 5
        print(f"Manual mode: {len(name_to_reps)} climb(s) requested")
        r = chain_segments.find_manual_repeat_route(
            loc.lat, loc.lon, name_to_reps, climbs, args.profile,
        )
        if r is None:
            print("No route could be built — none of the requested climbs were found in the index.",
                  file=sys.stderr)
            return 4
        results = [r]
    else:
        results = chain_segments.find_chained_routes(
            loc.lat, loc.lon, climbs,
            dist_min_km=args.dist_min, dist_max_km=args.dist_max,
            dplus_min_m=args.dplus_min, n_candidates=args.n_candidates,
            profile=args.profile,
            auto_hill_repeats=not args.no_hill_repeats,
        )

    if not results:
        msg = (f"No chained loop met D+ ≥ {args.dplus_min:.0f} m on "
               f"{args.dist_min}-{args.dist_max} km.")
        if args.dplus_min > 0 and not args.no_hill_repeats:
            msg += " (auto hill-repeats was ON — terrain may be too flat for the gap.)"
        print(msg)
        return 4

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = "manual" if args.repeat_segments else "chained"
    out_dir = _paths.output_dir() / f"{tag}_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    print()
    candidates = []
    for i, r in enumerate(results):
        gpx_name = f"route_{i + 1}.gpx"
        via = _format_climbs(r)
        gpx_utils.write_gpx(
            r.track.coordinates, out_dir / gpx_name,
            name=f"Chain D+{int(r.dplus_m)}m {r.track.length_km:.1f}km",
            description=f"Via: {via}",
        )
        candidates.append(visualize.Candidate(
            idx=i, coords=r.track.coordinates,
            length_km=r.track.length_km, dplus_m=r.dplus_m,
            elevations=r.elevations, gpx_filename=gpx_name,
        ))
        boosted = any(n > 1 for n in r.reps_per_climb.values())
        flag = " 🔁" if boosted else ""
        print(f"  #{i + 1}: {r.track.length_km:.1f} km · D+ {r.dplus_m:.0f} m · "
              f"{r.dplus_m / r.track.length_km:.0f} m/km{flag}")
        print(f"        via: {via}")

    html = visualize.build_html(loc.lat, loc.lon, loc.label, candidates, out_dir / "index.html")
    print(f"\nMap: xdg-open {html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
