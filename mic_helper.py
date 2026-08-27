"""
mic_helper.py - het kindproces dat als énige PortAudio/CoreAudio aanraakt.

Waarom dit bestaat
------------------
Elk afbreek-pad van een PortAudio-stream (`stop`, `abort`, `close`) takt af naar
`FinishStoppingStream` -> `AudioOutputUnitStop` -- nagekeken in de binary, er is geen
uitzondering. En PortAudio registreert bij het openen ongeconditioneerd een listener op
`kAudioOutputUnitProperty_IsRunning` (`startStopCallback`) die vanuit CoreAudio's eigen
IO-thread `AudioUnitGetProperty` aanroept. Die twee kunnen elkaars slot pakken:

    onze stop()          houdt de component-mutex, wil de HAL-mutex
    CoreAudio IO-thread  houdt de HAL-mutex,       wil de component-mutex

Klassieke lock-order-inversie: geen van beide laat ooit nog los. Gemeten in een levend
proces, met vier threads op die twee sloten. In-proces valt daar niets tegen te doen --
je kunt een thread die in een C-call hangt niet afbreken.

Wél te doen: het in een proces zetten dat je mág afschieten. Loopt dit proces vast, dan
schiet samflow.py het af en start een verse. Bewezen: het vastgelopen proces van 27-08
ging dood op een gewone SIGTERM, ondanks die vier geblokkeerde threads. Kosten van een
verse helper: ~320 ms (gemeten), en de mic openen daarna ~104 ms -- gelijk aan vroeger.

Bijvangst: een vers proces enumereert de apparaten lív. De PortAudio-hotplugval (zie
audiodev.refresh) is daarmee geen crashrisico meer maar een gewone herstart.

Protocol
--------
Commando's komen als regels op stdin: `open`, `close`, `ping`, `quit`. EOF = de ouder is
weg, dus wij ook (geen wezen).

Naar de ouder gaat een binaire stroom frames op stdout:

    b'SF' | type (1 byte) | lengte (uint32 LE) | payload

    type 'A'  audio, rauwe int16-LE samples
    type 'J'  status, UTF-8 JSON

Log gaat naar stderr; de ouder zet dat in de log met een prefix.
"""

import json
import queue
import struct
import sys
import threading

import sounddevice as sd

import audiodev

SAMPLE_RATE = 16000
BLOCK = 1024

MAGIC = b"SF"
HEADER = struct.Struct("<2scI")     # magic, type, lengte

# De audio-callback is een realtime-thread: die mag nóóit blokkeren op een pipe. Dus
# schrijft 'ie in deze wachtrij en doet een aparte thread de stdout-schrijf. Begrensd,
# want als de ouder stopt met lezen mag ons geheugen niet vollopen; loopt 'ie vol, dan
# gooien we het oudste blok weg (de ouder is dan toch al niet meer bij de les).
QUEUE_BLOCKS = 400                  # ~25 seconden audio


def _log(msg: str):
    print(msg, file=sys.stderr, flush=True)


class Helper:
    def __init__(self):
        self.stream = None
        self.device = None      # naam van de gekozen mic (voor de status)
        self.reason = ""
        self.out = sys.stdout.buffer
        self.q = queue.Queue(maxsize=QUEUE_BLOCKS)
        self.dropped = 0
        self._wlock = threading.Lock()
        threading.Thread(target=self._writer, daemon=True).start()

    # -- uitgaand ------------------------------------------------------------

    def _send(self, kind: bytes, payload: bytes):
        """Eén frame naar de ouder. Onder een lock, want de writer-thread en de
        commando-lus schrijven allebei naar dezelfde pipe."""
        with self._wlock:
            try:
                self.out.write(HEADER.pack(MAGIC, kind, len(payload)))
                self.out.write(payload)
                self.out.flush()
            except (BrokenPipeError, ValueError):
                # Ouder is weg. Niets meer te doen; de stdin-lus ziet zo de EOF.
                pass

    def status(self, **fields):
        self._send(b"J", json.dumps(fields).encode())

    def _writer(self):
        while True:
            block = self.q.get()
            if block is None:
                return
            self._send(b"A", block)

    # -- de mic --------------------------------------------------------------

    def _callback(self, indata, frames, time_info, status):
        try:
            self.q.put_nowait(bytes(indata))
        except queue.Full:
            try:
                self.q.get_nowait()          # oudste eruit, nieuwste erin
                self.q.put_nowait(bytes(indata))
            except (queue.Empty, queue.Full):
                pass
            self.dropped += 1

    def open(self):
        if self.stream is not None:
            self.status(ev="opened", device=self.device, reason=self.reason, reused=True)
            return
        # Her-initialiseer PortAudio zodat de apparaatlijst de huidige hardware
        # weerspiegelt (AirPods erin/eruit). Vroeger was dit levensgevaarlijk naast een
        # hangende call; hier niet meer -- hangt 'ie, dan schiet de ouder ons af.
        try:
            audiodev.refresh()
        except Exception as e:
            _log(f"refresh mislukt (verder met de bestaande lijst): {e}")
        try:
            device, name, reason = audiodev.choose_input()
            stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                                    blocksize=BLOCK, device=device,
                                    callback=self._callback)
            stream.start()
        except Exception as e:
            self.status(ev="open_failed", err=str(e))
            return
        self.stream, self.device, self.reason = stream, name, reason
        self.status(ev="opened", device=name, reason=reason, reused=False)

    def close(self):
        stream, self.stream = self.stream, None
        if stream is None:
            self.status(ev="closed", had_stream=False)
            return
        # Precies de call die kan vastlopen. Loopt 'ie vast, dan komt de 'closed'-status
        # nooit en schiet de ouder ons af -- dat is het hele ontwerp.
        try:
            stream.stop()
            stream.close()
        except Exception as e:
            self.status(ev="close_failed", err=str(e))
            return
        self.status(ev="closed", had_stream=True, dropped=self.dropped)

    # -- de lus --------------------------------------------------------------

    def run(self):
        self.status(ev="ready")
        for line in sys.stdin:                 # EOF = ouder weg = wij stoppen
            cmd = line.strip()
            if cmd == "open":
                self.open()
            elif cmd == "close":
                self.close()
            elif cmd == "ping":
                self.status(ev="pong", open=self.stream is not None)
            elif cmd == "quit":
                break
            elif cmd:
                _log(f"onbekend commando: {cmd!r}")
        # Netjes afsluiten mag hier blokkeren: de ouder heeft ons al losgelaten.
        try:
            self.close()
        except Exception:
            pass


if __name__ == "__main__":
    Helper().run()
