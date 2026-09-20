"""HTTP API routers."""

from fastapi import APIRouter

from app.api import (
    routes_analytics,
    routes_auth,
    routes_chat,
    routes_documents,
    routes_evaluation,
    routes_features,
    routes_health,
    routes_labs,
    routes_settings,
    routes_workspaces,
    routes_conversation_documents,
)

api_router = APIRouter(prefix="/api")

api_router.include_router(routes_health.router)
api_router.include_router(routes_auth.router)
api_router.include_router(routes_workspaces.router)
api_router.include_router(routes_documents.router)
api_router.include_router(routes_chat.router)
api_router.include_router(routes_conversation_documents.router)
api_router.include_router(routes_features.router)
api_router.include_router(routes_settings.router)
api_router.include_router(routes_analytics.router)
api_router.include_router(routes_evaluation.router)
api_router.include_router(routes_labs.router)

__all__ = ["api_router"]
