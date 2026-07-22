"""Geração de pops a partir das viagens observadas (:mod:`demand_data.flows`).

Cada viagem já vem com origem e destino resolvidos em endereço; aqui ela só é orientada em
casa↔atividade pelo motivo (:func:`flows.orient`) e vira um pop. As coordenadas são
quantizadas a uma grade fina para deduplicar endereços quase coincidentes — o que também
funde a ida e a volta de um mesmo trajeto no mesmo par de pontos.

Casa e atividade nunca dividem um ponto: cada célula recebe um id por papel (``h`` moradia,
``w`` destino), então um ponto tem sempre um tipo só (ADR-0010). Saída ``(points, pops)`` no
schema do depot / Subway Builder.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable

import numpy as np

from demand_data.config import settings
from demand_data.flows import Flow, orient

log = logging.getLogger(__name__)

_NUDGE = 1e-5  # ~1 m: separa coordenada duplicada (fallback raro)


def _largest_remainder(weights, total: int) -> list[int]:
    """Reparte ``total`` unidades inteiras entre ``weights`` (soma preservada)."""
    w = np.asarray(weights, dtype=float)
    s = w.sum()
    if s <= 0 or total <= 0:
        return [0] * len(w)
    raw = w / s * total
    out = np.floor(raw).astype(int)
    rem = total - int(out.sum())
    if rem > 0:
        for i in np.argsort(raw - out)[::-1][:rem]:
            out[i] += 1
    return out.tolist()


def generate(flows: Iterable[Flow]):
    """Constrói ``(points, pops)`` a partir das viagens orientadas em casa↔atividade.

    A casa de uma viagem é a origem, salvo na volta pra casa (motivo Residência), em que é o
    destino. O ``size`` do pop é o peso de expansão ``trips`` da viagem; ida e volta do mesmo
    trajeto caem no mesmo par de pontos e são fundidas (:func:`merge_identical_commutes`).
    """
    cell = settings.density_cell
    points: dict[str, dict] = {}
    cell_id: dict[tuple[int, str, int, int], str] = {}
    counters: dict[tuple[int, str], int] = defaultdict(int)

    def point_of(zone: int, role: str, coord: tuple[float, float],
                 place_type: str | None = None) -> str:
        gx, gy = round(coord[0] / cell), round(coord[1] / cell)
        key = (zone, role, gx, gy)
        pid = cell_id.get(key)
        if pid is None:
            index = counters[(zone, role)]
            counters[(zone, role)] += 1
            pid = f"z{zone}{role}{index}"
            cell_id[key] = pid
            points[pid] = {"id": pid, "location": [round(coord[0], 6), round(coord[1], 6)],
                           "jobs": 0, "residents": 0, "popIds": []}
        if place_type:
            points[pid].setdefault("type", place_type)
        return pid

    pops: list[dict] = []
    seq = 0
    for flow in flows:
        trip = orient(flow)
        rid = point_of(trip.home_zone, "h", trip.home)
        jid = point_of(trip.activity_zone, "w", trip.activity, trip.place_type)
        seq += 1
        pops.append({"id": f"p{seq:06d}", "size": int(trip.trips),
                     "residenceId": rid, "jobId": jid,
                     "drivingSeconds": 0, "drivingDistance": 0})

    _separate_shared_cells(points)
    pops = merge_identical_commutes(pops)
    pops = split_oversized(pops, settings.max_pop_size)

    kept = aggregate(points, pops)
    res_pts = sum(1 for p in kept if p["residents"] > 0)
    job_pts = sum(1 for p in kept if p["jobs"] > 0)
    log.info(
        "gerados: %d points (%d casa, %d trabalho), %d pops | Σsize=%d",
        len(kept), res_pts, job_pts, len(pops), sum(p["size"] for p in pops),
    )
    return kept, pops


def _separate_shared_cells(points: dict[str, dict]) -> None:
    """Afasta ~1 m os pontos que nascem na mesma coordenada.

    Uma casa e um destino podem cair na mesma célula (têm ids distintos, papel único), mas
    coordenada duplicada quebra o jogo — então a colisão é resolvida no mapa.
    """
    used: set[tuple[float, float]] = set()
    for point in points.values():
        x, y = point["location"]
        while (x, y) in used:
            x = round(x + _NUDGE, 6)
        used.add((x, y))
        point["location"] = [x, y]


def merge_identical_commutes(pops: list[dict]) -> list[dict]:
    """Funde pops que ligam exatamente o mesmo par casa↔atividade — a ida e a volta de um
    trajeto, e viagens repetidas, viram um pop só, somando os ``trips``."""
    merged: dict[tuple, dict] = {}
    for pop in pops:
        key = (pop["residenceId"], pop["jobId"])
        first = merged.get(key)
        if first is None:
            merged[key] = pop
        else:
            first["size"] += pop["size"]
    return list(merged.values())


def split_oversized(pops: list[dict], limit: int) -> list[dict]:
    """Quebra pops acima de ``limit`` pessoas em fatias iguais.

    Um pop é indivisível na simulação: deixar 3 mil pessoas num só faz a rede atender todas
    ou nenhuma.
    """
    if limit <= 0:
        return pops
    out: list[dict] = []
    for pop in pops:
        size = pop["size"]
        if size <= limit:
            out.append(pop)
            continue
        parts = -(-size // limit)
        for index, slice_size in enumerate(_largest_remainder(np.ones(parts), size)):
            piece = dict(pop)
            piece["size"] = int(slice_size)
            if index:
                piece["id"] = f"{pop['id']}_{index}"
            out.append(piece)
    return out


def aggregate(points, pops: list[dict]) -> list[dict]:
    """Recalcula residents/jobs/popIds a partir dos pops e descarta os pontos que ficaram sem
    demanda — é a mesma reconciliação que o ``sanitize`` do depot faz na importação.

    Precisa rodar de novo sempre que um ``jobId`` mudar de dono, como na captura pelos
    equipamentos nomeados.
    """
    by_id = points if isinstance(points, dict) else {p["id"]: p for p in points}
    for p in by_id.values():
        p["popIds"], p["jobs"], p["residents"] = [], 0, 0
    for pop in pops:
        r, j = by_id[pop["residenceId"]], by_id[pop["jobId"]]
        r["residents"] += pop["size"]
        r["popIds"].append(pop["id"])
        j["jobs"] += pop["size"]
        j["popIds"].append(pop["id"])
    return [p for p in by_id.values() if p["jobs"] + p["residents"] > 0]
