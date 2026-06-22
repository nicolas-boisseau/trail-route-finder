"""Geocoding via the French Base Adresse Nationale (BAN).

Free, no key, no rate limit for reasonable use.
Endpoint: https://api-adresse.data.gouv.fr/search/?q=<query>
"""
from __future__ import annotations

import re
import time
from typing import NamedTuple

import requests


class Location(NamedTuple):
    lat: float
    lon: float
    label: str


_COORD_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


def geocode(query: str) -> Location:
    """Resolve a free-form address or 'lat,lon' string to a Location."""
    m = _COORD_RE.match(query)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        return Location(lat=lat, lon=lon, label=f"{lat:.5f},{lon:.5f}")

    last_err = None
    resp = None
    for attempt in range(3):
        try:
            resp = requests.get(
                "https://api-adresse.data.gouv.fr/search/",
                params={"q": query, "limit": 1},
                timeout=15,
            )
            if resp.status_code in (429, 502, 503, 504):
                last_err = f"HTTP {resp.status_code}"
                time.sleep(1.5 + attempt * 2)
                continue
            resp.raise_for_status()
            break
        except requests.RequestException as e:
            last_err = str(e)
            time.sleep(1.5 + attempt * 2)
            resp = None
    if resp is None or resp.status_code != 200:
        raise RuntimeError(f"BAN geocoding failed for {query!r}: {last_err}")
    data = resp.json()
    features = data.get("features") or []
    if not features:
        raise ValueError(f"No geocoding result for: {query!r}")
    feat = features[0]
    lon, lat = feat["geometry"]["coordinates"]
    label = feat["properties"].get("label", query)
    return Location(lat=float(lat), lon=float(lon), label=label)


if __name__ == "__main__":
    import sys
    print(geocode(" ".join(sys.argv[1:])))
