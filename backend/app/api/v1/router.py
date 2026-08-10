"""
The versioned API router.

Everything the desktop client or the admin dashboard calls hangs off
``/api/v1``. Phase 3 mounts ``auth``; Phase 4+ mount ``licenses``, ``devices``,
``credits`` and ``updates``. They are listed here as comments rather than as
empty modules so the file describes the plan without shipping dead imports.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import admin_users
from app.api.v1 import auth as v1_auth
from app.api.v1 import health as v1_health

api_router = APIRouter()

api_router.include_router(v1_health.router)
api_router.include_router(v1_auth.router, prefix="/auth")

# Administrative endpoints. Authorisation is declared on each route via
# AdminUser rather than on the include, so that reading any single endpoint
# shows what it requires -- a router-level dependency is easy to remove by
# accident and hard to notice missing.
api_router.include_router(admin_users.router, prefix="/admin")

# Phase 4:  api_router.include_router(licenses.router, prefix="/licenses")
#           api_router.include_router(devices.router,  prefix="/devices")
#           api_router.include_router(credits.router,  prefix="/credits")
# Phase 5:  api_router.include_router(updates.router,  prefix="/updates")
