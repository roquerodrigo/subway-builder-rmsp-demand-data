"""Testes da configuração: leitura de ambiente, caminhos derivados, bbox e diretórios."""

from __future__ import annotations

import dataclasses
import importlib
from pathlib import Path

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


def test_env_returns_empty_string_when_variable_is_empty(monkeypatch):
    monkeypatch.setenv("DEMAND_TEST_TEXT", "")
    assert config._env("DEMAND_TEST_TEXT", "fallback") == ""


def test_env_float_returns_default_when_variable_is_absent(monkeypatch):
    monkeypatch.delenv("DEMAND_TEST_FLOAT", raising=False)
    assert config._env_float("DEMAND_TEST_FLOAT", 1.5) == 1.5


def test_env_float_returns_default_when_variable_is_empty(monkeypatch):
    monkeypatch.setenv("DEMAND_TEST_FLOAT", "")
    assert config._env_float("DEMAND_TEST_FLOAT", 1.5) == 1.5


def test_env_float_parses_variable(monkeypatch):
    monkeypatch.setenv("DEMAND_TEST_FLOAT", "0.00045")
    assert config._env_float("DEMAND_TEST_FLOAT", 1.5) == 0.00045


def test_env_float_rejects_non_numeric_variable(monkeypatch):
    monkeypatch.setenv("DEMAND_TEST_FLOAT", "muito")
    with pytest.raises(ValueError):
        config._env_float("DEMAND_TEST_FLOAT", 1.5)


def test_env_int_returns_default_when_variable_is_absent(monkeypatch):
    monkeypatch.delenv("DEMAND_TEST_INT", raising=False)
    assert config._env_int("DEMAND_TEST_INT", 42) == 42


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
        DEMAND_SEED=str(99),
        DEMAND_PEOPLE_PER_POP="250.5",
        DEMAND_LOTE_LAYER="geoportal:outra_camada",
    )
    assert reloaded.settings.sources_dir == tmp_path / "fontes"
    assert reloaded.settings.out_dir == tmp_path / "saida"
    assert reloaded.settings.seed == 99
    assert reloaded.settings.people_per_pop == 250.5
    assert reloaded.settings.lote_layer == "geoportal:outra_camada"


def test_module_settings_fall_back_to_defaults(reload_config, monkeypatch):
    for name in ("DEMAND_SEED", "DEMAND_PEOPLE_PER_POP", "DEMAND_DEST_CAP"):
        monkeypatch.delenv(name, raising=False)
    reloaded = reload_config()
    assert reloaded.settings.seed == 42
    assert reloaded.settings.people_per_pop == 300.0
    assert reloaded.settings.dest_cap == 0


def test_project_root_holds_the_package():
    assert (config.PROJECT_ROOT / "src" / "demand_data" / "config.py").exists()


def test_settings_is_frozen(settings):
    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.seed = 1


def test_source_paths_hang_from_sources_dir(settings, tmp_path):
    sources_dir = tmp_path / "sources"
    assert settings.cnefe_csv == sources_dir / "cnefe.csv"
    assert settings.setor_pop_csv == sources_dir / "setor_pop.csv"
    assert settings.lotes_csv == sources_dir / "lotes.csv"
    assert settings.od_zip == sources_dir / "od2023.zip"
    assert settings.od_extract_dir == sources_dir / "od2023"
    assert settings.cnefe_zip == sources_dir / "35_SP.zip"
    assert settings.censo_zip == sources_dir / "censo_basico_BR.zip"


def test_od_paths_follow_the_zip_layout(settings, tmp_path):
    assert settings.od_dir == tmp_path / "sources" / "od2023" / "Site_190225"
    assert settings.zones_shp.parent.name == "Shape"
    assert settings.zones_shp.name == "Zonas_2023"
    assert settings.zones_shp.parent.parent.name == "002_Site Metro Mapas_190225"
    assert settings.od_dbf == settings.od_dir / "Banco2023_divulgacao_190225.dbf"


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
    settings.setor_pop_csv.unlink()
    assert not settings.have_inputs()


def test_have_inputs_is_true_with_every_file(settings):
    _create_inputs(settings)
    assert settings.have_inputs()


def test_ensure_sources_creates_nested_directories(settings):
    settings.ensure_sources()
    assert settings.sources_dir.is_dir()


def test_ensure_sources_tolerates_an_existing_directory(settings):
    settings.ensure_sources()
    settings.ensure_sources()
    assert settings.sources_dir.is_dir()


def test_ensure_out_creates_nested_directories(tmp_path):
    settings = Settings(out_dir=tmp_path / "a" / "b" / "out")
    settings.ensure_out()
    assert settings.out_dir.is_dir()


def _create_inputs(settings: Settings) -> None:
    for path in (
        settings.zones_shp.with_suffix(".shp"),
        settings.od_dbf,
        settings.cnefe_csv,
        settings.setor_pop_csv,
    ):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("", encoding="ascii")
