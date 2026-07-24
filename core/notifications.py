"""Traccia quali annunci sono già stati notificati a quale utente.

Serve a due scopi: evitare di rimandare due volte lo stesso annuncio allo
stesso utente, e permettere il controllo "a ritroso" sul DB già scansionato
quando un utente aggiunge/attiva una parola nei propri filtri personali
(scripts.collect.notify_backlog_for_user) — senza questa tabella, riattivare
più volte lo stesso filtro rimanderebbe più volte gli stessi annunci."""

import sqlite3
from datetime import datetime, timezone


def has_been_notified(conn: sqlite3.Connection, chat_id: int, source: str, external_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM user_notifications WHERE chat_id = ? AND source = ? AND external_id = ?",
        (chat_id, source, external_id),
    ).fetchone()
    return row is not None


def mark_notified(conn: sqlite3.Connection, chat_id: int, source: str, external_id: str) -> None:
    conn.execute(
        """
        INSERT INTO user_notifications (chat_id, source, external_id, notified_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(chat_id, source, external_id) DO NOTHING
        """,
        (chat_id, source, external_id, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
