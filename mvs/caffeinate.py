"""caffeinate.py — opportunistic display power management.

Spawns `caffeinate -di` for the lifetime of a WebSocket session to prevent
macOS from putting the display (or system) to sleep while a remote-desktop
client is connected. When the last client disconnects, caffeinate exits
and macOS resumes its normal idle-timeout / sleep policy.

Why this exists:
  • A user opens the browser remote-desktop, walks away from the Mac,
    macOS dims/blacks the display after the configured idle timeout
    (~2 min by default), SCK then captures black frames and the
    remote view goes dark even though the bundle is healthy.
  • A laptop on battery may enter idle-sleep after ~10 min, freezing
    the bundle's networking entirely until the lid is opened.
  • This is at a different layer than the screensharingd-virtual-display
    keep-warm thread (which handles the cloud-Mac no-display-attached
    case). Both layers can fail independently; we use both.

Scoping:
  • Tied to active WebSocket clients via reference count. While >=1
    client is connected, caffeinate runs. When the last one disconnects,
    caffeinate is killed and the Mac resumes normal sleep policy. No
    permanent power-state changes; no leftover processes if the bundle
    crashes (caffeinate -w $$ would also tie lifetime to PID, but we
    don't need that since explicit kill on last-disconnect is cleaner).
  • Caffeinate -di flags:
      -d : prevent display sleep
      -i : prevent idle sleep (system sleep due to inactivity)
    NOT -s (system sleep prevention regardless of source — would prevent
    user-initiated sleep too, which is not what we want).
  • Skips entirely on non-Darwin (defensive — caller should avoid this
    module on non-Darwin anyway).

Thread-safe: count + spawn/kill guarded by a Lock.
"""
import logging
import platform
import subprocess
import threading

log = logging.getLogger("macvnc.caffeinate")

_lock = threading.Lock()
_count = 0
_proc: "subprocess.Popen | None" = None


def acquire() -> None:
    """Increment the reference count. Spawns caffeinate -di on the 0→1 edge."""
    global _count, _proc
    if platform.system() != "Darwin":
        return
    with _lock:
        _count += 1
        if _count == 1 and _proc is None:
            try:
                _proc = subprocess.Popen(
                    ["/usr/bin/caffeinate", "-d", "-i"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                log.info("caffeinate -di spawned (pid=%d) — display+idle sleep "
                         "suppressed while clients connected", _proc.pid)
            except Exception as e:
                log.warning("caffeinate spawn failed: %s — display may sleep "
                            "while clients are connected", e)
                _proc = None


def release() -> None:
    """Decrement the reference count. Kills caffeinate on the 1→0 edge."""
    global _count, _proc
    if platform.system() != "Darwin":
        return
    with _lock:
        if _count > 0:
            _count -= 1
        if _count == 0 and _proc is not None:
            try:
                _proc.terminate()
                _proc.wait(timeout=2)
                log.info("caffeinate exited — Mac resumes normal sleep policy")
            except Exception as e:
                log.debug("caffeinate kill: %s", e)
                try:
                    _proc.kill()
                except Exception:
                    pass
            _proc = None
