"""虚拟角色 Agent 创建模块"""

from __future__ import annotations

from autogen_agentchat.agents import AssistantAgent
from autogen_core.models import ChatCompletionClient

from app.agents.character_templates import get_template


def create_virtual_character(
    template_id: str,
    model_client: ChatCompletionClient,
    topic: str,
) -> AssistantAgent:
    """从模板创建虚拟角色 Agent。

    Args:
        template_id: 角色模板 ID（explorer/skeptic/peacemaker/storyteller）。
        model_client: AutoGen 模型客户端。
        topic: 讨论主题。
    """
    template = get_template(template_id)
    system_message = template.system_message + f"\n\n讨论主题：{topic}"

    return AssistantAgent(
        name=template.name,
        model_client=model_client,
        system_message=system_message,
        description=template.description,
        model_client_stream=True,
    )