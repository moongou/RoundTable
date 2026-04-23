"""语音服务工厂

根据配置创建对应的 TTS/ASR 提供商实例。
"""

from __future__ import annotations

import logging

from app.config import LOCAL_SERVICE_DEFAULTS, settings
from app.voice.base import ASRProvider, TTSProvider
from app.voice.chattts import ChatTTSProvider
from app.voice.cosyvoice import CosyVoiceProvider
from app.voice.edge_tts import EdgeTTSProvider
from app.voice.gateway import GatewayASRProvider, GatewayTTSProvider
from app.voice.openai_tts import OpenAITTSProvider
from app.voice.openai_whisper import OpenAIWhisperProvider

logger = logging.getLogger(__name__)


def create_tts_provider(provider_id: str | None = None) -> TTSProvider:
    """创建 TTS 提供商实例。

    Args:
        provider_id: 提供商 ID，为 None 时使用 settings.tts_provider。

    Returns:
        TTSProvider 实例。
    """
    pid = provider_id or settings.tts_provider

    if pid == "chattts":
        return ChatTTSProvider(
            base_url=settings.chattts_url or "http://localhost:9998",
        )
    elif pid == "edge_tts":
        return EdgeTTSProvider(
            base_url=settings.edge_tts_url or settings.tts_url or "http://localhost:5051",
        )
    elif pid == "cosyvoice":
        url = settings.cosyvoice_url or "http://localhost:50000"
        return CosyVoiceProvider(base_url=url)
    elif pid == "openai_tts":
        return OpenAITTSProvider(
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
        )
    elif pid in ("vibevoice", "fireredtts", "openvoice"):
        return GatewayTTSProvider(service=pid)
    elif pid == "browser":
        # 浏览器原生 TTS 由前端处理，后端不需要创建提供商
        # 返回 Edge TTS 作为默认后端 TTS
        logger.info("浏览器 TTS 由前端处理，后端默认使用 Edge TTS")
        return EdgeTTSProvider()
    else:
        logger.warning(f"未知的 TTS 提供商: {pid}，回退到 Edge TTS")
        return EdgeTTSProvider()


def create_tts_provider_with_url(provider_id: str, base_url: str = "") -> TTSProvider:
    """创建 TTS 提供商实例，使用指定的 base_url（独立测试用）。

    Args:
        provider_id: 提供商 ID。
        base_url: 服务地址，优先于 settings 配置。
    """
    from app.config import LOCAL_SERVICE_DEFAULTS

    url = base_url or LOCAL_SERVICE_DEFAULTS.get(provider_id, {}).get("url", "")

    if provider_id == "chattts":
        return ChatTTSProvider(base_url=url or "http://localhost:9998")
    elif provider_id == "edge_tts":
        return EdgeTTSProvider(base_url=url or "http://localhost:5051")
    elif provider_id == "cosyvoice":
        return CosyVoiceProvider(base_url=url or "http://localhost:50000")
    elif provider_id == "openai_tts":
        return OpenAITTSProvider(
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
        )
    elif provider_id in ("vibevoice", "fireredtts", "openvoice"):
        return GatewayTTSProvider(service=provider_id)
    else:
        logger.warning(f"未知的 TTS 提供商: {provider_id}，回退到 Edge TTS")
        return EdgeTTSProvider(base_url=url or "http://localhost:5051")


def create_asr_provider(provider_id: str | None = None) -> ASRProvider:
    """创建 ASR 提供商实例。

    Args:
        provider_id: 提供商 ID，为 None 时使用 settings.asr_provider。

    Returns:
        ASRProvider 实例。
    """
    pid = provider_id or settings.asr_provider

    if pid == "funasr":
        from app.voice.funasr import FunASRProvider
        # 优先使用 settings.funasr_url，其次是 settings.asr_url，
        # 最后回退到 LOCAL_SERVICE_DEFAULTS 中的默认值（HTTP 模式：8000）
        default_url = LOCAL_SERVICE_DEFAULTS.get("funasr", {}).get("url", "http://localhost:8000")
        # 兼容旧配置：若默认仍是 WS 端口但用户未启动 WS 服务，尝试 HTTP 回退
        configured_url = settings.funasr_url or settings.asr_url or default_url
        return FunASRProvider(base_url=configured_url)
    elif pid == "openai_whisper":
        return OpenAIWhisperProvider(
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
        )
    elif pid in ("capswriter", "vosk"):
        return GatewayASRProvider(service=pid)
    elif pid == "browser":
        # 浏览器原生 ASR 由前端处理，但若前端通过 ServerAsrService 将音频发到后端
        # （如用户在前端设置中选择了 funasr/capswriter 等），则根据配置的后端 ASR URL 自动选择。
        configured_url = (
            settings.asr_url
            or settings.funasr_url
            or LOCAL_SERVICE_DEFAULTS.get("funasr", {}).get("url", "")
        )
        if configured_url:
            from app.voice.funasr import FunASRProvider
            logger.info(f"浏览器 ASR 由前端处理，后端 ASR 代理使用 {configured_url}")
            return FunASRProvider(base_url=configured_url)
        logger.info("浏览器 ASR 由前端处理，无后端 ASR 配置可用")
        from app.voice.funasr import FunASRProvider
        return FunASRProvider()
    else:
        logger.warning(f"未知的 ASR 提供商: {pid}，回退到 FunASR")
        from app.voice.funasr import FunASRProvider
        return FunASRProvider()