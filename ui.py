"""
ui.py - kleine gedeelde UI-bouwstenen in SamFlow's eigen stijl (Helder).

Bevat Toggle: een aan/uit-schakelaar in groen (de "aan"-kleur, zelfde tint als de
"klaar"-stip) i.p.v. NSSwitch. NSSwitch volgt in de aan-stand de macOS-accentkleur
(blauw) en die is met de publieke API niet netjes te overrulen -- vandaar een eigen,
zelf-getekende control.

De Toggle "quackt" bewust als een NSSwitch: state()/tag()/setState_/setTag_/
setTarget_/setAction_, en stuurt bij een klik de action naar de target. Zo blijven
de bestaande `toggleSwitch:`-handlers in panel.py en prefs.py ongewijzigd werken --
alleen de control zelf wisselt.

Een leaf-module: importeert alleen AppKit + objc, nooit panel/prefs, dus geen
import-cyclus.
"""
import objc
from AppKit import (
    NSApplication, NSBezierPath, NSColor, NSControlStateValueOff,
    NSControlStateValueOn, NSMakeRect, NSView,
)

ON = (0.20, 0.72, 0.35)               # #33B859 -- "aan"-kleur (zelfde groen als de "klaar"-stip)


class Toggle(NSView):
    """Aan/uit-schakelaar in klei; NSSwitch-compatibel (zie module-docstring)."""

    def init(self):
        self = objc.super(Toggle, self).init()
        if self is None:
            return None
        self._on = False
        self._tag = 0
        self._target = None
        self._action = None
        return self

    # -- NSSwitch-compatibele API --
    def state(self):
        return NSControlStateValueOn if self._on else NSControlStateValueOff

    def setState_(self, s):
        self._on = (s == NSControlStateValueOn)
        self.setNeedsDisplay_(True)

    def tag(self):
        return self._tag

    def setTag_(self, t):
        self._tag = t

    def setTarget_(self, t):
        self._target = t

    def setAction_(self, a):
        self._action = a

    # -- interactie --
    def acceptsFirstMouse_(self, _ev):
        return True

    def mouseDown_(self, _ev):
        self._on = not self._on
        self.setNeedsDisplay_(True)
        if self._action is not None and self._target is not None:
            NSApplication.sharedApplication().sendAction_to_from_(
                self._action, self._target, self)

    # -- tekenen --
    def drawRect_(self, _rect):
        b = self.bounds()
        h = b.size.height
        track = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(b, h / 2, h / 2)
        if self._on:
            NSColor.colorWithCalibratedRed_green_blue_alpha_(*ON, 1.0).set()
        else:
            NSColor.tertiaryLabelColor().set()
        track.fill()
        d = h - 6
        kx = (b.size.width - d - 3) if self._on else 3
        NSColor.whiteColor().set()
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(kx, 3, d, d)).fill()
