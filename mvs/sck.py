import logging
import os
import queue
import struct
import sys
import threading
import time

import numpy as np

import mvs.audio as _audio_mod
import mvs.handler as _handler_mod

log = logging.getLogger("macvnc")

# ---------------------------------------------------------------------------
# SCK (ScreenCaptureKit) capture subprocess — Python script sent via -c to sys.executable.
# Requires the enhanced Screen Recording permission on macOS 15+ (System Settings >
# Privacy & Security > Screen Recording — toggle Python on).
# On macOS 26+, CGWindowListCreateImage only captures the desktop wallpaper;
# SCK is the only API that delivers full screen including application windows.
# Frame wire format: BVNC(4) + W(4LE uint32) + H(4LE uint32) + ts_ms(8LE uint64) + W*H*4 BGRA bytes.
# ---------------------------------------------------------------------------
_SCSTREAM_CAPTURE_SRC = r"""
# SCK capture subprocess via PyObjC ScreenCaptureKit.
# Runs a CoreFoundation run loop on the main thread; SCK delivers frames there.
# CVPixelBuffer raw bytes are extracted via ctypes CoreVideo.
import sys, os, struct, time, threading, ctypes
from Foundation import NSObject, NSRunLoop, NSDate, NSDefaultRunLoopMode
import ScreenCaptureKit as SCK
import CoreMedia

MAGIC = b'BVNC'
out   = sys.stdout.buffer
ppid  = os.getppid()

_cv = ctypes.CDLL('/System/Library/Frameworks/CoreVideo.framework/CoreVideo')
_cv.CVPixelBufferLockBaseAddress.restype    = ctypes.c_int32
_cv.CVPixelBufferLockBaseAddress.argtypes   = [ctypes.c_void_p, ctypes.c_uint64]
_cv.CVPixelBufferUnlockBaseAddress.restype  = ctypes.c_int32
_cv.CVPixelBufferUnlockBaseAddress.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
_cv.CVPixelBufferGetBaseAddress.restype     = ctypes.c_void_p
_cv.CVPixelBufferGetBaseAddress.argtypes    = [ctypes.c_void_p]
_cv.CVPixelBufferGetWidth.restype           = ctypes.c_size_t
_cv.CVPixelBufferGetWidth.argtypes          = [ctypes.c_void_p]
_cv.CVPixelBufferGetHeight.restype          = ctypes.c_size_t
_cv.CVPixelBufferGetHeight.argtypes         = [ctypes.c_void_p]
_cv.CVPixelBufferGetBytesPerRow.restype     = ctypes.c_size_t
_cv.CVPixelBufferGetBytesPerRow.argtypes    = [ctypes.c_void_p]

class _FrameOutput(NSObject):
    def stream_didOutputSampleBuffer_ofType_(self, stream, sampleBuffer, outputType):
        if outputType != 0:  # SCStreamOutputTypeScreen == 0
            return
        try:
            pb_obj = CoreMedia.CMSampleBufferGetImageBuffer(sampleBuffer)
            if pb_obj is None:
                return
            # PyObjC wraps CF objects; hash() returns the CF pointer for NSObject-bridged types.
            pb = hash(pb_obj)
            _cv.CVPixelBufferLockBaseAddress(pb, 1)  # kCVPixelBufferLock_ReadOnly = 1
            try:
                W   = int(_cv.CVPixelBufferGetWidth(pb))
                H   = int(_cv.CVPixelBufferGetHeight(pb))
                bpr = int(_cv.CVPixelBufferGetBytesPerRow(pb))
                if W <= 0 or H <= 0:
                    return
                base = _cv.CVPixelBufferGetBaseAddress(pb)
                if not base:
                    return
                ts_ms = int(time.time() * 1000)
                hdr = MAGIC + struct.pack('<IIQ', W, H, ts_ms)
                if bpr == W * 4:
                    pixel_data = ctypes.string_at(base, H * bpr)
                else:
                    pixel_data = b''.join(ctypes.string_at(base + r * bpr, W * 4) for r in range(H))
                out.write(hdr + pixel_data)
                out.flush()
            finally:
                _cv.CVPixelBufferUnlockBaseAddress(pb, 1)
        except BrokenPipeError:
            os._exit(0)
        except Exception as e:
            sys.stderr.write('SCKCapture frame: ' + str(e) + '\n')

_ready  = threading.Event()
_content = [None]
_cerr   = [None]

def _content_cb(content, error):
    _content[0] = content
    _cerr[0]    = error
    _ready.set()

try:
    SCK.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
        False, True, _content_cb)
except AttributeError:
    # Older macOS: try legacy method name
    SCK.SCShareableContent.getExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
        False, True, _content_cb)

t0 = time.time()
while not _ready.is_set() and time.time() - t0 < 10:
    NSRunLoop.mainRunLoop().runMode_beforeDate_(NSDefaultRunLoopMode, NSDate.dateWithTimeIntervalSinceNow_(0.1))

if _cerr[0] or _content[0] is None:
    sys.stderr.write('SCKCapture: content error: ' + str(_cerr[0]) + '\n')
    sys.exit(1)

displays = _content[0].displays()
if not displays:
    sys.stderr.write('SCKCapture: no displays\n')
    sys.exit(1)

display = displays[0]
W, H    = display.width(), display.height()
sys.stderr.write('SCKCapture: starting ' + str(W) + 'x' + str(H) + ' @ 60fps (BGRA/SCK)\n')
sys.stderr.flush()

filt   = SCK.SCContentFilter.alloc().initWithDisplay_excludingApplications_exceptingWindows_(display, [], [])
config = SCK.SCStreamConfiguration.alloc().init()
config.setWidth_(W)
config.setHeight_(H)
config.setPixelFormat_(0x42475241)  # kCVPixelFormatType_32BGRA
config.setShowsCursor_(True)
config.setCapturesAudio_(False)

writer = _FrameOutput.alloc().init()
stream = SCK.SCStream.alloc().initWithFilter_configuration_delegate_(filt, config, writer)
stream.addStreamOutput_type_sampleHandlerQueue_error_(writer, 0, None, None)

_started = threading.Event()
_serr    = [None]
def _start_cb(e): _serr[0] = e; _started.set()
stream.startCaptureWithCompletionHandler_(_start_cb)

t0 = time.time()
while not _started.is_set() and time.time() - t0 < 8:
    NSRunLoop.mainRunLoop().runMode_beforeDate_(NSDefaultRunLoopMode, NSDate.dateWithTimeIntervalSinceNow_(0.1))

if _serr[0]:
    sys.stderr.write('SCKCapture: start error: ' + str(_serr[0]) + '\n')
    sys.exit(1)

sys.stderr.write('SCKCapture: stream active\n')
sys.stderr.flush()

# Run main loop; parent-death watchdog fires every ~5s
_ppid_check = 0
while True:
    NSRunLoop.mainRunLoop().runMode_beforeDate_(NSDefaultRunLoopMode, NSDate.dateWithTimeIntervalSinceNow_(0.01))
    _ppid_check += 1
    if _ppid_check >= 500:
        _ppid_check = 0
        try: os.kill(ppid, 0)
        except ProcessLookupError: break
"""

# ---------------------------------------------------------------------------
# TCC permission watcher — polls TCC.db mtime and fires callbacks when
# Screen Recording or Accessibility grants appear (or are revoked).
# This lets the server upgrade from VNC fallback to native APIs live,
# without requiring a process restart after the user clicks Allow.
# ---------------------------------------------------------------------------
class TCCWatcher:
    """Watches TCC.db mtime and fires on_tcc_change() when permissions may have changed.

    Does NOT interpret the DB content — callers decide what to probe after a change.
    This avoids false positives from bundle-ID vs path-based grant mismatches.
    """
    _TCC_DB = os.path.expanduser(
        "~/Library/Application Support/com.apple.TCC/TCC.db")

    def __init__(self, on_tcc_change=None, interval=5):
        self._on_change  = on_tcc_change
        self._interval   = interval
        self._last_mtime = 0.0

    def start(self):
        threading.Thread(target=self._watch, daemon=True, name="tcc-watcher").start()

    def _watch(self):
        while True:
            time.sleep(self._interval)
            try:
                mtime = os.path.getmtime(self._TCC_DB)
                if mtime != self._last_mtime:
                    self._last_mtime = mtime
                    if self._on_change:
                        try:
                            self._on_change()
                        except Exception:
                            pass
            except Exception:
                pass


# ---------------------------------------------------------------------------
# DisplayStreamBridge — direct screen capture via SCK (ScreenCaptureKit) subprocess.
# Requires Screen Recording (kTCCServiceScreenCapture) granted to python3.
# Falls back gracefully: if no frame within 5s, is_running() returns False and
# BridgeProxy switches to VNCBridge for all capture calls.
# ---------------------------------------------------------------------------
class DisplayStreamBridge:
    _FRAME_HDR = 20  # magic(4) + W(4LE) + H(4LE) + ts_ms(8LE)

    def __init__(self):
        self._proc = None
        self._lock = threading.Lock()
        self._fb = None
        self._fb_seq = 0
        self._fb_ms = 0
        self._W = 0
        self._H = 0
        self._running = False

    def start(self):
        """Launch the capture subprocess. Returns True when first frame arrives (≤5s)."""
        import subprocess as _sp
        self._running = True
        self._proc = _sp.Popen(
            [sys.executable, "-c", _SCSTREAM_CAPTURE_SRC],
            stdout=_sp.PIPE, stderr=_sp.PIPE,
        )
        t = threading.Thread(target=self._read_loop, daemon=True)
        t.start()
        deadline = time.time() + 5.0
        while time.time() < deadline:
            with self._lock:
                if self._fb is not None:
                    log.info("DisplayStreamBridge: %dx%d capture active", self._W, self._H)
                    return True
            time.sleep(0.05)
        err = b""
        if self._proc and self._proc.poll() is not None:
            err = self._proc.stderr.read(200)
        log.warning("DisplayStreamBridge: no frame in 5s — %s",
                    err.decode(errors="replace").strip() or "Screen Recording permission needed")
        self._running = False
        return False

    def _read_exact(self, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = self._proc.stdout.read(n - len(buf))
            if not chunk:
                raise EOFError("capture helper exited")
            buf += chunk
        return bytes(buf)

    def _read_loop(self):
        try:
            while self._running:
                hdr = self._read_exact(self._FRAME_HDR)
                magic = hdr[:4]
                if magic not in (b'UVNC', b'BVNC'):
                    log.warning("DisplayStreamBridge: bad magic %r", magic)
                    break
                W     = struct.unpack_from('<I', hdr, 4)[0]
                H     = struct.unpack_from('<I', hdr, 8)[0]
                ts_ms = struct.unpack_from('<Q', hdr, 12)[0]
                if magic == b'BVNC':
                    # BGRA payload — 4 bytes/pixel; encoder uses format="bgra"
                    data = self._read_exact(W * H * 4)
                    frame = np.frombuffer(data, dtype=np.uint8).reshape(H, W, 4).copy()
                else:
                    data  = self._read_exact(W * H * 3)
                    frame = np.frombuffer(data, dtype=np.uint8).reshape(H, W, 3).copy()
                with self._lock:
                    self._fb      = frame
                    self._fb_seq += 1
                    self._fb_ms    = ts_ms if ts_ms else int(time.time() * 1000)
                    self._W, self._H = W, H
        except Exception as e:
            log.warning("DisplayStreamBridge: read loop stopped: %s", e)
        self._running = False

    @property
    def dimensions(self):
        with self._lock:
            return self._W, self._H

    def get_current_frame(self):
        with self._lock:
            if self._fb is None:
                return None, 0
            return self._fb, self._fb_ms

    def is_running(self):
        if not self._running:
            return False
        if self._proc and self._proc.poll() is not None:
            self._running = False
            return False
        return True


# ---------------------------------------------------------------------------
# InProcessSCKBridge — SCK capture running in the server process itself.
# On macOS 26, subprocess-spawned Python processes get -3801 even with a valid
# TCC grant; running SCK in the LaunchAgent process (GUI session, proper bundle
# context) avoids that.  Same API surface as DisplayStreamBridge.
# ---------------------------------------------------------------------------
class InProcessSCKBridge:
    _fo_class   = None  # ObjC class registered once; cached here after first use
    _sck_queue  = None  # GCD global queue for SCK frame delivery; set by _make_fo_class

    def __init__(self):
        self._lock   = threading.Lock()
        self._fb     = None
        self._fb_seq = 0
        self._fb_ms  = 0
        self._W = self._H = 0
        self._running = False
        self._stream  = None
        self._writer  = None

    @classmethod
    def _make_fo_class(cls):
        """Create and cache the NSObject frame-output delegate (ObjC class registration is once-only)."""
        if cls._fo_class is not None:
            return cls._fo_class
        from Foundation import NSObject
        import CoreMedia, ctypes, warnings, re as _re
        import objc as _objc
        warnings.filterwarnings('ignore', category=_objc.ObjCPointerWarning)
        _cv  = ctypes.CDLL('/System/Library/Frameworks/CoreVideo.framework/CoreVideo')
        _cm  = ctypes.CDLL('/System/Library/Frameworks/CoreMedia.framework/CoreMedia')
        _cm.CMSampleBufferGetImageBuffer.restype  = ctypes.c_void_p
        _cm.CMSampleBufferGetImageBuffer.argtypes = [ctypes.c_void_p]
        _gcd = ctypes.CDLL('/usr/lib/system/libdispatch.dylib')
        _gcd.dispatch_get_global_queue.restype  = ctypes.c_void_p
        _gcd.dispatch_get_global_queue.argtypes = [ctypes.c_long, ctypes.c_ulong]
        cls._sck_queue = _objc.objc_object(c_void_p=_gcd.dispatch_get_global_queue(21, 0))

        def _sb_to_ptr(sb):
            """Extract the ObjC CMSampleBufferRef pointer from a PyObjC proxy.
            Strategy 1: PyObjCPointer stores ptr at offset 16 in the CPython object.
            Strategy 2: Parse the first hex address from description (fragile but works
                        for CF-bridged types whose description starts with 'CMSampleBuffer 0x...')."""
            try:
                # In CPython+PyObjC, the Python object at id(sb) is laid out as:
                # [ob_refcnt:8][ob_type:8][objc_id:8]...
                return ctypes.cast(id(sb), ctypes.POINTER(ctypes.c_void_p))[2]
            except Exception:
                pass
            try:
                m = _re.search(r'\b0x([0-9a-fA-F]+)\b', str(sb))
                if m:
                    return int(m.group(1), 16)
            except Exception:
                pass
            return 0
        _cv.CVPixelBufferLockBaseAddress.restype    = ctypes.c_int32
        _cv.CVPixelBufferLockBaseAddress.argtypes   = [ctypes.c_void_p, ctypes.c_uint64]
        _cv.CVPixelBufferUnlockBaseAddress.restype  = ctypes.c_int32
        _cv.CVPixelBufferUnlockBaseAddress.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
        _cv.CVPixelBufferGetBaseAddress.restype     = ctypes.c_void_p
        _cv.CVPixelBufferGetBaseAddress.argtypes    = [ctypes.c_void_p]
        _cv.CVPixelBufferGetWidth.restype           = ctypes.c_size_t
        _cv.CVPixelBufferGetWidth.argtypes          = [ctypes.c_void_p]
        _cv.CVPixelBufferGetHeight.restype          = ctypes.c_size_t
        _cv.CVPixelBufferGetHeight.argtypes         = [ctypes.c_void_p]
        _cv.CVPixelBufferGetBytesPerRow.restype     = ctypes.c_size_t
        _cv.CVPixelBufferGetBytesPerRow.argtypes    = [ctypes.c_void_p]

        # CoreMedia audio extraction: get raw PCM from audio CMSampleBuffers.
        _cm.CMSampleBufferGetNumSamples.restype  = ctypes.c_long
        _cm.CMSampleBufferGetNumSamples.argtypes = [ctypes.c_void_p]
        _cm.CMSampleBufferGetDataBuffer.restype  = ctypes.c_void_p
        _cm.CMSampleBufferGetDataBuffer.argtypes = [ctypes.c_void_p]
        _cm.CMBlockBufferGetDataPointer.restype  = ctypes.c_int32
        _cm.CMBlockBufferGetDataPointer.argtypes = [
            ctypes.c_void_p,                  # theBuffer
            ctypes.c_size_t,                  # offset
            ctypes.POINTER(ctypes.c_size_t),  # lengthAtOffsetOut
            ctypes.POINTER(ctypes.c_size_t),  # totalLengthOut
            ctypes.POINTER(ctypes.c_void_p),  # dataPointerOut (char**)
        ]

        class _FrameOutputInProc(NSObject):
            # _bridge_ref is a class variable set to the active InProcessSCKBridge before
            # the stream starts.  Using a class variable avoids closure/ObjC registration issues.
            _bridge_ref = None

            def stream_didOutputSampleBuffer_ofType_(self_obj, stream, sampleBuffer, outputType):
                if outputType == 1:
                    # Audio sample buffer — extract raw PCM and queue for Opus encoding.
                    # _audio_clients, _audio_raw_q are looked up in MODULE globals (sck.py),
                    # which are actually _audio_mod._audio_clients and _audio_mod._audio_raw_q.
                    if _audio_mod._audio_clients == 0:
                        return
                    try:
                        sb_ptr = _sb_to_ptr(sampleBuffer)
                        if not sb_ptr:
                            log.debug("SCK audio: _sb_to_ptr returned 0")
                            return
                        block_buf = _cm.CMSampleBufferGetDataBuffer(sb_ptr)
                        if not block_buf:
                            log.debug("SCK audio: CMSampleBufferGetDataBuffer returned null")
                            return
                        length_at = ctypes.c_size_t(0)
                        total_len = ctypes.c_size_t(0)
                        data_ptr  = ctypes.c_void_p(0)
                        status = _cm.CMBlockBufferGetDataPointer(
                                block_buf, 0,
                                ctypes.byref(length_at), ctypes.byref(total_len),
                                ctypes.byref(data_ptr))
                        if status != 0:
                            log.debug("SCK audio: CMBlockBufferGetDataPointer status=%d", status)
                            return
                        if not data_ptr.value or total_len.value == 0:
                            log.debug("SCK audio: empty data ptr=%s len=%d", data_ptr.value, total_len.value)
                            return
                        raw = bytes((ctypes.c_char * total_len.value).from_address(data_ptr.value))
                        try:
                            _audio_mod._audio_raw_q.put_nowait(raw)
                        except queue.Full:
                            pass  # drop — encoder is behind
                    except Exception as _ae:
                        log.debug("SCK audio callback: %s", _ae)
                    return
                if outputType != 0:
                    return
                bridge = _FrameOutputInProc._bridge_ref
                if bridge is None:
                    return
                try:
                    # Skip expensive pixel copy when no clients are watching AND we already
                    # have a valid frame stored (the startup probe needs at least one frame
                    # to succeed, so skip only after _fb is set).
                    if _handler_mod._active_clients == 0 and bridge._fb is not None:
                        return
                    sb_ptr = _sb_to_ptr(sampleBuffer)
                    if not sb_ptr:
                        return
                    pb = _cm.CMSampleBufferGetImageBuffer(sb_ptr)
                    if not pb:
                        return
                    _cv.CVPixelBufferLockBaseAddress(pb, 1)
                    try:
                        W   = int(_cv.CVPixelBufferGetWidth(pb))
                        H   = int(_cv.CVPixelBufferGetHeight(pb))
                        bpr = int(_cv.CVPixelBufferGetBytesPerRow(pb))
                        base = _cv.CVPixelBufferGetBaseAddress(pb)
                        if not base or W <= 0 or H <= 0:
                            return
                        if bpr == W * 4:
                            data = bytes(ctypes.string_at(base, H * bpr))
                        else:
                            data = b''.join(ctypes.string_at(base + r * bpr, W * 4) for r in range(H))
                        frame = np.frombuffer(data, dtype=np.uint8).reshape(H, W, 4).copy()
                        with bridge._lock:
                            bridge._fb     = frame
                            bridge._fb_seq += 1
                            bridge._fb_ms  = int(time.time() * 1000)
                            bridge._W, bridge._H = W, H
                    finally:
                        _cv.CVPixelBufferUnlockBaseAddress(pb, 1)
                except Exception as e:
                    log.debug("InProcessSCK frame: %s", e)

        cls._fo_class = _FrameOutputInProc
        return cls._fo_class

    def start(self):
        """Initialize SCK capture in-process. Returns True when the first frame arrives (≤5s)."""
        done = threading.Event()
        ok   = [False]

        def _init():
            try:
                import ScreenCaptureKit as SCKmod
                FO = InProcessSCKBridge._make_fo_class()
                FO._bridge_ref = self

                _cnt_ev  = threading.Event()
                _content = [None]
                _cerr    = [None]

                def _cnt_cb(content, error):
                    _content[0] = content
                    _cerr[0]    = error
                    _cnt_ev.set()

                try:
                    SCKmod.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
                        False, True, _cnt_cb)
                except AttributeError:
                    SCKmod.SCShareableContent.getExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
                        False, True, _cnt_cb)

                # Give the user up to 60s to respond to the macOS consent dialog
                # (shown when no TCC entry exists for com.apple.python3).
                if not _cnt_ev.wait(60):
                    log.warning("InProcessSCK: content timeout — if a 'Python would like to record your screen' dialog appeared, click Allow then restart the server")
                    done.set(); return
                if _cerr[0] or not _content[0]:
                    log.warning("InProcessSCK: content error: %s — grant Screen Recording in System Settings > Privacy > Screen Recording, then restart", _cerr[0])
                    done.set(); return

                displays = _content[0].displays()
                if not displays:
                    log.warning("InProcessSCK: no displays")
                    done.set(); return

                display = displays[0]
                W, H = display.width(), display.height()

                filt = SCKmod.SCContentFilter.alloc().initWithDisplay_excludingApplications_exceptingWindows_(display, [], [])
                cfg  = SCKmod.SCStreamConfiguration.alloc().init()
                cfg.setWidth_(W)
                cfg.setHeight_(H)
                cfg.setPixelFormat_(0x42475241)  # kCVPixelFormatType_32BGRA
                cfg.setShowsCursor_(True)
                cfg.setCapturesAudio_(True)
                try: cfg.setExcludesCurrentProcessAudio_(True)
                except AttributeError: pass   # macOS 14+ only
                try: cfg.setSampleRate_(48000.0)
                except AttributeError: pass   # macOS 13+
                try: cfg.setChannelCount_(2)
                except AttributeError: pass   # macOS 13+

                writer = FO.alloc().init()
                stream = SCKmod.SCStream.alloc().initWithFilter_configuration_delegate_(filt, cfg, writer)
                stream.addStreamOutput_type_sampleHandlerQueue_error_(
                    writer, 0, InProcessSCKBridge._sck_queue, None)
                # Register audio output on the same queue (SCK type 1 = audio).
                try:
                    ok_audio = stream.addStreamOutput_type_sampleHandlerQueue_error_(
                        writer, 1, InProcessSCKBridge._sck_queue, None)
                    if ok_audio:
                        log.info("InProcessSCK: audio output registered")
                    else:
                        log.warning("InProcessSCK: audio output registration returned False — audio capture unavailable")
                except Exception as e:
                    log.warning("InProcessSCK: audio output registration failed: %s — audio capture unavailable", e)

                _st_ev = threading.Event()
                _serr  = [None]
                def _st_cb(e): _serr[0] = e; _st_ev.set()
                stream.startCaptureWithCompletionHandler_(_st_cb)

                if not _st_ev.wait(10):
                    log.warning("InProcessSCK: start timeout")
                    done.set(); return
                if _serr[0]:
                    log.warning("InProcessSCK: start error: %s", _serr[0])
                    done.set(); return

                self._stream  = stream
                self._writer  = writer
                self._running = True
                ok[0] = True
                log.info("InProcessSCK: stream active %dx%d", W, H)
                done.set()
                # Hold ObjC refs alive until stopped.
                while self._running:
                    time.sleep(1)
            except Exception as e:
                log.warning("InProcessSCK: init failed: %s", e)
                done.set()

        threading.Thread(target=_init, daemon=True).start()
        done.wait(70)  # allows 60s for user to respond to macOS consent dialog + stream start
        if not ok[0]:
            return False

        deadline = time.time() + 5.0
        while time.time() < deadline:
            with self._lock:
                if self._fb is not None:
                    log.info("InProcessSCKBridge: %dx%d capture active", self._W, self._H)
                    return True
            time.sleep(0.05)

        log.warning("InProcessSCKBridge: no frame in 5s")
        self._running = False
        return False

    @property
    def dimensions(self):
        with self._lock:
            return self._W, self._H

    def get_current_frame(self):
        with self._lock:
            if self._fb is None:
                return None, 0
            return self._fb, self._fb_ms

    def is_running(self):
        return self._running
