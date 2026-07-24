"""Tipo Listing condiviso da tutti i collector, indipendente dalla fonte.

Ogni collector implementa search() e restituisce sempre una lista di Listing,
così il resto della pipeline (filtri, DB, Discogs, vision, Telegram) non deve
sapere da quale marketplace viene un annuncio."""

from dataclasses import asdict, dataclass, field
from typing import Protocol


@dataclass
class Listing:
    source: str
    external_id: str
    title: str
    price: float | None = None
    currency: str | None = None
    url: str | None = None
    image_urls: list[str] = field(default_factory=list)
    listed_at: str | None = None
    # Campi specifici della fonte che non rientrano nello schema comune
    # (es. condizione, venditore) — non usati dalla pipeline generica.
    raw: dict = field(default_factory=dict)


class Collector(Protocol):
    """Interfaccia che ogni collector di marketplace deve implementare."""

    name: str

    def search(self, query: str, **settings) -> list["Listing"]: ...


def listing_to_dict(listing: Listing) -> dict:
    """Converte un Listing nel formato dict che il resto della pipeline
    (filters.py, db.py) si aspetta oggi. La colonna DB image_url è ancora
    singola: qui si usa la prima immagine, finché non aggiorniamo lo schema
    per la fase vision (che userà tutte le immagini da Listing.image_urls)."""
    data = asdict(listing)
    data["image_url"] = listing.image_urls[0] if listing.image_urls else None
    del data["image_urls"]
    del data["raw"]
    return data
