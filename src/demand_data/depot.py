"""Escreve a demanda no formato importável pelo depot / Subway Builder.

``demand_data.json`` = ``{"points": [...], "pops": [...]}`` (o que
``depot.demand.DemandData`` carrega). Os pops saem com ``drivingSeconds/Distance = 0``
para que o roteamento do depot (``calculate_routes``) os preencha na importação.
Também grava a versão ``.gz`` que o jogo/Railyard consome.
"""

from __future__ import annotations

import gzip
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def write(points: list[dict], pops: list[dict], path: Path) -> None:
    data = {"points": points, "pops": pops}
    payload = json.dumps(data, separators=(",", ":"))
    path.write_text(payload, encoding="utf-8")
    gz = path.with_suffix(path.suffix + ".gz")
    with gzip.open(gz, "wb", compresslevel=6) as f:
        f.write(payload.encode("utf-8"))
    log.info(
        "depot: %s (%.2f MB) + %s (%.2f MB)",
        path.name, path.stat().st_size / 1e6, gz.name, gz.stat().st_size / 1e6,
    )
