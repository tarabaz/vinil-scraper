"""Cerca annunci sui marketplace abilitati, li filtra a regole, salva nel DB
i nuovi e notifica su Telegram. Marketplace-agnostico: usa il registro dei
collector e l'impostazione marketplaces.enabled, non ha logica specifica di
nessuna fonte (quella vive nei singoli collector)."""

import html
import time
from datetime import datetime

from bot.notifier import send_message
from core.collectors.base import listing_to_dict
from core.collectors.ebay import find_category_id
from core.collectors.registry import REGISTRY
from core.db import RETENTION_HOURS, cleanup_old_listings, get_connection, insert_listing
from core.filters import load_rules, passes_filters
from core.keywords import enabled_keywords
from core.query_defaults import DEFAULT_GENRE_QUERIES, DEFAULT_LOT_QUERIES
from core.settings import get_setting
from core.user_filters import matches_user_filter
from core.users import ensure_admin_registered, list_approved

SECONDS_BETWEEN_MESSAGES = 1  # evita il flood control di Telegram su tanti messaggi consecutivi

# LIMITE TEMPORANEO per le prove: la ricerca reale può trovare centinaia di
# annunci nuovi al primo giro (il DB parte vuoto), che diventerebbero
# altrettanti messaggi Telegram. Finché si sta testando, cap totale di
# messaggi inviati per esecuzione — gli annunci oltre il limite restano
# comunque salvati nel DB (non si perdono), semplicemente non notificati
# in questo giro. Da alzare/rimuovere quando si passa all'uso reale.
MAX_NOTIFICATIONS_PER_RUN = 5

DEFAULT_ENABLED_MARKETPLACES = ["ebay", "subito"]
DEFAULT_SEARCH_MODES = ["lotti", "singoli"]
DEFAULT_EBAY_CATEGORY = "Vinili"


def format_listed_at(listed_at: str | None) -> str:
    if not listed_at:
        return "data non disponibile"
    try:
        return datetime.fromisoformat(listed_at.replace("Z", "+00:00")).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return listed_at


def build_message(item: dict) -> str:
    title = html.escape(item["title"] or "")
    url = html.escape(item["url"] or "", quote=True)
    # TODO: quando avremo l'arricchimento Discogs, calcolare qui il prezzo
    # realistico del disco (e più avanti la differenza/margine).
    return (
        f"🎵 {title}\n"
        f"Prezzo annuncio: {item['price']} {item['currency']}\n"
        f"Prezzo realistico: non disponibile\n"
        f"Pubblicato: {format_listed_at(item.get('listed_at'))}\n"
        f'<a href="{url}">Link {item["source"].capitalize()}</a>'
    )


def notify_new_listings(new_listings: list[dict]) -> None:
    """Va chiamata UNA SOLA VOLTA, con i nuovi annunci già aggregati da tutte
    le query di ricerca. Ricerca unica e globale: ogni utente approvato
    riceve solo gli annunci che passano anche il proprio filtro personale
    (nessun filtro personale impostato = riceve tutto). Il totale dei
    messaggi inviati è limitato da MAX_NOTIFICATIONS_PER_RUN."""
    users = list_approved()
    if not users or not new_listings:
        return

    sent_count = 0
    for item in new_listings:
        if sent_count >= MAX_NOTIFICATIONS_PER_RUN:
            break
        message = build_message(item)
        for user in users:
            if sent_count >= MAX_NOTIFICATIONS_PER_RUN:
                break
            if not matches_user_filter(user["chat_id"], item["title"]):
                continue
            try:
                send_message(message, parse_mode="HTML", chat_id=user["chat_id"])
            except Exception as exc:
                print(f"[ERRORE] invio a {user['chat_id']} fallito: {exc}")
            sent_count += 1
            time.sleep(SECONDS_BETWEEN_MESSAGES)

    if sent_count >= MAX_NOTIFICATIONS_PER_RUN:
        print(
            f"\n⚠️  Limite di {MAX_NOTIFICATIONS_PER_RUN} notifiche raggiunto: "
            "il resto dei nuovi annunci non è stato inviato su Telegram in questo giro "
            "(restano comunque salvati nel DB)."
        )


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


if __name__ == "__main__":
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
                print(f"[ERRORE] Ricerca '{query}' su {marketplace} fallita, salto: {exc}")

    print(f"\n=== Ricerche completate: {len(all_new_listings)} annunci nuovi in totale, notifica in corso ===")
    notify_new_listings(all_new_listings)
