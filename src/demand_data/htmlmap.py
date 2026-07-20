"""Mapa HTML (folium) com os pontos gerados e os limites das zonas OD.

Cada point vira um círculo no centroide, raio ∝ √(residents+jobs) e cor pelo balanço
residências×empregos (azul = mais moradia, laranja = mais trabalho). Os limites das
zonas OD entram como uma camada de contornos por baixo. Renderiza em canvas para
aguentar dezenas de milhares de pontos.

Os pontos viajam como um array compacto e os círculos nascem no navegador: um
``CircleMarker`` por ponto faz o folium escrever ~700 bytes de JS cada, o que levava o
arquivo a dezenas de MB.
"""

from __future__ import annotations

import collections
import json
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

_COORD_DECIMALS = 5  # ~1 m
_ZONE_SIMPLIFY = 0.0008  # ~80 m: mantém o formato reconhecível e enxuga o GeoJSON

# ordem em que as camadas aparecem no controle do mapa
_LAYERS = (
    ("home", "moradia"),
    ("work", "trabalho"),
    ("gateway", "conexões externas"),
    ("poi", "equipamentos"),
)

# no load: o folium só escreve o JS do mapa (e dos grupos) depois deste bloco
_MARKERS_JS = """
window.addEventListener('load', function () {
    var points = %(points)s;
    var groups = %(groups)s;
    var map = %(map)s;
    var KINDS = {SCH: 'ensino', HOS: 'saúde', SHP: 'comércio', PRK: 'lazer',
                 UNI: 'ensino', SPO: 'lazer', ZOO: 'lazer', CNV: 'eventos',
                 AIR: 'aeroporto', EXT: 'conexão externa'};
    function label(p) {
        var tipo = p[7] ? ' [' + (KINDS[p[7]] || p[7]) + ']' : '';
        return (p[5] || p[4]) + tipo + ': ' + p[2] + ' moram, ' + p[3] + ' trabalham';
    }
    for (var i = 0; i < points.length; i++) {
        var p = points[i], residents = p[2], jobs = p[3], total = residents + jobs;
        var share = total > 0 ? residents / total : 0.5;
        var kind = p[6];
        if (kind === 'poi') {
            L.marker([p[0], p[1]], {
                icon: L.divIcon({
                    className: 'poi-marker',
                    html: '<i></i><span>' + p[5] + '</span>',
                    iconSize: null
                })
            }).bindTooltip(label(p)).addTo(groups.poi);
            continue;
        }
        L.circleMarker([p[0], p[1]], {
            radius: Math.min(1.5 + Math.sqrt(total) / 40.0, 12),
            color: kind === 'gateway' ? '#2f855a'
                 : share >= 0.6 ? '#2b6cb0'
                 : share <= 0.4 ? '#dd6b20' : '#6b46c1',
            fill: true, fillOpacity: 0.55, weight: 0
        }).bindTooltip(label(p)).addTo(groups[kind]);
    }
    // com a região inteira na tela os rótulos se sobrepõem e viram um borrão
    function toggleLabels() {
        document.body.classList.toggle('poi-labels', map.getZoom() >= 12);
    }
    map.on('zoomend', toggleLabels);
    toggleLabels();
});
"""

_STAMP_CSS = """
<style>
.poi-marker i {
    display: block; width: 11px; height: 11px; transform: translate(-50%, -50%) rotate(45deg);
    background: #c53030; border: 1.5px solid #ffffff; box-shadow: 0 0 2px rgba(0, 0, 0, 0.5);
}
.poi-marker span { display: none; }
.poi-labels .poi-marker span {
    display: inline-block; position: absolute; left: 9px; top: -9px;
    font: 600 11px/1.2 system-ui, sans-serif; color: #1a202c; white-space: nowrap;
    background: rgba(255, 255, 255, 0.92); border: 1px solid #a0aec0;
    border-radius: 3px; padding: 2px 5px;
}
.demand-stamp {
    position: fixed; right: 12px; bottom: 22px; z-index: 9999;
    font: 12px/1.4 system-ui, sans-serif; color: #2d3748;
    background: rgba(255, 255, 255, 0.9); border: 1px solid #cbd5e0;
    border-radius: 4px; padding: 5px 9px;
}
</style>
"""


def _round_floats(value):
    if isinstance(value, (list, tuple)):
        return [_round_floats(v) for v in value]
    return round(value, _COORD_DECIMALS) if isinstance(value, float) else value


def _zone_outlines(zones) -> dict:
    from shapely.geometry import mapping

    features = []
    for zone_id, geom in zip(zones.ids, zones.polygons, strict=True):
        geometry = mapping(geom.simplify(_ZONE_SIMPLIFY, preserve_topology=True))
        geometry["coordinates"] = _round_floats(geometry["coordinates"])
        features.append({"type": "Feature", "geometry": geometry,
                         "properties": {"zona": zone_id}})
    return {"type": "FeatureCollection", "features": features}


def _kind(point: dict) -> str:
    """Camada do ponto: equipamento nomeado, conexão externa, moradia ou trabalho."""
    if point.get("name"):
        return "poi"
    if point["id"].startswith("EXT_"):
        return "gateway"
    return "home" if point.get("residents", 0) >= point.get("jobs", 0) else "work"


def _point_rows(points: list[dict]) -> list:
    rows = []
    for p in points:
        lng, lat = p["location"]
        rows.append([round(lat, _COORD_DECIMALS), round(lng, _COORD_DECIMALS),
                     p.get("residents", 0), p.get("jobs", 0), p["id"],
                     p.get("name", ""), _kind(p), p.get("type", "")])
    return rows


def write(points: list[dict], center: tuple[float, float], path: Path, zones=None) -> None:
    import folium

    generated_at = datetime.now().astimezone()
    m = folium.Map(location=[center[1], center[0]], zoom_start=10,
                   tiles="cartodbpositron", prefer_canvas=True)

    if zones is not None:
        folium.GeoJson(
            _zone_outlines(zones),
            name="limites das zonas",
            style_function=lambda _f: {"color": "#3182ce", "weight": 1, "fill": False,
                                       "opacity": 0.5},
            highlight_function=lambda _f: {"weight": 2.5, "color": "#1a365d"},
            tooltip=folium.GeoJsonTooltip(fields=["zona"], aliases=["zona OD:"]),
        ).add_to(m)

    rows = _point_rows(points)
    counts = collections.Counter(row[6] for row in rows)
    groups = {}
    for kind, label in _LAYERS:
        group = folium.FeatureGroup(name=f"{label} ({counts.get(kind, 0):,})".replace(",", "."))
        group.add_to(m)
        groups[kind] = group.get_name()
    folium.LayerControl(collapsed=False).add_to(m)
    m.get_root().script.add_child(folium.Element(_MARKERS_JS % {
        "points": json.dumps(rows, separators=(",", ":")),
        "groups": "{" + ",".join(f"{k}:{name}" for k, name in groups.items()) + "}",
        "map": m.get_name(),
    }))

    stamp = generated_at.strftime("%d/%m/%Y %H:%M")
    total = f"{len(points):,}".replace(",", ".")
    m.get_root().header.add_child(folium.Element(
        f"<title>Pops de demanda RMSP — {stamp}</title>{_STAMP_CSS}"
    ))
    m.get_root().html.add_child(folium.Element(
        f'<div class="demand-stamp">{total} pontos · gerado em {stamp}</div>'
    ))

    m.save(str(path))
    log.info("mapa HTML: %s (%d pontos, %.1f MB)",
             path.name, len(points), path.stat().st_size / 1e6)
