#!/usr/bin/env python3
"""
hud.py - the floating pill and menu-bar dot that show what samflow is doing.

    python hud.py     demo: cycles through the states so you can see them

Three states, and the pill only shows for two of them:

    idle       hidden. Menu bar shows a hollow dot.
    recording  visible, live bars driven by the microphone. This is the point of
               the whole thing: the bars move only if the mic is really hearing
               you, so you never sit talking into a denied microphone again.
    thinking   visible, pulsing dots while whisper-server works.
    done       a green tick for a moment, then back to hidden.

The window is a non-activating panel. That matters: if it ever took focus, the
Cmd+V that follows would paste into the pill instead of into your editor.

Every AppKit call has to happen on the main thread, so nothing here draws
directly. Background threads only write to Hud.state / Hud.level, and a 30 fps
timer on the main thread reads them and redraws.
"""

import math
import sys
import threading
import time

import objc
from AppKit import (
    NSApplication, NSApplicationActivationPolicyAccessory, NSBackingStoreBuffered,
    NSBezierPath, NSColor, NSMakeRect, NSMenu, NSMenuItem, NSPanel, NSScreen,
    NSStatusBar, NSTimer, NSView,
    NSWindowCollectionBehaviorCanJoinAllSpaces, NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary, NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)
from Foundation import NSObject

import focus

# ---------- config ----------
PILL_W, PILL_H = 168.0, 46.0
FOLLOW_CARET = True        # place the pill where you type, not bottom-centre
CARET_GAP = 14.0           # points between the caret and the pill
SCREEN_MARGIN = 12.0       # never sit flush against a screen edge
PILL_BOTTOM = 130.0        # fallback height above the bottom of the screen
BARS = 5
BAR_W, BAR_GAP = 5.0, 7.0
BAR_MIN, BAR_MAX = 4.0, 24.0
DONE_FLASH_SEC = 0.7
WINDOW_LEVEL = 25          # NSStatusWindowLevel: above normal windows, below alerts
MENU_ICONS = {"idle": "◌", "recording": "◉", "thinking": "◍", "done": "◉"}
MENU_LABELS = {"idle": "klaar — houd Fn ingedrukt om te dicteren",
               "recording": "aan het luisteren…",
               "thinking": "transcriberen…",
               "done": "geplakt ✓"}
# ----------------------------

_ACCENT = {
    "recording": (1.00, 0.35, 0.32),   # red
    "thinking": (0.45, 0.65, 1.00),    # blue
    "done": (0.30, 0.85, 0.45),        # green
}


def _bottom_centre(screen) -> tuple:
    f = screen.frame()
    return f.origin.x + (f.size.width - PILL_W) / 2, f.origin.y + PILL_BOTTOM


def placement() -> tuple:
    """
    Waar komt de pill? Pure functie: geen venster nodig, dus los te bevragen met
    `python hud.py --where`. Geeft ((x, y) in Cocoa-coordinaten, uitleg-dict).
    """
    if not FOLLOW_CARET:
        screen = NSScreen.mainScreen()
        return _bottom_centre(screen), {"anker": "uit", "scherm": focus.screen_index(screen)}

    (x, y_top, w, h), source = focus.target_rect()

    # Kies het scherm op het MIDDEN van het anker. Op een hoek kiezen is fout: bij
    # aangrenzende schermen ligt een hoekpunt op de gedeelde rand en wint willekeurig
    # het eerste scherm in de lijst -- precies de bug waarbij de pill op de verkeerde
    # monitor belandt.
    screen = focus.screen_for(*focus.point_to_cocoa(x + w / 2, y_top + h / 2))
    frame = screen.frame()

    # caret en muis-in-venster zijn punten: de pill komt eronder. Een venster is een
    # vlak: de pill komt onderin, niet eronder.
    punt = source in ("caret", "muis-in-venster")

    if source == "muis":
        origin = _bottom_centre(screen)
    else:
        left = x + w / 2 - PILL_W / 2
        top = y_top + h + CARET_GAP if punt else y_top + h - PILL_H - PILL_BOTTOM
        origin = focus.to_cocoa(left, top, PILL_H)

        # eronder is de natuurlijke plek, maar niet als dat buiten beeld valt
        if punt and origin[1] < frame.origin.y + SCREEN_MARGIN:
            origin = focus.to_cocoa(left, y_top - CARET_GAP - PILL_H, PILL_H)

    origin = (
        min(max(origin[0], frame.origin.x + SCREEN_MARGIN),
            frame.origin.x + frame.size.width - PILL_W - SCREEN_MARGIN),
        min(max(origin[1], frame.origin.y + SCREEN_MARGIN),
            frame.origin.y + frame.size.height - PILL_H - SCREEN_MARGIN),
    )
    return origin, {"anker": source, "anker_rect": (x, y_top, w, h),
                    "scherm": focus.screen_index(screen),
                    "scherm_frame": (frame.origin.x, frame.origin.y,
                                     frame.size.width, frame.size.height)}


class _PillView(NSView):
    """Draws the pill. Reads hud.state / hud.level, never writes them."""

    def initWithHud_(self, hud):
        self = objc.super(_PillView, self).initWithFrame_(
            NSMakeRect(0, 0, PILL_W, PILL_H))
        self.hud = hud
        return self

    def drawRect_(self, _rect):
        state, level = self.hud.snapshot()
        t = time.monotonic()

        backdrop = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(0, 0, PILL_W, PILL_H), PILL_H / 2, PILL_H / 2)
        NSColor.colorWithCalibratedWhite_alpha_(0.09, 0.88).set()
        backdrop.fill()
        NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.10).set()
        backdrop.setLineWidth_(1.0)
        backdrop.stroke()

        r, g, b = _ACCENT.get(state, (0.6, 0.6, 0.6))
        NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1.0).set()

        if state == "recording":
            self._draw_bars(level, t)
        elif state == "thinking":
            self._draw_dots(t)
        elif state == "done":
            self._draw_tick()

    def _draw_bars(self, level, t):
        total = BARS * BAR_W + (BARS - 1) * BAR_GAP
        x = (PILL_W - total) / 2
        for i in range(BARS):
            # a little phase offset per bar so a steady tone still looks alive
            wobble = 0.55 + 0.45 * math.sin(t * 7.0 + i * 1.1)
            h = BAR_MIN + (BAR_MAX - BAR_MIN) * level * wobble
            y = (PILL_H - h) / 2
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(x, y, BAR_W, h), BAR_W / 2, BAR_W / 2).fill()
            x += BAR_W + BAR_GAP

    def _draw_dots(self, t):
        d, gap = 7.0, 10.0
        x = (PILL_W - (3 * d + 2 * gap)) / 2
        for i in range(3):
            alpha = 0.30 + 0.70 * (0.5 + 0.5 * math.sin(t * 4.0 - i * 0.8))
            r, g, b = _ACCENT["thinking"]
            NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, alpha).set()
            NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(x, (PILL_H - d) / 2, d, d)).fill()
            x += d + gap

    def _draw_tick(self):
        cx, cy = PILL_W / 2, PILL_H / 2
        p = NSBezierPath.bezierPath()
        p.setLineWidth_(3.0)
        p.moveToPoint_((cx - 9, cy + 1))
        p.lineToPoint_((cx - 3, cy - 6))
        p.lineToPoint_((cx + 10, cy + 8))
        p.stroke()


class _Ticker(NSObject):
    def initWithHud_(self, hud):
        self = objc.super(_Ticker, self).init()
        self.hud = hud
        return self

    def tick_(self, _timer):
        self.hud._on_tick()

    def quit_(self, _sender):
        NSApplication.sharedApplication().terminate_(None)


class Hud:
    def __init__(self):
        self._lock = threading.Lock()
        self._state = "idle"
        self._level = 0.0
        self._shown = None
        self._done_until = 0.0

    # --- called from any thread -------------------------------------------
    def set_state(self, state: str):
        with self._lock:
            self._state = state
            if state == "done":
                self._done_until = time.monotonic() + DONE_FLASH_SEC
            if state != "recording":
                self._level = 0.0

    def set_level(self, level: float):
        with self._lock:
            # ease upward fast, fall back slowly, or the bars look like static
            self._level = max(min(level, 1.0), self._level * 0.75)

    def snapshot(self):
        with self._lock:
            return self._state, self._level

    # --- main thread only --------------------------------------------------
    def build(self):
        self.app = NSApplication.sharedApplication()
        self.app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

        screen = NSScreen.mainScreen().frame()
        frame = NSMakeRect((screen.size.width - PILL_W) / 2, PILL_BOTTOM, PILL_W, PILL_H)

        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered, False)
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(NSColor.clearColor())
        self.panel.setLevel_(WINDOW_LEVEL)
        self.panel.setIgnoresMouseEvents_(True)
        self.panel.setHidesOnDeactivate_(False)
        self.panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorFullScreenAuxiliary)
        self.panel.setContentView_(_PillView.alloc().initWithHud_(self))

        self.status = NSStatusBar.systemStatusBar().statusItemWithLength_(-1.0)
        self.status.button().setTitle_(MENU_ICONS["idle"])

        self._ticker = _Ticker.alloc().initWithHud_(self)
        self._build_menu()
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1 / 30.0, self._ticker, "tick:", None, True)

    def _place(self):
        """
        Put the pill where the user is looking. Called once, at the moment the
        pill appears - the caret cannot move while Fn is held, and repositioning
        every frame would make it jitter.
        """
        origin, _ = placement()
        self.panel.setFrameOrigin_(origin)

    def _build_menu(self):
        """Maakt de ◌ klikbaar: een statusregel en een manier om te stoppen."""
        menu = NSMenu.alloc().init()

        self._menu_status = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "SamFlow — klaar", None, "")
        self._menu_status.setEnabled_(False)
        menu.addItem_(self._menu_status)

        menu.addItem_(NSMenuItem.separatorItem())

        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Stop SamFlow", "quit:", "")
        quit_item.setTarget_(self._ticker)
        menu.addItem_(quit_item)

        # Vasthouden op self: setMenu_ hoort het menu te retainen, maar zonder een
        # eigen Python-referentie ruimt de garbage collector het object op en hangt
        # er een dood menu aan het icoon — dan doet klikken niets. (Getest: mét deze
        # regel verschijnt het menu, zonder niet.)
        self._menu = menu
        self.status.setMenu_(menu)

    def _on_tick(self):
        with self._lock:
            state = self._state
            if state == "done" and time.monotonic() > self._done_until:
                state = self._state = "idle"

        if state != self._shown:
            was = self._shown
            self._shown = state
            self.status.button().setTitle_(MENU_ICONS[state])
            self._menu_status.setTitle_(f"SamFlow — {MENU_LABELS[state]}")
            if state == "idle":
                self.panel.orderOut_(None)
            else:
                if was in (None, "idle"):
                    self._place()
                # never makeKeyAndOrderFront: that would steal focus from the app
                # we are about to paste into.
                self.panel.orderFrontRegardless()

        if state != "idle":
            self.panel.contentView().setNeedsDisplay_(True)

    def run(self):
        self.app.run()


def _where():
    """Klik in een app op een willekeurig scherm; dit vertelt wat de pill zou doen."""
    print("Klik binnen 3 seconden in de app waar het misgaat...")
    time.sleep(3)

    print("\nschermen (Cocoa, origin linksonder):")
    for i, screen in enumerate(NSScreen.screens()):
        f = screen.frame()
        print(f"  {i}: origin=({f.origin.x:7.0f},{f.origin.y:7.0f})  "
              f"{f.size.width:.0f}x{f.size.height:.0f}")

    origin, why = placement()
    print(f"\nanker      : {why['anker']}")
    if "anker_rect" in why:
        print(f"anker-rect : {tuple(round(v) for v in why['anker_rect'])}  (Quartz, y omlaag)")
        print(f"scherm     : {why['scherm']}  frame "
              f"{tuple(round(v) for v in why['scherm_frame'])}")
    print(f"pill komt op ({origin[0]:.0f}, {origin[1]:.0f})  (Cocoa, y omhoog)")


if __name__ == "__main__":
    if "--where" in sys.argv:
        _where()
        raise SystemExit(0)

    hud = Hud()
    hud.build()

    def demo():
        time.sleep(0.5)
        for cycle in range(2):
            hud.set_state("recording")
            for i in range(70):
                hud.set_level(abs(math.sin(i / 8.0)) * 0.9)
                time.sleep(1 / 30)
            hud.set_state("thinking")
            time.sleep(1.5)
            hud.set_state("done")
            time.sleep(1.5)
        print("demo klaar, Ctrl-C om te stoppen")

    threading.Thread(target=demo, daemon=True).start()
    hud.run()
