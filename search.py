"""Busca e catalogação de lugares sagrados do Contestado.

Coleta URLs de três fontes (Tavily, Google Scholar, Hemeroteca Digital),
deduplica via SQLite e exporta CSVs com score de relevância.

Uso:
    export TAVILY_API_KEY="tvly-..."
    python search.py
"""

from __future__ import annotations

import sqlite3
import time
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from itertools import product
from typing import Optional
from urllib.parse import urlparse, urljoin

import requests
import pandas as pd
from bs4 import BeautifulSoup
from rapidfuzz import fuzz
from dotenv import load_dotenv

load_dotenv()


# ── Configuration ─────────────────────────────────────────────────────────────

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

DB_PATH = "links_vistos.db"
OUTPUT_CSV = "resultados.csv"
CITIES_CSV = "ocorrencias_cidades.csv"

MAX_PAGE_TEXT_CHARS = 5_000
REQUEST_TIMEOUT = 15
TAVILY_TIMEOUT = 30
RATE_LIMIT = 1           # seconds between requests
FUZZY_CITY_THRESHOLD = 85

MAX_TAVILY_QUERIES = 2_000
MAX_SCHOLAR_QUERIES = 200
MAX_HEMEROTECA_QUERIES = 100
MAX_WORKERS = 5          # concurrent queries per source
TAVILY_PAGES = 5
TAVILY_MAX_RESULTS = 20

TIPOS = [
    "Poço", "Pocinho", "Poco",
    "Fonte", "Fonte santa", "Fonte milagrosa",
    "Nascente",
    "Olho d'água", "Olho dagua",
    "Gruta", "Caverna",
    "Ermida", "Capela", "Oratório",
    "Cruz", "Cruzeiro",
    "Pedra do monge", "Pedra santa",
    "Trilha do monge",
    "Lugar santo",
    "Passagem do monge",
    "Marca do monge",
]

MONGES = [
    "João Maria", "Joao Maria",
    "São João Maria", "Sao Joao Maria",
    "Monge João Maria", "Monge Joao Maria",
    "José Maria", "Jose Maria",
    "Monge José Maria", "Monge Jose Maria",
]

LOCAIS = [
    "Abelardo Luz", "Água Doce", "Bela Vista do Toldo",
    "Bom Jesus", "Caçador", "Calmon", "Campo Alegre",
    "Campos Novos", "Canoinhas", "Capinzal", "Catanduvas",
    "Curitibanos", "Fraiburgo", "Frei Rogério", "Ibiam",
    "Iomerê", "Irani", "Itaiópolis", "Lebon Régis",
    "Major Vieira", "Matos Costa", "Monte Castelo",
    "Papanduva", "Pinheiro Preto", "Ponte Alta do Norte",
    "Porto União", "Rio das Antas", "Santa Cecília",
    "São Cristóvão do Sul", "Três Barras", "Timbó Grande",
    "Videira", "Vargem", "Contestado",
]

QUERY_TEMPLATES = [
    '"{tipo}" "{monge}" "{local}"',
    '"{tipo}" "{local}"',
    '"{tipo}" "{local}" contestado',
    '"{tipo}" "{local}" santa catarina',
    '"{tipo}" "{local}" parana',
    '"{tipo}" "{local}" tradição',
    '"{tipo}" "{local}" romaria',
    '"{tipo}" "{local}" milagre',
    '"{tipo}" "{local}" peregrinação',
    '"{tipo}" "{local}" história',
]

DOMAIN_FILTERS = [
    "site:sc.gov.br",
    "site:gov.br",
    "site:ufsc.br",
    "site:blogspot.com",
    "site:wikipedia.org",
]

RELEVANCE_KEYWORDS = [
    "monge", "joão maria", "joao maria",
    "fonte", "gruta", "poço", "milagre", "romaria", "santo",
]


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class SearchResult:
    query: str
    title: str
    url: str
    domain: str
    description: str
    detected_city: Optional[str]
    mentions_joao_maria: bool
    relevance_score: int


# ── Query generation ──────────────────────────────────────────────────────────

def build_queries() -> list[str]:
    queries: list[str] = []

    for tipo, monge, local in product(TIPOS, MONGES, LOCAIS):
        for template in QUERY_TEMPLATES:
            queries.append(template.format(tipo=tipo, monge=monge, local=local))

    for domain, tipo, local in product(DOMAIN_FILTERS, TIPOS, LOCAIS):
        queries.append(f'"{tipo}" "{local}" {domain}')

    return list(set(queries))


# ── Database ──────────────────────────────────────────────────────────────────

class LinkDatabase:
    def __init__(self, path: str = DB_PATH) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS links_vistos (link TEXT PRIMARY KEY)"
        )
        self._conn.commit()

    def mark_seen(self, url: str) -> bool:
        """Insert URL; return True if new, False if already seen."""
        try:
            self._conn.execute("INSERT INTO links_vistos VALUES (?)", (url,))
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def close(self) -> None:
        self._conn.close()


def load_done_queries(csv_path: str) -> set[str]:
    """Return the set of queries already present in a previous results CSV."""
    if not os.path.exists(csv_path):
        return set()
    df = pd.read_csv(csv_path, usecols=["busca"], dtype=str)
    return set(df["busca"].dropna().unique())


# ── Text & classification ─────────────────────────────────────────────────────

def fetch_page_text(url: str) -> str:
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return BeautifulSoup(response.text, "html.parser").get_text(" ", strip=True)[:MAX_PAGE_TEXT_CHARS]
    except Exception:
        return ""


def detect_city(text: str) -> Optional[str]:
    text_lower = text.lower()
    for city in LOCAIS:
        if fuzz.partial_ratio(city.lower(), text_lower) > FUZZY_CITY_THRESHOLD:
            return city
    return None


def score_relevance(text: str) -> int:
    text_lower = text.lower()
    return sum(1 for kw in RELEVANCE_KEYWORDS if kw in text_lower)


def build_result(query: str, title: str, url: str, description: str) -> SearchResult:
    page_text = fetch_page_text(url)
    full_text = f"{title} {description} {page_text}"
    return SearchResult(
        query=query,
        title=title,
        url=url,
        domain=urlparse(url).netloc,
        description=description,
        detected_city=detect_city(full_text),
        mentions_joao_maria=(
            "joão maria" in full_text.lower() or "joao maria" in full_text.lower()
        ),
        relevance_score=score_relevance(full_text),
    )


# ── Search sources ────────────────────────────────────────────────────────────

def tavily_search(
    query: str,
    db: LinkDatabase,
    results: list[SearchResult],
    pages: int = TAVILY_PAGES,
    max_results: int = TAVILY_MAX_RESULTS,
) -> None:
    for _ in range(pages):
        payload = {
            "api_key": TAVILY_API_KEY,
            "query": query,
            "search_depth": "advanced",
            "max_results": max_results,
            "include_raw_content": False,
        }
        try:
            response = requests.post(
                "https://api.tavily.com/search",
                json=payload,
                timeout=TAVILY_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            print(f"  Tavily error: {exc}")
            continue

        for item in data.get("results", []):
            url = item.get("url", "")
            if url and db.mark_seen(url):
                results.append(build_result(query, item.get("title", ""), url, item.get("content", "")))

        time.sleep(RATE_LIMIT)


def scholar_search(
    query: str,
    db: LinkDatabase,
    results: list[SearchResult],
    pages: int = 5,
) -> None:
    for page in range(pages):
        url = f"https://scholar.google.com/scholar?q={query}&start={page * 10}"
        try:
            response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=REQUEST_TIMEOUT)
            soup = BeautifulSoup(response.text, "html.parser")
        except requests.RequestException as exc:
            print(f"  Scholar error: {exc}")
            continue

        for item in soup.select(".gs_ri"):
            title_el = item.select_one(".gs_rt")
            desc_el = item.select_one(".gs_rs")
            if not title_el:
                continue
            link_el = title_el.find("a")
            if not link_el:
                continue
            item_url = link_el.get("href", "")
            if item_url and db.mark_seen(item_url):
                results.append(build_result(
                    query, title_el.text, item_url, desc_el.text if desc_el else ""
                ))

        time.sleep(RATE_LIMIT * 2)


def hemeroteca_search(
    query: str,
    db: LinkDatabase,
    results: list[SearchResult],
    pages: int = 5,
) -> None:
    base_url = "https://hemerotecadigital.bn.gov.br"
    for page in range(1, pages + 1):
        url = f"{base_url}/?q={query}&page={page}"
        try:
            response = requests.get(url, timeout=REQUEST_TIMEOUT)
            soup = BeautifulSoup(response.text, "html.parser")
        except requests.RequestException as exc:
            print(f"  Hemeroteca error: {exc}")
            continue

        for link_el in soup.select("a")[:15]:
            href = link_el.get("href", "")
            if not href:
                continue
            full_url = urljoin(base_url, href)  # resolves relative URLs
            if db.mark_seen(full_url):
                results.append(build_result(query, link_el.text.strip(), full_url, ""))

        time.sleep(RATE_LIMIT * 2)


# ── Output ────────────────────────────────────────────────────────────────────

def _to_rows(results: list[SearchResult]) -> list[dict]:
    return [
        {
            "busca": r.query,
            "titulo": r.title,
            "link": r.url,
            "dominio": r.domain,
            "descricao": r.description,
            "cidade_detectada": r.detected_city,
            "mencao_joao_maria": r.mentions_joao_maria,
            "score_relevancia": r.relevance_score,
        }
        for r in results
    ]


def append_results(new_results: list[SearchResult]) -> None:
    """Append new rows to the CSV immediately after each query completes."""
    if not new_results:
        return
    write_header = not os.path.exists(OUTPUT_CSV)
    pd.DataFrame(_to_rows(new_results)).to_csv(
        OUTPUT_CSV, mode="a", header=write_header, index=False, encoding="utf-8"
    )


def finalise_results() -> None:
    """Sort the accumulated CSV by relevance score and regenerate the cities file."""
    if not os.path.exists(OUTPUT_CSV):
        return
    df = pd.read_csv(OUTPUT_CSV, dtype=str)
    df = df.sort_values("score_relevancia", ascending=False)
    df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8")
    df["cidade_detectada"].value_counts().to_csv(CITIES_CSV)
    print(f"\nTotal acumulado: {len(df)}")
    print(f"Arquivos: {OUTPUT_CSV}, {CITIES_CSV}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if not TAVILY_API_KEY:
        raise EnvironmentError("TAVILY_API_KEY não definida. Execute: export TAVILY_API_KEY='tvly-...'")

    queries = build_queries()
    done = load_done_queries(OUTPUT_CSV)
    pending = [q for q in queries if q not in done]
    print(f"Queries geradas: {len(queries)} | já pesquisadas: {len(done)} | pendentes: {len(pending)}")

    db = LinkDatabase()
    results: list[SearchResult] = []
    lock = threading.Lock()

    def run_batch(search_fn, batch: list[str], label: str) -> None:
        total = len(batch)
        print(f"\n{label}: {total} queries")
        counter = {"n": 0}

        def run_one(query: str) -> None:
            with lock:
                counter["n"] += 1
                n = counter["n"]
            print(f"  {label} [{n}/{total}]: {query}")
            local: list[SearchResult] = []
            search_fn(query, db, local)
            with lock:
                results.extend(local)
                append_results(local)

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(run_one, q) for q in batch]
            for f in as_completed(futures):
                f.result()  # re-raises any exception from the thread

    try:
        sources = [
            (tavily_search,     pending[:MAX_TAVILY_QUERIES],     "Tavily"),
            (scholar_search,    pending[:MAX_SCHOLAR_QUERIES],    "Scholar"),
            (hemeroteca_search, pending[:MAX_HEMEROTECA_QUERIES], "Hemeroteca"),
        ]
        with ThreadPoolExecutor(max_workers=len(sources)) as executor:
            futures = [executor.submit(run_batch, fn, batch, label) for fn, batch, label in sources]
            for f in as_completed(futures):
                f.result()
    finally:
        finalise_results()
        db.close()


if __name__ == "__main__":
    main()
