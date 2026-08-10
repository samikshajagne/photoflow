"""
Backend version constants.

Deliberately separate from ``utils/version.py``, which is the *desktop app's*
version. The two ship on different schedules -- the API is deployed weekly, the
installer when there is something worth downloading -- and coupling them would
force a pointless client release every time an endpoint changed.
"""

from __future__ import annotations

# The API contract version, which appears in the URL prefix.
API_VERSION = "1"

# The backend build. Bumped by hand; only ever used in logs and /health.
BACKEND_VERSION = "0.1.0"
