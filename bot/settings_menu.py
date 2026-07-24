"""Menu impostazioni interattivo su Telegram (bottoni inline).

A differenza di bot/notifier.py e degli script in scripts/, questo è un
PROCESSO SEPARATO E PERSISTENTE: resta in ascolto degli aggiornamenti
Telegram in continuo (long polling), non uno script lanciato periodicamente.
Va avviato a parte (es. in un terminale/servizio dedicato), non dallo
scheduler dei collector.

Impostazioni gestite: marketplace abilitati, tipo di ricerca (lotti/singoli),
categoria eBay, e le parole chiave di ricerca/esclusione (sospendibili e
aggiungibili). Le impostazioni più complesse per singolo marketplace (ordina
per/distanza/asta per eBay; regione/provincia/spedizione per Subito) sono
rimandate a un passaggio successivo — alcune (regione/provincia) non si
prestano bene a bottoni e servirebbe testo libero, un problema di design
diverso."""

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

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

MARKETPLACES = {"ebay": "eBay", "subito": "Subito"}
SEARCH_MODES = {"lotti": "Lotti", "singoli": "Singoli"}
EBAY_CATEGORY_OPTIONS = ["Vinili", "Tutte le categorie"]

DEFAULT_ENABLED_MARKETPLACES = ["ebay", "subito"]
DEFAULT_SEARCH_MODES = ["lotti", "singoli"]
DEFAULT_EBAY_CATEGORY = "Vinili"

MAIN_MENU_TEXT = "⚙️ Impostazioni vinil-scraper"
KEYWORD_CATEGORIES_TEXT = "🔑 Parole chiave — scegli categoria:"

# chat_id -> chiave impostazione a cui aggiungere la prossima parola scritta
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


def build_menu_markup() -> InlineKeyboardMarkup:
    """Costruisce la tastiera del menu principale leggendo lo stato attuale."""
    enabled_marketplaces = get_setting("marketplaces.enabled", DEFAULT_ENABLED_MARKETPLACES)
    search_modes = get_setting("search.modes", DEFAULT_SEARCH_MODES)
    ebay_category = get_setting("marketplace.ebay.category", DEFAULT_EBAY_CATEGORY)

    rows = [[InlineKeyboardButton("— Marketplace —", callback_data="noop")]]
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

    rows.append([InlineKeyboardButton("🔑 Parole chiave", callback_data="kwmain")])

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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(MAIN_MENU_TEXT, reply_markup=build_menu_markup())


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "noop":
        return

    if data == "home":
        await query.edit_message_text(MAIN_MENU_TEXT, reply_markup=build_menu_markup())
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
        _pending_add[update.effective_chat.id] = category_key
        label = KEYWORD_CATEGORIES[category_key][2]
        await query.message.reply_text(f"Scrivimi la nuova parola chiave da aggiungere a '{label}'.")
        return

    # Impostazioni del menu principale (mp/mode/ebaycat)
    handle_callback_data(data)
    await query.edit_message_reply_markup(reply_markup=build_menu_markup())


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cattura il testo inviato dopo aver premuto '+ Aggiungi nuova' su una categoria."""
    chat_id = update.effective_chat.id
    category_key = _pending_add.pop(chat_id, None)
    if category_key is None:
        return

    setting_key, defaults_fn, label = KEYWORD_CATEGORIES[category_key]
    new_keyword = (update.message.text or "").strip()
    if not new_keyword:
        return

    add_keyword(setting_key, new_keyword, defaults=defaults_fn())
    await update.message.reply_text(
        f"Aggiunta a '{label}': \"{new_keyword}\"",
        reply_markup=build_keyword_list_markup(category_key),
    )


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit("Errore: TELEGRAM_BOT_TOKEN non impostato in .env")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("impostazioni", start))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(tg_filters.TEXT & ~tg_filters.COMMAND, handle_text))

    print("Bot impostazioni avviato (long polling). Premi Ctrl+C per fermarlo.")
    app.run_polling()


if __name__ == "__main__":
    main()
