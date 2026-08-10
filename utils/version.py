"""
Single source of truth for PhotoFlow's version.

Everything that needs a version number reads it from here: the About dialog,
the PyInstaller spec's Windows version resource, the Inno Setup installer, the
update check, and the licence/telemetry payloads. Bumping the number in one
place keeps the installer, the executable's file properties and the app's own
"about" text from drifting apart -- which is otherwise very easy to get wrong
and confusing to support ("which build are you actually running?").

The build scripts read this file directly, so keep the assignment on one line
in a form a simple parser can handle.
"""

from __future__ import annotations

__version__ = "0.9.0"

APP_NAME = "PhotoFlow"
COMPANY_NAME = "Samiksha Technologies"
COMPANY_DOMAIN = "samikshatech.com"
COPYRIGHT = "Copyright (c) 2026 Samiksha Technologies"

# Support contact, defined once so switching mailboxes is a single edit rather
# than a hunt through the UI. Note it also appears in LICENSE and in the
# website's contact page, which are outside Python and must be changed there too.
SUPPORT_EMAIL = "hello@samikshatech.com"

# Where the application checks for a newer release. Served uncached (see
# website/_headers) so a new release is visible immediately.
UPDATE_MANIFEST_URL = f"https://{COMPANY_DOMAIN}/version.json"


def version_tuple() -> tuple[int, int, int, int]:
    """
    ``__version__`` as the 4-part tuple Windows version resources require.

    Windows wants ``(major, minor, patch, build)``; we always report build 0.
    Non-numeric suffixes (``"0.9.0-beta"``) are tolerated by taking the leading
    digits, so a pre-release tag can never break the build.
    """
    parts: list[int] = []
    for chunk in __version__.split(".")[:3]:
        digits = ""
        for char in chunk:
            if not char.isdigit():
                break
            digits += char
        parts.append(int(digits or 0))
    while len(parts) < 3:
        parts.append(0)
    return (parts[0], parts[1], parts[2], 0)
