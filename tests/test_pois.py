"""Equipamentos nomeados: localização nas zonas e captura de demanda."""

from __future__ import annotations

import pytest
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


def test_capture_nao_passa_da_capacidade(zones_shp, configure):
    """Tomar pops inteiros fazia o equipamento estourar a própria capacidade."""
    from demand_data.od import load_zones

    configure(pois, poi_max_zone_share=1.0)
    points, pops = demand()
    pequeno = (("SPO", "Autódromo Teste", [BASE_LNG + 0.01, BASE_LAT + 0.01], 100),)
    created = pois.capture(points, pops, load_zones(zones_shp), pequeno)
    capturado = sum(p["size"] for p in pops if p["jobId"] == created[0]["id"])
    assert capturado == 100


def test_capture_deixa_demanda_para_a_zona(zones_shp, configure):
    """Sem teto, um equipamento de capacidade alta levava a zona inteira."""
    from demand_data.od import load_zones

    configure(pois, poi_max_zone_share=0.6)
    points, pops = demand()
    faminto = (("AIR", "Aeroporto Grande", [BASE_LNG + 0.01, BASE_LAT + 0.01], 10**6),)
    created = pois.capture(points, pops, load_zones(zones_shp), faminto)
    poi_id = created[0]["id"]
    na_zona = sum(p["size"] for p in pops if p["jobId"] == "z1w1")
    capturado = sum(p["size"] for p in pops if p["jobId"] == poi_id)
    assert na_zona > 0, "a zona não pode ficar sem destino genérico"
    assert capturado == pytest.approx(0.6 * (na_zona + capturado), rel=0.01)


def test_capture_toma_fatia_de_todas_as_origens(zones_shp, configure):
    """Tomar os maiores pops primeiro deixava o equipamento com pouquíssimas origens."""
    from demand_data.od import load_zones

    configure(pois, poi_max_zone_share=1.0)
    points = [{"id": "z1w1", "location": [BASE_LNG + 0.02, BASE_LAT], "jobs": 900,
               "residents": 0, "popIds": []}]
    pops = [{"id": f"p{i}", "size": 300, "residenceId": f"casa{i}", "jobId": "z1w1"}
            for i in range(3)]
    alvo = (("SHP", "Shopping Teste", [BASE_LNG + 0.01, BASE_LAT + 0.01], 300),)
    created = pois.capture(points, pops, load_zones(zones_shp), alvo)
    origens = {p["residenceId"] for p in pops if p["jobId"] == created[0]["id"]}
    assert len(origens) == 3, "cada origem cede a mesma fração"


def test_capture_fatia_preserva_a_populacao(zones_shp, configure):
    from demand_data.od import load_zones

    configure(pois, poi_max_zone_share=0.5)
    points, pops = demand()
    total = sum(p["size"] for p in pops)
    pois.capture(points, pops, load_zones(zones_shp), CATALOGUE)
    assert sum(p["size"] for p in pops) == total
    assert len({p["id"] for p in pops}) == len(pops), "ids duplicados após fatiar"


def test_shares_reparte_proporcionalmente_sem_exceder():
    assert pois._shares([100, 200, 700], 100) == [10, 20, 70]
    assert sum(pois._shares([10, 20, 30], 37)) == 37
    assert pois._shares([5, 5], 0) == [0, 0]
    repartido = pois._shares([1, 1, 100], 3)
    assert all(part <= size for part, size in zip(repartido, [1, 1, 100], strict=True))


def test_capture_toma_o_pop_inteiro_quando_a_fatia_o_cobre(zones_shp, configure):
    """Fatia igual ao pop dispensa criar um pop novo — reetiqueta o que já existe."""
    from demand_data.od import load_zones

    configure(pois, poi_max_zone_share=1.0)
    points = [{"id": "z1w1", "location": [BASE_LNG + 0.02, BASE_LAT], "jobs": 100,
               "residents": 0, "popIds": []}]
    pops = [{"id": "p1", "size": 100, "residenceId": "casa", "jobId": "z1w1"}]
    alvo = (("SHP", "Loja Teste", [BASE_LNG + 0.01, BASE_LAT + 0.01], 500),)
    created = pois.capture(points, pops, load_zones(zones_shp), alvo)
    assert len(pops) == 1, "não cria pop novo quando leva o pop inteiro"
    assert pops[0]["jobId"] == created[0]["id"]


def test_capture_ignora_pop_com_fatia_zero(zones_shp, configure):
    """Pop pequeno demais para render uma pessoa fica onde está."""
    from demand_data.od import load_zones

    configure(pois, poi_max_zone_share=1.0)
    points = [{"id": "z1w1", "location": [BASE_LNG + 0.02, BASE_LAT], "jobs": 1001,
               "residents": 0, "popIds": []}]
    pops = [{"id": "grande", "size": 1000, "residenceId": "a", "jobId": "z1w1"},
            {"id": "minusculo", "size": 1, "residenceId": "b", "jobId": "z1w1"}]
    alvo = (("SHP", "Loja Teste", [BASE_LNG + 0.01, BASE_LAT + 0.01], 10),)
    pois.capture(points, pops, load_zones(zones_shp), alvo)
    assert next(p for p in pops if p["id"] == "minusculo")["jobId"] == "z1w1"
