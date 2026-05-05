# Performance

Measured on a Mac mini M1/M2 over localhost SSH tunnel.

| Capture | Codec | Frame rate | Encode time | Bandwidth |
|---------|-------|-----------|-------------|-----------|
| VNC (screensharingd) | JPEG | ~20fps | ~17ms/frame | ~55 Mbps |
| VNC (screensharingd) | H.264 | ~20fps | ~5ms/frame | ~5 Mbps |
| SCK (GPU compositor) | H.264 | **~60fps** | ~5ms/frame | ~5 Mbps |

The frame rate jump comes from switching capture backends: screensharingd is capped by its own polling rate, SCK delivers directly from the GPU compositor. The codec switch from JPEG to H.264 mainly affects bandwidth — H.264 only encodes changed pixels, JPEG re-encodes the entire frame every time. H.264/H.265 encoding uses Apple VideoToolbox (hardware media engine) — near-zero CPU.

## Browser compatibility

| Browser | Video codec | Audio | Clipboard | Notes |
|---------|------------|-------|-----------|-------|
| Chrome 110+ | H.264, H.265, AV1 | ✅ | Live auto-sync (both directions) | AV1 hardware requires M3+/A17 Pro |
| Firefox 130+ | H.264 | ✅ | Manual (Ctrl+V both directions) | No H.265 WebCodecs |
| Safari 26+ | H.265, H.264 | ✅ | Manual (Ctrl+V both directions) | H.265 selected automatically |

Clipboard works in both directions on every browser — the difference is whether sync is automatic or you press Ctrl+V. Chrome (with one-time permission grant) keeps the Mac and browser clipboards continuously aligned; Firefox and Safari paste with Ctrl+V into either side. A clipboard text box in the side menu also works as a manual fallback on every browser.

The server negotiates the best codec the browser reports it supports. JPEG fallback is used only when WebCodecs is unavailable (rare).

## Tip: keep the screen non-static for best responsiveness

This applies to both SCK and VNC paths — the throttle is in macOS itself, not in either capture backend. macOS's WindowServer drops the display compositor to ~3Hz when nothing is animating on screen. SCK reads frames out of that compositor; if the compositor is asleep, SCK has nothing to capture. So you type a character, wait for the compositor to wake, then see it appear — 500ms–3s of first-keystroke latency.

The server runs a compositor keepalive subprocess (a near-invisible window driven by CVDisplayLink) that prevents this throttling. In most cases you'll never notice. But if you do see sluggishness after a long idle period, **moving the mouse** or having any animation running (a terminal with a clock, a browser tab with activity) keeps the compositor warm and eliminates the latency entirely.

## Known limitations

- **Lock screen and login window are reachable from remote.** This is intentional — input events flow regardless of lock state, so you can unlock the Mac or log in from your browser tab. Both SCK and VNC paths can capture the loginwindow once Screen Recording is granted.
- **Retina/HiDPI.** SCK captures at logical resolution (e.g. 1920×1080 on a 27" 5K display). Physical pixel counts above 4K will strain the encoder; use `--max-fps 30` on very high-res displays.
- **HTTPS required for clipboard on LAN.** If you expose the server directly on a LAN (not via SSH tunnel), `navigator.clipboard.writeText` requires HTTPS. The SSH tunnel works around this by keeping everything on `localhost`.
- **`--api-only` requires permissions already granted.** If Screen Recording or Accessibility haven't been granted yet, the server falls back to VNC automatically in `auto` mode.
