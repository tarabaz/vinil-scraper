"""Storage SQLite per gli annunci raccolti, con dedup per (source, external_id)
e pulizia automatica degli annunci non più visti da tempo (last_seen_at)."""

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "listings.db"
RETENTION_HOURS = float(os.getenv("RETENTION_HOURS", "48"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    price REAL,
    currency TEXT,
    url TEXT,
    image_url TEXT,
    listed_at TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT,
    UNIQUE(source, external_id)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    chat_id INTEGER PRIMARY KEY,
    username TEXT,
    is_admin INTEGER NOT NULL DEFAULT 0,
    approved INTEGER NOT NULL DEFAULT 0,
    requested_at TEXT,
    approved_at TEXT
);

CREATE TABLE IF NOT EXISTS user_notifications (
    chat_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    notified_at TEXT NOT NULL,
    PRIMARY KEY (chat_id, source, external_id)
);
"""


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Aggiunge last_seen_at ai database creati prima di questa colonna,
    inizializzandolo con first_seen_at per le righe già esistenti."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(listings)")}
    if "last_seen_at" not in columns:
        conn.execute("ALTER TABLE listings ADD COLUMN last_seen_at TEXT")
        conn.execute("UPDATE listings SET last_seen_at = first_seen_at WHERE last_seen_at IS NULL")
        conn.commit()


def get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate_schema(conn)
    return conn


def insert_listing(
    conn: sqlite3.Connection,
    *,
    source: str,
    external_id: str,
    category: str,
    title: str,
    price: float | None = None,
    currency: str | None = None,
    url: str | None = None,
    image_url: str | None = None,
    listed_at: str | None = None,
) -> bool:
    """Inserisce un annuncio, o se già presente aggiorna solo last_seen_at
    (nessun altro campo viene toccato). Ritorna True se nuovo, False se già
    presente (duplicato, ma "ravvivato" come ancora attivo)."""
    now = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute(
        """
        INSERT INTO listings
            (source, external_id, category, title, price, currency, url, image_url, listed_at, first_seen_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, external_id) DO UPDATE SET last_seen_at = excluded.last_seen_at
        RETURNING (first_seen_at = ?) AS is_new
        """,
        (source, external_id, category, title, price, currency, url, image_url, listed_at, now, now, now),
    )
    is_new = bool(cursor.fetchone()[0])
    conn.commit()
    return is_new


def cleanup_old_listings(conn: sqlite3.Connection, retention_hours: float = RETENTION_HOURS) -> int:
    """Rimuove gli annunci non visti (last_seen_at) da più di retention_hours.
    Un annuncio ancora attivo viene 'ravvivato' a ogni esecuzione da insert_listing,
    quindi non viene mai rimosso finché continua a comparire nei risultati.
    Ritorna il numero di righe rimosse."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=retention_hours)).isoformat()
    cursor = conn.execute("DELETE FROM listings WHERE last_seen_at < ?", (cutoff,))
    conn.commit()
    return cursor.rowcount


if __name__ == "__main__":
    conn = get_connection()

    listing = dict(
        source="test",
        external_id="123",
        category="vinyl",
        title="Pink Floyd - The Dark Side of the Moon",
        price=15.0,
        currency="EUR",
        url="https://example.com/123",
    )

    first = insert_listing(conn, **listing)
    second = insert_listing(conn, **listing)

    print(f"Primo inserimento (atteso True): {first}")
    print(f"Secondo inserimento, stesso annuncio (atteso False, ma last_seen_at aggiornato): {second}")

    row = conn.execute(
        "SELECT first_seen_at, last_seen_at FROM listings WHERE source = ? AND external_id = ?",
        (listing["source"], listing["external_id"]),
    ).fetchone()
    print(f"first_seen_at: {row[0]}")
    print(f"last_seen_at:  {row[1]} (deve essere >= first_seen_at)")

    removed = cleanup_old_listings(conn, retention_hours=0)
    print(f"\nPulizia con retention_hours=0 (rimuove tutto ciò che non è 'appena adesso'): {removed} righe rimosse")
