"""Test manuale del riconoscimento vision locale via Ollama.

SOLO PER PROVE: processa al massimo VISION_TEST_LIMIT annunci (limite
volutamente bassissimo) e, per ognuno, al massimo MAX_IMAGES_PER_LISTING
foto (fronte, retro, etichetta... non solo l'anteprima salvata nel DB — un
disco può avere il codice catalogo solo sul retro o sull'etichetta). Stampa
cosa riconosce il modello per ogni foto. Non salva nulla, non invia
notifiche Telegram, non fa parte della pipeline automatica di
scripts.collect — è uno script a sé, da lanciare a mano quando si vuole
verificare la qualità del riconoscimento."""

import re

import requests

from core.collectors.ebay import get_item_images
from core.db import get_connection
from core.vision.ollama_vision import recognize_image

VISION_TEST_LIMIT = 2  # bassissimo apposta: è solo un test manuale
MAX_IMAGES_PER_LISTING = 5  # un lotto può avere decine di foto, non le processiamo tutte in prova


def upscale_ebay_image_url(url: str) -> str:
    """Le immagini salvate nel DB sono le miniature dei risultati di ricerca
    (es. s-l225.jpg, 225px) — troppo piccole per leggere testo. eBay espone
    la stessa immagine anche in alta risoluzione cambiando solo quel numero
    nell'URL. Se l'URL non è nel formato atteso lo lascia invariato."""
    return re.sub(r"s-l\d+", "s-l1600", url)


def fetch_image_bytes(url: str) -> bytes:
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    return response.content


def get_all_images(source: str, external_id: str, fallback_image_url: str | None) -> list[str]:
    """Tutte le foto dell'annuncio (fronte, retro, etichetta...), non solo
    l'anteprima salvata nel DB. Per ora solo eBay espone un modo per
    recuperarle tutte (getItem, non ancora verificato contro l'API reale);
    per le altre fonti si usa solo l'unica immagine già disponibile."""
    if source == "ebay":
        try:
            images = get_item_images(external_id)
            if images:
                return images[:MAX_IMAGES_PER_LISTING]
        except Exception as exc:
            print(f"[ERRORE] recupero foto complete fallito, uso solo l'anteprima: {exc}")

    if fallback_image_url:
        return [upscale_ebay_image_url(fallback_image_url)]
    return []


def main() -> None:
    conn = get_connection()
    rows = conn.execute(
        "SELECT source, external_id, title, image_url FROM listings WHERE image_url IS NOT NULL LIMIT ?",
        (VISION_TEST_LIMIT,),
    ).fetchall()
    conn.close()

    if not rows:
        print("Nessun annuncio con immagine nel DB — lancia prima python -m scripts.collect.")
        return

    print(f"Test su {len(rows)} annunci (limite: {VISION_TEST_LIMIT}, max {MAX_IMAGES_PER_LISTING} foto per annuncio).\n")

    for source, external_id, title, image_url in rows:
        images = get_all_images(source, external_id, image_url)
        print(f"=== {title} ===")
        print(f"{len(images)} foto da processare.")

        for i, img_url in enumerate(images, start=1):
            print(f"\n--- Foto {i}/{len(images)}: {img_url} ---")
            try:
                image_bytes = fetch_image_bytes(img_url)
                result = recognize_image(image_bytes)
            except Exception as exc:
                print(f"[ERRORE] {exc}")
                continue
            print(result)
        print()


if __name__ == "__main__":
    main()
