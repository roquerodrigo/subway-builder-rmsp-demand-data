# subway-builder-rmsp-demand-data

Gera **pops de demanda** (formato Subway Builder / [depot](https://github.com/Subway-Builder-Modded/depot)) a partir das **viagens observadas** da **Pesquisa Origem-Destino 2023** do Metrô-SP.

Projeto enxuto: consome as viagens já geolocalizadas do repositório de dados [transporte-sp-origem-destino](https://www.rodrigoroque.dev/transporte-sp-origem-destino/dados/) — cada linha é uma viagem real, com a coordenada de origem e destino resolvidas — e as converte em pops. Não extrai a matriz nem resolve densidade intra-zona (isso é feito no repositório de dados). Não faz mapa base, roteamento nem bundle — isso fica com o depot / jogo.

## Regras da demanda (v2)

1. **Uma viagem observada por linha** — o repositório de dados publica `fluxos.parquet`: cada linha é uma viagem da pesquisa, com a coordenada real de onde começa e termina, o motivo **no destino** (`MOTIVO_D`) e o peso de expansão `trips` (`FE_VIA`, viagens/dia). Nada é sorteado: o par origem→destino é o par de fato registrado.
2. **Orientação casa↔atividade pelo motivo** — cada viagem é orientada em uma ponta-casa e uma ponta-atividade. Na volta pra casa (motivo **Residência**) a casa é o **destino**; em qualquer outra viagem a casa é a **origem** e o destino é a atividade. Assim a ida (casa→trabalho) e a volta (trabalho→casa) do mesmo trajeto caem no mesmo par de pontos e são **fundidas** num pop, somando os `trips`.
3. **`size` do pop = viagens/dia** — o tamanho de cada pop é o número de viagens que a pesquisa expande para aquele par. `Σ tamanho dos pops == total de viagens/dia da pesquisa` (invariante).
4. **Pontos de papel único** — as coordenadas são quantizadas a uma grade fina (~50 m) para deduplicar endereços quase coincidentes; casa e atividade recebem ids por papel (`z{zona}h{i}` moradia, `z{zona}w{i}` destino), então nenhum ponto é ao mesmo tempo residência e destino. A zona do id vem da própria viagem.
5. **Destino tipado pelo motivo** — educação → escola (`SCH`), saúde → hospital (`HOS`), compras/trabalho no comércio → shopping (`SHP`), lazer → parque (`PRK`). Os demais motivos (indústria, serviços, refeição, assuntos pessoais) são difusos e ficam sem tipo.
6. **Equipamentos nomeados dão identidade, não demanda** — cada destino tipado adota a identidade do equipamento real **mais próximo** que atende o seu motivo (escolas, campi, hospitais, shoppings, parques, estádios do **OpenStreetMap**, cada um com o seu `osm_id`), herdando nome, tipo e coordenada, sem criar nenhum ponto. Entre dois igualmente próximos, o de maior porte (área do contorno do OSM) desempata. A demanda continua sendo a que a pesquisa manda para o destino.

## Fontes de dados

| Dado | Uso | Origem |
|---|---|---|
| Viagens (`fluxos.parquet`) | pares origem→destino geolocalizados, motivo e peso | [transporte-sp-origem-destino](https://www.rodrigoroque.dev/transporte-sp-origem-destino/dados/), a partir da Pesquisa OD 2023 (Metrô-SP) |
| Equipamentos (`pois.csv`) | escolas, campi, hospitais, shoppings, parques, estádios | OpenStreetMap, via Overpass |

O comando `sources` baixa os dois para `data/sources/` (idempotente).

## Uso

```bash
uv sync
uv run demand-data sources     # baixa viagens + equipamentos -> data/sources/
uv run demand-data generate    # viagens -> pops -> out/demand_data.json + out/pops_map.html
uv run demand-data flows-only  # só a leitura das viagens (diagnóstico)
./scripts/publish_map.sh       # publica out/pops_map.html no GitHub Pages (branch gh-pages)
```

## Testes

```bash
uv run pytest        # cobertura mínima de 90% (hoje 100%), medida automaticamente
uv run ruff check .
```

Os testes rodam sobre recortes minúsculos das fontes, no mesmo formato, sem rede. Além do comportamento normal, a suíte fixa as invariantes do modelo: fusão da ida e da volta num pop só, papel único do ponto, conservação das viagens e coordenada nunca duplicada.

`generate` roda `sources` automaticamente se os dados ainda não estiverem em `data/sources`.

Saídas em `out/`:
- **`demand_data.json`** (+ `.gz`) — importável no depot (`DemandData`).
- **`config.json`** e **`description.md`** — exigidos na submissão ao Railyard (recorte, total de viagens, câmera inicial e a ficha do mapa).
- **`pops_map.html`** — mapa dos pontos (raio ∝ demanda, cor = balanço moradia×destino). Versão publicada: **https://www.rodrigoroque.dev/subway-builder-rmsp-demand-data/**

Com `DEMAND_OSRM_URL` apontando para um servidor OSRM local, os pops já saem com `drivingSeconds`/`drivingDistance` preenchidos; sem ele os campos ficam em 0 e o depot roteia na importação. O `docstring` de `src/demand_data/routing.py` traz os comandos para subir o servidor.

## Configuração (`.env`)

Veja `.env.example`. Principais: `DEMAND_DENSITY_CELL` (grade de quantização dos pontos), `DEMAND_MAX_POP_SIZE` (fatia os pops maiores), `DEMAND_POI_SNAP_M` (raio de adoção do equipamento nomeado), `DEMAND_FLOW_URL` (URL do parquet de viagens), `DEMAND_SOURCES_DIR` (onde estão os dados).

## Estrutura

```
src/demand_data/
  sources.py   # aquisição: viagens (parquet) + equipamentos OSM (Overpass) em data/sources
  flows.py     # leitura das viagens e orientação casa↔atividade pelo motivo
  pops.py      # viagens -> pops (quantização, fusão ida+volta, fatiamento)
  pois.py      # adoção do equipamento nomeado mais próximo por destino
  depot.py     # escreve demand_data.json (+ .gz)
  htmlmap.py   # mapa HTML (folium)
  railyard.py  # config.json + description.md para a submissão
  routing.py   # tempo/distância de carro via OSRM local
  cli.py
```
