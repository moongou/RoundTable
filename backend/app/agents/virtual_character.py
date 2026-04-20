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
        "5. 不要重复已有观点，应补充新角度或追问以推动讨论深入。\n"
        "6. '上一位'只能指紧邻你之前发言的那个人，不可跳过。\n"
        "7. 绝不使用'你说得对/他说得好'等开头，除非紧接着引用对方原话。"
    )    system_message += (
        "\n\n拟人化发言规范（非常重要）：\n"
        "- 像真实的小学生一样说话，带有口语化表达和自然停顿\n"
        "- 可以使用语气词（嗯、哦、哎、呀、呢）让发言更自然\n"
        "- 表达观点时偶尔可以犹豫或思考（'我想想……'、'等一下'）\n"
        "- 可以用括号标注表情动作如（挠头）（眨眼），但这些不会被读出来\n"
        "- 发言不要太完美太工整，允许有真实对话的随意感\n"
        "- 可以表达惊喜、疑惑、赞同等情感，让讨论有温度"
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
        "5. 不要重复已有观点，应补充新角度或追问以推动讨论深入。\n"
        "6. '上一位'只能指紧邻你之前发言的那个人，不可跳过。\n"
        "7. 绝不使用'你说得对/他说得好'等开头，除非紧接着引用对方原话。"
    )
    system_message += (
        "\n\n拟人化发言规范（非常重要）：\n"
        "- 用你这位思想家本人的语言风格说话，体现时代感和个人特色\n"
        "- 可以使用语气词和自然停顿，让发言像真实对话\n"
        "- 表达观点时允许有思考痕迹（'让我想想……'、'这个问题嘛'）\n"
        "- 可以用括号标注表情动作如（捻须）（微笑），但这些不会被读出来\n"
        "- 发言应有温度和情感，不要像在做学术报告\n"
        "- 偶尔可以用比喻或小故事来表达深刻观点"
    )

    return AssistantAgent(
        name=safe_agent_name(thinker_id),
        model_client=model_client,
        system_message=system_message,
        description=description,
        model_client_stream=True,
    )