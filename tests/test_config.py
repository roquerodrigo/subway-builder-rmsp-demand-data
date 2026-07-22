"""Testes da configuração: leitura de ambiente, caminhos derivados, bbox e diretórios."""

from __future__ import annotations

import dataclasses
import importlib

import pytest

from demand_data import config
from demand_data.config import Settings


@pytest.fixture
def reload_config(monkeypatch):
    """Recarrega ``demand_data.config`` com variáveis de ambiente aplicadas.

    Os defaults do ``Settings`` são lidos na definição da classe, então só um reimport
    reflete o ambiente.
    """

    def _reload(**environment: str):
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        return importlib.reload(config)

    yield _reload
    monkeypatch.undo()
    importlib.reload(config)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(sources_dir=tmp_path / "sources", out_dir=tmp_path / "out")


def test_env_returns_default_when_variable_is_absent(monkeypatch):
    monkeypatch.delenv("DEMAND_TEST_TEXT", raising=False)
    assert config._env("DEMAND_TEST_TEXT", "fallback") == "fallback"


def test_env_returns_variable_when_present(monkeypatch):
    monkeypatch.setenv("DEMAND_TEST_TEXT", "from-environment")
    assert config._env("DEMAND_TEST_TEXT", "fallback") == "from-environment"


def test_env_float_returns_default_when_variable_is_empty(monkeypatch):
    monkeypatch.setenv("DEMAND_TEST_FLOAT", "")
    assert config._env_float("DEMAND_TEST_FLOAT", 1.5) == 1.5


def test_env_float_parses_variable(monkeypatch):
    monkeypatch.setenv("DEMAND_TEST_FLOAT", "0.00045")
    assert config._env_float("DEMAND_TEST_FLOAT", 1.5) == 0.00045


def test_env_int_returns_default_when_variable_is_empty(monkeypatch):
    monkeypatch.setenv("DEMAND_TEST_INT", "")
    assert config._env_int("DEMAND_TEST_INT", 42) == 42


def test_env_int_parses_variable(monkeypatch):
    monkeypatch.setenv("DEMAND_TEST_INT", "7")
    assert config._env_int("DEMAND_TEST_INT", 42) == 7


def test_env_int_rejects_float_text(monkeypatch):
    monkeypatch.setenv("DEMAND_TEST_INT", "7.5")
    with pytest.raises(ValueError):
        config._env_int("DEMAND_TEST_INT", 42)


def test_module_settings_read_the_environment(reload_config, tmp_path):
    reloaded = reload_config(
        DEMAND_SOURCES_DIR=str(tmp_path / "fontes"),
        DEMAND_OUT_DIR=str(tmp_path / "saida"),
        DEMAND_MAX_POP_SIZE="250",
        DEMAND_FLOW_URL="https://exemplo/fluxos.parquet",
    )
    assert reloaded.settings.sources_dir == tmp_path / "fontes"
    assert reloaded.settings.out_dir == tmp_path / "saida"
    assert reloaded.settings.max_pop_size == 250
    assert reloaded.settings.flow_url == "https://exemplo/fluxos.parquet"


def test_module_settings_fall_back_to_defaults(reload_config, monkeypatch):
    for name in ("DEMAND_MAX_POP_SIZE", "DEMAND_DENSITY_CELL", "DEMAND_POI_SNAP_M"):
        monkeypatch.delenv(name, raising=False)
    reloaded = reload_config()
    assert reloaded.settings.max_pop_size == 500
    assert reloaded.settings.density_cell == 0.00045
    assert reloaded.settings.poi_snap_m == 500.0


def test_project_root_holds_the_package():
    assert (config.PROJECT_ROOT / "src" / "demand_data" / "config.py").exists()


def test_settings_is_frozen(settings):
    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.max_pop_size = 1


def test_source_paths_hang_from_sources_dir(settings, tmp_path):
    sources_dir = tmp_path / "sources"
    assert settings.flows_parquet == sources_dir / "fluxos.parquet"
    assert settings.pois_csv == sources_dir / "pois.csv"


def test_output_paths_hang_from_out_dir(settings, tmp_path):
    assert settings.demand_json == tmp_path / "out" / "demand_data.json"
    assert settings.map_html == tmp_path / "out" / "pops_map.html"


def test_in_bbox_accepts_an_interior_point(settings):
    assert settings.in_bbox(-46.60, -23.55)


@pytest.mark.parametrize(
    ("lng", "lat"),
    [(-47.22, -24.08), (-45.68, -23.17), (-47.22, -23.17), (-45.68, -24.08)],
)
def test_in_bbox_includes_the_corners(settings, lng, lat):
    assert settings.in_bbox(lng, lat)


@pytest.mark.parametrize(
    ("lng", "lat"),
    [(-47.23, -23.55), (-45.67, -23.55), (-46.60, -24.09), (-46.60, -23.16)],
)
def test_in_bbox_rejects_points_outside(settings, lng, lat):
    assert not settings.in_bbox(lng, lat)


def test_in_bbox_uses_the_configured_bbox():
    settings = dataclasses.replace(Settings(), bbox=(0.0, 0.0, 1.0, 1.0))
    assert settings.in_bbox(0.5, 0.5)
    assert not settings.in_bbox(-46.60, -23.55)


def test_have_inputs_is_false_without_any_file(settings):
    assert not settings.have_inputs()


def test_have_inputs_is_false_while_one_file_is_missing(settings):
    _create_inputs(settings)
    settings.pois_csv.unlink()
    assert not settings.have_inputs()


def test_have_inputs_is_true_with_every_file(settings):
    _create_inputs(settings)
    assert settings.have_inputs()


def test_ensure_sources_creates_nested_directories(settings):
    settings.ensure_sources()
    assert settings.sources_dir.is_dir()


def test_ensure_out_creates_nested_directories(tmp_path):
    settings = Settings(out_dir=tmp_path / "a" / "b" / "out")
    settings.ensure_out()
    assert settings.out_dir.is_dir()


def _create_inputs(settings: Settings) -> None:
    settings.ensure_sources()
    for path in (settings.flows_parquet, settings.pois_csv):
        path.write_text("", encoding="ascii")
