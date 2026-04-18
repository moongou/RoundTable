"""RoundTable FastAPI 应用入口"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.config import settings

# Flutter Web 构建产物目录
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # Startup
    print(f"RoundTable 启动中... LLM 提供商: {settings.llm_provider}")
    yield
    # Shutdown
    print("RoundTable 关闭")


app = FastAPI(
    title="RoundTable",
    description="圆桌思辨讨论平台 - 为小学生提供AI引导的思辨训练场",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")

# 挂载 Flutter Web 静态文件（必须在路由之后挂载，否则会覆盖 API）
if STATIC_DIR.exists() and (STATIC_DIR / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
else:
    # 开发模式：静态文件未构建时，根路径返回提示
    @app.get("/")
    async def dev_root():
        return {"message": "RoundTable 后端运行中。请先构建前端: ./build_web.sh"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )