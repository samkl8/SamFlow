# SamFlow — build-out-plan: van mockup naar werkende app

Dit plan zet het ontwerp in `macos/design/app-interface.html` om in een echte macOS-app,
in fases die elk een **werkende, shipbare app** opleveren (main = directe release, dus elke
fase moet op zichzelf kunnen landen). Telemetrie is bewust de laatste, optionele fase.

## Randvoorwaarden (hard, uit CLAUDE.md/README — geen van deze is onderhandelbaar)

1. **De TCC-val.** De app draait als dunne launcher-bundle (`~/Applications/SamFlow.app`)
   die de uv-venv-python start. Rechten hangen aan die identiteit. Herstarten altijd via
   `open` op de bundle (`updater.relaunch`), nooit kale `python samflow.py`.
2. **Eén proces, één main run loop.** `NSApp.run()` drijft dezelfde loop als de Fn-tap.
   Alle AppKit op de main thread; achtergrondthreads schrijven alleen state. Niets mag de
   run loop blokkeren — transcriberen, git, en straks stats/historie draaien op threads.
3. **De pill blijft heilig.** Non-activating panel, nooit focus, vers opgebouwd per
   verschijnen. Dit plan raakt `hud.py`'s pill-code niet aan.
4. **100% lokaal, privacy-first.** `set_last_text` is nu bewust geheugen-only; alles wat
   voortaan wél op schijf komt is aggregaat (stats) of expliciet opt-in (historie).
5. **Meegroeien, niet herbouwen.** `settings.py` (mtime-cache, atomisch schrijven),
   `ui.py` (Toggle), het `_Flipped`/`_label`/`_switch_row`-patroon van `prefs.py`/`panel.py`
   en de `_GlyphView`-stijl van eigen tekenwerk zijn de bouwstenen. Geen tweede
   settings-systeem, geen webview.

**Al aanwezig en herbruikbaar** (uit de code gelezen): activation policy wordt nu in
`hud.build()` en `prefs._run_standalone()` op *accessory* gezet; `prefs.py` bewijst dat een
accessory-app gewoon vensters kan tonen; de bundle heeft al `SamFlow.icns` + `LSUIElement`
+ `make-icon.py`; `~/Library/Application Support/SamFlow/` is al in gebruik (telemetry);
`handle()` in `samflow.py` is de ene trechter waar elk gelukt dictaat doorheen komt;
`candidates.json` + `add_term`/`add_mapping` zijn de leer-loop; `telemetry.py` bestaat al
en is inert (lege sink) met een `share_usage`-toggle.

## Fase-overzicht

| # | Fase | Maat | Levert op |
|---|------|------|-----------|
| O | Output-kwaliteit / oppoetsen | S→L | Alinea's, opsommingen, grammatica, toon — Route A nu, B/C later (los van de schil) |
| 1 | Hoofdvenster-schil + navigatie | M | Echt venster met zijbalk, Instellingen-tab, "Open SamFlow…" in het paneel |
| 2 | Basic ↔ App-modus | M | De moduskeuze werkt echt: dock-icoon, ⌘Tab, onboarding-stap |
| 3 | Stats-laag + Dashboard | M | Overzicht-tab met echte cijfers uit een lokale stats-laag |
| 4 | Historie (opt-in) | M | Doorzoekbare lokale historie met retentie en wissen |
| 5 | Woordenlijst-UI | M | De leer-loop zichtbaar: suggesties, termen, correcties |
| 6 | Helder-verfijning | S | Mode-kaarten, hero-band, iconenfamilie, wordmark |
| 7 | Distributie | M/L | Icoon, ondertekening, DMG, "Toch openen"-verhaal |
| 8 | Telemetrie (uitgesteld) | S | Sink invullen voor de bestaande heartbeat — optioneel |

---

## Fase O — Output-kwaliteit / oppoetsen (los van de app-schil, start nu)

**Doel.** De transcriptie leest als geschreven tekst i.p.v. één blok spraak: alinea's,
opsommingen, vloeiende grammatica en passende toon — het kernverschil met Wispr Flow.
Belangrijk: Wispr Flow doet dit met een **cloud-LLM**; SamFlow houdt het lokaal, dus dit is
een gefaseerde keuze, geen enkele knop. Instelbaar met *Oppoetsen: Uit / Licht / Vol*,
zó dat de snelle, letterlijke modus (zoals nu) altijd blijft bestaan.

**Route A — regels (lokaal, ~0 latency, NU).** In `cleanup.py`, test-gedreven: elke regel
afgedwongen door een `EXAMPLES`-voorbeeld, positief én negatief tegen valse treffers,
`python cleanup.py` groen.
- Stopwoorden (uh/eh/ehm): **bestaat al** (`FILLERS`). Gesproken "nieuwe regel"/"nieuwe
  alinea": **bestaat al** (`COMMANDS`).
- **Opsommingen**: expliciete ordinaal-markers (≥2: "ten eerste… ten tweede…", "punt één…
  punt twee…") → genummerde lijst. Conservatief: minstens twee markers, zodat een losse
  "ten eerste" in een zin ongemoeid blijft. Achter `ENABLE_LISTS` (zelfde patroon als
  `ENABLE_STUTTER`/`ENABLE_COMMANDS`).
- **Alinea's uit pauzes**: robuuster dan tekst-raden, maar vereist segment-tijdstempels →
  `transcribe()` vraagt segments op (`response_format=verbose_json`) en `cleanup` knipt een
  alinea bij een pauze boven een drempel. Drempel afstemmen op écht dictaat (de
  onderhoudslus uit CLAUDE.md), dus ná Sam's eerste echte lijst/alinea-audio.

**Route B — lokaal oppoets-model (Wispr-kwaliteit, lokaal).** Een klein instruct-LLM warm
naast `whisper-server` (llama.cpp `llama-server`, zelfde patroon: mmap, localhost, warm).
Ruwe tekst + strakke prompt ("reformatteer/corrigeer, herschrijf niet: grammatica,
stopwoorden/valse starts, alinea's, gesproken opsommingen → bullets, behoud woorden en
betekenis") → nette tekst. Guardrails: lexicon-canonicalisatie ná de LLM; "nooit slechter
dan ruw" (te grote afwijking in lengte/betekenis → val terug op Route A). Kost: latency
(warm 3B ~2–6s vs. 0,5s nu; goede NL-grammatica wil eerder 7–8B) + RAM — daarom de
*Vol*-stand achter de toggle. Model kiezen + benchmarken (3B vs 7–8B voor Nederlands).

**Route C — optionele cloud-oppoets (snelst/beste, breekt de lokaal-belofte).** Tekst door
Claude (API bestaat al). Alleen als expliciete opt-in, default uit, duidelijk gemeld — want
dit stuurt je dictaat de deur uit. Voor privacy-vrienden gevoelig; hooguit een bewuste
extra stand.

**Bestanden.** Route A: `cleanup.py` (+ `EXAMPLES`), evt. `samflow.py` (`transcribe`
segments). Route B: nieuw `polish.py` + een `launchd`-job voor `llama-server` + model,
`settings.py` (`polish_level`), `prefs.py` (segmented). Route C: `polish.py` (cloud-tak) +
opt-in-toggle.

**Persistentie.** Geen nieuwe (alleen de `polish_level`-setting bij B/C).

**Risico's / regels.** Oppoetsen botst met de kernbelofte "we raken geen woord aan buiten
je lijst" — daarom een stand, niet de default, en de lexicon draait er altijd overheen.
Nooit de run loop blokkeren: een LLM-call gaat op de handle-thread (zoals `transcribe` nu),
fail-silent terug naar Route A. Cloud (C) alleen met expliciete toestemming.

---

## Fase 1 — Hoofdvenster-schil + navigatie (M)

**Doel.** Eén echt `NSWindow` met zijbalk (Overzicht / Historie / Woordenlijst /
Instellingen), bereikbaar vanuit het menubalk-paneel. Nog géén moduskeuze: het venster
opent zoals Voorkeuren nu al opent (accessory-app mét venster — dat werkt vandaag al).

**Wat erin zit**
- Nieuw `mainwindow.py`: één venstercontroller met een eigen zijbalk-view (geen
  NSSplitViewController-magie nodig; een `_Flipped`-container met nav-rijen links en een
  wisselend content-view rechts, zelfde bouwtrant als `prefs.py`).
- Zijbalk-items wisselen content-views; per tab een `build_*_view()`-factory.
- **Instellingen-tab = de bestaande prefs-view.** Refactor: haal `_Flipped`, `_label`,
  `_section`, `_separator`, `_row_label` uit `prefs.py` naar `ui.py` (leaf-module, geen
  cyclusrisico), en splits `PreferencesWindow._build` in "bouw de view" en "zet 'm in een
  venster". De view gaat dan zowel in het losse Voorkeuren-venster (blijft bestaan voor
  `--prefs`) als in de tab. Eén systeem, twee plekken.
- Overzicht-tab v1 = status-lite: de drie permissie-checks (bestaan al in `prefs.py`),
  `server_up()`, mic-naam uit `audiodev.choose_input()`, en het laatste dictaat uit
  `Hud.last_text()`. Historie/Woordenlijst-tabs tonen een nette platshouder.
- `panel.py`: nieuwe actie-rij "Open SamFlow…" bovenaan de acties; `hud._Ticker` krijgt
  `openMainWindow_` (zelfde patroon als `openPreferences_`).

**Bestanden.** Nieuw: `mainwindow.py`. Wijzig: `ui.py`, `prefs.py`, `panel.py`, `hud.py`.

**Persistentie.** Geen.

**Risico's / regels.** Venster opent altijd vanuit een klik (main thread) — AppKit-regel
gedekt. Referentie-beheer via hetzelfde `_open`-dict-patroon als `prefs.py` (anders ruimt
de GC de controller op). Het venster mág focus pakken (bewuste klik); dicteren in het
venster zelf (zoekveld straks) is dan gewoon een feature.

---

## Fase 2 — Basic ↔ App-modus (M)

**Doel.** De kern van het ontwerp echt maken: **modus = aanwezigheid, niet
functionaliteit.**

**Wat erin zit**
- Nieuwe settings-sleutel `app_mode: "basic"` (default = huidig gedrag; een verse
  installatie verandert dus niets — het vaste DEFAULTS-principe van `settings.py`).
- `hud.build()` leest `app_mode` en zet de policy: accessory (basic) of
  `NSApplicationActivationPolicyRegular` (app). `LSUIElement` in Info.plist blijft staan;
  de runtime-policy overrulet dat, dus de bundle hoeft niet te wijzigen (TCC-identiteit
  blijft onaangeroerd — belangrijk).
- Live wisselen vanuit Instellingen: policy runtime omzetten +
  `activateIgnoringOtherApps_`. Bekende macOS-quirk: na accessory→regular soms pas een
  dock-icoon na activate; na regular→accessory blijft het venster gewoon werken. Testen
  op beide richtingen; de wissel is één klik, dus een korte flikkering is acceptabel.
- App-modus: dock-icoon (de bestaande `SamFlow.icns`), `applicationShouldHandleReopen_`
  op een app-delegate zodat een dock-klik het hoofdvenster (her)opent, venster in ⌘Tab.
- Basic-modus: exact vandaag, plus "Open SamFlow…" uit fase 1. **Technisch inzicht uit de
  code: er is geen "tijdelijk dock-icoon" nodig** — een accessory-app toont vensters
  prima (Voorkeuren bewijst dat elke dag). Venster dicht = weer onzichtbaar, vanzelf.
- Onboarding: `WelcomeWindow` krijgt de moduskeuze-stap uit de mockup (na de
  rechten-stappen), die `app_mode` schrijft. V1 als `NSSegmentedControl` + uitlegtekst;
  de visuele kaarten komen in fase 6.
- Paneel-voetregel toont de actieve modus (zoals de mockup).

**Bestanden.** Wijzig: `settings.py` (DEFAULTS), `hud.py` (policy + delegate),
`prefs.py` (wizard-stap + instellingen-rij), `mainwindow.py`, `panel.py` (voetregel).

**Persistentie.** Alleen de nieuwe settings-sleutel.

**Risico's / regels.** De policy-wissel raakt de main run loop — alleen vanaf een klik op
de main thread uitvoeren, nooit vanaf een thread. De pill-code heeft er geen last van:
het pill-panel hangt niet aan de policy (non-activating, eigen level). Watchdog en
updater blijven identiek werken (zelfde proces, zelfde bundle).

---

## Fase 3 — Stats-laag + Dashboard (M)

**Doel.** Het Overzicht uit de mockup met echte cijfers, uit een lokale, inhoudsloze
stats-laag.

**Wat erin zit**
- Nieuw `stats.py`: dag-aggregaten in
  `~/Library/Application Support/SamFlow/stats.json` (de map die telemetry al aanmaakt).
  Per dag: `dictations`, `words`, `speech_sec`, `fastest_took`, `total_took`. **Geen
  tekst, geen app-namen — alleen tellingen**, daarom standaard aan (net als de
  "laatste dictaat"-teller nu in het geheugen), met een toggle in Instellingen.
  Atomisch schrijven via het `settings.py`-patroon (mkstemp + `os.replace`).
- Hook: één aanroep in `samflow.handle()` ná het plakken, in `try/except` en fail-silent —
  zelfde contract als `lexicon.record()` ("het dictaat gaat altijd voor"). Draait al op de
  handle-thread, dus schrijven blokkeert de run loop nooit.
- Afgeleiden in `stats.py`: weektotaal + delta, "tijd bespaard" (spraak-seconden vs.
  dezelfde woorden typen op 40 wpm — de aanname staat als constante bovenin, zichtbaar in
  de UI-subtekst), snelste dictaat, streak (aaneengesloten dagen met ≥1 dictaat).
- Dashboard-tab: hero-band (eigen `NSView.drawRect_` in grafiet — zelfde tekentrant als
  `_GlyphView`/`_status_image`, geen afbeeldingen), vier stat-tegels, het
  woorden-per-dag-staafgrafiekje (eigen view, klei `#C67B52`, alleen vandaag met getal),
  status-chips (permissies + server + mic — de checks uit fase 1). Ververst via een
  `NSTimer` op de main loop, alléén terwijl het venster zichtbaar is (venster-delegate
  start/stopt de timer), leest een in-memory kopie — geen schijf-I/O per tik.
- Retentie: aggregaten zijn klein; bewaar 400 dagen, prune bij het wegschrijven.

**Bestanden.** Nieuw: `stats.py`. Wijzig: `samflow.py` (één hook-regel + app-naam nog
niet nodig), `mainwindow.py`, `prefs.py` (toggle-rij), `settings.py` (`stats_enabled`).

**Persistentie.** `stats.json` in App Support — buiten de repo-dir, dus nooit per ongeluk
in git en ongevoelig voor updater/git-operaties. (`settings.json` blijft juist in de
repo-dir: `watchdog.sh` grept dat bestand rechtstreeks.)

**Risico's / regels.** Nooit het plakken vertragen (hook ná `paste`, fail-silent).
Main-thread-regel gedekt: tekenen leest alleen een snapshot, zoals de pill dat doet.

---

## Fase 4 — Historie, lokaal en opt-in (M)

**Doel.** De Historie-tab uit de mockup, met de privacy-belofte als gedrag: standaard
uit, altijd wisbaar, nooit het netwerk op.

**Wat erin zit**
- Nieuwe sleutels: `history_enabled: False` (opt-in!), `history_days: 30`.
- Nieuw `history.py`: JSONL-bestand `~/Library/Application Support/SamFlow/history.jsonl`
  met per regel `ts`, `text`, `app`, `words`, `speech_sec`, `took`. Bestandsrechten 0600.
  Append op de handle-thread; prunen (ouder dan `history_days`) bij opstart en na append.
  `clear()` voor "Wis alles", `remove(ts)` per rij, `search(q)` in-memory (het bestand
  blijft klein; 30 dagen dicteren is megabytes, geen gigabytes).
- App-naam-capture: op het Fn-up-moment (main thread, in de tap-callback — één goedkope
  `NSWorkspace.frontmostApplication()`-read, ruim binnen de "callback keert meteen
  terug"-regel) en meegeven aan `handle()`. Niet op de handle-thread rondvragen.
- Hook in `handle()`: alléén als `history_enabled`. **`Hud.set_last_text` blijft
  geheugen-only** — de bestaande regel blijft letterlijk staan; historie is een tweede,
  expliciet aangezet pad.
- UI: opt-in-kaart bij het eerste bezoek aan de tab (zolang uit: niets op schijf), daarna
  de lijst met dag-groepen, zoekveld, app-chip, kopieer (via het bestaande
  `copyLastText_`-patroon, maar met de rij-tekst), wis-per-rij, en de permanente kopregel
  "Alleen op deze Mac · bewaart N dagen · Wis alles · Zet uit". Uitzetten laat kiezen:
  bestand bewaren of meteen wissen.

**Bestanden.** Nieuw: `history.py`. Wijzig: `samflow.py` (app-naam + hook),
`settings.py`, `mainwindow.py`, `prefs.py` (rij onder Gedrag).

**Persistentie.** `history.jsonl` in App Support (zelfde argumenten als stats; plus: dít
bestand bevat wél inhoud, dus zeker niet naast de git-checkout).

**Risico's / regels.** Privacy is hier de harde regel: geen schrijfpad zolang de toggle
uit staat, en de opt-in-kaart is de enige plek die 'm aanzet. De tap-callback-regel
(meteen terugkeren) bewaakt de app-naam-capture.

---

## Fase 5 — Woordenlijst-UI (M)

**Doel.** De leer-loop die al bestaat (`--review`, `candidates.json`) een gezicht geven.

**Wat erin zit**
- `lexicon.py` krijgt een niet-interactieve API naast `review()`: `suggestions()`
  (top-N uit `candidates.json`, zelfde ranking als `review()`), `accept(word, spelling)`,
  `map_to(word, canon)`, `ignore(word)` — allemaal dezelfde bewegingen die `review()` nu
  inline doet, zodat terminal en UI één implementatie delen. Plus `remove_term(term)` /
  `remove_mapping(heard)`: herschrijf het bestand regel-gefilterd en atomisch, met behoud
  van comments en volgorde (de bestanden blijven hand-bewerkbaar, dat is een feature).
- UI-tab: suggestie-rijen (Toevoegen / Corrigeer naar… met invoerveld / Negeer), de
  termenlijst (met AMBIGUOUS-markering zoals de mockup), de mappings-tabel met wis.
- Alles werkt live dankzij de bestaande mtime-cache — geen herstart, geen refresh-knop
  nodig behalve het herbouwen van de view na een actie (zelfde "rebuild bij openen"-
  filosofie als `panel.py`).

**Bestanden.** Wijzig: `lexicon.py` (API + remove), `mainwindow.py`. `--review` blijft
bestaan en gaat de nieuwe API gebruiken.

**Risico's / regels.** De corrector-belofte ("raakt nooit een woord buiten de lijst")
staat in `canonicalise` en die blijft onaangeroerd — de UI muteert alleen de bestanden.
`remove_term` moet de mtime-cache laten invalideren via het bestaande
`_cache.pop`-patroon.

---

## Fase 6 — Helder-verfijning (S)

**Doel.** Het merk uit de mockup in AppKit: herkenbaar, maar spaarzaam.

**Wat erin zit**
- Moduskeuze als twee klikbare kaarten (eigen `NSView` met tekenwerk: mini-scène +
  selectie-ring in klei) in onboarding én Instellingen — vervangt de v1-segmented.
- Hero-band op het dashboard: grafiet-gradiënt met klei-hint en het stille merkteken
  (tekenen zoals `_status_image` dat al doet), begroeting + held-getal.
- Zijbalk-iconen als eigen tekenwerk in de staafjes-taal (verticaal/horizontaal/A/
  schuifjes — 1:1 uit de mockup-SVG's over te zetten naar `NSBezierPath`), actief in klei.
- Wordmark + versie in de zijbalk-voet; paneel en venster delen de `_GlyphView`-tegel.
- Beweging: alleen wat er al is (de pill). Vensters statisch houden — AppKit-animaties
  toevoegen is hier circus, geen leven.

**Bestanden.** Wijzig: `mainwindow.py`, `prefs.py`, `ui.py` (gedeelde teken-helpers).

**Risico's.** Puur visueel; geen enkele harde regel in de buurt. Wel: elke kleur uit het
vaste Helder-palet (grafiet `#1E1E22`, klei `#C67B52`, groen `#33B859`), rolvast.

---

## Fase 7 — Distributie (M/L)

**Doel.** SamFlow deelbaar maken buiten deze machine, zonder het release-model te breken
(main = release; auto-update = git fast-forward; bundle wordt lokaal gebouwd).

**Wat erin zit**
- App-icoon: `make-icon.py` bijwerken naar het merk-icoon uit de mockup (grafiet-tegel,
  witte staafjes, klei-hint) en `SamFlow.icns` regenereren — dat icoon is in fase 2 ook
  het dock-icoon.
- Ondertekening: minimaal ad-hoc (`codesign -s -`) in `install.sh` zodat de bundle een
  stabiele identiteit heeft; optioneel een zelf-ondertekend certificaat. Let op de
  TCC-val: identiteitswissel van de bundle kan rechten resetten — dus signing vóór het
  eerste `--grant` van een nieuwe gebruiker, en op bestaande installaties documenteren
  dat een her-sign eenmalig opnieuw rechten vraagt.
- DMG voor derden: een DMG met de repo + een begeleide `install.sh`-run (het git-model
  blijft hét update-kanaal; de DMG is een nette voordeur, geen tweede release-kanaal).
  Inclusief het "Toch openen"-verhaal (rechtsklik → Open) voor niet-genotariseerde apps.
- README-hoofdstuk "Delen met anderen".

**Bestanden.** Wijzig: `macos/make-icon.py`, `install.sh`, `README.md`. Nieuw:
`macos/make-dmg.sh`.

**Risico's.** De TCC-val is hier het echte gevaar (zie boven). Notarisatie vergt een
betaald Apple Developer-account — bewust buiten scope tot daar behoefte aan is.

---

## Fase 8 — Telemetrie (UITGESTELD, optioneel, S)

`telemetry.py` bestaat al en is compleet: anonieme dagelijkse heartbeat (install-id,
versie, macOS-versie), hard inert zolang `HEARTBEAT_URL` leeg is, live uitzetbaar via de
bestaande `share_usage`-toggle. Deze fase is alleen: een sink opzetten (Google Apps
Script of Cloudflare Worker), de URL invullen, en in de UI benoemen wat er meegaat.
Niets hiervan blokkeert een eerdere fase; expliciet als laatste en optioneel.

---

## Nieuwe persistentie — samenvatting

| Bestand | Plek | Inhoud | Default | Waarom daar |
|---|---|---|---|---|
| `stats.json` | App Support/SamFlow | dag-aggregaten, géén tekst | aan (toggle) | buiten repo/git; telemetry-map bestaat al |
| `history.jsonl` | App Support/SamFlow | dictaten + app-naam, 0600 | **uit (opt-in)** | inhoud hoort nooit naast een git-checkout |
| `settings.json` | repo-dir (ongewijzigd) | + `app_mode`, `stats_enabled`, `history_enabled`, `history_days` | zie fases | watchdog.sh leest dit pad rechtstreeks |
| `lexicon.txt` / `mappings.txt` / `candidates.json` | repo-dir (ongewijzigd) | als nu | als nu | bestaand contract, hand-bewerkbaar |

## Volgorde en afhankelijkheden

1 → 2 (venster moet bestaan voor de modus iets betekent) → 3/4/5 in willekeurige
volgorde (alle drie hangen alleen op de schil; 3 vóór 4 is logisch omdat het dashboard
"Recent" pas met fase 4 historie kan tonen) → 6 (polish over alles heen) → 7 → 8.
Elke fase eindigt met een draaiende daemon + `--check` groen; geen fase laat een
half venster achter. **Fase O staat hier los van**: 't raakt alleen de transcriptie-
pijplijn (`cleanup.py`), niet de app-schil, en start nu met Route A — parallel aan alles.

## Open beslissingen (voor Sam)

1. **Stats standaard aan?** Voorstel: ja — het zijn tellingen zonder inhoud, met toggle.
   Alternatief: net als historie opt-in maken, dan opent het dashboard leeg.
2. **Onboarding-voorselectie:** Basic voorgeselecteerd als "zoals nu" (mijn voorstel),
   of geen voorselectie en een verplichte keuze?
3. **Historie-retentie:** 30 dagen default, instelbaar als 7/30/altijd — akkoord?
4. **"Recent" op het dashboard** zolang historie uit staat: alleen het laatste dictaat
   (geheugen, zoals nu), of de rij helemaal verbergen?
5. **Distributie-ambitie:** is de DMG-voordeur voor derden nu al gewenst (fase 7 naar
   voren), of blijft dit een privé-app tot de rest staat?
6. **Ondertekening:** ad-hoc volstaat voor eigen gebruik; zelf-ondertekend certificaat
   alleen nodig als de DMG er komt. Welke ambitie heeft prioriteit?
