"""Find likely-high POIs (peaks, viewpoints, châteaux on knolls, towers) via Overpass,
then enrich with IGN altimetry to get true elevation.

OSM tags that correlate with hilltops in France:
 - natural=peak                          (actual peaks, often missing in viticole areas)
 - tourism=viewpoint                     (almost always on a high point)
 - historic=castle / historic=tower      (châteaux were built on knolls — strong signal)
 - man_made=tower (telecom, water)       (built on local highpoints)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

import requests

import ign_altimetry


log = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "trail-route-finder/0.1 (https://github.com/nicolas-boisseau/trail-route-finder)"


@dataclass
class HilltopPOI:
    lat: float
    lon: float
    kind: str       # 'peak', 'viewpoint', 'castle', 'tower'
    name: str
    elevation_m: float  # filled in from IGN
    rel_height_m: float  # elevation above start point


_OVERPASS_QUERY = """
[out:json][timeout:25];
(
  node["natural"="peak"](around:{r},{lat},{lon});
  node["tourism"="viewpoint"](around:{r},{lat},{lon});
  node["historic"="castle"](around:{r},{lat},{lon});
  node["historic"="tower"](around:{r},{lat},{lon});
  node["man_made"="tower"](around:{r},{lat},{lon});
  way["historic"="castle"](around:{r},{lat},{lon});
);
out center;
"""


def query_high_pois(
    lat: float,
    lon: float,
    radius_m: int,
    min_rel_height_m: float = 15.0,
    keep_top: int = 12,
) -> list[HilltopPOI]:
    """Query Overpass, enrich with IGN elevation, keep top-K by relative height.

    Filters out POIs that aren't actually above the start point's elevation —
    e.g. a 'castle' tag on a riverside manor.
    """
    log.info("Overpass: searching POIs within %d m of (%.4f, %.4f)", radius_m, lat, lon)
    last_err = None
    for attempt in range(3):
        try:
            resp = requests.post(
                OVERPASS_URL,
                headers={"User-Agent": USER_AGENT},
                data={"data": _OVERPASS_QUERY.format(r=radius_m, lat=lat, lon=lon)},
                timeout=60,
            )
            if resp.status_code in (504, 429, 503):
                last_err = f"HTTP {resp.status_code}"
                log.warning("Overpass %s — retrying (%d/3)", resp.status_code, attempt + 1)
                time.sleep(2 + attempt * 3)
                continue
            resp.raise_for_status()
            break
        except requests.RequestException as e:
            last_err = str(e)
            log.warning("Overpass error %s — retrying (%d/3)", e, attempt + 1)
            time.sleep(2 + attempt * 3)
    else:
        raise RuntimeError(f"Overpass failed after 3 attempts: {last_err}")
    elements = resp.json().get("elements", [])
    log.info("  → %d raw OSM elements", len(elements))

    raw = []
    for e in elements:
        t = e.get("tags", {}) or {}
        name = t.get("name") or "?"
        kind = t.get("natural") or t.get("tourism") or t.get("historic") or t.get("man_made") or "?"
        if "lat" in e:
            p_lat, p_lon = e["lat"], e["lon"]
        else:
            c = e.get("center") or {}
            p_lat, p_lon = c.get("lat"), c.get("lon")
            if p_lat is None:
                continue
        raw.append((p_lat, p_lon, kind, name))

    if not raw:
        return []

    # IGN altimetry on start + all POIs
    coords = [(lon, lat)] + [(p[1], p[0]) for p in raw]
    elevs = ign_altimetry.get_elevations(coords)
    start_elev = elevs[0]
    log.info("  start elevation %.1f m", start_elev)

    pois: list[HilltopPOI] = []
    for (p_lat, p_lon, kind, name), e in zip(raw, elevs[1:]):
        if e != e:  # NaN
            continue
        rel = e - start_elev
        if rel < min_rel_height_m:
            continue
        pois.append(HilltopPOI(lat=p_lat, lon=p_lon, kind=kind, name=name, elevation_m=e, rel_height_m=rel))

    pois.sort(key=lambda p: -p.rel_height_m)
    log.info("  %d POIs above %+.0f m relative", len(pois), min_rel_height_m)
    return pois[:keep_top]
