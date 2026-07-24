# vinil-scraper

Tool multi-marketplace (eBay, Subito, in futuro Vinted/Wallapop/Kleinanzeigen)
che monitora lotti di vinili sottoprezzati, li filtra con regole
deterministiche, arricchisce i prezzi via Discogs e in futuro userà un layer
AI (vision, a cascata locale/cloud) solo dove serve. Notifica i risultati su
Telegram, configurabile da un menu a bottoni nel bot stesso. In futuro anche
CD e carte Pokémon.

Il progetto è costruito un pezzo alla volta, partendo sempre dal pezzo più
piccolo che funziona.

## Stato attuale

- ✅ Bot Telegram (notifiche con link cliccabili HTML)
- ✅ Storage SQLite con dedup per annuncio (`source` + `external_id`) e
  **retention automatica**: gli annunci non più visti da `RETENTION_HOURS`
  (default 48h) vengono rimossi da soli; un annuncio ancora attivo non scade
  mai perché viene "ravvivato" a ogni esecuzione
- ✅ **Architettura multi-marketplace**: tipo `Listing` unificato e interfaccia
  `Collector` comune — la pipeline (filtri, DB, notifiche) non sa da quale
  marketplace viene un annuncio
- ✅ Collector eBay (Browse API ufficiale), categoria vinili trovata
  dinamicamente via Taxonomy API
- ✅ Collector Subito (scraping dati strutturati schema.org/JSON-LD) — ⚠️ non
  ufficiale, non ancora verificato contro il sito reale
- ✅ Filtri a regole YAML per categoria (blacklist per parola intera, nessun
  tetto di prezzo assoluto)
- ✅ Client Discogs: ricerca per artista/titolo, ricerca per codice catalogo
  (con filtro opzionale paese/anno — il codice non è garantito univoco tra
  edizioni), prezzi suggeriti per condizione
- ✅ **Menu impostazioni su Telegram** (bottoni inline, processo persistente
  separato): marketplace attivi, tipo ricerca (lotti/singoli), categoria
  eBay, e gestione delle **parole chiave** (di ricerca e di esclusione) —
  sospendibili/riattivabili con un tocco, aggiungibili scrivendo un messaggio
- ✅ **Multi-utente**: la ricerca resta unica e globale (impostazioni
  riservate all'amministratore, `TELEGRAM_CHAT_ID` in `.env`); chiunque
  altro scriva al bot deve essere approvato manualmente dall'amministratore
  (bottoni ✅/❌ diretti in chat), poi può impostare solo i propri **filtri
  personali** ("🔍 I miei filtri") che decidono quali annunci della ricerca
  globale gli vengono notificati — nessun filtro impostato = riceve tutto
- ✅ Fusione multi-foto e sistema di confidenza (`core/vision/matching.py`):
  la logica che unirà i dati letti da foto diverse dello stesso disco in
  un'unica voce — pronta, non ancora collegata alla fusione multi-disco per
  i lotti (l'unione oggi è "ingenua", primo valore trovato per campo)
- ✅ **Riconoscimento vision locale** (`core/vision/ollama_vision.py`, via
  Ollama/qwen2.5vl) **collegato alla pipeline reale**
  (`core/vision/enrichment.py`): per ogni annuncio nuovo prova prima a
  leggere artista/album dal titolo (gratis), poi controlla se è già stato
  processato in precedenza (niente doppia chiamata alla vision), solo come
  ultima risorsa guarda le foto (tutte quelle dell'annuncio, non solo
  l'anteprima)
- ✅ **Arricchimento Discogs collegato alla pipeline**: cerca per codice
  catalogo se letto (più preciso), altrimenti artista+titolo; il messaggio
  Telegram mostra i dati riconosciuti, i prezzi Discogs (Poor/Good/Very
  Good) e la % rispetto al prezzo Discogs "Good" — limitato ai primi 10
  annunci "validi" (con corrispondenza Discogs) per esecuzione
- ✅ Pipeline end-to-end: marketplace abilitati → filtri → dedup →
  arricchimento (vision + Discogs) → notifica

**Non ancora implementato**: fusione multi-disco per lotti con più record
diversi nelle stesse foto (oggi l'unione dei dati tra foto è "ingenua"),
escalation vision locale/cloud per bassa confidenza, calcolo del margine
reale come criterio di notifica (per ora la % di sconto è solo
informativa), contatore di spesa giornaliero, scheduling automatico ogni 10
minuti, collector Facebook Marketplace (escluso di proposito: nessuna API
pubblica, rischio alto).

## Architettura

- **Collector** per marketplace: eBay (Browse API, fatto), Subito (scraping
  non ufficiale, fatto ma da verificare), Facebook Marketplace (escluso per
  ora). Ogni collector implementa `search()` e ritorna sempre `Listing`
  (`core/collectors/base.py`) — aggiungere un marketplace futuro (Vinted,
  Wallapop, Kleinanzeigen...) è un nuovo file collector + una riga nel
  registro (`core/collectors/registry.py`), senza toccare il resto.
- **Filtri a regole** in YAML per categoria (blacklist, prezzo/pezzo, soglie):
  girano prima di qualsiasi AI. Niente whitelist per genere/artista di
  proposito: un venditore che non sa cosa vende non scrive l'artista nel
  titolo, quindi filtrare per genere scarterebbe proprio le occasioni nei
  lotti "anonimi" che interessano di più. La blacklist è modificabile a
  runtime dal menu Telegram (sospendere/aggiungere parole).
- **Impostazioni** (`core/settings.py`): schema statico in YAML (cosa esiste,
  editato da chi sviluppa) + stato mutabile in SQLite (cosa è scelto ora,
  modificabile dal bot senza toccare file).
- **Arricchimento prezzi** via Discogs API (deterministico, niente AI). Il
  codice catalogo di un disco non è garantito univoco (la stessa etichetta
  europea può riusarlo in più paesi, le ristampe a volte riusano il codice
  originale): la ricerca ritorna tutti i candidati trovati, mai una media tra
  prezzi di edizioni diverse.
- **Fase vision a cascata** (da fare): detector (zero-shot, tipo Grounding
  DINO/YOLO-World) per isolare i singoli dischi nelle foto di lotti affollati,
  OCR dedicato per leggere testo, poi ricerca Discogs testuale — il
  "riconoscimento" vero lo fa la ricerca testuale deterministica, non l'AI a
  naso. Escalation a modello locale (Ollama) poi cloud solo se bassa
  confidenza. La fusione multi-foto (`core/vision/matching.py`, già fatta)
  unisce i dati parziali letti da foto diverse dello stesso disco in un'unica
  voce, distinguendo copie fisiche reali (stesso disco visto due volte nella
  stessa foto) da fotografie multiple dello stesso esemplare.
- **Layer AI astratto** (da fare): provider e modello configurabili in
  `.env`, mai hardcoded.
- **Contatore di spesa giornaliero** (da fare): superata la soglia, la fase
  AI cloud si disattiva fino al giorno dopo.
- **Notifiche Telegram**: un messaggio per annuncio nuovo, con link cliccabile
  invece dell'URL per esteso. Per i lotti con più dischi riconosciuti (da
  fare): dettaglio completo (codice, paese/lingua, etichetta, prezzo Discogs,
  link alla release) solo per i dischi che superano la soglia di margine; gli
  altri riassunti in una riga sola.
- **Menu impostazioni Telegram** (`bot/settings_menu.py`): processo separato
  e persistente (non uno script periodico) con bottoni inline per marketplace
  attivi, tipo ricerca, categoria eBay, e gestione parole chiave.
- **Multi-utente** (`core/users.py`, `core/user_filters.py`): tabella
  `users` in SQLite (non file per utente) con `chat_id`, `is_admin`,
  `approved`. L'amministratore è auto-registrato da `TELEGRAM_CHAT_ID` in
  `.env` a ogni avvio. Un utente non approvato che scrive al bot genera una
  richiesta d'accesso con bottoni di approvazione diretti nella chat
  dell'amministratore; una volta approvato, l'utente vede solo la sezione
  "I miei filtri" (parole chiave personali, stesso meccanismo
  sospendi/aggiungi delle parole chiave globali, riusa `core/keywords.py`
  con chiave `user.<chat_id>.filter.keywords`). La ricerca resta unica:
  `scripts/collect.py` cerca una volta sola e poi invia a ogni utente
  approvato solo gli annunci che passano anche il suo filtro personale.
- **Scheduling** (da fare): ogni 10 minuti + heartbeat giornaliero.

Il core è pensato per essere indipendente da marketplace e categoria; vinili,
CD e carte Pokémon saranno plugin che definiscono le proprie regole e
arricchimenti.

## Ambiente

Sviluppo in **WSL2** (Windows): scraper e scheduling sono più semplici da
gestire in ambiente Linux, e WSL2 supporta il passthrough GPU CUDA per Ollama
(in alternativa Ollama può girare su Windows nativo, raggiungibile da WSL2
via `localhost:11434`).

## Struttura del progetto

```
vinil-scraper/
├── .env.example                 # template chiavi API (mai committare .env)
├── config/categories/           # regole YAML per categoria (es. vinyl.yaml)
├── core/
│   ├── db.py                    # storage SQLite, dedup, retention/pulizia
│   ├── settings.py              # impostazioni mutabili a runtime (SQLite)
│   ├── keywords.py               # parole chiave sospendibili/aggiungibili
│   ├── query_defaults.py        # liste di query di default per marketplace
│   ├── filters.py               # filtri a regole (blacklist, prezzo)
│   ├── users.py                  # utenti autorizzati (admin, approvazioni)
│   ├── user_filters.py           # filtri personali per utente approvato
│   ├── notifications.py          # tracking annunci già notificati per utente
│   ├── vision/
│   │   ├── matching.py          # fusione multi-foto + confidenza (dischi)
│   │   ├── ollama_vision.py     # riconoscimento locale via Ollama (qwen2.5vl)
│   │   └── enrichment.py        # titolo->cache->vision + Discogs + messaggio (condiviso)
│   └── collectors/
│       ├── base.py              # tipo Listing + interfaccia Collector
│       ├── registry.py          # mappa marketplace -> classe collector
│       ├── ebay.py              # eBay Browse API (OAuth, ricerca, categoria)
│       ├── subito.py            # Subito (scraping JSON-LD, non ufficiale)
│       └── discogs.py           # ricerca release + prezzi per condizione
├── bot/
│   ├── notifier.py               # invio messaggi Telegram
│   ├── send_test_message.py      # test rapido del bot
│   └── settings_menu.py          # menu impostazioni a bottoni (processo persistente)
└── scripts/
    ├── collect.py                # pipeline: cerca (marketplace abilitati) → filtra → salva → notifica
    └── vision_test.py            # test manuale riconoscimento vision (limite 2 annunci)
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
  `https://api.telegram.org/bot<TOKEN>/getUpdates`. Questo chat id va in
  `TELEGRAM_CHAT_ID` ed è anche quello che diventa **amministratore** del
  bot multi-utente (unico che vede le impostazioni globali e approva nuovi
  utenti).
- **eBay**: registrati su [developer.ebay.com](https://developer.ebay.com/),
  crea un keyset Production, sblocca il keyset con l'esenzione "I do not
  persist eBay data" (Application Keys → Notifications → Marketplace Account
  Deletion → toggle "Exempted").
- **Discogs**: token di accesso personale da
  [discogs.com/settings/developers](https://www.discogs.com/settings/developers)
  → "Genera token".

### Altre variabili (`.env`)

- `RETENTION_HOURS` (default `48`): dopo quante ore un annuncio non più visto
  viene rimosso dal DB.

## Uso

Test del bot Telegram:

```bash
python -m bot.send_test_message
```

Test del collector eBay (ricerca + categoria vinili):

```bash
python -m core.collectors.ebay
```

Test del collector Subito (⚠️ non verificato contro il sito reale):

```bash
python -m core.collectors.subito
```

Test del client Discogs (ricerca per titolo e per codice catalogo):

```bash
python -m core.collectors.discogs
```

Test del riconoscimento vision locale (richiede [Ollama](https://ollama.com)
installato e in esecuzione, con un modello vision scaricato — testato con
`ollama pull qwen2.5vl`): prende al massimo 2 annunci già nel DB (limite
volutamente basso, `VISION_TEST_LIMIT` in `scripts/vision_test.py`), scarica
la loro foto e stampa cosa riconosce il modello. Non fa parte della
pipeline automatica, non invia notifiche:

```bash
python -m scripts.vision_test
```

Pipeline completa — cerca sui marketplace abilitati (eBay + Subito di
default), applica i filtri, salva i nuovi annunci nel DB, notifica su
Telegram, pulisce gli annunci scaduti:

```bash
python -m scripts.collect
```

Il DB SQLite (`data/listings.db`) tiene traccia di cosa è già stato
notificato, quindi le esecuzioni successive segnalano solo i nuovi annunci.

Menu impostazioni su Telegram — **processo separato e persistente**, va
lasciato aperto in un terminale mentre lo si usa (non lanciato dallo
scheduler dei collector):

```bash
python -m bot.settings_menu
```

Da lì, direttamente dal bot: abilita/disabilita marketplace, tipo di ricerca
(lotti/singoli), categoria eBay, e gestisci le parole chiave di ricerca ed
esclusione (sospendi/riattiva con un tocco, aggiungi scrivendo un messaggio).

**Avviare la ricerca da Telegram** (senza terminale): comando `/cerca` o
bottone "🔍 Cerca ora" nel menu (solo amministratore) — esegue lo stesso
ciclo di `python -m scripts.collect`. L'amministratore riceve sempre un
report della scansione (anche con zero risultati): un messaggio iniziale
che si aggiorna mentre procede (progresso), poi un riepilogo finale
(annunci nuovi, controllati, con corrispondenza Discogs, notificati, quanti
sotto il 50% del valore Discogs). Richiede comunque che
`python -m bot.settings_menu` sia in esecuzione da qualche parte (PC
acceso), esattamente come per il resto del bot.

**Multi-utente**: la prima volta che il chat id in `TELEGRAM_CHAT_ID` scrive
`/start` diventa automaticamente amministratore. Chiunque altro scriva al
bot riceve un messaggio "richiesta inviata" e l'amministratore riceve una
notifica con bottoni ✅ Approva / ❌ Rifiuta. Una volta approvato, l'utente
vede solo "🔍 I miei filtri": può abilitare parole chiave personali (es.
"883") per ricevere solo gli annunci che le contengono, oppure lasciare
tutto disabilitato per ricevere tutti i risultati della ricerca globale.
Cambiare i filtri è un'azione silenziosa (salva e basta, non parte nessuna
ricerca): il controllo — sia sui nuovi annunci sia su quelli già in DB non
ancora notificati con i filtri attuali — parte solo quando si lancia
`/cerca`. L'amministratore gestisce approvazioni/rimozioni anche in seguito
da "👥 Gestisci utenti" nel menu principale.

## Changelog

Ogni riga indica quando è stata fatta la modifica (data del commit).

- **2026-07-23** — Struttura iniziale del progetto, bot Telegram di test
- **2026-07-23** — Storage SQLite con dedup per annuncio
- **2026-07-23** — Collector eBay Browse API
- **2026-07-23** — Collegamento eBay → DB con dedup
- **2026-07-23** — Filtri a regole YAML per la categoria vinili
- **2026-07-23** — Primo ciclo end-to-end: notifica Telegram sui nuovi annunci
- **2026-07-23** — Notifiche individuali con data di pubblicazione dell'annuncio
- **2026-07-23** — Filtri vinili allineati alla strategia reale (caccia a lotti sottoprezzati, nessuna whitelist per genere)
- **2026-07-23** — Corretti falsi positivi nel filtro blacklist (confronto a parola intera)
- **2026-07-24** — Messaggio Telegram riformattato: link cliccabile, campo prezzo realistico
- **2026-07-24** — Client Discogs: prezzi per condizione
- **2026-07-24** — Client Discogs: ricerca per codice catalogo
- **2026-07-24** — Multi-utente sul bot Telegram: tabella `users` in SQLite,
  amministratore auto-registrato da `TELEGRAM_CHAT_ID`, richiesta/approvazione
  d'accesso per nuovi utenti, filtri personali per utente ("🔍 I miei
  filtri") applicati sopra la ricerca globale unica, menu "👥 Gestisci
  utenti" per l'amministratore
- **2026-07-24** — Limite temporaneo di 5 notifiche Telegram per esecuzione
  di `scripts.collect` (`MAX_NOTIFICATIONS_PER_RUN`), per fare prove senza
  intasarsi di messaggi al primo giro con DB vuoto — gli annunci oltre il
  limite restano comunque salvati nel DB
- **2026-07-24** — `scripts.collect` ora aggrega i risultati di TUTTE le
  query di ricerca prima di notificare, invece di notificare separatamente
  dopo ogni query: le query restano multiple (servono a coprire formulazioni
  diverse dello stesso annuncio, es. "vinile" vs "disco vinile" — le
  marketplace API cercano per parola, non esiste un "dammi tutto"), ma sono
  ricerca, non filtri; il filtro/dedup/notifica restano un unico passaggio
  sui risultati aggregati
- **2026-07-24** — Fix robustezza: se una query fallisce (es. Subito
  risponde 403), le altre query/marketplace non vengono più perse — l'errore
  viene loggato e la ricerca continua con quelle successive
- **2026-07-24** — Collector Subito: header di richiesta più completi
  (Accept, Accept-Language, Sec-Fetch-*) e una visita preventiva alla
  homepage per ottenere i cookie di sessione prima della ricerca, nel
  tentativo di superare il blocco 403 osservato contro il sito reale — non
  garantito, ancora da verificare
- **2026-07-24** — Comando `/cerca` (e bottone "🔍 Cerca ora" nel menu, solo
  amministratore): avvia la ricerca completa direttamente da Telegram, senza
  bisogno del terminale — gira in un thread separato per non bloccare il bot
  mentre cerca, e alla fine manda un riepilogo (annunci nuovi trovati,
  eventuali errori per marketplace)
- **2026-07-24** — Ricerca per codice catalogo restringibile per paese/anno
- **2026-07-24** — Ricerca eBay ristretta alla categoria ufficiale vinili (Taxonomy API)
- **2026-07-24** — Fusione multi-foto e sistema di confidenza per i dischi rilevati (`core/vision/matching.py`)
- **2026-07-24** — Retention configurabile con pulizia automatica del DB (`RETENTION_HOURS`)
- **2026-07-24** — Architettura multi-marketplace: tipo `Listing` unificato, collector Subito
- **2026-07-24** — Menu impostazioni Telegram (marketplace, tipo ricerca, categoria eBay)
- **2026-07-24** — Gestione parole chiave (ricerca ed esclusione) dal menu Telegram
- **2026-07-24** — Controllo "a ritroso" per i filtri personali: quando un
  utente aggiunge o riattiva una parola nei propri filtri (🔍 I miei filtri),
  il bot controlla anche gli annunci GIÀ presenti nel DB (trovati da
  scansioni precedenti), non solo quelli delle prossime ricerche, e notifica
  quelli che corrispondono — nuova tabella `user_notifications`
  (`core/notifications.py`) per non rimandare mai due volte lo stesso
  annuncio allo stesso utente, sia dal controllo a ritroso che dalle
  notifiche "live"
- **2026-07-24** — Comandi Telegram registrati con descrizione (menu "/" del
  bot: `/start`, `/impostazioni`, `/cerca`) e testi del menu più chiari
  (spiegazione breve in cima a menu principale, filtri personali, gestione
  utenti)
- **2026-07-24** — Primo collegamento della fase vision: riconoscimento
  locale via un modello vision servito da Ollama (`core/vision/ollama_vision.py`,
  testato con `qwen2.5vl` — legge correttamente tracklist, codice catalogo e
  codice a barre da una foto reale). Script di solo test manuale
  (`scripts/vision_test.py`, limite bassissimo di 2 annunci per prova) — non
  è ancora collegato alla pipeline automatica né alla ricerca Discogs
- **2026-07-24** — Fix: il test vision usava solo l'anteprima 225px salvata
  nel DB (una per annuncio) invece di tutte le foto in alta risoluzione —
  nuova `core/collectors/ebay.get_item_images()` (chiamata `getItem`,
  fronte/retro/etichetta) con tetto `MAX_IMAGES_PER_LISTING` per non
  processare decine di foto di un lotto in una prova — verificata contro
  l'API eBay reale (5 foto recuperate per un annuncio, trovato un codice
  catalogo reale sul retro)
- **2026-07-24** — Il riconoscimento vision ora risponde in JSON strutturato
  a campi fissi (artista, titolo album, etichetta, codice catalogo,
  barcode, altro) invece di descrizioni in prosa — salvato nel DB (nuova
  tabella `vision_results`, una riga per foto) e stampato come riga
  sintetica invece del testo lungo di prima
- **2026-07-24** — `scripts.vision_test` ora unisce i dati letti dalle foto
  di uno stesso annuncio (unione ingenua, primo valore trovato per campo —
  non ancora la fusione multi-disco per i lotti), cerca su Discogs (codice
  catalogo se disponibile, altrimenti artista+titolo — mai media i prezzi
  tra edizioni diverse, mostra tutti i candidati trovati) e manda un
  messaggio Telegram di riepilogo per annuncio: prezzo dell'annuncio, dati
  riconosciuti, prezzo Discogs per edizione, link
- **2026-07-24** — Due fix dal primo test reale con dati Discogs veri: (1)
  il modello a volte univa codice catalogo e barcode in una sola stringa
  (es. "LMLP165: 502454968712") impedendo la ricerca — ora viene ripulito
  prima di cercare (con il valore grezzo come ripiego), e il prompt vision è
  più esplicito nel tenerli separati; (2) una release Discogs senza prezzi
  suggeriti dava 404 mostrato come errore grezzo — ora un messaggio pulito
  ("nessun prezzo disponibile per questa edizione")
- **2026-07-24** — Aggiunto `VISION_TEST_OFFSET` per testare annunci diversi
  dal primo, e il link diretto alla pagina Discogs (`discogs.com/release/id`)
  per ogni candidato nel messaggio Telegram, per poter confrontare a colpo
  d'occhio se la corrispondenza trovata è quella giusta
- **2026-07-24** — I link nel messaggio Telegram di `vision_test` sono ora
  testo cliccabile ("Link Ebay", "Link Discogs" sotto ogni candidato) invece
  dell'URL grezzo per esteso, stesso stile già usato in `scripts.collect` —
  titolo e dati riconosciuti correttamente escapati per HTML
- **2026-07-24** — Prezzi Discogs arrotondati all'euro intero (niente più
  decimali illeggibili) e mostrati su tre fasce di riferimento (Poor/Good/
  Very Good) invece di un range basso-alto. Aggiunta la % rispetto al
  prezzo Discogs in condizione "Good" (usata come riferimento per valutare
  l'affare) prima del titolo nel messaggio — per ora solo informativa, in
  futuro (a limiti di test rimossi) sarà il criterio per decidere se
  notificare o no un annuncio
- **2026-07-24** — Messaggio Telegram di `vision_test` riformattato a
  sezioni schematiche (titolo, dati riconosciuti, Discogs, link), ognuna
  separata da una riga vuota e con intestazione in grassetto, invece di
  tutto compresso su poche righe — ogni dato riconosciuto e ogni candidato
  Discogs (numerati se più di uno) su blocchi propri
- **2026-07-24** — Vision e ricerca Discogs collegate alla pipeline reale
  (`scripts.collect`), non più solo allo script di test isolato. Nuovo
  modulo condiviso `core/vision/enrichment.py` (usato sia da
  `scripts.collect` che da `scripts.vision_test`) con arricchimento a costo
  crescente: prova prima a leggere artista/album dal **titolo**
  (gratis, pattern "Artista - Album"), poi controlla se l'annuncio è **già
  stato processato** in precedenza (righe già in `vision_results`, non
  richiama il modello), solo come ultima risorsa usa la **vision sulle
  foto**. La notifica arricchita (dati riconosciuti + Discogs + % sconto)
  sostituisce il vecchio messaggio "prezzo realistico: non disponibile" —
  limitata ai primi `MAX_ENRICHED_LISTINGS_PER_RUN` (10) annunci "validi"
  (con almeno una corrispondenza Discogs trovata) per esecuzione; gli
  annunci senza corrispondenza non vengono notificati né contano nel
  limite.
- **2026-07-24** — Rimosso il controllo automatico sul backlog quando si
  aggiunge/attiva un filtro personale (era fastidioso: partiva subito senza
  chiederlo). Spostato dentro la scansione stessa (`/cerca`): ogni volta che
  si lancia una ricerca, oltre ai nuovi annunci controlla anche quelli GIÀ
  in DB che ora corrispondono al filtro personale di qualcuno e non sono
  ancora stati notificati — un'unica azione deliberata invece di scattare
  ad ogni cambio di filtro. `notify_backlog_for_user` (messaggio semplice,
  non arricchito) è stato rimosso, sostituito da `get_backlog_candidates`
  che confluisce nello stesso ciclo di arricchimento dei nuovi annunci.
- **2026-07-24** — Report sempre inviato all'amministratore a fine
  scansione, anche con zero risultati: un messaggio iniziale (quanti nuovi
  + quanti dal backlog) che si aggiorna in corso d'opera (ogni 5 annunci
  controllati) e poi un riepilogo finale (controllati, con corrispondenza
  Discogs, notificati, quanti sotto il 50% del valore Discogs "Good") — per
  capire cosa ha fatto la scansione senza dover guardare il terminale.
  `bot/notifier.send_message` ora ritorna il `message_id` e nuova
  `edit_message()` per aggiornare un messaggio già inviato invece di
  mandarne uno nuovo ogni volta.
- **2026-07-24** — Nuovi comandi `/report` (solo amministratore) e
  `/report_miei`: un check SOLO sul DB già filtrato (nessuna ricerca nuova
  sui marketplace, nessuna vision fresca — usa solo dati già noti da titolo
  o cache), riga sintetica per annuncio (titolo, album, codice catalogo,
  prezzo eBay, prezzo Discogs Good). Separato apposta da `/cerca`, che
  invece fa ricerca+arricchimento veri.
  - `/report`: solo gli annunci già sotto il prezzo Discogs "Good"
    (qualunque sconto positivo, non serve arrivare al 50% come nel
    riepilogo di `/cerca`) — se non è già un affare non interessa vederlo.
  - `/report_miei`: applica il filtro personale di chi lo lancia (cosa gli
    interessa), senza nessun filtro di prezzo.
