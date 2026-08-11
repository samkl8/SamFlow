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
    thread naar de log. Dat is precies de regel waarop hij hangt.

Kost niets als er niets aan de hand is: één timer-tik per halve seconde en een thread die
slaapt. En de vangrail werkt alleen als de log ook echt op schijf komt -- daarom zet
samflow.py z'n uitvoer op regel-buffering (zie de opmerking daar).
"""
import sys
import threading
import time
import traceback

from Foundation import NSObject, NSRunLoop, NSRunLoopCommonModes, NSTimer

TICK_SEC = 0.5      # hoe vaak de main thread "ik leef" zegt
STALL_SEC = 2.0     # zo lang stil = vastgelopen (een gezonde tik is 0,5s; 2s is nooit normaal)
POLL_SEC = 0.5      # hoe vaak de waker kijkt

_beat = time.monotonic()
_main_id = None


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


def _watch():
    stalled_since = None
    while True:
        time.sleep(POLL_SEC)
        late = time.monotonic() - _beat
        if late > STALL_SEC and stalled_since is None:
            stalled_since = time.monotonic() - late
            print(f"! main thread staat {late:.1f}s stil - de app reageert nu nergens op "
                  f"(geen Fn, geen pill). Stack van de main thread:\n{_main_stack()}",
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
