"""Filtri personali per utente: si applicano SOPRA la ricerca globale unica
(quella resta gestita solo dall'amministratore) e decidono solo quali
annunci notificare a un dato utente.

Nessuna parola impostata = nessuna restrizione (l'utente riceve tutto).
Appena l'utente abilita almeno una parola, riceve solo gli annunci il cui
titolo la contiene."""

from core.filters import contains_keyword
from core.keywords import add_keyword, get_keywords, toggle_keyword


def _key(chat_id: int) -> str:
    return f"user.{chat_id}.filter.keywords"


def get_user_keywords(chat_id: int) -> dict[str, bool]:
    return get_keywords(_key(chat_id), [])


def add_user_keyword(chat_id: int, keyword: str) -> dict[str, bool]:
    return add_keyword(_key(chat_id), keyword, defaults=[])


def toggle_user_keyword(chat_id: int, keyword: str) -> dict[str, bool]:
    return toggle_keyword(_key(chat_id), keyword, defaults=[])


def matches_user_filter(chat_id: int, title: str) -> bool:
    enabled = [kw for kw, on in get_user_keywords(chat_id).items() if on]
    if not enabled:
        return True
    title_lower = (title or "").lower()
    return any(contains_keyword(title_lower, kw) for kw in enabled)
