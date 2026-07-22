"""Fixtures compartilhadas: recortes minúsculos das fontes, no mesmo formato."""

from __future__ import annotations

from dataclasses import replace

import pytest

from demand_data.config import settings

# canto do bbox da RMSP, longe das bordas para os testes não dependerem do recorte
BASE_LNG, BASE_LAT = -46.60, -23.55

FLOW_COLUMNS = (
    "origin_zone", "dest_zone", "motive", "motive_name", "trips",
    "o_lon", "o_lat", "d_lon", "d_lat",
)


@pytest.fixture
def configure(monkeypatch):
    """Troca o ``settings`` visto por um módulo, que é frozen e global."""

    def _configure(module, **overrides):
        patched = replace(settings, **overrides)
        monkeypatch.setattr(module, "settings", patched)
        return patched

    return _configure


def flow_row(origin_zone, dest_zone, motive, motive_name, trips, origin, dest) -> dict:
    """Uma linha do parquet de viagens, no formato de :mod:`demand_data.flows`."""
    return {
        "origin_zone": origin_zone, "dest_zone": dest_zone,
        "motive": motive, "motive_name": motive_name, "trips": trips,
        "o_lon": origin[0], "o_lat": origin[1], "d_lon": dest[0], "d_lat": dest[1],
    }


def write_parquet(path, rows: list[dict]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    columns = {name: [row[name] for row in rows] for name in FLOW_COLUMNS}
    pq.write_table(pa.table(columns), str(path))


@pytest.fixture
def flows_parquet(tmp_path):
    """Parquet minúsculo: uma ida e a sua volta (Residência) e um destino tipado (escola)."""
    home = (BASE_LNG, BASE_LAT)
    work = (BASE_LNG + 0.05, BASE_LAT)
    school = (BASE_LNG + 0.02, BASE_LAT + 0.02)
    rows = [
        flow_row(1, 2, 3, "Trabalho Serviços", 100, home, work),
        flow_row(2, 1, 8, "Residência", 80, work, home),
        flow_row(1, 3, 4, "Educação", 40, home, school),
    ]
    path = tmp_path / "fluxos.parquet"
    write_parquet(path, rows)
    return path
