"""Aquisição própria dos dados das pesquisas — o projeto não depende de nada externo.

Baixa e processa, para dentro de ``data/sources``:
  - **Pesquisa OD 2023** (Metrô-SP): zip -> shapefile de zonas + microdados DBF;
  - **CNEFE 2022** (IBGE, SP): zip ~1 GB -> ``cnefe.csv`` compacto (``lng,lat,especie,setor``),
    filtrado ao bbox e sem espécies descartadas (stream direto do zip, sem extrair);
  - **Censo 2022 básico** (IBGE): zip -> ``setor_pop.csv`` (``setor,pop``, UF 35).

Tudo idempotente: pula o que já existe.
"""

from __future__ import annotations

import json
import logging
import re
import ssl
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from demand_data.config import settings

log = logging.getLogger(__name__)


def _ssl_context() -> ssl.SSLContext | None:
    """Contexto com o CA bundle do certifi — o FTP-over-HTTPS do IBGE serve uma cadeia
    incompleta que o store padrão do Python rejeita."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


class _Tally:
    """Conta os itens que passam por um iterável — permite logar lidos vs mantidos sem
    materializar o arquivo inteiro."""

    def __init__(self, source):
        self._source, self.count = source, 0

    def __iter__(self):
        for item in self._source:
            self.count += 1
            yield item


def parse_cnefe(lines, skip: frozenset[int]):
    """Linhas cruas do CSV do CNEFE (com cabeçalho) -> ``lng,lat,especie,setor`` do bbox.

    Separado do download para poder ser exercitado sem o zip de ~1 GB.
    """
    rows = iter(lines)
    first = next(rows, None)
    if first is None:  # sem cabeçalho o `next` viraria RuntimeError dentro do gerador
        return
    header = first.decode("latin-1").rstrip("\r\n").split(";")
    col = {c: i for i, c in enumerate(header)}
    ilng, ilat = col["LONGITUDE"], col["LATITUDE"]
    iesp, isetor = col["COD_ESPECIE"], col["COD_SETOR"]
    for line in rows:
        f = line.decode("latin-1").rstrip("\r\n").split(";")
        try:
            especie = int(f[iesp])
            if especie in skip:
                continue
            lng, lat = float(f[ilng]), float(f[ilat])
        except (ValueError, IndexError):
            continue
        if not settings.in_bbox(lng, lat):
            continue
        setor = re.sub(r"\D+$", "", f[isetor])  # tira o sufixo de situação (1 char)
        yield f"{lng},{lat},{especie},{setor}\n"


def parse_censo(lines):
    """Linhas cruas dos agregados do Censo -> ``setor,pop`` da UF 35 (São Paulo)."""

    def cells(line: bytes) -> list[str]:
        return [c.strip().strip('"') for c in line.decode("latin-1").rstrip("\r\n").split(";")]

    rows = iter(lines)
    first = next(rows, None)
    if first is None:
        return
    header = cells(first)
    isetor, ipop = header.index("CD_SETOR"), header.index("v0001")
    for line in rows:
        f = cells(line)
        try:
            setor = f[isetor]
            pop = int(f[ipop] or 0)
        except (ValueError, IndexError):
            continue
        if pop <= 0 or not setor.startswith("35"):
            continue
        yield f"{setor},{pop}\n"


def parse_lotes(features, use_map: dict[str, str]):
    """Features do WFS do GeoSampa -> ``lng,lat,uso,area`` (uso ∈ R/N) do bbox."""
    for ft in features:
        props = ft.get("properties") or {}
        use = use_map.get(props.get("dc_tipo_uso_imovel"))
        try:  # o WFS às vezes devolve a área como texto
            area = float(props.get("qt_area_construida") or 0)
        except (TypeError, ValueError):
            continue
        if not use or area <= 0:
            continue
        c = _lote_centroid(ft.get("geometry") or {})
        if c is None or not settings.in_bbox(c[0], c[1]):
            continue
        yield f"{round(c[0], 6)},{round(c[1], 6)},{use},{round(area)}\n"


def _first_csv(archive: zipfile.ZipFile, source: Path) -> str:
    name = next((m for m in archive.namelist() if m.lower().endswith(".csv")), None)
    if name is None:
        raise ValueError(f"{source.name} não contém nenhum CSV")
    return name


def _mb(p: Path) -> float:
    return p.stat().st_size / 1e6


def _download(url: str, dest: Path) -> None:
    if dest.exists():
        log.info("já baixado: %s", dest.name)
        return
    log.info("baixando %s -> %s", url, dest.name)
    # baixa para .part e só então renomeia: um download interrompido no meio ficaria no
    # destino final e a execução seguinte o trataria como completo.
    partial = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, context=_ssl_context()) as r, open(partial, "wb") as f:  # noqa: S310
        while chunk := r.read(1 << 20):
            f.write(chunk)
    partial.replace(dest)


def od() -> None:
    """Baixa e extrai o zip da Pesquisa OD 2023 (zonas + microdados)."""
    if settings.od_extract_dir.exists():
        log.info("OD já extraída: %s", settings.od_extract_dir.name)
        return
    _download(settings.od_zip_url, settings.od_zip)
    log.info("extraindo %s", settings.od_zip.name)
    with zipfile.ZipFile(settings.od_zip) as z:
        z.extractall(settings.od_extract_dir)


def cnefe() -> None:
    """Baixa o CNEFE (SP) e escreve ``cnefe.csv`` (``lng,lat,especie,setor``) do bbox."""
    out = settings.cnefe_csv
    if out.exists():
        log.info("já processado: %s", out.name)
        return
    _download(settings.cnefe_url, settings.cnefe_zip)
    log.info("filtrando CNEFE -> %s (bbox %s)", out.name, settings.bbox)
    kept = 0
    with zipfile.ZipFile(settings.cnefe_zip) as z:
        name = _first_csv(z, settings.cnefe_zip)
        with z.open(name) as raw, open(out, "w", encoding="ascii") as fout:
            tally = _Tally(raw)
            for row in parse_cnefe(tally, settings.cnefe_skip_especies):
                fout.write(row)
                kept += 1
    log.info("CNEFE: lidos=%d mantidos=%d -> %s (%.1f MB)",
             tally.count, kept, out.name, _mb(out))


def censo() -> None:
    """Baixa os agregados do Censo 2022 e escreve ``setor_pop.csv`` (``setor,pop``, UF 35)."""
    out = settings.setor_pop_csv
    if out.exists():
        log.info("já processado: %s", out.name)
        return
    _download(settings.censo_url, settings.censo_zip)
    log.info("lendo população por setor -> %s", out.name)
    kept = 0
    with zipfile.ZipFile(settings.censo_zip) as z:
        name = _first_csv(z, settings.censo_zip)
        with z.open(name) as raw, open(out, "w", encoding="ascii") as fout:
            tally = _Tally(raw)
            for row in parse_censo(tally):
                fout.write(row)
                kept += 1
    log.info("Censo: lidos=%d setores-SP=%d -> %s", tally.count, kept, out.name)


def _get_json(url: str, tries: int = 4):
    tries = max(1, tries)
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:  # noqa: S310
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


def _lote_centroid(geom: dict) -> tuple[float, float] | None:
    """Centroide (média dos vértices do anel externo) de um Polygon/MultiPolygon."""
    t, coords = geom.get("type"), geom.get("coordinates")
    if not coords:
        return None
    ring = coords[0][0] if t == "MultiPolygon" else coords[0]
    pts = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
    if not pts:
        return None
    n = len(pts)
    return sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n


def lotes() -> None:
    """Baixa os lotes do GeoSampa (WFS, paginado) e escreve ``lotes.csv`` (``lng,lat,uso,area``,
    uso ∈ R/N). Densidade por área construída e uso, só do município de SP."""
    out = settings.lotes_csv
    if out.exists():
        log.info("já processado: %s", out.name)
        return
    page, use_map = settings.lote_page, settings.lote_use_map
    log.info("baixando lotes GeoSampa (WFS %s) -> %s", settings.lote_layer, out.name)
    start = total = kept = 0
    with open(out, "w", encoding="ascii") as fout:
        while True:
            q = urllib.parse.urlencode({
                "service": "WFS", "version": "2.0.0", "request": "GetFeature",
                "typeNames": settings.lote_layer, "count": str(page), "startIndex": str(start),
                "sortBy": "cd_identificador", "srsName": "EPSG:4326",
                "outputFormat": "application/json",
                "propertyName": "dc_tipo_uso_imovel,qt_area_construida,ge_poligono",
            })
            data = _get_json(settings.lote_wfs_url + "?" + q)
            feats = data.get("features", [])
            total += len(feats)
            for row in parse_lotes(feats, use_map):
                fout.write(row)
                kept += 1
            got = data.get("numberReturned", len(feats))
            if got < page:
                break
            start += page
            if start % 100000 == 0:
                log.info("  ... %d lotes lidos (%d mantidos)", start, kept)
    log.info("lotes: lidos=%d mantidos=%d -> %s (%.1f MB)", total, kept, out.name, _mb(out))


# tipos do OSM -> código da taxonomia do depot. Só o que gera deslocamento próprio.
POI_QUERIES: tuple[tuple[str, str], ...] = (
    ("AIR", '["aeroway"="aerodrome"]["iata"]'),
    ("UNI", '["amenity"="university"]["wikidata"]'),
    ("SCH", '["amenity"="college"]["wikidata"]'),
    ("HOS", '["amenity"="hospital"]["wikidata"]'),
    ("SHP", '["shop"="mall"]["wikidata"]'),
    ("SPO", '["leisure"="stadium"]["wikidata"]'),
    ("PRK", '["leisure"="park"]["wikidata"]'),
    ("ZOO", '["tourism"="zoo"]'),
    ("CNV", '["amenity"="conference_centre"]'),
    ("CNV", '["amenity"="exhibition_centre"]'),
    ("EXT", '["amenity"="bus_station"]["wikidata"]'),
)


def parse_overpass(elements, codes: dict[int, str]):
    """Elementos do Overpass -> ``lng,lat,tipo,osm_id,min_lng,min_lat,max_lng,max_lat,nome``.

    A extensão vem junto porque o porte do equipamento é medido dentro dela: um raio fixo
    faria uma pracinha de esquina herdar os prédios do quarteirão inteiro. Elementos sem
    geometria (nós soltos) saem com a extensão zerada e caem no raio mínimo.
    """
    seen = set()
    for element in elements:
        tags = element.get("tags") or {}
        name = (tags.get("name") or "").strip().replace(",", " ")
        b = element.get("bounds") or {}
        center = element.get("center") or element
        lng, lat = center.get("lon"), center.get("lat")
        if lng is None and b:  # com "bb" o Overpass deixa de mandar o centro dos ways
            lng = (b["minlon"] + b["maxlon"]) / 2
            lat = (b["minlat"] + b["maxlat"]) / 2
        code = codes.get(id(element)) or tags.get("_code")
        if not name or lng is None or lat is None or not code:
            continue
        key = (code, name)
        if key in seen or not settings.in_bbox(float(lng), float(lat)):
            continue
        seen.add(key)
        extent = [b.get("minlon", 0.0), b.get("minlat", 0.0),
                  b.get("maxlon", 0.0), b.get("maxlat", 0.0)]
        yield (f"{round(float(lng), 6)},{round(float(lat), 6)},{code},{element.get('id')},"
               + ",".join(f"{round(float(v), 6)}" for v in extent)
               + f",{name}\n")


def _overpass_query() -> str:
    b = settings.bbox
    bbox = f"{b[1]},{b[0]},{b[3]},{b[2]}"
    union = "".join(f"  nwr{filters}({bbox});\n" for _code, filters in POI_QUERIES)
    return f"[out:json][timeout:180];\n(\n{union});\nout center bb tags;"


def _code_for(tags: dict) -> str | None:
    """Qual código da taxonomia o elemento atende (o primeiro que casar)."""
    checks = (
        ("AIR", "aeroway", "aerodrome"), ("UNI", "amenity", "university"),
        ("SCH", "amenity", "college"), ("HOS", "amenity", "hospital"),
        ("SHP", "shop", "mall"), ("SPO", "leisure", "stadium"),
        ("PRK", "leisure", "park"), ("ZOO", "tourism", "zoo"),
        ("CNV", "amenity", "conference_centre"), ("CNV", "amenity", "exhibition_centre"),
        ("EXT", "amenity", "bus_station"),
    )
    for code, key, value in checks:
        if tags.get(key) == value:
            return code
    return None


def pois() -> None:
    """Baixa os equipamentos do OpenStreetMap (Overpass) para ``pois.csv``."""
    out = settings.pois_csv
    if out.exists():
        log.info("já processado: %s", out.name)
        return
    log.info("baixando equipamentos do OpenStreetMap (Overpass)")
    data = urllib.parse.urlencode({"data": _overpass_query()}).encode()
    request = urllib.request.Request(
        settings.overpass_url, data=data, headers={"User-Agent": "demand-data/1.0"}
    )
    with urllib.request.urlopen(request, timeout=300) as response:  # noqa: S310
        payload = json.loads(response.read().decode("utf-8"))

    elements = payload.get("elements", [])
    codes = {}
    for element in elements:
        code = _code_for(element.get("tags") or {})
        if code:
            codes[id(element)] = code
    kept = 0
    with open(out, "w", encoding="utf-8") as fout:
        for row in parse_overpass(elements, codes):
            fout.write(row)
            kept += 1
    log.info("equipamentos: %d lidos, %d mantidos -> %s", len(elements), kept, out.name)


def acquire() -> None:
    """Baixa + processa tudo (idempotente)."""
    settings.ensure_sources()
    od()
    cnefe()
    censo()
    lotes()
    pois()
