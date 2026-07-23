"""Gramática do id de um ponto de demanda, num lugar só.

Casa e destino recebem um id por papel (``z{zona}{papel}{índice}``, papel ``h`` moradia,
``w`` destino). Um destino tipado que não adota um equipamento nomeado leva o código do tipo
como **prefixo** (``SCH_z2w0``), porque o jogo reconhece o tipo pelo prefixo do id
(``id.split("_")[0]``), não por um campo à parte.

Produtor (:mod:`demand_data.pops`, :mod:`demand_data.pois`) e consumidor
(:mod:`demand_data.htmlmap`) compartilham estas funções em vez de reconstruir o formato.
"""

from __future__ import annotations

import re

_ZONE = re.compile(r"z(\d+)[hw]\d+$")


def generic(zone: int, role: str, index: int) -> str:
    """Id de casa/destino: ``z{zona}{papel}{índice}``."""
    return f"z{zone}{role}{index}"


def with_type(code: str, base_id: str) -> str:
    """Prefixa o id com o código do tipo, para o jogo reconhecê-lo: ``SCH_z2w0``."""
    return f"{code}_{base_id}"


def zone_of(point_id: str) -> int:
    """Zona embutida no id (0 se não há uma), tolerando o prefixo de tipo."""
    match = _ZONE.search(point_id)
    return int(match.group(1)) if match else 0
