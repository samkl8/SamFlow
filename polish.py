"""
polish.py - optionele Route B: een lokaal instruct-model (via Ollama) poetst de
al opgeschoonde tekst nog een slag op -- losse spraak wordt nette geschreven zinnen,
versprekingen en zelfcorrecties eruit, grammatica recht. Draait ná cleanup.clean
(Route A) en op de handle-thread, dus blokkeert de run loop niet.

Standaard UIT (`settings['polish_enabled']`): een bewuste opt-in. Aan kost het ~0,6s
extra en houdt het model warm in RAM (Ollama `keep_alive`); uit kost het niets -- geen
call, geen model, geen RAM. Zo zet je 't uit als je Mac al vol zit.

Vangrail (de belofte): bij ELKE twijfel valt polish terug op de binnenkomende tekst.
Ollama niet bereikbaar, model niet gepulld, timeout, leeg antwoord, of een antwoord dat
qua lengte te ver van het origineel afwijkt -> gewoon de Route-A-tekst. Het model mag je
dictaat nooit ophangen of kapotmaken; erger dan de opgeschoonde tekst wordt het nooit.
(De lengte-vangrail vangt uitdijen/inklappen; een subtiele betekeniswijziging kan 'ie
níét vangen -- vandaar dat dit opt-in is en niet de default.)
"""
import json
import urllib.request

import settings

_URL = "http://127.0.0.1:11434/api/chat"
_TAGS_URL = "http://127.0.0.1:11434/api/tags"
_KEEP_ALIVE = "5m"     # model warm ná gebruik, dan geeft Ollama de RAM weer vrij
_TIMEOUT = 8.0         # seconden; erna: vangrail (ruwe tekst)

# De strenge "polijst, herschrijf niet"-prompt. Uit de prototype-test gekomen: zonder
# de expliciete regels (behoud tijden/data, zelfcorrectie-afhandeling) verdraaide de 3B
# soms de betekenis ("naar drie uur" -> "van drie uur"). Raak dit niet aan zonder opnieuw
# met echte dictaten te testen -- elke regel hieronder ving een echte misser.
_SYSTEM = (
    "Je bent een redacteur die Nederlandse spraakdictaten opschoont tot nette geschreven "
    "tekst. Je polijst, je herschrijft NIET.\n\n"
    "Regels:\n"
    "1. Behoud de betekenis exact. Voeg niets toe, laat geen informatie weg.\n"
    "2. Behoud alle concrete gegevens letterlijk: tijden, data, namen, getallen, plaatsen "
    "(bv. 'morgen', 'drie uur', 'naar staging' blijven exact staan; 'naar X' betekent naar X).\n"
    "3. Bij een verspreking of zelfcorrectie ('nee, wacht', 'ik bedoel', 'de... nee') houd je "
    "ALLEEN de gecorrigeerde versie; de foute aanzet laat je weg.\n"
    "4. Verwijder aarzelingen en stopwoorden (eh, uhm, weet je, zeg maar, 'dus' als opvulling).\n"
    "5. Herstel grammatica, interpunctie en hoofdletters. Splits in nette zinnen; maak alleen "
    "een opsomming bij een duidelijke opsomming.\n"
    "6. Blijf in het Nederlands; vertaal niets.\n"
    "7. Geef UITSLUITEND de opgeschoonde tekst terug -- geen uitleg, geen aanhalingstekens."
)

_FEWSHOT = [
    {"role": "user", "content": "eh kun je de meeting van vandaag verzetten naar half vier"},
    {"role": "assistant", "content": "Kun je de meeting van vandaag naar half vier verzetten?"},
    {"role": "user",
     "content": "we moeten de nee wacht eerst even de facturen controleren en dan pas versturen"},
    {"role": "assistant", "content": "We moeten eerst de facturen controleren en ze dan pas versturen."},
]


def _sane(original: str, polished: str) -> bool:
    """Conservatieve vangrail: accepteer de polish alleen als 'ie plausibel een
    opgeschoonde versie is -- geen leeg, ge-explodeerd of ingeklapt antwoord. Polijsten
    kort licht in (stopwoorden eruit); sterk uitdijen wijst op uitleg/hallucinatie."""
    if not polished:
        return False
    o, p = len(original), len(polished)
    if p > o * 1.6 + 40:
        return False
    if p < o * 0.4:
        return False
    return True


def polish(text: str) -> str:
    """Poets `text` op met het lokale model. Uit (default) of bij welke fout dan ook:
    geef `text` onveranderd terug. Nooit een exceptie naar de aanroeper."""
    if not settings.get("polish_enabled"):
        return text
    if not text or not text.strip():
        return text
    model = settings.get("polish_model")
    body = {
        "model": model,
        "messages": [{"role": "system", "content": _SYSTEM}] + _FEWSHOT +
                    [{"role": "user", "content": text}],
        "stream": False,
        "keep_alive": _KEEP_ALIVE,
        "options": {"temperature": 0.0, "num_predict": 512},
    }
    try:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            _URL, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            out = json.loads(r.read())
        polished = (out.get("message") or {}).get("content", "").strip()
    except Exception as e:
        print(f"  ! oppoetsen overgeslagen ({e}); opgeschoonde tekst gebruikt")
        return text
    if not _sane(text, polished):
        print("  ! oppoets-resultaat te afwijkend; opgeschoonde tekst gebruikt")
        return text
    return polished


def available(model: str = None) -> bool:
    """Draait Ollama én is het gekozen model gepulld? Voor een UI-statuslabel.
    Kort getimed; bij twijfel False (dan valt polish sowieso terug op de ruwe tekst)."""
    model = model or settings.get("polish_model")
    stem = model.split(":")[0]
    try:
        with urllib.request.urlopen(_TAGS_URL, timeout=1.5) as r:
            tags = json.loads(r.read())
    except Exception:
        return False
    names = [m.get("name", "") for m in tags.get("models", [])]
    return any(n == model or n.split(":")[0] == stem for n in names)
