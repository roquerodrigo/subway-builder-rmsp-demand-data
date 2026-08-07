"""Escala de jogo: a demanda observada reduzida ao que a simulação sustenta.

A pesquisa expande ~35 M de viagens/dia na RMSP. Como um pop é indivisível, todo pop acima de
``DEMAND_MAX_POP_SIZE`` ainda é fatiado (:func:`pops.split_oversized`), então a contagem final
cresce com o total de viagens, não com o número de trajetos — e é a contagem de pops que o
jogo simula a cada tick.

Reduzir o ``size`` de cada pop pelo **mesmo** fator mantém intacto o que a rede enfrenta: os
trajetos são os mesmos, nas mesmas coordenadas, e o peso relativo entre eles é o observado. Só
a magnitude muda, como a escala de uma maquete. O piso descarta a cauda de trajetos que, na
escala escolhida, representaria menos gente do que vale simular.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def resolve_factor(observed_trips: int, target_population: int, pop_scale: float) -> float:
    """O fator de escala, derivado da população alvo quando ela existe.

    Declarar a população é mais estável do que declarar o fator: o total observado muda com a
    amostra de viagens usada (completa, 50k, 10k…), e derivar dele mantém o mapa do mesmo
    tamanho. Sem alvo (``0``), vale o ``pop_scale`` explícito.
    """
    if target_population <= 0:
        return pop_scale
    if observed_trips <= 0:
        return 1.0
    return target_population / observed_trips


def scale(pops: list[dict], factor: float, min_size: int) -> list[dict]:
    """Aplica ``factor`` ao ``size`` de cada pop e descarta os que ficam abaixo de ``min_size``.

    Função pura: devolve pops novos, sem tocar nos originais. ``factor`` 1.0 com ``min_size``
    até 1 é identidade — a demanda sai na escala real da pesquisa. Quem chama deve reconciliar
    os pontos depois (:func:`pops.aggregate`), já que os descartes deixam pontos sem demanda.
    """
    if factor <= 0:
        raise ValueError(f"escala deve ser positiva: {factor}")
    floor = max(min_size, 1)
    kept = [
        {**pop, "size": scaled}
        for pop in pops
        if (scaled := round(pop["size"] * factor)) >= floor
    ]
    if factor != 1.0 or floor > 1:
        _log_effect(pops, kept, factor, floor)
    return kept


def _log_effect(before: list[dict], after: list[dict], factor: float, floor: int) -> None:
    trips_before = sum(pop["size"] for pop in before)
    trips_after = sum(pop["size"] for pop in after)
    dropped = len(before) - len(after)
    log.info(
        "escala 1:%g (piso %d): %d -> %d pops (%d descartados), Σsize %d -> %d "
        "(%.1f%% da escala nominal)",
        round(1 / factor, 2), floor, len(before), len(after), dropped,
        trips_before, trips_after,
        100 * trips_after / (trips_before * factor) if trips_before else 100.0,
    )
