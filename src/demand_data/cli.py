"""CLI do subway-builder-rmsp-demand-data."""

from __future__ import annotations

import logging

import typer

from demand_data import density, depot, htmlmap, od, pops, sources
from demand_data.config import settings

app = typer.Typer(add_completion=False, help="Gera pops de demanda da Pesquisa OD 2023 -> depot.")


@app.callback()
def _main(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format="%(message)s")


@app.command(name="sources")
def cmd_sources() -> None:
    """Baixa e processa os dados das pesquisas (OD + CNEFE + Censo) em data/sources."""
    sources.acquire()


@app.command()
def generate() -> None:
    """Pipeline completo: OD + densidade -> pops -> demand_data.json + mapa HTML.

    Roda ``sources`` automaticamente se os dados ainda não estiverem em data/sources."""
    if not settings.have_inputs():
        typer.echo("dados ausentes em data/sources — rodando `sources` primeiro...")
        sources.acquire()
    settings.ensure_out()

    zones = od.load_zones(settings.zones_shp)
    zset = set(zones.ids)
    pop, odm = od.extract_od(settings.od_dbf, zset)

    weights = density.setor_weights(settings.cnefe_csv, settings.setor_pop_csv)
    home_cands, work_cands = density.zone_candidates(
        settings.cnefe_csv, settings.zones_shp, weights
    )

    points, poplist = pops.generate(zones, pop, odm, home_cands, work_cands)

    depot.write(points, poplist, settings.demand_json)
    b = settings.bbox
    htmlmap.write(points, ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2), settings.map_html,
                  zones=zones)
    typer.echo(f"OK -> {settings.demand_json}  |  {settings.map_html}")


@app.command()
def od_only() -> None:
    """Só a extração da OD (diagnóstico): população por zona + matriz."""
    zones = od.load_zones(settings.zones_shp)
    pop, odm = od.extract_od(settings.od_dbf, set(zones.ids))
    typer.echo(f"zonas={len(zones.ids)} pop_total={sum(pop.values()):.0f} od_pares={len(odm)}")


if __name__ == "__main__":
    app()
