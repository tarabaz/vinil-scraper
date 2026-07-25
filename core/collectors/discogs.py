"""Client Discogs: ricerca release e prezzi suggeriti per condizione (deterministico, niente AI)."""

import os

import requests
from dotenv import load_dotenv

load_dotenv()

DISCOGS_TOKEN = os.getenv("DISCOGS_TOKEN")

BASE_URL = "https://api.discogs.com"
USER_AGENT = "VinilScraper/1.0 +https://github.com/tarabaz/vinil-scraper"

# Dal peggiore al migliore, come definito da Discogs.
CONDITION_ORDER = [
    "Poor (P)",
    "Fair (F)",
    "Good (G)",
    "Good Plus (G+)",
    "Very Good (VG)",
    "Very Good Plus (VG+)",
    "Near Mint (NM or M-)",
    "Mint (M)",
]


def _headers() -> dict:
    if not DISCOGS_TOKEN:
        raise SystemExit("Errore: DISCOGS_TOKEN non impostato in .env")
    return {
        "User-Agent": USER_AGENT,
        "Authorization": f"Discogs token={DISCOGS_TOKEN}",
    }


def search_release(artist: str, title: str) -> dict | None:
    """Cerca una release su Discogs. Ritorna il primo risultato (release_id, ecc.) o None."""
    response = requests.get(
        f"{BASE_URL}/database/search",
        headers=_headers(),
        params={
            "artist": artist,
            "release_title": title,
            "type": "release",
            "format": "Vinyl",
        },
        timeout=10,
    )
    response.raise_for_status()
    results = response.json().get("results", [])
    return results[0] if results else None


def search_by_title(title: str) -> list[dict]:
    """Cerca release solo per titolo, senza artista — usata come ultimo
    tentativo quando la vision legge il titolo ma non l'artista (es. logo
    stilizzato non riconosciuto). Più a rischio di falsi positivi di
    search_release (titoli comuni possono appartenere a più artisti): chi
    chiama deve verificare la somiglianza del titolo restituito in modo più
    severo di una ricerca con artista noto."""
    response = requests.get(
        f"{BASE_URL}/database/search",
        headers=_headers(),
        params={"release_title": title, "type": "release", "format": "Vinyl"},
        timeout=10,
    )
    response.raise_for_status()
    results = response.json().get("results", [])
    return [
        {
            "id": r["id"],
            "title": r.get("title"),
            "country": r.get("country"),
            "year": r.get("year"),
            "label": r.get("label"),
            "catno": r.get("catno"),
        }
        for r in results
    ]


def search_by_catalog_number(
    catno: str, country: str | None = None, year: str | None = None
) -> list[dict]:
    """Cerca release per codice catalogo. Ritorna tutti i candidati trovati (paese/anno/etichetta
    possono differire per lo stesso codice: non è garantito un solo risultato univoco).

    Se si conosce con certezza il paese e/o l'anno (es. letto dalla copertina in foto),
    passarli per restringere i candidati invece di indovinare o fare medie tra prezzi
    di edizioni diverse."""
    params = {"catno": catno, "type": "release", "format": "Vinyl"}
    if country:
        params["country"] = country
    if year:
        params["year"] = year

    response = requests.get(
        f"{BASE_URL}/database/search",
        headers=_headers(),
        params=params,
        timeout=10,
    )
    response.raise_for_status()
    results = response.json().get("results", [])
    return [
        {
            "id": r["id"],
            "title": r.get("title"),
            "country": r.get("country"),
            "year": r.get("year"),
            "label": r.get("label"),
            "catno": r.get("catno"),
            "format": r.get("format"),
        }
        for r in results
    ]


def get_price_suggestions(release_id: int) -> dict:
    """Ritorna il dizionario condizione -> {currency, value} per una release."""
    response = requests.get(
        f"{BASE_URL}/marketplace/price_suggestions/{release_id}",
        headers=_headers(),
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def price_range(price_suggestions: dict) -> tuple[dict | None, dict | None]:
    """Ritorna (prezzo condizione peggiore, prezzo condizione migliore) tra quelle disponibili."""
    available = [c for c in CONDITION_ORDER if c in price_suggestions]
    if not available:
        return None, None
    return price_suggestions[available[0]], price_suggestions[available[-1]]


def _print_prices(release_id: int) -> None:
    prices = get_price_suggestions(release_id)
    low, high = price_range(prices)
    if low and high:
        print(
            f"  Prezzo Discogs: {low['value']} {low['currency']} (peggiore) "
            f"– {high['value']} {high['currency']} (migliore)"
        )
    else:
        print("  Nessun prezzo suggerito disponibile per questa release.")


if __name__ == "__main__":
    print("--- Ricerca per artista + titolo ---")
    release = search_release("Pink Floyd", "The Wall")
    if not release:
        print("Nessuna release trovata.")
    else:
        print(f"Trovata: {release['title']} (id {release['id']})")
        _print_prices(release["id"])

    print("\n--- Ricerca per codice catalogo (può dare più candidati) ---")
    candidates = search_by_catalog_number("510 022-1")
    if not candidates:
        print("Nessuna release trovata per questo codice.")
    else:
        print(f"{len(candidates)} candidati trovati per questo codice:")
        for c in candidates:
            print(f"  - {c['title']} | {c['country']} {c['year']} | {c['label']} | {c['catno']} | id {c['id']}")
