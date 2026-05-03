
# ---------------------------------------------------------------------------
# BridgeProxy — routes capture calls to DisplayStreamBridge (if active) else VNCBridge,
# and always routes input (key/pointer/clipboard) to VNCBridge.
# ---------------------------------------------------------------------------
class BridgeProxy:
    def __init__(self, vnc, ds=None):
        self._v = vnc
        self._d = ds

    def _cap(self):
        return self._d if (self._d and self._d.is_running()) else self._v

    def get_current_frame(self):
        return self._cap().get_current_frame()

    @property
    def _fb_seq(self):
        return self._cap()._fb_seq

    @property
    def _fb_ms(self):
        return self._cap()._fb_ms

    @property
    def _fbu_count(self):
        return self._v._fbu_count

    @property
    def server_clipboard_seq(self):
        return self._v.server_clipboard_seq

    @property
    def server_clipboard(self):
        return self._v.server_clipboard

    @property
    def dimensions(self):
        return self._cap().dimensions

    def send_pointer(self, *a, **k):
        return self._v.send_pointer(*a, **k)

    def send_key(self, *a, **k):
        return self._v.send_key(*a, **k)

    def send_clipboard(self, *a, **k):
        return self._v.send_clipboard(*a, **k)

    def send_key_reset(self):
        return self._v.send_key_reset()

    def set_capture(self, ds):
        """Hot-swap the display capture backend (e.g., when SCK permission is granted later)."""
        self._d = ds
