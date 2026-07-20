"""CLI do subway-builder-rmsp-demand-data."""

from __future__ import annotations

import logging

import typer

from demand_data import (
    density,
    depot,
    htmlmap,
    od,
    pois,
    pops,
    railyard,
    routing,
    sources,
)
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
        # sem revalidar, uma aquisição incompleta só apareceria lá na frente, como um erro
        # de arquivo ausente no meio do processamento
        missing = settings.missing_inputs()
        if missing:
            typer.echo("faltam dados mesmo após `sources`: "
                       + ", ".join(str(path) for path in missing), err=True)
            raise typer.Exit(code=1)
    settings.ensure_out()

    zones = od.load_zones(settings.zones_shp)
    survey = od.extract_od(settings.od_dbf, set(zones.ids))

    weights = density.setor_weights(settings.cnefe_csv, settings.setor_pop_csv)
    home_cands, work_cands, cells = density.zone_candidates(
        settings.cnefe_csv, settings.zones_shp, weights, od.demand_by_zone(survey)
    )

    points, poplist = pops.generate(zones, survey, home_cands, work_cands)
    pois.capture(points, poplist, zones, cells)
    points = pops.aggregate(points, poplist)
    pois.classify(points, poplist, cells)
    points = pops.aggregate(points, poplist)

    if settings.osrm_url:
        routing.fill(points, poplist, settings.osrm_url)
    else:
        typer.echo("sem DEMAND_OSRM_URL — o depot calcula as rotas na importação")

    depot.write(points, poplist, settings.demand_json)
    railyard.write(points, poplist, settings.out_dir, settings.bbox, settings.map_name,
                   settings.map_code, settings.map_creator, settings.map_version)
    b = settings.bbox
    htmlmap.write(points, ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2), settings.map_html,
                  zones=zones)
    typer.echo(f"OK -> {settings.demand_json}  |  {settings.map_html}")


@app.command()
def od_only() -> None:
    """Só a extração da OD (diagnóstico): população por zona + matriz."""
    zones = od.load_zones(settings.zones_shp)
    survey = od.extract_od(settings.od_dbf, set(zones.ids))
    pairs = sum(len(f) for f in survey.flows.values())
    typer.echo(f"zonas={len(zones.ids)} "
               f"pop_total={sum(survey.population.values()):.0f} od_pares={pairs}")


if __name__ == "__main__":
    app()
