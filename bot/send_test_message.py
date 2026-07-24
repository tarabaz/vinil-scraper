"""Invia un messaggio di test al bot Telegram configurato in .env."""

from bot.notifier import send_message

if __name__ == "__main__":
    send_message("Test: il bot di vinil-scraper funziona!")
    print("Messaggio inviato con successo.")
