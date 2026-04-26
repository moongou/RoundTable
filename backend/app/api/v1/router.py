"""API v1 路由"""

from fastapi import APIRouter

from app.api.v1.benchmark_api import router as benchmark_router
from app.api.v1.characters import router as characters_router
from app.api.v1.config_api import router as config_router
from app.api.v1.history_api import router as history_router
from app.api.v1.sessions import router as sessions_router
from app.api.v1.thinkers_api import router as thinkers_router
from app.api.v1.topics import router as topics_router
from app.api.v1.voice_api import router as voice_router
from app.api.v1.websocket import router as websocket_router

api_router = APIRouter()

api_router.include_router(topics_router, tags=["topics"])
api_router.include_router(characters_router, tags=["characters"])
api_router.include_router(sessions_router, tags=["sessions"])
api_router.include_router(config_router, tags=["config"])
api_router.include_router(history_router, tags=["history"])
api_router.include_router(thinkers_router, tags=["thinkers"])
api_router.include_router(voice_router, tags=["voice"])
api_router.include_router(benchmark_router, tags=["benchmark"])
api_router.include_router(websocket_router, tags=["websocket"])