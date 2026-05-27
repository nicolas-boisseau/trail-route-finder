"""IGN Géoplateforme elevation API client.

Endpoint: https://data.geopf.fr/altimetrie/1.0/calcul/alti/rest/elevation
Free, no auth. Rate-limited to ~5 req/s per IP — we throttle to be safe.
RGE ALTI 1m resource for mainland France.

The API accepts up to ~5000 points per request; we batch.
"""
from __future__ import annotations

import logging
import time
from typing import Iterable

import requests


ENDPOINT = "https://data.geopf.fr/altimetrie/1.0/calcul/alti/rest/elevation.json"
RESOURCE = "ign_rge_alti_wld"  # RGE ALTI worldwide composite (France: 1m grid)
MAX_POINTS_PER_REQ = 200  # keep URL short for GET; can go higher with POST
MIN_REQ_INTERVAL = 0.25  # ~4 req/s

log = logging.getLogger(__name__)


_last_call_ts: float = 0.0


def _throttle() -> None:
    global _last_call_ts
    elapsed = time.time() - _last_call_ts
    if elapsed < MIN_REQ_INTERVAL:
        time.sleep(MIN_REQ_INTERVAL - elapsed)
    _last_call_ts = time.time()


def get_elevations(points: list[tuple[float, float]]) -> list[float]:
    """Return elevation (meters) for each (lon, lat) point, in order.

    Batches requests to stay under API limits. Returns NaN for any
    point the API couldn't resolve (very rare in mainland France).
    """
    out: list[float] = []
    for i in range(0, len(points), MAX_POINTS_PER_REQ):
        batch = points[i : i + MAX_POINTS_PER_REQ]
        lons = "|".join(f"{lon:.6f}" for lon, _ in batch)
        lats = "|".join(f"{lat:.6f}" for _, lat in batch)
        _throttle()
        resp = requests.get(
            ENDPOINT,
            params={
                "lon": lons,
                "lat": lats,
                "resource": RESOURCE,
                "delimiter": "|",
                "indent": "false",
                "measures": "false",
                "zonly": "true",
            },
            timeout=30,
        )
        if resp.status_code != 200:
            log.warning("IGN altimetry %s: %s", resp.status_code, resp.text[:200])
            out.extend([float("nan")] * len(batch))
            continue
        try:
            data = resp.json()
        except ValueError:
            log.warning("IGN altimetry non-JSON: %s", resp.text[:200])
            out.extend([float("nan")] * len(batch))
            continue
        # zonly=true returns {"elevations": [z1, z2, ...]}
        elevs = data.get("elevations") or []
        if len(elevs) != len(batch):
            log.warning("IGN returned %d elevations for %d points", len(elevs), len(batch))
        out.extend(float(e) if e is not None else float("nan") for e in elevs)
    return out


def compute_dplus(elevations: Iterable[float], smoothing_threshold_m: float = 5.0) -> float:
    """Compute filtered cumulative ascent (D+) with Garmin-style smoothing."""
    elevs = [e for e in elevations if e == e]  # drop NaN
    return _filtered_ascent(elevs, smoothing_threshold_m)


def _filtered_ascent(elevs: list[float], threshold: float) -> float:
    """Filtered cumulative ascent — collapses small oscillations.

    Implemented as: find local extrema (peaks/valleys) whose successive
    difference >= threshold, sum positive differences between them.
    """
    if len(elevs) < 2:
        return 0.0
    extrema: list[float] = [elevs[0]]
    cur = elevs[0]
    direction = 0
    for e in elevs[1:]:
        if direction == 0:
            if e > cur:
                direction = 1
            elif e < cur:
                direction = -1
            cur = e
            continue
        if direction == 1:
            if e > cur:
                cur = e
            elif cur - e >= threshold:
                extrema.append(cur)
                cur = e
                direction = -1
        else:  # direction == -1
            if e < cur:
                cur = e
            elif e - cur >= threshold:
                extrema.append(cur)
                cur = e
                direction = 1
    extrema.append(cur)
    return sum(max(0.0, b - a) for a, b in zip(extrema, extrema[1:]))


if __name__ == "__main__":
    # quick smoke test on Mont Ventoux summit (~1909 m)
    e = get_elevations([(5.27833, 44.17389)])
    print("Mont Ventoux elevation:", e)
