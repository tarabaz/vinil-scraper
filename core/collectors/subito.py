"""Collector Subito: ricerca annunci via scraping della pagina di ricerca pubblica.

Subito non ha un'API pubblica ufficiale (a differenza di eBay), quindi questo
collector legge i dati strutturati (schema.org, tag <script type="application/
ld+json">) incorporati nella pagina HTML dei risultati — è il modo più robusto
per estrarre annunci senza dipendere dai nomi delle classi CSS, che cambiano
più spesso dei dati strutturati usati per la SEO.

NON VERIFICATO contro il sito reale: l'ambiente in cui è stato scritto questo
codice blocca l'accesso in rete a subito.it, quindi non è stato possibile
controllare la struttura vera della pagina. Va testato e probabilmente
aggiustato (formato dei dati strutturati, eventuale fallback se assenti)
contro il sito vero."""

import json
import re

import requests
from bs4 import BeautifulSoup

from core.collectors.base import Listing

SEARCH_URL = "https://www.subito.it/annunci-italia/vendita/usato/"
HOME_URL = "https://www.subito.it/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
# Header più completi di un solo User-Agent: molte protezioni anti-scraping
# (403 "a freddo") controllano anche Accept/Accept-Language/Sec-Fetch-*, non
# solo lo User-Agent.
REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
}


def _extract_id_from_url(url: str | None) -> str | None:
    """Molti siti di annunci italiani mettono un ID numerico in fondo all'URL
    (es. .../annuncio-123456789.htm) — lo uso come fallback se manca uno SKU
    esplicito nei dati strutturati."""
    if not url:
        return None
    match = re.search(r"(\d+)\.htm", url)
    return match.group(1) if match else url


def _parse_json_ld(html_text: str) -> list[dict]:
    """Cerca annunci nei blocchi di dati strutturati schema.org della pagina."""
    soup = BeautifulSoup(html_text, "html.parser")
    items = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate.get("@type") in ("Product", "Offer"):
                items.append(candidate)
    return items


def _fetch_search_page(query: str) -> requests.Response:
    """Prima visita la homepage per ottenere i cookie di sessione (alcune
    protezioni anti-scraping bloccano richieste 'a freddo' senza una
    navigazione precedente), poi fa la ricerca vera con un Referer plausibile."""
    session = requests.Session()
    session.headers.update(REQUEST_HEADERS)

    session.get(HOME_URL, timeout=15)

    response = session.get(
        SEARCH_URL,
        params={"q": query},
        headers={"Referer": HOME_URL},
        timeout=15,
    )
    response.raise_for_status()
    return response


def search_items(query: str, limit: int = 50) -> list[dict]:
    """NON VERIFICATO contro subito.it reale — vedi il docstring del modulo."""
    response = _fetch_search_page(query)

    structured = _parse_json_ld(response.text)

    results = []
    for entry in structured[:limit]:
        offers = entry.get("offers") or {}
        price = offers.get("price") if isinstance(offers, dict) else None
        url = entry.get("url")
        results.append(
            {
                "source": "subito",
                "external_id": entry.get("sku") or _extract_id_from_url(url),
                "title": entry.get("name"),
                "price": float(price) if price else None,
                "currency": offers.get("priceCurrency") if isinstance(offers, dict) else None,
                "url": url,
                "image_url": entry.get("image"),
                "listed_at": None,
            }
        )
    return results


def _to_listing(item: dict) -> Listing:
    return Listing(
        source=item["source"],
        external_id=item["external_id"],
        title=item["title"],
        price=item["price"],
        currency=item["currency"],
        url=item["url"],
        image_urls=[item["image_url"]] if item.get("image_url") else [],
        listed_at=item.get("listed_at"),
    )


class SubitoCollector:
    """Adatta search_items() all'interfaccia comune Collector."""

    name = "subito"

    def search(self, query: str, **settings) -> list[Listing]:
        limit = settings.get("limit", 50)
        items = search_items(query, limit=limit)
        return [_to_listing(item) for item in items]


if __name__ == "__main__":
    items = search_items("vinile", limit=5)
    if not items:
        print(
            "Nessun risultato — probabile che il parsing vada aggiustato "
            "contro la pagina reale (vedi docstring del modulo)."
        )
    for item in items:
        print(f"{item['title']} — {item['price']} {item['currency']} — {item['url']}")
