"""Pontos de interesse nomeados: aeroportos, campi, estádios, shoppings, hospitais.

A pesquisa diz quantas pessoas vão para cada ZONA, não para qual equipamento. Aqui os
destinos genéricos de uma zona são reetiquetados como o equipamento que de fato os atrai:
o pop que ia para "algum ponto de trabalho da zona de Congonhas" passa a ir para o próprio
aeroporto, com o nome e o tipo que o Subway Builder mostra.

Isso captura demanda em vez de criar: a origem de cada viagem continua sendo a da matriz
O-D, e Σ tamanho dos pops segue igual à população. O depot resolve o mesmo problema com um
modelo de gravidade sintético (``add_points``), necessário lá porque a fonte americana só
tem casa-trabalho.

``capacity`` é o movimento de um dia típico, não o pico: estádios e casas de evento já
entram com a capacidade diluída pela frequência de uso. Os códigos de tipo são os da
taxonomia do depot (``special_demand_types.json``), então o arquivo é reconhecido lá.
"""

from __future__ import annotations

import logging
import re

from demand_data.config import settings

log = logging.getLogger(__name__)

_ZONE_POINT = re.compile(r"^z(\d+)(?:hf|wf|h|w)\d+$")

# (código do tipo no depot, nome, [lng, lat], pessoas num dia típico)
CATALOGUE: tuple[tuple[str, str, list[float], int], ...] = (
    ("AIR", "Aeroporto de Guarulhos", [-46.4731, -23.4356], 118000),
    ("AIR", "Aeroporto de Congonhas", [-46.6553, -23.6266], 60000),
    ("EXT", "Rodoviária do Tietê", [-46.6256, -23.5155], 90000),
    ("UNI", "USP Cidade Universitária", [-46.7167, -23.5595], 60000),
    ("UNI", "Universidade Mackenzie", [-46.6520, -23.5479], 25000),
    ("UNI", "PUC-SP Perdizes", [-46.6786, -23.5378], 20000),
    ("UNI", "UNIFESP Vila Clementino", [-46.6440, -23.5985], 12000),
    ("HOS", "Hospital das Clínicas", [-46.6700, -23.5578], 40000),
    ("HOS", "Hospital Albert Einstein", [-46.7168, -23.5993], 15000),
    ("SHP", "Shopping Aricanduva", [-46.5044, -23.5645], 60000),
    ("SHP", "Shopping Center Norte", [-46.6206, -23.5115], 50000),
    ("SHP", "Shopping Ibirapuera", [-46.6672, -23.6103], 40000),
    ("SHP", "Shopping Morumbi", [-46.6975, -23.6222], 40000),
    ("SHP", "Mercado Municipal", [-46.6294, -23.5416], 15000),
    ("SPO", "Estádio do Morumbi", [-46.7196, -23.6003], 19000),
    ("SPO", "Neo Química Arena", [-46.4742, -23.5453], 14000),
    ("SPO", "Allianz Parque", [-46.6786, -23.5275], 12000),
    ("SPO", "Autódromo de Interlagos", [-46.6997, -23.7036], 3000),
    ("CNV", "Anhembi", [-46.6353, -23.5153], 30000),
    ("CNV", "Expo Center Norte", [-46.6180, -23.5107], 15000),
    ("CNV", "São Paulo Expo", [-46.6739, -23.6900], 15000),
    ("PRK", "Parque Ibirapuera", [-46.6572, -23.5874], 40000),
    ("PRK", "Parque Villa-Lobos", [-46.7248, -23.5459], 15000),
    ("ZOO", "Zoológico de São Paulo", [-46.6191, -23.6489], 8000),
)


def _identifier(name: str) -> str:
    keep = [c if c.isalnum() else "_" for c in name]
    return "".join(keep).strip("_")


def locate(zones, catalogue=CATALOGUE) -> list[dict]:
    """Descobre em que zona cada equipamento está; descarta os que caem fora do recorte."""
    located = []
    for type_code, name, location, capacity in catalogue:
        zone = zones.zone_of(location[0], location[1])
        if zone is None:
            log.warning("POI fora das zonas, ignorado: %s", name)
            continue
        located.append({
            "id": f"{type_code}_{_identifier(name)}",
            "type": type_code, "name": name,
            "location": location, "capacity": capacity, "zone": zone,
        })
    return located


def _shares(sizes: list[int], total: int) -> list[int]:
    """Reparte ``total`` entre os pops ∝ tamanho, preservando a soma."""
    if total <= 0:
        return [0] * len(sizes)
    pool = sum(sizes)
    raw = [size * total / pool for size in sizes]
    out = [int(value) for value in raw]
    remainder = total - sum(out)
    for index in sorted(range(len(raw)), key=lambda i: raw[i] - out[i], reverse=True):
        if remainder <= 0:
            break
        if out[index] < sizes[index]:
            out[index] += 1
            remainder -= 1
    return out


def capture(points: list[dict], pops: list[dict], zones, catalogue=CATALOGUE) -> list[dict]:
    """Reetiqueta parte dos destinos genéricos como os equipamentos que os atraem.

    Cada equipamento toma uma FATIA PROPORCIONAL de cada pop que chega à sua zona, até
    ``capacity`` e até ``poi_max_zone_share`` da demanda ainda disponível ali. Tomar pops
    inteiros, dos maiores para os menores, fazia o equipamento estourar a própria capacidade,
    esvaziar a zona quando a capacidade era maior que ela, e herdar só as origens dos poucos
    pops grandes que coubessem.

    Devolve os pontos criados; ``points`` e ``pops`` são alterados no lugar.
    """
    located = locate(zones, catalogue)
    if not located:
        return []

    zone_of_point = {}
    for point in points:
        match = _ZONE_POINT.match(point["id"])
        if match:
            zone_of_point[point["id"]] = int(match.group(1))

    arriving: dict[int, list[dict]] = {}
    for pop in pops:
        zone = zone_of_point.get(pop["jobId"])
        if zone is not None:
            arriving.setdefault(zone, []).append(pop)

    max_share = min(max(settings.poi_max_zone_share, 0.0), 1.0)
    created, added = [], []
    for poi in located:
        # só a demanda que ainda vai para um ponto genérico da zona: o que outro equipamento
        # da mesma zona já levou não está mais disponível
        pool = [p for p in arriving.get(poi["zone"], [])
                if zone_of_point.get(p["jobId"]) == poi["zone"] and p["size"] > 0]
        available = sum(p["size"] for p in pool)
        target = min(poi["capacity"], int(available * max_share))
        if target <= 0:
            log.warning("sem demanda para capturar em %s (zona %d)", poi["name"], poi["zone"])
            continue
        if poi["capacity"] > available * max_share:
            log.info("  %s limitado pela zona: %d de %d pretendidos",
                     poi["name"], target, poi["capacity"])

        point = {"id": poi["id"], "location": poi["location"], "name": poi["name"],
                 "type": poi["type"], "jobs": 0, "residents": 0, "popIds": []}
        for pop, share in zip(pool, _shares([p["size"] for p in pool], target), strict=True):
            if share <= 0:
                continue
            if share >= pop["size"]:
                pop["jobId"] = poi["id"]
                continue
            slice_pop = dict(pop)
            slice_pop["id"] = f"{pop['id']}@{len(added)}"
            slice_pop["size"] = share
            slice_pop["jobId"] = poi["id"]
            pop["size"] -= share
            added.append(slice_pop)
        points.append(point)
        created.append(point)
        poi["captured"] = target

    pops.extend(added)
    log.info(
        "POIs: %d equipamentos capturaram %d pessoas (de %d catalogados), %d pops fatiados",
        len(created), sum(p.get("captured", 0) for p in located), len(catalogue), len(added),
    )
    return created
