"""Aquisição das fontes: download das viagens, consulta ao Overpass e ``acquire``."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

import pytest

from demand_data import sources

INSIDE_LNG, INSIDE_LAT = -46.60, -23.55
OUTSIDE_LNG, OUTSIDE_LAT = -40.00, -20.00


class FakeResponse:
    """Resposta mínima de ``urlopen``: gerenciador de contexto com ``read``."""

    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self, size: int | None = None) -> bytes:
        if size is None:
            size = len(self._payload)
        chunk, self._payload = self._payload[:size], self._payload[size:]
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def test_mb_reports_the_file_size(tmp_path):
    path = tmp_path / "arquivo.bin"
    path.write_bytes(b"\0" * 2_500_000)
    assert sources._mb(path) == pytest.approx(2.5)


def test_download_writes_the_response_in_chunks(tmp_path, monkeypatch):
    payload = b"x" * (1 << 21)
    seen: list[str] = []

    def _urlopen(request):
        seen.append(request.full_url)
        assert request.headers["User-agent"]
        return FakeResponse(payload)

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    dest = tmp_path / "arquivo.bin"
    sources._download("https://exemplo/arquivo.bin", dest)
    assert dest.read_bytes() == payload
    assert seen == ["https://exemplo/arquivo.bin"]


def test_download_skips_an_existing_file(tmp_path, monkeypatch):
    def _urlopen(*args, **kwargs):
        raise AssertionError("não deveria baixar de novo")

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    dest = tmp_path / "arquivo.bin"
    dest.write_bytes(b"antigo")
    sources._download("https://exemplo/arquivo.bin", dest)
    assert dest.read_bytes() == b"antigo"


def test_download_logs_that_the_file_already_exists(tmp_path, caplog):
    dest = tmp_path / "arquivo.bin"
    dest.write_bytes(b"pronto")
    with caplog.at_level(logging.INFO, logger="demand_data.sources"):
        sources._download("https://exemplo/arquivo.bin", dest)
    assert "já baixado" in caplog.text


def test_regressao_download_interrompido_nao_vira_arquivo_final(tmp_path, monkeypatch):
    """Gravar direto no destino fazia a execução seguinte tratar um arquivo truncado como pronto."""

    class TornStream:
        def read(self, _size):
            raise OSError("conexão caiu")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(sources.urllib.request, "urlopen", lambda *a, **k: TornStream())
    dest = tmp_path / "arquivo.bin"
    with pytest.raises(OSError, match="conexão caiu"):
        sources._download("http://exemplo/arquivo.bin", dest)
    assert not dest.exists()


def test_download_flows_baixa_e_registra(tmp_path, configure, monkeypatch, caplog):
    settings = configure(sources, sources_dir=tmp_path)
    calls: list[tuple] = []

    def _download(url, dest):
        calls.append((url, dest))
        dest.write_bytes(b"parquet")

    monkeypatch.setattr(sources, "_download", _download)
    with caplog.at_level(logging.INFO, logger="demand_data.sources"):
        sources.download_flows()
    assert calls == [(settings.flow_url, settings.flows_parquet)]
    assert settings.flows_parquet.read_bytes() == b"parquet"
    assert "viagens:" in caplog.text


def test_overpass_query_cobre_o_recorte_e_os_tipos(configure):
    configure(sources, bbox=(-47.0, -24.0, -45.0, -23.0))
    com_geometria = sources._overpass_query('["amenity"="university"]', True)
    sem_geometria = sources._overpass_query('["amenity"="school"]', False)
    assert "(-24.0,-47.0,-23.0,-45.0)" in com_geometria
    assert com_geometria.rstrip().endswith("out geom tags;")
    assert sem_geometria.rstrip().endswith("out center tags;")


def test_parse_overpass_ignora_elemento_incompleto(configure):
    configure(sources)
    elementos = [
        {"id": 1, "tags": {"name": ""}, "lat": -23.5, "lon": -46.6},
        {"id": 2, "tags": {"name": "Sem coordenada"}},
        {"id": 3, "tags": {"name": "Fora"}, "lat": -20.0, "lon": -40.0},
    ]
    codes = {id(e): "AIR" for e in elementos}
    assert list(sources.parse_overpass(elementos, codes)) == []


def test_parse_overpass_usa_o_centro_e_deduplica(configure):
    configure(sources)
    elemento = {"id": 7, "tags": {"name": "Aeroporto X"}, "center": {"lat": -23.5, "lon": -46.6}}
    codes = {id(elemento): "AIR"}
    linhas = list(sources.parse_overpass([elemento, elemento], codes))
    assert linhas == ["-46.6,-23.5,AIR,7,Aeroporto X,\n"], "way sem geometria usa o center"


def test_parse_overpass_deriva_o_centro_do_contorno(configure):
    """Com "bb" o Overpass deixa de mandar o centro dos ways; ele sai dos limites."""
    configure(sources)
    elemento = {"id": 5, "type": "way", "tags": {"name": "Parque Y"}, "geometry": [
        {"lon": -46.62, "lat": -23.52}, {"lon": -46.60, "lat": -23.52},
        {"lon": -46.60, "lat": -23.50}, {"lon": -46.62, "lat": -23.50},
        {"lon": -46.62, "lat": -23.52}]}
    codes = {id(elemento): "PRK"}
    linha = next(iter(sources.parse_overpass([elemento], codes)))
    assert linha.startswith("-46.61,-23.51,PRK,5,Parque Y,")
    assert linha.rstrip().endswith("-46.62 -23.52")


def test_outline_conserta_poligono_invalido():
    """Contorno que se cruza vira geometria inválida; o buffer(0) recupera."""
    element = {"geometry": [
        {"lon": -46.60, "lat": -23.50}, {"lon": -46.58, "lat": -23.48},
        {"lon": -46.60, "lat": -23.48}, {"lon": -46.58, "lat": -23.50},
        {"lon": -46.60, "lat": -23.50}]}
    assert len(sources.outline(element)) >= 6


def test_outline_ignora_geometria_curta():
    assert sources.outline({"geometry": [{"lon": -46.6, "lat": -23.5}]}) == []
    assert sources.outline({}) == []


def test_pois_pula_quando_ja_existe(configure, tmp_path, caplog):
    settings = configure(sources, sources_dir=tmp_path)
    settings.pois_csv.write_text("-46.6,-23.5,AIR,1,X\n", encoding="utf-8")
    with caplog.at_level("INFO"):
        sources.pois()
    assert "já processado" in caplog.text


def test_pois_baixa_e_escreve_o_csv(configure, tmp_path, monkeypatch):
    settings = configure(sources, sources_dir=tmp_path, bbox=(-47.0, -24.0, -45.0, -23.0))
    # o Overpass responde uma vez por tipo; só a consulta de aeroporto devolve algo
    aeroporto = {"elements": [{"id": 1, "tags": {"aeroway": "aerodrome", "name": "Aeroporto X"},
                               "lat": -23.5, "lon": -46.6}]}
    respostas = iter([json.dumps(aeroporto).encode()])
    monkeypatch.setattr(
        sources.urllib.request, "urlopen",
        lambda *a, **k: FakeResponse(next(respostas, b'{"elements": []}')),
    )
    sources.pois()
    assert settings.pois_csv.read_text(encoding="utf-8") == "-46.6,-23.5,AIR,1,Aeroporto X,\n"


def test_overpass_repete_a_consulta_apos_falha(monkeypatch):
    tentativas = []

    def flaky(*args, **kwargs):
        tentativas.append(1)
        if len(tentativas) < 2:
            raise urllib.error.URLError("caiu")
        return FakeResponse(b'{"elements": [{"id": 1}]}')

    monkeypatch.setattr(sources.urllib.request, "urlopen", flaky)
    monkeypatch.setattr(sources.time, "sleep", lambda _s: None)
    assert sources._overpass("query", tries=3) == [{"id": 1}]
    assert len(tentativas) == 2


def test_overpass_desiste_apos_as_tentativas(monkeypatch):
    def sempre_falha(*args, **kwargs):
        raise urllib.error.URLError("fora do ar")

    monkeypatch.setattr(sources.urllib.request, "urlopen", sempre_falha)
    monkeypatch.setattr(sources.time, "sleep", lambda _s: None)
    with pytest.raises(urllib.error.URLError):
        sources._overpass("query", tries=2)


def test_overpass_usa_a_ultima_tentativa(monkeypatch):
    """Depois das repetições, a chamada final é a que vale — e propaga o erro se falhar."""
    chamadas = []

    def falha_uma_vez(*args, **kwargs):
        chamadas.append(1)
        if len(chamadas) == 1:
            raise urllib.error.URLError("primeira falhou")
        return FakeResponse(b'{"elements": [{"id": 9}]}')

    monkeypatch.setattr(sources.urllib.request, "urlopen", falha_uma_vez)
    monkeypatch.setattr(sources.time, "sleep", lambda _s: None)
    assert sources._overpass("query", tries=2) == [{"id": 9}]


def test_acquire_baixa_viagens_e_equipamentos(tmp_path, configure, monkeypatch):
    settings = configure(sources, sources_dir=tmp_path / "fontes")
    order: list[str] = []
    for step in ("download_flows", "pois"):
        monkeypatch.setattr(sources, step, lambda step=step: order.append(step))
    sources.acquire()
    assert settings.sources_dir.is_dir()
    assert order == ["download_flows", "pois"]
