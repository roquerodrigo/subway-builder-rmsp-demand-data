"""Equipamentos nomeados: aeroportos, campi, estádios, shoppings, hospitais, parques.

A pesquisa diz quantas pessoas vão para cada ZONA, não para qual equipamento. Aqui os
destinos genéricos de uma zona são reetiquetados como o equipamento que os atrai: o pop que
ia para "algum ponto de trabalho da zona de Congonhas" passa a ir para o próprio aeroporto,
com o nome e o tipo que o Subway Builder mostra.

Nada aqui é estimado à mão. As coordenadas e os nomes vêm do **OpenStreetMap** (com o
``osm_id`` de cada um no arquivo, para conferência), e o porte de cada equipamento é medido
pela **atividade não-residencial ao redor dele** — área construída do IPTU na capital,
estabelecimentos do CNEFE no resto —, a mesma medida que posiciona os pontos de demanda.
Um equipamento captura a fatia da zona equivalente ao seu peso ali.

Isso captura demanda em vez de criar: a origem de cada viagem continua sendo a da matriz
O-D e Σ tamanho dos pops segue igual à população. O depot resolve o mesmo problema com um
modelo de gravidade e capacidades declaradas à mão (``add_points``), necessário lá porque a
fonte americana só traz casa-trabalho.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from demand_data.config import settings
from demand_data.od import HEALTH, LEISURE, PERSONAL, SCHOOL, SHOPPING, WORK
from demand_data.pops import ACTIVITY_FIELD

log = logging.getLogger(__name__)

_ZONE_POINT = re.compile(r"^z(\d+)(hf|wf|h|w)\d+$")
_WORK_PREFIXES = frozenset({"w", "wf"})
_WORK_WEIGHT = 1  # índice do peso de emprego nos acumuladores de densidade
# zona do equipamento: serve à geração, não ao jogo, e sai na escrita
ZONE_FIELD = "_zone"

# quem cada tipo de equipamento atende. O motivo declarado na pesquisa manda: ninguém vai ao
# zoológico por consulta médica. Trabalho entra em todos — gente trabalha em qualquer um.
ACCEPTS: dict[str, frozenset[str]] = {
    "UNI": frozenset({SCHOOL, WORK}),
    "SCH": frozenset({SCHOOL, WORK}),
    "HOS": frozenset({HEALTH, WORK}),
    "SHP": frozenset({SHOPPING, WORK}),
    "PRK": frozenset({LEISURE, WORK}),
    "ZOO": frozenset({LEISURE, WORK}),
    "SPO": frozenset({LEISURE, WORK}),
    "CNV": frozenset({LEISURE, PERSONAL, WORK}),
    "AIR": frozenset({PERSONAL, WORK}),
    "EXT": frozenset({PERSONAL, WORK}),
}

# tipo de equipamento que cada motivo exige na zona de destino, e como chamá-lo quando o
# OpenStreetMap não nomeia nenhum ali
REQUIRED_BY_ACTIVITY: dict[str, tuple[str, str]] = {
    SCHOOL: ("SCH", "Ensino"),
    HEALTH: ("HOS", "Saúde"),
    SHOPPING: ("SHP", "Comércio"),
    LEISURE: ("PRK", "Lazer"),
}


def _identifier(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in name).strip("_")[:40]


def load(path: Path | None = None) -> list[dict]:
    """Lê ``pois.csv`` (``lng,lat,tipo,osm_id,extensão…,nome``) escrito por ``sources``."""
    path = path or settings.pois_csv
    if not path.exists():
        log.warning("sem %s — rode `sources` para baixar os equipamentos", path.name)
        return []
    found = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split(",", 8)
            if len(parts) != 9:
                continue
            try:
                lng, lat = float(parts[0]), float(parts[1])
                extent = [float(v) for v in parts[4:8]]
            except ValueError:
                continue
            found.append({"location": [lng, lat], "type": parts[2], "osm_id": parts[3],
                          "extent": extent, "name": parts[8]})
    return found


def locate(zones, catalogue: list[dict]) -> list[dict]:
    """Descobre a zona de cada equipamento; descarta os que caem fora do recorte."""
    located = []
    for poi in catalogue:
        zone = zones.zone_of(poi["location"][0], poi["location"][1])
        if zone is None:
            continue
        located.append({**poi, "zone": zone,
                        "id": f"{poi['type']}_{_identifier(poi['name'])}"})
    return located


def footprint(poi: dict) -> tuple[float, float, float, float]:
    """Retângulo em que o equipamento é medido: a extensão do OSM, com uma folga mínima.

    Medir num raio fixo fazia uma praça de 40 m herdar os prédios de todo o quarteirão.
    """
    margin = settings.poi_radius_m
    lng, lat = poi["location"]
    dx = margin / settings.m_per_deg_lng
    dy = margin / settings.m_per_deg_lat
    min_lng, min_lat, max_lng, max_lat = poi.get("extent") or [0.0, 0.0, 0.0, 0.0]
    if max_lng <= min_lng or max_lat <= min_lat:  # nó solto, sem geometria no OSM
        return (lng - dx, lat - dy, lng + dx, lat + dy)
    return (min_lng - dx, min_lat - dy, max_lng + dx, max_lat + dy)


def measure(located: list[dict], cells_by_zone: dict) -> None:
    """Mede o peso de cada equipamento: atividade não-residencial dentro dele / a da zona.

    É o que substitui uma capacidade declarada — o porte sai da mesma fonte que decide onde
    ficam os pontos de trabalho.
    """
    for poi in located:
        cells = cells_by_zone.get(poi["zone"], {})
        min_lng, min_lat, max_lng, max_lat = footprint(poi)
        inside = total = 0.0
        for values in cells.values():
            weight = values[_WORK_WEIGHT]
            if weight <= 0:
                continue
            total += weight
            if min_lng <= values[6] <= max_lng and min_lat <= values[7] <= max_lat:
                inside += weight
        poi["share"] = inside / total if total > 0 else 0.0


def capture(points: list[dict], pops: list[dict], zones, cells_by_zone: dict,
            catalogue: list[dict] | None = None) -> list[dict]:
    """Reetiqueta parte dos destinos genéricos como os equipamentos que os atraem.

    Cada equipamento leva a fatia da demanda da zona equivalente ao seu peso ali, tomando um
    pedaço proporcional de CADA pop que chega (e dividindo o pop quando preciso), até o teto
    ``poi_max_zone_share``. Tomar pops inteiros dos maiores para os menores fazia o
    equipamento estourar o próprio porte, esvaziar a zona e herdar poucas origens.
    """
    located = locate(zones, catalogue if catalogue is not None else load())
    if not located:
        return []
    measure(located, cells_by_zone)
    located.sort(key=lambda p: -p["share"])

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
    taken: dict[tuple[int, str], float] = {}
    created, added, skipped = [], [], 0
    for poi in located:
        accepts = ACCEPTS.get(poi["type"], frozenset())
        # o motivo declarado manda: um pop de compras não vira visita ao hospital
        pool = [p for p in arriving.get(poi["zone"], [])
                if zone_of_point.get(p["jobId"]) == poi["zone"] and p["size"] > 0
                and p.get(ACTIVITY_FIELD) in accepts]
        available = sum(p["size"] for p in pool)
        key = (poi["zone"], poi["type"])
        room = max_share - taken.get(key, 0.0)
        target = int(available * min(poi["share"], max(room, 0.0)))
        if target < settings.min_pop_size:
            skipped += 1
            continue
        # fatiar todo pop que chega renderia dezenas de milhares de fatias minúsculas; os
        # maiores bastam para o equipamento herdar origens variadas
        pool.sort(key=lambda p: -p["size"])
        pool = pool[: max(1, target // settings.min_pop_size)]
        taken[key] = taken.get(key, 0.0) + poi["share"]

        point = {"id": poi["id"], "location": poi["location"], "name": poi["name"],
                 "type": poi["type"], "jobs": 0, "residents": 0, "popIds": [],
                 ZONE_FIELD: poi["zone"]}
        if poi.get("osm_id"):
            point["osmId"] = poi["osm_id"]
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
    named = sum(1 for p in created if p.get("osmId"))
    log.info(
        "equipamentos: %d do OpenStreetMap + %d genéricos (motivo sem equipamento mapeado na "
        "zona) = %d pontos, %d pessoas; %d sem peso suficiente; %d pops fatiados",
        named, len(created) - named, len(created),
        sum(p.get("captured", 0) for p in located), skipped, len(added),
    )
    return created


def classify(points: list[dict], pops: list[dict], cells_by_zone: dict) -> dict[str, int]:
    """Marca cada ponto de destino com o motivo que mais o alimenta.

    A pesquisa diz que quem sai da zona A para a B por saúde vai a um destino de saúde em B.
    Em vez de inventar um equipamento para cada par (zona, motivo) — o que criaria milhares
    de pontos-agregadores e desmancharia a distribuição espacial —, o ponto que recebe essas
    viagens É o destino de saúde da zona.

    Depois disso, toda zona que recebe viagens de um motivo tem ao menos um ponto do tipo
    correspondente. Devolve a contagem por tipo.
    """
    by_point: dict[str, dict[str, int]] = {}
    for pop in pops:
        activity = pop.get(ACTIVITY_FIELD)
        if activity in REQUIRED_BY_ACTIVITY:
            by_point.setdefault(pop["jobId"], {})
            by_point[pop["jobId"]][activity] = (
                by_point[pop["jobId"]].get(activity, 0) + pop["size"]
            )

    points_by_id = {p["id"]: p for p in points}
    counts: dict[str, int] = {}
    candidates: dict[tuple[int, str], list[tuple[int, str]]] = {}
    for point_id, activities in by_point.items():
        point = points_by_id.get(point_id)
        if point is None or point.get("name"):  # equipamento nomeado já tem tipo
            continue
        activity, people = max(activities.items(), key=lambda kv: kv[1])
        type_code = REQUIRED_BY_ACTIVITY[activity][0]
        point["type"] = type_code
        counts[type_code] = counts.get(type_code, 0) + 1

        match = _ZONE_POINT.match(point_id)
        if match:
            zone = int(match.group(1))
            for name, size in activities.items():
                candidates.setdefault((zone, name), []).append((size, point_id))

    # nenhuma zona pode receber viagens de um motivo sem um destino que o atenda — e quem
    # atende não é só o tipo "canônico": uma universidade também recebe viagens de escola
    served = _served_by_zone(points)
    reassigned: set[str] = set()
    forced = 0
    for (zone, activity), pool in sorted(candidates.items()):
        if activity in served.get(zone, set()):
            continue
        # o ponto escolhido não pode ser o que sustenta outro motivo na zona: retipá-lo
        # deixaria esse outro motivo a descoberto. Sem tipo primeiro, depois o maior.
        ranked = sorted(pool, key=lambda item: (points_by_id[item[1]].get("type") is not None,
                                                -item[0]))
        chosen = next((pid for _size, pid in ranked
                       if pid not in reassigned and _spare(points_by_id[pid], zone, points)),
                      None)
        type_code = REQUIRED_BY_ACTIVITY[activity][0]
        if chosen is None:
            # zona onde um único ponto serve dois motivos: só criando um destino a mais
            created = _new_destination(points, pops, zone, activity, type_code, cells_by_zone)
            if created is None:
                continue
            points_by_id[created["id"]] = created
            served.setdefault(zone, set()).update(ACCEPTS.get(type_code, frozenset()))
            counts[type_code] = counts.get(type_code, 0) + 1
            forced += 1
            continue
        points_by_id[chosen]["type"] = type_code
        reassigned.add(chosen)
        served.setdefault(zone, set()).update(ACCEPTS.get(type_code, frozenset()))
        counts[type_code] = counts.get(type_code, 0) + 1
        forced += 1

    spread = _spread_crowded(points, pops, counts)
    missing = _uncovered(points, pops)
    log.info("destinos tipados pelo motivo da viagem: %s "
             "(%d ajustados para cobrir a zona, %d para aliviar concentração)",
             ", ".join(f"{n} {c}" for c, n in sorted(counts.items())), forced, spread)
    if missing:
        log.warning("zonas recebendo um motivo sem destino do tipo: %d", missing)
    return counts


def _spread_crowded(points: list[dict], pops: list[dict], counts: dict[str, int]) -> int:
    """Reparte a demanda de um motivo quando um único destino concentra demais.

    Um destino de saúde com dezenas de milhares de pessoas vira um poço que a rede atende ou
    não atende em bloco. Os pops excedentes passam para outros pontos da mesma zona, que
    ganham o mesmo tipo — a zona fica com vários destinos daquele motivo em vez de um só.
    """
    ceiling = settings.poi_spread_above
    if ceiling <= 0:
        return 0

    zone_of, by_zone = {}, {}
    for point in points:
        match = _ZONE_POINT.match(point["id"])
        if match and not point.get("name"):
            zone = int(match.group(1))
            zone_of[point["id"]] = zone
            # só destinos: mandar demanda para um ponto de moradia faria dele casa e
            # trabalho ao mesmo tempo
            if match.group(2) in _WORK_PREFIXES:
                by_zone.setdefault(zone, []).append(point)

    crowded: dict[tuple[str, str], list[dict]] = {}
    for pop in pops:
        activity = pop.get(ACTIVITY_FIELD)
        if activity in REQUIRED_BY_ACTIVITY and pop["jobId"] in zone_of:
            crowded.setdefault((pop["jobId"], activity), []).append(pop)

    points_by_id = {p["id"]: p for p in points}
    promoted = 0
    for (point_id, activity), group in sorted(crowded.items()):
        people = sum(p["size"] for p in group)
        type_code = REQUIRED_BY_ACTIVITY[activity][0]
        if people <= ceiling or points_by_id[point_id].get("type") != type_code:
            continue
        zone = zone_of[point_id]
        wanted = min(int(people / ceiling), len(group) - 1)
        helpers = [other for other in by_zone.get(zone, [])
                   if other["id"] != point_id
                   and (other.get("type") in (None, type_code)
                        or _spare(other, zone, points))]
        helpers = helpers[:wanted]
        if not helpers:
            continue
        # os maiores pops ficam onde estão; os menores migram, um por destino novo
        group.sort(key=lambda p: -p["size"])
        for index, pop in enumerate(group[1:], start=0):
            helper = helpers[index % len(helpers)]
            if sum(p["size"] for p in group if p["jobId"] == point_id) <= ceiling:
                break
            pop["jobId"] = helper["id"]
            if helper.get("type") != type_code:
                helper["type"] = type_code
                counts[type_code] = counts.get(type_code, 0) + 1
                promoted += 1
    return promoted


def _new_destination(points, pops, zone: int, activity: str, type_code: str, cells_by_zone):
    """Cria o destino do motivo na maior concentração de atividade da zona e leva para lá os
    pops daquele motivo que chegam ali."""
    anchor = _busiest(cells_by_zone.get(zone, {}))
    if anchor is None:
        return None
    zone_points = set()
    for point in points:
        match = _ZONE_POINT.match(point["id"])
        if match and int(match.group(1)) == zone:
            zone_points.add(point["id"])
    moving = [p for p in pops
              if p["jobId"] in zone_points and p.get(ACTIVITY_FIELD) == activity]
    if not moving:
        return None
    point = {"id": f"{type_code}_z{zone}", "location": [round(anchor[0], 6),
             round(anchor[1], 6)], "type": type_code, "jobs": 0, "residents": 0,
             "popIds": [], ZONE_FIELD: zone}
    for pop in moving:
        pop["jobId"] = point["id"]
    points.append(point)
    return point


def _spare(point: dict, zone: int, points: list[dict]) -> bool:
    """O tipo atual do ponto pode ser trocado sem descobrir o motivo que ele atendia?"""
    current = point.get("type")
    if not current:
        return True
    same = 0
    for other in points:
        if other is point or other.get("type") != current:
            continue
        match = _ZONE_POINT.match(other["id"])
        other_zone = other.get(ZONE_FIELD) or (int(match.group(1)) if match else None)
        if other_zone == zone:
            same += 1
    return same > 0


def _served_by_zone(points: list[dict]) -> dict[int, set]:
    """{zona: motivos que os destinos dali atendem}, contando os equipamentos nomeados."""
    served: dict[int, set] = {}
    for point in points:
        zone = point.get(ZONE_FIELD)
        if zone is None:
            match = _ZONE_POINT.match(point["id"])
            zone = int(match.group(1)) if match else None
        if zone is None or not point.get("type"):
            continue
        served.setdefault(zone, set()).update(ACCEPTS.get(point["type"], frozenset()))
    return served


def _uncovered(points: list[dict], pops: list[dict]) -> int:
    """Quantos pares (zona, motivo) recebem viagens sem um destino que os atenda."""
    served = _served_by_zone(points)
    zone_of = {}
    for point in points:
        match = _ZONE_POINT.match(point["id"])
        if match:
            zone_of[point["id"]] = int(match.group(1))
    needed = set()
    for pop in pops:
        activity = pop.get(ACTIVITY_FIELD)
        zone = zone_of.get(pop["jobId"])
        if activity in REQUIRED_BY_ACTIVITY and zone is not None:
            needed.add((zone, activity))
    gaps = [(zone, activity) for zone, activity in needed
            if activity not in served.get(zone, set())]
    if gaps:
        log.warning("  sem destino do tipo: %s", sorted(gaps)[:6])
    return len(gaps)


def _busiest(cells: dict):
    """Endereço da célula de maior atividade não-residencial da zona."""
    best, best_weight = None, 0.0
    for values in cells.values():
        if values[_WORK_WEIGHT] > best_weight:
            best, best_weight = (values[6], values[7]), values[_WORK_WEIGHT]
    return best


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
