"""Mapa HTML (folium) com os pontos gerados e os limites das zonas OD.

Cada point vira um círculo no centroide, raio ∝ √(residents+jobs) e cor pelo balanço
residências×empregos (azul = mais moradia, laranja = mais trabalho). Os limites das
zonas OD entram como uma camada de contornos por baixo. Renderiza em canvas para
aguentar milhares de pontos.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def _color(residents: int, jobs: int) -> str:
    tot = residents + jobs
    if tot <= 0:
        return "#888888"
    r = residents / tot
    return "#2b6cb0" if r >= 0.6 else "#dd6b20" if r <= 0.4 else "#6b46c1"


def write(points: list[dict], center: tuple[float, float], path: Path, zones=None) -> None:
    import folium
    from shapely.geometry import mapping

    m = folium.Map(location=[center[1], center[0]], zoom_start=10,
                   tiles="cartodbpositron", prefer_canvas=True)

    # limites das zonas OD (contornos, por baixo dos pontos). Simplifica (~80 m) só p/ o
    # desenho — mantém o formato reconhecível e evita um HTML gigante (alta resolução pesa).
    if zones is not None:
        fc = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature",
                 "geometry": mapping(g.simplify(0.0008, preserve_topology=True)),
                 "properties": {"zona": zid}}
                for zid, g in zip(zones.ids, zones.polygons, strict=True)
            ],
        }
        folium.GeoJson(
            fc,
            name="limites das zonas",
            style_function=lambda _f: {"color": "#3182ce", "weight": 1, "fill": False,
                                       "opacity": 0.5},
            highlight_function=lambda _f: {"weight": 2.5, "color": "#1a365d"},
            tooltip=folium.GeoJsonTooltip(fields=["zona"], aliases=["zona OD:"]),
        ).add_to(m)

    pg = folium.FeatureGroup(name="pontos de demanda")
    for p in points:
        lng, lat = p["location"]
        res, jobs = p.get("residents", 0), p.get("jobs", 0)
        radius = 1.5 + (res + jobs) ** 0.5 / 40.0
        folium.CircleMarker(
            [lat, lng], radius=min(radius, 12), color=_color(res, jobs),
            fill=True, fill_opacity=0.55, weight=0,
            tooltip=f"{p['id']}: {res} moram, {jobs} trabalham",
        ).add_to(pg)
    pg.add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)

    m.save(str(path))
    log.info("mapa HTML: %s (%d pontos)", path.name, len(points))
