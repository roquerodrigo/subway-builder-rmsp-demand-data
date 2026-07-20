"""Densidade: agregação das fontes, sorteio dos pontos e ancoragem em endereços reais."""

from __future__ import annotations

import numpy as np
import pytest
from tests.conftest import BASE_LAT, BASE_LNG, cells

from demand_data import density
from demand_data.config import settings


def test_unit_hash_e_deterministico():
    assert density._unit_hash(3, 7) == density._unit_hash(3, 7)
    assert density._unit_hash(3, 7) != density._unit_hash(7, 3)


def test_unit_hash_e_uniforme_em_zero_um():
    draws = np.array([density._unit_hash(x, y) for x in range(120) for y in range(120)])
    assert draws.min() > 0.0
    assert draws.max() < 1.0
    assert abs(draws.mean() - 0.5) < 0.01
    decis = np.histogram(draws, bins=10, range=(0, 1))[0] / len(draws)
    assert decis.min() > 0.08, "algum decil ficou vazio: a mistura não espalha"


def test_regressao_unit_hash_espalha_entradas_vizinhas():
    """Com FNV sobre inteiros os bits altos mal mudavam: entradas vizinhas caíam quase no
    mesmo valor e o sorteio ficava preso a uma faixa do mapa."""
    neighbours = [density._unit_hash(x, 0) for x in range(50)]
    spread = max(neighbours) - min(neighbours)
    assert spread > 0.8, f"entradas vizinhas ocupam só {spread:.3f} do intervalo"


def test_cell_indexa_pela_grade_configurada(configure):
    configure(density, density_cell=0.001, bbox=(-47.0, -24.0, -45.0, -23.0))
    step_lng = 0.001 * settings.m_per_deg_lat / settings.m_per_deg_lng
    assert density._cell(-47.0, -24.0) == (0, 0)
    assert density._cell(-46.9995, -23.9995) == (0, 0)
    assert density._cell(-47.0 + 2 * step_lng, -23.997) == (2, 3)


def test_keep_anchor_fica_com_o_maior_sorteio():
    acc = [0.0, 0.0, 0.0, 0.0, 0.0, -1.0, 0.0, 0.0]
    density._keep_anchor(acc, -46.6, -23.5)
    first = (acc[6], acc[7])
    for lng in (-46.601, -46.602, -46.603):
        density._keep_anchor(acc, lng, -23.5)
    candidates = [(-46.6, -23.5), (-46.601, -23.5), (-46.602, -23.5), (-46.603, -23.5)]
    best = max(candidates, key=lambda c: density._unit_hash(int(c[0] * 1e6), int(c[1] * 1e6)))
    assert (acc[6], acc[7]) == best
    assert acc[5] == pytest.approx(density._unit_hash(int(best[0] * 1e6), int(best[1] * 1e6)))
    assert first in candidates


def test_cells_by_zone_descarta_celulas_sem_peso():
    acc = {
        (1, (0, 0)): [1.0, 0.0, 0.0, 0.0, 2.0, 0.5, -46.6, -23.5],
        (1, (0, 1)): [0.0, 0.0, 0.0, 0.0, 0.0, -1.0, 0.0, 0.0],
    }
    by_zone = density._cells_by_zone(acc)
    assert list(by_zone[1]) == [(0, 0)]


def test_draw_cells_respeita_a_quantidade_e_nao_repete():
    pool = cells(*[(x, y, 1.0, 1.0) for x in range(20) for y in range(20)])
    drawn = density._draw_cells(pool, density._HOME, 50)
    assert len(drawn) == 50
    assert len(set(drawn)) == 50


def test_draw_cells_devolve_tudo_quando_pede_demais():
    pool = cells((0, 0, 1.0, 0.0), (1, 1, 1.0, 0.0))
    assert sorted(density._draw_cells(pool, density._HOME, 10)) == [(0, 0), (1, 1)]


def test_draw_cells_ignora_peso_zero_e_k_nao_positivo():
    pool = cells((0, 0, 0.0, 5.0), (1, 1, 3.0, 0.0))
    assert density._draw_cells(pool, density._HOME, 5) == [(1, 1)]
    assert density._draw_cells(pool, density._WORK, 5) == [(0, 0)]
    assert density._draw_cells(pool, density._HOME, 0) == []


def test_draw_cells_pula_celulas_sem_ancora():
    pool = cells((0, 0, 1.0, 0.0), (1, 1, 1.0, 0.0))
    pool[(0, 0)][density._DRAW] = -1.0
    assert density._draw_cells(pool, density._HOME, 5) == [(1, 1)]


def test_draw_cells_respeita_celulas_ja_tomadas():
    pool = cells(*[(x, 0, 1.0, 1.0) for x in range(10)])
    taken = {(0, 0), (1, 0)}
    drawn = density._draw_cells(pool, density._HOME, 8, taken=taken)
    assert not set(drawn) & taken


def test_draw_cells_favorece_o_peso():
    """Metade das células com peso 100x deve levar quase todos os sorteios."""
    pool = cells(*[(x, y, 100.0 if x < 10 else 1.0, 1.0) for x in range(20) for y in range(20)])
    drawn = density._draw_cells(pool, density._HOME, 200)
    heavy = sum(1 for key in drawn if key[0] < 10)
    assert heavy / len(drawn) > 0.9


def test_draw_cells_cobre_todo_o_espaco_com_peso_uniforme():
    """Viés espacial foi um bug real: o sorteio se concentrava numa faixa das colunas."""
    pool = cells(*[(x, y, 1.0, 1.0) for x in range(30) for y in range(30)])
    drawn = density._draw_cells(pool, density._HOME, 300)
    columns = {key[0] for key in drawn}
    rows = {key[1] for key in drawn}
    assert len(columns) > 25 and len(rows) > 25
    assert 10 < np.mean([key[0] for key in drawn]) < 19


def test_draw_cells_e_deterministico():
    pool = cells(*[(x, y, 2.0, 1.0) for x in range(15) for y in range(15)])
    assert density._draw_cells(pool, density._HOME, 40) == density._draw_cells(
        pool, density._HOME, 40
    )


def test_point_count_escala_com_a_demanda(configure):
    configure(density, people_per_point=1000.0)
    assert density._point_count(0) == 0
    assert density._point_count(-5) == 0
    assert density._point_count(10) == 1, "demanda pequena ainda merece um ponto"
    assert density._point_count(2500) == 2
    assert density._point_count(41000) == 41


def test_merge_lote_zones_troca_so_as_zonas_bem_cobertas(configure):
    configure(density, lote_min_coverage=0.5)
    by_zone = {
        1: cells(*[(x, 0, 1.0, 0.0) for x in range(10)]),
        2: cells(*[(x, 0, 1.0, 0.0) for x in range(10)]),
    }
    lote_zones = {
        1: cells(*[(x, 5, 2.0, 0.0) for x in range(8)]),
        2: cells((0, 5, 2.0, 0.0)),
    }
    used, dropped = density.merge_lote_zones(by_zone, lote_zones)
    assert (used, dropped) == (1, 1)
    assert list(by_zone[1]) == list(lote_zones[1])
    assert len(by_zone[2]) == 10, "zona de borda mantém o CNEFE"


def test_merge_lote_zones_aceita_zona_ausente_no_cnefe():
    by_zone = {}
    lote_zones = {7: cells((0, 0, 1.0, 0.0))}
    used, dropped = density.merge_lote_zones(by_zone, lote_zones)
    assert (used, dropped) == (1, 0)
    assert 7 in by_zone


def test_select_candidates_separa_casa_de_trabalho(configure):
    configure(density, people_per_point=100.0)
    by_zone = {1: cells(*[(x, y, 5.0, 5.0) for x in range(12) for y in range(12)])}
    home, work, short = density.select_candidates(by_zone, {1: (1000.0, 500.0)})
    assert len(home[1]) == 10
    assert len(work[1]) == 5
    assert not set(home[1]) & set(work[1]), "um ponto não pode ser casa e trabalho"
    assert short == 0


def test_select_candidates_conta_zonas_sem_enderecos_suficientes(configure):
    configure(density, people_per_point=100.0)
    by_zone = {1: cells((0, 0, 1.0, 1.0), (1, 1, 1.0, 1.0))}
    _home, _work, short = density.select_candidates(by_zone, {1: (5000.0, 5000.0)})
    assert short == 1


def test_select_candidates_zona_sem_demanda_nao_recebe_pontos(configure):
    configure(density, people_per_point=100.0)
    by_zone = {1: cells((0, 0, 1.0, 1.0))}
    home, work, _short = density.select_candidates(by_zone, {})
    assert home == {} and work == {}


def test_select_candidates_devolve_a_ancora_da_celula(configure):
    configure(density, people_per_point=100.0)
    by_zone = {1: cells((3, 4, 10.0, 0.0))}
    home, _work, _short = density.select_candidates(by_zone, {1: (100.0, 0.0)})
    assert home[1] == [(round(BASE_LNG + 0.003, 6), round(BASE_LAT + 0.004, 6))]


def test_setor_weights_limita_o_peso_por_endereco(configure, cnefe_csv, tmp_path):
    setor_pop = tmp_path / "setor_pop.csv"
    setor_pop.write_text("350000001,100000\n350000002,10\n", encoding="ascii")
    weights = density.setor_weights(cnefe_csv, setor_pop)
    assert weights["350000001"] == density._MAX_ADDR_WEIGHT
    assert weights["350000002"] == pytest.approx(5.0)


def test_setor_weights_ignora_setor_sem_populacao(cnefe_csv, tmp_path):
    setor_pop = tmp_path / "setor_pop.csv"
    setor_pop.write_text("350000001,1000\n", encoding="ascii")
    assert set(density.setor_weights(cnefe_csv, setor_pop)) == {"350000001"}


def test_line_offsets_cobre_o_arquivo_inteiro(tmp_path):
    path = tmp_path / "dados.csv"
    path.write_text("".join(f"linha {i}\n" for i in range(100)), encoding="ascii")
    ranges = density._line_offsets(path, 4)
    assert ranges[0][0] == 0
    assert ranges[-1][1] == path.stat().st_size
    assert all(end > start for start, end in ranges)
    assert [end for _s, end in ranges[:-1]] == [start for start, _e in ranges[1:]]


def test_aggregate_chunk_soma_pesos_e_separa_vocacao(configure, cnefe_csv, zones_shp):
    configure(density, density_cell=0.01, bbox=(-47.0, -24.0, -45.0, -23.0))
    config = density._chunk_config()
    weights = {"350000001": 10.0, "350000002": 4.0}
    acc = density._aggregate_chunk(
        str(cnefe_csv), 0, cnefe_csv.stat().st_size, str(zones_shp), config, weights
    )
    zones_seen = {zone for zone, _cell in acc}
    assert zones_seen == {1, 2}
    home_weight = sum(v[density._HOME] for (zone, _c), v in acc.items() if zone == 1)
    work_weight = sum(v[density._WORK] for (zone, _c), v in acc.items() if zone == 1)
    assert home_weight == pytest.approx(40.0), "4 endereços residenciais a peso 10"
    assert work_weight == pytest.approx(4.0), "espécies 6 (1.0) e 4 (3.0)"
    for vals in acc.values():
        assert vals[density._DRAW] >= 0
        assert vals[density._LNG] != 0.0


def test_aggregate_chunk_ignora_setor_sem_peso(configure, cnefe_csv, zones_shp):
    configure(density, density_cell=0.01, bbox=(-47.0, -24.0, -45.0, -23.0))
    config = density._chunk_config()
    acc = density._aggregate_chunk(
        str(cnefe_csv), 0, cnefe_csv.stat().st_size, str(zones_shp), config, {}
    )
    assert all(vals[density._HOME] == 0.0 for vals in acc.values())


def test_aggregate_chunk_pula_linhas_invalidas(configure, zones_shp, tmp_path):
    configure(density, density_cell=0.01, bbox=(-47.0, -24.0, -45.0, -23.0))
    config = density._chunk_config()
    path = tmp_path / "ruim.csv"
    path.write_text(
        "sem virgulas\n"
        "a,b,c,d\n"
        f"{BASE_LNG + 0.001},{BASE_LAT + 0.001},99,350000001\n"
        f"{BASE_LNG + 0.001},{BASE_LAT + 0.001},1,350000001\n",
        encoding="ascii",
    )
    acc = density._aggregate_chunk(
        str(path), 0, path.stat().st_size, str(zones_shp), config, {"350000001": 2.0}
    )
    assert len(acc) == 1


def test_aggregate_chunk_descarta_endereco_fora_das_zonas(configure, zones_shp, tmp_path):
    configure(density, density_cell=0.01, bbox=(-47.0, -24.0, -45.0, -23.0))
    config = density._chunk_config()
    path = tmp_path / "fora.csv"
    path.write_text(f"{BASE_LNG - 5},{BASE_LAT - 5},1,350000001\n", encoding="ascii")
    acc = density._aggregate_chunk(
        str(path), 0, path.stat().st_size, str(zones_shp), config, {"350000001": 2.0}
    )
    assert acc == {}


def test_lote_chunk_pesa_por_area_e_uso(configure, lotes_csv, zones_shp):
    configure(density, density_cell=0.01, bbox=(-47.0, -24.0, -45.0, -23.0))
    config = density._chunk_config()
    acc = density._lote_chunk(
        str(lotes_csv), 0, lotes_csv.stat().st_size, str(zones_shp), config
    )
    assert sum(v[density._HOME] for v in acc.values()) == pytest.approx(350.0)
    assert sum(v[density._WORK] for v in acc.values()) == pytest.approx(900.0)


def test_lote_chunk_pula_linha_invalida(configure, zones_shp, tmp_path):
    configure(density, density_cell=0.01, bbox=(-47.0, -24.0, -45.0, -23.0))
    config = density._chunk_config()
    path = tmp_path / "lotes.csv"
    path.write_text(f"x,y,R,200\n{BASE_LNG + 0.001},{BASE_LAT + 0.001},R,200\n", encoding="ascii")
    acc = density._lote_chunk(str(path), 0, path.stat().st_size, str(zones_shp), config)
    assert len(acc) == 1


def test_regressao_parallel_aggregate_leva_a_configuracao_aos_workers(
    configure, cnefe_csv, zones_shp
):
    """Os workers são outros processos: se lessem o módulo por conta própria, usariam o .env
    do disco e agregariam numa grade diferente da do processo principal."""
    configure(density, density_cell=0.01, bbox=(-47.0, -24.0, -45.0, -23.0))
    config = density._chunk_config()
    weights = {"350000001": 10.0, "350000002": 4.0}
    combined = density._parallel_aggregate(
        density._aggregate_chunk, cnefe_csv, zones_shp, weights
    )
    single = density._aggregate_chunk(
        str(cnefe_csv), 0, cnefe_csv.stat().st_size, str(zones_shp), config, weights
    )
    assert set(combined) == set(single)
    for key, vals in single.items():
        assert combined[key][:5] == pytest.approx(vals[:5])
        assert combined[key][density._LNG:] == pytest.approx(vals[density._LNG:])


def test_zone_candidates_ponto_a_ponto_sobre_endereco_real(
    configure, cnefe_csv, setor_pop_csv, zones_shp, tmp_path
):
    """Regressão: o ponto ficava no centroide ponderado da célula, que cai no meio da rua."""
    configure(density, people_per_point=100.0, sources_dir=tmp_path / "vazio")
    weights = density.setor_weights(cnefe_csv, setor_pop_csv)
    home, work, _cells = density.zone_candidates(
        cnefe_csv, zones_shp, weights, {1: (1000.0, 300.0), 2: (500.0, 0.0)}
    )
    addresses = {
        (round(float(line.split(",")[0]), 6), round(float(line.split(",")[1]), 6))
        for line in cnefe_csv.read_text().splitlines()
    }
    generated = [p for pts in list(home.values()) + list(work.values()) for p in pts]
    assert generated, "nenhum ponto gerado"
    assert all(p in addresses for p in generated)


def test_zone_candidates_usa_lotes_quando_a_cobertura_basta(
    configure, cnefe_csv, setor_pop_csv, zones_shp, lotes_csv
):
    configure(
        density, people_per_point=100.0, sources_dir=lotes_csv.parent, lote_min_coverage=0.1
    )
    weights = density.setor_weights(cnefe_csv, setor_pop_csv)
    home, _work, _cells = density.zone_candidates(
        cnefe_csv, zones_shp, weights, {1: (1000.0, 300.0)}
    )
    lote_points = {
        (round(float(line.split(",")[0]), 6), round(float(line.split(",")[1]), 6))
        for line in lotes_csv.read_text().splitlines()
    }
    assert set(home[1]) <= lote_points


def test_res_count_ignora_linhas_ilegiveis(tmp_path):
    path = tmp_path / "cnefe.csv"
    path.write_text(
        "linha sem campos\n"
        f"{BASE_LNG},{BASE_LAT},x,350000001\n"
        f"{BASE_LNG},{BASE_LAT},1,350000001\n",
        encoding="ascii",
    )
    assert density._res_count(path) == {"350000001": 1}


def test_aggregate_chunk_para_no_fim_do_arquivo(configure, cnefe_csv, zones_shp):
    """O intervalo pedido pode passar do fim do arquivo quando o último chunk é curto."""
    configure(density, density_cell=0.01, bbox=(-47.0, -24.0, -45.0, -23.0))
    config = density._chunk_config()
    acc = density._aggregate_chunk(
        str(cnefe_csv), 0, cnefe_csv.stat().st_size * 10, str(zones_shp), config,
        {"350000001": 1.0},
    )
    assert acc


def test_lote_chunk_pula_linha_com_menos_campos(configure, zones_shp, tmp_path):
    configure(density, density_cell=0.01, bbox=(-47.0, -24.0, -45.0, -23.0))
    config = density._chunk_config()
    path = tmp_path / "lotes.csv"
    path.write_text(
        f"{BASE_LNG},{BASE_LAT},R\n"
        f"{BASE_LNG + 0.001},{BASE_LAT + 0.001},R,200\n",
        encoding="ascii",
    )
    acc = density._lote_chunk(str(path), 0, path.stat().st_size * 10, str(zones_shp), config)
    assert len(acc) == 1


def test_lote_chunk_descarta_lote_fora_das_zonas(configure, zones_shp, tmp_path):
    configure(density, density_cell=0.01, bbox=(-47.0, -24.0, -45.0, -23.0))
    config = density._chunk_config()
    path = tmp_path / "lotes.csv"
    path.write_text(f"{BASE_LNG - 5},{BASE_LAT - 5},R,200\n", encoding="ascii")
    assert density._lote_chunk(str(path), 0, path.stat().st_size, str(zones_shp), config) == {}


def test_cell_compensa_a_distorcao_longitudinal(configure):
    """Sem compensar, a célula sairia ~8% mais estreita em longitude do que em latitude."""
    configure(density, density_cell=0.001, bbox=(-47.0, -24.0, -45.0, -23.0))
    steps_lng = 0
    while density._cell(-47.0 + (steps_lng + 1) * 0.0001, -24.0) == (0, 0):
        steps_lng += 1
    steps_lat = 0
    while density._cell(-47.0, -24.0 + (steps_lat + 1) * 0.0001) == (0, 0):
        steps_lat += 1
    width = (steps_lng + 1) * 0.0001 * settings.m_per_deg_lng
    height = (steps_lat + 1) * 0.0001 * settings.m_per_deg_lat
    assert abs(width - height) / height < 0.05, f"célula de {width:.0f}x{height:.0f} m"
