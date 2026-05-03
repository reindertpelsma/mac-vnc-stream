import logging

log = logging.getLogger("macvnc")

# Mac virtual key codes indexed by browser e.code.
# Allows CGEvent injection to bypass screensharingd's X11 keysym→VK translation,
# which becomes unreliable after VNC reconnects on newer macOS versions.
VK = {
    "KeyA":0,"KeyB":11,"KeyC":8,"KeyD":2,"KeyE":14,"KeyF":3,"KeyG":5,"KeyH":4,
    "KeyI":34,"KeyJ":38,"KeyK":40,"KeyL":37,"KeyM":46,"KeyN":45,"KeyO":31,"KeyP":35,
    "KeyQ":12,"KeyR":15,"KeyS":1,"KeyT":17,"KeyU":32,"KeyV":9,"KeyW":13,"KeyX":7,
    "KeyY":16,"KeyZ":6,
    "Digit0":29,"Digit1":18,"Digit2":19,"Digit3":20,"Digit4":21,
    "Digit5":23,"Digit6":22,"Digit7":26,"Digit8":28,"Digit9":25,
    "Space":49,"Enter":36,"Return":36,"Tab":48,"Backspace":51,"Delete":117,
    "Escape":53,"Home":115,"End":119,"PageUp":116,"PageDown":121,
    "ArrowLeft":123,"ArrowRight":124,"ArrowUp":126,"ArrowDown":125,
    "Equal":24,"Minus":27,"BracketLeft":33,"BracketRight":30,
    "Backslash":42,"Semicolon":41,"Quote":39,"Comma":43,"Period":47,"Slash":44,"Backquote":50,
    "MetaLeft":55,"MetaRight":54,
    "ShiftLeft":56,"ShiftRight":60,
    "ControlLeft":59,"ControlRight":62,
    "AltLeft":58,"AltRight":61,
    "CapsLock":57,
    "F1":122,"F2":120,"F3":99,"F4":118,"F5":96,"F6":97,
    "F7":98,"F8":100,"F9":101,"F10":109,"F11":103,"F12":111,
}
# e.key aliases: browser sends lowercase letter as k ("a") and code as "KeyA".
# Add both so CGEvent handles the letter without falling back to VNC.
for _c in "abcdefghijklmnopqrstuvwxyz":
    VK[_c] = VK[f"Key{_c.upper()}"]
    VK[_c.upper()] = VK[f"Key{_c.upper()}"]
for _d in "0123456789":
    VK[_d] = VK[f"Digit{_d}"]
VK[" "] = 49   # e.key for Space is " "
del _c, _d

# kCGEventFlag masks for each modifier VK code
_VK_FLAGS = {
    55:0x100000, 54:0x100000,  # MetaLeft/Right → Command
    56:0x020000, 60:0x020000,  # ShiftLeft/Right → Shift
    59:0x040000, 62:0x040000,  # ControlLeft/Right → Control
    58:0x080000, 61:0x080000,  # AltLeft/Right → Option
    57:0x010000,               # CapsLock → AlphaShift
}
_VK_MODS = frozenset(_VK_FLAGS)
# Global modifier-held state for CGEvent path (system-global, not per-session).
_cg_mod_held: set = set()
# True once AXIsProcessTrusted() and a test CGEventPost both succeed.
_cg_kb_ok: bool = False

# Previous VNC button mask for delta detection (CGEvent path only).
_cg_mouse_prev_btn: int = 0
# Last known CGEvent pointer position (native Mac coordinates, top-left origin).
# Stored so _cg_release_all() can release buttons at the correct position instead
# of posting a button-up at (0,0) which would teleport the Mac cursor to top-left.
_cg_mouse_last_pt: tuple = (0, 0)

# kCGEvent* constants accessed at call-time to avoid import-time Quartz dependency.
_CG_BTNS = (
    # (mask, down_event,           up_event,             button_index)
    (1, "kCGEventLeftMouseDown",  "kCGEventLeftMouseUp",  "kCGMouseButtonLeft"),
    (2, "kCGEventOtherMouseDown", "kCGEventOtherMouseUp", "kCGMouseButtonCenter"),
    (4, "kCGEventRightMouseDown", "kCGEventRightMouseUp", "kCGMouseButtonRight"),
)


def _cg_release_all() -> None:
    """Release all CGEvent modifier keys and mouse buttons. Called on client disconnect."""
    global _cg_mod_held, _cg_mouse_prev_btn
    try:
        import Quartz as _Q
        for vk in list(_cg_mod_held):
            evt = _Q.CGEventCreateKeyboardEvent(None, vk, False)
            _Q.CGEventSetFlags(evt, 0)
            _Q.CGEventPost(_Q.kCGHIDEventTap, evt)
        _cg_mod_held.clear()
        if _cg_mouse_prev_btn:
            lx, ly = _cg_mouse_last_pt
            pt = _Q.CGPoint(lx, ly)
            for mask, _, up_name, btn_name in _CG_BTNS:
                if _cg_mouse_prev_btn & mask:
                    _Q.CGEventPost(_Q.kCGHIDEventTap,
                                   _Q.CGEventCreateMouseEvent(None, getattr(_Q, up_name), pt, getattr(_Q, btn_name)))
            _cg_mouse_prev_btn = 0
    except Exception:
        pass


def _check_cg_kb() -> bool:
    """Probe Accessibility permission and enable CGEvent keyboard injection if granted."""
    global _cg_kb_ok
    if _cg_kb_ok:
        return True
    try:
        import ctypes, Quartz as _Q
        ax = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        ax.AXIsProcessTrusted.restype = ctypes.c_bool
        if not ax.AXIsProcessTrusted():
            return False
        # Confirm CGEventPost works: post a key-up for a non-existent VK (no-op).
        evt = _Q.CGEventCreateKeyboardEvent(None, 0xFF, False)
        _Q.CGEventPost(_Q.kCGHIDEventTap, evt)
        _cg_kb_ok = True
        log.info("CGEvent keyboard: Accessibility granted — CGEvent input active")
        return True
    except Exception as e:
        log.debug("CGEvent keyboard probe: %s", e)
        return False


def _poll_cg_kb():
    """Background thread: poll until Accessibility is granted, then enable CGEvent input."""
    import time
    while not _cg_kb_ok:
        time.sleep(5)
        _check_cg_kb()


def _cg_send_pointer(buttons: int, x: int, y: int) -> bool:
    """Send mouse move/click via CGEvent (kCGHIDEventTap).

    Coordinates are in VNC space (top-left origin, logical pixels).
    CGEvent (CoreGraphics) also uses top-left origin with Y increasing downward,
    so VNC coordinates map directly — no Y-flip needed.
    Returns True on success; caller falls back to VNC on False.
    """
    global _cg_mouse_prev_btn, _cg_mouse_last_pt
    try:
        import Quartz as _Q
        pt = _Q.CGPoint(x, y)
        _cg_mouse_last_pt = (x, y)
        changed = buttons ^ _cg_mouse_prev_btn
        _cg_mouse_prev_btn = buttons

        # Move / drag event
        if buttons & 1:
            move_type = _Q.kCGEventLeftMouseDragged
        elif buttons & 4:
            move_type = _Q.kCGEventRightMouseDragged
        else:
            move_type = _Q.kCGEventMouseMoved
        _Q.CGEventPost(_Q.kCGHIDEventTap,
                       _Q.CGEventCreateMouseEvent(None, move_type, pt, _Q.kCGMouseButtonLeft))

        # Button press/release for changed bits
        for mask, dn_name, up_name, btn_name in _CG_BTNS:
            if changed & mask:
                etype = getattr(_Q, dn_name if (buttons & mask) else up_name)
                btn   = getattr(_Q, btn_name)
                _Q.CGEventPost(_Q.kCGHIDEventTap,
                               _Q.CGEventCreateMouseEvent(None, etype, pt, btn))
        return True
    except Exception:
        return False
