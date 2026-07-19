"""Arquivos que acompanham a demanda na submissão ao Railyard.

``config.json`` é exigido pelo importador do jogo (mesmos campos que o ``create_config`` do
depot escreve) e ``description.md`` é o texto que aparece na ficha do mapa.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

_ZOOM = 12
_OD_URL = "https://transparencia.metrosp.com.br/dataset/pesquisa-origem-e-destino"
_CNEFE_URL = ("https://www.ibge.gov.br/estatisticas/sociais/populacao/"
              "38734-cadastro-nacional-de-enderecos-para-fins-estatisticos.html")
_CENSO_URL = ("https://www.ibge.gov.br/estatisticas/sociais/populacao/"
              "22827-censo-demografico-2022.html")


def build_config(points: list[dict], pops: list[dict], bbox, name: str, code: str,
                 creator: str, version: str, description: str = "",
                 country: str = "BR") -> dict:
    """Config do mapa: recorte, população e posição inicial da câmera."""
    min_lng, min_lat, max_lng, max_lat = bbox
    config = {
        "code": code,
        "name": name,
        "bbox": [min_lng, min_lat, max_lng, max_lat],
        "description": description,
        "population": sum(p["size"] for p in pops),
        "initialViewState": {
            "latitude": round((min_lat + max_lat) / 2, 5),
            "longitude": round((min_lng + max_lng) / 2, 5),
            "zoom": _ZOOM,
            "bearing": 0,
        },
        "creator": creator,
        "version": version,
    }
    if country:
        config["country"] = country
    log.info("config: %s (%s), população=%d, %d pontos",
             config["name"], config["code"], config["population"], len(points))
    return config


def _stats(points: list[dict], pops: list[dict]) -> dict[str, int]:
    sizes = sorted(p["size"] for p in pops)
    demand = sorted(p["jobs"] + p["residents"] for p in points)
    gateways = [p for p in points if p["id"].startswith("EXT_")]
    return {
        "points": len(points),
        "pops": len(pops),
        "population": sum(sizes),
        "median_pop": sizes[len(sizes) // 2] if sizes else 0,
        "max_pop": sizes[-1] if sizes else 0,
        "median_point": demand[len(demand) // 2] if demand else 0,
        "gateways": len(gateways),
        "gateway_people": sum(p["jobs"] + p["residents"] for p in gateways),
    }


def _br(value: int) -> str:
    """Número no formato brasileiro: 21236872 -> 21.236.872."""
    return f"{value:,}".replace(",", ".")


def build_description(points: list[dict], pops: list[dict], generated_at: datetime) -> str:
    """Ficha do mapa em Markdown: o que ele é, de onde vem e como foi construído."""
    s = _stats(points, pops)
    return f"""# Região Metropolitana de São Paulo

Demanda gerada a partir da **Pesquisa Origem-Destino 2023** do Metrô-SP, com a densidade
intra-zona resolvida por endereços e lotes reais.

## Números

| | |
|---|---|
| População | {_br(s["population"])} |
| Pontos de demanda | {_br(s["points"])} |
| Pops | {_br(s["pops"])} |
| Tamanho do pop (mediana / máximo) | {_br(s["median_pop"])} / {_br(s["max_pop"])} |
| Demanda por ponto (mediana) | {_br(s["median_point"])} |
| Conexões externas | {s["gateways"]} ({_br(s["gateway_people"])} pessoas) |

## Metodologia

- Cada morador vai para o destino que **declarou** na pesquisa: local de trabalho, escola
  ou, para quem não tem nenhum dos dois, os motivos não-pendulares (compras, saúde, lazer).
- A posição dentro da zona sai de um sorteio proporcional à densidade entre endereços
  reais — CNEFE na região e lotes do IPTU na capital, estes ponderados por área construída
  e uso.
- Quem trabalha ou estuda fora da região chega a uma conexão externa na borda do recorte.
- Os tempos e distâncias de carro são calculados por roteamento na malha viária.

## Fontes

- [Pesquisa Origem-Destino 2023]({_OD_URL}) — Metrô-SP
- [CNEFE 2022]({_CNEFE_URL}) e [Censo 2022]({_CENSO_URL}) — IBGE
- [GeoSampa](https://geosampa.prefeitura.sp.gov.br/) — lotes do IPTU, Prefeitura de São Paulo

Gerado em {generated_at.strftime("%d/%m/%Y")} por
[subway-builder-rmsp-demand-data](https://github.com/roquerodrigo/subway-builder-rmsp-demand-data).
"""


def write(points: list[dict], pops: list[dict], out_dir: Path, bbox, name: str, code: str,
          creator: str, version: str, generated_at: datetime | None = None) -> None:
    config = build_config(points, pops, bbox, name, code, creator, version)
    (out_dir / "config.json").write_text(
        json.dumps(config, indent=4, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "description.md").write_text(
        build_description(points, pops, generated_at or datetime.now().astimezone()),
        encoding="utf-8",
    )
    log.info("railyard: config.json + description.md")
