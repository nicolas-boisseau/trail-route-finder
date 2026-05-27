"""Render climbing segments as a colored HTML map (folium + IGN tiles)."""
from __future__ import annotations

from pathlib import Path

import folium

from segments import Climb
from visualize import IGN_LAYERS


def _color_for_slope(slope_pct: float) -> str:
    if slope_pct >= 12:
        return "#7a0000"  # very steep
    if slope_pct >= 8:
        return "#d00000"  # steep
    if slope_pct >= 5:
        return "#f4651f"  # moderate
    return "#e8b71a"      # gentle


def build_segments_html(start_lat: float, start_lon: float, label: str, climbs: list[Climb], out_path: Path) -> Path:
    m = folium.Map(location=[start_lat, start_lon], zoom_start=12, control_scale=True, tiles=None)

    for name, spec in IGN_LAYERS.items():
        folium.TileLayer(tiles=spec["url"], attr=spec["attr"], name=name, max_zoom=18).add_to(m)

    folium.Marker(
        [start_lat, start_lon],
        tooltip=f"Centre : {label}",
        icon=folium.Icon(color="black", icon="play"),
    ).add_to(m)

    # Sort so highest D+ draws on top
    climbs_sorted = sorted(climbs, key=lambda c: c.dplus_m)

    for c in climbs_sorted:
        color = _color_for_slope(c.avg_slope_pct)
        latlon = [(lat, lon) for lon, lat, _ in c.geometry]
        popup = (
            f"<b>{c.way_name}</b> ({c.way_type}, {c.direction})<br>"
            f"D+ <b>{c.dplus_m:.0f} m</b> · {c.length_m:.0f} m · "
            f"pente moy <b>{c.avg_slope_pct:.1f}%</b> (max {c.max_slope_pct:.1f}%)<br>"
            f"{c.start_elev_m:.0f} m → {c.top_elev_m:.0f} m"
        )
        # Highlight the high end (top) with a small marker
        if latlon:
            top_lat, top_lon = latlon[-1]
            folium.CircleMarker(
                [top_lat, top_lon], radius=3, color=color, fill=True, fillOpacity=0.9,
                tooltip=f"↑ {c.top_elev_m:.0f} m",
            ).add_to(m)
        folium.PolyLine(
            latlon,
            color=color,
            weight=4,
            opacity=0.85,
            tooltip=f"D+{c.dplus_m:.0f}m · {c.avg_slope_pct:.1f}% · {c.length_m:.0f}m",
            popup=folium.Popup(popup, max_width=320),
        ).add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)

    # Top-10 climbs legend
    top10 = sorted(climbs, key=lambda c: -c.dplus_m)[:10]
    rows = "".join(
        f'<tr><td style="color:{_color_for_slope(c.avg_slope_pct)}">●</td>'
        f'<td><b>{c.dplus_m:.0f}m</b></td><td>{c.length_m:.0f}m</td>'
        f'<td>{c.avg_slope_pct:.1f}%</td><td>{c.way_name[:30]}</td></tr>'
        for c in top10
    )
    legend = (
        '<div style="position:fixed;top:10px;right:10px;background:white;padding:10px 12px;'
        'border:1px solid #999;border-radius:6px;font:12px sans-serif;z-index:9999;max-height:80vh;overflow-y:auto;max-width:380px">'
        f"<b>Top 10 grimpettes — {label}</b><br>"
        f"<small>{len(climbs)} segments détectés · D+≥25m · pente≥4%</small><br>"
        '<table style="width:100%;font-size:11px;margin-top:6px;border-collapse:collapse">'
        '<tr style="background:#eee"><th></th><th>D+</th><th>L</th><th>%</th><th>nom</th></tr>'
        f"{rows}"
        '</table>'
        '<br><small style="color:#666">Cliquer un tracé pour les détails.<br>'
        'Couleurs : <span style="color:#e8b71a">●</span>3-5% · '
        '<span style="color:#f4651f">●</span>5-8% · '
        '<span style="color:#d00000">●</span>8-12% · '
        '<span style="color:#7a0000">●</span>≥12%</small>'
        '</div>'
    )
    m.get_root().html.add_child(folium.Element(legend))

    out_path.write_text(m.get_root().render(), encoding="utf-8")
    return out_path
