"""Test manuale della pipeline (riconoscimento + Discogs + messaggio) su UN
annuncio eBay specifico, dato il suo link — utile per verificare un lotto
particolare (es. per vedere come gestisce dischi multipli) senza aspettare
che salti fuori da una ricerca.

Uso:
    python -m scripts.test_listing "https://www.ebay.it/itm/..."

Salva l'annuncio nel DB come farebbe una ricerca normale (così poi compare
anche in /report), poi arricchisce e manda il messaggio su Telegram."""

import sys

from bot.notifier import send_message
from core.collectors.ebay import extract_legacy_item_id, get_item_by_legacy_id
from core.db import get_connection, insert_listing
from core.vision.enrichment import build_enrichment_message, enrich_listing

MAX_IMAGES_PER_LISTING = 5


def main() -> None:
    if len(sys.argv) < 2:
        print('Uso: python -m scripts.test_listing "https://www.ebay.it/itm/..."')
        return

    url = sys.argv[1]
    legacy_id = extract_legacy_item_id(url)
    if not legacy_id:
        print(f"Non riesco a trovare l'ID annuncio in questo URL: {url}")
        return

    print(f"ID annuncio: {legacy_id}")
    item = get_item_by_legacy_id(legacy_id)
    print(f"Titolo: {item['title']}")
    print(f"Prezzo: {item['price']} {item['currency']}")
    print(f"Foto trovate: {len(item['images'])}")

    first_image = item["images"][0] if item["images"] else None

    conn = get_connection()
    insert_listing(
        conn,
        source=item["source"],
        external_id=item["external_id"],
        category="vinyl",
        title=item["title"],
        price=item["price"],
        currency=item["currency"],
        url=item["url"],
        image_url=first_image,
        listed_at=item["listed_at"],
    )

    print("\nArricchimento in corso...")
    enrichment = enrich_listing(
        conn, item["source"], item["external_id"], item["title"], first_image, max_images=MAX_IMAGES_PER_LISTING
    )
    print(f"Fonte dati: {enrichment['source_of_data']}")
    print(f"Riconosciuto: {enrichment['merged']}")
    print(f"Candidati Discogs: {len(enrichment['candidates'])}")

    message, discount_pct = build_enrichment_message(
        item["source"], item["title"], item["price"], item["currency"], item["url"], enrichment["merged"], enrichment["candidates"]
    )

    print(f"\n--- Messaggio (% sconto: {discount_pct}) ---")
    print(message)

    try:
        send_message(message, parse_mode="HTML")
    except Exception as exc:
        print(f"[ERRORE] invio Telegram fallito: {exc}")

    conn.close()


if __name__ == "__main__":
    main()
