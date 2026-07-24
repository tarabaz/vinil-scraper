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

PROMPT = (
    "Guarda questa immagine di un disco in vinile (copertina, retro o etichetta). "
    "Rispondi SOLO con un oggetto JSON con esattamente questi campi: "
    '"artist", "album_title", "label", "catalog_number", "barcode", "other_text". '
    "Usa null per un campo se non è visibile in questa immagine — non inventare mai nulla. "
    '"other_text" è per eventuale altro testo utile non coperto dagli altri campi (es. tracklist), '
    "altrimenti null. Nessun testo fuori dal JSON."
)


def recognize_image(image_bytes: bytes, prompt: str = PROMPT) -> dict:
    """Manda un'immagine al modello vision locale via l'API di Ollama.
    Ritorna un dict con i campi FIELDS (None se il modello non li ha
    trovati/non ha risposto in JSON valido) più "raw_response" con il testo
    grezzo, tenuto per debug ma non pensato per essere mostrato all'utente."""
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

    result = {field: None for field in FIELDS}
    result["raw_response"] = raw
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return result

    for field in FIELDS:
        value = parsed.get(field)
        if isinstance(value, str) and value.strip().lower() not in ("", "null", "none", "n/a"):
            result[field] = value.strip()
    return result
