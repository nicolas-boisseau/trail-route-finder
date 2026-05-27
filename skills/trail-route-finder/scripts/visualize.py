"""Render candidate loops as an interactive HTML map with IGN tiles.

Output: a single self-contained index.html with:
 - IGN Plan + Scan25 layers (toggleable)
 - One color per candidate
 - Popup with km, D+, D+/km
 - Embedded elevation profile per candidate (small SVG)
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import folium


# IGN Géoplateforme WMTS — no key needed
_IGN_TILE_BASE = (
    "https://data.geopf.fr/wmts?"
    "SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
    "&STYLE=normal&TILEMATRIXSET=PM"
    "&FORMAT={fmt}&LAYER={layer}"
    "&TILEMATRIX={{z}}&TILEROW={{y}}&TILECOL={{x}}"
)

IGN_LAYERS = {
    "IGN Plan": {
        "url": _IGN_TILE_BASE.format(layer="GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2", fmt="image/png"),
        "attr": "IGN-F/Géoportail",
    },
    "IGN Scan25 (topo)": {
        "url": _IGN_TILE_BASE.format(layer="GEOGRAPHICALGRIDSYSTEMS.MAPS.SCAN25TOUR", fmt="image/jpeg"),
        "attr": "IGN-F/Géoportail",
    },
    "OSM": {
        "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "attr": "© OpenStreetMap",
    },
}

_COLORS = ["#e6194B", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#42d4f4", "#f032e6", "#9A6324"]


@dataclass
class Candidate:
    idx: int
    coords: list[tuple[float, float, float]]  # (lon, lat, ele)
    length_km: float
    dplus_m: float
    elevations: list[float]  # finely sampled, for the profile chart
    gpx_filename: str


def _profile_svg(elevations: list[float], width: int = 280, height: int = 80) -> str:
    """Tiny inline SVG sparkline of the elevation profile."""
    if len(elevations) < 2:
        return ""
    emin, emax = min(elevations), max(elevations)
    if emax - emin < 1:
        emax = emin + 1
    pts = []
    for i, e in enumerate(elevations):
        x = i * width / (len(elevations) - 1)
        y = height - (e - emin) * height / (emax - emin)
        pts.append(f"{x:.1f},{y:.1f}")
    poly = " ".join(pts)
    return (
        f'<svg width="{width}" height="{height}" style="background:#fafafa;border:1px solid #ddd">'
        f'<polyline fill="none" stroke="#4363d8" stroke-width="1.5" points="{poly}"/>'
        f'<text x="2" y="12" font-size="10" fill="#666">{emax:.0f} m</text>'
        f'<text x="2" y="{height-2}" font-size="10" fill="#666">{emin:.0f} m</text>'
        f"</svg>"
    )


def build_html(
    start_lat: float,
    start_lon: float,
    start_label: str,
    candidates: list[Candidate],
    out_path: Path,
) -> Path:
    m = folium.Map(location=[start_lat, start_lon], zoom_start=13, control_scale=True, tiles=None)

    for name, spec in IGN_LAYERS.items():
        folium.TileLayer(tiles=spec["url"], attr=spec["attr"], name=name, max_zoom=18).add_to(m)

    folium.Marker(
        [start_lat, start_lon],
        tooltip=f"Départ : {start_label}",
        icon=folium.Icon(color="black", icon="play"),
    ).add_to(m)

    for c in candidates:
        color = _COLORS[c.idx % len(_COLORS)]
        latlon = [(lat, lon) for lon, lat, _ in c.coords]
        profile = _profile_svg(c.elevations)
        popup_html = (
            f"<b>#{c.idx + 1}</b> — {c.length_km:.1f} km · "
            f"D+ {c.dplus_m:.0f} m · {c.dplus_m / c.length_km:.0f} m/km<br>"
            f'<a href="{c.gpx_filename}" download>Télécharger GPX</a><br>'
            f"{profile}"
        )
        folium.PolyLine(
            latlon,
            color=color,
            weight=4,
            opacity=0.85,
            tooltip=f"#{c.idx + 1}: {c.length_km:.1f} km, D+ {c.dplus_m:.0f} m",
            popup=folium.Popup(popup_html, max_width=320),
        ).add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)

    # Legend
    legend_rows = "".join(
        f'<div><span style="display:inline-block;width:14px;height:4px;background:{_COLORS[c.idx % len(_COLORS)]};vertical-align:middle"></span> '
        f"#{c.idx + 1} — {c.length_km:.1f} km · D+ {c.dplus_m:.0f} m</div>"
        for c in candidates
    )
    legend = (
        '<div style="position:fixed;top:10px;right:10px;background:white;padding:10px 14px;'
        'border:1px solid #999;border-radius:6px;font:13px sans-serif;z-index:9999;max-width:300px">'
        f"<b>Parcours candidats</b><br>Départ : {start_label}<br><br>"
        f"{legend_rows}"
        "</div>"
    )
    m.get_root().html.add_child(folium.Element(legend))

    out_path.write_text(m.get_root().render(), encoding="utf-8")
    return out_path
