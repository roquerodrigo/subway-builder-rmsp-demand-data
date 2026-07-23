"""Taxonomia dos tipos de destino, num lugar só.

Um destino tipado (o que um motivo alcança) aceita um conjunto de códigos de equipamento do
OpenStreetMap para adotar uma identidade, e cada código tem um rótulo para o mapa. :mod:`pois`
e :mod:`htmlmap` derivam suas visões daqui em vez de repetir os códigos; todos são um
subconjunto da taxonomia que o depot reconhece.
"""

from __future__ import annotations

# place type -> códigos de equipamento OSM que podem lhe dar identidade (o primeiro é ele mesmo)
ACCEPTED_EQUIPMENT: dict[str, tuple[str, ...]] = {
    "SCH": ("SCH", "UNI"),
    "HOS": ("HOS",),
    "SHP": ("SHP",),
    "PRK": ("PRK", "ZOO", "SPO", "CNV"),
}

# rótulo de cada código no mapa (o adotado herda o sentido do destino que o serve)
EQUIPMENT_LABELS: dict[str, str] = {
    "SCH": "ensino", "UNI": "ensino",
    "HOS": "saúde",
    "SHP": "comércio",
    "PRK": "lazer", "SPO": "lazer", "ZOO": "lazer", "CNV": "eventos",
}

# todo código que pode virar o tipo de um ponto (destinos + equipamentos adotados)
EQUIPMENT_CODES: tuple[str, ...] = tuple(
    dict.fromkeys(code for codes in ACCEPTED_EQUIPMENT.values() for code in codes)
)
