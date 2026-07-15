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

import objc
from AppKit import (
    NSApplication, NSApplicationActivationPolicyAccessory, NSBackingStoreBuffered,
    NSBox, NSBoxSeparator, NSButton, NSColor, NSControlStateValueOff,
    NSControlStateValueOn, NSFont, NSMakeRect, NSSegmentedControl,
    NSSegmentSwitchTrackingSelectOne, NSTextAlignmentRight, NSTextField,
    NSView, NSWindow, NSWindowStyleMaskClosable, NSWindowStyleMaskTitled,
)
from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
from Foundation import CFPreferencesCopyAppValue, NSObject, NSTimer
from Quartz import (
    CGPreflightListenEventAccess, CGPreflightPostEventAccess,
    CGRequestListenEventAccess, CGRequestPostEventAccess,
)

import lexicon
import settings
import ui

# ---------- layout ----------
W = 470
PAD = 22
ROW_H = 46
SEC_GAP = 22
APP_PATH = os.path.expanduser("~/Applications/SamFlow.app")

_PRIVACY_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_"
_KEYBOARD_PANE = "x-apple.systempreferences:com.apple.preference.keyboard"

LANG_LABELS = ["Nederlands", "English", "Automatisch"]
LANG_CODES = ["nl", "en", "auto"]

LOCK_LABELS = ["Uit", "Tik", "Dubbel-tik", "Fn+⌘"]
LOCK_CODES = ["off", "tap", "double", "chord"]


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


def _login_item_present():
    try:
        out = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to get the name of every login item'],
            capture_output=True, text=True, timeout=3)
        return "SamFlow" in (out.stdout or "")
    except Exception:
        return True   # onbekend: install.sh zet 'm standaard, neem aan van wel


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


# ---------- gedeelde bouwstenen ----------
class _Flipped(NSView):
    """Een view met de oorsprong linksboven, zodat we top-down kunnen layouten
    in plaats van in Cocoa's y-omhoog-coordinaten."""
    def isFlipped(self):
        return True


def _label(text, size=13, weight="regular", color=None):
    f = NSTextField.labelWithString_(text)
    font = (NSFont.systemFontOfSize_(size) if weight == "regular"
            else NSFont.boldSystemFontOfSize_(size) if weight == "bold"
            else NSFont.systemFontOfSize_weight_(size, 0.3))
    f.setFont_(font)
    if color is not None:
        f.setTextColor_(color)
    return f


def _section(view, y, title):
    lbl = _label(title.upper(), size=11, color=NSColor.secondaryLabelColor())
    lbl.setFrame_(NSMakeRect(PAD + 2, y, W - 2 * PAD, 16))
    view.addSubview_(lbl)
    return y + 22


def _separator(view, y):
    box = NSBox.alloc().initWithFrame_(NSMakeRect(PAD, y, W - 2 * PAD, 1))
    box.setBoxType_(NSBoxSeparator)
    view.addSubview_(box)


def _row_label(view, y, title, sub=None):
    lbl = _label(title, size=13)
    if sub:
        lbl.setFrame_(NSMakeRect(PAD, y + 6, W - 2 * PAD - 120, 18))
        s = _label(sub, size=11, color=NSColor.secondaryLabelColor())
        s.setFrame_(NSMakeRect(PAD, y + 24, W - 2 * PAD - 120, 15))
        view.addSubview_(s)
    else:
        lbl.setFrame_(NSMakeRect(PAD, y + (ROW_H - 20) / 2, W - 2 * PAD - 120, 20))
    view.addSubview_(lbl)


# =====================================================================
#  Voorkeuren-venster
# =====================================================================
class PreferencesWindow(NSObject):
    def init(self):
        self = objc.super(PreferencesWindow, self).init()
        if self is None:
            return None
        self._tag_keys = {}   # NSSwitch.tag() -> settings-sleutel
        self._build()
        return self

    @objc.python_method
    def _switch(self, view, y, key):
        _row_label(view, y, *_ROW_TEXT[key])
        sw = ui.Toggle.alloc().init()
        sw.setFrame_(NSMakeRect(W - PAD - 40, y + (ROW_H - 22) / 2, 40, 22))
        sw.setState_(NSControlStateValueOn if settings.get(key) else NSControlStateValueOff)
        tag = len(self._tag_keys) + 1
        sw.setTag_(tag)
        self._tag_keys[tag] = key
        sw.setTarget_(self)
        sw.setAction_("toggleSwitch:")
        view.addSubview_(sw)

    @objc.python_method
    def _build(self):
        v = _Flipped.alloc().initWithFrame_(NSMakeRect(0, 0, W, 800))
        y = PAD

        y = _section(v, y, "Dicteren")
        # taal
        _row_label(v, y, "Taal")
        seg = NSSegmentedControl.segmentedControlWithLabels_trackingMode_target_action_(
            LANG_LABELS, NSSegmentSwitchTrackingSelectOne, self, "changeLanguage:")
        try:
            idx = LANG_CODES.index(settings.get("language"))
        except ValueError:
            idx = 0
        seg.setSelectedSegment_(idx)
        seg.sizeToFit()
        sw_w = seg.frame().size.width
        seg.setFrame_(NSMakeRect(W - PAD - sw_w, y + (ROW_H - 24) / 2, sw_w, 24))
        v.addSubview_(seg)
        y += ROW_H
        _separator(v, y)
        # model (nog niet live)
        _row_label(v, y, "Model", "Binnenkort instelbaar")
        mv = _label("Turbo — snel", size=13, color=NSColor.secondaryLabelColor())
        mv.setFrame_(NSMakeRect(W - PAD - 130, y + (ROW_H - 18) / 2, 130, 18))
        mv.setAlignment_(NSTextAlignmentRight)
        v.addSubview_(mv)
        y += ROW_H
        _separator(v, y)
        # sneltoets (vast)
        _row_label(v, y, "Sneltoets", "Aanpasbaar in een latere versie")
        kv = _label("fn", size=12, color=NSColor.secondaryLabelColor())
        kv.setFrame_(NSMakeRect(W - PAD - 60, y + (ROW_H - 18) / 2, 60, 18))
        kv.setAlignment_(NSTextAlignmentRight)
        v.addSubview_(kv)
        y += ROW_H
        _separator(v, y)
        # vastzetten (hands-free): niet blijven vasthouden voor een langer dictaat
        _row_label(v, y, "Vastzetten", "Zodat je Fn niet hoeft vast te houden")
        lock = NSSegmentedControl.segmentedControlWithLabels_trackingMode_target_action_(
            LOCK_LABELS, NSSegmentSwitchTrackingSelectOne, self, "changeLockMode:")
        try:
            lidx = LOCK_CODES.index(settings.get("lock_mode"))
        except ValueError:
            lidx = 0
        lock.setSelectedSegment_(lidx)
        lock.sizeToFit()
        lw = lock.frame().size.width
        lock.setFrame_(NSMakeRect(W - PAD - lw, y + (ROW_H - 24) / 2, lw, 24))
        v.addSubview_(lock)
        y += ROW_H + SEC_GAP

        y = _section(v, y, "Gedrag")
        for key in ("sound_cues", "pause_media", "show_pill", "auto_update"):
            self._switch(v, y, key)
            y += ROW_H
            _separator(v, y)
        # start bij inloggen -- bron van waarheid is het OS-login-item, niet settings
        _row_label(v, y, "Start bij inloggen")
        login = ui.Toggle.alloc().init()
        login.setFrame_(NSMakeRect(W - PAD - 40, y + (ROW_H - 22) / 2, 40, 22))
        login.setState_(NSControlStateValueOn if _login_item_present() else NSControlStateValueOff)
        login.setTarget_(self)
        login.setAction_("toggleLogin:")
        v.addSubview_(login)
        y += ROW_H + SEC_GAP

        y = _section(v, y, "Woordenlijst")
        _row_label(v, y, "Eigen termen", "Projectnamen, merken, jargon")
        btn = NSButton.buttonWithTitle_target_action_("Bewerken…", self, "editLexicon:")
        btn.sizeToFit()
        bw = max(btn.frame().size.width, 90)
        btn.setFrame_(NSMakeRect(W - PAD - bw, y + (ROW_H - 24) / 2, bw, 24))
        v.addSubview_(btn)
        y += ROW_H + PAD

        v.setFrame_(NSMakeRect(0, 0, W, y))
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W, y),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable,
            NSBackingStoreBuffered, False)
        win.setTitle_("Voorkeuren")
        win.setContentView_(v)
        win.center()
        win.setReleasedWhenClosed_(False)
        win.setDelegate_(self)
        self.window = win

    # --- acties ---
    def toggleSwitch_(self, sender):
        key = self._tag_keys.get(sender.tag())
        if key:
            settings.set(key, sender.state() == NSControlStateValueOn)

    def changeLanguage_(self, sender):
        i = sender.selectedSegment()
        if 0 <= i < len(LANG_CODES):
            settings.set("language", LANG_CODES[i])

    def changeLockMode_(self, sender):
        i = sender.selectedSegment()
        if 0 <= i < len(LOCK_CODES):
            settings.set("lock_mode", LOCK_CODES[i])

    def toggleLogin_(self, sender):
        _login_item_set(sender.state() == NSControlStateValueOn)

    def editLexicon_(self, _sender):
        subprocess.Popen(["open", "-t", lexicon.LEXICON_FILE])

    def windowWillClose_(self, _note):
        _closed("prefs")


_ROW_TEXT = {
    "sound_cues": ("Geluiden", "Klik bij start, stop en klaar"),
    "pause_media": ("Media pauzeren tijdens dictaat", None),
    "show_pill": ("Pill bij de cursor tonen", None),
    "auto_update": ("Automatisch bijwerken", "Haalt updates op de achtergrond op van GitHub"),
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
        fnrow = _label("Fn-toets vrijmaken: Toetsenbord → “Druk op fn” → Niets doen",
                       size=12, color=NSColor.secondaryLabelColor())
        fnrow.setFrame_(NSMakeRect(PAD, y + 4, W - 2 * PAD - 150, 30))
        fnrow.setLineBreakMode_(0)
        v.addSubview_(fnrow)
        kb = NSButton.buttonWithTitle_target_action_("Toetsenbord", self, "openKeyboard:")
        kb.sizeToFit()
        kbw = max(kb.frame().size.width, 100)
        kb.setFrame_(NSMakeRect(W - PAD - kbw, y + 4, kbw, 24))
        v.addSubview_(kb)
        y += 44

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

    # --- acties ---
    def refresh_(self, _timer):
        self._refresh_dots()

    def openPane_(self, sender):
        _open_privacy(_STEPS[sender.tag()][3])

    def openKeyboard_(self, _sender):
        subprocess.Popen(["open", _KEYBOARD_PANE])

    def requestAll_(self, _sender):
        _request_all()

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
