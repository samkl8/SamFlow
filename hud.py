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

import json
import math
import os
import shlex
import subprocess
import sys
import threading
import time

import objc
from AppKit import (
    NSApplication, NSApplicationActivationPolicyAccessory,
    NSApplicationDidChangeScreenParametersNotification, NSBackingStoreBuffered,
    NSBezierPath, NSColor, NSEventMaskLeftMouseDown, NSImage, NSMakeRect, NSPanel,
    NSPasteboard, NSPasteboardTypeString, NSScreen, NSStatusBar, NSTimer, NSView,
    NSWindowCollectionBehaviorCanJoinAllSpaces, NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary, NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)
from Foundation import NSNotificationCenter, NSObject

import focus
import lexicon
import panel
import prefs
import settings
import updater

# Eerste update-check kort na opstart (netwerk moet er zijn), daarna elke 6 uur.
UPDATE_FIRST_DELAY_SEC = 8
UPDATE_INTERVAL_SEC = 6 * 3600

# ---------- config ----------
PILL_W, PILL_H = 92.0, 30.0     # compact & clean -- was 168x46
FOLLOW_CARET = True        # place the pill where you type, not bottom-centre
CARET_GAP = 12.0           # points between the caret and the pill
SCREEN_MARGIN = 12.0       # never sit flush against a screen edge
PILL_BOTTOM = 130.0        # fallback height above the bottom of the screen
BARS = 5
BAR_W, BAR_GAP = 3.5, 4.5
BAR_MIN, BAR_MAX = 3.0, 15.0
DONE_FLASH_SEC = 0.7
WINDOW_LEVEL = 25          # NSStatusWindowLevel: above normal windows, below alerts
# ----------------------------

# Kleuren binnen de pill (bij de cursor). Wit tijdens werken -- net als de witte
# balkjes van het menu-glyph op de donkere pill -- en groen als 't klaar is:
# dezelfde groen als de "klaar"-stip en de toggles in het menubalk-paneel.
_WHITE = (0.94, 0.94, 0.95)
_GREEN = (0.20, 0.72, 0.35)             # #33B859 -- gelijk aan het menu
_CLAY = (0.776, 0.482, 0.322)           # #C67B52 -- nu alleen nog het menubalk-icoon
_PILL = {
    "recording": _WHITE,
    "thinking": _WHITE,
    "done": _GREEN,
}

# Kleuren voor het menubalk-icoon per status. Opnemen in klei zodat 't op zowel een
# lichte als donkere menubalk zichtbaar blijft; idle neutraal grijs. Bewust los van
# _PILL: wit zou op een lichte menubalk verdwijnen.
_STATUS_COLORS = {
    "recording": _CLAY,
    "thinking": _CLAY,
    "done": _CLAY,
    "idle": (0.60, 0.60, 0.62),
}


def _status_image(state):
    """Het balkjes-icoon voor de menubalk, gekleurd naar de status. Klein en niet-
    template, zodat de kleur (rood tijdens opnemen) behouden blijft."""
    r, g, b = _STATUS_COLORS.get(state, (0.6, 0.6, 0.62))
    w, h = 22.0, 16.0
    heights = [0.42, 0.72, 1.00, 0.60]
    bw, gap = 2.8, 2.4
    img = NSImage.alloc().initWithSize_((w, h))
    img.lockFocus()
    NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1.0).set()
    total = len(heights) * bw + (len(heights) - 1) * gap
    x = (w - total) / 2
    for hh in heights:
        bh = 4.0 + (h - 6.0) * hh
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(x, (h - bh) / 2, bw, bh), bw / 2, bw / 2).fill()
        x += bw + gap
    img.unlockFocus()
    return img


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
        # grafiet #1E1E22, licht doorschijnend -- leesbaar boven lichte én donkere apps
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.118, 0.118, 0.133, 0.90).set()
        backdrop.fill()
        NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.10).set()
        backdrop.setLineWidth_(1.0)
        backdrop.stroke()

        r, g, b = _PILL.get(state, (0.9, 0.9, 0.9))
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
            # (kalmer dan voorheen -- cleaner beeld bij de kleinere pill)
            wobble = 0.70 + 0.30 * math.sin(t * 6.0 + i * 1.1)
            h = BAR_MIN + (BAR_MAX - BAR_MIN) * level * wobble
            y = (PILL_H - h) / 2
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(x, y, BAR_W, h), BAR_W / 2, BAR_W / 2).fill()
            x += BAR_W + BAR_GAP

    def _draw_dots(self, t):
        d, gap = 6.0, 8.0
        x = (PILL_W - (3 * d + 2 * gap)) / 2
        for i in range(3):
            alpha = 0.30 + 0.70 * (0.5 + 0.5 * math.sin(t * 4.0 - i * 0.8))
            r, g, b = _PILL["thinking"]
            NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, alpha).set()
            NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(x, (PILL_H - d) / 2, d, d)).fill()
            x += d + gap

    def _draw_tick(self):
        cx, cy = PILL_W / 2, PILL_H / 2
        s = PILL_H / 46.0          # schaal mee met de pill-hoogte, blijft proportioneel
        p = NSBezierPath.bezierPath()
        p.setLineWidth_(2.5)
        p.moveToPoint_((cx - 9 * s, cy + 1 * s))
        p.lineToPoint_((cx - 3 * s, cy - 6 * s))
        p.lineToPoint_((cx + 10 * s, cy + 8 * s))
        p.stroke()


class _Ticker(NSObject):
    def initWithHud_(self, hud):
        self = objc.super(_Ticker, self).init()
        self.hud = hud
        return self

    def tick_(self, _timer):
        self.hud._on_tick()

    def screensChanged_(self, _note):
        self.hud._rebuild_panel()

    def togglePopover_(self, _sender):
        self.hud._panel.toggle(self.hud.status.button())

    def restartApp_(self, _sender):
        # Nieuwe code is al binnengehaald; een herstart laadt 'm. TCC-veilig via
        # de bundle (updater.relaunch), dan onszelf afsluiten.
        updater.relaunch()
        self.hud.app.terminate_(None)

    def applyUpdate_(self, _sender):
        # Handmatig "nu bijwerken" (auto stond uit of kon niet vanzelf). Kort
        # blokkeren mag: het is een bewuste klik. Lukt de fast-forward, herstart.
        info = updater.check()
        ok, _msg = updater.apply(info) if info else (False, "")
        if ok:
            updater.relaunch()
            self.hud.app.terminate_(None)

    def checkForUpdates_(self, _sender):
        # Op een thread: git fetch is netwerk. Werkt de Hud-state bij; het paneel
        # toont de uitkomst bij de volgende keer openen.
        threading.Thread(target=self._check_updates_bg, daemon=True).start()

    @objc.python_method
    def _check_updates_bg(self):
        info = updater.check()
        if not info or info["behind"] == 0:
            self.hud.set_update({"applied": False, "available": False,
                                 "subject": "", "can_apply": False})
            return
        if settings.get("auto_update") and info["can_apply"]:
            ok, _ = updater.apply(info)
            self.hud.set_update({"applied": ok, "available": not ok,
                                 "subject": info["subject"], "can_apply": info["can_apply"]})
        else:
            self.hud.set_update({"applied": False, "available": True,
                                 "subject": info["subject"], "can_apply": info["can_apply"]})

    # --- paneel-acties. Draaien op de main thread (het zijn klikken), maar het
    #     echte werk gaat naar losse processen zodat het paneel nooit blokkeert.
    def editLexicon_(self, _sender):
        subprocess.Popen(["open", "-t", lexicon.LEXICON_FILE])

    def reviewWords_(self, _sender):
        # --review is interactief, dus in een echte Terminal, niet in dit proces.
        base = os.path.dirname(os.path.abspath(__file__))
        cmd = f"cd {shlex.quote(base)} && {shlex.quote(sys.executable)} samflow.py --review"
        subprocess.Popen([
            "osascript",
            "-e", 'tell application "Terminal" to activate',
            "-e", f'tell application "Terminal" to do script {json.dumps(cmd)}',
        ])

    def openPreferences_(self, _sender):
        prefs.open_preferences()

    def openWelcome_(self, _sender):
        prefs.open_welcome()

    def openPermissions_(self, _sender):
        subprocess.Popen([
            "open",
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
        ])

    def copyLastText_(self, _sender):
        # Vangnet: paste() geeft het klembord na het plakken terug aan de vorige
        # eigenaar, dus wie zijn dictaat wegklikt is het anders echt kwijt.
        text = self.hud.last_text()
        if text:
            pb = NSPasteboard.generalPasteboard()
            pb.clearContents()
            pb.setString_forType_(text, NSPasteboardTypeString)

    def validateMenuItem_(self, item):
        if item.action() == "copyLastText:":
            return self.hud.last_text() is not None
        return True

    def quit_(self, _sender):
        NSApplication.sharedApplication().terminate_(None)


class Hud:
    def __init__(self):
        self._lock = threading.Lock()
        self._state = "idle"
        self._level = 0.0
        self._shown = None
        self._done_until = 0.0
        self._last_text = None
        self._update = {"applied": False, "available": False,
                        "subject": "", "can_apply": False}
        self.panel = None

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

    def set_last_text(self, text: str):
        # Alleen in het geheugen, nooit op schijf: dictaten zijn persoonlijk.
        with self._lock:
            self._last_text = text

    def last_text(self):
        with self._lock:
            return self._last_text

    def set_update(self, state: dict):
        with self._lock:
            self._update = dict(state)

    def update_state(self):
        with self._lock:
            return dict(self._update)

    def _update_loop(self):
        """Achtergrond: check bij GitHub en (als auto_update aanstaat en het een
        schone fast-forward is) trek de update binnen. Zet alleen Hud-state; de
        nieuwe code gaat pas leven na een herstart, die het paneel aanbiedt."""
        time.sleep(UPDATE_FIRST_DELAY_SEC)
        while True:
            try:
                info = updater.check()
                if info and info["behind"] > 0:
                    if settings.get("auto_update") and info["can_apply"]:
                        ok, _ = updater.apply(info)
                        self.set_update({"applied": ok, "available": not ok,
                                         "subject": info["subject"],
                                         "can_apply": info["can_apply"]})
                    else:
                        self.set_update({"applied": False, "available": True,
                                         "subject": info["subject"],
                                         "can_apply": info["can_apply"]})
            except Exception:
                pass
            time.sleep(UPDATE_INTERVAL_SEC)

    def snapshot(self):
        with self._lock:
            return self._state, self._level

    # --- main thread only --------------------------------------------------
    def build(self):
        self.app = NSApplication.sharedApplication()
        self.app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

        self._build_panel()

        self.status = NSStatusBar.systemStatusBar().statusItemWithLength_(-1.0)
        self._status_images = {s: _status_image(s)
                               for s in ("idle", "recording", "thinking", "done")}
        self.status.button().setImage_(self._status_images["idle"])

        self._ticker = _Ticker.alloc().initWithHud_(self)

        # Een extern scherm in- of uitpluggen terwijl we draaien maakt het paneel
        # ongeldig; zonder deze notificatie blijft de pill onvindbaar tot een
        # herstart. Zie _rebuild_panel voor het waarom. Levert op de main thread.
        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            self._ticker, "screensChanged:",
            NSApplicationDidChangeScreenParametersNotification, None)

        # Klik op het icoon opent ons eigen paneel (panel.py), niet de kale
        # systeem-NSMenu. De knop stuurt de klik naar _Ticker.togglePopover_.
        self._panel = panel.MenuPanel.alloc().initWithHud_ticker_(self, self._ticker)
        btn = self.status.button()
        btn.setTarget_(self._ticker)
        btn.setAction_("togglePopover:")
        # Op mouse-DOWN vuren, niet -up: een transient popover sluit al bij de
        # muisklik-neer buiten z'n content. Vuurde de toggle pas op mouse-up, dan
        # zag 'ie de popover als "dicht" en heropende 'm meteen — klikken-om-te-
        # sluiten werkte dan nooit. Op mouse-down klopt isShown() nog wél.
        btn.sendActionOn_(NSEventMaskLeftMouseDown)

        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1 / 30.0, self._ticker, "tick:", None, True)

        # Auto-update draait op een eigen thread: git fetch is netwerk-I/O en mag
        # de run loop (Fn-tap + pill) nooit blokkeren. Schrijft alleen Hud-state.
        threading.Thread(target=self._update_loop, daemon=True).start()

    def _build_panel(self):
        """Maakt het pill-paneel aan tegen de HUIDIGE schermtopologie. Apart van
        build() zodat _rebuild_panel het na een schermwissel kan hermaken."""
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

    def _rebuild_panel(self):
        """
        Displays veranderd: bouw het paneel opnieuw op. Een NSPanel die is
        aangemaakt terwijl er nog een extern scherm hing, blijft na het
        loskoppelen 'verweesd' op dat verdwenen scherm -- orderFrontRegardless()
        met een setFrameOrigin_ naar geldige coordinaten toont hem dan niet meer
        op het scherm dat overblijft. Een vers paneel tegen de huidige topologie
        werkt wel. (Getest: extern scherm los -> pill onvindbaar tot dit draait;
        met deze herbouw komt hij terug zonder de daemon te herstarten.)

        Draait op de main thread: NSApplication levert deze notificatie op de run
        loop waar ook de 30 fps-timer en de Fn-tap aan hangen, dus geen race met
        _on_tick dat hetzelfde paneel leest.
        """
        shown = self._shown not in (None, "idle")
        if self.panel is not None:
            self.panel.orderOut_(None)
        self._build_panel()
        if shown:
            self._place()
            self.panel.orderFrontRegardless()

    def _place(self):
        """
        Put the pill where the user is looking. Called at the moment the pill
        appears (idle -> visible); the caret cannot move while Fn is held.

        We rebuild the panel from scratch here every time, not just move it. A
        panel created on a display that was later unplugged stays orphaned on
        that gone display: setFrameOrigin_ + orderFrontRegardless then place it
        nowhere you can see. A fresh panel against the current screen always
        renders, and building one costs next to nothing. This is the belt to the
        screen-change observer's braces: even if that notification never arrives,
        every dictation still gets a panel that shows.
        """
        if self.panel is not None:
            self.panel.orderOut_(None)
        self._build_panel()
        origin, _ = placement()
        self.panel.setFrameOrigin_(origin)

    def _on_tick(self):
        with self._lock:
            state = self._state
            if state == "done" and time.monotonic() > self._done_until:
                state = self._state = "idle"

        # De menubalk toont de status altijd; de zwevende pill is een aparte
        # voorkeur (show_pill), zodat je 'm uit kunt zetten en tóch het icoon houdt.
        show_pill = settings.get("show_pill")

        if state != self._shown:
            was = self._shown
            self._shown = state
            self.status.button().setImage_(self._status_images[state])
            if state == "idle" or not show_pill:
                self.panel.orderOut_(None)
            else:
                if was in (None, "idle"):
                    self._place()
                # never makeKeyAndOrderFront: that would steal focus from the app
                # we are about to paste into.
                self.panel.orderFrontRegardless()

        if state != "idle" and show_pill:
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
