"""Extração da Pesquisa OD: zonas, população por zona e matriz origem-destino."""

from __future__ import annotations

import pytest
from tests.conftest import BASE_LAT, BASE_LNG

from demand_data import od


def person(dom, fam, pes, weight, home, work=None, school=None):
    return {
        "ID_DOM": dom, "ID_FAM": fam, "ID_PESS": pes,
        "FE_PESS": weight, "ZONA": home, "ZONATRA1": work, "ZONA_ESC": school,
    }


def test_as_int_converte_o_que_da():
    assert od._as_int("7") == 7
    assert od._as_int(7.0) == 7
    assert od._as_int(None) is None
    assert od._as_int("zona") is None


def test_accumulate_classifica_pelo_destino_declarado():
    records = [
        person(1, 1, 1, 100.0, 1, work=2),
        person(1, 1, 2, 50.0, 1, school=2),
        person(2, 1, 1, 30.0, 2),
    ]
    survey = od.accumulate_od(records, {1, 2})
    assert survey.population == {1: 150.0, 2: 30.0}
    assert survey.totals() == {"work": 100.0, "school": 50.0, "other": 30.0}
    assert survey.flows["work"] == {(1, 2): 100.0}
    assert survey.flows["school"] == {(1, 2): 50.0}


def test_accumulate_trata_zero_como_ausencia():
    """A pesquisa preenche 0 em ZONATRA1/ZONA_ESC para quem não trabalha nem estuda."""
    survey = od.accumulate_od([person(1, 1, 1, 80.0, 1, work=0, school=0)], {1})
    assert survey.totals()["other"] == 80.0
    assert survey.flows["work"] == {}


def test_accumulate_prefere_trabalho_a_escola():
    survey = od.accumulate_od([person(1, 1, 1, 90.0, 1, work=2, school=1)], {1, 2})
    assert survey.totals() == {"work": 90.0, "school": 0.0, "other": 0.0}


def test_accumulate_separa_destino_fora_das_zonas():
    survey = od.accumulate_od([person(1, 1, 1, 70.0, 1, work=803)], {1, 2})
    assert survey.external["work"] == {1: 70.0}
    assert survey.flows["work"] == {}
    assert survey.totals()["work"] == 70.0, "a pessoa continua contando na população"


def test_accumulate_usa_as_viagens_para_os_motivos_nao_pendulares():
    """A distribuição de compras/saúde/lazer vem das viagens (FE_VIA), não das pessoas."""
    trip = person(1, 1, 1, 10.0, 1)
    trip.update({"MOTIVO_D": 5, "ZONA_O": 1, "ZONA_D": 2, "FE_VIA": 400.0})
    survey = od.accumulate_od([trip], {1, 2})
    assert survey.flows["other"] == {(1, 2): 400.0}


def test_accumulate_ignora_motivo_de_volta_para_casa():
    trip = person(1, 1, 1, 10.0, 1)
    trip.update({"MOTIVO_D": 8, "ZONA_O": 1, "ZONA_D": 2, "FE_VIA": 999.0})
    survey = od.accumulate_od([trip], {1, 2})
    assert survey.flows["other"] == {}


def test_accumulate_deduplica_pessoa_repetida():
    record = person(1, 1, 1, 100.0, 1, work=1)
    survey = od.accumulate_od([record, dict(record)], {1})
    assert survey.population == {1: 100.0}


def test_accumulate_ignora_peso_zero_ou_ausente():
    records = [person(1, 1, 1, 0.0, 1, work=1), person(2, 1, 1, None, 1, work=1)]
    survey = od.accumulate_od(records, {1})
    assert survey.population == {} and survey.flows["work"] == {}


def test_accumulate_descarta_morador_de_fora():
    survey = od.accumulate_od([person(1, 1, 1, 100.0, 99, work=1)], {1})
    assert survey.population == {} and survey.flows["work"] == {}


def test_demand_by_zone_soma_moradores_e_chegadas():
    survey = od.accumulate_od(
        [person(1, 1, 1, 600.0, 1, work=2), person(2, 1, 1, 400.0, 2, work=2)], {1, 2}
    )
    demand = od.demand_by_zone(survey)
    assert demand[1] == (600.0, 0.0)
    assert demand[2] == (400.0, pytest.approx(1000.0))
    residents = sum(r for r, _a in demand.values())
    arrivals = sum(a for _r, a in demand.values())
    assert residents == pytest.approx(arrivals), "cada morador tem exatamente um destino"


def test_demand_by_zone_reparte_por_origem():
    survey = od.accumulate_od(
        [person(1, 1, 1, 300.0, 1, work=2), person(1, 1, 2, 100.0, 1, work=3)], {1, 2, 3}
    )
    demand = od.demand_by_zone(survey)
    assert demand[2][1] == pytest.approx(300.0)
    assert demand[3][1] == pytest.approx(100.0)


def test_demand_by_zone_sem_fluxos():
    survey = od.accumulate_od([person(1, 1, 1, 100.0, 1)], {1})
    assert od.demand_by_zone(survey) == {1: (100.0, 0.0)}


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
    records = [person(1, 1, 1, 100.0, 1, work=2), person(1, 1, 2, 40.0, 2, work=2)]
    monkeypatch.setattr("dbfread.DBF", lambda *args, **kwargs: records)
    survey = od.extract_od(tmp_path / "banco.dbf", {1, 2})
    assert survey.population == {1: 100.0, 2: 40.0}
    assert survey.flows["work"] == {(1, 2): 100.0, (2, 2): 40.0}


def test_load_zones_ignora_geometria_intransformavel(zones_shp, monkeypatch):
    import shapely.ops

    def explode(*args, **kwargs):
        raise ValueError("geometria inválida")

    monkeypatch.setattr(shapely.ops, "transform", explode)
    zones = od.load_zones(zones_shp)
    assert zones.ids == []


def test_demand_by_zone_ignora_origem_sem_pessoas_na_atividade():
    """A matriz de motivos não-pendulares pode ter viagens de uma zona cujos moradores todos
    trabalham ou estudam — não há quem distribuir por ela."""
    trip = person(1, 1, 1, 50.0, 1, work=2)
    trip.update({"MOTIVO_D": 5, "ZONA_O": 1, "ZONA_D": 2, "FE_VIA": 900.0})
    survey = od.accumulate_od([trip], {1, 2})
    assert survey.flows["other"] == {(1, 2): 900.0}
    assert survey.activity[1]["other"] == 0.0
    assert od.demand_by_zone(survey)[2][1] == pytest.approx(50.0), "só o fluxo de trabalho"
