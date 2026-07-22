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
import re
import urllib.request

import settings

_URL = "http://127.0.0.1:11434/api/chat"
_TAGS_URL = "http://127.0.0.1:11434/api/tags"
_KEEP_ALIVE = "5m"     # model warm ná gebruik, dan geeft Ollama de RAM weer vrij
_TIMEOUT = 8.0         # seconden; erna: vangrail (ruwe tekst)

# De "polijst, herschrijf niet"-prompt. Uit de prototype-tests gekomen: zonder de
# expliciete regels (behoud tijden/data, zelfcorrectie-afhandeling) verdraaide de 3B soms
# de betekenis ("naar drie uur" -> "van drie uur"), en zonder de structuur-regels + de
# few-shot die witregels/streepjes voordoet maakte 'ie nooit alinea's of opsommingen.
# Raak dit niet aan zonder opnieuw met echte dictaten te testen -- elke regel en elk
# voorbeeld hieronder ving een echte misser.
_SYSTEM = (
    "Je bent een redacteur die Nederlandse spraakdictaten opschoont tot nette geschreven "
    "tekst. Je polijst, je herschrijft NIET.\n\n"
    "Bewoording:\n"
    "1. Behoud de betekenis exact. Voeg niets toe, laat geen informatie weg.\n"
    "2. Behoud alle concrete gegevens letterlijk: tijden, data, namen, getallen, plaatsen, "
    "technische termen (bv. 'morgen', 'drie uur', 'naar staging' blijven exact staan).\n"
    "3. Bij een verspreking of zelfcorrectie ('nee, wacht', 'ik bedoel', 'de... nee') houd je "
    "ALLEEN de gecorrigeerde versie; de foute aanzet laat je weg.\n"
    "4. Verwijder aarzelingen en stopwoorden (eh, uhm, weet je, zeg maar, 'dus' als opvulling).\n"
    "5. Herstel grammatica, interpunctie en hoofdletters. Blijf in het Nederlands.\n\n"
    "Structuur:\n"
    "6. Gaat het dictaat over meerdere onderwerpen of stappen? Splits in alinea's met een "
    "WITREGEL (lege regel) ertussen.\n"
    "7. Zit er een opsomming in (drie of meer punten, taken of items)? Zet die als een lijst, "
    "elk item op een eigen regel met '- ' ervoor.\n"
    "8. Een kort, enkelvoudig bericht (een of twee zinnen) blijft lopende tekst -- forceer "
    "daar GEEN structuur.\n\n"
    "Geef UITSLUITEND de opgeschoonde tekst terug -- geen uitleg, geen aanhalingstekens."
)

# Few-shot: doet de vier gedragingen letterlijk voor -- tijd/richting behouden, een
# zelfcorrectie oplossen, een opsomming met streepjes, en een alinea-splitsing met witregel.
_FEWSHOT = [
    {"role": "user", "content": "eh kun je de meeting van vandaag verzetten naar half vier"},
    {"role": "assistant", "content": "Kun je de meeting van vandaag naar half vier verzetten?"},
    {"role": "user",
     "content": "we moeten de nee wacht eerst even de facturen controleren en dan pas versturen"},
    {"role": "assistant", "content": "We moeten eerst de facturen controleren en ze dan pas versturen."},
    {"role": "user",
     "content": "we moeten nog drie dingen doen de site live zetten de nieuwsbrief versturen en de facturen maken"},
    {"role": "assistant",
     "content": "We moeten nog drie dingen doen:\n\n- De site live zetten\n- De nieuwsbrief versturen\n- De facturen maken"},
    {"role": "user",
     "content": "de build is groen dus we kunnen mergen daarnaast wil ik het even hebben over de vakantieplanning want ik ben volgende week weg"},
    {"role": "assistant",
     "content": "De build is groen, dus we kunnen mergen.\n\nDaarnaast wil ik het even hebben over de vakantieplanning, want ik ben volgende week weg."},
]


def _norm(s: str) -> str:
    """Kleinletters, leestekens weg, witruimte samengevouwen -- zodat een vergelijking
    niet struikelt over een komma of hoofdletter."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s.lower())).strip()


def _fewshot_fragments() -> tuple:
    """Onderscheidende zinsdelen uit de few-shot. Het model hoort ze NOOIT in een output
    te zetten tenzij ze in de input stonden; een zwak model echoot soms een voorbeeld."""
    frags = []
    for m in _FEWSHOT:
        for part in re.split(r"[.\n:]", m["content"]):
            f = _norm(part)
            if len(f) >= 20:           # lang genoeg om onderscheidend te zijn
                frags.append(f)
    return tuple(frags)


_LEAK_FRAGMENTS = _fewshot_fragments()


def _leaks_fewshot(original: str, polished: str) -> bool:
    """Staat er een few-shot-voorbeeldzin in de output die niet in het dictaat stond?
    Dan echode het model een voorbeeld (de beruchte 'vakantieplanning, want ik ben
    volgende week weg' die nergens anders in de pijplijn bestaat) -> lek, niet vertrouwen.
    Genormaliseerd vergeleken, zodat een échte dictatie van diezelfde zin blijft staan
    (die zit dan óók in de input)."""
    o, p = _norm(original), _norm(polished)
    return any(frag in p and frag not in o for frag in _LEAK_FRAGMENTS)


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
    if _leaks_fewshot(text, polished):
        print("  ! oppoets lekte een voorbeeldzin; opgeschoonde tekst gebruikt")
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
