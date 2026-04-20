"""虚拟角色 Agent 创建模块"""

from __future__ import annotations

from autogen_agentchat.agents import AssistantAgent
from autogen_core.models import ChatCompletionClient

from app.agents.character_templates import get_template
from app.agents.human_proxy import safe_agent_name
from app.core.thinkers import get_thinker


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
    system_message += (
        "\n\n首轮发言规范：如果这是你本场第一次发言，"
        "不要提及‘上一位同学’或引用他人观点，"
        "先独立给出你的第一反应与理由。"
    )
    system_message += (
        "\n\n引用与回应规范（必须严格遵守）：\n"
        "1. 只可以引用本场对话中**确实出现过**的发言内容，绝不编造或臆想他人观点。\n"
        "2. 引用时必须使用该发言者的**准确名字**，不可张冠李戴。\n"
        "3. 若不确定是谁说的，使用'有同学提到'而非随意点名。\n"
        "4. 回应用户（真人学生）的发言时，要引用其具体表述，体现认真倾听。\n"
        "5. 不要重复已有观点，应补充新角度或追问以推动讨论深入。"
    )

    return AssistantAgent(
        name=template.id,
        model_client=model_client,
        system_message=system_message,
        description=template.description,
        model_client_stream=True,
    )


def create_thinker_agent(
    thinker_id: str,
    model_client: ChatCompletionClient,
    topic: str,
) -> AssistantAgent:
    """从思想家数据创建 Agent。

    Args:
        thinker_id: 思想家 ID（weber/confucius 等）。
        model_client: AutoGen 模型客户端。
        topic: 讨论主题。
    """
    thinker = get_thinker(thinker_id)
    if not thinker:
        raise ValueError(f"思想家 '{thinker_id}' 不存在")

    name = thinker.get("name", thinker_id)
    system_message = thinker.get("system_message", f"你是{name}。")
    description = thinker.get("description", f"思想家{name}")

    system_message += f"\n\n讨论主题：{topic}"
    system_message += (
        "\n\n首轮发言规范：如果这是你本场第一次发言，"
        "不要提及‘上一位同学’或引用他人观点，"
        "先独立给出你的第一反应与理由。"
    )
    system_message += (
        "\n\n引用与回应规范（必须严格遵守）：\n"
        "1. 只可以引用本场对话中**确实出现过**的发言内容，绝不编造或臆想他人观点。\n"
        "2. 引用时必须使用该发言者的**准确名字**，不可张冠李戴。\n"
        "3. 若不确定是谁说的，使用'有同学提到'而非随意点名。\n"
        "4. 回应用户（真人学生）的发言时，要引用其具体表述，体现认真倾听。\n"
        "5. 不要重复已有观点，应补充新角度或追问以推动讨论深入。"
    )

    return AssistantAgent(
        name=safe_agent_name(thinker_id),
        model_client=model_client,
        system_message=system_message,
        description=description,
        model_client_stream=True,
    )