"""Client for a self-hosted BRouter instance (port 17777 by default).

Uses the round-trip feature (PR #759) to generate loop candidates.
Round-trip params: direction (deg), roundtripDistance (m), allowSamewayback.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import requests


BROUTER_URL = "http://localhost:17777/brouter"

log = logging.getLogger(__name__)


@dataclass
class BRouterTrack:
    coordinates: list[tuple[float, float, float]]  # (lon, lat, ele)
    length_m: float
    ascend_filtered_m: float
    ascend_plain_m: float
    total_time_s: float
    direction_deg: float
    roundtrip_radius_m: int
    raw: dict

    @property
    def length_km(self) -> float:
        return self.length_m / 1000.0


def round_trip(
    lat: float,
    lon: float,
    direction_deg: float,
    roundtrip_radius_m: int,
    profile: str = "trail-hilly",
    base_url: str = BROUTER_URL,
    timeout: int = 30,
) -> Optional[BRouterTrack]:
    """Request a single round-trip loop from BRouter.

    Returns None if BRouter could not build a loop in that direction
    (no path network, dead-end peninsula, etc.).
    """
    params = {
        "lonlats": f"{lon:.6f},{lat:.6f}",
        "profile": profile,
        "alternativeidx": "0",
        "format": "geojson",
        "engineMode": "4",  # BROUTER_ENGINEMODE_ROUNDTRIP
        "direction": int(direction_deg) % 360,
        "roundTripDistance": int(roundtrip_radius_m),
        "roundTripPoints": 5,
        "allowSamewayback": "0",
        "profile:correctMisplacedViaPoints": "1",
    }
    try:
        resp = requests.get(base_url, params=params, timeout=timeout)
    except requests.RequestException as e:
        log.warning("BRouter request failed dir=%s radius=%s: %s", direction_deg, roundtrip_radius_m, e)
        return None

    if resp.status_code != 200:
        log.warning("BRouter %s dir=%s radius=%s: %s", resp.status_code, direction_deg, roundtrip_radius_m, resp.text[:200])
        return None

    try:
        data = resp.json()
    except ValueError:
        log.warning("BRouter non-JSON response (%s): %s", resp.status_code, resp.text[:200])
        return None

    feats = data.get("features") or []
    if not feats:
        return None
    feat = feats[0]
    geom = feat.get("geometry") or {}
    coords = geom.get("coordinates") or []
    if len(coords) < 10:
        return None
    props = feat.get("properties") or {}
    return BRouterTrack(
        coordinates=[(float(c[0]), float(c[1]), float(c[2]) if len(c) > 2 else 0.0) for c in coords],
        length_m=float(props.get("track-length", 0)),
        ascend_filtered_m=float(props.get("filtered ascend", 0)),
        ascend_plain_m=float(props.get("plain-ascend", 0)),
        total_time_s=float(props.get("total-time", 0)),
        direction_deg=float(direction_deg),
        roundtrip_radius_m=int(roundtrip_radius_m),
        raw=data,
    )


def ping(base_url: str = BROUTER_URL, timeout: int = 5) -> bool:
    """Quick check that the BRouter server is responsive (any HTTP response counts)."""
    try:
        resp = requests.get(base_url, timeout=timeout)
        return resp.status_code < 500
    except requests.RequestException:
        return False
