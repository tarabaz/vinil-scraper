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

import difflib
import html
import re

import requests

from core.collectors.discogs import get_marketplace_stats, get_price_suggestions, search_by_catalog_number, search_by_title, search_release
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


TITLE_MATCH_THRESHOLD = 0.3  # con un altro segnale (catalogo, artista) ad ancorare il match
TITLE_ONLY_MATCH_THRESHOLD = 0.6  # nessun altro segnale: la sola somiglianza del titolo deve bastare da sola


def _titles_plausibly_match(recognized_title: str | None, candidate_title: str | None, threshold: float = TITLE_MATCH_THRESHOLD) -> bool:
    """Verifica di sanità: se abbiamo sia un titolo riconosciuto (da testo o
    vision) sia il titolo del candidato Discogs, controlla che si
    somiglino almeno un po'. Un codice catalogo (o un barcode letto per
    sbaglio come catalogo) può abbinare la release di un disco del tutto
    diverso — senza questo controllo verrebbe accettata comunque."""
    if not recognized_title or not candidate_title:
        return True  # niente da confrontare, non possiamo scartare per sicurezza
    ratio = difflib.SequenceMatcher(None, recognized_title.lower(), candidate_title.lower()).ratio()
    return ratio >= threshold


ARTIST_MATCH_THRESHOLD = 0.4


def _split_candidate_title(candidate_title: str | None) -> tuple[str | None, str]:
    """Il campo "title" di un risultato Discogs è quasi sempre nel formato
    "Artista - Album": lo separa in (artista o None, solo album — l'intera
    stringa se non c'è il separatore)."""
    if not candidate_title:
        return None, ""
    parts = TITLE_SEPARATOR_PATTERN.split(candidate_title, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return None, candidate_title


def _candidate_artist(candidate_title: str | None) -> str | None:
    return _split_candidate_title(candidate_title)[0]


def _trace(msg: str) -> None:
    """Log sintetico di un passaggio dell'identificazione, passo passo —
    utile in fase di test per capire perché un disco viene trovato/scartato
    invece di scoprirlo solo dal risultato finale."""
    print(f"    ↳ {msg}")


def _candidate_plausibly_matches(record: dict, candidate: dict, title_threshold: float = TITLE_MATCH_THRESHOLD) -> bool:
    """Verifica di sanità applicata a QUALSIASI candidato trovato, a
    prescindere da come lo si è cercato (codice catalogo, barcode, artista,
    solo titolo): un codice può abbinare per sbaglio la release di un disco
    completamente diverso (o con lo stesso barcode riusato/refuso), quindi
    ogni dato che la vision ha effettivamente letto va confrontato con
    quello che dice Discogs, non solo il titolo. Confronta il titolo
    riconosciuto solo con la parte album del candidato (non "Artista -
    Album" per intero, altrimenti il prefisso artista abbassa la somiglianza
    a prescindere da quanto l'album corrisponda)."""
    candidate_artist, candidate_album = _split_candidate_title(candidate.get("title"))

    if not _titles_plausibly_match(record.get("album_title"), candidate_album, threshold=title_threshold):
        _trace(f"scarto '{candidate.get('title')}': titolo troppo diverso da '{record.get('album_title')}'")
        return False

    recognized_artist = record.get("artist")
    if recognized_artist and candidate_artist:
        ratio = difflib.SequenceMatcher(None, recognized_artist.lower(), candidate_artist.lower()).ratio()
        if ratio < ARTIST_MATCH_THRESHOLD:
            _trace(f"scarto '{candidate.get('title')}': artista '{candidate_artist}' non combacia con '{recognized_artist}'")
            return False

    _trace(f"'{candidate.get('title')}' plausibile → accettato")
    return True


def _search_single_candidate(record: dict) -> dict | None:
    """Cerca UN disco (per identificarlo dentro il raggruppamento di un
    lotto): preferisce il codice catalogo (più preciso), poi artista+titolo
    esatto, poi una ricerca per solo titolo come rete di sicurezza finale —
    quest'ultima scatta ogni volta che le prime due non hanno trovato nulla
    di plausibile, non solo quando manca l'artista. A differenza di
    find_discogs_candidates verifica sempre che titolo E (se letto) artista
    del candidato somiglino a quelli riconosciuti, scartando abbinamenti
    implausibili invece di accettare il primo risultato a occhi chiusi."""
    _trace(f"disco da identificare: artista={record.get('artist')!r}, titolo={record.get('album_title')!r}, catalogo={record.get('catalog_number')!r}, barcode={record.get('barcode')!r}")

    if record.get("catalog_number"):
        raw = record["catalog_number"]
        for candidate_value in dict.fromkeys([clean_catalog_number(raw), raw]):
            _trace(f"cerco su Discogs per codice catalogo '{candidate_value}'...")
            try:
                candidates = search_by_catalog_number(candidate_value)
            except Exception as exc:
                print(f"[ERRORE] ricerca Discogs per codice catalogo '{candidate_value}' fallita: {exc}")
                continue
            _trace(f"{len(candidates)} candidati trovati per codice catalogo")
            for candidate in candidates:
                if _candidate_plausibly_matches(record, candidate):
                    return candidate

    if record.get("artist") and record.get("album_title"):
        _trace(f"cerco su Discogs per artista+titolo esatto '{record['artist']}' / '{record['album_title']}'...")
        try:
            release = search_release(record["artist"], record["album_title"])
        except Exception as exc:
            print(f"[ERRORE] ricerca Discogs per artista/titolo fallita: {exc}")
            release = None
        if release:
            candidate = {
                "id": release["id"],
                "title": release.get("title"),
                "country": None,
                "year": None,
                "label": None,
                "catno": None,
            }
            if _candidate_plausibly_matches(record, candidate):
                return candidate
        else:
            _trace("nessun risultato per artista+titolo esatto")

    if record.get("album_title"):
        # Rete di sicurezza ad ampio raggio: scatta ogni volta che le
        # ricerche più mirate sopra non hanno trovato nulla, non solo
        # quando manca l'artista — capita anche quando l'artista letto non
        # combacia esattamente con la stringa indicizzata da Discogs (piccole
        # differenze di grafia, alias, ecc.) e search_release non trova
        # nulla pur essendo il disco giusto. Se abbiamo comunque un artista
        # letto, il controllo incrociato su di esso resta la rete di
        # sicurezza (soglia normale sul titolo); se non lo abbiamo, soglia
        # più severa sul titolo per compensare la mancanza di un secondo
        # segnale di conferma — un titolo generico può appartenere a più
        # dischi diversi.
        threshold = TITLE_MATCH_THRESHOLD if record.get("artist") else TITLE_ONLY_MATCH_THRESHOLD
        _trace(f"rete di sicurezza: cerco su Discogs per solo titolo '{record['album_title']}' (soglia somiglianza {threshold})...")
        try:
            candidates = search_by_title(record["album_title"])
        except Exception as exc:
            print(f"[ERRORE] ricerca Discogs per solo titolo fallita: {exc}")
            candidates = []
        _trace(f"{len(candidates)} candidati trovati per solo titolo")
        for candidate in candidates:
            if _candidate_plausibly_matches(record, candidate, title_threshold=threshold):
                return candidate

    _trace("nessuna corrispondenza Discogs plausibile per questo disco")
    return None


def build_items_from_records(photo_records: list[tuple[str, dict]]) -> tuple[list[dict], int]:
    """Da una lista di (photo_id, record riconosciuto) produce (items, numero
    totale di dischi distinti rilevati prima di scartare quelli deboli).
    items è una lista di {"merged": {...}, "candidates": [...]} — un item
    per disco fisico distinto ritenuto abbastanza affidabile da mostrare.
    Cerca su Discogs OGNI record singolarmente (non dopo averli uniti), poi
    usa core.vision.matching per raggruppare le rilevazioni che sono lo
    stesso disco (stesso release_id, foto diverse) separandole da dischi
    diversi (release_id diversi, anche nella stessa foto — es. più copertine
    affiancate in un lotto). Un gruppo senza corrispondenza Discogs e senza
    codice catalogo/barcode viene scartato (probabile testo letto male dalla
    vision) ma resta conteggiato nel totale, cosi il chiamante può segnalare
    quanti dischi visibili nelle foto non sono stati identificati con
    sicurezza invece di sparire silenziosamente dal conteggio."""
    detections = []
    candidates_by_release: dict[int, dict] = {}

    for photo_id, record in photo_records:
        if not any(record.get(f) for f in MERGE_FIELDS):
            continue  # foto/riga senza nulla di leggibile, non genera una detection

        print(f"  📷 {photo_id}")
        candidate = _search_single_candidate(record)
        release_id = candidate["id"] if candidate else None
        if candidate:
            candidates_by_release[release_id] = candidate

        detections.append(
            Detection(
                photo_id=photo_id,
                release_id=release_id,
                artist=record.get("artist"),
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
                    "discogs_match": bool(candidate),
                },
            )
        )

    if not detections:
        return [], 0

    grouped_items = group_detections(detections)
    total_groups = len(grouped_items)

    print(f"  📊 {total_groups} dischi distinti rilevati nelle foto, verifico quali tenere...")

    items = []
    for grouped in grouped_items:
        if grouped.release_id is None and not (grouped.catno or grouped.barcode):
            # Nessuna corrispondenza Discogs e nessun segnale forte (codice
            # catalogo/barcode): probabilmente testo letto male dalla vision
            # (es. una scritta promozionale scambiata per il titolo) — meglio
            # scartarlo che mostrarlo come un disco fantasma in più nel lotto.
            print(f"    ✗ scarto '{grouped.title}': nessun match Discogs e nessun segnale forte (probabile testo letto male)")
            continue

        print(f"    ✓ tengo '{grouped.title}' (artista: {grouped.artist})")

        candidate = candidates_by_release.get(grouped.release_id)
        merged = {field: None for field in MERGE_FIELDS}
        merged["artist"] = grouped.artist
        merged["album_title"] = grouped.title
        merged["catalog_number"] = grouped.catno
        merged["barcode"] = grouped.barcode
        merged["label"] = grouped.label

        if not merged["artist"] and candidate:
            # Se la vision non ha letto l'artista ma il match è già
            # confermato (es. trovato via solo titolo), lo recuperiamo da
            # Discogs invece di lasciarlo vuoto.
            merged["artist"] = _candidate_artist(candidate.get("title"))

        items.append(
            {
                "merged": merged,
                "candidates": [candidate] if candidate else [],
                "confidence_score": grouped.confidence_score,
                "confidence_level": grouped.confidence_level,
            }
        )
    return items, total_groups


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


def get_market_reference(release_id: int) -> dict:
    """Prezzo più basso TRA le copie realmente in vendita ora per questa
    release (non una stima). Dict vuoto se non disponibile (nessuna copia
    in vendita, o errore Discogs — non blocca il resto del messaggio)."""
    try:
        stats = get_marketplace_stats(release_id)
    except Exception as exc:
        print(f"[ERRORE] statistiche di mercato Discogs fallite per release {release_id}: {exc}")
        return {}
    if not stats.get("lowest_price"):
        return {}
    return stats


def format_discogs_candidate(candidate: dict, index: int) -> tuple[str, dict]:
    """Ritorna (blocco di testo formattato del candidato, prezzi di riferimento arrotondati).
    Il dict di riferimento include anche "market_lowest" (prezzo più basso
    tra le copie realmente in vendita ora, se disponibile e in EUR) — più
    affidabile della stima "Good" per release poco scambiate o mai vendute
    su Discogs, dove la stima algoritmica può essere molto lontana dal
    prezzo reale a cui la gente la vende oggi."""
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

    market = get_market_reference(candidate["id"])
    if market:
        num_for_sale = market.get("num_for_sale")
        copies_text = f" ({num_for_sale} copie)" if num_for_sale else ""
        block.append(f"   In vendita ora: da {round(market['lowest_price'])} {market.get('currency', '')}{copies_text}")
        if (market.get("currency") or "").upper() == "EUR":
            reference_prices["market_lowest"] = round(market["lowest_price"])

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
    # Il riferimento può essere il prezzo di mercato reale (preferito) o,
    # in mancanza di copie in vendita, la stima "Good" — vedi
    # build_enrichment_message. Testo volutamente generico ("Discogs") per
    # non promettere "Good" quando in realtà si sta usando l'altro.
    if discount_pct is None:
        return ""
    if discount_pct > 0:
        return f"🔻 -{discount_pct}% rispetto a Discogs"
    if discount_pct < 0:
        return f"🔺 +{abs(discount_pct)}% rispetto a Discogs"
    return "➖ Prezzo in linea con Discogs"


def enrich_listing(conn, source: str, external_id: str, title: str, image_url: str | None, max_images: int = 5) -> dict:
    """Arricchisce un annuncio con dati riconosciuti e corrispondenze
    Discogs, al minor costo possibile (titolo -> cache -> vision). Ritorna
    {"items": [{"merged": {...}, "candidates": [...]}, ...],
    "source_of_data": "title"|"cache"|"vision"|"none", "total_detected": int}
    — un item per disco fisico distinto identificato (uno per un disco
    singolo, più di uno per un lotto con dischi diversi nelle foto).
    total_detected è il numero di dischi distinti rilevati nelle foto PRIMA
    di scartare quelli senza dati abbastanza affidabili — se maggiore di
    len(items), vuol dire che in foto se ne vedevano di più di quelli
    effettivamente identificati."""
    hints = parse_title_hints(title)
    if hints.get("artist") and hints.get("album_title"):
        merged = {field: None for field in MERGE_FIELDS}
        merged.update(hints)
        items = [{"merged": merged, "candidates": find_discogs_candidates(merged)}]
        return {"items": items, "source_of_data": "title", "total_detected": len(items)}

    cached = get_cached_vision_results(conn, source, external_id)
    if cached:
        photo_records = [(row.get("image_url") or f"cache-{i}", row) for i, row in enumerate(cached)]
        items, total_detected = build_items_from_records(photo_records)
        return {"items": items, "source_of_data": "cache", "total_detected": total_detected}

    images = get_all_images(source, external_id, image_url, max_images)
    photo_records = []
    for img_url in images:
        try:
            image_bytes = fetch_image_bytes(img_url)
            records = recognize_image(image_bytes)
        except Exception as exc:
            print(f"[ERRORE] riconoscimento immagine fallito ({img_url}): {exc}")
            continue

        if not any(any(record.get(f) for f in MERGE_FIELDS) for record in records):
            # Nessun campo utile in nessun record per questa foto: stampo la
            # risposta grezza del modello per capire se ha risposto "niente
            # di leggibile" a ragion veduta o se ha semplicemente ignorato il
            # formato richiesto — altrimenti da qui non lo sapremmo mai.
            raw_preview = (records[0].get("raw_response") or "")[:500]
            print(f"[DEBUG] nessun dato riconosciuto in {img_url} — risposta grezza del modello: {raw_preview!r}")

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

    items, total_detected = build_items_from_records(photo_records)
    return {"items": items, "source_of_data": "vision", "total_detected": total_detected}


def build_enrichment_message(
    source: str, title: str, price, currency, url: str | None, items: list[dict], total_detected: int | None = None
) -> tuple[str, int | None]:
    """Messaggio Telegram UNICO per l'intero annuncio, anche se contiene più
    dischi diversi (lotto) — ognuno in una propria sotto-sezione numerata
    "Disco N" quando ce n'è più di uno. Lo sconto è calcolato sul totale:
    somma, per ogni disco identificato, del prezzo più basso tra le copie
    REALMENTE in vendita ora su Discogs (più affidabile) o, se non
    disponibile (nessuna copia in vendita), della stima "Good" —
    confrontata col prezzo dell'intero annuncio, mai un disco singolo
    contro il prezzo di tutto il lotto. Ritorna anche (oltre al testo) la %
    di sconto come numero, per poterla usare altrove (es. contarla in un
    report) senza doverla riparsare dal testo."""
    multi = len(items) > 1
    item_blocks = []
    total_reference_price = 0
    any_reference_price = False

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

        confidence_level = item.get("confidence_level")
        if confidence_level and recognized_lines:
            confidence_pct = round((item.get("confidence_score") or 0) * 100)
            lines.append(f"🔍 Affidabilità: {confidence_level} ({confidence_pct}%)")

        if candidates:
            candidate_texts = []
            for i, candidate in enumerate(candidates, start=1):
                text, reference_prices = format_discogs_candidate(candidate, i)
                candidate_texts.append(text)
                if i == 1:
                    # Preferisci il prezzo reale di mercato (copie
                    # effettivamente in vendita ora) alla stima "Good": per
                    # release poco scambiate o mai vendute la stima
                    # algoritmica può essere molto lontana dalla realtà.
                    deal_price = reference_prices.get("market_lowest") or reference_prices.get(DEAL_CONDITION)
                    if deal_price:
                        total_reference_price += deal_price
                        any_reference_price = True
            lines.append("💿 Discogs:\n" + "\n\n".join(candidate_texts))
        else:
            lines.append("💿 Nessuna corrispondenza trovata su Discogs.")

        item_blocks.append("\n".join(lines))

    discount_pct = compute_discount_pct(price, currency, total_reference_price if any_reference_price else None)
    discount_line = build_discount_line(discount_pct)
    if discount_line and total_detected and total_detected > len(items):
        # Lo sconto è calcolato solo sui dischi identificati: se nelle foto
        # se ne vedevano di più, il confronto col prezzo dell'intero lotto
        # non è equo e va segnalato, non lasciato implicito.
        discount_line += f" (su {len(items)} di {total_detected} dischi rilevati)"

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
            items, _ = build_items_from_records(photo_records)

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
