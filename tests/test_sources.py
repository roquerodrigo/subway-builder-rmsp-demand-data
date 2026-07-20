"""Testes da aquisição das fontes: parsers puros, download/zip e as etapas de ``acquire``."""

from __future__ import annotations

import json
import logging
import ssl
import urllib.error
import urllib.request
import zipfile

import pytest

from demand_data import sources

INSIDE_LNG, INSIDE_LAT = -46.60, -23.55
OUTSIDE_LNG, OUTSIDE_LAT = -40.00, -20.00

CNEFE_HEADER = "COD_UNICO_ENDERECO;COD_SETOR;COD_ESPECIE;LATITUDE;LONGITUDE"
CENSO_HEADER = '"CD_SETOR";"CD_MUN";"v0001"'


def cnefe_row(setor: str, especie, lat, lng, unique: str = "1") -> str:
    return f"{unique};{setor};{especie};{lat};{lng}"


def cnefe_lines(*rows: str) -> list[bytes]:
    return [f"{line}\r\n".encode("latin-1") for line in (CNEFE_HEADER, *rows)]


def censo_row(setor: str, pop, mun: str = "3550308") -> str:
    return f'"{setor}";"{mun}";"{pop}"'


def censo_lines(*rows: str) -> list[bytes]:
    return [f"{line}\r\n".encode("latin-1") for line in (CENSO_HEADER, *rows)]


def polygon(*points) -> dict:
    return {"type": "Polygon", "coordinates": [list(points)]}


def lote_feature(use: str | None, area, lng: float = INSIDE_LNG, lat: float = INSIDE_LAT) -> dict:
    return {
        "properties": {"dc_tipo_uso_imovel": use, "qt_area_construida": area},
        "geometry": polygon([lng, lat], [lng + 0.001, lat], [lng, lat + 0.001]),
    }


def write_zip(path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)


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


@pytest.fixture
def fake_download(monkeypatch):
    """Substitui ``_download`` por um que fabrica um zip local e registra as chamadas."""

    calls: list[tuple[str, object]] = []

    def _install(members: dict[str, bytes]):
        def _download(url, dest):
            calls.append((url, dest))
            write_zip(dest, members)

        monkeypatch.setattr(sources, "_download", _download)
        return calls

    return _install


@pytest.fixture
def forbid_download(monkeypatch):
    def _fail(url, dest):
        raise AssertionError(f"download inesperado: {url}")

    monkeypatch.setattr(sources, "_download", _fail)


def test_tally_counts_every_item():
    tally = sources._Tally([1, 2, 3])
    assert list(tally) == [1, 2, 3]
    assert tally.count == 3


def test_tally_counts_only_what_was_consumed():
    tally = sources._Tally(range(10))
    iterator = iter(tally)
    next(iterator)
    next(iterator)
    assert tally.count == 2


def test_tally_of_an_empty_source():
    tally = sources._Tally([])
    assert list(tally) == []
    assert tally.count == 0


def test_parse_cnefe_keeps_addresses_inside_the_bbox(configure):
    configure(sources)
    lines = cnefe_lines(cnefe_row("350000001", 1, INSIDE_LAT, INSIDE_LNG))
    assert list(sources.parse_cnefe(lines, frozenset())) == ["-46.6,-23.55,1,350000001\n"]


def test_parse_cnefe_reads_columns_by_name(configure):
    configure(sources)
    header = "LONGITUDE;LATITUDE;COD_SETOR;COD_ESPECIE"
    line = f"{INSIDE_LNG};{INSIDE_LAT};350000001;2"
    lines = [f"{header}\n".encode("latin-1"), f"{line}\n".encode("latin-1")]
    assert list(sources.parse_cnefe(lines, frozenset())) == ["-46.6,-23.55,2,350000001\n"]


def test_parse_cnefe_drops_the_skipped_especies(configure):
    configure(sources)
    lines = cnefe_lines(
        cnefe_row("350000001", 7, INSIDE_LAT, INSIDE_LNG),
        cnefe_row("350000001", 1, INSIDE_LAT, INSIDE_LNG),
    )
    rows = list(sources.parse_cnefe(lines, frozenset({7})))
    assert rows == ["-46.6,-23.55,1,350000001\n"]


def test_parse_cnefe_drops_addresses_outside_the_bbox(configure):
    configure(sources)
    lines = cnefe_lines(cnefe_row("350000001", 1, OUTSIDE_LAT, OUTSIDE_LNG))
    assert list(sources.parse_cnefe(lines, frozenset())) == []


def test_parse_cnefe_strips_the_situation_suffix(configure):
    configure(sources)
    lines = cnefe_lines(cnefe_row("350000001P", 1, INSIDE_LAT, INSIDE_LNG))
    assert list(sources.parse_cnefe(lines, frozenset()))[0].endswith(",1,350000001\n")


@pytest.mark.parametrize(
    "row",
    [
        cnefe_row("350000001", "", INSIDE_LAT, INSIDE_LNG),
        cnefe_row("350000001", "residencial", INSIDE_LAT, INSIDE_LNG),
        cnefe_row("350000001", 1, "", INSIDE_LNG),
        cnefe_row("350000001", 1, INSIDE_LAT, "sem-coordenada"),
        "1;350000001",
        "",
    ],
)
def test_parse_cnefe_skips_malformed_rows(configure, row):
    configure(sources)
    assert list(sources.parse_cnefe(cnefe_lines(row), frozenset())) == []


def test_parse_cnefe_decodes_latin1(configure):
    configure(sources)
    row = cnefe_row("350000001", 1, INSIDE_LAT, INSIDE_LNG, unique="Bar\xe3o")
    assert len(list(sources.parse_cnefe(cnefe_lines(row), frozenset()))) == 1


def test_parse_cnefe_honours_a_custom_bbox(configure):
    configure(sources, bbox=(0.0, 0.0, 1.0, 1.0))
    lines = cnefe_lines(
        cnefe_row("350000001", 1, 0.5, 0.5),
        cnefe_row("350000001", 1, INSIDE_LAT, INSIDE_LNG),
    )
    assert list(sources.parse_cnefe(lines, frozenset())) == ["0.5,0.5,1,350000001\n"]


def test_parse_censo_keeps_sao_paulo_sectors():
    lines = censo_lines(censo_row("350000001", 1000))
    assert list(sources.parse_censo(lines)) == ["350000001,1000\n"]


def test_parse_censo_drops_other_states():
    lines = censo_lines(censo_row("410000001", 1000), censo_row("350000001", 20))
    assert list(sources.parse_censo(lines)) == ["350000001,20\n"]


@pytest.mark.parametrize("pop", [0, -5, ""])
def test_parse_censo_drops_non_positive_population(pop):
    assert list(sources.parse_censo(censo_lines(censo_row("350000001", pop)))) == []


@pytest.mark.parametrize("row", ['"350000001";"3550308";"mil"', '"350000001"', ""])
def test_parse_censo_skips_malformed_rows(row):
    assert list(sources.parse_censo(censo_lines(row))) == []


def test_parse_censo_accepts_unquoted_cells():
    lines = [b"CD_SETOR;v0001\n", b" 350000001 ; 42 \n"]
    assert list(sources.parse_censo(lines)) == ["350000001,42\n"]


def test_parse_lotes_maps_the_uses(configure):
    configure(sources)
    features = [
        lote_feature("Residencial", 200),
        lote_feature("Condomínio", 150),
        lote_feature("Não residencial", 900),
    ]
    rows = sources.parse_lotes(features, sources.settings.lote_use_map)
    assert [row.split(",")[2] for row in rows] == ["R", "R", "N"]


def test_parse_lotes_rounds_the_centroid_and_the_area(configure):
    configure(sources)
    feature = {
        "properties": {"dc_tipo_uso_imovel": "Residencial", "qt_area_construida": 120.7},
        "geometry": polygon([-46.6000004, -23.55], [-46.6000004, -23.55]),
    }
    assert list(sources.parse_lotes([feature], {"Residencial": "R"})) == ["-46.6,-23.55,R,121\n"]


def test_parse_lotes_aceita_area_em_texto(configure):
    """O WFS às vezes devolve a área como string; antes isso estourava a página inteira."""
    configure(sources)
    feature = {
        "properties": {"dc_tipo_uso_imovel": "Residencial", "qt_area_construida": "200"},
        "geometry": polygon([-46.6, -23.55], [-46.6, -23.55]),
    }
    assert list(sources.parse_lotes([feature], {"Residencial": "R"})) == ["-46.6,-23.55,R,200\n"]


def test_parse_lotes_descarta_area_ilegivel(configure):
    configure(sources)
    feature = {
        "properties": {"dc_tipo_uso_imovel": "Residencial", "qt_area_construida": "muita"},
        "geometry": polygon([-46.6, -23.55], [-46.6, -23.55]),
    }
    assert list(sources.parse_lotes([feature], {"Residencial": "R"})) == []


def test_regressao_parse_cnefe_com_arquivo_vazio(configure):
    """Sem cabeçalho, o `next` dentro do gerador virava RuntimeError (PEP 479)."""
    configure(sources)
    assert list(sources.parse_cnefe(iter([]), frozenset())) == []


def test_regressao_parse_censo_com_arquivo_vazio(configure):
    configure(sources)
    assert list(sources.parse_censo(iter([]))) == []


def test_first_csv_exige_um_csv_no_zip(tmp_path):
    import zipfile

    archive = tmp_path / "vazio.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("leiame.txt", "sem csv")
    with zipfile.ZipFile(archive) as z, pytest.raises(ValueError, match="não contém nenhum CSV"):
        sources._first_csv(z, archive)


def test_regressao_download_interrompido_nao_vira_arquivo_final(tmp_path, monkeypatch):
    """Gravar direto no destino fazia a execução seguinte tratar um zip truncado como pronto."""

    class TorntStream:
        def read(self, _size):
            raise OSError("conexão caiu")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(sources.urllib.request, "urlopen", lambda *a, **k: TorntStream())
    destino = tmp_path / "arquivo.zip"
    with pytest.raises(OSError, match="conexão caiu"):
        sources._download("http://exemplo/arquivo.zip", destino)
    assert not destino.exists()


@pytest.mark.parametrize("use", ["Terreno", None, ""])
def test_parse_lotes_drops_unmapped_uses(configure, use):
    configure(sources)
    assert list(sources.parse_lotes([lote_feature(use, 200)], {"Residencial": "R"})) == []


@pytest.mark.parametrize("area", [0, -10, None])
def test_parse_lotes_drops_lots_without_built_area(configure, area):
    configure(sources)
    feature = lote_feature("Residencial", area)
    assert list(sources.parse_lotes([feature], {"Residencial": "R"})) == []


def test_parse_lotes_drops_features_without_properties(configure):
    configure(sources)
    features = [{}, {"properties": None, "geometry": polygon([INSIDE_LNG, INSIDE_LAT])}]
    assert list(sources.parse_lotes(features, {"Residencial": "R"})) == []


def test_parse_lotes_drops_features_without_geometry(configure):
    configure(sources)
    feature = {"properties": {"dc_tipo_uso_imovel": "Residencial", "qt_area_construida": 200}}
    assert list(sources.parse_lotes([feature], {"Residencial": "R"})) == []


def test_parse_lotes_drops_centroids_outside_the_bbox(configure):
    configure(sources)
    feature = lote_feature("Residencial", 200, lng=OUTSIDE_LNG, lat=OUTSIDE_LAT)
    assert list(sources.parse_lotes([feature], {"Residencial": "R"})) == []


def test_lote_centroid_averages_an_open_ring():
    geom = {"type": "Polygon", "coordinates": [[[0.0, 0.0], [2.0, 0.0], [1.0, 3.0]]]}
    assert sources._lote_centroid(geom) == (1.0, 1.0)


def test_lote_centroid_ignores_the_closing_vertex():
    ring = [[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0], [0.0, 0.0]]
    assert sources._lote_centroid({"type": "Polygon", "coordinates": [ring]}) == (1.0, 1.0)


def test_lote_centroid_uses_the_first_ring_of_a_multipolygon():
    geom = {
        "type": "MultiPolygon",
        "coordinates": [
            [[[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 0.0]]],
            [[[10.0, 10.0], [12.0, 10.0], [10.0, 10.0]]],
        ],
    }
    assert sources._lote_centroid(geom) == pytest.approx((4 / 3, 2 / 3))


def test_lote_centroid_accepts_a_single_vertex():
    assert sources._lote_centroid({"type": "Polygon", "coordinates": [[[1.0, 2.0]]]}) == (1.0, 2.0)


def test_lote_centroid_collapses_a_degenerate_ring():
    geom = {"type": "Polygon", "coordinates": [[[1.0, 2.0], [1.0, 2.0]]]}
    assert sources._lote_centroid(geom) == (1.0, 2.0)


@pytest.mark.parametrize(
    "geom",
    [{}, {"type": "Polygon"}, {"type": "Polygon", "coordinates": []},
     {"type": "Polygon", "coordinates": [[]]}],
)
def test_lote_centroid_returns_none_without_vertices(geom):
    assert sources._lote_centroid(geom) is None


def test_mb_reports_the_file_size(tmp_path):
    path = tmp_path / "arquivo.bin"
    path.write_bytes(b"\0" * 2_500_000)
    assert sources._mb(path) == pytest.approx(2.5)


def test_ssl_context_uses_the_certifi_bundle():
    assert isinstance(sources._ssl_context(), ssl.SSLContext)


def test_ssl_context_returns_none_when_the_bundle_is_unavailable(monkeypatch):
    def _fail(*args, **kwargs):
        raise OSError("bundle indisponível")

    monkeypatch.setattr(sources.ssl, "create_default_context", _fail)
    assert sources._ssl_context() is None


def test_download_writes_the_response_in_chunks(tmp_path, monkeypatch):
    payload = b"x" * (1 << 21)
    seen: list[str] = []

    def _urlopen(request, context=None):
        seen.append(request.full_url)
        assert request.headers["User-agent"]
        return FakeResponse(payload)

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    dest = tmp_path / "arquivo.zip"
    sources._download("https://exemplo/arquivo.zip", dest)
    assert dest.read_bytes() == payload
    assert seen == ["https://exemplo/arquivo.zip"]


def test_download_skips_an_existing_file(tmp_path, monkeypatch):
    def _urlopen(*args, **kwargs):
        raise AssertionError("não deveria baixar de novo")

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    dest = tmp_path / "arquivo.zip"
    dest.write_bytes(b"antigo")
    sources._download("https://exemplo/arquivo.zip", dest)
    assert dest.read_bytes() == b"antigo"


def test_od_extracts_the_zip(tmp_path, configure, fake_download):
    settings = configure(sources, sources_dir=tmp_path)
    calls = fake_download({"Site_190225/leiame.txt": b"conteudo"})
    sources.od()
    assert calls == [(settings.od_zip_url, settings.od_zip)]
    assert (settings.od_extract_dir / "Site_190225" / "leiame.txt").read_bytes() == b"conteudo"


def test_od_skips_an_already_extracted_directory(tmp_path, configure, forbid_download):
    settings = configure(sources, sources_dir=tmp_path)
    settings.od_extract_dir.mkdir(parents=True)
    sources.od()
    assert list(settings.od_extract_dir.iterdir()) == []


def test_cnefe_writes_the_filtered_csv(tmp_path, configure, fake_download):
    settings = configure(sources, sources_dir=tmp_path)
    body = "\r\n".join([
        CNEFE_HEADER,
        cnefe_row("350000001", 1, INSIDE_LAT, INSIDE_LNG),
        cnefe_row("350000001", 7, INSIDE_LAT, INSIDE_LNG),
        cnefe_row("350000002", 6, OUTSIDE_LAT, OUTSIDE_LNG),
        cnefe_row("350000002P", 4, INSIDE_LAT, INSIDE_LNG),
    ]).encode("latin-1")
    calls = fake_download({"leiame.txt": b"ignorado", "35_SP.csv": body})
    sources.cnefe()
    assert calls == [(settings.cnefe_url, settings.cnefe_zip)]
    assert settings.cnefe_csv.read_text(encoding="ascii").splitlines() == [
        "-46.6,-23.55,1,350000001",
        "-46.6,-23.55,4,350000002",
    ]


def test_cnefe_logs_how_much_was_read_and_kept(tmp_path, configure, fake_download, caplog):
    configure(sources, sources_dir=tmp_path)
    body = "\r\n".join([CNEFE_HEADER, cnefe_row("350000001", 1, INSIDE_LAT, INSIDE_LNG)])
    fake_download({"35_SP.csv": body.encode("latin-1")})
    with caplog.at_level(logging.INFO, logger="demand_data.sources"):
        sources.cnefe()
    assert "lidos=2 mantidos=1" in caplog.text


def test_cnefe_skips_an_existing_output(tmp_path, configure, forbid_download):
    settings = configure(sources, sources_dir=tmp_path)
    settings.cnefe_csv.write_text("ja-processado\n", encoding="ascii")
    sources.cnefe()
    assert settings.cnefe_csv.read_text(encoding="ascii") == "ja-processado\n"


def test_censo_writes_the_population_by_sector(tmp_path, configure, fake_download):
    settings = configure(sources, sources_dir=tmp_path)
    body = "\r\n".join([
        CENSO_HEADER,
        censo_row("350000001", 1000),
        censo_row("410000001", 900),
        censo_row("350000002", 0),
        censo_row("350000003", 12),
    ]).encode("latin-1")
    calls = fake_download({"leiame.txt": b"ignorado", "basico_BR.csv": body})
    sources.censo()
    assert calls == [(settings.censo_url, settings.censo_zip)]
    assert settings.setor_pop_csv.read_text(encoding="ascii").splitlines() == [
        "350000001,1000",
        "350000003,12",
    ]


def test_censo_logs_how_many_sectors_were_kept(tmp_path, configure, fake_download, caplog):
    configure(sources, sources_dir=tmp_path)
    body = "\r\n".join([CENSO_HEADER, censo_row("350000001", 1000)])
    fake_download({"basico_BR.csv": body.encode("latin-1")})
    with caplog.at_level(logging.INFO, logger="demand_data.sources"):
        sources.censo()
    assert "lidos=2 setores-SP=1" in caplog.text


def test_censo_skips_an_existing_output(tmp_path, configure, forbid_download):
    settings = configure(sources, sources_dir=tmp_path)
    settings.setor_pop_csv.write_text("ja-processado\n", encoding="ascii")
    sources.censo()
    assert settings.setor_pop_csv.read_text(encoding="ascii") == "ja-processado\n"


def test_get_json_decodes_the_response(monkeypatch):
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda url, timeout=None: FakeResponse(b'{"features": []}')
    )
    assert sources._get_json("https://exemplo/wfs") == {"features": []}


def test_get_json_retries_before_succeeding(monkeypatch):
    attempts = []
    naps = []

    def _urlopen(url, timeout=None):
        attempts.append(url)
        if len(attempts) < 3:
            raise urllib.error.URLError("instável")
        return FakeResponse(b"[1]")

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    monkeypatch.setattr(sources.time, "sleep", naps.append)
    assert sources._get_json("https://exemplo/wfs") == [1]
    assert len(attempts) == 3
    assert naps == [2, 4]


def test_get_json_raises_after_the_last_attempt(monkeypatch):
    attempts = []

    def _urlopen(url, timeout=None):
        attempts.append(url)
        raise urllib.error.URLError("fora do ar")

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    monkeypatch.setattr(sources.time, "sleep", lambda seconds: None)
    with pytest.raises(urllib.error.URLError):
        sources._get_json("https://exemplo/wfs", tries=2)
    assert len(attempts) == 2


def test_lotes_walks_every_page(tmp_path, configure, monkeypatch):
    settings = configure(sources, sources_dir=tmp_path, lote_page=2)
    pages = [
        {"features": [lote_feature("Residencial", 200), lote_feature("Terreno", 0)],
         "numberReturned": 2},
        {"features": [lote_feature("Não residencial", 900)], "numberReturned": 1},
    ]
    urls: list[str] = []

    def _get_json(url):
        urls.append(url)
        return pages[len(urls) - 1]

    monkeypatch.setattr(sources, "_get_json", _get_json)
    sources.lotes()
    assert len(urls) == 2
    assert "startIndex=0" in urls[0]
    assert "startIndex=2" in urls[1]
    assert settings.lotes_csv.read_text(encoding="ascii").splitlines() == [
        "-46.599667,-23.549667,R,200",
        "-46.599667,-23.549667,N,900",
    ]


def test_lotes_stops_when_the_page_count_is_missing(tmp_path, configure, monkeypatch):
    settings = configure(sources, sources_dir=tmp_path, lote_page=2)
    monkeypatch.setattr(sources, "_get_json", lambda url: {"features": []})
    sources.lotes()
    assert settings.lotes_csv.read_text(encoding="ascii") == ""


def test_lotes_logs_progress_every_hundred_thousand_lots(tmp_path, configure, monkeypatch, caplog):
    configure(sources, sources_dir=tmp_path, lote_page=100000)
    pages = [
        {"features": [lote_feature("Residencial", 200)], "numberReturned": 100000},
        {"features": [], "numberReturned": 0},
    ]
    monkeypatch.setattr(sources, "_get_json", lambda url: pages.pop(0))
    with caplog.at_level(logging.INFO, logger="demand_data.sources"):
        sources.lotes()
    assert "100000 lotes lidos (1 mantidos)" in caplog.text


def test_lotes_skips_an_existing_output(tmp_path, configure, monkeypatch):
    settings = configure(sources, sources_dir=tmp_path)
    settings.lotes_csv.write_text("ja-processado\n", encoding="ascii")

    def _fail(url):
        raise AssertionError("não deveria consultar o WFS")

    monkeypatch.setattr(sources, "_get_json", _fail)
    sources.lotes()
    assert settings.lotes_csv.read_text(encoding="ascii") == "ja-processado\n"


def test_lotes_asks_the_configured_layer(tmp_path, configure, monkeypatch):
    configure(sources, sources_dir=tmp_path, lote_page=5, lote_layer="geoportal:outra")
    urls: list[str] = []

    def _get_json(url):
        urls.append(url)
        return {"features": [], "numberReturned": 0}

    monkeypatch.setattr(sources, "_get_json", _get_json)
    sources.lotes()
    assert "typeNames=geoportal%3Aoutra" in urls[0]
    assert "outputFormat=application%2Fjson" in urls[0]


def test_acquire_runs_every_step_after_creating_the_directory(tmp_path, configure, monkeypatch):
    settings = configure(sources, sources_dir=tmp_path / "fontes")
    order: list[str] = []
    for step in ("od", "cnefe", "censo", "lotes", "pois"):
        monkeypatch.setattr(sources, step, lambda step=step: order.append(step))
    sources.acquire()
    assert settings.sources_dir.is_dir()
    assert order == ["od", "cnefe", "censo", "lotes", "pois"]


def test_download_is_reachable_through_the_public_steps(tmp_path, configure, monkeypatch):
    """A etapa ``od`` deve pedir a URL configurada ao ``_download`` real."""

    settings = configure(sources, sources_dir=tmp_path, od_zip_url="https://exemplo/od.zip")
    buffer = tmp_path / "fabricado.zip"
    write_zip(buffer, {"Site_190225/dados.txt": b"ok"})
    payload = buffer.read_bytes()
    seen: list[str] = []

    def _urlopen(request, context=None):
        seen.append(request.full_url)
        return FakeResponse(payload)

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    sources.od()
    assert seen == ["https://exemplo/od.zip"]
    assert (settings.od_extract_dir / "Site_190225" / "dados.txt").read_bytes() == b"ok"


def test_download_logs_that_the_file_already_exists(tmp_path, caplog):
    dest = tmp_path / "arquivo.zip"
    dest.write_bytes(b"pronto")
    with caplog.at_level(logging.INFO, logger="demand_data.sources"):
        sources._download("https://exemplo/arquivo.zip", dest)
    assert "já baixado" in caplog.text


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
    assert linhas == ["-46.6,-23.5,AIR,7,Aeroporto X\n"]


def test_code_for_reconhece_os_tipos():
    assert sources._code_for({"aeroway": "aerodrome"}) == "AIR"
    assert sources._code_for({"amenity": "hospital"}) == "HOS"
    assert sources._code_for({"leisure": "park"}) == "PRK"
    assert sources._code_for({"amenity": "cafe"}) is None


def test_pois_pula_quando_ja_existe(configure, tmp_path, caplog):
    settings = configure(sources, sources_dir=tmp_path)
    settings.pois_csv.write_text("-46.6,-23.5,AIR,1,X\n", encoding="utf-8")
    with caplog.at_level("INFO"):
        sources.pois()
    assert "já processado" in caplog.text


def test_overpass_query_cobre_o_recorte_e_os_tipos(configure):
    configure(sources, bbox=(-47.0, -24.0, -45.0, -23.0))
    query = sources._overpass_query()
    assert "(-24.0,-47.0,-23.0,-45.0)" in query
    assert query.count("nwr") == len(sources.POI_QUERIES)
    assert query.startswith("[out:json]") and query.rstrip().endswith("out center tags;")


def test_pois_baixa_e_escreve_o_csv(configure, tmp_path, monkeypatch):
    settings = configure(sources, sources_dir=tmp_path, bbox=(-47.0, -24.0, -45.0, -23.0))
    payload = {"elements": [
        {"id": 1, "tags": {"aeroway": "aerodrome", "name": "Aeroporto X"},
         "lat": -23.5, "lon": -46.6},
        {"id": 2, "tags": {"amenity": "cafe", "name": "Café"}, "lat": -23.5, "lon": -46.6},
    ]}
    monkeypatch.setattr(
        sources.urllib.request, "urlopen",
        lambda *a, **k: FakeResponse(json.dumps(payload).encode()),
    )
    sources.pois()
    assert settings.pois_csv.read_text(encoding="utf-8") == "-46.6,-23.5,AIR,1,Aeroporto X\n"
