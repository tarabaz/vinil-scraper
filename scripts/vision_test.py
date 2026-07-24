"""Test manuale del riconoscimento vision locale via Ollama.

SOLO PER PROVE: processa al massimo VISION_TEST_LIMIT annunci (limite
volutamente bassissimo, per non scaricare/processare decine di immagini a
ogni prova), usa l'unica immagine già salvata per annuncio nel DB, stampa
cosa riconosce il modello. Non salva nulla, non invia notifiche Telegram,
non fa parte della pipeline automatica di scripts.collect — è uno script a
sé, da lanciare a mano quando si vuole verificare la qualità del
riconoscimento."""

import re

import requests

from core.db import get_connection
from core.vision.ollama_vision import recognize_image

VISION_TEST_LIMIT = 2  # bassissimo apposta: è solo un test manuale


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


def main() -> None:
    conn = get_connection()
    rows = conn.execute(
        "SELECT title, image_url FROM listings WHERE image_url IS NOT NULL LIMIT ?",
        (VISION_TEST_LIMIT,),
    ).fetchall()
    conn.close()

    if not rows:
        print("Nessun annuncio con immagine nel DB — lancia prima python -m scripts.collect.")
        return

    print(f"Test su {len(rows)} annunci (limite: {VISION_TEST_LIMIT}).\n")

    for title, image_url in rows:
        image_url = upscale_ebay_image_url(image_url)
        print(f"=== {title} ===")
        print(f"Immagine: {image_url}")
        try:
            image_bytes = fetch_image_bytes(image_url)
            result = recognize_image(image_bytes)
        except Exception as exc:
            print(f"[ERRORE] {exc}\n")
            continue
        print(result)
        print()


if __name__ == "__main__":
    main()
