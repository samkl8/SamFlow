# SamFlow — sessie-handoff (context voor een verse sessie)

_Doel: na een `/clear` of in een nieuwe sessie meteen verder kunnen. Wat er staat, de
staat van de code, openstaande draden en hoe je de app bedient._

**Laatste update (18 augustus 2026) — achtergrondgeluid (radio) uit de transcriptie.
Commit `0db6898` op `main`, lokaal, nog niet gepusht.**

Vraag uit gebruik: "kunnen we iets maken dat als de radio aan staat dit geluid weggefilterd
wordt?" Fysieke radio in de kamer, drie symptomen: de radio werd meegetranscribeerd, eigen
woorden kwamen verkeerd uit, en er kwam onzin bij stukken waar niet gesproken werd.

**A. De fix zit in de server, niet in het filteren: `--vad`.**

whisper-server draait nu met spraakdetectie (`--vad -vm models/ggml-silero-v5.1.2.bin`,
865 kB, in `install.sh` erbij). Geen spraaksegment → geen transcriptie. Gemeten op de echte
opnamen: radio-aan-en-zwijgen levert **niets** (0,05s) waar het eerst tekst plakte; een dictaat
mét radio komt er onveranderd uit (0,79s); stille kamer onveranderd (0,68s). Kost **+0,08s**
op een echt dictaat en scheelt **1,3s** op puur achtergrondgeluid (0,03s i.p.v. 1,37s — het
model draait dan helemaal niet).

Waarom niet de `HALLUCINATIONS`-lijst: **het is geen vaste string.** Dezelfde zes seconden
radio gaven drie keer een ánder verzinsel — `*repeat*`, `Gov. Gov. Gov. Gov.`, `Tja, Tja, Tja`.
(De server draait met `--carry-initial-prompt`, dus de uitkomst hangt af van wat er daarvóór
doorheen ging.) Die lijst blijft een vangnet en heeft `*repeat*` erbij gekregen — Whisper's
sterretjes-notatie voor niet-spraak, naast de al bestaande `[BLANK_AUDIO]` en `(...)` — met een
voorbeeld dat 'm afdwingt én een negatief voorbeeld (`*echt* nu, en *meteen*` moet blijven
staan; daarom `^\*[^*]*\*$` en niet `.*`).

**B. Twee routes gemeten en afgevallen. Staan in CLAUDE.md; niet opnieuw proberen.**

- **Apple's voice processing** (`AVAudioInputNode.setVoiceProcessingEnabled`) werkt technisch
  prima vanuit PyObjC, en de cijfers zien er prachtig uit: ruisvloer 5x omlaag, SNR van 8,2x
  naar 22,3x, en een toon uit de eigen speakers verdwijnt volledig (13,4x → 0,85x — de
  echo-onderdrukking is compleet). **En de transcriptie wordt er slechter van.** Dezelfde zin,
  dezelfde radio: ruw `"...of je mij alsnog goed hoort met de radio aan"`, met voice processing
  `"...of je meisje kan doen of je kan doen of je kan doen"` — een decoder in een herhaallus.
  Whisper is op ruizige audio getraind; de artefacten van een ruisonderdrukker verwarren 'm
  méér dan de ruis zelf. Het zou bovendien het mic-pad van sounddevice naar AVAudioEngine
  dwingen, precies de code met alle HAL-mutex-regels.
  Praktisch, mocht iemand het tóch ooit nodig hebben: `setVoiceProcessingEnabled` maakt van de
  input-node een 9-kanaals `DiscreteInOrder`-formaat, maar PyObjC geeft voor **elke**
  kanaalindex dezelfde pointer terug (alleen kanaal 0 is echt, en dat is de verwerkte mono-mic),
  en `as_buffer()` telt in floats, niet in bytes. Een mixer-node ertussen hangen werkt niet:
  met VP aan is het één gecombineerde I/O-unit en faalt `kAUInitialize` met `-10875`.
- **Een SNR-poort** (luidste 100ms-venster t.o.v. de ruisvloer) in plaats van de absolute
  `SILENCE_RMS`. Klinkt logisch, meet niet wat je denkt. Met dezelfde spraak en oplopend
  radiovolume erdoorheen gemengd: Whisper transcribeerde nog **perfect** bij SNR 3,9x, 3,1x en
  3,6x, en ging pas kapot bij 3,7x. **De SNR voorspelt niet of Whisper het redt**, dus zo'n
  poort gooit goede dictaten weg — precies wat "tekst kwijtraken mag nooit stil gebeuren"
  verbiedt.

**C. Wat er níét verandert.** In een stille kamer raakt VAD niets aan, ook niet bij 30% volume
(luidste 141, transcriptie goed). Wat daaronder stopt is de **bestaande** energie-poort
(`SILENCE_RMS = 120`, stopt bij 20% volume / luidste 94), onveranderd. Korte dictaten vallen
niet weg: nagemeten op fragmenten van 0,4–2,0s, identiek aan zonder VAD.

**Openstaande draden:**

- **De knop bij zacht praten is `--vad-threshold`** (nu de default 0,50). Fluisteren of een mic
  verder weg heb ik niet écht kunnen meten — alleen gesimuleerd door de opname zachter te
  draaien, en dan zakt de kamerruis mee terwijl die in het echt blijft staan. Komt een zacht
  dictaat er ooit niet uit: eerst de opname bewaren, dán aan die drempel draaien.
- **Foutcue bij een leeg resultaat, bewust niet gebouwd.** Bij een écht harde radio (16x in de
  test) levert VAD leeg op in plaats van onzin — beter, maar stil: het uitblijven van de
  "klaar"-cue is nu het enige signaal. Een expliciete foutcue zou ook piepen bij elke onbedoelde
  Fn-druk met de radio aan. Sam moest dat in de praktijk eerst voelen; nog niet besloten.
- **Deze machine draait nog op de oude `com.sam.samflow-server`** (symlink naar het
  gitignorede `launchd/`), niet op de `com.samflow.server` die `install.sh` genereert. Beide
  zijn bijgewerkt, maar wie hier iets aan de server-vlaggen verandert moet ze **allebei** raken.

**Laatste update (13-14 augustus 2026) — de vastloper gevónden, meertalig dicteren en een
Engelse interface. Alles staat op `main` (merge-commit `c9937f2`), dus dit is released:
de app werkt zichzelf ff-only bij vanaf main.**

**A. De vastloper — het was geen CoreAudio, het was een `stat()` per frame.**

De vorige sessie gokte op onbegrensde C-calls (mic openen, Apple Events). Die fixes waren
terecht, maar niet de dagelijkse vastloper. Wat wél hielp: `stall.py` schiet nu ook een
**native** stack via `/usr/bin/sample` (de Python-stack stopte telkens bij `app.run()`, want
er liep op dat moment geen Python — dan zegt zo'n dump niets). Dat sloot eerst alles uit:
geen mutex, geen Apple Event, geen modale run loop, en de andere threads sliepen in
`nanosleep`, dus ook geen GIL-verstopping.

De vangst kwam uit de Python-dump zélf, die één keer wél gevuld was:

```
hud.py:435  tick_ → hud.py:774 _on_tick → settings.get("show_pill")
settings.py _load → genericpath getmtime
! main thread weer vrij na 6.2s
```

De 60 fps-pill-tik deed **elke frame** een `stat()`-syscall op `settings.json`, op de main
thread. Normaal microseconden; hikt het bestandssysteem één keer, dan ligt de hele app stil.
Twee lagen fix: de lookup staat nu achter `state != "idle"` (een idle app raakt het
bestandssysteem niet aan) en `settings._load()` doet die stat hooguit 5x/sec (`RECHECK_SEC`).
Gemeten: 100.000 `settings.get()`-aanroepen kosten nu 13 ms. Regel staat in CLAUDE.md.

Ook mee: `prefs._login_item_present()` vroeg System Events op de main thread naar de
login-item-status (osascript, 3s timeout, lánceert System Events als die niet draait) — bij
elke klik op de Instellingen-tab én elke reflow tijdens een resize. Nu een werkthread met
cache; de schakelaar corrigeert zichzelf zodra het antwoord er is.

**B. "Er kwamen Chinese tekens uit" — het oppoets-model, niet Whisper.**

Echt gebeurd: `Oké, kun je me甚至 帮助 我 補正這件事？`. Dat is een woord-voor-woord vertaling
van de Nederlandse zin (甚至 = "even", 帮助 = "helpen"), die halverwege begint — qwen2.5:3b is
Chinees getraind en kiept om. whisper-server is vrijgepleit met synthetische spraak (`say` →
ffmpeg → server): vier varianten (met/zonder woordenlijst-prompt, met/zonder taal) gaven
allemaal keurig Nederlands. `_script_drift` weigert nu elke polish die een schrift introduceert
dat het dictaat niet had; dat staat náást `_kept_ratio`, want bij een lang dictaat waarvan
alléén de staart omkiept blijft het woordbehoud gewoon 1,00.

**C. Meertalig dicteren (7 talen + automatisch).**

De taal-instelling bestond al en ging naar whisper-server; alles eróm was impliciet Nederlands.
Nu doorgetrokken: `cleanup.LANGS` (commando's, opsomming-markers, stotter-uitzonderingen per
taal; een profiel vult alléén in wat we van die taal weten, de rest valt terug op de vereniging
— conservatief), het label vóór de woordenlijst in `whisper_prompt()` staat in de dicteertaal
(bij "auto" gaat het label eraf: "Woordenlijst:" duwt een Engels dictaat richting Nederlands),
en `polish.py` noemt de taal expliciet. Gemeten met qwen2.5:3b op een Duits dictaat:
"in dezelfde taal als de invoer" gaf 0,18 woordbehoud, "in het Duits" 0,91. De Nederlandse
few-shot mocht blijven staan. `_kept_ratio` is de vangrail die vertalingen tegenhoudt
(1,00 bij een echte polish, 0,07 bij een vertaling; drempel 0,5).

**D. Engelse interface (`i18n.py`, NIEUW).**

Nederlandse tekst in de code is de sleutel; vertaald wordt er in de **sinks** (`ui.label`,
`section`, `row_label`, `glabel`, `mono`, de labels van Segmented/Dropdown) plus de plekken die
rechtstreeks met AppKit praten. Tien plekken in plaats van tweehonderd, en een gemiste vertaling
is één Nederlandse regel tussen het Engels. `ui_language` = `auto` (volgt de Mac) | `nl` | `en`;
auto is de default, dus een Nederlandse Mac merkt niets en een Engelse krijgt vanzelf Engels.
Datums, weekdagen en dagdelen hebben eigen lijsten per taal in `mainwindow.py`.
**Geverifieerd door de views écht te renderen** (headless: `build_view()` + `_tab_view(0..3)` +
de wizard): 13.433 teksten, nul Nederlands, en in de NL-stand nul per ongeluk Engels. Die test
ving meteen een crash — één achtergebleven `_DAYS_NL`-verwijzing sloopte het dashboard.
`python i18n.py` is de statische helft van diezelfde controle.

**E. Kleine dingen die bij het nakijken bovenkwamen.**

- **Regressie van mezelf:** `install.sh` las de dicteertaal uit `LANGUAGE` in `samflow.py` —
  precies de dode constante die deze sessie weg is. Viel stil terug op `en` en zette `-l en` in
  de server-plist (runtime ongevaarlijk, want elk dictaat stuurt z'n eigen taal mee). Leest nu
  `settings.py`. Stap (c) van de handleiding verwees ook nog naar `VOCAB` in cleanup.py.
- Twee wizard-teksten waren onvertaald gebleven omdat mijn eerste verificatie het
  welkomstvenster niet rendeerde.
- README: de download-instructie stond nog op `git clone <this-repo>`; nu de echte URL, plus
  een gemeten geheugen-tabel (~120 MB whisper-server, ~125 MB samflow.py, ~2,5 GB ollama
  alléén met oppoetsen aan; het model is memory-mapped, dus geen 834 MB app-geheugen).

**F. Git: de bedrijfsnaam is uit de historie.**

Op verzoek "Kloeth Digital B.V." overal vervangen door "Sam Kloeth" (zijbalk, LICENSE,
Info.plist) én uit de hele historie herschreven met `git filter-repo --replace-text`, daarna
force-push van alle 7 branches. De commit die de credit veranderde werd daardoor leeg en is
gepruned: het lijkt nu alsof er altijd "Sam Kloeth" stond. **Voorbehoud, geverifieerd:** GitHub
ruimt losgekoppelde commits niet meteen op — de oude blob is nog op te halen via een directe
SHA-link, en PR #9/#10 verwijzen nog naar oude SHA's. Wil je dat óók dicht: GitHub Support om
een gc vragen, of de repo weggooien en opnieuw pushen. Backup van de oude historie stond in de
scratchpad van die sessie (`samflow-voor-rewrite.bundle`) — die is weg na een reboot.

**Openstaande draden:**

- `macos/design/hosted-versie-plan.md` staat **bewust untracked**: prijsstelling, omzet en de
  belastingkant horen niet in een publieke repo.
- Reddit-post over SamFlow ligt klaar in derde persoon mét disclosure-regel. Sam vroeg om een
  versie die klinkt als een willekeurige gebruiker die de app "gevonden" heeft; dat is
  geweigerd (nepaanbeveling van je eigen project). Alternatief genoemd: r/opensource of
  r/SideProject, waar "I built this" gewoon het format is.
- Grootste gat richting vreemden blijft **distributie**: installeren is `git clone` +
  `./install.sh`, er is geen gesigneerde DMG. Ter vergelijking uitgezocht:
  [SpeakType](https://github.com/karansinghgit/speaktype) (Swift + WhisperKit, 393 sterren) doet
  dat wél, en verdient via **Polar.sh** met 14 dagen proef, daarna 10 dictaten/dag en woordenlijst
  + export achter Pro. Relevant voor het hosted-plan: Polar is een derde optie naast Lemon
  Squeezy en Stripe. Hun zwakke plek is juist onze sterke: 30-60s koude start bij het eerste
  model, en hun woordenlijst is een platte zoek-vervang ná de transcriptie.
- `history.top_words()` leest bij elke reflow de hele `history.jsonl` opnieuw, terwijl de rest
  van dat pad een mtime-cache heeft. Nu 292 kB, dus geen probleem — het groeit wel mee.

**Laatste update (11 augustus 2026) — lange dictaten & de vastlopers (branch
`fix/lange-dictaten-en-vastlopers`, 6 commits, gepusht, PR nog te openen):**

Twee klachten uit echt gebruik, allebei tot op de oorzaak uitgezocht en per oorzaak atomair
gecommit. Werkboom achteraf byte-vergeleken met de versies waarop de metingen zijn gedaan
(identiek); elke commit compileert ook los, dus `git bisect` blijft bruikbaar.

**A. "Hele lange berichten worden niet goed opgenomen" — twee onafhankelijke oorzaken.**

- **De harde afkap (`f2aa3f0`).** `MAX_SPEECH_SEC` stond hard op 120s en `handle()` knipte de
  staart er zonder één signaal af. Nu instelbaar: `speech_cap()` leest `max_speech_sec`
  (default **300 = 5 min**, `0` = onbeperkt), UI-rij "Maximale lengte" in Dicteren (1/2/5/15
  min of Onbeperkt). Onzin in settings.json (negatief, tekst, <5s) valt terug op de constante.
  Afkappen geeft nu **foutcue + logregel** met het aantal verloren seconden. `transcribe()`
  had een vaste `timeout=60`; die schaalt mee (halve realtime, 60s bodem) — anders breekt een
  geslaagde lange transcriptie alsnog af.
- **Het oppoetsen (`551736c`) — dit was de stille.** `num_predict` stond vast op 512 tokens:
  het model stopte middenin een zin en `_sane()` zag dat niet, want die kijkt alleen naar
  lengteverhouding. **Gemeten met een echt dictaat van 371 woorden: output op 89% van het
  origineel, laatste alinea eraf, geen melding.** Met polish áán plakte je dus een half
  bericht zonder het te weten. `_budget()` schaalt ruimte + timeout nu mee met de invoer
  (plafond 60s), en `done_reason == "length"` is een harde vangrail geworden.

**B. "SamFlow loopt af en toe vast" — symptoom: Fn dood, hele app dood tot herstart.**

Mechanisme: de main thread is tegelijk de run loop van de event-tap, de pill én het venster.
Op het Fn-omlaag-pad stonden twee **onbegrensde** C-calls. Bewijs dat deze Mac daar regelmatig
in komt: **42 AUHAL-fouten in de log**, waarvan 39× `err='!obj'` (audio-object dat onder ons
vandaan verdween) en 3× `Audio Hardware Not Running` vlak vóór de laatste herstart.

- **`270646c` — log niet meer blok-bufferen.** De app-bundle start ons via een shell die stdout
  naar de log stuurt → Python bufferde per kilobyte, dus **elke vastloper wiste zijn eigen
  bewijs**. Aangetoond: het logbestand liep 22 minuten achter op het laatste dictaat. Alleen de
  AUHAL-regels overleefden (die schrijft PortAudio in C naar stderr). Opgelost in `samflow.py`
  zelf, **niet in de launcher**: die zit in een ad-hoc gesigneerde bundle en elke wijziging daar
  kost je de mic- en toetsenbordpermissies. (`PYTHONUNBUFFERED` in `com.sam.samflow.plist` geldt
  alleen voor de launchd-route — **die service is niet geladen**, de app draait via de bundle.)
- **`56532fb` — `stall.py` NIEUW.** NSTimer tikt op de run loop, achtergrondthread kijkt of die
  tik nog komt; >2s stil → **Python-stack van de main thread naar de log**. Timer in
  `NSRunLoopCommonModes` (default-mode staat stil bij menu-tracking → vals alarm). Klassenaam
  `_StallTicker`, want `hud.py` heeft al een `_Ticker` en ObjC-namen zijn procesbreed.
- **`fb18b02` — de mic gaat niet meer open op de main thread.** `InputStream()/start()` pakt de
  HAL-mutex: gezond 70-110 ms, tijdens een apparaatwissel onbegrensd. Nu `_ensure_open()` /
  `_open_worker()` op een werkthread, met `OPEN_WAIT_SEC = 0,25s` als deadline in de callback.
  `recording` gaat aan **vóór** het wachten (anders verlies je de eerste woorden), en één
  open-poging tegelijk via `_open_ev`. De eerdere fix in `_close()` ging over de *lock*, niet
  over de *thread* — het openen stond er nog gewoon op.
- **`d02eab9` — AppleScript-timeout.** `_really_playing()` stuurt bij elke Fn-druk een Apple
  Event naar Spotify/Music; zonder `with timeout` wacht AppleScript minuten. Nu 2 seconden
  (normaal antwoordt 'ie in ~27 ms). In de log stond vlak vóór de laatste vastloper 5× achter
  elkaar `⏸ Spotify`.

**Metingen (deze Mac, warme staat).** Transcriptie: 30s → 0,8s; 2 min → 5,1s; **5 min → 13,5s
(~22× realtime, 1067 woorden)**. Mic openen: warm **0,01 ms** (was 71-109 ms), koud 116 ms;
met een nagebootste hang van 10s geeft `start()` de main thread na 256 ms terug en pakt het
dictaat alsnog audio op zodra de mic opengaat. Oppoetsen: 371 woorden → 7,2s, volledig.

**Operationeel.** Branch gepusht, **PR nog niet geopend**:
`https://github.com/samkl8/SamFlow/pull/new/fix/lange-dictaten-en-vastlopers`. Lokale `main`
staat nog op `e44545a`; auto-update trekt dit pas na de merge. **De app draait al wél op deze
code** (herstart 11 aug 17:51, draait uit de werkboom). Herstarten: `pkill -f
"SamFlow.app/Contents/MacOS/SamFlow"; pkill -f samflow.py` → `open -a SamFlow`;
**whisper-server (launchd) nooit meeherstarten**.

**Openstaand.** (1) PR openen + mergen = release. (2) De design-mockups en dit handoff-bestand
staan nog **bewust ongecommit** — ander werk, niet in deze PR gestopt. (3) Loopt het tóch nog
een keer vast: `grep "main thread staat" ~/Library/Logs/samflow.log` geeft nu de hangende call;
`sample $(pgrep -f samflow.py) 5` geeft de C-stack erbij. Pas dán weten we zeker of CoreAudio
het was — dat is met dit werk nog steeds *niet* bewezen, alleen zeer aannemelijk gemaakt.

**Correctie op eigen werk deze sessie:** de nieuwe `stall.py`-sectie belandde eerst midden in
de samflow-regels van CLAUDE.md (drie bullets hingen onder de verkeerde kop); rechtgezet vóór
het committen.

---

**Laatste update (22 juli 2026) — dashboard-heatmap, snippets & "Jouw stem" (PR #9 → main `e44545a`, GEMERGED):**

Voortbordurend op Wispr-inspiratie: drie features + een layout-fix, gebouwd op branch
`feat/heatmap-en-snippets`, headless geverifieerd, per onderdeel atomair gecommit. **PR #9
gemerged → lokale `main` = `e44545a`** (auto-update = release; lokale repo staat weer op
`main`, ff-only ok). Design-mockups in `macos/design/` (buiten git, ter beoordeling).

- **Correctie op de vorige handoff:** de oppoets-fix `ac1457f` (few-shot vakantie-zin-lek) is
  **niet** in PR #8 meegegaan — die merge stopte bij `ce27aa7`; `ac1457f` werd 17 min ná de
  merge gecommit en bleef op de branch staan. Nu alsnog gereleased als ancestor in PR #9.

- **Reeks-heatmap (feat, mainwindow.py + stats.py).** Het "Reeks"-tegeltje vervangen door een
  GitHub-achtige kalender-heatmap (groen = merk); streak-getal in de kaartkop + "langste · N
  dagen". `_Heatmap` NSView: kiest week-kolommen op vensterbreedte (`_heatmap_layout` /
  `_heatmap_height`, pitch gecapt op 17), niveaus 1-4 t.o.v. 85e percentiel, hover → woorden/dag
  via **één tracking-area met `NSTrackingMouseMoved`** (werkt zonder acceptsMouseMovedEvents).
  Tooltip = grafiet-pil in drawRect_ + los label, binnen bounds geklemd. `stats.summary()` kreeg
  `heatmap_days` ({iso: woorden}, 26 wk) + `longest_streak`.

- **Snippets (feat, `snippets.py` NIEUW + samflow.py + mainwindow.py).** Trigger-frase → expansie
  ("mijn linkedin" → URL). **Laatste pijplijn-laag** in `handle()` (ná cleanup én polish),
  fail-silent. Matcher = zelfde belofte als `lexicon.canonicalise`: hele frase op woordgrenzen
  (`(?<!\w)…(?!\w)`), genormaliseerd, **één regex-pas langste-eerst** (ingevoegde expansie wordt
  niet zelf opnieuw gescand). Opslag `~/Library/Application Support/SamFlow/snippets.json` (0600,
  mtime-cache, per dictaat herlezen). UI: rustige kaart onderin **Woordenlijst** (geen 5e tab),
  `_present_sheet("snippet")` = trigger-veld + meerregelige expansie. CLAUDE.md kreeg een
  snippets-sectie met de invarianten.

- **"Jouw stem"-kaart (feat, mainwindow.py + stats.py + history.py).** Feitelijke voice-samenvatting
  onderaan Overzicht, **net boven Recent**: spreektempo (`wpm`), gem. lengte (`avg_len`),
  piek-dagdeel (dagdeel-staafjes) + "meest gezegd" (**alleen bij historie-aan** → `history.top_words()`,
  stopwoorden weg; anders een "zet historie aan"-uitnodiging) + stijl-chip (afgeleid label uit
  lengte+tempo via `_voice_style`, **géén LLM**). `stats.record()` telt nu ook het dagdeel bij —
  **inhoudsloos: wannéér je dicteert, nooit wát** (oude dagen missen `dayparts`, dus de piek vult
  zich pas vanaf nieuwe dictaten). Bewust géén gamification/share/persoonlijkheid ("geen slop" —
  expliciete Sam-voorkeur).

- **Layout-fix (fix, mainwindow.py + stats.py).** 3 tegels lieten een gat in de 2-koloms-stand;
  4e tegel **"Totaal gedicteerd"** (`stats.total_words`) teruggebracht → even grid (2×2 / 4×1).
  Niet naar 3-koloms geforceerd (dan kappen labels af op smalle vensters).

**Nieuw bestand:** `snippets.py`. **Gewijzigd:** `stats.py`, `history.py`, `mainwindow.py`,
`samflow.py`, `CLAUDE.md`. **Design-mockups (untracked, buiten PR, ter referentie):**
`insights-upgrade-`, `insights-snippets-sober-`, `snippets-`, `jouw-stem-`,
`jouw-stem-plaatsing-mockup.html`.

**Operationeel:** lokale `main` = `e44545a` = release. Branch `feat/heatmap-en-snippets` mag
opgeruimd (lokaal + remote). App draait live met de nieuwe code. Herstart = `pkill -TERM -f
"Code/samflow/samflow.py"` → `open -a "SamFlow"`; **whisper-server (launchd, pid ~1066) nooit
meeherstarten**. `screencapture` blijft geblokkeerd → headless verifiëren + Sam laten kijken.

**Openstaand / volgende ideeën (besproken, nog te bouwen):** Route B-setupkaart (mockup
`polish-setup-mockup.html`, goedgekeurd), per-app "waar je dicteert" (privacy-keuze),
Engelse UI-lokalisatie (groot, eigen traject).

---

**Laatste update (21 juli 2026) — media stilhouden (PR #7 → main `3d7063a`) + vier fixes (PR #8, open):**

Onderhoudslus vanuit echt gebruik. PR #7 gemerged naar `main` (= release); PR #8 staat **open** met vier
fixes op branch `fix/menubalk-paneel-en-historie` (3 commits). App na elke stap herstart en live geverifieerd.

- **Media stilhouden tijdens dictaat (feat, PR #7 → main `3d7063a`).** Spotify/Music werden al
  gepauzeerd, maar een `<video>` in de browser (YouTube) niet. Twee oorzaken, gemeten: (1) webaudio
  klinkt niet onder de browsernaam maar via een **hulpproces** (`com.apple.WebKit.GPU` voor Safari,
  `… Helper (Renderer)` voor Chromium) → de browser-entries in `MEDIA_APPS` matchten het geluidmakende
  proces nooit; (2) `MRMediaRemoteSendCommand(pause)` gaat naar de **éne now-playing-app** (vaak een al
  gepauzeerde Spotify), niet de tab — pauze raakte de video niet en de `play` erna startte juist Spotify.
  **Fix:** tweede laag naast pauzeren — zolang we opnemen én er webcontent klinkt (`web_sounding()` in
  media.py) dempen we de **systeem-output** via NSAppleScript in-process (0,6 ms). Aparte boekhouding
  `_muted` naast `_paused`: `play` sturen we alléén voor wat we écht pauzeerden (een YouTube-tab start
  dus nooit je Spotify), en we ontdempen nooit een gebruiker die zélf op mute stond. Mute is systeembreed
  → gaat alleen aan als er webcontent klinkt. UI: toggle **"Media pauzeren" → "Media stilhouden tijdens
  dictaat"** (+ ondertitel). Files: media.py, samflow.py, panel.py, prefs.py, settings.py, CLAUDE.md.

- **Menubalk-paneel opende niet over andere/fullscreen apps (fix, PR #8, panel.py).** Klikken vanuit een
  andere app deed niets: de popover flitste open en sloot meteen. Drie gestapelde oorzaken (met tijdelijke
  logging blootgelegd): (1) op recente macOS (Darwin 25) doet **`activateIgnoringOtherApps_` niets** voor
  een accessory-app → app werd niet actief (`active=False`) → een **transient** popover sluit dan meteen;
  nu de nieuwe coöperatieve **`NSApplication.activate()`** (macOS 14+, via `respondsToSelector_("activate")`).
  (2) Popover is nu **`ApplicationDefined`** (sluit niet vanzelf); we sluiten 'm zelf bij een klik buiten de
  app via een **global mouse-monitor + debounce** (zodat klik-op-icoon-om-te-sluiten niet heropent). (3) Het
  venster krijgt **`CanJoinAllSpaces | FullScreenAuxiliary`** → paneel komt mee naar de actieve Space, óók
  een fullscreen-app op een ander scherm (anders landde 'ie op de home-desktop en zag Sam 'm niet).

- **Historie-scroll hakkelde (fix, PR #8, mainwindow.py).** Bij honderden dictaten staan er ~1700
  transparante, zelf-tekenende views in de documentView. Zonder lagen kan de scrollview (drawsBackground=
  False, niet-opaak) geen **copy-on-scroll** doen → hertekent elke scrollstap de hele zichtbare inhoud.
  **Fix:** `documentView.setWantsLayer_(True)` in `show_tab` (geldt voor elke tab; overleeft een reflow
  omdat `_reflow` via `show_tab` herbouwt) → GPU composit cached lagen.

- **Kopieer-bevestiging viel over tekst (fix, PR #8, mainwindow.py).** "✓ Gekopieerd" was breder dan
  "Kopieer" en groeide (rechterrand vast) naar links over het woordtal/dictaat heen. Nu een **kaal,
  gecentreerd groen vinkje** zonder chip-achtergrond (`drawRect_` slaat de pill over zolang `_flashing`).

- **Oppoets-model plakte een few-shot voorbeeldzin (fix, PR #8, polish.py).** Sams bug: "plakt soms iets
  over mijn vakantie dat ik helemaal niet zei." De zin *"…de vakantieplanning, want ik ben volgende week
  weg"* is het **láátste few-shot voorbeeld** in polish.py en bestaat nergens anders in de pijplijn.
  `qwen2.5:3b` echode dat voorbeeld soms in z'n output; de lengte-vangrail (`_sane`) zag het niet.
  **Uitgesloten:** de warme whisper-server geeft op stilte `***` terug (geen cross-request context-bleed);
  pre-roll (0,4s) + Recorder-buffer legen netjes. **Fix:** `_leaks_fewshot()` valt terug op de opgeschoonde
  (Route-A) tekst zodra een few-shot-fragment in de output staat dat niet in het dictaat stond
  (genormaliseerd vergeleken, dus een échte dictatie van die zin blijft staan). Unit-getest.

**Git/release-staat:** PR #7 gemerged → **lokale `main` = `3d7063a`**. **PR #8 staat OPEN** met 3 commits
(`f8cebc9` paneel, `ce27aa7` scroll+kopieer, `ac1457f` polish) op `fix/menubalk-paneel-en-historie`. **Ná de
merge: lokale repo terug naar `main`** (staat nu op de feature-branch — anders botst de ff-only-auto-update).
De pre-existing untracked design-bestanden (`hosted-versie-plan.md`, `polish-setup-mockup.html`) en dít
handoff-bestand bleven bewust buiten beide PR's.

**Operationeel (bevestigd deze sessie):** herstart = `pkill -TERM -f "Code/samflow/samflow.py"` (de bundel-
wrapper `wait`t op z'n kind en sluit vanzelf mee) → `open -a "SamFlow"`. **whisper-server is een lós proces**
(launchd `com.sam.samflow-server`, pid ~1066) — **nooit meeherstarten** (koud = 11s, model kwijt). Hoofdvenster
programmatisch openen: nóg een keer `open -a "SamFlow"` = reopen-event (`applicationShouldHandleReopen_`).
`screencapture` blijft geblokkeerd in de shell (geen Screen Recording-recht) → headless verifiëren + Sam
laten kijken.

---

**Laatste update (19 juli 2026, avond) — tester-feedback + polish, PR #4/#5/#6 → main (`cdfe8da`):**

Een reeks fixes n.a.v. de tester + eigen polish op de Woordenlijst- en Instellingen-UI. Alles
gemerged naar `main` (= release), lokale main = **`cdfe8da`**. De app is na elke stap herstart en
live geverifieerd.

- **Woordenlijst overal in-app bewerkbaar (fix, PR #4).** "Bewerken…" (Voorkeuren),
  "Woordenlijst bewerken…" (menubalk-paneel) én "+ Nieuwe term" openden `lexicon.txt` met
  `open -t` in een teksteditor. Dat bestand staat **buiten git** en bestaat niet op een verse
  install → `open -t` faalde stil (exit 1) → bij de tester deed de knop **niets**. Alle drie
  routeren nu naar de **Woordenlijst-tab** (de echte in-app editor) via lazy
  `import mainwindow; open_main_window().show_tab(2)` (lui om de prefs↔mainwindow-cyclus te
  vermijden). Dode `import lexicon` uit `hud.py`/`prefs.py` weg.
- **Gebrand invoer-paneel (feat, PR #4).** De kale `NSAlert`-dialogen (blauw systeem-icoon +
  blauwe knop) vervangen door een **sheet in Helder-stijl** (`_present_sheet` in `mainwindow.py`)
  met een zelf-getekende **klei-accentknop `_ClayButton`** (bewust géén systeem-blauw). Drie modi:
  `term` (meerregelig `NSTextView` — **meerdere termen tegelijk, één per regel**, hele lijst plakken
  kan; dedup tegen bestaande/DEFAULT-termen, spaties-in-term behouden), `map` (twee velden
  gehoord→canoniek) en `correct` (voorgevuld voorstel-woord). **"+ Nieuwe correctie"**-knop
  toegevoegd aan de Fonetische-correcties-kaart (ontbrak — je kon alleen wissen of via een
  voorstel). Verder: zichtbare **"Whisper" → "SamFlow"** in labels (docstrings/engine-refs blijven
  Whisper), ondertitel **wrapt** i.p.v. afkappen, en het **×-knopje in een term-chip** verticaal
  gecentreerd (volle chip-hoogte i.p.v. y=4/h=20).
- **Menubalk-icoon (fix, PR #4).** `_STATUS_COLORS` (hud.py): idle was grijs, `done` klei (oranje,
  zelfde als opnemen). Nu is **idle een template-image** (macOS kleurt mee: zwart in licht, wit in
  donker) en **`done` groen** (net als de pill; brand: groen = klaar). Oranje = alleen nog "bezig"
  (recording/thinking).
- **Fn-status zichtbaar (fix, PR #4).** De grootste tester-bug: bij haar opende **Fn de
  emoji-kiezer** bij elk dictaat. Oorzaak: de event-tap is **listen-only** (kan Fn niet opslokken)
  en op haar Mac stond *Systeeminstellingen → Toetsenbord → "Druk op fn"* ≠ "Niets doen".
  SamFlow detecteerde dit al (`fn_key_is_free()` / `prefs._fn_free()`) maar **printte het alleen
  naar de console**. Nu is de Fn-regel in *Setup & permissies* een **live status** (klei ⚠ als
  macOS Fn afpakt, groen ✓ als vrij; ververst in `_refresh_dots`). **Fix ligt bij de gebruiker:
  Fn op "Niets doen" zetten** — de app kán Fn niet vrijmaken zonder de tap actief te maken (bewust
  listen-only, zie CLAUDE.md).
- **Oppoets-melding (fix, PR #4).** Zet je "AI-oppoetsen" aan zonder Ollama/model, dan viel polish
  stil terug op de kale tekst (alleen een console-print). Nu doet `toggleSwitch_` een async
  `polish.available()`-check en toont bij afwezigheid een dialoog met het `ollama pull
  qwen2.5:3b`-commando + "Ollama installeren…"-knop. Polish blijft opt-in/default UIT, dus wie het
  nooit aanzet merkt niks.
- **"Vasthouden"-modus toegevoegd én weer weggehaald (PR #4 feat → PR #5 revert).** Op verzoek
  gebouwd: nieuwe `lock_mode "hold"` — Fn >1,2s vasthouden-dan-loslaten = hands-free, stopt vanzelf
  ~1,5s na je laatste woord (VAD in `Recorder._callback`, main-thread `NSTimer` roept `end()` —
  **nooit vanuit de audio-callback**, dat zou op `Recorder.lock` deadlocken; latch-beslissing valt
  op het loslaat-moment, dus geen extra timer nodig). **Daarna weer verwijderd** (schone revert van
  `f2c31c0`): de 5e optie maakte de Vastzetten-rij te breed (titel afgekapt), en "Vasthouden" botst
  met de default (Fn ingedrukt houden ÍS al de standaard, `lock_mode="off"`). De auto-stop-engine
  leeft in de git-historie (`f2c31c0`) — als 'ie terugkomt: als **los schuifje "Stop bij stilte"**
  onder de bestaande vastzet-manier, niet als 5e segment.
- **Vastzetten als dropdown (fix, PR #6).** Zelfs met 4 opties claimde de segmented-control zoveel
  breedte dat de ondertitel afkapte. Nu een `ui.Dropdown` via `_grp_drop` (zelfde patroon als
  "Positie"): compact, ondertitel houdt op elke vensterbreedte ruimte. Handler ongewijzigd.

**E-mail-opmaak (onderzocht, GEEN wijziging).** De tester miste dat SamFlow e-mails
(hoi/inhoud/groetjes) niet mooi opmaakte zoals Wispr Flow. Bevinding: de **bestaande** polish-prompt
dóét dit al (aanhef op eigen regel, alinea's, afsluiting met naam eronder) — getest tegen het echte
`qwen2.5:3b`. Mijn poging de prompt te "verbeteren" met een e-mail-few-shot maakte het juist
slechter (brak de alinea-splitsing, zette namen in HOOFDLETTERS). **`polish.py` dus onaangeroerd.**
Waarom de tester het niet zag: waarschijnlijk een stille terugval (toggle stond uit, of een
koude-start/timeout). Warm kost een lange mail ~2,6s (< de 8s-timeout).

**Openstaand (niet gefixt): bug 2 — het laad-icoon valt weg bij dubbel-tik/vastzetten en "springt
op groen".** Niet kunnen reproduceren; de state-overgangen náár "thinking" ogen correct in álle
lock-varianten (hold/chord/double). Een tijdelijke state-trace stond even in `hud_state` (weer
verwijderd). Volgende stap: trace terugzetten en Sam laten reproduceren om de echte volgorde met
tijdstippen te zien.

**Branch-hygiëne deze sessie:** PR #4 (woordenlijst + gebrand paneel + menubalk + Fn + polish) en
PR #5 (Vasthouden-revert) waren al gemerged toen de dropdown-fix erbij kwam; die ging via een
schone branch vanaf de actuele main (cherry-pick) als **PR #6**. Alles nu op `cdfe8da`; branches
opgeruimd.

---

**Eerder op 19 juli 2026 — twee losse fixes gereleased:**

- **Mic schakelde niet mee na een AirPods-wissel** (van AirPods terug naar de Mac gaf stilte).
  Oorzaak: PortAudio (V19/CoreAudio) enumereert audio-apparaten **éénmalig bij proces-start** en
  ziet hotplug niet; de app draait dagen. Na een wissel keek `choose_input()` op de bevroren
  sounddevice-lijst → de stale default (AirPods) glipte langs de Bluetooth-check (verdwenen uit
  de live CoreAudio-`transports()`) en kwam terug als "gewone default" → `InputStream(device=None)`
  opende het verdwenen apparaat → AUHAL `-10851` → stilte. Bewezen met een software-simulatie van
  de stale topologie én zichtbaar als `-10851`-spam in `samflow.log` tijdens de buggy sessie.
  **Fix** (`samflow.py` + `audiodev.py`): `audiodev.refresh()` (`sd._terminate()/_initialize()`,
  ~3 ms) vlak vóór `choose_input()` in `_open()` maakt de apparaatlijst weer live — veilig daar,
  want `_open()` komt alleen zover als `self.stream` None is (geen open stream raakt de re-init).
  Plus `audiodev.effective_input_name()`: een **live CoreAudio-uitlezing** voor de dashboard-mic-
  chip (het `choose_input`-pad toonde daar na een wissel het verdwenen apparaat; deze is actueel
  én veilig terwijl er opgenomen wordt). CLAUDE.md audiodev-sectie bijgewerkt. **PR #2 → main
  (`f65293a`), gereleased.**
- **Hover op de weekgrafiek** (`mainwindow.py`, `_WeekChart`): je ziet nu bij élke dag het
  woordenaantal bij hover (voorheen alleen boven vandaag), en de gehoverde balk licht op. **Eén
  tracking-area per dag-kolom** met enter/exit (betrouwbaarder dan `mouseMoved`, dat de venster-
  vlag `acceptsMouseMovedEvents` vereist die standaard uit staat); kolom-index reist mee in
  `userInfo` (komt als gewone Python-dict terug, dus `info["slot"]`, niet `objectForKey_`); lege
  dagen tonen eerlijk "0"; verborgen boven vandaag (daar staat het vaste getal al). **PR #3 →
  main (`9d69a93`), gereleased.**
- **Zijvraag beantwoord:** de dashboard-groet ("Goedenavond, Sam") is **niet** hardgecodeerd —
  `_greeting()` (mainwindow.py) haalt de voornaam uit `NSFullUserName()`, dus andere gebruikers
  zien hun eigen naam + het juiste dagdeel. Geen wijziging nodig.

Lokale `main` = origin op **`9d69a93`** (Merge PR #3). De draaiende app is herstart met beide
fixes. Verificatie was headless (simulatie van de stale topologie, echt `Recorder`-pad met de
refresh, hover-toestandsmachine + tracking-areas) — `screencapture` kan niet in de shell, dus de
hover zichtbaar checken vraagt Sam op **Overzicht**.

**Voorgaande staat (app-schil Fase 1–6 + design-pass):** app-schil **Fase 1–6 klaar**; **design-pass: fundament + zijbalk
+ dashboard + álle tabs (Historie/Woordenlijst/Instellingen) op de mockup**, én nu ook het
**menubalk-paneel (`panel.py`) op de Helder-tokens** (zie Stap 5 hieronder). Het **venster is
resizable** met live-meelopende content — óók Instellingen loopt nu live mee (was even "vult
bij loslaten", teruggedraaid nadat de subprocess-lookups gecachet werden). **Volgende
design-veeg:** korrel-textuur in de hero **teruggedraaid** (gebouwd, maar Sam vond 'm niet
mooi — er is nu géén grain), Positie als echte `.drop`-dropdown **KLAAR** (zie Stap 6);
resteert de instellingen-controls op autoresizing-ankers voor een écht "vastgelijmde"
live-reflow (marginaal, werkt nu al via rebuild) en — bewust buiten Fase 6 — Model als échte
keuze (dat is een feature: multi-model + server-plumbing, niet polish).
**Alles is gemerged naar `main` (PR #1) en dus RELEASED** via auto-update — de hele app-schil
(Fase 1–6), de audio-deadlock-fix én de optionele Route B staan nu live, ook bij de andere
gebruikers. Lokale `main` staat gelijk met de release (`e1acc8a`). De branch `app-schil-buildout`
is opgegaan in main. Enige losse untracked file: `macos/design/polish-setup-mockup.html` (mockup
van de Route B-setupkaart, ter beoordeling — nog niet ingebouwd/gecommit).
De user (Sam) is tevreden met de huidige staat ("ja nice!"). **Volgende grote openstaande taak:
Engelse UI-lokalisatie** (i18n-laag + aparte "App-taal"-selector; ~150–250 hardcoded NL-strings
verspreid over panel/prefs/mainwindow/hud — eigen branch/PR, géén tuck-in). Zie sectie 7.

**Audio-deadlock gefixt (vorige sessie — niet te verwarren met de AirPods-hotplug-fix
hierboven; dit ging over bevriezen, niet over de verkeerde mic).** De app bevroor volledig: een stack-sample toonde de
Fn-tap (main thread) hangend op `Recorder.lock`, vastgehouden door de idle-reaper (`_close`)
die tijdens `stream.stop()` op de CoreAudio HAL-mutex (`AudioOutputUnitStop`) bleef hangen na
een apparaat-hik (AUHAL `err=-10851`). Fix in `samflow.py`: `_close()` swapt de stream-ref
ónder de lock en stopt/sluit erbuiten (geen CoreAudio-call raakt ooit de lock meer); `_open()`
vangt CoreAudio-fouten af zodat ze de event-tap niet stilleggen. Regel toegevoegd aan CLAUDE.md
(samflow-sectie). Bewijs: headless test (`stop()` blokkeert → lock blijft vrij). **Niet het
paneel** — die stond nergens in de stack. App herstart, draait met de fix.

---

## 1. Wat er nu staat — app-schil Fase 1–5 (klaar, draait)

Het hoofdvenster (`mainwindow.py`) draait met een zijbalk + vier tabs, bereikbaar via het
menubalk-paneel ("Open SamFlow…") en in App-modus via het dock-icoon.

- **Fase 1 — Schil.** Venster met zijbalk (Overzicht/Historie/Woordenlijst/Instellingen),
  NSScrollView-content per tab. **Instellingen-tab = de échte prefs-view** (`PrefsController`,
  afgesplitst uit `PreferencesWindow` — één implementatie, twee plekken). Paneel-actie
  "Open SamFlow…" + `hud._Ticker.openMainWindow_`. Gedeelde bouwstenen
  (Flipped/label/section/separator/row_label/GlyphView + maten `W/PAD/ROW_H/SEC_GAP`)
  verhuisd van prefs/panel naar **`ui.py`**.
- **Fase 2 — Basic ↔ App-modus.** **`appmode.py`** (leaf) zet de runtime activation policy
  (accessory=Basic / regular=App); Info.plist/LSUIElement onaangeroerd (TCC-veilig).
  `settings.app_mode="basic"` default. Live wisselen in Instellingen → **Weergave → Modus**;
  moduskeuze-stap in de onboarding; dock-reopen via `_Ticker` als **app-delegate**
  (`applicationShouldHandleReopen_hasVisibleWindows_`). Paneel-voet toont de modus.
- **Fase 3 — Dashboard + stats.** **`stats.py`** = inhoudsloze dag-aggregaten in
  `~/Library/Application Support/SamFlow/stats.json` (géén tekst; default aan, toggle in
  Gedrag). Hook in `samflow.handle()` ná het plakken, fail-silent, op de handle-thread.
  Overzicht-tab = dashboard: grafiet-hero-band, status-chips, 4 stat-tegels,
  week-staafgrafiek. Live refresh via een **mtime-gated NSTimer** (`refreshTick_`).
- **Fase 4 — Historie (opt-in).** **`history.py`** = JSONL in App Support, **rechten 0600,
  default UIT**, retentie 7/30/altijd (`history_days`, 0=altijd). App-naam op het
  Fn-loslaten-moment (main thread, `_frontmost_app()` in `end()`). Historie-tab:
  opt-in-kaart / lijst met dag-groepen, zoekveld, kopieer, wis-per-rij, "Wis alles",
  "Zet uit" (wissen of behouden). Recent-rij op het dashboard zodra historie aan.
- **Fase 5 — Woordenlijst-UI.** **`lexicon.py`** kreeg een gedeelde API
  (`suggestions/accept/map_to/ignore` + `remove_term/remove_mapping`, regel-gefilterd,
  comments/volgorde behouden). Woordenlijst-tab: suggesties (veld + Toevoegen/Map/Negeer),
  term-chips (standaard=grijs niet-wisbaar, ambigu=klei, eigen=×), correcties. `--review`
  gebruikt nu dezelfde API. **`canonicalise` bleef onaangeroerd.**

**Ook deze sessie:** credit **"© 2026 Sam Kloeth"** (zijbalk-voet, `LICENSE`,
`macos/Info.plist` NSHumanReadableCopyright); **positionering geneutraliseerd** (CLAUDE.md-titel
→ "lokale dictatie-app"; README-tagline noemt Wispr niet meer / geen "clone"); **kopieer-
bevestiging** `ui.flash_copied` ("✓ Gekopieerd", groen, fade-in) in de historie-lijst.

## 2. De design-pass (mockup: `macos/design/app-interface.html`)

Aanpak: **fundament → zijbalk → dashboard → rest** (foundation-first).

- **Stap 1 — Fundament: KLAAR.** **`theme.py`** = de Helder-tokens als *adaptieve* NSColors
  (licht/donker lossen vanzelf op; grafiet/klei/groen constant). Toegepast in `ui.py`
  (`FillView`/`fill()`, `label`/`section`/`row_label` op tokens) en `mainwindow.py`
  (venster-bg, kaarten=`SUNKEN`, chips=`CHIP`, teksten=`TEXT/TEXT2/FAINT`). Zijbalk kreeg
  een **vlakke** `--sidebg`-achtergrond i.p.v. de doorschijnende macOS-zijbalk.
- **Stap 2 — Zijbalk: BEWUST TERUGGEDRAAID.** Ik bouwde eigen staafjes-iconen + grijze-chip-
  actief + 176px; **de user vond de vorige beter**. De zijbalk staat nu op: **SF-Symbol-iconen,
  klei-getinte actieve rij (`_rgb(_CLAY,0.14)` bg + klei tekst/icoon), 210 breed.** (De vlakke
  Helder-bg uit stap 1 bleef.) → **Voor de zijbalk telt de user-voorkeur boven de mockup —
  niet opnieuw naar de mockup-iconen/grijze-chip trekken tenzij gevraagd.**
- **Stap 3 — Dashboard: KLAAR.** In `_overzicht_view` / `_HeroBand` (mainwindow.py):
  hero herzien naar de mockup — datum **rechtsboven**, groet **klein/gedimd** (13px, ~0.72
  wit), getal met **"woorden vandaag" inline**, **status-chips ín de grafiet-band** (groene
  stippen: Microfoon→apparaatnaam, Rechten, Model→warm/uit async; wrappen bij smal, band-
  hoogte volgt), klei-gloed + merkteken samen **rechtsonder**. **Stat-tegels: 4-op-een-rij**
  boven `inner_w ≥ STATS_4COL_W` (620), anders **terugval naar 2×2**. Korrel-textuur bewust
  **uitgesteld** (0.04-noise vraagt een gecachete bitmap-textuur; niet nu).

### Resizable venster (nieuw deze sessie, in mainwindow.py)
Het venster was vaste breedte; nu **Resizable** met min-maat `SIDE_W + ui.W` × 480. `CONTENT_W`
(constant) is `self._content_w` (dynamisch) geworden; álle tabs bouwen daarop. Kernpunten:
- **Chrome** (zijbalk vast 210, scroll flexibel, hairline, voet) volgt live via **autoresizing-
  masks**; `_reflow()` is de autoritatieve her-plaatsing + herbouw van de huidige tab.
- **Live meelopen:** `windowDidResize_` reflowt **direct, gethrotteld tot ~30/s** (een NSTimer
  vuurt niet tijdens tracking-mode). Trailing-timer in **`NSRunLoopCommonModes`** pakt de exacte
  eindmaat (ook de zoom-knop). Scroll-positie blijft behouden (`show_tab(keep_scroll=)`).
- **Geen schijf-hamer / geen geflikker bij resize:** `history.mtime()` + mtime-caches voor
  stats én historie; mic/rechten-status in `self._status_cache`; de Model-chip toont de laatst
  bekende `self._server_up` en de server-check draait **alleen bij een verse view** (nav/tik),
  niet per reflow. In `prefs.py` zijn **`_login_item_present` (osascript, 5s TTL)** en
  **`_short_version` (git, sessie)** gecachet — zónder cache spawnde een resize tientallen
  subprocessen/sec en liep 't vast (echt gebeurd; niet weghalen).
- **`_built_w` vs `_content_w`:** `_reflow` herbouwt alleen als de nieuwe breedte ≠ de breedte
  waarop de tab écht gebouwd is (`_built_w`, gezet in `show_tab`). Zet je `_content_w` te vroeg
  gelijk, dan denkt de reflow "niks veranderd" en herbouwt 'ie niet — dat was de "Instellingen
  vult niet"-bug. Een instellingen-herbouw is ~8 ms (≈ dashboard), dus live meelopen kan.
- Mockup-CSS ter referentie: `.hero`, `.hchips`, `.hmark`, `.stats/.stat`, `.chartcard/.wk`.

### Stap 4 — Historie / Woordenlijst / Instellingen op de mockup (KLAAR deze sessie)
Gedeelde bouwstenen in **`ui.py`** zodat alle tabs én het losse voorkeuren-venster één taal
spreken: **`card_group`** (SUNKEN-kaart met haarlijn-rijen = `.rows`/`.group`), **`glabel`**
(kop + lichte subtitel, attributed), **`mono`**, **`hline`**, en **`Segmented`** (custom `.segc`:
chip-vlak met verhoogd wit pilletje op de selectie; **quackt als NSSegmentedControl** via
`selectedSegment()`, dus de bestaande `change*`-handlers in prefs.py werken ongewijzigd).
Nieuwe view-klassen in **`mainwindow.py`**: **`_Chip`** (solid/dashed/plain pill voor term-chips),
**`_PillButton`** (chip- én ghost-knop; gebruikt voor de suggestie-acties én historie Kopieer/Wis,
met een eigen `flash_copied()` die de bezel-`ui.flash_copied` vervangt).
- **Woordenlijst:** mainhead/mainsub; Voorstellen als **één nette regel** (term · frequentie ·
  chip **"Corrigeer naar…"** + ghost **"Negeer"**), gecapt op 8 + "+N meer". `_sugg` bevat nu
  **strings**; `wordCorrect_` opent een dialoog — tekst ongewijzigd = `accept` (toevoegen), tekst
  aangepast = `map_to` (correctie); `wordAdd_`/`wordMap_` weg, `wordNew_` ("+ Nieuwe term" opent
  de lexicon-lijst). Eigen termen = **pill-chips** (ambigu = gestreepte `_Chip`), correcties =
  maprows met klei "wis"-link.
- **Historie:** kop met groene **privacy-badge** + inline zoek + meta-regel met klei-links; dag-
  groepen als **`card_group`** met **horizontale rijen** (mono-tijd · app-chip · tekst · "N w · X s"
  · Kopieer-chip + Wis-ghost). Opt-in-kaart met checklist. Helpers `_privacy_badge`, `_app_chip`,
  `_link_btn`.
- **Instellingen (`prefs.build_view`, herschreven):** mainhead/mainsub; **Weergave = Basic/App als
  twee `_ModeCard`-mini-kaarten** (klei-rand + vinkje op de selectie, live via `selectMode_`);
  gegroepeerde `.group`-kaarten onder `glabel`-koppen (Weergave/Dicteren/Pill/Gedrag/Historie/
  Woordenlijst); alle keuzes via **`ui.Segmented`**; keycap voor Sneltoets; Model = statische
  waarde; voet met versie + werkende **"Controleer op updates"** (`checkUpdates_`/`_updateResult_`
  → `updater.check/apply/relaunch`). **Alle bestaande settings behouden**, herordend.
  **`build_view(width=None)` is breedte-bewust**: het hoofdvenster geeft `self._content_w` door
  (vult de volle breedte, controls rechts uitgelijnd), het losse `--prefs`-venster blijft `W`=470.
- **Bewuste afwijkingen (functie eerst):** suggestie-correctie via dialoog i.p.v. inline veld;
  Model = statische waarde (nog niet wisselbaar); Positie = segmented i.p.v. dropdown; segmented/
  toggles blijven de custom controls (geen native). Model/Positie als echte `.drop`-dropdowns is
  een mogelijke volgende veeg.

### Stap 6 — Fase 6-restjes: hero-korrel + Positie-dropdown (KLAAR deze sessie)
- **Korrel-textuur in de hero** — gebouwd (gecachete ruis-tegel als `colorWithPatternImage_`-
  fill over de grafiet), maar op verzoek van Sam **weer teruggedraaid**: hij vond 'm niet mooi.
  De hero heeft nu géén grain (grafiet-gradiënt + klei-gloed + merkteken, zoals ervoor). Niet
  opnieuw toevoegen tenzij gevraagd.
- **Positie als `.drop`-dropdown**: nieuw gedeeld component **`ui.Dropdown`** (window-vlak +
  `theme.LINE2`-rand + zelf-getekende chevron; klik opent een NSMenu). Quackt als
  NSSegmentedControl (`selectedSegment()`), dus `prefs.changePosition_` blijft ongewijzigd.
  Breedte = het bréédste label (geen verspringen bij keuze), en compacter dan de segmented.
  Nieuwe `prefs._grp_drop`-helper (kopie van `_grp_seg`) voedt de Positie-rij; de rest van de
  instellingen blijft segmented. **Model bewust níét** omgezet — vaste instelling (CLAUDE.md).
- **Verificatie:** headless — hero+grain getekend in een offscreen-image (geen fout, tegel
  gecachet); Dropdown gebouwd/bediend (110px, keuze doorgestuurd, dubbele keuze genegeerd);
  volledige `build_view()` bevat precies 1 Dropdown. Zichtbaar checken: dashboard-hero (korrel)
  + Instellingen → Pill → Positie.

### Stap 5 — Menubalk-paneel op de tokens (KLAAR deze sessie)
`panel.py` (de NSPopover-dropdown achter het menubalk-icoon) draaide nog op systeem-grijzen
(`NSColor.secondaryLabelColor` e.d.) en een grijze CGColor-laag; nu volledig op **`theme.py`** +
de gedeelde `ui.py`-bouwstenen, gelijk aan het hoofdvenster. Mockup: `menubar-panel-mockup.html`
(let op: díe HTML heeft nog het oude róde accent; alleen de *structuur* is de referentie, de
kleuren zijn Helder).
- **Egaal Helder-oppervlak** i.p.v. het doorschijnende systeemmateriaal: de content-view is
  `ui.fill(…, theme.WINDOW, 0)`. (De popover-pijl blijft het systeemmateriaal — niet via de
  publieke API te kleuren; de body is wél egaal.)
- **Status als pil rechtsboven** (`_status_pill`, mockup `.p-status`): gekleurde stip + korte
  tekst op een 0.13-getinte pil. `_PILL`: **groen** voor de rusttoestanden (`idle`→"klaar",
  `done`→"geplakt"), **klei** terwijl 't werkt (`recording`→"luistert", `thinking`→
  "transcribeert"). De subtitel is nu een vaste hint ("Houd Fn ingedrukt om te dicteren") en
  wordt afgekapt vóór de pil (geen overlap).
- **Subtiele klei-getinte kopband** (`_rgb(_CLAY, 0.06)` over `theme.WINDOW`, `HEAD_H=60`),
  **`theme.SUNKEN`-kaart** voor het laatste dictaat, **volle-breedte `theme.LINE`-haarlijnen**
  (`ui.hline`) i.p.v. ingesprongen NSBox-separators, `ui.glabel` voor "LAATSTE DICTAAT",
  actie-rijen getint op `theme.TEXT`, "Kopiëren" op klei, voet op `theme.FAINT`.
- **API onaangeroerd:** `initWithHud_ticker_` / `.toggle()` / `toggleSwitch:` en alle
  ticker-selectors ongewijzigd; hud roept 't net zo aan. Verwijderd: `_STATE_RGB/_STATE_LABEL`,
  `_cg` + Quartz-import, `_PanelFlipped`, de losse `_label` (nu `ui.label`).
- **Verificatie:** headless `_make_view()` over 4 statussen × 4 update-varianten × 3 laatste-
  tekst-varianten = 48 builds, allemaal niet-leeg; tokens resolven in licht (WINDOW=wit) én
  donker (WINDOW=#17171c). Zichtbaar checken vraagt een herstart (de live app heeft nog de oude
  `panel.py`).

## 3. Nieuwe / gewijzigde bestanden (deze sessie, ongecommit)

**Nieuw:** `mainwindow.py`, `appmode.py`, `stats.py`, `history.py`, `theme.py`.
**Gewijzigd:** `ui.py`, `prefs.py`, `panel.py`, `hud.py`, `samflow.py`, `settings.py`,
`lexicon.py`, `CLAUDE.md`, `README.md`, `LICENSE`, `macos/Info.plist`.
**Nieuwe settings-defaults:** `app_mode="basic"`, `stats_enabled=True`,
`history_enabled=False`, `history_days=30`.

## 4. Hoe de app draait / herstarten (nodig na code-wijziging)

De app draait als bundel-launcher die `.venv/bin/python samflow.py` als kind start (TCC hangt
aan die identiteit — **nooit** kale terminal-python).

```
pkill -9 -f "Code/samflow/samflow.py"; pkill -9 -f "Applications/SamFlow.app/Contents/MacOS/SamFlow"
open "$HOME/Applications/SamFlow.app"
```
Verifiëren: verse `=== SamFlow start …` in `~/Library/Logs/samflow.log`, geen traceback,
en `pgrep -fil samflow.py` toont een nieuwe PID. Watchdog brengt 'm binnen 30s terug bij crash.
Losse vensters testen: `--window` (hoofdvenster), `--prefs`, `--welcome`. `--check` = groen.

## 5. Verificatie zonder de app te zien

- **Headless bouw-test:** `PYTHONPATH=<repo> ./.venv/bin/python` een scriptje dat
  `NSApplication` op accessory zet, `mainwindow.MainWindow.alloc().initWithHud_(None)` bouwt
  en `show_tab(0..3)` cyclet. Voor stats/history/lexicon: **monkeypatch de FILE-paden naar een
  tempdir** (echte data niet aanraken); voor settings: overschrijf `settings.get`.
- `screencapture` lukt **niet** in de shell (geen Screen Recording-recht) → altijd headless
  verifiëren én de user laten kijken.
- Licht/donker-tokens checken: `NSAppearance.appearanceNamed_(...).performAsCurrentDrawingAppearance_(fn)`
  met `colorUsingColorSpace_(sRGB)`.

## 6. Committen / release-staat — GEDAAN

**Alles is gecommit, gepusht en gemerged.** De hele sessie is per onderdeel op de branch
`app-schil-buildout` gezet (~15 logische commits; de audio-deadlock-fix als eigen commit
geïsoleerd via een tijdelijke revert-reapply), gepusht, en via **PR #1 naar `main` gemerged**
(`e1acc8a`). Main = release-branch, dus dit is **live via auto-update**, ook bij de andere
gebruikers. Lokale main staat gelijk.

Herinnering voor de volgende keer: main is de directe release-branch. Werk op een branch,
push die, en merge pas als het af is (mergen = uitrollen). De per-fase-commit-splitsing over
een gemengde tree lukte via **whole-file commits + revert-reapply** voor het isoleren van één
bugfix — interactief hunk-splitsen kan deze omgeving niet.

## 7. Vaste voorkeuren van de gebruiker (Sam) — meenemen

- Instelbaar met verstandige default; gerenderde mockups om opties te zien.
- Geen AI-slop; Helder-merk: grafiet `#1E1E22`, klei `#C67B52`, groen `#33B859`. Géén indigo/rood.
- **Zijbalk:** SF-Symbol-iconen + klei-getinte actieve rij (koos dit boven de mockup-variant).
- Functie-eerst, design daarna. **Route B** (lokaal oppoets-model, Fase O) is **gebouwd,
  gereleased en opt-in** — zie hieronder; Sam test 'm nu een poos in echt gebruik. Fase 7
  (distributie) is grotendeels moot voor zijn doelgroep; Fase 8 (telemetrie) blijft uitgesteld.

### Openstaande taken (na de PR-#1-release)
- **Engelse UI-lokalisatie (grootste openstaande taak, NIET quick).** Sam wil de hele schil +
  het menu in het Engels kunnen zetten, zodat Engelse gebruikers 'm ook kunnen gebruiken. Nu
  staan **alle ~150–250 UI-strings hardcoded inline** (panel/prefs/mainwindow/hud + welkom-
  wizard + label-tabellen POS/LOCK/SIZE/MOTION/`_ROW_TEXT`), géén i18n-laag. Aanpak: een
  `i18n.py` (`t("key")` + NL/EN-dicts), alle strings extraheren+keyen, vertalen, en een **aparte
  "App-taal / Interface"-selector** in Weergave (LET OP: dat is iets ánders dan de bestaande
  "Taal"=dicteertaal nl/en/auto voor Whisper). Views herbouwen bij wisselen + layout-checks
  (EN-breedtes ≠ NL). **Eigen branch/PR**, geen tuck-in — een gerichte sessie van uren.
- **Route B-setupkaart** (mockup `macos/design/polish-setup-mockup.html`, goedgekeurd door Sam):
  een statuskaart onder de "AI-oppoetsen"-toggle die `polish.available()` leest — 4 toestanden
  (klaar / model mist → "Model ophalen" `ollama pull` / bezig / Ollama ontbreekt → `brew`-hint).
  Mockup klaar, **nog niet in AppKit gebouwd**. Níét in de verplichte eerste-start-wizard.
- **Route B 7B-upgrade** (als de 3B te wiebelig blijkt in gebruik): `qwen2.5:7b`, ~4,7 GB, ~2s.
- **Fase 6-restje:** instellingen-controls op autoresizing-ankers (marginaal, werkt al via rebuild).

### Route B — lokale AI-oppoets (gebouwd deze sessie, opt-in, default UIT)
`polish.py`: hangt ná `cleanup.clean` (Route A) in `samflow.handle()`, op de handle-thread.
Roept een lokaal Ollama-model aan (`qwen2.5:3b`, HTTP `127.0.0.1:11434`) met een strenge
"polijst-niet-herschrijf"-prompt + few-shot (temp 0). **Default UIT** (`settings['polish_enabled']`
=False): uit = geen call, geen model, geen RAM — knop in Instellingen → Dicteren → "AI-oppoetsen".
**Vangrail:** bij élke fout (Ollama weg, model niet gepulld, timeout, leeg, of lengte te
afwijkend via `_sane`) → gewoon de Route-A-tekst terug; nooit een exceptie naar de Fn-lus.
- **Opmaak (alinea's/witregels + opsommingen):** de prompt heeft nu structuur-regels + een
  few-shot die een lijst met '- ' en een alinea-splitsing met witregel voordoet. Getest: de 3B
  maakt nu wel opsommingen en alinea's, korte berichten blijven een zin. Zat puur in de prompt.
- **Prototype-bevinding (waarom opt-in):** latency is prima (warm ~0,6s), opmaak werkt, maar de 3B is niet
  100% trouw — met de strenge prompt 3/4 goed, harde zelfcorrecties ("de… nee wacht") blijven
  fragiel. Een subtiele betekeniswijziging vangt de lengte-vangrail níét. Daarom bewust opt-in,
  default uit, Route A blijft eronder. Groter model (7-8B) = trouwer maar ~2s + ~5 GB RAM.
- **Mac-headroom:** M3 Max, 36 GB (ruim), maar draait vaak vol; schijf krap (~18 GB vrij, model
  = 1,9 GB). `keep_alive=5m` geeft de RAM vrij als je 't niet gebruikt.
- Model verwijderen: `ollama rm qwen2.5:3b`. Advanced knoppen (`polish_model`, keep_alive,
  timeout) zitten in settings/polish.py, niet in de UI.
- **Besluit (deze sessie):** 3B blijft de default; we testen 'm eerst een poos in echt gebruik
  (Sam merkt de missers in de praktijk weinig). De 7B (`qwen2.5:7b`, ~4,7 GB, ~2s) is de knop
  als de betrouwbaarheid toch gaat storen — niet gepulld, wel als getest-plan genoteerd.
- Doelgroep: jezelf + een paar bekenden (geen €99 Apple Developer; lokaal bouwen omzeilt de
  Gatekeeper-notarisatiehek). Nederlands, zakelijk, leg *waarom* uit.

## 8. Harde regels (uit CLAUDE.md) — niet overtreden

- **TCC-val:** bundel/venv-identiteit niet wijzigen; app start via de bundel, python is het kind.
- **Alle AppKit op de main thread**; de run loop / CFRunLoop **nooit** blokkeren; de **pill pakt
  nooit focus** (non-activating panel, `orderFrontRegardless`).
- `cleanup.py`: nooit een regex zonder een `EXAMPLES`-voorbeeld; let op NL valse positieven.
- `lexicon.py`: `canonicalise` raakt nooit een woord buiten de lijst aan (de belofte).
- **Nooit stilte naar Whisper** (energie-poort + HALLUCINATIONS).
- Nieuwe schijf-data buiten de repo-dir (App Support); `settings.json` blijft in de repo-dir
  (watchdog.sh grept dat pad). `history.jsonl` bevat tekst → 0600, nooit naast een git-checkout.

---

## Vorige sessie (nog steeds ongecommit in de tree)

Pill-animaties (Fors + Soepel, 60 fps), `pill_position/size/motion`-instellingen, Esc-cancel,
watchdog (geïnstalleerd + geladen), telemetrie (gebouwd maar **inert**, lege `HEARTBEAT_URL`),
Route A (genummerde lijsten in `cleanup.py`, live), en de design-HTML's. Zie de git-log en de
eerdere secties van dit bestand in de historie voor detail.
