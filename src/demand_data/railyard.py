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
_DATA_URL = "https://www.rodrigoroque.dev/transporte-sp-origem-destino/dados/"
_OSM_URL = "https://www.openstreetmap.org/"


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
    named = [p for p in points if p.get("name")]
    return {
        "points": len(points),
        "pops": len(pops),
        "trips": sum(sizes),
        "median_pop": sizes[len(sizes) // 2] if sizes else 0,
        "max_pop": sizes[-1] if sizes else 0,
        "median_point": demand[len(demand) // 2] if demand else 0,
        "named": len(named),
    }


def _br(value: int) -> str:
    """Número no formato brasileiro: 21236872 -> 21.236.872."""
    return f"{value:,}".replace(",", ".")


def build_description(points: list[dict], pops: list[dict], generated_at: datetime) -> str:
    """Ficha do mapa em Markdown: o que ele é, de onde vem e como foi construído."""
    s = _stats(points, pops)
    return f"""# Região Metropolitana de São Paulo

Demanda gerada a partir das **viagens observadas** da Pesquisa Origem-Destino 2023 do
Metrô-SP, cada uma geolocalizada na origem e no destino reais.

## Números

| | |
|---|---|
| Viagens/dia | {_br(s["trips"])} |
| Pontos de demanda | {_br(s["points"])} |
| Pops | {_br(s["pops"])} |
| Tamanho do pop (mediana / máximo) | {_br(s["median_pop"])} / {_br(s["max_pop"])} |
| Demanda por ponto (mediana) | {_br(s["median_point"])} |
| Destinos nomeados | {_br(s["named"])} |

## Metodologia

- Cada linha da pesquisa é uma **viagem observada**, com a coordenada real de onde começa e
  termina. Nada é sorteado: o par origem→destino é o par de fato registrado.
- A viagem é orientada em casa↔atividade pelo motivo do destino: a casa é a origem, salvo na
  volta pra casa, em que é o destino. Ida e volta de um mesmo trajeto se fundem num pop, e o
  tamanho do pop é o número de **viagens/dia** que a pesquisa expande.
- Cada destino de educação, saúde, comércio ou lazer adota a identidade do equipamento real
  mais próximo do **OpenStreetMap** (escola, hospital, shopping, parque…), sem criar ponto.
- Os tempos e distâncias de carro são calculados por roteamento na malha viária.

## Fontes

- [Viagens geolocalizadas da OD 2023]({_DATA_URL}) — a partir da
  [Pesquisa Origem-Destino 2023]({_OD_URL}) do Metrô-SP
- [OpenStreetMap]({_OSM_URL}) — equipamentos nomeados (escolas, hospitais, shoppings, parques)

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
