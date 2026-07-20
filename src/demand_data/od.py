"""Extração da Pesquisa Origem-Destino 2023 (Metrô-SP): zonas (polígonos WGS84 + índice
espacial), população por zona (Σ FE_PESS de residência) e matriz O-D casa→trabalho
(Σ FE_PESS por (ZONA, ZONATRA1)). Só entrega os totais oficiais, não posiciona pontos.
"""

from __future__ import annotations

import collections
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Zones:
    ids: list[int]
    polygons: list  # shapely (Multi)Polygon, WGS84
    _tree: object  # shapely STRtree
    _order: list[int]  # zone id na ordem das geoms do tree

    def zone_of(self, lng: float, lat: float) -> int | None:
        import shapely

        hits = self._tree.query(shapely.points(lng, lat), predicate="within")
        return self._order[int(hits[0])] if len(hits) else None


def load_zones(zones_shp: Path) -> Zones:
    """Lê o shapefile de zonas e reprojeta de Córrego Alegre UTM 23S (do .prj) para WGS84."""
    import shapefile
    from pyproj import CRS, Transformer
    from shapely import STRtree
    from shapely.geometry import shape as shp_shape
    from shapely.ops import transform as shp_transform

    logging.getLogger("shapefile").setLevel(logging.ERROR)  # silencia rings órfãos (inofensivo)
    to_wgs = Transformer.from_crs(
        CRS.from_wkt(zones_shp.with_suffix(".prj").read_text()), "EPSG:4326", always_xy=True
    ).transform
    sf = shapefile.Reader(str(zones_shp), encoding="latin-1")
    flds = [f[0] for f in sf.fields[1:]]

    ids: list[int] = []
    polygons: list = []
    for sr in sf.iterShapeRecords():
        rec = dict(zip(flds, sr.record, strict=False))
        try:
            g = shp_transform(to_wgs, shp_shape(sr.shape.__geo_interface__))
        except Exception:
            continue
        ids.append(int(rec["NumeroZona"]))
        polygons.append(g)
    log.info("zonas OD: %d", len(ids))
    return Zones(ids, polygons, STRtree(polygons), list(ids))


def _as_int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# Motivos de destino, do dicionário oficial da pesquisa (Layout_BD_OD2023):
# 1 Trabalho Indústria, 2 Trabalho Comércio, 3 Trabalho Serviços, 4 Escola/Educação,
# 5 Compras, 6 Médico/Dentista/Saúde, 7 Recreação/Visitas/Lazer, 8 Residência,
# 9 Procurar Emprego, 10 Assuntos Pessoais, 11 Refeição.
WORK, SCHOOL = "work", "school"
SHOPPING, HEALTH, LEISURE, PERSONAL = "shopping", "health", "leisure", "personal"
ACTIVITIES = (WORK, SCHOOL, SHOPPING, HEALTH, LEISURE, PERSONAL)

# atividades sem destino fixo declarado: a pessoa que não trabalha nem estuda é distribuída
# entre elas pelo volume de viagens que a própria pesquisa registra
FLOATING = (SHOPPING, HEALTH, LEISURE, PERSONAL)

_MOTIVE_ACTIVITY = {
    5: SHOPPING, 6: HEALTH, 7: LEISURE, 9: PERSONAL, 10: PERSONAL, 11: SHOPPING,
}


@dataclass(frozen=True, slots=True)
class Survey:
    """O que a pesquisa diz sobre para onde cada morador vai.

    ``population``: moradores por zona.
    ``activity``: {zona: {atividade: pessoas}} — o destino PRINCIPAL de cada morador, que é o
    que o formato do jogo comporta (um destino por pop).
    ``flows``: {atividade: {(origem, destino): peso}} — a distribuição dos destinos.
    ``external``: {atividade: {origem: pessoas}} — quem tem destino fora das zonas da pesquisa.
    """

    population: dict[int, float]
    activity: dict[int, dict[str, float]]
    flows: dict[str, dict[tuple[int, int], float]]
    external: dict[str, dict[int, float]]

    def totals(self) -> dict[str, float]:
        out = {a: 0.0 for a in ACTIVITIES}
        for shares in self.activity.values():
            for name, value in shares.items():
                out[name] += value
        return out


def accumulate_od(records: Iterable[dict], zones: set[int]) -> Survey:
    """Uma passada nos registros da pesquisa -> :class:`Survey`.

    As pessoas são deduplicadas por (ID_DOM, ID_FAM, ID_PESS) e classificadas pelo destino
    que declararam: trabalho, senão escola, senão os motivos não-pendulares. Antes o destino
    de TODA a população era sorteado pela matriz de trabalho, que cobre metade dela — os
    outros 10,8 milhões (estudantes, aposentados, crianças) iam para empregos que a pesquisa
    nunca registrou.

    A distribuição dos motivos não-pendulares vem das viagens (FE_VIA), não das pessoas.
    """
    population: dict[int, float] = collections.defaultdict(float)
    activity: dict[int, dict[str, float]] = collections.defaultdict(
        lambda: dict.fromkeys(ACTIVITIES, 0.0)
    )
    flows: dict[str, dict[tuple[int, int], float]] = {a: collections.defaultdict(float)
                                                      for a in ACTIVITIES}
    external: dict[str, dict[int, float]] = {a: collections.defaultdict(float)
                                             for a in ACTIVITIES}
    floating: dict[int, float] = collections.defaultdict(float)
    seen: set[tuple] = set()
    for r in records:
        home = _as_int(r.get("ZONA"))
        activity_of_trip = _MOTIVE_ACTIVITY.get(_as_int(r.get("MOTIVO_D")))
        if activity_of_trip:
            origin, destination = _as_int(r.get("ZONA_O")), _as_int(r.get("ZONA_D"))
            weight = r.get("FE_VIA") or 0.0
            if weight and origin in zones and destination in zones:
                flows[activity_of_trip][(origin, destination)] += weight

        key = (r.get("ID_DOM"), r.get("ID_FAM"), r.get("ID_PESS"))
        if key in seen:
            continue
        seen.add(key)
        people = r.get("FE_PESS") or 0.0
        if not people or home not in zones:
            continue
        population[home] += people

        # a pesquisa usa 0 para "não trabalha"/"não estuda", e 0 não é ausência para _as_int
        work = _as_int(r.get("ZONATRA1")) or None
        school = _as_int(r.get("ZONA_ESC")) or None
        if work is not None:
            name, target = WORK, work
        elif school is not None:
            name, target = SCHOOL, school
        else:
            floating[home] += people
            continue
        activity[home][name] += people
        if target in zones:
            flows[name][(home, target)] += people
        else:
            external[name][home] += people

    _spread_floating(activity, floating, flows)
    return Survey(
        dict(population),
        {z: dict(shares) for z, shares in activity.items()},
        {a: dict(f) for a, f in flows.items()},
        {a: dict(e) for a, e in external.items()},
    )


def _spread_floating(activity, floating, flows) -> None:
    """Reparte quem não trabalha nem estuda entre compras, saúde, lazer e assuntos pessoais.

    O peso de cada motivo é o volume de viagens que sai da própria zona por ele — quem mora
    num lugar de muita viagem de saúde tem mais chance de ter a saúde como destino principal.
    """
    outgoing = {name: collections.defaultdict(float) for name in FLOATING}
    for name in FLOATING:
        for (origin, _destination), weight in flows[name].items():
            outgoing[name][origin] += weight
    for zone, people in floating.items():
        weights = [outgoing[name].get(zone, 0.0) for name in FLOATING]
        total = sum(weights)
        if total <= 0:  # zona sem viagem registrada por esses motivos
            activity[zone][PERSONAL] += people
            continue
        for name, weight in zip(FLOATING, weights, strict=True):
            activity[zone][name] += people * weight / total


def extract_od(dbf_path: Path, zones: set[int]) -> Survey:
    """Uma passada no microdado DBF -> :func:`accumulate_od`."""
    from dbfread import DBF

    survey = accumulate_od(DBF(str(dbf_path), encoding="latin-1", raw=False), zones)
    totals = survey.totals()
    log.info(
        "população: Σ=%.0f em %d zonas | fora das zonas: %.0f",
        sum(survey.population.values()), len(survey.population),
        sum(sum(e.values()) for e in survey.external.values()),
    )
    log.info("destino principal: " + ", ".join(
        f"{totals[name]:.0f} {name}" for name in ACTIVITIES))
    for name in ACTIVITIES:
        log.info("  matriz %-6s %6d pares (Σ=%.0f)",
                 name, len(survey.flows[name]), sum(survey.flows[name].values()))
    return survey


def demand_by_zone(survey: Survey) -> dict[int, tuple[float, float]]:
    """{zona: (moradores, pessoas que chegam)} — os dois lados que a densidade precisa.

    Como cada morador tem exatamente um destino, os dois lados já somam a população: não há
    mais o reescalonamento que existia quando só a matriz de trabalho era usada.
    """
    arrivals: dict[int, float] = collections.defaultdict(float)
    for name in ACTIVITIES:
        by_origin: dict[int, list[tuple[int, float]]] = collections.defaultdict(list)
        for (origin, destination), weight in survey.flows[name].items():
            by_origin[origin].append((destination, weight))
        for origin, targets in by_origin.items():
            people = survey.activity.get(origin, {}).get(name, 0.0)
            total = sum(weight for _d, weight in targets)
            if people <= 0 or total <= 0:
                continue
            for destination, weight in targets:
                arrivals[destination] += people * weight / total
    zones = survey.population.keys() | arrivals.keys()
    return {z: (survey.population.get(z, 0.0), arrivals.get(z, 0.0)) for z in zones}
