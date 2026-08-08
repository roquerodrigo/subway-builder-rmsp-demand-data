"""Dimensionamento: a demanda observada publicada em frações do total.

A pesquisa expande ~35 M de viagens/dia na RMSP. Como um pop é indivisível, todo pop acima de
``DEMAND_MAX_POP_SIZE`` ainda é fatiado (:func:`pops.split_oversized`), então a contagem final
cresce com o total de viagens, não com o número de trajetos — e é a contagem de pops que o
jogo simula a cada tick. Não existe um tamanho certo: o que uma máquina roda com fluidez a
outra não roda, então a demanda sai em vários dimensionamentos e quem joga escolhe.

Reduzir o ``size`` de cada pop pelo **mesmo** fator mantém intacto o que a rede enfrenta: os
trajetos são os mesmos, nas mesmas coordenadas, e o peso relativo entre eles é o observado. Só
a magnitude muda, como a escala de uma maquete. O piso descarta a cauda de trajetos que, no
dimensionamento escolhido, representaria menos gente do que vale simular.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True, order=True)
class Scale:
    """Um dimensionamento: a fração da demanda observada que o mapa publica.

    ``100`` é a escala real da pesquisa; ``5`` é um vigésimo dela. Declarar a fração, e não uma
    população alvo, mantém o significado do número estável quando a amostra de viagens muda.
    """

    percent: int

    def __post_init__(self) -> None:
        if not 0 < self.percent <= 100:
            raise ValueError(f"dimensionamento deve estar entre 1% e 100%: {self.percent}")

    @property
    def factor(self) -> float:
        return self.percent / 100

    @property
    def slug(self) -> str:
        """Identificador estável, que ordena por tamanho no disco (``5`` -> ``005``)."""
        return f"{self.percent:03d}"

    @property
    def ratio(self) -> str:
        """A fração como escala legível (``5%`` -> ``1:20``)."""
        return "1:" + f"{round(100 / self.percent, 1):g}".replace(".", ",")

    @property
    def is_full(self) -> bool:
        return self.percent == 100


def apply(pops: list[dict], scale: Scale, min_size: int) -> list[dict]:
    """Reduz o ``size`` de cada pop a ``scale`` e descarta os que ficam abaixo de ``min_size``.

    Função pura: devolve pops novos, sem tocar nos originais. Em 100% com ``min_size`` até 1 é
    identidade — a demanda sai na escala real da pesquisa. Quem chama deve reconciliar os
    pontos depois (:func:`pops.aggregate`), já que os descartes deixam pontos sem demanda.
    """
    floor = max(min_size, 1)
    kept = [
        {**pop, "size": scaled}
        for pop in pops
        if (scaled := round(pop["size"] * scale.factor)) >= floor
    ]
    if not scale.is_full or floor > 1:
        _log_effect(pops, kept, scale, floor)
    return kept


def _log_effect(before: list[dict], after: list[dict], scale: Scale, floor: int) -> None:
    trips_before = sum(pop["size"] for pop in before)
    trips_after = sum(pop["size"] for pop in after)
    dropped = len(before) - len(after)
    log.info(
        "dimensionamento %d%% (escala %s, piso %d): %d -> %d pops (%d descartados), "
        "Σsize %d -> %d (%.1f%% do nominal)",
        scale.percent, scale.ratio, floor, len(before), len(after), dropped,
        trips_before, trips_after,
        100 * trips_after / (trips_before * scale.factor) if trips_before else 100.0,
    )
