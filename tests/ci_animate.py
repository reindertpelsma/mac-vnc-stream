#!/usr/bin/env python3
"""Borderless fullscreen stress animation for the smoke/2Mbps tests.

Two modes (selected via STRESS_MODE env var):

  default ("balls") — fullscreen with 50 hue-drifting bouncing circles.
    Realistic-content stress for the stability test. H.264 compresses
    this to ~1.2Mbps regardless of bandwidth budget (good codec,
    predictable motion).

  "saturation" — same shape, but 250 small fast-moving circles. Stays
    visually realistic and compressible (it's still ball motion, not
    pixel noise), but each ball moves independently so H.264 can't
    use one global motion vector — every ball produces its own
    residual. Pushes encoded output past 1.7Mbps so the controller
    actually saturates against the 2Mbps cap instead of coasting
    under it. Used by the saturation test.
"""
import os, sys, random

try:
    import AppKit
    from Quartz import (
        CALayer, CAGradientLayer, CATransaction,
        CGRectMake, CGColorGetColorSpace,
        CGPointMake,
    )
except ImportError:
    import time
    time.sleep(86400)
    sys.exit(0)

random.seed(42)   # reproducible layout across runs

MODE = os.environ.get("STRESS_MODE", "balls")

if MODE == "saturation":
    # Many small gradient-shaded blobs — looks like flowing video texture
    # (smooth shading, lots of small independent motions), compresses
    # naturally but has too much novel residual per frame to fit under
    # the codec's "easy" floor. Pushes encoded output >1.7Mbps.
    NUM_BALLS  = 250
    BALL_R_MIN = 12
    BALL_R_MAX = 28
    BALL_V_MIN = 14
    BALL_V_MAX = 32
else:
    # Realistic stability content: 50 large hue-drifting circles. Compresses
    # to ~1.2Mbps; tests controller stability under everyday motion.
    NUM_BALLS  = 50
    BALL_R_MIN = 30
    BALL_R_MAX = 80
    BALL_V_MIN = 8
    BALL_V_MAX = 18
TICK_HZ      = 60

app = AppKit.NSApplication.sharedApplication()
app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)

screen_frame = AppKit.NSScreen.mainScreen().frame()
WIN_W = int(screen_frame.size.width)
WIN_H = int(screen_frame.size.height)

win = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
    AppKit.NSMakeRect(0, 0, WIN_W, WIN_H),
    AppKit.NSWindowStyleMaskBorderless,
    AppKit.NSBackingStoreBuffered,
    False,
)
win.setTitle_("CI Stress Animation")
win.setLevel_(AppKit.NSScreenSaverWindowLevel)
win.setIgnoresMouseEvents_(True)

view = win.contentView()
view.setWantsLayer_(True)
root = view.layer()


def _nscolor(h, s=0.9, b=0.95, a=1.0):
    return AppKit.NSColor.colorWithHue_saturation_brightness_alpha_(h, s, b, a)


# Bouncing ball state + a CALayer per ball.
# Sizes/velocities scaled so the balls traverse a meaningful fraction of
# whatever the runner's display happens to be (don't hardcode 900×650).
# In saturation mode each ball is a CAGradientLayer (smooth shading) —
# more video-like to the encoder than flat colors and produces meaningful
# per-ball residuals when dozens of them move independently.
_scale = min(WIN_W, WIN_H) / 650.0
balls = []
for _ in range(NUM_BALLS):
    r  = random.uniform(BALL_R_MIN, BALL_R_MAX) * _scale
    bx = random.uniform(r, WIN_W - r)
    by = random.uniform(r, WIN_H - r)
    vx = random.choice([-1, 1]) * random.uniform(BALL_V_MIN, BALL_V_MAX) * _scale
    vy = random.choice([-1, 1]) * random.uniform(BALL_V_MIN, BALL_V_MAX) * _scale
    h  = random.uniform(0.0, 1.0)

    if MODE == "saturation":
        layer = CAGradientLayer.layer()
        layer.setStartPoint_(CGPointMake(0.2, 0.2))
        layer.setEndPoint_(CGPointMake(0.8, 0.8))
        layer.setColors_([_nscolor(h, s=0.9, b=1.0).CGColor(),
                          _nscolor((h + 0.15) % 1.0, s=0.6, b=0.4).CGColor()])
    else:
        layer = CALayer.layer()
        layer.setBackgroundColor_(_nscolor(h).CGColor())
    layer.setFrame_(CGRectMake(bx - r, by - r, r * 2, r * 2))
    layer.setCornerRadius_(r)
    root.addSublayer_(layer)

    balls.append({"layer": layer, "x": bx, "y": by, "r": r,
                  "vx": vx, "vy": vy, "h": h, "hv": random.uniform(0.003, 0.008)})

_bg_h = [0.0]


class _Ticker(AppKit.NSObject):
    def tick_(self, _timer):
        # Background: slow hue cycle
        _bg_h[0] = (_bg_h[0] + 2.5) % 360.0
        bg = _nscolor(_bg_h[0] / 360.0, 0.75, 0.65)
        root.setBackgroundColor_(bg.CGColor())

        # Balls: move + bounce + hue drift.
        # Disable implicit animations so every position update appears
        # immediately — each captured frame sees discrete new positions,
        # maximising the H.264 residual per P-frame.
        CATransaction.begin()
        CATransaction.setDisableActions_(True)
        for b in balls:
            b["x"] += b["vx"]
            b["y"] += b["vy"]
            if b["x"] < b["r"] or b["x"] > WIN_W - b["r"]:
                b["vx"] *= -1
            if b["y"] < b["r"] or b["y"] > WIN_H - b["r"]:
                b["vy"] *= -1
            b["h"] = (b["h"] + b["hv"]) % 1.0
            r = b["r"]
            b["layer"].setFrame_(CGRectMake(b["x"] - r, b["y"] - r, r * 2, r * 2))
            if MODE == "saturation":
                h = b["h"]
                b["layer"].setColors_([_nscolor(h, s=0.9, b=1.0).CGColor(),
                                       _nscolor((h + 0.15) % 1.0, s=0.6, b=0.4).CGColor()])
            else:
                b["layer"].setBackgroundColor_(_nscolor(b["h"]).CGColor())
        CATransaction.commit()


win.makeKeyAndOrderFront_(None)
app.activateIgnoringOtherApps_(True)

_t = _Ticker.alloc().init()
AppKit.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
    1.0 / TICK_HZ, _t, "tick:", None, True
)

app.run()
