"""Equipamentos nomeados: campi, estádios, shoppings, hospitais, parques.

As viagens dizem para qual coordenada e por qual motivo cada uma vai, não para qual
equipamento. Aqui cada destino tipado adota a identidade do equipamento real **mais próximo**
que atende o seu motivo — um ponto de saúde passa a ser o hospital que o serve, com o nome e o
tipo que o Subway Builder mostra.

Nada aqui é estimado à mão: as coordenadas, os nomes e os contornos vêm do **OpenStreetMap**
(com o ``osm_id`` para conferência). Entre dois equipamentos igualmente próximos, o de maior
porte (área do contorno) desempata. Nenhum ponto é criado nem tem demanda alterada — a
origem e o tamanho de cada viagem continuam os observados; o destino só ganha identidade.

O **id** é o que carrega o tipo para o jogo: o depot lê ``id.split("_")[0]``. Um destino
adotado passa a se chamar ``HOS_HospitalDasClinicas``, e os pops que apontavam para o id
antigo são reapontados.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path

from demand_data import pointid
from demand_data.config import settings
from demand_data.coordinates import CoordinateRegistry
from demand_data.taxonomy import ACCEPTED_EQUIPMENT, EQUIPMENT_CODES

log = logging.getLogger(__name__)

# que tipo de equipamento do OSM pode dar identidade a um destino de cada place type
ACCEPTS: dict[str, frozenset[str]] = {
    place: frozenset(codes) for place, codes in ACCEPTED_EQUIPMENT.items()
}
_POI_TYPES = frozenset(EQUIPMENT_CODES)


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


class _EquipmentIndex:
    """Índice espacial dos equipamentos utilizáveis, com busca do compatível mais próximo."""

    def __init__(self, catalogue: list[dict]) -> None:
        self._usable = [poi for poi in catalogue if poi["type"] in _POI_TYPES]
        self._tree = None
        if not self._usable:
            return
        _assign_ids(self._usable)
        for poi in self._usable:
            poi["area"] = area(poi.get("ring") or [])
        from shapely import STRtree
        from shapely import points as as_points

        self._geoms = as_points([(poi["location"][0], poi["location"][1])
                                 for poi in self._usable])
        self._tree = STRtree(self._geoms)

    def __bool__(self) -> bool:
        return self._tree is not None

    def nearest(self, location, accepts: frozenset[str], radius: float,
                taken: set[str]) -> dict | None:
        """Equipamento compatível mais próximo dentro do raio (desempate por porte), ou None."""
        from shapely import box
        from shapely.geometry import Point

        origin = Point(location[0], location[1])
        # a janela é um quadrado (a STRtree só usa o envelope); o círculo real do raio é o
        # filtro de distância abaixo, senão adotaríamos até radius·√2 além do teto
        window = box(origin.x - radius, origin.y - radius, origin.x + radius, origin.y + radius)
        best, best_key = None, None
        for i in self._tree.query(window):
            poi = self._usable[i]
            if poi["id"] in taken or poi["type"] not in accepts:
                continue
            distance = origin.distance(self._geoms[i])
            if distance > radius:
                continue
            key = (distance, -poi["area"])
            if best_key is None or key < best_key:
                best, best_key = poi, key
        return best


def adopt(points: list[dict], pops: list[dict], catalogue: list[dict] | None = None) -> int:
    """Dá a cada destino tipado o equipamento compatível mais próximo (desempate por porte).

    Reaponta os pops do destino para o novo id e renomeia o ponto. Nenhum ponto é criado.
    """
    catalogue = load() if catalogue is None else catalogue
    index = _EquipmentIndex(catalogue)
    if not index:
        return 0

    jobs_by_point: dict[str, list[dict]] = defaultdict(list)
    for pop in pops:
        jobs_by_point[pop["jobId"]].append(pop)

    radius = settings.poi_snap_m / settings.m_per_deg_lat
    coordinates = CoordinateRegistry(p["location"] for p in points)
    taken: set[str] = set()
    adopted = 0

    # o destino de maior demanda escolhe primeiro: quando dois disputam o mesmo equipamento,
    # o polo mais forte fica com ele
    dests = [p for p in points if p.get("type") in ACCEPTS]
    dests.sort(key=lambda p: -sum(pop["size"] for pop in jobs_by_point.get(p["id"], ())))

    for point in dests:
        equipment = index.nearest(point["location"], ACCEPTS[point["type"]], radius, taken)
        if equipment is None:
            continue
        taken.add(equipment["id"])
        previous = point["id"]
        point["id"] = equipment["id"]
        point["location"] = coordinates.place(equipment["location"][0], equipment["location"][1])
        point["name"] = equipment["name"]
        point["osmId"] = equipment["osm_id"]
        point["type"] = equipment["type"]
        for pop in jobs_by_point.get(previous, ()):
            pop["jobId"] = equipment["id"]
        adopted += 1
    log.info("equipamentos adotados: %d destinos nomeados", adopted)
    return adopted


def tag_untyped(points: list[dict], pops: list[dict]) -> int:
    """Prefixa o id dos destinos tipados que não adotaram equipamento (sem ``name``).

    O jogo lê o tipo pelo prefixo do id (``id.split("_")[0]``), não pelo campo ``type``; sem o
    prefixo, uma escola sem equipamento nomeado por perto chegaria como demanda sem tipo.
    Roda depois de :func:`adopt` e reaponta os pops do destino. Devolve quantos foram."""
    jobs_by_point: dict[str, list[dict]] = defaultdict(list)
    for pop in pops:
        jobs_by_point[pop["jobId"]].append(pop)

    tagged = 0
    for point in points:
        if not point.get("type") or point.get("name"):
            continue
        previous = point["id"]
        point["id"] = pointid.with_type(point["type"], previous)
        for pop in jobs_by_point.get(previous, ()):
            pop["jobId"] = point["id"]
        tagged += 1
    log.info("destinos tipados sem equipamento: %d marcados pelo tipo", tagged)
    return tagged
