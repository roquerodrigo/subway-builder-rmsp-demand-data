"""Densidade populacional intra-zona (Censo 2022) realizada nos endereços CNEFE.

A OD é só nível de zona; para posicionar pontos dentro da zona segundo a densidade de
pessoas usamos a população por setor (Censo 2022) distribuída sobre os endereços
residenciais do CNEFE (cada endereço pesa pop_do_setor / nº_endereços_do_setor). Os
endereços são agregados numa grade fina (``density_cell``, ~50 m) por zona, e cada célula
guarda um endereço real sorteado para representá-la.

Os pontos da zona são então SORTEADOS entre essas células ∝ peso, um a cada
``people_per_point`` pessoas. Um ponto por célula desenharia a grade no mapa — pontos
igualmente espaçados e alinhados, que é o que se vê ao olhar de perto uma malha regular.
Sorteando, os pontos se adensam onde há gente e desaparecem onde não há, e a célula serve só
como átomo de posicionamento e espaçamento mínimo.

Cada ponto fica sobre um endereço REAL: o centroide da célula, por ser a média dos dois lados
da via, cairia no meio da rua.
"""

from __future__ import annotations

import heapq
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

Candidates = dict[int, list[tuple[float, float]]]  # {zona: [(lng, lat), ...]}


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


def _chunk_config() -> dict:
    """Configuração dos workers em tipos primitivos.

    Mandar o objeto de configuração exigiria que a classe fosse a mesma nos dois processos,
    o que deixa de valer assim que o módulo é recarregado.
    """
    return {
        "cell": settings.density_cell,
        "bbox": settings.bbox,
        "res_especies": settings.cnefe_res_especies,
        "job_especies": settings.cnefe_job_especies,
        "job_weights": settings.cnefe_job_especie_weight,
    }


def _cell(lng: float, lat: float, config: dict | None = None) -> tuple[int, int]:
    cs = config["cell"] if config else settings.density_cell
    # um grau de longitude é ~8% mais curto que um de latitude nesta latitude, então usar o
    # mesmo passo nos dois eixos daria células retangulares
    cs_lng = cs * settings.m_per_deg_lat / settings.m_per_deg_lng
    b = config["bbox"] if config else settings.bbox
    return (int((lng - b[0]) / cs_lng), int((lat - b[1]) / cs))


_MASK64 = 0xFFFFFFFFFFFFFFFF
_GOLDEN64 = 0x9E3779B97F4A7C15


def _unit_hash(*parts: int) -> float:
    """Uniforme em (0, 1) determinístico — não depende de seed, ordem de leitura nem do
    número de processos, então o resultado é o mesmo em qualquer máquina.

    Mistura splitmix64: entradas vizinhas (células adjacentes) precisam cair longe uma da
    outra, senão o sorteio fica preso a uma faixa do mapa.
    """
    h = _GOLDEN64
    for p in parts:
        h = (h + (p & _MASK64) * _GOLDEN64) & _MASK64
        h = ((h ^ (h >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
        h = ((h ^ (h >> 27)) * 0x94D049BB133111EB) & _MASK64
        h ^= h >> 31
    return ((h >> 11) + 1) / (2**53 + 2)


def _keep_anchor(acc: list[float], lng: float, lat: float) -> None:
    """Sorteia UM endereço real da célula para representá-la. Pegar sempre o mais central
    alinharia os pontos entre células vizinhas, desenhando a grade no mapa."""
    draw = _unit_hash(int(lng * 1e6), int(lat * 1e6))
    if draw > acc[5]:
        acc[5], acc[6], acc[7] = draw, lng, lat


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
    cnefe: str, start: int, end: int, zones_shp: str, config: dict, weights: dict[str, float]
) -> dict[tuple[int, tuple[int, int]], list[float]]:
    """Agrega um intervalo do CNEFE em {(zona, célula): [rw, jw, w*lng, w*lat, w, d², lng, lat]}:
    peso residencial (rw = pop do setor) e de emprego (jw = espécie), o centroide ponderado e o
    endereço real mais central da célula."""
    zones = load_zones(Path(zones_shp))
    res = config["res_especies"]
    job = config["job_especies"]
    job_w = config["job_weights"]
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
            cell = _cell(lng, lat, config)
            key = (zone, cell)
            e = acc.get(key)
            if e is None:
                e = acc[key] = [0.0, 0.0, 0.0, 0.0, 0.0, -1.0, 0.0, 0.0]
            e[0] += rw
            e[1] += jw
            e[2] += w * lng
            e[3] += w * lat
            e[4] += w
            _keep_anchor(e, lng, lat)
    return acc


def _lote_chunk(
    lotes: str, start: int, end: int, zones_shp: str, config: dict
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
            cell = _cell(lng, lat, config)
            key = (zone, cell)
            e = acc.get(key)
            if e is None:
                e = acc[key] = [0.0, 0.0, 0.0, 0.0, 0.0, -1.0, 0.0, 0.0]
            e[0] += rw
            e[1] += jw
            e[2] += area * lng
            e[3] += area * lat
            e[4] += area
            _keep_anchor(e, lng, lat)
    return acc


def _parallel_aggregate(worker, path: Path, zones_shp: Path, *extra):
    """Roda ``worker`` em paralelo sobre intervalos de bytes de ``path``, somando os
    acumuladores por (zona, célula).

    A configuração vai explícita para os workers: eles são outros processos e, se lessem o
    módulo por conta própria, usariam o ``.env`` do disco em vez do que o processo principal
    tem em mãos — grades diferentes de um lado e do outro, silenciosamente.
    """
    n = max(1, (os.cpu_count() or 2) - 1)
    ranges = _line_offsets(path, n)
    acc: dict[tuple[int, tuple[int, int]], list[float]] = defaultdict(
        lambda: [0.0, 0.0, 0.0, 0.0, 0.0, -1.0, 0.0, 0.0]
    )
    with ProcessPoolExecutor(max_workers=n) as ex:
        futs = [
            ex.submit(worker, str(path), s, e, str(zones_shp), _chunk_config(), *extra)
            for s, e in ranges
        ]
        for fut in futs:
            for key, (rw, jw, cl, ct, w, draw, alng, alat) in fut.result().items():
                a = acc[key]
                a[0] += rw
                a[1] += jw
                a[2] += cl
                a[3] += ct
                a[4] += w
                if draw > a[5]:
                    a[5], a[6], a[7] = draw, alng, alat
    return acc


# célula -> [peso_casa, peso_trabalho, w*lng, w*lat, w, sorteio, âncora_lng, âncora_lat]
ZoneCells = dict[tuple[int, int], list[float]]
_HOME, _WORK, _WEIGHT, _DRAW, _LNG, _LAT = 0, 1, 4, 5, 6, 7


def _cells_by_zone(acc) -> dict[int, ZoneCells]:
    """{zona: {célula: acumuladores}}."""
    out: dict[int, ZoneCells] = defaultdict(dict)
    for (zone, cell), vals in acc.items():
        if vals[_WEIGHT] > 0:
            out[zone][cell] = list(vals)
    return out


def _draw_cells(cells: ZoneCells, weight_index: int, k: int, taken=frozenset()) -> list:
    """Sorteia ``k`` células ∝ peso, sem reposição e sem viés (Efraimidis-Spirakis: cada
    célula recebe a chave log(u)/peso e ficam as maiores).

    É o que evita a treliça: usar TODAS as células daria um ponto a cada 50 m, alinhados.
    Sorteando ∝ peso, os pontos se adensam onde há gente e somem onde não há.
    """
    if k <= 0:
        return []
    pool = [(key, vals[weight_index]) for key, vals in cells.items()
            if vals[weight_index] > 0 and vals[_DRAW] >= 0 and key not in taken]
    if len(pool) <= k:
        return [key for key, _w in pool]
    scored = ((math.log(_unit_hash(key[0], key[1], weight_index)) / w, key) for key, w in pool)
    return [key for _score, key in heapq.nlargest(k, scored)]


def _point_count(demand: float) -> int:
    if demand <= 0:
        return 0
    return max(1, round(demand / settings.people_per_point))


def merge_lote_zones(by_zone: dict[int, ZoneCells], lote_zones: dict[int, ZoneCells]):
    """Troca a densidade CNEFE pela dos lotes nas zonas bem cobertas pelo GeoSampa.

    Cobertura baixa é borda da capital: a amostra de lotes ali não representa a zona, então
    fica o CNEFE, que cobre a RMSP inteira. Devolve (zonas trocadas, zonas descartadas).
    """
    used = 0
    for zone, cells in lote_zones.items():
        cnefe_n = len(by_zone.get(zone, []))
        if cnefe_n == 0 or len(cells) >= settings.lote_min_coverage * cnefe_n:
            by_zone[zone] = cells
            used += 1
    return used, len(lote_zones) - used


def select_candidates(
    by_zone: dict[int, ZoneCells], demand: dict[int, tuple[float, float]]
) -> tuple[Candidates, Candidates, int]:
    """(casa, trabalho, zonas sem endereços suficientes) sorteando os pontos de cada zona.

    Trabalho sorteia primeiro e casa evita as células já usadas, para que nenhum ponto
    acumule os dois papéis.
    """
    home_out: dict[int, list[tuple[float, float]]] = defaultdict(list)
    work_out: dict[int, list[tuple[float, float]]] = defaultdict(list)
    short = 0
    for zone in sorted(by_zone):
        res, job = demand.get(zone, (0.0, 0.0))
        cells = by_zone[zone]
        work_keys = _draw_cells(cells, _WORK, _point_count(job))
        home_keys = _draw_cells(cells, _HOME, _point_count(res), taken=set(work_keys))
        if len(work_keys) + len(home_keys) < _point_count(res) + _point_count(job):
            short += 1
        for keys, out in ((work_keys, work_out), (home_keys, home_out)):
            for key in keys:
                vals = cells[key]
                out[zone].append((round(vals[_LNG], 6), round(vals[_LAT], 6)))
    return dict(home_out), dict(work_out), short


def zone_candidates(
    cnefe: Path, zones_shp: Path, weights: dict[str, float], demand: dict[int, tuple[float, float]]
) -> tuple[Candidates, Candidates, dict]:
    """(casa, trabalho, células por zona): pontos sorteados entre os endereços ∝ densidade.

    Cada zona recebe ``demanda / people_per_point`` pontos de cada tipo, em células sorteadas
    ∝ peso residencial (casa) ou de emprego (trabalho) de ``demand``
    ``{zona: (moradores, trabalhadores)}``; cada ponto fica sobre o endereço real que
    representa a sua célula. Os tipos são disjuntos: trabalho escolhe primeiro e casa evita
    as células já usadas. Como o sorteio já é ∝ densidade, os pontos saem com peso igual —
    quem carrega a densidade é a quantidade de pontos, não o tamanho de cada um.
    Densidade híbrida: lotes do GeoSampa nas zonas da capital bem cobertas por ``lotes.csv``,
    CNEFE no resto da RMSP.
    """
    by_zone = _cells_by_zone(_parallel_aggregate(_aggregate_chunk, cnefe, zones_shp, weights))

    if settings.lotes_csv.exists():
        lote_zones = _cells_by_zone(_parallel_aggregate(_lote_chunk, settings.lotes_csv, zones_shp))
        used, dropped = merge_lote_zones(by_zone, lote_zones)
        log.info(
            "densidade híbrida: %d zonas usam lotes GeoSampa, %d no CNEFE "
            "(%d zonas de borda descartadas)",
            used, len(by_zone) - used, dropped,
        )

    home_out, work_out, short = select_candidates(by_zone, demand)
    log.info(
        "pontos sorteados ∝ densidade: %d casa / %d trabalho em %d zonas "
        "(1 a cada %.0f pessoas, grade de %.0f m; %d zonas sem endereços suficientes)",
        sum(len(v) for v in home_out.values()), sum(len(v) for v in work_out.values()),
        len(by_zone), settings.people_per_point,
        settings.density_cell * settings.m_per_deg_lat, short,
    )
    return home_out, work_out, by_zone
