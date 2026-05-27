"""CLI: discover and visualize climbing segments around an address.

Output: an HTML map of every detected climb, colored by slope, plus a cache
file so subsequent runs on the same zone are instant.
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path

import _paths
import segments as seg
import visualize_segments
from geocode import geocode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--address", required=True, help="Free-form address or 'lat,lon'")
    ap.add_argument("--radius-km", type=float, default=8.0, help="Search radius in km")
    ap.add_argument("--force-refresh", action="store_true", help="Ignore cache, re-query Overpass + IGN")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s")

    loc = geocode(args.address)
    print(f"Zone: {loc.label} ({loc.lat:.4f}, {loc.lon:.4f}) — radius {args.radius_km} km")

    climbs = seg.discover_zone(loc.lat, loc.lon, int(args.radius_km * 1000), force_refresh=args.force_refresh)

    if not climbs:
        print("No climbing segments detected. Try a larger radius or different start.")
        return

    print(f"\n{len(climbs)} climbing segments found.\n")
    print("Top 15 by D+:")
    print(f"  {'D+':>5}  {'len':>5}  {'avg':>5}  {'max':>5}  {'dir':>3}  name")
    for c in climbs[:15]:
        print(f"  {c.dplus_m:5.0f}  {c.length_m:5.0f}  {c.avg_slope_pct:4.1f}%  {c.max_slope_pct:4.1f}%  {c.direction:>3}  {c.way_name[:50]}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = _paths.output_dir() / f"segments_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)
    html = visualize_segments.build_segments_html(loc.lat, loc.lon, loc.label, climbs, out_dir / "index.html")
    print(f"\nMap: xdg-open {html}")


if __name__ == "__main__":
    main()
