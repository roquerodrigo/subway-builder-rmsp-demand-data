# subway-builder-rmsp-demand-data

Generates Subway Builder / [depot](https://github.com/Subway-Builder-Modded/depot)
demand pops from the Metrô-SP **Pesquisa OD 2023**. Self-contained: acquires and
processes its own survey data; does no basemap, routing, or bundling. See
`README.md` for the demand algorithm and data sources.

## Commands

```bash
uv sync
uv run demand-data sources    # download + process OD/CNEFE/Censo -> data/sources/
uv run demand-data generate   # full pipeline -> out/demand_data.json (+ .gz) + out/pops_map.html
uv run demand-data od-only    # OD extraction only (diagnostic)
uv run ruff check .           # lint (run before committing)
uv run ruff format .
uv run pytest                 # no tests exist yet, despite pytest in the dev group
```

`generate` auto-runs `sources` if `data/sources/` is empty. All `.env` knobs
(`DEMAND_*`) have defaults — see `.env.example`.

## Gotchas & rationale

- **Source data is committed to git.** `data/` is in `.gitignore`, but
  `data/sources/**` was force-added and stays tracked; `.gitattributes` marks it
  `-text` to preserve downloads byte-for-byte. So a fresh clone runs `generate`
  with no network, and `sources` (CNEFE ~1 GB, GeoSampa lotes ~1.68M rows / ~15
  min) rarely needs re-running. Don't "clean up" these tracked files.
- **Invariant:** `Σ pop sizes == total population` (`FE_PESS` summed per zone).
  Changes to `pops.py` must preserve this.
- **depot coupling:** emitted pops carry `drivingSeconds/Distance = 0` on purpose
  — depot routes them on import. Don't compute travel times here.
- `certifi` is a hard dependency because IBGE's FTP-over-HTTPS serves an
  incomplete cert chain; the default system bundle fails.
- Coordinates are reprojected from Córrego Alegre UTM 23S to WGS84 via `pyproj`.
- Density is **hybrid**: GeoSampa IPTU lots (built area × use) inside the capital,
  CNEFE elsewhere. `data/sources/lotes.csv` is optional — absent means CNEFE
  everywhere.

## Git

Private repo: commit and push directly to `main` (no PR). Commit only when asked.
Code, comments, and commit messages in English; the README and `.env.example`
are intentionally pt-BR.
