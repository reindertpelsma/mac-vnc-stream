"""build_app.py — produce macscreencast.app via py2app.

Usage:
    python3 build_app.py py2app

Output:
    dist/macscreencast.app

Why this exists:
    macOS Tahoe (26+) refuses to honor Screen Recording grants for the
    Python interpreter (com.apple.python3) even when TCC.db says they're
    allowed. Bundling our server as a .app with our own bundle identifier
    (com.macscreencast.server) escapes Tahoe's interpreter restriction —
    TCC tracks our identity, not Apple's, and grants stick.

After the build, the bundle is ad-hoc signed (codesign --sign -) so it has
a stable CDHash for TCC to attach grants to. No Apple Developer cert
required; signing locally is sufficient because we don't need
notarization (notarization is for Gatekeeper, which we sidestep with
xattr -dr com.apple.quarantine post-install).

Both install paths converge on the same artifact:
    setup.sh   (from git clone)            → runs this script
    install.sh (curl-pipe convenience)     → downloads the same .app
                                              from a GitHub Release tag
"""
import os
import subprocess
import sys
from pathlib import Path

try:
    from setuptools import setup
except ImportError:
    sys.exit("ERROR: setuptools missing. Run: python3 -m pip install --user setuptools py2app")

REPO = Path(__file__).resolve().parent
APP = ['server.py']

# Resources copied into Contents/Resources/. The frontend HTML must be here
# because mvs.handler resolves it via the bundle-aware loader at import time.
DATA_FILES = [
    ('frontend', [str(REPO / 'frontend' / 'index.html')]),
]

# CFBundleIdentifier is the TCC identity. com.macscreencast.server is unique
# to this project (not com.apple.anything), so Tahoe doesn't apply
# interpreter-grant restrictions to it.
PLIST = {
    'CFBundleIdentifier':       'com.macscreencast.server',
    'CFBundleName':             'macscreencast',
    'CFBundleDisplayName':      'macscreencast',
    'CFBundleShortVersionString': '0.1.0',
    'CFBundleVersion':          '0.1.0',
    'NSHighResolutionCapable':  True,
    # Background-only — no menu bar, no dock icon. We're a service, not an app.
    'LSUIElement':              True,
    # Required for any TCC prompt to fire from inside the bundle. Even though
    # we don't show our own dialogs, the system uses these strings when
    # presenting the Screen Recording / Accessibility consent UI.
    'NSScreenCaptureUsageDescription':
        'macscreencast captures the screen so you can view it remotely in a browser.',
    'NSAppleEventsUsageDescription':
        'macscreencast sends keyboard and mouse input to control this Mac remotely.',
    # Inputs Monitoring is a related TCC class on macOS 14+; safe to declare.
    'NSInputMonitoringUsageDescription':
        'macscreencast forwards browser keypresses and mouse events to this Mac.',
}

OPTIONS = {
    'argv_emulation': False,
    'plist':          PLIST,
    # Packages whose entire tree must be copied — anything we import from
    # transitively. py2app's static analyser misses dynamic imports inside
    # PyAV and PyObjC, hence the explicit list.
    'packages': [
        'mvs',
        'av',
        'numpy',
        'PIL',
        'cryptography',
        'websockets',
        'objc',
    ],
    # Modules to force-include (single .py files / submodules py2app's
    # analyser may skip).
    'includes': [
        'AppKit', 'Foundation', 'Quartz',
        'ScreenCaptureKit', 'CoreMedia', 'CoreAudio',
        'AVFoundation',
    ],
    # Bloat to skip.
    'excludes': [
        'tkinter', 'PyQt5', 'PySide2', 'PySide6', 'IPython',
        'jupyter', 'notebook', 'matplotlib', 'pytest',
    ],
    # Skip the optional turbojpeg — it's a runtime nice-to-have, not required,
    # and bundling its system dylib is messy. Server falls back to PIL.
    # (We list it in excludes implicitly by not listing it in packages.)
    'optimize':       1,
    'compressed':     True,
    # Don't bundle a Python.framework symlink farm at the bundle root —
    # py2app's default is to include one inside the .app, which is what
    # we want here so the .app is self-contained.
    'semi_standalone': False,
}


def main():
    setup(
        app=APP,
        data_files=DATA_FILES,
        options={'py2app': OPTIONS},
        setup_requires=['py2app'],
    )


def _post_build_sign():
    """Ad-hoc sign the produced .app so TCC has a stable CDHash to track.
    Called after `python3 build_app.py py2app` succeeds."""
    app_path = REPO / 'dist' / 'macscreencast.app'
    if not app_path.is_dir():
        return
    print("\n==> Ad-hoc signing %s" % app_path)
    # --force overrides any existing signature, --deep signs nested binaries
    # (Python.framework dylibs, C extensions). - (single dash) is the ad-hoc
    # identity — no certificate needed.
    # Notes on flags:
    #   --force      — overwrite the existing Apple signatures inside the bundle
    #   --deep       — recursively sign nested binaries (Python3.framework dylibs,
    #                  C extensions inside site-packages)
    #   --sign -     — ad-hoc identity (no certificate)
    # NOT using --options runtime: hardened runtime forces strict library
    # validation, which then rejects loading Apple's Python3 dylib because the
    # ad-hoc-signed launcher has no Team ID while the framework was signed by
    # Apple — they don't "match" under hardened-runtime rules. Hardened runtime
    # only matters for notarization, which we don't do (we use xattr -dr quarantine
    # instead). Without it, dyld is permissive enough to load mixed signatures.
    subprocess.check_call([
        'codesign', '--force', '--deep', '--sign', '-',
        str(app_path),
    ])
    # Verify
    subprocess.check_call(['codesign', '-dv', '--verbose=2', str(app_path)])
    print("==> Signed. Bundle ready at %s" % app_path)


if __name__ == '__main__':
    main()
    if 'py2app' in sys.argv:
        try:
            _post_build_sign()
        except subprocess.CalledProcessError as e:
            print("WARNING: codesign step failed (%s) — bundle exists but TCC may not track it stably." % e)
