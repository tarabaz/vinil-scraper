"""Cerca annunci su eBay, li filtra a regole, salva nel DB i nuovi e notifica su Telegram."""

from bot.notifier import send_message
from core.collectors.ebay import search_items
from core.db import get_connection, insert_listing
from core.filters import load_rules, passes_filters

TELEGRAM_MESSAGE_BUDGET = 3500  # margine sotto il limite di 4096 caratteri di Telegram


def notify_new_listings(new_listings: list[dict]) -> None:
    if not new_listings:
        return

    lines = [f"🎵 {len(new_listings)} nuovi annunci vinili trovati:\n"]
    shown = 0
    for item in new_listings:
        line = f"\n{item['title']} — {item['price']} {item['currency']}\n{item['url']}"
        if sum(len(l) for l in lines) + len(line) > TELEGRAM_MESSAGE_BUDGET:
            break
        lines.append(line)
        shown += 1

    if shown < len(new_listings):
        lines.append(f"\n\n…e altri {len(new_listings) - shown} annunci non mostrati qui.")

    send_message("".join(lines))


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
