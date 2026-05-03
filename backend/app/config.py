"""RoundTable 应用配置

支持多种 LLM 提供商：OpenAI, 通义千问, DeepSeek, Ollama, 豆包, 火山引擎, 阿里百炼
支持语音识别/合成配置
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict

# ── LLM 提供商默认配置 ──────────────────────────────────────────────────────

PROVIDER_DEFAULTS = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
    },
    "siliconflow": {
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "deepseek-ai/DeepSeek-R1-0528-Qwen3-8B",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "model": "qwen2.5:7b",
    },
    "ollama_cloud": {
        "base_url": "https://api.ohmyllama.com/v1",
        "model": "qwen2.5:72b",
    },
    "doubao": {
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-pro-32k",
        "alias_of": "volcengine",  # 豆包=火山引擎，标记为别名
    },
    "volcengine": {
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-pro-128k",
    },
    "bailian": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-max",
    },
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-3-5-sonnet-20241022",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "model": "gemini-2.0-flash",
    },
    # 自定义提供商：用于其他 OpenAI 兼容 API（如月之暗面、自建 LLM 网关、第三方代理等）
    "custom1": {
        "base_url": "",
        "model": "",
    },
}

# 所有可用的提供商列表
AVAILABLE_PROVIDERS = list(PROVIDER_DEFAULTS.keys())


def canonical_provider_id(provider_id: str) -> str:
    normalized = (provider_id or "").strip().lower()
    alias_of = PROVIDER_DEFAULTS.get(normalized, {}).get("alias_of")
    return alias_of or normalized


def provider_candidate_ids(provider_id: str) -> list[str]:
    normalized = (provider_id or "").strip().lower()
    canonical = canonical_provider_id(normalized)
    candidates: list[str] = []
    for candidate in (normalized, canonical):
        if candidate and candidate in PROVIDER_DEFAULTS and candidate not in candidates:
            candidates.append(candidate)
    for pid, defaults in PROVIDER_DEFAULTS.items():
        if defaults.get("alias_of") == canonical and pid not in candidates:
            candidates.append(pid)
    return candidates


# 提供商中文名映射
PROVIDER_NAMES = {
    "openai": "OpenAI",
    "qwen": "通义千问",
    "deepseek": "DeepSeek",
    "siliconflow": "硅基流动",
    "ollama": "Ollama (本地)",
    "ollama_cloud": "Ollama (云端)",
    "doubao": "豆包（=火山引擎）",
    "volcengine": "火山引擎（豆包）",
    "bailian": "阿里百炼",
    "zhipu": "智谱AI (ChatGLM)",
    "anthropic": "Anthropic Claude",
    "gemini": "Google Gemini",
    "custom1": "自定义（OpenAI 兼容）",
}


# ── 语音服务默认配置 ────────────────────────────────────────────────────────

ASR_PROVIDERS = {
    "browser": "浏览器原生语音识别",
    "capswriter": "CapsWriter 本地服务（推荐，低延迟）",
    "vosk": "Vosk 本地服务（支持流式）",
    "funasr": "FunASR 本地服务",
    "openai_whisper": "OpenAI Whisper API",
    "siliconflow_asr": "硅基流动 ASR",
    "groq_whisper": "Groq Whisper API",
    "volcengine_asr": "火山引擎 ASR",
    "disabled": "禁用语音识别（纯文本输入）",
}

TTS_PROVIDERS = {
    "browser": "浏览器原生语音合成",
    "chattts": "ChatTTS 本地服务（对话风格，优先）",
    "edge_tts": "OpenAI Edge TTS 本地服务",
    "vibevoice": "VibeVoice 本地服务（微软，高品质）",
    "fireredtts": "FireRedTTS 本地服务（支持流式）",
    "openvoice": "OpenVoice 本地服务（支持变声）",
    "cosyvoice": "CosyVoice 本地服务",
    "openai_tts": "OpenAI TTS API",
    "siliconflow_tts": "硅基流动 TTS",
    "volcengine_tts": "火山引擎 TTS",
    "disabled": "禁用语音合成（纯文本显示）",
}

LOCAL_SERVICE_DEFAULTS = {
    "chattts": {"url": "http://localhost:9998", "health": "/gradio_api/info"},
    "edge_tts": {"url": "http://localhost:5051", "health": "/v1/models"},
    "cosyvoice": {"url": "http://localhost:50000", "health": "/health"},
    "funasr": {"url": "ws://localhost:10095", "health": "/"},
    "ollama": {"url": "http://localhost:11434", "health": "/api/tags"},
    "capswriter": {"url": "ws://localhost:6016", "health": "/"},
    "vosk": {"url": "http://localhost:6702", "health": "/health"},
    "vibevoice": {"url": "http://localhost:6704", "health": "/health"},
    "fireredtts": {"url": "http://localhost:6706", "health": "/health"},
    "openvoice": {"url": "http://localhost:6707", "health": "/health"},
}

# 语音服务提供商详细元数据
VOICE_SERVICE_META = {
    "capswriter": {
        "name": "CapsWriter 本地语音识别（推荐）",
        "default_url": "ws://localhost:6016",
        "type": "asr",
        "needs_api_key": False,
        "health_path": "/",
    },
    "vosk": {
        "name": "Vosk 本地语音识别（流式）",
        "default_url": "http://localhost:6702",
        "type": "asr",
        "needs_api_key": False,
        "health_path": "/health",
    },
    "funasr": {
        "name": "FunASR 本地语音识别",
        "default_url": "ws://localhost:10095",
        "type": "asr",
        "needs_api_key": False,
        "health_path": "/",
        # HTTP 模式：将 url 改为 http://localhost:10096（需先启动 FunASR HTTP server）
        # WS  模式：ws://localhost:10095（当前运行中）
    },
    "openai_whisper": {
        "name": "OpenAI Whisper API",
        "default_url": "https://api.openai.com/v1",
        "type": "asr",
        "mode": "cloud",
        "needs_api_key": True,
        "health_path": "/models",
        "default_model": "whisper-1",
    },
    "siliconflow_asr": {
        "name": "硅基流动 ASR",
        "default_url": "https://api.siliconflow.cn/v1",
        "type": "asr",
        "mode": "cloud",
        "needs_api_key": True,
        "health_path": "/models",
        "default_model": "TeleAI/TeleSpeechASR",
    },
    "groq_whisper": {
        "name": "Groq Whisper API",
        "default_url": "https://api.groq.com/openai/v1",
        "type": "asr",
        "mode": "cloud",
        "needs_api_key": True,
        "health_path": "/models",
        "default_model": "whisper-large-v3-turbo",
    },
    "edge_tts": {
        "name": "OpenAI Edge TTS 本地服务",
        "default_url": "http://localhost:5051",
        "type": "tts",
        "mode": "local",
        "needs_api_key": False,
        "health_path": "/v1/models",
    },
    "chattts": {
        "name": "ChatTTS 本地服务（对话风格）",
        "default_url": "http://localhost:9998",
        "type": "tts",
        "mode": "local",
        "needs_api_key": False,
        "health_path": "/gradio_api/info",
    },
    "vibevoice": {
        "name": "VibeVoice 微软高品质语音合成",
        "default_url": LOCAL_SERVICE_DEFAULTS["vibevoice"]["url"],
        "type": "tts",
        "mode": "local",
        "needs_api_key": False,
        "health_path": LOCAL_SERVICE_DEFAULTS["vibevoice"]["health"],
    },
    "fireredtts": {
        "name": "FireRedTTS 本地语音合成（流式）",
        "default_url": LOCAL_SERVICE_DEFAULTS["fireredtts"]["url"],
        "type": "tts",
        "mode": "local",
        "needs_api_key": False,
        "health_path": LOCAL_SERVICE_DEFAULTS["fireredtts"]["health"],
    },
    "openvoice": {
        "name": "OpenVoice 本地语音合成（变声）",
        "default_url": LOCAL_SERVICE_DEFAULTS["openvoice"]["url"],
        "type": "tts",
        "mode": "local",
        "needs_api_key": False,
        "health_path": LOCAL_SERVICE_DEFAULTS["openvoice"]["health"],
    },
    "cosyvoice": {
        "name": "CosyVoice 本地语音合成",
        "default_url": "http://localhost:50000",
        "type": "tts",
        "mode": "local",
        "needs_api_key": False,
        "health_path": "/health",
    },
    "openai_tts": {
        "name": "OpenAI TTS API",
        "default_url": "https://api.openai.com/v1",
        "type": "tts",
        "mode": "cloud",
        "needs_api_key": True,
        "health_path": "/models",
        "default_model": "tts-1",
        "default_voice": "alloy",
    },
    "siliconflow_tts": {
        "name": "硅基流动 TTS",
        "default_url": "https://api.siliconflow.cn/v1",
        "type": "tts",
        "mode": "cloud",
        "needs_api_key": True,
        "health_path": "/models",
        "default_model": "FunAudioLLM/CosyVoice2-0.5B",
        "default_voice": "FunAudioLLM/CosyVoice2-0.5B:alex",
    },
    "volcengine_asr": {
        "name": "火山引擎 ASR",
        "default_url": "https://openspeech.bytedance.com/api/v1/asr",
        "type": "asr",
        "mode": "cloud",
        "needs_api_key": True,
        "health_path": "/",
        "default_model": "bigmodel",
    },
    "volcengine_tts": {
        "name": "火山引擎 TTS",
        "default_url": "https://openspeech.bytedance.com/api/v1/tts",
        "type": "tts",
        "mode": "cloud",
        "needs_api_key": True,
        "health_path": "/",
        "default_model": "tts-1",
        "default_voice": "zh_female_qingxin",
    },
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── LLM 提供商 ─────────────────────────────────────────────────────────

    llm_provider: str = "deepseek"

    # OpenAI
    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_model: str = ""

    # 通义千问
    qwen_api_key: str = ""
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_model: str = "qwen-plus"

    # DeepSeek
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-chat"

    # 硅基流动
    siliconflow_api_key: str = ""
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    siliconflow_model: str = "deepseek-ai/DeepSeek-R1-0528-Qwen3-8B"

    # Ollama 本地
    ollama_api_key: str = "ollama"
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = "qwen2.5:7b"

    # Ollama 云端
    ollama_cloud_api_key: str = ""
    ollama_cloud_base_url: str = "https://api.ohmyllama.com/v1"
    ollama_cloud_model: str = "qwen2.5:72b"

    # 豆包
    doubao_api_key: str = ""
    doubao_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    doubao_model: str = "doubao-pro-32k"

    # 火山引擎
    volcengine_api_key: str = ""
    volcengine_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    volcengine_model: str = "doubao-pro-128k"

    # 阿里百炼
    bailian_api_key: str = ""
    bailian_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    bailian_model: str = "qwen-max"

    # 智谱AI (ChatGLM)
    zhipu_api_key: str = ""
    zhipu_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    zhipu_model: str = "glm-4-flash"

    # Anthropic Claude
    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.anthropic.com/v1"
    anthropic_model: str = "claude-3-5-sonnet-20241022"

    # Google Gemini
    gemini_api_key: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_model: str = "gemini-2.0-flash"

    # ── 自定义提供商（OpenAI 兼容服务） ─────────────────────────────────
    # 用于配置其他 OpenAI 兼容的 LLM 服务（如月之暗面、自建 LLM 网关等）。
    # 仅需填写 base_url、API Key 和模型名即可。
    custom1_api_key: str = ""
    custom1_base_url: str = ""
    custom1_model: str = ""
    custom1_display_name: str = "自定义一"

    custom_providers: str = "[]"  # JSON string of custom provider configs（保留兼容字段）

    # ── 语音服务 ────────────────────────────────────────────────────────────

    # ASR (语音识别)
    asr_provider: str = "funasr"  # browser / funasr / openai_whisper
    asr_url: str = "ws://localhost:10095"  # deprecated, use funasr_url
    capswriter_url: str = "ws://localhost:6016"
    vosk_url: str = "http://localhost:6702"
    funasr_url: str = "ws://localhost:10095"  # WS 模式；HTTP 模式改为 http://localhost:10096
    openai_whisper_api_key: str = ""  # uses openai_api_key if blank
    openai_whisper_base_url: str = "https://api.openai.com/v1"
    openai_whisper_model: str = "whisper-1"
    siliconflow_asr_api_key: str = ""  # uses siliconflow_api_key if blank
    siliconflow_asr_base_url: str = "https://api.siliconflow.cn/v1"
    siliconflow_asr_model: str = "FunAudioLLM/SenseVoiceSmall"
    groq_whisper_api_key: str = ""
    groq_whisper_base_url: str = "https://api.groq.com/openai/v1"
    groq_whisper_model: str = "whisper-large-v3-turbo"

    # TTS (语音合成)
    tts_provider: str = "edge_tts"  # browser / edge_tts / cosyvoice / local services
    tts_url: str = "http://localhost:5051"  # deprecated, use edge_tts_url
    chattts_url: str = "http://localhost:9998"
    edge_tts_url: str = "http://localhost:5051"
    cosyvoice_url: str = "http://localhost:50000"
    vibevoice_url: str = "http://localhost:6704"
    fireredtts_url: str = "http://localhost:6706"
    openvoice_url: str = "http://localhost:6707"
    openai_tts_api_key: str = ""  # uses openai_api_key if blank
    openai_tts_base_url: str = "https://api.openai.com/v1"
    openai_tts_model: str = "tts-1"
    openai_tts_voice: str = "alloy"
    siliconflow_tts_api_key: str = ""  # uses siliconflow_api_key if blank
    siliconflow_tts_base_url: str = "https://api.siliconflow.cn/v1"
    siliconflow_tts_model: str = "FunAudioLLM/CosyVoice2-0.5B"
    siliconflow_tts_voice: str = "FunAudioLLM/CosyVoice2-0.5B:alex"
    # 火山引擎 ASR/TTS
    volcengine_asr_access_key: str = ""
    volcengine_asr_secret_key: str = ""
    volcengine_asr_base_url: str = "https://openspeech.bytedance.com/api/v1/asr"
    volcengine_asr_model: str = "bigmodel"
    volcengine_tts_access_key: str = ""
    volcengine_tts_secret_key: str = ""
    volcengine_tts_base_url: str = "https://openspeech.bytedance.com/api/v1/tts"
    volcengine_tts_model: str = "tts-1"
    volcengine_tts_voice: str = "zh_female_qingxin"
    tts_voice: str = "zh-CN-XiaoxiaoNeural"
    cosyvoice_voice: str = "default"

    # 交互方式
    push_to_talk: bool = True

    # ── 网络搜索 ───────────────────────────────────────────────────────────

    tavily_api_key: str = ""
    tavily_base_url: str = "https://api.tavily.com"
    web_search_enabled: bool = False

    # ── 服务器 ─────────────────────────────────────────────────────────────

    host: str = "0.0.0.0"
    port: int = 8001
    debug: bool = True
    hardware_detection_on_startup: bool = False
    cors_allowed_origins: str = (
        "http://localhost:8001,"
        "http://127.0.0.1:8001,"
        "http://localhost:3000,"
        "http://127.0.0.1:3000,"
        "http://localhost:5173,"
        "http://127.0.0.1:5173"
    )
    management_api_token: str = "roundtable-admin-dev"
    management_auth_enforced: bool = False

    # ── 讨论参数 ───────────────────────────────────────────────────────────

    max_turns: int = 24
    human_turn_timeout: int = 15

    # ── 路径 ────────────────────────────────────────────────────────────────

    base_dir: Path = Path(__file__).parent.parent
    character_templates_dir: Path = Path(__file__).parent / "agents" / "character_templates"

    @property
    def model_config_dict(self) -> dict:
        """根据当前提供商返回模型配置字典，用于创建 AutoGen ChatCompletionClient。"""
        provider = canonical_provider_id(self.llm_provider)

        def _first_config_value(field_suffix: str) -> str:
            for pid in provider_candidate_ids(self.llm_provider):
                value = getattr(self, f"{pid}_{field_suffix}", "")
                if value:
                    return value
            return ""

        api_key = _first_config_value("api_key")
        base_url = _first_config_value("base_url")
        model = _first_config_value("model")

        if not model:
            defaults = PROVIDER_DEFAULTS.get(provider, {})
            model = defaults.get("model", "")

        config = {"model": model}

        # Ollama 本地不需要 API key
        if provider == "ollama" and (not api_key or api_key == "sk-xxx"):
            api_key = "ollama"

        if api_key and api_key != "sk-xxx":
            config["api_key"] = api_key
        elif provider != "ollama":
            config["api_key"] = ""

        if base_url:
            config["base_url"] = base_url

        # AutoGen 对非 OpenAI 模型名要求显式提供 model_info
        # 为避免兼容服务（DeepSeek/Qwen/百炼/自定义网关）出现
        # "model_info is required when model name is not a valid OpenAI model" 错误，
        # 仅当 provider 为 openai 且 base_url 指向真正的 api.openai.com、
        # 同时模型名又是 OpenAI 原生前缀时，才省略 model_info；其他情况一律显式注入。
        _openai_prefixes = ("gpt-", "o1-", "o3-", "o4-", "chatgpt-", "text-embedding-", "ft:")
        try:
            from urllib.parse import urlparse as _urlparse
            _host = (_urlparse(base_url or "https://api.openai.com").hostname or "").lower()
        except Exception:
            _host = ""
        is_real_openai_endpoint = provider == "openai" and _host.endswith("openai.com")
        is_native_openai_model = any(model.startswith(p) for p in _openai_prefixes)
        if not (is_real_openai_endpoint and is_native_openai_model):
            config["model_info"] = {
                "vision": False,
                "function_calling": True,
                "json_output": True,
                "family": "unknown",
                "structured_output": False,
                "multiple_system_messages": True,
            }

        return config

    def validate_llm_config(self) -> tuple[bool, str]:
        """验证当前 LLM 配置是否有效。

        Returns:
            (is_valid, error_message) 元组。is_valid=True 表示配置有效。
        """
        provider = canonical_provider_id(self.llm_provider)

        # Ollama 本地不需要 API key
        if provider == "ollama":
            return True, ""

        # 尝试从已知字段或通用字段读取 api_key
        api_key = ""
        for pid in provider_candidate_ids(self.llm_provider):
            api_key = getattr(self, f"{pid}_api_key", "")
            if api_key:
                break

        # 检查 API key 是否为空或占位符
        _placeholder = ("sk-xxx", "your-api-key", "api-key", "placeholder")
        if not api_key or api_key.lower() in _placeholder or api_key.startswith("sk-xxx"):
            provider_name = PROVIDER_NAMES.get(provider, provider)
            return False, f"{provider_name} 的 API Key 未配置，请在设置中填写有效的 API Key"

        return True, ""

    def get_runtime_config(self, runtime_override: dict | None = None) -> dict:
        """获取运行时配置（支持前端传入的覆盖）。

        Args:
            runtime_override: 前端通过 WebSocket 传入的配置覆盖。

        Returns:
            模型配置字典。
        """
        config = self.model_config_dict.copy()
        if runtime_override:
            config.update(runtime_override)
        return config

    def update_runtime(self, updates: dict) -> None:
        """运行时动态更新配置（不写入 .env 文件）。

        允许前端通过 API 更新 LLM 提供商、API Key、模型名等配置。

        Args:
            updates: 配置字段与新值的映射。
        """
        allowed_fields = {
            "llm_provider",
            "asr_provider",
            "tts_provider",
            "push_to_talk",
            "max_turns",
            "human_turn_timeout",
            "hardware_detection_on_startup",
            # Voice service URLs
            "chattts_url",
            "capswriter_url",
            "vosk_url",
            "funasr_url",
            "edge_tts_url",
            "cosyvoice_url",
            "vibevoice_url",
            "fireredtts_url",
            "openvoice_url",
            "openai_whisper_api_key",
            "openai_whisper_base_url",
            "openai_whisper_model",
            "siliconflow_asr_api_key",
            "siliconflow_asr_base_url",
            "siliconflow_asr_model",
            "groq_whisper_api_key",
            "groq_whisper_base_url",
            "groq_whisper_model",
            "openai_tts_api_key",
            "openai_tts_base_url",
            "openai_tts_model",
            "openai_tts_voice",
            "siliconflow_tts_api_key",
            "siliconflow_tts_base_url",
            "siliconflow_tts_model",
            "volcengine_asr_access_key",
            "volcengine_asr_secret_key",
            "volcengine_asr_base_url",
            "volcengine_asr_model",
            "volcengine_tts_access_key",
            "volcengine_tts_secret_key",
            "volcengine_tts_base_url",
            "volcengine_tts_model",
            "volcengine_tts_voice",
            "tts_voice",
            "cosyvoice_voice",
            # Web search
            "tavily_api_key",
            "tavily_base_url",
            "web_search_enabled",
        }
        # 动态允许所有提供商的 api_key / base_url / model 字段
        for pid in PROVIDER_DEFAULTS:
            allowed_fields.add(f"{pid}_api_key")
            allowed_fields.add(f"{pid}_base_url")
            allowed_fields.add(f"{pid}_model")
        # 自定义提供商额外允许设置显示名
        allowed_fields.add("custom1_display_name")

        for field, value in updates.items():
            if field == "llm_provider":
                value = canonical_provider_id(str(value))
            if field in allowed_fields and hasattr(self, field):
                object.__setattr__(self, field, value)

    def get_voice_service_url(self, service_id: str) -> str:
        """获取语音服务的 URL。"""
        sid = (service_id or "").strip().lower()
        url_map = {
            "chattts": self.chattts_url,
            "capswriter": self.capswriter_url,
            "vosk": self.vosk_url,
            "funasr": self.funasr_url,
            "edge_tts": self.edge_tts_url,
            "cosyvoice": self.cosyvoice_url,
            "vibevoice": self.vibevoice_url,
            "fireredtts": self.fireredtts_url,
            "openvoice": self.openvoice_url,
            "openai_whisper": self.openai_whisper_base_url,
            "siliconflow_asr": self.siliconflow_asr_base_url,
            "groq_whisper": self.groq_whisper_base_url,
            "openai_tts": self.openai_tts_base_url,
            "siliconflow_tts": self.siliconflow_tts_base_url,
            "volcengine_asr": self.volcengine_asr_base_url,
            "volcengine_tts": self.volcengine_tts_base_url,
        }
        url = url_map.get(sid, "")
        if url:
            return url
        return LOCAL_SERVICE_DEFAULTS.get(sid, {}).get("url", "") or VOICE_SERVICE_META.get(
            sid, {}
        ).get("default_url", "")

    def get_voice_service_api_key(self, service_id: str) -> str:
        """获取语音服务的 API Key。"""
        sid = (service_id or "").strip().lower()
        api_key_map = {
            "openai_whisper": self.openai_whisper_api_key or self.openai_api_key,
            "siliconflow_asr": self.siliconflow_asr_api_key or self.siliconflow_api_key,
            "groq_whisper": self.groq_whisper_api_key,
            "openai_tts": self.openai_tts_api_key or self.openai_api_key,
            "siliconflow_tts": self.siliconflow_tts_api_key or self.siliconflow_api_key,
            "volcengine_asr": self.volcengine_asr_access_key,
            "volcengine_tts": self.volcengine_tts_access_key,
        }
        return api_key_map.get(sid, "")

    def get_voice_service_model(self, service_id: str) -> str:
        """获取语音服务当前模型名。"""
        sid = (service_id or "").strip().lower()
        model_map = {
            "openai_whisper": self.openai_whisper_model,
            "siliconflow_asr": self.siliconflow_asr_model,
            "groq_whisper": self.groq_whisper_model,
            "openai_tts": self.openai_tts_model,
            "siliconflow_tts": self.siliconflow_tts_model,
            "volcengine_asr": self.volcengine_asr_model,
            "volcengine_tts": self.volcengine_tts_model,
        }
        return model_map.get(sid, VOICE_SERVICE_META.get(sid, {}).get("default_model", ""))

    def get_tts_voice_for_provider(self, provider_id: str | None = None) -> str:
        """获取指定 TTS provider 的默认音色。"""
        sid = (provider_id or self.tts_provider or "").strip().lower()
        voice_map = {
            "openai_tts": self.openai_tts_voice,
            "siliconflow_tts": self.siliconflow_tts_voice,
            "cosyvoice": self.cosyvoice_voice,
            "edge_tts": self.tts_voice,
            "volcengine_tts": self.volcengine_tts_voice,
        }
        return voice_map.get(
            sid, VOICE_SERVICE_META.get(sid, {}).get("default_voice", self.tts_voice)
        )

    @property
    def cors_origins_list(self) -> list[str]:
        """解析 CORS 白名单（支持 JSON 数组或逗号分隔）。"""
        raw = (self.cors_allowed_origins or "").strip()
        if not raw:
            return []

        origins: list[str]
        if raw.startswith("["):
            try:
                payload = json.loads(raw)
                if isinstance(payload, list):
                    origins = [str(item).strip() for item in payload if str(item).strip()]
                else:
                    origins = []
            except Exception:
                origins = []
        else:
            origins = [item.strip() for item in raw.split(",") if item.strip()]

        seen: set[str] = set()
        deduped: list[str] = []
        for origin in origins:
            parsed = urlparse(origin)
            if parsed.scheme not in {"http", "https"}:
                continue
            if not parsed.netloc:
                continue
            normalized = f"{parsed.scheme}://{parsed.netloc}"
            if normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped

    @property
    def management_auth_required(self) -> bool:
        """是否启用管理接口鉴权。"""
        return self.management_auth_enforced or bool((self.management_api_token or "").strip())


settings = Settings()
