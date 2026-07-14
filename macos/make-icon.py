#!/usr/bin/env python3
"""Teken het SamFlow-icoon (donkere squircle + lichte equalizer-balkjes) en render
het naar PNG's op alle iconset-formaten + een preview. Pure AppKit, geen tools."""
import os
import sys

from AppKit import (
    NSBitmapImageRep, NSGraphicsContext, NSColor, NSBezierPath, NSMakeRect,
    NSDeviceRGBColorSpace, NSGradient,
)
try:
    from AppKit import NSBitmapImageFileTypePNG as PNG
except ImportError:
    from AppKit import NSPNGFileType as PNG

OUT = sys.argv[1] if len(sys.argv) > 1 else "."
BARS = [0.40, 0.70, 1.00, 0.60, 0.34]      # equalizer-hoogtes
INK = (0.94, 0.94, 0.95)     # off-white balkjes -- Helder: grafiet + wit; klei blijft voor de live-UI


def draw(px):
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, px, px, 8, 4, True, False, NSDeviceRGBColorSpace, 0, 0)
    ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(ctx)
    s = px / 1024.0

    # squircle-achtergrond met subtiel verticaal verloop
    margin = 64 * s
    r = NSMakeRect(margin, margin, px - 2 * margin, px - 2 * margin)
    corner = 232 * s
    bg = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(r, corner, corner)
    top = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.173, 0.173, 0.192, 1.0)
    bot = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.090, 0.090, 0.106, 1.0)
    NSGradient.alloc().initWithStartingColor_endingColor_(top, bot).drawInBezierPath_angle_(bg, -90.0)
    # dunne lichtrand bovenlangs
    NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.07).set()
    bg.setLineWidth_(6 * s)
    bg.stroke()

    # equalizer-balkjes, verticaal gecentreerd
    bw, gap = 78 * s, 44 * s
    total = len(BARS) * bw + (len(BARS) - 1) * gap
    x = (px - total) / 2
    NSColor.colorWithCalibratedRed_green_blue_alpha_(*INK, 1.0).set()
    for h in BARS:
        bh = 300 * s * h + 96 * s
        y = (px - bh) / 2
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(x, y, bw, bh), bw / 2, bw / 2).fill()
        x += bw + gap

    NSGraphicsContext.restoreGraphicsState()
    return rep


def save(rep, path):
    rep.representationUsingType_properties_(PNG, {}).writeToFile_atomically_(path, True)


# iconset: elk formaat + @2x
os.makedirs(os.path.join(OUT, "SamFlow.iconset"), exist_ok=True)
for base in (16, 32, 128, 256, 512):
    save(draw(base), os.path.join(OUT, "SamFlow.iconset", f"icon_{base}x{base}.png"))
    save(draw(base * 2), os.path.join(OUT, "SamFlow.iconset", f"icon_{base}x{base}@2x.png"))
# losse preview om te tonen
save(draw(512), os.path.join(OUT, "preview.png"))
print("iconset + preview klaar in", OUT)
