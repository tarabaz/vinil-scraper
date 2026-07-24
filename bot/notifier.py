"""Invio messaggi al bot Telegram configurato in .env."""

import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def send_message(text: str, parse_mode: str | None = None, chat_id: str | int | None = None) -> None:
    target_chat_id = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not target_chat_id:
        sys.exit("Errore: TELEGRAM_BOT_TOKEN e/o TELEGRAM_CHAT_ID non impostati in .env")

    data = {"chat_id": target_chat_id, "text": text}
    if parse_mode:
        data["parse_mode"] = parse_mode

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    response = requests.post(url, data=data, timeout=10)
    response.raise_for_status()
