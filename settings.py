"""
settings.py - de gebruikersvoorkeuren van SamFlow, als klein JSON-bestand.

Eén bron van waarheid voor de knoppen in het voorkeuren-venster. Net als de
woordenlijst wordt het bestand per dictaat opnieuw gelezen (mtime-cache), zodat
een wijziging in het venster meteen effect heeft zonder herstart. Ontbreekt het
bestand of een sleutel, dan gelden de DEFAULTS -- en die matchen exact het
gedrag van vóór dit venster bestond, zodat een verse installatie (nog geen
settings.json) zich identiek gedraagt.

Persoonlijk en machine-lokaal: settings.json staat buiten git (zie .gitignore),
net als lexicon.txt.
"""
import json
import os
import tempfile

BASE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(BASE, "settings.json")

# De defaults spiegelen de constanten in samflow.py van vóór dit venster; een
# ontbrekende sleutel valt hierop terug, dus 'geen settings.json' == oud gedrag.
DEFAULTS = {
    "language": "nl",     # "nl" | "en" | "auto" -- per dictaat naar whisper-server
    "sound_cues": True,   # klik bij start / stop / klaar
    "pause_media": True,  # Spotify/video pauzeren tijdens een dictaat
    "show_pill": True,    # de zwevende pill bij de cursor (menubalk blijft altijd)
    "pill_position": "caret",  # plek van de pill: "caret" (volgt je typen) | "bottom" (onderin) | "fixed" (vaste hoek)
    "pill_size": "fors",       # staafjes-grootte: "compact" | "ruim" | "fors"
    "pill_motion": "soepel",   # animatie-gevoel: "soepel" | "kwiek"
    "model": "turbo",     # "turbo" | "large-v3" -- nog niet live, zie voorkeuren
    "auto_update": True,  # op de achtergrond bijwerken vanaf GitHub (fast-forward)
    "keep_alive": True,   # watchdog brengt de app terug als 'ie onverwacht stopt (zie launchd/watchdog.sh)
    "share_usage": True,  # anonieme dagelijkse heartbeat (alleen tellen, nooit inhoud); zie telemetry.py
    "lock_mode": "off",   # vastzetten zonder Fn vast te houden: "off"|"tap"|"double"|"chord" (Fn+⌘)
    "app_mode": "basic",  # aanwezigheid: "basic" (menubalk-accessoire, zoals altijd) | "app" (dock-icoon + ⌘Tab); zie appmode.py
    "stats_enabled": True,  # inhoudsloze dag-tellingen voor het dashboard (nooit tekst); zie stats.py
    "history_enabled": False,  # opt-in: bewaar je dictaten (mét tekst) lokaal; standaard UIT; zie history.py
    "history_days": 30,     # retentie in dagen (0 = altijd bewaren)
}

_cache = None  # (mtime, dict) -- zelfde patroon als lexicon._read


def _read_file():
    """settings.json inlezen; een kapot of onleesbaar bestand geeft {} terug,
    zodat we terugvallen op de defaults i.p.v. te crashen of het te overschrijven."""
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


def _load():
    global _cache
    try:
        mtime = os.path.getmtime(SETTINGS_FILE)
    except OSError:
        mtime = None
    if _cache and _cache[0] == mtime:
        return _cache[1]
    merged = {**DEFAULTS, **(_read_file() if mtime is not None else {})}
    _cache = (mtime, merged)
    return merged


def get(key):
    """De waarde voor `key`, of de default als 'ie ontbreekt/onbekend is."""
    return _load().get(key, DEFAULTS.get(key))


def current():
    """Een kopie van alle actuele waarden (defaults + wat op schijf staat)."""
    return dict(_load())


def set(key, value):
    """Schrijf één sleutel weg. Atomisch (schrijf-dan-hernoem) zodat een half
    geschreven bestand nooit stilletjes de defaults terugzet."""
    data = _read_file()
    data[key] = value
    fd, tmp = tempfile.mkstemp(dir=BASE, prefix=".settings-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, SETTINGS_FILE)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    global _cache
    _cache = None  # forceer herlezen bij de volgende get()
