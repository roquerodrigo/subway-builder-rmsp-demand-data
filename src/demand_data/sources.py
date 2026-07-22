"""Aquisição dos dados de entrada para ``data/sources``:

  - **viagens** já geolocalizadas (``fluxos.parquet``) do repositório de dados;
  - **equipamentos** nomeados do OpenStreetMap (Overpass) -> ``pois.csv``.

Tudo idempotente: pula o que já existe.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
from pathlib import Path

from demand_data.config import settings

log = logging.getLogger(__name__)

_OUTLINE_TOLERANCE = 0.0002  # ~20 m: enxuga o contorno sem perder o formato


def _mb(p: Path) -> float:
    return p.stat().st_size / 1e6


def _download(url: str, dest: Path) -> None:
    if dest.exists():
        log.info("já baixado: %s", dest.name)
        return
    log.info("baixando %s -> %s", url, dest.name)
    # baixa para .part e só então renomeia: um download interrompido no meio ficaria no
    # destino final e a execução seguinte o trataria como completo.
    partial = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as r, open(partial, "wb") as f:  # noqa: S310
        while chunk := r.read(1 << 20):
            f.write(chunk)
    partial.replace(dest)


def download_flows() -> None:
    """Baixa as viagens geolocalizadas (``fluxos.parquet``) do repositório de dados."""
    settings.ensure_sources()
    _download(settings.flow_url, settings.flows_parquet)
    if settings.flows_parquet.exists():
        log.info("viagens: %s (%.1f MB)", settings.flows_parquet.name,
                 _mb(settings.flows_parquet))


# tipos do OSM -> código da taxonomia do depot. Só o que gera deslocamento próprio.
# O filtro é ter NOME, não ser notável: exigir "wikidata" trazia 30 escolas para os 4,4
# milhões de moradores cujo destino é a escola. Parques ficam na notabilidade porque os de
# bairro são incontáveis e cada um atrai pouco.
# (código, filtro, precisa do contorno). O contorno só muda o resultado em equipamento
# grande e de forma irregular — campus, parque, aeroporto. Escola e hospital são compactos e
# cabem no raio mínimo, e pedir a geometria de milhares deles derruba o endpoint público.
POI_QUERIES: tuple[tuple[str, str, bool], ...] = (
    ("AIR", '["aeroway"="aerodrome"]["iata"]', True),
    ("UNI", '["amenity"="university"]["name"]', True),
    ("PRK", '["leisure"="park"]["wikidata"]', True),
    ("SPO", '["leisure"="stadium"]["name"]', True),
    ("ZOO", '["tourism"="zoo"]["name"]', True),
    ("SCH", '["amenity"="college"]["name"]', True),
    ("SCH", '["amenity"="school"]["name"]', False),
    ("HOS", '["amenity"="hospital"]["name"]', True),
    ("SHP", '["shop"="mall"]["name"]', True),
    ("CNV", '["amenity"="conference_centre"]["name"]', True),
    ("CNV", '["amenity"="exhibition_centre"]["name"]', True),
    ("EXT", '["amenity"="bus_station"]["name"]', True),
)


def outline(element: dict) -> list[float]:
    """Contorno do elemento, simplificado, como ``[lng, lat, lng, lat, ...]``.

    O porte do equipamento é medido dentro dele. O retângulo envolvente servia mal a
    geometrias em L ou alongadas — um parque estreito engolia quarteirões que não são dele.
    """
    geometry = element.get("geometry") or []
    ring = [(p["lon"], p["lat"]) for p in geometry
            if p.get("lon") is not None and p.get("lat") is not None]
    if len(ring) < 3:
        return []
    from shapely.geometry import Polygon

    shape = Polygon(ring)
    if not shape.is_valid:
        shape = shape.buffer(0)
    shape = shape.simplify(_OUTLINE_TOLERANCE, preserve_topology=True)
    coords = list(getattr(shape, "exterior", shape).coords) if not shape.is_empty else []
    flat = []
    for lng, lat in coords:
        flat += [round(lng, 6), round(lat, 6)]
    return flat


def parse_overpass(elements, codes: dict[int, str]):
    """Elementos do Overpass -> ``lng,lat,tipo,osm_id,nome,contorno``.

    O contorno vai junto porque o porte é medido dentro dele; elementos sem geometria (nós
    soltos) saem com o contorno vazio e caem num raio mínimo.
    """
    seen = set()
    for element in elements:
        tags = element.get("tags") or {}
        name = (tags.get("name") or "").strip().replace(",", " ")
        ring = outline(element)
        if ring:
            # centroide do polígono: a média dos vértices contaria o ponto de fechamento
            # duas vezes e pesaria mais onde o contorno tem mais detalhe
            from shapely.geometry import Polygon

            centre = Polygon(list(zip(ring[0::2], ring[1::2], strict=True))).centroid
            lng, lat = centre.x, centre.y
        else:
            # nó solto traz lon/lat direto; way consultado sem geometria traz "center"
            source = element.get("center") or element
            lng, lat = source.get("lon"), source.get("lat")
        code = codes.get(id(element)) or tags.get("_code")
        if not name or lng is None or lat is None or not code:
            continue
        # dedupe pelo id do OSM, não pelo nome: a cidade tem dezenas de escolas homônimas
        # em bairros diferentes, e todas são destinos distintos
        key = element.get("id")
        if key in seen or not settings.in_bbox(float(lng), float(lat)):
            continue
        seen.add(key)
        yield (f"{round(float(lng), 6)},{round(float(lat), 6)},{code},{element.get('id')},"
               f"{name}," + " ".join(str(v) for v in ring) + "\n")


def _overpass_query(filters: str, with_geometry: bool) -> str:
    b = settings.bbox
    output = "out geom tags;" if with_geometry else "out center tags;"
    return (f"[out:json][timeout:300];\n"
            f"nwr{filters}({b[1]},{b[0]},{b[3]},{b[2]});\n{output}")


def pois() -> None:
    """Baixa os equipamentos do OpenStreetMap (Overpass) para ``pois.csv``."""
    out = settings.pois_csv
    if out.exists():
        log.info("já processado: %s", out.name)
        return
    settings.ensure_sources()
    log.info("baixando equipamentos do OpenStreetMap (Overpass)")
    # uma consulta por tipo: com a geometria de todos juntos o endpoint público estoura o
    # tempo, e assim a falha de um tipo não derruba os outros
    elements, codes = [], {}
    for code, filters, with_geometry in POI_QUERIES:
        found = _overpass(_overpass_query(filters, with_geometry))
        for element in found:
            codes[id(element)] = code
        elements += found
        log.info("  %-4s %5d elementos", code, len(found))

    kept = 0
    with open(out, "w", encoding="utf-8") as fout:
        for row in parse_overpass(elements, codes):
            fout.write(row)
            kept += 1
    log.info("equipamentos: %d lidos, %d mantidos -> %s", len(elements), kept, out.name)


def _overpass(query: str, tries: int = 3) -> list:
    """Consulta o Overpass com repetição: o endpoint público responde 504 sob carga."""
    data = urllib.parse.urlencode({"data": query}).encode()
    request = urllib.request.Request(
        settings.overpass_url, data=data, headers={"User-Agent": "demand-data/1.0"}
    )
    for attempt in range(max(1, tries) - 1):
        try:
            with urllib.request.urlopen(request, timeout=600) as response:  # noqa: S310
                return json.loads(response.read().decode("utf-8")).get("elements", [])
        except Exception:
            time.sleep(10 * (attempt + 1))
    with urllib.request.urlopen(request, timeout=600) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8")).get("elements", [])


def acquire() -> None:
    """Baixa + processa tudo (idempotente)."""
    settings.ensure_sources()
    download_flows()
    pois()
