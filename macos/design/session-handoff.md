# SamFlow — sessie-handoff (context voor een verse sessie)

_Doel: na een `/clear` of in een nieuwe sessie meteen verder kunnen. Wat er staat, de
staat van de code, openstaande draden en hoe je de app bedient._

**Laatste update:** app-schil **Fase 1–5 klaar en live**; **design-pass: fundament + zijbalk
+ dashboard + álle tabs (Historie/Woordenlijst/Instellingen) op de mockup**, én nu ook het
**menubalk-paneel (`panel.py`) op de Helder-tokens** (zie Stap 5 hieronder). Het **venster is
resizable** met live-meelopende content — óók Instellingen loopt nu live mee (was even "vult
bij loslaten", teruggedraaid nadat de subprocess-lookups gecachet werden). **Volgende
design-veeg:** korrel-textuur in de hero **teruggedraaid** (gebouwd, maar Sam vond 'm niet
mooi — er is nu géén grain), Positie als echte `.drop`-dropdown **KLAAR** (zie Stap 6);
resteert de instellingen-controls op autoresizing-ankers voor een écht "vastgelijmde"
live-reflow (marginaal, werkt nu al via rebuild) en — bewust buiten Fase 6 — Model als échte
keuze (dat is een feature: multi-model + server-plumbing, niet polish).
**Alles staat ongecommit** in de working tree.
De user (Sam) is tevreden met de huidige staat ("ja nice!").

**Audio-deadlock gefixt (deze sessie).** De app bevroor volledig: een stack-sample toonde de
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

**Ook deze sessie:** credit **"© 2026 Kloeth Digital B.V."** (zijbalk-voet, `LICENSE`,
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

## 6. Committen — let op

Nog niets gecommit. De working tree **mengt deze sessie met de vorige** (hud/panel/prefs/
samflow/settings door beide aangeraakt) → een schone per-fase-commit kan niet zonder
interactief hunks te splitsen, en dat kan deze omgeving niet. Opties: **één checkpoint-commit**
van de hele tree (**zonder push = geen release**; let op: main = release-branch, dus een push
deployt via auto-update), of doorwerken en later splitsen. **Alleen op verzoek.**

## 7. Vaste voorkeuren van de gebruiker (Sam) — meenemen

- Instelbaar met verstandige default; gerenderde mockups om opties te zien.
- Geen AI-slop; Helder-merk: grafiet `#1E1E22`, klei `#C67B52`, groen `#33B859`. Géén indigo/rood.
- **Zijbalk:** SF-Symbol-iconen + klei-getinte actieve rij (koos dit boven de mockup-variant).
- Functie-eerst, design daarna. **Route B** (lokaal oppoets-model, Fase O) is nu **gebouwd als
  opt-in** — zie hieronder. Fase 7 (distributie) is grotendeels moot voor zijn doelgroep;
  Fase 8 (telemetrie) blijft uitgesteld.

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
