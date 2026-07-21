"""
mainwindow.py - het hoofdvenster van SamFlow (de app-schil, fase 1).

Eén echt NSWindow met een zijbalk (Overzicht / Historie / Woordenlijst /
Instellingen) en een wisselend content-gebied. Zelfde bouwtrant als prefs.py: een
Flipped-container, top-down gelayout, met de gedeelde helpers uit ui.py. Geen
NSSplitViewController-magie -- een zijbalk-view links en een NSScrollView rechts
waarvan we de documentView per tab omwisselen.

De Instellingen-tab is exact de voorkeuren-view uit prefs.py (PrefsController) --
één implementatie, twee plekken. Overzicht is het dashboard: een grafiet-hero-band
met je dag in één getal, status-chips (rechten/mic/Whisper-server), vier stat-tegels
en het week-staafgrafiekje -- gevoed door de lokale, inhoudsloze stats-laag (stats.py).
Historie is de opt-in dictaat-historie (history.py); Woordenlijst geeft de leer-loop
(lexicon.py) een gezicht: suggesties, termen (chips) en correcties.

Regels (uit het buildout-plan):
- Het venster opent altijd vanuit een klik (paneel/dock) -> main thread, dus de
  AppKit-regel is gedekt. server_up() heeft een 2s-timeout en gaat daarom op een
  achtergrondthread (nooit de run loop blokkeren); de uitkomst wordt via
  performSelectorOnMainThread terug op de main thread gezet.
- Het venster mág focus pakken -- het is een bewuste klik, geen pill.
- De controller wordt in _win vastgehouden tegen de GC (zelfde patroon als
  prefs._open). samflow wordt lui geïmporteerd, zodat er geen import-cyclus met
  hud/samflow ontstaat.
"""
import threading
import time
from datetime import datetime

import objc
from AppKit import (
    NSAlert, NSApplication, NSApplicationActivationPolicyAccessory,
    NSBackingStoreBuffered, NSBezierPath, NSButton, NSColor, NSFont,
    NSFontWeightRegular, NSGradient, NSGraphicsContext, NSImage,
    NSImageScaleProportionallyUpOrDown, NSImageSymbolConfiguration, NSImageView,
    NSMakePoint, NSMakeRect, NSNoBorder, NSPasteboard, NSPasteboardTypeString,
    NSScrollView, NSSearchField, NSTextAlignmentCenter, NSTextAlignmentRight,
    NSTextField, NSTextFieldRoundedBezel, NSTextView, NSTimer, NSTrackingActiveInKeyWindow,
    NSTrackingArea, NSTrackingMouseEnteredAndExited,
    NSView, NSViewHeightSizable, NSViewMinYMargin,
    NSViewWidthSizable, NSWindow, NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable, NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from Foundation import (
    NSFullUserName, NSObject, NSRunLoop, NSRunLoopCommonModes,
)

import appmode
import audiodev
import history
import lexicon
import prefs
import settings
import stats
import theme
import ui
import updater

SIDE_W = 210                   # vaste zijbalk; alleen de content-kolom groeit mee
MIN_CONTENT_W = ui.W           # 470 -- de smalste stand; de prefs-view past hier 1:1 in
WIN_W = SIDE_W + MIN_CONTENT_W  # start- én minimumbreedte van het venster
WIN_H = 600                    # starthoogte
MIN_WIN_H = 480                # onder deze hoogte wordt het dashboard te krap
STATS_4COL_W = 620             # inner-breedte vanaf waar de stat-tegels 4-op-een-rij gaan

_GRAPHITE = (0.118, 0.118, 0.133)         # #1E1E22 -- de constante van SamFlow
_DAYS_NL = ["ma", "di", "wo", "do", "vr", "za", "zo"]
_MONTHS_NL = ["januari", "februari", "maart", "april", "mei", "juni", "juli",
              "augustus", "september", "oktober", "november", "december"]
_WEEKDAYS_NL = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag",
                "zaterdag", "zondag"]

NAV = [
    ("Overzicht", "waveform"),
    ("Historie", "clock"),
    ("Woordenlijst", "book"),
    ("Instellingen", "slider.horizontal.3"),
]

_CLAY = (0.776, 0.482, 0.322)             # #C67B52 -- merk-accent (Helder)
_GREEN = (0.20, 0.72, 0.35)               # #33B859 -- "aan/klaar"


def _rgb(t, a=1.0):
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(t[0], t[1], t[2], a)


def _symbol(name, size=15):
    """Een SF Symbol als template-image (neemt de tint aan). None als het symbool
    of de API ontbreekt -- de rij toont dan gewoon alleen tekst."""
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


def _card(frame):
    """Een 'verzonken' afgerond vlak (--sunken uit het palet), adaptief voor
    licht/donker. Tekent zichzelf (geen layer-CGColor), zodat het thema meewisselt."""
    return ui.fill(frame, theme.SUNKEN, 12)


def _white(a):
    return NSColor.colorWithCalibratedWhite_alpha_(1.0, a)


def _mini_btn(title, target, action, tag):
    b = NSButton.buttonWithTitle_target_action_(title, target, action)
    b.setBezelStyle_(1)
    b.setFont_(NSFont.systemFontOfSize_(11))
    b.setTag_(tag)
    return b


# ---------- Nederlandse opmaak (locale is onbetrouwbaar; hardgecodeerd) ----------
def _nl_int(n):
    """1240 -> '1.240' (punt als duizendtal-scheiding)."""
    return f"{int(n):,}".replace(",", ".")


def _nl_dec(x, digits=1):
    """0.44 -> '0,4' (komma als decimaalteken)."""
    return f"{x:.{digits}f}".replace(".", ",")


def _nl_date(dt):
    """'vrijdag 17 juli'."""
    return f"{_WEEKDAYS_NL[dt.weekday()]} {dt.day} {_MONTHS_NL[dt.month - 1]}"


def _greeting(dt):
    """'Goedemorgen, Sam' -- naam uit de macOS-accountnaam (niet hardgecodeerd)."""
    h = dt.hour
    part = "Goedemorgen" if h < 12 else "Goedemiddag" if h < 18 else "Goedenavond"
    try:
        name = (NSFullUserName() or "").split()[0]
    except Exception:
        name = ""
    return f"{part}, {name}" if name else part


def _dur_hm(sec):
    """Seconden -> '≈ 1 u 52 m' / '≈ 12 m' / '≈ 40 s'."""
    sec = int(round(sec))
    if sec < 60:
        return f"≈ {sec} s"
    m = sec // 60
    if m < 60:
        return f"≈ {m} m"
    return f"≈ {m // 60} u {m % 60:02d} m"


class _NavItem(NSView):
    """Eén klikbare zijbalk-rij: icoon + label, met een klei-getinte achtergrond
    als 'ie actief is. Custom view i.p.v. NSButton omdat we de selectie-stijl
    zelf willen tekenen (in de Helder-taal)."""
    def initWithFrame_index_title_symbol_owner_(self, frame, index, title, sym, owner):
        self = objc.super(_NavItem, self).initWithFrame_(frame)
        if self is None:
            return None
        self._index = index
        self._owner = owner
        self._selected = False
        self.setWantsLayer_(True)
        iv = NSImageView.alloc().initWithFrame_(
            NSMakeRect(12, (frame.size.height - 18) / 2, 18, 18))
        img = _symbol(sym, 15)
        if img is not None:
            iv.setImage_(img)
        iv.setImageScaling_(NSImageScaleProportionallyUpOrDown)
        iv.setContentTintColor_(theme.TEXT2)
        self._iv = iv
        self.addSubview_(iv)
        lbl = ui.label(title, 13.5)
        lbl.setFrame_(NSMakeRect(40, (frame.size.height - 18) / 2, frame.size.width - 48, 18))
        self._lbl = lbl
        self.addSubview_(lbl)
        return self

    def isFlipped(self):
        return True

    def acceptsFirstMouse_(self, _ev):
        return True

    def mouseDown_(self, _ev):
        self._owner.show_tab(self._index)

    @objc.python_method
    def set_selected(self, on):
        self._selected = on
        self._lbl.setTextColor_(_rgb(_CLAY) if on else theme.TEXT)
        self._iv.setContentTintColor_(_rgb(_CLAY) if on else theme.TEXT2)
        self.setNeedsDisplay_(True)

    def drawRect_(self, _rect):
        if not self._selected:
            return
        b = self.bounds()
        _rgb(_CLAY, 0.14).set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(b, 7, 7).fill()


class _HeroBand(ui.Flipped):
    """De grafiet-band bovenaan het dashboard: het enige merk-moment van het scherm.
    Grafiet is de constante van SamFlow (verandert nooit met het thema, net als de
    pill) met een subtiele klei-gloed in de hoek en het stille merkteken rechtsonder
    -- eigen tekenwerk, geen afbeeldingen (zelfde trant als ui.GlyphView). De tekst
    zit als subviews eroverheen; dit tekent alleen de achtergrond."""
    def drawRect_(self, _rect):
        b = self.bounds()
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(b, 14, 14)
        top = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.16, 0.16, 0.18, 1.0)
        base = NSColor.colorWithCalibratedRed_green_blue_alpha_(
            _GRAPHITE[0], _GRAPHITE[1], _GRAPHITE[2], 1.0)
        NSGradient.alloc().initWithStartingColor_endingColor_(top, base).drawInBezierPath_angle_(
            path, -90)

        NSGraphicsContext.saveGraphicsState()
        path.addClip()
        clay = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.776, 0.482, 0.322, 0.26)
        clear = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.776, 0.482, 0.322, 0.0)
        glow = NSGradient.alloc().initWithStartingColor_endingColor_(clay, clear)
        # rechtsonder, bij het merkteken (mockup: radial at 92% 125%). Flipped view:
        # de onderrand ligt bij grote y.
        c = NSMakePoint(b.size.width - 30, b.size.height - 16)
        glow.drawFromCenter_radius_toCenter_radius_options_(c, 0, c, 165, 0)

        # stil merkteken: lichte staafjes rechtsonder (zoals ui.GlyphView, maar op
        # grafiet en heel subtiel). Flipped view: de basislijn ligt bij grote y
        # (onderrand), de staafjes groeien omhoog = naar kleinere y.
        _white(0.13).set()
        heights = [0.42, 0.72, 1.00, 0.60]
        bw, gap, mh = 4.0, 3.5, 26.0
        total = len(heights) * bw + (len(heights) - 1) * gap
        x = b.size.width - 24 - total
        by = b.size.height - 20
        for hh in heights:
            bh = 8.0 + mh * hh
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(x, by - bh, bw, bh), bw / 2, bw / 2).fill()
            x += bw + gap
        NSGraphicsContext.restoreGraphicsState()


class _WeekChart(NSView):
    """Woorden-per-dag-staafgrafiek in klei. Alleen vandáág draagt een getal (de
    rest is 'af te lezen in twee seconden'). Bars tekenen we in drawRect_; de
    dag-labels en het getal zijn subviews (eenvoudiger dan tekst tekenen)."""
    def initWithFrame_words_today_(self, frame, words, today_index):
        self = objc.super(_WeekChart, self).initWithFrame_(frame)
        if self is None:
            return None
        self._words = [int(w) for w in words]
        self._today = today_index
        W, H = frame.size.width, frame.size.height
        label_h, top_pad = 16.0, 18.0
        self._base_y = label_h
        self._area_h = H - label_h - top_pad
        mx = max(self._words) or 1
        slot = W / 7.0
        self._bars = []
        for i, wv in enumerate(self._words):
            bw = slot * 0.46
            bx = slot * i + (slot - bw) / 2
            bh = (wv / mx) * self._area_h
            self._bars.append((bx, bw, bh))
            # dag-label onder de bar
            dl = ui.label(_DAYS_NL[i], 10,
                          color=(_rgb(_CLAY) if i == today_index
                                 else theme.FAINT))
            dl.setAlignment_(NSTextAlignmentCenter)
            dl.setFrame_(NSMakeRect(slot * i, 0, slot, 13))
            self.addSubview_(dl)
        # getal boven de bar van vandaag
        if 0 <= today_index < 7 and self._words[today_index] > 0:
            bx, bw, bh = self._bars[today_index]
            num = ui.label(_nl_int(self._words[today_index]), 11, "bold")
            num.setAlignment_(NSTextAlignmentCenter)
            num.setFrame_(NSMakeRect(slot * today_index, self._base_y + bh + 2, slot, 15))
        else:
            num = None
        if num is not None:
            self.addSubview_(num)
        self._today_num = num          # blijft altijd staan; hover-getal is apart
        self._slot = slot
        self._hover = -1
        # Eén herbruikbaar getal-label dat we boven de gehoverde balk schuiven. Zo
        # zie je ook bij de andere dagen hoeveel woorden, zonder alle zeven getallen
        # vast te tonen (dat was juist de rust van de grafiek).
        self._hover_num = ui.label("", 11, "bold")
        self._hover_num.setAlignment_(NSTextAlignmentCenter)
        self._hover_num.setHidden_(True)
        self.addSubview_(self._hover_num)
        return self

    def isFlipped(self):
        return False   # bars groeien van onderen (y=0 = basislijn boven de labels)

    def updateTrackingAreas(self):
        # Eén tracking-area per dag-kolom, met enter/exit -- die komen altijd door, óók
        # zonder acceptsMouseMovedEvents op het venster (anders dan mouseMoved). De
        # kolom-index reist mee in userInfo. AppKit roept dit ook bij resize aan; de
        # dashboard-herbouw maakt de view sowieso opnieuw, dit is het vangnet.
        objc.super(_WeekChart, self).updateTrackingAreas()
        for ta in list(self.trackingAreas()):
            self.removeTrackingArea_(ta)
        h = self.bounds().size.height
        opts = NSTrackingMouseEnteredAndExited | NSTrackingActiveInKeyWindow
        for i in range(7):
            rect = NSMakeRect(self._slot * i, 0, self._slot, h)
            self.addTrackingArea_(NSTrackingArea.alloc()
                .initWithRect_options_owner_userInfo_(rect, opts, self, {"slot": i}))

    def _event_slot(self, event):
        # userInfo() komt als gewone dict terug (PyObjC-brug); [] werkt óók op een
        # NSDictionary, dus dit blijft goed mocht dat ooit veranderen.
        ta = event.trackingArea()
        info = ta.userInfo() if ta is not None else None
        if not info:
            return -1
        try:
            return int(info["slot"])
        except (KeyError, TypeError, ValueError):
            return -1

    def mouseEntered_(self, event):
        self._set_hover(self._event_slot(event))

    def mouseExited_(self, event):
        # Alleen wissen als we de kolom verlaten die nú oplicht: bij het schuiven naar
        # een buur kan enter(nieuw) vóór exit(oud) komen -- dan niet per ongeluk wissen.
        if self._event_slot(event) == self._hover:
            self._set_hover(-1)

    def _set_hover(self, i):
        self._hover = i
        # Boven vandaag staat het getal al vast -> geen dubbel hover-getal daar.
        show = i >= 0 and not (i == self._today and self._today_num is not None)
        if show:
            bx, bw, bh = self._bars[i]
            self._hover_num.setStringValue_(_nl_int(self._words[i]))
            self._hover_num.setFrame_(
                NSMakeRect(self._slot * i, self._base_y + bh + 2, self._slot, 15))
        self._hover_num.setHidden_(not show)
        self.setNeedsDisplay_(True)   # herteken: gehoverde balk licht op

    def drawRect_(self, _rect):
        for i, (bx, bw, bh) in enumerate(self._bars):
            if i == self._today:
                _rgb(_CLAY, 1.0).set()
            elif i == self._hover:
                _rgb(_CLAY, 0.62).set()   # opgelicht onder de cursor
            else:
                _rgb(_CLAY, 0.40).set()
            r = NSMakeRect(bx, self._base_y, bw, max(bh, 2.0))
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(r, 3, 3).fill()


class _Chip(NSView):
    """Een pill-chip in de Helder-taal (mockup .chip). Drie stijlen: 'solid' = gevuld
    (CHIP-token), 'dashed' = transparant met gestreepte rand (een ambigue term: ook een
    gewoon woord, gaat wel mee in de herkenning maar wordt niet overal met hoofdletter
    geforceerd), 'plain' = niets (transparant). Zelf-getekend zodat vulling én rand met
    licht/donker meewisselen."""
    def initWithFrame_style_(self, frame, style):
        self = objc.super(_Chip, self).initWithFrame_(frame)
        if self is None:
            return None
        self._style = style
        return self

    def isFlipped(self):
        return True

    def drawRect_(self, _rect):
        b = self.bounds()
        inset = 0.75
        rad = (b.size.height - 2 * inset) / 2
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(inset, inset, b.size.width - 2 * inset, b.size.height - 2 * inset),
            rad, rad)
        if self._style == "solid":
            theme.CHIP.set()
            path.fill()
        elif self._style == "dashed":
            theme.LINE2.set()
            path.setLineWidth_(1.0)
            path.setLineDash_count_phase_([3.0, 3.0], 2, 0.0)
            path.stroke()


class _PillButton(NSView):
    """Een chip-knop in de mockup-stijl (.btn / .btn.ghost). Primair = een neutraal
    chip-vlak met semibold tekst en een haarlijn; ghost = kaal, alleen gedimde tekst.
    Zelf-getekend én zelf-klikbaar: stuurt de action naar de target met zichzelf als
    sender, zodat de bestaande tag-gebaseerde handlers ongewijzigd blijven werken.
    De breedte volgt uit de titel (setFrame bij init), dus de tekst kapt nooit af."""
    def initWithTitle_target_action_tag_ghost_(self, title, target, action, tag, ghost):
        lbl = ui.label(title, 11.5, "medium",
                       color=(theme.TEXT2 if ghost else theme.TEXT))
        lbl.sizeToFit()
        tw = lbl.frame().size.width
        padx = 8 if ghost else 11
        w, h = tw + 2 * padx, 22
        self = objc.super(_PillButton, self).initWithFrame_(NSMakeRect(0, 0, w, h))
        if self is None:
            return None
        self._ghost = ghost
        self._target = target
        self._action = action
        self._tagv = tag
        self._title = title
        self._padx = padx
        self._lbl = lbl
        self._flashing = False
        lbl.setFrame_(NSMakeRect(padx, (h - 15) / 2, tw, 15))
        self.addSubview_(lbl)
        return self

    def isFlipped(self):
        return True

    def tag(self):
        return self._tagv

    def acceptsFirstMouse_(self, _ev):
        return True

    def mouseDown_(self, _ev):
        if self._action and self._target:
            NSApplication.sharedApplication().sendAction_to_from_(
                self._action, self._target, self)

    def drawRect_(self, _rect):
        # Tijdens de kopieer-flash geen chip tekenen: dan staat er een kaal vinkje i.p.v.
        # een los pilletje met een vinkje erin.
        if self._ghost or self._flashing:
            return
        b = self.bounds()
        r = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(0.5, 0.5, b.size.width - 1, b.size.height - 1), 8, 8)
        theme.CHIP.set()
        r.fill()
        theme.LINE.set()
        r.setLineWidth_(0.5)
        r.stroke()

    @objc.python_method
    def _relabel(self, text, color):
        self._lbl.setStringValue_(text)
        self._lbl.setTextColor_(color)
        self._lbl.sizeToFit()
        tw = self._lbl.frame().size.width
        b = self.frame()
        neww = tw + 2 * self._padx
        # groei naar links, houd de rechterrand vast (er is ruimte links van de knop)
        self.setFrame_(NSMakeRect(b.origin.x + b.size.width - neww, b.origin.y,
                                  neww, b.size.height))
        self._lbl.setFrame_(NSMakeRect(self._padx, (b.size.height - 15) / 2, tw, 15))

    @objc.python_method
    def flash_copied(self):
        """Korte bevestiging: een kaal, gecentreerd groen vinkje -- géén chip-achtergrond
        (drawRect_ slaat 'm over zolang _flashing) en geen bredere 'Gekopieerd' die over
        de tekst links ervan zou vallen. We houden hetzelfde kader vast en centreren het
        vinkje erin, dus niets verspringt. Klapt na ~1,3 s terug naar de 'Kopieer'-chip."""
        if self._flashing:
            return
        self._flashing = True
        b = self.bounds()
        self._lbl.setStringValue_("✓")
        self._lbl.setTextColor_(_rgb(_GREEN))
        self._lbl.setAlignment_(NSTextAlignmentCenter)
        self._lbl.setFrame_(NSMakeRect(0, (b.size.height - 15) / 2, b.size.width, 15))
        self.setNeedsDisplay_(True)

        def revert(_t):
            self._flashing = False
            self._relabel(self._title, theme.TEXT2 if self._ghost else theme.TEXT)
            self.setNeedsDisplay_(True)
        try:
            NSTimer.scheduledTimerWithTimeInterval_repeats_block_(1.3, False, revert)
        except Exception:
            revert(None)


class _ClayButton(NSView):
    """De primaire actie in een gebrand paneel: een gevuld klei-vlak met witte, semibold
    tekst. Zelf-getekend en zelf-klikbaar -- juist zodat 'ie NIET de blauwe systeem-accent
    van een default-NSButton pakt (dat botste met Helder). Breedte volgt de titel."""
    def initWithTitle_target_action_(self, title, target, action):
        lbl = ui.label(title, 12.5, "medium", color=NSColor.whiteColor())
        lbl.sizeToFit()
        tw = lbl.frame().size.width
        w, h = tw + 32, 30
        self = objc.super(_ClayButton, self).initWithFrame_(NSMakeRect(0, 0, w, h))
        if self is None:
            return None
        self._target = target
        self._action = action
        lbl.setFrame_(NSMakeRect(16, (h - 16) / 2, tw, 16))
        self.addSubview_(lbl)
        return self

    def isFlipped(self):
        return True

    def acceptsFirstMouse_(self, _ev):
        return True

    def mouseDown_(self, _ev):
        if self._action and self._target:
            NSApplication.sharedApplication().sendAction_to_from_(
                self._action, self._target, self)

    def drawRect_(self, _rect):
        b = self.bounds()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(0, 0, b.size.width, b.size.height), 8, 8).addClip()
        theme.CLAY.set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(0, 0, b.size.width, b.size.height), 8, 8).fill()


class MainWindow(NSObject):
    def initWithHud_(self, hud):
        self = objc.super(MainWindow, self).init()
        if self is None:
            return None
        self._hud = hud                 # kan None zijn (--window standalone)
        self._navitems = []
        self._prefs_ctrl = None         # in leven houden zolang de Instellingen-tab bestaat
        self._server_dot = None
        self._server_val = None         # het "Model — …"-tekstlabel in de hero (async bijgewerkt)
        self._server_up = None          # laatst bekende server-status; overleeft een reflow
        self._server_checking = False   # voorkomt een stapel checks (één tegelijk)
        self._current = -1
        self._stats_mtime = None
        self._stats_cache = None        # (mtime, summary): serveert het dashboard tijdens een resize
        self._timer = None
        self._resize_timer = None       # trailing-timer: garandeert een reflow op de eindmaat
        self._last_reflow = 0.0         # monotone klok; throttelt de live-reflow tot ~30/s
        self._status_cache = None       # (mic_ok, mic_val, rights_ok): niet elke reflow opnieuw opvragen
        self._content_w = MIN_CONTENT_W  # groeit mee met het venster; start op de smalste stand
        self._built_w = MIN_CONTENT_W   # breedte waarop de huidige tab écht gebouwd is
        self._doc_natural_h = WIN_H     # de echte contenthoogte (los van de zichtbare hoogte)
        # chrome-refs die bij een resize herplaatst worden (zie _reflow)
        self._sidebar = None
        self._side_hairline = None
        self._side_foot = None
        self._side_cred = None
        # historie-tab
        self._hist_query = ""
        self._hist_search = None
        self._hist_list = None
        self._hist_shown = []
        self._hist_header_h = 0
        self._hist_cache = {}           # query -> items, gecachet per history.mtime()
        self._hist_cache_m = object()   # sentinel: forceert eerste vulling
        # woordenlijst-tab
        self._sugg = []
        self._term_list = []
        self._map_list = []
        self._sheet_win = None          # gebrand invoer-paneel (term/correctie), één tegelijk
        self._sheet_mode = None
        self._sheet_text = None         # NSTextView (term-modus: meerdere regels)
        self._sheet_heard = None        # NSTextField (correctie-/voorstel-modus, enkel veld)
        self._sheet_canon = None
        self._sheet_word = None         # het gehoorde voorstel-woord (correct-modus)
        try:
            self._version = updater.short_version()
        except Exception:
            self._version = "?"
        self._build()
        self.show_tab(0)
        # Ververs het dashboard live terwijl het venster open is -- maar alléén als
        # de stats-file écht veranderde (mtime-check in refreshTick_, geen I/O per tik).
        self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            4.0, self, "refreshTick:", None, True)
        return self

    # ---------- opbouw ----------
    @objc.python_method
    def _build(self):
        root = ui.Flipped.alloc().initWithFrame_(NSMakeRect(0, 0, WIN_W, WIN_H))
        root.setAutoresizesSubviews_(True)
        sidebar = self._build_sidebar()
        # vaste breedte, groeit alleen in hoogte mee met het venster
        sidebar.setAutoresizingMask_(NSViewHeightSizable)
        root.addSubview_(sidebar)

        scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(SIDE_W, 0, self._content_w, WIN_H))
        scroll.setHasVerticalScroller_(True)
        scroll.setHasHorizontalScroller_(False)
        scroll.setDrawsBackground_(False)
        scroll.setAutohidesScrollers_(True)
        scroll.setBorderType_(NSNoBorder)
        # links vast tegen de zijbalk; groeit naar rechts (breedte) en omlaag (hoogte)
        scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        self._scroll = scroll
        root.addSubview_(scroll)

        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, WIN_W, WIN_H),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
            | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable,
            NSBackingStoreBuffered, False)
        win.setTitle_("SamFlow")
        win.setContentView_(root)
        win.setContentMinSize_((WIN_W, MIN_WIN_H))   # nooit smaller dan de prefs-kolom
        win.setBackgroundColor_(theme.WINDOW)      # --ss: het app-oppervlak
        win.center()
        win.setReleasedWhenClosed_(False)
        win.setDelegate_(self)
        self.window = win

    @objc.python_method
    def _build_sidebar(self):
        # Vlakke zijbalk in de Helder-kleur (--sidebg), zoals de mockup -- niet de
        # doorschijnende macOS-zijbalk. FillView is flipped, dus we layouten er direct
        # top-down in. Een hairline aan de rechterrand (border-right .5px).
        bar = ui.fill(NSMakeRect(0, 0, SIDE_W, WIN_H), theme.SIDEBAR, 0)
        bar.setAutoresizesSubviews_(True)
        self._sidebar = bar
        hairline = ui.fill(NSMakeRect(SIDE_W - 1, 0, 1, WIN_H), theme.LINE, 0)
        hairline.setAutoresizingMask_(NSViewHeightSizable)   # groeit mee in de hoogte
        self._side_hairline = hairline
        bar.addSubview_(hairline)

        y = 18
        bar.addSubview_(ui.GlyphView.alloc().initWithFrame_(NSMakeRect(16, y, 26, 26)))
        name = ui.label("SamFlow", 14, "bold")
        name.setFrame_(NSMakeRect(52, y + 4, SIDE_W - 60, 20))
        bar.addSubview_(name)
        y += 26 + 16

        for i, (title, sym) in enumerate(NAV):
            item = _NavItem.alloc().initWithFrame_index_title_symbol_owner_(
                NSMakeRect(10, y, SIDE_W - 20, 34), i, title, sym, self)
            bar.addSubview_(item)
            self._navitems.append(item)
            y += 38

        # voet: pint onderaan (top-marge flexibel) zodat 'ie bij een hoger venster
        # meezakt i.p.v. midden in de zijbalk te blijven hangen.
        foot = ui.label(f"SamFlow · {self._version}", 11, color=theme.FAINT)
        foot.setFrame_(NSMakeRect(16, WIN_H - 42, SIDE_W - 24, 14))
        foot.setAutoresizingMask_(NSViewMinYMargin)
        self._side_foot = foot
        bar.addSubview_(foot)
        cred = ui.label("© 2026 Kloeth Digital B.V.", 10, color=theme.FAINT)
        cred.setFrame_(NSMakeRect(16, WIN_H - 26, SIDE_W - 24, 13))
        cred.setAutoresizingMask_(NSViewMinYMargin)
        self._side_cred = cred
        bar.addSubview_(cred)
        return bar

    # ---------- tab-wissel ----------
    @objc.python_method
    def show_tab(self, i, keep_scroll=None):
        if keep_scroll is None:      # echte tab-wissel of 4s-tik: status opnieuw ophalen
            self._status_cache = None
        self._current = i
        for n, item in enumerate(self._navitems):
            item.set_selected(n == i)
        v, h = self._tab_view(i)
        self._doc_natural_h = h
        # de documentView vult minstens de zichtbare hoogte, zodat de achtergrond
        # doorloopt bij een hoog venster (en scrolt als de content langer is).
        visible_h = self._scroll.contentView().bounds().size.height or WIN_H
        doc_h = max(h, visible_h)
        v.setFrame_(NSMakeRect(0, 0, self._content_w, doc_h))
        # Layer-backed: bij een historie van honderden rijen staan er ~1700 transparante,
        # zelf-tekenende custom-views in de documentView. Zonder lagen kan de scrollview
        # (drawsBackground=False, niet-opake content) geen copy-on-scroll doen en hertekent
        # 'ie elke scrollstap de hele zichtbare inhoud -> hakkelen. Met wantsLayer composit
        # de GPU cached lagen en wordt scrollen soepel. Geldt voor elke tab (goedkoop).
        v.setWantsLayer_(True)
        self._built_w = self._content_w   # onthoud op welke breedte deze tab gebouwd is
        self._scroll.setDocumentView_(v)
        clip = self._scroll.contentView()
        if keep_scroll is not None:               # resize: houd de scroll-positie vast
            y = min(max(0.0, keep_scroll), max(0.0, doc_h - visible_h))
            clip.scrollToPoint_(NSMakePoint(0, y))
        else:                                     # tab-wissel: terug naar boven (y 0)
            clip.scrollToPoint_(NSMakePoint(0, 0))
        self._scroll.reflectScrolledClipView_(clip)

    @objc.python_method
    def _tab_view(self, i):
        if i == 0:
            return self._overzicht_view()
        if i == 1:
            return self._historie_view()
        if i == 2:
            return self._woordenlijst_view()
        return self._instellingen_view()

    # ---------- tabs ----------
    @objc.python_method
    def _instellingen_view(self):
        ctrl = prefs.PrefsController.alloc().init()
        self._prefs_ctrl = ctrl          # in leven houden: de acties hangen eraan
        # vult de volledige content-breedte mee (rijen zetten hun control rechts uit),
        # zoals de andere tabs -- niet langer een vaste 470-kolom in het midden.
        return ctrl.build_view(self._content_w)

    # ---------- Historie ----------
    @objc.python_method
    def _hist_items(self, query):
        """history.search(query), maar gecachet per history.mtime() -- zodat een
        venster-resize (die de hele tab herbouwt) niet elke keer de jsonl opnieuw
        leest. De mtime verandert bij append/wis, dus de cache invalideert zichzelf."""
        m = history.mtime()
        if self._hist_cache_m != m:
            self._hist_cache = {}
            self._hist_cache_m = m
        if query not in self._hist_cache:
            self._hist_cache[query] = history.search(query)
        return self._hist_cache[query]

    @objc.python_method
    def _historie_view(self):
        cw = self._content_w
        v = ui.Flipped.alloc().initWithFrame_(NSMakeRect(0, 0, cw, WIN_H))
        inner_w = cw - 2 * ui.PAD
        y = 24
        title = ui.label("Historie", 19, "bold")
        title.sizeToFit()
        tw = title.frame().size.width
        title.setFrame_(NSMakeRect(ui.PAD, y, tw, 24))
        v.addSubview_(title)

        if not settings.get("history_enabled"):
            y += 34
            return self._history_optin(v, y, inner_w)

        # privacy-belofte als groene badge, direct naast de titel (deel van de kop)
        self._privacy_badge(v, ui.PAD + tw + 12, y + 2)
        # zoekveld rechts op dezelfde regel
        search_w = min(230.0, max(150.0, inner_w * 0.42))
        search = NSSearchField.alloc().initWithFrame_(
            NSMakeRect(cw - ui.PAD - search_w, y - 1, search_w, 26))
        try:
            search.setPlaceholderString_("Zoek in dictaten…")
        except Exception:
            pass
        search.setTarget_(self)
        search.setAction_("historySearch:")
        search.setStringValue_(self._hist_query or "")
        self._hist_search = search
        v.addSubview_(search)
        y += 36

        # meta-regel: bewaartermijn + klei-links "Wis alles" / "Zet uit"
        days = settings.get("history_days")
        keep = ("altijd bewaard" if not days
                else f"bewaart {days} dagen, daarna vanzelf weg")
        meta = ui.label(f"Aan · {keep} · ", 11.5, color=theme.TEXT2)
        meta.sizeToFit()
        mw = meta.frame().size.width
        meta.setFrame_(NSMakeRect(ui.PAD, y, mw, 16))
        v.addSubview_(meta)
        x = ui.PAD + mw
        clr = self._link_btn("Wis alles", "historyClear:")
        cwid = clr.frame().size.width
        clr.setFrame_(NSMakeRect(x, y - 1, cwid, 18))
        v.addSubview_(clr)
        x += cwid + 2
        dot = ui.label("·", 11.5, color=theme.FAINT)
        dot.setFrame_(NSMakeRect(x, y, 8, 16))
        v.addSubview_(dot)
        x += 12
        off = self._link_btn("Zet uit", "historyOff:")
        off.setFrame_(NSMakeRect(x, y - 1, off.frame().size.width, 18))
        v.addSubview_(off)
        y += 28

        self._hist_header_h = y
        listc = ui.Flipped.alloc().initWithFrame_(NSMakeRect(0, y, cw, 10))
        self._hist_list = listc
        v.addSubview_(listc)
        list_h = self._fill_history_list(listc, self._hist_query)
        listc.setFrameSize_((cw, list_h))
        return v, y + list_h + ui.PAD

    @objc.python_method
    def _privacy_badge(self, v, x, y):
        lbl = ui.label("Alleen op deze Mac", 11.5, "medium", color=_rgb(_GREEN))
        lbl.sizeToFit()
        tw = lbl.frame().size.width
        w = 22 + tw + 10
        pill = ui.fill(NSMakeRect(x, y, w, 22), _rgb(_GREEN, 0.13), 11)
        d = ui.label("●", 7, color=_rgb(_GREEN))
        d.setFrame_(NSMakeRect(10, 6, 8, 11))
        pill.addSubview_(d)
        lbl.setFrame_(NSMakeRect(22, 3, tw, 16))
        pill.addSubview_(lbl)
        v.addSubview_(pill)
        return w

    @objc.python_method
    def _link_btn(self, title, action, tag=0):
        b = NSButton.buttonWithTitle_target_action_(title, self, action)
        b.setBordered_(False)
        b.setFont_(NSFont.systemFontOfSize_(11.5))
        b.setContentTintColor_(_rgb(_CLAY))
        b.setTag_(tag)
        b.sizeToFit()
        return b

    @objc.python_method
    def _app_chip(self, container, x, y, text):
        lbl = ui.label(text, 10.5, "medium", color=theme.TEXT2)
        lbl.sizeToFit()
        tw = min(lbl.frame().size.width, 120)
        w = tw + 16
        chip = ui.fill(NSMakeRect(x, y, w, 20), theme.CHIP, 6)
        lbl.setFrame_(NSMakeRect(8, 2, tw, 15))
        chip.addSubview_(lbl)
        container.addSubview_(chip)
        return w

    @objc.python_method
    def _history_optin(self, v, y, inner_w):
        items = ["Een leesbaar bestand op je eigen schijf — geen cloud, geen netwerk",
                 "Bewaart 30 dagen (in te stellen), daarna vanzelf weg",
                 "Uitzetten of alles wissen kan altijd, met één klik"]
        card_h = 150 + len(items) * 22
        card = _card(NSMakeRect(ui.PAD, y, inner_w, card_h))
        t = ui.label("Dictaten bewaren op deze Mac?", 15, "bold")
        t.setFrame_(NSMakeRect(16, 16, inner_w - 32, 20))
        card.addSubview_(t)
        body = NSTextField.wrappingLabelWithString_(
            "Dan kun je ze later teruglezen, doorzoeken en opnieuw kopiëren — lokaal, "
            "met bestandsrechten 0600, nooit op het netwerk.")
        body.setFont_(NSFont.systemFontOfSize_(12.5))
        body.setTextColor_(theme.TEXT2)
        body.setFrame_(NSMakeRect(16, 42, inner_w - 32, 38))
        card.addSubview_(body)
        cy = 86
        for it in items:
            chk = ui.label("✓", 11.5, "bold", color=_rgb(_GREEN))
            chk.setFrame_(NSMakeRect(16, cy, 14, 15))
            card.addSubview_(chk)
            il = ui.label(it, 12, color=theme.TEXT2)
            il.setFrame_(NSMakeRect(34, cy, inner_w - 66, 16))
            card.addSubview_(il)
            cy += 22
        btn = NSButton.buttonWithTitle_target_action_("Bewaar lokaal", self, "historyEnable:")
        btn.setBezelStyle_(1)
        btn.sizeToFit()
        bw = max(btn.frame().size.width, 130)
        btn.setFrame_(NSMakeRect(16, card_h - 42, bw, 30))
        card.addSubview_(btn)
        v.addSubview_(card)
        return v, y + card_h + ui.PAD

    @objc.python_method
    def _fill_history_list(self, c, query):
        for s in list(c.subviews()):
            s.removeFromSuperview()
        items = self._hist_items(query)
        self._hist_shown = items
        inner_w = self._content_w - 2 * ui.PAD
        if not items:
            msg = "Geen dictaten gevonden." if query else "Nog niets bewaard."
            ui.card_group(c, ui.PAD, 4, inner_w, [44],
                          lambda cc, i, top, w, _h: self._empty_row(cc, top, w, msg))
            return 4 + 44 + 8
        # groepeer per dag
        today = datetime.now().date()
        groups = []
        for idx, e in enumerate(items):
            d = datetime.fromtimestamp(e.get("ts", 0)).date()
            if not groups or groups[-1][0] != d:
                groups.append((d, []))
            groups[-1][1].append((idx, e))

        y = 0
        for d, rows in groups:
            delta = (today - d).days
            hdr = ("Vandaag" if delta == 0 else "Gisteren" if delta == 1
                   else _nl_date(datetime(d.year, d.month, d.day)))
            y += 8
            y = ui.glabel(c, ui.PAD, y, inner_w, hdr)

            def fill_row(container, ridx, top, rw, _h, rows=rows):
                idx, e = rows[ridx]
                dt = datetime.fromtimestamp(e.get("ts", 0))
                tm = ui.mono(dt.strftime("%H:%M"), 11, color=theme.FAINT)
                tm.setFrame_(NSMakeRect(12, top + 15, 42, 15))
                container.addSubview_(tm)
                appw = self._app_chip(container, 58, top + 13, e.get("app") or "—")
                # rechter cluster: Kopieer (chip) + Wis (ghost), zoals de mockup
                by = top + (46 - 22) / 2
                wis = self._pill_button("Wis", "historyRemove:", idx, ghost=True)
                wisw = wis.frame().size.width
                wis_x = rw - 12 - wisw
                wis.setFrame_(NSMakeRect(wis_x, by, wisw, 22))
                container.addSubview_(wis)
                kop = self._pill_button("Kopieer", "historyCopy:", idx)
                kopw = kop.frame().size.width
                kop_x = wis_x - 8 - kopw
                kop.setFrame_(NSMakeRect(kop_x, by, kopw, 22))
                container.addSubview_(kop)
                took = e.get("took")
                wc_text = f"{e.get('words', 0)} w" + (f" · {_nl_dec(took, 1)} s" if took else "")
                wc = ui.label(wc_text, 10.5, color=theme.FAINT)
                wc.setAlignment_(NSTextAlignmentRight)
                wc_w, wc_x = 84.0, kop_x - 10 - 84
                wc.setFrame_(NSMakeRect(wc_x, top + 16, wc_w, 14))
                container.addSubview_(wc)
                tx_x = 58 + appw + 10
                tx_w = max(40.0, wc_x - 10 - tx_x)
                txt = e.get("text", "")
                cap = int(tx_w / 6.2)
                shown = txt if len(txt) <= cap else txt[:max(1, cap - 1)] + "…"
                tl = ui.label(shown, 12.5)
                tl.setFrame_(NSMakeRect(tx_x, top + 15, tx_w, 16))
                container.addSubview_(tl)

            y = ui.card_group(c, ui.PAD, y, inner_w, [46] * len(rows), fill_row)
            y += 6
        return y + 8

    # historie-acties
    def historyEnable_(self, _sender):
        settings.set("history_enabled", True)
        self.show_tab(1)

    def historySearch_(self, sender):
        self._hist_query = sender.stringValue()
        h = self._fill_history_list(self._hist_list, self._hist_query)
        cw = self._content_w
        self._hist_list.setFrameSize_((cw, h))
        visible_h = self._scroll.contentView().bounds().size.height or WIN_H
        doc = self._scroll.documentView()
        doc.setFrameSize_((cw, max(self._hist_header_h + h + ui.PAD, visible_h)))

    def historyCopy_(self, sender):
        i = sender.tag()
        if 0 <= i < len(self._hist_shown):
            pb = NSPasteboard.generalPasteboard()
            pb.clearContents()
            pb.setString_forType_(self._hist_shown[i].get("text", ""), NSPasteboardTypeString)
            sender.flash_copied()

    def historyRemove_(self, sender):
        i = sender.tag()
        if 0 <= i < len(self._hist_shown):
            history.remove(self._hist_shown[i].get("ts"))
            self.historySearch_(self._hist_search)

    def historyClear_(self, _sender):
        history.clear()
        self.show_tab(1)

    def historyOff_(self, _sender):
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Historie uitzetten")
        alert.setInformativeText_(
            "Wil je de bewaarde dictaten ook wissen, of behouden op deze Mac?")
        alert.addButtonWithTitle_("Wissen")       # NSAlertFirstButtonReturn  (1000)
        alert.addButtonWithTitle_("Behouden")     # NSAlertSecondButtonReturn (1001)
        alert.addButtonWithTitle_("Annuleren")    # NSAlertThirdButtonReturn  (1002)
        r = alert.runModal()
        if r == 1002:
            return
        if r == 1000:
            history.clear()
        settings.set("history_enabled", False)
        self.show_tab(1)

    # ---------- Woordenlijst (de leer-loop met een gezicht) ----------
    @objc.python_method
    def _woordenlijst_view(self):
        cw = self._content_w
        v = ui.Flipped.alloc().initWithFrame_(NSMakeRect(0, 0, cw, WIN_H))
        inner_w = cw - 2 * ui.PAD
        self._sugg = []
        self._term_list = []
        self._map_list = []
        y = 24
        title = ui.label("Woordenlijst", 19, "bold")
        title.setFrame_(NSMakeRect(ui.PAD, y, inner_w, 24))
        v.addSubview_(title)
        y += 28
        sub = NSTextField.wrappingLabelWithString_(
            "Wordt bij elk dictaat opnieuw gelezen — een woord toevoegen werkt meteen, "
            "zonder herstart.")
        sub.setFont_(NSFont.systemFontOfSize_(12.5))
        sub.setTextColor_(theme.TEXT2)
        sub.setFrame_(NSMakeRect(ui.PAD, y, inner_w, 34))
        v.addSubview_(sub)
        y += 40

        # 1) Voorstellen uit de leer-loop -- één nette regel per woord (term · frequentie
        #    · knoppen), zoals de mockup. Gecapt op de meest gehoorde; de rest komt vanzelf
        #    weer langs. "Corrigeer…" opent een klein invoervenster i.p.v. een veld per rij.
        sugg_all = lexicon.suggestions()
        sugg = sugg_all[:8]
        if sugg:
            y = ui.glabel(v, ui.PAD, y, inner_w, "Voorstellen", "vaak gezegd, nog onbekend")

            def fill_sugg(c, idx, top, w, _h):
                word, count = sugg[idx]
                self._sugg.append(word)
                term = ui.mono(word, 13, "medium")
                term.sizeToFit()
                tmw = min(term.frame().size.width, 200)
                term.setFrame_(NSMakeRect(16, top + 14, tmw, 18))
                c.addSubview_(term)
                # rechter cluster: chip-knop "Corrigeer naar…" + ghost "Negeer"
                by = top + (46 - 22) / 2
                neg = self._pill_button("Negeer", "wordIgnore:", idx, ghost=True)
                negw = neg.frame().size.width
                nx = w - 16 - negw
                neg.setFrame_(NSMakeRect(nx, by, negw, 22))
                c.addSubview_(neg)
                cor = self._pill_button("Corrigeer naar…", "wordCorrect:", idx)
                corw = cor.frame().size.width
                cx = nx - 10 - corw
                cor.setFrame_(NSMakeRect(cx, by, corw, 22))
                c.addSubview_(cor)
                freq = ui.label(f"{count}× gehoord deze week", 11.5, color=theme.FAINT)
                fx = 16 + tmw + 12
                freq.setFrame_(NSMakeRect(fx, top + 15, max(60.0, cx - 12 - fx), 16))
                c.addSubview_(freq)

            y = ui.card_group(v, ui.PAD, y, inner_w, [46] * len(sugg), fill_sugg)
            if len(sugg_all) > len(sugg):
                more = ui.label(
                    f"+ {len(sugg_all) - len(sugg)} meer voorstellen — behandel de meest "
                    "gehoorde eerst.", 11, color=theme.FAINT)
                more.setFrame_(NSMakeRect(ui.PAD + 2, y + 6, inner_w - 4, 15))
                v.addSubview_(more)
                y += 22
            y += ui.SEC_GAP

        # 2) Eigen termen als pill-chips in één kaart. Ambigu = gestreept, plus een
        #    klei "+ Nieuwe term"-chip die de lexicon-lijst opent.
        custom_lc = {t.lower() for t in lexicon.custom_terms()}
        specs = []                       # (term, style, removable, tag)
        for t in lexicon.terms():
            is_ambig = t.lower() in lexicon.AMBIGUOUS
            removable = t.lower() in custom_lc
            tag = -1
            if removable:
                tag = len(self._term_list)
                self._term_list.append(t)
            specs.append((t, "dashed" if is_ambig else "solid", removable, tag))
        specs.append(("+ Nieuwe term", "add", False, -1))
        n_custom = len(self._term_list)
        y = ui.glabel(v, ui.PAD, y, inner_w, "Eigen termen",
                      f"{n_custom} — altijd in de juiste vorm geplakt")

        pad, gap_x, gap_y, chip_h = 14, 8, 8, 26
        cx, cy = pad, pad
        placed = []
        for term, style, removable, tag in specs:
            tw, w = self._pill_metrics(term, removable)
            if cx > pad and cx + w > inner_w - pad:
                cx = pad
                cy += chip_h + gap_y
            placed.append((cx, cy, term, tw, w, style, removable, tag))
            cx += w + gap_x
        card_h = cy + chip_h + pad
        card = _card(NSMakeRect(ui.PAD, y, inner_w, card_h))
        for cx, cy, term, tw, w, style, removable, tag in placed:
            if style == "add":
                btn = NSButton.buttonWithTitle_target_action_(
                    "+ Nieuwe term", self, "wordNew:")
                btn.setBordered_(False)
                btn.setFont_(NSFont.systemFontOfSize_(12))
                btn.setContentTintColor_(_rgb(_CLAY))
                btn.setFrame_(NSMakeRect(cx, cy + 3, w, 20))
                card.addSubview_(btn)
            else:
                self._place_pill(card, cx, cy, term, tw, w, style, removable, tag)
        v.addSubview_(card)
        y += card_h + 6
        note = ui.label("Gestreept = ook een gewoon woord (gaat mee, maar niet geforceerd "
                        "met hoofdletter).", 10.5, color=theme.FAINT)
        note.setFrame_(NSMakeRect(ui.PAD, y, inner_w, 14))
        v.addSubview_(note)
        y += 14 + ui.SEC_GAP

        # 3) Fonetische correcties
        y = ui.glabel(v, ui.PAD, y, inner_w, "Fonetische correcties",
                      "als SamFlow er net naast zit")
        maps_items = list(lexicon.mappings().items())
        if not maps_items:
            y = ui.card_group(v, ui.PAD, y, inner_w, [40], lambda c, i, top, w, _h:
                              self._empty_row(c, top, w,
                                              "Nog geen correcties — voeg er zelf een toe of behandel een voorstel."))
        else:
            def fill_map(c, idx, top, w, _h):
                heard, canon = maps_items[idx]
                src = ui.mono(heard, 12, color=theme.TEXT2)
                src.setFrame_(NSMakeRect(14, top + 11, 110, 16))
                c.addSubview_(src)
                arr = ui.label("→", 12, color=theme.FAINT)
                arr.setFrame_(NSMakeRect(130, top + 11, 16, 16))
                c.addSubview_(arr)
                dst = ui.label(canon, 12.5, "medium")
                dst.setFrame_(NSMakeRect(152, top + 10, w - 152 - 56, 18))
                c.addSubview_(dst)
                wis = NSButton.buttonWithTitle_target_action_("wis", self, "mapRemove:")
                wis.setBordered_(False)
                wis.setFont_(NSFont.systemFontOfSize_(11.5))
                wis.setContentTintColor_(_rgb(_CLAY))
                wis.setTag_(idx)
                wis.setFrame_(NSMakeRect(w - 12 - 40, top + 9, 40, 20))
                c.addSubview_(wis)
                self._map_list.append(heard)

            y = ui.card_group(v, ui.PAD, y, inner_w, [38] * len(maps_items), fill_map)
        # "+ Nieuwe correctie": zelfde in-app-lijn als "+ Nieuwe term" -- opent het gebrande
        # paneel met twee velden. add_mapping maakt mappings.txt zo nodig aan.
        addc = NSButton.buttonWithTitle_target_action_("+ Nieuwe correctie", self, "mapNew:")
        addc.setBordered_(False)
        addc.setFont_(NSFont.systemFontOfSize_(12))
        addc.setContentTintColor_(_rgb(_CLAY))
        addc.setFrame_(NSMakeRect(ui.PAD + 2, y + 8, 170, 20))
        v.addSubview_(addc)
        y += 30
        y += ui.PAD
        return v, y

    @objc.python_method
    def _pill_button(self, title, action, tag, ghost=False):
        return _PillButton.alloc().initWithTitle_target_action_tag_ghost_(
            title, self, action, tag, ghost)

    @objc.python_method
    def _empty_row(self, c, top, w, text):
        lbl = ui.label(text, 12, color=theme.FAINT)
        lbl.setFrame_(NSMakeRect(14, top + 12, w - 28, 16))
        c.addSubview_(lbl)

    @objc.python_method
    def _pill_metrics(self, term, removable):
        lbl = ui.label(term, 12, "medium")
        lbl.sizeToFit()
        tw = lbl.frame().size.width
        return tw, tw + 24 + (18 if removable else 0)

    @objc.python_method
    def _place_pill(self, container, x, y, term, tw, w, style, removable, tag):
        color = theme.TEXT2 if style == "dashed" else theme.TEXT
        chip = _Chip.alloc().initWithFrame_style_(NSMakeRect(x, y, w, 26), style)
        lbl = ui.label(term, 12, "medium", color=color)
        lbl.setFrame_(NSMakeRect(12, 5, tw, 16))
        chip.addSubview_(lbl)
        if removable:
            rm = NSButton.buttonWithTitle_target_action_("×", self, "termRemove:")
            rm.setBordered_(False)
            rm.setFont_(NSFont.systemFontOfSize_(13))
            rm.setContentTintColor_(theme.FAINT)
            rm.setTag_(tag)
            # volle chip-hoogte: de knop centreert het "×" dan zelf verticaal op het
            # chip-midden, gelijk met de term-tekst (voorheen y=4/h=20 -> 1px te laag).
            rm.setFrame_(NSMakeRect(12 + tw + 2, 0, 20, 26))
            chip.addSubview_(rm)
        container.addSubview_(chip)

    # woordenlijst-acties (rebouwen de tab na elke mutatie; mtime-cache is al invalide)
    def wordCorrect_(self, sender):
        # De primaire actie op een voorstel: open het gebrande paneel voorgevuld met het
        # gehoorde woord. Laat je de tekst staan, dan nemen we het woord over zoals gehoord
        # (accept); pas je 'm aan, dan is het een fonetische correctie (map_to gehoord ->
        # canoniek). Zo dekt één knop "Voeg toe" én "Corrigeer naar…" uit de mockup.
        i = sender.tag()
        if 0 <= i < len(self._sugg):
            self._present_sheet("correct", self._sugg[i])

    def wordIgnore_(self, sender):
        i = sender.tag()
        if 0 <= i < len(self._sugg):
            lexicon.ignore(self._sugg[i])
            self.show_tab(2)

    def wordNew_(self, _sender):
        # "+ Nieuwe term": open het gebrande invoer-paneel (meerregelig, meerdere termen
        # tegelijk). Vroeger opende dit lexicon.txt in een teksteditor -- dat deed niets
        # als het bestand nog niet bestond, en zag er niet uit als de app.
        self._present_sheet("term")

    def mapNew_(self, _sender):
        # "+ Nieuwe correctie": open het gebrande invoer-paneel met twee velden
        # (gehoord -> canoniek).
        self._present_sheet("map")

    def termRemove_(self, sender):
        i = sender.tag()
        if 0 <= i < len(self._term_list):
            lexicon.remove_term(self._term_list[i])
            self.show_tab(2)

    def mapRemove_(self, sender):
        i = sender.tag()
        if 0 <= i < len(self._map_list):
            lexicon.remove_mapping(self._map_list[i])
            self.show_tab(2)

    # ---------- gebrand invoer-paneel (vervangt de kale NSAlert) ----------
    @objc.python_method
    def _rounded_field(self, placeholder, w):
        f = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, w, 30))
        f.setPlaceholderString_(placeholder)
        f.setFont_(NSFont.systemFontOfSize_(13))
        f.setBezelStyle_(NSTextFieldRoundedBezel)
        f.setFocusRingType_(1)                  # NSFocusRingTypeNone -- rustiger, past bij Helder
        return f

    @objc.python_method
    def _present_sheet(self, mode, word=None):
        """Toon een gebrand invoer-paneel als sheet op het hoofdvenster. mode 'term' geeft
        een meerregelig veld (meerdere termen tegelijk, één per regel); 'map' geeft twee
        velden (gehoord -> canoniek); 'correct' geeft één veld voorgevuld met een gehoord
        voorstel-woord. De invoer wordt in sheetAdd_ afgehandeld."""
        if self._sheet_win is not None:         # al een sheet open? niet stapelen
            return
        self._sheet_mode = mode
        self._sheet_word = word
        W_, P = 440, 26
        iw = W_ - 2 * P
        v = ui.Flipped.alloc().initWithFrame_(NSMakeRect(0, 0, W_, 320))
        y = P

        if mode == "term":
            title = "Nieuwe termen"
            sub = "Projectnamen, merken of jargon. Schrijf ze zoals je ze geplakt wilt zien."
        elif mode == "correct":
            title = f"“{word}” toevoegen of corrigeren"
            sub = "Laat staan om zo toe te voegen, of pas aan naar de juiste schrijfwijze."
        else:
            title = "Nieuwe correctie"
            sub = "Als SamFlow een woord fonetisch net verkeerd hoort."
        t = ui.label(title, 17, "bold")
        t.setFrame_(NSMakeRect(P, y, iw, 24))
        v.addSubview_(t)
        y += 30
        # Afbrekend label: de zin is breder dan het paneel, dus een enkelregelig label
        # kapte 'm af. wrappingLabelWithString_ laat 'm netjes over twee regels lopen.
        s = NSTextField.wrappingLabelWithString_(sub)
        s.setFont_(NSFont.systemFontOfSize_(12.5))
        s.setTextColor_(theme.TEXT2)
        s.setFrame_(NSMakeRect(P, y, iw, 34))
        v.addSubview_(s)
        y += 42

        if mode == "term":
            fh = 140
            v.addSubview_(_card(NSMakeRect(P, y, iw, fh)))
            sc = NSScrollView.alloc().initWithFrame_(NSMakeRect(P + 2, y + 2, iw - 4, fh - 4))
            sc.setDrawsBackground_(False)
            sc.setBorderType_(NSNoBorder)
            sc.setHasVerticalScroller_(True)
            tv = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, iw - 4, fh - 4))
            tv.setDrawsBackground_(False)
            tv.setRichText_(False)
            tv.setFont_(NSFont.systemFontOfSize_(13))
            tv.setTextColor_(theme.TEXT)
            tv.setInsertionPointColor_(theme.CLAY)
            tv.setTextContainerInset_((8, 8))
            tv.setAutomaticQuoteSubstitutionEnabled_(False)
            tv.setAutomaticDashSubstitutionEnabled_(False)
            tv.setAutomaticTextReplacementEnabled_(False)
            tv.setAutomaticSpellingCorrectionEnabled_(False)
            sc.setDocumentView_(tv)
            v.addSubview_(sc)
            self._sheet_text = tv
            y += fh + 8
            hint = ui.label("Eén per regel — plak gerust een hele lijst.", 11, color=theme.FAINT)
            hint.setFrame_(NSMakeRect(P, y, iw, 15))
            v.addSubview_(hint)
            y += 24
        elif mode == "map":
            heard = self._rounded_field("SamFlow hoort (bijv. klavijo)", iw)
            heard.setFrameOrigin_(NSMakePoint(P, y))
            v.addSubview_(heard)
            self._sheet_heard = heard
            y += 38
            arrow = ui.label("↓ wordt", 11.5, color=theme.FAINT)
            arrow.setFrame_(NSMakeRect(P + 2, y, iw, 15))
            v.addSubview_(arrow)
            y += 20
            canon = self._rounded_field("Moet worden (bijv. Klaviyo)", iw)
            canon.setFrameOrigin_(NSMakePoint(P, y))
            v.addSubview_(canon)
            self._sheet_canon = canon
            heard.setNextKeyView_(canon)
            y += 40
        else:                                    # correct: één veld, voorgevuld
            field = self._rounded_field("", iw)
            field.setStringValue_(word)
            field.setFrameOrigin_(NSMakePoint(P, y))
            v.addSubview_(field)
            self._sheet_heard = field
            y += 40

        # knoppenrij, rechts uitgelijnd: klei-primair + ghost-annuleren
        add_label = "Bewaar" if mode == "correct" else "Toevoegen"
        add = _ClayButton.alloc().initWithTitle_target_action_(add_label, self, "sheetAdd:")
        aw = add.frame().size.width
        add.setFrameOrigin_(NSMakePoint(W_ - P - aw, y))
        v.addSubview_(add)
        cancel = _PillButton.alloc().initWithTitle_target_action_tag_ghost_(
            "Annuleren", self, "sheetCancel:", 0, True)
        cw = cancel.frame().size.width
        cancel.setFrameOrigin_(NSMakePoint(W_ - P - aw - 10 - cw, y + 4))
        v.addSubview_(cancel)
        y += 30 + P

        v.setFrame_(NSMakeRect(0, 0, W_, y))
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W_, y), NSWindowStyleMaskTitled, NSBackingStoreBuffered, False)
        win.setContentView_(v)
        win.setBackgroundColor_(theme.WINDOW)
        win.setReleasedWhenClosed_(False)       # wij houden de ref in _sheet_win vast
        self._sheet_win = win
        self.window.beginSheet_completionHandler_(win, None)
        win.makeFirstResponder_(self._sheet_text if mode == "term" else self._sheet_heard)
        if mode == "correct":                    # voorgevuld: selecteer alles zodat je
            self._sheet_heard.selectText_(None)  # meteen kunt overtypen

    @objc.python_method
    def _end_sheet(self):
        if self._sheet_win is not None:
            self.window.endSheet_(self._sheet_win)
            self._sheet_win = None
            self._sheet_text = self._sheet_heard = self._sheet_canon = None
            self._sheet_word = None

    def sheetCancel_(self, _sender):
        self._end_sheet()

    def sheetAdd_(self, _sender):
        mode = self._sheet_mode
        if mode == "term":
            raw = self._sheet_text.string() if self._sheet_text is not None else ""
            existing = {t.lower() for t in lexicon.terms()}
            added = 0
            for line in raw.splitlines():        # één term per regel; spaties in een term blijven
                term = line.strip()
                if term and term.lower() not in existing and lexicon.add_term(term):
                    existing.add(term.lower())
                    added += 1
            self._end_sheet()
            if added:
                self.show_tab(2)
        elif mode == "map":
            heard = self._sheet_heard.stringValue().strip() if self._sheet_heard else ""
            canon = self._sheet_canon.stringValue().strip() if self._sheet_canon else ""
            self._end_sheet()
            if heard and len(canon) >= 2:        # add_mapping weigert een te kort doel zelf ook
                lexicon.add_mapping(heard, canon)
                self.show_tab(2)
        else:                                    # correct: laat je 't staan -> accept,
            target = self._sheet_heard.stringValue().strip() if self._sheet_heard else ""
            word = self._sheet_word              # pas je 't aan -> map_to (gehoord -> canoniek)
            self._end_sheet()
            if target:
                if target == word:
                    lexicon.accept(word, word)
                else:
                    lexicon.map_to(word, target)
                self.show_tab(2)

    # ---------- dashboard-bouwstenen ----------
    @objc.python_method
    def _hero_chip_w(self, bold, value):
        """De totale breedte van een hero-chip (stip + vet label + gedimde waarde),
        gemeten vóór plaatsing zodat we weten wanneer we naar een tweede regel moeten
        wrappen. Meet met wegwerp-labels; goedkoop en exact."""
        b = ui.label(bold, 11.5, "medium")
        b.sizeToFit()
        val = ui.label("— " + value, 11.5)
        val.sizeToFit()
        return 15 + b.frame().size.width + 5 + val.frame().size.width

    @objc.python_method
    def _hero_chip(self, hero, x, y, bold, value, ok):
        """Eén status-chip ín de grafiet-band: gekleurde stip + vet label + gedimde
        waarde, alles in wit-tinten op grafiet. `ok` mag None zijn (nog onbekend ->
        gedimde stip). Geeft (de stip, het waarde-label) terug zodat de server-chip
        later async bijgewerkt kan worden."""
        color = (_white(0.42) if ok is None
                 else _rgb(_GREEN) if ok else NSColor.systemOrangeColor())
        dot = ui.label("●", 8.5, color=color)
        dot.setFrame_(NSMakeRect(x, y + 3, 10, 12))
        hero.addSubview_(dot)
        bl = ui.label(bold, 11.5, "medium", color=_white(0.95))
        bl.sizeToFit()
        bw = bl.frame().size.width
        bl.setFrame_(NSMakeRect(x + 15, y, bw, 15))
        hero.addSubview_(bl)
        vl = ui.label("— " + value, 11.5, color=_white(0.6))
        vl.sizeToFit()
        vw = vl.frame().size.width
        vl.setFrame_(NSMakeRect(x + 15 + bw + 5, y, vw, 15))
        hero.addSubview_(vl)
        return dot, vl

    @objc.python_method
    def _stat_tile(self, v, x, y, w, label, value, sub):
        card = _card(NSMakeRect(x, y, w, 76))
        l = ui.label(label.upper(), 10.5, color=theme.FAINT)
        l.setFrame_(NSMakeRect(13, 11, w - 26, 14))
        card.addSubview_(l)
        val = ui.label(value, 21, "bold")
        val.setFrame_(NSMakeRect(13, 26, w - 26, 28))
        card.addSubview_(val)
        s = ui.label(sub, 11, color=theme.TEXT2)
        s.setFrame_(NSMakeRect(13, 55, w - 26, 15))
        card.addSubview_(s)
        v.addSubview_(card)

    @objc.python_method
    def _overzicht_view(self):
        # stats uit de mtime-cache: een venster-resize herbouwt het hele dashboard,
        # maar mag daarvoor niet elke keer stats.json lezen (zie ook refreshTick_).
        m = stats.mtime()
        if self._stats_cache is not None and self._stats_cache[0] == m:
            s = self._stats_cache[1]
        else:
            try:
                s = stats.summary()
            except Exception:
                s = None
            self._stats_cache = (m, s)
        self._stats_mtime = m
        now = datetime.now()
        cw = self._content_w
        v = ui.Flipped.alloc().initWithFrame_(NSMakeRect(0, 0, cw, WIN_H))
        inner_w = cw - 2 * ui.PAD
        y = 20

        # --- grafiet-hero-band: het merk-moment. Datum rechtsboven, kleine gedimde
        #     groet, het getal met "woorden vandaag" inline, en de status als chips
        #     ín de band (groene stippen). De band-hoogte volgt uit het chip-wrappen. ---
        words_today = s["words_today"] if s else 0
        # status-waarden voor de chips -- gecachet: mic/rechten opvragen (CoreAudio +
        # TCC-preflight) is te duur om bij elke resize-reflow opnieuw te doen. Ververst
        # bij een tab-wissel of de 4s-tik (show_tab wist de cache dan).
        fresh = self._status_cache is None    # nav/tik (niet een resize-reflow)
        if fresh:
            mic_ok = prefs._mic_ok()
            if mic_ok:
                try:
                    # Live uit CoreAudio, niet via choose_input(): dat pad leunt op de
                    # bevroren PortAudio-lijst en zou hier na een AirPods-wissel een
                    # verdwenen apparaat als naam tonen. effective_input_name() is actueel
                    # én veilig terwijl er een opname loopt (geen PortAudio-aanraking).
                    mic_name = audiodev.effective_input_name()
                except Exception:
                    mic_name = None
                mic_val = mic_name or "toegekend"
                if len(mic_val) > 24:
                    mic_val = mic_val[:23] + "…"
            else:
                mic_val = "geen toegang"
            rights_ok = prefs._listen_ok() and prefs._post_ok()
            self._status_cache = (mic_ok, mic_val, rights_ok)
        mic_ok, mic_val, rights_ok = self._status_cache
        rights_val = "toegekend" if rights_ok else "actie nodig"
        # Model-chip uit de laatst bekende server-status -- niet elke reflow terug naar
        # "controleren…" (dat gaf geflikker tijdens het slepen).
        if self._server_up is True:
            model_val, model_ok = "warm", True
        elif self._server_up is False:
            model_val, model_ok = "uit", False
        else:
            model_val, model_ok = "controleren…", None
        chips = [("Microfoon", mic_val, mic_ok),
                 ("Rechten", rights_val, rights_ok),
                 ("Model", model_val, model_ok)]

        # chips flowen vanaf x=20, wrappen bij de rechterrand -> bepaal de band-hoogte
        chip_x0, chip_y0, chip_gap, row_h = 20, 90, 18, 22
        right_limit = inner_w - 18
        cx, cy = chip_x0, chip_y0
        placements = []
        for bold, value, ok in chips:
            w = self._hero_chip_w(bold, value)
            if cx > chip_x0 and cx + w > right_limit:
                cx = chip_x0
                cy += row_h
            placements.append((cx, cy, bold, value, ok))
            cx += w + chip_gap
        hero_h = cy + 15 + 15    # laatste chip-regel (15px) + ondermarge

        hero = _HeroBand.alloc().initWithFrame_(NSMakeRect(ui.PAD, y, inner_w, hero_h))
        hd = ui.label(_nl_date(now), 11.5, color=_white(0.5))
        hd.setAlignment_(NSTextAlignmentRight)
        hd.setFrame_(NSMakeRect(inner_w - 20 - 220, 14, 220, 15))
        hero.addSubview_(hd)
        hg = ui.label(_greeting(now), 13, "medium", color=_white(0.72))
        hg.setFrame_(NSMakeRect(20, 16, inner_w - 60, 18))
        hero.addSubview_(hg)
        hn = ui.label(_nl_int(words_today), 30, "bold", color=_white(1.0))
        hn.sizeToFit()
        nw = hn.frame().size.width
        hn.setFrame_(NSMakeRect(20, 37, nw, 38))
        hero.addSubview_(hn)
        hsub = ui.label("woorden vandaag", 14, color=_white(0.62))
        hsub.setFrame_(NSMakeRect(20 + nw + 8, 51, inner_w - 40 - nw, 18))
        hero.addSubview_(hsub)
        for cx, cy, bold, value, ok in placements:
            dot, vl = self._hero_chip(hero, cx, cy, bold, value, ok)
            if bold == "Model":
                self._server_dot, self._server_val = dot, vl
        v.addSubview_(hero)
        if fresh and not self._server_checking:
            self._server_checking = True
            threading.Thread(target=self._check_server_bg, daemon=True).start()
        y += hero_h + 16

        # --- stat-tegels: 4-op-een-rij als het breed genoeg is, anders terug naar 2x2 ---
        if s and s["delta"] is not None:
            d = s["delta"]
            wk_sub = f"{'▲' if d >= 0 else '▼'} {abs(d) * 100:.0f}% t.o.v. vorige week"
        else:
            wk_sub = "t.o.v. vorige week"
        fastest = s["fastest"] if s else None
        streak = s["streak"] if s else 0
        tiles = [
            ("Woorden deze week", _nl_int(s["words_week"]) if s else "0", wk_sub),
            ("Tijd bespaard", _dur_hm(s["saved_sec"]) if s else "≈ 0 m", "t.o.v. typen (40 wpm)"),
            ("Snelste dictaat", f"{_nl_dec(fastest, 1)} s" if fastest else "—", "transcriptietijd"),
            ("Reeks", f"{streak} {'dag' if streak == 1 else 'dagen'}", "aaneengesloten dagen"),
        ]
        gap, tile_h = 12, 76
        cols = 4 if inner_w >= STATS_4COL_W else 2
        col_w = (inner_w - (cols - 1) * gap) / cols
        for idx, (lbl, val, sub) in enumerate(tiles):
            r, c = divmod(idx, cols)
            self._stat_tile(v, ui.PAD + c * (col_w + gap), y + r * (tile_h + 10),
                            col_w, lbl, val, sub)
        rows = (len(tiles) + cols - 1) // cols
        y += rows * tile_h + (rows - 1) * 10 + 16

        # --- week-staafgrafiek ---
        chart_card = _card(NSMakeRect(ui.PAD, y, inner_w, 160))
        ch = ui.label("Woorden per dag", 13, "bold")
        ch.setFrame_(NSMakeRect(14, 12, inner_w - 120, 18))
        chart_card.addSubview_(ch)
        cs = ui.label("deze week", 11, color=theme.FAINT)
        cs.setAlignment_(NSTextAlignmentRight)
        cs.setFrame_(NSMakeRect(inner_w - 14 - 110, 14, 110, 15))
        chart_card.addSubview_(cs)
        week_words = s["week_words"] if s else [0] * 7
        today_index = s["today_index"] if s else now.weekday()
        chart = _WeekChart.alloc().initWithFrame_words_today_(
            NSMakeRect(12, 40, inner_w - 24, 108), week_words, today_index)
        chart_card.addSubview_(chart)
        v.addSubview_(chart_card)
        y += 160 + 20

        # --- Recent (alleen als historie aanstaat; anders leeg, zoals vroeger) ---
        if settings.get("history_enabled"):
            recent = self._hist_items("")[:3]
            if recent:
                y = ui.section(v, y, "Recent")
                for e in recent:
                    dt = datetime.fromtimestamp(e.get("ts", 0))
                    row = _card(NSMakeRect(ui.PAD, y, inner_w, 44))
                    meta = ui.label(f"{dt.strftime('%H:%M')} · {e.get('app') or '—'}", 10.5,
                                    color=theme.FAINT)
                    meta.setFrame_(NSMakeRect(12, 5, inner_w - 24, 14))
                    row.addSubview_(meta)
                    txt = e.get("text", "")
                    shown = txt if len(txt) <= 92 else txt[:91] + "…"
                    tl = ui.label(shown, 12, color=theme.TEXT2)
                    tl.setFrame_(NSMakeRect(12, 22, inner_w - 24, 16))
                    row.addSubview_(tl)
                    v.addSubview_(row)
                    y += 44 + 8
                y += 12
        return v, y

    # ---------- server-check (achtergrond) ----------
    @objc.python_method
    def _check_server_bg(self):
        try:
            import samflow                 # lui: breekt de cyclus samflow<->hud<->mainwindow
            up = bool(samflow.server_up())
        except Exception:
            up = False
        self._server_up = up
        self._server_checking = False
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "refreshServerChip:", None, False)

    def refreshServerChip_(self, _obj):
        up = self._server_up
        if self._server_dot is not None:
            self._server_dot.setTextColor_(
                _rgb(_GREEN) if up else NSColor.systemOrangeColor())
        if self._server_val is not None:
            self._server_val.setStringValue_("— warm" if up else "— uit")

    def refreshTick_(self, _t):
        # Ververs het dashboard alléén als het zichtbaar is én de stats-file écht
        # veranderd is (goedkope mtime-vergelijking, geen schijf-lezing per tik).
        if self._current != 0 or not self.window.isVisible():
            return
        if stats.mtime() == self._stats_mtime:
            return
        self.show_tab(0)

    # ---------- resize ----------
    def windowDidResize_(self, _note):
        # Live meelopen tijdens het slepen: reflow direct, maar gethrotteld tot ~30/s
        # zodat we de tab niet 60x/sec herbouwen (de dure lookups zijn gecachet, dus een
        # reflow is goedkoop). Een gewone NSTimer vuurt niet tijdens een muis-resize (de
        # run loop staat dan in tracking-mode) -- vandaar de directe aanpak hier. De
        # trailing-timer in NSRunLoopCommonModes garandeert nog één reflow op de exacte
        # eindmaat (ook bij een niet-live resize zoals de zoom-knop).
        now = time.monotonic()
        if now - self._last_reflow >= 0.033:
            self._last_reflow = now
            self._reflow()
        if self._resize_timer is not None:
            self._resize_timer.invalidate()
        self._resize_timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            0.06, self, "resizeCoalesced:", None, False)
        NSRunLoop.currentRunLoop().addTimer_forMode_(self._resize_timer, NSRunLoopCommonModes)

    def resizeCoalesced_(self, _t):
        self._resize_timer = None
        self._last_reflow = time.monotonic()
        self._reflow()

    def windowDidEndLiveResize_(self, _note):
        if self._resize_timer is not None:
            self._resize_timer.invalidate()
            self._resize_timer = None
        self._last_reflow = time.monotonic()
        self._reflow()

    @objc.python_method
    def _reflow(self):
        """Herbouw de huidige tab op de nieuwe content-breedte, met behoud van de
        scroll-positie -- 4- vs 2-koloms tegels, een bredere hero, enz. De masks doen
        de grove chrome-resize al; hier zetten we de content en de zijbalk-voet goed."""
        cv = self.window.contentView()
        w, h = cv.bounds().size.width, cv.bounds().size.height
        # De masks doen dit tijdens de sleep al; hier zetten we de chrome expliciet op
        # de eindmaat, zodat een resize nooit stil blijft steken mocht een mask niet pakken.
        if self._sidebar is not None:
            self._sidebar.setFrameSize_((SIDE_W, h))
        if self._side_hairline is not None:
            self._side_hairline.setFrame_(NSMakeRect(SIDE_W - 1, 0, 1, h))
        if self._side_foot is not None:
            self._side_foot.setFrame_(NSMakeRect(16, h - 42, SIDE_W - 24, 14))
        if self._side_cred is not None:
            self._side_cred.setFrame_(NSMakeRect(16, h - 26, SIDE_W - 24, 13))
        self._scroll.setFrame_(NSMakeRect(SIDE_W, 0, w - SIDE_W, h))
        clip = self._scroll.contentView()
        new_w = clip.bounds().size.width
        self._content_w = new_w
        visible_h = clip.bounds().size.height or WIN_H
        doc = self._scroll.documentView()
        # Staat de content al op deze breedte (bv. een puur verticale resize), dan niet
        # herbouwen -- alleen de doc-hoogte volgen zodat de achtergrond de zichtbare hoogte
        # vult. Anders herbouwen we de huidige tab live op de nieuwe breedte; met de
        # gecachete lookups is dat ~8 ms, net als het dashboard, dus soepel bij ~30/s.
        if abs(new_w - self._built_w) < 0.5:
            if doc is not None:
                doc.setFrameSize_((new_w, max(self._doc_natural_h, visible_h)))
            return
        self.show_tab(self._current, keep_scroll=clip.bounds().origin.y)

    def windowWillClose_(self, _note):
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None
        if self._resize_timer is not None:
            self._resize_timer.invalidate()
            self._resize_timer = None
        _on_close()


# =====================================================================
#  Openen + standalone
# =====================================================================
_win = None          # MainWindow-controller (tegen GC)
_standalone = False


def open_main_window(hud=None):
    """Open (of breng naar voren) het hoofdvenster. Vanuit een klik op de main
    thread; houdt de controller vast in _win."""
    global _win
    app = NSApplication.sharedApplication()
    if _win is None:
        _win = MainWindow.alloc().initWithHud_(hud)
    else:
        if hud is not None:
            _win._hud = hud
        # ververs de huidige tab, zodat bv. het laatste dictaat klopt
        _win.show_tab(_win._current if _win._current >= 0 else 0)
    app.activateIgnoringOtherApps_(True)
    _win.window.makeKeyAndOrderFront_(None)
    return _win


def _on_close():
    global _win
    _win = None
    if _standalone:
        NSApplication.sharedApplication().terminate_(None)


def _run_standalone():
    """Voor `samflow.py --window`: draai een eigen mini-app-loop om alleen het
    hoofdvenster los te bekijken (zonder de daemon)."""
    global _standalone
    _standalone = True
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    open_main_window(None)
    app.activateIgnoringOtherApps_(True)
    app.run()
