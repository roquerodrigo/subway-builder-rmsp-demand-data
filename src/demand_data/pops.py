"""Geração de pops a partir da OD + densidade.

Pipeline ÚNICA de células (:func:`demand_data.density.zone_candidates`) dividida em células
de casa e de trabalho (disjuntas por vocação):
  1. o nº de pops por zona é ∝ ÁREA (total = Σ round(pop / people_per_pop));
  2. a casa é amostrada entre as células de casa da zona ∝ população;
  3. o trabalho é sorteado pela matriz O-D e amostrado entre as células de trabalho da zona
     de destino ∝ densidade de emprego, numa alocação única por zona de destino.

Como casa e trabalho vêm de células disjuntas, os pontos nunca coincidem; cada ponto tem
um tipo só (casa = ``residents``, trabalho = ``jobs``). Saída ``(points, pops)`` no schema
do depot / Subway Builder.
"""

from __future__ import annotations

import logging
from collections import defaultdict

import numpy as np

from demand_data.config import settings

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


def _probs(cands: dict[int, list[tuple[float, float, float]]]) -> dict[int, np.ndarray | None]:
    """Probabilidade por célula: ``equal_fraction`` uniforme (1/n) + resto ∝ peso — comprime
    as células muito grandes e muito pequenas dentro de uma zona."""
    eq = min(max(settings.equal_fraction, 0.0), 1.0)
    out: dict[int, np.ndarray | None] = {}
    for z, cs in cands.items():
        w = np.array([c[2] for c in cs], dtype=float)
        s, n = w.sum(), len(w)
        out[z] = eq / n + (1 - eq) * w / s if s > 0 else None
    return out


def _alloc(probs: np.ndarray, n: int, rng) -> np.ndarray:
    """Distribui ``n`` pops entre as células de forma DETERMINÍSTICA (round(n·p) por célula),
    depois embaralha para descorrelacionar do destino. Retorna o índice de célula por pop.

    Só é fiel a ``probs`` com ``n`` grande: em ``n`` pequeno o arredondamento sempre premia
    as células de maior peso, então cada chamada precisa cobrir TODOS os pops daquela fonte
    de células de uma vez (ver :func:`generate`), nunca um par origem-destino por vez.
    """
    counts = _largest_remainder(probs, n)
    idx = np.repeat(np.arange(len(probs)), counts)
    rng.shuffle(idx)
    return idx


def generate(zones, pop: dict[int, float], od: dict[tuple[int, int], float],
             home_cands: dict[int, list[tuple[float, float, float]]],
             work_cands: dict[int, list[tuple[float, float, float]]]):
    """Constrói (points, pops): casa de células de casa, trabalho de células de trabalho."""
    rng = np.random.default_rng(settings.seed)
    p_home, p_work = _probs(home_cands), _probs(work_cands)

    out_by_home: dict[int, list[tuple[int, float]]] = defaultdict(list)
    for (h, w), f in od.items():
        out_by_home[h].append((w, f))

    points: dict[str, dict] = {}
    used: set[tuple[float, float]] = set()

    def point(prefix: str, z: int, i: int, cands: list[tuple[float, float, float]]) -> str:
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
    # células daquele tipo). Prefixos distintos mantêm cada ponto de tipo único.
    def home_src(z: int):
        if p_home.get(z) is not None:
            return home_cands[z], p_home[z], "h", z
        if p_work.get(z) is not None:
            return work_cands[z], p_work[z], "hf", z
        return None

    def work_src(z: int):
        if p_work.get(z) is not None:
            return work_cands[z], p_work[z], "w", z
        if p_home.get(z) is not None:
            return home_cands[z], p_home[z], "wf", z
        return None

    ppp = settings.people_per_pop
    elig = [z for z in sorted(pop) if pop[z] > 0 and home_src(z) is not None]
    total = sum(max(1, round(pop[z] / ppp)) for z in elig)
    area = {zid: poly.area * settings.m_per_deg_lat * settings.m_per_deg_lng / 1e6
            for zid, poly in zip(zones.ids, zones.polygons, strict=True)}
    n_by_zone = dict(zip(elig, _largest_remainder([area.get(z, 0.0) for z in elig], total),
                         strict=True))
    log.info("contagem de pops ∝ área da zona (total=%d)", total)

    pops: list[dict] = []
    seq = 0
    # A célula de trabalho fica pendente e é sorteada só depois, uma vez por fonte de células:
    # a maioria dos pares origem-destino manda 1 ou 2 pops, e alocar par a par empilharia
    # todos eles na célula de maior peso da zona de destino.
    pending: dict[tuple[int, str], list[int]] = defaultdict(list)
    work_cells: dict[tuple[int, str], tuple[list, np.ndarray]] = {}
    for zone in elig:
        n = n_by_zone[zone]
        if n <= 0:
            continue
        P = pop[zone]
        # não subdivide a zona em mais pops do que ela comporta ao tamanho mínimo:
        # funde os pops minúsculos das zonas esparsas em menos pops maiores.
        if settings.min_pop_size > 0:
            n = min(n, max(1, round(P / settings.min_pop_size)))
        hc, hp, hpre, hz = home_src(zone)

        dests = sorted(out_by_home.get(zone, []), key=lambda x: x[1], reverse=True)
        if settings.dest_cap > 0:
            dests = dests[: settings.dest_cap]
        dests = dests or [(zone, 1.0)]
        counts = _largest_remainder([f for _, f in dests], n)

        h_idx = _alloc(hp, n, rng)
        sizes = _largest_remainder(np.ones(n), round(P))
        k = 0
        for (wz, _f), c in zip(dests, counts, strict=True):
            if c <= 0:
                continue
            ws = work_src(wz) or work_src(zone)
            if ws is None:
                k += c
                continue
            wc, wp, wpre, wzz = ws
            key = (wzz, wpre)
            work_cells[key] = (wc, wp)
            for _ in range(c):
                sz = int(sizes[k])
                hi = int(h_idx[k])
                k += 1
                if sz <= 0:
                    continue
                rid = point(hpre, hz, hi, hc)
                seq += 1
                pops.append({"id": f"p{seq:06d}", "size": sz, "residenceId": rid,
                             "jobId": "", "drivingSeconds": 0, "drivingDistance": 0})
                pending[key].append(len(pops) - 1)

    for key in sorted(pending):
        wzz, wpre = key
        wc, wp = work_cells[key]
        idxs = pending[key]
        for i, cell in zip(idxs, _alloc(wp, len(idxs), rng), strict=True):
            pops[i]["jobId"] = point(wpre, wzz, int(cell), wc)

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
