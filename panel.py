"""
panel.py - het menubalk-paneel: een NSPopover met een eigen view i.p.v. de kale
systeem-NSMenu. Klik op het menubalk-icoon opent dit paneel.

Waarom een popover en geen menu: een NSMenu is een systeem-tekstlijst die je
nauwelijks kunt stylen. Een NSPopover host een gewone view, dus we hebben er de
volledige controle over -- status, snelle toggles, laatste dictaat, en (als 'ie
klaarstaat) een update-knop, in SamFlow's eigen stijl. De iconen zijn SF Symbols
(Apple's vector-set, template zodat ze de tekstkleur aannemen), geen emoji.

Alle kleuren komen uit `theme.py` (de Helder-tokens) en de bouwstenen uit `ui.py`
(fill/label/glabel/hline/GlyphView), zodat het paneel exact dezelfde taal spreekt
als het hoofdvenster: één grafiet/klei/groen-palet, licht én donker adaptief, geen
losse systeemgrijzen meer. De content-view is een egaal Helder-oppervlak
(`theme.WINDOW`) i.p.v. het doorschijnende systeemmateriaal -- gelijk aan de mockup
(macos/design/menubar-panel-mockup.html). De status leeft in een gekleurde pil
rechtsboven (groen = klaar/geplakt, klei = luistert/transcribeert), net als de
status-chips op het dashboard.

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
    NSButton, NSColor, NSFont,
    NSFontWeightRegular, NSImage, NSImageLeft,
    NSImageSymbolConfiguration, NSMakeRect, NSMinYEdge, NSPopover,
    NSPopoverBehaviorTransient, NSControlStateValueOff, NSControlStateValueOn,
    NSTextAlignmentLeft, NSTextField, NSViewController,
)
from Foundation import NSObject

import appmode
import settings
import theme
import ui
import updater

W = 300
PAD = 16
HEAD_H = 60          # hoogte van de klei-getinte kopband (glyph + naam + status-pil)

# Merk-accenten als tuples voor _rgb (translucent tints trekken we hier zelf, i.p.v.
# uit de adaptieve theme-NSColors -- klei en groen zijn constant in licht/donker).
# Lokaal gehouden i.p.v. uit hud geïmporteerd: hud importeert deze module, dus
# andersom zou een cyclus geven.
_CLAY = (0.776, 0.482, 0.322)          # #C67B52 — merk-accent (Helder)
_GREEN = (0.20, 0.72, 0.35)            # #33B859 — "klaar/geplakt" + "update binnengehaald"
_ACCENT = _CLAY

# Status -> (korte pil-tekst, kleur). Groen voor de "goede" rusttoestanden
# (klaar/geplakt), klei terwijl SamFlow werkt (luistert/transcribeert).
_PILL = {
    "idle":      ("klaar", _GREEN),
    "recording": ("luistert", _CLAY),
    "thinking":  ("transcribeert", _CLAY),
    "done":      ("geplakt", _GREEN),
}


def _rgb(t, a=1.0):
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(t[0], t[1], t[2], a)


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
    def _status_pill(self, root, state):
        """De status-pil rechtsboven in de kopband (mockup .p-status): gekleurde stip
        + korte tekst op een licht getinte pil. Geeft de linker-x terug zodat de
        vaste hint-subtitel ernaast wordt afgekapt en nooit overlapt."""
        text, col = _PILL.get(state, _PILL["idle"])
        lbl = ui.label(text, 11.5, "medium", color=_rgb(col))
        lbl.sizeToFit()
        tw = lbl.frame().size.width
        w = 20 + tw + 10
        x = W - PAD - w
        pill = ui.fill(NSMakeRect(x, 22, w, 20), _rgb(col, 0.13), 10)
        d = ui.label("●", 7, color=_rgb(col))
        d.setFrame_(NSMakeRect(9, 5, 8, 11))
        pill.addSubview_(d)
        lbl.setFrame_(NSMakeRect(20, 2, tw, 15))
        pill.addSubview_(lbl)
        root.addSubview_(pill)
        return x

    @objc.python_method
    def _separator(self, root, y):
        # Haarlijn over de volle breedte (theme.LINE) -- gelijk aan de scheidingen in
        # het hoofdvenster; de content houdt zelf zijn PAD-inspringing.
        root.addSubview_(ui.hline(0, y, W))

    @objc.python_method
    def _switch_row(self, root, y, label, key):
        root.addSubview_(_framed(ui.label(label, 13), NSMakeRect(PAD, y + 8, W - 90, 20)))
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
        btn.setContentTintColor_(theme.TEXT)   # inkt-kleur i.p.v. systeem-labelColor
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
        state = self._hud.current_state()
        last = self._hud.last_text()
        u = self._hud.update_state()
        root = ui.fill(NSMakeRect(0, 0, W, 700), theme.WINDOW, 0)  # egaal Helder-oppervlak

        # kop: subtiele klei-getinte band met glyph, naam, vaste hint + status-pil
        root.addSubview_(ui.fill(NSMakeRect(0, 0, W, HEAD_H), _rgb(_CLAY, 0.06), 0))
        root.addSubview_(ui.GlyphView.alloc().initWithFrame_(NSMakeRect(PAD, 15, 34, 34)))
        root.addSubview_(_framed(ui.label("SamFlow", 14, "bold"),
                                 NSMakeRect(PAD + 45, 16, 130, 18)))
        pill_x = self._status_pill(root, state)
        sub_w = max(60, pill_x - 8 - (PAD + 45))
        root.addSubview_(_framed(
            ui.label("Houd Fn ingedrukt om te dicteren", 11.5, color=theme.TEXT2),
            NSMakeRect(PAD + 45, 35, sub_w, 15)))
        y = HEAD_H
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
                ui.label("Update beschikbaar op GitHub (git pull)", 12, color=theme.TEXT2),
                NSMakeRect(PAD, y, W - 2 * PAD, 18)))
            y += 26

        # laatste dictaat
        y = ui.glabel(root, PAD, y, W - 2 * PAD, "Laatste dictaat")
        card = ui.fill(NSMakeRect(PAD, y, W - 2 * PAD, 56), theme.SUNKEN, 9)
        lbl = NSTextField.wrappingLabelWithString_("")
        lbl.setFont_(NSFont.systemFontOfSize_(12.5))
        lbl.setFrame_(NSMakeRect(11, 8, W - 2 * PAD - 22 - 62, 40))
        if last:
            shown = last if len(last) <= 140 else last[:139] + "…"
            lbl.setStringValue_(f"“{shown}”")
            lbl.setTextColor_(theme.TEXT)
        else:
            lbl.setStringValue_("Nog niets gedicteerd")
            lbl.setTextColor_(theme.FAINT)
        card.addSubview_(lbl)
        copy = NSButton.buttonWithTitle_target_action_("Kopiëren", self._ticker, "copyLastText:")
        copy.setBordered_(False)
        copy.setFont_(NSFont.systemFontOfSize_(11.5))
        copy.setContentTintColor_(_rgb(_CLAY))
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
        self._action_row(root, y, "Open SamFlow…", "macwindow", "openMainWindow:")
        y += 30
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

        # voet -- versie + actieve modus (Basic / App-modus)
        root.addSubview_(_framed(
            ui.label(f"SamFlow · {self._version} · {appmode.label()}", 11.5, color=theme.FAINT),
            NSMakeRect(PAD, y + 5, 200, 15)))
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
