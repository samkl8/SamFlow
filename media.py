#!/usr/bin/env python3
"""
media.py - pauzeer wat er speelt zolang Fn ingedrukt is, en zet het daarna terug.

    python media.py     toon wat er nu geluid maakt (pauzeert niets)

Twee vragen, allebei subtieler dan ze lijken.

**Speelt er iets, en wat?**
Niet aan het uitvoerapparaat vragen. `kAudioDevicePropertyDeviceIsRunningSomewhere`
staat permanent op 1 zodra Chrome of een Electron-app open is, ook in complete stilte:
die houden de speaker vast. De juiste vraag is per proces, met de publieke CoreAudio-
API uit macOS 14: `kAudioProcessPropertyIsRunningOutput`. Die zegt wél of er geluid
uit komt, en uit welk proces.

**Hoe pauzeer ik het?**
Niet met een synthetische play/pause-mediatoets. Die wordt op recente macOS genegeerd
(empirisch getest: Spotify bleef gewoon doorspelen), en het is bovendien een *toggle* —
mis je je doel, dan start je juist iets.

Wel met `MRMediaRemoteSendCommand`. Apple heeft in macOS 15.4 het *uitlezen* van
MediaRemote achter een entitlement gezet, waardoor `nowplaying-cli` brak, maar het
*sturen* van commando's werkt nog. En dat geeft ons een echte `pause` en `play` in
plaats van een toggle: pauzeren en hervatten zijn dan onafhankelijk van de toestand.

De whitelist blijft nodig voor het hervatten. Maakt Zoom het geluid, dan doet `pause`
niets, maar zou `play` daarna je stilstaande Spotify starten. Dus: alleen ingrijpen
als de geluidmakende app een mediaspeler of browser is.
"""

import ctypes
import ctypes.util
import subprocess
import threading

from Foundation import NSAppleScript

# ---------- config ----------
# Alleen deze apps pauzeren we. Alles wat hier niet in staat - Zoom, Teams, games,
# systeemgeluiden - laten we met rust.
MEDIA_APPS = {
    "Google Chrome", "Google Chrome Helper", "Safari", "firefox", "Arc", "Vivaldi",
    "Brave Browser", "Microsoft Edge", "Spotify", "Music", "TV", "Podcasts",
    "VLC", "IINA", "QuickTime Player", "mpv", "Plex",
}

# Deze apps kunnen hun eigen afspeelstatus vertellen. Dat is de enige betrouwbare
# bron: een app houdt zijn audio-IO na een pauze nog ~2,6 seconden open, dus
# IsRunningOutput blijft in dat venster op 1 terwijl er niets klinkt. Zonder deze
# controle zou samflow je muziek *starten* als je hem net zelf had uitgezet.
SCRIPTABLE = ("Spotify", "Music")
# ----------------------------

_SYSTEM_OBJECT = 1
_MR_PLAY, _MR_PAUSE = 0, 1

_ca = ctypes.CDLL(ctypes.util.find_library("CoreAudio"))

try:
    _mr = ctypes.CDLL("/System/Library/PrivateFrameworks/MediaRemote.framework/MediaRemote")
    _mr.MRMediaRemoteSendCommand.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    _mr.MRMediaRemoteSendCommand.restype = ctypes.c_bool
except OSError:
    _mr = None


class _Address(ctypes.Structure):
    _fields_ = [("selector", ctypes.c_uint32),
                ("scope", ctypes.c_uint32),
                ("element", ctypes.c_uint32)]


def _fourcc(code: str) -> int:
    return int.from_bytes(code.encode(), "big")


_GLOBAL = _fourcc("glob")
_PROCESS_LIST = _fourcc("prs#")     # kAudioHardwarePropertyProcessObjectList
_PROCESS_PID = _fourcc("ppid")      # kAudioProcessPropertyPID
_RUNNING_OUTPUT = _fourcc("piro")   # kAudioProcessPropertyIsRunningOutput

_names = {}   # pid -> procesnaam; `ps` kost ~10 ms en pids veranderen niet


def _uint32_array(obj: int, selector: int) -> list:
    address, size = _Address(selector, _GLOBAL, 0), ctypes.c_uint32(0)
    if _ca.AudioObjectGetPropertyDataSize(ctypes.c_uint32(obj), ctypes.byref(address),
                                          0, None, ctypes.byref(size)):
        return []
    buffer = (ctypes.c_uint32 * (size.value // 4))()
    if _ca.AudioObjectGetPropertyData(ctypes.c_uint32(obj), ctypes.byref(address),
                                      0, None, ctypes.byref(size), ctypes.byref(buffer)):
        return []
    return list(buffer)


def _uint32(obj: int, selector: int):
    address = _Address(selector, _GLOBAL, 0)
    value, size = ctypes.c_uint32(0), ctypes.c_uint32(4)
    err = _ca.AudioObjectGetPropertyData(ctypes.c_uint32(obj), ctypes.byref(address),
                                         0, None, ctypes.byref(size), ctypes.byref(value))
    return None if err else value.value


def _process_name(pid: int) -> str:
    if pid not in _names:
        out = subprocess.run(["ps", "-p", str(pid), "-o", "comm="],
                             capture_output=True, text=True).stdout.strip()
        _names[pid] = out.rsplit("/", 1)[-1] if out else ""
    return _names[pid]


def sounding() -> list:
    """[(pid, naam), ...] van elk proces dat op dít moment audio uitvoert."""
    found = []
    for process in _uint32_array(_SYSTEM_OBJECT, _PROCESS_LIST):
        if not _uint32(process, _RUNNING_OUTPUT):
            continue
        pid = _uint32(process, _PROCESS_PID)
        if pid is not None:
            found.append((pid, _process_name(pid)))
    return found


# `tell application "Music" to player state` START Music.app als die niet draait.
# De `is running`-test doet dat niet. En `as text` dwingt "playing" af in plaats van
# de rauwe AppleEvent-code ('kPSP'), waar we niet op willen bouwen.
_PLAYER_STATE = '''
if application "{app}" is running then
    tell application "{app}" to return player state as text
else
    return "stopped"
end if
'''

_scripts = {}   # app -> gecompileerd NSAppleScript; compileren kost, uitvoeren 27 ms


def _really_playing(app: str) -> bool:
    """Vraag het de app zelf. Alleen Spotify en Music kunnen dit."""
    if app not in _scripts:
        script = NSAppleScript.alloc().initWithSource_(_PLAYER_STATE.format(app=app))
        ok, _ = script.compileAndReturnError_(None)
        _scripts[app] = script if ok else None

    script = _scripts[app]
    if script is None:
        return True   # niet te bevragen? dan is het audio-signaal onze beste gok

    result, error = script.executeAndReturnError_(None)
    if error or result is None:
        return True
    return result.stringValue() == "playing"


def pauseable() -> list:
    """Media-apps die op dit moment écht afspelen."""
    found = []
    for pid, name in sounding():
        if name not in MEDIA_APPS:
            continue
        if name in SCRIPTABLE and not _really_playing(name):
            continue   # audio-IO nog open, maar de app staat stil
        found.append((pid, name))
    return found


class MediaGuard:
    """Pauzeert bij het begin van een dictaat, hervat aan het eind - en alleen als wíj
    ook echt gepauzeerd hebben. Zonder die boekhouding zou `play` na een dictaat
    muziek starten die al stil stond."""

    available = _mr is not None

    def __init__(self):
        self._paused = False
        self._lock = threading.Lock()

    def pause(self) -> list:
        """Geeft terug wat er gepauzeerd is (leeg = niets gedaan)."""
        if not self.available:
            return []
        with self._lock:
            if self._paused:
                return []
            playing = pauseable()
            if not playing:
                return []
            _mr.MRMediaRemoteSendCommand(_MR_PAUSE, None)
            self._paused = True
            return playing

    def resume(self):
        if not self.available:
            return
        with self._lock:
            if self._paused:
                _mr.MRMediaRemoteSendCommand(_MR_PLAY, None)
                self._paused = False


if __name__ == "__main__":
    if not MediaGuard.available:
        print("MediaRemote niet beschikbaar - pauzeren staat uit")

    playing = sounding()
    if not playing:
        print("er speelt niets af")
    for pid, name in playing:
        mark = "PAUZEERBAAR" if name in MEDIA_APPS else "met rust laten"
        print(f"  {name:24} pid {pid:<8} {mark}")
