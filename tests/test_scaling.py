"""Escala de jogo: proporção preservada, cauda descartada e identidade quando desligada."""

from __future__ import annotations

import pytest

from demand_data import scaling


def pop(pop_id: str, size: int) -> dict:
    return {"id": pop_id, "size": size, "residenceId": "a", "jobId": "b"}


def test_scale_reduz_na_mesma_proporcao():
    scaled = scaling.scale([pop("p1", 1000), pop("p2", 500)], 0.05, 1)
    assert [p["size"] for p in scaled] == [50, 25]


def test_scale_preserva_os_demais_campos():
    (scaled,) = scaling.scale([pop("p1", 1000)], 0.05, 1)
    assert scaled["id"] == "p1"
    assert scaled["residenceId"] == "a" and scaled["jobId"] == "b"


def test_scale_descarta_abaixo_do_piso():
    scaled = scaling.scale([pop("grande", 2000), pop("pequeno", 400)], 0.05, 50)
    assert [p["id"] for p in scaled] == ["grande"]


def test_scale_descarta_o_que_arredondaria_para_zero_mesmo_sem_piso():
    assert scaling.scale([pop("p1", 5)], 0.05, 0) == []


def test_scale_desligada_e_identidade():
    original = [pop("p1", 1000), pop("p2", 3)]
    assert scaling.scale(original, 1.0, 1) == original


def test_scale_nao_muta_a_entrada():
    original = [pop("p1", 1000)]
    scaling.scale(original, 0.05, 1)
    assert original[0]["size"] == 1000


def test_scale_rejeita_fator_nao_positivo():
    with pytest.raises(ValueError, match="escala deve ser positiva"):
        scaling.scale([pop("p1", 100)], 0, 1)


def test_scale_registra_o_descarte(caplog):
    with caplog.at_level("INFO"):
        scaling.scale([pop("grande", 2000), pop("pequeno", 400)], 0.05, 50)
    assert "1 descartados" in caplog.text


def test_scale_label_formata_a_fracao():
    from demand_data import railyard

    assert railyard.scale_label(0.05) == "1:20"
    assert railyard.scale_label(0.5) == "1:2"
    assert railyard.scale_label(1.0) == ""


def test_scale_label_usa_virgula_decimal():
    from demand_data import railyard

    assert railyard.scale_label(0.141) == "1:7,1"


def test_resolve_factor_deriva_da_populacao_alvo():
    assert scaling.resolve_factor(35_000_000, 5_000_000, 1.0) == pytest.approx(1 / 7)


def test_resolve_factor_mantem_a_populacao_ao_trocar_de_amostra():
    """Amostras com totais diferentes chegam ao mesmo alvo."""
    completa = scaling.resolve_factor(35_661_166, 5_000_000, 1.0)
    leve = scaling.resolve_factor(35_443_925, 5_000_000, 1.0)
    assert 35_661_166 * completa == pytest.approx(35_443_925 * leve)


def test_resolve_factor_cai_no_fator_explicito_sem_alvo():
    assert scaling.resolve_factor(35_000_000, 0, 0.05) == 0.05


def test_resolve_factor_e_neutro_sem_viagens():
    assert scaling.resolve_factor(0, 5_000_000, 1.0) == 1.0
