"""Riconoscimento locale di un'immagine via un modello vision servito da
Ollama (es. qwen2.5vl). Primo livello della cascata AI prevista dal
progetto: locale prima, un'eventuale escalation cloud per bassa confidenza
è prevista per una fase successiva, non ancora implementata."""

import base64
import json
import os

import requests
from dotenv import load_dotenv

load_dotenv()

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "qwen2.5vl")

# Campi fissi, sempre gli stessi: la pipeline (DB, ricerca Discogs) ha
# bisogno di dati strutturati, non di una descrizione in prosa.
FIELDS = ["artist", "album_title", "label", "catalog_number", "barcode", "other_text"]

# Campi usati per riconoscere due letture come "lo stesso disco" (dedup,
# raggruppamento): esclude "other_text", testo libero che può variare leggermente
# anche quando il modello ripete la stessa identificazione (es. un frammento
# diverso di tracklist letto ogni volta) — non è un segnale di un disco diverso.
IDENTITY_FIELDS = [f for f in FIELDS if f != "other_text"]

# Una foto di un lotto può mostrare più copertine/dischi insieme (es. 4
# fronti in una foto, i rispettivi 4 retri nella foto successiva) — il
# modello deve poter restituire un elemento per ogni disco visibile, non
# uno solo.
PROMPT = (
    "Guarda questa immagine, che può mostrare UNO o PIÙ dischi in vinile "
    "(es. più copertine, retri o etichette affiancati nella stessa foto di un lotto). "
    "Rispondi SOLO con un array JSON: un oggetto per ogni disco distinto visibile "
    "nell'immagine (se ce n'è uno solo, un array con un solo elemento; se non ne vedi "
    "nessuno, un array vuoto []). Ogni oggetto deve avere esattamente questi campi: "
    '"artist", "album_title", "label", "catalog_number", "barcode", "other_text". '
    "Usa null per un campo se non è visibile — non inventare mai nulla. "
    "FORMATO OBBLIGATORIO: un oggetto separato per ogni disco, MAI un oggetto solo con "
    "liste come valori dei campi (es. mai \"album_title\": [\"X\", \"Y\"] — è sbagliato, "
    "servono due oggetti distinti uno con album_title \"X\" e uno con album_title \"Y\"). "
    "Se vedi più copertine ma non sei sicuro di quale artista corrisponda a quale album, "
    "lascia \"artist\" a null per quell'oggetto invece di abbinarlo a caso: un dato mancante "
    "va bene, un abbinamento sbagliato no. "
    '"catalog_number" e "barcode" sono DUE campi separati e vanno tenuti distinti: '
    "non scrivere mai il barcode dentro catalog_number o viceversa, e non unirli con "
    "due punti o altri separatori nello stesso valore. "
    '"other_text" è per eventuale altro testo utile non coperto dagli altri campi (es. tracklist), '
    "altrimenti null. Nessun testo fuori dall'array JSON."
)


def _empty_result(raw: str) -> dict:
    result = {field: None for field in FIELDS}
    result["raw_response"] = raw
    return result


def _parse_entry(entry, raw: str) -> dict:
    result = _empty_result(raw)
    if not isinstance(entry, dict):
        return result
    for field in FIELDS:
        value = entry.get(field)
        if isinstance(value, str) and value.strip().lower() not in ("", "null", "none", "n/a"):
            result[field] = value.strip()
    return result


def _entries_from_columnar(parsed: dict) -> list[dict] | None:
    """Su foto con più copertine affiancate il modello a volte risponde con
    UN oggetto solo dove ogni campo è una LISTA di valori (uno per disco,
    "a colonne") invece dell'array di oggetti richiesto dal prompt — es.
    {"artist": ["A", "B"], "album_title": ["X", "Y", "Z"], ...}. Se non
    gestito, questa forma viene scartata per intero (nessun campo è una
    stringa) anche quando contiene dati letti correttamente. Qui la
    "trasponiamo" in una lista di oggetti per disco, allineando per indice;
    liste di lunghezza diversa tra campi (mapping incompleto, es. meno
    artisti che album) lasciano None oltre la loro lunghezza invece di
    indovinare un abbinamento. Ritorna None se il dict non è in questa
    forma (nessun campo è una lista)."""
    list_fields = {f: parsed[f] for f in FIELDS if isinstance(parsed.get(f), list)}
    if not list_fields:
        return None

    count = max(len(v) for v in list_fields.values())
    entries = []
    for i in range(count):
        entry = {}
        for f in FIELDS:
            value = parsed.get(f)
            if isinstance(value, list):
                entry[f] = value[i] if i < len(value) else None
            else:
                entry[f] = value
        entries.append(entry)
    return entries


def recognize_image(image_bytes: bytes, prompt: str = PROMPT) -> list[dict]:
    """Manda un'immagine al modello vision locale via l'API di Ollama.
    Ritorna una LISTA di dict (uno per ogni disco riconosciuto nella foto —
    di solito uno, più di uno se la foto mostra più copertine insieme),
    ognuno con i campi FIELDS (None se non trovato) più "raw_response" per
    debug. Se il modello non risponde in JSON valido o non riconosce nulla,
    ritorna una lista con un solo elemento vuoto (mai una lista vuota, così
    il chiamante ha sempre almeno un "tentativo" da registrare)."""
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    response = requests.post(
        f"{OLLAMA_HOST}/api/generate",
        json={
            "model": OLLAMA_VISION_MODEL,
            "prompt": prompt,
            "images": [image_b64],
            "format": "json",
            "stream": False,
        },
        timeout=120,
    )
    response.raise_for_status()
    raw = response.json()["response"]

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return [_empty_result(raw)]

    if isinstance(parsed, dict):
        columnar = _entries_from_columnar(parsed)
        # il modello a volte risponde con un oggetto solo: o un singolo
        # disco (nessun campo è una lista), o più dischi "a colonne" (ogni
        # campo è una lista) — vedi _entries_from_columnar
        parsed = columnar if columnar is not None else [parsed]
    if not isinstance(parsed, list):
        return [_empty_result(raw)]

    results = [_parse_entry(entry, raw) for entry in parsed]
    results = _dedupe_identical(results)
    return results or [_empty_result(raw)]


def _dedupe_identical(results: list[dict]) -> list[dict]:
    """Il modello a volte ripete la stessa identica lettura più volte nella
    stessa risposta (comportamento noto dei modelli generativi, non un
    disco fisico in più) — senza questo, un raggruppamento a valle
    (core.vision.matching) le scambierebbe per prova di copie fisiche
    duplicate nella stessa foto. Tiene solo la prima occorrenza di ogni
    combinazione identica di campi (IDENTITY_FIELDS, non FIELDS per
    intero: "other_text" può variare leggermente anche per la stessa
    identificazione, senza per questo essere un disco diverso)."""
    seen = set()
    deduped = []
    for r in results:
        key = tuple(r.get(f) for f in IDENTITY_FIELDS)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    return deduped
