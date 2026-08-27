#!/usr/bin/env python3
"""
samflow.py - hold Fn, talk, and the text lands in whatever app has focus.

    python samflow.py           run the daemon
    python samflow.py --check   verify permissions, mic and Whisper server
    python samflow.py --grant   ask macOS for the three permissions it needs
    python samflow.py --once    record one dictation, print it, do not paste

How it hangs together:

    Fn down ─► mic (already open) ─► Fn up ─► whisper-server ─► cleanup.py ─► paste
              + 0.4s pre-roll                 (warm, ~0.5s)      (rules)     (Cmd+V)

The model lives in whisper-server, not here, so it stays warm between dictations
and is mmap'd rather than held on the Python heap. Cold it costs 11s; warm, 0.5s.
The mic stream is opened on first use and closed after IDLE_CLOSE_SEC so the
orange recording dot is not on all day.
"""

import argparse
import collections
import io
import json
import math
import os
import queue
import struct
import subprocess
import sys
import threading
import time
import wave

# Regel-buffering afdwingen. De app-bundle start ons via een shell die stdout naar
# ~/Library/Logs/samflow.log stuurt, en dan buffert Python in blokken van kilobytes.
# Gevolg: precies de regels vlak vóór een vastloper stonden nog in de buffer en gingen
# verloren zodra je de app afknalde -- elke vastloper wiste zijn eigen bewijs. (De
# PYTHONUNBUFFERED in launchd/com.sam.samflow.plist geldt alleen voor de launchd-route,
# niet voor de app-bundle.) Hier zetten, niet in de launcher: die zit in een ad-hoc
# gesigneerde bundle en elke wijziging daar kost je de mic- en toetsenbord-permissies.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(line_buffering=True)
    except Exception:
        pass

import numpy as np
import requests
from AppKit import (
    NSEvent, NSEventMaskKeyDown, NSPasteboard, NSPasteboardTypeString, NSWorkspace,
)
from Foundation import CFPreferencesCopyAppValue
from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
from Quartz import (
    CFMachPortCreateRunLoopSource, CFRunLoopAddSource, CFRunLoopGetCurrent,
    CFRunLoopRun, CGEventCreateKeyboardEvent, CGEventGetFlags, CGEventMaskBit,
    CGEventPost, CGEventSetFlags, CGEventTapCreate, CGEventTapEnable,
    CGPreflightListenEventAccess, CGPreflightPostEventAccess,
    CGRequestListenEventAccess, CGRequestPostEventAccess,
    kCFRunLoopCommonModes, kCGEventFlagMaskCommand, kCGEventFlagsChanged,
    kCGEventTapOptionListenOnly, kCGHeadInsertEventTap, kCGHIDEventTap,
    kCGSessionEventTap,
)

import audiodev
import cleanup
import history
import hud as hud_module
import lexicon
import media as media_module
import polish
import settings
import snippets
import stall
import stats
import telemetry

# ---------- config ----------
SERVER_URL = "http://127.0.0.1:8181/inference"
# De dicteertaal is een instelling (settings "language", default "nl"); transcribe() geeft
# 'm per dictaat aan whisper-server door en cleanup/polish lezen 'm zelf. Hier stond ooit
# een constante LANGUAGE -- die was al dood en is weg, zodat er één bron van waarheid is.
SAMPLE_RATE = 16000
BLOCK = 1024               # 64 ms per block at 16 kHz
PREROLL_SEC = 0.4          # audio kept from *before* you pressed Fn
IDLE_CLOSE_SEC = 45        # close the mic after this long without a dictation
MIN_SPEECH_SEC = 0.35      # shorter than this is a stray Fn tap, not speech
OPEN_WAIT_SEC = 0.25       # zo lang mag de Fn-callback op een koude mic wachten. Openen
                           # kost gezond 70-110 ms; hierboven is CoreAudio in de knoop en
                           # laten we de main thread los i.p.v. de app te laten bevriezen
HELPER_DEADLINE = 3.0      # zo lang mag de mic-helper over een commando doen. Openen
                           # kost ~104 ms en sluiten minder; blijft het antwoord hierna
                           # uit, dan hangt 'ie in CoreAudio (de lock-order-inversie uit
                           # mic_helper.py) en komt 'ie er nooit meer uit. Afschieten en
                           # een verse starten is dan het enige wat helpt -- zie
                           # Recorder._supervise
HELPER_RESPAWN_SEC = 1.0   # minimale tijd tussen twee helper-starts. Lukt het starten
                           # zélf niet, dan zou de supervisor er vier per seconde
                           # proberen en de log volschrijven
MAX_SPEECH_SEC = 300       # terugval-plafond; de echte grens is instelbaar
                           # (settings 'max_speech_sec', 0 = onbeperkt) -- zie speech_cap()
SILENCE_RMS = 120          # speech measures ~4000, a quiet room ~40. Below this we
                           # never call Whisper: fed silence, it invents sentences.
DEAD_RMS = 1.0             # onder dit is het geen stille kamer maar digitale stilte:
                           # een echte mic heeft altijd een ruisvloer (~40), exact nul
                           # komt van een dode stream of een hardware-gemute mic
HELD_NO_AUDIO_SEC = 1.0    # Fn minstens zo lang vast = een echt dictaat, geen tik.
                           # Komt er dan (vrijwel) geen audio binnen, dan is dat een
                           # mic-fout en klinkt de foutcue -- nooit een stil wegslikken
SOUND_CUES = True
SHOW_HUD = True            # floating pill + menu-bar dot, see hud.py
HUD_FULL_SCALE = 3000.0    # mic RMS that drives the bars to full height
PAUSE_MEDIA = True         # pauze Spotify/Music + demp webaudio (YouTube) tijdens dictaat, zie media.py
SERVER_WAIT_SEC = 60       # at login, wait this long for whisper-server to warm up
CLIPBOARD_RESTORE_SEC = 0.35
# ----------------------------

FN_MASK = 0x00800000       # kCGEventFlagMaskSecondaryFn
KEY_V = 9
SOUNDS = {
    "start": "/System/Library/Sounds/Tink.aiff",
    "done": "/System/Library/Sounds/Pop.aiff",
    "error": "/System/Library/Sounds/Basso.aiff",
    "cancel": "/System/Library/Sounds/Bottle.aiff",
}

KEY_ESC = 53               # keyCode van Esc -- breekt een lopend dictaat af

# Event types that mean "the tap was switched off", not "a key changed".
TAP_DISABLED = (0xFFFFFFFE, 0xFFFFFFFF)

HUD = None   # set by run_daemon; None means headless (--once, --check)


def hud_state(state: str):
    if HUD:
        HUD.set_state(state)


def cue(kind: str):
    # SOUND_CUES is de harde uit-schakelaar (constante); daarbinnen bepaalt de
    # live voorkeur of 'ie klinkt, zodat de toggle in het venster meteen werkt.
    if SOUND_CUES and settings.get("sound_cues"):
        subprocess.Popen(["afplay", SOUNDS[kind]],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def loudest_rms(audio: np.ndarray, window: int = SAMPLE_RATE // 10) -> float:
    """RMS of the loudest 100 ms. Averaging the whole clip would let a short
    sentence inside a long recording look like silence."""
    if len(audio) < window:
        return float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
    blocks = audio[: len(audio) // window * window].astype(np.float64).reshape(-1, window)
    return float(np.sqrt((blocks ** 2).mean(axis=1)).max())


def speech_cap() -> int:
    """Hoeveel seconden spraak we hooguit naar Whisper sturen; 0 = onbeperkt.

    Instelbaar (Instellingen > Dicteren > Maximale lengte). Vroeger stond dit hard op
    120s en werd de staart daarna stil afgeknipt -- een lang bericht kwam half aan zonder
    dat je het merkte. Een kapotte of onzinnige waarde (negatief, tekst, een paar seconden)
    valt terug op MAX_SPEECH_SEC: een handgeschreven settings.json mag de cap nooit per
    ongeluk onder een normale zin duwen."""
    try:
        cap = int(settings.get("max_speech_sec"))
    except (TypeError, ValueError):
        return MAX_SPEECH_SEC
    if cap == 0:
        return 0
    return cap if cap >= 5 else MAX_SPEECH_SEC


def wav_bytes(frames: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(frames.tobytes())
    return buf.getvalue()


def transcribe(audio: bytes) -> str:
    # De timeout schaalt mee met de lengte van de opname. Vast op 60s was prima zolang
    # de cap 2 minuten was, maar met een instelbare (of onbeperkte) lengte kapt hij een
    # geslaagde transcriptie af: een warme turbo doet ~15-20x realtime, dus 15 minuten
    # audio kost tientallen seconden. Helft-van-realtime geeft daar ruime marge boven,
    # en 60s blijft de bodem voor korte dictaten (koud model, eerste dictaat na login).
    seconds = max(0.0, (len(audio) - 44) / (SAMPLE_RATE * 2))   # 44 = WAV-header
    r = requests.post(
        SERVER_URL,
        files={"file": ("speech.wav", audio, "audio/wav")},
        data={"response_format": "json", "language": settings.get("language"),
              "temperature": "0", "prompt": cleanup.whisper_prompt()},
        timeout=max(60.0, seconds * 0.5),
    )
    r.raise_for_status()
    return r.json().get("text", "")


def paste(text: str):
    """Put text on the clipboard, press Cmd+V, then hand the clipboard back."""
    pb = NSPasteboard.generalPasteboard()
    previous = pb.stringForType_(NSPasteboardTypeString)

    pb.clearContents()
    pb.setString_forType_(text, NSPasteboardTypeString)
    change = pb.changeCount()

    for down in (True, False):
        ev = CGEventCreateKeyboardEvent(None, KEY_V, down)
        CGEventSetFlags(ev, kCGEventFlagMaskCommand)
        CGEventPost(kCGHIDEventTap, ev)

    def restore():
        time.sleep(CLIPBOARD_RESTORE_SEC)
        # Alleen terugzetten als het klembord nog van ons is. Heeft iemand het
        # intussen geclaimd ("Kopieer laatste dictaat", of een eigen Cmd+C), dan
        # zou terugzetten die verse kopie vernietigen.
        if previous is not None and pb.changeCount() == change:
            pb.clearContents()
            pb.setString_forType_(previous, NSPasteboardTypeString)

    threading.Thread(target=restore, daemon=True).start()


# Het frameformaat waarmee de mic-helper praat -- zie de kop van mic_helper.py.
APP_DIR = os.path.dirname(os.path.abspath(__file__))
HELPER_MAGIC = b"SF"
HELPER_HEADER = struct.Struct("<2scI")     # magic, type, lengte


def _read_exact(pipe, n: int):
    """Lees precies n bytes, of None als de pipe dichtvalt. Een pipe-lees geeft je zomaar
    minder dan je vroeg; een frame half inlezen zou de hele stroom uit de maat gooien."""
    buf = bytearray()
    while len(buf) < n:
        try:
            chunk = pipe.read(n - len(buf))
        except Exception:
            return None
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


class Recorder:
    """De microfoon, vastgehouden door een kindproces. Houdt een korte pre-roll tussen
    dictaten door, precies als vroeger.

    Waarom een kindproces -- zie de kop van mic_helper.py. Kort: élk afbreek-pad van een
    PortAudio-stream loopt via `AudioOutputUnitStop`, en PortAudio hangt bij het openen
    ongeconditioneerd een listener op die vanuit CoreAudio's eigen IO-thread terugbelt.
    Die twee kunnen in een lock-order-inversie belanden waar geen van beide ooit uit
    komt (gemeten: vier threads op twee sloten). Een thread die in een C-call hangt kun
    je niet afbreken -- in-proces was dat dus het einde van de mic tot een app-herstart,
    terwijl de pill vrolijk "opnemen" bleef tonen. Een proces kún je wél afschieten.

    Gevolg voor deze klasse: hij raakt PortAudio nérgens meer aan. Er is geen
    `self.stream` meer, geen zombie-boekhouding, geen `audiodev.refresh()` (die is naar
    de helper verhuisd, waar hij ongevaarlijk is). Het enige wat hier nog kan blokkeren
    is een pipe-lees op een werkthread; zelfs commando's gaan via een wachtrij, zodat de
    Fn-callback op de main thread niets doet dat kan wachten."""

    def __init__(self):
        self.recording = False
        self.frames = []
        self.preroll = collections.deque(maxlen=int(PREROLL_SEC * SAMPLE_RATE / BLOCK))
        self.lock = threading.Lock()
        self.last_used = 0.0
        self.mic_open = False       # vervangt de oude self.stream -- wij hébben er geen
        self.mic_name = None
        self.respawns = 0           # hoe vaak we een vastgelopen helper opruimden
        self._opened = threading.Event()
        self._proc = None
        self._cmdq = None           # wachtrij naar de stdin-schrijver van déze helper
        self._gen = 0               # generatie; frames van een oude helper negeren we
        self._pending = None        # (commando, starttijd) waar we antwoord op wachten.
                                    # Eén tuple in één attribuut, bewust: twee losse
                                    # attributen zijn twee stores, en las de supervisor
                                    # daartussen dan zag 'ie het nieuwe commando met de
                                    # oude tijd -- en schoot de helper meteen af
        self._close_sent = False    # er is een 'close' onderweg; 'de mic staat open' is
                                    # dan niet genoeg om op af te gaan (zie start())
        self._spawn_t = 0.0
        self._fails = 0             # helpers op rij die niet eens 'ready' haalden
        self._hlock = threading.Lock()   # serialiseert spawn/afschieten
        self._spawn()
        threading.Thread(target=self._reap_idle, daemon=True).start()
        threading.Thread(target=self._supervise, daemon=True).start()

    # ---------- de helper ----------

    def _spawn(self):
        """Start een verse helper en gooi de oude weg. Kost ~320 ms (gemeten), dus dit
        gebeurt bij het opstarten en verder alleen na een vastloper."""
        with self._hlock:
            self._kill_locked()
            self._gen += 1
            gen = self._gen
            self._spawn_t = time.monotonic()
            try:
                proc = subprocess.Popen(
                    [sys.executable, os.path.join(APP_DIR, "mic_helper.py")],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, bufsize=0, cwd=APP_DIR)
            except Exception as e:
                self._fails += 1
                print(f"  ! mic-helper starten mislukt: {e}")
                return
            q = queue.Queue()
            self._proc, self._cmdq = proc, q
            self._pending = None
            self._close_sent = False
            self._opened.clear()
            with self.lock:
                self.mic_open = False
            for target, args in ((self._reader, (proc, gen)),
                                 (self._stderr_reader, (proc,)),
                                 (self._writer, (proc, q))):
                threading.Thread(target=target, args=args, daemon=True).start()

    def _kill_locked(self):
        """SIGKILL, geen SIGTERM-beleefdheid: een helper die in CoreAudio hangt komt
        nergens meer aan toe. Dat dit werkt is geen aanname -- het vastgelopen proces
        van 27-08 ging dood op een gewóne kill, mét vier threads op de HAL-sloten."""
        proc, self._proc, self._cmdq = self._proc, None, None
        if proc is None:
            return
        try:
            proc.kill()
        except Exception:
            pass
        # Oogsten op een aparte thread. SIGKILL is niet te blokkeren dus wait() keert
        # snel terug, maar niemand hoort hier ooit op een proces te staan wachten.
        threading.Thread(target=self._reap_proc, args=(proc,), daemon=True).start()

    @staticmethod
    def _reap_proc(proc):
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            try:
                pipe.close()
            except Exception:
                pass

    def _respawn(self, why: str):
        # Rem erop: mislukt het starten zelf, dan zou de supervisor vier keer per
        # seconde een nieuwe proberen en de log volschrijven. Bij helpers die op rij
        # niet eens 'ready' halen (bestand weg, kapotte import) loopt de wachttijd op
        # tot een halve minuut -- luid genoeg om te zien, stil genoeg om te lezen.
        wait = min(HELPER_RESPAWN_SEC * (2 ** self._fails), 30.0)
        if time.monotonic() - self._spawn_t < wait:
            return
        self.respawns += 1
        print(f"  ! mic-helper opnieuw gestart ({why})")
        was_recording = self.recording
        self._spawn()
        if was_recording:
            # Middenin een dictaat: meteen weer open, anders praat je tegen niets. Wat
            # tot hier binnenkwam staat al in self.frames en blijft gewoon staan.
            self._request_open()

    # ---------- praten met de helper ----------

    def _writer(self, proc, q):
        """Commando's naar de helper. Op een eigen thread, want een pipe-schrijf kan
        blokkeren als de helper niet meer leest -- en de aanroeper is soms de main
        thread (de Fn-callback), die nooit mag wachten."""
        while True:
            cmd = q.get()
            if cmd is None:
                return
            try:
                proc.stdin.write((cmd + "\n").encode())
                proc.stdin.flush()
            except Exception:
                return

    def _send(self, cmd: str):
        q = self._cmdq
        if q is not None:
            q.put(cmd)

    def _request_open(self):
        self._opened.clear()
        self._close_sent = False
        self._pending = ("open", time.monotonic())
        self._send("open")

    def _request_close(self):
        self._close_sent = True
        self._pending = ("close", time.monotonic())
        self._send("close")

    def _done(self, cmd):
        """Meld dat de helper `cmd` heeft afgehandeld. Alleen als het antwoord bij het
        openstaande commando hóórt: staat er een 'open' te wachten en komt er een
        'closed' binnen (de reaper sloot net, jij drukte Fn), dan moet die open gewoon
        bewaakt blijven."""
        pending = self._pending
        if pending is not None and (cmd is None or pending[0] == cmd):
            self._pending = None

    def _reader(self, proc, gen):
        """Leest de framestroom van de helper. Blokkeert vrolijk -- dit is een
        werkthread, en gaat de helper dood dan valt de pipe dicht en stoppen we."""
        out = proc.stdout
        while True:
            head = _read_exact(out, HELPER_HEADER.size)
            if head is None:
                return
            magic, kind, length = HELPER_HEADER.unpack(head)
            if magic != HELPER_MAGIC:
                print("  ! mic-helper stuurt onleesbare frames; verse helper")
                return          # de supervisor ziet het dode proces en start opnieuw
            payload = _read_exact(out, length) if length else b""
            if payload is None:
                return
            if gen != self._gen:
                continue        # oude helper: zijn audio hoort niet meer bij dit dictaat
            if kind == b"A":
                self._on_audio(payload)
            elif kind == b"J":
                self._on_status(payload)

    def _stderr_reader(self, proc):
        for line in iter(proc.stderr.readline, b""):
            text = line.decode(errors="replace").rstrip()
            if text:
                print(f"  ! mic-helper: {text}")

    def _on_audio(self, payload: bytes):
        # Zelfde vorm als vroeger uit sounddevice: (samples, 1) int16.
        block = np.frombuffer(payload, dtype="<i2").reshape(-1, 1)
        with self.lock:
            (self.frames if self.recording else self.preroll).append(block)
            recording = self.recording
        if recording and HUD:
            rms = float(np.sqrt(np.mean(block.astype(np.float64) ** 2)))
            HUD.set_level(math.sqrt(min(rms / HUD_FULL_SCALE, 1.0)))

    def _on_status(self, payload: bytes):
        try:
            msg = json.loads(payload.decode())
        except Exception:
            return
        ev = msg.get("ev")
        if ev == "opened":
            with self.lock:
                self.mic_open = True
                self.mic_name = msg.get("device")
            self._done("open")
            self._opened.set()
        elif ev == "open_failed":
            print(f"  ! mic openen mislukt: {msg.get('err')}")
            self._done("open")
            # Wél vrijgeven: dit dictaat neemt niets op, en dat merkt handle() aan de
            # lege audio (foutcue). Eeuwig laten wachten zou erger zijn.
            self._opened.set()
        elif ev in ("closed", "close_failed"):
            if ev == "close_failed":
                print(f"  ! mic sluiten mislukt: {msg.get('err')}")
            with self.lock:
                self.mic_open = False
            self._close_sent = False
            self._done("close")
            # Niet _opened.clear() als er nog een 'open' onderweg is: die zette het
            # event bewust op scherp en mag 'm zo meteen zetten.
            if self._pending is None:
                self._opened.clear()
        elif ev in ("ready", "pong"):
            self._fails = 0          # deze helper leeft echt; rem weer los
            self._done(None)

    def _supervise(self):
        """De enige plek die een vastgelopen helper opruimt -- en daarmee het hele
        antwoord op "hoe zorgen we dat dit niet meer gebeurt". Blijft een commando
        onbeantwoord, dan hangt de helper in CoreAudio: afschieten en verder."""
        while True:
            time.sleep(0.25)
            proc = self._proc
            if proc is None or proc.poll() is not None:
                self._respawn("het proces was weg")
                continue
            pending = self._pending          # één lees: (commando, starttijd)
            if pending is not None and \
                    time.monotonic() - pending[1] > HELPER_DEADLINE:
                self._respawn(f"'{pending[0]}' bleef {HELPER_DEADLINE:.0f}s "
                              "onbeantwoord -- vastgelopen in CoreAudio")

    def _reap_idle(self):
        while True:
            time.sleep(5)
            with self.lock:
                idle = self.mic_open and not self.recording \
                    and time.monotonic() - self.last_used > IDLE_CLOSE_SEC
            if idle:
                # Precies de call die vroeger de hele mic kon meenemen. Blijft 'ie
                # hangen, dan ruimt de supervisor de helper op en merk jij er niets van.
                self._request_close()

    # ---------- wat de rest van de app gebruikt ----------

    def start(self, use_preroll: bool = True):
        # Eerst opnemen aanzetten, dán pas (eventueel) op de mic wachten. Zo landt elk
        # blok dat binnenkomt meteen in frames, ook als de stream een fractie later pas
        # leeft -- er gaat geen spraak verloren die we anders wél hadden gehad.
        with self.lock:
            # Zonder pre-roll als we net media hebben gepauzeerd: die 0,4 seconde
            # van vóór de Fn-druk bestaat dan uit muziek, en die wil Whisper niet.
            self.frames = list(self.preroll) if use_preroll else []
            self.recording = True
            # 'De mic staat open' is niet genoeg als de reaper net een 'close' heeft
            # weggestuurd: die komt zo alsnog aan en dan praat je tegen niets. De
            # helper werkt commando's op volgorde af, dus een 'open' erachteraan laat
            # 'm gewoon weer opengaan.
            have = self.mic_open and not self._close_sent
        if have:
            return          # normale geval: de mic staat al open
        self._request_open()
        if not self._opened.wait(OPEN_WAIT_SEC):
            print(f"  ! mic reageert niet binnen {OPEN_WAIT_SEC:.2f}s; dit dictaat begint "
                  "zodra hij open is (de app blijft gewoon werken)")

    def stop(self) -> np.ndarray:
        with self.lock:
            self.recording = False
            frames, self.frames = self.frames, []
            self.preroll.clear()
            self.last_used = time.monotonic()
        return np.concatenate(frames) if frames else np.zeros(0, dtype=np.int16)


def handle(audio: np.ndarray, do_paste: bool = True, app: str = None,
           held: float = None):
    # `held` = hoe lang Fn echt vast zat (None bij --once). Nodig om een losse Fn-tik
    # te onderscheiden van "je dicteerde seconden lang en de mic leverde níéts" --
    # die twee eindigen allebei met (bijna) lege frames, maar alleen de eerste mag stil
    # worden weggeslikt. Tekst kwijtraken mag nooit stil gebeuren.
    seconds = len(audio) / SAMPLE_RATE
    if seconds < MIN_SPEECH_SEC:
        if held is not None and held >= HELD_NO_AUDIO_SEC:
            cue("error")
            print(f"  ! geen audio binnengekomen (Fn {held:.1f}s vast, {seconds:.2f}s "
                  "audio) - de mic leverde niets; zie de regels hierboven in de log")
        hud_state("idle")
        return
    # Boven de cap knippen we de staart eraf -- maar nooit meer stilletjes. Wie tegen de
    # grens loopt verliest tekst die hij wél heeft ingesproken; dat hoor en zie je nu
    # (foutgeluid + regel in de log), zodat de knop in Instellingen vindbaar wordt.
    cap = speech_cap()
    if cap and seconds > cap:
        audio = audio[: int(cap * SAMPLE_RATE)]
        cue("error")
        print(f"  ! dictaat van {seconds:.0f}s afgekapt op {cap}s (maximale lengte); "
              f"{seconds - cap:.0f}s spraak niet meegenomen. "
              f"Instellingen > Dicteren > Maximale lengte.")
        seconds = float(cap)

    level = loudest_rms(audio)
    if level < SILENCE_RMS:
        if level < DEAD_RMS and seconds >= HELD_NO_AUDIO_SEC:
            # Geen stille kamer maar digitale stilte: de stream liep, maar leverde
            # exact nullen. Dat is een dood/verkeerd apparaat of een hardware-mute,
            # geen zwijgende spreker -- dus hoorbaar maken, niet stil weggooien.
            cue("error")
            print(f"  ! {seconds:.1f}s digitale stilte (RMS {level:.1f}) - de mic "
                  "levert nullen: dood of verkeerd apparaat, of hardware-mute")
        else:
            print(f"  ({seconds:.1f}s stilte, RMS {level:.0f} - niets verstuurd)")
        hud_state("idle")
        return

    began = time.monotonic()
    try:
        raw = transcribe(wav_bytes(audio))
    except Exception as exc:
        cue("error")
        hud_state("idle")
        print(f"! transcriptie mislukt: {exc}", file=sys.stderr)
        return

    text = cleanup.clean(raw)
    took = time.monotonic() - began

    if not text:
        print(f"  ({seconds:.1f}s spraak, niets bruikbaars: {raw.strip()!r})")
        hud_state("idle")
        return

    # Route B (optioneel, opt-in): een lokaal model poetst de tekst nog een slag op.
    # Uit (default) of bij fout: onveranderd terug. Draait op deze handle-thread, dus
    # blokkeert de run loop niet. Zie polish.py voor de vangrail.
    text = polish.polish(text)

    # Snippets: trigger-frases -> expansies (URL/handtekening/…). Bewust de allerláátste
    # laag -- ná cleanup én ná polish -- zodat geen enkele laag de expansie nog verbouwt.
    # Fail-silent: een fout in de expansie mag het dictaat nooit ophangen.
    try:
        text = snippets.apply(text)
    except Exception:
        pass

    # onthoud wat we nog niet kenden; voer voor `samflow.py --review` (zie lexicon.py)
    lexicon.record(raw)

    print(f"  [{seconds:.1f}s spraak -> {took:.2f}s] {text}")
    if HUD:
        HUD.set_last_text(text)
    if do_paste:
        paste(text)
        cue("done")
    hud_state("done")

    # Inhoudsloze dag-telling voor het dashboard (alleen getallen, nooit tekst).
    # Ná het plakken en fail-silent: het dictaat gaat altijd voor (zoals
    # lexicon.record). Draait op de handle-thread, dus blokkeert de run loop niet.
    if do_paste:
        words = len(text.split())
        try:
            stats.record(words, seconds, took)
        except Exception:
            pass
        # Opt-in historie (mét tekst). No-op zolang de gebruiker 'm uit heeft staan;
        # de app-naam is op het Fn-loslaten-moment op de main thread opgevangen.
        try:
            history.record(text, app, words, seconds, took)
        except Exception:
            pass


def _frontmost_app():
    """De app die nú voorgrond is -- waar je dictaat in geplakt wordt. Wordt op het
    Fn-loslaten-moment (main thread, in de tap-callback) gelezen: één goedkope
    NSWorkspace-call, ruim binnen de 'callback keert meteen terug'-regel. Alleen voor
    de opt-in historie; None als het niet lukt."""
    try:
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        return app.localizedName() if app else None
    except Exception:
        return None


def run_daemon():
    # CGEventTapCreate happily hands back a tap without Input Monitoring - it just
    # never delivers an event. Refuse to start rather than sit there looking alive.
    missing = [n for n, (granted, _, _) in permissions().items() if not granted]
    if missing:
        sys.exit(f"! ontbrekende rechten: {', '.join(missing)}\n"
                 f"  draai eerst: {sys.executable} samflow.py --grant")

    # Wacht op de server in plaats van meteen te stoppen. Bij inloggen starten de
    # server (launchd) en deze app (login item) tegelijk, maar het model laden kost
    # ~12s. Zonder deze wachtlus zou de app te vroeg starten, stoppen, en - als
    # login item zonder herstart - niet meer terugkomen. Komt de server helemaal
    # niet, dan starten we tóch: losse dictaten falen dan netjes tot 'ie er is.
    for _ in range(SERVER_WAIT_SEC):
        if server_up():
            break
        time.sleep(1)
    else:
        print(f"! whisper-server na {SERVER_WAIT_SEC}s nog niet bereikbaar; "
              "start toch door (dictaten falen tot de server er is)")

    if not fn_key_is_free():
        print("! let op: Fn doet nog iets van macOS zelf, zie --check")

    global HUD

    rec = Recorder()
    guard = media_module.MediaGuard() if PAUSE_MEDIA else None
    tap = None

    # Vastzetten (hands-free), zie de "Vastzetten"-voorkeur (lock_mode). 'locked'
    # betekent: doorgaan met opnemen ná Fn-loslaten, tot Fn 'm weer stopt.
    #   off    = alleen vasthouden (zoals vanouds)
    #   tap    = korte Fn-tik zet vast
    #   double = dubbele Fn-tik zet vast
    #   chord  = Fn + ⌘ zet vast
    TAP_MAX = 0.35        # Fn omlaag->omhoog korter dan dit is een "tik", geen "houden"
    DOUBLE_GAP = 0.40     # twee tikken binnen dit venster = een dubbel-tik
    locked = False
    press_t = 0.0
    last_tap_t = 0.0
    fn_was_held = False   # vorige Fn-stand: scheidt een échte Fn-druk van een modifier
                          # (⌘) die verandert terwijl Fn al omlaag is

    def begin():
        # Eerst pauzeren, dan pas opnemen: de pre-roll van vóór de Fn-druk zou
        # anders muziek bevatten. Detectie kost ~20 ms, dat merk je niet.
        paused = guard.pause() if (guard and settings.get("pause_media")) else []
        if paused:
            print(f"  ⏸ {', '.join(name for _, name in paused)}")
        cue("start")
        hud_state("recording")
        rec.start(use_preroll=not paused)

    def end():
        # press_t is de Fn-druk die dít dictaat startte (ook bij vastzetten: de
        # stop-druk werkt press_t niet bij), dus dit is de echte opnameduur.
        held = time.monotonic() - press_t
        hud_state("thinking")
        audio = rec.stop()
        if guard:
            guard.resume()
        # App-naam nú opvangen (main thread): dit is het venster waar geplakt wordt.
        # Alleen als historie aanstaat -- anders geen capture, geen werk.
        app = _frontmost_app() if settings.get("history_enabled") else None
        threading.Thread(target=handle, args=(audio, True, app, held),
                         daemon=True).start()

    def cancel():
        # Esc tijdens opnemen: gooi het dictaat weg. Geen transcriptie, geen plakken,
        # media weer aan. Anders dan end(): die stuurt het naar Whisper; deze niet.
        nonlocal locked
        if not rec.recording:
            return
        rec.stop()                 # frames worden weggegooid (return niet gebruikt)
        if guard:
            guard.resume()
        locked = False
        cue("cancel")
        hud_state("idle")
        print("  ⎋ afgebroken")

    def on_key(event):
        # Globale monitor: vuurt voor toetsen in de app waar je typt (wij zijn een
        # menubalk-accessoire, dus nooit zelf de voorgrond). Alleen Esc, en alleen
        # terwijl we opnemen. Passief: Esc gaat óók naar de app eronder, wat vrijwel
        # nooit kwaad kan -- swallowen zou een actieve tap vragen en dat is het niet
        # waard tegenover het risico voor de Fn-tap.
        if event.keyCode() == KEY_ESC and rec.recording:
            cancel()

    def on_event(proxy, type_, event, refcon):
        nonlocal locked, press_t, last_tap_t, fn_was_held
        if type_ in TAP_DISABLED:
            CGEventTapEnable(tap, True)
            return event
        flags = CGEventGetFlags(event)
        fn_held = bool(flags & FN_MASK)
        cmd_held = bool(flags & kCGEventFlagMaskCommand)
        mode = settings.get("lock_mode")     # per event herlezen; live wisselbaar
        now = time.monotonic()

        fn_down = fn_held and not fn_was_held      # Fn zojuist ingedrukt
        fn_up = (not fn_held) and fn_was_held      # Fn zojuist losgelaten
        fn_was_held = fn_held

        if fn_down:
            if rec.recording and locked:           # Fn opnieuw ingedrukt = stoppen
                end()
                locked = False
                last_tap_t = 0.0
                return event
            if not rec.recording:                  # starten
                press_t = now
                if mode == "double" and last_tap_t and (now - last_tap_t) < DOUBLE_GAP:
                    begin()                        # tweede tik van een dubbel-tik
                    locked = True
                    last_tap_t = 0.0
                else:
                    begin()
                    locked = False
            return event

        if fn_up:
            if not rec.recording or locked:        # losgelaten terwijl vastgezet: door
                return event
            elapsed = now - press_t
            if mode == "tap" and elapsed < TAP_MAX:
                locked = True                      # korte tik zet vast
                return event
            if mode == "double" and elapsed < TAP_MAX:
                rec.stop()                         # eerste tik: weggooien, wacht op #2
                if guard:
                    guard.resume()
                hud_state("idle")
                last_tap_t = now
                return event
            end()                                  # houden losgelaten (of mode uit)
            last_tap_t = 0.0
            return event

        # Geen Fn-transitie: een modifier veranderde. In de chord-modus zet Fn + ⌘
        # tijdens het opnemen vast (⌘ typt niets, dus veilig in de listen-only tap).
        if mode == "chord" and rec.recording and not locked and fn_held and cmd_held:
            locked = True
        return event

    tap = CGEventTapCreate(kCGSessionEventTap, kCGHeadInsertEventTap,
                           kCGEventTapOptionListenOnly,
                           CGEventMaskBit(kCGEventFlagsChanged), on_event, None)
    if tap is None:
        sys.exit("! kon geen event tap maken - geef Invoercontrole (Input Monitoring) "
                 "aan deze python. Draai `python samflow.py --check`.")

    source = CFMachPortCreateRunLoopSource(None, tap, 0)
    CFRunLoopAddSource(CFRunLoopGetCurrent(), source, kCFRunLoopCommonModes)
    CGEventTapEnable(tap, True)

    print("samflow draait. Houd Fn ingedrukt, praat, laat los. Ctrl-C stopt.")

    # Hartslag op de main thread. Staat die stil, dan reageert de app nergens meer op;
    # deze schrijft dan de stack van de main thread naar de log, zodat een vastloper
    # zichzelf verklaart in plaats van sporenloos te verdwijnen. Zie stall.py.
    stall.start()

    # Anonieme dagelijkse heartbeat (alleen tellen). Inert tot er een sink is
    # ingesteld en zolang share_usage aanstaat; draait op een eigen thread.
    telemetry.maybe_send()

    # Opt-in historie: eenmalig prunen bij opstart (verwijdert wat over de retentie
    # heen is). No-op als historie uit staat of retentie op 'altijd'.
    if settings.get("history_enabled"):
        try:
            history.prune()
        except Exception:
            pass

    if not SHOW_HUD:
        CFRunLoopRun()
        return

    # NSApp.run() drives the same main run loop the tap source is attached to,
    # so the pill and the Fn tap share one thread and never race.
    HUD = hud_module.Hud()
    HUD.build()
    # Esc breekt een lopend dictaat af. Globale monitor op de main run loop; raakt
    # de Fn-tap niet. De referentie moet blijven leven, anders ruimt macOS de
    # monitor op -- vandaar het vasthouden in een lokale die leeft zolang HUD.run().
    esc_monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(  # noqa: F841
        NSEventMaskKeyDown, on_key)
    HUD.run()


# Ask macOS itself, never infer. sounddevice will happily open a denied microphone
# and hand you a stream of digital silence, so "the stream opened" proves nothing.
SETTINGS_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_"


def permissions() -> dict:
    return {
        "Microfoon": (
            AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio) == 3,
            "om je stem te horen", "Microphone"),
        "Invoercontrole": (
            bool(CGPreflightListenEventAccess()),
            "om de Fn-toets te zien", "ListenEvent"),
        "Toegankelijkheid": (
            bool(CGPreflightPostEventAccess()),
            "om Cmd+V te sturen", "Accessibility"),
    }


def server_up() -> bool:
    try:
        requests.get(SERVER_URL.rsplit("/", 1)[0] + "/", timeout=2)
        return True
    except Exception:
        return False


def fn_key_is_free() -> bool:
    """
    System Settings > Keyboard > 'Press the fn key to'. Anything but 'Do Nothing'
    means macOS pops the emoji picker (or switches input source) every time you
    start dictating. Unset means the system default, which is not 'Do Nothing'.
    """
    return CFPreferencesCopyAppValue("AppleFnUsageType", "com.apple.HIToolbox") == 0


def check() -> int:
    ok = True
    for name, (granted, why, _) in permissions().items():
        print(f"{'OK ' if granted else 'NEE'} {name:18} {why}")
        ok &= granted

    up = server_up()
    print(f"{'OK ' if up else 'NEE'} {'whisper-server':18} {SERVER_URL}")
    ok &= up

    _, mic_name, reason = audiodev.choose_input()
    print(f"OK  {'Microfoon-keuze':18} {mic_name}  ({reason})")

    if not fn_key_is_free():
        print("\nLET OP: de Fn-toets doet nog iets van macOS zelf (emoji-kiezer of\n"
              "invoerbron). Systeeminstellingen > Toetsenbord > 'Druk op fn-toets om'\n"
              "> 'Niets doen', anders popt dat bij elk dictaat op.")

    if not ok:
        print("\nDraai `python samflow.py --grant` voor de ontbrekende rechten.")
    print(f"\nrechten hangen aan deze binary:\n  {sys.executable}\n"
          f"  -> {os.path.realpath(sys.executable)}")
    return 0 if ok else 1


MIC_STATUS = {0: "nog nooit gevraagd", 1: "beperkt", 2: "geweigerd", 3: "toegestaan"}


def grant() -> int:
    """
    Trigger the real macOS prompts. macOS asks exactly once per permission, ever.
    The microphone is the awkward one: its Settings pane has no '+' button, so a
    binary that was denied (or whose prompt was never answered) cannot be added
    by hand at all. The only way back is `tccutil reset Microphone`, which makes
    macOS forget it ever asked - for every app.
    """
    mic = AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio)
    if mic == 0:
        print("Microfoon: dialoog wordt geopend, klik 'Sta toe'...")
        AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            AVMediaTypeAudio, lambda granted: None)
        for _ in range(60):   # give a human time to actually click it
            time.sleep(1)
            if AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio) != 0:
                break
    elif mic == 2:
        print(f"Microfoon: {MIC_STATUS[mic]}. Het Microfoon-paneel heeft geen '+'-knop,\n"
              "  dus dit is niet met de hand te herstellen. Laat macOS vergeten dat het\n"
              "  ooit gevraagd heeft, en draai --grant opnieuw:\n\n"
              "    tccutil reset Microphone\n")

    if not CGPreflightListenEventAccess():
        CGRequestListenEventAccess()
    if not CGPreflightPostEventAccess():
        CGRequestPostEventAccess()
        AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})

    time.sleep(1)
    missing = [(n, pane) for n, (granted, _, pane) in permissions().items() if not granted]
    if not missing:
        print("Alle rechten staan goed.")
        return 0

    print("Nog ontbrekend:")
    for name, pane in missing:
        extra = f"  (status: {MIC_STATUS.get(mic, '?')})" if name == "Microfoon" else ""
        print(f"  {name:18} open '{SETTINGS_PANE}{pane}'{extra}")
    print(f"\nToegankelijkheid en Invoercontrole: voeg deze binary toe met '+'\n"
          f"(Cmd+Shift+G om het pad te plakken):\n  {sys.executable}")
    return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--check", action="store_true", help="rechten, mic en server verifiëren")
    ap.add_argument("--grant", action="store_true", help="macOS om de rechten vragen")
    ap.add_argument("--once", action="store_true", help="één dictaat opnemen en printen")
    ap.add_argument("--review", action="store_true",
                    help="vaak-gehoorde onbekende woorden afhandelen (de leer-loop)")
    ap.add_argument("--prefs", action="store_true", help="alleen het voorkeuren-venster tonen")
    ap.add_argument("--welcome", action="store_true", help="alleen de eerste-start-wizard tonen")
    ap.add_argument("--window", action="store_true", help="alleen het hoofdvenster tonen")
    args = ap.parse_args()

    if args.window:
        import mainwindow
        mainwindow._run_standalone()
        return

    if args.prefs or args.welcome:
        import prefs
        prefs._run_standalone("welcome" if args.welcome else "prefs")
        return

    if args.check:
        sys.exit(check())

    if args.grant:
        sys.exit(grant())

    if args.review:
        lexicon.review()
        return

    if args.once:
        rec = Recorder()
        input("Enter, praat, dan nog een Enter... ")
        rec.start()
        input("...opnemen, Enter om te stoppen ")
        handle(rec.stop(), do_paste=False)
        return

    run_daemon()


if __name__ == "__main__":
    main()
