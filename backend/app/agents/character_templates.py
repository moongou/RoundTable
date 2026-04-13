"""角色模板加载器

从 YAML 文件加载角色定义，提供创建 AutoGen Agent 的工厂函数。
"""

from __future__ import annotations

from pathlib import Path

import yaml
from autogen_agentchat.agents import AssistantAgent, UserProxyAgent
from autogen_core.models import ChatCompletionClient

from app.models.session import CharacterTemplate, ParticipantType

TEMPLATES_DIR = Path(__file__).parent / "character_templates"

# 缓存已加载的模板
_template_cache: dict[str, CharacterTemplate] | None = None


def load_all_templates() -> dict[str, CharacterTemplate]:
    """从 YAML 文件加载所有角色模板。"""
    global _template_cache
    if _template_cache is not None:
        return _template_cache

    templates: dict[str, CharacterTemplate] = {}
    for yaml_file in TEMPLATES_DIR.glob("*.yaml"):
        with open(yaml_file, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        template = CharacterTemplate(**data)
        templates[template.id] = template

    _template_cache = templates
    return templates


def get_template(template_id: str) -> CharacterTemplate:
    """获取指定 ID 的角色模板。"""
    templates = load_all_templates()
    if template_id not in templates:
        available = ", ".join(templates.keys())
        raise ValueError(f"角色模板 '{template_id}' 不存在。可用模板: {available}")
    return templates[template_id]


def create_moderator_agent(
    model_client: ChatCompletionClient,
    topic: str,
    participant_names: list[str],
) -> AssistantAgent:
    """创建主持人 Agent。

    Args:
        model_client: AutoGen 模型客户端。
        topic: 讨论主题。
        participant_names: 所有参与者名字列表。
    """
    template = get_template("moderator")
    # 在 system_message 中注入主题和参与者信息
    system_message = template.system_message
    system_message += f"\n\n讨论主题：{topic}"
    system_message += f"\n参与者：{', '.join(participant_names)}"

    return AssistantAgent(
        name=template.name,
        model_client=model_client,
        system_message=system_message,
        description=template.description,
        model_client_stream=True,
    )


def create_character_agent(
    template_id: str,
    model_client: ChatCompletionClient,
    topic: str,
) -> AssistantAgent:
    """创建虚拟角色 Agent。

    Args:
        template_id: 角色模板 ID（如 'explorer', 'skeptic'）。
        model_client: AutoGen 模型客户端。
        topic: 讨论主题。
    """
    template = get_template(template_id)
    system_message = template.system_message
    system_message += f"\n\n讨论主题：{topic}"

    return AssistantAgent(
        name=template.name,
        model_client=model_client,
        system_message=system_message,
        description=template.description,
        model_client_stream=True,
    )


def create_human_proxy(
    name: str,
    description: str = "",
    input_func=None,
) -> UserProxyAgent:
    """创建人类参与者 Agent。

    Args:
        name: 参与者名字。
        description: 参与者描述。
        input_func: 自定义输入函数，用于接收人类输入。
            签名: async (prompt: str, cancellation_token) -> str
    """
    if not description:
        description = f"学生{name}，真人参与者"

    return UserProxyAgent(
        name=name,
        description=description,
        input_func=input_func,
    )