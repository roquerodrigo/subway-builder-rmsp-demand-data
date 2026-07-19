"""Extração da Pesquisa OD: zonas, população por zona e matriz origem-destino."""

from __future__ import annotations

import pytest
from tests.conftest import BASE_LAT, BASE_LNG

from demand_data import od


def person(dom, fam, pes, weight, home, work=None):
    return {
        "ID_DOM": dom, "ID_FAM": fam, "ID_PESS": pes,
        "FE_PESS": weight, "ZONA": home, "ZONATRA1": work,
    }


def test_as_int_converte_o_que_da():
    assert od._as_int("7") == 7
    assert od._as_int(7.0) == 7
    assert od._as_int(None) is None
    assert od._as_int("zona") is None


def test_accumulate_soma_populacao_e_matriz():
    records = [
        person(1, 1, 1, 100.0, 1, 2),
        person(1, 1, 2, 50.0, 1, 1),
        person(2, 1, 1, 30.0, 2, 1),
    ]
    pop, matrix = od.accumulate_od(records, {1, 2})
    assert pop == {1: 150.0, 2: 30.0}
    assert matrix == {(1, 2): 100.0, (1, 1): 50.0, (2, 1): 30.0}


def test_accumulate_deduplica_pessoa_repetida():
    records = [person(1, 1, 1, 100.0, 1, 1), person(1, 1, 1, 100.0, 1, 1)]
    pop, _matrix = od.accumulate_od(records, {1})
    assert pop == {1: 100.0}


def test_accumulate_ignora_peso_zero_ou_ausente():
    records = [person(1, 1, 1, 0.0, 1, 1), person(2, 1, 1, None, 1, 1)]
    pop, matrix = od.accumulate_od(records, {1})
    assert pop == {} and matrix == {}


def test_accumulate_conta_populacao_mesmo_sem_destino_valido():
    """Quem não declara trabalho entra na população, mas não na matriz."""
    records = [person(1, 1, 1, 80.0, 1, None), person(2, 1, 1, 20.0, 1, 99)]
    pop, matrix = od.accumulate_od(records, {1})
    assert pop == {1: 100.0}
    assert matrix == {}


def test_accumulate_descarta_zona_de_fora():
    records = [person(1, 1, 1, 100.0, 99, 1)]
    pop, matrix = od.accumulate_od(records, {1})
    assert pop == {} and matrix == {}


def test_demand_by_zone_reescala_empregos_para_a_populacao():
    """A matriz cobre só quem declarou trabalho, mas todo pop recebe um jobId."""
    pop = {1: 600.0, 2: 400.0}
    matrix = {(1, 2): 300.0, (2, 1): 200.0}
    demand = od.demand_by_zone(pop, matrix)
    assert demand[1] == (600.0, pytest.approx(400.0))
    assert demand[2] == (400.0, pytest.approx(600.0))
    assert sum(jobs for _res, jobs in demand.values()) == pytest.approx(sum(pop.values()))


def test_demand_by_zone_sem_matriz():
    demand = od.demand_by_zone({1: 100.0}, {})
    assert demand == {1: (100.0, 0.0)}


def test_demand_by_zone_inclui_zona_que_so_recebe_trabalhadores():
    demand = od.demand_by_zone({1: 100.0}, {(1, 2): 100.0})
    assert demand[2][0] == 0.0
    assert demand[2][1] > 0.0


def test_load_zones_le_e_reprojeta(zones_shp):
    zones = od.load_zones(zones_shp)
    assert zones.ids == [1, 2]
    assert len(zones.polygons) == 2
    minx, miny, maxx, maxy = zones.polygons[0].bounds
    assert minx == pytest.approx(BASE_LNG, abs=1e-6)
    assert miny == pytest.approx(BASE_LAT, abs=1e-6)
    assert maxx == pytest.approx(BASE_LNG + 0.04, abs=1e-6)
    assert maxy == pytest.approx(BASE_LAT + 0.04, abs=1e-6)


def test_zone_of_localiza_o_ponto(zones_shp):
    zones = od.load_zones(zones_shp)
    assert zones.zone_of(BASE_LNG + 0.01, BASE_LAT + 0.01) == 1
    assert zones.zone_of(BASE_LNG + 0.06, BASE_LAT + 0.01) == 2
    assert zones.zone_of(BASE_LNG - 10, BASE_LAT) is None


def test_extract_od_le_o_dbf(tmp_path, monkeypatch):
    records = [person(1, 1, 1, 100.0, 1, 2), person(1, 1, 2, 40.0, 2, 2)]
    monkeypatch.setattr("dbfread.DBF", lambda *args, **kwargs: records)
    pop, matrix = od.extract_od(tmp_path / "banco.dbf", {1, 2})
    assert pop == {1: 100.0, 2: 40.0}
    assert matrix == {(1, 2): 100.0, (2, 2): 40.0}


def test_load_zones_ignora_geometria_intransformavel(zones_shp, monkeypatch):
    import shapely.ops

    def explode(*args, **kwargs):
        raise ValueError("geometria inválida")

    monkeypatch.setattr(shapely.ops, "transform", explode)
    zones = od.load_zones(zones_shp)
    assert zones.ids == []
