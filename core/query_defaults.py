"""Parole chiave di ricerca di default, usate per inizializzare le
impostazioni modificabili a runtime (menu Telegram) al primo utilizzo.

Nessun artista specifico di default: sono ricerche generiche per trovare
vinili/lotti in generale. Se vuoi cercare un artista preciso, aggiungilo tu
manualmente dal bot ("🔑 Parole chiave" → categoria → "+ Aggiungi nuova")."""

DEFAULT_GENRE_QUERIES = {
    "ebay": [
        "vinyl record",
        "vinyl LP",
        "vinile 33 giri",
    ],
    "subito": [
        "vinile",
        "disco vinile",
        "LP vinile",
    ],
}

DEFAULT_LOT_QUERIES = {
    "ebay": ["vinyl record lot", "lotto vinili", "vinyl collection"],
    "subito": ["lotto vinili", "lotto dischi vinile", "collezione vinili"],
}
