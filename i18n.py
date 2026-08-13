"""i18n.py - de interfacetaal van SamFlow.

Nederlands is de brontaal: de Nederlandse tekst ín de code is meteen de sleutel. Dat is
bewust. Een sleutel-systeem (`t("prefs.dictation.title")`) leest slechter in de code, en
bij een gemiste vertaling zie je dan een sleutel op je scherm in plaats van gewoon de
Nederlandse zin. Nu is een missende vertaling hooguit één Nederlandse regel tussen het
Engels -- lelijk, niet stuk.

Waar dit ingeplugd zit: in de *sinks*, niet op elke aanroepplek. Alle zichtbare tekst gaat
door een handvol helpers in `ui.py` (label/section/row_label, Segmented, Dropdown), plus de
paar plekken die rechtstreeks met AppKit praten (knoptitels, NSAlert, menu-items). Zo zijn
het tien plekken in plaats van tweehonderd, en pikt een nieuwe tekst de vertaling vanzelf
mee zodra 'ie in de tabel staat.

Taalkeuze: `ui_language` in settings ("auto" | "nl" | "en"). Bij "auto" volgen we de
systeemtaal: een Nederlandse Mac krijgt Nederlands, al het andere Engels. Dat is de default,
zodat een Engelse gebruiker niets hoeft in te stellen en een Nederlandse niets merkt.

Zelftest: `python i18n.py` vergelijkt de tabel met de teksten die in de code staan en
noemt wat er nog ontbreekt of overbodig is geworden.
"""
import settings

# Dynamische tekst (dictaten, termen, getallen) loopt óók door t(). Dat is veilig: een
# tekst die niet in de tabel staat komt onveranderd terug. Enige theoretische bijwerking:
# dicteer je letterlijk het woord "Instellingen", dan staat er in het Engels "Settings".
EN = {
    # ---------- zijbalk / tabs ----------
    "Overzicht": "Overview",
    "Historie": "History",
    "Woordenlijst": "Vocabulary",
    "Instellingen": "Settings",
    "Voorkeuren": "Preferences",
    "Welkom": "Welcome",

    # ---------- dashboard ----------
    "Goedemorgen": "Good morning",
    "Goedemiddag": "Good afternoon",
    "Goedenavond": "Good evening",
    "woorden vandaag": "words today",
    "Microfoon": "Microphone",
    "Rechten": "Permissions",
    "Model": "Model",
    "Recent": "Recent",
    "Jouw stem": "Your voice",
    "STIJL": "STYLE",
    "Woorden per dag": "Words per day",
    "deze week": "this week",
    "meer": "more",
    "minder": "less",
    " op rij": " in a row",
    "Reeks — ": "Streak — ",
    "langste · ": "longest · ",
    "Aan · ": "On · ",
    "alleen op deze Mac": "on this Mac only",
    "Alleen op deze Mac": "On this Mac only",
    "Zet historie aan om je meest gebruikte woorden te zien.":
        "Turn on history to see the words you use most.",
    "controleren…": "checking…",
    "warm": "warm",
    "uit": "off",
    "toegekend": "granted",
    "geen toegang": "no access",
    "actie nodig": "action needed",

    # ---------- historie ----------
    "Zoek in dictaten…": "Search dictations…",
    "Nog niets gedicteerd": "Nothing dictated yet",
    "Dictaten bewaren op deze Mac?": "Keep dictations on this Mac?",
    "Een leesbaar bestand op je eigen schijf — geen cloud, geen netwerk":
        "A readable file on your own disk — no cloud, no network",
    "Bewaart 30 dagen (in te stellen), daarna vanzelf weg":
        "Kept for 30 days (configurable), then removed automatically",
    "Uitzetten of alles wissen kan altijd, met één klik":
        "Turn it off or erase everything at any time, in one click",
    "Bewaar lokaal": "Keep locally",
    "Dan kun je ze later teruglezen, doorzoeken en opnieuw kopiëren — lokaal, met "
    "bestandsrechten 0600, nooit op het netwerk.":
        "So you can read them back, search them and copy them again — locally, with file "
        "permissions 0600, never over the network.",
    "Historie uitzetten": "Turn off history",
    "Wil je de bewaarde dictaten ook wissen, of behouden op deze Mac?":
        "Do you want to erase the stored dictations too, or keep them on this Mac?",
    "Wissen": "Erase",
    "Behouden": "Keep",
    "Annuleren": "Cancel",
    "Wis alles": "Erase all",
    "Zet uit": "Turn off",
    "wis": "erase",
    "Kopiëren": "Copy",
    "✓ Gekopieerd": "✓ Copied",
    " dagen, daarna vanzelf weg": " days, then removed automatically",

    # ---------- woordenlijst ----------
    "+ Nieuwe term": "+ New term",
    "+ Nieuwe correctie": "+ New correction",
    "+ Nieuwe snippet": "+ New snippet",
    "Eigen termen": "Your own terms",
    "Projectnamen, merken, jargon": "Project names, brands, jargon",
    "Projectnamen, merken of jargon. Schrijf ze zoals je ze geplakt wilt zien.":
        "Project names, brands or jargon. Write them the way you want them pasted.",
    "Wordt bij elk dictaat opnieuw gelezen — een woord toevoegen werkt meteen, zonder "
    "herstart.":
        "Re-read for every dictation — adding a word works immediately, no restart.",
    "Gestreept = ook een gewoon woord (gaat mee, maar niet geforceerd met hoofdletter).":
        "Striped = also an ordinary word (goes into the prompt, but never force-capitalised).",
    "Nog geen correcties — voeg er zelf een toe of behandel een voorstel.":
        "No corrections yet — add one yourself or handle a suggestion.",
    "als SamFlow er net naast zit": "when SamFlow is just slightly off",
    "Als SamFlow een woord fonetisch net verkeerd hoort.":
        "For when SamFlow hears a word slightly wrong phonetically.",
    "SamFlow hoort (bijv. klavijo)": "SamFlow hears (e.g. klavijo)",
    "Moet worden (bijv. Klaviyo)": "Should become (e.g. Klaviyo)",
    "Eén per regel — plak gerust een hele lijst.":
        "One per line — feel free to paste a whole list.",
    "Laat staan om zo toe te voegen, of pas aan naar de juiste schrijfwijze.":
        "Leave as is to add it that way, or correct the spelling.",
    " meer voorstellen — behandel de meest gehoorde eerst.":
        " more suggestions — handle the most-heard ones first.",
    "× gehoord deze week": "× heard this week",
    " — altijd in de juiste vorm geplakt": " — always pasted in the right form",
    "Toevoegen": "Add",
    "Map": "Map",
    "Negeer": "Ignore",
    "Nog geen snippets — bijv. “mijn linkedin” → je URL.":
        "No snippets yet — e.g. “my linkedin” → your URL.",
    "zeg een trigger, plak een blok": "say a trigger, paste a block",
    "Zeg de trigger tijdens een dictaat; SamFlow plakt de expansie ervoor in de plaats.":
        "Say the trigger while dictating; SamFlow pastes the expansion in its place.",
    "Trigger — wat je zegt (bijv. mijn linkedin)":
        "Trigger — what you say (e.g. my linkedin)",
    "Kies iets dat je normaal niet per ongeluk zegt.":
        "Pick something you would not normally say by accident.",
    "Wordt geplakt:": "Gets pasted:",
    "Nieuwe termen": "New terms",
    "Nieuwe snippet": "New snippet",
    "Nieuwe correctie": "New correction",
    " toevoegen of corrigeren": " — add or correct",
    "Expansie — wat er geplakt wordt": "Expansion — what gets pasted",
    "Nieuwe term": "New term",

    # ---------- instellingen: groepen ----------
    "Weergave": "Appearance",
    "Dicteren": "Dictation",
    "Pill": "Pill",
    "Gedrag": "Behaviour",
    "Elke wijziging werkt direct — geen “Opslaan”-knop.":
        "Every change applies immediately — no “Save” button.",

    # ---------- instellingen: rijen ----------
    "Taal": "Language",
    "Wat je spreekt — “Automatisch” laat Whisper kiezen":
        "What you speak — “Automatic” lets Whisper decide",
    "Interfacetaal": "Interface language",
    "De taal van dit venster; “Automatisch” volgt je Mac":
        "The language of this window; “Automatic” follows your Mac",
    "Binnenkort instelbaar": "Configurable soon",
    "Turbo — snel": "Turbo — fast",
    "Sneltoets": "Shortcut",
    "Ingedrukt houden = opnemen": "Hold it down = recording",
    "Vastzetten": "Hands-free",
    "Zodat je Fn niet hoeft vast te houden": "So you do not have to hold Fn down",
    "Maximale lengte": "Maximum length",
    "Langer dan dit wordt afgekapt": "Anything longer gets cut off",
    "Positie": "Position",
    "Waar de pill verschijnt": "Where the pill appears",
    "Grootte": "Size",
    "Hoe fors de staafjes zijn": "How chunky the bars are",
    "Beweging": "Motion",
    "Soepel of kwiek": "Smooth or snappy",
    "Start bij inloggen": "Start at login",
    "Bewaren": "Retention",
    "Hoe lang je historie blijft (0 = altijd)":
        "How long your history is kept (0 = forever)",
    "Bewerken…": "Edit…",
    "Controleer op updates": "Check for updates",
    "Controleren…": "Checking…",
    " · lokaal & open source": " · local & open source",

    # ---------- instellingen: schakelaars ----------
    "AI-oppoetsen (lokaal)": "AI polishing (local)",
    "Een lokaal model maakt er nette zinnen van. Kost ~0,6s extra en RAM; uit = alleen de "
    "regels.":
        "A local model turns it into proper sentences. Costs ~0.6s extra and some RAM; "
        "off = rules only.",
    "Geluiden": "Sounds",
    "Klik bij start, stop en klaar": "A click at start, stop and done",
    "Media stilhouden tijdens dictaat": "Silence media while dictating",
    "Zet even stil wat er speelt terwijl je opneemt":
        "Pauses whatever is playing while you record",
    "Pill bij de cursor tonen": "Show the pill at the cursor",
    "Statistieken bijhouden": "Keep statistics",
    "Alleen tellingen op deze Mac — nooit je tekst. Voor het dashboard.":
        "Counts on this Mac only — never your text. For the dashboard.",
    "Historie bewaren": "Keep history",
    "Bewaart je dictaten (mét tekst) lokaal. Standaard uit.":
        "Stores your dictations (including the text) locally. Off by default.",
    "Automatisch bijwerken": "Update automatically",
    "Haalt updates op de achtergrond op van GitHub":
        "Fetches updates from GitHub in the background",
    "Automatisch herstarten": "Restart automatically",
    "Brengt SamFlow terug als 'ie onverwacht stopt":
        "Brings SamFlow back if it stops unexpectedly",
    "Anonieme gebruiksstatistiek": "Anonymous usage statistics",
    "Alleen een telling — nooit je dictaten. Uit te zetten.":
        "A count only — never your dictations. Can be turned off.",

    # ---------- keuze-labels ----------
    "Automatisch": "Automatic",
    "Uit": "Off",
    "Tik": "Tap",
    "Dubbel-tik": "Double-tap",
    "Bij cursor": "At the cursor",
    "Onderin": "Bottom",
    "Vaste hoek": "Fixed corner",
    "Compact": "Compact",
    "Ruim": "Roomy",
    "Fors": "Chunky",
    "Soepel": "Smooth",
    "Kwiek": "Snappy",
    "7 dagen": "7 days",
    "30 dagen": "30 days",
    "Altijd": "Forever",
    "1 min": "1 min",
    "2 min": "2 min",
    "5 min": "5 min",
    "15 min": "15 min",
    "Onbeperkt": "Unlimited",

    # ---------- modus-kaarten ----------
    "Alleen menubalk + pill. Geen venster, geen dock-icoon.":
        "Menu bar + pill only. No window, no dock icon.",
    "Dit venster, dock-icoon en ⌘Tab erbij.":
        "This window, plus a dock icon and ⌘Tab.",
    "Kies je Basic, dan sluit dit venster en verdwijnt het dock-icoon. Terugkomen kan "
    "altijd via “Open SamFlow…” in het menubalk-paneel.":
        "Choosing Basic closes this window and removes the dock icon. You can always come "
        "back via “Open SamFlow…” in the menu bar panel.",
    "Basic houdt SamFlow in de menubalk. App geeft ook een dock-icoon en plek in ⌘Tab. "
    "Later te wisselen in Voorkeuren.":
        "Basic keeps SamFlow in the menu bar. App adds a dock icon and a place in ⌘Tab. "
        "You can switch later in Preferences.",

    # ---------- updates ----------
    "Update beschikbaar": "Update available",
    " nieuwe versie": " new version",
    " klaar. Nu bijwerken en herstarten?": " ready. Update and restart now?",
    "Bijwerken": "Update",
    "Later": "Later",
    "Je gebruikt de nieuwste versie.": "You are on the latest version.",
    "Er staan nieuwe versies klaar, maar ze kunnen niet automatisch geïnstalleerd worden "
    "(lokale wijzigingen of een afwijkende branch).":
        "New versions are available, but they cannot be installed automatically (local "
        "changes or a different branch).",
    "Bijwerken mislukt": "Update failed",
    "Update beschikbaar op GitHub (git pull)": "Update available on GitHub (git pull)",
    "Update beschikbaar — nu bijwerken": "Update available — update now",
    "✓  Update binnengehaald — nu herstarten": "✓  Update fetched — restart now",

    # ---------- oppoets-melding ----------
    "Oppoets-model niet gevonden": "Polishing model not found",
    "AI-oppoetsen staat aan, maar Ollama of het model “":
        "AI polishing is on, but Ollama or the model “",
    "” draait niet. Zonder dat blijft je tekst onopgepoetst — de opschoon-regels doen wél "
    "gewoon hun werk.\n\nInstalleer Ollama en draai in Terminal:\n    ollama pull ":
        "” is not running. Without it your text stays unpolished — the clean-up rules do "
        "keep working.\n\nInstall Ollama and run in Terminal:\n    ollama pull ",
    "Ollama installeren…": "Install Ollama…",
    "Oké": "OK",

    # ---------- welkom / rechten ----------
    "Welkom bij SamFlow": "Welcome to SamFlow",
    "Houd fn ingedrukt, praat, en laat los — de tekst verschijnt waar je typt. Nog een "
    "paar rechten en je bent klaar.":
        "Hold fn, talk, let go — the text appears where you type. A few permissions and "
        "you are ready.",
    "● 100% lokaal — niets gaat naar de cloud":
        "● 100% local — nothing goes to the cloud",
    "Om je stem te horen": "To hear your voice",
    "Invoercontrole": "Input Monitoring",
    "Om de Fn-toets te zien": "To see the Fn key",
    "Toegankelijkheid": "Accessibility",
    "Om de tekst te kunnen plakken": "To be able to paste the text",
    "Openen…": "Open…",
    "Vraag de rechten aan": "Request the permissions",
    "Begin met dicteren": "Start dictating",
    "Toetsenbord": "Keyboard",
    "Fn-toets is vrij — klaar voor SamFlow.": "Fn key is free — ready for SamFlow.",
    "Fn opent nu iets van macOS (emoji-kiezer). Zet Toetsenbord → “Druk op fn” op “Niets "
    "doen”.":
        "Fn currently opens something in macOS (the emoji picker). Set Keyboard → “Press "
        "fn to” to “Do Nothing”.",

    # ---------- samengestelde regels (tellingen, datums) ----------
    "dag": "day",
    "dagen": "days",
    "Vandaag": "Today",
    "Gisteren": "Yesterday",
    "altijd bewaard": "kept forever",
    "bewaart ": "keeps ",
    "piek — ": "peak — ",
    "piekmoment": "peak time",
    "↓ wordt": "↓ becomes",
    "Kopieer": "Copy",
    "van de tijd": "of the time",

    # ---------- menubalk-paneel ----------
    "Houd Fn ingedrukt om te dicteren": "Hold Fn to dictate",
    "Vaak gehoorde woorden reviewen…": "Review frequently heard words…",
    "Stop": "Quit",
    "Open SamFlow…": "Open SamFlow…",
}


_system = [None]      # de systeemtaal is vastgesteld bij de eerste vraag


def _system_language() -> str:
    """"nl" als de Mac Nederlands staat, anders "en". Eén keer bepalen: dit verandert
    niet tijdens het draaien, en het scheelt een NSLocale-call per label."""
    if _system[0] is None:
        try:
            from Foundation import NSLocale
            pref = list(NSLocale.preferredLanguages() or [])
            _system[0] = "nl" if pref and str(pref[0]).startswith("nl") else "en"
        except Exception:
            _system[0] = "nl"
    return _system[0]


def language() -> str:
    """De actieve interfacetaal: "nl" of "en"."""
    keuze = settings.get("ui_language") or "auto"
    return _system_language() if keuze == "auto" else ("en" if keuze == "en" else "nl")


def t(text):
    """Vertaal als de interface Engels staat. Geen vertaling in de tabel? Dan komt de
    Nederlandse tekst terug -- zichtbaar, maar nooit stuk. Niet-strings gaan ongemoeid
    door, zodat een sink 'm blind mag aanroepen."""
    if not isinstance(text, str) or language() == "nl":
        return text
    return EN.get(text, text)


def tt(items):
    """Zelfde, maar voor een lijst labels (Segmented/Dropdown)."""
    return [t(s) for s in items] if isinstance(items, (list, tuple)) else items


# ---------- zelftest ----------
if __name__ == "__main__":
    import ast
    import os

    BASE = os.path.dirname(os.path.abspath(__file__))
    SINKS = {"label", "_label", "section", "_section", "row_label", "_glabel", "_rowhead",
             "buttonWithTitle_target_action_", "setMessageText_", "setInformativeText_",
             "addButtonWithTitle_", "setTitle_", "setPlaceholderString_", "_grp_seg",
             "_grp_drop", "_grp_static", "_grp_keycap", "_grp_button", "_t", "t"}
    # Deze horen niet vertaald te worden: merknaam, toetsnaam, fontgewichten, een
    # settings-sleutel die toevallig een woord is, en de credit.
    NEGEER = {"SamFlow", "SamFlow ", "fn", "bold", "medium", "regular", "language", "© 2026 Sam Kloeth"}

    def zichtbaar(s):
        return (len(s) > 1 and any(c.isalpha() for c in s)
                and not s.startswith(("NS", "http", "com.", "x-apple", "~/", "/"))
                and not (s.endswith(":") and " " not in s)
                and not ("_" in s and " " not in s)
                and not ("." in s and " " not in s and s.islower()))

    gevonden = set()
    for naam in ("prefs.py", "mainwindow.py", "hud.py", "panel.py", "ui.py"):
        with open(os.path.join(BASE, naam), encoding="utf-8") as f:
            boom = ast.parse(f.read(), naam)
        for node in ast.walk(boom):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if (fn.id if isinstance(fn, ast.Name) else
                    getattr(fn, "attr", "")) not in SINKS:
                continue
            for arg in list(node.args) + [k.value for k in node.keywords]:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if zichtbaar(arg.value):
                        gevonden.add(arg.value)
                elif isinstance(arg, ast.BinOp):     # _t("a") + var + _t("b")
                    for deel in ast.walk(arg):
                        if (isinstance(deel, ast.Constant)
                                and isinstance(deel.value, str) and zichtbaar(deel.value)):
                            gevonden.add(deel.value)

    ontbreekt = sorted(s for s in gevonden if s not in EN and s not in NEGEER)
    print(f"{len(EN)} vertalingen; {len(gevonden)} teksten gevonden op aanroepplekken.")
    if ontbreekt:
        print(f"\nNog niet vertaald ({len(ontbreekt)}) -- deze blijven Nederlands:")
        for s in ontbreekt:
            print(f"  {s!r}")
    else:
        print("Alles wat de scan ziet, staat in de tabel.")
