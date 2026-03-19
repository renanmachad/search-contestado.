# Busca Contestado

Ferramenta de pesquisa para catalogar lugares sagrados da região do Contestado (SC/PR) — poços, grutas, fontes, ermidas, cruzeiros e trilhas associados ao monge João Maria e José Maria.

Os resultados são coletados de três fontes (Tavily, Google Scholar, Hemeroteca Digital), deduplicados e exportados em CSV com score de relevância.

## Pré-requisitos

- Python 3.10+
- Chave de API do [Tavily](https://tavily.com)

## Instalação

```bash
pip install -r requirements.txt
```

## Configuração

Crie um arquivo `.env` na raiz do projeto:

```env
TAVILY_API_KEY="tvly-..."
```

> Adicione `.env` ao `.gitignore` para não expor sua chave de API.

## Execução

```bash
python search.py
```

Na primeira execução, todas as queries são processadas. Nas execuções seguintes, as queries já presentes em `resultados.csv` são ignoradas automaticamente — apenas as pendentes são buscadas.

## Arquivos gerados

| Arquivo                   | Descrição                                                |
| ------------------------- | -------------------------------------------------------- |
| `resultados.csv`          | Todos os resultados acumulados, ordenados por relevância |
| `ocorrencias_cidades.csv` | Frequência de resultados por município                   |
| `links_vistos.db`         | SQLite com URLs já coletadas (evita duplicatas)          |

## Colunas do `resultados.csv`

| Coluna              | Descrição                                           |
| ------------------- | --------------------------------------------------- |
| `busca`             | Query utilizada                                     |
| `titulo`            | Título da página                                    |
| `link`              | URL                                                 |
| `dominio`           | Domínio da URL                                      |
| `descricao`         | Trecho retornado pela API                           |
| `cidade_detectada`  | Município identificado no texto (fuzzy match)       |
| `mencao_joao_maria` | `True` se o texto menciona João Maria ou José Maria |
| `score_relevancia`  | Pontuação de 0 a 9 com base em palavras-chave       |

## Recomendações

- ~~**Paralelismo** — as buscas nas três fontes são sequenciais. Usar `concurrent.futures.ThreadPoolExecutor` reduziria o tempo total significativamente, já que cada chamada passa a maior parte do tempo esperando resposta da rede.~~
- **Busca em texto completo** — `fetch_page_text` faz uma requisição extra para cada URL encontrada. Desativar ou tornar opcional para queries de baixo score economiza tempo e banda.
- ~~**Checkpoint incremental** — atualmente o CSV só é salvo ao fim da execução. Salvar a cada N queries evita perder progresso em caso de interrupção.~~
- **Rotação de User-Agent** — o scraping do Google Scholar com User-Agent fixo tende a ser bloqueado rapidamente. Usar uma lista rotativa ou biblioteca como `fake-useragent` aumenta a resiliência.

## Municípios cobertos

Abelardo Luz, Água Doce, Bela Vista do Toldo, Bom Jesus, Caçador, Calmon, Campo Alegre, Campos Novos, Canoinhas, Capinzal, Catanduvas, Curitibanos, Fraiburgo, Frei Rogério, Ibiam, Iomerê, Irani, Itaiópolis, Lebon Régis, Major Vieira, Matos Costa, Monte Castelo, Papanduva, Pinheiro Preto, Ponte Alta do Norte, Porto União, Rio das Antas, Santa Cecília, São Cristóvão do Sul, Três Barras, Timbó Grande, Videira, Vargem.
