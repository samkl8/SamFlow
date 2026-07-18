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

Daarnaast de gedeelde layout-bouwstenen (Flipped, label, section, separator,
row_label + maten) en GlyphView (het app-merkje). Die woonden eerst in prefs.py
resp. panel.py; ze staan hier zodat het losse Voorkeuren-venster, het menubalk-
paneel én het hoofdvenster (mainwindow.py) één en dezelfde bouwstenen delen --
één stijl, drie plekken.

Een leaf-module: importeert alleen AppKit + objc, nooit panel/prefs/mainwindow, dus
geen import-cyclus.
"""
import objc
from AppKit import (
    NSAnimationContext, NSApplication, NSAttributedString, NSBezierPath, NSBox,
    NSBoxSeparator, NSColor, NSControlStateValueOff, NSControlStateValueOn,
    NSFont, NSFontAttributeName, NSForegroundColorAttributeName,
    NSKernAttributeName, NSMakePoint, NSMakeRect, NSMenu,
    NSMutableAttributedString, NSTextAlignmentCenter, NSTextField, NSTimer,
    NSView,
)

import theme

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


# ---------- gedeelde layout-maten ----------
# Eén set maten voor alle "instellingen-achtige" kolommen: het losse Voorkeuren-
# venster (prefs.py) en de tabs van het hoofdvenster (mainwindow.py) bouwen
# allemaal op deze breedte, zodat de prefs-view 1:1 in een tab past.
W = 470          # contentbreedte van een instellingen-kolom
PAD = 22         # marge links/rechts
ROW_H = 46       # hoogte van een instelrij
SEC_GAP = 22     # ruimte tussen twee secties


class Flipped(NSView):
    """Een view met de oorsprong linksboven, zodat we top-down layouten i.p.v. in
    Cocoa's y-omhoog-coordinaten. Gedeeld door prefs en mainwindow."""
    def isFlipped(self):
        return True


class FillView(Flipped):
    """Een flipped view die zichzelf vult met een (adaptieve) NSColor-token, met
    optionele hoekradius. Tekent in drawRect_ (niet via een layer-CGColor), zodat de
    kleur vanzelf met licht/donker meewisselt. De achtergrond-bouwsteen voor kaartjes
    en chips (vervangt de losse layer-achtergronden)."""
    def initWithFrame_color_radius_(self, frame, color, radius):
        self = objc.super(FillView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._color = color
        self._radius = radius
        return self

    def drawRect_(self, _r):
        b = self.bounds()
        self._color.set()
        if self._radius > 0:
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                b, self._radius, self._radius).fill()
        else:
            NSBezierPath.bezierPathWithRect_(b).fill()


def fill(frame, color, radius=0):
    return FillView.alloc().initWithFrame_color_radius_(frame, color, radius)


def label(text, size=13, weight="regular", color=None):
    f = NSTextField.labelWithString_(text)
    font = (NSFont.systemFontOfSize_(size) if weight == "regular"
            else NSFont.boldSystemFontOfSize_(size) if weight == "bold"
            else NSFont.systemFontOfSize_weight_(size, 0.3))
    f.setFont_(font)
    f.setTextColor_(color if color is not None else theme.TEXT)
    return f


def section(view, y, title):
    lbl = label(title.upper(), size=11, color=theme.FAINT)
    lbl.setFrame_(NSMakeRect(PAD + 2, y, W - 2 * PAD, 16))
    view.addSubview_(lbl)
    return y + 22


def separator(view, y):
    box = NSBox.alloc().initWithFrame_(NSMakeRect(PAD, y, W - 2 * PAD, 1))
    box.setBoxType_(NSBoxSeparator)
    view.addSubview_(box)


def row_label(view, y, title, sub=None):
    lbl = label(title, size=13)
    if sub:
        lbl.setFrame_(NSMakeRect(PAD, y + 6, W - 2 * PAD - 120, 18))
        s = label(sub, size=11, color=theme.TEXT2)
        s.setFrame_(NSMakeRect(PAD, y + 24, W - 2 * PAD - 120, 15))
        view.addSubview_(s)
    else:
        lbl.setFrame_(NSMakeRect(PAD, y + (ROW_H - 20) / 2, W - 2 * PAD - 120, 20))
    view.addSubview_(lbl)


def mono(text, size=12, weight="regular", color=None):
    """Een label in het monospaced systeemlettertype -- voor termen, tijden en
    fonetische bronwoorden (mockup: .term/.tm/.src). Valt terug op het gewone
    systeemfont als de monospaced-API ontbreekt."""
    f = NSTextField.labelWithString_(text)
    try:
        wt = 0.3 if weight in ("medium", "bold") else 0.0
        font = NSFont.monospacedSystemFontOfSize_weight_(size, wt)
    except Exception:
        font = NSFont.systemFontOfSize_(size)
    f.setFont_(font)
    f.setTextColor_(color if color is not None else theme.TEXT)
    return f


def glabel(view, x, y, w, title, sub=None):
    """De sectiekop uit de mockup (.glabel): klein, HOOFDLETTERS, gespatieerd, faint,
    met een optionele subtitel in gewone schrijfwijze ernaast ('· vaak gezegd'). Eén
    label met twee opmaak-runs. Geeft de y ná de kop terug."""
    lbl = NSTextField.labelWithString_("")
    s = NSMutableAttributedString.alloc().init()
    tfont = NSFont.systemFontOfSize_weight_(10.5, 0.3)
    s.appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(
        title.upper(), {NSFontAttributeName: tfont,
                        NSForegroundColorAttributeName: theme.FAINT,
                        NSKernAttributeName: 0.7}))
    if sub:
        s.appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(
            "   " + sub, {NSFontAttributeName: NSFont.systemFontOfSize_(10.5),
                          NSForegroundColorAttributeName: theme.FAINT}))
    lbl.setAttributedStringValue_(s)
    lbl.setFrame_(NSMakeRect(x, y, w, 15))
    view.addSubview_(lbl)
    return y + 22


def hline(x, y, w, color=None):
    """Een haarlijn (0.5px logisch, hier 1px) -- de scheiding tussen rijen in een
    gegroepeerde kaart. Adaptief via een token, dus wisselt mee met licht/donker."""
    return fill(NSMakeRect(x, y, w, 1), color if color is not None else theme.LINE, 0)


def card_group(view, x, y, w, row_heights, filler, radius=12):
    """De gegroepeerde rijen-kaart uit de mockup (.rows/.group): één SUNKEN-vlak met
    haarlijnen tússen de rijen. `filler(container, idx, row_top, row_w, row_h)` vult
    elke rij (lokale coördinaten binnen de kaart). Geeft de y ná de kaart terug.

    Zo delen de historie-dag-groepen, de woordenlijst-secties en de instellingen-
    groepen precies dezelfde anatomie -- één stijl, meerdere plekken."""
    total = sum(row_heights)
    card = fill(NSMakeRect(x, y, w, total), theme.SUNKEN, radius)
    ry = 0
    for idx, rh in enumerate(row_heights):
        if idx > 0:
            card.addSubview_(hline(12, ry, w - 24))
        filler(card, idx, ry, w, rh)
        ry += rh
    view.addSubview_(card)
    return y + total


def flash_copied(button, revert_title="Kopieer"):
    """Korte kopieer-bevestiging op een borderless NSButton: de titel wordt heel
    even '✓ Gekopieerd' in groen, faded in, en keert na ~1,3 s terug. Zo weet je dat
    de klik aankwam (de knop deed anders zichtbaar niets). Gedeeld door de historie-
    lijst (mainwindow) en het menubalk-paneel (via hud). Main thread only."""
    green = NSColor.colorWithCalibratedRed_green_blue_alpha_(*ON, 1.0)
    button.setTitle_("✓ Gekopieerd")
    try:
        button.setContentTintColor_(green)
    except Exception:
        pass
    button.setAlphaValue_(0.0)
    NSAnimationContext.beginGrouping()
    NSAnimationContext.currentContext().setDuration_(0.2)
    button.animator().setAlphaValue_(1.0)
    NSAnimationContext.endGrouping()

    def _revert(_timer):
        button.setTitle_(revert_title)
        button.setAlphaValue_(1.0)   # vangnet: nooit onzichtbaar blijven
        try:
            button.setContentTintColor_(NSColor.secondaryLabelColor())
        except Exception:
            pass

    try:
        NSTimer.scheduledTimerWithTimeInterval_repeats_block_(1.3, False, _revert)
    except Exception:
        _revert(None)


class Segmented(NSView):
    """Een zelf-getekende segmented control in de Helder-taal (mockup .segc): een
    chip-vlak met per segment een label; het geselecteerde segment krijgt een licht
    verhoogd wit pilletje. Quackt als NSSegmentedControl -- selectedSegment() geeft de
    index -- en stuurt bij een klik de action naar de target, zodat de bestaande
    change*-handlers in prefs.py ongewijzigd blijven werken (één control wisselt, niet
    de handler). De breedte volgt uit de labels, dus tekst kapt nooit af."""
    def initWithLabels_selected_target_action_(self, labels, sel, target, action):
        seg_pad, gap, pad_out, h = 11, 2, 2, 26
        widths = []
        for t in labels:
            m = label(t, 12, "medium")
            m.sizeToFit()
            widths.append(m.frame().size.width + 2 * seg_pad)
        total = 2 * pad_out + sum(widths) + gap * (len(labels) - 1)
        self = objc.super(Segmented, self).initWithFrame_(NSMakeRect(0, 0, total, h))
        if self is None:
            return None
        self._sel = sel
        self._target = target
        self._action = action
        self._rects = []
        self._lbls = []
        x = pad_out
        for i, t in enumerate(labels):
            w = widths[i]
            self._rects.append((x, w))
            lb = label(t, 12, "medium", color=(theme.TEXT if i == sel else theme.TEXT2))
            lb.setAlignment_(NSTextAlignmentCenter)
            lb.setFrame_(NSMakeRect(x, (h - 15) / 2, w, 15))
            self.addSubview_(lb)
            self._lbls.append(lb)
            x += w + gap
        return self

    def selectedSegment(self):
        return self._sel

    def isFlipped(self):
        return True

    def acceptsFirstMouse_(self, _ev):
        return True

    def mouseDown_(self, ev):
        p = self.convertPoint_fromView_(ev.locationInWindow(), None)
        for i, (x, w) in enumerate(self._rects):
            if x <= p.x <= x + w and i != self._sel:
                self._sel = i
                for j, lb in enumerate(self._lbls):
                    lb.setTextColor_(theme.TEXT if j == i else theme.TEXT2)
                self.setNeedsDisplay_(True)
                if self._action and self._target:
                    NSApplication.sharedApplication().sendAction_to_from_(
                        self._action, self._target, self)
                break

    def drawRect_(self, _r):
        b = self.bounds()
        cont = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(0.5, 0.5, b.size.width - 1, b.size.height - 1), 8, 8)
        theme.CHIP.set()
        cont.fill()
        theme.LINE.set()
        cont.setLineWidth_(0.5)
        cont.stroke()
        x, w = self._rects[self._sel]
        pill = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(x, 2, w, b.size.height - 4), 6, 6)
        theme.WINDOW.set()
        pill.fill()
        theme.LINE.set()
        pill.setLineWidth_(0.5)
        pill.stroke()


class Dropdown(NSView):
    """Een Helder-dropdown (mockup .drop): een afgerond window-vlak met het huidige
    label en een zelf-getekende chevron; een klik opent een NSMenu met de opties.
    Quackt als NSSegmentedControl -- selectedSegment() geeft de gekozen index -- en
    stuurt bij een keuze de action naar de target, zodat de bestaande change*-handlers
    in prefs.py ongewijzigd blijven werken (net als Segmented: één control wisselt,
    niet de handler). De breedte volgt uit het bréédste label, zodat een keuze de rij
    nooit laat verspringen."""
    def initWithLabels_selected_target_action_(self, labels, sel, target, action):
        pad, h = 11, 26
        wmax = 0.0
        for t in labels:
            m = label(t, 12.5, "medium")
            m.sizeToFit()
            wmax = max(wmax, m.frame().size.width)
        total = pad + wmax + 8 + 9 + pad
        self = objc.super(Dropdown, self).initWithFrame_(NSMakeRect(0, 0, total, h))
        if self is None:
            return None
        self._pad = pad
        self._labels = list(labels)
        self._sel = sel if 0 <= sel < len(labels) else 0
        self._target = target
        self._action = action
        self._lbl = label(self._labels[self._sel], 12.5, "medium")
        self._lbl.setFrame_(NSMakeRect(pad, (h - 16) / 2, wmax, 16))
        self.addSubview_(self._lbl)
        return self

    def selectedSegment(self):
        return self._sel

    def isFlipped(self):
        return True

    def acceptsFirstMouse_(self, _ev):
        return True

    def mouseDown_(self, _ev):
        menu = NSMenu.alloc().init()
        for i, t in enumerate(self._labels):
            it = menu.addItemWithTitle_action_keyEquivalent_(t, "pick:", "")
            it.setTarget_(self)
            it.setTag_(i)
            if i == self._sel:
                it.setState_(NSControlStateValueOn)
        b = self.bounds()
        menu.popUpMenuPositioningItem_atLocation_inView_(
            None, NSMakePoint(0, b.size.height + 3), self)

    def pick_(self, sender):
        i = sender.tag()
        if not (0 <= i < len(self._labels)) or i == self._sel:
            return
        self._sel = i
        self._lbl.setStringValue_(self._labels[i])
        if self._action and self._target:
            NSApplication.sharedApplication().sendAction_to_from_(
                self._action, self._target, self)

    def drawRect_(self, _r):
        b = self.bounds()
        rr = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(0.5, 0.5, b.size.width - 1, b.size.height - 1), 7, 7)
        theme.WINDOW.set()
        rr.fill()
        theme.LINE2.set()
        rr.setLineWidth_(0.5)
        rr.stroke()
        # chevron (zelf getekend, geen glyph-onzekerheid): een dunne 'v' rechts, faint.
        # Flipped view: de punt ligt bij grotere y (omlaag).
        cw = 9.0
        cx = b.size.width - self._pad - cw
        cy = b.size.height / 2
        theme.FAINT.set()
        chev = NSBezierPath.bezierPath()
        chev.moveToPoint_(NSMakePoint(cx, cy - 2))
        chev.lineToPoint_(NSMakePoint(cx + cw / 2, cy + 3))
        chev.lineToPoint_(NSMakePoint(cx + cw, cy - 2))
        chev.setLineWidth_(1.3)
        chev.stroke()


class GlyphView(NSView):
    """Het app-merkje: lichte equalizer-balkjes op een donker afgerond vierkant --
    grafiet+wit, gelijk aan het app-icoon. Statisch (de status leeft in de stip
    ernaast), puur identiteit. Gedeeld door het menubalk-paneel (panel.py) en het
    hoofdvenster (mainwindow.py); de balk-hoogtes schalen mee met de tegelgrootte."""
    def drawRect_(self, _rect):
        b = self.bounds()
        NSColor.colorWithCalibratedWhite_alpha_(0.09, 1.0).set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(b, 9, 9).fill()
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.94, 0.94, 0.95, 1.0).set()
        heights = [0.42, 0.72, 1.00, 0.60]
        bw, gap = 3.0, 2.6
        total = len(heights) * bw + (len(heights) - 1) * gap
        x = (b.size.width - total) / 2
        for hh in heights:
            bh = 5.0 + (b.size.height - 15.0) * hh
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(x, (b.size.height - bh) / 2, bw, bh), bw / 2, bw / 2).fill()
            x += bw + gap
