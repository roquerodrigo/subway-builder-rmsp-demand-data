"""Equipamentos nomeados: localização nas zonas e captura de demanda."""

from __future__ import annotations

from tests.conftest import BASE_LAT, BASE_LNG

from demand_data import pois

CATALOGUE = (
    ("AIR", "Aeroporto Teste", [BASE_LNG + 0.01, BASE_LAT + 0.01], 300),
    ("UNI", "Universidade Teste", [BASE_LNG + 0.06, BASE_LAT + 0.01], 100),
)


def demand():
    points = [
        {"id": "z1h1", "location": [BASE_LNG, BASE_LAT], "jobs": 0, "residents": 500,
         "popIds": []},
        {"id": "z1w1", "location": [BASE_LNG + 0.02, BASE_LAT], "jobs": 500, "residents": 0,
         "popIds": []},
        {"id": "z2w1", "location": [BASE_LNG + 0.05, BASE_LAT], "jobs": 200, "residents": 0,
         "popIds": []},
    ]
    pops = [
        {"id": "p1", "size": 200, "residenceId": "z1h1", "jobId": "z1w1"},
        {"id": "p2", "size": 150, "residenceId": "z1h1", "jobId": "z1w1"},
        {"id": "p3", "size": 150, "residenceId": "z1h1", "jobId": "z1w1"},
        {"id": "p4", "size": 200, "residenceId": "z1h1", "jobId": "z2w1"},
    ]
    return points, pops


def test_locate_descobre_a_zona_de_cada_equipamento(zones_shp):
    from demand_data.od import load_zones

    located = pois.locate(load_zones(zones_shp), CATALOGUE)
    assert [p["zone"] for p in located] == [1, 2]
    assert [p["id"] for p in located] == ["AIR_Aeroporto_Teste", "UNI_Universidade_Teste"]


def test_locate_descarta_equipamento_fora_do_recorte(zones_shp):
    from demand_data.od import load_zones

    fora = (("AIR", "Fora", [BASE_LNG - 10, BASE_LAT], 100),)
    assert pois.locate(load_zones(zones_shp), fora) == []


def test_capture_reetiqueta_destinos_ate_a_capacidade(zones_shp):
    from demand_data.od import load_zones

    points, pops = demand()
    created = pois.capture(points, pops, load_zones(zones_shp), CATALOGUE)
    assert len(created) == 2
    airport = next(p for p in created if p["name"] == "Aeroporto Teste")
    captured = sum(p["size"] for p in pops if p["jobId"] == airport["id"])
    assert captured >= 300, "toma pops até cobrir a capacidade"
    assert captured <= 500, "não toma a zona inteira"


def test_capture_preserva_a_populacao(zones_shp):
    from demand_data.od import load_zones

    points, pops = demand()
    total = sum(p["size"] for p in pops)
    pois.capture(points, pops, load_zones(zones_shp), CATALOGUE)
    assert sum(p["size"] for p in pops) == total, "captura reetiqueta, não cria demanda"


def test_capture_nao_deixa_dois_equipamentos_disputarem_o_mesmo_pop(zones_shp):
    from demand_data.od import load_zones

    vizinhos = (
        ("SHP", "Shopping A", [BASE_LNG + 0.01, BASE_LAT + 0.01], 1000),
        ("SHP", "Shopping B", [BASE_LNG + 0.02, BASE_LAT + 0.01], 1000),
    )
    points, pops = demand()
    created = pois.capture(points, pops, load_zones(zones_shp), vizinhos)
    destinos = [p["jobId"] for p in pops]
    assert len(destinos) == len(set(p["id"] for p in pops)), "cada pop tem um destino só"
    if len(created) == 2:
        a, b = (p["id"] for p in created)
        assert not (set(d for d in destinos if d == a) & set(d for d in destinos if d == b))


def test_capture_ignora_equipamento_sem_demanda_na_zona(zones_shp, caplog):
    from demand_data.od import load_zones

    points, pops = demand()
    for pop in pops:
        pop["jobId"] = "z1w1"
    solo = (("UNI", "Sem Demanda", [BASE_LNG + 0.06, BASE_LAT + 0.01], 100),)
    with caplog.at_level("WARNING"):
        assert pois.capture(points, pops, load_zones(zones_shp), solo) == []
    assert "sem demanda para capturar" in caplog.text


def test_capture_marca_tipo_e_nome_no_ponto(zones_shp):
    from demand_data.od import load_zones

    points, pops = demand()
    created = pois.capture(points, pops, load_zones(zones_shp), CATALOGUE)
    assert all(p["type"] and p["name"] for p in created)
    assert all(p["id"].split("_")[0] == p["type"] for p in created)


def test_catalogo_real_usa_codigos_da_taxonomia_do_depot():
    conhecidos = {"AIR", "EXT", "UNI", "HOS", "SHP", "SPO", "CNV", "PRK", "ZOO"}
    assert {code for code, _n, _l, _c in pois.CATALOGUE} <= conhecidos


def test_catalogo_real_esta_dentro_do_recorte():
    from demand_data.config import settings

    for _code, name, (lng, lat), capacity in pois.CATALOGUE:
        assert settings.in_bbox(lng, lat), f"{name} fora do bbox"
        assert capacity > 0


def test_capture_sem_equipamento_no_recorte_nao_faz_nada(zones_shp):
    from demand_data.od import load_zones

    points, pops = demand()
    fora = (("AIR", "Fora", [BASE_LNG - 10, BASE_LAT], 100),)
    assert pois.capture(points, pops, load_zones(zones_shp), fora) == []
    assert all(p["jobId"] in ("z1w1", "z2w1") for p in pops)
