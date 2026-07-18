"""
history.py - lokale, opt-in historie van je dictaten.

**Standaard uit.** Zolang de toggle uit staat is er geen enkel schrijfpad -- dit
bestand doet dan letterlijk niets (record() keert meteen terug). Zet je 'm bewust
aan (via de Historie-tab of Voorkeuren), dan bewaart SamFlow per dictaat één regel
in ~/Library/Application Support/SamFlow/history.jsonl.

Anders dan stats.json bevat dit bestand wél je tekst. Daarom:
- bestandsrechten 0600 (alleen jij),
- buiten de repo/git en nooit op het netwerk,
- altijd wisbaar (per rij of alles), en een expliciete opt-in om te beginnen.

Per regel (JSONL): ts (epoch), text, app, words, speech_sec, took.

Append gebeurt op de handle-thread, fail-silent (het dictaat gaat altijd voor).
Prunen (ouder dan history_days; 0 = altijd bewaren) bij opstart en na een append,
maar alléén als er echt iets weg moet. Lezen/zoeken/wissen gaat in het geheugen --
het bestand blijft klein (30 dagen dicteren is megabytes, geen gigabytes).
"""
import json
import os
import time

import settings

APP_SUPPORT = os.path.expanduser("~/Library/Application Support/SamFlow")
HISTORY_FILE = os.path.join(APP_SUPPORT, "history.jsonl")


def _ensure_perms():
    try:
        os.chmod(HISTORY_FILE, 0o600)
    except OSError:
        pass


def _read_all():
    out = []
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    out.append(json.loads(ln))
                except ValueError:
                    continue
    except OSError:
        return []
    return out


def _write_all(items):
    if not items:
        clear()
        return
    os.makedirs(APP_SUPPORT, exist_ok=True)
    tmp = HISTORY_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for e in items:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    os.replace(tmp, HISTORY_FILE)
    _ensure_perms()


def record(text, app, words, speech_sec, took):
    """Eén dictaat bijschrijven -- no-op zolang historie uit staat (de opt-in-belofte)."""
    if not settings.get("history_enabled") or not text:
        return
    os.makedirs(APP_SUPPORT, exist_ok=True)
    line = {"ts": time.time(), "text": text, "app": app or "",
            "words": int(words), "speech_sec": float(speech_sec), "took": float(took)}
    new = not os.path.exists(HISTORY_FILE)
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    if new:
        _ensure_perms()
    prune()


def prune():
    """Verwijder regels ouder dan history_days (0 = altijd bewaren). Herschrijft het
    bestand alléén als er echt iets weg moet, zodat een append meestal goedkoop blijft."""
    days = settings.get("history_days")
    if not days:
        return
    cutoff = time.time() - days * 86400
    items = _read_all()
    kept = [e for e in items if e.get("ts", 0) >= cutoff]
    if len(kept) != len(items):
        _write_all(kept)


def entries():
    """Alle dictaten, nieuwste eerst."""
    return sorted(_read_all(), key=lambda e: e.get("ts", 0), reverse=True)


def search(q):
    """entries() gefilterd op tekst of app-naam (leeg = alles)."""
    items = entries()
    q = (q or "").strip().lower()
    if not q:
        return items
    return [e for e in items
            if q in e.get("text", "").lower() or q in e.get("app", "").lower()]


def remove(ts):
    _write_all([e for e in _read_all() if e.get("ts") != ts])


def clear():
    try:
        os.remove(HISTORY_FILE)
    except OSError:
        pass


def count():
    return len(_read_all())


def mtime():
    """De wijzigingstijd van history.jsonl, of None als 'ie (nog) niet bestaat.
    Een goedkope stat()-call zodat de UI z'n historie-lijst uit een cache kan
    serveren tijdens een venster-resize -- geen schijf-lezing per herbouw. De
    mtime verandert vanzelf bij een append/wis, dus de cache invalideert zichzelf."""
    try:
        return os.path.getmtime(HISTORY_FILE)
    except OSError:
        return None
