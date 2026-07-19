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


def acquire() -> None:
    """Baixa + processa tudo (idempotente)."""
    settings.ensure_sources()
    od()
    cnefe()
    censo()
    lotes()
