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

# The umbrella company. PhotoFlow is one product by SA Innovations, so the
# product keeps its own name everywhere the user sees it and this is only used
# for company-level branding: the About box, the Windows version resource, the
# installer's publisher field.
COMPANY_NAME = "SA Innovations"
COPYRIGHT = "Copyright (c) 2026 SA Innovations"

# The live company website. There is no custom domain yet, so this is the
# Render URL the site is actually served from rather than an aspirational one.
# TODO (SA Innovations domain): replace with the real domain once registered.
COMPANY_WEBSITE = "https://sa-innovations.onrender.com"

# ---------------------------------------------------------------------------
# Legacy identifiers -- deliberately NOT renamed with the company.
#
# These two values are load-bearing rather than cosmetic, and changing them
# silently breaks existing installs:
#
#   LEGACY_DATA_DIR_NAME is the folder under %LOCALAPPDATA% that holds
#   license.json (the activation record), collage_presets.json (the studio's
#   saved house styles), usage counters and the downloaded model cache. Rename
#   it and every existing user looks unlicensed and loses their presets, with
#   no error message to explain why. See utils/paths.py::user_data_dir.
#
#   SUPPORT_EMAIL is the mailbox that is actually monitored today. Pointing it
#   at a domain that has not been registered would send support mail nowhere.
#
# TODO (SA Innovations domain): when an SA Innovations domain and mailbox
# exist, change SUPPORT_EMAIL here, in LICENSE, and on the website's contact
# page. LEGACY_DATA_DIR_NAME must only change alongside migration code that
# moves the old directory across first.
# ---------------------------------------------------------------------------
LEGACY_DATA_DIR_NAME = "Samiksha Technologies"
SUPPORT_EMAIL = "hello@samikshatech.com"

# Shown in the licence dialog as "visit <this>", so it must be somewhere a
# customer can actually reach.
COMPANY_DOMAIN = "sa-innovations.onrender.com"

# Where the application checks for a newer release. Served uncached (see
# website/_headers) so a new release is visible immediately. This points at
# the live site, which is where version.json is deployed.
UPDATE_MANIFEST_URL = f"{COMPANY_WEBSITE}/version.json"


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
