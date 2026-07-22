"""Leitura e orientação das viagens observadas (:mod:`demand_data.flows`)."""

from __future__ import annotations

import pytest
from tests.conftest import BASE_LAT, BASE_LNG, flow_row

from demand_data import flows

INSIDE = (BASE_LNG, BASE_LAT)
OUTSIDE = (-40.0, -20.0)


def make_flow(motive=3, name="Trabalho Serviços", trips=100, origin=INSIDE, dest=INSIDE):
    row = flow_row(1, 2, motive, name, trips, origin, dest)
    return next(flows.parse_rows([row]))


def test_parse_rows_lê_as_colunas_do_parquet(configure):
    configure(flows)
    row = flow_row(1, 2, 3, "Trabalho Serviços", 100, INSIDE, (BASE_LNG + 0.01, BASE_LAT))
    parsed = list(flows.parse_rows([row]))
    assert len(parsed) == 1
    assert (parsed[0].origin_zone, parsed[0].dest_zone, parsed[0].motive) == (1, 2, 3)
    assert parsed[0].trips == 100


def test_parse_rows_descarta_viagem_fora_do_recorte(configure):
    configure(flows)
    assert list(flows.parse_rows([flow_row(1, 2, 3, "x", 10, OUTSIDE, INSIDE)])) == []
    assert list(flows.parse_rows([flow_row(1, 2, 3, "x", 10, INSIDE, OUTSIDE)])) == []


def test_parse_rows_descarta_viagem_sem_peso(configure):
    configure(flows)
    assert list(flows.parse_rows([flow_row(1, 2, 3, "x", 0, INSIDE, INSIDE)])) == []


@pytest.mark.parametrize("row", [{"origin_zone": 1}, {"trips": "muito"}])
def test_parse_rows_pula_linha_malformada(configure, row):
    configure(flows)
    assert list(flows.parse_rows([row])) == []


def test_orient_põe_a_casa_na_origem_das_viagens_de_atividade():
    trip = flows.orient(make_flow(motive=4, origin=INSIDE, dest=(BASE_LNG + 0.02, BASE_LAT)))
    assert trip.home == INSIDE
    assert trip.activity == (BASE_LNG + 0.02, BASE_LAT)
    assert trip.place_type == "SCH", "educação leva a uma escola"


def test_orient_põe_a_casa_no_destino_da_volta_pra_casa():
    trip = flows.orient(make_flow(motive=flows.MOTIVE_HOME, name="Residência",
                                  origin=(BASE_LNG + 0.02, BASE_LAT), dest=INSIDE))
    assert trip.home == INSIDE, "a volta pra casa tem a casa no destino"
    assert trip.activity == (BASE_LNG + 0.02, BASE_LAT)
    assert trip.place_type is None


def test_orient_deixa_o_trabalho_difuso_sem_tipo():
    trip = flows.orient(make_flow(motive=3, name="Trabalho Serviços"))
    assert trip.place_type is None


def test_orient_preserva_as_zonas_de_cada_ponta():
    row = flow_row(7, 9, 6, "Saúde", 50, INSIDE, (BASE_LNG + 0.01, BASE_LAT))
    trip = flows.orient(next(flows.parse_rows([row])))
    assert (trip.home_zone, trip.activity_zone) == (7, 9)
    assert trip.place_type == "HOS"


def test_load_flows_lê_o_arquivo(flows_parquet, configure):
    configure(flows)
    loaded = flows.load_flows(flows_parquet)
    assert len(loaded) == 3
    assert sum(f.trips for f in loaded) == 220
