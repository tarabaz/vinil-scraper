"""Utenti autorizzati a interagire col bot Telegram.

Un amministratore (identificato da TELEGRAM_CHAT_ID in .env) approva le
richieste di accesso; gli utenti approvati possono impostare solo i propri
filtri personali, non le impostazioni globali (marketplace, parole di
ricerca, blacklist) — quelle restano riservate all'amministratore."""

import os
from datetime import datetime, timezone

from dotenv import load_dotenv

from core.db import get_connection

load_dotenv()

ADMIN_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_admin_registered() -> None:
    """Registra l'amministratore (da TELEGRAM_CHAT_ID in .env) se non è già
    a database. Idempotente, va chiamata a ogni avvio di bot/script."""
    if not ADMIN_CHAT_ID:
        return
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO users (chat_id, username, is_admin, approved, requested_at, approved_at)
        VALUES (?, ?, 1, 1, ?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET is_admin = 1, approved = 1
        """,
        (int(ADMIN_CHAT_ID), "admin", _now(), _now()),
    )
    conn.commit()
    conn.close()


def get_user(chat_id: int) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT chat_id, username, is_admin, approved FROM users WHERE chat_id = ?", (chat_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return {"chat_id": row[0], "username": row[1], "is_admin": bool(row[2]), "approved": bool(row[3])}


def is_admin(chat_id: int) -> bool:
    user = get_user(chat_id)
    return bool(user and user["is_admin"])


def is_approved(chat_id: int) -> bool:
    user = get_user(chat_id)
    return bool(user and user["approved"])


def request_access(chat_id: int, username: str | None) -> None:
    """Registra una richiesta di accesso in sospeso. Non fa nulla se
    l'utente esiste già (approvato o già in attesa)."""
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO users (chat_id, username, is_admin, approved, requested_at)
        VALUES (?, ?, 0, 0, ?)
        ON CONFLICT(chat_id) DO NOTHING
        """,
        (chat_id, username, _now()),
    )
    conn.commit()
    conn.close()


def approve_user(chat_id: int) -> None:
    conn = get_connection()
    conn.execute("UPDATE users SET approved = 1, approved_at = ? WHERE chat_id = ?", (_now(), chat_id))
    conn.commit()
    conn.close()


def reject_user(chat_id: int) -> None:
    """Rifiuta/rimuove un utente. Non permette mai di rimuovere un admin."""
    conn = get_connection()
    conn.execute("DELETE FROM users WHERE chat_id = ? AND is_admin = 0", (chat_id,))
    conn.commit()
    conn.close()


def list_pending() -> list[dict]:
    conn = get_connection()
    rows = conn.execute("SELECT chat_id, username FROM users WHERE approved = 0").fetchall()
    conn.close()
    return [{"chat_id": r[0], "username": r[1]} for r in rows]


def list_approved() -> list[dict]:
    conn = get_connection()
    rows = conn.execute("SELECT chat_id, username, is_admin FROM users WHERE approved = 1").fetchall()
    conn.close()
    return [{"chat_id": r[0], "username": r[1], "is_admin": bool(r[2])} for r in rows]
