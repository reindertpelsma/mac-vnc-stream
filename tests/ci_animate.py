#!/usr/bin/env python3
"""Color-cycling NSWindow at 60fps to drive SCK frame delivery in CI.

Run as a background subprocess before the smoke test. The window forces
the macOS compositor to produce new frames continuously, so SCK delivers
them to the server which encodes and sends them to the WebSocket client.
"""
import sys

try:
    import AppKit
except ImportError:
    import time
    time.sleep(86400)
    sys.exit(0)


class _Anim(AppKit.NSObject):
    def tick_(self, _timer):
        self._h = (getattr(self, "_h", 0.0) + 4.0) % 360.0
        color = AppKit.NSColor.colorWithHue_saturation_brightness_alpha_(
            self._h / 360.0, 0.9, 0.85, 1.0
        )
        _VIEW.layer().setBackgroundColor_(color.CGColor())
        _VIEW.setNeedsDisplay_(True)


app = AppKit.NSApplication.sharedApplication()
app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)

win = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
    AppKit.NSMakeRect(10, 10, 900, 650),
    AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskResizable,
    AppKit.NSBackingStoreBuffered,
    False,
)
win.setTitle_("CI Smoke Animation")
_VIEW = win.contentView()
_VIEW.setWantsLayer_(True)
win.makeKeyAndOrderFront_(None)
app.activateIgnoringOtherApps_(True)

anim = _Anim.alloc().init()
AppKit.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
    1.0 / 60.0, anim, "tick:", None, True
)

app.run()
