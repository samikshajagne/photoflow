"""
The versioned API router.

Everything the desktop client or the admin dashboard calls hangs off
``/api/v1``. Phase 3 mounts ``auth``; Phase 4+ mount ``licenses``, ``devices``,
``credits`` and ``updates``. They are listed here as comments rather than as
empty modules so the file describes the plan without shipping dead imports.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import health as v1_health

api_router = APIRouter()

api_router.include_router(v1_health.router)

# Phase 3:  api_router.include_router(auth.router,     prefix="/auth")
# Phase 4:  api_router.include_router(licenses.router, prefix="/licenses")
#           api_router.include_router(devices.router,  prefix="/devices")
#           api_router.include_router(credits.router,  prefix="/credits")
# Phase 5:  api_router.include_router(updates.router,  prefix="/updates")
# Admin endpoints mount under /admin with their own authorisation dependency.
