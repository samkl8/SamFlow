"""
stats.py - lokale, inhoudsloze dag-aggregaten voor het dashboard.

Per dag tellen we alleen: aantal dictaten, woorden, spraak-seconden, en de snelste
en totale 'took' (transcriptietijd). **Geen tekst, geen app-namen -- puur getallen.**
Daarom staat dit standaard aan (met een toggle): er is niets gevoeligs om te lekken,
net als de "laatste dictaat"-teller die nu al in het geheugen leeft.

Opslag: ~/Library/Application Support/SamFlow/stats.json -- buiten de repo/git (waar
de updater en git-operaties nooit bij komen), in de map die telemetry al aanmaakt.
Atomisch schrijven (mkstemp + os.replace), zelfde patroon als settings.py. Retentie
RETAIN_DAYS, geprund bij het wegschrijven.

De hook in samflow.handle() draait ná het plakken, op de handle-thread, fail-silent
-- het dictaat gaat altijd voor (zelfde contract als lexicon.record()). Nooit de run
loop of het plakken vertragen; summary() leest het bestand één keer bij openen/tik.
"""
import json
import os
import tempfile
from datetime import date, datetime, timedelta

import settings

APP_SUPPORT = os.path.expanduser("~/Library/Application Support/SamFlow")
STATS_FILE = os.path.join(APP_SUPPORT, "stats.json")

RETAIN_DAYS = 400
TYPING_WPM = 40    # aanname voor "tijd bespaard"; staat als subtekst in de UI


def _read():
    try:
        with open(STATS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


def _write(data):
    os.makedirs(APP_SUPPORT, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=APP_SUPPORT, prefix=".stats-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, STATS_FILE)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _blank():
    return {"dictations": 0, "words": 0, "speech_sec": 0.0,
            "fastest_took": None, "total_took": 0.0, "dayparts": [0, 0, 0, 0]}


def _daypart(hour):
    """0=nacht (0-6u), 1=ochtend (6-12u), 2=middag (12-18u), 3=avond (18-24u). Alleen
    wannéér je dicteert -- inhoudsloos, net als de rest van stats.json."""
    return 0 if hour < 6 else 1 if hour < 12 else 2 if hour < 18 else 3


def record(words, speech_sec, took):
    """Tel één gelukt dictaat bij vandaag. Fail-silent aangeroepen vanuit handle();
    no-op als de gebruiker statistieken heeft uitgezet."""
    if not settings.get("stats_enabled"):
        return
    data = _read()
    key = date.today().isoformat()
    day = data.get(key) or _blank()
    day.setdefault("dayparts", [0, 0, 0, 0])   # oudere dagen misten dit veld
    day["dictations"] += 1
    day["words"] += int(words)
    day["speech_sec"] += float(speech_sec)
    day["total_took"] += float(took)
    day["dayparts"][_daypart(datetime.now().hour)] += 1
    if day["fastest_took"] is None or took < day["fastest_took"]:
        day["fastest_took"] = float(took)
    data[key] = day
    cutoff = (date.today() - timedelta(days=RETAIN_DAYS)).isoformat()
    data = {k: v for k, v in data.items() if k >= cutoff}
    _write(data)


def mtime():
    """De wijzigingstijd van stats.json, of None als 'ie (nog) niet bestaat. Een
    goedkope stat()-call zodat het dashboard alleen herbouwt als er écht een dictaat
    bij kwam (zie mainwindow.refreshTick_) -- geen schijf-lezing per timer-tik."""
    try:
        return os.path.getmtime(STATS_FILE)
    except OSError:
        return None


def _val(data, d, key, default=0):
    return data.get(d.isoformat(), {}).get(key, default) or default


def _longest_streak(data):
    """De langste aaneengesloten reeks dagen met >=1 dictaat over alle bewaarde data --
    het record dat naast de huidige reeks staat ('langste · N dagen'). Puur uit de
    datum-sleutels afgeleid; geen extra opslag."""
    days = sorted(k for k, v in data.items() if int(v.get("dictations", 0)) > 0)
    best = run = 0
    prev = None
    for iso in days:
        try:
            cur = date.fromisoformat(iso)
        except ValueError:
            continue
        run = run + 1 if (prev is not None and (cur - prev).days == 1) else 1
        best = max(best, run)
        prev = cur
    return best


def summary():
    """Een momentopname voor het dashboard: alle afgeleiden in één keer, uit één
    bestandslezing. De UI-laag formatteert (Nederlandse komma's, "u/m")."""
    data = _read()
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    week = [monday + timedelta(days=i) for i in range(7)]
    prev = [monday - timedelta(days=7) + timedelta(days=i) for i in range(7)]

    week_words = [int(_val(data, d, "words")) for d in week]
    words_today = int(_val(data, today, "words"))
    words_week = sum(week_words)
    words_prev = sum(int(_val(data, d, "words")) for d in prev)
    delta = (words_week - words_prev) / words_prev if words_prev > 0 else None

    # tijd bespaard deze week: typtijd (woorden / 40 wpm) minus de spraaktijd
    speech_week = sum(float(_val(data, d, "speech_sec", 0.0)) for d in week)
    saved_sec = max(0.0, words_week / TYPING_WPM * 60.0 - speech_week)

    fastest = None
    for d in week:
        f = data.get(d.isoformat(), {}).get("fastest_took")
        if f is not None and (fastest is None or f < fastest):
            fastest = f

    # streak: aaneengesloten dagen met >=1 dictaat, t/m vandaag (of gisteren als
    # vandaag nog leeg is, zodat één rustige ochtend de reeks niet meteen breekt).
    streak = 0
    d = today if _val(data, today, "dictations") else today - timedelta(days=1)
    while data.get(d.isoformat(), {}).get("dictations", 0) > 0:
        streak += 1
        d -= timedelta(days=1)

    total_dictations = sum(int(v.get("dictations", 0)) for v in data.values())

    # heatmap-data: dag-woorden voor de laatste 26 weken (alleen niet-lege dagen, klein
    # gehouden). De view kiest zelf hoeveel week-kolommen 'ie toont op de vensterbreedte;
    # ontbrekende dagen leest 'ie als 0. Plus de langste reeks als record naast de huidige.
    hm_days = {}
    dd = today - timedelta(days=181)
    while dd <= today:
        wv = int(_val(data, dd, "words"))
        if wv:
            hm_days[dd.isoformat()] = wv
        dd += timedelta(days=1)

    # "Jouw stem": spreektempo + gemiddelde lengte over alles wat we bewaren (stabieler dan
    # één week), en de dagdeel-verdeling (wannéér je dicteert, nooit wát).
    tot_words = sum(int(v.get("words", 0)) for v in data.values())
    tot_speech = sum(float(v.get("speech_sec", 0.0)) for v in data.values())
    wpm = (tot_words / tot_speech * 60.0) if tot_speech > 0 else None
    avg_len = (tot_words / total_dictations) if total_dictations > 0 else None
    dayparts = [0, 0, 0, 0]
    for v in data.values():
        dp = v.get("dayparts") or []
        for i in range(min(4, len(dp))):
            dayparts[i] += int(dp[i])
    peak_daypart = max(range(4), key=lambda i: dayparts[i]) if any(dayparts) else None

    return {
        "words_today": words_today,
        "words_week": words_week,
        "delta": delta,               # float (fractie) of None
        "saved_sec": saved_sec,
        "fastest": fastest,           # float (s) of None
        "streak": streak,
        "week_words": week_words,     # 7 ints, maandag..zondag
        "today_index": today.weekday(),
        "total_dictations": total_dictations,   # goedkope 'is er iets veranderd'-signatuur
        "heatmap_days": hm_days,      # {iso-datum: woorden} voor niet-lege dagen, 26 wk
        "longest_streak": _longest_streak(data),
        "wpm": wpm,                   # gem. woorden/minuut, of None
        "avg_len": avg_len,           # gem. woorden per dictaat, of None
        "dayparts": dayparts,         # [nacht, ochtend, middag, avond]
        "peak_daypart": peak_daypart, # index van het drukste dagdeel, of None
    }
