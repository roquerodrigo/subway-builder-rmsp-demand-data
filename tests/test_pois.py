"""Equipamentos nomeados: leitura do OSM, porte medido e captura por motivo da viagem."""

from __future__ import annotations

import pytest
from tests.conftest import BASE_LAT, BASE_LNG, cells

from demand_data import pois
from demand_data.od import HEALTH, LEISURE, SCHOOL, SHOPPING, WORK
from demand_data.pops import ACTIVITY_FIELD


def poi(type_code, name, dx=0.01, dy=0.01, osm_id="1"):
    return {"location": [BASE_LNG + dx, BASE_LAT + dy], "type": type_code,
            "osm_id": osm_id, "name": name}


def zone_cells(zone_weight=100.0, near_weight=100.0):
    """Células da zona 1: uma colada no equipamento, outra longe."""
    return {1: cells((10, 10, 0.0, near_weight), (30, 30, 0.0, zone_weight))}


def demand(sizes=(200, 150, 150), activity=WORK):
    points = [
        {"id": "z1h1", "location": [BASE_LNG, BASE_LAT], "jobs": 0, "residents": sum(sizes),
         "popIds": []},
        {"id": "z1w1", "location": [BASE_LNG + 0.02, BASE_LAT], "jobs": sum(sizes),
         "residents": 0, "popIds": []},
    ]
    pops = [{"id": f"p{i}", "size": size, "residenceId": "z1h1", "jobId": "z1w1",
             ACTIVITY_FIELD: activity} for i, size in enumerate(sizes)]
    return points, pops


def test_load_le_o_csv_do_openstreetmap(tmp_path):
    path = tmp_path / "pois.csv"
    path.write_text("-46.6,-23.5,AIR,123,Aeroporto Um\n"
                    "linha ruim\n"
                    "x,y,UNI,9,Sem coordenada\n", encoding="utf-8")
    assert pois.load(path) == [{"location": [-46.6, -23.5], "type": "AIR",
                               "osm_id": "123", "name": "Aeroporto Um"}]


def test_load_sem_arquivo_avisa(tmp_path, caplog):
    with caplog.at_level("WARNING"):
        assert pois.load(tmp_path / "ausente.csv") == []
    assert "rode `sources`" in caplog.text


def test_locate_descobre_a_zona(zones_shp):
    from demand_data.od import load_zones

    located = pois.locate(load_zones(zones_shp), [poi("AIR", "Aeroporto Teste")])
    assert located[0]["zone"] == 1
    assert located[0]["id"] == "AIR_Aeroporto_Teste"


def test_locate_descarta_fora_do_recorte(zones_shp):
    from demand_data.od import load_zones

    fora = [{"location": [BASE_LNG - 10, BASE_LAT], "type": "AIR", "osm_id": "1", "name": "F"}]
    assert pois.locate(load_zones(zones_shp), fora) == []


def test_measure_mede_o_porte_pela_atividade_ao_redor():
    """O porte substitui a capacidade declarada: sai da atividade medida no entorno."""
    located = [{"zone": 1, "location": [BASE_LNG + 0.010, BASE_LAT + 0.010]}]
    pois.measure(located, zone_cells(zone_weight=300.0, near_weight=100.0))
    assert located[0]["share"] == pytest.approx(0.25)


def test_measure_sem_atividade_na_zona():
    located = [{"zone": 99, "location": [BASE_LNG, BASE_LAT]}]
    pois.measure(located, {})
    assert located[0]["share"] == 0.0


def test_capture_respeita_o_motivo_da_viagem(zones_shp, configure):
    """Um pop de compras não pode virar visita ao hospital."""
    from demand_data.od import load_zones

    configure(pois, poi_max_zone_share=1.0, min_pop_size=10)
    points, pops = demand(activity=SHOPPING)
    assert pois.capture(points, pops, load_zones(zones_shp), zone_cells(),
                        [poi("HOS", "Hospital Teste")]) == []
    assert all(p["jobId"] == "z1w1" for p in pops)


def test_capture_aceita_o_motivo_correspondente(zones_shp, configure):
    from demand_data.od import load_zones

    configure(pois, poi_max_zone_share=1.0, min_pop_size=10)
    points, pops = demand(activity=SHOPPING)
    created = pois.capture(points, pops, load_zones(zones_shp), zone_cells(),
                           [poi("SHP", "Shopping Teste")])
    assert len(created) == 1
    assert any(p["jobId"] == created[0]["id"] for p in pops)


def test_capture_aceita_trabalho_em_qualquer_tipo(zones_shp, configure):
    """Gente trabalha em hospital, aeroporto e zoológico."""
    from demand_data.od import load_zones

    configure(pois, poi_max_zone_share=1.0, min_pop_size=10)
    points, pops = demand(activity=WORK)
    created = pois.capture(points, pops, load_zones(zones_shp), zone_cells(),
                           [poi("ZOO", "Zoológico Teste")])
    assert created and any(p["jobId"] == created[0]["id"] for p in pops)


def test_capture_preserva_a_populacao(zones_shp, configure):
    from demand_data.od import load_zones

    configure(pois, poi_max_zone_share=0.5, min_pop_size=10)
    points, pops = demand()
    total = sum(p["size"] for p in pops)
    pois.capture(points, pops, load_zones(zones_shp), zone_cells(), [poi("SHP", "Loja")])
    assert sum(p["size"] for p in pops) == total
    assert len({p["id"] for p in pops}) == len(pops)


def test_capture_limita_pelo_porte_medido(zones_shp, configure):
    """Metade da atividade da zona ao redor do equipamento = metade da demanda."""
    from demand_data.od import load_zones

    configure(pois, poi_max_zone_share=1.0, min_pop_size=10)
    points, pops = demand(sizes=(400, 400, 200))
    created = pois.capture(points, pops, load_zones(zones_shp),
                           zone_cells(zone_weight=100.0, near_weight=100.0),
                           [poi("SHP", "Loja")])
    capturado = sum(p["size"] for p in pops if p["jobId"] == created[0]["id"])
    assert capturado == pytest.approx(500, rel=0.05)


def test_capture_deixa_demanda_para_a_zona(zones_shp, configure):
    from demand_data.od import load_zones

    configure(pois, poi_max_zone_share=0.6, min_pop_size=10)
    points, pops = demand()
    created = pois.capture(points, pops, load_zones(zones_shp),
                           zone_cells(zone_weight=0.0, near_weight=100.0),
                           [poi("SHP", "Loja")])
    na_zona = sum(p["size"] for p in pops if p["jobId"] == "z1w1")
    capturado = sum(p["size"] for p in pops if p["jobId"] == created[0]["id"])
    assert na_zona > 0, "o teto impede um equipamento de levar a zona inteira"
    assert capturado == pytest.approx(0.6 * (na_zona + capturado), rel=0.05)


def test_capture_sem_equipamentos():
    assert pois.capture([], [], None, {}, []) == []


def test_shares_reparte_proporcionalmente_sem_exceder():
    assert pois._shares([100, 200, 700], 100) == [10, 20, 70]
    assert sum(pois._shares([10, 20, 30], 37)) == 37
    assert pois._shares([5, 5], 0) == [0, 0]
    repartido = pois._shares([1, 1, 100], 3)
    assert all(part <= size for part, size in zip(repartido, [1, 1, 100], strict=True))


def classify_case(activities):
    points = [{"id": "z1h1", "location": [BASE_LNG, BASE_LAT], "jobs": 0, "residents": 10}]
    pops = []
    for index, activity in enumerate(activities):
        point_id = f"z1w{index}"
        points.append({"id": point_id, "location": [BASE_LNG + 0.001 * index, BASE_LAT],
                       "jobs": 100, "residents": 0})
        pops.append({"id": f"p{index}", "size": 100, "residenceId": "z1h1",
                     "jobId": point_id, ACTIVITY_FIELD: activity})
    return points, pops


def test_classify_tipa_o_destino_pelo_motivo_que_o_alimenta():
    points, pops = classify_case([HEALTH, SHOPPING, SCHOOL, LEISURE])
    pois.classify(points, pops, {})
    tipos = {p["id"]: p.get("type") for p in points if p.get("type")}
    assert tipos == {"z1w0": "HOS", "z1w1": "SHP", "z1w2": "SCH", "z1w3": "PRK"}


def test_classify_usa_o_motivo_dominante():
    points, pops = classify_case([HEALTH])
    pops.append({"id": "extra", "size": 500, "residenceId": "z1h1", "jobId": "z1w0",
                 ACTIVITY_FIELD: SHOPPING})
    pois.classify(points, pops, {})
    assert next(p for p in points if p["id"] == "z1w0")["type"] == "SHP"


def test_classify_cobre_todo_motivo_que_chega_na_zona():
    """Se alguém vai à zona por saúde, a zona tem um destino de saúde."""
    points, pops = classify_case([SHOPPING])
    pops.append({"id": "extra", "size": 10, "residenceId": "z1h1", "jobId": "z1w0",
                 ACTIVITY_FIELD: HEALTH})
    pois.classify(points, pops, {1: cells((5, 5, 0.0, 50.0))})
    tipos = {p.get("type") for p in points if p.get("type")}
    assert "HOS" in tipos and "SHP" in tipos


def test_classify_nao_rouba_a_cobertura_de_outro_motivo():
    """Retipar o ponto que sustenta um motivo deixaria esse motivo a descoberto."""
    points, pops = classify_case([HEALTH, SHOPPING, SCHOOL])
    for activity in (LEISURE, HEALTH, SHOPPING):
        pops.append({"id": f"x{activity}", "size": 5, "residenceId": "z1h1",
                     "jobId": "z1w0", ACTIVITY_FIELD: activity})
    pois.classify(points, pops, {1: cells((5, 5, 0.0, 50.0))})
    tipos = [p.get("type") for p in points if p.get("type")]
    assert "HOS" in tipos and "SHP" in tipos and "SCH" in tipos


def test_classify_ignora_equipamento_nomeado():
    points, pops = classify_case([HEALTH])
    points[1]["name"] = "Hospital Nomeado"
    points[1]["type"] = "HOS"
    pois.classify(points, pops, {})
    assert points[1]["type"] == "HOS"


def test_capture_leva_o_pop_inteiro_quando_a_fatia_o_cobre(zones_shp, configure):
    from demand_data.od import load_zones

    configure(pois, poi_max_zone_share=1.0, min_pop_size=10)
    points, pops = demand(sizes=(100,))
    created = pois.capture(points, pops, load_zones(zones_shp),
                           zone_cells(zone_weight=0.0, near_weight=100.0),
                           [poi("SHP", "Loja")])
    assert len(pops) == 1, "sem pop novo quando o equipamento leva o pop inteiro"
    assert pops[0]["jobId"] == created[0]["id"]


def test_capture_ignora_fatia_zero(zones_shp, configure):
    from demand_data.od import load_zones

    configure(pois, poi_max_zone_share=0.01, min_pop_size=1)
    points, pops = demand(sizes=(1000, 1))
    pois.capture(points, pops, load_zones(zones_shp),
                 zone_cells(zone_weight=0.0, near_weight=100.0), [poi("SHP", "Loja")])
    assert any(p["jobId"] == "z1w1" for p in pops)


def test_classify_cria_destino_quando_um_ponto_serve_dois_motivos():
    """Zona com um único candidato para dois motivos: só criando um destino a mais."""
    points, pops = classify_case([HEALTH])
    pops.append({"id": "lazer", "size": 90, "residenceId": "z1h1", "jobId": "z1w0",
                 ACTIVITY_FIELD: LEISURE})
    pois.classify(points, pops, {1: cells((7, 7, 0.0, 80.0))})
    tipos = {p.get("type") for p in points if p.get("type")}
    assert {"HOS", "PRK"} <= tipos
    assert any(p["id"].startswith("PRK_z") or p["id"].startswith("HOS_z") for p in points)


def test_classify_sem_celulas_nao_cria_destino():
    points, pops = classify_case([HEALTH])
    pops.append({"id": "lazer", "size": 90, "residenceId": "z1h1", "jobId": "z1w0",
                 ACTIVITY_FIELD: LEISURE})
    antes = len(points)
    pois.classify(points, pops, {})
    assert len(points) == antes


def test_spare_protege_o_unico_ponto_de_um_tipo():
    ponto = {"id": "z1w0", "type": "HOS"}
    outro = {"id": "z1w1", "type": "HOS"}
    assert pois._spare({"id": "z1w9"}, 1, [ponto]) is True, "sem tipo, pode ser usado"
    assert pois._spare(ponto, 1, [ponto]) is False, "é a única cobertura do tipo"
    assert pois._spare(ponto, 1, [ponto, outro]) is True, "há outro do mesmo tipo"


def test_classify_retipa_um_ponto_livre_quando_existe():
    """Havendo candidato sobrando, cobre o motivo sem precisar criar destino."""
    points, pops = classify_case([HEALTH, HEALTH])
    pops.append({"id": "lazer", "size": 5, "residenceId": "z1h1", "jobId": "z1w1",
                 ACTIVITY_FIELD: LEISURE})
    antes = len(points)
    pois.classify(points, pops, {1: cells((7, 7, 0.0, 80.0))})
    assert len(points) == antes, "não cria destino quando dá para retipar"
    assert {"HOS", "PRK"} <= {p.get("type") for p in points if p.get("type")}


def test_new_destination_sem_pops_do_motivo():
    points = [{"id": "z1w0", "location": [BASE_LNG, BASE_LAT], "jobs": 10, "residents": 0}]
    pops = [{"id": "p", "size": 10, "residenceId": "z1h1", "jobId": "z1w0",
             ACTIVITY_FIELD: WORK}]
    assert pois._new_destination(points, pops, 1, HEALTH, "HOS",
                                 {1: cells((1, 1, 0.0, 5.0))}) is None
