"""主持人 Agent 创建模块"""

from __future__ import annotations

from autogen_agentchat.agents import AssistantAgent
from autogen_core.models import ChatCompletionClient

MODERATOR_SYSTEM_PROMPT = """你是"李老师"，一场圆桌讨论的主持人。

你的职责：
1. 开场介绍讨论主题和规则
2. 点名让参与者发言，确保每人都有机会表达
3. 如果有人偏离主题，温和地引导回来
4. 适时总结各方观点，帮助大家理解不同立场
5. 鼓励同学之间互动和回应对方的观点
6. 结束时做简短总结，提炼关键思辨要点

你是面向小学生的老师，语言要简单易懂、亲切温和。
不要长篇大论，每次发言控制在2-3句话。
点名时说"请XXX发言"或"XXX，你怎么看？"
如果同学沉默不语，温和地鼓励："没关系，想到什么都可以说。"
如果讨论偏题，说："让我们回到主题，我们讨论的是……"
"""


def create_moderator(
    model_client: ChatCompletionClient,
    topic: str,
    participant_names: list[str],
) -> AssistantAgent:
    """创建主持人 Agent。

    Args:
        model_client: AutoGen 模型客户端（建议使用较强的模型）。
        topic: 讨论主题。
        participant_names: 所有参与者名字列表。
    """
    system_message = MODERATOR_SYSTEM_PROMPT
    system_message += f"\n\n讨论主题：{topic}"
    system_message += f"\n参与者：{', '.join(participant_names)}"

    return AssistantAgent(
        name="moderator",
        model_client=model_client,
        system_message=system_message,
        description="讨论主持人李老师，负责引导讨论流程、点名和总结观点",
        model_client_stream=True,
    )