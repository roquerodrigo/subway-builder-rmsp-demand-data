"""Fixtures compartilhadas: recortes minúsculos das fontes reais, no mesmo formato."""

from __future__ import annotations

from dataclasses import replace

import pytest

from demand_data.config import settings

# canto do bbox da RMSP, longe das bordas para os testes não dependerem do recorte
BASE_LNG, BASE_LAT = -46.60, -23.55


@pytest.fixture
def configure(monkeypatch):
    """Troca o ``settings`` visto por um módulo, que é frozen e global."""

    def _configure(module, **overrides):
        patched = replace(settings, **overrides)
        monkeypatch.setattr(module, "settings", patched)
        return patched

    return _configure


@pytest.fixture
def zones_shp(tmp_path):
    """Shapefile com duas zonas quadradas adjacentes (1 e 2), em WGS84."""
    import shapefile

    path = tmp_path / "zonas"
    writer = shapefile.Writer(str(path))
    writer.field("NumeroZona", "N")
    for zone_id, offset in ((1, 0.0), (2, 0.05)):
        left, bottom = BASE_LNG + offset, BASE_LAT
        writer.poly([[
            [left, bottom], [left + 0.04, bottom],
            [left + 0.04, bottom + 0.04], [left, bottom + 0.04], [left, bottom],
        ]])
        writer.record(zone_id)
    writer.close()
    path.with_suffix(".prj").write_text(
        'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],'
        'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]'
    )
    return path


def cnefe_line(lng: float, lat: float, especie: int, setor: str) -> str:
    return f"{lng},{lat},{especie},{setor}\n"


@pytest.fixture
def cnefe_csv(tmp_path):
    """Endereços: 4 residenciais e 2 estabelecimentos na zona 1, 2 residenciais na zona 2."""
    rows = [
        cnefe_line(BASE_LNG + 0.001, BASE_LAT + 0.001, 1, "350000001"),
        cnefe_line(BASE_LNG + 0.002, BASE_LAT + 0.001, 1, "350000001"),
        cnefe_line(BASE_LNG + 0.010, BASE_LAT + 0.010, 2, "350000001"),
        cnefe_line(BASE_LNG + 0.011, BASE_LAT + 0.010, 1, "350000001"),
        cnefe_line(BASE_LNG + 0.020, BASE_LAT + 0.020, 6, "350000001"),
        cnefe_line(BASE_LNG + 0.021, BASE_LAT + 0.020, 4, "350000001"),
        cnefe_line(BASE_LNG + 0.051, BASE_LAT + 0.001, 1, "350000002"),
        cnefe_line(BASE_LNG + 0.052, BASE_LAT + 0.001, 1, "350000002"),
    ]
    path = tmp_path / "cnefe.csv"
    path.write_text("".join(rows), encoding="ascii")
    return path


@pytest.fixture
def setor_pop_csv(tmp_path):
    path = tmp_path / "setor_pop.csv"
    path.write_text("350000001,1000\n350000002,500\n", encoding="ascii")
    return path


@pytest.fixture
def lotes_csv(tmp_path):
    rows = [
        f"{BASE_LNG + 0.001},{BASE_LAT + 0.001},R,200\n",
        f"{BASE_LNG + 0.002},{BASE_LAT + 0.002},R,150\n",
        f"{BASE_LNG + 0.020},{BASE_LAT + 0.020},N,900\n",
    ]
    path = tmp_path / "lotes.csv"
    path.write_text("".join(rows), encoding="ascii")
    return path


def cells(*specs) -> dict[tuple[int, int], list[float]]:
    """Células no formato interno de :mod:`demand_data.density`.

    ``specs`` são tuplas ``(cx, cy, peso_casa, peso_trabalho)``; o resto dos acumuladores é
    derivado para manter o centroide coerente com a âncora.
    """
    out = {}
    for cx, cy, home, work in specs:
        weight = home + work
        lng, lat = BASE_LNG + cx * 0.001, BASE_LAT + cy * 0.001
        out[(cx, cy)] = [home, work, weight * lng, weight * lat, weight, 0.5, lng, lat]
    return out
