# CLAUDE.md

Turns the observed trips of the 2023 São Paulo Origin-Destination survey into Subway Builder /
depot demand pops. README.md (Portuguese) holds the demand rules, commands and outputs; this file
only adds what is easy to break. Code, docstrings, log messages and README are in Brazilian
Portuguese; commit messages are English Conventional Commits.

## Cross-repo coupling

- Input `fluxos.parquet` is published by the `transporte-sp-origem-destino` data repo. The column
  set this repo reads is `_COLUMNS` in `src/demand_data/flows.py` (mirrored by `FLOW_COLUMNS` in
  `tests/conftest.py`); a schema change upstream breaks `flows.load_flows`. Motive codes
  (`MOTIVE_HOME = 8`, `MOTIVE_PLACE_TYPE`) are the survey's `MOTIVO_D` values.
- Output `demand_data.json` must load as depot's `DemandData`; `config.json` mirrors the fields of
  depot's `create_config` and is required by the Railyard importer.
- Place-type codes in `src/demand_data/taxonomy.py` must stay a subset of depot's taxonomy.
- The game reads a point's type from its id prefix (`id.split("_")[0]`, e.g. `SCH_z2w0`), not
  from a field. Build and parse ids only through `src/demand_data/pointid.py`.
- Each scaling is a separate Railyard map, so code and name carry the percent (`RMSP25`,
  "... (25%)"); dropping that makes the packages collide on submission.

## Invariants the tests pin

- Sum of pop `size` equals the survey's total trips/day before scaling; slicing oversized pops
  uses largest-remainder apportionment to keep it exact.
- A point is never both home and destination (`h`/`w` role in the id), and the outbound and
  return legs of the same trip merge into one pop.
- Named equipment (OSM) gives a destination identity only: it never creates points or demand.
- Scaling shrinks every pop by the same factor; `scaling.apply` is pure and callers must
  reconcile points afterwards (`pops.aggregate`), since drops leave points without demand.

## Gotchas

- `sources` is idempotent by file name: it skips `data/sources/fluxos.parquet` and `pois.csv` if
  they exist. After changing `DEMAND_FLOW_URL` (e.g. to a `fluxos_10k` sample) or `POI_QUERIES`,
  delete the cached file or the old data is silently reused.
- The public Overpass endpoint returns 504 from about the fifth back-to-back query; keep one
  query per type and the `DEMAND_OVERPASS_PAUSE_S` pause between them. Only fetch outlines
  (`out geom`) for large, irregular equipment; geometry for all schools takes the endpoint down.
- POIs are deduplicated by OSM id, not name: many distinct schools share a name.
- `Settings` is a frozen global built at import time from `.env` at the project root. Tests swap
  it per module with the `configure` fixture in `tests/conftest.py`; patch the module that uses
  `settings`, not `config`.
- Without `DEMAND_OSRM_URL` the driving fields stay 0 and depot routes on import; the OSRM setup
  commands live in the `src/demand_data/routing.py` docstring.
- `scripts/publish_map.sh` force-pushes a single-commit `gh-pages` branch (default map:
  `out/scale-100/pops_map.html`).

## Checks and releases

- `uv run pytest` enforces `--cov-fail-under=90` via `addopts` in `pyproject.toml`; tests run
  offline on tiny fixtures. `uv run ruff check .` is the lint. No CI runs tests (the only
  workflow is auto-assign), so run both before committing.
- A release bumps the version in three places together: `pyproject.toml`,
  `DEMAND_MAP_VERSION` default in `src/demand_data/config.py`, and `.env.example`. A change to
  the published demand's meaning is a new major (map version).
