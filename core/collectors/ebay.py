"""Collector eBay: cerca annunci reali via Browse API (OAuth client credentials)."""

import base64
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

EBAY_APP_ID = os.getenv("EBAY_APP_ID")
EBAY_CERT_ID = os.getenv("EBAY_CERT_ID")

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"

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


def search_items(query: str, limit: int = 10, marketplace: str = "EBAY_IT") -> list[dict]:
    token = get_access_token()
    response = requests.get(
        SEARCH_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": marketplace,
        },
        params={"q": query, "limit": limit},
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
            }
        )
    return results


if __name__ == "__main__":
    items = search_items("vinyl record", limit=5)
    for item in items:
        print(f"{item['title']} — {item['price']} {item['currency']} — {item['url']}")
