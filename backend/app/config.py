"""RoundTable 应用配置

支持多种 LLM 提供商：OpenAI, 通义千问, DeepSeek, Ollama, 豆包, 火山引擎, 阿里百炼
支持语音识别/合成配置
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import Field
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
}

# 所有可用的提供商列表
AVAILABLE_PROVIDERS = list(PROVIDER_DEFAULTS.keys())

# 提供商中文名映射
PROVIDER_NAMES = {
    "openai": "OpenAI",
    "qwen": "通义千问",
    "deepseek": "DeepSeek",
    "ollama": "Ollama (本地)",
    "ollama_cloud": "Ollama (云端)",
    "doubao": "豆包（=火山引擎）",
    "volcengine": "火山引擎（豆包）",
    "bailian": "阿里百炼",
    "zhipu": "智谱AI (ChatGLM)",
    "anthropic": "Anthropic Claude",
    "gemini": "Google Gemini",
}


# ── 语音服务默认配置 ────────────────────────────────────────────────────────

ASR_PROVIDERS = {
    "browser": "浏览器原生语音识别",
    "capswriter": "CapsWriter 本地服务（推荐，低延迟）",
    "vosk": "Vosk 本地服务（支持流式）",
    "funasr": "FunASR 本地服务",
    "openai_whisper": "OpenAI Whisper API",
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
    "disabled": "禁用语音合成（纯文本显示）",
}

LOCAL_SERVICE_DEFAULTS = {
    "chattts": {"url": "http://localhost:9998", "health": "/gradio_api/info"},
    "edge_tts": {"url": "http://localhost:5051", "health": "/v1/models"},
    "cosyvoice": {"url": "http://localhost:50000", "health": "/health"},
    "funasr": {"url": "ws://localhost:10095", "health": "/"},
    "ollama": {"url": "http://localhost:11434", "health": "/api/tags"},
    "capswriter": {"url": "http://localhost:6701", "health": "/health"},
    "vosk": {"url": "http://localhost:6702", "health": "/health"},
    "vibevoice": {"url": "http://localhost:6704", "health": "/health"},
    "fireredtts": {"url": "http://localhost:6706", "health": "/health"},
    "openvoice": {"url": "http://localhost:6707", "health": "/health"},
    "gateway": {"url": "http://localhost:6666", "health": "/health"},
}

# 语音服务提供商详细元数据
VOICE_SERVICE_META = {
    "capswriter": {
        "name": "CapsWriter 本地语音识别（推荐）",
        "default_url": "http://localhost:6666",
        "type": "asr",
        "needs_api_key": False,
        "health_path": "/health/capswriter",
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
        # HTTP 模式：将 url 改为 http://localhost:8000（需先启动 FunASR HTTP server）
        # WS  模式：ws://localhost:10095（当前运行中）
    },
    "openai_whisper": {
        "name": "OpenAI Whisper API",
        "default_url": "https://api.openai.com/v1",
        "type": "asr",
        "needs_api_key": True,
        "health_path": "/models",
    },
    "edge_tts": {
        "name": "OpenAI Edge TTS 本地服务",
        "default_url": "http://localhost:5051",
        "type": "tts",
        "needs_api_key": False,
        "health_path": "/v1/models",
    },
    "chattts": {
        "name": "ChatTTS 本地服务（对话风格）",
        "default_url": "http://localhost:9998",
        "type": "tts",
        "needs_api_key": False,
        "health_path": "/gradio_api/info",
    },
    "vibevoice": {
        "name": "VibeVoice 微软高品质语音合成",
        "default_url": "http://localhost:6666",
        "type": "tts",
        "needs_api_key": False,
        "health_path": "/health/vibevoice",
    },
    "fireredtts": {
        "name": "FireRedTTS 本地语音合成（流式）",
        "default_url": "http://localhost:6666",
        "type": "tts",
        "needs_api_key": False,
        "health_path": "/health/fireredtts",
    },
    "openvoice": {
        "name": "OpenVoice 本地语音合成（变声）",
        "default_url": "http://localhost:6666",
        "type": "tts",
        "needs_api_key": False,
        "health_path": "/health/openvoice",
    },
    "cosyvoice": {
        "name": "CosyVoice 本地语音合成",
        "default_url": "http://localhost:50000",
        "type": "tts",
        "needs_api_key": False,
        "health_path": "/health",
    },
    "openai_tts": {
        "name": "OpenAI TTS API",
        "default_url": "https://api.openai.com/v1",
        "type": "tts",
        "needs_api_key": True,
        "health_path": "/models",
    },
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── LLM 提供商 ─────────────────────────────────────────────────────────

    llm_provider: str = "openai"

    # OpenAI
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"

    # 通义千问
    qwen_api_key: str = ""
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_model: str = "qwen-plus"

    # DeepSeek
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-chat"

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

    # ── 自定义提供商 ─────────────────────────────────────────────────────
    custom_providers: str = "[]"  # JSON string of custom provider configs

    # ── 语音服务 ────────────────────────────────────────────────────────────

    # ASR (语音识别)
    asr_provider: str = "funasr"  # browser / funasr / openai_whisper
    asr_url: str = "ws://localhost:10095"  # deprecated, use funasr_url
    funasr_url: str = "ws://localhost:10095"  # WS 模式（当前运行）；HTTP 模式改为 http://localhost:8000
    openai_whisper_api_key: str = ""  # uses openai_api_key if blank
    openai_whisper_base_url: str = "https://api.openai.com/v1"

    # TTS (语音合成)
    tts_provider: str = "edge_tts"  # browser / edge_tts / cosyvoice / openai_tts
    tts_url: str = "http://localhost:5051"  # deprecated, use edge_tts_url
    chattts_url: str = "http://localhost:9998"
    edge_tts_url: str = "http://localhost:5051"
    cosyvoice_url: str = "http://localhost:50000"
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

    # ── 讨论参数 ───────────────────────────────────────────────────────────

    max_turns: int = 30
    human_turn_timeout: int = 15

    # ── 路径 ────────────────────────────────────────────────────────────────

    base_dir: Path = Path(__file__).parent.parent
    character_templates_dir: Path = Path(__file__).parent / "agents" / "character_templates"

    @property
    def model_config_dict(self) -> dict:
        """根据当前提供商返回模型配置字典，用于创建 AutoGen ChatCompletionClient。"""
        provider = self.llm_provider
        api_key = getattr(self, f"{provider}_api_key", "")
        base_url = getattr(self, f"{provider}_base_url", "")
        model = getattr(self, f"{provider}_model", "")

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
        # （OpenAI 原生模型 gpt-*/o1-*/o3-* 由 autogen 自动识别，无需传入）
        _openai_prefixes = ("gpt-", "o1-", "o3-", "o4-", "chatgpt-", "text-embedding-", "ft:")
        if provider != "openai" or not any(model.startswith(p) for p in _openai_prefixes):
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
        provider = self.llm_provider

        # Ollama 本地不需要 API key
        if provider == "ollama":
            return True, ""

        # 尝试从已知字段或通用字段读取 api_key
        api_key = getattr(self, f"{provider}_api_key", None)
        if api_key is None:
            # 自定义提供商：只要 provider 字段非空且非占位符就认为有效
            api_key = ""

        # 检查 API key 是否为空或占位符
        _placeholder = ("sk-xxx", "your-api-key", "api-key", "placeholder")
        if not api_key or api_key.lower() in _placeholder or api_key.startswith("sk-xxx"):
            provider_name = PROVIDER_NAMES.get(provider, provider)
            return False, f"{provider_name} 的 API Key 未配置，请在设置中填写有效的 API Key"

        return True, ""

    def get_runtime_config(self, runtime_override: Optional[dict] = None) -> dict:
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
            "llm_provider", "asr_provider", "tts_provider", "push_to_talk",
            "max_turns", "human_turn_timeout",
            "hardware_detection_on_startup",
            # Voice service URLs
            "funasr_url", "edge_tts_url", "cosyvoice_url",
            "openai_whisper_api_key", "openai_whisper_base_url",
            "tts_voice", "cosyvoice_voice",
            # Web search
            "tavily_api_key", "tavily_base_url", "web_search_enabled",
        }
        # 动态允许所有提供商的 api_key / base_url / model 字段
        for pid in PROVIDER_DEFAULTS:
            allowed_fields.add(f"{pid}_api_key")
            allowed_fields.add(f"{pid}_base_url")
            allowed_fields.add(f"{pid}_model")

        for field, value in updates.items():
            if field in allowed_fields and hasattr(self, field):
                object.__setattr__(self, field, value)

    def get_voice_service_url(self, service_id: str) -> str:
        """获取语音服务的 URL。"""
        sid = (service_id or "").strip().lower()
        url_map = {
            "chattts": self.chattts_url,
            "funasr": self.funasr_url,
            "edge_tts": self.edge_tts_url,
            "cosyvoice": self.cosyvoice_url,
            "openai_whisper": self.openai_whisper_base_url,
            "openai_tts": self.openai_base_url,
        }
        url = url_map.get(sid, "")
        if url:
            return url
        return (
            LOCAL_SERVICE_DEFAULTS.get(sid, {}).get("url", "")
            or VOICE_SERVICE_META.get(sid, {}).get("default_url", "")
        )


settings = Settings()