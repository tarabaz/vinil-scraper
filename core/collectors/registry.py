"""Registro dei collector disponibili per marketplace. Aggiungere un nuovo
marketplace = un nuovo file collector + una riga qui."""

from core.collectors.ebay import EbayCollector
from core.collectors.subito import SubitoCollector

REGISTRY = {
    "ebay": EbayCollector,
    "subito": SubitoCollector,
}
