"""
polish.py - optionele Route B: een lokaal instruct-model (via Ollama) poetst de
al opgeschoonde tekst nog een slag op -- losse spraak wordt nette geschreven zinnen,
versprekingen en zelfcorrecties eruit, grammatica recht. Draait ná cleanup.clean
(Route A) en op de handle-thread, dus blokkeert de run loop niet.

Standaard UIT (`settings['polish_enabled']`): een bewuste opt-in. Aan kost het ~0,6s
extra en houdt het model warm in RAM (Ollama `keep_alive`); uit kost het niets -- geen
call, geen model, geen RAM. Zo zet je 't uit als je Mac al vol zit.

Vangrail (de belofte): bij ELKE twijfel valt polish terug op de binnenkomende tekst.
Ollama niet bereikbaar, model niet gepulld, timeout, leeg antwoord, een antwoord dat door
de tokengrens is afgekapt, of een antwoord dat qua lengte te ver van het origineel afwijkt
-> gewoon de Route-A-tekst. Het model mag je dictaat nooit ophangen of kapotmaken; erger
dan de opgeschoonde tekst wordt het nooit. (De lengte-vangrail vangt uitdijen/inklappen;
een subtiele betekeniswijziging kan 'ie níét vangen -- vandaar dat dit opt-in is en niet
de default.)

De ~0,6s geldt voor een kort dictaat. Ruimte (num_predict) en timeout schalen mee met de
lengte van de tekst (zie `_budget`), want die stonden vast en lieten juist lange dictaten
stranden. Een dictaat van vijf minuten kost daardoor tientallen seconden oppoetsen vóór
het plakken -- de prijs van polish áán bij lange dictaten.
"""
import json
import re
import urllib.request

import settings

_URL = "http://127.0.0.1:11434/api/chat"
_TAGS_URL = "http://127.0.0.1:11434/api/tags"
_KEEP_ALIVE = "5m"     # model warm ná gebruik, dan geeft Ollama de RAM weer vrij
_TIMEOUT = 8.0         # bodem in seconden; erna: vangrail (ruwe tekst)
_TIMEOUT_MAX = 60.0    # plafond: liever een onopgepoetst dictaat dan een minutenlange plak-wachttijd
_CHARS_PER_SEC = 60.0  # ~een 3B-model op Metal; bepaalt hoeveel timeout een lang dictaat krijgt
_CHARS_PER_TOKEN = 3.0  # ruwe schatting voor Nederlands; alleen om num_predict te dimensioneren
_MIN_PREDICT = 512     # genoeg voor een kort dictaat; de oude vaste waarde

# De "polijst, herschrijf niet"-prompt. Uit de prototype-tests gekomen: zonder de
# expliciete regels (behoud tijden/data, zelfcorrectie-afhandeling) verdraaide de 3B soms
# de betekenis ("naar drie uur" -> "van drie uur"), en zonder de structuur-regels + de
# few-shot die witregels/streepjes voordoet maakte 'ie nooit alinea's of opsommingen.
# Raak dit niet aan zonder opnieuw met echte dictaten te testen -- elke regel en elk
# voorbeeld hieronder ving een echte misser.
#
# Regel 5 stond ooit op "Blijf in het Nederlands". Dat was fout zodra de taal instelbaar
# werd: gemeten met qwen2.5:3b kwam een Engels dictaat er als **Nederlandse vertaling**
# uit (woordbehoud 0,07) en `_sane` liet dat door, want de lengte klopte gewoon. De
# few-shot hieronder is Nederlands en trekt hard, dus de regel benoemt die expliciet.
# Zo gemeten: hetzelfde Engelse dictaat blijft nu Engels (woordbehoud 1,00), en drie
# Nederlandse dictaten leverden onveranderd 1,00 op -- de few-shot mocht dus blijven.
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
    "5. Herstel grammatica, interpunctie en hoofdletters. Schrijf je antwoord in EXACT "
    "dezelfde taal als de invoer -- vertaal nooit, ook niet als de voorbeelden hieronder "
    "een andere taal hebben.\n\n"
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


# De taal noemen werkt meetbaar beter dan "dezelfde taal als de invoer". Gemeten met
# qwen2.5:3b op een Duits dictaat: woordbehoud 0,18 (het model antwoordde in het
# Nederlands, getrokken door de Nederlandse few-shot) tegen 0,91 zodra de prompt "in het
# Duits" zegt. Bij "auto" kennen we de taal niet, en dan is de dezelfde-taal-regel het
# beste dat er is -- die houdt Engels wél vast (gemeten 1,00).
_SAME_LANG = "in EXACT dezelfde taal als de invoer"
_LANG_NAMES = {
    "nl": "het Nederlands", "en": "het Engels", "de": "het Duits", "fr": "het Frans",
    "es": "het Spaans", "it": "het Italiaans", "pt": "het Portugees",
}


def _system(lang: str) -> str:
    name = _LANG_NAMES.get(lang)
    return _SYSTEM.replace(_SAME_LANG, f"in {name}") if name else _SYSTEM


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


_CONTENT_WORD = re.compile(r"[^\W\d_]{4,}")
_MIN_KEEP = 0.5    # zoveel van de inhoudswoorden hoort een polish te laten staan

# Schrift-drift. Echt gebeurd met qwen2.5:3b, twee dictaten achter elkaar:
#   "Oké, kun je me甚至 帮助 我 補正這件事？"
# Dat is geen transcriptiefout maar een woord-voor-woord vertáling van het dictaat die
# middenin de zin begint (甚至 = "even", 帮助 = "helpen", 補正這件事 = "dit fixen"). De
# server leverde keurig Nederlands: vier varianten (met/zonder woordenlijst-prompt,
# met/zonder taal) gaven allemaal de juiste zin -- het model erna kiepte om.
# Waarom dit een eigen check heeft en niet aan `_kept_ratio` genoeg heeft: bij een lang
# dictaat waarvan alleen de staart omschakelt blijft het woordbehoud ruim boven 0,5.
# Grieks staat er bewust niet bij: een model dat "pi" als "π" schrijft is geen drift.
_SCRIPTS = (
    ("Chinees", 0x4E00, 0x9FFF), ("Chinees", 0x3400, 0x4DBF),
    ("Japans", 0x3040, 0x30FF), ("Koreaans", 0xAC00, 0xD7AF),
    ("Cyrillisch", 0x0400, 0x04FF), ("Arabisch", 0x0600, 0x06FF),
    ("Hebreeuws", 0x0590, 0x05FF), ("Devanagari", 0x0900, 0x097F),
    ("Thai", 0x0E00, 0x0E7F),
)


def _script_drift(original: str, polished: str) -> str:
    """De naam van een schrift dat in de opgepoetste tekst opduikt terwijl het dictaat
    het niet had. Leeg = niets aan de hand. Dicteer je zélf Chinees, dan staat het in
    beide en keurt deze check niets af."""
    for naam, lo, hi in _SCRIPTS:
        if (any(lo <= ord(c) <= hi for c in polished)
                and not any(lo <= ord(c) <= hi for c in original)):
            return naam
    return ""


def _kept_ratio(original: str, polished: str) -> float:
    """Welk deel van de inhoudswoorden uit het dictaat haalt de opgepoetste tekst?
    Vergeleken op de eerste vijf letters, zodat een verbogen vorm ('versturen' ->
    'verstuurd') gewoon meetelt. Een polish laat de woorden per definitie staan (regels
    1 en 2 van de prompt); een vertaling deelt er bijna geen. Gemeten: 1,00 bij drie
    Nederlandse dictaten, 0,07 toen het model een Engels dictaat vertaalde."""
    def stems(s):
        return {w[:5] for w in _CONTENT_WORD.findall(s.lower())}
    want = stems(original)
    if not want:
        return 1.0            # niets om te tellen (heel kort dictaat): geen oordeel
    return len(want & stems(polished)) / len(want)


def _sane(original: str, polished: str) -> bool:
    """Conservatieve vangrail: accepteer de polish alleen als 'ie plausibel een
    opgeschoonde versie is -- geen leeg, ge-explodeerd of ingeklapt antwoord. Polijsten
    kort licht in (stopwoorden eruit); sterk uitdijen wijst op uitleg/hallucinatie.
    Let op wat deze check *niet* ziet: een vertaling heeft een prima lengte. Daar is
    `_kept_ratio` voor."""
    if not polished:
        return False
    o, p = len(original), len(polished)
    if p > o * 1.6 + 40:
        return False
    if p < o * 0.4:
        return False
    return True


def _budget(text: str) -> tuple:
    """(num_predict, timeout) voor dit dictaat. Beide schaalden vroeger niet mee: 512
    tokens en 8 seconden waren ruim bij een cap van 2 minuten, maar een lang dictaat liep
    er stil op stuk -- het antwoord werd halverwege afgekapt, `_sane` keurde die
    inklapping (terecht) af, en je kreeg de onopgepoetste tekst zonder te weten waarom.
    De ruimte volgt nu de lengte van de input: `_sane` accepteert tot ~1,6x de invoer,
    dus daar dimensioneren we num_predict op. De timeout heeft wél een plafond -- een
    dictaat dat pas na een minuut geplakt wordt is erger dan een dictaat zonder polish."""
    predict = max(_MIN_PREDICT, int(len(text) * 1.6 / _CHARS_PER_TOKEN) + 128)
    timeout = min(_TIMEOUT_MAX, max(_TIMEOUT, len(text) / _CHARS_PER_SEC))
    return predict, timeout


def polish(text: str) -> str:
    """Poets `text` op met het lokale model. Uit (default) of bij welke fout dan ook:
    geef `text` onveranderd terug. Nooit een exceptie naar de aanroeper."""
    if not settings.get("polish_enabled"):
        return text
    if not text or not text.strip():
        return text
    model = settings.get("polish_model")
    predict, timeout = _budget(text)
    body = {
        "model": model,
        "messages": [{"role": "system",
                      "content": _system(settings.get("language") or "auto")}] + _FEWSHOT +
                    [{"role": "user", "content": text}],
        "stream": False,
        "keep_alive": _KEEP_ALIVE,
        "options": {"temperature": 0.0, "num_predict": predict},
    }
    try:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            _URL, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read())
        polished = (out.get("message") or {}).get("content", "").strip()
    except Exception as e:
        print(f"  ! oppoetsen overgeslagen ({e}); opgeschoonde tekst gebruikt")
        return text
    # Het model raakte num_predict op i.p.v. z'n eigen stop-token: het antwoord houdt
    # midden in een zin op. Dit is de gevaarlijkste uitkomst van allemaal, want een
    # afgekapt antwoord is niet raar genoeg voor _sane -- die kijkt alleen naar lengte,
    # en 89% van het origineel glipt er moeiteloos doorheen. Zo verdween vroeger stilletjes
    # de laatste alinea van een lang dictaat (gemeten, niet bedacht). Vangrail: weggooien.
    if out.get("done_reason") == "length":
        print("  ! oppoets liep tegen de tokengrens (antwoord afgekapt); "
              "opgeschoonde tekst gebruikt")
        return text
    if not _sane(text, polished):
        print("  ! oppoets-resultaat te afwijkend; opgeschoonde tekst gebruikt")
        return text
    if _leaks_fewshot(text, polished):
        print("  ! oppoets lekte een voorbeeldzin; opgeschoonde tekst gebruikt")
        return text
    drift = _script_drift(text, polished)
    if drift:
        print(f"  ! oppoets schakelde midden in de tekst over op {drift}; "
              f"opgeschoonde tekst gebruikt")
        return text
    keep = _kept_ratio(text, polished)
    if keep < _MIN_KEEP:
        print(f"  ! oppoets gaf andere woorden terug dan het dictaat (behoud {keep:.2f}, "
              f"vertaling of herschrijving); opgeschoonde tekst gebruikt")
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
