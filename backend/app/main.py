"""RoundTable FastAPI 应用入口"""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.config import settings

logger = logging.getLogger(__name__)

# Flutter Web 构建产物目录
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


async def _startup_preload():
    """后台预加载任务：硬件检测、LLM 连接池预热、TTS 常见短语缓存。"""
    from app.core.hardware import detect_hardware, log_hardware_summary

    # 1. 硬件检测（≤1s）
    log_hardware_summary()
    profile = detect_hardware()

    # 2 & 3: 并行执行 LLM 预热和 TTS 缓存（充分利用多核）
    async def _preheat_llm():
        try:
            cfg = settings.model_config_dict
            if cfg.get("api_key"):
                from app.core.llm_factory import create_model_client
                _client = create_model_client()
                logger.info(f"LLM 客户端预热完成: {cfg.get('model')}")
        except Exception as e:
            logger.warning(f"LLM 预热跳过: {e}")

    async def _preheat_tts():
        try:
            from app.voice.factory import create_tts_provider
            tts = create_tts_provider(settings.tts_provider)
            common_phrases = ["好的", "我认为", "但是", "你说得对", "让我想想",
                              "有道理", "我同意", "请继续", "这个问题", "从另一个角度"]

            async def _synth(phrase: str):
                try:
                    await tts.synthesize(phrase)
                except Exception:
                    pass

            await asyncio.gather(*[_synth(p) for p in common_phrases])
            logger.info("TTS 常见短语预缓存完成（并行，%d 条）", len(common_phrases))
        except Exception as e:
            logger.debug(f"TTS 预缓存跳过: {e}")

    await asyncio.gather(_preheat_llm(), _preheat_tts())

    logger.info("预加载任务全部完成")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # Startup
    print(f"RoundTable 启动中... LLM 提供商: {settings.llm_provider}")

    # 后台预加载（不阻塞启动）
    asyncio.create_task(_startup_preload())

    yield
    # Shutdown
    from app.core.hardware import _thread_pool
    if _thread_pool:
        _thread_pool.shutdown(wait=False)
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