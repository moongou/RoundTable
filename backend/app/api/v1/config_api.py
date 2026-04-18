"""配置管理 API

提供 LLM 提供商列表、语音服务状态、本地服务健康检查等接口。
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from fastapi import APIRouter

from app.config import (
    ASR_PROVIDERS,
    LOCAL_SERVICE_DEFAULTS,
    PROVIDER_DEFAULTS,
    PROVIDER_NAMES,
    TTS_PROVIDERS,
    settings,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/config", tags=["config"])


@router.get("/providers")
async def list_providers():
    """列出所有可用的 LLM 提供商及其默认配置。"""
    providers = []
    for pid, defaults in PROVIDER_DEFAULTS.items():
        # 读取当前配置值
        api_key = getattr(settings, f"{pid}_api_key", "")
        base_url = getattr(settings, f"{pid}_base_url", defaults.get("base_url", ""))
        model = getattr(settings, f"{pid}_model", defaults.get("model", ""))

        # 检查 API key 是否已配置（非空且非占位符）
        has_key = bool(api_key and api_key != "sk-xxx" and not api_key.startswith("sk-xxx"))
        if pid == "ollama":
            has_key = True  # Ollama 本地不需要 key

        providers.append({
            "id": pid,
            "name": PROVIDER_NAMES.get(pid, pid),
            "base_url": base_url,
            "model": model,
            "has_api_key": has_key,
            "is_active": pid == settings.llm_provider,
            "needs_api_key": pid != "ollama",
        })

    return providers


@router.get("/speech")
async def list_speech_providers():
    """列出语音识别/合成服务提供商。"""
    return {
        "asr": [
            {"id": pid, "name": name, "is_active": pid == settings.asr_provider}
            for pid, name in ASR_PROVIDERS.items()
        ],
        "tts": [
            {"id": pid, "name": name, "is_active": pid == settings.tts_provider}
            for pid, name in TTS_PROVIDERS.items()
        ],
        "push_to_talk": settings.push_to_talk,
    }


@router.get("/health")
async def check_services_health():
    """检查本地服务是否可达。"""
    results = {}
    tasks = []

    async def check_service(name: str, url: str, health_path: str):
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{url}{health_path}")
                results[name] = {
                    "url": url,
                    "reachable": resp.status_code < 500,
                    "status_code": resp.status_code,
                }
        except Exception:
            results[name] = {"url": url, "reachable": False, "status_code": None}

    for name, svc in LOCAL_SERVICE_DEFAULTS.items():
        tasks.append(check_service(name, svc["url"], svc["health"]))

    await asyncio.gather(*tasks)
    return results


@router.get("/current")
async def get_current_config():
    """获取当前生效的配置（隐藏 API key 中间部分）。"""
    provider = settings.llm_provider

    def mask_key(key: str) -> str:
        if not key or key == "sk-xxx" or key.startswith("sk-xxx"):
            return ""
        if len(key) <= 8:
            return "***"
        return key[:4] + "..." + key[-4:]

    api_key = getattr(settings, f"{provider}_api_key", "")

    return {
        "llm_provider": provider,
        "llm_provider_name": PROVIDER_NAMES.get(provider, provider),
        "api_key_masked": mask_key(api_key),
        "model": getattr(settings, f"{provider}_model", ""),
        "asr_provider": settings.asr_provider,
        "tts_provider": settings.tts_provider,
        "push_to_talk": settings.push_to_talk,
    }


@router.post("/validate")
async def validate_current_config():
    """验证当前 LLM 配置是否有效。"""
    is_valid, error_msg = settings.validate_llm_config()
    return {"valid": is_valid, "message": error_msg if not is_valid else "配置有效"}