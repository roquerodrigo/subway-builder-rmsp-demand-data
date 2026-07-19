"""Arquivos de submissão ao Railyard: config.json e description.md."""

from __future__ import annotations

import json
from datetime import datetime

from demand_data import railyard

BBOX = (-47.22, -24.08, -45.68, -23.17)


def sample():
    points = [
        {"id": "z1h1", "location": [-46.6, -23.5], "jobs": 0, "residents": 1000},
        {"id": "z1w1", "location": [-46.5, -23.5], "jobs": 900, "residents": 0},
        {"id": "EXT_N-46.6_-23.2", "location": [-46.6, -23.2], "jobs": 100, "residents": 0},
    ]
    pops = [
        {"id": "p1", "size": 900, "residenceId": "z1h1", "jobId": "z1w1"},
        {"id": "p2", "size": 100, "residenceId": "z1h1", "jobId": "EXT_N-46.6_-23.2"},
    ]
    return points, pops


def test_config_traz_os_campos_que_o_railyard_exige():
    points, pops = sample()
    config = railyard.build_config(points, pops, BBOX, "RMSP", "RMSP", "alguem", "1.1.0")
    assert set(config) == {"code", "name", "bbox", "description", "population",
                           "initialViewState", "creator", "version", "country"}
    assert config["population"] == 1000
    assert config["bbox"] == list(BBOX)


def test_config_centraliza_a_camera_no_recorte():
    points, pops = sample()
    view = railyard.build_config(points, pops, BBOX, "n", "C", "c", "1")["initialViewState"]
    assert view["longitude"] == -46.45
    assert view["latitude"] == -23.625
    assert view["zoom"] == 12 and view["bearing"] == 0


def test_config_omite_pais_vazio():
    points, pops = sample()
    config = railyard.build_config(points, pops, BBOX, "n", "C", "c", "1", country="")
    assert "country" not in config


def test_description_resume_a_demanda():
    points, pops = sample()
    text = railyard.build_description(points, pops, datetime(2026, 7, 19))
    assert "| População | 1.000 |" in text
    assert "| Conexões externas | 1 (100 pessoas) |" in text
    assert "19/07/2026" in text


def test_description_nao_estraga_a_pontuacao_do_texto():
    """Formatar milhares trocando toda vírgula por ponto quebrava as frases."""
    points, pops = sample()
    text = railyard.build_description(points, pops, datetime(2026, 7, 19))
    assert "Metrô-SP, com a densidade" in text
    assert "compras, saúde, lazer" in text


def test_description_aguenta_demanda_vazia():
    text = railyard.build_description([], [], datetime(2026, 7, 19))
    assert "| População | 0 |" in text


def test_write_grava_os_dois_arquivos(tmp_path):
    points, pops = sample()
    railyard.write(points, pops, tmp_path, BBOX, "RMSP", "RMSP", "alguem", "1.1.0",
                   generated_at=datetime(2026, 7, 19))
    config = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert config["code"] == "RMSP"
    assert "Região Metropolitana" in (tmp_path / "description.md").read_text(encoding="utf-8")
