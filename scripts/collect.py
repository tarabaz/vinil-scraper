"""Cerca annunci sui marketplace abilitati, li filtra a regole, salva nel DB
i nuovi e notifica su Telegram. Marketplace-agnostico: usa il registro dei
collector e l'impostazione marketplaces.enabled, non ha logica specifica di
nessuna fonte (quella vive nei singoli collector)."""

import time

from bot.notifier import edit_message, send_message
from core.collectors.base import listing_to_dict
from core.collectors.ebay import find_category_id
from core.collectors.registry import REGISTRY
from core.db import RETENTION_HOURS, cleanup_old_listings, get_connection, insert_listing
from core.filters import load_rules, passes_filters
from core.keywords import enabled_keywords
from core.notifications import has_been_notified, mark_notified
from core.query_defaults import DEFAULT_GENRE_QUERIES, DEFAULT_LOT_QUERIES
from core.settings import get_setting
from core.user_filters import matches_user_filter
from core.users import ADMIN_CHAT_ID, ensure_admin_registered, list_approved
from core.vision.enrichment import build_enrichment_message, enrich_listing

SECONDS_BETWEEN_MESSAGES = 1  # evita il flood control di Telegram su tanti messaggi consecutivi

# Tetti opzionali sugli annunci arricchiti per esecuzione — None = nessun
# limite. MAX_ENRICHED_LISTINGS_PER_RUN conterebbe solo gli annunci
# "validi" (con corrispondenza Discogs); MAX_LISTINGS_CHECKED_PER_RUN è un
# tetto separato sul totale controllato. Utili se in futuro servisse di
# nuovo limitare il costo di una scansione molto grande.
MAX_ENRICHED_LISTINGS_PER_RUN = None
MAX_LISTINGS_CHECKED_PER_RUN = None
MAX_IMAGES_PER_LISTING = 5  # un lotto può avere decine di foto, non le processiamo tutte

PROGRESS_EDIT_EVERY = 5  # ogni quanti annunci controllati aggiornare il messaggio di avanzamento
UNDER_VALUE_THRESHOLD_PCT = 50  # soglia (%) per il conteggio "sotto il valore Discogs" nel report finale

DEFAULT_ENABLED_MARKETPLACES = ["ebay", "subito"]
DEFAULT_SEARCH_MODES = ["lotti", "singoli"]
DEFAULT_EBAY_CATEGORY = "Vinili"


def get_backlog_candidates(conn, users: list[dict], exclude_keys: set[tuple[str, str]]) -> list[dict]:
    """Annunci GIÀ nel DB (non necessariamente nuovi in questo giro) che
    potrebbero interessare a qualche utente approvato in base al filtro
    personale ATTUALE, e che non gli sono ancora stati notificati — recupera
    corrispondenze con filtri aggiunti dopo che l'annuncio era già stato
    trovato. Controllato solo quando si lancia una scansione (/cerca), non
    ad ogni cambio di filtro."""
    if not users:
        return []
    rows = conn.execute("SELECT source, external_id, title, price, currency, url, image_url FROM listings").fetchall()
    candidates = []
    for source, external_id, title, price, currency, url, image_url in rows:
        if (source, external_id) in exclude_keys:
            continue
        relevant = any(
            matches_user_filter(user["chat_id"], title) and not has_been_notified(conn, user["chat_id"], source, external_id)
            for user in users
        )
        if relevant:
            candidates.append(
                {"source": source, "external_id": external_id, "title": title, "price": price, "currency": currency, "url": url, "image_url": image_url}
            )
    return candidates


def notify_new_listings(new_listings: list[dict], errors: list[str] | None = None) -> dict:
    """Va chiamata UNA SOLA VOLTA, con i nuovi annunci già aggregati da tutte
    le query di ricerca. Oltre ai nuovi, controlla anche il backlog (annunci
    già in DB che ora corrispondono al filtro personale di qualcuno, non
    ancora notificati — es. filtro aggiunto dopo che l'annuncio era già
    stato trovato). Per ognuno (fino ai limiti sopra, condivisi tra nuovi e
    backlog): arricchisce con dati riconosciuti (titolo -> cache -> vision,
    il meno costoso che basta) e cerca su Discogs, poi notifica i "validi"
    (con corrispondenza Discogs) a ogni utente approvato la cui filtro
    personale corrisponde.

    Manda sempre un report all'amministratore (anche con 0 risultati): un
    messaggio all'avvio che si aggiorna mentre la scansione procede
    (progresso), poi un riepilogo finale — così si può seguire cosa sta
    facendo anche da lontano, senza aprire il terminale."""
    errors = errors or []
    users = list_approved()

    conn = get_connection()
    backlog_candidates = get_backlog_candidates(conn, users, {(item["source"], item["external_id"]) for item in new_listings})
    items_to_process = new_listings + backlog_candidates

    progress_message_id = None
    if ADMIN_CHAT_ID:
        try:
            if items_to_process:
                start_text = (
                    f"🔍 Scansione: {len(new_listings)} annunci nuovi trovati "
                    f"({len(backlog_candidates)} anche dal backlog per i filtri personali). Arricchimento in corso..."
                )
            else:
                start_text = "🔍 Scansione completata: nessun annuncio nuovo o da ricontrollare."
            progress_message_id = send_message(start_text, chat_id=ADMIN_CHAT_ID)
        except Exception as exc:
            print(f"[ERRORE] invio messaggio di avvio fallito: {exc}")

    checked_count = 0
    valid_count = 0
    notified_count = 0
    under_threshold_count = 0

    if items_to_process and users:
        for item in items_to_process:
            if MAX_ENRICHED_LISTINGS_PER_RUN is not None and valid_count >= MAX_ENRICHED_LISTINGS_PER_RUN:
                break
            if MAX_LISTINGS_CHECKED_PER_RUN is not None and checked_count >= MAX_LISTINGS_CHECKED_PER_RUN:
                break
            checked_count += 1
            print(f"[{checked_count}/{len(items_to_process)}] Arricchimento: {item['title']}")

            enrichment = enrich_listing(
                conn, item["source"], item["external_id"], item["title"], item.get("image_url"), max_images=MAX_IMAGES_PER_LISTING
            )
            items = enrichment["items"]
            matched_count = sum(1 for it in items if it["candidates"])
            print(f"    fonte dati: {enrichment['source_of_data']}, dischi identificati: {len(items)}, con corrispondenza Discogs: {matched_count}")

            if matched_count:
                valid_count += 1
                message, discount_pct = build_enrichment_message(
                    item["source"],
                    item["title"],
                    item["price"],
                    item["currency"],
                    item["url"],
                    items,
                    total_detected=enrichment["total_detected"],
                )
                is_deal = discount_pct is not None and discount_pct >= UNDER_VALUE_THRESHOLD_PCT
                if is_deal:
                    under_threshold_count += 1
                    print(f"    ✅ affare: sconto {discount_pct}% vs Discogs (Good)")
                elif discount_pct is not None:
                    print(f"    scarto {discount_pct}% vs Discogs (Good), sotto soglia")

                # Notifica solo i veri affari (sconto >= soglia): gli altri
                # restano comunque nel DB, recuperabili con /report.
                if is_deal:
                    sent_to_anyone = False
                    for user in users:
                        if has_been_notified(conn, user["chat_id"], item["source"], item["external_id"]):
                            continue
                        if not matches_user_filter(user["chat_id"], item["title"]):
                            continue
                        try:
                            send_message(message, parse_mode="HTML", chat_id=user["chat_id"])
                            mark_notified(conn, user["chat_id"], item["source"], item["external_id"])
                            sent_to_anyone = True
                        except Exception as exc:
                            print(f"[ERRORE] invio a {user['chat_id']} fallito: {exc}")
                        time.sleep(SECONDS_BETWEEN_MESSAGES)
                    if sent_to_anyone:
                        notified_count += 1

            if ADMIN_CHAT_ID and progress_message_id and checked_count % PROGRESS_EDIT_EVERY == 0:
                try:
                    edit_message(
                        ADMIN_CHAT_ID,
                        progress_message_id,
                        f"⏳ Scansione in corso: {checked_count}/{len(items_to_process)} annunci controllati, "
                        f"{valid_count} validi trovati finora...",
                    )
                except Exception as exc:
                    print(f"[ERRORE] aggiornamento messaggio di progresso fallito: {exc}")
    conn.close()

    summary_lines = [
        f"✅ Scansione completata: {len(new_listings)} annunci nuovi trovati, "
        f"{len(backlog_candidates)} dal backlog per i filtri personali."
    ]
    if items_to_process:
        summary_lines.append(f"{checked_count} controllati, {valid_count} con corrispondenza Discogs, {notified_count} notificati.")
        summary_lines.append(f"{under_threshold_count} sotto il {UNDER_VALUE_THRESHOLD_PCT}% del valore Discogs (Good).")
        if MAX_ENRICHED_LISTINGS_PER_RUN is not None and valid_count >= MAX_ENRICHED_LISTINGS_PER_RUN:
            summary_lines.append(f"⚠️ Limite di {MAX_ENRICHED_LISTINGS_PER_RUN} annunci validi raggiunto in questo giro.")
        elif MAX_LISTINGS_CHECKED_PER_RUN is not None and checked_count >= MAX_LISTINGS_CHECKED_PER_RUN:
            summary_lines.append(f"⚠️ Limite di {MAX_LISTINGS_CHECKED_PER_RUN} annunci controllati raggiunto in questo giro.")
    if errors:
        summary_lines.append(f"⚠️ {len(errors)} ricerche fallite: " + "; ".join(errors))
    summary = "\n".join(summary_lines)

    if ADMIN_CHAT_ID:
        try:
            if progress_message_id:
                edit_message(ADMIN_CHAT_ID, progress_message_id, summary)
            else:
                send_message(summary, chat_id=ADMIN_CHAT_ID)
        except Exception as exc:
            print(f"[ERRORE] invio riepilogo fallito: {exc}")

    print(f"\n{summary}")

    return {"checked": checked_count, "valid": valid_count, "notified": notified_count, "under_threshold": under_threshold_count}


def collect(collector, query: str, category: str = "vinyl", **search_settings) -> list[dict]:
    """Cerca via una query, filtra, salva nel DB i nuovi. Ritorna solo la
    lista dei nuovi annunci trovati DA QUESTA query — la notifica avviene
    altrove (notify_new_listings), una sola volta, con tutte le query
    aggregate: non ha senso notificare/ripetere la pipeline per ogni parola
    di ricerca, sono solo formulazioni diverse della STESSA ricerca."""
    conn = get_connection()
    rules = load_rules(category)
    listings = collector.search(query, **search_settings)

    new_listings = []
    duplicate_count = 0
    discarded_count = 0

    for listing in listings:
        item = listing_to_dict(listing)

        ok, reason = passes_filters(item, rules)
        if not ok:
            discarded_count += 1
            print(f"[SCARTATO] {item['title']} — {reason}")
            continue

        is_new = insert_listing(conn, category=category, **item)
        if is_new:
            new_listings.append(item)
            print(f"[NUOVO] {item['title']} — {item['price']} {item['currency']}")
        else:
            duplicate_count += 1

    print(
        f"\nTotale: {len(listings)} annunci trovati, {discarded_count} scartati dai filtri, "
        f"{len(new_listings)} nuovi, {duplicate_count} già visti."
    )

    return new_listings


def run_collection() -> dict:
    """Ciclo completo: pulizia DB, ricerca su tutti i marketplace abilitati
    (tutte le query aggregate), filtri, dedup, arricchimento (vision +
    Discogs), notifica. Ritorna un riepilogo — usata sia dall'esecuzione da
    terminale (__main__) sia dal comando /cerca del bot Telegram, così la
    logica vive in un posto solo."""
    ensure_admin_registered()

    cleanup_conn = get_connection()
    removed = cleanup_old_listings(cleanup_conn)
    cleanup_conn.close()
    print(f"Pulizia DB: rimossi {removed} annunci non visti da più di {RETENTION_HOURS} ore.\n")

    enabled_marketplaces = get_setting("marketplaces.enabled", DEFAULT_ENABLED_MARKETPLACES)
    search_modes = get_setting("search.modes", DEFAULT_SEARCH_MODES)
    print(f"Marketplace abilitati: {enabled_marketplaces}")
    print(f"Tipi di ricerca abilitati: {search_modes}\n")

    # eBay ha bisogno di un lookup una tantum della categoria, solo se
    # l'impostazione richiede di restringere la ricerca a "Vinili".
    ebay_category_id = None
    ebay_category_setting = get_setting("marketplace.ebay.category", DEFAULT_EBAY_CATEGORY)
    if "ebay" in enabled_marketplaces and ebay_category_setting == DEFAULT_EBAY_CATEGORY:
        ebay_category_id = find_category_id("Vinyl Records")
        print(f"Categoria eBay usata per le ricerche: {ebay_category_id}\n")
    elif "ebay" in enabled_marketplaces:
        print(f"Categoria eBay: '{ebay_category_setting}', nessuna restrizione applicata.\n")

    all_new_listings = []
    errors = []

    for marketplace in enabled_marketplaces:
        if marketplace not in REGISTRY:
            print(f"Marketplace sconosciuto in REGISTRY, salto: {marketplace}")
            continue

        collector = REGISTRY[marketplace]()
        queries = []
        if "singoli" in search_modes:
            queries += enabled_keywords(
                f"search.queries.{marketplace}.genre", DEFAULT_GENRE_QUERIES.get(marketplace, [])
            )
        if "lotti" in search_modes:
            queries += enabled_keywords(
                f"search.queries.{marketplace}.lot", DEFAULT_LOT_QUERIES.get(marketplace, [])
            )

        for query in queries:
            print(f"\n=== [{marketplace}] Ricerca: {query} ===")
            search_settings = {"category_ids": ebay_category_id} if marketplace == "ebay" else {}
            try:
                all_new_listings += collect(collector, query, **search_settings)
            except Exception as exc:
                error_message = f"Ricerca '{query}' su {marketplace} fallita: {exc}"
                print(f"[ERRORE] {error_message}")
                errors.append(error_message)

    print(f"\n=== Ricerche completate: {len(all_new_listings)} annunci nuovi in totale, notifica in corso ===")
    notify_summary = notify_new_listings(all_new_listings, errors=errors)

    return {"removed": removed, "new_listings": len(all_new_listings), "errors": errors, **notify_summary}


if __name__ == "__main__":
    run_collection()
