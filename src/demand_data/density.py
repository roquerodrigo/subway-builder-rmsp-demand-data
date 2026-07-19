"""Densidade populacional intra-zona (Censo 2022) realizada nos endereços CNEFE.

A OD é só nível de zona; para posicionar pontos dentro da zona segundo a densidade de
pessoas usamos a população por setor (Censo 2022) distribuída sobre os endereços
residenciais do CNEFE (cada endereço pesa pop_do_setor / nº_endereços_do_setor). Os
endereços são agregados numa grade por zona; cada célula não vazia vira um ponto-candidato
com peso = atividade ali, posicionado no endereço REAL mais próximo do seu centroide
ponderado — o centroide em si costuma cair no meio da rua, por ser a média dos dois lados
da via.

A grade é adaptativa: a agregação roda em ``base_cell`` e cada zona funde as células de volta
até a grade padrão ``density_cell``, saindo dela só quando a demanda por ponto foge da faixa
[``min_demand_per_point``, ``max_demand_per_point``] — afina até ``density_cell_min`` onde a
demanda é concentrada (zonas centrais, muito emprego em pouca área) e engrossa até
``density_cell_max`` onde é rarefeita (zonas periféricas grandes, que senão virariam muitos
pontos minúsculos).
"""

from __future__ import annotations

import logging
import math
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
    cs = settings.base_cell
    b = settings.bbox
    return (int((lng - b[0]) / cs), int((lat - b[1]) / cs))


def _off_center(lng: float, lat: float, cell: tuple[int, int]) -> float:
    """Distância² do endereço ao centro geométrico da sua célula (em graus, só p/ comparar)."""
    cs = settings.base_cell
    b = settings.bbox
    return (lng - (b[0] + (cell[0] + 0.5) * cs)) ** 2 + (lat - (b[1] + (cell[1] + 0.5) * cs)) ** 2


def _keep_anchor(acc: list[float], lng: float, lat: float, cell: tuple[int, int]) -> None:
    """Guarda na célula o endereço REAL mais central dela — o ponto é ancorado nele em vez de
    no centroide ponderado, que costuma cair no meio da rua (média dos dois lados da via)."""
    d2 = _off_center(lng, lat, cell)
    if d2 < acc[5]:
        acc[5], acc[6], acc[7] = d2, lng, lat


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
    """Agrega um intervalo do CNEFE em {(zona, célula): [rw, jw, w*lng, w*lat, w, d², lng, lat]}:
    peso residencial (rw = pop do setor) e de emprego (jw = espécie), o centroide ponderado e o
    endereço real mais central da célula."""
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
            cell = _cell(lng, lat)
            key = (zone, cell)
            e = acc.get(key)
            if e is None:
                e = acc[key] = [0.0, 0.0, 0.0, 0.0, 0.0, float("inf"), 0.0, 0.0]
            e[0] += rw
            e[1] += jw
            e[2] += w * lng
            e[3] += w * lat
            e[4] += w
            _keep_anchor(e, lng, lat, cell)
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
            cell = _cell(lng, lat)
            key = (zone, cell)
            e = acc.get(key)
            if e is None:
                e = acc[key] = [0.0, 0.0, 0.0, 0.0, 0.0, float("inf"), 0.0, 0.0]
            e[0] += rw
            e[1] += jw
            e[2] += area * lng
            e[3] += area * lat
            e[4] += area
            _keep_anchor(e, lng, lat, cell)
    return acc


def _parallel_aggregate(worker, path: Path, zones_shp: Path, *extra):
    """Roda ``worker`` em paralelo sobre intervalos de bytes de ``path``, somando os
    acumuladores {(zona, célula): [rw, jw, w*lng, w*lat, w]}."""
    n = max(1, (os.cpu_count() or 2) - 1)
    ranges = _line_offsets(path, n)
    acc: dict[tuple[int, tuple[int, int]], list[float]] = defaultdict(
        lambda: [0.0, 0.0, 0.0, 0.0, 0.0, float("inf"), 0.0, 0.0]
    )
    with ProcessPoolExecutor(max_workers=n) as ex:
        futs = [ex.submit(worker, str(path), s, e, str(zones_shp), *extra) for s, e in ranges]
        for fut in futs:
            for key, (rw, jw, cl, ct, w, d2, alng, alat) in fut.result().items():
                a = acc[key]
                a[0] += rw
                a[1] += jw
                a[2] += cl
                a[3] += ct
                a[4] += w
                if d2 < a[5]:
                    a[5], a[6], a[7] = d2, alng, alat
    return acc


# célula da grade fina -> [rw, jw, w*lng, w*lat, w, d², âncora_lng, âncora_lat]
ZoneCells = dict[tuple[int, int], list[float]]
# célula já fundida -> ([rw, jw, w*lng, w*lat, w], âncoras das células finas que a compõem)
MergedCells = dict[tuple[int, int], tuple[list[float], list[tuple[float, float]]]]


def _cells_by_zone(acc) -> dict[int, ZoneCells]:
    """{zona: {célula: acumuladores}} na grade fina."""
    out: dict[int, ZoneCells] = defaultdict(dict)
    for (zone, cell), vals in acc.items():
        if vals[4] > 0:
            out[zone][cell] = list(vals)
    return out


def _coarsen(cells: ZoneCells, factor: int) -> MergedCells:
    """Funde a grade fina em células ``factor`` vezes maiores (alinhadas à grade global),
    juntando as âncoras das células de origem."""
    out: MergedCells = {}
    for (cx, cy), vals in cells.items():
        key = (cx // factor, cy // factor) if factor > 1 else (cx, cy)
        entry = out.get(key)
        if entry is None:
            entry = out[key] = ([0.0] * 5, [])
        acc, anchors = entry
        for i in range(5):
            acc[i] += vals[i]
        if vals[5] != float("inf"):
            anchors.append((vals[6], vals[7]))
    return out


def _nearest_anchor(anchors, lng: float, lat: float) -> tuple[float, float]:
    """Endereço real mais próximo do centroide ponderado da célula."""
    if len(anchors) == 1:
        return anchors[0]
    kx, ky = settings.m_per_deg_lng, settings.m_per_deg_lat
    return min(anchors, key=lambda a: ((a[0] - lng) * kx) ** 2 + ((a[1] - lat) * ky) ** 2)


def _grid_scale() -> tuple[list[int], int, list[int]]:
    """(mais finas, padrão, mais grossas) em múltiplos de ``base_cell``, dobrando a partir do
    padrão até os limites ``density_cell_min``/``density_cell_max``."""
    base = settings.base_cell
    default = max(1, round(settings.density_cell / base))
    coarsest = max(default, round(settings.density_cell_max / base))

    finer, factor = [], default
    while factor > 1:
        factor = max(1, factor // 2)
        finer.append(factor)
    coarser, factor = [], default
    while factor < coarsest:
        factor = min(coarsest, factor * 2)
        coarser.append(factor)
    return finer, default, coarser


def _resolve(cells: ZoneCells, scale, n_min: int, n_max: int) -> tuple[MergedCells, int]:
    """Grade da zona: parte do padrão e só sai dele para caber na faixa de demanda por ponto
    — afina enquanto tiver menos de ``n_min`` células, engrossa enquanto tiver mais de
    ``n_max``. O teto (``n_min``) manda: nunca engrossa a ponto de violá-lo."""
    finer, default, coarser = scale
    merged, chosen = _coarsen(cells, default), default

    if len(merged) < n_min:
        for factor in finer:
            merged, chosen = _coarsen(cells, factor), factor
            if len(merged) >= n_min:
                break
    elif len(merged) > n_max:
        for factor in coarser:
            candidate = _coarsen(cells, factor)
            if len(candidate) < n_min:
                break
            merged, chosen = candidate, factor
            if len(candidate) <= n_max:
                break
    return merged, chosen


def _work_count(cells: list[tuple[float, float, float, float]], res: float, job: float) -> int:
    """Quantas células da zona vão para trabalho: ∝ à demanda (moradores × trabalhadores),
    limitado pelas células que têm atividade daquele tipo.

    A vocação célula a célula (jw/Σjw vs rw/Σrw) reparte bem entre células equivalentes, mas
    ignora o VOLUME: uma zona de escritórios com poucos moradores gastava metade das células
    escassas com moradia.
    """
    n = len(cells)
    job_capable = sum(1 for c in cells if c[1] > 0)
    res_capable = sum(1 for c in cells if c[0] > 0)
    total = res + job
    if total <= 0:
        return sum(1 for c in cells if c[1] > c[0])
    k = round(n * job / total)
    k = max(n - res_capable, min(k, job_capable))
    if job > 0 and job_capable > 0:
        k = max(k, 1)
    if res > 0 and res_capable > 0:
        k = min(k, n - 1)
    return max(0, min(k, n))


def zone_candidates(
    cnefe: Path, zones_shp: Path, weights: dict[str, float], demand: dict[int, tuple[float, float]]
) -> tuple[Candidates, Candidates]:
    """(casa, trabalho): pipeline ÚNICA de células dividida por vocação.

    Cada célula vira UM ponto no centroide ponderado. A resolução é por zona: parte da grade
    padrão e afina ou engrossa até que a demanda por ponto de ``demand``
    ``{zona: (moradores, trabalhadores)}`` caia na faixa configurada. As células vão para
    trabalho ou casa ∝ à demanda da zona, as de vocação mais forte primeiro; tipos disjuntos.
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
    scale = _grid_scale()
    default_factor = scale[1]
    cap, floor = settings.max_demand_per_point, settings.min_demand_per_point
    finer_zones = coarser_zones = 0
    for zone in sorted(by_zone):
        res, job = demand.get(zone, (0.0, 0.0))
        total = res + job
        n_min = max(1, math.ceil(total / cap)) if cap > 0 else 1
        n_max = max(n_min, int(total // floor)) if floor > 0 else len(by_zone[zone])
        cells_map, factor = _resolve(by_zone[zone], scale, n_min, n_max)
        finer_zones += factor < default_factor
        coarser_zones += factor > default_factor

        cells = []
        for _k, (acc, anchors) in sorted(cells_map.items()):
            if not anchors:
                continue
            lng, lat = _nearest_anchor(anchors, acc[2] / acc[4], acc[3] / acc[4])
            cells.append((acc[0], acc[1], round(lng, 6), round(lat, 6)))
        zres = sum(c[0] for c in cells) or 1.0
        zjob = sum(c[1] for c in cells) or 1.0
        ranked = sorted(cells, key=lambda c: (c[1] / zjob - c[0] / zres, c[2], c[3]), reverse=True)
        k = _work_count(ranked, res, job)
        for _rw, jw, lng, lat in ranked[:k]:
            work_out[zone].append((lng, lat, jw))
        for rw, _jw, lng, lat in ranked[k:]:
            home_out[zone].append((lng, lat, rw))

    log.info(
        "células: %d casa / %d trabalho em %d zonas | grade %.0f m no padrão, "
        "%d zonas afinadas (até %.0f m), %d condensadas (até %.0f m)",
        sum(len(v) for v in home_out.values()), sum(len(v) for v in work_out.values()),
        len(by_zone), settings.density_cell * settings.m_per_deg_lat,
        finer_zones, settings.density_cell_min * settings.m_per_deg_lat,
        coarser_zones, settings.density_cell_max * settings.m_per_deg_lat,
    )
    return dict(home_out), dict(work_out)
