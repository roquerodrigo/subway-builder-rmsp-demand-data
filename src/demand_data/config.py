"""Configuração do subway-builder-rmsp-demand-data (via .env na raiz).

O projeto consome as viagens já geolocalizadas do repositório de dados
(``transporte-sp-origem-destino``) e os equipamentos do OpenStreetMap; o comando
``sources`` baixa os dois para ``data/sources``.
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

    # grade (graus) de quantização dos pontos (~50 m a -23.5°): endereços mais próximos que
    # isso viram um ponto só, o que também funde a ida e a volta de um mesmo trajeto.
    density_cell: float = _env_float("DEMAND_DENSITY_CELL", 0.00045)
    # tamanho máximo de pop: um pop é indivisível na simulação, então os grandes são
    # fatiados. 0 = sem limite.
    max_pop_size: int = _env_int("DEMAND_MAX_POP_SIZE", 500)

    # identificação do mapa nos arquivos de submissão ao Railyard
    map_code: str = _env("DEMAND_MAP_CODE", "RMSP")
    map_name: str = _env("DEMAND_MAP_NAME", "Região Metropolitana de São Paulo")
    map_creator: str = _env("DEMAND_MAP_CREATOR", "")
    map_version: str = _env("DEMAND_MAP_VERSION", "2.0.0")
    # servidor OSRM local para tempo/distância de carro (vazio = deixa o depot rotear)
    osrm_url: str = _env("DEMAND_OSRM_URL", "")

    # distância máxima (m) para um destino adotar o equipamento nomeado mais próximo
    poi_snap_m: float = _env_float("DEMAND_POI_SNAP_M", 500.0)

    # conversão graus->metros a ~lat -23.5
    m_per_deg_lat: float = 110900.0

    # viagens já geolocalizadas do repositório de dados (uma linha por viagem)
    flow_url: str = _env(
        "DEMAND_FLOW_URL",
        "https://www.rodrigoroque.dev/transporte-sp-origem-destino/dados/fluxos_10k.parquet",
    )
    # OpenStreetMap: coordenadas dos equipamentos nomeados
    overpass_url: str = _env("DEMAND_OVERPASS_URL", "https://overpass-api.de/api/interpreter")

    out_dir: Path = Path(_env("DEMAND_OUT_DIR", str(PROJECT_ROOT / "out")))
    # bbox da RMSP: min_lng, min_lat, max_lng, max_lat
    bbox: tuple[float, float, float, float] = field(
        default_factory=lambda: (-47.22, -24.08, -45.68, -23.17)
    )

    @property
    def flows_parquet(self) -> Path:
        return self.sources_dir / "fluxos.parquet"

    @property
    def pois_csv(self) -> Path:
        return self.sources_dir / "pois.csv"

    @property
    def demand_json(self) -> Path:
        return self.out_dir / "demand_data.json"

    @property
    def map_html(self) -> Path:
        return self.out_dir / "pops_map.html"

    def in_bbox(self, lng: float, lat: float) -> bool:
        b = self.bbox
        return b[0] <= lng <= b[2] and b[1] <= lat <= b[3]

    def missing_inputs(self) -> list[Path]:
        """Arquivos que ``sources`` deveria ter deixado em data/sources."""
        required = (self.flows_parquet, self.pois_csv)
        return [path for path in required if not path.exists()]

    def have_inputs(self) -> bool:
        """True se os arquivos já existem (não precisa rodar ``sources``)."""
        return not self.missing_inputs()

    def ensure_sources(self) -> None:
        self.sources_dir.mkdir(parents=True, exist_ok=True)

    def ensure_out(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
