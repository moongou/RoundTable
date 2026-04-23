"""语音 API 端点

提供 TTS（文本转语音）和 ASR（语音转文本）的 HTTP 接口。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.config import settings
from app.voice.factory import create_asr_provider, create_tts_provider
from app.voice.openvoice_profiles import openvoice_profile_for_character_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice", tags=["voice"])


# ── 请求/响应模型 ────────────────────────────────────────────────────────────


class TTSRequest(BaseModel):
    """TTS 请求"""

    text: str
    provider: Optional[str] = None
    voice: str = "alloy"
    character_id: Optional[str] = None
    speed: float = Field(default=1.0, ge=0.8, le=1.1)


class TTSResponse(BaseModel):
    """TTS 响应元数据（音频数据直接作为二进制返回）"""

    content_type: str = "audio/mpeg"
    duration_seconds: Optional[float] = None


class ASRResponse(BaseModel):
    """ASR 响应"""

    text: str
    confidence: float = 0.0
    language: Optional[str] = None


class ASRRefineRequest(BaseModel):
    """ASR 文本纠错请求"""

    text: str


class ASRRefineResponse(BaseModel):
    """ASR 文本纠错响应"""

    text: str


def _detect_audio_content_type(audio_data: bytes) -> tuple[str, str]:
    if audio_data.startswith(b"RIFF") and audio_data[8:12] == b"WAVE":
        return "audio/wav", "wav"
    if audio_data.startswith(b"ID3") or (len(audio_data) >= 2 and audio_data[0] == 0xFF and (audio_data[1] & 0xE0) == 0xE0):
        return "audio/mpeg", "mp3"
    return "application/octet-stream", "bin"


# ── 角色音色映射 ──────────────────────────────────────────────────────────────

def _get_voice_for_character(character_id: str, provider_id: str | None = None) -> str:
    """根据角色 ID 获取对应的 TTS 音色。

    如果角色有指定的音色，返回该音色；否则返回默认音色。
    需求14：优先级 思想家 YAML > 角色模板 YAML > 默认。
    """
    if provider_id == "openvoice":
        profile_id = openvoice_profile_for_character_id(character_id)
        if profile_id:
            return profile_id

    try:
        from app.core.thinkers import get_thinker
        thinker = get_thinker(character_id)
        if thinker:
            if provider_id == "openvoice":
                return "ov:thinker_elder"
            voice = (thinker.get("voice") or "").strip()
            if voice:
                return voice
    except Exception:
        pass
    try:
        from app.agents.character_templates import load_all_templates
        templates = load_all_templates()
        if character_id in templates:
            if provider_id == "openvoice":
                profile_id = openvoice_profile_for_character_id(character_id)
                if profile_id:
                    return profile_id
            tpl = templates[character_id]
            voice = getattr(tpl, "voice", "") or ""
            if voice:
                return voice
    except Exception:
        pass
    return "alloy"


# ── TTS 端点 ──────────────────────────────────────────────────────────────────

@router.post("/tts")
async def text_to_speech(request: TTSRequest) -> Response:
    """将文本合成为语音音频。

    Args:
        request: 包含文本、音色和可选角色 ID 的请求。

    Returns:
        音频二进制数据（MP3 格式）。
    """
    # 确定音色：角色指定的 > 请求指定的 > 默认值
    voice = request.voice
    if request.character_id:
        voice = _get_voice_for_character(request.character_id, request.provider)

    try:
        provider = create_tts_provider(request.provider)
        last_error: Exception | None = None
        audio_data: bytes | None = None
        for attempt in range(2):
            try:
                audio_data = await provider.synthesize(
                    request.text,
                    voice=voice,
                    speed=request.speed,
                )
                break
            except Exception as e:
                last_error = e
                # 仅对瞬时错误做一次快速重试，避免把偶发上游 500 直接暴露给前端。
                if attempt == 0:
                    await asyncio.sleep(0.2)
                    continue
        if audio_data is None:
            raise last_error or RuntimeError("unknown tts error")
        media_type, suffix = _detect_audio_content_type(audio_data)
        return Response(
            content=audio_data,
            media_type=media_type,
            headers={
                "Content-Disposition": f"inline; filename=tts_output.{suffix}",
                "X-Voice-Used": voice,
            },
        )
    except Exception as e:
        logger.error(f"TTS 合成失败: {e}")
        raise HTTPException(status_code=500, detail=f"语音合成失败: {str(e)}")


# ── ASR 端点 ─────────────────────────────────────────────────────────────────

@router.post("/asr")
async def speech_to_text(
    audio: UploadFile = File(...),
    format: str = Form("wav"),
    provider: Optional[str] = Form(None),
) -> ASRResponse:
    """将语音音频转录为文本。

    Args:
        audio: 上传的音频文件。
        format: 音频格式（wav/mp3/webm 等）。

    Returns:
        包含转录文本和置信度的响应。
    """
    audio_data = await audio.read()
    if not audio_data:
        raise HTTPException(status_code=400, detail="音频数据为空")

    provider_id = (provider or "").strip() or None
    effective_provider = provider_id or settings.asr_provider

    try:
        provider_impl = create_asr_provider(provider_id)
        # 先检查服务是否可用，避免直接抛出 500
        if not await provider_impl.is_available():
            raise HTTPException(
                status_code=503,
                detail=f"语音识别服务 ({effective_provider}) 当前不可用，请检查服务是否已启动或在设置中切换其他服务。",
            )
        text = await provider_impl.transcribe(audio_data, format=format)
        return ASRResponse(
            text=text,
            confidence=0.9,  # 本地服务无法提供准确置信度
            language="zh",
        )
    except HTTPException:
        raise
    except RuntimeError as e:
        # 来自 ASR 提供商的业务错误（如服务未启动）
        logger.error(f"ASR 业务错误: {e}")
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"ASR 识别失败: {e}")
        raise HTTPException(status_code=500, detail=f"语音识别失败: {str(e)}")


def _refine_transcript_text(text: str) -> str:
    """轻量级转写后处理：去口语噪声、去重复、补标点。"""
    import re

    v = (text or "").strip()
    if not v:
        return ""

    v = re.sub(r"\s+", " ", v)
    v = re.sub(r"([，。！？；,.!?;])\1+", r"\1", v)
    v = re.sub(r"(嗯|呃|啊|那个|就是)(\s*\1)+", r"\1", v)
    v = re.sub(r"(我觉得){2,}", "我觉得", v)
    v = re.sub(r"(然后){2,}", "然后", v)

    parts = [p.strip() for p in re.split(r"[，。！？；,.!?;]+", v)]
    dedup: list[str] = []
    prev = ""
    for p in parts:
        if not p or p == prev:
            continue
        dedup.append(p)
        prev = p

    v = "，".join(dedup).strip()
    if not v:
        return ""
    if not re.search(r"[。！？!?]$", v):
        v += "。"
    return v


@router.post("/asr/refine")
async def refine_asr_text(request: ASRRefineRequest) -> ASRRefineResponse:
    """对 ASR 最终文本做后处理，提升句式可读性。"""
    refined = _refine_transcript_text(request.text)
    return ASRRefineResponse(text=refined)