"""CLI do subway-builder-rmsp-demand-data."""

from __future__ import annotations

import collections
import logging

import typer

from demand_data import (
    depot,
    flows,
    htmlmap,
    pois,
    pops,
    railyard,
    routing,
    sources,
)
from demand_data.config import settings

app = typer.Typer(add_completion=False,
                  help="Gera pops de demanda das viagens OD 2023 -> depot.")


@app.callback()
def _main(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format="%(message)s")


@app.command(name="sources")
def cmd_sources() -> None:
    """Baixa os dados de entrada (viagens + equipamentos do OSM) em data/sources."""
    sources.acquire()


@app.command()
def generate() -> None:
    """Pipeline completo: viagens -> pops -> demand_data.json + mapa HTML.

    Roda ``sources`` automaticamente se os dados ainda não estiverem em data/sources."""
    if not settings.have_inputs():
        typer.echo("dados ausentes em data/sources — rodando `sources` primeiro...")
        sources.acquire()
        missing = settings.missing_inputs()
        if missing:
            typer.echo("faltam dados mesmo após `sources`: "
                       + ", ".join(str(path) for path in missing), err=True)
            raise typer.Exit(code=1)
    settings.ensure_out()

    points, poplist = pops.generate(flows.load_flows())
    pois.adopt(points, poplist)
    points = pops.aggregate(points, poplist)

    if settings.osrm_url:
        routing.fill(points, poplist, settings.osrm_url)
    else:
        typer.echo("sem DEMAND_OSRM_URL — o depot calcula as rotas na importação")

    depot.write(points, poplist, settings.demand_json)
    railyard.write(points, poplist, settings.out_dir, settings.bbox, settings.map_name,
                   settings.map_code, settings.map_creator, settings.map_version)
    b = settings.bbox
    htmlmap.write(points, ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2), settings.map_html)
    typer.echo(f"OK -> {settings.demand_json}  |  {settings.map_html}")


@app.command()
def flows_only() -> None:
    """Só a leitura das viagens (diagnóstico): total e distribuição por motivo."""
    loaded = flows.load_flows()
    by_motive = collections.Counter()
    for flow in loaded:
        by_motive[flow.motive_name] += flow.trips
    typer.echo(f"viagens={len(loaded)} trips_total={sum(f.trips for f in loaded)}")
    for name, trips in by_motive.most_common():
        typer.echo(f"  {name:<20} {trips}")


if __name__ == "__main__":
    app()
