#!/usr/bin/env python3
"""
lexicon.py - jouw persoonlijke woordenlijst, plus wat samflow er zelf van leert.

Het idee: jij onderhoudt één lijst met termen, de rest gaat vanzelf. Die lijst
doet twee dingen tegelijk:

  1. STUREN   -> de termen gaan als woordenlijst mee in de Whisper-prompt, zodat
                 de decoder ze al kent voordat hij gokt (nul latency).
  2. SNAPPEN  -> na de transcriptie snapt canonicalise() elke variant terug naar
                 de juiste vorm: hoofdletters en splitsingen. "graph ql",
                 "graphql", "Graph QL" -> allemaal "GraphQL".

De harde regel: de corrector raakt NOOIT een gewoon Nederlands woord aan. Hij
matcht alleen de letters van een term die JIJ hebt toegevoegd, en tolereert daar
alleen een spatie/koppelteken op de plekken waar de term zelf al een grens heeft
(camelCase, cijfer, koppelteken). Een gewoon woord blijft dus altijd staan.

Wat de corrector niet vangt zijn fonetische missers (je zegt GitHub, Whisper
tikt "gitub"): die letters lijken niet genoeg. Daarvoor is mappings.txt -- een
geleerde "gehoorde vorm = Canoniek". Die groeit via `samflow.py --review`, dat je
de woorden voorstelt die je vaak zei maar samflow nog niet kende.

Drie bestanden, alle naast deze code en alle buiten git (persoonlijk):

  lexicon.txt      jouw termen, canonieke schrijfwijze, een per regel (# = comment)
  mappings.txt     "gitub = GitHub", voor fonetische missers
  candidates.json  telt wat je vaak zei maar nog niet kent; voer voor --review

lexicon.txt en mappings.txt worden per dictaat opnieuw gelezen (mtime-cache), dus
een woord toevoegen werkt meteen -- geen herstart nodig.
"""

import json
import os
import re

BASE = os.path.dirname(os.path.abspath(__file__))
LEXICON_FILE = os.path.join(BASE, "lexicon.txt")
MAPPINGS_FILE = os.path.join(BASE, "mappings.txt")
CANDIDATES_FILE = os.path.join(BASE, "candidates.json")

# ---------- config ----------
AUTO_PROMOTE = False        # True: vaak-gehoorde woorden vanzelf aan lexicon toevoegen
AUTO_PROMOTE_AFTER = 8      # ... zodra ze zo vaak gehoord zijn (ruw; standaard uit)
CANDIDATE_MIN_LEN = 4       # korter dan dit is zelden jargon
REVIEW_TOP = 15             # hoeveel kandidaten --review toont
# ----------------------------

# Neutrale basislijst -- generieke termen die iedereen kan gebruiken. Personaliseer
# via lexicon.txt (persoonlijk, buiten git): daar horen je eigen jargon, merken en
# projectnamen. Houd deze lijst neutraal, zodat de repo deelbaar blijft.
DEFAULT_TERMS = [
    "GitHub", "GitLab", "GraphQL", "PostgreSQL", "TypeScript", "JavaScript",
    "OAuth", "npm", "API", "URL", "SDK", "CLI",
    "repo", "commit", "branch", "deploy", "staging", "webhook", "endpoint",
]

# Termen die óók een gewoon woord zijn: wel meesturen in de prompt, maar niet
# overal met hoofdletter forceren ("een meta-analyse" moet blijven staan).
AMBIGUOUS = {"meta"}

# Veelvoorkomende woorden die de leer-loop nooit als kandidaat voorstelt. Hoeft
# niet volledig te zijn; hij haalt alleen de grootste ruis eruit.
STOPWORDS = {
    "de", "het", "een", "en", "van", "ik", "te", "dat", "die", "in", "is", "je",
    "niet", "met", "op", "voor", "zijn", "er", "maar", "om", "aan", "ook", "als",
    "dan", "of", "wat", "hij", "we", "naar", "nog", "wel", "mijn", "me", "was",
    "worden", "wordt", "heb", "heeft", "had", "ben", "bent", "gaat", "gaan", "ga",
    "doen", "doet", "doe", "dit", "deze", "daar", "hier", "waar", "wie", "hoe",
    "want", "dus", "even", "gewoon", "echt", "heel", "meer", "minder", "moet",
    "moeten", "kan", "kunnen", "kun", "wil", "willen", "zou", "zal", "zullen",
    "mag", "laat", "laten", "maak", "maken", "zet", "zetten", "check", "dan",
    "nu", "al", "geen", "veel", "weer", "zo", "toch", "over", "onder", "tot",
    "door", "bij", "uit", "af", "tegen", "tussen", "zonder", "omdat", "terwijl",
    "the", "and", "to", "of", "it", "for", "with", "this", "that",
}

# ---------- lezen, met mtime-cache zodat edits meteen tellen ----------

_cache = {}


def _read(path, build):
    """Lees + parse een bestand, gecached op mtime. build() krijgt de regels."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = None
    hit = _cache.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    lines = []
    if mtime is not None:
        with open(path, encoding="utf-8") as f:
            lines = [ln.split("#", 1)[0].strip() for ln in f]
    value = build([ln for ln in lines if ln])
    _cache[path] = (mtime, value)
    return value


def terms():
    """Basislijst + persoonlijke lexicon.txt, ontdubbeld, canonieke vorm behouden."""
    extra = _read(LEXICON_FILE, lambda lines: lines)
    out, seen = [], set()
    for t in DEFAULT_TERMS + extra:
        k = t.lower()
        if k not in seen:
            seen.add(k)
            out.append(t)
    return out


def mappings():
    """Gehoorde vorm -> canoniek, uit mappings.txt ('gitub = GitHub')."""
    def build(lines):
        out = {}
        for ln in lines:
            if "=" in ln:
                heard, canon = (s.strip() for s in ln.split("=", 1))
                if heard and canon:
                    out[heard.lower()] = canon
        return out
    return _read(MAPPINGS_FILE, build)


# ---------- de corrector ----------

_regex_cache = {}


def _parts(term):
    """Splits een term op zijn eigen grenzen: koppelteken, camelCase, cijfergrens.
    'GitHub' -> ['Git','Hub'];  'well-known' -> ['well','known'];  'oauth2' -> ['oauth','2']."""
    s = term.replace("-", " ")
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)      # camelCase
    s = re.sub(r"(?<=[A-Za-z])(?=[0-9])", " ", s)   # letter -> cijfer
    s = re.sub(r"(?<=[0-9])(?=[A-Za-z])", " ", s)   # cijfer -> letter
    return [p for p in s.split() if p]


def _tolerant_regex(source):
    """Regex die de letters van 'source' matcht, met een optionele spatie/koppel-
    teken op elke woordgrens, en niet middenin een groter woord."""
    if source not in _regex_cache:
        body = r"[\s\-]?".join(re.escape(p) for p in (_parts(source) or [source]))
        _regex_cache[source] = re.compile(
            r"(?<![A-Za-z0-9])" + body + r"(?![A-Za-z0-9])", re.IGNORECASE)
    return _regex_cache[source]


def canonicalise(text):
    """Snap elke gehoorde variant terug naar de juiste vorm. Eerst de geleerde
    fonetische mappings, dan de woordenlijst-termen (hoofdletters + splitsingen)."""
    for heard, canon in mappings().items():
        text = _tolerant_regex(heard).sub(lambda m, c=canon: c, text)
    for term in terms():
        if term.lower() in AMBIGUOUS:
            continue
        text = _tolerant_regex(term).sub(lambda m, t=term: t, text)
    return text


# ---------- de leer-loop ----------

def _known_forms():
    """Alles wat we al kennen (termen, hun losse delen, geleerde gehoorde vormen),
    genormaliseerd, zodat de leer-loop die niet opnieuw voorstelt."""
    forms = set(STOPWORDS)
    for t in terms():
        forms.add(t.lower())
        for p in _parts(t):
            forms.add(p.lower())
    forms.update(mappings().keys())
    return forms


def _load_candidates():
    try:
        with open(CANDIDATES_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    data.setdefault("counts", {})
    data.setdefault("ignored", [])
    return data


def _save_candidates(data):
    tmp = CANDIDATES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=0)
    os.replace(tmp, CANDIDATES_FILE)


def record(raw_text):
    """Tel de woorden uit een ruwe transcriptie die we nog niet kennen. Draait op
    de handle()-thread na elk dictaat; faalt nooit hard (dictaat gaat voor)."""
    try:
        known = _known_forms()
        tokens = [t.lower() for t in re.findall(r"[A-Za-zÀ-ÿ]+", raw_text)]
        fresh = [t for t in tokens if len(t) >= CANDIDATE_MIN_LEN and t not in known]
        if not fresh:
            return
        data = _load_candidates()
        ignored = set(data["ignored"])
        promoted = []
        for t in fresh:
            if t in ignored:
                continue
            data["counts"][t] = data["counts"].get(t, 0) + 1
            if AUTO_PROMOTE and data["counts"][t] >= AUTO_PROMOTE_AFTER:
                promoted.append(t)
        for t in promoted:                    # ruwe auto-toevoeging (standaard uit)
            add_term(t)
            data["counts"].pop(t, None)
            data["ignored"].append(t)
        _save_candidates(data)
    except Exception:
        pass


def add_term(term):
    """Voeg een term toe aan lexicon.txt. Weigert te korte onzin. True als het lukte."""
    term = term.strip()
    if len(term) < 2:
        return False
    with open(LEXICON_FILE, "a", encoding="utf-8") as f:
        f.write(term + "\n")
    _cache.pop(LEXICON_FILE, None)
    return True


def add_mapping(heard, canon):
    """Leer een fonetische misser: 'heard = Canonical' naar mappings.txt. Weigert een
    leeg of eenletterig doel -- zoiets ('dashboard = m') zou een gewoon woord slopen.
    True als het lukte."""
    heard, canon = heard.strip(), canon.strip()
    if not heard or len(canon) < 2:
        return False
    with open(MAPPINGS_FILE, "a", encoding="utf-8") as f:
        f.write(f"{heard} = {canon}\n")
    _cache.pop(MAPPINGS_FILE, None)
    return True


def custom_terms():
    """Alleen de persoonlijke termen uit lexicon.txt (dus zonder DEFAULT_TERMS). Voor
    de UI: die staan in de code en kunnen niet weg; deze wél."""
    return list(_read(LEXICON_FILE, lambda lines: lines))


# ---------- niet-interactieve leer-loop-API (gedeeld door --review en de UI) ----------

def suggestions(top=REVIEW_TOP):
    """Vaak-gehoorde onbekende woorden als [(woord, aantal)], gerankt en zonder de
    genegeerde -- dezelfde selectie die review() toont."""
    data = _load_candidates()
    ignored = set(data["ignored"])
    ranked = sorted(data["counts"].items(), key=lambda kv: kv[1], reverse=True)
    return [(w, n) for w, n in ranked if w not in ignored][:top]


def _resolve_candidate(word):
    """Haal een afgehandeld kandidaat-woord uit de teller en zet 'm op ignored, zodat
    hij niet opnieuw wordt voorgesteld."""
    data = _load_candidates()
    data["counts"].pop(word, None)
    if word not in data["ignored"]:
        data["ignored"].append(word)
    _save_candidates(data)


def accept(word, spelling=None):
    """Voeg een kandidaat toe als term (met optionele schrijfwijze) en handel 'm af."""
    if add_term(spelling or word):
        _resolve_candidate(word)
        return True
    return False


def map_to(word, canon):
    """Leer een kandidaat als fonetische mapping (word = canon) en handel 'm af."""
    if add_mapping(word, canon):
        _resolve_candidate(word)
        return True
    return False


def ignore(word):
    """Negeer een kandidaat voorgoed -- nooit meer voorstellen."""
    _resolve_candidate(word)


# ---------- verwijderen (regel-gefilterd, comments/volgorde behouden) ----------

def _rewrite_lines(path, keep):
    """Herschrijf een bestand regel voor regel: keep(inhoud) bepaalt of een niet-
    comment-regel blijft. Comment- en lege regels blijven altijd staan, zodat de
    bestanden hand-bewerkbaar blijven. Atomisch; invalideert de mtime-cache."""
    try:
        with open(path, encoding="utf-8") as f:
            original = f.readlines()
    except OSError:
        return
    out = [ln for ln in original
           if not ln.split("#", 1)[0].strip() or keep(ln.split("#", 1)[0].strip())]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(out)
    os.replace(tmp, path)
    _cache.pop(path, None)


def remove_term(term):
    """Verwijder een persoonlijke term uit lexicon.txt (hoofdletterongevoelig).
    DEFAULT_TERMS staan in de code en kunnen hier niet weg."""
    t = term.strip().lower()
    _rewrite_lines(LEXICON_FILE, lambda line: line.lower() != t)


def remove_mapping(heard):
    """Verwijder een correctie uit mappings.txt op de gehoorde vorm (links van '=')."""
    h = heard.strip().lower()
    _rewrite_lines(
        MAPPINGS_FILE,
        lambda line: "=" not in line or line.split("=", 1)[0].strip().lower() != h)


def review():
    """Interactief: toont vaak-gehoorde onbekende woorden en laat je ze afhandelen.
    Bedoeld voor `samflow.py --review` vanuit een terminal. Deelt de bewegingen
    (suggestions/accept/map_to/ignore) met de Woordenlijst-UI."""
    ranked = suggestions()
    if not ranked:
        print("Niets te reviewen -- nog geen onbekende woorden verzameld.")
        return

    print("Vaak gehoord, nog niet in je woordenlijst:\n")
    print("  [a] toevoegen als term   [m] mappen naar bestaande vorm")
    print("  [i] negeren (nooit meer)  [Enter] overslaan   [q] stoppen\n")
    for word, count in ranked:
        choice = input(f"  \"{word}\" ({count}x)  [a/m/i/Enter/q] ").strip().lower()
        if choice == "q":
            break
        if choice == "a":
            spelling = input(f"      schrijfwijze [{word}]: ").strip() or word
            if accept(word, spelling):
                print(f"      + '{spelling}' toegevoegd aan lexicon.txt")
            else:
                print("      ongeldig (te kort), overgeslagen")
        elif choice == "m":
            canon = input(f"      '{word}' hoort te zijn (canonieke vorm): ").strip()
            if map_to(word, canon):
                print(f"      + '{word} = {canon}' toegevoegd aan mappings.txt")
            else:
                print("      ongeldig (leeg of te kort) -- niet opgeslagen")
        elif choice == "i":
            ignore(word)
    print("\nKlaar. Nieuwe termen tellen meteen mee -- geen herstart nodig.")


if __name__ == "__main__":
    import sys
    if "--review" in sys.argv:
        review()
    else:
        print("termen:", terms())
        print("mappings:", mappings())
        for probe in ["graph ql", "type script", "de markt", "git hub", "well known"]:
            print(f"  {probe!r:20} -> {canonicalise(probe)!r}")
