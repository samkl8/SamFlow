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
