"""Extração da Pesquisa Origem-Destino 2023 (Metrô-SP): zonas (polígonos WGS84 + índice
espacial), população por zona (Σ FE_PESS de residência) e matriz O-D casa→trabalho
(Σ FE_PESS por (ZONA, ZONATRA1)). Só entrega os totais oficiais, não posiciona pontos.
"""

from __future__ import annotations

import collections
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Zones:
    ids: list[int]
    polygons: list  # shapely (Multi)Polygon, WGS84
    _tree: object  # shapely STRtree
    _order: list[int]  # zone id na ordem das geoms do tree

    def zone_of(self, lng: float, lat: float) -> int | None:
        import shapely

        hits = self._tree.query(shapely.points(lng, lat), predicate="within")
        return self._order[int(hits[0])] if len(hits) else None


def load_zones(zones_shp: Path) -> Zones:
    """Lê o shapefile de zonas e reprojeta de Córrego Alegre UTM 23S (do .prj) para WGS84."""
    import shapefile
    from pyproj import CRS, Transformer
    from shapely import STRtree
    from shapely.geometry import shape as shp_shape
    from shapely.ops import transform as shp_transform

    logging.getLogger("shapefile").setLevel(logging.ERROR)  # silencia rings órfãos (inofensivo)
    to_wgs = Transformer.from_crs(
        CRS.from_wkt(zones_shp.with_suffix(".prj").read_text()), "EPSG:4326", always_xy=True
    ).transform
    sf = shapefile.Reader(str(zones_shp), encoding="latin-1")
    flds = [f[0] for f in sf.fields[1:]]

    ids: list[int] = []
    polygons: list = []
    for sr in sf.iterShapeRecords():
        rec = dict(zip(flds, sr.record, strict=False))
        try:
            g = shp_transform(to_wgs, shp_shape(sr.shape.__geo_interface__))
        except Exception:
            continue
        ids.append(int(rec["NumeroZona"]))
        polygons.append(g)
    log.info("zonas OD: %d", len(ids))
    return Zones(ids, polygons, STRtree(polygons), list(ids))


def _as_int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def extract_od(
    dbf_path: Path, zones: set[int]
) -> tuple[dict[int, float], dict[tuple[int, int], float]]:
    """Uma passada no microdado: (população por zona, matriz O-D casa→trabalho).

    Deduplica por pessoa (ID_DOM, ID_FAM, ID_PESS) via FE_PESS. Só conta pares cujas duas
    zonas estão em ``zones`` (intra-zona hz==wz é demanda local real e é mantida).
    """
    from dbfread import DBF

    pop: dict[int, float] = collections.defaultdict(float)
    od: dict[tuple[int, int], float] = collections.defaultdict(float)
    seen: set[tuple] = set()
    for r in DBF(str(dbf_path), encoding="latin-1", raw=False):
        key = (r.get("ID_DOM"), r.get("ID_FAM"), r.get("ID_PESS"))
        if key in seen:
            continue
        seen.add(key)
        fp = r.get("FE_PESS") or 0.0
        if not fp:
            continue
        hz = _as_int(r.get("ZONA"))
        if hz in zones:
            pop[hz] += fp
        wz = _as_int(r.get("ZONATRA1"))
        if hz in zones and wz in zones:
            od[(hz, wz)] += fp
    log.info(
        "população: Σ=%.0f em %d zonas | matriz O-D: %d pares (Σ=%.0f)",
        sum(pop.values()), len(pop), len(od), sum(od.values()),
    )
    return dict(pop), dict(od)
