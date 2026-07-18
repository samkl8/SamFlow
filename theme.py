"""
theme.py - de Helder-kleurtokens als adaptieve NSColors.

Eén bron van waarheid voor het palet uit de mockup
(macos/design/app-interface.html), met eigen licht- én donker-waarden. Elke token
is een *dynamische* NSColor: AppKit lost 'm per-appearance zelf op, dus licht/donker
wisselt vanzelf mee zonder de views te herbouwen.

Grafiet, klei en groen zijn in beide thema's gelijk -- de merk-constanten, net als
de pill die ook nooit van kleur verandert met het thema.

Leaf-module (alleen AppKit): ui/panel/mainwindow delen 'm zonder cyclus.
"""
from AppKit import (
    NSAppearanceNameAqua, NSAppearanceNameDarkAqua, NSColor,
)


def _hex(h, a=1.0):
    h = h.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    return NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, a)


def _w(v, a):   # wit met alpha (hairlines in donker)
    return NSColor.colorWithSRGBRed_green_blue_alpha_(v, v, v, a)


def _ink(a):    # #14121e met alpha (hairlines in licht)
    return NSColor.colorWithSRGBRed_green_blue_alpha_(20 / 255, 18 / 255, 30 / 255, a)


# Exacte tokens uit de :root / dark-media van de mockup.
_LIGHT = {
    "window":  _hex("#ffffff"),   # --ss: het app-oppervlak (venster is wit)
    "sunken":  _hex("#f7f6f8"),   # --sunken: verzonken kaartjes
    "sidebar": _hex("#f0eff3"),   # --sidebg
    "chip":    _hex("#ebeaee"),   # --chip
    "text":    _hex("#1b1b20"),   # --si
    "text2":   _hex("#5c5c66"),   # --sm
    "faint":   _hex("#9a9aa4"),   # --sf
    "line":    _ink(0.10),        # --sl
    "line2":   _ink(0.16),        # --slx
}
_DARK = {
    "window":  _hex("#17171c"),
    "sunken":  _hex("#121216"),
    "sidebar": _hex("#1b1b20"),
    "chip":    _hex("#26262c"),
    "text":    _hex("#f1f1f4"),
    "text2":   _hex("#9a9aa4"),
    "faint":   _hex("#63636d"),
    "line":    _w(1.0, 0.10),
    "line2":   _w(1.0, 0.17),
}


def _dynamic(key):
    def provider(appearance):
        names = [NSAppearanceNameAqua, NSAppearanceNameDarkAqua]
        is_dark = appearance.bestMatchFromAppearancesWithNames_(names) == NSAppearanceNameDarkAqua
        return (_DARK if is_dark else _LIGHT)[key]
    return NSColor.colorWithName_dynamicProvider_("helder." + key, provider)


# --- oppervlakken & tekst (adaptief) ---
WINDOW = _dynamic("window")
SUNKEN = _dynamic("sunken")
SIDEBAR = _dynamic("sidebar")
CHIP = _dynamic("chip")
TEXT = _dynamic("text")
TEXT2 = _dynamic("text2")
FAINT = _dynamic("faint")
LINE = _dynamic("line")
LINE2 = _dynamic("line2")

# --- merk-constanten (gelijk in licht en donker) ---
CLAY = _hex("#c67b52")
GREEN = _hex("#33b859")
GRAPHITE = _hex("#1e1e22")
