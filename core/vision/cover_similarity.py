"""Confronto visivo tra la foto reale di un disco e la copertina di
riferimento Discogs di un candidato, via CLIP (modello di embedding
immagine locale — gira sulla stessa GPU della vision testuale, non serve
nessuna API cloud). È un segnale indipendente dal testo letto (OCR): due
copertine della stessa release restano visivamente simili anche se il
testo è letto male o manca del tutto, e viceversa un testo che sembra
combaciare ma la copertina è chiaramente un'altra è un segnale forte di
abbinamento sbagliato (es. codice catalogo/barcode riusato o refuso per
un disco completamente diverso).

Se le dipendenze (torch, transformers, Pillow) o il modello non sono
disponibili — non installate, niente connessione per scaricare il
checkpoint la prima volta, ecc. — tutte le funzioni ritornano None invece
di sollevare un'eccezione: il confronto visivo è un'aggiunta opzionale,
la pipeline continua a funzionare solo su testo come prima se manca."""

import io

CLIP_MODEL_NAME = "openai/clip-vit-base-patch32"  # leggero, sufficiente per un confronto di somiglianza copertine

_model = None
_processor = None
_load_failed = False


def _load_model():
    """Carica il modello CLIP una sola volta per processo. La primissima
    chiamata scarica il checkpoint da HuggingFace se non è già in cache
    locale (qualche centinaio di MB, serve una connessione quella volta
    sola). Ritorna (model, processor) o (None, None) se non disponibile."""
    global _model, _processor, _load_failed
    if _load_failed:
        return None, None
    if _model is not None:
        return _model, _processor
    try:
        import torch
        from transformers import CLIPModel, CLIPProcessor

        _model = CLIPModel.from_pretrained(CLIP_MODEL_NAME)
        _processor = CLIPProcessor.from_pretrained(CLIP_MODEL_NAME)
        _model.eval()
        if torch.cuda.is_available():
            _model = _model.to("cuda")
    except Exception as exc:
        print(f"[ERRORE] modello CLIP non disponibile, confronto visivo copertine disattivato: {exc}")
        _load_failed = True
        return None, None
    return _model, _processor


def get_image_embedding(image_bytes: bytes):
    """Embedding CLIP normalizzato di un'immagine (per il confronto di
    somiglianza coseno). None se il modello non è disponibile o
    l'immagine non è leggibile."""
    model, processor = _load_model()
    if model is None:
        return None
    try:
        import torch
        from PIL import Image

        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        inputs = processor(images=image, return_tensors="pt")
        if next(model.parameters()).is_cuda:
            inputs = {k: v.to("cuda") for k, v in inputs.items()}
        with torch.no_grad():
            features = model.get_image_features(**inputs)
        return (features / features.norm(dim=-1, keepdim=True)).cpu()
    except Exception as exc:
        print(f"[ERRORE] calcolo embedding immagine fallito: {exc}")
        return None


def cosine_similarity(embedding_a, embedding_b) -> float | None:
    """Somiglianza coseno tra due embedding (1.0 = identiche, -1.0 =
    opposte). None se uno dei due manca (embedding non calcolabile)."""
    if embedding_a is None or embedding_b is None:
        return None
    return float((embedding_a @ embedding_b.T).item())
