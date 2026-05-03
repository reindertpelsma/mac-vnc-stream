#!/usr/bin/env python3
"""test_keys.py — keyboard shortcut verification via mac-vnc-stream WebSocket.

Run from the Mac (port 6081 is loopback-only):
  python3 test_keys.py

tests/keymap_capture.py window must be focused in the web UI before running.
Results land in /tmp/keylog.txt.
"""
import asyncio, json, time, sys

WS_URL = "ws://127.0.0.1:6081/?token=guacweb"
DELAY  = 0.05   # seconds between individual key events
GROUP  = 0.15   # pause between separate shortcuts

async def run():
    import websockets
    print(f"Connecting to {WS_URL} ...")
    async with websockets.connect(WS_URL) as ws:
        print("Connected. Starting in 1s — Key Logger must be focused.\n")
        await asyncio.sleep(1.0)

        async def kd(k, code=None):
            await ws.send(json.dumps({"t": "kd", "k": k, "code": code or k}))
            await asyncio.sleep(DELAY)

        async def ku(k, code=None):
            await ws.send(json.dumps({"t": "ku", "k": k, "code": code or k}))
            await asyncio.sleep(DELAY)

        async def tap(label, k, code=None):
            print(f"  {label}")
            await kd(k, code)
            await ku(k, code)
            await asyncio.sleep(GROUP)

        async def combo(label, *pairs):
            """pairs = [(k, code), ...] — press in order, release in reverse."""
            print(f"  {label}")
            for k, code in pairs:
                await kd(k, code)
            for k, code in reversed(pairs):
                await ku(k, code)
            await asyncio.sleep(GROUP)

        CMD = ("Meta", "MetaLeft")
        SHF = ("Shift", "ShiftLeft")
        CTL = ("Control", "ControlLeft")
        ALT = ("Alt", "AltLeft")

        # ── Modifier keys alone ───────────────────────────────────────────
        print("=== Modifier keys ===")
        await tap("Shift",   "Shift",   "ShiftLeft")
        await tap("Control", "Control", "ControlLeft")
        await tap("Option",  "Alt",     "AltLeft")
        await tap("Command", "Meta",    "MetaLeft")

        # ── Plain letters ─────────────────────────────────────────────────
        print("\n=== Letters a-z ===")
        for ch in "abcdefghijklmnopqrstuvwxyz":
            await tap(ch, ch, f"Key{ch.upper()}")

        # ── Digits ────────────────────────────────────────────────────────
        print("\n=== Digits 0-9 ===")
        for d in "0123456789":
            await tap(d, d, f"Digit{d}")

        # ── Special keys ──────────────────────────────────────────────────
        print("\n=== Special keys ===")
        await tap("Return",    "Enter",      "Enter")
        await tap("Tab",       "Tab",        "Tab")
        await tap("Escape",    "Escape",     "Escape")
        await tap("Backspace", "Backspace",  "Backspace")
        await tap("Delete",    "Delete",     "Delete")
        await tap("Home",      "Home",       "Home")
        await tap("End",       "End",        "End")
        await tap("PageUp",    "PageUp",     "PageUp")
        await tap("PageDown",  "PageDown",   "PageDown")
        await tap("ArrowLeft", "ArrowLeft",  "ArrowLeft")
        await tap("ArrowRight","ArrowRight", "ArrowRight")
        await tap("ArrowUp",   "ArrowUp",    "ArrowUp")
        await tap("ArrowDown", "ArrowDown",  "ArrowDown")
        await tap("Space",     " ",          "Space")

        # ── Function keys ─────────────────────────────────────────────────
        print("\n=== Function keys ===")
        for n in range(1, 13):
            await tap(f"F{n}", f"F{n}", f"F{n}")

        # ── Cmd + safe letters (copy/paste/undo/cut/save/find/redo) ───────
        print("\n=== Cmd + letter (safe subset) ===")
        for ch in "acvzxsf":   # deliberately omit q/h/m/n/w/t that open/hide apps
            await combo(f"Cmd+{ch.upper()}", CMD, (ch, f"Key{ch.upper()}"))

        # ── Cmd + Shift + Z (redo) ────────────────────────────────────────
        print("\n=== Cmd+Shift combos ===")
        await combo("Cmd+Shift+Z", CMD, SHF, ("z", "KeyZ"))

        # ── Cmd + digits / symbols ────────────────────────────────────────
        print("\n=== Cmd + digit/symbol ===")
        await combo("Cmd+0",     CMD, ("0", "Digit0"))
        await combo("Cmd+=",     CMD, ("=", "Equal"))
        await combo("Cmd+-",     CMD, ("-", "Minus"))
        await combo("Cmd+[",     CMD, ("[", "BracketLeft"))
        await combo("Cmd+]",     CMD, ("]", "BracketRight"))

        # ── Ctrl + letter ─────────────────────────────────────────────────
        print("\n=== Ctrl + letter ===")
        for ch in "acvzx":
            await combo(f"Ctrl+{ch.upper()}", CTL, (ch, f"Key{ch.upper()}"))

        # ── Shift + arrows ────────────────────────────────────────────────
        print("\n=== Shift + arrows ===")
        for arrow, code in [("ArrowLeft","ArrowLeft"),("ArrowRight","ArrowRight"),
                             ("ArrowUp","ArrowUp"),("ArrowDown","ArrowDown")]:
            await combo(f"Shift+{arrow}", SHF, (arrow, code))

        # ── Option + letter ───────────────────────────────────────────────
        print("\n=== Option + letter ===")
        for ch in "acv":
            await combo(f"Option+{ch.upper()}", ALT, (ch, f"Key{ch.upper()}"))

        # ── Cmd + Shift + digit (screenshots — intercepted, won't fire system action
        #    because keymap_capture's performKeyEquivalent_ returns True) ──────
        print("\n=== Cmd+Shift+digit ===")
        for d in "345":
            await combo(f"Cmd+Shift+{d}", CMD, SHF, (d, f"Digit{d}"))

        print("\n=== Done ===")

asyncio.run(run())
