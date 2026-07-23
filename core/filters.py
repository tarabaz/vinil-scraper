"""Filtri a regole (YAML) per categoria: girano prima di qualsiasi AI, scartano il rumore."""

from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config" / "categories"


def load_rules(category: str) -> dict:
    path = CONFIG_DIR / f"{category}.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def passes_filters(listing: dict, rules: dict) -> tuple[bool, str | None]:
    """Ritorna (True, None) se l'annuncio passa i filtri, altrimenti (False, motivo dello scarto)."""
    title = (listing.get("title") or "").lower()

    for keyword in rules.get("blacklist_keywords", []):
        if keyword.lower() in title:
            return False, f"blacklist: '{keyword}'"

    whitelist = rules.get("whitelist_keywords", [])
    if whitelist and not any(keyword.lower() in title for keyword in whitelist):
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
