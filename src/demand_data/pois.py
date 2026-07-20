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


def capture(points: list[dict], pops: list[dict], zones, catalogue=CATALOGUE) -> list[dict]:
    """Reetiqueta destinos genéricos como os equipamentos que os atraem.

    Cada POI toma até ``capacity`` pessoas entre os pops que já chegam à sua zona, dos
    maiores para os menores. Devolve os pontos criados; ``points`` e ``pops`` são alterados
    no lugar.
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
    for zone in arriving:
        arriving[zone].sort(key=lambda p: -p["size"])

    created = []
    claimed: set[str] = set()  # dois equipamentos na mesma zona não disputam o mesmo pop
    for poi in located:
        pool = arriving.get(poi["zone"], [])
        point = {"id": poi["id"], "location": poi["location"], "name": poi["name"],
                 "type": poi["type"], "jobs": 0, "residents": 0, "popIds": []}
        taken = 0
        for pop in pool:
            if taken >= poi["capacity"]:
                break
            if pop["id"] in claimed:
                continue
            pop["jobId"] = poi["id"]
            claimed.add(pop["id"])
            taken += pop["size"]
        if taken == 0:
            log.warning("sem demanda para capturar em %s (zona %d)", poi["name"], poi["zone"])
            continue
        points.append(point)
        created.append(point)
        poi["captured"] = taken

    log.info(
        "POIs: %d equipamentos capturaram %d pessoas (de %d catalogados)",
        len(created), sum(p.get("captured", 0) for p in located), len(catalogue),
    )
    return created
