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
    done       a green tick that draws itself on, then back to hidden.

De pill teleporteert niet: 'ie veert in beeld (entrance-spring), de glyphs
cross-faden tussen de fasen, het vinkje tekent zichzelf met een korte groene
flash, en bij 'idle' krimpt de pill weg. De pill zit in een iets groter,
transparant venster (PILL + PAD rondom) zodat de schaduw en de overshoot niet
tegen de vensterrand clippen. De plaatsing rekent in pill-coordinaten; het
venster schuift daarna simpelweg PAD naar linksonder.

The window is a non-activating panel. That matters: if it ever took focus, the
Cmd+V that follows would paste into the pill instead of into your editor.

Every AppKit call has to happen on the main thread, so nothing here draws
directly. Background threads only write to Hud.state / Hud.level, and a 60 fps
timer on the main thread reads them and redraws (60 i.p.v. 30 voor soepele
springs; de mini-view is spotgoedkoop om te tekenen).
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
    NSAffineTransform, NSApplication,
    NSApplicationDidChangeScreenParametersNotification, NSBackingStoreBuffered,
    NSBezierPath, NSColor, NSEventMaskLeftMouseDown, NSGraphicsContext, NSImage,
    NSLineCapStyleRound, NSLineJoinStyleRound, NSMakeRect, NSPanel, NSPasteboard,
    NSPasteboardTypeString, NSScreen, NSShadow, NSStatusBar, NSTimer, NSView,
    NSWindowCollectionBehaviorCanJoinAllSpaces, NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary, NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)
from Foundation import NSNotificationCenter, NSObject

import appmode
import focus
import panel
import prefs
import settings
import telemetry
import updater

# Eerste update-check kort na opstart (netwerk moet er zijn), daarna elke 6 uur.
UPDATE_FIRST_DELAY_SEC = 8
UPDATE_INTERVAL_SEC = 6 * 3600

# ---------- config ----------
PILL_W, PILL_H = 92.0, 30.0     # het zichtbare grafiet-vlak -- ongewijzigd
PAD = 20.0                      # transparante rand rond de pill: ruimte voor de
                                # schaduw en de entrance-overshoot/exit-krimp
WIN_W, WIN_H = PILL_W + 2 * PAD, PILL_H + 2 * PAD
# De plaatsing komt uit de instelling `pill_position` (caret/bottom/fixed) -- zie
# placement(). Per dictaat herlezen, dus wisselen in de voorkeuren werkt meteen.
CARET_GAP = 12.0           # points between the caret and the pill
SCREEN_MARGIN = 12.0       # never sit flush against a screen edge
PILL_BOTTOM = 130.0        # fallback height above the bottom of the screen
BARS = 5
BAR_W, BAR_GAP = 5.5, 5.5       # "Fors" -- forsere, beter leesbare staafjes
BAR_MIN, BAR_MAX = 4.5, 22.0    # hogere ondergrens: zacht praten blijft zichtbaar
DONE_FLASH_SEC = 0.85
FPS = 60.0
WINDOW_LEVEL = 25          # NSStatusWindowLevel: above normal windows, below alerts

# --- motion ("Soepel") -- gekozen in de pill-animations mockup ---
ANIM_IN = 0.26            # entrance: schaal 0.82->1 met veer-overshoot
ANIM_OUT = 0.16           # exit: schaal 1->0.9 + fade
ANIM_MORPH = 0.14         # cross-fade tussen twee glyphs
ANIM_TICK = 0.50          # het vinkje tekent zichzelf
SCALE_IN, SCALE_OUT = 0.82, 0.90
DRIFT_IN, DRIFT_OUT = 7.0, 5.0  # px die de pill in-/wegdrijft (omhoog / omlaag)
EASE_BACK = 1.70158       # overshoot-sterkte van de entrance-veer
# ----------------------------

# Grootte en beweging zijn instelbaar (voorkeuren-venster). De constanten hierboven
# zijn de default ("Fors" + "Soepel"); _apply_style() overschrijft ze uit de
# instellingen zodra de pill verschijnt. Elke preset past binnen PILL_H + PAD, dus
# geen clipping -- geverifieerd bij het kiezen van deze waarden.
BAR_PRESETS = {              # (BAR_W, BAR_GAP, BAR_MIN, BAR_MAX)
    "compact": (3.5, 4.5, 3.0, 15.0),
    "ruim":    (4.5, 5.5, 4.0, 20.0),
    "fors":    (5.5, 5.5, 4.5, 22.0),
}
MOTION_PRESETS = {           # (ANIM_IN, ANIM_OUT, ANIM_MORPH, ANIM_TICK, SCALE_IN, SCALE_OUT, DRIFT_IN, DRIFT_OUT, EASE_BACK)
    "soepel": (0.26, 0.16, 0.14, 0.50, 0.82, 0.90, 7.0, 5.0, 1.70158),
    "kwiek":  (0.16, 0.11, 0.10, 0.38, 0.85, 0.92, 5.0, 4.0, 2.20000),
}

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


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _ease_out_cubic(p: float) -> float:
    q = 1.0 - _clamp01(p)
    return 1.0 - q * q * q


def _ease_out_back(p: float, s: float = 1.70158) -> float:
    # veert net voorbij 1 en zakt terug -- de "spring" van de entrance
    p = _clamp01(p) - 1.0
    return 1.0 + (s + 1.0) * p ** 3 + s * p ** 2


def _smooth(shown: float, target: float, dt: float, tau: float) -> float:
    # frame-rate-onafhankelijke demping richting target (tijdconstante tau seconden)
    if tau <= 0.0 or dt <= 0.0:
        return target
    return shown + (target - shown) * (1.0 - math.exp(-dt / tau))


def _apply_style():
    """Zet de teken-constanten uit de instellingen `pill_size` en `pill_motion`.
    Aangeroepen wanneer de pill verschijnt, zodat een wissel in de voorkeuren geldt
    vanaf het volgende dictaat -- zonder herstart. Onbekende waarde -> de default."""
    global BAR_W, BAR_GAP, BAR_MIN, BAR_MAX
    global ANIM_IN, ANIM_OUT, ANIM_MORPH, ANIM_TICK
    global SCALE_IN, SCALE_OUT, DRIFT_IN, DRIFT_OUT, EASE_BACK
    BAR_W, BAR_GAP, BAR_MIN, BAR_MAX = BAR_PRESETS.get(
        settings.get("pill_size"), BAR_PRESETS["fors"])
    (ANIM_IN, ANIM_OUT, ANIM_MORPH, ANIM_TICK,
     SCALE_IN, SCALE_OUT, DRIFT_IN, DRIFT_OUT, EASE_BACK) = MOTION_PRESETS.get(
        settings.get("pill_motion"), MOTION_PRESETS["soepel"])


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


def _fixed_screen():
    """Het scherm waar je wérkt (van de actieve app), voor de vaste plaatsings-
    standen. Zo verschijnt de pill ook bij 'onderin'/'vaste hoek' op de monitor
    waar je bezig bent, niet altijd op het hoofdscherm. Valt terug op het
    hoofdscherm als het anker niets prijsgeeft."""
    try:
        (x, y_top, w, h), _ = focus.target_rect()
        return focus.screen_for(*focus.point_to_cocoa(x + w / 2, y_top + h / 2))
    except Exception:
        return NSScreen.mainScreen()


def placement() -> tuple:
    """
    Waar komt de pill? Pure functie: geen venster nodig, dus los te bevragen met
    `python hud.py --where`. Geeft ((x, y) in Cocoa-coordinaten van de PILL, niet
    het venster; het venster schuift daarna PAD naar linksonder) plus uitleg-dict.

    De stand komt uit de instelling `pill_position` (per dictaat herlezen):
        caret   volg waar je typt -- caret, anders venster/muis. De default.
        bottom  vaste balk onderin-midden, op het scherm waar je werkt.
        fixed   vaste hoek (rechtsonder), op het scherm waar je werkt.
    """
    mode = settings.get("pill_position")

    if mode == "bottom":
        screen = _fixed_screen()
        frame = screen.frame()
        return _bottom_centre(screen), {
            "anker": "onderin", "scherm": focus.screen_index(screen),
            "scherm_frame": (frame.origin.x, frame.origin.y, frame.size.width, frame.size.height)}

    if mode == "fixed":
        screen = _fixed_screen()
        frame = screen.frame()
        origin = (frame.origin.x + frame.size.width - PILL_W - SCREEN_MARGIN,
                  frame.origin.y + SCREEN_MARGIN)
        return origin, {
            "anker": "vaste-hoek", "scherm": focus.screen_index(screen),
            "scherm_frame": (frame.origin.x, frame.origin.y, frame.size.width, frame.size.height)}

    # mode == "caret" (default, of onbekende waarde): volg waar je typt
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
    """Draws the pill. Reads hud.snapshot(), never writes state.

    Alle beweging (entrance, exit, cross-fade, het tekenende vinkje, de flash)
    wordt hier per frame uit klok-tijdstippen berekend -- de view blijft de enige
    plek die tekent. Het venster is niet-opaak, dus AppKit wist het vlak elk frame
    vanzelf: geen sporen van vorige balk-hoogtes.
    """

    def initWithHud_(self, hud):
        self = objc.super(_PillView, self).initWithFrame_(
            NSMakeRect(0, 0, WIN_W, WIN_H))
        self.hud = hud
        return self

    def drawRect_(self, _rect):
        p = self.hud.snapshot()
        now = p["now"]

        # --- entrance / exit: schaal, drift en alpha voor de hele pill ---
        if p["exiting"]:
            e = _ease_out_cubic((now - p["exit_t0"]) / ANIM_OUT)
            scale = 1.0 + (SCALE_OUT - 1.0) * e
            drift = DRIFT_OUT * e
            alpha = 1.0 - e
        else:
            q = (now - p["appear_t0"]) / ANIM_IN
            scale = SCALE_IN + (1.0 - SCALE_IN) * _ease_out_back(q, EASE_BACK)
            drift = -DRIFT_IN * (1.0 - _ease_out_cubic(q))    # start lager, veert omhoog
            alpha = _ease_out_cubic((now - p["appear_t0"]) / (ANIM_IN * 0.7))
        if alpha <= 0.0:
            return

        cx = PAD + PILL_W / 2.0
        cy = PAD + PILL_H / 2.0 + drift
        ctx = NSGraphicsContext.currentContext()
        ctx.saveGraphicsState()
        t = NSAffineTransform.transform()
        t.translateXBy_yBy_(cx, cy)
        t.scaleBy_(scale)
        t.translateXBy_yBy_(-PILL_W / 2.0, -PILL_H / 2.0)
        t.concat()
        self._draw_pill(p, now, alpha)
        ctx.restoreGraphicsState()

    def _draw_pill(self, p, now, alpha):
        state = p["state"]

        # groene flash-intensiteit op 'klaar': snel op, langzaam weg
        flash = 0.0
        if state == "done":
            td = now - p["done_t0"]
            flash = td / 0.10 if td < 0.10 else max(0.0, 1.0 - (td - 0.10) / 0.55)

        backdrop = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(0, 0, PILL_W, PILL_H), PILL_H / 2, PILL_H / 2)

        # grafiet #1E1E22 met schaduw; tijdens de flash een groene zweem erdoorheen
        ctx = NSGraphicsContext.currentContext()
        ctx.saveGraphicsState()
        sh = NSShadow.alloc().init()
        sh.setShadowColor_(NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.30 * alpha))
        sh.setShadowOffset_((0.0, -4.0))
        sh.setShadowBlurRadius_(11.0)
        sh.set()
        f = flash * 0.6
        br, bg, bb = 0.118, 0.118, 0.133
        gr, gg, gb = 0.10, 0.22, 0.14
        NSColor.colorWithCalibratedRed_green_blue_alpha_(
            br + (gr - br) * f, bg + (gg - bg) * f, bb + (gb - bb) * f, 0.90 * alpha).set()
        backdrop.fill()
        ctx.restoreGraphicsState()   # schaduw weer uit voor de randen en glyphs

        if flash > 0.01:             # even een groene gloed-rand op 'klaar'
            gr_, gg_, gb_ = _GREEN
            NSColor.colorWithCalibratedRed_green_blue_alpha_(
                gr_, gg_, gb_, 0.85 * flash * alpha).set()
            backdrop.setLineWidth_(1.5)
            backdrop.stroke()
        NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.10 * alpha).set()
        backdrop.setLineWidth_(1.0)
        backdrop.stroke()

        # --- glyphs met cross-fade tussen de vorige en de huidige fase ---
        prev = p["prev"]
        morph = _clamp01((now - p["state_t0"]) / ANIM_MORPH)
        if morph < 1.0 and prev != state and prev in ("recording", "thinking", "done"):
            self._draw_glyph(prev, alpha * (1.0 - morph), p, now)
        self._draw_glyph(state, alpha * (morph if prev != state else 1.0), p, now)

    def _draw_glyph(self, state, a, p, now):
        if a <= 0.0:
            return
        if state == "recording":
            self._draw_bars(p["level"], now, a)
        elif state == "thinking":
            self._draw_dots(now, a)
        elif state == "done":
            self._draw_tick(now, p["done_t0"], a)

    def _draw_bars(self, level, t, a):
        r, g, b = _WHITE
        NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, a).set()
        total = BARS * BAR_W + (BARS - 1) * BAR_GAP
        x = (PILL_W - total) / 2
        for i in range(BARS):
            # a little phase offset per bar so a steady tone still looks alive
            wobble = 0.72 + 0.28 * math.sin(t * 6.0 + i * 1.1)
            h = BAR_MIN + (BAR_MAX - BAR_MIN) * level * wobble
            y = (PILL_H - h) / 2
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(x, y, BAR_W, h), BAR_W / 2, BAR_W / 2).fill()
            x += BAR_W + BAR_GAP

    def _draw_dots(self, t, a):
        d, gap = 6.0, 8.0
        x = (PILL_W - (3 * d + 2 * gap)) / 2
        r, g, b = _WHITE
        for i in range(3):
            alpha = 0.30 + 0.70 * (0.5 + 0.5 * math.sin(t * 4.0 - i * 0.8))
            NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, alpha * a).set()
            NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(x, (PILL_H - d) / 2, d, d)).fill()
            x += d + gap

    def _draw_tick(self, now, done_t0, a):
        # het vinkje tekent zichzelf: teken de polyline tot een groeiend deel van
        # de totale lengte (draw-on), met een ease-out zodat 't afremt bij de punt.
        q = _ease_out_cubic((now - done_t0) / ANIM_TICK)
        r, g, b = _GREEN
        NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, a).set()
        cx, cy = PILL_W / 2, PILL_H / 2
        s = PILL_H / 46.0          # schaalt mee met de pill-hoogte, blijft proportioneel
        a0 = (cx - 9 * s, cy + 1 * s)
        b0 = (cx - 3 * s, cy - 6 * s)
        c0 = (cx + 10 * s, cy + 8 * s)
        l1 = math.hypot(b0[0] - a0[0], b0[1] - a0[1])
        l2 = math.hypot(c0[0] - b0[0], c0[1] - b0[1])
        d = q * (l1 + l2)
        path = NSBezierPath.bezierPath()
        path.setLineWidth_(2.5)
        path.setLineCapStyle_(NSLineCapStyleRound)
        path.setLineJoinStyle_(NSLineJoinStyleRound)
        path.moveToPoint_(a0)
        if d <= l1:
            f = d / l1 if l1 else 1.0
            path.lineToPoint_((a0[0] + (b0[0] - a0[0]) * f, a0[1] + (b0[1] - a0[1]) * f))
        else:
            path.lineToPoint_(b0)
            f = (d - l1) / l2 if l2 else 1.0
            path.lineToPoint_((b0[0] + (c0[0] - b0[0]) * f, b0[1] + (c0[1] - b0[1]) * f))
        path.stroke()


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
        # Open de Woordenlijst-tab van het hoofdvenster (de in-app editor: chips
        # toevoegen/verwijderen, correcties), niet meer lexicon.txt in een teksteditor.
        # Dat laatste deed niets bij een verse install zonder persoonlijke lijst -- het
        # bestand bestond dan nog niet, dus `open -t` faalde stil. Lui geïmporteerd,
        # net als openMainWindow_.
        import mainwindow
        mainwindow.open_main_window(self.hud).show_tab(2)

    def reviewWords_(self, _sender):
        # --review is interactief, dus in een echte Terminal, niet in dit proces.
        base = os.path.dirname(os.path.abspath(__file__))
        cmd = f"cd {shlex.quote(base)} && {shlex.quote(sys.executable)} samflow.py --review"
        subprocess.Popen([
            "osascript",
            "-e", 'tell application "Terminal" to activate',
            "-e", f'tell application "Terminal" to do script {json.dumps(cmd)}',
        ])

    def openMainWindow_(self, _sender):
        # Lui geïmporteerd: mainwindow importeert prefs/ui/audiodev/updater, geen van
        # allen importeert hud, dus geen cyclus -- maar lui houden is de veilige regel.
        import mainwindow
        mainwindow.open_main_window(self.hud)

    # App-delegate: in App-modus heropent een klik op het dock-icoon (zonder open
    # venster) het hoofdvenster. In Basic-modus is er geen dock-icoon, dus vuurt dit
    # nooit. True => macOS handelt de reopen verder standaard af.
    def applicationShouldHandleReopen_hasVisibleWindows_(self, _app, has_windows):
        if not has_windows:
            import mainwindow
            mainwindow.open_main_window(self.hud)
        return True

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

        # animatie-boekhouding -- alleen main-thread (_on_tick schrijft, drawRect
        # leest); geen lock nodig want beide draaien op de run loop.
        self._last_tick = 0.0
        self._level_shown = 0.0     # render-side gesmoothde mic-stand
        self._visible = False
        self._exiting = False
        self._draw_state = "idle"
        self._prev_draw_state = "idle"
        self._state_t0 = 0.0
        self._appear_t0 = 0.0
        self._exit_t0 = 0.0
        self._done_t0 = 0.0

    # --- called from any thread -------------------------------------------
    def set_state(self, state: str):
        with self._lock:
            self._state = state
            if state == "done":
                self._done_until = time.monotonic() + DONE_FLASH_SEC
            if state != "recording":
                self._level = 0.0

    def set_level(self, level: float):
        # alleen de ruwe doel-stand; het snel-omhoog/langzaam-terug-smoothen
        # gebeurt per frame in _on_tick (frame-rate-onafhankelijk, en zo blijft
        # de beweging een functie van de echte mic i.p.v. de audio-blokgrootte).
        with self._lock:
            self._level = max(0.0, min(level, 1.0))

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
            telemetry.maybe_send()   # dagelijkse heartbeat voor lang-draaiende daemons
            time.sleep(UPDATE_INTERVAL_SEC)

    def current_state(self):
        """De ruwe status (idle/recording/thinking/done) -- voor het menubalk-
        paneel, dat de status-stip erop kleurt. Los van snapshot(), dat de
        teken-toestand (draw-state + animatieklokken) voor de pill teruggeeft."""
        with self._lock:
            return self._state

    def snapshot(self):
        # Uitsluitend main-thread-velden -> geen lock nodig. _level_shown wordt in
        # _on_tick bijgewerkt (ook main thread), dus drawRect en _on_tick raken
        # elkaar nooit tegelijk.
        return {
            "state": self._draw_state,
            "prev": self._prev_draw_state,
            "level": self._level_shown,
            "state_t0": self._state_t0,
            "appear_t0": self._appear_t0,
            "exit_t0": self._exit_t0,
            "exiting": self._exiting,
            "done_t0": self._done_t0,
            "now": time.monotonic(),
        }

    # --- main thread only --------------------------------------------------
    def build(self):
        self.app = NSApplication.sharedApplication()
        # Basic (accessory) of App (regular, met dock-icoon) -- de opgeslagen modus.
        # Niet activeren bij het opstarten: dat zou focus stelen bij elke login.
        appmode.apply(activate=False)

        self._build_panel()

        self.status = NSStatusBar.systemStatusBar().statusItemWithLength_(-1.0)
        self._status_images = {s: _status_image(s)
                               for s in ("idle", "recording", "thinking", "done")}
        self.status.button().setImage_(self._status_images["idle"])

        self._ticker = _Ticker.alloc().initWithHud_(self)
        # In App-modus opent een klik op het dock-icoon het hoofdvenster weer. De
        # ticker is de app-delegate; alleen deze reopen-hook is geïmplementeerd, de
        # rest houdt het standaardgedrag (venster dicht stopt de app niet).
        self.app.setDelegate_(self._ticker)

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
            1 / FPS, self._ticker, "tick:", None, True)

        # Auto-update draait op een eigen thread: git fetch is netwerk-I/O en mag
        # de run loop (Fn-tap + pill) nooit blokkeren. Schrijft alleen Hud-state.
        threading.Thread(target=self._update_loop, daemon=True).start()

    def _build_panel(self):
        """Maakt het pill-paneel aan tegen de HUIDIGE schermtopologie. Apart van
        build() zodat _rebuild_panel het na een schermwissel kan hermaken. Het
        venster is WIN_W x WIN_H: de pill plus PAD rondom voor schaduw en veer."""
        screen = NSScreen.mainScreen().frame()
        frame = NSMakeRect((screen.size.width - WIN_W) / 2, PILL_BOTTOM - PAD, WIN_W, WIN_H)

        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered, False)
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(NSColor.clearColor())
        self.panel.setLevel_(WINDOW_LEVEL)
        self.panel.setIgnoresMouseEvents_(True)
        self.panel.setHidesOnDeactivate_(False)
        self.panel.setHasShadow_(False)   # we tekenen zelf een schaduw (schaalt mee)
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
        loop waar ook de fps-timer en de Fn-tap aan hangen, dus geen race met
        _on_tick dat hetzelfde paneel leest.
        """
        was_visible = self._visible
        if self.panel is not None:
            self.panel.orderOut_(None)
        self._build_panel()
        if was_visible:
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

        placement() geeft de PILL-oorsprong; het venster is PAD groter, dus schuif
        de venster-oorsprong PAD naar linksonder zodat de pill exact op z'n plek valt.
        """
        if self.panel is not None:
            self.panel.orderOut_(None)
        self._build_panel()
        origin, _ = placement()
        self.panel.setFrameOrigin_((origin[0] - PAD, origin[1] - PAD))

    def _on_tick(self):
        now = time.monotonic()
        with self._lock:
            dt = (now - self._last_tick) if self._last_tick else 1.0 / FPS
            self._last_tick = now
            state = self._state
            if state == "done" and now > self._done_until:
                state = self._state = "idle"
            # render-side smoothing: snel omhoog (mic pikt je op), langzaam terug
            tau = 0.045 if self._level > self._level_shown else 0.18
            self._level_shown = _smooth(self._level_shown, self._level, dt, tau)

        # De menubalk toont de status altijd; de zwevende pill is een aparte
        # voorkeur (show_pill), zodat je 'm uit kunt zetten en tóch het icoon houdt.
        show_pill = settings.get("show_pill")
        want_visible = state != "idle" and show_pill

        if state != self._shown:
            self._shown = state
            self.status.button().setImage_(self._status_images[state])

        # Glyph-wissel binnen een zichtbare pill -> start een cross-fade (en bij
        # 'done' de vinkje-teken- en flash-klok). Bij het vérschijnen zet het
        # entrance-blok hieronder de glyph juist vers op, dus dat slaan we hier over.
        if self._visible and not self._exiting \
                and state in ("recording", "thinking", "done") and state != self._draw_state:
            self._prev_draw_state = self._draw_state
            self._draw_state = state
            self._state_t0 = now
            if state == "done":
                self._done_t0 = now

        if want_visible:
            if self._exiting:
                # kwam terug tijdens het wegkrimpen: exit afbreken, opnieuw inveren
                self._exiting = False
                self._appear_t0 = now
            if not self._visible:
                # idle -> zichtbaar: pas de gekozen grootte/beweging toe, dan een vers
                # paneel op de juiste plek en de entrance starten
                _apply_style()
                self._place()
                self._visible = True
                self._exiting = False
                self._appear_t0 = now
                self._prev_draw_state = "idle"
                self._draw_state = state
                self._state_t0 = now
                if state == "done":
                    self._done_t0 = now
                # never makeKeyAndOrderFront: that would steal focus from the app
                # we are about to paste into.
                self.panel.orderFrontRegardless()
            self.panel.contentView().setNeedsDisplay_(True)
        else:
            if self._visible and not self._exiting:
                # zichtbaar -> weg: exit-animatie starten, pas daarna orderOut
                self._exiting = True
                self._exit_t0 = now
            if self._exiting:
                self.panel.contentView().setNeedsDisplay_(True)
                if now - self._exit_t0 >= ANIM_OUT:
                    self.panel.orderOut_(None)
                    self._visible = False
                    self._exiting = False

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
        for _ in range(3):
            hud.set_state("recording")
            for i in range(80):
                hud.set_level(abs(math.sin(i / 8.0)) * 0.9)
                time.sleep(1 / FPS)
            hud.set_state("thinking")
            time.sleep(1.4)
            hud.set_state("done")
            time.sleep(1.2)
            hud.set_state("idle")     # laat de exit-krimp zien voordat we opnieuw beginnen
            time.sleep(1.0)
        print("demo klaar, Ctrl-C om te stoppen")

    threading.Thread(target=demo, daemon=True).start()
    hud.run()
