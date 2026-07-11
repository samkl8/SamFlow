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
import math
import os
import subprocess
import sys
import threading
import time
import wave

import numpy as np
import requests
import sounddevice as sd
from AppKit import NSPasteboard, NSPasteboardTypeString
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
import hud as hud_module
import lexicon
import media as media_module

# ---------- config ----------
SERVER_URL = "http://127.0.0.1:8181/inference"
LANGUAGE = "nl"            # "auto" lets Whisper detect, unreliable on short clips
SAMPLE_RATE = 16000
BLOCK = 1024               # 64 ms per block at 16 kHz
PREROLL_SEC = 0.4          # audio kept from *before* you pressed Fn
IDLE_CLOSE_SEC = 45        # close the mic after this long without a dictation
MIN_SPEECH_SEC = 0.35      # shorter than this is a stray Fn tap, not speech
MAX_SPEECH_SEC = 120
SILENCE_RMS = 120          # speech measures ~4000, a quiet room ~40. Below this we
                           # never call Whisper: fed silence, it invents sentences.
SOUND_CUES = True
SHOW_HUD = True            # floating pill + menu-bar dot, see hud.py
HUD_FULL_SCALE = 3000.0    # mic RMS that drives the bars to full height
PAUSE_MEDIA = True         # pause Spotify/video while you dictate, see media.py
SERVER_WAIT_SEC = 60       # at login, wait this long for whisper-server to warm up
CLIPBOARD_RESTORE_SEC = 0.35
# ----------------------------

FN_MASK = 0x00800000       # kCGEventFlagMaskSecondaryFn
KEY_V = 9
SOUNDS = {
    "start": "/System/Library/Sounds/Tink.aiff",
    "done": "/System/Library/Sounds/Pop.aiff",
    "error": "/System/Library/Sounds/Basso.aiff",
}

# Event types that mean "the tap was switched off", not "a key changed".
TAP_DISABLED = (0xFFFFFFFE, 0xFFFFFFFF)

HUD = None   # set by run_daemon; None means headless (--once, --check)


def hud_state(state: str):
    if HUD:
        HUD.set_state(state)


def cue(kind: str):
    if SOUND_CUES:
        subprocess.Popen(["afplay", SOUNDS[kind]],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def loudest_rms(audio: np.ndarray, window: int = SAMPLE_RATE // 10) -> float:
    """RMS of the loudest 100 ms. Averaging the whole clip would let a short
    sentence inside a long recording look like silence."""
    if len(audio) < window:
        return float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
    blocks = audio[: len(audio) // window * window].astype(np.float64).reshape(-1, window)
    return float(np.sqrt((blocks ** 2).mean(axis=1)).max())


def wav_bytes(frames: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(frames.tobytes())
    return buf.getvalue()


def transcribe(audio: bytes) -> str:
    r = requests.post(
        SERVER_URL,
        files={"file": ("speech.wav", audio, "audio/wav")},
        data={"response_format": "json", "language": LANGUAGE,
              "temperature": "0", "prompt": cleanup.whisper_prompt()},
        timeout=60,
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


class Recorder:
    """A mic stream that stays open between dictations and keeps a short pre-roll."""

    def __init__(self):
        self.stream = None
        self.recording = False
        self.frames = []
        self.preroll = collections.deque(maxlen=int(PREROLL_SEC * SAMPLE_RATE / BLOCK))
        self.lock = threading.Lock()
        self.last_used = 0.0
        threading.Thread(target=self._reap_idle, daemon=True).start()

    def _callback(self, indata, frames, time_info, status):
        with self.lock:
            (self.frames if self.recording else self.preroll).append(indata.copy())
            recording = self.recording
        if recording and HUD:
            rms = float(np.sqrt(np.mean(indata.astype(np.float64) ** 2)))
            HUD.set_level(math.sqrt(min(rms / HUD_FULL_SCALE, 1.0)))

    def _reap_idle(self):
        while True:
            time.sleep(5)
            with self.lock:
                idle = self.stream and not self.recording \
                    and time.monotonic() - self.last_used > IDLE_CLOSE_SEC
            if idle:
                self._close()

    def _open(self):
        if self.stream is None:
            # Kies de mic elke keer opnieuw: koppel je AirPods los, dan wisselt de
            # keuze mee. Opnemen van een Bluetooth-mic zou je muziek naar telefoon-
            # kwaliteit trekken, dus 'auto' mijdt die - zie audiodev.py.
            device, name, _ = audiodev.choose_input()
            self.stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                         dtype="int16", blocksize=BLOCK,
                                         device=device, callback=self._callback)
            self.stream.start()

    def _close(self):
        with self.lock:
            if self.stream:
                self.stream.stop()
                self.stream.close()
                self.stream = None
                self.preroll.clear()

    def start(self, use_preroll: bool = True):
        self._open()
        with self.lock:
            # Zonder pre-roll als we net media hebben gepauzeerd: die 0,4 seconde
            # van vóór de Fn-druk bestaat dan uit muziek, en die wil Whisper niet.
            self.frames = list(self.preroll) if use_preroll else []
            self.recording = True

    def stop(self) -> np.ndarray:
        with self.lock:
            self.recording = False
            frames, self.frames = self.frames, []
            self.preroll.clear()
            self.last_used = time.monotonic()
        return np.concatenate(frames) if frames else np.zeros(0, dtype=np.int16)


def handle(audio: np.ndarray, do_paste: bool = True):
    seconds = len(audio) / SAMPLE_RATE
    if seconds < MIN_SPEECH_SEC:
        hud_state("idle")
        return
    if seconds > MAX_SPEECH_SEC:
        audio = audio[: MAX_SPEECH_SEC * SAMPLE_RATE]

    level = loudest_rms(audio)
    if level < SILENCE_RMS:
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

    # onthoud wat we nog niet kenden; voer voor `samflow.py --review` (zie lexicon.py)
    lexicon.record(raw)

    print(f"  [{seconds:.1f}s spraak -> {took:.2f}s] {text}")
    if HUD:
        HUD.set_last_text(text)
    if do_paste:
        paste(text)
        cue("done")
    hud_state("done")


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

    def on_event(proxy, type_, event, refcon):
        if type_ in TAP_DISABLED:
            CGEventTapEnable(tap, True)
            return event
        fn_held = bool(CGEventGetFlags(event) & FN_MASK)
        if fn_held and not rec.recording:
            # Eerst pauzeren, dan pas opnemen: de pre-roll van vóór de Fn-druk zou
            # anders muziek bevatten. Detectie kost ~20 ms, dat merk je niet.
            paused = guard.pause() if guard else []
            if paused:
                print(f"  ⏸ {', '.join(name for _, name in paused)}")
            cue("start")
            hud_state("recording")
            rec.start(use_preroll=not paused)
        elif not fn_held and rec.recording:
            hud_state("thinking")
            audio = rec.stop()
            if guard:
                guard.resume()
            threading.Thread(target=handle, args=(audio,), daemon=True).start()
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

    if not SHOW_HUD:
        CFRunLoopRun()
        return

    # NSApp.run() drives the same main run loop the tap source is attached to,
    # so the pill and the Fn tap share one thread and never race.
    HUD = hud_module.Hud()
    HUD.build()
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
    args = ap.parse_args()

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
