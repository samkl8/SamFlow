"""
panel.py - het menubalk-paneel: een NSPopover met een eigen view i.p.v. de kale
systeem-NSMenu. Klik op het menubalk-icoon opent dit paneel.

Waarom een popover en geen menu: een NSMenu is een systeem-tekstlijst die je
nauwelijks kunt stylen. Een NSPopover host een gewone view, dus we hebben er de
volledige controle over -- status, snelle toggles, laatste dictaat, en (als 'ie
klaarstaat) een update-knop, in SamFlow's eigen stijl. De iconen zijn SF Symbols
(Apple's vector-set, template zodat ze de tekstkleur aannemen), geen emoji.

Het paneel wordt bij élke opening opnieuw opgebouwd (_rebuild): zo weerspiegelt
het altijd de actuele status, laatste dictaat, toggle-standen en update-stand
zonder een aparte verver-route. Dat kost niets -- het opent maar af en toe.

Bij openen activeren we de app even (activateIgnoringOtherApps_): anders rendert
macOS de switches in de inactieve, grijze stijl i.p.v. groen. Veilig, want het
paneel is een bewuste klik en de Fn-tap is globaal.

Alle AppKit-calls op de main thread (de popover opent door een klik).
"""
import objc
from AppKit import (
    NSApplication,
    NSBezierPath, NSBox, NSBoxSeparator, NSButton, NSColor, NSFont,
    NSFontWeightRegular, NSImage, NSImageLeft,
    NSImageSymbolConfiguration, NSMakeRect, NSMinYEdge, NSPopover,
    NSPopoverBehaviorTransient, NSControlStateValueOff, NSControlStateValueOn,
    NSTextAlignmentLeft, NSTextField, NSView, NSViewController,
)
from Foundation import NSObject
from Quartz import CGColorCreateGenericRGB

import settings
import ui
import updater

W = 300
PAD = 16

# Status -> (kleur, tekst). Lokaal gehouden i.p.v. uit hud geïmporteerd: hud
# importeert deze module, dus andersom zou een cyclus geven.
_CLAY = (0.776, 0.482, 0.322)          # #C67B52 — merk-accent (Helder)
_GREEN = (0.20, 0.72, 0.35)            # semantisch groen: "klaar" + "update binnengehaald"
_STATE_RGB = {
    "idle": _GREEN,                    # groene "ready"-stip: klaar om te dicteren
    "recording": _CLAY,
    "thinking": _CLAY,
    "done": _CLAY,
}
_STATE_LABEL = {
    "idle": "klaar — houd Fn ingedrukt",
    "recording": "aan het luisteren…",
    "thinking": "transcriberen…",
    "done": "geplakt ✓",
}
_ACCENT = _CLAY


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


def _framed(view, frame):
    view.setFrame_(frame)
    return view


class _GlyphView(NSView):
    """Het app-merkje: lichte equalizer-balkjes op een donker afgerond vierkant --
    zelfde grafiet+wit als het app-icoon. Statisch (de status leeft in de stip
    ernaast), puur identiteit."""
    def drawRect_(self, _rect):
        b = self.bounds()
        NSColor.colorWithCalibratedWhite_alpha_(0.09, 1.0).set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(b, 9, 9).fill()
        _rgb((0.94, 0.94, 0.95)).set()      # off-white, gelijk aan het app-icoon
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
        self._switches = []
        try:
            self._version = updater.short_version()
        except Exception:
            self._version = "?"
        vc = NSViewController.alloc().init()
        pop = NSPopover.alloc().init()
        pop.setContentViewController_(vc)
        pop.setBehavior_(NSPopoverBehaviorTransient)
        self._vc = vc
        self.popover = pop
        self._rebuild()
        return self

    # --- bouwstenen ---
    @objc.python_method
    def _sunken(self, frame):
        v = _PanelFlipped.alloc().initWithFrame_(frame)
        v.setWantsLayer_(True)
        v.layer().setCornerRadius_(9)
        v.layer().setBackgroundColor_(_cg((0.5, 0.5, 0.5), 0.10))
        return v

    @objc.python_method
    def _separator(self, root, y):
        box = NSBox.alloc().initWithFrame_(NSMakeRect(PAD, y, W - 2 * PAD, 1))
        box.setBoxType_(NSBoxSeparator)
        root.addSubview_(box)

    @objc.python_method
    def _switch_row(self, root, y, label, key):
        root.addSubview_(_framed(_label(label, 13), NSMakeRect(PAD, y + 8, W - 90, 20)))
        sw = ui.Toggle.alloc().init()
        sw.setFrame_(NSMakeRect(W - PAD - 38, y + 6, 38, 22))
        sw.setState_(NSControlStateValueOn if settings.get(key) else NSControlStateValueOff)
        sw.setTag_(len(self._switches) + 1)
        sw.setTarget_(self)
        sw.setAction_("toggleSwitch:")
        self._switches.append((sw, key))
        root.addSubview_(sw)

    @objc.python_method
    def _action_row(self, root, y, title, symbol, selector):
        img = _symbol(symbol)
        btn = NSButton.buttonWithTitle_target_action_("  " + title, self._ticker, selector)
        if img is not None:
            btn.setImage_(img)
            btn.setImagePosition_(NSImageLeft)
        btn.setBordered_(False)
        btn.setAlignment_(NSTextAlignmentLeft)
        btn.setFont_(NSFont.systemFontOfSize_(13))
        btn.setFrame_(NSMakeRect(PAD - 4, y, W - 2 * (PAD - 4), 28))
        root.addSubview_(btn)

    @objc.python_method
    def _update_button(self, root, y, title, selector, rgb):
        btn = NSButton.buttonWithTitle_target_action_(title, self._ticker, selector)
        btn.setBezelStyle_(1)
        btn.setFont_(NSFont.systemFontOfSize_weight_(12.5, 0.3))
        try:
            btn.setContentTintColor_(_rgb(rgb))
        except Exception:
            pass
        btn.setFrame_(NSMakeRect(PAD, y, W - 2 * PAD, 30))
        root.addSubview_(btn)

    # --- opbouw + tonen ---
    @objc.python_method
    def _make_view(self):
        self._switches = []
        state, _ = self._hud.snapshot()
        last = self._hud.last_text()
        u = self._hud.update_state()
        root = _PanelFlipped.alloc().initWithFrame_(NSMakeRect(0, 0, W, 640))
        y = 14

        # kop: glyph + naam + status
        root.addSubview_(_GlyphView.alloc().initWithFrame_(NSMakeRect(PAD, y, 34, 34)))
        root.addSubview_(_framed(_label("SamFlow", 14, "bold"),
                                 NSMakeRect(PAD + 44, y, W - PAD - 44, 18)))
        dot = _PanelFlipped.alloc().initWithFrame_(NSMakeRect(PAD + 44, y + 22, 8, 8))
        dot.setWantsLayer_(True)
        dot.layer().setCornerRadius_(4)
        dot.layer().setBackgroundColor_(_cg(_STATE_RGB[state]))
        root.addSubview_(dot)
        root.addSubview_(_framed(
            _label(_STATE_LABEL[state], 11.5, color=NSColor.secondaryLabelColor()),
            NSMakeRect(PAD + 58, y + 20, W - PAD - 58, 15)))
        y += 34 + 12
        self._separator(root, y)
        y += 11

        # update-regel (alleen als er iets te melden is)
        if u.get("applied"):
            self._update_button(root, y, "✓  Update binnengehaald — nu herstarten",
                                "restartApp:", _GREEN)
            y += 38
        elif u.get("available") and u.get("can_apply"):
            self._update_button(root, y, "Update beschikbaar — nu bijwerken",
                                "applyUpdate:", _ACCENT)
            y += 38
        elif u.get("available"):
            root.addSubview_(_framed(
                _label("Update beschikbaar op GitHub (git pull)", 12,
                       color=NSColor.secondaryLabelColor()),
                NSMakeRect(PAD, y, W - 2 * PAD, 18)))
            y += 26

        # laatste dictaat
        root.addSubview_(_framed(
            _label("LAATSTE DICTAAT", 10.5, color=NSColor.tertiaryLabelColor()),
            NSMakeRect(PAD, y, W - 2 * PAD, 14)))
        y += 20
        card = self._sunken(NSMakeRect(PAD, y, W - 2 * PAD, 56))
        lbl = NSTextField.wrappingLabelWithString_("")
        lbl.setFont_(NSFont.systemFontOfSize_(12.5))
        lbl.setFrame_(NSMakeRect(11, 8, W - 2 * PAD - 22 - 62, 40))
        if last:
            shown = last if len(last) <= 140 else last[:139] + "…"
            lbl.setStringValue_(f"“{shown}”")
            lbl.setTextColor_(NSColor.labelColor())
        else:
            lbl.setStringValue_("Nog niets gedicteerd")
            lbl.setTextColor_(NSColor.tertiaryLabelColor())
        card.addSubview_(lbl)
        copy = NSButton.buttonWithTitle_target_action_("Kopiëren", self._ticker, "copyLastText:")
        copy.setBordered_(False)
        copy.setFont_(NSFont.systemFontOfSize_(11.5))
        copy.setContentTintColor_(NSColor.secondaryLabelColor())
        copy.setEnabled_(bool(last))
        copy.setFrame_(NSMakeRect(W - 2 * PAD - 66, 14, 60, 22))
        card.addSubview_(copy)
        root.addSubview_(card)
        y += 56 + 12
        self._separator(root, y)
        y += 8

        # snelle toggles
        self._switch_row(root, y, "Geluiden", "sound_cues")
        y += 34
        self._switch_row(root, y, "Media pauzeren", "pause_media")
        y += 34
        self._switch_row(root, y, "Pill tonen", "show_pill")
        y += 34
        self._switch_row(root, y, "Automatisch bijwerken", "auto_update")
        y += 34 + 6
        self._separator(root, y)
        y += 8

        # acties
        self._action_row(root, y, "Voorkeuren…", "slider.horizontal.3", "openPreferences:")
        y += 30
        self._action_row(root, y, "Woordenlijst bewerken…", "book", "editLexicon:")
        y += 30
        self._action_row(root, y, "Vaak gehoorde woorden reviewen…", "sparkles", "reviewWords:")
        y += 30
        self._action_row(root, y, "Setup & permissies…", "checkmark.shield", "openWelcome:")
        y += 30
        self._action_row(root, y, "Controleer op updates", "arrow.triangle.2.circlepath",
                         "checkForUpdates:")
        y += 30 + 6
        self._separator(root, y)
        y += 8

        # voet
        root.addSubview_(_framed(
            _label(f"SamFlow · {self._version}", 11.5, color=NSColor.tertiaryLabelColor()),
            NSMakeRect(PAD, y + 5, 170, 15)))
        stop = NSButton.buttonWithTitle_target_action_("Stop", self._ticker, "quit:")
        stop.setFont_(NSFont.systemFontOfSize_(12))
        stop.setBezelStyle_(1)
        stop.sizeToFit()
        sw_ = max(stop.frame().size.width, 60)
        stop.setFrame_(NSMakeRect(W - PAD - sw_, y, sw_, 26))
        root.addSubview_(stop)
        y += 26 + 12

        root.setFrame_(NSMakeRect(0, 0, W, y))
        return root, y

    @objc.python_method
    def _rebuild(self):
        root, h = self._make_view()
        self._vc.setView_(root)
        self.popover.setContentSize_((W, h))

    @objc.python_method
    def toggle(self, button):
        if self.popover.isShown():
            self.popover.performClose_(None)
            return
        self._rebuild()
        self.popover.showRelativeToRect_ofView_preferredEdge_(
            button.bounds(), button, NSMinYEdge)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        win = self._vc.view().window()
        if win is not None:
            win.makeKeyWindow()

    def toggleSwitch_(self, sender):
        for sw, key in self._switches:
            if sw.tag() == sender.tag():
                settings.set(key, sender.state() == NSControlStateValueOn)
                break
