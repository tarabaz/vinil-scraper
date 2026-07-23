"""Invia un messaggio di test al bot Telegram configurato in .env."""

import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def send_message(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        sys.exit("Errore: TELEGRAM_BOT_TOKEN e/o TELEGRAM_CHAT_ID non impostati in .env")

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    response = requests.post(
        url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10
    )
    response.raise_for_status()
    print("Messaggio inviato con successo.")


if __name__ == "__main__":
    send_message("Test: il bot di vinil-scraper funziona!")
