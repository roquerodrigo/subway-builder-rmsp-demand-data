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
_KIND_INDEX = {kind: index for index, (kind, _label) in enumerate(_LAYERS)}
# tipos na ordem em que o JS os traduz; 0 = sem tipo
_TYPES = ("", "SCH", "HOS", "SHP", "PRK", "UNI", "SPO", "ZOO", "CNV", "AIR", "EXT")
_TYPE_INDEX = {code: index for index, code in enumerate(_TYPES)}

# no load: o folium só escreve o JS do mapa (e dos grupos) depois deste bloco
_MARKERS_JS = """
window.addEventListener('load', function () {
    var points = %(points)s;
    var groups = %(groups)s;
    var map = %(map)s;
    var TYPES = %(types)s;
    var NAMES = {SCH: 'ensino', HOS: 'saúde', SHP: 'comércio', PRK: 'lazer',
                 UNI: 'ensino', SPO: 'lazer', ZOO: 'lazer', CNV: 'eventos',
                 AIR: 'aeroporto', EXT: 'conexão externa'};
    var labels = [];  // [marcador, prioridade] — quanto menor, mais cedo ganha espaço
    function label(p, isPoi) {
        var code = TYPES[p[6]];
        var tipo = code ? ' [' + (NAMES[code] || code) + ']' : '';
        var quem = isPoi ? p[5] : 'zona ' + p[5];
        return quem + tipo + ': ' + p[2] + ' moram, ' + p[3] + ' trabalham';
    }
    for (var i = 0; i < points.length; i++) {
        var p = points[i], residents = p[2], jobs = p[3], total = residents + jobs;
        var share = total > 0 ? residents / total : 0.5;
        var kind = p[4];
        if (kind === 3) {
            var icon = L.divIcon({
                className: 'poi-marker',
                html: '<i></i><span>' + p[5] + '</span>',
                iconSize: null
            });
            var marker = L.marker([p[0], p[1]], {icon: icon})
                .bindTooltip(label(p, true)).addTo(groups.poi);
            marker._labelText = p[5];
            labels.push([marker, p[7]]);
            continue;
        }
        L.circleMarker([p[0], p[1]], {
            radius: Math.min(1.5 + Math.sqrt(total) / 40.0, 12),
            color: kind === 2 ? '#2f855a'
                 : share >= 0.6 ? '#2b6cb0'
                 : share <= 0.4 ? '#dd6b20' : '#6b46c1',
            fill: true, fillOpacity: 0.55, weight: 0
        }).bindTooltip(label(p, false)).addTo(groups[kind]);
    }
    // Milhares de equipamentos: escalonar por zoom não basta, porque os maiores ficam todos
    // no centro e os nomes se empilham. Aqui o rótulo só aparece se couber — os mais
    // importantes reservam seu espaço primeiro, o resto espera o próximo nível de zoom.
    labels.sort(function (a, b) { return a[1] - b[1]; });
    // largura estimada pelo texto: medir a caixa real de milhares de rótulos forçaria
    // um reflow por elemento a cada movimento do mapa
    var CHAR_W = 7.0, PAD = 34, LABEL_H = 30, BUCKET = 120, MAX_LABELS = 45;
    function declutter() {
        var buckets = {}, size = map.getSize(), shown = 0;
        for (var i = 0; i < labels.length; i++) {
            var marker = labels[i][0], element = marker._icon;
            if (!element) { continue; }
            if (shown >= MAX_LABELS) { element.classList.remove('named'); continue; }
            var point = map.latLngToContainerPoint(marker.getLatLng());
            var width = marker._labelText.length * CHAR_W + PAD;
            var left = point.x + 9, top = point.y - 9;
            if (left < 0 || top < 0 || left + width > size.x || top + LABEL_H > size.y) {
                element.classList.remove('named');
                continue;
            }
            // testa a caixa real contra as já aceitas; os buckets evitam comparar com todas
            var fits = true, keys = [];
            for (var bx = Math.floor(left / BUCKET); bx <= Math.floor((left + width) / BUCKET);
                 bx++) {
                for (var by = Math.floor(top / BUCKET);
                     by <= Math.floor((top + LABEL_H) / BUCKET); by++) {
                    var key = bx + ':' + by;
                    keys.push(key);
                    var box = buckets[key] || [];
                    for (var b = 0; b < box.length; b++) {
                        var o = box[b];
                        if (left < o[2] && o[0] < left + width
                            && top < o[3] && o[1] < top + LABEL_H) { fits = false; }
                    }
                }
            }
            if (!fits) {
                element.classList.remove('named');
                continue;
            }
            var rect = [left, top, left + width, top + LABEL_H];
            for (var k = 0; k < keys.length; k++) {
                (buckets[keys[k]] = buckets[keys[k]] || []).push(rect);
            }
            element.classList.add('named');
            shown++;
        }
    }
    map.on('zoomend', declutter);
    map.on('moveend', declutter);
    setTimeout(declutter, 0);
});
"""

_STAMP_CSS = """
<style>
.poi-marker i {
    display: block; width: 7px; height: 7px; transform: translate(-50%, -50%) rotate(45deg);
    background: #c53030; border: 1px solid #ffffff; opacity: 0.85;
}
.poi-marker span { display: none; }
.poi-marker.named span {
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


def _zone_of(point_id: str) -> int:
    digits = ""
    for char in point_id[1:]:
        if not char.isdigit():
            break
        digits += char
    return int(digits) if digits else 0


def _point_rows(points: list[dict]) -> list:
    """Linhas compactas: ``[lat, lng, moradores, empregos, camada, zona|nome, tipo, zoom]``.

    Os índices de camada e tipo, e a zona no lugar do id inteiro, existem porque este array
    é ~80% do arquivo — repetir "z12345w678" e "home" em 45 mil linhas custa megabytes.
    Equipamentos levam um oitavo campo: a prioridade do rótulo na disputa por espaço.
    """
    # prioridade do rótulo: quem tem mais demanda reserva espaço na tela primeiro
    ranked = sorted((p for p in points if p.get("name")),
                    key=lambda p: -(p.get("jobs", 0) + p.get("residents", 0)))
    priority = {p["id"]: rank for rank, p in enumerate(ranked)}

    rows = []
    for p in points:
        lng, lat = p["location"]
        kind = _kind(p)
        row = [round(lat, _COORD_DECIMALS), round(lng, _COORD_DECIMALS),
               p.get("residents", 0), p.get("jobs", 0), _KIND_INDEX[kind],
               p.get("name") or _zone_of(p["id"]),
               _TYPE_INDEX.get(p.get("type", ""), 0)]
        if kind == "poi":
            row.append(priority[p["id"]])
        rows.append(row)
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
    counts = collections.Counter(row[4] for row in rows)
    groups = {}
    for index, (kind, label) in enumerate(_LAYERS):
        group = folium.FeatureGroup(name=f"{label} ({counts.get(index, 0):,})".replace(",", "."))
        group.add_to(m)
        groups[kind] = group.get_name()
    folium.LayerControl(collapsed=False).add_to(m)
    m.get_root().script.add_child(folium.Element(_MARKERS_JS % {
        "points": json.dumps(rows, separators=(",", ":")),
        "groups": "{" + ",".join(f"{_KIND_INDEX[k]}:{name}" for k, name in groups.items())
                  + ",poi:" + groups["poi"] + "}",
        "map": m.get_name(),
        "types": json.dumps(_TYPES),
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
