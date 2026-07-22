"""Testes do mapa HTML (folium) de :mod:`demand_data.htmlmap`."""

from __future__ import annotations

import json
import re
from datetime import datetime

import pytest

from demand_data import htmlmap

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

    def _render(points, name="mapa.html"):
        path = tmp_path / name
        htmlmap.write(points, (BASE_LNG, BASE_LAT), path)
        return path.read_text(encoding="utf-8"), path

    return _render


def test_point_rows_inverte_lng_lat_e_arredonda():
    points = [{"id": "pop-1", "location": [-46.601234567, -23.551234567],
               "residents": 12, "jobs": 34}]

    assert htmlmap._point_rows(points) == [[-23.55123, -46.60123, 12, 34, 1, 0, 0]]


def test_point_rows_assume_zero_sem_residents_e_jobs():
    assert htmlmap._point_rows([{"id": "z7h1", "location": [-46.6, -23.55]}]) == [
        [-23.55, -46.6, 0, 0, 0, 7, 0]
    ]


def test_regressao_circulos_criados_dentro_do_listener_de_load(render):
    """Sem o listener de ``load`` o mapa renderiza em branco.

    O folium escreve o JS do mapa e do FeatureGroup DEPOIS de qualquer script somado ao
    root, então rodar direto usaria variáveis que ainda não existem.
    """
    html, _ = render(make_points(3))

    bloco = listener_block(html)
    assert ".addTo(groups[kind])" in bloco
    assert html.count(".addTo(groups") == bloco.count(".addTo(groups")

    grupos = re.search(r"var groups = \{(.*?)\};", bloco).group(1)
    primeiro = re.search(r"(feature_group_\w+)", grupos).group(1)
    declaracao = f"var {primeiro} = L.featureGroup("
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


def test_kind_separa_as_camadas_do_mapa():
    poi = {"id": "SCH_Colegio", "name": "Colégio", "residents": 0, "jobs": 900}
    casa = {"id": "z1h1", "residents": 800, "jobs": 0}
    trabalho = {"id": "z1w1", "residents": 0, "jobs": 800}
    assert htmlmap._kind(poi) == "poi"
    assert htmlmap._kind(casa) == "home"
    assert htmlmap._kind(trabalho) == "work"


def test_point_rows_carrega_nome_e_camada():
    pontos = [{"id": "SCH_X", "name": "Colégio X", "location": [-46.6, -23.5],
               "residents": 0, "jobs": 700, "type": "SCH"}]
    linha = htmlmap._point_rows(pontos)[0]
    assert linha[4] == 2, "camada dos equipamentos"
    assert linha[5] == "Colégio X"
    assert linha[6] == htmlmap._TYPE_INDEX["SCH"]
    assert linha[7] == 0, "prioridade do rótulo: o maior vem primeiro"


def test_mapa_cria_uma_camada_por_tipo(tmp_path):
    pontos = [
        {"id": "z1h1", "location": [-46.6, -23.5], "residents": 100, "jobs": 0},
        {"id": "z1w1", "location": [-46.5, -23.5], "residents": 0, "jobs": 100},
        {"id": "SPO_Arena", "name": "Arena", "location": [-46.4, -23.5],
         "residents": 0, "jobs": 90},
    ]
    destino = tmp_path / "mapa.html"
    htmlmap.write(pontos, (-46.5, -23.5), destino)
    html = destino.read_text(encoding="utf-8")
    for rotulo in ("moradia (1)", "destinos (1)", "equipamentos (1)"):
        assert rotulo in html
    assert "conexões externas" not in html


def test_mapa_so_mostra_rotulo_que_cabe(tmp_path):
    """Milhares de equipamentos: sem disputa por espaço os nomes viram uma mancha."""
    pontos = [{"id": f"SPO_Arena{i}", "name": f"Arena {i}", "location": [-46.4, -23.5],
               "residents": 0, "jobs": 90 - i} for i in range(3)]
    destino = tmp_path / "mapa.html"
    htmlmap.write(pontos, (-46.5, -23.5), destino)
    html = destino.read_text(encoding="utf-8")
    assert ".poi-label span" in html
    assert "MAX_LABELS" in html
    assert "labels.sort" in html, "os maiores reservam espaço primeiro"


def test_regressao_rotulos_nascem_e_morrem_com_a_tela(tmp_path):
    """Um elemento por equipamento fazia o Leaflet remexer milhares de nós a cada zoom.

    Só os rótulos exibidos podem existir no DOM, e o recálculo espera o mapa parar:
    refazê-lo a cada evento intermediário travava o zoom e piscava a tela.
    """
    pontos = [{"id": "SPO_Arena", "name": "Arena", "location": [-46.4, -23.5],
               "residents": 0, "jobs": 90}]
    destino = tmp_path / "mapa.html"
    htmlmap.write(pontos, (-46.5, -23.5), destino)
    bloco = listener_block(destino.read_text(encoding="utf-8"))

    assert "L.marker(" in bloco.split("function declutter")[1], "rótulo criado sob demanda"
    assert "L.marker(" not in bloco.split("function declutter")[0], "nenhum marcador fixo"
    assert "labelLayer.clearLayers()" in bloco
    assert "setTimeout(declutter, 60)" in bloco
    assert "zoomstart" not in bloco, "apagar o rótulo a cada movimento faz piscar"


def test_point_rows_ordena_a_prioridade_pela_demanda():
    pontos = [
        {"id": "SPO_Pequeno", "name": "Pequeno", "location": [-46.4, -23.5],
         "residents": 0, "jobs": 10},
        {"id": "SPO_Grande", "name": "Grande", "location": [-46.5, -23.5],
         "residents": 0, "jobs": 900},
    ]
    linhas = {row[5]: row[7] for row in htmlmap._point_rows(pontos)}
    assert linhas["Grande"] < linhas["Pequeno"]


def test_regressao_zoom_e_continuo(render):
    """Com ``zoomSnap`` inteiro o Leaflet arredonda para cima qualquer fração de rolagem.

    Um gesto só virava três saltos de um nível, parando entre eles: uma escada visual.
    """
    html, _ = render(make_points(2))

    assert '"zoomSnap": 0' in html
    assert '"zoomDelta": 1' in html, "os botões + e − seguem andando um nível"
    assert '"zoomAnimation": false' in html, "esperar a transição atrasa o passo seguinte"


def test_zone_of_extrai_o_numero_da_zona():
    assert htmlmap._zone_of("z73w12") == 73
    assert htmlmap._zone_of("z301h5") == 301
    assert htmlmap._zone_of("SCH_Colegio") == 0
