"""Geração dos pops a partir das viagens: orientação, fusão ida+volta e invariantes."""

from __future__ import annotations

import re

from tests.conftest import BASE_LAT, BASE_LNG

from demand_data import pops
from demand_data.flows import Flow

POINT_ID = re.compile(r"^z(\d+)(h|w)(\d+)$")

A = (BASE_LNG, BASE_LAT)
B = (BASE_LNG + 0.05, BASE_LAT)
C = (BASE_LNG + 0.02, BASE_LAT + 0.02)


def flow(origin_zone, dest_zone, motive, name, trips, origin, dest) -> Flow:
    return Flow(origin_zone, dest_zone, motive, name, trips,
                origin[0], origin[1], dest[0], dest[1])


def role(point_id: str) -> str:
    return POINT_ID.match(point_id).group(2)


def test_largest_remainder_preserva_o_total():
    assert pops._largest_remainder([3.0, 2.0, 1.0], 12) == [6, 4, 2]


def test_largest_remainder_reparte_o_resto_uma_unidade_por_vez():
    repartido = pops._largest_remainder([1.0, 1.0, 1.0], 10)
    assert sum(repartido) == 10
    assert sorted(repartido) == [3, 3, 4]


def test_largest_remainder_trata_casos_degenerados():
    assert pops._largest_remainder([0.0, 0.0], 5) == [0, 0]
    assert pops._largest_remainder([1.0, 1.0], 0) == [0, 0]
    assert pops._largest_remainder([], 3) == []


def test_generate_conserva_as_viagens():
    points, generated = pops.generate([
        flow(1, 2, 3, "Trabalho Serviços", 100, A, B),
        flow(1, 3, 4, "Educação", 40, A, C),
    ])
    assert sum(p["size"] for p in generated) == 140
    assert sum(p["residents"] for p in points) == 140
    assert sum(p["jobs"] for p in points) == 140


def test_generate_orienta_a_casa_na_origem_da_ida():
    points, generated = pops.generate([flow(1, 2, 3, "Trabalho Serviços", 100, A, B)])
    (pop,) = generated
    assert role(pop["residenceId"]) == "h" and role(pop["jobId"]) == "w"
    home = next(p for p in points if p["id"] == pop["residenceId"])
    assert tuple(home["location"]) == A, "a casa da ida é a origem"


def test_generate_funde_ida_e_volta_no_mesmo_par():
    """A ida (Trabalho) e a volta (Residência) do mesmo trajeto viram um pop só."""
    points, generated = pops.generate([
        flow(1, 2, 3, "Trabalho Serviços", 100, A, B),
        flow(2, 1, 8, "Residência", 80, B, A),
    ])
    assert len(generated) == 1
    assert generated[0]["size"] == 180
    home = next(p for p in points if p["id"] == generated[0]["residenceId"])
    assert tuple(home["location"]) == A


def test_generate_mantem_pontos_de_papel_unico():
    points, _ = pops.generate([
        flow(1, 2, 3, "Trabalho Serviços", 100, A, B),
        flow(2, 3, 3, "Trabalho Serviços", 50, B, C),
    ])
    assert not [p for p in points if p["jobs"] > 0 and p["residents"] > 0]
    assert not [p for p in points if p["jobs"] == 0 and p["residents"] == 0]


def test_generate_tipa_o_destino_pela_atividade():
    points, _ = pops.generate([
        flow(1, 3, 4, "Educação", 40, A, C),
        flow(1, 2, 3, "Trabalho Serviços", 100, A, B),
    ])
    school = next(p for p in points if p.get("type"))
    assert school["type"] == "SCH"
    work = next(p for p in points if p["jobs"] > 0 and p["id"].startswith("z2"))
    assert "type" not in work, "trabalho difuso não tem tipo"


def test_generate_quantiza_enderecos_proximos(configure):
    """Duas casas na mesma célula viram um ponto só — e a demanda soma."""
    configure(pops, density_cell=0.01)
    near = (BASE_LNG + 0.001, BASE_LAT)
    points, _ = pops.generate([
        flow(1, 2, 3, "Trabalho Serviços", 100, A, B),
        flow(1, 2, 3, "Trabalho Serviços", 50, near, B),
    ])
    homes = [p for p in points if p["residents"] > 0]
    assert len(homes) == 1
    assert homes[0]["residents"] == 150


def test_generate_liga_todo_pop_a_pontos_existentes():
    points, generated = pops.generate([
        flow(1, 2, 3, "Trabalho Serviços", 100, A, B),
        flow(1, 3, 4, "Educação", 40, A, C),
    ])
    ids = {p["id"] for p in points}
    assert all(p["residenceId"] in ids and p["jobId"] in ids for p in generated)


def test_generate_separa_casa_e_destino_na_mesma_coordenada():
    """Uma coordenada pode ser casa de um trajeto e destino de outro: dois pontos, papel
    único, mas coordenada duplicada quebraria o jogo."""
    points, _ = pops.generate([
        flow(1, 2, 3, "Trabalho Serviços", 100, A, B),
        flow(3, 1, 3, "Trabalho Serviços", 50, C, A),
    ])
    coords = [tuple(p["location"]) for p in points]
    assert len(set(coords)) == len(coords)
    at_a = [p for p in points if abs(p["location"][0] - A[0]) < 1e-4]
    assert {role(p["id"]) for p in at_a} == {"h", "w"}


def test_generate_fatia_pop_acima_do_maximo(configure):
    configure(pops, max_pop_size=250)
    _points, generated = pops.generate([flow(1, 2, 3, "Trabalho Serviços", 900, A, B)])
    assert max(p["size"] for p in generated) <= 250
    assert sum(p["size"] for p in generated) == 900
    assert len({p["id"] for p in generated}) == len(generated)


def test_merge_identical_commutes_soma_os_tamanhos():
    pops_list = [
        {"id": "p1", "size": 10, "residenceId": "a", "jobId": "b"},
        {"id": "p2", "size": 5, "residenceId": "a", "jobId": "b"},
        {"id": "p3", "size": 7, "residenceId": "a", "jobId": "c"},
    ]
    merged = pops.merge_identical_commutes(pops_list)
    assert {p["id"]: p["size"] for p in merged} == {"p1": 15, "p3": 7}


def test_split_oversized_reparte_sem_perder_pessoas():
    original = [{"id": "p1", "size": 1250, "residenceId": "a", "jobId": "b"}]
    pieces = pops.split_oversized(original, 500)
    assert len(pieces) == 3
    assert sum(p["size"] for p in pieces) == 1250
    assert all(p["size"] <= 500 for p in pieces)
    assert len({p["id"] for p in pieces}) == 3


def test_split_oversized_respeita_limite_desligado():
    original = [{"id": "p1", "size": 9999, "residenceId": "a", "jobId": "b"}]
    assert pops.split_oversized(original, 0) == original


def test_aggregate_recalcula_do_zero():
    points = {
        "a": {"id": "a", "location": [0, 0], "jobs": 99, "residents": 99, "popIds": ["velho"]},
        "b": {"id": "b", "location": [1, 1], "jobs": 99, "residents": 99, "popIds": ["velho"]},
    }
    generated = [{"id": "p1", "size": 10, "residenceId": "a", "jobId": "b"}]
    kept = pops.aggregate(points, generated)
    assert len(kept) == 2
    assert points["a"]["residents"] == 10 and points["a"]["jobs"] == 0
    assert points["b"]["jobs"] == 10 and points["b"]["residents"] == 0
    assert points["a"]["popIds"] == ["p1"] and points["b"]["popIds"] == ["p1"]


def test_aggregate_descarta_ponto_sem_demanda():
    points = {
        "a": {"id": "a", "location": [0, 0], "jobs": 0, "residents": 0, "popIds": []},
        "b": {"id": "b", "location": [1, 1], "jobs": 0, "residents": 0, "popIds": []},
        "c": {"id": "c", "location": [2, 2], "jobs": 0, "residents": 0, "popIds": []},
    }
    generated = [{"id": "p1", "size": 5, "residenceId": "a", "jobId": "b"}]
    kept = pops.aggregate(points, generated)
    assert {p["id"] for p in kept} == {"a", "b"}
