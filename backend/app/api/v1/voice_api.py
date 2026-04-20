"""语音 API 端点

提供 TTS（文本转语音）和 ASR（语音转文本）的 HTTP 接口。
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import Response

from app.config import settings
from app.voice.factory import create_asr_provider, create_tts_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice", tags=["voice"])


# ── 请求/响应模型 ────────────────────────────────────────────────────────────

from pydantic import BaseModel


class TTSRequest(BaseModel):
    """TTS 请求"""

    text: str
    voice: str = "alloy"
    character_id: Optional[str] = None


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


# ── 角色音色映射 ──────────────────────────────────────────────────────────────

def _get_voice_for_character(character_id: str) -> str:
    """根据角色 ID 获取对应的 TTS 音色。

    如果角色有指定的音色，返回该音色；否则返回默认音色。
    """
    try:
        from app.agents.character_templates import load_all_templates
        templates = load_all_templates()
        if character_id in templates:
            voice = templates[character_id].get("voice", "")
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
        voice = _get_voice_for_character(request.character_id)

    try:
        provider = create_tts_provider()
        audio_data = await provider.synthesize(request.text, voice=voice)
        return Response(
            content=audio_data,
            media_type="audio/mpeg",
            headers={
                "Content-Disposition": "inline; filename=tts_output.mp3",
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

    try:
        provider = create_asr_provider()
        text = await provider.transcribe(audio_data, format=format)
        return ASRResponse(
            text=text,
            confidence=0.9,  # 本地服务无法提供准确置信度
            language="zh",
        )
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