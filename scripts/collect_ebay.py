"""Cerca annunci su eBay, li filtra a regole, salva nel DB i nuovi e notifica su Telegram."""

import html
import time
from datetime import datetime

from bot.notifier import send_message
from core.collectors.ebay import search_items
from core.db import get_connection, insert_listing
from core.filters import load_rules, passes_filters

SECONDS_BETWEEN_MESSAGES = 1  # evita il flood control di Telegram su tanti messaggi consecutivi

# Ricerche mirate: generi/artisti diretti (occasioni dove il titolo li nomina)
GENRE_QUERIES = [
    "AC/DC vinyl",
    "Metallica vinyl",
    "Nirvana vinyl",
    "Led Zeppelin vinyl",
    "Pink Floyd vinyl",
    "rock vinyl record",
    "metal vinyl record",
    "vinile pop italiano",
]

# Ricerche su lotti "anonimi": qui possono nascondersi le occasioni migliori,
# perché il venditore non sa cosa c'è dentro (nessun filtro per genere apposta)
LOT_QUERIES = [
    "vinyl record lot",
    "lotto vinili",
    "vinyl collection",
]

QUERIES = GENRE_QUERIES + LOT_QUERIES


def format_listed_at(listed_at: str | None) -> str:
    if not listed_at:
        return "data non disponibile"
    try:
        return datetime.fromisoformat(listed_at.replace("Z", "+00:00")).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return listed_at


def build_message(item: dict) -> str:
    title = html.escape(item["title"] or "")
    url = html.escape(item["url"] or "", quote=True)
    # TODO: quando avremo l'arricchimento Discogs, calcolare qui il prezzo
    # realistico del disco (e più avanti la differenza/margine).
    return (
        f"🎵 {title}\n"
        f"Prezzo annuncio: {item['price']} {item['currency']}\n"
        f"Prezzo realistico: non disponibile\n"
        f"Pubblicato: {format_listed_at(item.get('listed_at'))}\n"
        f'<a href="{url}">Link Ebay</a>'
    )


def notify_new_listings(new_listings: list[dict]) -> None:
    for item in new_listings:
        send_message(build_message(item), parse_mode="HTML")
        time.sleep(SECONDS_BETWEEN_MESSAGES)


def collect(query: str, category: str = "vinyl", limit: int = 50) -> None:
    conn = get_connection()
    rules = load_rules(category)
    items = search_items(query, limit=limit)

    new_listings = []
    duplicate_count = 0
    discarded_count = 0

    for item in items:
        ok, reason = passes_filters(item, rules)
        if not ok:
            discarded_count += 1
            print(f"[SCARTATO] {item['title']} — {reason}")
            continue

        is_new = insert_listing(
            conn,
            source=item["source"],
            external_id=item["external_id"],
            category=category,
            title=item["title"],
            price=item["price"],
            currency=item["currency"],
            url=item["url"],
            image_url=item["image_url"],
            listed_at=item.get("listed_at"),
        )
        if is_new:
            new_listings.append(item)
            print(f"[NUOVO] {item['title']} — {item['price']} {item['currency']}")
        else:
            duplicate_count += 1

    print(
        f"\nTotale: {len(items)} annunci trovati, {discarded_count} scartati dai filtri, "
        f"{len(new_listings)} nuovi, {duplicate_count} già visti."
    )

    notify_new_listings(new_listings)


if __name__ == "__main__":
    for query in QUERIES:
        print(f"\n=== Ricerca: {query} ===")
        collect(query)
