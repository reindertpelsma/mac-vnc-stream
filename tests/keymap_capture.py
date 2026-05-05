#!/usr/bin/env python3
"""tests/keymap_capture.py — manual test fixture for keyboard input forwarding.

Opens a foreground GUI window that records every NSEvent it receives to
/tmp/keylog.txt with full modifier + keycode detail. Used with test_keys.py
(in the repo root) to verify that the kd/ku stream from the browser arrives
intact at the macOS event loop, including Cmd-key chords normally consumed
by the system.

This is a TEST FIXTURE, not part of the server. It is only invoked when run
manually for keyboard-input verification — no production code imports it,
no install path launches it, no LaunchAgent references it. It writes to
/tmp/keylog.txt only while the window is open and the test is running.
"""
import sys, datetime, os

LOG = '/tmp/keylog.txt'

def main():
    from AppKit import (
        NSApplication, NSWindow, NSTextView, NSScrollView,
        NSFont, NSMakeRect,
        NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
        NSWindowStyleMaskMiniaturizable, NSWindowStyleMaskResizable,
        NSBackingStoreBuffered, NSApplicationActivationPolicyRegular,
        NSViewWidthSizable, NSViewHeightSizable,
        NSEventModifierFlagCommand, NSEventModifierFlagShift,
        NSEventModifierFlagControl, NSEventModifierFlagOption,
        NSEventModifierFlagFunction, NSEventModifierFlagCapsLock,
        NSEventModifierFlagNumericPad,
    )
    import Foundation
    import objc as _objc

    with open(LOG, 'w') as f:
        f.write('# macscreencast key logger started ' + datetime.datetime.now().isoformat() + '\n')
        f.write('# columns: type  kc=keycode  mods=modifiers  chars=chars_with_mods  raw=chars_ignoring_mods\n')
    print('Logging to', LOG)

    def log(line):
        ts = datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]
        entry = ts + '  ' + line + '\n'
        with open(LOG, 'a') as f:
            f.write(entry)
        sys.stdout.write(entry)
        sys.stdout.flush()

    FLAG_MAP = [
        (NSEventModifierFlagCommand,    'CMD'),
        (NSEventModifierFlagShift,      'SHIFT'),
        (NSEventModifierFlagControl,    'CTRL'),
        (NSEventModifierFlagOption,     'OPT'),
        (NSEventModifierFlagFunction,   'FN'),
        (NSEventModifierFlagCapsLock,   'CAPS'),
        (NSEventModifierFlagNumericPad, 'NUMPAD'),
    ]
    def fmt_mods(flags):
        return '+'.join(name for mask, name in FLAG_MAP if flags & mask) or 'none'

    class KView(NSTextView):
        def keyDown_(self, event):
            mods  = event.modifierFlags()
            chars = event.characters() or ''
            raw   = event.charactersIgnoringModifiers() or ''
            kc    = event.keyCode()
            log(f'kd   kc={kc:<4} mods={fmt_mods(mods):<30} chars={repr(chars):<14} raw={repr(raw)}')

        def keyUp_(self, event):
            mods  = event.modifierFlags()
            chars = event.characters() or ''
            kc    = event.keyCode()
            log(f'ku   kc={kc:<4} mods={fmt_mods(mods):<30} chars={repr(chars)}')

        def flagsChanged_(self, event):
            mods = event.modifierFlags()
            kc   = event.keyCode()
            log(f'mod  kc={kc:<4} mods={fmt_mods(mods)}')
            _objc.super(KView, self).flagsChanged_(event)

        def performKeyEquivalent_(self, event):
            # Log command-key shortcuts but do NOT act — prevents Cmd+Q quit,
            # Cmd+H hide, Cmd+M minimize from interfering with the test.
            mods  = event.modifierFlags()
            chars = event.characters() or ''
            raw   = event.charactersIgnoringModifiers() or ''
            kc    = event.keyCode()
            log(f'kEQ  kc={kc:<4} mods={fmt_mods(mods):<30} chars={repr(chars):<14} raw={repr(raw)}')
            return True

        def acceptsFirstResponder(self): return True
        def becomeFirstResponder(self):
            log('--- window focused ---')
            return _objc.super(KView, self).becomeFirstResponder()
        def resignFirstResponder(self):
            log('--- window lost focus ---')
            return _objc.super(KView, self).resignFirstResponder()

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)

    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(80, 200, 720, 400),
        NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
        NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable,
        NSBackingStoreBuffered, False)
    win.setTitle_('Key Logger — click here to focus, then run test_keys.py')
    win.center()

    scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, 720, 400))
    scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
    tv = KView.alloc().initWithFrame_(NSMakeRect(0, 0, 720, 400))
    tv.setEditable_(True)
    tv.setFont_(NSFont.monospacedSystemFontOfSize_weight_(12, 0))
    tv.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
    tv.setString_(
        'KEY LOGGER — macscreencast keyboard test\n\n'
        '1. Click anywhere in this window in the web UI to give it focus.\n'
        '2. Run:  python3 test_keys.py   (from another SSH session)\n'
        '3. Review results in /tmp/keylog.txt\n\n'
        'Cmd+Q, Cmd+H, Cmd+M etc. are intercepted and logged without acting.\n'
        'Log: ' + LOG + '\n'
    )
    tv.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
    scroll.setDocumentView_(tv)
    scroll.setHasVerticalScroller_(True)
    win.setContentView_(scroll)
    win.makeFirstResponder_(tv)
    win.makeKeyAndOrderFront_(None)
    app.activateIgnoringOtherApps_(True)
    app.run()

if __name__ == '__main__':
    main()
