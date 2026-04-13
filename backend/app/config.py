"""RoundTable 应用配置"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM 提供商: openai / qwen / deepseek
    llm_provider: str = "openai"

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # 通义千问
    qwen_api_key: str = ""
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_model: str = "qwen-plus"

    # DeepSeek
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-chat"

    # 服务器
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = True

    # 讨论参数
    max_turns: int = 30
    human_turn_timeout: int = 15

    # 路径
    base_dir: Path = Path(__file__).parent.parent
    character_templates_dir: Path = Path(__file__).parent / "agents" / "character_templates"

    @property
    def model_config_dict(self) -> dict:
        """根据当前提供商返回模型配置字典，用于创建 AutoGen ChatCompletionClient。"""
        if self.llm_provider == "openai":
            return {
                "model": self.openai_model,
                "api_key": self.openai_api_key,
            }
        elif self.llm_provider == "qwen":
            return {
                "model": self.qwen_model,
                "api_key": self.qwen_api_key,
                "base_url": self.qwen_base_url,
            }
        elif self.llm_provider == "deepseek":
            return {
                "model": self.deepseek_model,
                "api_key": self.deepseek_api_key,
                "base_url": self.deepseek_base_url,
            }
        else:
            raise ValueError(f"不支持的 LLM 提供商: {self.llm_provider}")


settings = Settings()