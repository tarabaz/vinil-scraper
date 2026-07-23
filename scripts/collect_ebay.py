"""Cerca annunci su eBay, li filtra a regole, salva nel DB i nuovi e notifica su Telegram."""

import time
from datetime import datetime

from bot.notifier import send_message
from core.collectors.ebay import search_items
from core.db import get_connection, insert_listing
from core.filters import load_rules, passes_filters

SECONDS_BETWEEN_MESSAGES = 1  # evita il flood control di Telegram su tanti messaggi consecutivi


def format_listed_at(listed_at: str | None) -> str:
    if not listed_at:
        return "data non disponibile"
    try:
        return datetime.fromisoformat(listed_at.replace("Z", "+00:00")).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return listed_at


def notify_new_listings(new_listings: list[dict]) -> None:
    # TODO: quando avremo l'arricchimento Discogs, aggiungere qui prezzo reale
    # del disco e differenza rispetto al prezzo dell'annuncio.
    for item in new_listings:
        text = (
            f"🎵 {item['title']}\n"
            f"Prezzo annuncio: {item['price']} {item['currency']}\n"
            f"Pubblicato: {format_listed_at(item.get('listed_at'))}\n"
            f"{item['url']}"
        )
        send_message(text)
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
    collect("vinyl record")
