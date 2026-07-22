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
    """Troca ``flows``/``pops``/``pois``/``depot``/``htmlmap``/``sources`` por dublês."""
    calls: dict[str, list] = {}
    loaded = [SimpleNamespace(motive_name="Trabalho Serviços", trips=100),
              SimpleNamespace(motive_name="Residência", trips=80)]
    points = [{"id": "z1w0"}]
    poplist = [{"id": "p000001", "residenceId": "z1h0", "jobId": "z1w0", "size": 100}]

    def record(name, result=None):
        def _call(*args, **kwargs):
            calls.setdefault(name, []).append((args, kwargs))
            return result

        return _call

    monkeypatch.setattr(cli, "sources", SimpleNamespace(acquire=record("sources.acquire")))
    monkeypatch.setattr(cli, "railyard", SimpleNamespace(write=record("railyard.write")))
    monkeypatch.setattr(cli, "pois", SimpleNamespace(adopt=record("pois.adopt", 0)))
    monkeypatch.setattr(cli, "routing", SimpleNamespace(fill=record("routing.fill", 0)))
    monkeypatch.setattr(cli, "flows",
                        SimpleNamespace(load_flows=record("flows.load_flows", loaded)))
    monkeypatch.setattr(cli, "pops", SimpleNamespace(
        generate=record("pops.generate", (points, poplist)),
        aggregate=record("pops.aggregate", points),
    ))
    monkeypatch.setattr(cli, "depot", SimpleNamespace(write=record("depot.write")))
    monkeypatch.setattr(cli, "htmlmap", SimpleNamespace(write=record("htmlmap.write")))
    return SimpleNamespace(calls=calls, loaded=loaded, points=points, poplist=poplist)


@pytest.fixture
def settings(tmp_path, configure):
    return configure(cli, sources_dir=tmp_path / "sources", out_dir=tmp_path / "out")


def create_inputs(settings) -> None:
    """Cria os arquivos que fazem ``have_inputs`` enxergar as fontes prontas."""
    settings.sources_dir.mkdir(parents=True, exist_ok=True)
    settings.flows_parquet.touch()
    settings.pois_csv.touch()


def test_sources_baixa_as_fontes(runner, pipeline, settings):
    result = runner.invoke(cli.app, ["sources"])
    assert result.exit_code == 0
    assert len(pipeline.calls["sources.acquire"]) == 1


def sources_that_produce(settings, pipeline):
    """Dublê de ``acquire`` que de fato deixa as entradas prontas, como a aquisição real."""

    def _acquire(*args, **kwargs):
        pipeline.calls.setdefault("sources.acquire", []).append((args, kwargs))
        create_inputs(settings)

    return SimpleNamespace(acquire=_acquire)


def test_generate_roda_sources_quando_faltam_as_entradas(runner, pipeline, settings, monkeypatch):
    monkeypatch.setattr(cli, "sources", sources_that_produce(settings, pipeline))
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
    create_inputs(settings)
    result = runner.invoke(cli.app, ["generate"])
    assert result.exit_code == 0
    assert settings.out_dir.is_dir()


def test_generate_encadeia_o_pipeline(runner, pipeline, settings):
    create_inputs(settings)
    result = runner.invoke(cli.app, ["generate"])
    assert result.exit_code == 0
    assert pipeline.calls["flows.load_flows"][0][0] == ()
    assert pipeline.calls["pops.generate"][0][0] == (pipeline.loaded,)
    assert pipeline.calls["pois.adopt"][0][0] == (pipeline.points, pipeline.poplist)
    assert pipeline.calls["pops.aggregate"][0][0] == (pipeline.points, pipeline.poplist)


def test_generate_grava_depot_e_mapa(runner, pipeline, settings):
    create_inputs(settings)
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
    assert kwargs == {}
    assert str(settings.demand_json) in result.output
    assert str(settings.map_html) in result.output


def test_flows_only_resume_as_viagens(runner, pipeline, settings):
    result = runner.invoke(cli.app, ["flows-only"])
    assert result.exit_code == 0
    assert "viagens=2 trips_total=180" in result.output
    assert "Residência" in result.output
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


def test_regressao_generate_para_quando_sources_nao_produz_as_entradas(runner, pipeline, settings):
    """Sem revalidar, uma aquisição incompleta seguia adiante e quebrava lá na frente."""
    result = runner.invoke(cli.app, ["generate"])
    assert result.exit_code == 1
    assert "faltam dados mesmo após `sources`" in result.output
    assert "flows.load_flows" not in pipeline.calls


def test_generate_roteia_quando_ha_servidor_osrm(runner, pipeline, settings, configure):
    configure(cli, sources_dir=settings.sources_dir, out_dir=settings.out_dir,
              osrm_url="http://127.0.0.1:5000")
    create_inputs(settings)
    result = runner.invoke(cli.app, ["generate"])
    assert result.exit_code == 0
    assert pipeline.calls["routing.fill"][0][0] == (
        pipeline.points, pipeline.poplist, "http://127.0.0.1:5000"
    )


def test_generate_avisa_quando_nao_ha_osrm(runner, pipeline, settings):
    create_inputs(settings)
    result = runner.invoke(cli.app, ["generate"])
    assert "sem DEMAND_OSRM_URL" in result.output
    assert "routing.fill" not in pipeline.calls
