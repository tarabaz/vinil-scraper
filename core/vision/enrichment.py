"""Arricchimento di un annuncio: dati riconosciuti (testo o vision) +
corrispondenze Discogs + formattazione del messaggio Telegram. Condiviso tra
scripts.vision_test (test manuale), scripts.test_listing (test su un
singolo annuncio dato il link) e scripts.collect (pipeline reale), così la
logica vive in un posto solo.

Un annuncio può contenere PIÙ dischi diversi (lotto): una foto può mostrare
più copertine/retri insieme. Ogni annuncio viene quindi arricchito in una
lista di "item" (uno per disco fisico distinto identificato), fusi tramite
core.vision.matching (stesso release_id in foto diverse = stesso disco
fotografato più volte; stesso release_id nella STESSA foto = copie fisiche
reali). Tutti gli item confluiscono in un unico messaggio Telegram per
l'annuncio.

Ordine di costo crescente per arrivare ai dati, si ferma al primo che basta:
  1. Parsing del titolo (gratis, istantaneo) — se il titolo ha già un
     pattern chiaro "Artista - Album", non serve altro (vale solo per un
     disco singolo, non per i lotti).
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
from core.user_filters import matches_user_filter
from core.vision.matching import Detection, group_detections
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
    nessun dato che uno indovinato male. Vale solo per un disco singolo: un
    lotto non ha un singolo artista/album nel titolo."""
    if not title:
        return {}
    parts = TITLE_SEPARATOR_PATTERN.split(title, maxsplit=1)
    if len(parts) == 2 and len(parts[0].strip()) > 1 and len(parts[1].strip()) > 1:
        return {"artist": parts[0].strip(), "album_title": parts[1].strip()}
    return {}


def get_cached_vision_results(conn, source: str, external_id: str) -> list[dict]:
    """Righe già salvate in vision_results per questo annuncio, da run
    precedenti — se presenti evita di richiamare la vision da capo. Include
    image_url: serve a sapere quali righe vengono dalla stessa foto (per
    distinguere dischi diversi da copie fisiche reali durante la fusione)."""
    rows = conn.execute(
        "SELECT image_url, artist, album_title, label, catalog_number, barcode, other_text "
        "FROM vision_results WHERE source = ? AND external_id = ?",
        (source, external_id),
    ).fetchall()
    columns = ["image_url", "artist", "album_title", "label", "catalog_number", "barcode", "other_text"]
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
    catalogo ripulito, poi quello grezzo come ripiego. Usata per il disco
    singolo (titolo/cache senza lotto); per i lotti vedi _search_single_candidate."""
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


def _search_single_candidate(record: dict) -> dict | None:
    """Come find_discogs_candidates ma ritorna solo il primo/migliore
    candidato — usata per identificare OGNI disco di un lotto singolarmente
    (una ricerca per record riconosciuto, non una sola sui dati uniti)."""
    candidates = find_discogs_candidates(record, max_candidates=1)
    return candidates[0] if candidates else None


def build_items_from_records(photo_records: list[tuple[str, dict]]) -> list[dict]:
    """Da una lista di (photo_id, record riconosciuto) produce una lista di
    {"merged": {...}, "candidates": [...]} — un item per disco fisico
    distinto. Cerca su Discogs OGNI record singolarmente (non dopo averli
    uniti), poi usa core.vision.matching per raggruppare le rilevazioni che
    sono lo stesso disco (stesso release_id, foto diverse) separandole da
    dischi diversi (release_id diversi, anche nella stessa foto — es. più
    copertine affiancate in un lotto)."""
    detections = []
    candidates_by_release: dict[int, dict] = {}

    for photo_id, record in photo_records:
        if not any(record.get(f) for f in MERGE_FIELDS):
            continue  # foto/riga senza nulla di leggibile, non genera una detection

        candidate = _search_single_candidate(record)
        release_id = candidate["id"] if candidate else None
        if candidate:
            candidates_by_release[release_id] = candidate

        detections.append(
            Detection(
                photo_id=photo_id,
                release_id=release_id,
                title=record.get("album_title"),
                catno=record.get("catalog_number"),
                barcode=record.get("barcode"),
                country=candidate.get("country") if candidate else None,
                label=record.get("label"),
                signals={
                    "catalog_number": bool(record.get("catalog_number")),
                    "barcode": bool(record.get("barcode")),
                    "artist_title_text": bool(record.get("artist") and record.get("album_title")),
                    "label_match": bool(record.get("label")),
                },
            )
        )

    if not detections:
        return []

    items = []
    for grouped in group_detections(detections):
        candidate = candidates_by_release.get(grouped.release_id)
        merged = {field: None for field in MERGE_FIELDS}
        merged["album_title"] = grouped.title
        merged["catalog_number"] = grouped.catno
        merged["barcode"] = grouped.barcode
        merged["label"] = grouped.label
        items.append({"merged": merged, "candidates": [candidate] if candidate else []})
    return items


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


def compute_discount_pct(listing_price, listing_currency: str | None, reference_price: float | int | None) -> int | None:
    """% rispetto a un prezzo Discogs di riferimento (positiva = annuncio
    più economico, un affare). None se non calcolabile (manca il prezzo di
    riferimento, manca il prezzo annuncio, o valute diverse — calcola solo
    se in EUR). Per i lotti, reference_price è la SOMMA dei prezzi Good di
    tutti i dischi identificati, non quello di un disco singolo — confrontare
    un solo disco col prezzo dell'intero lotto sarebbe scorretto."""
    if not reference_price or listing_price is None or (listing_currency or "").upper() != "EUR":
        return None
    return round((reference_price - listing_price) / reference_price * 100)


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
    {"items": [{"merged": {...}, "candidates": [...]}, ...],
    "source_of_data": "title"|"cache"|"vision"|"none"} — un item per disco
    fisico distinto identificato (uno per un disco singolo, più di uno per
    un lotto con dischi diversi nelle foto)."""
    hints = parse_title_hints(title)
    if hints.get("artist") and hints.get("album_title"):
        merged = {field: None for field in MERGE_FIELDS}
        merged.update(hints)
        return {"items": [{"merged": merged, "candidates": find_discogs_candidates(merged)}], "source_of_data": "title"}

    cached = get_cached_vision_results(conn, source, external_id)
    if cached:
        photo_records = [(row.get("image_url") or f"cache-{i}", row) for i, row in enumerate(cached)]
        return {"items": build_items_from_records(photo_records), "source_of_data": "cache"}

    images = get_all_images(source, external_id, image_url, max_images)
    photo_records = []
    for img_url in images:
        try:
            image_bytes = fetch_image_bytes(img_url)
            records = recognize_image(image_bytes)
        except Exception as exc:
            print(f"[ERRORE] riconoscimento immagine fallito ({img_url}): {exc}")
            continue

        for record in records:
            insert_vision_result(
                conn,
                source=source,
                external_id=external_id,
                image_url=img_url,
                artist=record["artist"],
                album_title=record["album_title"],
                label=record["label"],
                catalog_number=record["catalog_number"],
                barcode=record["barcode"],
                other_text=record["other_text"],
                raw_response=record["raw_response"],
            )
            photo_records.append((img_url, record))

    return {"items": build_items_from_records(photo_records), "source_of_data": "vision"}


def build_enrichment_message(source: str, title: str, price, currency, url: str | None, items: list[dict]) -> tuple[str, int | None]:
    """Messaggio Telegram UNICO per l'intero annuncio, anche se contiene più
    dischi diversi (lotto) — ognuno in una propria sotto-sezione numerata
    "Disco N" quando ce n'è più di uno. Lo sconto è calcolato sul totale:
    somma dei prezzi Discogs (Good) di tutti i dischi identificati,
    confrontata col prezzo dell'intero annuncio — mai un disco singolo
    contro il prezzo di tutto il lotto. Ritorna anche (oltre al testo) la %
    di sconto come numero, per poterla usare altrove (es. contarla in un
    report) senza doverla riparsare dal testo."""
    multi = len(items) > 1
    item_blocks = []
    total_good_price = 0
    any_good_price = False

    for idx, item in enumerate(items, start=1):
        merged = item["merged"]
        candidates = item["candidates"]

        lines = [f"<b>Disco {idx}</b>"] if multi else []

        recognized_lines = []
        for f in merged:
            if f == "catalog_number":
                # Il codice catalogo è il segnale più preciso per la ricerca
                # Discogs: se manca, un match trovato via artista+titolo è
                # più debole — meglio dirlo esplicitamente che ometterlo.
                value = html.escape(merged[f]) if merged.get(f) else "non identificato"
                recognized_lines.append(f"{FIELD_LABELS[f]}: {value}")
            elif merged.get(f):
                recognized_lines.append(f"{FIELD_LABELS[f]}: {html.escape(merged[f])}")
        lines.append("\n".join(recognized_lines) if recognized_lines else "Nessun dato riconosciuto")

        if candidates:
            candidate_texts = []
            for i, candidate in enumerate(candidates, start=1):
                text, reference_prices = format_discogs_candidate(candidate, i)
                candidate_texts.append(text)
                if i == 1 and reference_prices.get(DEAL_CONDITION):
                    total_good_price += reference_prices[DEAL_CONDITION]
                    any_good_price = True
            lines.append("💿 Discogs:\n" + "\n\n".join(candidate_texts))
        else:
            lines.append("💿 Nessuna corrispondenza trovata su Discogs.")

        item_blocks.append("\n".join(lines))

    discount_pct = compute_discount_pct(price, currency, total_good_price if any_good_price else None)
    discount_line = build_discount_line(discount_pct)

    sections = []

    if discount_line:
        sections.append(discount_line)

    sections.append(f"🎵 <b>{html.escape(title or '')}</b>\n💰 Prezzo annuncio: {price} {currency}")

    if url:
        escaped_url = html.escape(url, quote=True)
        sections.append(f'🔗 <a href="{escaped_url}">Link {source.capitalize()}</a>')

    if item_blocks:
        header = "📋 <b>Dati riconosciuti</b>" if not multi else f"📋 <b>{len(items)} dischi identificati nel lotto</b>"
        sections.append(header + "\n\n" + "\n\n".join(item_blocks))
    else:
        sections.append("📋 Nessun dato riconosciuto")

    return "\n\n".join(sections), discount_pct


def format_digest_line(title: str, price, currency, merged: dict, reference_prices: dict) -> str:
    """Riga sintetica (titolo, album, catalogo, prezzo eBay, prezzo Discogs
    Good) per i comandi /report e /report_miei — una riga per DISCO
    identificato, non per annuncio (un lotto con più dischi genera più righe)."""
    good_price = reference_prices.get(DEAL_CONDITION)
    good_text = f"€{good_price}" if good_price else "n/d"

    label = merged.get("album_title") or title or "?"
    if merged.get("artist"):
        label = f"{merged['artist']} — {label}"
    catalog_part = f" | Cat: {merged['catalog_number']}" if merged.get("catalog_number") else ""

    return f"🎵 {html.escape(label)}{html.escape(catalog_part)} | eBay: {price} {currency} | Discogs (Good): {good_text}"


def generate_digest(conn, limit: int, chat_id: int | None = None, require_discount: bool = False) -> list[str]:
    """Righe sintetiche per gli annunci GIÀ nel DB (niente ricerca nuova sui
    marketplace, niente vision fresca — usa solo dati già noti da titolo o
    cache) che hanno una corrispondenza Discogs, una riga per disco
    identificato (un lotto con più dischi genera più righe).

    chat_id=None mostra tutto, senza applicare nessun filtro personale; se
    passato, mostra solo gli annunci che corrispondono al filtro personale
    di quell'utente (report "i miei filtri" — non guarda il prezzo, solo
    cosa interessa all'utente).

    require_discount=True mostra solo i dischi il cui prezzo (per i lotti:
    quota del prezzo Discogs Good sul totale dei dischi identificati) è già
    sotto il prezzo Discogs "Good"; False non applica nessun filtro di prezzo."""
    rows = conn.execute("SELECT source, external_id, title, price, currency FROM listings ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    lines = []
    for source, external_id, title, price, currency in rows:
        if chat_id is not None and not matches_user_filter(chat_id, title):
            continue

        hints = parse_title_hints(title)
        if hints.get("artist") and hints.get("album_title"):
            merged = {field: None for field in MERGE_FIELDS}
            merged.update(hints)
            items = [{"merged": merged, "candidates": find_discogs_candidates(merged)}]
        else:
            cached = get_cached_vision_results(conn, source, external_id)
            if not cached:
                continue
            photo_records = [(row.get("image_url") or f"cache-{i}", row) for i, row in enumerate(cached)]
            items = build_items_from_records(photo_records)

        for item in items:
            if not item["candidates"]:
                continue
            _, reference_prices = format_discogs_candidate(item["candidates"][0], 1)

            if require_discount:
                discount_pct = compute_discount_pct(price, currency, reference_prices.get(DEAL_CONDITION))
                if discount_pct is None or discount_pct <= 0:
                    continue

            lines.append(format_digest_line(title, price, currency, item["merged"], reference_prices))

    return lines
