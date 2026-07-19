"""Testes do mapa HTML (folium) de :mod:`demand_data.htmlmap`."""

from __future__ import annotations

import json
import re
from datetime import datetime
from types import SimpleNamespace

import pytest

from demand_data import htmlmap, od

BASE_LNG, BASE_LAT = -46.60, -23.55


class FrozenDatetime(datetime):
    """``datetime`` com ``now`` fixo, para o carimbo do mapa ser determinístico."""

    @classmethod
    def now(cls, tz=None):
        return cls(2026, 3, 4, 5, 6)


def make_points(total: int, residents: int = 10, jobs: int = 5) -> list[dict]:
    return [
        {
            "id": f"pop-{index}",
            "location": [BASE_LNG + index * 0.0001, BASE_LAT + index * 0.0001],
            "residents": residents,
            "jobs": jobs,
        }
        for index in range(total)
    ]


def listener_block(html: str) -> str:
    """Recorta o bloco ``window.addEventListener('load', ...)`` por balanço de chaves."""
    start = html.index("window.addEventListener('load'")
    depth = 0
    for index in range(start, len(html)):
        if html[index] == "{":
            depth += 1
        elif html[index] == "}":
            depth -= 1
            if depth == 0:
                return html[start : index + 1]
    raise AssertionError("o bloco do listener não fecha")


@pytest.fixture
def render(tmp_path, monkeypatch):
    """Gera o mapa num arquivo temporário e devolve ``(html, path)``."""
    monkeypatch.setattr(htmlmap, "datetime", FrozenDatetime)

    def _render(points, zones=None, name="mapa.html"):
        path = tmp_path / name
        htmlmap.write(points, (BASE_LNG, BASE_LAT), path, zones=zones)
        return path.read_text(encoding="utf-8"), path

    return _render


@pytest.fixture
def zones(zones_shp):
    return od.load_zones(zones_shp)


def test_round_floats_arredonda_listas_aninhadas():
    origem = [[[-46.601234567, -23.551234567]], [[-46.6, -23.5]]]

    assert htmlmap._round_floats(origem) == [[[-46.60123, -23.55123]], [[-46.6, -23.5]]]


def test_round_floats_converte_tuplas_em_listas():
    assert htmlmap._round_floats(((-46.601234567, -23.551234567),)) == [[-46.60123, -23.55123]]


def test_round_floats_preserva_valores_que_nao_sao_float():
    assert htmlmap._round_floats([1, "zona", None, True]) == [1, "zona", None, True]


def test_point_rows_inverte_lng_lat_e_arredonda():
    points = [{"id": "pop-1", "location": [-46.601234567, -23.551234567],
               "residents": 12, "jobs": 34}]

    assert htmlmap._point_rows(points) == [[-23.55123, -46.60123, 12, 34, "pop-1"]]


def test_point_rows_assume_zero_sem_residents_e_jobs():
    assert htmlmap._point_rows([{"id": "pop-1", "location": [-46.6, -23.55]}]) == [
        [-23.55, -46.6, 0, 0, "pop-1"]
    ]


def test_zone_outlines_gera_uma_feature_por_zona(zones):
    outlines = htmlmap._zone_outlines(zones)

    assert outlines["type"] == "FeatureCollection"
    assert len(outlines["features"]) == len(zones.ids)
    assert [f["properties"]["zona"] for f in outlines["features"]] == zones.ids
    assert all(f["type"] == "Feature" for f in outlines["features"])


def test_zone_outlines_arredonda_as_coordenadas():
    from shapely.geometry import Polygon

    poligono = Polygon([(-46.601234567, -23.551234567), (-46.591234567, -23.551234567),
                        (-46.591234567, -23.541234567), (-46.601234567, -23.541234567)])
    outlines = htmlmap._zone_outlines(SimpleNamespace(ids=[7], polygons=[poligono]))

    coordenadas = outlines["features"][0]["geometry"]["coordinates"][0]
    assert outlines["features"][0]["properties"]["zona"] == 7
    assert all(valor == round(valor, 5) for par in coordenadas for valor in par)


def test_regressao_circulos_criados_dentro_do_listener_de_load(render):
    """Sem o listener de ``load`` o mapa renderiza em branco.

    O folium escreve o JS do mapa e do FeatureGroup DEPOIS de qualquer script somado ao
    root, então rodar direto usaria variáveis que ainda não existem.
    """
    html, _ = render(make_points(3))

    bloco = listener_block(html)
    assert ".addTo(group)" in bloco
    assert html.count(".addTo(group)") == bloco.count(".addTo(group)")

    variavel_do_grupo = re.search(r"var group = (feature_group_\w+);", bloco).group(1)
    declaracao = f"var {variavel_do_grupo} = L.featureGroup("
    assert declaracao in html
    assert html.index(declaracao) > html.index("window.addEventListener('load'")


def test_pontos_embutidos_como_json_compacto(render):
    html, _ = render(make_points(2))

    embutido = re.search(r"var points = (\[.*?\]);\n", html, re.S).group(1)
    assert ", " not in embutido
    assert json.loads(embutido) == htmlmap._point_rows(make_points(2))


def test_carimbo_traz_data_de_geracao_no_title_e_no_rodape(render):
    html, _ = render(make_points(4321))

    assert "<title>Pops de demanda RMSP — 04/03/2026 05:06</title>" in html
    assert '<div class="demand-stamp">4.321 pontos · gerado em 04/03/2026 05:06</div>' in html
    assert ".demand-stamp {" in html


def test_html_fica_bem_abaixo_de_700_bytes_por_ponto(render):
    _, path = render(make_points(5000))

    assert path.stat().st_size / 5000 < 200


def test_write_sem_zonas_nao_desenha_os_limites(render):
    html, _ = render(make_points(2))

    assert "limites das zonas" not in html
    assert "pontos de demanda" in html


def test_write_com_zonas_desenha_os_limites(render, zones):
    html, _ = render(make_points(2), zones=zones)

    assert "limites das zonas" in html
    assert "zona OD:" in html
    assert '"zona": 1' in html.replace("&quot;", '"')
