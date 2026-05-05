# Project status

**Last updated:** 2026-05-04

## What this is

mac-vnc-stream is a single-author project. First public commit was 2026-05-01;
~140 commits as of this writing. It is in active daily development by one
person (Reindert Pelsma). There has been no external community review yet,
no external contributors, and no security audit.

If you're evaluating whether to run it, please read this honestly rather than
guessing from commit timestamps alone.

## What's tested

- Smoke tests on every push:
  - macOS hosted GH Actions runner (`macos-latest`) — bootstrap, capture,
    encode, browser-decode end-to-end through the install path.
  - 2 Mbps real-TCP throttle harness (`tests/tcp_throttle.py`) — exercises
    the congestion controller under realistic backpressure.
  - 2 Mbps stress-saturation animation (250 gradient blobs) to keep the
    rate controller's drain/backoff paths exercised.
- Real-world cross-continent stress test (Safari on Scaleway M2 in Paris →
  relay VPS → SSH-tunnelled to a `macos-latest` GH runner) at 161 ms RTT,
  ~200 ms input-to-glass, steady 20 fps, ~2 Mbps wire — see `CLAUDE.md`
  ▸ "Validated under" for full numbers.
- Real-Chrome smoke (DevTools 2 Mbps throttle) on the dev mac.

## What's NOT tested

- **Multi-user / shared-Mac setups.** Not designed for these. The plist's
  password storage trade-off (see README ▸ Security) only makes sense on
  Macs you fully control.
- **Long-duration soak.** No 24 h+ run logged.
- **Browsers other than Chrome and Safari.** Firefox falls back to JPEG
  (WebCodecs gating), but that path hasn't been exercised in days.
- **macOS versions older than 14.** Architecturally most paths should
  work back to 12.3 (SCK requirement); not verified.

## Known limitations

See `docs/performance.md` ▸ "Known limitations" for the up-to-date list.
The biggest ones are: HiDPI displays cap useful fps, clipboard sync needs
HTTPS or a localhost SSH tunnel (browser API rule, not our limitation),
and `--api-only` requires Screen Recording + Accessibility already
granted (auto mode handles the bootstrap automatically). The lock screen
and login window are reachable from remote by design — you can unlock
or log in from the browser tab.

## Bus factor / what happens if I get hit by a bus

Solo project, no contributors yet. The codebase is intentionally readable
(no clever abstractions, comments explain the *why* not the *what*); the
`CLAUDE.md` documents non-obvious design decisions including the failed
approaches that were tried and reverted. If someone forked it tomorrow,
those two files plus the commit history are the handover.

## How to help / report issues

GitHub Issues on `reindertpelsma/mac-vnc-stream`. Useful bug reports include
the relevant section of `/tmp/macvncstream.log` and what you saw in the
browser's DevTools console.
