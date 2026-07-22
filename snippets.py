"""
snippets.py - trigger-frase -> expansie, de laatste laag ná transcriptie.

Je zegt "mijn linkedin" en SamFlow plakt de volledige URL. Dezelfde belofte als lexicon:
NOOIT iets aanraken buiten de lijst. Alleen een trigger die je zélf toevoegde wordt
vervangen, als hele frase (op woordgrenzen), en de vervanging is letterlijk wat je invulde.

Draait als allerlaatste stap in samflow.handle() -- ná cleanup én ná het oppoets-model --
zodat geen enkele laag een URL of handtekening nog verbouwt.

Matching (net als lexicon.canonicalise een bewuste, enge match):
- De trigger matcht genormaliseerd (kleinletters, flexibele witruimte tussen de woorden),
  begrensd door woordgrenzen -- "mijn linkedin" matcht ook "... mijn linkedin." maar nooit
  een deel van een woord.
- Alle triggers gaan in één regex-pas (langste eerst), zodat een net ingevoegde expansie
  nooit zelf opnieuw als trigger wordt gelezen, en een langere frase wint van een kortere.

Opslag: ~/Library/Application Support/SamFlow/snippets.json (0600, buiten git -- kan een
handtekening of bankgegevens bevatten, dus net als history.jsonl niet naast een checkout).
Per dictaat opnieuw gelezen (mtime-cache): een nieuwe snippet werkt meteen, zonder herstart.
"""
import json
import os
import re
import tempfile

APP_SUPPORT = os.path.expanduser("~/Library/Application Support/SamFlow")
SNIPPETS_FILE = os.path.join(APP_SUPPORT, "snippets.json")

# Een trigger korter dan dit (genormaliseerd) is verdacht: één kort/algemeen woord dat je
# per ongeluk zegt. De UI waarschuwt erop; de engine zelf vervangt gewoon wat er staat.
MIN_SAFE_TRIGGER = 6

_cache = None    # (mtime, [(trigger, expansion), ...])


def _norm(s):
    """Kleinletters + witruimte samengevouwen -- zodat het matchen en het ontdubbelen niet
    struikelen over een hoofdletter of dubbele spatie."""
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _read():
    """Lees + parse snippets.json, gecached op mtime (zoals lexicon)."""
    global _cache
    try:
        mtime = os.path.getmtime(SNIPPETS_FILE)
    except OSError:
        mtime = None
    if _cache is not None and _cache[0] == mtime:
        return _cache[1]
    items = []
    if mtime is not None:
        try:
            with open(SNIPPETS_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for e in data:
                    if not isinstance(e, dict):
                        continue
                    t = (e.get("trigger") or "").strip()
                    x = e.get("expansion") or ""
                    if t and x:
                        items.append((t, x))
        except (ValueError, OSError):
            items = []
    _cache = (mtime, items)
    return items


def _write(items):
    os.makedirs(APP_SUPPORT, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=APP_SUPPORT, prefix=".snippets-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump([{"trigger": t, "expansion": x} for t, x in items],
                      f, ensure_ascii=False, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, SNIPPETS_FILE)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    global _cache
    _cache = None


def items():
    """[(trigger, expansion), ...] in de bewaarde volgorde. Voor de UI."""
    return list(_read())


def is_risky_trigger(trigger):
    """Waarschuwsignaal voor de UI: een te korte/algemene trigger die je per ongeluk zegt."""
    n = _norm(trigger)
    return len(n) < MIN_SAFE_TRIGGER or (" " not in n and len(n) < 10)


def apply(text):
    """Vervang elke trigger-frase in `text` door z'n expansie. Onbekende/lege lijst of lege
    tekst -> onveranderd terug. Eén regex-pas (langste trigger eerst) zodat een ingevoegde
    expansie nooit zelf opnieuw wordt gescand."""
    its = _read()
    if not its or not text:
        return text
    its = sorted(its, key=lambda tx: -len(tx[0]))
    lookup, parts = {}, []
    for trig, exp in its:
        toks = trig.split()
        if not toks:
            continue
        parts.append(r"\s+".join(re.escape(tok) for tok in toks))
        lookup[_norm(trig)] = exp
    if not parts:
        return text
    rx = re.compile(r"(?<!\w)(" + "|".join(parts) + r")(?!\w)", re.IGNORECASE)
    return rx.sub(lambda m: lookup.get(_norm(m.group(0)), m.group(0)), text)


def add(trigger, expansion):
    """Voeg toe (of vervang een bestaande met dezelfde genormaliseerde trigger). Interne
    newlines in de expansie blijven (handtekeningen); rand-witruimte gaat eraf."""
    trigger = (trigger or "").strip()
    expansion = (expansion or "").strip()
    if not trigger or not expansion:
        return False
    key = _norm(trigger)
    kept = [(t, x) for (t, x) in _read() if _norm(t) != key]
    kept.append((trigger, expansion))
    _write(kept)
    return True


def remove(trigger):
    key = _norm(trigger)
    _write([(t, x) for (t, x) in _read() if _norm(t) != key])
