from fastapi import APIRouter, Depends
from app.api.routes import data, analytics, ai, voice, settings, auth, sessions, users
from app.core.dependencies import get_current_user, get_current_admin

api_router = APIRouter()
api_router.include_router(auth.router)

# Admin only routes
api_router.include_router(users.router, dependencies=[Depends(get_current_admin)])

# User or Admin routes
api_router.include_router(settings.router, dependencies=[Depends(get_current_user)])
api_router.include_router(data.router, dependencies=[Depends(get_current_user)])
api_router.include_router(analytics.router, dependencies=[Depends(get_current_user)])
api_router.include_router(ai.router, dependencies=[Depends(get_current_user)])
api_router.include_router(sessions.router, dependencies=[Depends(get_current_user)])

# Voice routes - synthesize_stream has its own token validation
api_router.include_router(voice.router)
