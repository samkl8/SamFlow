#!/usr/bin/env python3
"""
cleanup.py - turn a raw Whisper transcript into text you'd actually have typed.

Two layers, applied in this order:

  1. WOORDENLIJST  jouw termen (lexicon.py) gaan als initial prompt naar Whisper en
                   sturen de decoder vóór hij gokt; achteraf snapt lexicon.canonicalise
                   elke variant terug naar de juiste vorm (hoofdletters + splitsingen).
  2. RULES         deterministische mop-up van wat de decoder nog fout doet: fonetische
                   missers, stopwoorden, stotters, stilte-hallucinaties, hoofdletters.

De woordenlijst onderhoud je in lexicon.py / lexicon.txt, de rest hier.
Run `python cleanup.py` to see the rules applied to a set of examples.
"""

import re
import unicodedata

import lexicon
import settings

# ---------- config ----------
ENABLE_COMMANDS = True     # spoken "nieuwe regel" becomes an actual newline
ENABLE_STUTTER = True      # collapse "naar naar" -> "naar"
ENABLE_LISTS = True        # "ten eerste ... ten tweede ..." -> een genummerde lijst
# ----------------------------


# De woordenlijst zelf staat in lexicon.py (plus je persoonlijke lexicon.txt). Die
# stuurt Whisper vooraf én corrigeert achteraf hoofdletters en splitsingen van elke
# term. Een woord toevoegen doe je daar of via `samflow.py --review`, niet hier.


# Fonetische missers die noch de woordenlijst noch canonicalise() vangen -- de letters
# liggen te ver weg, of het woord splitst op een niet-structurele plek ("launch d").
# Persoonlijke gevallen leer je via `samflow.py --review` (schrijft naar mappings.txt);
# dit zijn de ingebouwde. Elk patroon wordt afgedwongen door een voorbeeld in EXAMPLES.
REPLACEMENTS = {
    r"\blaunch ?d\b": "launchd",
}


# Whisper invents these when handed silence or a stray breath. If the whole
# transcript reduces to one of them, throw it away rather than paste it.
# The energy gate in samflow.py catches most silence before it ever gets here;
# this is the backstop for a clip that is quiet but not quite silent.
HALLUCINATIONS = [
    r"ondertitel(?:d|ing)",
    r"amara\.org",
    r"abonneer",
    r"bedankt voor het kijken",
    r"thanks? for watching",
    r"untertitel",
    r"^\W*$",                        # nothing but punctuation
    r"^\[.*\]$",                     # [BLANK_AUDIO], [Muziek]
    r"^\(.*\)$",
    # Whisper's derde notatie voor niet-spraak: *repeat*, *music*, *applause*. Gemeten
    # met een radio aan en een zwijgende spreker -- de energie-poort liet die 6 seconden
    # door (luidste 100ms RMS 162, boven SILENCE_RMS 120) en '*repeat*' werd geplakt.
    # Bewust [^*] en niet .*: '*echt* nu, en *meteen*' begint én eindigt op een ster,
    # maar is gewone tekst met nadruk. Alleen één ononderbroken sterretjes-blok telt.
    r"^\*[^*]*\*$",
    r"^(?:www\.|https?://)",         # a bare URL and nothing else
    r"^[\w\-]+(?:\.[\w\-]+){2,}$",   # a.b.c domain and nothing else
]


FILLERS = r"\b(?:u+h+m?|e+h+m?|a+h+m|hmm+|ehm)\b"


# ---------- taal ----------
# Fillers, hallucinaties, hoofdletters en het plakken van segmenten zijn taalneutraal en
# gelden altijd. Wat wél aan een taal hangt, staat hieronder per taal: het woord waarmee
# de woordenlijst aan Whisper wordt voorgesteld, de gesproken commando's, de opsomming-
# markers, en de woorden die een taal legitiem verdubbelt.
#
# Een profiel vult alléén in wat we van die taal weten. Wat het openlaat -- en alles bij
# "auto", waar we vooraf niet weten wat er komt -- valt terug op de vereniging van alle
# profielen. Dat is bewust de conservatieve kant: een ruimere stotter-uitzonderingslijst
# laat juist méér staan, en commando's en markers zijn letterlijke woorden die in een
# andere taal simpelweg niet voorkomen. Een taal toevoegen aan de dropdown zonder profiel
# is dus veilig -- je verliest alleen de commando's en opsommingen van die taal.
LANGS = {
    "nl": {
        "prompt": "Woordenlijst",
        # "het feit dat dat werkt" -- geen stotter maar Nederlands
        "stutter_ok": {"dat", "die", "heel", "had"},
        "commands": {r"\bnieuwe? regel\b": "\n", r"\bnieuwe? alinea\b": "\n\n"},
        # "ten" matcht alleen mét ordinaal erachter, dus "ten opzichte" blijft ongemoeid
        "list": r"ten\s+(?:eerste|tweede|derde|vierde|vijfde|zesde|zevende|achtste|"
                r"negende|tiende)|punt\s+(?:een|één|twee|drie|vier|vijf|zes|zeven|acht|"
                r"negen|tien|\d+)",
    },
    "en": {
        "prompt": "Vocabulary",
        # "the fact that that works", "he had had enough"
        "stutter_ok": {"that", "had", "very"},
        "commands": {r"\bnew ?line\b": "\n", r"\bnew paragraph\b": "\n\n"},
        # bewust de bijwoorden: kaal "first"/"second" staat in elke gewone zin
        "list": r"(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)ly"
                r"|point\s+(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)",
    },
    "de": {
        "prompt": "Wortliste",
        "commands": {r"\bneue zeile\b": "\n", r"\bneuer absatz\b": "\n\n"},
        "list": r"(?:erstens|zweitens|drittens|viertens|fünftens|sechstens|siebtens|"
                r"achtens|neuntens|zehntens)",
    },
    "fr": {
        "prompt": "Vocabulaire",
        "commands": {r"\bnouvelle ligne\b": "\n", r"\bnouveau paragraphe\b": "\n\n"},
        "list": r"(?:premièrement|deuxièmement|troisièmement|quatrièmement|cinquièmement)",
    },
    "es": {
        "prompt": "Vocabulario",
        "commands": {r"\bnueva línea\b": "\n", r"\bnuevo párrafo\b": "\n\n"},
        "list": r"(?:primero|segundo|tercero|cuarto|quinto)\s*[,:]",
    },
    "it": {
        "prompt": "Vocabolario",
        "commands": {r"\bnuova riga\b": "\n", r"\bnuovo paragrafo\b": "\n\n"},
    },
    "pt": {
        "prompt": "Vocabulário",
        "commands": {r"\bnova linha\b": "\n", r"\bnovo parágrafo\b": "\n\n"},
    },
}


def _union(field):
    """Alles wat élk profiel voor dit veld meebrengt -- de terugval voor 'auto' en voor
    een taal zonder (volledig) profiel."""
    if field == "commands":
        out = {}
        for prof in LANGS.values():
            out.update(prof.get("commands", {}))
        return out
    if field == "stutter_ok":
        out = set()
        for prof in LANGS.values():
            out |= prof.get("stutter_ok", set())
        return out
    return [p["list"] for p in LANGS.values() if p.get("list")]


COMMANDS = _union("commands")            # de terugval; per taal smaller via LANGS
STUTTER_ALLOW = _union("stutter_ok")
_LIST_ALL = re.compile(r"\b(?:" + "|".join(_union("list")) + r")\b", re.IGNORECASE)
_LIST_CACHE = {}


def _lang(lang=None) -> str:
    """De ingestelde dicteertaal. Wordt per dictaat opnieuw gelezen (settings heeft z'n
    eigen mtime-cache), dus een wissel in het venster werkt meteen."""
    if lang:
        return lang
    return settings.get("language") or "auto"


def _rules(lang=None) -> tuple:
    """(commando's, stotter-uitzonderingen, opsomming-regex) voor deze taal."""
    prof = LANGS.get(_lang(lang), {})
    marker = _LIST_ALL
    if prof.get("list"):
        key = prof["list"]
        if key not in _LIST_CACHE:
            _LIST_CACHE[key] = re.compile(r"\b(?:" + key + r")\b", re.IGNORECASE)
        marker = _LIST_CACHE[key]
    return (prof.get("commands", COMMANDS),
            prof.get("stutter_ok", STUTTER_ALLOW),
            marker)


def whisper_prompt(lang=None) -> str:
    """The initial_prompt handed to Whisper. A plain comma list conditions fine.
    Terms come from lexicon.py: built-in defaults plus your personal lexicon.txt.

    Het label ervoor staat in de taal van het dictaat, want de initial prompt stuurt óók
    de taalkeuze van de decoder: "Woordenlijst:" vóór een Engels dictaat duwt Whisper
    richting Nederlands. Bij "auto" (en bij een taal zonder profiel) laten we het label
    daarom helemaal weg -- de kale termenlijst stuurt de taal het minst."""
    label = LANGS.get(_lang(lang), {}).get("prompt")
    terms = ", ".join(lexicon.terms())
    return f"{label}: {terms}." if label else f"{terms}."


def _join_segments(text: str) -> str:
    """
    whisper-server hands back segments separated by newlines, and every real
    segment begins with a leading space (' Eerste zin.\\n Tweede zin.').
    It also sometimes emits a stray newline *inside* a word ('KM\\nUTS'), and
    that one has no space after it. So the space is the discriminator: keep it
    as a separator, and close up the break when it is missing.
    """
    text = re.sub(r"\n(?=\S)", "", text)     # in-word break -> close it up
    return re.sub(r"\s*\n\s*", " ", text)    # real segment boundary -> one space


def _is_hallucination(text: str) -> bool:
    t = text.strip().lower()
    return any(re.search(p, t) for p in HALLUCINATIONS)


def _collapse_stutter(text: str, allow=None) -> str:
    allow = STUTTER_ALLOW if allow is None else allow

    def repl(m):
        word = m.group(1)
        return m.group(0) if word.lower() in allow else word
    return re.sub(r"\b(\w+)(?:\s+\1\b)+", repl, text, flags=re.IGNORECASE)


def _format_lists(text: str, marker=None) -> str:
    """Gesproken opsommingen met >=2 ordinaal-markers ('ten eerste ... ten tweede ...',
    'punt een ... punt twee ...') worden een genummerde lijst. De nummering telt zelf
    door, dus een misgehoorde ordinaal maakt niet uit. Onder de twee markers raken we
    niets aan -- een losse 'ten eerste' hoort gewoon in de zin. De tekst vóór de eerste
    marker blijft als aanloop-regel boven de lijst staan."""
    marks = list((marker or _LIST_ALL).finditer(text))
    if len(marks) < 2:
        return text
    lead = text[:marks[0].start()].strip()
    items = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        item = text[m.end():end]
        item = re.sub(r"^[\s,:;.\-]+", "", item)                            # leesteken vooraan weg
        item = re.sub(r"^(?:en|dan|ook)\s+", "", item, flags=re.IGNORECASE)  # bindwoord vooraan weg
        item = item.strip().rstrip(".").strip()
        if item:
            items.append(f"{len(items) + 1}. {item[0].upper()}{item[1:]}")
    if len(items) < 2:
        return text
    block = "\n".join(items)
    return f"{lead}\n{block}" if lead else block


def _sentence_case(text: str) -> str:
    """
    Capitalise the first letter, and the first letter after a sentence ends.
    A full stop only ends a sentence when whitespace follows it - otherwise
    'example.com' becomes 'Example.Com' and 'versie 3.5 is af' becomes '3.5 Is af'.
    """
    text = re.sub(r"([.!?]\s+|\n+)([a-zà-ÿ])",
                  lambda m: m.group(1) + m.group(2).upper(), text)
    return re.sub(r"\A(\W*)([a-zà-ÿ])",
                  lambda m: m.group(1) + m.group(2).upper(), text)


def clean(text: str, lang=None) -> str:
    """Raw Whisper output in, text you can paste out. Empty string means: paste nothing.
    `lang` overschrijft de ingestelde dicteertaal (voor de zelftest hieronder)."""
    commands, stutter_ok, marker = _rules(lang)
    text = _join_segments(unicodedata.normalize("NFC", text)).strip()
    if not text or _is_hallucination(text):
        return ""

    text = re.sub(FILLERS, " ", text, flags=re.IGNORECASE)

    # snap elke variant van je woordenlijst-termen terug naar de juiste vorm
    text = lexicon.canonicalise(text)

    for pattern, canonical in REPLACEMENTS.items():
        text = re.sub(pattern, canonical, text, flags=re.IGNORECASE)

    if ENABLE_STUTTER:
        text = _collapse_stutter(text, stutter_ok)

    if ENABLE_COMMANDS:
        for pattern, literal in commands.items():
            text = re.sub(pattern, literal, text, flags=re.IGNORECASE)

    if ENABLE_LISTS:
        text = _format_lists(text, marker)

    # tidy the whitespace the substitutions left behind
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ([,.!?;:])", r"\1", text)
    text = re.sub(r" *\n *", "\n", text)
    text = text.strip()

    return _sentence_case(text) if text else ""


# Elk voorbeeld dwingt een regel af. De taalcode hoort erbij sinds de regels per taal
# verschillen -- juist de negatieve gevallen ("dit commando mag hier níét vuren") zijn
# wat het profiel-systeem waard maakt.
EXAMPLES = [
    ("nl", "ik push de git hub repo naar staging"),              # canonicalise: git hub -> GitHub
    ("nl", "de graph ql endpoint praat met de type script sdk"),  # canonicalise: GraphQL, TypeScript, SDK
    ("nl", "dat draait als cronjob via launch d"),               # REPLACEMENTS: launch d -> launchd
    ("nl", "we hebben een centrale hub in het netwerk"),         # GEEN valse treffer: 'hub' != GitHub
    ("nl", " uh dus ik wil dat de repo uh pusht naar naar staging"),
    ("nl", "zet de teller op nul en push nieuwe regel dat was het"),
    ("nl", "het feit dat dat werkt is mooi"),
    ("nl", " Dit is een test van de git\nhub repo.\n"),          # in-word break
    ("nl", " Eerste zin over de deploy.\n Ik ga naar huis.\n"),  # segment boundary
    ("nl", "[BLANK_AUDIO]"),
    ("nl", "*repeat*"),                                          # radio zonder spraak, echt gemeten
    ("nl", "*echt* nu, en *meteen*"),                            # GEEN hallucinatie: nadruk is tekst
    ("nl", "Ondertiteld door de Amara.org gemeenschap"),
    ("nl", "Www.Nil.Com.Br"),
    ("nl", "ga naar example.com en check versie 3.5. daarna pushen"),
    ("nl", "er zijn drie redenen ten eerste snelheid ten tweede prijs ten derde gemak"),  # opsomming -> genummerd
    ("nl", "punt een koffie punt twee thee punt drie water"),                             # opsomming via 'punt'
    ("nl", "ten eerste moeten we dit echt afmaken vandaag"),                              # GEEN lijst: één marker blijft zin
    # --- meertalig ---
    ("en", "we ship on friday new line that is the plan"),       # commando in de ingestelde taal
    ("en", "there are three reasons firstly speed secondly price thirdly ease"),  # opsomming
    ("en", "the fact that that works is nice"),                  # GEEN stotter: Engels verdubbelt dit
    ("en", "we gaan nieuwe regel maken"),                        # GEEN Nederlands commando in een Engels dictaat
    ("en", "the first thing we do is deploy"),                   # GEEN lijst: 'first' zonder -ly is gewone tekst
    ("de", "wir starten montag neue zeile das ist der plan"),    # commando in het Duits
    ("auto", "zet de teller op nul en push nieuwe regel dat was het"),  # auto kent álle commando's
]


if __name__ == "__main__":
    for code in ("nl", "en", "auto"):
        p = whisper_prompt(code)
        print(f"whisper prompt [{code}] ({len(p)} chars):\n  {p}\n")
    for lang, raw in EXAMPLES:
        result = clean(raw, lang)
        print(f"  [{lang}] in : {raw!r}")
        print(f"       out: {result!r}\n" if result else "       out: <weggegooid>\n")
