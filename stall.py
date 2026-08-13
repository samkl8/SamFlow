"""stall.py - merkt wanneer de main thread stilstaat, en schrijft op wáár hij vastzit.

De main thread is in samflow alles tegelijk: de run loop waar de Fn-event-tap aan hangt,
de pill, en het hoofdvenster. Blokkeert daar één call, dan is de hele app dood -- geen
Fn, geen pill, geen venster. Dat gebeurt niet vanzelf: het zijn de C-calls op dat pad die
geen bovengrens hebben. CoreAudio kan op de HAL-mutex wachten tot een apparaatwissel klaar
is, een Apple Event wacht op een app die misschien nooit antwoordt.

Het probleem bij het diagnosticeren was dat zo'n vastloper niets achterliet: je kunt geen
foutmelding printen vanaf een thread die zelf stilstaat, en wie de app daarna afknalt
gooit de logbuffer weg. Vandaar deze module. Werking:

  - Een NSTimer op de main run loop tikt elke TICK_SEC een teller op.
  - Een achtergrondthread (die dus wél blijft draaien) kijkt of die teller nog beweegt.
  - Staat 'ie langer dan STALL_SEC stil, dan schrijven we de Python-stack van de main
    thread naar de log, én een native stack via `sample` (zie hieronder).

**Waarom er óók een native stack bij hoort.** De Python-stack stopt bij de regel die de
C-call deed -- prima als díé regel van ons is. Maar hangt de main thread in code waar geen
Python aan te pas komt, dan is de bovenste frame gewoon `app.run()` en zegt de dump niets.
Precies dat is hier gebeurd: zeven dumps, allemaal `hud.py -> self.app.run()`, geen enkel
eigen frame. `/usr/bin/sample` ziet de native frames wél en maakt in één oogopslag het
verschil tussen de drie mogelijkheden: een écht geblokkeerde call (mutex/Apple Event), een
modale run loop (`runModalForWindow` -- dan tikt onze timer niet en is het een vals alarm,
want NSModalPanelRunLoopMode zit níét in de common modes), of een main thread die gewoon
op de GIL staat te wachten omdat een andere thread 'm vasthoudt.

Kost niets als er niets aan de hand is: één timer-tik per halve seconde en een thread die
slaapt. En de vangrail werkt alleen als de log ook echt op schijf komt -- daarom zet
samflow.py z'n uitvoer op regel-buffering (zie de opmerking daar).
"""
import os
import re
import subprocess
import sys
import threading
import time
import traceback

from Foundation import NSObject, NSRunLoop, NSRunLoopCommonModes, NSTimer

TICK_SEC = 0.5      # hoe vaak de main thread "ik leef" zegt
STALL_SEC = 2.0     # zo lang stil = vastgelopen (een gezonde tik is 0,5s; 2s is nooit normaal)
POLL_SEC = 0.5      # hoe vaak de waker kijkt
SAMPLE_SEC = 1      # hoe lang `sample` meekijkt; een hangende stack is na 1s al duidelijk
SAMPLE_GAP = 60.0   # hooguit één sample per minuut (elk bestand is ~200 kB)
SAMPLE_TAIL = 14    # zoveel diepste frames van de main thread gaan mee de log in

_beat = time.monotonic()
_main_id = None
_last_sample = -SAMPLE_GAP
_THREAD_LINE = re.compile(r"^\s*\d+ Thread_")


class _StallTicker(NSObject):
    # Naam bewust uniek: Objective-C-klassenamen zijn procesbreed, en hud.py heeft al
    # een _Ticker. Twee klassen met dezelfde naam laat PyObjC bij import knallen.
    def tick_(self, _timer):
        global _beat
        _beat = time.monotonic()


_ticker = None      # referentie vasthouden, anders ruimt PyObjC 'm op


def _main_stack() -> str:
    """De Python-stack van de main thread, nú. Zit die vast in een C-call (CoreAudio,
    Apple Event), dan is de bovenste frame de Python-regel die 'm aanriep -- dat is de
    naam van de schuldige."""
    frame = sys._current_frames().get(_main_id)
    if frame is None:
        return "    (geen stack beschikbaar)"
    return "".join(traceback.format_stack(frame))


def _main_native(path: str) -> str:
    """De diepste frames van de main thread uit een `sample`-bestand. Een vastgelopen
    thread levert bij elke sample dezelfde stack op, dus de boom is één rechte tak en de
    laatste regels zijn precies de call die hangt."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return ""
    block, seen = [], False
    for ln in lines:
        if not seen:
            seen = "main-thread" in ln
            continue
        if not ln.strip() or _THREAD_LINE.match(ln):
            break
        block.append(ln)
    return "\n".join(block[-SAMPLE_TAIL:])


def _native_sample() -> str:
    """Native stack van het hele proces naar een eigen bestand, en de staart ervan
    terug de log in. Draait op de waker-thread (die leeft nog) en `sample` is een los
    proces, dus dit werkt juist wél terwijl de app dood in het water ligt."""
    global _last_sample
    now = time.monotonic()
    if now - _last_sample < SAMPLE_GAP:
        return "    (geen sample: minder dan een minuut na de vorige)"
    _last_sample = now
    path = os.path.expanduser(
        time.strftime("~/Library/Logs/samflow-stall-%Y%m%d-%H%M%S.txt"))
    try:
        subprocess.run(["/usr/bin/sample", str(os.getpid()), str(SAMPLE_SEC),
                        "-file", path],
                       capture_output=True, timeout=SAMPLE_SEC + 30)
    except Exception as exc:                     # sample weg of geweigerd: geen drama
        return f"    (sample mislukt: {exc})"
    tail = _main_native(path)
    return f"    native stack ({path}):\n{tail}" if tail else f"    native stack: {path}"


def _watch():
    stalled_since = None
    while True:
        time.sleep(POLL_SEC)
        late = time.monotonic() - _beat
        if late > STALL_SEC and stalled_since is None:
            stalled_since = time.monotonic() - late
            print(f"! main thread staat {late:.1f}s stil - de app reageert nu nergens op "
                  f"(geen Fn, geen pill). Stack van de main thread:\n{_main_stack()}"
                  f"{_native_sample()}",
                  file=sys.stderr, flush=True)
        elif late <= STALL_SEC and stalled_since is not None:
            print(f"! main thread weer vrij na {time.monotonic() - stalled_since:.1f}s",
                  file=sys.stderr, flush=True)
            stalled_since = None


def start():
    """Installeer de hartslag. Aanroepen vanaf de main thread, vóór de run loop begint."""
    global _ticker, _main_id
    if _ticker is not None:
        return
    _main_id = threading.main_thread().ident
    _ticker = _StallTicker.alloc().init()
    # In COMMON modes, niet de default-mode. Een default-mode-timer staat stil zodra de
    # run loop in event-tracking zit (menu open, venster slepen) -- volkomen normaal
    # gedrag dat deze waker anders als "vastgelopen" zou melden. Vals alarm is hier
    # duurder dan geen alarm: één onterechte stack-dump en je gelooft de volgende niet meer.
    timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
        TICK_SEC, _ticker, "tick:", None, True)
    NSRunLoop.currentRunLoop().addTimer_forMode_(timer, NSRunLoopCommonModes)
    threading.Thread(target=_watch, daemon=True).start()
