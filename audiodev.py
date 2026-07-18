#!/usr/bin/env python3
"""
audiodev.py - kies de microfoon waarvan we opnemen, en vermijd de Bluetooth-val.

    python audiodev.py     toon de inputs, hun transport, en welke gekozen wordt

Het probleem: opnemen van een Bluetooth-microfoon (AirPods) dwingt macOS om diezelfde
koptelefoon van A2DP (48 kHz, muziekkwaliteit) naar het handsfree-codec (24 kHz, telefoon)
te schakelen. Zolang de mic open staat klinkt je muziek blikkerig; pas als samflow de mic
weer sluit springt hij terug. Empirisch bevestigd:

    AirPods-mic open        -> AirPods-output zakt naar 24000 Hz
    ingebouwde mic open     -> AirPods-output blijft 48000 Hz

De oplossing is simpel: neem niet op van een Bluetooth-apparaat. De ingebouwde MacBook-mic
is voor dictatie prima (beter zelfs dan die van AirPods) en laat je muziek met rust.

Transport-type komt uit CoreAudio (taalonafhankelijk: 'bltn' = ingebouwd, 'blue' = Bluetooth);
apparaatnamen en -indexen uit sounddevice. Beide API's rapporteren identieke namen, dus we
matchen daarop.
"""

import ctypes
import ctypes.util

import sounddevice as sd

# ---------- config ----------
# "auto" | apparaatnaam (deel volstaat) | index. "auto" mijdt Bluetooth-mics.
MIC_DEVICE = "auto"
# ----------------------------

_ca = ctypes.CDLL(ctypes.util.find_library("CoreAudio"))
_cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
_cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
_cf.CFStringGetCString.restype = ctypes.c_bool
_cf.CFRelease.argtypes = [ctypes.c_void_p]


class _Address(ctypes.Structure):
    _fields_ = [("selector", ctypes.c_uint32), ("scope", ctypes.c_uint32),
                ("element", ctypes.c_uint32)]


def _fourcc(code: str) -> int:
    return int.from_bytes(code.encode(), "big")


_GLOBAL = _fourcc("glob")
_INPUT = _fourcc("inpt")
_DEVICE_LIST = _fourcc("dev#")       # kAudioHardwarePropertyDevices
_DEFAULT_INPUT = _fourcc("dIn ")     # kAudioHardwarePropertyDefaultInputDevice
_STREAMS = _fourcc("stm#")           # kAudioDevicePropertyStreams
_TRANSPORT = _fourcc("tran")         # kAudioDevicePropertyTransportType
_NAME = _fourcc("lnam")              # kAudioObjectPropertyName

_TRANSPORTS = {
    _fourcc("bltn"): "ingebouwd",
    _fourcc("blue"): "Bluetooth",
    _fourcc("usb "): "USB",
    _fourcc("hdmi"): "HDMI",
    _fourcc("aggr"): "aggregaat",
    _fourcc("virt"): "virtueel",
}


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


def _name(obj: int) -> str:
    address, ref, size = _Address(_NAME, _GLOBAL, 0), ctypes.c_void_p(), ctypes.c_uint32(8)
    if _ca.AudioObjectGetPropertyData(ctypes.c_uint32(obj), ctypes.byref(address),
                                      0, None, ctypes.byref(size), ctypes.byref(ref)) or not ref:
        return ""
    buffer = ctypes.create_string_buffer(256)
    ok = _cf.CFStringGetCString(ref, buffer, 256, 0x08000100)  # UTF-8
    _cf.CFRelease(ref)
    return buffer.value.decode() if ok else ""


def _has_input(obj: int) -> bool:
    address, size = _Address(_STREAMS, _INPUT, 0), ctypes.c_uint32(0)
    _ca.AudioObjectGetPropertyDataSize(ctypes.c_uint32(obj), ctypes.byref(address),
                                       0, None, ctypes.byref(size))
    return size.value > 0


def refresh():
    """
    Her-initialiseer PortAudio zodat de apparaatlijst de HUIDIGE hardware weerspiegelt.

    PortAudio (V19 op CoreAudio) enumereert apparaten eenmalig bij Pa_Initialize() en
    ziet hotplug daarna NIET meer. De app draait dagen achter elkaar; koppel je je AirPods
    los, dan blijft sounddevice ze tonen en blijft sd.default.device naar dat verdwenen
    apparaat wijzen. choose_input() baseert zich dan op die dode topologie: de stale default
    ('AirPods') staat niet meer in de live CoreAudio-transports, glipt langs de Bluetooth-
    check en wordt als 'gewone default' teruggegeven -> sd.InputStream(device=None) opent het
    verdwenen apparaat -> fout of stilte. Dát is 'de mic schakelt niet mee'. transports()
    zelf is wél live (rechtstreeks CoreAudio); alleen de sounddevice-helft bevriest.

    Deze re-init (gemeten ~3 ms) maakt query_devices()/default weer live. ALLEEN aanroepen
    als er geen stream open staat: _terminate() sluit PortAudio's interne staat af, en dat
    mag niet onder een lopende stream.
    """
    sd._terminate()
    sd._initialize()


def transports() -> dict:
    """{apparaatnaam: transport-string} voor elk apparaat met een input-stream."""
    result = {}
    for device in _uint32_array(1, _DEVICE_LIST):
        if not _has_input(device):
            continue
        name = _name(device)
        if name:
            result[name] = _TRANSPORTS.get(_uint32(device, _TRANSPORT), "?")
    return result


def effective_input_name():
    """
    De naam van de mic waarvan we NU zouden opnemen, live uit CoreAudio -- geen sounddevice,
    dus altijd actueel én veilig aan te roepen terwijl er een opname-stream open staat (in
    tegenstelling tot choose_input(), dat op de bevroren PortAudio-lijst leunt). Puur voor het
    label in de UI; het echte openen loopt via _open() -> refresh() + choose_input().

    Spiegelt het beleid van choose_input: de systeem-default-mic, tenzij die Bluetooth is ->
    dan de ingebouwde mic (we nemen nooit op van een Bluetooth-mic; zie de module-docstring).
    """
    default_dev = _uint32(1, _DEFAULT_INPUT)
    default_name, builtin = None, None
    for device in _uint32_array(1, _DEVICE_LIST):
        if not _has_input(device):
            continue
        name = _name(device)
        transport = _TRANSPORTS.get(_uint32(device, _TRANSPORT), "?")
        if device == default_dev:
            default_name = name
            if transport != "Bluetooth":
                return name          # gewone default -> die gebruiken we ook
        if transport == "ingebouwd" and builtin is None:
            builtin = name
    return builtin or default_name   # default is Bluetooth/onbekend -> ingebouwd


def choose_input(prefer=MIC_DEVICE):
    """
    (sounddevice-index of None, naam, reden). None = laat sounddevice de systeem-default nemen.

    'auto': gebruik de standaard-mic tenzij die Bluetooth is; dan de ingebouwde mic, om te
    voorkomen dat opnemen je muziekkwaliteit sloopt.
    """
    devices = sd.query_devices()
    inputs = [(i, d["name"]) for i, d in enumerate(devices) if d["max_input_channels"] > 0]
    trans = transports()

    if isinstance(prefer, int):
        return prefer, devices[prefer]["name"], "vaste index"

    if prefer and prefer != "auto":
        for index, name in inputs:
            if prefer.lower() in name.lower():
                return index, name, f"naam bevat {prefer!r}"
        # niet gevonden -> val door naar auto

    default_index = sd.default.device[0]
    default_name = devices[default_index]["name"] if isinstance(default_index, int) \
        and default_index >= 0 else None

    if default_name and trans.get(default_name) != "Bluetooth":
        return None, default_name, f"standaard-mic ({trans.get(default_name, '?')})"

    for index, name in inputs:
        if trans.get(name) == "ingebouwd":
            return index, name, "ingebouwde mic (voorkomt Bluetooth-kwaliteitsval)"

    return None, default_name, "geen ingebouwde mic; val terug op standaard"


if __name__ == "__main__":
    trans = transports()
    print("inputs volgens CoreAudio:")
    for name, t in trans.items():
        print(f"  {name:34} {t}")

    index, name, reason = choose_input()
    where = f"sounddevice-index {index}" if index is not None else "systeem-default"
    print(f"\nsamflow neemt op van: {name!r}\n  via {where}\n  reden: {reason}")
