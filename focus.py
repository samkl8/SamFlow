#!/usr/bin/env python3
"""
focus.py - work out where on screen the user is actually typing.

    python focus.py     wait 3s, then report what it can see about the focused app

Three levels, best first. Not every app answers, so each one falls through:

    caret    the text insertion point, via the Accessibility API. Native Cocoa
             apps answer this. Terminals and some Electron apps do not.
    window   the focused window of the frontmost app. Always something.
    muis     the screen the mouse is on. Last resort.

Everything here speaks Quartz coordinates: origin top-left, y grows downward.
Cocoa windows use origin bottom-left. `to_cocoa()` is the only place that flips,
and getting that wrong puts the pill on the wrong half of the screen.
"""

import time

import ApplicationServices as AX
from AppKit import NSEvent, NSScreen, NSWorkspace

# ---------- config ----------
AX_TIMEOUT = 0.15     # seconds; a hung app must not freeze the Fn callback
MOUSE_IN_WINDOW = True  # zonder caret: anker op de muis als die in het actieve venster staat
# ----------------------------

_SUCCESS = 0


def _primary_height() -> float:
    """Screen 0 carries the menu bar and defines the global coordinate flip."""
    return NSScreen.screens()[0].frame().size.height


def to_cocoa(x: float, y_top: float, h: float) -> tuple:
    """Quartz top-left rect origin -> Cocoa bottom-left window origin."""
    return x, _primary_height() - (y_top + h)


def point_to_cocoa(x: float, y: float) -> tuple:
    """Quartz point -> Cocoa point. A point has no height to subtract."""
    return x, _primary_height() - y


def _attr(element, attribute):
    err, value = AX.AXUIElementCopyAttributeValue(element, attribute, None)
    return value if err == _SUCCESS else None


def caret_rect():
    system = AX.AXUIElementCreateSystemWide()
    AX.AXUIElementSetMessagingTimeout(system, AX_TIMEOUT)

    focused = _attr(system, AX.kAXFocusedUIElementAttribute)
    if focused is None:
        return None

    selection = _attr(focused, AX.kAXSelectedTextRangeAttribute)
    if selection is None:
        return None

    err, bounds = AX.AXUIElementCopyParameterizedAttributeValue(
        focused, AX.kAXBoundsForRangeParameterizedAttribute, selection, None)
    if err != _SUCCESS or bounds is None:
        return None

    ok, rect = AX.AXValueGetValue(bounds, AX.kAXValueCGRectType, None)
    if not ok:
        return None

    # A collapsed caret has zero width, and some apps report a zero-height rect
    # when the field is empty. Both are useless as an anchor.
    if rect.size.height <= 0:
        return None
    return rect.origin.x, rect.origin.y, max(rect.size.width, 1.0), rect.size.height


def window_rect():
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        return None

    element = AX.AXUIElementCreateApplication(app.processIdentifier())
    AX.AXUIElementSetMessagingTimeout(element, AX_TIMEOUT)

    window = _attr(element, AX.kAXFocusedWindowAttribute)
    if window is None:
        return None

    position, size = _attr(window, AX.kAXPositionAttribute), _attr(window, AX.kAXSizeAttribute)
    if position is None or size is None:
        return None

    ok_p, point = AX.AXValueGetValue(position, AX.kAXValueCGPointType, None)
    ok_s, extent = AX.AXValueGetValue(size, AX.kAXValueCGSizeType, None)
    if not (ok_p and ok_s):
        return None
    return point.x, point.y, extent.width, extent.height


def mouse_rect():
    point = NSEvent.mouseLocation()           # Cocoa: bottom-left origin
    return point.x, _primary_height() - point.y, 1.0, 1.0


def _contains(rect, point) -> bool:
    x, y, w, h = rect
    return x <= point[0] < x + w and y <= point[1] < y + h


def target_rect():
    """
    Geeft (rect in Quartz-coordinaten, naam van het anker). Nooit None.

    Volgorde: caret, dan muis-in-venster, dan venster, dan muis.

    Die tweede is er omdat Electron-apps en terminals hun caret niet prijsgeven.
    Zonder hem landt de pill onderin het venster, wat op een schermvullende
    terminal onderin je hele monitor is. Je muis staat waar je zojuist klikte,
    en dus waar je kijkt — mits hij binnen het actieve venster valt. Staat hij
    daarbuiten (geparkeerd op een ander scherm), dan is het venster de betere gok.
    """
    try:
        rect = caret_rect()
    except Exception:
        rect = None
    if rect:
        return rect, "caret"

    try:
        window = window_rect()
    except Exception:
        window = None

    if window:
        mouse = mouse_rect()
        if MOUSE_IN_WINDOW and _contains(window, (mouse[0], mouse[1])):
            return mouse, "muis-in-venster"
        return window, "venster"

    return mouse_rect(), "muis"


def screen_for(x: float, y_cocoa: float):
    """
    Het scherm dat dit Cocoa-punt bevat. Gebruik het MIDDEN van je anker, niet een
    hoek: schermranden raken elkaar, dus een hoek op x=795 ligt tegelijk in het
    linker- en het rechterscherm en dan wint toevallig de eerste in de lijst.
    Halfopen intervallen (`< max`, niet `<=`) zorgen dat een gedeelde rand bij
    precies één scherm hoort.
    """
    for screen in NSScreen.screens():
        f = screen.frame()
        if f.origin.x <= x < f.origin.x + f.size.width \
           and f.origin.y <= y_cocoa < f.origin.y + f.size.height:
            return screen
    return NSScreen.mainScreen()


def screen_index(screen) -> int:
    for i, s in enumerate(NSScreen.screens()):
        if s.frame().origin.x == screen.frame().origin.x \
           and s.frame().origin.y == screen.frame().origin.y:
            return i
    return -1


if __name__ == "__main__":
    print("Klik binnen 3 seconden in de app die je wilt testen...")
    time.sleep(3)

    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    print(f"\nvoorgrond-app : {app.localizedName() if app else '?'}")
    print(f"caret         : {caret_rect() or 'geeft niets prijs'}")
    print(f"venster       : {window_rect() or 'geeft niets prijs'}")
    print(f"muis          : {mouse_rect()}")

    rect, source = target_rect()
    print(f"\ngekozen anker : {source}  {tuple(round(v) for v in rect)}")
