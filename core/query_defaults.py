"""Parole chiave di ricerca di default, usate per inizializzare le
impostazioni modificabili a runtime (menu Telegram) al primo utilizzo."""

DEFAULT_GENRE_QUERIES = {
    "ebay": [
        "AC/DC vinyl",
        "Metallica vinyl",
        "Nirvana vinyl",
        "Led Zeppelin vinyl",
        "Pink Floyd vinyl",
        "rock vinyl record",
        "metal vinyl record",
        "vinile pop italiano",
    ],
    "subito": [
        "AC/DC vinile",
        "Metallica vinile",
        "Nirvana vinile",
        "Led Zeppelin vinile",
        "Pink Floyd vinile",
        "vinile rock",
        "vinile metal",
        "vinile pop italiano",
    ],
}

DEFAULT_LOT_QUERIES = {
    "ebay": ["vinyl record lot", "lotto vinili", "vinyl collection"],
    "subito": ["lotto vinili", "lotto dischi vinile", "collezione vinili"],
}
