"""Viagens observadas da Pesquisa OD 2023, já geolocalizadas nas duas pontas.

O trabalho de extrair a matriz e resolver a densidade intra-zona foi movido para o
repositório de dados (``transporte-sp-origem-destino``), que publica ``fluxos.parquet``: uma
linha por viagem, com a coordenada real de origem e destino, o motivo **no destino**
(``MOTIVO_D``) e o peso de expansão ``trips`` (``FE_VIA``). Este módulo só lê o arquivo e
orienta cada viagem em casa↔atividade — não posiciona nada, não sorteia nada.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from demand_data.config import settings

log = logging.getLogger(__name__)

# MOTIVO_D da pesquisa. O motivo é o do destino: a volta pra casa vira "Residência".
MOTIVE_HOME = 8

# tipo de destino (taxonomia do depot) que cada motivo alcança. Só os que levam a um
# equipamento nomeável; os difusos (indústria, serviços, refeição…) ficam sem tipo.
MOTIVE_PLACE_TYPE: dict[int, str] = {
    4: "SCH",  # Educação
    5: "SHP",  # Compras
    6: "HOS",  # Saúde
    7: "PRK",  # Lazer
}

_COLUMNS = (
    "origin_zone", "dest_zone", "motive", "motive_name", "trips",
    "o_lon", "o_lat", "d_lon", "d_lat",
)


@dataclass(frozen=True, slots=True)
class Flow:
    """Uma viagem observada, como vem no parquet."""

    origin_zone: int
    dest_zone: int
    motive: int
    motive_name: str
    trips: int
    o_lon: float
    o_lat: float
    d_lon: float
    d_lat: float


@dataclass(frozen=True, slots=True)
class Trip:
    """A viagem orientada em casa↔atividade, que é o que os pops precisam."""

    home_zone: int
    home: tuple[float, float]
    activity_zone: int
    activity: tuple[float, float]
    place_type: str | None
    trips: int


def orient(flow: Flow) -> Trip:
    """Decide qual ponta é a casa pelo motivo do destino.

    Na volta pra casa (``MOTIVE_HOME``) a casa é o **destino**; em qualquer outra viagem a
    casa é a **origem** e o destino é a atividade, que herda o tipo do motivo.
    """
    if flow.motive == MOTIVE_HOME:
        return Trip(flow.dest_zone, (flow.d_lon, flow.d_lat),
                    flow.origin_zone, (flow.o_lon, flow.o_lat), None, flow.trips)
    return Trip(flow.origin_zone, (flow.o_lon, flow.o_lat),
                flow.dest_zone, (flow.d_lon, flow.d_lat),
                MOTIVE_PLACE_TYPE.get(flow.motive), flow.trips)


def parse_rows(rows: Iterable[dict]) -> Iterator[Flow]:
    """Linhas cruas (dicts com as colunas do parquet) -> :class:`Flow`, dentro do bbox.

    Separado da leitura do parquet para poder ser exercitado sem o arquivo real.
    """
    for row in rows:
        try:
            flow = Flow(
                int(row["origin_zone"]), int(row["dest_zone"]),
                int(row["motive"]), str(row["motive_name"]), int(row["trips"]),
                float(row["o_lon"]), float(row["o_lat"]),
                float(row["d_lon"]), float(row["d_lat"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
        if flow.trips <= 0:
            continue
        if not (settings.in_bbox(flow.o_lon, flow.o_lat)
                and settings.in_bbox(flow.d_lon, flow.d_lat)):
            continue
        yield flow


def load_flows(path: Path | None = None) -> list[Flow]:
    """Lê ``fluxos.parquet`` -> lista de :class:`Flow` dentro do recorte."""
    import pyarrow.parquet as pq

    path = path or settings.flows_parquet
    table = pq.read_table(path, columns=list(_COLUMNS))
    flows = list(parse_rows(table.to_pylist()))
    total = sum(f.trips for f in flows)
    log.info("viagens: %d geolocalizadas | Σ trips=%d viagens/dia", len(flows), total)
    return flows
