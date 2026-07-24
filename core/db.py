"""Storage SQLite per gli annunci raccolti, con dedup per (source, external_id)."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "listings.db"

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
    UNIQUE(source, external_id)
);
"""


def get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(SCHEMA)
    conn.commit()
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
    """Inserisce un annuncio. Ritorna True se nuovo, False se già presente (duplicato)."""
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO listings
            (source, external_id, category, title, price, currency, url, image_url, listed_at, first_seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source,
            external_id,
            category,
            title,
            price,
            currency,
            url,
            image_url,
            listed_at,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    return cursor.rowcount > 0


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
    print(f"Secondo inserimento, stesso annuncio (atteso False, è un duplicato): {second}")
