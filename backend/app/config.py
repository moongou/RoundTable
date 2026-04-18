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
    },
    "volcengine": {
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-pro-128k",
    },
    "bailian": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-max",
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
    "doubao": "豆包",
    "volcengine": "火山引擎",
    "bailian": "阿里百炼",
}


# ── 语音服务默认配置 ────────────────────────────────────────────────────────

ASR_PROVIDERS = {
    "browser": "浏览器原生语音识别",
    "funasr": "FunASR 本地服务",
    "openai_whisper": "OpenAI Whisper API",
}

TTS_PROVIDERS = {
    "browser": "浏览器原生语音合成",
    "edge_tts": "OpenAI Edge TTS 本地服务",
    "cosyvoice": "CosyVoice 本地服务",
    "openai_tts": "OpenAI TTS API",
}

LOCAL_SERVICE_DEFAULTS = {
    "edge_tts": {"url": "http://localhost:5051", "health": "/v1/models"},
    "cosyvoice": {"url": "http://localhost:50000", "health": "/health"},
    "funasr": {"url": "http://localhost:10096", "health": "/"},
    "ollama": {"url": "http://localhost:11434", "health": "/api/tags"},
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

    # ── 语音服务 ────────────────────────────────────────────────────────────

    # ASR (语音识别)
    asr_provider: str = "browser"  # browser / funasr / openai_whisper
    asr_url: str = "http://localhost:10096"

    # TTS (语音合成)
    tts_provider: str = "browser"  # browser / edge_tts / cosyvoice / openai_tts
    tts_url: str = "http://localhost:5051"
    tts_voice: str = "alloy"

    # 交互方式
    push_to_talk: bool = True

    # ── 服务器 ─────────────────────────────────────────────────────────────

    host: str = "0.0.0.0"
    port: int = 8001
    debug: bool = True

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

        return config

    def validate_llm_config(self) -> tuple[bool, str]:
        """验证当前 LLM 配置是否有效。

        Returns:
            (is_valid, error_message) 元组。is_valid=True 表示配置有效。
        """
        provider = self.llm_provider
        if provider not in PROVIDER_DEFAULTS:
            return False, f"不支持的 LLM 提供商: {provider}"

        api_key = getattr(self, f"{provider}_api_key", "")

        # Ollama 本地不需要 API key
        if provider == "ollama":
            return True, ""

        # 检查 API key 是否为空或占位符
        if not api_key or api_key == "sk-xxx" or api_key.startswith("sk-xxx"):
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


settings = Settings()