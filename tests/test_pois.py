"""Equipamentos nomeados: leitura do OSM, porte pela área e adoção por proximidade."""

from __future__ import annotations

from tests.conftest import BASE_LAT, BASE_LNG

from demand_data import pois

DEST = (BASE_LNG, BASE_LAT)


def ring(lng, lat, half=0.0004):
    return [lng - half, lat - half, lng + half, lat - half,
            lng + half, lat + half, lng - half, lat + half]


def make_poi(type_code, name, offset, osm_id="1", half=0.0004):
    lng, lat = DEST[0] + offset, DEST[1]
    return {"location": [lng, lat], "type": type_code, "osm_id": osm_id,
            "name": name, "ring": ring(lng, lat, half)}


def demand(dest_type="SCH", jobs=300):
    points = [
        {"id": "z1h0", "location": [BASE_LNG - 0.02, BASE_LAT], "jobs": 0,
         "residents": jobs, "popIds": []},
        {"id": "z2w0", "location": [DEST[0], DEST[1]], "jobs": jobs,
         "residents": 0, "popIds": [], "type": dest_type},
    ]
    pops = [{"id": "p1", "size": jobs, "residenceId": "z1h0", "jobId": "z2w0"}]
    return points, pops


def test_area_mede_o_contorno():
    assert pois.area(ring(DEST[0], DEST[1], half=0.001)) > 0
    assert pois.area([]) == 0.0
    assert pois.area([1.0, 2.0]) == 0.0, "contorno curto não tem área"


def test_area_conserta_contorno_invalido():
    """Gravata-borboleta: anel que se cruza vira geometria inválida; o buffer(0) recupera."""
    bowtie = [-46.60, -23.50, -46.58, -23.48, -46.60, -23.48, -46.58, -23.50]
    assert pois.area(bowtie) >= 0


def test_adopt_pega_o_compativel_mais_proximo():
    points, pops_list = demand("SCH")
    catalogue = [
        make_poi("SCH", "Perto", 0.001, osm_id="10"),
        make_poi("UNI", "Longe", 0.003, osm_id="20"),
        make_poi("HOS", "Errado", 0.0005, osm_id="30"),
    ]
    adopted = pois.adopt(points, pops_list, catalogue)
    assert adopted == 1
    dest = points[1]
    assert dest["id"] == "SCH_Perto"
    assert dest["name"] == "Perto" and dest["osmId"] == "10"


def test_adopt_reaponta_os_pops():
    points, pops_list = demand("SCH")
    pois.adopt(points, pops_list, [make_poi("SCH", "Escola", 0.001)])
    assert pops_list[0]["jobId"] == "SCH_Escola"


def test_adopt_ignora_destino_fora_do_raio():
    points, pops_list = demand("SCH")
    adopted = pois.adopt(points, pops_list, [make_poi("SCH", "Distante", 0.02)])
    assert adopted == 0
    assert points[1]["id"] == "z2w0"


def test_adopt_desempata_por_porte():
    points, pops_list = demand("SHP")
    catalogue = [
        make_poi("SHP", "Pequeno", 0.001, osm_id="1", half=0.0002),
        make_poi("SHP", "Grande", 0.001, osm_id="2", half=0.001),
    ]
    pois.adopt(points, pops_list, catalogue)
    assert points[1]["name"] == "Grande", "à mesma distância, o maior porte vence"


def test_adopt_nao_toca_destino_sem_tipo():
    points, pops_list = demand("SCH")
    del points[1]["type"]
    adopted = pois.adopt(points, pops_list, [make_poi("SCH", "Escola", 0.001)])
    assert adopted == 0
    assert points[1]["id"] == "z2w0"


def test_adopt_sem_equipamento_util_nao_faz_nada():
    points, pops_list = demand("SCH")
    adopted = pois.adopt(points, pops_list, [make_poi("EXT", "Rodoviária", 0.001)])
    assert adopted == 0


def test_adopt_desambigua_homonimos_e_afasta_colisao():
    """Dois equipamentos homônimos na mesma coordenada: o id desempata pelo osm_id e a
    coordenada duplicada é afastada."""
    spot = (-46.599, -23.55)  # coordenada limpa: casa com o valor arredondado do ponto
    points = [
        {"id": "z1w0", "location": [-46.5992, -23.55], "jobs": 300, "residents": 0,
         "popIds": [], "type": "SCH"},
        {"id": "z2w0", "location": [-46.5988, -23.55], "jobs": 100, "residents": 0,
         "popIds": [], "type": "SCH"},
    ]
    pops_list = [
        {"id": "p1", "size": 300, "residenceId": "h1", "jobId": "z1w0"},
        {"id": "p2", "size": 100, "residenceId": "h2", "jobId": "z2w0"},
    ]
    catalogue = [
        {"location": list(spot), "type": "SCH", "osm_id": "1", "name": "Escola", "ring": []},
        {"location": list(spot), "type": "SCH", "osm_id": "2", "name": "Escola", "ring": []},
    ]
    assert pois.adopt(points, pops_list, catalogue) == 2
    ids = {p["id"] for p in points}
    assert ids == {"SCH_Escola", "SCH_Escola_2"}, "homônimo desempata pelo osm_id"
    coords = [tuple(p["location"]) for p in points]
    assert len(set(coords)) == len(coords), "a colisão de coordenada foi afastada"


def test_load_pula_linha_malformada(tmp_path, configure):
    settings = configure(pois, sources_dir=tmp_path)
    settings.pois_csv.parent.mkdir(parents=True, exist_ok=True)
    settings.pois_csv.write_text("campos,de,menos\nx,y,SCH,1,Nome,\n", encoding="utf-8")
    assert pois.load() == []


def test_load_le_o_csv(tmp_path, configure):
    settings = configure(pois, sources_dir=tmp_path)
    settings.pois_csv.parent.mkdir(parents=True, exist_ok=True)
    contour = f"{DEST[0]} {DEST[1]} {DEST[0] + 0.001} {DEST[1]}"
    settings.pois_csv.write_text(
        f"{DEST[0]},{DEST[1]},SCH,42,Escola Teste,{contour}\n",
        encoding="utf-8",
    )
    loaded = pois.load()
    assert len(loaded) == 1
    assert loaded[0]["type"] == "SCH" and loaded[0]["name"] == "Escola Teste"
    assert loaded[0]["osm_id"] == "42"


def test_load_avisa_quando_falta_o_arquivo(tmp_path, configure, caplog):
    configure(pois, sources_dir=tmp_path / "vazio")
    with caplog.at_level("WARNING"):
        assert pois.load() == []
    assert "rode `sources`" in caplog.text
