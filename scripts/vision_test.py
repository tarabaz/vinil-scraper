"""Test manuale del riconoscimento vision locale + ricerca Discogs.

SOLO PER PROVE: processa al massimo VISION_TEST_LIMIT annunci (limite
volutamente bassissimo), a partire da VISION_TEST_OFFSET (per non ripescare
sempre gli stessi). La logica vera (parsing titolo -> cache -> vision,
ricerca Discogs, formattazione messaggio) vive in core.vision.enrichment,
condivisa con la pipeline reale (scripts.collect) — questo script è solo un
giro manuale a bassissimo volume per verificare la qualità prima di fidarsi
della pipeline automatica."""

from bot.notifier import send_message
from core.db import get_connection
from core.vision.enrichment import build_enrichment_message, enrich_listing

VISION_TEST_LIMIT = 2  # bassissimo apposta: è solo un test manuale
VISION_TEST_OFFSET = 1  # cambialo per testare altri annunci (0 = i primi)
MAX_IMAGES_PER_LISTING = 5  # un lotto può avere decine di foto, non le processiamo tutte in prova


def main() -> None:
    conn = get_connection()
    rows = conn.execute(
        "SELECT source, external_id, title, price, currency, url, image_url FROM listings "
        "WHERE image_url IS NOT NULL ORDER BY id LIMIT ? OFFSET ?",
        (VISION_TEST_LIMIT, VISION_TEST_OFFSET),
    ).fetchall()

    if not rows:
        conn.close()
        print("Nessun annuncio con immagine nel DB — lancia prima python -m scripts.collect.")
        return

    print(f"Test su {len(rows)} annunci (limite: {VISION_TEST_LIMIT}, max {MAX_IMAGES_PER_LISTING} foto per annuncio).\n")

    for source, external_id, title, price, currency, url, image_url in rows:
        print(f"=== {title} ===")

        enrichment = enrich_listing(conn, source, external_id, title, image_url, max_images=MAX_IMAGES_PER_LISTING)
        print(f"Fonte dati: {enrichment['source_of_data']}")
        print(f"Riconosciuto: {enrichment['merged']}")
        print(f"Candidati Discogs: {len(enrichment['candidates'])}")

        message = build_enrichment_message(source, title, price, currency, url, enrichment["merged"], enrichment["candidates"])

        print("\n--- Messaggio ---")
        print(message)

        try:
            send_message(message, parse_mode="HTML")
        except Exception as exc:
            print(f"[ERRORE] invio Telegram fallito: {exc}")
        print()

    conn.close()


if __name__ == "__main__":
    main()
