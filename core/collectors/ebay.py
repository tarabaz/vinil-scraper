"""Collector eBay: cerca annunci reali via Browse API (OAuth client credentials)."""

import base64
import os
import time

import requests
from dotenv import load_dotenv

from core.collectors.base import Listing

load_dotenv()

EBAY_APP_ID = os.getenv("EBAY_APP_ID")
EBAY_CERT_ID = os.getenv("EBAY_CERT_ID")

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
CATEGORY_TREE_ID_URL = "https://api.ebay.com/commerce/taxonomy/v1/get_default_category_tree_id"
CATEGORY_SUGGESTIONS_URL_TEMPLATE = (
    "https://api.ebay.com/commerce/taxonomy/v1/category_tree/{tree_id}/get_category_suggestions"
)

_token_cache = {"access_token": None, "expires_at": 0.0}


def get_access_token() -> str:
    if _token_cache["access_token"] and time.time() < _token_cache["expires_at"]:
        return _token_cache["access_token"]

    if not EBAY_APP_ID or not EBAY_CERT_ID:
        raise SystemExit("Errore: EBAY_APP_ID e/o EBAY_CERT_ID non impostati in .env")

    credentials = base64.b64encode(f"{EBAY_APP_ID}:{EBAY_CERT_ID}".encode()).decode()
    response = requests.post(
        TOKEN_URL,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {credentials}",
        },
        data={
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        },
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()

    _token_cache["access_token"] = payload["access_token"]
    _token_cache["expires_at"] = time.time() + payload["expires_in"] - 60
    return _token_cache["access_token"]


def find_category_id(query: str, marketplace: str = "EBAY_IT") -> str | None:
    """Cerca l'ID di categoria eBay più adatto per una query (es. 'Vinyl Records'),
    senza doverlo scrivere fisso e rischiare di sbagliarlo per marketplace."""
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": marketplace,
    }

    tree_response = requests.get(
        CATEGORY_TREE_ID_URL,
        headers=headers,
        params={"marketplace_id": marketplace},
        timeout=10,
    )
    tree_response.raise_for_status()
    tree_id = tree_response.json()["categoryTreeId"]

    suggestions_response = requests.get(
        CATEGORY_SUGGESTIONS_URL_TEMPLATE.format(tree_id=tree_id),
        headers=headers,
        params={"q": query},
        timeout=10,
    )
    suggestions_response.raise_for_status()
    suggestions = suggestions_response.json().get("categorySuggestions", [])

    if not suggestions:
        return None
    return suggestions[0]["category"]["categoryId"]


def search_items(
    query: str,
    limit: int = 10,
    marketplace: str = "EBAY_IT",
    category_ids: str | None = None,
) -> list[dict]:
    token = get_access_token()
    params = {"q": query, "limit": limit}
    if category_ids:
        params["category_ids"] = category_ids

    response = requests.get(
        SEARCH_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": marketplace,
        },
        params=params,
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()

    results = []
    for item in payload.get("itemSummaries", []):
        price = item.get("price") or {}
        image = item.get("image") or {}
        results.append(
            {
                "source": "ebay",
                "external_id": item["itemId"],
                "title": item.get("title"),
                "price": float(price["value"]) if "value" in price else None,
                "currency": price.get("currency"),
                "url": item.get("itemWebUrl"),
                "image_url": image.get("imageUrl"),
                "listed_at": item.get("itemCreationDate"),
            }
        )
    return results


def _to_listing(item: dict) -> Listing:
    return Listing(
        source=item["source"],
        external_id=item["external_id"],
        title=item["title"],
        price=item["price"],
        currency=item["currency"],
        url=item["url"],
        image_urls=[item["image_url"]] if item.get("image_url") else [],
        listed_at=item.get("listed_at"),
    )


class EbayCollector:
    """Adatta le funzioni eBay esistenti all'interfaccia comune Collector."""

    name = "ebay"

    def search(self, query: str, **settings) -> list[Listing]:
        limit = settings.get("limit", 50)
        marketplace = settings.get("marketplace", "EBAY_IT")
        category_ids = settings.get("category_ids")
        items = search_items(query, limit=limit, marketplace=marketplace, category_ids=category_ids)
        return [_to_listing(item) for item in items]


if __name__ == "__main__":
    category_id = find_category_id("Vinyl Records")
    print(f"ID categoria trovato per 'Vinyl Records': {category_id}")

    items = search_items("vinyl record", limit=5, category_ids=category_id)
    for item in items:
        print(f"{item['title']} — {item['price']} {item['currency']} — {item['url']}")
