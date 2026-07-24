"""Menu impostazioni interattivo su Telegram (bottoni inline).

A differenza di bot/notifier.py e degli script in scripts/, questo è un
PROCESSO SEPARATO E PERSISTENTE: resta in ascolto degli aggiornamenti
Telegram in continuo (long polling), non uno script lanciato periodicamente.
Va avviato a parte (es. in un terminale/servizio dedicato), non dallo
scheduler dei collector.

Multi-utente: la ricerca resta UNICA e globale, gestita solo
dall'amministratore (TELEGRAM_CHAT_ID in .env). Chiunque altro scriva al bot
deve prima essere approvato manualmente dall'amministratore; una volta
approvato può impostare solo i propri filtri personali ("🔍 I miei filtri"),
che decidono quali annunci della ricerca globale gli vengono notificati.

Impostazioni gestite (solo amministratore): marketplace abilitati, tipo di
ricerca (lotti/singoli), categoria eBay, parole chiave di ricerca/esclusione
globali, gestione utenti (approvazione/rimozione). Le impostazioni più
complesse per singolo marketplace (ordina per/distanza/asta per eBay;
regione/provincia/spedizione per Subito) sono rimandate a un passaggio
successivo — alcune (regione/provincia) non si prestano bene a bottoni e
servirebbe testo libero, un problema di design diverso."""

import asyncio
import os

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
)
from telegram.ext import filters as tg_filters

from core.filters import load_yaml_rules
from core.keywords import add_keyword, get_keywords, toggle_keyword
from core.query_defaults import DEFAULT_GENRE_QUERIES, DEFAULT_LOT_QUERIES
from core.settings import get_setting, set_setting
from core.user_filters import add_user_keyword, get_user_keywords, toggle_user_keyword
from core.users import (
    ADMIN_CHAT_ID,
    approve_user,
    ensure_admin_registered,
    get_user,
    is_admin,
    is_approved,
    list_approved,
    list_pending,
    reject_user,
    request_access,
)
from scripts.collect import run_collection

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

MARKETPLACES = {"ebay": "eBay", "subito": "Subito"}
SEARCH_MODES = {"lotti": "Lotti", "singoli": "Singoli"}
EBAY_CATEGORY_OPTIONS = ["Vinili", "Tutte le categorie"]

DEFAULT_ENABLED_MARKETPLACES = ["ebay", "subito"]
DEFAULT_SEARCH_MODES = ["lotti", "singoli"]
DEFAULT_EBAY_CATEGORY = "Vinili"

MAIN_MENU_TEXT = "⚙️ Impostazioni vinil-scraper"
KEYWORD_CATEGORIES_TEXT = "🔑 Parole chiave (ricerca globale) — scegli categoria:"
USERS_TEXT = "👥 Gestisci utenti"
MY_FILTERS_TEXT = (
    "🔍 I tuoi filtri personali\n"
    "La ricerca è unica per tutti. Se non abiliti nessuna parola qui sotto "
    "ricevi tutti i risultati; se ne abiliti almeno una, ricevi solo gli "
    "annunci il cui titolo la contiene.\n\n"
    "Parole chiave:"
)

# chat_id -> chiave impostazione (o "__personal__") a cui aggiungere la
# prossima parola scritta
_pending_add: dict[int, str] = {}


def _blacklist_defaults() -> list[str]:
    return load_yaml_rules("vinyl").get("blacklist_keywords", [])


# category_key -> (chiave impostazione, funzione per i default, etichetta)
KEYWORD_CATEGORIES = {
    "ebay_genre": ("search.queries.ebay.genre", lambda: DEFAULT_GENRE_QUERIES.get("ebay", []), "Ricerca eBay - generi"),
    "ebay_lot": ("search.queries.ebay.lot", lambda: DEFAULT_LOT_QUERIES.get("ebay", []), "Ricerca eBay - lotti"),
    "subito_genre": (
        "search.queries.subito.genre",
        lambda: DEFAULT_GENRE_QUERIES.get("subito", []),
        "Ricerca Subito - generi",
    ),
    "subito_lot": ("search.queries.subito.lot", lambda: DEFAULT_LOT_QUERIES.get("subito", []), "Ricerca Subito - lotti"),
    "blacklist": ("filters.vinyl.blacklist", _blacklist_defaults, "Blacklist (esclusioni)"),
}


def toggle_in_list(key: str, value: str, default: list[str]) -> list[str]:
    """Aggiunge/rimuove value dalla lista salvata sotto key. Ritorna la lista aggiornata."""
    current = get_setting(key, default)
    if value in current:
        updated = [v for v in current if v != value]
    else:
        updated = current + [value]
    set_setting(key, updated)
    return updated


def build_menu_markup(chat_id: int) -> InlineKeyboardMarkup:
    """Costruisce la tastiera del menu principale. Le sezioni di ricerca
    globale e gestione utenti sono visibili solo all'amministratore; "I miei
    filtri" è visibile a chiunque sia approvato."""
    rows: list[list[InlineKeyboardButton]] = []

    if is_admin(chat_id):
        enabled_marketplaces = get_setting("marketplaces.enabled", DEFAULT_ENABLED_MARKETPLACES)
        search_modes = get_setting("search.modes", DEFAULT_SEARCH_MODES)
        ebay_category = get_setting("marketplace.ebay.category", DEFAULT_EBAY_CATEGORY)

        rows.append([InlineKeyboardButton("— Marketplace —", callback_data="noop")])
        for key, label in MARKETPLACES.items():
            mark = "✅" if key in enabled_marketplaces else "☐"
            rows.append([InlineKeyboardButton(f"{mark} {label}", callback_data=f"mp:{key}")])

        rows.append([InlineKeyboardButton("— Tipo ricerca —", callback_data="noop")])
        for key, label in SEARCH_MODES.items():
            mark = "✅" if key in search_modes else "☐"
            rows.append([InlineKeyboardButton(f"{mark} {label}", callback_data=f"mode:{key}")])

        rows.append([InlineKeyboardButton(f"— Categoria eBay: {ebay_category} —", callback_data="noop")])
        for option in EBAY_CATEGORY_OPTIONS:
            mark = "●" if option == ebay_category else "○"
            rows.append([InlineKeyboardButton(f"{mark} {option}", callback_data=f"ebaycat:{option}")])

        rows.append([InlineKeyboardButton("🔑 Parole chiave (globali)", callback_data="kwmain")])
        rows.append([InlineKeyboardButton("🔍 Cerca ora", callback_data="searchnow")])
        rows.append([InlineKeyboardButton("👥 Gestisci utenti", callback_data="users")])

    rows.append([InlineKeyboardButton("🔍 I miei filtri", callback_data="myfilters")])

    return InlineKeyboardMarkup(rows)


def build_keyword_categories_markup() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(label, callback_data=f"kwcat:{key}")] for key, (_, _, label) in KEYWORD_CATEGORIES.items()]
    rows.append([InlineKeyboardButton("« Menu principale", callback_data="home")])
    return InlineKeyboardMarkup(rows)


def build_keyword_list_markup(category_key: str) -> InlineKeyboardMarkup:
    """Elenco delle parole chiave di una categoria, con checkbox per sospenderle/riattivarle."""
    setting_key, defaults_fn, _ = KEYWORD_CATEGORIES[category_key]
    keywords = get_keywords(setting_key, defaults_fn())

    rows = []
    for i, kw in enumerate(sorted(keywords.keys())):
        mark = "✅" if keywords[kw] else "☐"
        rows.append([InlineKeyboardButton(f"{mark} {kw}", callback_data=f"kwtoggle:{category_key}:{i}")])

    rows.append([InlineKeyboardButton("+ Aggiungi nuova", callback_data=f"kwadd:{category_key}")])
    rows.append([InlineKeyboardButton("« Categorie", callback_data="kwmain")])
    return InlineKeyboardMarkup(rows)


def build_my_filters_markup(chat_id: int) -> InlineKeyboardMarkup:
    keywords = get_user_keywords(chat_id)

    rows = []
    for i, kw in enumerate(sorted(keywords.keys())):
        mark = "✅" if keywords[kw] else "☐"
        rows.append([InlineKeyboardButton(f"{mark} {kw}", callback_data=f"myftoggle:{i}")])

    rows.append([InlineKeyboardButton("+ Aggiungi parola", callback_data="myfadd")])
    rows.append([InlineKeyboardButton("« Menu principale", callback_data="home")])
    return InlineKeyboardMarkup(rows)


def build_users_markup() -> InlineKeyboardMarkup:
    rows = []

    pending = list_pending()
    if pending:
        rows.append([InlineKeyboardButton("— Richieste in attesa —", callback_data="noop")])
        for user in pending:
            label = f"@{user['username']}" if user["username"] else str(user["chat_id"])
            rows.append(
                [
                    InlineKeyboardButton(f"✅ {label}", callback_data=f"approve:{user['chat_id']}"),
                    InlineKeyboardButton(f"❌ {label}", callback_data=f"reject:{user['chat_id']}"),
                ]
            )

    approved = [u for u in list_approved() if not u["is_admin"]]
    rows.append([InlineKeyboardButton("— Utenti approvati —", callback_data="noop")])
    if not approved:
        rows.append([InlineKeyboardButton("(nessuno)", callback_data="noop")])
    for user in approved:
        label = f"@{user['username']}" if user["username"] else str(user["chat_id"])
        rows.append([InlineKeyboardButton(f"🗑 Rimuovi {label}", callback_data=f"removeuser:{user['chat_id']}")])

    rows.append([InlineKeyboardButton("« Menu principale", callback_data="home")])
    return InlineKeyboardMarkup(rows)


def handle_callback_data(data: str) -> None:
    """Applica le azioni del menu principale (marketplace/tipo ricerca/categoria eBay).
    Separata dalla parte async/Telegram per poter essere testata senza un bot reale."""
    if data == "noop":
        return

    action, _, value = data.partition(":")

    if action == "mp":
        toggle_in_list("marketplaces.enabled", value, DEFAULT_ENABLED_MARKETPLACES)
    elif action == "mode":
        toggle_in_list("search.modes", value, DEFAULT_SEARCH_MODES)
    elif action == "ebaycat":
        set_setting("marketplace.ebay.category", value)


async def _request_access(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, username: str | None) -> None:
    """Registra la richiesta di accesso e avvisa l'amministratore, a meno che
    non ci sia già una richiesta in sospeso per questo utente."""
    already_pending = get_user(chat_id) is not None
    request_access(chat_id, username)

    if already_pending:
        await update.effective_message.reply_text(
            "La tua richiesta è già in attesa di approvazione dall'amministratore."
        )
        return

    await update.effective_message.reply_text(
        "👋 Richiesta di accesso inviata all'amministratore. Riceverai un messaggio appena verrà approvata."
    )

    if not ADMIN_CHAT_ID:
        return

    label = f"@{username}" if username else str(chat_id)
    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Approva", callback_data=f"approve:{chat_id}"),
                InlineKeyboardButton("❌ Rifiuta", callback_data=f"reject:{chat_id}"),
            ]
        ]
    )
    await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=f"Nuova richiesta di accesso al bot da {label} (chat_id {chat_id}).",
        reply_markup=markup,
    )


async def _run_search_now(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    """Esegue run_collection() (ricerca + filtri + notifica) su richiesta,
    invece di dover lanciare scripts.collect da terminale. Gira in un thread
    a parte (asyncio.to_thread): run_collection fa chiamate di rete
    bloccanti, farla girare direttamente nell'handler bloccherebbe l'intero
    bot finché non finisce."""
    if not is_admin(chat_id):
        await context.bot.send_message(chat_id=chat_id, text="Solo l'amministratore può avviare la ricerca manualmente.")
        return

    await context.bot.send_message(chat_id=chat_id, text="🔍 Ricerca avviata, può richiedere qualche minuto...")
    try:
        summary = await asyncio.to_thread(run_collection)
    except Exception as exc:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Ricerca fallita: {exc}")
        return

    lines = [f"✅ Ricerca completata: {summary['new_listings']} annunci nuovi trovati."]
    if summary["errors"]:
        lines.append("\n⚠️ Errori:")
        lines += [f"- {e}" for e in summary["errors"]]
    await context.bot.send_message(chat_id=chat_id, text="\n".join(lines))


async def cerca_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run_search_now(update, context, update.effective_chat.id)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    username = update.effective_user.username if update.effective_user else None

    if not is_approved(chat_id):
        await _request_access(update, context, chat_id, username)
        return

    await update.message.reply_text(MAIN_MENU_TEXT, reply_markup=build_menu_markup(chat_id))


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    data = query.data

    # Le richieste di approvazione/rifiuto arrivano solo all'amministratore
    # (sono nel SUO messaggio di notifica), ma verifichiamo comunque.
    if data.startswith("approve:") or data.startswith("reject:"):
        if not is_admin(chat_id):
            return
        target_chat_id = int(data.split(":", 1)[1])
        if data.startswith("approve:"):
            approve_user(target_chat_id)
            await query.edit_message_text(f"✅ Utente {target_chat_id} approvato.")
            try:
                await context.bot.send_message(
                    chat_id=target_chat_id,
                    text="✅ Sei stato approvato! Scrivi /start per aprire il menu ed impostare i tuoi filtri.",
                )
            except Exception:
                pass
        else:
            reject_user(target_chat_id)
            await query.edit_message_text(f"❌ Richiesta di {target_chat_id} rifiutata.")
        return

    if not is_approved(chat_id):
        return

    if data == "noop":
        return

    if data == "home":
        await query.edit_message_text(MAIN_MENU_TEXT, reply_markup=build_menu_markup(chat_id))
        return

    if data == "myfilters":
        await query.edit_message_text(MY_FILTERS_TEXT, reply_markup=build_my_filters_markup(chat_id))
        return

    if data.startswith("myftoggle:"):
        index = int(data.split(":", 1)[1])
        sorted_keys = sorted(get_user_keywords(chat_id).keys())
        if 0 <= index < len(sorted_keys):
            toggle_user_keyword(chat_id, sorted_keys[index])
        await query.edit_message_text(MY_FILTERS_TEXT, reply_markup=build_my_filters_markup(chat_id))
        return

    if data == "myfadd":
        _pending_add[chat_id] = "__personal__"
        await query.message.reply_text("Scrivimi la parola chiave da aggiungere ai tuoi filtri personali.")
        return

    # Da qui in poi: sezioni riservate all'amministratore.
    if not is_admin(chat_id):
        return

    if data == "searchnow":
        await _run_search_now(update, context, chat_id)
        return

    if data == "users":
        await query.edit_message_text(USERS_TEXT, reply_markup=build_users_markup())
        return

    if data.startswith("removeuser:"):
        target_chat_id = int(data.split(":", 1)[1])
        reject_user(target_chat_id)
        await query.edit_message_text(USERS_TEXT, reply_markup=build_users_markup())
        return

    if data == "kwmain":
        await query.edit_message_text(KEYWORD_CATEGORIES_TEXT, reply_markup=build_keyword_categories_markup())
        return

    if data.startswith("kwcat:"):
        category_key = data.split(":", 1)[1]
        label = KEYWORD_CATEGORIES[category_key][2]
        await query.edit_message_text(f"🔑 {label}:", reply_markup=build_keyword_list_markup(category_key))
        return

    if data.startswith("kwtoggle:"):
        _, category_key, index_str = data.split(":", 2)
        setting_key, defaults_fn, label = KEYWORD_CATEGORIES[category_key]
        keywords = get_keywords(setting_key, defaults_fn())
        sorted_keys = sorted(keywords.keys())
        index = int(index_str)
        if 0 <= index < len(sorted_keys):
            toggle_keyword(setting_key, sorted_keys[index], defaults=defaults_fn())
        await query.edit_message_text(f"🔑 {label}:", reply_markup=build_keyword_list_markup(category_key))
        return

    if data.startswith("kwadd:"):
        category_key = data.split(":", 1)[1]
        _pending_add[chat_id] = category_key
        label = KEYWORD_CATEGORIES[category_key][2]
        await query.message.reply_text(f"Scrivimi la nuova parola chiave da aggiungere a '{label}'.")
        return

    # Impostazioni del menu principale (mp/mode/ebaycat)
    handle_callback_data(data)
    await query.edit_message_reply_markup(reply_markup=build_menu_markup(chat_id))


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cattura il testo inviato dopo aver premuto '+ Aggiungi' (globale, solo
    amministratore, o personale, chiunque sia approvato)."""
    chat_id = update.effective_chat.id
    if not is_approved(chat_id):
        return

    pending = _pending_add.pop(chat_id, None)
    if pending is None:
        return

    new_keyword = (update.message.text or "").strip()
    if not new_keyword:
        return

    if pending == "__personal__":
        add_user_keyword(chat_id, new_keyword)
        await update.message.reply_text(
            f'Aggiunta ai tuoi filtri personali: "{new_keyword}"',
            reply_markup=build_my_filters_markup(chat_id),
        )
        return

    if not is_admin(chat_id):
        return

    setting_key, defaults_fn, label = KEYWORD_CATEGORIES[pending]
    add_keyword(setting_key, new_keyword, defaults=defaults_fn())
    await update.message.reply_text(
        f"Aggiunta a '{label}': \"{new_keyword}\"",
        reply_markup=build_keyword_list_markup(pending),
    )


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit("Errore: TELEGRAM_BOT_TOKEN non impostato in .env")

    ensure_admin_registered()

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("impostazioni", start))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cerca", cerca_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(tg_filters.TEXT & ~tg_filters.COMMAND, handle_text))

    print("Bot impostazioni avviato (long polling). Premi Ctrl+C per fermarlo.")
    app.run_polling()


if __name__ == "__main__":
    main()
