"""
panel.py - het menubalk-paneel: een NSPopover met een eigen view i.p.v. de kale
systeem-NSMenu. Klik op het menubalk-icoon opent dit paneel.

Waarom een popover en geen menu: een NSMenu is een systeem-tekstlijst die je
nauwelijks kunt stylen. Een NSPopover host een gewone view, dus we hebben er de
volledige controle over -- status, snelle toggles en het laatste dictaat, in
SamFlow's eigen stijl. De iconen zijn SF Symbols (Apple's vector-set, template
zodat ze de tekstkleur aannemen), geen emoji.

Het paneel gebruikt de native popover-material, dus het schakelt vanzelf mee met
licht/donker. Het wordt vlak vóór tonen ververst (popoverWillShow_); zolang het
dicht is hoeven we niets bij te werken.

Alle AppKit-calls op de main thread: de popover opent door een klik op de
statusbar-knop (main thread) en de 30 fps-timer raakt dit paneel niet aan.
"""
import objc
from AppKit import (
    NSApplication,
    NSBezierPath, NSBox, NSBoxSeparator, NSButton, NSColor, NSFont,
    NSFontWeightRegular, NSImage, NSImageLeft,
    NSImageSymbolConfiguration, NSMakeRect, NSMinYEdge, NSPopover,
    NSPopoverBehaviorTransient, NSControlStateValueOff, NSControlStateValueOn,
    NSSwitch, NSTextAlignmentLeft, NSTextField, NSView, NSViewController,
)
from Foundation import NSObject
from Quartz import CGColorCreateGenericRGB

import settings

W = 300
PAD = 16

# Status -> (kleur, tekst). Lokaal gehouden i.p.v. uit hud geïmporteerd: hud
# importeert deze module, dus andersom zou een cyclus geven.
_STATE_RGB = {
    "idle": (0.30, 0.85, 0.45),        # groene "ready"-stip: klaar om te dicteren
    "recording": (1.00, 0.35, 0.32),
    "thinking": (0.45, 0.65, 1.00),
    "done": (0.30, 0.85, 0.45),
}
_STATE_LABEL = {
    "idle": "klaar — houd Fn ingedrukt",
    "recording": "aan het luisteren…",
    "thinking": "transcriberen…",
    "done": "geplakt ✓",
}


def _rgb(t, a=1.0):
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(t[0], t[1], t[2], a)


def _cg(t, a=1.0):
    """Een CGColor voor layer-achtergronden. Via Quartz i.p.v. NSColor.CGColor(),
    dat een ObjCPointerWarning geeft bij elke aanroep."""
    return CGColorCreateGenericRGB(t[0], t[1], t[2], a)


def _label(text, size=13, weight="regular", color=None):
    f = NSTextField.labelWithString_(text)
    f.setFont_(NSFont.boldSystemFontOfSize_(size) if weight == "bold"
               else NSFont.systemFontOfSize_(size))
    if color is not None:
        f.setTextColor_(color)
    return f


def _symbol(name, size=14):
    """Een SF Symbol als template-image (neemt de tekstkleur aan). None als het
    symbool of de API ontbreekt -- de knop toont dan gewoon alleen tekst."""
    try:
        img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
    except Exception:
        img = None
    if img is None:
        return None
    try:
        cfg = NSImageSymbolConfiguration.configurationWithPointSize_weight_(
            size, NSFontWeightRegular)
        img = img.imageWithSymbolConfiguration_(cfg) or img
    except Exception:
        pass
    img.setTemplate_(True)
    return img


class _GlyphView(NSView):
    """Het app-merkje: rode equalizer-balkjes op een donker afgerond vierkant.
    Statisch (de status leeft in de gekleurde stip ernaast), puur identiteit."""
    def drawRect_(self, _rect):
        b = self.bounds()
        NSColor.colorWithCalibratedWhite_alpha_(0.09, 1.0).set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(b, 9, 9).fill()
        _rgb(_STATE_RGB["recording"]).set()
        heights = [0.42, 0.72, 1.00, 0.60]
        bw, gap = 3.0, 2.6
        total = len(heights) * bw + (len(heights) - 1) * gap
        x = (b.size.width - total) / 2
        for hh in heights:
            bh = 5.0 + (b.size.height - 15.0) * hh
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(x, (b.size.height - bh) / 2, bw, bh), bw / 2, bw / 2).fill()
            x += bw + gap


class _PanelFlipped(NSView):
    def isFlipped(self):
        return True


class MenuPanel(NSObject):
    def initWithHud_ticker_(self, hud, ticker):
        self = objc.super(MenuPanel, self).init()
        if self is None:
            return None
        self._hud = hud
        self._ticker = ticker
        self._switches = []   # (NSSwitch, settings-sleutel)
        self._build()
        return self

    @objc.python_method
    def _sunken(self, frame):
        v = _PanelFlipped.alloc().initWithFrame_(frame)
        v.setWantsLayer_(True)
        v.layer().setCornerRadius_(9)
        v.layer().setBackgroundColor_(_cg((0.5, 0.5, 0.5), 0.10))
        return v

    @objc.python_method
    def _switch_row(self, root, y, label, key):
        root.addSubview_(_framed(_label(label, 13), NSMakeRect(PAD, y + 8, W - 90, 20)))
        sw = NSSwitch.alloc().init()
        sw.setFrame_(NSMakeRect(W - PAD - 38, y + 6, 38, 22))
        sw.setState_(NSControlStateValueOn if settings.get(key) else NSControlStateValueOff)
        sw.setTag_(len(self._switches) + 1)
        sw.setTarget_(self)
        sw.setAction_("toggleSwitch:")
        self._switches.append((sw, key))
        root.addSubview_(sw)

    @objc.python_method
    def _action_row(self, root, y, title, symbol, selector, key=""):
        img = _symbol(symbol)
        btn = NSButton.buttonWithTitle_target_action_("  " + title, self._ticker, selector)
        if img is not None:
            btn.setImage_(img)
            btn.setImagePosition_(NSImageLeft)
        btn.setBordered_(False)
        btn.setAlignment_(NSTextAlignmentLeft)
        btn.setFont_(NSFont.systemFontOfSize_(13))
        if key:
            btn.setKeyEquivalent_(key)
        btn.setFrame_(NSMakeRect(PAD - 4, y, W - 2 * (PAD - 4), 28))
        root.addSubview_(btn)

    @objc.python_method
    def _separator(self, root, y):
        box = NSBox.alloc().initWithFrame_(NSMakeRect(PAD, y, W - 2 * PAD, 1))
        box.setBoxType_(NSBoxSeparator)
        root.addSubview_(box)

    @objc.python_method
    def _build(self):
        root = _PanelFlipped.alloc().initWithFrame_(NSMakeRect(0, 0, W, 600))
        y = 14

        # --- kop: glyph + naam + status ---
        glyph = _GlyphView.alloc().initWithFrame_(NSMakeRect(PAD, y, 34, 34))
        root.addSubview_(glyph)
        root.addSubview_(_framed(_label("SamFlow", 14, "bold"),
                                 NSMakeRect(PAD + 44, y, W - PAD - 44, 18)))
        self._status_dot = _PanelFlipped.alloc().initWithFrame_(
            NSMakeRect(PAD + 44, y + 22, 8, 8))
        self._status_dot.setWantsLayer_(True)
        self._status_dot.layer().setCornerRadius_(4)
        root.addSubview_(self._status_dot)
        self._status_label = _label("", 11.5, color=NSColor.secondaryLabelColor())
        self._status_label.setFrame_(NSMakeRect(PAD + 58, y + 20, W - PAD - 58, 15))
        root.addSubview_(self._status_label)
        y += 34 + 12
        self._separator(root, y)
        y += 11

        # --- laatste dictaat ---
        root.addSubview_(_framed(
            _label("LAATSTE DICTAAT", 10.5, color=NSColor.tertiaryLabelColor()),
            NSMakeRect(PAD, y, W - 2 * PAD, 14)))
        y += 20
        card = self._sunken(NSMakeRect(PAD, y, W - 2 * PAD, 56))
        self._last_label = NSTextField.wrappingLabelWithString_("")
        self._last_label.setFont_(NSFont.systemFontOfSize_(12.5))
        self._last_label.setFrame_(NSMakeRect(11, 8, W - 2 * PAD - 22 - 62, 40))
        card.addSubview_(self._last_label)
        self._copy_btn = NSButton.buttonWithTitle_target_action_(
            "Kopiëren", self._ticker, "copyLastText:")
        self._copy_btn.setBordered_(False)
        self._copy_btn.setFont_(NSFont.systemFontOfSize_(11.5))
        self._copy_btn.setContentTintColor_(NSColor.systemRedColor())
        self._copy_btn.setFrame_(NSMakeRect(W - 2 * PAD - 66, 14, 60, 22))
        card.addSubview_(self._copy_btn)
        root.addSubview_(card)
        y += 56 + 12
        self._separator(root, y)
        y += 8

        # --- snelle toggles ---
        self._switch_row(root, y, "Geluiden", "sound_cues")
        y += 34
        self._switch_row(root, y, "Media pauzeren", "pause_media")
        y += 34
        self._switch_row(root, y, "Pill tonen", "show_pill")
        y += 34 + 6
        self._separator(root, y)
        y += 8

        # --- acties ---
        self._action_row(root, y, "Voorkeuren…", "slider.horizontal.3", "openPreferences:", ",")
        y += 30
        self._action_row(root, y, "Woordenlijst bewerken…", "book", "editLexicon:")
        y += 30
        self._action_row(root, y, "Vaak gehoorde woorden reviewen…", "sparkles", "reviewWords:")
        y += 30
        self._action_row(root, y, "Setup & permissies…", "checkmark.shield", "openWelcome:")
        y += 30 + 6
        self._separator(root, y)
        y += 8

        # --- voet ---
        root.addSubview_(_framed(
            _label("SamFlow · lokaal", 11.5, color=NSColor.tertiaryLabelColor()),
            NSMakeRect(PAD, y + 5, 160, 15)))
        stop = NSButton.buttonWithTitle_target_action_("Stop", self._ticker, "quit:")
        stop.setFont_(NSFont.systemFontOfSize_(12))
        stop.setBezelStyle_(1)  # afgerond
        stop.sizeToFit()
        sw_ = max(stop.frame().size.width, 60)
        stop.setFrame_(NSMakeRect(W - PAD - sw_, y, sw_, 26))
        root.addSubview_(stop)
        y += 26 + 12

        root.setFrame_(NSMakeRect(0, 0, W, y))
        vc = NSViewController.alloc().init()
        vc.setView_(root)
        pop = NSPopover.alloc().init()
        pop.setContentViewController_(vc)
        pop.setContentSize_((W, y))
        pop.setBehavior_(NSPopoverBehaviorTransient)
        pop.setDelegate_(self)
        self._vc = vc
        self.popover = pop

    # --- refresh + acties ---
    @objc.python_method
    def refresh(self):
        state, _ = self._hud.snapshot()
        self._status_dot.layer().setBackgroundColor_(_cg(_STATE_RGB[state]))
        self._status_label.setStringValue_(_STATE_LABEL[state])
        last = self._hud.last_text()
        if last:
            shown = last if len(last) <= 140 else last[:139] + "…"
            self._last_label.setStringValue_(f"“{shown}”")
            self._last_label.setTextColor_(NSColor.labelColor())
            self._copy_btn.setEnabled_(True)
        else:
            self._last_label.setStringValue_("Nog niets gedicteerd")
            self._last_label.setTextColor_(NSColor.tertiaryLabelColor())
            self._copy_btn.setEnabled_(False)
        for sw, key in self._switches:
            sw.setState_(NSControlStateValueOn if settings.get(key) else NSControlStateValueOff)

    def toggleSwitch_(self, sender):
        for sw, key in self._switches:
            if sw.tag() == sender.tag():
                settings.set(key, sender.state() == NSControlStateValueOn)
                break

    def popoverWillShow_(self, _note):
        self.refresh()

    @objc.python_method
    def toggle(self, button):
        if self.popover.isShown():
            self.popover.performClose_(None)
        else:
            self.refresh()
            self.popover.showRelativeToRect_ofView_preferredEdge_(
                button.bounds(), button, NSMinYEdge)
            # Activeer de app + maak het popover-venster key, anders rendert macOS
            # de switches in de inactieve (grijze) stijl i.p.v. groen -- ze lijken
            # dan uit terwijl ze aan staan, tot je klikt. Veilig: het paneel is een
            # bewuste klik, en de Fn-tap is globaal, dus focus doet er niet toe.
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            win = self._vc.view().window()
            if win is not None:
                win.makeKeyWindow()


def _framed(view, frame):
    view.setFrame_(frame)
    return view
