"""Dimensionamento: proporção preservada, cauda descartada e escala real como identidade."""

from __future__ import annotations

import pytest

from demand_data import scaling
from demand_data.scaling import Scale


def pop(pop_id: str, size: int) -> dict:
    return {"id": pop_id, "size": size, "residenceId": "a", "jobId": "b"}


def test_apply_reduz_na_mesma_proporcao():
    scaled = scaling.apply([pop("p1", 1000), pop("p2", 500)], Scale(5), 1)
    assert [p["size"] for p in scaled] == [50, 25]


def test_apply_preserva_os_demais_campos():
    (scaled,) = scaling.apply([pop("p1", 1000)], Scale(5), 1)
    assert scaled["id"] == "p1"
    assert scaled["residenceId"] == "a" and scaled["jobId"] == "b"


def test_apply_descarta_abaixo_do_piso():
    scaled = scaling.apply([pop("grande", 2000), pop("pequeno", 400)], Scale(5), 50)
    assert [p["id"] for p in scaled] == ["grande"]


def test_apply_descarta_o_que_arredondaria_para_zero_mesmo_sem_piso():
    assert scaling.apply([pop("p1", 5)], Scale(5), 0) == []


def test_apply_na_escala_real_e_identidade():
    original = [pop("p1", 1000), pop("p2", 3)]
    assert scaling.apply(original, Scale(100), 1) == original


def test_apply_nao_muta_a_entrada():
    original = [pop("p1", 1000)]
    scaling.apply(original, Scale(5), 1)
    assert original[0]["size"] == 1000


def test_apply_registra_o_descarte(caplog):
    with caplog.at_level("INFO"):
        scaling.apply([pop("grande", 2000), pop("pequeno", 400)], Scale(5), 50)
    assert "1 descartados" in caplog.text


def test_apply_nao_registra_nada_na_escala_real_sem_piso(caplog):
    with caplog.at_level("INFO"):
        scaling.apply([pop("p1", 1000)], Scale(100), 1)
    assert caplog.text == ""


def test_apply_aguenta_demanda_vazia(caplog):
    with caplog.at_level("INFO"):
        assert scaling.apply([], Scale(5), 1) == []
    assert "100.0% do nominal" in caplog.text


def test_factor_e_a_fracao_do_percentual():
    assert Scale(5).factor == 0.05
    assert Scale(100).factor == 1.0


def test_slug_ordena_por_tamanho():
    assert [Scale(p).slug for p in (5, 10, 100)] == ["005", "010", "100"]


def test_ratio_traduz_o_percentual_em_escala():
    assert Scale(5).ratio == "1:20"
    assert Scale(50).ratio == "1:2"
    assert Scale(100).ratio == "1:1"


def test_ratio_usa_virgula_decimal():
    assert Scale(14).ratio == "1:7,1"


def test_is_full_so_em_cem_por_cento():
    assert Scale(100).is_full
    assert not Scale(50).is_full


@pytest.mark.parametrize("percent", [0, -5, 101])
def test_scale_rejeita_percentual_fora_da_faixa(percent):
    with pytest.raises(ValueError, match="entre 1% e 100%"):
        Scale(percent)


def test_scale_ordena_do_mais_leve_ao_completo():
    assert sorted([Scale(100), Scale(5), Scale(25)]) == [Scale(5), Scale(25), Scale(100)]
