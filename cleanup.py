#!/usr/bin/env python3
"""
cleanup.py - turn a raw Whisper transcript into text you'd actually have typed.

Two layers, applied in this order:

  1. VOCAB   fed to Whisper as an initial prompt, biasing the decoder toward our
             jargon *before* it guesses. Costs nothing, no latency, fixes most of it.
  2. RULES   deterministic mop-up of what the decoder still gets wrong: phonetic
             misses, filler words, stutters, silence hallucinations, capitalisation.

Editing VOCAB and REPLACEMENTS below is the whole tuning surface.
Run `python cleanup.py` to see the rules applied to a set of examples.
"""

import re
import unicodedata

# ---------- config ----------
ENABLE_COMMANDS = True     # spoken "nieuwe regel" becomes an actual newline
ENABLE_STUTTER = True      # collapse "naar naar" -> "naar"
# ----------------------------


# Words Whisper does not know but you say all day. Order does not matter; keep it
# short-ish, an over-long prompt eats decoder context and starts to hurt.
#
# >>> THIS IS THE LIST YOU PERSONALISE. <<<
# Replace these examples with the terms YOU say that Whisper mishears: your product
# names, tools, colleagues, project codenames, technical jargon. The examples below
# are generic developer terms just to show the shape.
VOCAB = [
    "GitHub", "Kubernetes", "PostgreSQL", "nginx", "Redis", "Terraform",
    "webhook", "GraphQL", "endpoint", "middleware", "OAuth", "JWT",
    "repo", "commit", "branch", "rebase", "deploy", "staging",
    "launchd", "systemd", "cronjob", "SDK", "API", "CLI",
]


# Phonetic misses the vocab prompt cannot reach. Keys are regexes matched
# case-insensitively against word boundaries; values are the canonical spelling.
# Add a line here every time you catch a wrong transcription - that is the loop.
# The examples below are generic; replace them with your own mishearings.
REPLACEMENTS = {
    r"\bnpm\b": "npm",
    r"\bgit ?hub\b": "GitHub",
    r"\bpostgres(?:ql)?\b": "PostgreSQL",
    r"\bgraph ?ql\b": "GraphQL",
    r"\blaunch ?d\b": "launchd",
    r"\bo ?auth\b": "OAuth",
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


def whisper_prompt() -> str:
    """The initial_prompt handed to Whisper. A plain comma list conditions fine."""
    return "Woordenlijst: " + ", ".join(VOCAB) + "."


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

    for pattern, canonical in REPLACEMENTS.items():
        text = re.sub(pattern, canonical, text, flags=re.IGNORECASE)

    if ENABLE_STUTTER:
        text = _collapse_stutter(text)

    if ENABLE_COMMANDS:
        for pattern, literal in COMMANDS.items():
            text = re.sub(pattern, literal, text, flags=re.IGNORECASE)

    # tidy the whitespace the substitutions left behind
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ([,.!?;:])", r"\1", text)
    text = re.sub(r" *\n *", "\n", text)
    text = text.strip()

    return _sentence_case(text) if text else ""


EXAMPLES = [
    "Dit is een test van de git hub repo die naar postgres dispatcht.",
    " uh dus ik wil dat de git hub repo uh pusht naar naar staging",
    "zet de teller op nul en push naar de branch nieuwe regel dat was het",
    "het feit dat dat werkt is mooi",
    " Dit is een test van de git\nhub repo.\n",       # in-word break
    " Eerste zin over de deploy.\n Ik ga naar huis.\n",  # segment boundary
    "[BLANK_AUDIO]",
    "Ondertiteld door de Amara.org gemeenschap",
    "Www.Nil.Com.Br",
    "ga naar example.com en check versie 3.5. daarna pushen",
]


if __name__ == "__main__":
    print(f"whisper prompt ({len(whisper_prompt())} chars):\n  {whisper_prompt()}\n")
    for raw in EXAMPLES:
        result = clean(raw)
        print(f"  in : {raw!r}")
        print(f"  out: {result!r}\n" if result else "  out: <weggegooid>\n")
