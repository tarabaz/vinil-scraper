"""Cerca annunci sui marketplace abilitati, li filtra a regole, salva nel DB
i nuovi e notifica su Telegram. Marketplace-agnostico: usa il registro dei
collector e l'impostazione marketplaces.enabled, non ha logica specifica di
nessuna fonte (quella vive nei singoli collector)."""

import html
import time
from datetime import datetime

from bot.notifier import send_message
from core.collectors.base import listing_to_dict
from core.collectors.ebay import find_category_id
from core.collectors.registry import REGISTRY
from core.db import RETENTION_HOURS, cleanup_old_listings, get_connection, insert_listing
from core.filters import load_rules, passes_filters
from core.settings import get_setting

SECONDS_BETWEEN_MESSAGES = 1  # evita il flood control di Telegram su tanti messaggi consecutivi

DEFAULT_ENABLED_MARKETPLACES = ["ebay", "subito"]

# Ricerche per marketplace: generi/artisti diretti (occasioni dove il titolo
# li nomina) + lotti "anonimi" (dove possono nascondersi le occasioni migliori,
# perché il venditore non sa cosa c'è dentro — nessun filtro per genere apposta).
QUERIES_BY_MARKETPLACE = {
    "ebay": [
        "AC/DC vinyl",
        "Metallica vinyl",
        "Nirvana vinyl",
        "Led Zeppelin vinyl",
        "Pink Floyd vinyl",
        "rock vinyl record",
        "metal vinyl record",
        "vinile pop italiano",
        "vinyl record lot",
        "lotto vinili",
        "vinyl collection",
    ],
    "subito": [
        "AC/DC vinile",
        "Metallica vinile",
        "Nirvana vinile",
        "Led Zeppelin vinile",
        "Pink Floyd vinile",
        "vinile rock",
        "vinile metal",
        "vinile pop italiano",
        "lotto vinili",
        "lotto dischi vinile",
        "collezione vinili",
    ],
}


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
        f'<a href="{url}">Link {item["source"].capitalize()}</a>'
    )


def notify_new_listings(new_listings: list[dict]) -> None:
    for item in new_listings:
        send_message(build_message(item), parse_mode="HTML")
        time.sleep(SECONDS_BETWEEN_MESSAGES)


def collect(collector, query: str, category: str = "vinyl", **search_settings) -> None:
    conn = get_connection()
    rules = load_rules(category)
    listings = collector.search(query, **search_settings)

    new_listings = []
    duplicate_count = 0
    discarded_count = 0

    for listing in listings:
        item = listing_to_dict(listing)

        ok, reason = passes_filters(item, rules)
        if not ok:
            discarded_count += 1
            print(f"[SCARTATO] {item['title']} — {reason}")
            continue

        is_new = insert_listing(conn, category=category, **item)
        if is_new:
            new_listings.append(item)
            print(f"[NUOVO] {item['title']} — {item['price']} {item['currency']}")
        else:
            duplicate_count += 1

    print(
        f"\nTotale: {len(listings)} annunci trovati, {discarded_count} scartati dai filtri, "
        f"{len(new_listings)} nuovi, {duplicate_count} già visti."
    )

    notify_new_listings(new_listings)


if __name__ == "__main__":
    cleanup_conn = get_connection()
    removed = cleanup_old_listings(cleanup_conn)
    cleanup_conn.close()
    print(f"Pulizia DB: rimossi {removed} annunci non visti da più di {RETENTION_HOURS} ore.\n")

    enabled_marketplaces = get_setting("marketplaces.enabled", DEFAULT_ENABLED_MARKETPLACES)
    print(f"Marketplace abilitati: {enabled_marketplaces}\n")

    # eBay ha bisogno di un lookup una tantum della categoria; è l'unico
    # marketplace che oggi richiede questo passaggio in più.
    ebay_category_id = None
    if "ebay" in enabled_marketplaces:
        ebay_category_id = find_category_id("Vinyl Records")
        print(f"Categoria eBay usata per le ricerche: {ebay_category_id}\n")

    for marketplace in enabled_marketplaces:
        if marketplace not in REGISTRY:
            print(f"Marketplace sconosciuto in REGISTRY, salto: {marketplace}")
            continue

        collector = REGISTRY[marketplace]()
        queries = QUERIES_BY_MARKETPLACE.get(marketplace, [])

        for query in queries:
            print(f"\n=== [{marketplace}] Ricerca: {query} ===")
            search_settings = {"category_ids": ebay_category_id} if marketplace == "ebay" else {}
            collect(collector, query, **search_settings)
