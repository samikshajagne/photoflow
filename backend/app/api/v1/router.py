"""
The versioned API router.

Everything the desktop client or the admin dashboard calls hangs off
``/api/v1``.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import admin_licenses
from app.api.v1 import admin_users
from app.api.v1 import auth as v1_auth
from app.api.v1 import health as v1_health
from app.api.v1 import licenses as v1_licenses

api_router = APIRouter()

# Health
api_router.include_router(v1_health.router)

# Authentication
api_router.include_router(v1_auth.router, prefix="/auth")

# Administrative user endpoints
api_router.include_router(admin_users.router, prefix="/admin")

# Administrative license endpoints
api_router.include_router(admin_licenses.router, prefix="/admin")

# Customer licensing endpoints
api_router.include_router(v1_licenses.router)