"""
prefs.py - het voorkeuren-venster en de eerste-start-wizard.

Twee echte NSWindows (in tegenstelling tot de pill in hud.py mógen die focus
pakken -- ze verschijnen door een bewuste klik, niet tijdens het plakken). De
app draait als menubalk-accessoire zonder dock-icoon, dus om een venster echt
naar voren te halen activeren we de app eenmalig (activateIgnoringOtherApps_).

De permissie-checks staan hier zélf (AVFoundation/Quartz), niet geïmporteerd uit
samflow.py: dat zou een import-cyclus geven (samflow -> hud -> prefs). Het zijn
drie preflight-calls; die kleine duplicatie is de ontkoppeling waard.

Alle AppKit-calls horen op de main thread. Deze vensters worden geopend vanuit
een menu-klik (main thread) of vanuit --prefs/--welcome (draait de app-loop),
dus dat klopt vanzelf.
"""
import os
import subprocess

import threading
import time

import objc
from AppKit import (
    NSAlert, NSApplication, NSApplicationActivationPolicyAccessory,
    NSBackingStoreBuffered, NSBezierPath, NSButton, NSColor,
    NSControlStateValueOff, NSControlStateValueOn, NSFont, NSMakeRect,
    NSSegmentedControl, NSSegmentSwitchTrackingSelectOne, NSTextAlignmentCenter,
    NSTextAlignmentRight, NSTextField, NSView, NSWindow,
    NSWindowStyleMaskClosable, NSWindowStyleMaskTitled,
)
from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
from Foundation import CFPreferencesCopyAppValue, NSObject, NSTimer
from Quartz import (
    CGPreflightListenEventAccess, CGPreflightPostEventAccess,
    CGRequestListenEventAccess, CGRequestPostEventAccess,
)

import appmode
import settings
import theme
import ui
import updater
# Gedeelde layout-bouwstenen wonen nu in ui.py (zie die module) zodat het
# hoofdvenster ze deelt; hier onder de vertrouwde privé-namen.
from ui import W, PAD, ROW_H, SEC_GAP
from ui import (Flipped as _Flipped, label as _label, section as _section,
                separator as _separator, row_label as _row_label,
                glabel as _glabel, card_group as _card_group, mono as _mono)

_CLAY = NSColor.colorWithSRGBRed_green_blue_alpha_(0.776, 0.482, 0.322, 1.0)

# ---------- layout ----------
APP_PATH = os.path.expanduser("~/Applications/SamFlow.app")

_PRIVACY_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_"
_KEYBOARD_PANE = "x-apple.systempreferences:com.apple.preference.keyboard"

LANG_LABELS = ["Nederlands", "English", "Automatisch"]
LANG_CODES = ["nl", "en", "auto"]

LOCK_LABELS = ["Uit", "Tik", "Dubbel-tik", "Fn+⌘", "Vasthouden"]
LOCK_CODES = ["off", "tap", "double", "chord", "hold"]

POS_LABELS = ["Bij cursor", "Onderin", "Vaste hoek"]
POS_CODES = ["caret", "bottom", "fixed"]

SIZE_LABELS = ["Compact", "Ruim", "Fors"]
SIZE_CODES = ["compact", "ruim", "fors"]

MOTION_LABELS = ["Soepel", "Kwiek"]
MOTION_CODES = ["soepel", "kwiek"]

MODE_LABELS = ["Basic", "App"]
MODE_CODES = ["basic", "app"]

RETAIN_LABELS = ["7 dagen", "30 dagen", "Altijd"]
RETAIN_VALUES = [7, 30, 0]     # 0 = altijd bewaren


# ---------- permissie-helpers (zelfstandig, zie module-docstring) ----------
def _mic_ok():
    return AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio) == 3


def _listen_ok():
    return bool(CGPreflightListenEventAccess())


def _post_ok():
    return bool(CGPreflightPostEventAccess())


def _fn_free():
    return CFPreferencesCopyAppValue("AppleFnUsageType", "com.apple.HIToolbox") == 0


def _request_all():
    """Vuur de echte macOS-dialogen af voor wat nog ontbreekt. macOS vraagt per
    permissie precies één keer ooit; daarna moet het via het paneel met de knop."""
    if AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio) == 0:
        AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            AVMediaTypeAudio, lambda granted: None)
    if not _listen_ok():
        CGRequestListenEventAccess()
    if not _post_ok():
        CGRequestPostEventAccess()
        AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})


def _open_privacy(which):
    subprocess.Popen(["open", _PRIVACY_PANE + which])


_ver_cache = [None]            # de versie verandert pas na een update (= herstart), dus 1x


def _short_version():
    if _ver_cache[0] is None:
        try:
            _ver_cache[0] = updater.short_version()
        except Exception:
            _ver_cache[0] = "?"
    return _ver_cache[0]


_login_cache = [0.0, True]     # [monotone tijd, waarde] -- osascript is traag; kort cachen


def _login_item_present():
    # Gecachet (5s): dit draait osascript (~100 ms). Zonder cache zou een venster-resize,
    # die de Instellingen-tab herbouwt, dit tientallen keren per seconde aanroepen.
    now = time.monotonic()
    if now - _login_cache[0] < 5.0:
        return _login_cache[1]
    try:
        out = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to get the name of every login item'],
            capture_output=True, text=True, timeout=3)
        val = "SamFlow" in (out.stdout or "")
    except Exception:
        val = True   # onbekend: install.sh zet 'm standaard, neem aan van wel
    _login_cache[0] = now
    _login_cache[1] = val
    return val


def _login_item_set(on):
    try:
        if on:
            subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to make login item at end '
                 f'with properties {{path:"{APP_PATH}", hidden:true}}'],
                capture_output=True, timeout=3)
        else:
            subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to delete '
                 '(every login item whose name is "SamFlow")'],
                capture_output=True, timeout=3)
    except Exception:
        pass


# =====================================================================
#  Voorkeuren -- controller + venster
# =====================================================================
class _ModeCard(NSView):
    """Eén selecteerbare mini-kaart voor de Basic/App-keuze (mockup .mopt): titel,
    omschrijving en een vinkje rechtsboven; geselecteerd krijgt 'ie een klei-rand.
    Klikken meldt de keuze bij de controller, die beide kaarten bijwerkt en de modus
    live toepast. Vervangt de kale segmented-schakelaar -- de betekenis (venster +
    dock erbij of niet) staat zo op de plek van de keuze zelf."""
    def initWithFrame_code_title_desc_owner_(self, frame, code, title, desc, owner):
        self = objc.super(_ModeCard, self).initWithFrame_(frame)
        if self is None:
            return None
        self._code = code
        self._owner = owner
        self._selected = False
        t = _label(title, 14, "bold")
        t.setFrame_(NSMakeRect(13, 12, frame.size.width - 44, 18))
        self.addSubview_(t)
        d = NSTextField.wrappingLabelWithString_(desc)
        d.setFont_(NSFont.systemFontOfSize_(11.5))
        d.setTextColor_(theme.TEXT2)
        d.setFrame_(NSMakeRect(13, 34, frame.size.width - 26, 36))
        self.addSubview_(d)
        chk = _label("✓", 10, "bold", color=NSColor.whiteColor())
        chk.setAlignment_(NSTextAlignmentCenter)
        chk.setFrame_(NSMakeRect(frame.size.width - 13 - 18, 13, 18, 15))
        chk.setHidden_(True)
        self.addSubview_(chk)
        self._chk = chk
        return self

    def isFlipped(self):
        return True

    def acceptsFirstMouse_(self, _ev):
        return True

    def mouseDown_(self, _ev):
        self._owner.selectMode_(self._code)

    @objc.python_method
    def set_selected(self, on):
        self._selected = on
        self._chk.setHidden_(not on)
        self.setNeedsDisplay_(True)

    def drawRect_(self, _rect):
        b = self.bounds()
        card = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(1, 1, b.size.width - 2, b.size.height - 2), 10, 10)
        theme.WINDOW.set()
        card.fill()
        if self._selected:
            _CLAY.set()
            card.setLineWidth_(1.5)
            card.stroke()
        else:
            theme.LINE2.set()
            card.setLineWidth_(1.0)
            card.stroke()
        # vinkje-cirkel rechtsboven
        ring = NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(b.size.width - 13 - 18, 12, 18, 18))
        if self._selected:
            _CLAY.set()
            ring.fill()
        else:
            theme.LINE2.set()
            ring.setLineWidth_(1.2)
            ring.stroke()


class PrefsController(NSObject):
    """Bouwt de voorkeuren-view en bezit de acties (toggles, segmenten). Losgemaakt
    van het venster zodat dezelfde view zowel het losse Voorkeuren-venster (--prefs,
    paneel-actie) als de Instellingen-tab van het hoofdvenster (mainwindow.py) vult
    -- één implementatie, twee plekken. build_view() geeft (view, hoogte) terug; de
    caller wikkelt 'm in een venster of zet 'm in een tab."""
    def init(self):
        self = objc.super(PrefsController, self).init()
        if self is None:
            return None
        self._tag_keys = {}        # Toggle.tag() -> settings-sleutel
        self._mode_cards = {}      # "basic"/"app" -> _ModeCard (live selectie)
        self._upd_info = None      # updater.check()-resultaat (achtergrond -> main thread)
        self._upd_link = None      # de "Controleer op updates"-knop
        return self

    # ---------- gegroepeerde rijen (mockup .group/.row) ----------
    @objc.python_method
    def _group(self, v, y, rows):
        """rows = lijst van (hoogte, filler(container, top, row_w)). Bouwt één SUNKEN-
        kaart met haarlijnen ertussen (ui.card_group) en geeft de y ná de kaart terug."""
        heights = [r[0] for r in rows]

        def dispatch(c, idx, top, rw, _h):
            rows[idx][1](c, top, rw)
        return _card_group(v, PAD, y, self._bw - 2 * PAD, heights, dispatch)

    @objc.python_method
    def _rowhead(self, c, top, rw, h, title, sub, reserve):
        tw = rw - 28 - reserve
        b = _label(title, 13, "medium")
        if sub:
            b.setFrame_(NSMakeRect(14, top + 8, tw, 18))
            c.addSubview_(b)
            s = _label(sub, 11.5, color=theme.TEXT2)
            s.setFrame_(NSMakeRect(14, top + 26, tw, 15))
            c.addSubview_(s)
        else:
            b.setFrame_(NSMakeRect(14, top + (h - 18) / 2, tw, 18))
            c.addSubview_(b)

    @objc.python_method
    def _grp_switch(self, key):
        title, sub = _ROW_TEXT[key]
        h = 52 if sub else 46

        def filler(c, top, rw):
            sw = ui.Toggle.alloc().init()
            sw.setFrame_(NSMakeRect(rw - 14 - 40, top + (h - 22) / 2, 40, 22))
            sw.setState_(NSControlStateValueOn if settings.get(key)
                         else NSControlStateValueOff)
            tag = len(self._tag_keys) + 1
            sw.setTag_(tag)
            self._tag_keys[tag] = key
            sw.setTarget_(self)
            sw.setAction_("toggleSwitch:")
            c.addSubview_(sw)
            self._rowhead(c, top, rw, h, title, sub, 60)
        return (h, filler)

    @objc.python_method
    def _grp_seg(self, title, sub, labels, codes, key, action, default_idx=0):
        h = 52 if sub else 46

        def filler(c, top, rw):
            try:
                idx = codes.index(settings.get(key))
            except ValueError:
                idx = default_idx
            seg = ui.Segmented.alloc().initWithLabels_selected_target_action_(
                labels, idx, self, action)
            sw = seg.frame().size.width
            seg.setFrame_(NSMakeRect(rw - 14 - sw, top + (h - 26) / 2, sw, 26))
            c.addSubview_(seg)
            self._rowhead(c, top, rw, h, title, sub, sw + 20)
        return (h, filler)

    @objc.python_method
    def _grp_drop(self, title, sub, labels, codes, key, action, default_idx=0):
        # Als _grp_seg, maar met een ui.Dropdown (.drop) i.p.v. een segmented control:
        # compacter bij veel/lange opties, en de dropdown quackt als een segmented
        # (selectedSegment()) dus de change*-handler blijft ongewijzigd.
        h = 52 if sub else 46

        def filler(c, top, rw):
            try:
                idx = codes.index(settings.get(key))
            except ValueError:
                idx = default_idx
            dd = ui.Dropdown.alloc().initWithLabels_selected_target_action_(
                labels, idx, self, action)
            sw = dd.frame().size.width
            dd.setFrame_(NSMakeRect(rw - 14 - sw, top + (h - 26) / 2, sw, 26))
            c.addSubview_(dd)
            self._rowhead(c, top, rw, h, title, sub, sw + 20)
        return (h, filler)

    @objc.python_method
    def _grp_static(self, title, sub, value):
        h = 52 if sub else 46

        def filler(c, top, rw):
            vw = 200.0
            mv = _label(value, 13, color=theme.TEXT2)
            mv.setAlignment_(NSTextAlignmentRight)
            mv.setFrame_(NSMakeRect(rw - 14 - vw, top + (h - 18) / 2, vw, 18))
            c.addSubview_(mv)
            self._rowhead(c, top, rw, h, title, sub, vw + 10)
        return (h, filler)

    @objc.python_method
    def _grp_keycap(self, title, sub, keytext):
        h = 52 if sub else 46

        def filler(c, top, rw):
            kw = 34
            cap = ui.fill(NSMakeRect(rw - 14 - kw, top + (h - 22) / 2, kw, 22),
                          theme.CHIP, 6)
            kl = _mono(keytext, 12, "medium")
            kl.setAlignment_(NSTextAlignmentCenter)
            kl.setFrame_(NSMakeRect(0, 3, kw, 16))
            cap.addSubview_(kl)
            c.addSubview_(cap)
            self._rowhead(c, top, rw, h, title, sub, kw + 20)
        return (h, filler)

    @objc.python_method
    def _grp_button(self, title, sub, btn_title, action):
        h = 52 if sub else 46

        def filler(c, top, rw):
            btn = NSButton.buttonWithTitle_target_action_(btn_title, self, action)
            btn.setBezelStyle_(1)
            btn.sizeToFit()
            bw = max(btn.frame().size.width, 90)
            btn.setFrame_(NSMakeRect(rw - 14 - bw, top + (h - 24) / 2, bw, 24))
            c.addSubview_(btn)
            self._rowhead(c, top, rw, h, title, sub, bw + 10)
        return (h, filler)

    @objc.python_method
    def _grp_login(self):
        def filler(c, top, rw):
            login = ui.Toggle.alloc().init()
            login.setFrame_(NSMakeRect(rw - 14 - 40, top + (46 - 22) / 2, 40, 22))
            login.setState_(NSControlStateValueOn if _login_item_present()
                            else NSControlStateValueOff)
            login.setTarget_(self)
            login.setAction_("toggleLogin:")
            c.addSubview_(login)
            self._rowhead(c, top, rw, 46, "Start bij inloggen", None, 60)
        return (46, filler)

    @objc.python_method
    def _fill_mode(self, c, top, rw):
        gap = 10
        cwd = (rw - 28 - gap) / 2
        cur = settings.get("app_mode")
        basic = _ModeCard.alloc().initWithFrame_code_title_desc_owner_(
            NSMakeRect(14, top + 12, cwd, 82), "basic", "Basic",
            "Alleen menubalk + pill. Geen venster, geen dock-icoon.", self)
        app = _ModeCard.alloc().initWithFrame_code_title_desc_owner_(
            NSMakeRect(14 + cwd + gap, top + 12, cwd, 82), "app", "App",
            "Dit venster, dock-icoon en ⌘Tab erbij.", self)
        self._mode_cards = {"basic": basic, "app": app}
        basic.set_selected(cur != "app")
        app.set_selected(cur == "app")
        c.addSubview_(basic)
        c.addSubview_(app)
        note = NSTextField.wrappingLabelWithString_(
            "Kies je Basic, dan sluit dit venster en verdwijnt het dock-icoon. "
            "Terugkomen kan altijd via “Open SamFlow…” in het menubalk-paneel.")
        note.setFont_(NSFont.systemFontOfSize_(11.5))
        note.setTextColor_(theme.TEXT2)
        note.setFrame_(NSMakeRect(14, top + 102, rw - 28, 40))
        c.addSubview_(note)

    @objc.python_method
    def build_view(self, width=None):
        # breedte-bewust: het hoofdvenster geeft z'n content-breedte door (vult mee),
        # het losse Voorkeuren-venster laat 'm leeg en blijft op W. De rijen leggen hun
        # control rechts uit, dus een bredere kolom groeit netjes mee.
        bw = max(W, int(width)) if width else W
        self._bw = bw
        v = _Flipped.alloc().initWithFrame_(NSMakeRect(0, 0, bw, 900))
        iw = bw - 2 * PAD
        y = PAD
        title = _label("Instellingen", 19, "bold")
        title.setFrame_(NSMakeRect(PAD, y, iw, 24))
        v.addSubview_(title)
        y += 28
        sub = _label("Elke wijziging werkt direct — geen “Opslaan”-knop.", 12.5,
                     color=theme.TEXT2)
        sub.setFrame_(NSMakeRect(PAD, y, iw, 16))
        v.addSubview_(sub)
        y += 30

        # Weergave: de Basic/App-keuze als twee mini-kaarten + login
        y = _glabel(v, PAD, y, iw, "Weergave")
        y = self._group(v, y, [(150, self._fill_mode), self._grp_login()])
        y += SEC_GAP

        y = _glabel(v, PAD, y, iw, "Dicteren")
        y = self._group(v, y, [
            self._grp_seg("Taal", None, LANG_LABELS, LANG_CODES, "language",
                          "changeLanguage:"),
            self._grp_static("Model", "Binnenkort instelbaar", "Turbo — snel"),
            self._grp_keycap("Sneltoets", "Ingedrukt houden = opnemen", "fn"),
            self._grp_seg("Vastzetten",
                          "Zodat je Fn niet hoeft vast te houden. “Vasthouden” = Fn langer "
                          "vasthouden, dan stopt 'ie vanzelf zodra je klaar bent met praten",
                          LOCK_LABELS, LOCK_CODES, "lock_mode", "changeLockMode:"),
            self._grp_switch("polish_enabled"),
        ])
        y += SEC_GAP

        y = _glabel(v, PAD, y, iw, "Pill")
        y = self._group(v, y, [
            self._grp_drop("Positie", "Waar de pill verschijnt", POS_LABELS, POS_CODES,
                           "pill_position", "changePosition:"),
            self._grp_seg("Grootte", "Hoe fors de staafjes zijn", SIZE_LABELS, SIZE_CODES,
                          "pill_size", "changePillSize:", default_idx=len(SIZE_CODES) - 1),
            self._grp_seg("Beweging", "Soepel of kwiek", MOTION_LABELS, MOTION_CODES,
                          "pill_motion", "changePillMotion:"),
            self._grp_switch("show_pill"),
        ])
        y += SEC_GAP

        y = _glabel(v, PAD, y, iw, "Gedrag")
        y = self._group(v, y, [self._grp_switch(k) for k in (
            "sound_cues", "pause_media", "stats_enabled", "auto_update",
            "keep_alive", "share_usage")])
        y += SEC_GAP

        y = _glabel(v, PAD, y, iw, "Historie")
        y = self._group(v, y, [
            self._grp_switch("history_enabled"),
            self._grp_seg("Bewaren", "Hoe lang je historie blijft (0 = altijd)",
                          RETAIN_LABELS, RETAIN_VALUES, "history_days",
                          "changeRetention:", default_idx=1),
        ])
        y += SEC_GAP

        y = _glabel(v, PAD, y, iw, "Woordenlijst")
        y = self._group(v, y, [
            self._grp_button("Eigen termen", "Projectnamen, merken, jargon",
                             "Bewerken…", "editLexicon:")])
        y += SEC_GAP

        # voet: versie + "Controleer op updates" (mockup .winfoot)
        v.addSubview_(ui.hline(PAD, y, iw))
        y += 12
        fl = _label(f"SamFlow {_short_version()} · lokaal & open source", 12,
                    color=theme.FAINT)
        fl.setFrame_(NSMakeRect(PAD, y, iw - 160, 16))
        v.addSubview_(fl)
        upd = NSButton.buttonWithTitle_target_action_(
            "Controleer op updates", self, "checkUpdates:")
        upd.setBordered_(False)
        upd.setFont_(NSFont.systemFontOfSize_(12))
        upd.setContentTintColor_(_CLAY)
        upd.sizeToFit()
        uw = upd.frame().size.width
        upd.setFrame_(NSMakeRect(bw - PAD - uw, y - 1, uw, 18))
        v.addSubview_(upd)
        self._upd_link = upd
        y += 24

        v.setFrame_(NSMakeRect(0, 0, bw, y))
        return v, y

    # --- acties ---
    def toggleSwitch_(self, sender):
        key = self._tag_keys.get(sender.tag())
        if not key:
            return
        on = sender.state() == NSControlStateValueOn
        settings.set(key, on)
        # AI-oppoetsen leunt op een lokaal Ollama-model. Zet iemand 'm aan zonder dat
        # model, dan valt polish stil terug op de kale tekst (zie polish.py) -- dat is
        # onzichtbaar en verwarrend. Daarom hier meteen een melding. De check doet een
        # netwerk-call (~1.5s) dus op een achtergrondthread; het resultaat komt op de
        # main thread terug.
        if key == "polish_enabled" and on:
            def work():
                import polish
                if not polish.available():
                    self.performSelectorOnMainThread_withObject_waitUntilDone_(
                        "_polishUnavailableAlert:", None, False)
            threading.Thread(target=work, daemon=True).start()

    def _polishUnavailableAlert_(self, _obj):
        if not settings.get("polish_enabled"):     # ondertussen weer uitgezet? laat maar
            return
        model = settings.get("polish_model")
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Oppoets-model niet gevonden")
        alert.setInformativeText_(
            f"AI-oppoetsen staat aan, maar Ollama of het model “{model}” draait niet. "
            "Zonder dat blijft je tekst onopgepoetst — de opschoon-regels doen wél gewoon "
            "hun werk.\n\n"
            f"Installeer Ollama en draai in Terminal:\n    ollama pull {model}")
        alert.addButtonWithTitle_("Oké")
        alert.addButtonWithTitle_("Ollama installeren…")
        if alert.runModal() == 1001:               # NSAlertSecondButtonReturn
            subprocess.Popen(["open", "https://ollama.com/download"])

    def changeLanguage_(self, sender):
        i = sender.selectedSegment()
        if 0 <= i < len(LANG_CODES):
            settings.set("language", LANG_CODES[i])

    def changeLockMode_(self, sender):
        i = sender.selectedSegment()
        if 0 <= i < len(LOCK_CODES):
            settings.set("lock_mode", LOCK_CODES[i])

    def changePosition_(self, sender):
        i = sender.selectedSegment()
        if 0 <= i < len(POS_CODES):
            settings.set("pill_position", POS_CODES[i])

    def changePillSize_(self, sender):
        i = sender.selectedSegment()
        if 0 <= i < len(SIZE_CODES):
            settings.set("pill_size", SIZE_CODES[i])

    def changePillMotion_(self, sender):
        i = sender.selectedSegment()
        if 0 <= i < len(MOTION_CODES):
            settings.set("pill_motion", MOTION_CODES[i])

    def selectMode_(self, code):
        # Klik op een Basic/App-kaart: opslaan, live toepassen (dock-icoon verschijnt/
        # verdwijnt; activate zodat een verse dock-app meteen naar voren komt) en beide
        # kaarten bijwerken -- geen herbouw van de hele view nodig.
        settings.set("app_mode", code)
        appmode.apply(code, activate=True)
        for c, card in self._mode_cards.items():
            card.set_selected(c == code)

    def checkUpdates_(self, sender):
        # Achtergrond-check (netwerk-fetch mag de main thread niet blokkeren); het
        # resultaat komt via _updateResult_ terug op de main thread.
        sender.setEnabled_(False)
        sender.setTitle_("Controleren…")

        def work():
            try:
                self._upd_info = updater.check()
            except Exception:
                self._upd_info = None
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "_updateResult:", None, False)
        threading.Thread(target=work, daemon=True).start()

    def _updateResult_(self, _obj):
        info = self._upd_info
        alert = NSAlert.alloc().init()
        if info and info.get("can_apply"):
            n = info.get("behind", 0)
            alert.setMessageText_("Update beschikbaar")
            alert.setInformativeText_(
                f"{n} nieuwe versie{'s' if n != 1 else ''} klaar. Nu bijwerken en herstarten?")
            alert.addButtonWithTitle_("Bijwerken")
            alert.addButtonWithTitle_("Later")
            if alert.runModal() == 1000:            # NSAlertFirstButtonReturn
                ok, _msg = updater.apply(info)
                if ok:
                    updater.relaunch()
                    NSApplication.sharedApplication().terminate_(None)
                    return
        elif info and info.get("behind", 0) > 0:
            alert.setMessageText_("Update beschikbaar")
            alert.setInformativeText_(
                "Er staan nieuwe versies klaar, maar ze kunnen niet automatisch "
                "geïnstalleerd worden (lokale wijzigingen of een afwijkende branch).")
            alert.addButtonWithTitle_("Oké")
            alert.runModal()
        else:
            alert.setMessageText_("Je gebruikt de nieuwste versie.")
            alert.addButtonWithTitle_("Oké")
            alert.runModal()
        if self._upd_link is not None:
            self._upd_link.setEnabled_(True)
            self._upd_link.setTitle_("Controleer op updates")

    def changeRetention_(self, sender):
        i = sender.selectedSegment()
        if 0 <= i < len(RETAIN_VALUES):
            settings.set("history_days", RETAIN_VALUES[i])

    def toggleLogin_(self, sender):
        _login_item_set(sender.state() == NSControlStateValueOn)

    def editLexicon_(self, _sender):
        # Spring naar de Woordenlijst-tab van het hoofdvenster -- de echte in-app editor
        # (termen als chips toevoegen/verwijderen, fonetische correcties). Vroeger opende
        # dit lexicon.txt in TextEdit, wat niets deed als het bestand nog niet bestond.
        # Mainwindow lui importeren: mainwindow importeert prefs, dus een top-level import
        # hier zou een cyclus geven.
        import mainwindow
        mainwindow.open_main_window().show_tab(2)


class PreferencesWindow(NSObject):
    """Het losse Voorkeuren-venster (voor --prefs en de paneel-actie 'Voorkeuren…').
    Wikkelt een PrefsController-view in een echt NSWindow en houdt de controller in
    leven zolang het venster bestaat."""
    def init(self):
        self = objc.super(PreferencesWindow, self).init()
        if self is None:
            return None
        self.controller = PrefsController.alloc().init()
        v, h = self.controller.build_view()
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W, h),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable,
            NSBackingStoreBuffered, False)
        win.setTitle_("Voorkeuren")
        win.setContentView_(v)
        win.center()
        win.setReleasedWhenClosed_(False)
        win.setDelegate_(self)
        self.window = win
        return self

    def windowWillClose_(self, _note):
        _closed("prefs")


_ROW_TEXT = {
    "polish_enabled": ("AI-oppoetsen (lokaal)",
                       "Een lokaal model maakt er nette zinnen van. Kost ~0,6s extra en RAM; uit = alleen de regels."),
    "sound_cues": ("Geluiden", "Klik bij start, stop en klaar"),
    "pause_media": ("Media pauzeren tijdens dictaat", None),
    "show_pill": ("Pill bij de cursor tonen", None),
    "stats_enabled": ("Statistieken bijhouden", "Alleen tellingen op deze Mac — nooit je tekst. Voor het dashboard."),
    "history_enabled": ("Historie bewaren", "Bewaart je dictaten (mét tekst) lokaal. Standaard uit."),
    "auto_update": ("Automatisch bijwerken", "Haalt updates op de achtergrond op van GitHub"),
    "keep_alive": ("Automatisch herstarten", "Brengt SamFlow terug als 'ie onverwacht stopt"),
    "share_usage": ("Anonieme gebruiksstatistiek", "Alleen een telling — nooit je dictaten. Uit te zetten."),
}


# =====================================================================
#  Eerste-start-wizard
# =====================================================================
_STEPS = [
    ("Microfoon", "Om je stem te horen", _mic_ok, "Microphone"),
    ("Invoercontrole", "Om de Fn-toets te zien", _listen_ok, "ListenEvent"),
    ("Toegankelijkheid", "Om de tekst te kunnen plakken", _post_ok, "Accessibility"),
]


class WelcomeWindow(NSObject):
    def init(self):
        self = objc.super(WelcomeWindow, self).init()
        if self is None:
            return None
        self._dots = []      # (statusveld, check-functie)
        self._fn_dot = None  # live Fn-status (los van _dots: blokkeert 'Begin' niet)
        self._fn_lbl = None
        self._build()
        self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.5, self, "refresh:", None, True)
        return self

    @objc.python_method
    def _build(self):
        v = _Flipped.alloc().initWithFrame_(NSMakeRect(0, 0, W, 800))
        y = PAD

        title = _label("Welkom bij SamFlow", size=20, weight="bold")
        title.setFrame_(NSMakeRect(PAD, y, W - 2 * PAD, 28))
        v.addSubview_(title)
        y += 34
        intro = _label(
            "Houd fn ingedrukt, praat, en laat los — de tekst verschijnt waar je "
            "typt. Nog een paar rechten en je bent klaar.", size=13,
            color=NSColor.secondaryLabelColor())
        intro.setFrame_(NSMakeRect(PAD, y, W - 2 * PAD, 34))
        intro.setLineBreakMode_(0)   # word wrap
        v.addSubview_(intro)
        y += 44

        priv = _label("● 100% lokaal — niets gaat naar de cloud", size=12,
                      color=NSColor.systemGreenColor())
        priv.setFrame_(NSMakeRect(PAD, y, W - 2 * PAD, 18))
        v.addSubview_(priv)
        y += 30

        for name, why, check, pane in _STEPS:
            dot = _label("○", size=15, color=NSColor.tertiaryLabelColor())
            dot.setFrame_(NSMakeRect(PAD, y + (ROW_H - 20) / 2, 22, 20))
            v.addSubview_(dot)
            self._dots.append((dot, check))
            nm = _label(name, size=13, weight="bold")
            nm.setFrame_(NSMakeRect(PAD + 30, y + 6, W - 2 * PAD - 140, 18))
            v.addSubview_(nm)
            sub = _label(why, size=11, color=NSColor.secondaryLabelColor())
            sub.setFrame_(NSMakeRect(PAD + 30, y + 24, W - 2 * PAD - 140, 15))
            v.addSubview_(sub)
            btn = NSButton.buttonWithTitle_target_action_("Openen…", self, "openPane:")
            btn.setTag_(len(self._dots) - 1)
            btn.sizeToFit()
            bw = max(btn.frame().size.width, 84)
            btn.setFrame_(NSMakeRect(W - PAD - bw, y + (ROW_H - 24) / 2, bw, 24))
            v.addSubview_(btn)
            y += ROW_H

        y += 8
        # Fn-status: LIVE, want dit is de nummer-1-valkuil. Staat 'Druk op fn' op iets
        # anders dan 'Niets doen', dan opent macOS bij elk dictaat de emoji-kiezer (de
        # tap is listen-only en kan Fn niet opslokken). Bewust NIET in _dots -- het
        # blokkeert 'Begin' niet, maar het vinkje/⚠ slaat wel meteen om als je 't goedzet.
        self._fn_dot = _label("○", size=15, color=NSColor.tertiaryLabelColor())
        self._fn_dot.setFrame_(NSMakeRect(PAD, y + 4, 22, 20))
        v.addSubview_(self._fn_dot)
        self._fn_lbl = _label("", size=12, color=NSColor.secondaryLabelColor())
        self._fn_lbl.setFrame_(NSMakeRect(PAD + 30, y, W - 2 * PAD - 30 - 116, 38))
        self._fn_lbl.setLineBreakMode_(0)
        v.addSubview_(self._fn_lbl)
        kb = NSButton.buttonWithTitle_target_action_("Toetsenbord", self, "openKeyboard:")
        kb.sizeToFit()
        kbw = max(kb.frame().size.width, 100)
        kb.setFrame_(NSMakeRect(W - PAD - kbw, y + 4, kbw, 24))
        v.addSubview_(kb)
        y += 44

        # moduskeuze: Basic (menubalk, zoals nu) of App (dock-icoon + ⌘Tab). Basic
        # staat voorgeselecteerd -- een verse installatie verandert dus niets.
        _separator(v, y)
        y += 14
        mtitle = _label("Weergave", size=13, weight="bold")
        mtitle.setFrame_(NSMakeRect(PAD, y, W - 2 * PAD, 18))
        v.addSubview_(mtitle)
        msub = _label("Basic houdt SamFlow in de menubalk. App geeft ook een "
                      "dock-icoon en plek in ⌘Tab. Later te wisselen in Voorkeuren.",
                      size=11, color=NSColor.secondaryLabelColor())
        msub.setFrame_(NSMakeRect(PAD, y + 22, W - 2 * PAD - 150, 32))
        msub.setLineBreakMode_(0)
        v.addSubview_(msub)
        wmode = NSSegmentedControl.segmentedControlWithLabels_trackingMode_target_action_(
            MODE_LABELS, NSSegmentSwitchTrackingSelectOne, self, "changeAppMode:")
        try:
            wmoidx = MODE_CODES.index(settings.get("app_mode"))
        except ValueError:
            wmoidx = 0
        wmode.setSelectedSegment_(wmoidx)
        wmode.sizeToFit()
        wmow = max(wmode.frame().size.width, 130)
        wmode.setFrame_(NSMakeRect(W - PAD - wmow, y + 4, wmow, 24))
        v.addSubview_(wmode)
        y += 48

        _separator(v, y)
        y += 12
        ask = NSButton.buttonWithTitle_target_action_("Vraag de rechten aan", self, "requestAll:")
        ask.sizeToFit()
        ask.setFrame_(NSMakeRect(PAD, y, max(ask.frame().size.width, 150), 30))
        v.addSubview_(ask)
        self._begin = NSButton.buttonWithTitle_target_action_(
            "Begin met dicteren", self, "begin:")
        self._begin.sizeToFit()
        beginw = max(self._begin.frame().size.width, 150)
        self._begin.setFrame_(NSMakeRect(W - PAD - beginw, y, beginw, 30))
        try:
            self._begin.setKeyEquivalent_("\r")
        except Exception:
            pass
        v.addSubview_(self._begin)
        y += 30 + PAD

        v.setFrame_(NSMakeRect(0, 0, W, y))
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W, y),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable,
            NSBackingStoreBuffered, False)
        win.setTitle_("Welkom")
        win.setContentView_(v)
        win.center()
        win.setReleasedWhenClosed_(False)
        win.setDelegate_(self)
        self.window = win
        self._refresh_dots()

    @objc.python_method
    def _refresh_dots(self):
        all_ok = True
        for dot, check in self._dots:
            ok = check()
            all_ok &= ok
            dot.setStringValue_("✓" if ok else "○")
            dot.setTextColor_(NSColor.systemGreenColor() if ok
                              else NSColor.tertiaryLabelColor())
        self._begin.setEnabled_(all_ok)
        # Fn-status apart bijwerken (blokkeert 'Begin' niet, maar wél zichtbaar). Klei
        # ⚠ = macOS pakt Fn nog af; groen ✓ = vrij voor SamFlow.
        if self._fn_dot is not None:
            free = _fn_free()
            self._fn_dot.setStringValue_("✓" if free else "⚠")
            self._fn_dot.setTextColor_(NSColor.systemGreenColor() if free else _CLAY)
            self._fn_lbl.setStringValue_(
                "Fn-toets is vrij — klaar voor SamFlow." if free else
                "Fn opent nu iets van macOS (emoji-kiezer). Zet Toetsenbord → "
                "“Druk op fn” op “Niets doen”.")
            self._fn_lbl.setTextColor_(
                NSColor.secondaryLabelColor() if free else _CLAY)

    # --- acties ---
    def refresh_(self, _timer):
        self._refresh_dots()

    def openPane_(self, sender):
        _open_privacy(_STEPS[sender.tag()][3])

    def openKeyboard_(self, _sender):
        subprocess.Popen(["open", _KEYBOARD_PANE])

    def requestAll_(self, _sender):
        _request_all()

    def changeAppMode_(self, sender):
        i = sender.selectedSegment()
        if 0 <= i < len(MODE_CODES):
            settings.set("app_mode", MODE_CODES[i])
            # Live toepassen als feedback (dock-icoon verschijnt/verdwijnt), maar niet
            # activeren -- de gebruiker is nog aan het lezen, geen focus stelen.
            appmode.apply(MODE_CODES[i], activate=False)

    def begin_(self, _sender):
        self.window.close()

    def windowWillClose_(self, _note):
        if getattr(self, "_timer", None) is not None:
            self._timer.invalidate()
            self._timer = None
        _closed("welcome")


# =====================================================================
#  Openen + standalone
# =====================================================================
_open = {}          # "prefs"/"welcome" -> venster-controller (tegen GC)
_standalone = False


def _closed(kind):
    _open.pop(kind, None)
    if _standalone and not _open:
        NSApplication.sharedApplication().terminate_(None)


def _show(kind, factory):
    app = NSApplication.sharedApplication()
    ctrl = _open.get(kind)
    if ctrl is None:
        ctrl = factory()
        _open[kind] = ctrl
    app.activateIgnoringOtherApps_(True)
    ctrl.window.makeKeyAndOrderFront_(None)
    return ctrl


def open_preferences():
    return _show("prefs", PreferencesWindow.alloc().init)


def open_welcome():
    return _show("welcome", WelcomeWindow.alloc().init)


def _run_standalone(kind):
    """Voor `samflow.py --prefs` / `--welcome`: draai een eigen mini-app-loop,
    puur om het venster los te kunnen bekijken."""
    global _standalone
    _standalone = True
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    (open_welcome if kind == "welcome" else open_preferences)()
    app.activateIgnoringOtherApps_(True)
    app.run()
