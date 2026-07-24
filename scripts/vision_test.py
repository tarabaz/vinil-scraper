"""Test manuale del riconoscimento vision locale via Ollama + ricerca Discogs.

SOLO PER PROVE: processa al massimo VISION_TEST_LIMIT annunci (limite
volutamente bassissimo) e, per ognuno, al massimo MAX_IMAGES_PER_LISTING
foto (fronte, retro, etichetta... non solo l'anteprima salvata nel DB — un
disco può avere il codice catalogo solo sul retro o sull'etichetta). Ogni
foto processata viene salvata in vision_results (core.db) e stampata come
riga sintetica. Poi unisce i dati letti dalle foto di uno stesso annuncio
(unione ingenua: primo valore non nullo trovato per campo — non è ancora la
fusione multi-disco di core/vision/matching.py, adatta a un lotto con più
dischi diversi nelle stesse foto) e cerca su Discogs (prima per codice
catalogo se letto, altrimenti per artista+titolo). Manda un messaggio
Telegram di riepilogo all'amministratore per annuncio.

Non fa parte della pipeline automatica di scripts.collect — è uno script a
sé, da lanciare a mano quando si vuole verificare la qualità end-to-end
(riconoscimento + prezzo Discogs + notifica)."""

import re

import requests

from bot.notifier import send_message
from core.collectors.discogs import get_price_suggestions, price_range, search_by_catalog_number, search_release
from core.collectors.ebay import get_item_images
from core.db import get_connection, insert_vision_result
from core.vision.ollama_vision import FIELDS, recognize_image

VISION_TEST_LIMIT = 2  # bassissimo apposta: è solo un test manuale
VISION_TEST_OFFSET = 1  # salta il 1° annuncio (Nirvana, già testato) e prende il 2° e 3°
MAX_IMAGES_PER_LISTING = 5  # un lotto può avere decine di foto, non le processiamo tutte in prova
MAX_DISCOGS_CANDIDATES = 3  # un codice catalogo può corrispondere a più edizioni: le mostriamo tutte, non ne mediamo i prezzi

FIELD_LABELS = {
    "artist": "Artista",
    "album_title": "Album",
    "label": "Etichetta",
    "catalog_number": "Catalogo",
    "barcode": "Barcode",
    "other_text": "Altro",
}


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
    recuperarle tutte (getItem); per le altre fonti si usa solo l'unica
    immagine già disponibile."""
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


def format_summary(result: dict) -> str:
    """Una riga sola con solo i campi che il modello ha davvero trovato."""
    found = [f"{FIELD_LABELS[f]}: {result[f]}" for f in FIELDS if f != "other_text" and result.get(f)]
    if result.get("other_text"):
        found.append(f"Altro: {result['other_text']}")
    return " | ".join(found) if found else "(nessun dato leggibile in questa foto)"


def merge_photo_results(photo_results: list[dict]) -> dict:
    """Unione ingenua tra le foto di UNO STESSO annuncio: primo valore non
    nullo trovato per campo. Va bene per un singolo disco fotografato da più
    lati; non separa dischi diversi nello stesso lotto (per quello serve
    core/vision/matching.py, non ancora collegato qui)."""
    merged = {field: None for field in FIELDS if field != "other_text"}
    for result in photo_results:
        for field in merged:
            if merged[field] is None and result.get(field):
                merged[field] = result[field]
    return merged


def clean_catalog_number(catalog_number: str) -> str:
    """Il modello a volte unisce codice catalogo e barcode in un'unica
    stringa (es. "LMLP165: 502454968712") nonostante il prompt chieda di
    tenerli separati — prendo solo la parte prima del separatore, che nella
    pratica è il codice catalogo vero."""
    return re.split(r"[:;/]", catalog_number, maxsplit=1)[0].strip()


def find_discogs_candidates(merged: dict) -> list[dict]:
    """Preferisce il codice catalogo (più preciso) se letto, altrimenti
    artista+titolo. Non media mai i prezzi tra edizioni diverse: ritorna
    tutti i candidati trovati (fino a MAX_DISCOGS_CANDIDATES). Prova prima
    il codice catalogo ripulito, poi quello grezzo come ripiego (nel caso la
    pulizia abbia tagliato qualcosa di utile)."""
    if merged.get("catalog_number"):
        raw = merged["catalog_number"]
        for candidate_value in dict.fromkeys([clean_catalog_number(raw), raw]):
            try:
                candidates = search_by_catalog_number(candidate_value)
            except Exception as exc:
                print(f"[ERRORE] ricerca Discogs per codice catalogo '{candidate_value}' fallita: {exc}")
                continue
            if candidates:
                return candidates[:MAX_DISCOGS_CANDIDATES]

    if merged.get("artist") and merged.get("album_title"):
        try:
            release = search_release(merged["artist"], merged["album_title"])
        except Exception as exc:
            print(f"[ERRORE] ricerca Discogs per artista/titolo fallita: {exc}")
            release = None
        if release:
            return [
                {
                    "id": release["id"],
                    "title": release.get("title"),
                    "country": None,
                    "year": None,
                    "label": None,
                    "catno": None,
                }
            ]

    return []


def format_discogs_candidate(candidate: dict) -> str:
    details = " | ".join(
        str(v) for v in (candidate.get("country"), candidate.get("year"), candidate.get("label"), candidate.get("catno")) if v
    )
    line = f"{candidate.get('title') or 'Release Discogs'}"
    if details:
        line += f" ({details})"

    discogs_url = f"\n  https://www.discogs.com/release/{candidate['id']}"

    try:
        prices = get_price_suggestions(candidate["id"])
        low, high = price_range(prices)
    except requests.exceptions.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return f"{line} — nessun prezzo disponibile su Discogs per questa edizione{discogs_url}"
        return f"{line} — prezzo non disponibile (errore Discogs: {exc}){discogs_url}"
    except Exception as exc:
        return f"{line} — prezzo non disponibile ({exc}){discogs_url}"

    if low and high:
        if low == high:
            line += f" — prezzo Discogs: {low['value']} {low['currency']}"
        else:
            line += f" — prezzo Discogs: {low['value']}–{high['value']} {high['currency']}"
    else:
        line += " — nessun prezzo suggerito su Discogs"
    return line + discogs_url


def build_summary_message(title: str, price, currency, url: str | None, merged: dict, candidates: list[dict]) -> str:
    lines = [f"🎵 {title}", f"Prezzo annuncio: {price} {currency}"]

    recognized = [f"{FIELD_LABELS[f]}: {merged[f]}" for f in merged if merged.get(f)]
    lines.append("Riconosciuto: " + (", ".join(recognized) if recognized else "nessun dato leggibile"))

    if candidates:
        lines.append("\nDiscogs:")
        lines += [f"- {format_discogs_candidate(c)}" for c in candidates]
    else:
        lines.append("\nNessuna corrispondenza trovata su Discogs.")

    if url:
        lines.append(f"\n{url}")
    return "\n".join(lines)


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
        images = get_all_images(source, external_id, image_url)
        print(f"=== {title} ===")
        print(f"{len(images)} foto da processare.")

        photo_results = []
        for i, img_url in enumerate(images, start=1):
            try:
                image_bytes = fetch_image_bytes(img_url)
                result = recognize_image(image_bytes)
            except Exception as exc:
                print(f"  Foto {i}: [ERRORE] {exc}")
                continue

            insert_vision_result(
                conn,
                source=source,
                external_id=external_id,
                image_url=img_url,
                artist=result["artist"],
                album_title=result["album_title"],
                label=result["label"],
                catalog_number=result["catalog_number"],
                barcode=result["barcode"],
                other_text=result["other_text"],
                raw_response=result["raw_response"],
            )
            print(f"  Foto {i}: {format_summary(result)}")
            photo_results.append(result)

        merged = merge_photo_results(photo_results)
        candidates = find_discogs_candidates(merged)
        message = build_summary_message(title, price, currency, url, merged, candidates)

        print("\n--- Riepilogo Discogs ---")
        print(message)

        try:
            send_message(message)
        except Exception as exc:
            print(f"[ERRORE] invio Telegram fallito: {exc}")
        print()

    conn.close()


if __name__ == "__main__":
    main()
