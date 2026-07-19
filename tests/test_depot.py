"""Testes da escrita do arquivo de demanda (JSON + .gz)."""

from __future__ import annotations

import gzip
import json
import logging

import pytest

from demand_data import depot

POINTS = [{"id": "p1", "lat": -23.55, "lng": -46.6}]
POPS = [{"id": "pop1", "home": "p1", "work": "p1", "drivingSeconds": 0, "drivingDistance": 0}]


@pytest.fixture
def demand_json(tmp_path):
    return tmp_path / "demand_data.json"


def test_write_creates_json_and_gzip(demand_json):
    depot.write(POINTS, POPS, demand_json)
    assert demand_json.exists()
    assert demand_json.with_suffix(".json.gz").exists()


def test_write_stores_points_and_pops(demand_json):
    depot.write(POINTS, POPS, demand_json)
    assert json.loads(demand_json.read_text(encoding="utf-8")) == {"points": POINTS, "pops": POPS}


def test_gzip_holds_the_same_payload(demand_json):
    depot.write(POINTS, POPS, demand_json)
    compressed = gzip.decompress(demand_json.with_suffix(".json.gz").read_bytes())
    assert compressed.decode("utf-8") == demand_json.read_text(encoding="utf-8")


def test_write_uses_compact_separators(demand_json):
    depot.write(POINTS, POPS, demand_json)
    payload = demand_json.read_text(encoding="utf-8")
    assert ", " not in payload
    assert ": " not in payload


def test_write_accepts_empty_collections(demand_json):
    depot.write([], [], demand_json)
    assert json.loads(demand_json.read_text(encoding="utf-8")) == {"points": [], "pops": []}


def test_write_overwrites_previous_content(demand_json):
    depot.write(POINTS, POPS, demand_json)
    depot.write([], [], demand_json)
    assert json.loads(demand_json.read_text(encoding="utf-8"))["points"] == []
    compressed = gzip.decompress(demand_json.with_suffix(".json.gz").read_bytes())
    assert json.loads(compressed)["points"] == []


def test_write_keeps_accented_names(demand_json):
    points = [{"id": "p1", "zone": "São Caetano"}]
    depot.write(points, [], demand_json)
    assert json.loads(demand_json.read_text(encoding="utf-8"))["points"] == points


def test_gzip_name_appends_to_the_existing_suffix(tmp_path):
    path = tmp_path / "demanda"
    depot.write(POINTS, POPS, path)
    assert (tmp_path / "demanda.gz").exists()


def test_write_logs_both_file_sizes(demand_json, caplog):
    with caplog.at_level(logging.INFO, logger="demand_data.depot"):
        depot.write(POINTS, POPS, demand_json)
    assert "demand_data.json" in caplog.text
    assert "demand_data.json.gz" in caplog.text
