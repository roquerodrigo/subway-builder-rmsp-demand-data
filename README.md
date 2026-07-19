# subway-builder-rmsp-demand-data

Gera **pops de demanda** (formato Subway Builder / [depot](https://github.com/Subway-Builder-Modded/depot)) a partir da **Pesquisa Origem-Destino 2023** do Metrô-SP.

Projeto enxuto e **autossuficiente**: baixa e processa os próprios dados das pesquisas (em `data/`) e gera os pops. Não depende de nenhum outro projeto. Não faz mapa base, roteamento nem bundle — isso fica com o depot / jogo.

## Regras da demanda (v1)

1. **Contagem ∝ pessoas** — cada zona OD recebe `N = round(população_da_zona / people_per_pop)` pops. A população vem da própria pesquisa (`FE_PESS` por zona de residência).
2. **Distribuição por densidade** — a residência de cada pop é amostrada entre pontos-candidato da zona **proporcional à densidade populacional**; setores (Censo 2022) com mais gente recebem mais pontos.
3. **Destino pela matriz O-D** — o local de trabalho de cada pop é sorteado pela matriz origem→destino da pesquisa e posicionado na zona de destino, também por densidade.

`Σ tamanho dos pops == população total` (invariante).

## Fontes de dados

| Dado | Uso | Origem |
|---|---|---|
| Zonas OD (shapefile) + microdados (DBF) | zonas, população, matriz O-D | Pesquisa OD 2023 (Metrô-SP) |
| População por setor (`setor_pop.csv`) | densidade populacional | Censo 2022 (IBGE) |
| Endereços CNEFE (`cnefe.csv`) | densidade (fora da capital) | CNEFE 2022 (IBGE) |
| Lotes IPTU (`lotes.csv`) | densidade por **área construída e uso** (só na capital) | GeoSampa (PMSP), via WFS |

O comando `sources` baixa e processa tudo para `data/sources/` (idempotente). O CNEFE bruto (SP) tem ~1 GB e é filtrado ao bbox da RMSP em streaming. Os **lotes do GeoSampa** (~1,68M, camada WFS `lote_cidadao`) trazem uso + área construída por lote → densidade **híbrida**: casa/trabalho por área construída real na **capital**; CNEFE no resto da RMSP. `lotes.csv` é opcional (ausente = tudo no CNEFE).

## Uso

```bash
uv sync
uv run demand-data sources    # baixa + processa OD/CNEFE/Censo -> data/sources/
uv run demand-data generate   # OD + densidade -> pops -> out/demand_data.json + out/pops_map.html
uv run demand-data od-only    # só a extração da OD (diagnóstico)
./scripts/publish_map.sh      # publica out/pops_map.html no GitHub Pages (branch gh-pages)
```

`generate` roda `sources` automaticamente se os dados ainda não estiverem em `data/sources`.

Saídas em `out/`:
- **`demand_data.json`** (+ `.gz`) — importável no depot (`DemandData`). Pops saem com `drivingSeconds/Distance = 0` para o depot rotear na importação.
- **`pops_map.html`** — mapa dos pontos (raio ∝ tamanho, cor = balanço moradia×trabalho). Versão publicada: **https://www.rodrigoroque.dev/subway-builder-rmsp-demand-data/**

## Configuração (`.env`)

Veja `.env.example`. Principais: `DEMAND_PEOPLE_PER_POP` (pessoas/pop, controla o total), `DEMAND_DENSITY_CELL` (resolução da grade de densidade), `DEMAND_DEST_CAP` (destinos O-D por origem), `DEMAND_SOURCES_DIR` (onde estão os dados).

## Estrutura

```
src/demand_data/
  sources.py   # aquisição: OD/CNEFE/Censo + lotes GeoSampa (WFS) em data/sources
  od.py        # extração da Pesquisa OD (zonas + população + matriz)
  density.py   # densidade híbrida: lotes IPTU (capital) + CNEFE (resto)
  pops.py      # algoritmo de geração dos pops
  depot.py     # escreve demand_data.json (+ .gz)
  htmlmap.py   # mapa HTML (folium)
  cli.py
```
