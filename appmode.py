"""
appmode.py - Basic vs App-modus: de activation policy van het proces.

De modus is *aanwezigheid, geen functionaliteit* -- dicteren werkt in beide precies
gelijk. Het verschil is puur de runtime NSApplication-policy:

- **Basic** = accessory: menubalk-app zonder dock-icoon, niet in ⌘Tab (het gedrag
  van altijd; een verse installatie staat hierop, dus verandert er niets).
- **App** = regular: volwaardige app met dock-icoon en ⌘Tab.

Belangrijk voor de TCC-val: we zetten alléén de *runtime* policy om. De bundle
(Info.plist `LSUIElement`) blijft ongemoeid -- de policy overrulet dat op runtime.
Zo verandert de code-identiteit waar de rechten aan hangen niet, en blijft de wissel
één klik zonder herstart.

Leaf-module: importeert alleen AppKit + settings (dat is puur JSON, geen AppKit),
nooit hud/prefs/mainwindow -- dus die drie kunnen 'm delen zonder cyclus.

Alleen op de main thread aanroepen (vanuit build() of een bewuste klik). De
policy-wissel raakt de main run loop waar ook de Fn-tap aan hangt; nooit vanaf een
achtergrondthread.
"""
from AppKit import (
    NSApplication, NSApplicationActivationPolicyAccessory,
    NSApplicationActivationPolicyRegular,
)

import settings


def current():
    """De opgeslagen modus: "app" of "basic" (default)."""
    return "app" if settings.get("app_mode") == "app" else "basic"


def label(mode=None):
    """Nette Nederlandse naam voor de voetregels ("App-modus" / "Basic")."""
    if mode is None:
        mode = current()
    return "App-modus" if mode == "app" else "Basic"


def apply(mode=None, activate=False):
    """Zet de activation policy op `mode` (of de opgeslagen). `activate=True` haalt
    de app ook naar voren -- doe dat alléén bij een bewuste live-wissel, nooit bij
    het opstarten (anders steelt SamFlow focus bij elke login)."""
    if mode is None:
        mode = current()
    app = NSApplication.sharedApplication()
    if mode == "app":
        app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        if activate:
            app.activateIgnoringOtherApps_(True)
    else:
        app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    return mode
