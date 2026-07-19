"""Testes do CLI, com os módulos pesados trocados por dublês (sem disco nem rede)."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from demand_data import cli


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def pipeline(monkeypatch):
    """Troca ``od``/``density``/``pops``/``depot``/``htmlmap``/``sources`` por dublês.

    Cada dublê registra as chamadas em ``calls`` e devolve um resultado fixo.
    """
    calls: dict[str, list] = {}
    zones = SimpleNamespace(ids=[1, 2])
    points = [{"id": "pop-1"}]
    poplist = [{"id": "pop-1", "residents": 10}]

    def record(name, result=None):
        def _call(*args, **kwargs):
            calls.setdefault(name, []).append((args, kwargs))
            return result

        return _call

    monkeypatch.setattr(cli, "sources", SimpleNamespace(acquire=record("sources.acquire")))
    monkeypatch.setattr(cli, "od", SimpleNamespace(
        load_zones=record("od.load_zones", zones),
        extract_od=record("od.extract_od", ({1: 100.0, 2: 50.0}, {(1, 2): 30.0})),
        demand_by_zone=record("od.demand_by_zone", {1: (100.0, 30.0)}),
    ))
    monkeypatch.setattr(cli, "density", SimpleNamespace(
        setor_weights=record("density.setor_weights", {"350000001": 1.0}),
        zone_candidates=record("density.zone_candidates", ({1: "casas"}, {1: "empregos"})),
    ))
    monkeypatch.setattr(cli, "pops", SimpleNamespace(
        generate=record("pops.generate", (points, poplist)),
    ))
    monkeypatch.setattr(cli, "depot", SimpleNamespace(write=record("depot.write")))
    monkeypatch.setattr(cli, "htmlmap", SimpleNamespace(write=record("htmlmap.write")))
    return SimpleNamespace(calls=calls, zones=zones, points=points, poplist=poplist)


@pytest.fixture
def settings(tmp_path, configure):
    return configure(cli, sources_dir=tmp_path / "sources", out_dir=tmp_path / "out")


def create_inputs(settings) -> None:
    """Cria os arquivos vazios que fazem ``have_inputs`` enxergar as fontes prontas."""
    settings.zones_shp.parent.mkdir(parents=True, exist_ok=True)
    settings.sources_dir.mkdir(parents=True, exist_ok=True)
    for path in (settings.zones_shp.with_suffix(".shp"), settings.od_dbf,
                 settings.cnefe_csv, settings.setor_pop_csv):
        path.touch()


def test_sources_baixa_as_fontes(runner, pipeline, settings):
    result = runner.invoke(cli.app, ["sources"])

    assert result.exit_code == 0
    assert len(pipeline.calls["sources.acquire"]) == 1


def test_generate_roda_sources_quando_faltam_as_entradas(runner, pipeline, settings):
    result = runner.invoke(cli.app, ["generate"])

    assert result.exit_code == 0
    assert "dados ausentes" in result.output
    assert len(pipeline.calls["sources.acquire"]) == 1


def test_generate_pula_sources_quando_as_entradas_existem(runner, pipeline, settings):
    create_inputs(settings)

    result = runner.invoke(cli.app, ["generate"])

    assert result.exit_code == 0
    assert "sources.acquire" not in pipeline.calls


def test_generate_cria_o_diretorio_de_saida(runner, pipeline, settings):
    result = runner.invoke(cli.app, ["generate"])

    assert result.exit_code == 0
    assert settings.out_dir.is_dir()


def test_generate_encadeia_o_pipeline(runner, pipeline, settings):
    result = runner.invoke(cli.app, ["generate"])

    assert result.exit_code == 0
    assert pipeline.calls["od.load_zones"][0][0] == (settings.zones_shp,)
    assert pipeline.calls["od.extract_od"][0][0] == (settings.od_dbf, {1, 2})
    assert pipeline.calls["density.setor_weights"][0][0] == (
        settings.cnefe_csv, settings.setor_pop_csv
    )
    assert pipeline.calls["density.zone_candidates"][0][0] == (
        settings.cnefe_csv, settings.zones_shp, {"350000001": 1.0}, {1: (100.0, 30.0)}
    )
    assert pipeline.calls["pops.generate"][0][0] == (
        pipeline.zones, {1: 100.0, 2: 50.0}, {(1, 2): 30.0}, {1: "casas"}, {1: "empregos"}
    )


def test_generate_grava_depot_e_mapa(runner, pipeline, settings):
    result = runner.invoke(cli.app, ["generate"])

    assert result.exit_code == 0
    assert pipeline.calls["depot.write"][0][0] == (
        pipeline.points, pipeline.poplist, settings.demand_json
    )
    args, kwargs = pipeline.calls["htmlmap.write"][0]
    min_lng, min_lat, max_lng, max_lat = settings.bbox
    assert args == (
        pipeline.points,
        ((min_lng + max_lng) / 2, (min_lat + max_lat) / 2),
        settings.map_html,
    )
    assert kwargs == {"zones": pipeline.zones}
    assert str(settings.demand_json) in result.output
    assert str(settings.map_html) in result.output


def test_od_only_resume_zonas_populacao_e_pares(runner, pipeline, settings):
    result = runner.invoke(cli.app, ["od-only"])

    assert result.exit_code == 0
    assert result.output.strip() == "zonas=2 pop_total=150 od_pares=1"
    assert "sources.acquire" not in pipeline.calls


def test_verbose_liga_o_log_de_debug(runner, pipeline, settings, monkeypatch):
    niveis = []
    monkeypatch.setattr(cli.logging, "basicConfig", lambda **kwargs: niveis.append(kwargs["level"]))

    assert runner.invoke(cli.app, ["--verbose", "sources"]).exit_code == 0
    assert niveis == [logging.DEBUG]


def test_sem_verbose_o_log_fica_em_info(runner, pipeline, settings, monkeypatch):
    niveis = []
    monkeypatch.setattr(cli.logging, "basicConfig", lambda **kwargs: niveis.append(kwargs["level"]))

    assert runner.invoke(cli.app, ["sources"]).exit_code == 0
    assert niveis == [logging.INFO]
