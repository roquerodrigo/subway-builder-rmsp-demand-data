"""Configuração do subway-builder-rmsp-demand-data (via .env na raiz).

O projeto é autossuficiente: baixa e processa os próprios dados das pesquisas em
``data/sources`` (comando ``sources``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_float(name: str, default: float) -> float:
    v = os.environ.get(name)
    return float(v) if v not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    v = os.environ.get(name)
    return int(v) if v not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    sources_dir: Path = Path(_env("DEMAND_SOURCES_DIR", str(PROJECT_ROOT / "data" / "sources")))

    # TOTAL de pops = Σ_zona round(pop_zona / people_per_pop), distribuído entre as zonas
    # ∝ ÁREA (não população). Menor = mais pops.
    people_per_pop: float = _env_float("DEMAND_PEOPLE_PER_POP", 300.0)
    # grade (graus) de agregação da densidade (~50 m a -23.5°): átomo de posicionamento e
    # espaçamento mínimo entre pontos. Os pontos NÃO são um por célula — são sorteados entre
    # as células ∝ densidade, senão formam uma treliça visível no mapa.
    density_cell: float = _env_float("DEMAND_DENSITY_CELL", 0.00045)
    # pessoas (moradores ou trabalhadores) por ponto: define quantos pontos a zona recebe.
    people_per_point: float = _env_float("DEMAND_PEOPLE_PER_POINT", 1000.0)
    seed: int = _env_int("DEMAND_SEED", 42)
    # destinos de trabalho por zona de origem (0 = todos). Não altera o total de pops.
    dest_cap: int = _env_int("DEMAND_DEST_CAP", 0)
    # tamanho mínimo de pop: limita nº de pops da zona a P/min_pop_size, fundindo os pops
    # minúsculos das zonas esparsas em menos pops maiores. 0 = sem limite.
    min_pop_size: int = _env_int("DEMAND_MIN_POP_SIZE", 50)

    # COD_ESPECIE 1,2 = domicílio; cada endereço pesa pop_do_setor / nº_endereços_do_setor.
    cnefe_res_especies: frozenset[int] = frozenset({1, 2})
    # COD_ESPECIE 3-6,8 = estabelecimentos → densidade de emprego.
    cnefe_job_especies: frozenset[int] = frozenset({3, 4, 5, 6, 8})
    # Peso de emprego por espécie (o CNEFE não conta vínculos): 4=ensino e 5=saúde empregam
    # mais; 3=agropecuário e 8=religioso, menos; 6=comércio/serviços/indústria é a base.
    cnefe_job_especie_weight: dict[int, float] = field(
        default_factory=lambda: {3: 0.5, 4: 3.0, 5: 3.0, 6: 1.0, 8: 0.3}
    )
    # 7 = edificação em construção (descartada).
    cnefe_skip_especies: frozenset[int] = frozenset({7})

    # conversão graus<->metros a ~lat -23.5
    m_per_deg_lat: float = 110900.0
    m_per_deg_lng: float = 101900.0

    od_zip_url: str = _env(
        "DEMAND_OD_ZIP_URL",
        "https://transparencia.metrosp.com.br/sites/default/files/Site_190225_PesquisaOD2023.zip",
    )
    cnefe_url: str = _env(
        "DEMAND_CNEFE_URL",
        "https://ftp.ibge.gov.br/Cadastro_Nacional_de_Enderecos_para_Fins_Estatisticos/"
        "Censo_Demografico_2022/Arquivos_CNEFE/CSV/UF/35_SP.zip",
    )
    censo_url: str = _env(
        "DEMAND_CENSO_URL",
        "https://ftp.ibge.gov.br/Censos/Censo_Demografico_2022/Agregados_por_Setores_Censitarios/"
        "Agregados_por_Setor_csv/Agregados_por_setores_basico_BR_20260520.zip",
    )

    # GeoSampa: lotes do IPTU (densidade por área construída e uso), só do município de SP →
    # densidade da capital; o resto da RMSP fica no CNEFE (híbrido).
    lote_wfs_url: str = _env(
        "DEMAND_LOTE_WFS_URL",
        "http://wfs.geosampa.prefeitura.sp.gov.br/geoserver/geoportal/wfs",
    )
    lote_layer: str = _env("DEMAND_LOTE_LAYER", "geoportal:lote_cidadao")
    lote_page: int = _env_int("DEMAND_LOTE_PAGE", 10000)
    # só usa lotes numa zona se cobrirem >= esta fração das células CNEFE da zona (evita a
    # amostra de borda em zonas mais fora da capital).
    lote_min_coverage: float = _env_float("DEMAND_LOTE_MIN_COVERAGE", 0.5)
    # dc_tipo_uso_imovel -> "R" (residência) ou "N" (não-residencial); "Terreno"/nulo descartados.
    lote_use_map: dict[str, str] = field(default_factory=lambda: {
        "Residencial": "R", "Condomínio": "R", "Não residencial": "N",
    })

    out_dir: Path = Path(_env("DEMAND_OUT_DIR", str(PROJECT_ROOT / "out")))
    # bbox da RMSP: min_lng, min_lat, max_lng, max_lat
    bbox: tuple[float, float, float, float] = field(
        default_factory=lambda: (-47.22, -24.08, -45.68, -23.17)
    )

    @property
    def od_dir(self) -> Path:
        return self.sources_dir / "od2023" / "Site_190225"

    @property
    def zones_shp(self) -> Path:
        return self.od_dir / "002_Site Metro Mapas_190225" / "Shape" / "Zonas_2023"

    @property
    def od_dbf(self) -> Path:
        return self.od_dir / "Banco2023_divulgacao_190225.dbf"

    @property
    def cnefe_csv(self) -> Path:
        return self.sources_dir / "cnefe.csv"

    @property
    def setor_pop_csv(self) -> Path:
        return self.sources_dir / "setor_pop.csv"

    @property
    def lotes_csv(self) -> Path:
        return self.sources_dir / "lotes.csv"

    @property
    def od_zip(self) -> Path:
        return self.sources_dir / "od2023.zip"

    @property
    def od_extract_dir(self) -> Path:
        return self.sources_dir / "od2023"

    @property
    def cnefe_zip(self) -> Path:
        return self.sources_dir / "35_SP.zip"

    @property
    def censo_zip(self) -> Path:
        return self.sources_dir / "censo_basico_BR.zip"

    @property
    def demand_json(self) -> Path:
        return self.out_dir / "demand_data.json"

    @property
    def map_html(self) -> Path:
        return self.out_dir / "pops_map.html"

    def in_bbox(self, lng: float, lat: float) -> bool:
        b = self.bbox
        return b[0] <= lng <= b[2] and b[1] <= lat <= b[3]

    def have_inputs(self) -> bool:
        """True se os arquivos processados já existem (não precisa rodar ``sources``)."""
        return (
            self.zones_shp.with_suffix(".shp").exists()
            and self.od_dbf.exists()
            and self.cnefe_csv.exists()
            and self.setor_pop_csv.exists()
        )

    def ensure_sources(self) -> None:
        self.sources_dir.mkdir(parents=True, exist_ok=True)

    def ensure_out(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
