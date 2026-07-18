"""
telemetry.py - een lichte, anonieme dagelijkse heartbeat. Puur om te tellen hoeveel
mensen SamFlow gebruiken. Meer niet.

Wat er WEL verstuurd wordt: een willekeurige install-id (eenmalig lokaal aangemaakt),
de app-versie (git-sha) en de macOS-versie. Wat er NOOIT verstuurd wordt: je dictaten,
je naam, je bestanden -- niets van je inhoud. Het IP dat een HTTP-call inherent
meestuurt bewaren wij niet (de Apps Script schrijft alleen de vier velden hierboven).

Twee harde remmen:
  1. HEARTBEAT_URL leeg  -> volledig inert. Er wordt niets verzonden en zelfs geen
     install-id aangemaakt. Dit is de default in git, zodat een kloon niks doet tot
     jij bewust een sink invult.
  2. instelling share_usage uit -> niets. Live uit te zetten in de voorkeuren.

En: hooguit één keer per dag, altijd op een achtergrond-thread, en elke fout wordt
stil ingeslikt. Telemetrie mag een dictaat nooit vertragen of laten struikelen.
"""
import json
import os
import platform
import subprocess
import threading
import urllib.request
import uuid
from datetime import date

import settings

# Vul dit met je eigen sink-URL (Google Apps Script web-app, of een Cloudflare
# Worker). LEEG = telemetrie helemaal uit; er wordt dan niets verzonden of
# aangemaakt. Zie de opzet-instructies bij het inschakelen.
HEARTBEAT_URL = ""

_SUPPORT = os.path.expanduser("~/Library/Application Support/SamFlow")
_ID_FILE = os.path.join(_SUPPORT, "install-id")
_LAST_FILE = os.path.join(_SUPPORT, "heartbeat-last")


def _install_id() -> str:
    """Een willekeurige, stabiele id per installatie. Anoniem: een UUID, niets dat
    naar jou te herleiden is. Eenmalig aangemaakt en daarna hergebruikt."""
    try:
        with open(_ID_FILE) as f:
            existing = f.read().strip()
            if existing:
                return existing
    except OSError:
        pass
    new = uuid.uuid4().hex
    try:
        os.makedirs(_SUPPORT, exist_ok=True)
        with open(_ID_FILE, "w") as f:
            f.write(new)
    except OSError:
        pass
    return new


def _already_today() -> bool:
    try:
        with open(_LAST_FILE) as f:
            return f.read().strip() == date.today().isoformat()
    except OSError:
        return False


def _mark_today():
    try:
        os.makedirs(_SUPPORT, exist_ok=True)
        with open(_LAST_FILE, "w") as f:
            f.write(date.today().isoformat())
    except OSError:
        pass


def _app_version() -> str:
    """Korte git-sha van de repo; faalt stil naar 'onbekend'."""
    try:
        base = os.path.dirname(os.path.abspath(__file__))
        out = subprocess.run(["git", "-C", base, "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=2)
        return out.stdout.strip() or "onbekend"
    except Exception:
        return "onbekend"


def _send():
    payload = {
        "id": _install_id(),
        "version": _app_version(),
        "os": platform.mac_ver()[0] or "?",
        "day": date.today().isoformat(),
    }
    req = urllib.request.Request(
        HEARTBEAT_URL, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=5).read()
        _mark_today()
    except Exception:
        pass  # nooit een dictaat laten struikelen over telemetrie


def maybe_send():
    """Verstuur hooguit één keer per dag, op een eigen thread. Inert als er geen
    sink is ingesteld of als de gebruiker `share_usage` heeft uitgezet."""
    if not HEARTBEAT_URL:
        return
    if not settings.get("share_usage"):
        return
    if _already_today():
        return
    threading.Thread(target=_send, daemon=True).start()
