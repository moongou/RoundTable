"""API v1 路由"""

from fastapi import APIRouter

from app.api.v1.characters import router as characters_router
from app.api.v1.sessions import router as sessions_router
from app.api.v1.topics import router as topics_router
from app.api.v1.websocket import router as websocket_router

api_router = APIRouter()

api_router.include_router(topics_router, tags=["topics"])
api_router.include_router(characters_router, tags=["characters"])
api_router.include_router(sessions_router, tags=["sessions"])
api_router.include_router(websocket_router, tags=["websocket"])