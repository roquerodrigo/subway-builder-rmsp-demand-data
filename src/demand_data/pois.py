"""Equipamentos nomeados: aeroportos, campi, estádios, shoppings, hospitais, parques.

As viagens dizem para qual coordenada e por qual motivo cada uma vai, não para qual
equipamento. Aqui cada destino tipado adota a identidade do equipamento real **mais próximo**
que atende o seu motivo — o ponto de saúde perto de Congonhas passa a ser o hospital que o
serve, com o nome e o tipo que o Subway Builder mostra.

Nada aqui é estimado à mão: as coordenadas, os nomes e os contornos vêm do **OpenStreetMap**
(com o ``osm_id`` para conferência). Entre dois equipamentos igualmente próximos, o de maior
porte (área do contorno) desempata. Nenhum ponto é criado nem tem demanda alterada — a
origem e o tamanho de cada viagem continuam os observados; o destino só ganha identidade.

O **id** é o que carrega o tipo para o jogo: o depot lê ``id.split("_")[0]``. Um destino
adotado passa a se chamar ``AIR_Congonhas``, e os pops que apontavam para o id antigo são
reapontados.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path

from demand_data.config import settings

log = logging.getLogger(__name__)

_NUDGE = 1e-5  # ~1 m: separa coordenada duplicada quando dois destinos caem no mesmo lugar

# que tipo de equipamento do OSM pode dar identidade a um destino de cada place type. Quem
# vai por educação chega numa escola ou campus; por lazer, num parque, estádio, zoo ou centro
# de eventos.
ACCEPTS: dict[str, frozenset[str]] = {
    "SCH": frozenset({"SCH", "UNI"}),
    "HOS": frozenset({"HOS"}),
    "SHP": frozenset({"SHP"}),
    "PRK": frozenset({"PRK", "ZOO", "SPO", "CNV"}),
}
_POI_TYPES = frozenset().union(*ACCEPTS.values())


def _identifier(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in name).strip("_")[:40]


def load(path: Path | None = None) -> list[dict]:
    """Lê ``pois.csv`` (``lng,lat,tipo,osm_id,nome,contorno``) escrito por ``sources``."""
    path = path or settings.pois_csv
    if not path.exists():
        log.warning("sem %s — rode `sources` para baixar os equipamentos", path.name)
        return []
    found = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split(",", 5)
            if len(parts) != 6:
                continue
            try:
                lng, lat = float(parts[0]), float(parts[1])
                ring = [float(v) for v in parts[5].split()] if parts[5] else []
            except ValueError:
                continue
            found.append({"location": [lng, lat], "type": parts[2], "osm_id": parts[3],
                          "name": parts[4], "ring": ring})
    return found


def area(ring: list[float]) -> float:
    """Porte do equipamento: a área do contorno do OSM. Nó solto (sem contorno) fica em 0."""
    if len(ring) < 6:
        return 0.0
    from shapely.geometry import Polygon

    shape = Polygon(list(zip(ring[0::2], ring[1::2], strict=True)))
    if not shape.is_valid:
        shape = shape.buffer(0)
    return shape.area


def _assign_ids(catalogue: list[dict]) -> None:
    """Dá a cada equipamento um id ``TIPO_Nome`` estável; homônimos desempatam pelo osm_id."""
    used: set[str] = set()
    for poi in catalogue:
        point_id = f"{poi['type']}_{_identifier(poi['name'])}"
        if point_id in used:
            point_id = f"{point_id}_{poi['osm_id']}"
        used.add(point_id)
        poi["id"] = point_id


def adopt(points: list[dict], pops: list[dict], catalogue: list[dict] | None = None) -> int:
    """Dá a cada destino tipado o equipamento compatível mais próximo (desempate por porte).

    Reaponta os pops do destino para o novo id e renomeia o ponto. Nenhum ponto é criado.
    """
    catalogue = load() if catalogue is None else catalogue
    usable = [poi for poi in catalogue if poi["type"] in _POI_TYPES]
    if not usable:
        return 0
    _assign_ids(usable)
    for poi in usable:
        poi["area"] = area(poi.get("ring") or [])

    from shapely import STRtree
    from shapely import points as as_points
    from shapely.geometry import Point

    geoms = as_points([(poi["location"][0], poi["location"][1]) for poi in usable])
    tree = STRtree(geoms)

    jobs_by_point: dict[str, list[dict]] = defaultdict(list)
    for pop in pops:
        jobs_by_point[pop["jobId"]].append(pop)

    radius = settings.poi_snap_m / settings.m_per_deg_lat
    used_loc = {tuple(p["location"]) for p in points}
    taken: set[str] = set()
    adopted = 0

    # o destino de maior demanda escolhe primeiro: quando dois disputam o mesmo equipamento,
    # o polo mais forte fica com ele
    dests = [p for p in points if p.get("type") in ACCEPTS]
    dests.sort(key=lambda p: -sum(pop["size"] for pop in jobs_by_point.get(p["id"], ())))

    for point in dests:
        accepts = ACCEPTS[point["type"]]
        origin = Point(point["location"][0], point["location"][1])
        best, best_key = None, None
        for i in tree.query(origin.buffer(radius)):
            poi = usable[i]
            if poi["id"] in taken or poi["type"] not in accepts:
                continue
            key = (origin.distance(geoms[i]), -poi["area"])
            if best_key is None or key < best_key:
                best, best_key = poi, key
        if best is None:
            continue
        taken.add(best["id"])
        lng, lat = round(best["location"][0], 6), round(best["location"][1], 6)
        while (lng, lat) in used_loc:
            lng = round(lng + _NUDGE, 6)
        used_loc.add((lng, lat))
        previous = point["id"]
        point["id"] = best["id"]
        point["location"] = [lng, lat]
        point["name"] = best["name"]
        point["osmId"] = best["osm_id"]
        point["type"] = best["type"]
        for pop in jobs_by_point.get(previous, ()):
            pop["jobId"] = best["id"]
        adopted += 1
    log.info("equipamentos adotados: %d destinos nomeados", adopted)
    return adopted
