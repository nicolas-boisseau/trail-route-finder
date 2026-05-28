"""CLI: build loops that chain together pre-discovered climbing segments.

Workflow:
  1. Ensure segment cache exists for the zone (or run find_segments.py first).
  2. Pick segments near the start point, try combinations, route via BRouter.
  3. Filter by distance + D+, dedupe, write GPX + HTML.
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path

import _paths
import brouter
import chain_segments
import gpx_utils
import segments as seg_mod
import visualize
from geocode import geocode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--address", required=True)
    ap.add_argument("--dist-min", type=float, required=True)
    ap.add_argument("--dist-max", type=float, required=True)
    ap.add_argument("--dplus-min", type=float, default=0.0)
    ap.add_argument("--zone-address", help="Address used for the segment cache (default: --address)")
    ap.add_argument("--zone-radius-km", type=float, default=8.0)
    ap.add_argument("--n-candidates", type=int, default=5)
    ap.add_argument("--profile", default="trail-hilly")
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

    results = chain_segments.find_chained_routes(
        loc.lat, loc.lon, climbs,
        dist_min_km=args.dist_min, dist_max_km=args.dist_max,
        dplus_min_m=args.dplus_min, n_candidates=args.n_candidates,
        profile=args.profile,
    )

    if not results:
        print(f"No chained loop met D+ ≥ {args.dplus_min:.0f} m on {args.dist_min}-{args.dist_max} km.")
        return 4

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = _paths.output_dir() / f"chained_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    print()
    candidates = []
    for i, r in enumerate(results):
        gpx_name = f"route_{i + 1}.gpx"
        names = " → ".join((c.way_name or "?")[:20] for c in r.climbs_used)
        gpx_utils.write_gpx(
            r.track.coordinates, out_dir / gpx_name,
            name=f"Chain D+{int(r.dplus_m)}m {r.track.length_km:.1f}km",
            description=f"Via: {names}",
        )
        candidates.append(visualize.Candidate(
            idx=i, coords=r.track.coordinates,
            length_km=r.track.length_km, dplus_m=r.dplus_m,
            elevations=r.elevations, gpx_filename=gpx_name,
        ))
        print(f"  #{i + 1}: {r.track.length_km:.1f} km · D+ {r.dplus_m:.0f} m · {r.dplus_m / r.track.length_km:.0f} m/km")
        print(f"        via: {names}")

    html = visualize.build_html(loc.lat, loc.lon, loc.label, candidates, out_dir / "index.html")
    print(f"\nMap: xdg-open {html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
