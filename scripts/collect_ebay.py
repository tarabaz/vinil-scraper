"""Cerca annunci su eBay e li salva nel DB, scartando i duplicati già visti."""

from core.collectors.ebay import search_items
from core.db import get_connection, insert_listing


def collect(query: str, category: str = "vinyl", limit: int = 10) -> None:
    conn = get_connection()
    items = search_items(query, limit=limit)

    new_count = 0
    duplicate_count = 0

    for item in items:
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

    print(f"\nTotale: {len(items)} annunci trovati, {new_count} nuovi, {duplicate_count} già visti.")


if __name__ == "__main__":
    collect("vinyl record")
