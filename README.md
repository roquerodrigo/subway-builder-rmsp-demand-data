# subway-builder-rmsp-demand-data

Gera **pops de demanda** (formato Subway Builder / [depot](https://github.com/Subway-Builder-Modded/depot)) a partir da **Pesquisa Origem-Destino 2023** do Metrô-SP.

Projeto enxuto e **autossuficiente**: baixa e processa os próprios dados das pesquisas (em `data/`) e gera os pops. Não depende de nenhum outro projeto. Não faz mapa base, roteamento nem bundle — isso fica com o depot / jogo.

## Regras da demanda (v1)

1. **Tamanho do pop ∝ área** — o orçamento de pops (`Σ round(população_da_zona / people_per_pop)`) é distribuído entre as zonas proporcionalmente à **área**, e é isso que define o tamanho típico do pop de cada zona, para que o mapa não fique com pops muito mais densos no centro. A população vem da própria pesquisa (`FE_PESS` por zona de residência).
2. **Distribuição por densidade** — a residência de cada pop é amostrada entre pontos-candidato da zona **proporcional à densidade populacional**; setores (Censo 2022) com mais gente recebem mais pontos. Cada ponto-candidato fica sobre um **endereço ou lote real** (o mais próximo do centroide da sua célula), e não sobre o centroide em si, que por ser a média dos dois lados da via cairia no meio da rua.
3. **Destino pelo que a pessoa declarou** — a pesquisa registra o destino principal de cada morador: local de trabalho (`ZONATRA1`), escola (`ZONA_ESC`) ou, para quem não tem nenhum dos dois, os motivos não-pendulares (compras, saúde, lazer, expandidos por viagem). Cada grupo é repartido entre os destinos proporcionalmente à **sua** matriz, em **pessoas** (repartir o *número de pops* zeraria os destinos de fluxo menor). Antes toda a população disputava a matriz de trabalho, que cobre metade dela — os outros 10,8 milhões iam para empregos que a pesquisa nunca registrou.
4. **Saída da região** — quem trabalha ou estuda fora das zonas da pesquisa (87 mil pessoas) vai para um **portal externo** (`EXT_*`, o `outside_connection` do depot), projetado na borda do recorte mais próxima da zona de origem.
5. **Equipamentos nomeados** — aeroportos, campi, estádios, shoppings, hospitais e parques aparecem como pontos próprios (`AIR_*`, `UNI_*`, … na taxonomia do depot). Eles **capturam** a demanda que a pesquisa já manda para a zona deles, até a capacidade de um dia típico, em vez de criar demanda nova por um modelo de gravidade: as origens continuam sendo as da matriz O-D e a população total não muda.
6. **Pontos sorteados ∝ densidade** — cada zona recebe `demanda / people_per_point` pontos de moradia e de trabalho, sorteados entre as células de ~50 m da zona com probabilidade proporcional ao peso (gente ou área construída) de cada uma. Usar *todas* as células desenharia a grade no mapa, com pontos alinhados e igualmente espaçados; sorteando, eles se adensam onde há gente e somem onde não há. Como o sorteio já é proporcional à densidade, todo ponto carrega aproximadamente a mesma demanda — quem representa a densidade é a **quantidade** de pontos, não o tamanho de cada um.

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

## Testes

```bash
uv run pytest        # cobertura mínima de 90% (hoje 100%), medida automaticamente
uv run ruff check .
```

Os testes rodam sobre recortes minúsculos das fontes reais, no mesmo formato, sem rede nem os arquivos de ~1 GB. Além do comportamento normal, a suíte fixa as regressões dos problemas já corrigidos: concentração de empregos num único ponto, destinos O-D zerados pela repartição por número de pops, viés espacial no sorteio das células, pontos fora de construções reais e o mapa em branco quando o script roda antes do folium montar o mapa.

`generate` roda `sources` automaticamente se os dados ainda não estiverem em `data/sources`.

Saídas em `out/`:
- **`demand_data.json`** (+ `.gz`) — importável no depot (`DemandData`).
- **`config.json`** e **`description.md`** — exigidos na submissão ao Railyard (recorte, população, câmera inicial e a ficha do mapa).
- **`pops_map.html`** — mapa dos pontos (raio ∝ tamanho, cor = balanço moradia×trabalho). Versão publicada: **https://www.rodrigoroque.dev/subway-builder-rmsp-demand-data/**

Com `DEMAND_OSRM_URL` apontando para um servidor OSRM local, os pops já saem com `drivingSeconds`/`drivingDistance` preenchidos; sem ele os campos ficam em 0 e o depot roteia na importação. O `docstring` de `src/demand_data/routing.py` traz os comandos para subir o servidor.

## Configuração (`.env`)

Veja `.env.example`. Principais: `DEMAND_PEOPLE_PER_POP` (pessoas/pop, controla o total), `DEMAND_PEOPLE_PER_POINT` (pessoas por ponto, controla quantos pontos cada zona recebe), `DEMAND_DENSITY_CELL` (grade de agregação/espaçamento mínimo), `DEMAND_DEST_CAP` (destinos O-D por origem), `DEMAND_SOURCES_DIR` (onde estão os dados).

## Estrutura

```
src/demand_data/
  sources.py   # aquisição: OD/CNEFE/Censo + lotes GeoSampa (WFS) em data/sources
  od.py        # extração da Pesquisa OD (zonas + população + matriz)
  density.py   # densidade híbrida: lotes IPTU (capital) + CNEFE (resto)
  pops.py      # algoritmo de geração dos pops
  depot.py     # escreve demand_data.json (+ .gz)
  htmlmap.py   # mapa HTML (folium)
  railyard.py  # config.json + description.md para a submissão
  routing.py   # tempo/distância de carro via OSRM local
  cli.py
```
