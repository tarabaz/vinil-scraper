"""Parole chiave (di ricerca ed esclusione) modificabili a runtime, es. dal
menu Telegram. Ogni parola ha uno stato abilitata/sospesa — sospendere non
cancella, permette di riattivarla in seguito."""

from core.settings import get_setting, set_setting


def get_keywords(key: str, defaults: list[str]) -> dict[str, bool]:
    """Ritorna {parola: abilitata}. Al primo utilizzo la inizializza dai default."""
    current = get_setting(key)
    if current is None:
        current = {kw: True for kw in defaults}
        set_setting(key, current)
    return current


def enabled_keywords(key: str, defaults: list[str]) -> list[str]:
    """Solo le parole abilitate, come lista — quello che ricerche/filtri usano."""
    keywords = get_keywords(key, defaults)
    return [kw for kw, enabled in keywords.items() if enabled]


def toggle_keyword(key: str, keyword: str, defaults: list[str] | None = None) -> dict[str, bool]:
    """defaults va passato se la categoria potrebbe non essere ancora stata
    inizializzata (altrimenti si rischia di partire da un dizionario vuoto)."""
    keywords = get_keywords(key, defaults or [])
    if keyword in keywords:
        keywords[keyword] = not keywords[keyword]
        set_setting(key, keywords)
    return keywords


def add_keyword(key: str, keyword: str, defaults: list[str] | None = None) -> dict[str, bool]:
    keywords = get_keywords(key, defaults or [])
    keywords[keyword] = True
    set_setting(key, keywords)
    return keywords
