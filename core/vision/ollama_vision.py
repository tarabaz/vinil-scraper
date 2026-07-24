"""Riconoscimento locale di un'immagine via un modello vision servito da
Ollama (es. qwen2.5vl). Primo livello della cascata AI prevista dal
progetto: locale prima, un'eventuale escalation cloud per bassa confidenza
è prevista per una fase successiva, non ancora implementata."""

import base64
import os

import requests
from dotenv import load_dotenv

load_dotenv()

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "qwen2.5vl")

PROMPT = (
    "Descrivi questa immagine di un disco in vinile o della sua copertina/etichetta. "
    "Trascrivi tutto il testo visibile: artista, titolo dell'album, etichetta discografica, "
    "codice catalogo, codice a barre. Se non vedi qualcosa, dillo esplicitamente invece di inventarlo."
)


def recognize_image(image_bytes: bytes, prompt: str = PROMPT) -> str:
    """Manda un'immagine al modello vision locale via l'API di Ollama,
    ritorna la risposta testuale grezza (nessun parsing strutturato qui:
    quello arriva in una fase successiva, insieme alla ricerca Discogs)."""
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    response = requests.post(
        f"{OLLAMA_HOST}/api/generate",
        json={
            "model": OLLAMA_VISION_MODEL,
            "prompt": prompt,
            "images": [image_b64],
            "stream": False,
        },
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["response"]
