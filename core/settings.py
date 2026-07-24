"""Impostazioni modificabili a runtime (es. dal futuro menu Telegram), salvate
nella tabella settings di SQLite.

Diverso da config/categories/*.yaml e config/marketplaces/*.yaml: quelli sono
schema statico (quali impostazioni esistono, editati da chi sviluppa, versionati
in git). Qui invece vive lo STATO attuale delle scelte dell'utente — cambia a
runtime, non richiede di toccare file."""

import json
from datetime import datetime, timezone

from core.db import get_connection


def get_setting(key: str, default=None):
    """Legge un'impostazione. Ritorna default se non è mai stata impostata."""
    conn = get_connection()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    if row is None:
        return default
    return json.loads(row[0])


def set_setting(key: str, value) -> None:
    """Scrive/aggiorna un'impostazione. value può essere qualsiasi tipo
    serializzabile in JSON (bool, stringa, numero, lista, dict)."""
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO settings (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (key, json.dumps(value), datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


if __name__ == "__main__":
    print(f"marketplaces.enabled (default): {get_setting('marketplaces.enabled', ['ebay'])}")

    set_setting("marketplaces.enabled", ["ebay", "subito"])
    print(f"marketplaces.enabled (dopo set): {get_setting('marketplaces.enabled')}")

    set_setting("marketplaces.enabled", ["ebay"])
    print(f"marketplaces.enabled (dopo un secondo set, deve sovrascrivere): {get_setting('marketplaces.enabled')}")
