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
        parsed = [parsed]  # il modello a volte risponde con un oggetto solo anche se l'array ha un elemento
    if not isinstance(parsed, list):
        return [_empty_result(raw)]

    results = [_parse_entry(entry, raw) for entry in parsed]
    return results or [_empty_result(raw)]
