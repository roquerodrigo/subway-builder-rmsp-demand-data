"""Formatação de números para exibição (arquivos do Railyard e mapa HTML)."""

from __future__ import annotations


def thousands(value: int) -> str:
    """Milhar no formato brasileiro: 21236872 -> 21.236.872."""
    return f"{value:,}".replace(",", ".")
