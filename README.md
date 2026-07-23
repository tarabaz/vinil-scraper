# vinil-scraper

Tool che monitora marketplace (eBay, Subito, Vinted) per trovare lotti di vinili
sottoprezzati, li filtra con regole deterministiche, arricchisce i prezzi via
Discogs e usa un layer AI (vision, a cascata locale/cloud) solo dove serve.
Notifica i risultati su Telegram. In futuro: CD e carte Pokémon.

Il progetto è costruito un pezzo alla volta. Stato attuale: **bot Telegram di
test** (invio di un messaggio, nessuna logica di scraping ancora).

## Architettura (in costruzione)

- **Collector** per fonte: eBay Browse API (ufficiale) prima, poi Subito e
  Vinted via endpoint non ufficiali. Salvataggio in SQLite con dedup.
- **Filtri a regole** in YAML per categoria (blacklist, whitelist,
  prezzo/pezzo, soglie): girano prima di qualsiasi AI e scartano ~90% dei
  risultati.
- **Arricchimento prezzi** via Discogs API (deterministico, niente AI).
- **Fase vision a cascata**: prima modello locale via Ollama, escalation a
  modello cloud solo se bassa confidenza o valore stimato alto.
- **Layer AI astratto**: provider e modello configurabili in `.env`, mai
  hardcoded.
- **Contatore di spesa giornaliero**: superata la soglia, la fase AI cloud si
  disattiva fino al giorno dopo.
- **Notifiche Telegram** con foto + lista dischi riconosciuti + offerta
  massima.
- **Scheduling** ogni 10 minuti + heartbeat giornaliero.

Il core è pensato per essere indipendente dalla categoria; vinili, CD e carte
Pokémon saranno plugin che definiscono le proprie regole e arricchimenti.

## Ambiente

Sviluppo consigliato in **WSL2** (Windows): scraper e scheduling sono più
semplici da gestire in ambiente Linux, e WSL2 supporta il passthrough GPU
CUDA per Ollama (in alternativa Ollama può girare su Windows nativo,
raggiungibile da WSL2 via `localhost:11434`). Windows nativo resta comunque
un'opzione valida.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Compila TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID in .env
```

Per ottenere `TELEGRAM_BOT_TOKEN`: crea un bot con
[@BotFather](https://t.me/BotFather) su Telegram.
Per `TELEGRAM_CHAT_ID`: scrivi al bot, poi leggi il chat id dalla risposta di
`https://api.telegram.org/bot<TOKEN>/getUpdates`.

**`.env` non va mai committato** (è già in `.gitignore`); le chiavi vivono
solo lì.

## Uso

Invia un messaggio di test al bot Telegram:

```bash
python -m bot.send_test_message
```

Se tutto è configurato correttamente, riceverai un messaggio di test nella
chat collegata.
