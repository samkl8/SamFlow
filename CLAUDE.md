# Project: samflow — lokale dictatie (Wispr Flow-kloon)

## Wat dit project doet
Fn ingedrukt houden neemt op, loslaten transcribeert lokaal en plakt de tekst in het actieve
venster. Drie processen: `samflow.py` (Fn-tap + mic + plakken), `whisper-server` (het warme
model), `cleanup.py` (vocab-prompt + regels). Alles blijft op deze machine.

## Vaste instellingen
- **Model:** `models/ggml-large-v3-turbo-q5_0.bin`, bediend door `whisper-server` op
  `127.0.0.1:8181`. Warm houden is niet optioneel: koud kost een dictaat 11s, warm 0,5s.
- **Python:** de venv draait op een **door uv beheerde** 3.12, niet die van Homebrew.
  Zie "De TCC-val" in `README.md` — verander dit niet zonder die sectie te lezen.
- **Taal:** `LANGUAGE = "nl"` in `samflow.py`.

## Werkwijze bij een gemiste transcriptie
Dit is de onderhoudslus van het project. Hoor je een woord dat er verkeerd uitkomt:

1. Reproduceer met het echte audiofragment als dat er is, anders met `--once`.
2. Kijk naar de **ruwe** Whisper-output, niet naar de opgeschoonde:
   ```python
   samflow.transcribe(samflow.wav_bytes(audio))   # print de repr()
   ```
   De ruwe string bevat newlines en leidende spaties die je in de nette output niet ziet.
3. Kies de juiste laag:
   - Is het een woord dat Whisper simpelweg niet kent? → toevoegen aan `VOCAB`. Dit is
     altijd de eerste keuze: nul latency, nul kosten. Houd de lijst kort.
   - Blijft het na een `VOCAB`-toevoeging fout gaan (fonetisch te ver weg, zoals `git hab`
     voor `GitHub`)? → een regel in `REPLACEMENTS`.
4. Voeg het geval toe aan `EXAMPLES` in `cleanup.py` en draai `python cleanup.py`. Elk
   opgelost geval hoort daar te blijven staan.

## Regels bij het aanpassen van cleanup.py
- **Wijzig nooit een regex zonder een voorbeeld toe te voegen dat 'm afdwingt.** Elk
  `REPLACEMENTS`-patroon bestaat omdat er ooit een echte misser was.
- Let op valse positieven in het Nederlands. `_collapse_stutter` heeft daarom `STUTTER_ALLOW`
  ("het feit **dat dat** werkt"), en `_sentence_case` kapitaliseert alleen ná witruimte
  (anders wordt `example.com` → `Example.Com` en `versie 3.5 is af` → `3.5 Is af`).
- `_join_segments` steunt op een empirisch feit: whisper-server zet vóór elk écht segment een
  spatie, maar een verdwaalde newline midden in een woord heeft die niet. Dat verschil is de
  hele discriminator. Verifieer met een echte transcriptie voordat je dit aanraakt.
- De `HALLUCINATIONS`-lijst mag alleen de **volledige** output afkeuren, nooit een deel ervan.
  `Ga naar example.com` moet blijven staan; kale `Www.Nil.Com.Br` niet.

## Regels bij het aanpassen van samflow.py
- **Nooit stilte naar Whisper sturen.** Het model verzint dan zinnen (echt gebeurd:
  2s stilte → `Www.Nil.Com.Br`). De energie-poort in `handle()` is de eerste verdediging,
  `HALLUCINATIONS` de tweede.
- `loudest_rms()` meet het luidste venster van 100 ms, niet het gemiddelde. Een korte zin in
  een lange opname zou anders als stilte worden weggegooid.
- Blokkeer de CFRunLoop nooit. De Fn-callback moet meteen terugkeren; transcriberen gebeurt
  in een aparte thread. Doe je dat niet, dan mist de tap toetsaanslagen.
- Concludeer nooit uit "de stream opende" dat de mic werkt. Een geweigerde microfoon levert
  op macOS nullen op, geen fout. Vraag AVFoundation.

## Regels bij het aanpassen van hud.py
- **De pill mag nooit focus pakken.** Het is een `NSPanel` met
  `NSWindowStyleMaskNonactivatingPanel`, getoond met `orderFrontRegardless()`. Gebruik nooit
  `makeKeyAndOrderFront_`: dan gaat de `Cmd+V` die erop volgt naar de pill in plaats van naar
  de editor waar je in stond.
- **Alle AppKit-calls op de main thread.** Achtergrondthreads schrijven alleen naar
  `Hud.state` / `Hud.level`; een 30 fps `NSTimer` op de main thread leest die en tekent.
- `NSApp.run()` draait dezelfde main run loop waar de event tap aan hangt. Vervang dat niet
  door een eigen loop naast `CFRunLoopRun()` — dan mist de Fn-tap events.
- De balken worden gevoed door de échte mic-RMS. Vervang dat niet door een animatie: het feit
  dat ze alleen bewegen als de microfoon je hoort, is precies de diagnostische waarde.

## Regels bij het aanpassen van focus.py
- **Quartz telt y naar beneden vanaf het hoofdscherm, Cocoa naar boven.** `to_cocoa()` is de
  enige plek waar geflipt wordt. Stel je hebt drie schermen, waarvan twee *boven* het hoofdscherm:
  vensters daar hebben een **negatieve** Quartz-y. Een flip die dat niet aankan zet de pill op
  het verkeerde scherm, en dat merk je niet op één monitor.
- De caret wordt maar één keer opgevraagd, op het moment dat de pill verschijnt. Niet elke
  frame: dat jittert, en de caret beweegt toch niet terwijl Fn ingedrukt is.
- `AXUIElementSetMessagingTimeout` staat op 0,15s. De Fn-callback draait op de main thread; een
  hangende app mag die nooit blokkeren.

## Regels bij het aanpassen van media.py
- **Nooit `play` sturen zonder dat wíj gepauzeerd hebben.** `MediaGuard._paused` is die
  boekhouding. Zonder haar start een dictaat de muziek die je net zelf had uitgezet.
- **Leid "speelt er iets" nooit af uit de audio-IO alleen.** Spotify houdt die na een pauze
  nog ~2,6 seconden open. Apps in `SCRIPTABLE` vragen we hun eigen `player state`.
- **AppleScript naar een app die niet draait, start die app.** Altijd de `is running`-guard
  eromheen. Getest: zonder guard lanceert een Fn-druk Music.app.
- `NSAppleScript` in-process kost 27 ms, `osascript` als subproces 132 ms. Dit draait op de
  main thread bij Fn-omlaag, dus dat verschil is het verschil tussen wel en niet merkbaar.
- MediaRemote *uitlezen* (`MRMediaRemoteGetNowPlayingApplicationIsPlaying`) is sinds macOS
  15.4 geblokkeerd; een block via `ctypes` crasht het proces. Niet opnieuw proberen.

## Regels bij het aanpassen van audiodev.py
- **Neem nooit op van een Bluetooth-mic zonder reden.** Dat trekt de output van diezelfde
  koptelefoon naar telefoonkwaliteit (bewezen: AirPods 48→24 kHz). De hele module bestaat
  hiervoor; sloop de Bluetooth-check niet weg.
- Transport-type komt uit CoreAudio ('bltn'/'blue'), niet uit de naam — namen zijn
  gelokaliseerd ("MacBook Pro microfoon") en veranderen per taal. De índex en de matching
  komen uit sounddevice, dat dezelfde namen rapporteert.
- De mic wordt bij elke `_open()` opnieuw gekozen (kost 1 ms), zodat AirPods loskoppelen
  vanzelf naar de ingebouwde mic schakelt en andersom.
- Diagnose bij "muziek klinkt slecht": check eerst of er een oude samflow-instantie draait die
  de AirPods-mic vasthoudt (`pgrep -f samflow.py`). Een oude instantie met verouderde code was
  de echte oorzaak toen dit voor het eerst opdook.

## Toon van de documentatie
Nederlands, zakelijk. Leg uit *waarom* een keuze zo is gemaakt, niet alleen wat er staat —
de scherpe randen in dit project zijn allemaal ooit een bug geweest.
