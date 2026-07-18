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
    r"^(?:www\.|https?://)",         # a bare URL and nothing else
    r"^[\w\-]+(?:\.[\w\-]+){2,}$",   # a.b.c domain and nothing else
]


FILLERS = r"\b(?:u+h+m?|e+h+m?|a+h+m|hmm+|ehm)\b"

COMMANDS = {
    r"\bnieuwe? regel\b": "\n",
    r"\bnieuwe? alinea\b": "\n\n",
    r"\bnew ?line\b": "\n",
}

# Dutch doubles these legitimately ("het feit dat dat werkt"), so leave them alone.
STUTTER_ALLOW = {"dat", "die", "heel", "had"}

# Gesproken opsommingen: expliciete ordinaal-markers. Alleen bij >=2 markers zetten we om
# naar een genummerde lijst, zodat een losse "ten eerste" in een zin blijft staan. "ten"
# matcht alleen mét een ordinaal erachter, dus "ten opzichte"/"ten einde" blijven ongemoeid.
LIST_MARKER = re.compile(
    r"\b(?:ten\s+(?:eerste|tweede|derde|vierde|vijfde|zesde|zevende|achtste|negende|tiende)"
    r"|punt\s+(?:een|één|twee|drie|vier|vijf|zes|zeven|acht|negen|tien|\d+))\b",
    re.IGNORECASE)


def whisper_prompt() -> str:
    """The initial_prompt handed to Whisper. A plain comma list conditions fine.
    Terms come from lexicon.py: built-in defaults plus your personal lexicon.txt."""
    return "Woordenlijst: " + ", ".join(lexicon.terms()) + "."


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


def _collapse_stutter(text: str) -> str:
    def repl(m):
        word = m.group(1)
        return m.group(0) if word.lower() in STUTTER_ALLOW else word
    return re.sub(r"\b(\w+)(?:\s+\1\b)+", repl, text, flags=re.IGNORECASE)


def _format_lists(text: str) -> str:
    """Gesproken opsommingen met >=2 ordinaal-markers ('ten eerste ... ten tweede ...',
    'punt een ... punt twee ...') worden een genummerde lijst. De nummering telt zelf
    door, dus een misgehoorde ordinaal maakt niet uit. Onder de twee markers raken we
    niets aan -- een losse 'ten eerste' hoort gewoon in de zin. De tekst vóór de eerste
    marker blijft als aanloop-regel boven de lijst staan."""
    marks = list(LIST_MARKER.finditer(text))
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


def clean(text: str) -> str:
    """Raw Whisper output in, text you can paste out. Empty string means: paste nothing."""
    text = _join_segments(unicodedata.normalize("NFC", text)).strip()
    if not text or _is_hallucination(text):
        return ""

    text = re.sub(FILLERS, " ", text, flags=re.IGNORECASE)

    # snap elke variant van je woordenlijst-termen terug naar de juiste vorm
    text = lexicon.canonicalise(text)

    for pattern, canonical in REPLACEMENTS.items():
        text = re.sub(pattern, canonical, text, flags=re.IGNORECASE)

    if ENABLE_STUTTER:
        text = _collapse_stutter(text)

    if ENABLE_COMMANDS:
        for pattern, literal in COMMANDS.items():
            text = re.sub(pattern, literal, text, flags=re.IGNORECASE)

    if ENABLE_LISTS:
        text = _format_lists(text)

    # tidy the whitespace the substitutions left behind
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ([,.!?;:])", r"\1", text)
    text = re.sub(r" *\n *", "\n", text)
    text = text.strip()

    return _sentence_case(text) if text else ""


EXAMPLES = [
    "ik push de git hub repo naar staging",                 # canonicalise: git hub -> GitHub
    "de graph ql endpoint praat met de type script sdk",    # canonicalise: GraphQL, TypeScript, SDK
    "dat draait als cronjob via launch d",                  # REPLACEMENTS: launch d -> launchd
    "we hebben een centrale hub in het netwerk",            # GEEN valse treffer: 'hub' != GitHub
    " uh dus ik wil dat de repo uh pusht naar naar staging",
    "zet de teller op nul en push nieuwe regel dat was het",
    "het feit dat dat werkt is mooi",
    " Dit is een test van de git\nhub repo.\n",       # in-word break
    " Eerste zin over de deploy.\n Ik ga naar huis.\n",  # segment boundary
    "[BLANK_AUDIO]",
    "Ondertiteld door de Amara.org gemeenschap",
    "Www.Nil.Com.Br",
    "ga naar example.com en check versie 3.5. daarna pushen",
    "er zijn drie redenen ten eerste snelheid ten tweede prijs ten derde gemak",  # opsomming -> genummerd
    "punt een koffie punt twee thee punt drie water",                             # opsomming via 'punt'
    "ten eerste moeten we dit echt afmaken vandaag",                              # GEEN lijst: één marker blijft zin
]


if __name__ == "__main__":
    print(f"whisper prompt ({len(whisper_prompt())} chars):\n  {whisper_prompt()}\n")
    for raw in EXAMPLES:
        result = clean(raw)
        print(f"  in : {raw!r}")
        print(f"  out: {result!r}\n" if result else "  out: <weggegooid>\n")
