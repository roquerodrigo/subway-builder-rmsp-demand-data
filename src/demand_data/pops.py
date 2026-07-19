"""Geração de pops a partir da OD + densidade.

Pipeline ÚNICA de células (:func:`demand_data.density.zone_candidates`) dividida em células
de casa e de trabalho (disjuntas por vocação):
  1. o TAMANHO do pop da zona vem de um orçamento ∝ ÁREA (Σ round(pop / people_per_pop));
  2. a casa é amostrada entre as células de casa da zona ∝ população;
  3. as pessoas da zona são repartidas entre os destinos ∝ matriz O-D e viram pops daquele
     tamanho; a célula de trabalho sai das células de trabalho do destino ∝ densidade de
     emprego, numa alocação única por zona de destino.

Como casa e trabalho vêm de células disjuntas, os pontos nunca coincidem; cada ponto tem
um tipo só (casa = ``residents``, trabalho = ``jobs``). Saída ``(points, pops)`` no schema
do depot / Subway Builder.
"""

from __future__ import annotations

import logging
from collections import defaultdict

import numpy as np

from demand_data.config import settings
from demand_data.density import Candidates

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


def _alloc(size: int, n: int, rng) -> np.ndarray:
    """Reparte ``n`` pops igualmente entre ``size`` pontos e embaralha, devolvendo o índice de
    ponto de cada pop.

    Os pontos já foram sorteados ∝ densidade (ver :mod:`demand_data.density`), então cada um
    representa a mesma fatia de demanda e a repartição aqui é uniforme. A repartição inteira
    só é fiel com ``n`` grande — em ``n`` pequeno ela premia sempre os primeiros índices, e é
    por isso que cada chamada precisa cobrir TODOS os pops daquela fonte de uma vez
    (ver :func:`generate`), nunca um par origem-destino por vez.
    """
    counts = _largest_remainder(np.ones(size), n)
    idx = np.repeat(np.arange(size), counts)
    rng.shuffle(idx)
    return idx


def _plan_destinations(
    dests: list[tuple[int, float]], people: int, target_size: int
) -> list[tuple[int, int, int]]:
    """[(zona de destino, nº de pops, pessoas)] repartindo as PESSOAS da zona ∝ matriz O-D.

    Repartir o nº de POPS ∝ fluxo, como os pops de uma zona têm todos ~o mesmo tamanho,
    arredondava o destino inteiro para cima ou para baixo: os de fluxo menor ficavam zerados
    e as pessoas deles iam parar nos maiores. Aqui o fluxo decide quantas pessoas vão a cada
    destino, e o nº de pops sai do tamanho de pop da zona.
    """
    share = _largest_remainder([f for _, f in dests], people)
    kept = [(wz, ppl) for (wz, _f), ppl in zip(dests, share, strict=True) if ppl > 0]
    floor = settings.min_pop_size
    if floor > 0 and any(ppl >= floor for _, ppl in kept):
        kept = [(wz, ppl) for wz, ppl in kept if ppl >= floor]
    if not kept:
        return []
    # a cauda que não alcança o tamanho mínimo volta para quem ficou, nas mesmas proporções
    final = _largest_remainder([ppl for _, ppl in kept], people)
    return [
        (wz, max(1, round(ppl / target_size)), int(ppl))
        for (wz, _ppl), ppl in zip(kept, final, strict=True)
        if ppl > 0
    ]


def generate(zones, pop: dict[int, float], od: dict[tuple[int, int], float],
             home_cands: Candidates, work_cands: Candidates):
    """Constrói (points, pops): casa dos pontos de casa, trabalho dos pontos de trabalho."""
    rng = np.random.default_rng(settings.seed)

    out_by_home: dict[int, list[tuple[int, float]]] = defaultdict(list)
    for (h, w), f in od.items():
        out_by_home[h].append((w, f))

    points: dict[str, dict] = {}
    used: set[tuple[float, float]] = set()

    def point(prefix: str, z: int, i: int, cands: list[tuple[float, float]]) -> str:
        pid = f"z{z}{prefix}{i}"
        if pid in points:
            return pid
        x, y = round(cands[i][0], 6), round(cands[i][1], 6)
        while (x, y) in used:
            x = round(x + _NUDGE, 6)
        used.add((x, y))
        points[pid] = {"id": pid, "location": [x, y], "jobs": 0, "residents": 0, "popIds": []}
        return pid

    # prefixos: casa h/hf, trabalho w/wf (o sufixo f é o fallback quando a zona não tem
    # pontos daquele tipo). Prefixos distintos mantêm cada ponto de tipo único.
    def home_src(z: int):
        if home_cands.get(z):
            return home_cands[z], "h", z
        if work_cands.get(z):
            return work_cands[z], "hf", z
        return None

    def work_src(z: int):
        if work_cands.get(z):
            return work_cands[z], "w", z
        if home_cands.get(z):
            return home_cands[z], "wf", z
        return None

    ppp = settings.people_per_pop
    elig = [z for z in sorted(pop) if pop[z] > 0 and home_src(z) is not None]
    total = sum(max(1, round(pop[z] / ppp)) for z in elig)
    area = {zid: poly.area * settings.m_per_deg_lat * settings.m_per_deg_lng / 1e6
            for zid, poly in zip(zones.ids, zones.polygons, strict=True)}
    n_by_zone = dict(zip(elig, _largest_remainder([area.get(z, 0.0) for z in elig], total),
                         strict=True))
    log.info("tamanho de pop por zona ∝ área da zona (alvo=%d pops)", total)

    pops: list[dict] = []
    seq = 0
    # A célula de trabalho fica pendente e é sorteada só depois, uma vez por fonte de células:
    # a maioria dos pares origem-destino manda 1 ou 2 pops, e alocar par a par empilharia
    # todos eles na célula de maior peso da zona de destino.
    pending: dict[tuple[int, str], list[int]] = defaultdict(list)
    work_points: dict[tuple[int, str], list[tuple[float, float]]] = {}
    for zone in elig:
        n = n_by_zone[zone]
        if n <= 0:
            continue
        P = round(pop[zone])
        # não subdivide a zona em mais pops do que ela comporta ao tamanho mínimo:
        # funde os pops minúsculos das zonas esparsas em menos pops maiores.
        if settings.min_pop_size > 0:
            n = min(n, max(1, round(P / settings.min_pop_size)))
        hc, hpre, hz = home_src(zone)

        dests = sorted(out_by_home.get(zone, []), key=lambda x: x[1], reverse=True)
        if settings.dest_cap > 0:
            dests = dests[: settings.dest_cap]
        dests = [(wz, f) for wz, f in dests if work_src(wz) is not None] or [(zone, 1.0)]

        plan = _plan_destinations(dests, P, max(1, P // n))
        if not plan:
            continue

        h_idx = _alloc(len(hc), sum(k for _, k, _ in plan), rng)
        i = 0
        for wz, k, people in plan:
            wc, wpre, wzz = work_src(wz) or work_src(zone)
            key = (wzz, wpre)
            work_points[key] = wc
            # k nunca passa de `people` (o tamanho-alvo é ≥ 1), então toda fatia sai positiva
            for sz in _largest_remainder(np.ones(k), people):
                hi = int(h_idx[i])
                i += 1
                rid = point(hpre, hz, hi, hc)
                seq += 1
                pops.append({"id": f"p{seq:06d}", "size": int(sz), "residenceId": rid,
                             "jobId": "", "drivingSeconds": 0, "drivingDistance": 0})
                pending[key].append(len(pops) - 1)

    for key in sorted(pending):
        wzz, wpre = key
        wc = work_points[key]
        idxs = pending[key]
        for i, slot in zip(idxs, _alloc(len(wc), len(idxs), rng), strict=True):
            pops[i]["jobId"] = point(wpre, wzz, int(slot), wc)

    _aggregate(points, pops)
    res_pts = sum(1 for p in points.values() if p["residents"] > 0)
    job_pts = sum(1 for p in points.values() if p["jobs"] > 0)
    log.info(
        "gerados: %d points (%d casa, %d trabalho), %d pops | Σsize=%d",
        len(points), res_pts, job_pts, len(pops), sum(p["size"] for p in pops),
    )
    return list(points.values()), pops


def _aggregate(points: dict[str, dict], pops: list[dict]) -> None:
    """Recalcula residents/jobs/popIds — casa (h/hf) só recebe residents, trabalho (w/wf) jobs."""
    for p in points.values():
        p["popIds"], p["jobs"], p["residents"] = [], 0, 0
    for pop in pops:
        r, j = points[pop["residenceId"]], points[pop["jobId"]]
        r["residents"] += pop["size"]
        r["popIds"].append(pop["id"])
        j["jobs"] += pop["size"]
        j["popIds"].append(pop["id"])
