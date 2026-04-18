"""可插拔 LLM 模型客户端工厂

支持 OpenAI、通义千问(Qwen)、DeepSeek、Ollama、豆包、火山引擎、阿里百炼等提供商，
所有提供商均使用 OpenAI 兼容接口，通过 base_url 切换。
"""

from __future__ import annotations

from autogen_ext.models.openai import OpenAIChatCompletionClient

from app.config import settings


def create_model_client(
    model_config_override: dict | None = None,
) -> OpenAIChatCompletionClient:
    """创建 AutoGen 模型客户端。

    Args:
        model_config_override: 覆盖默认模型配置。用于不同角色使用不同模型。
            例如: {"model": "gpt-4o"} 让主持人使用更强的模型。

    Returns:
        配置好的 OpenAIChatCompletionClient 实例。
    """
    config = settings.model_config_dict.copy()
    if model_config_override:
        config.update(model_config_override)
    return OpenAIChatCompletionClient(**config)


def create_model_client_from_config(config: dict) -> OpenAIChatCompletionClient:
    """从前端传入的配置创建模型客户端（每个会话可以使用不同配置）。

    Args:
        config: 包含 model, api_key, base_url 的字典。

    Returns:
        配置好的 OpenAIChatCompletionClient 实例。
    """
    return OpenAIChatCompletionClient(**config)


def create_moderator_client() -> OpenAIChatCompletionClient:
    """为主持人创建模型客户端。

    主持人需要更强的推理能力来引导讨论和选择发言者，
    因此使用更强大的模型。
    """
    return create_model_client()


def create_character_client() -> OpenAIChatCompletionClient:
    """为虚拟角色创建模型客户端。

    角色回应可以更快更便宜，但需要中文能力好。
    """
    return create_model_client()