"""Filtri a regole (YAML) per categoria: girano prima di qualsiasi AI, scartano il rumore."""

import re
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config" / "categories"


def load_yaml_rules(category: str) -> dict:
    """Regole statiche da file, così come scritte in config/categories/<categoria>.yaml."""
    path = CONFIG_DIR / f"{category}.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_rules(category: str) -> dict:
    """Regole per la categoria, con blacklist_keywords sovrascritta dalle
    parole abilitate a runtime (es. dal menu Telegram) — lo YAML resta la
    lista di default usata solo la prima volta."""
    from core.keywords import enabled_keywords  # import locale: evita dipendenza circolare a livello di modulo

    rules = load_yaml_rules(category)
    default_blacklist = rules.get("blacklist_keywords", [])
    rules["blacklist_keywords"] = enabled_keywords(f"filters.{category}.blacklist", default_blacklist)
    return rules


def _contains_keyword(text: str, keyword: str) -> bool:
    """Confronto per parola intera: 'stand' non deve scattare su 'standard'."""
    return re.search(rf"\b{re.escape(keyword.lower())}\b", text) is not None


def passes_filters(listing: dict, rules: dict) -> tuple[bool, str | None]:
    """Ritorna (True, None) se l'annuncio passa i filtri, altrimenti (False, motivo dello scarto)."""
    title = (listing.get("title") or "").lower()

    for keyword in rules.get("blacklist_keywords", []):
        if _contains_keyword(title, keyword):
            return False, f"blacklist: '{keyword}'"

    whitelist = rules.get("whitelist_keywords", [])
    if whitelist and not any(_contains_keyword(title, keyword) for keyword in whitelist):
        return False, "nessuna parola whitelist trovata nel titolo"

    price = listing.get("price")
    price_rules = rules.get("price", {})
    if price is not None:
        min_price = price_rules.get("min")
        max_price = price_rules.get("max")
        if min_price is not None and price < min_price:
            return False, f"prezzo troppo basso ({price} < {min_price})"
        if max_price is not None and price > max_price:
            return False, f"prezzo troppo alto ({price} > {max_price})"

    return True, None
