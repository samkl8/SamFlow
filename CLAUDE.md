# Project: samflow — lokale dictatie-app

## Wat dit project doet
Fn ingedrukt houden neemt op, loslaten transcribeert lokaal en plakt de tekst in het actieve
venster. Drie processen: `samflow.py` (Fn-tap + mic + plakken), `whisper-server` (het warme
model), `cleanup.py` (vocab-prompt + regels). Alles blijft op deze machine.

## Vaste instellingen
- **Model:** `models/ggml-large-v3-turbo-q8_0.bin`, bediend door `whisper-server` op
  `127.0.0.1:8181` met beam search (`-bs 5`). Warm houden is niet optioneel: koud kost een
  dictaat 11s, warm 0,5s. Gemeten: q8 i.p.v. q5 is strikt preciezer, níét trager (q8_0
  dequantiseert simpeler op Metal) en kost ~+390 MB RAM. Beam search kost ~50 ms.
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
   - Is het een woord dat Whisper niet kent, of mist alleen de hoofdletters/splitsing?
     → zet de canonieke vorm in `lexicon.txt`. `lexicon.canonicalise` snapt voortaan elke
     variant terug (`market os`, `marketos` → `MarketOS`). Nul latency, en het werkt bij
     het volgende dictaat — geen herstart (de lijst wordt per dictaat herlezen).
   - Blijft het fonetisch te ver weg (je zegt Klaviyo, er komt `klavijo`)? → een mapping
     `klavijo = Klaviyo` in `mappings.txt`.
4. De makkelijke route voor beide: `python samflow.py --review`. Samflow heeft onbekende
   woorden die je vaak zei al geteld en stelt ze voor; jij kiest toevoegen of mappen.
5. Voor een ingebouwde default of regel: voeg 'm toe aan `DEFAULT_TERMS` (lexicon.py) of
   `REPLACEMENTS` (cleanup.py) én aan `EXAMPLES` in `cleanup.py`, en draai
   `python cleanup.py`. Elk opgelost geval hoort daar te blijven staan.

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

## Regels bij het aanpassen van lexicon.py
- **De corrector mag nóóit een woord buiten de lijst aanraken.** Dat is de hele belofte.
  `canonicalise` matcht alleen de letters van een term die de gebruiker zélf toevoegde, en
  tolereert een spatie/koppelteken uitsluitend op de eigen grenzen van de term (camelCase,
  cijfer, koppelteken). Daarom wordt `de markt` nooit `MarketOS`. Raak je dit aan, voeg dan
  een voorbeeld toe dat een echt Nederlands woord met rust laat (zie de `markt/meta`-regel
  in `EXAMPLES` van cleanup.py).
- **Termen die óók een gewoon woord zijn horen in `AMBIGUOUS`.** Die gaan wel mee in de
  Whisper-prompt maar worden niet overal met hoofdletter geforceerd (`meta` → niet `Meta`).
- **lexicon.txt en mappings.txt zijn persoonlijk en staan buiten git.** De ingebouwde
  basislijst is `DEFAULT_TERMS` (wél getrackt). Een bijna-leeg lexicon.txt is normaal: de
  leer-loop en handmatige toevoegingen vullen het.
- **De bestanden worden per dictaat opnieuw gelezen (mtime-cache).** Een woord toevoegen
  werkt dus meteen, zonder herstart. Sloop de cache-sleutel op mtime niet weg, anders moet
  je weer herstarten voor elke wijziging.
- De leer-loop (`record`) telt alleen woorden die niet bekend en niet in `STOPWORDS` staan.
  Die lijst hoeft niet volledig — hij haalt de grootste ruis eruit; de rest negeer je in
  `--review`. `AUTO_PROMOTE` staat bewust uit: automatisch toevoegen pakt ook rommel.

## Regels bij het aanpassen van samflow.py
- **Nooit stilte naar Whisper sturen.** Het model verzint dan zinnen (echt gebeurd:
  2s stilte → `Www.Nil.Com.Br`). De energie-poort in `handle()` is de eerste verdediging,
  `HALLUCINATIONS` de tweede.
- `loudest_rms()` meet het luidste venster van 100 ms, niet het gemiddelde. Een korte zin in
  een lange opname zou anders als stilte worden weggegooid.
- Blokkeer de CFRunLoop nooit. De Fn-callback moet meteen terugkeren; transcriberen gebeurt
  in een aparte thread. Doe je dat niet, dan mist de tap toetsaanslagen.
- **Houd `Recorder.lock` nooit vast over een CoreAudio-call heen.** `stream.stop()/close()`
  (en `.start()`) kunnen bij een apparaatwissel op de HAL-mutex blokkeren (AUHAL `err=-10851`).
  Deed `_close()` dat vroeger mét de lock, dan blokkeerde de Fn-callback (main thread) op diezelfde
  lock → de héle app bevroor (bewezen met een stack-sample: `AudioOutputUnitStop` → `HALB_Mutex::Lock`).
  Daarom: ref eruit swappen ónder de lock, stop/close erbuiten. De lock beschermt alleen de
  Python-staat (frames/preroll/stream-ref), nooit een blokkerende C-call.
- **Een audio-fout mag de Fn-callback nooit als exceptie bereiken.** `_open()` vangt CoreAudio-
  fouten af (mislukt openen = dit dictaat neemt niets op, volgende Fn-druk probeert opnieuw);
  een geraiseerde fout in de listen-only event-tap zou 'm stilleggen.
- Concludeer nooit uit "de stream opende" dat de mic werkt. Een geweigerde microfoon levert
  op macOS nullen op, geen fout. Vraag AVFoundation.

## Regels bij het aanpassen van hud.py
- **De pill mag nooit focus pakken.** Het is een `NSPanel` met
  `NSWindowStyleMaskNonactivatingPanel`, getoond met `orderFrontRegardless()`. Gebruik nooit
  `makeKeyAndOrderFront_`: dan gaat de `Cmd+V` die erop volgt naar de pill in plaats van naar
  de editor waar je in stond.
- **Alle AppKit-calls op de main thread.** Achtergrondthreads schrijven alleen naar
  `Hud.state` / `Hud.level`; een 60 fps `NSTimer` op de main thread leest die en tekent
  (60 i.p.v. 30 sinds de entrance/exit-springs — een soepele veer wil meer frames; de
  mini-view is spotgoedkoop om te tekenen).
- `NSApp.run()` draait dezelfde main run loop waar de event tap aan hangt. Vervang dat niet
  door een eigen loop naast `CFRunLoopRun()` — dan mist de Fn-tap events.
- De balken worden gevoed door de échte mic-RMS. Vervang dat niet door een animatie: het feit
  dat ze alleen bewegen als de microfoon je hoort, is precies de diagnostische waarde.
- **Bouw het paneel vers op elk moment dat de pill verschijnt.** Een `NSPanel` die is aangemaakt
  terwijl er nog een extern scherm hing, blijft na het loskoppelen verweesd op dat verdwenen
  scherm: `orderFrontRegardless()` mét een geldige `setFrameOrigin_` toont hem dan niet meer op
  het overgebleven scherm — de pill lijkt helemaal weg. Daarom bouwt `_place()` het paneel bij
  élke idle→zichtbaar-overgang opnieuw op (kost niets, en een vers paneel rendert altijd). De
  observer op `NSApplicationDidChangeScreenParametersNotification` (`_rebuild_panel`) is het extra
  vangnet voor een schermwissel terwijl de pill al zichtbaar is. Vertrouw niet op die notificatie
  alléén: gebleken is dat 'ie niet altijd aankomt, en dan was de pill weer weg. (De placement-
  wiskunde is onschuldig — die leest de schermen elk dictaat live.)

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
- De mic wordt bij elke `_open()` opnieuw gekozen, zodat AirPods loskoppelen vanzelf naar de
  ingebouwde mic schakelt en andersom. **Dat werkt alleen mét de `audiodev.refresh()` die
  `_open()` er vlak vóór aanroept.** PortAudio (V19 op CoreAudio) enumereert apparaten éénmalig
  bij proces-start en ziet hotplug niet; de app draait dagen. Zonder de re-init blijft
  `choose_input()` op de bevroren lijst kijken: haal je AirPods eruit, dan toont sounddevice ze
  nog en wijst `sd.default.device` naar het verdwenen apparaat, dat langs de Bluetooth-check
  glipt (staat niet meer in de live CoreAudio-`transports()`) en als "gewone default"
  terugkomt → `InputStream(device=None)` opent het dode apparaat → stilte. `transports()` is wél
  altijd live (rechtstreeks CoreAudio); enkel de sounddevice-helft bevriest. `refresh()`
  (`sd._terminate()/_initialize()`, ~3 ms) mag alléén als er geen stream open staat — `_open()`
  is de juiste plek (self.stream is daar None); doe 't nooit op de status-/labelpaden
  (`check()`, dashboard-mic-chip), want daar kan een opname-stream openstaan.
- Diagnose bij "muziek klinkt slecht": check eerst of er een oude samflow-instantie draait die
  de AirPods-mic vasthoudt (`pgrep -f samflow.py`). Een oude instantie met verouderde code was
  de echte oorzaak toen dit voor het eerst opdook.

## Toon van de documentatie
Nederlands, zakelijk. Leg uit *waarom* een keuze zo is gemaakt, niet alleen wat er staat —
de scherpe randen in dit project zijn allemaal ooit een bug geweest.
