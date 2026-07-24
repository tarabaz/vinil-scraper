"""Menu impostazioni interattivo su Telegram (bottoni inline).

A differenza di bot/notifier.py e degli script in scripts/, questo è un
PROCESSO SEPARATO E PERSISTENTE: resta in ascolto degli aggiornamenti
Telegram in continuo (long polling), non uno script lanciato periodicamente.
Va avviato a parte (es. in un terminale/servizio dedicato), non dallo
scheduler dei collector.

Impostazioni gestite in questo primo passaggio: marketplace abilitati, tipo
di ricerca (lotti/singoli), categoria eBay. Le impostazioni più complesse per
singolo marketplace (ordina per/distanza/asta per eBay; regione/provincia/
spedizione per Subito) sono rimandate a un passaggio successivo — alcune
(regione/provincia) non si prestano bene a bottoni e servirebbe testo libero,
un problema di design diverso."""

import os

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from core.settings import get_setting, set_setting

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

MARKETPLACES = {"ebay": "eBay", "subito": "Subito"}
SEARCH_MODES = {"lotti": "Lotti", "singoli": "Singoli"}
EBAY_CATEGORY_OPTIONS = ["Vinili", "Tutte le categorie"]

DEFAULT_ENABLED_MARKETPLACES = ["ebay", "subito"]
DEFAULT_SEARCH_MODES = ["lotti", "singoli"]
DEFAULT_EBAY_CATEGORY = "Vinili"


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
    """Costruisce la tastiera inline leggendo lo stato attuale delle impostazioni."""
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

    return InlineKeyboardMarkup(rows)


def handle_callback_data(data: str) -> None:
    """Applica l'azione codificata in un callback_data. Separata dalla parte
    async/Telegram per poter essere testata senza un bot reale in ascolto."""
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
    await update.message.reply_text("⚙️ Impostazioni vinil-scraper", reply_markup=build_menu_markup())


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    handle_callback_data(query.data)
    await query.edit_message_reply_markup(reply_markup=build_menu_markup())


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit("Errore: TELEGRAM_BOT_TOKEN non impostato in .env")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("impostazioni", start))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_callback))

    print("Bot impostazioni avviato (long polling). Premi Ctrl+C per fermarlo.")
    app.run_polling()


if __name__ == "__main__":
    main()
