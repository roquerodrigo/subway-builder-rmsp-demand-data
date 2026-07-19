"""Roteamento por OSRM: consulta, fallback em linha reta e preenchimento dos pops."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from demand_data import routing

SAO_PAULO = [-46.63, -23.55]
SANTOS = [-46.33, -23.96]


@pytest.fixture
def osrm():
    """Servidor OSRM mínimo: responde rotas fixas e permite forçar erro."""
    state = {"code": "Ok", "distance": 12345.6, "duration": 987.6, "status": 200, "calls": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["calls"] += 1
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            body = {"code": state["code"]}
            if state["code"] == "Ok":
                body["routes"] = [{"distance": state["distance"], "duration": state["duration"]}]
            self.wfile.write(json.dumps(body).encode())

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["url"] = f"http://127.0.0.1:{server.server_port}"
    yield state
    server.shutdown()


def test_haversine_bate_com_a_distancia_conhecida():
    """São Paulo a Santos são ~55 km em linha reta."""
    metres = routing.haversine(*SAO_PAULO, *SANTOS)
    assert 53000 < metres < 57000


def test_haversine_de_um_ponto_para_ele_mesmo():
    assert routing.haversine(*SAO_PAULO, *SAO_PAULO) == pytest.approx(0.0)


def test_straight_line_usa_velocidade_media():
    distance, seconds = routing.straight_line(SAO_PAULO, SANTOS)
    assert distance == pytest.approx(routing.haversine(*SAO_PAULO, *SANTOS), abs=1)
    assert seconds == pytest.approx(distance / (30 * 1000 / 3600), rel=0.01)


def test_route_le_a_resposta_do_servidor(osrm):
    assert routing.route(SAO_PAULO, SANTOS, osrm["url"]) == (12345, 987)


def test_route_cai_para_linha_reta_sem_rota(osrm):
    osrm["code"] = "NoRoute"
    distance, seconds = routing.route(SAO_PAULO, SANTOS, osrm["url"])
    assert (distance, seconds) == routing.straight_line(SAO_PAULO, SANTOS)


def test_route_cai_para_linha_reta_com_servidor_fora():
    distance, _seconds = routing.route(SAO_PAULO, SANTOS, "http://127.0.0.1:1")
    assert distance == routing.straight_line(SAO_PAULO, SANTOS)[0]


def test_route_cai_para_linha_reta_com_erro_http(osrm):
    osrm["status"] = 500
    distance, _seconds = routing.route(SAO_PAULO, SANTOS, osrm["url"])
    assert distance == routing.straight_line(SAO_PAULO, SANTOS)[0]


def points_and_pops():
    points = [
        {"id": "casa", "location": SAO_PAULO, "jobs": 0, "residents": 10},
        {"id": "trabalho", "location": SANTOS, "jobs": 10, "residents": 0},
    ]
    pops = [{"id": "p1", "size": 10, "residenceId": "casa", "jobId": "trabalho",
             "drivingSeconds": 0, "drivingDistance": 0}]
    return points, pops


def test_fill_preenche_os_pops(osrm):
    points, pops = points_and_pops()
    assert routing.fill(points, pops, osrm["url"]) == 1
    assert pops[0]["drivingDistance"] == 12345
    assert pops[0]["drivingSeconds"] == 987


def test_fill_pula_o_que_ja_tem_rota(osrm):
    points, pops = points_and_pops()
    pops[0]["drivingSeconds"] = 600
    assert routing.fill(points, pops, osrm["url"]) == 0
    assert osrm["calls"] == 0
    assert pops[0]["drivingSeconds"] == 600


def test_fill_sem_pops_pendentes(osrm):
    assert routing.fill([], [], osrm["url"]) == 0


def test_default_url_vem_da_configuracao(configure):
    configure(routing, osrm_url="http://exemplo:5000")
    assert routing.default_url() == "http://exemplo:5000"
