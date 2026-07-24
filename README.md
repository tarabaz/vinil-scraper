# vinil-scraper

Tool che monitora marketplace (eBay, poi Subito e Vinted) per trovare lotti di
vinili sottoprezzati, li filtra con regole deterministiche, arricchisce i
prezzi via Discogs e in futuro userà un layer AI (vision, a cascata
locale/cloud) solo dove serve. Notifica i risultati su Telegram. In futuro
anche CD e carte Pokémon.

Il progetto è costruito un pezzo alla volta, partendo sempre dal pezzo più
piccolo che funziona.

## Stato attuale

- ✅ Bot Telegram (invio messaggi, con supporto HTML per link cliccabili)
- ✅ Storage SQLite con dedup per annuncio (`source` + `external_id`)
- ✅ Collector eBay (Browse API ufficiale), ricerca ristretta alla categoria
  vinili trovata dinamicamente via Taxonomy API
- ✅ Filtri a regole YAML per categoria (blacklist per parola intera, nessun
  tetto di prezzo assoluto)
- ✅ Client Discogs: ricerca per artista/titolo, ricerca per codice catalogo
  (con filtro opzionale paese/anno), prezzi suggeriti per condizione
- ✅ Pipeline end-to-end: eBay → filtri → dedup → notifica Telegram

**Non ancora implementato**: fase vision (locale via Ollama + escalation
cloud), calcolo del margine reale (prezzo annuncio vs prezzo Discogs),
contatore di spesa giornaliero, scheduling automatico ogni 10 minuti.

## Architettura

- **Collector** per fonte: eBay Browse API (fatto), poi Subito e Vinted via
  endpoint non ufficiali. Salvataggio in SQLite con dedup.
- **Filtri a regole** in YAML per categoria (blacklist, prezzo/pezzo, soglie):
  girano prima di qualsiasi AI. Niente whitelist per genere/artista di
  proposito: un venditore che non sa cosa vende non scrive l'artista nel
  titolo, quindi filtrare per genere scarterebbe proprio le occasioni nei
  lotti "anonimi" che interessano di più.
- **Arricchimento prezzi** via Discogs API (deterministico, niente AI). Il
  codice catalogo di un disco non è garantito univoco (la stessa etichetta
  europea può riusarlo in più paesi, le ristampe a volte riusano il codice
  originale): la ricerca ritorna tutti i candidati trovati, mai una media tra
  prezzi di edizioni diverse.
- **Fase vision a cascata** (da fare): prima modello locale via Ollama,
  escalation a modello cloud solo se bassa confidenza o valore stimato alto.
  Il modello legge il testo visibile nella foto (OCR-style) e cerca su
  Discogs per testo — il "riconoscimento" vero lo fa la ricerca testuale
  deterministica, non l'AI a naso.
- **Layer AI astratto** (da fare): provider e modello configurabili in
  `.env`, mai hardcoded.
- **Contatore di spesa giornaliero** (da fare): superata la soglia, la fase
  AI cloud si disattiva fino al giorno dopo.
- **Notifiche Telegram**: un messaggio per annuncio nuovo, con link cliccabile
  invece dell'URL per esteso. Per i lotti con più dischi riconosciuti: dettaglio
  completo (codice, paese/lingua, etichetta, prezzo Discogs, link alla release)
  solo per i dischi che superano la soglia di margine; gli altri riassunti in
  una riga sola.
- **Scheduling** (da fare): ogni 10 minuti + heartbeat giornaliero.

Il core è pensato per essere indipendente dalla categoria; vinili, CD e carte
Pokémon saranno plugin che definiscono le proprie regole e arricchimenti.

## Ambiente

Sviluppo in **WSL2** (Windows): scraper e scheduling sono più semplici da
gestire in ambiente Linux, e WSL2 supporta il passthrough GPU CUDA per Ollama
(in alternativa Ollama può girare su Windows nativo, raggiungibile da WSL2
via `localhost:11434`).

## Struttura del progetto

```
vinil-scraper/
├── .env.example              # template chiavi API (mai committare .env)
├── config/categories/        # regole YAML per categoria (es. vinyl.yaml)
├── core/
│   ├── db.py                 # storage SQLite + dedup
│   ├── filters.py            # filtri a regole (blacklist, prezzo)
│   └── collectors/
│       ├── ebay.py           # eBay Browse API (OAuth, ricerca, categoria)
│       └── discogs.py        # ricerca release + prezzi per condizione
├── bot/
│   ├── notifier.py           # invio messaggi Telegram
│   └── send_test_message.py  # test rapido del bot
└── scripts/
    └── collect_ebay.py       # pipeline: cerca → filtra → salva → notifica
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Compila le chiavi in .env (vedi sotto)
```

**`.env` non va mai committato** (è già in `.gitignore`); le chiavi vivono
solo lì.

### Chiavi necessarie

- **Telegram**: crea un bot con [@BotFather](https://t.me/BotFather), poi
  scrivi al bot e leggi il chat id da
  `https://api.telegram.org/bot<TOKEN>/getUpdates`.
- **eBay**: registrati su [developer.ebay.com](https://developer.ebay.com/),
  crea un keyset Production, sblocca il keyset con l'esenzione "I do not
  persist eBay data" (Application Keys → Notifications → Marketplace Account
  Deletion → toggle "Exempted").
- **Discogs**: token di accesso personale da
  [discogs.com/settings/developers](https://www.discogs.com/settings/developers)
  → "Genera token".

## Uso

Test del bot Telegram:

```bash
python -m bot.send_test_message
```

Test del collector eBay (ricerca + categoria vinili):

```bash
python -m core.collectors.ebay
```

Test del client Discogs (ricerca per titolo e per codice catalogo):

```bash
python -m core.collectors.discogs
```

Pipeline completa — cerca su eBay (più query: generi/artisti diretti + lotti
generici), applica i filtri, salva i nuovi annunci nel DB, notifica su
Telegram:

```bash
python -m scripts.collect_ebay
```

Il DB SQLite (`data/listings.db`) tiene traccia di cosa è già stato notificato,
quindi le esecuzioni successive segnalano solo i nuovi annunci.
