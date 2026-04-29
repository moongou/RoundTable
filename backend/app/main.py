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
    """后台预加载任务：按需硬件检测、LLM 连接池预热、TTS 常见短语缓存。"""
    if settings.hardware_detection_on_startup:
        from app.core.hardware import detect_hardware, get_thread_pool, log_hardware_summary

        log_hardware_summary()
        profile = detect_hardware()
        get_thread_pool()
        logger.info("线程池初始化完成: workers=%d", profile.recommended_workers)
    else:
        logger.info("已跳过启动期硬件检测；如需检测，请在设置页手动触发。")

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
            sem = asyncio.Semaphore(4)

            async def _synth(phrase: str):
                try:
                    async with sem:
                        await tts.synthesize(phrase)
                except Exception:
                    pass

            await asyncio.gather(*[_synth(p) for p in common_phrases])
            logger.info("TTS 常见短语预缓存完成（并行，%d 条）", len(common_phrases))
        except Exception as e:
            logger.debug(f"TTS 预缓存跳过: {e}")

    async def _preheat_asr():
        try:
            from app.voice.factory import create_asr_provider, create_tts_provider

            asr = create_asr_provider(settings.asr_provider)
            tts = create_tts_provider(settings.tts_provider)
            test_texts = ["你好", "请继续", "我有一个想法"]

            async def _one_round(text: str):
                audio = await tts.synthesize(text)
                await asr.transcribe(audio)

            await asyncio.gather(*[_one_round(t) for t in test_texts])
            logger.info("ASR 预热完成（%d 轮）", len(test_texts))
        except Exception as e:
            logger.debug(f"ASR 预热跳过: {e}")

    await asyncio.gather(_preheat_llm(), _preheat_tts(), _preheat_asr())

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
    version="1.0.2",
    lifespan=lifespan,
)

cors_origins = settings.cors_origins_list
if not cors_origins:
    cors_origins = ["http://localhost:8001", "http://127.0.0.1:8001"]
    logger.warning("CORS 白名单为空，已回退到本地默认来源。")

if not settings.management_auth_required:
    logger.warning("管理接口鉴权未启用：建议配置 MANAGEMENT_API_TOKEN。")

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[
        "X-Voice-Requested",
        "X-Voice-Used",
        "X-TTS-Provider",
        "X-TTS-Attempts",
        "X-TTS-Elapsed-Ms",
    ],
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