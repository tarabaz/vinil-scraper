"""Cerca annunci su eBay, li filtra a regole e salva nel DB i soli nuovi che passano."""

from core.collectors.ebay import search_items
from core.db import get_connection, insert_listing
from core.filters import load_rules, passes_filters


def collect(query: str, category: str = "vinyl", limit: int = 50) -> None:
    conn = get_connection()
    rules = load_rules(category)
    items = search_items(query, limit=limit)

    new_count = 0
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
            new_count += 1
            print(f"[NUOVO] {item['title']} — {item['price']} {item['currency']}")
        else:
            duplicate_count += 1

    print(
        f"\nTotale: {len(items)} annunci trovati, {discarded_count} scartati dai filtri, "
        f"{new_count} nuovi, {duplicate_count} già visti."
    )


if __name__ == "__main__":
    collect("vinyl record")
