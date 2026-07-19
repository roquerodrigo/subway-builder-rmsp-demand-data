"""Tempo e distância de carro por par casa-destino, via servidor OSRM local.

O depot preenche esses campos na importação, mas rodar aqui deixa o arquivo pronto para uso
direto. Sem servidor, os campos ficam em 0 e o depot os calcula como sempre.

Subir o OSRM (uma vez, com o extrato da região):

    curl -O https://download.geofabrik.de/south-america/brazil/sudeste-latest.osm.pbf
    img=osrm/osrm-backend
    docker run -t -v "$PWD:/data" $img osrm-extract -p /opt/car.lua /data/sudeste-latest.osm.pbf
    docker run -t -v "$PWD:/data" $img osrm-partition /data/sudeste-latest.osrm
    docker run -t -v "$PWD:/data" $img osrm-customize /data/sudeste-latest.osrm
    docker run -p 5000:5000 -v "$PWD:/data" $img osrm-routed --algorithm mld \
        /data/sudeste-latest.osrm
"""

from __future__ import annotations

import json
import logging
import math
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from demand_data.config import settings

log = logging.getLogger(__name__)

_FALLBACK_KPH = 30.0
_EARTH_RADIUS_M = 6371000.0


def haversine(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    """Distância em metros entre dois pontos, sobre a esfera."""
    lng1, lat1, lng2, lat2 = map(math.radians, (lng1, lat1, lng2, lat2))
    a = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2)
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))


def straight_line(origin, destination) -> tuple[int, int]:
    """Fallback para pares sem rota: linha reta a uma velocidade média."""
    distance = haversine(origin[0], origin[1], destination[0], destination[1])
    return int(distance), int(distance / (_FALLBACK_KPH * 1000 / 3600))


def _query(url: str, timeout: float):
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def route(origin, destination, base_url: str, timeout: float = 10.0) -> tuple[int, int]:
    """(distância em metros, segundos) entre duas coordenadas ``[lng, lat]``."""
    url = (f"{base_url.rstrip('/')}/route/v1/driving/"
           f"{origin[0]},{origin[1]};{destination[0]},{destination[1]}?overview=false")
    try:
        payload = _query(url, timeout)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return straight_line(origin, destination)
    routes = payload.get("routes") or []
    if payload.get("code") != "Ok" or not routes:
        return straight_line(origin, destination)
    return int(routes[0]["distance"]), int(routes[0]["duration"])


def fill(points: list[dict], pops: list[dict], base_url: str, workers: int = 8) -> int:
    """Preenche ``drivingDistance``/``drivingSeconds`` de cada pop. Devolve quantos foram."""
    location = {p["id"]: p["location"] for p in points}
    pending = [p for p in pops if not p.get("drivingSeconds")]
    if not pending:
        return 0

    def solve(pop: dict) -> None:
        distance, seconds = route(
            location[pop["residenceId"]], location[pop["jobId"]], base_url
        )
        pop["drivingDistance"], pop["drivingSeconds"] = distance, seconds

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(solve, pending))

    routed = sum(1 for p in pending if p["drivingSeconds"] > 0)
    distances = [p["drivingDistance"] for p in pending if p["drivingDistance"] > 0]
    log.info(
        "roteamento: %d pops | distância mediana=%.1f km | tempo mediano=%.0f min",
        routed,
        sorted(distances)[len(distances) // 2] / 1000 if distances else 0.0,
        sorted(p["drivingSeconds"] for p in pending)[len(pending) // 2] / 60,
    )
    return routed


def default_url() -> str:
    return settings.osrm_url
