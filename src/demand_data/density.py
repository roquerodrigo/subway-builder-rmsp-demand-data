"""Densidade populacional intra-zona (Censo 2022) realizada nos endereços CNEFE.

A OD é só nível de zona; para posicionar pontos dentro da zona segundo a densidade de
pessoas usamos a população por setor (Censo 2022) distribuída sobre os endereços
residenciais do CNEFE (cada endereço pesa pop_do_setor / nº_endereços_do_setor). Os
endereços são agregados numa grade (``density_cell``) por zona; cada célula não vazia vira
um ponto-candidato no seu centroide ponderado, com peso = atividade ali.
"""

from __future__ import annotations

import logging
import os
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from demand_data.config import settings
from demand_data.od import load_zones

log = logging.getLogger(__name__)

# teto de pessoas/endereço (evita setores com pop >> nº de endereços dominarem a densidade)
_MAX_ADDR_WEIGHT = 50.0

Candidates = dict[int, list[tuple[float, float, float]]]  # {zona: [(lng, lat, peso), ...]}


def _setor_pop(path: Path) -> dict[str, float]:
    pop: dict[str, float] = {}
    with open(path, encoding="ascii") as f:
        for line in f:
            s, p = line.rstrip("\n").split(",")
            pop[s] = float(p)
    return pop


def _res_count(cnefe: Path) -> Counter[str]:
    """Nº de endereços residenciais por setor."""
    res = settings.cnefe_res_especies
    count: Counter[str] = Counter()
    with open(cnefe, "rb") as f:
        for raw in f:
            parts = raw.rstrip(b"\n").split(b",")
            if len(parts) != 4:
                continue
            try:
                if int(parts[2]) in res:
                    count[parts[3].decode()] += 1
            except ValueError:
                continue
    return count


def setor_weights(cnefe: Path, setor_pop_csv: Path) -> dict[str, float]:
    """setor -> peso por endereço residencial = pop_setor / nº_endereços (limitado)."""
    pop = _setor_pop(setor_pop_csv)
    count = _res_count(cnefe)
    weights = {s: min(pop[s] / c, _MAX_ADDR_WEIGHT) for s, c in count.items() if s in pop and c > 0}
    log.info("setores: pop=%d cnefe-res=%d casados=%d", len(pop), len(count), len(weights))
    return weights


def _cell(lng: float, lat: float) -> tuple[int, int]:
    cs = settings.density_cell
    b = settings.bbox
    return (int((lng - b[0]) / cs), int((lat - b[1]) / cs))


def _line_offsets(path: Path, n: int) -> list[tuple[int, int]]:
    """Divide o arquivo em ~n intervalos de bytes alinhados a quebras de linha."""
    size = path.stat().st_size
    step = max(1, size // n)
    bounds = [0]
    with open(path, "rb") as f:
        for _ in range(1, n):
            f.seek(bounds[-1] + step)
            f.readline()  # alinha ao próximo \n
            pos = f.tell()
            if pos <= bounds[-1] or pos >= size:
                break
            bounds.append(pos)
    bounds.append(size)
    return list(zip(bounds[:-1], bounds[1:], strict=True))


def _aggregate_chunk(
    cnefe: str, start: int, end: int, zones_shp: str, weights: dict[str, float]
) -> dict[tuple[int, tuple[int, int]], list[float]]:
    """Agrega um intervalo do CNEFE em {(zona, célula): [rw, jw, w*lng, w*lat, w]}: peso
    residencial (rw = pop do setor) e de emprego (jw = espécie), mais o centroide ponderado."""
    zones = load_zones(Path(zones_shp))
    res = settings.cnefe_res_especies
    job = settings.cnefe_job_especies
    job_w = settings.cnefe_job_especie_weight
    acc: dict[tuple[int, tuple[int, int]], list[float]] = {}
    with open(cnefe, "rb") as f:
        f.seek(start)
        while f.tell() < end:
            raw = f.readline()
            if not raw:
                break
            parts = raw.rstrip(b"\n").split(b",")
            if len(parts) != 4:
                continue
            try:
                especie = int(parts[2])
                lng, lat = float(parts[0]), float(parts[1])
            except ValueError:
                continue
            if especie in res:
                w = weights.get(parts[3].decode())
                if not w:
                    continue
                rw, jw = w, 0.0
            elif especie in job:
                rw, jw, w = 0.0, job_w.get(especie, 1.0), job_w.get(especie, 1.0)
            else:
                continue
            zone = zones.zone_of(lng, lat)
            if zone is None:
                continue
            key = (zone, _cell(lng, lat))
            e = acc.get(key)
            if e is None:
                e = acc[key] = [0.0, 0.0, 0.0, 0.0, 0.0]
            e[0] += rw
            e[1] += jw
            e[2] += w * lng
            e[3] += w * lat
            e[4] += w
    return acc


def _lote_chunk(
    lotes: str, start: int, end: int, zones_shp: str
) -> dict[tuple[int, tuple[int, int]], list[float]]:
    """Agrega um intervalo de ``lotes.csv`` (lng,lat,uso,area) em {(zona, célula): [...]} —
    rw = área construída residencial (R), jw = não-residencial (N). Densidade por área."""
    zones = load_zones(Path(zones_shp))
    acc: dict[tuple[int, tuple[int, int]], list[float]] = {}
    with open(lotes, "rb") as f:
        f.seek(start)
        while f.tell() < end:
            raw = f.readline()
            if not raw:
                break
            parts = raw.rstrip(b"\n").split(b",")
            if len(parts) != 4:
                continue
            try:
                lng, lat, area = float(parts[0]), float(parts[1]), float(parts[3])
            except ValueError:
                continue
            zone = zones.zone_of(lng, lat)
            if zone is None:
                continue
            rw = area if parts[2] == b"R" else 0.0
            jw = area if parts[2] == b"N" else 0.0
            key = (zone, _cell(lng, lat))
            e = acc.get(key)
            if e is None:
                e = acc[key] = [0.0, 0.0, 0.0, 0.0, 0.0]
            e[0] += rw
            e[1] += jw
            e[2] += area * lng
            e[3] += area * lat
            e[4] += area
    return acc


def _parallel_aggregate(worker, path: Path, zones_shp: Path, *extra):
    """Roda ``worker`` em paralelo sobre intervalos de bytes de ``path``, somando os
    acumuladores {(zona, célula): [rw, jw, w*lng, w*lat, w]}."""
    n = max(1, (os.cpu_count() or 2) - 1)
    ranges = _line_offsets(path, n)
    acc: dict[tuple[int, tuple[int, int]], list[float]] = defaultdict(
        lambda: [0.0, 0.0, 0.0, 0.0, 0.0]
    )
    with ProcessPoolExecutor(max_workers=n) as ex:
        futs = [ex.submit(worker, str(path), s, e, str(zones_shp), *extra) for s, e in ranges]
        for fut in futs:
            for key, (rw, jw, cl, ct, w) in fut.result().items():
                a = acc[key]
                a[0] += rw
                a[1] += jw
                a[2] += cl
                a[3] += ct
                a[4] += w
    return acc


def _cells_by_zone(acc) -> dict[int, list[tuple[float, float, float, float]]]:
    """{zona: [(rw, jw, lng, lat), ...]} — uma célula por (zona, cell), no centroide ponderado."""
    out: dict[int, list[tuple[float, float, float, float]]] = defaultdict(list)
    for (zone, _c), (rw, jw, cl, ct, w) in acc.items():
        if w > 0:
            out[zone].append((rw, jw, round(cl / w, 6), round(ct / w, 6)))
    return out


def zone_candidates(
    cnefe: Path, zones_shp: Path, weights: dict[str, float]
) -> tuple[Candidates, Candidates]:
    """(casa, trabalho): pipeline ÚNICA de células dividida por vocação.

    Cada célula vira UM ponto no centroide ponderado — casa se relativamente mais
    residencial, trabalho se mais de emprego (jw/Σjw vs rw/Σrw da zona); tipos disjuntos.
    Densidade híbrida: lotes do GeoSampa nas zonas da capital bem cobertas por ``lotes.csv``,
    CNEFE no resto da RMSP.
    """
    by_zone = _cells_by_zone(_parallel_aggregate(_aggregate_chunk, cnefe, zones_shp, weights))

    if settings.lotes_csv.exists():
        lote_zones = _cells_by_zone(_parallel_aggregate(_lote_chunk, settings.lotes_csv, zones_shp))
        used = 0
        for zone, cells in lote_zones.items():
            # só troca pro lote se a zona for bem coberta (senão é borda da capital com amostra
            # não-representativa → mantém CNEFE, que cobre a RMSP inteira)
            cnefe_n = len(by_zone.get(zone, []))
            if cnefe_n == 0 or len(cells) >= settings.lote_min_coverage * cnefe_n:
                by_zone[zone] = cells
                used += 1
        log.info(
            "densidade híbrida: %d zonas usam lotes GeoSampa, %d no CNEFE "
            "(%d zonas de borda descartadas)",
            used, len(by_zone) - used, len(lote_zones) - used,
        )

    home_out: dict[int, list[tuple[float, float, float]]] = defaultdict(list)
    work_out: dict[int, list[tuple[float, float, float]]] = defaultdict(list)
    for zone, cells in by_zone.items():
        zres = sum(c[0] for c in cells) or 1.0
        zjob = sum(c[1] for c in cells) or 1.0
        for rw, jw, lng, lat in cells:
            if jw / zjob > rw / zres:  # relativamente mais emprego → trabalho
                work_out[zone].append((lng, lat, jw))
            else:
                home_out[zone].append((lng, lat, rw))
    log.info(
        "pipeline única: %d células -> %d casa / %d trabalho",
        sum(len(c) for c in by_zone.values()),
        sum(len(v) for v in home_out.values()), sum(len(v) for v in work_out.values()),
    )
    return dict(home_out), dict(work_out)
