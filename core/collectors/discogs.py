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


if __name__ == "__main__":
    release = search_release("Pink Floyd", "The Wall")
    if not release:
        print("Nessuna release trovata.")
    else:
        print(f"Trovata: {release['title']} (id {release['id']})")
        prices = get_price_suggestions(release["id"])
        low, high = price_range(prices)
        if low and high:
            print(
                f"Prezzo Discogs: {low['value']} {low['currency']} (peggiore) "
                f"– {high['value']} {high['currency']} (migliore)"
            )
        else:
            print("Nessun prezzo suggerito disponibile per questa release.")
