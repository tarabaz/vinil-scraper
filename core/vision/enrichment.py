"""Arricchimento di un annuncio: dati riconosciuti (testo o vision) +
corrispondenze Discogs + formattazione del messaggio Telegram. Condiviso tra
scripts.vision_test (test manuale) e scripts.collect (pipeline reale), così
la logica vive in un posto solo.

Ordine di costo crescente, si ferma al primo che basta:
  1. Parsing del titolo (gratis, istantaneo) — se il titolo ha già un
     pattern chiaro "Artista - Album", non serve altro.
  2. Cache: l'annuncio è già stato processato dalla vision in precedenza
     (righe già in vision_results) — riusa quei dati, non richiama il
     modello.
  3. Vision sulle foto (costoso: rete + GPU) — solo se le prime due non
     bastano."""

import html
import re

import requests

from core.collectors.discogs import get_price_suggestions, search_by_catalog_number, search_release
from core.collectors.ebay import get_item_images
from core.db import insert_vision_result
from core.vision.ollama_vision import FIELDS, recognize_image

MERGE_FIELDS = [f for f in FIELDS if f != "other_text"]

FIELD_LABELS = {
    "artist": "Artista",
    "album_title": "Album",
    "label": "Etichetta",
    "catalog_number": "Catalogo",
    "barcode": "Barcode",
    "other_text": "Altro",
}

MAX_DISCOGS_CANDIDATES = 3  # un codice catalogo può corrispondere a più edizioni: le mostriamo tutte, non ne mediamo i prezzi

# Fasce di prezzo Discogs mostrate come riferimento qualitativo. "Good (G)" è
# quella usata per valutare l'affare (% rispetto al prezzo annuncio): non
# troppo ottimista come Very Good, non troppo pessimista come Poor.
REFERENCE_CONDITIONS = ["Poor (P)", "Good (G)", "Very Good (VG)"]
REFERENCE_LABELS = {"Poor (P)": "Poor", "Good (G)": "Good", "Very Good (VG)": "Very Good"}
DEAL_CONDITION = "Good (G)"

TITLE_SEPARATOR_PATTERN = re.compile(r"\s+-\s+")


def parse_title_hints(title: str) -> dict:
    """Estrazione gratuita/deterministica dal titolo, prima di ricorrere
    alla vision. Riconosce solo il pattern netto "Artista - Album" (un
    trattino separatore con spazi); se non c'è, ritorna vuoto — meglio
    nessun dato che uno indovinato male."""
    if not title:
        return {}
    parts = TITLE_SEPARATOR_PATTERN.split(title, maxsplit=1)
    if len(parts) == 2 and len(parts[0].strip()) > 1 and len(parts[1].strip()) > 1:
        return {"artist": parts[0].strip(), "album_title": parts[1].strip()}
    return {}


def get_cached_vision_results(conn, source: str, external_id: str) -> list[dict]:
    """Righe già salvate in vision_results per questo annuncio, da run
    precedenti — se presenti evita di richiamare la vision da capo."""
    rows = conn.execute(
        "SELECT artist, album_title, label, catalog_number, barcode, other_text "
        "FROM vision_results WHERE source = ? AND external_id = ?",
        (source, external_id),
    ).fetchall()
    columns = ["artist", "album_title", "label", "catalog_number", "barcode", "other_text"]
    return [dict(zip(columns, row)) for row in rows]


def upscale_ebay_image_url(url: str) -> str:
    """Le immagini salvate nel DB sono le miniature dei risultati di ricerca
    (es. s-l225.jpg, 225px) — troppo piccole per leggere testo. eBay espone
    la stessa immagine anche in alta risoluzione cambiando solo quel numero
    nell'URL. Se l'URL non è nel formato atteso lo lascia invariato."""
    return re.sub(r"s-l\d+", "s-l1600", url)


def get_all_images(source: str, external_id: str, fallback_image_url: str | None, max_images: int) -> list[str]:
    """Tutte le foto dell'annuncio (fronte, retro, etichetta...), non solo
    l'anteprima salvata nel DB. Per ora solo eBay espone un modo per
    recuperarle tutte (getItem); per le altre fonti si usa solo l'unica
    immagine già disponibile."""
    if source == "ebay":
        try:
            images = get_item_images(external_id)
            if images:
                return images[:max_images]
        except Exception as exc:
            print(f"[ERRORE] recupero foto complete fallito, uso solo l'anteprima: {exc}")

    if fallback_image_url:
        return [upscale_ebay_image_url(fallback_image_url)]
    return []


def fetch_image_bytes(url: str) -> bytes:
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    return response.content


def merge_photo_results(photo_results: list[dict]) -> dict:
    """Unione ingenua tra le foto/righe di UNO STESSO annuncio: primo valore
    non nullo trovato per campo. Va bene per un singolo disco fotografato da
    più lati; non separa dischi diversi nello stesso lotto (per quello serve
    core/vision/matching.py, non ancora collegato qui)."""
    merged = {field: None for field in MERGE_FIELDS}
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


def find_discogs_candidates(merged: dict, max_candidates: int = MAX_DISCOGS_CANDIDATES) -> list[dict]:
    """Preferisce il codice catalogo (più preciso) se letto, altrimenti
    artista+titolo. Non media mai i prezzi tra edizioni diverse: ritorna
    tutti i candidati trovati (fino a max_candidates). Prova prima il codice
    catalogo ripulito, poi quello grezzo come ripiego."""
    if merged.get("catalog_number"):
        raw = merged["catalog_number"]
        for candidate_value in dict.fromkeys([clean_catalog_number(raw), raw]):
            try:
                candidates = search_by_catalog_number(candidate_value)
            except Exception as exc:
                print(f"[ERRORE] ricerca Discogs per codice catalogo '{candidate_value}' fallita: {exc}")
                continue
            if candidates:
                return candidates[:max_candidates]

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


def get_reference_prices(release_id: int) -> dict:
    """Prezzi Discogs arrotondati all'euro intero per le tre condizioni di
    riferimento (Poor/Good/Very Good). Dict vuoto se non disponibili (release
    senza dati di prezzo)."""
    try:
        prices = get_price_suggestions(release_id)
    except requests.exceptions.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return {}
        raise
    return {condition: round(prices[condition]["value"]) for condition in REFERENCE_CONDITIONS if condition in prices}


def format_discogs_candidate(candidate: dict, index: int) -> tuple[str, dict]:
    """Ritorna (blocco di testo formattato del candidato, prezzi di riferimento arrotondati)."""
    details = " | ".join(
        html.escape(str(v))
        for v in (candidate.get("country"), candidate.get("year"), candidate.get("label"), candidate.get("catno"))
        if v
    )
    title_line = f"{index}. {html.escape(candidate.get('title') or 'Release Discogs')}"

    block = [title_line]
    if details:
        block.append(f"   {details}")

    raw_url = f"https://www.discogs.com/release/{candidate['id']}"
    link_line = f'   <a href="{html.escape(raw_url, quote=True)}">Link Discogs</a>'

    try:
        reference_prices = get_reference_prices(candidate["id"])
    except Exception as exc:
        block.append(f"   Prezzo non disponibile (errore Discogs: {exc})")
        block.append(link_line)
        return "\n".join(block), {}

    if reference_prices:
        price_line = " | ".join(f"{REFERENCE_LABELS[c]}: €{reference_prices[c]}" for c in REFERENCE_CONDITIONS if c in reference_prices)
        block.append(f"   {price_line}")
    else:
        block.append("   Nessun prezzo suggerito su Discogs")
    block.append(link_line)
    return "\n".join(block), reference_prices


def compute_discount_pct(listing_price, listing_currency: str | None, reference_prices: dict) -> int | None:
    """% rispetto al prezzo Discogs in condizione Good (positiva = annuncio
    più economico, un affare). None se non calcolabile (manca il prezzo
    Good, manca il prezzo annuncio, o valute diverse — calcola solo se
    entrambi in EUR)."""
    good_price = reference_prices.get(DEAL_CONDITION)
    if not good_price or listing_price is None or (listing_currency or "").upper() != "EUR":
        return None
    return round((good_price - listing_price) / good_price * 100)


def build_discount_line(discount_pct: int | None) -> str:
    if discount_pct is None:
        return ""
    if discount_pct > 0:
        return f"🔻 -{discount_pct}% rispetto a Discogs (Good)"
    if discount_pct < 0:
        return f"🔺 +{abs(discount_pct)}% rispetto a Discogs (Good)"
    return "➖ Prezzo in linea con Discogs (Good)"


def enrich_listing(conn, source: str, external_id: str, title: str, image_url: str | None, max_images: int = 5) -> dict:
    """Arricchisce un annuncio con dati riconosciuti e corrispondenze
    Discogs, al minor costo possibile (titolo -> cache -> vision). Ritorna
    {"merged": {...}, "candidates": [...], "source_of_data": "title"|"cache"|"vision"|"none"}."""
    hints = parse_title_hints(title)
    if hints.get("artist") and hints.get("album_title"):
        merged = {field: None for field in MERGE_FIELDS}
        merged.update(hints)
        return {"merged": merged, "candidates": find_discogs_candidates(merged), "source_of_data": "title"}

    cached = get_cached_vision_results(conn, source, external_id)
    if cached:
        merged = merge_photo_results(cached)
        return {"merged": merged, "candidates": find_discogs_candidates(merged), "source_of_data": "cache"}

    images = get_all_images(source, external_id, image_url, max_images)
    photo_results = []
    for img_url in images:
        try:
            image_bytes = fetch_image_bytes(img_url)
            result = recognize_image(image_bytes)
        except Exception as exc:
            print(f"[ERRORE] riconoscimento immagine fallito ({img_url}): {exc}")
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
        photo_results.append(result)

    if not photo_results:
        return {"merged": {field: None for field in MERGE_FIELDS}, "candidates": [], "source_of_data": "none"}

    merged = merge_photo_results(photo_results)
    return {"merged": merged, "candidates": find_discogs_candidates(merged), "source_of_data": "vision"}


def build_enrichment_message(
    source: str, title: str, price, currency, url: str | None, merged: dict, candidates: list[dict]
) -> tuple[str, int | None]:
    """Messaggio Telegram a sezioni (titolo, dati riconosciuti, Discogs,
    link), ognuna separata da una riga vuota. Ritorna anche (oltre al testo)
    la % di sconto vs Discogs Good come numero, per poterla usare altrove
    (es. contarla in un report) senza doverla riparsare dal testo."""
    candidate_blocks = []
    best_reference_prices: dict = {}
    for i, candidate in enumerate(candidates, start=1):
        block, reference_prices = format_discogs_candidate(candidate, i)
        candidate_blocks.append(block)
        if i == 1:
            best_reference_prices = reference_prices

    discount_pct = compute_discount_pct(price, currency, best_reference_prices)
    discount_line = build_discount_line(discount_pct)

    sections = []

    if discount_line:
        sections.append(discount_line)

    sections.append(f"🎵 <b>{html.escape(title or '')}</b>\n💰 Prezzo annuncio: {price} {currency}")

    if merged and any(merged.values()):
        recognized_lines = [f"{FIELD_LABELS[f]}: {html.escape(merged[f])}" for f in merged if merged.get(f)]
        sections.append("📋 <b>Dati riconosciuti</b>\n" + "\n".join(recognized_lines))
    else:
        sections.append("📋 Nessun dato riconosciuto")

    if candidate_blocks:
        sections.append("💿 <b>Discogs</b>\n" + "\n\n".join(candidate_blocks))
    else:
        sections.append("💿 Nessuna corrispondenza trovata su Discogs.")

    if url:
        escaped_url = html.escape(url, quote=True)
        sections.append(f'🔗 <a href="{escaped_url}">Link {source.capitalize()}</a>')

    return "\n\n".join(sections), discount_pct
