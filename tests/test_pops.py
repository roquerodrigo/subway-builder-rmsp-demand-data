"""Geração dos pops: repartição da demanda, invariantes e as regressões de concentração."""

from __future__ import annotations

import collections
import re

import numpy as np
import pytest
from shapely.geometry import Polygon
from tests.conftest import BASE_LAT, BASE_LNG

from demand_data import pops
from demand_data.od import Zones

POINT_ID = re.compile(r"^z(\d+)(hf|wf|h|w)(\d+)$")


def make_zones(*zone_ids):
    polygons = [
        Polygon([
            (BASE_LNG + i * 0.05, BASE_LAT),
            (BASE_LNG + i * 0.05 + 0.04, BASE_LAT),
            (BASE_LNG + i * 0.05 + 0.04, BASE_LAT + 0.04),
            (BASE_LNG + i * 0.05, BASE_LAT + 0.04),
        ])
        for i, _z in enumerate(zone_ids)
    ]
    return Zones(list(zone_ids), polygons, None, list(zone_ids))


def spread(zone: int, count: int):
    return [(round(BASE_LNG + zone + i * 0.001, 6), round(BASE_LAT + i * 0.001, 6))
            for i in range(count)]


def zone_of(point_id: str) -> int:
    return int(POINT_ID.match(point_id).group(1))


def test_largest_remainder_preserva_o_total():
    assert sum(pops._largest_remainder([3.0, 2.0, 1.0], 12)) == 12
    assert pops._largest_remainder([3.0, 2.0, 1.0], 12) == [6, 4, 2]


def test_largest_remainder_reparte_o_resto_uma_unidade_por_vez():
    repartido = pops._largest_remainder([1.0, 1.0, 1.0], 10)
    assert sum(repartido) == 10
    assert sorted(repartido) == [3, 3, 4], "o resto vai para um só, nunca acumula"


def test_largest_remainder_trata_casos_degenerados():
    assert pops._largest_remainder([0.0, 0.0], 5) == [0, 0]
    assert pops._largest_remainder([1.0, 1.0], 0) == [0, 0]
    assert pops._largest_remainder([], 3) == []


def test_alloc_distribui_igualmente_entre_os_pontos():
    rng = np.random.default_rng(1)
    idx = pops._alloc(4, 40, rng)
    assert len(idx) == 40
    assert sorted(collections.Counter(idx).values()) == [10, 10, 10, 10]


def test_regressao_alloc_com_n_grande_cobre_todos_os_pontos():
    """Alocar por par origem-destino (n=1 por chamada) empilhava tudo no primeiro ponto:
    76% dos pares mandam 1 ou 2 pops, e a repartição inteira sempre premia o mesmo índice."""
    rng = np.random.default_rng(1)
    per_pair = collections.Counter()
    for _ in range(200):
        per_pair.update(pops._alloc(7, 1, rng))
    assert len(per_pair) == 1, "com n=1 a repartição é degenerada — por isso ela é agrupada"

    grouped = collections.Counter(pops._alloc(7, 200, rng))
    assert len(grouped) == 7
    assert max(grouped.values()) / 200 < 0.2


def test_plan_destinations_reparte_pessoas_proporcionalmente(configure):
    configure(pops, min_pop_size=0)
    dests = [(10, 900.0), (20, 600.0), (30, 500.0)]
    plan = pops._plan_destinations(dests, 2000, 500)
    people = {zone: count for zone, _pops, count in plan}
    assert sum(people.values()) == 2000
    assert people[10] == pytest.approx(900, abs=2)
    assert people[20] == pytest.approx(600, abs=2)
    assert people[30] == pytest.approx(500, abs=2)


def test_regressao_plan_destinations_nao_zera_destinos_menores(configure):
    """Repartir o NÚMERO DE POPS ∝ fluxo zerava os destinos pequenos e jogava as pessoas
    deles nos maiores: só 66% dos pares O-D relevantes chegavam a existir."""
    configure(pops, min_pop_size=0)
    flows = [900.0, 800.0, 700.0, 600.0, 500.0, 400.0, 300.0, 200.0, 100.0, 50.0]
    dests = [(index, flow) for index, flow in enumerate(flows)]
    people_total = 16746

    plan = pops._plan_destinations(dests, people_total, people_total // 5)
    assert len(plan) == len(dests), "todo destino com fluxo precisa receber pessoas"

    got = {zone: people for zone, _pops, people in plan}
    for zone, flow in dests:
        expected = people_total * flow / sum(flows)
        assert got[zone] == pytest.approx(expected, rel=0.01)


def test_plan_destinations_devolve_a_cauda_curta_para_quem_ficou(configure):
    configure(pops, min_pop_size=50)
    dests = [(1, 1000.0), (2, 1.0)]
    plan = pops._plan_destinations(dests, 1000, 200)
    assert [zone for zone, _p, _ppl in plan] == [1]
    assert plan[0][2] == 1000, "as pessoas do destino cortado não podem sumir"


def test_plan_destinations_mantem_a_cauda_quando_ninguem_alcanca_o_minimo(configure):
    configure(pops, min_pop_size=500)
    dests = [(1, 1.0), (2, 1.0)]
    plan = pops._plan_destinations(dests, 100, 50)
    assert sum(people for _z, _p, people in plan) == 100


def test_plan_destinations_sem_pessoas():
    assert pops._plan_destinations([(1, 1.0)], 0, 10) == []


def build(zones, pop, od, home_cands, work_cands):
    return pops.generate(zones, pop, od, home_cands, work_cands)


def test_generate_preserva_a_populacao(configure):
    configure(pops, people_per_pop=100.0, min_pop_size=10, dest_cap=0, seed=42)
    zones = make_zones(1, 2)
    points, generated = build(
        zones,
        {1: 5000.0, 2: 3000.0},
        {(1, 1): 2000.0, (1, 2): 3000.0, (2, 2): 3000.0},
        {1: spread(1, 12), 2: spread(2, 12)},
        {1: spread(11, 12), 2: spread(12, 12)},
    )
    assert sum(p["size"] for p in generated) == 8000
    assert sum(p["residents"] for p in points) == 8000
    assert sum(p["jobs"] for p in points) == 8000


def test_generate_mantem_pontos_de_tipo_unico(configure):
    configure(pops, people_per_pop=100.0, min_pop_size=10)
    zones = make_zones(1, 2)
    points, _generated = build(
        zones,
        {1: 5000.0, 2: 3000.0},
        {(1, 2): 5000.0, (2, 1): 3000.0},
        {1: spread(1, 10), 2: spread(2, 10)},
        {1: spread(11, 10), 2: spread(12, 10)},
    )
    assert not [p for p in points if p["jobs"] > 0 and p["residents"] > 0]
    assert not [p for p in points if p["jobs"] == 0 and p["residents"] == 0]


def test_generate_nao_repete_coordenadas(configure):
    configure(pops, people_per_pop=100.0, min_pop_size=10)
    zones = make_zones(1)
    points, _generated = build(
        zones, {1: 4000.0}, {(1, 1): 4000.0}, {1: spread(1, 8)}, {1: spread(1, 8)}
    )
    locations = [tuple(p["location"]) for p in points]
    assert len(locations) == len(set(locations)), "coordenadas iguais viram um ponto só no mapa"


def test_generate_liga_todo_pop_a_pontos_existentes(configure):
    configure(pops, people_per_pop=100.0, min_pop_size=10)
    zones = make_zones(1, 2)
    points, generated = build(
        zones,
        {1: 5000.0, 2: 3000.0},
        {(1, 2): 5000.0, (2, 1): 3000.0},
        {1: spread(1, 10), 2: spread(2, 10)},
        {1: spread(11, 10), 2: spread(12, 10)},
    )
    ids = {p["id"] for p in points}
    assert all(p["residenceId"] in ids and p["jobId"] in ids for p in generated)
    assert all(p["jobId"] for p in generated), "nenhum pop pode ficar sem destino"


def test_regressao_trabalho_nao_se_concentra_num_unico_ponto(configure):
    """A zona 73 recebia 61% dos seus 330 mil empregos num único ponto: cada par
    origem-destino alocava sozinho e sempre caía na mesma célula."""
    configure(pops, people_per_pop=200.0, min_pop_size=10, dest_cap=0)
    origins = list(range(2, 60))
    zones = make_zones(1, *origins)
    pop = {1: 4000.0} | {origin: 4000.0 for origin in origins}
    od = {(origin, 1): 3000.0 for origin in origins}
    od[(1, 1)] = 4000.0

    home_cands = {zone: spread(zone, 10) for zone in pop}
    work_cands = {1: spread(80, 40)} | {origin: spread(origin + 100, 4) for origin in origins}

    points, _generated = build(zones, pop, od, home_cands, work_cands)
    in_zone_one = [p for p in points if zone_of(p["id"]) == 1 and p["jobs"] > 0]
    total_jobs = sum(p["jobs"] for p in in_zone_one)
    biggest = max(p["jobs"] for p in in_zone_one)

    assert len(in_zone_one) > 20, "os empregos precisam se espalhar pelos pontos disponíveis"
    assert biggest / total_jobs < 0.15


def test_generate_usa_fallback_quando_falta_um_tipo_de_ponto(configure):
    configure(pops, people_per_pop=100.0, min_pop_size=10)
    zones = make_zones(1)
    points, generated = build(zones, {1: 2000.0}, {(1, 1): 2000.0}, {1: spread(1, 6)}, {})
    assert generated
    assert any(POINT_ID.match(p["id"]).group(2) == "wf" for p in points)


def test_generate_usa_fallback_de_casa(configure):
    configure(pops, people_per_pop=100.0, min_pop_size=10)
    zones = make_zones(1)
    points, generated = build(zones, {1: 2000.0}, {(1, 1): 2000.0}, {}, {1: spread(1, 6)})
    assert generated
    assert any(POINT_ID.match(p["id"]).group(2) == "hf" for p in points)


def test_generate_ignora_zona_sem_populacao_ou_sem_pontos(configure):
    configure(pops, people_per_pop=100.0, min_pop_size=10)
    zones = make_zones(1, 2, 3)
    points, _generated = build(
        zones,
        {1: 2000.0, 2: 0.0, 3: 1000.0},
        {(1, 1): 2000.0},
        {1: spread(1, 6)},
        {1: spread(11, 6)},
    )
    assert {zone_of(p["id"]) for p in points} == {1}


def test_generate_respeita_o_teto_de_destinos(configure):
    configure(pops, people_per_pop=50.0, min_pop_size=10, dest_cap=1)
    zones = make_zones(1, 2, 3)
    _points, generated = build(
        zones,
        {1: 5000.0, 2: 100.0, 3: 100.0},
        {(1, 2): 4000.0, (1, 3): 1000.0},
        {z: spread(z, 6) for z in (1, 2, 3)},
        {z: spread(z + 10, 6) for z in (1, 2, 3)},
    )
    destinations = {zone_of(p["jobId"]) for p in generated if zone_of(p["residenceId"]) == 1}
    assert destinations == {2}, "com dest_cap=1 só o maior fluxo sobrevive"


def test_generate_sem_matriz_manda_todo_mundo_para_a_propria_zona(configure):
    configure(pops, people_per_pop=100.0, min_pop_size=10)
    zones = make_zones(1)
    _points, generated = build(zones, {1: 1000.0}, {}, {1: spread(1, 5)}, {1: spread(11, 5)})
    assert generated
    assert all(zone_of(p["jobId"]) == 1 for p in generated)


def test_generate_e_reprodutivel(configure):
    def run():
        configure(pops, people_per_pop=100.0, min_pop_size=10, seed=7)
        return build(
            make_zones(1, 2),
            {1: 3000.0, 2: 2000.0},
            {(1, 2): 3000.0, (2, 1): 2000.0},
            {1: spread(1, 8), 2: spread(2, 8)},
            {1: spread(11, 8), 2: spread(12, 8)},
        )

    first_points, first_pops = run()
    second_points, second_pops = run()
    assert first_points == second_points
    assert first_pops == second_pops


def test_min_pop_size_limita_a_fragmentacao(configure):
    configure(pops, people_per_pop=1.0, min_pop_size=500)
    zones = make_zones(1)
    _points, generated = build(
        zones, {1: 5000.0}, {(1, 1): 5000.0}, {1: spread(1, 50)}, {1: spread(11, 50)}
    )
    assert len(generated) <= 10


def test_aggregate_recalcula_do_zero():
    points = {
        "a": {"id": "a", "location": [0, 0], "jobs": 99, "residents": 99, "popIds": ["velho"]},
        "b": {"id": "b", "location": [1, 1], "jobs": 99, "residents": 99, "popIds": ["velho"]},
    }
    generated = [{"id": "p1", "size": 10, "residenceId": "a", "jobId": "b"}]
    pops._aggregate(points, generated)
    assert points["a"]["residents"] == 10 and points["a"]["jobs"] == 0
    assert points["b"]["jobs"] == 10 and points["b"]["residents"] == 0
    assert points["a"]["popIds"] == ["p1"] and points["b"]["popIds"] == ["p1"]


def test_generate_descarta_destino_sem_pontos(configure):
    """Destino sem candidato de nenhum tipo sai da lista antes de repartir as pessoas."""
    configure(pops, people_per_pop=100.0, min_pop_size=10)
    zones = make_zones(1, 2)
    _points, generated = build(
        zones,
        {1: 2000.0},
        {(1, 2): 5000.0, (1, 1): 1000.0},
        {1: spread(1, 6)},
        {1: spread(11, 6)},
    )
    assert generated
    assert {zone_of(p["jobId"]) for p in generated} == {1}


def test_generate_pula_zona_que_nao_recebe_nenhum_pop(configure):
    """Zona de área desprezível não tira nenhum pop do orçamento repartido por área."""
    configure(pops, people_per_pop=1000.0, min_pop_size=10)
    zones = make_zones(1, 2)
    zones.polygons[1] = zones.polygons[1].buffer(-0.0199999)
    points, generated = build(
        zones,
        {1: 100000.0, 2: 1.0},
        {(1, 1): 100000.0, (2, 2): 1.0},
        {1: spread(1, 8), 2: spread(2, 8)},
        {1: spread(11, 8), 2: spread(12, 8)},
    )
    assert generated
    assert 2 not in {zone_of(p["id"]) for p in points}


def test_generate_nunca_emite_pop_vazio(configure):
    """Garante a premissa que permite dispensar a checagem de fatia não positiva."""
    configure(pops, people_per_pop=1.0, min_pop_size=1)
    zones = make_zones(1)
    _points, generated = build(
        zones, {1: 37.0}, {(1, 1): 37.0}, {1: spread(1, 9)}, {1: spread(11, 9)}
    )
    assert generated
    assert all(p["size"] > 0 for p in generated)
    assert sum(p["size"] for p in generated) == 37


def test_generate_pula_zona_cuja_populacao_arredonda_para_zero(configure):
    configure(pops, people_per_pop=100.0, min_pop_size=10)
    zones = make_zones(1, 2)
    points, generated = build(
        zones,
        {1: 2000.0, 2: 0.4},
        {(1, 1): 2000.0, (2, 2): 0.4},
        {1: spread(1, 6), 2: spread(2, 6)},
        {1: spread(11, 6), 2: spread(12, 6)},
    )
    assert generated
    assert 2 not in {zone_of(p["id"]) for p in points}
