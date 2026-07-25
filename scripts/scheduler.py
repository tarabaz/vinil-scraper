"""Esegue run_collection() a intervalli regolari.

Processo persistente, come bot/settings_menu.py: va lasciato aperto in un
terminale a parte (o avviato come servizio) — non è collegato al bot
Telegram, gira per conto suo e usa bot.notifier per mandare i messaggi,
quindi funziona anche se bot.settings_menu.py non è in esecuzione (serve
solo per i comandi interattivi come /cerca manuale e la gestione filtri)."""

import time
from datetime import datetime

from scripts.collect import run_collection

INTERVAL_SECONDS = 3600  # un'ora


def main() -> None:
    print(f"Scheduler avviato: una scansione ogni {INTERVAL_SECONDS} secondi. Premi Ctrl+C per fermarlo.")
    while True:
        print(f"\n=== Scansione automatica — {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} ===")
        try:
            run_collection()
        except Exception as exc:
            print(f"[ERRORE] scansione fallita: {exc}")
        print(f"\nProssima scansione tra {INTERVAL_SECONDS} secondi...")
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
