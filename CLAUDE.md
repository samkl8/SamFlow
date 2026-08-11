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

## Regels bij het aanpassen van snippets.py
- **Snippets zijn de állerlaatste laag** in `handle()`: ná `cleanup.clean` én ná `polish.polish`,
  vlak vóór `paste`. Bewust — zo verbouwt geen enkele laag (zeker niet het oppoets-model) een
  ingevoegde URL of handtekening nog. Fail-silent aangeroepen: een fout in de expansie mag het
  dictaat nooit ophangen (zelfde contract als `lexicon.record`).
- **`apply()` mag nóóit iets buiten de lijst aanraken** — dezelfde belofte als
  `lexicon.canonicalise`. Een trigger matcht alleen als **hele frase** (genormaliseerd:
  kleinletters, flexibele witruimte), begrensd door woordgrenzen (`(?<!\w)…(?!\w)`), zodat
  "de site" nooit in "sitemap" vuurt. Alle triggers gaan in **één regex-pas, langste eerst**:
  zo wordt een net ingevoegde expansie nooit zelf opnieuw als trigger gelezen, en wint een
  langere frase van een kortere die erin zit. Raak je de matcher aan, breid dan
  `test_snippets.py` (scratchpad-stijl) uit — elk geval staat er omdat het een echte val was.
- **Opslag in App Support, `0600`** (`snippets.json`), níét naast een git-checkout: een snippet
  kan een handtekening of bankgegevens bevatten (zelfde reden als `history.jsonl`). **Per
  dictaat herlezen via mtime-cache** (zoals lexicon) — een nieuwe snippet werkt meteen, zonder
  herstart. Sloop de mtime-sleutel niet weg.

## Regels bij het aanpassen van polish.py
- **Een afgekapt antwoord is gevaarlijker dan een raar antwoord.** Raakt het model
  `num_predict` op (Ollama meldt dat als `done_reason == "length"`), dan stopt de tekst
  middenin een zin — en `_sane` mérkt dat niet, want die kijkt alleen naar lengteverhouding.
  Gemeten: een dictaat van 371 woorden kwam er op 89% van het origineel uit, netjes binnen
  de vangrail, met de laatste alinea eraf. Sloop de `done_reason`-check niet weg, en verlaag
  `num_predict` nooit tot een vaste waarde.
- **Ruimte en timeout horen bij de lengte van de tekst, niet bij een constante**
  (`_budget`). `_sane` accepteert tot ~1,6x de invoer, dus daar is `num_predict` op gedimen-
  sioneerd. De timeout heeft wél een plafond (`_TIMEOUT_MAX`): een dictaat dat pas na een
  minuut geplakt wordt is erger dan een dictaat zonder oppoetsen.
- Test een wijziging hier nooit met herhaalde audio. Een model dat drie identieke alinea's
  terecht samenvat, valt op `_sane` terug en dat lijkt dan een bug in je wijziging.

## Regels bij het aanpassen van samflow.py
- **Nooit stilte naar Whisper sturen.** Het model verzint dan zinnen (echt gebeurd:
  2s stilte → `Www.Nil.Com.Br`). De energie-poort in `handle()` is de eerste verdediging,
  `HALLUCINATIONS` de tweede.
- `loudest_rms()` meet het luidste venster van 100 ms, niet het gemiddelde. Een korte zin in
  een lange opname zou anders als stilte worden weggegooid.
- **Tekst kwijtraken mag nooit stil gebeuren.** De maximale lengte is instelbaar
  (`speech_cap()`, settings `max_speech_sec`, 0 = onbeperkt; default 5 min). Loopt een
  dictaat tegen die grens, dan klinkt de foutcue en zegt de log hoeveel seconden eraf gingen.
  Dat is de les van de oude harde `MAX_SPEECH_SEC = 120`: die knipte de staart eraf zonder
  één signaal, dus wie lang dicteerde dacht dat de app hem "niet goed opnam".
- **De request-timeout naar whisper-server schaalt mee met de lengte** (halve realtime, met
  60s als bodem). Gemeten op een warme turbo: ~22x realtime, dus 5 minuten audio kost ~13s.
  Een vaste 60s was prima bij een cap van 2 minuten, maar zou een geslaagde lange
  transcriptie alsnog afbreken.
- Blokkeer de CFRunLoop nooit. De Fn-callback moet meteen terugkeren; transcriberen gebeurt
  in een aparte thread. Doe je dat niet, dan mist de tap toetsaanslagen.
- **Geen enkele call zonder bovengrens hoort op de main thread.** Dat is dezelfde thread als
  de event-tap, de pill én het venster: blokkeert daar iets, dan is de app dood — geen Fn,
  geen pill, niets, tot je 'm afknalt. `InputStream()/start()` was zo'n call: gezond 70-110 ms
  (gemeten), maar tijdens een apparaatwissel wacht 'ie onbegrensd op de HAL-mutex. Daarom
  opent de mic nu op een werkthread (`_ensure_open`/`_open_worker`) en wacht de Fn-callback
  daar hooguit `OPEN_WAIT_SEC` op. Het normale geval is ongewijzigd (openen past ruim binnen
  die deadline, en bij een warme stream raken we CoreAudio helemaal niet aan); het
  pathologische geval kost je nu één dictaat in plaats van de hele app.
- **`recording` gaat aan vóór het wachten op de mic**, niet erna. Zo landt elk blok dat
  binnenkomt meteen in `frames`, ook als de stream een fractie later pas leeft — anders
  verlies je bij een koude start de eerste woorden.
- **Eén open-poging tegelijk** (`_open_ev`). Een tweede Fn-druk tijdens een hangende open
  moet op diezelfde poging wachten, niet er nóg een CoreAudio-call bovenop gooien.
- **stdout/stderr staan op regel-buffering** (bovenaan het bestand). De app-bundle start ons
  via een shell die de uitvoer naar `~/Library/Logs/samflow.log` stuurt, en dan buffert Python
  per kilobyte: precies de regels vóór een vastloper waren wég zodra je de app afknalde. Elke
  vastloper wiste zo zijn eigen bewijs. Zet dit niet in de launcher — die zit in een ad-hoc
  gesigneerde bundle, en elke wijziging daar kost je de mic- en toetsenbordpermissies.
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

## Regels bij het aanpassen van stall.py
- De hartslag bestaat omdat een vastgelopen main thread niet zélf kan melden dat 'ie
  vastzit. Een NSTimer tikt op de run loop, een achtergrondthread kijkt of die tik nog
  komt, en dumpt anders de Python-stack van de main thread — dát is de call die hangt.
- **De timer hoort in `NSRunLoopCommonModes`, niet in de default-mode.** Een default-mode-
  timer staat stil zodra de run loop in event-tracking zit (menu open, venster slepen). Dat
  is normaal gedrag en zou een valse stack-dump opleveren; één vals alarm en je gelooft de
  volgende niet meer.
- De ObjC-klassenaam moet uniek zijn in het hele proces (`_StallTicker`, want `hud.py` heeft
  al een `_Ticker`). Twee ObjC-klassen met dezelfde naam laat PyObjC bij import knallen.

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
- **Webcontent (YouTube) pauzeer je niet, die demp je.** MediaRemote stuurt naar de één
  now-playing-app (vaak een al gepauzeerde Spotify), niet naar de browsertab — bewezen: pauze
  raakte de video niet en de `play` erna startte Spotify. Bovendien klinkt webaudio onder een
  hulp-procesnaam (`com.apple.WebKit.GPU`, "… Helper (Renderer)"), niet onder de browsernaam,
  dus de browser-entries in `MEDIA_APPS` matchten nooit. Daarom `web_sounding()` + systeem-mute.
  **Muten heeft z'n eigen boekhouding (`_muted`), los van `_paused`.** Zo sturen we `play` alleen
  voor wat we écht pauzeerden, en ontdempen we nooit een gebruiker die zélf op mute stond
  (`pause()` dempt alleen als `_output_muted()` False is). De mute is systeembreed, dus hij raakt
  ook een gesprek — daarom gaat hij alléén aan als er webcontent klinkt, niet standaard.
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
