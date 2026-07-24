"""Fusione dei dischi rilevati in più foto dello stesso annuncio, e sistema di
confidenza per decidere quando un'identificazione è abbastanza affidabile.

Principio di fusione: due rilevazioni con lo stesso release_id trovate in foto
DIVERSE sono considerate lo stesso disco fisico fotografato più volte, e i loro
campi vengono uniti in un'unica voce (es. copertina da una foto, codice
catalogo dal retro in un'altra). Due rilevazioni con lo stesso release_id nella
STESSA foto sono invece copie fisiche reali (prova fisica: non si può
fotografare lo stesso oggetto due volte in un solo scatto) e restano voci
separate. In un lotto è comunque raro avere copie duplicate dello stesso
disco, quindi questa eccezione riguarda solo il caso in cui se ne trova prova
diretta nella stessa immagine.
"""

from dataclasses import dataclass, field

# Peso di ogni segnale nel punteggio di confidenza. Il codice catalogo e il
# barcode sono i segnali più affidabili (quasi univoci); il resto è di supporto.
SIGNAL_WEIGHTS = {
    "catalog_number": 0.5,
    "barcode": 0.5,
    "artist_title_text": 0.25,
    "label_match": 0.1,
    "year_match": 0.05,
    "clip_similarity": 0.15,
}

CONFIDENCE_HIGH = 0.5
CONFIDENCE_MEDIUM = 0.25


@dataclass
class Detection:
    """Una singola rilevazione grezza: un disco visto in una foto, con quello
    che si è riusciti a estrarre da quella foto soltanto."""

    photo_id: str
    release_id: int | None = None
    title: str | None = None
    catno: str | None = None
    country: str | None = None
    label: str | None = None
    signals: dict[str, bool | float] = field(default_factory=dict)


@dataclass
class Item:
    """Un disco fisico presunto, risultato della fusione di una o più
    rilevazioni che si ritiene siano lo stesso oggetto."""

    release_id: int | None
    title: str | None
    catno: str | None
    country: str | None
    label: str | None
    photo_ids: list[str]
    confidence_score: float
    confidence_level: str


def compute_confidence(signals: dict[str, bool | float]) -> tuple[float, str]:
    """Somma i pesi dei segnali presenti (True o valore numerico > 0).
    Ritorna (punteggio 0-1, livello 'alta'/'media'/'bassa')."""
    score = 0.0
    for name, value in signals.items():
        if not value:
            continue
        score += SIGNAL_WEIGHTS.get(name, 0.0)
    score = min(score, 1.0)

    if score >= CONFIDENCE_HIGH:
        level = "alta"
    elif score >= CONFIDENCE_MEDIUM:
        level = "media"
    else:
        level = "bassa"
    return score, level


def _merge_field(current: str | None, new: str | None) -> str | None:
    """Tiene il primo valore trovato; non sovrascrive con un vuoto."""
    return current if current is not None else new


def _merge_detections(detections: list[Detection]) -> Item:
    title = catno = country = label = None
    photo_ids = []
    merged_signals: dict[str, bool | float] = {}

    for d in detections:
        title = _merge_field(title, d.title)
        catno = _merge_field(catno, d.catno)
        country = _merge_field(country, d.country)
        label = _merge_field(label, d.label)
        photo_ids.append(d.photo_id)
        merged_signals.update(d.signals)

    score, level = compute_confidence(merged_signals)

    return Item(
        release_id=detections[0].release_id,
        title=title,
        catno=catno,
        country=country,
        label=label,
        photo_ids=photo_ids,
        confidence_score=score,
        confidence_level=level,
    )


def group_detections(detections: list[Detection]) -> list[Item]:
    """Raggruppa le rilevazioni in item (dischi fisici presunti). Vedi il
    docstring del modulo per la regola di fusione/eccezione."""
    unidentified = [d for d in detections if d.release_id is None]
    identified = [d for d in detections if d.release_id is not None]

    items: list[Item] = []

    by_release: dict[int, list[Detection]] = {}
    for d in identified:
        by_release.setdefault(d.release_id, []).append(d)

    for group in by_release.values():
        by_photo: dict[str, list[Detection]] = {}
        for d in group:
            by_photo.setdefault(d.photo_id, []).append(d)

        max_per_photo = max(len(v) for v in by_photo.values())
        if max_per_photo == 1:
            # Nessuna foto ha più di una rilevazione di questo release: è lo
            # stesso disco fotografato più volte, fondi tutto in un item.
            items.append(_merge_detections(group))
        else:
            # Prova fisica di copie multiple nella stessa foto: un item per
            # copia. Le rilevazioni delle altre foto vengono distribuite alle
            # copie in ordine di apparizione (senza modo certo di sapere quale
            # copia è quale tra foto diverse — caso raro e comunque best-effort).
            buckets: list[list[Detection]] = [[] for _ in range(max_per_photo)]
            for photo_detections in by_photo.values():
                for i, d in enumerate(photo_detections):
                    buckets[min(i, max_per_photo - 1)].append(d)
            for bucket in buckets:
                items.append(_merge_detections(bucket))

    for d in unidentified:
        items.append(_merge_detections([d]))

    return items
