"""Registro de coordenadas distintas.

O jogo quebra com dois pontos na mesma coordenada (invariante: coordenada nunca duplicada).
Este registro devolve, para cada coordenada pedida, uma versão livre de colisão — empurrando
~1 m quando preciso. Usado tanto ao posicionar os pontos (:mod:`demand_data.pops`) quanto ao
mudá-los para o equipamento adotado (:mod:`demand_data.pois`).
"""

from __future__ import annotations

from collections.abc import Iterable

_NUDGE = 1e-5  # ~1 m: afasta uma coordenada que colide com outra já usada


class CoordinateRegistry:
    """Coordenadas já ocupadas; ``place`` devolve sempre uma livre."""

    def __init__(self, taken: Iterable[Iterable[float]] = ()) -> None:
        self._taken: set[tuple[float, float]] = {tuple(coord) for coord in taken}

    def place(self, lng: float, lat: float) -> list[float]:
        """``[lng, lat]`` livre de colisão, deslocando a longitude ~1 m se preciso."""
        lng, lat = round(lng, 6), round(lat, 6)
        while (lng, lat) in self._taken:
            lng = round(lng + _NUDGE, 6)
        self._taken.add((lng, lat))
        return [lng, lat]
