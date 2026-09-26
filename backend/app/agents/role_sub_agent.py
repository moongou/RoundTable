"""角色子 Agent（Role Sub-Agent）模块

解决多角色讨论中"角色混乱 / 张冠李戴"问题的核心架构：

传统 SelectorGroupChat 中，所有角色的发言会以**匿名 user 消息**的形式
拼接进每个角色的模型上下文——模型无法可靠区分"谁说了什么"，只能靠
脑补，这是角色混乱的根源。

本模块将每个角色实现为一个**完全隔离的子 agent**：

1. 私有结构化台账（ledger）：子 agent 收到群聊消息后，不再把原始消息
   塞进自身模型上下文，而是登记到私有台账，每条记录都带明确归属
   （发言者身份 + 名字 + 原文）。
2. 隔离生成上下文：每次轮到该角色发言时，用"人格 system 消息 + 结构化
   台账 + 轮次指令"构建一次性的全新上下文调用 LLM——上下文中不存在
   任何匿名他人发言。
3. 流式保留：复用 AssistantAgent 的 `_call_llm` 通道，`ModelClientStreamingChunkEvent`
   照常产出，前端增量渲染与流式 TTS 不受影响。
4. 独立模型客户端：每个子 agent 持有自己的 client 实例（连接/请求完全
   隔离，也便于后续按角色配置不同模型）。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Optional, Sequence

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.base import Response
from autogen_agentchat.messages import (
    BaseAgentEvent,
    BaseChatMessage,
    ModelClientStreamingChunkEvent,
    TextMessage,
)
from autogen_core import CancellationToken
from autogen_core.model_context import UnboundedChatCompletionContext
from autogen_core.models import CreateResult, SystemMessage, UserMessage

logger = logging.getLogger(__name__)

# 角色类型常量
ROLE_MODERATOR = "moderator"
ROLE_STUDENT = "student"
ROLE_THINKER = "thinker"
ROLE_HUMAN = "human"

_ROLE_LABELS = {
    ROLE_MODERATOR: "老师（主持人）",
    ROLE_STUDENT: "同学",
    ROLE_THINKER: "思想家",
    ROLE_HUMAN: "真人同学",
}


class RoleDirectory:
    """全场共享的角色注册表：内部 agent 名 → (展示名, 角色类型)。

    所有子 agent 共享同一个注册表，用于把 thread 消息的 source
    精确翻译为"[身份] 中文名"，从结构上杜绝归属歧义。
    """

    def __init__(self) -> None:
        self._entries: dict[str, tuple[str, str]] = {}

    def register(self, agent_name: str, display_name: str, role_type: str) -> None:
        if not agent_name:
            return
        self._entries[agent_name] = ((display_name or agent_name).strip() or agent_name, role_type)

    def display_name(self, agent_name: str) -> str:
        entry = self._entries.get(agent_name)
        return entry[0] if entry else agent_name

    def role_type(self, agent_name: str) -> Optional[str]:
        entry = self._entries.get(agent_name)
        return entry[1] if entry else None

    def role_label(self, agent_name: str) -> str:
        role_type = self.role_type(agent_name)
        if role_type is None:
            return "系统"
        return _ROLE_LABELS.get(role_type, role_type)

    def known(self, agent_name: str) -> bool:
        return agent_name in self._entries

    def roster_lines(self, self_name: Optional[str] = None) -> list[str]:
        """渲染全场参与者名单（可标注"你"）。"""
        lines: list[str] = []
        for agent_name, (display, role_type) in self._entries.items():
            label = _ROLE_LABELS.get(role_type, role_type)
            mark = "（就是你）" if agent_name == self_name else ""
            lines.append(f"- [{label}] {display}{mark}")
        return lines


@dataclass
class _LedgerEntry:
    source: str  # 内部 agent 名
    text: str
    is_self: bool = False
    is_own_reply: bool = False
    seq: int = 0


SUB_AGENT_SYSTEM_APPENDIX = """

【子 Agent 身份协议（最高优先级，覆盖一切其他指令）】
你是一个独立子 Agent，只代表上面对应的这一个角色参与圆桌讨论。
- 你只以该角色身份发言，使用第一人称"我"；
- 你绝不能扮演、模仿或替其他任何角色（老师、其他同学、思想家、真人）发言；
- 你收到的"发言台账"中每一条都标注了真实发言人，格式为 [身份] 名字：内容；
- 引用或回应任何人之前，必须先在台账中核对那句话确实出自该人名下；
- 若台账中查不到某句话的出处，就使用"有同学提到"等中性表述，绝不张冠李戴；
- 台账中标注"（就是你）"的是你自己的历史发言，引用时用"我刚才说"。
"""


class RoleSubAgent(AssistantAgent):
    """每个角色一个独立子 Agent：私有台账 + 隔离生成上下文 + 保留流式。"""

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("model_client_stream", True)
        super().__init__(*args, **kwargs)
        self._role_display_name: str = self.name
        self._role_type: str = ROLE_STUDENT
        self._directory: Optional[RoleDirectory] = None
        self._ledger: list[_LedgerEntry] = []
        self._seen_msg_keys: set = set()
        self._ledger_seq: int = 0
        self._own_last_reply: str = ""
        # 控制台账规模，保证长讨论依然流畅（保留最近的发言原文）
        self._ledger_char_limit: int = 16000
        self._ledger_max_entries: int = 80

    # ------------------------------------------------------------------ #
    # 角色绑定
    # ------------------------------------------------------------------ #

    def bind_role(
        self,
        display_name: str,
        role_type: str,
        directory: Optional[RoleDirectory] = None,
    ) -> "RoleSubAgent":
        """绑定角色身份：展示名、角色类型、共享角色注册表。"""
        self._role_display_name = (display_name or self.name).strip() or self.name
        self._role_type = role_type
        self._directory = directory
        if directory is not None:
            directory.register(self.name, self._role_display_name, role_type)
        return self

    # ------------------------------------------------------------------ #
    # 台账管理
    # ------------------------------------------------------------------ #

    def _next_seq(self) -> int:
        self._ledger_seq += 1
        return self._ledger_seq

    def _extract_text(self, message: BaseChatMessage | BaseAgentEvent) -> str:
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content.strip()
        if content is None:
            return ""
        return str(content).strip()

    def _ingest(self, messages: Sequence[BaseChatMessage | BaseAgentEvent]) -> None:
        """把群聊新消息登记进私有台账（带明确归属，去重、跳过自己）。

        只处理 TextMessage：MemoryQueryEvent / SelectSpeakerEvent 等
        框架事件不进入台账，避免污染归属记录。
        """
        for message in messages:
            if not isinstance(message, TextMessage):
                continue
            source = str(getattr(message, "source", "") or "")
            text = self._extract_text(message)
            if not text:
                continue
            if source == self.name:
                # 自己的发言由本 agent 生成时自行登记，忽略回放
                continue
            msg_id = getattr(message, "id", None)
            key = f"id:{msg_id}" if msg_id else f"body:{source}:{hash(text)}"
            if key in self._seen_msg_keys:
                continue
            self._seen_msg_keys.add(key)
            self._ledger.append(_LedgerEntry(source=source, text=text, seq=self._next_seq()))
        self._trim_ledger()

    def _record_own_reply(self, text: str) -> None:
        self._own_last_reply = text
        self._ledger.append(
            _LedgerEntry(
                source=self.name,
                text=text,
                is_self=True,
                is_own_reply=True,
                seq=self._next_seq(),
            )
        )
        self._trim_ledger()

    def _trim_ledger(self) -> None:
        if len(self._ledger) > self._ledger_max_entries:
            self._ledger = self._ledger[-self._ledger_max_entries :]
        while len(self._ledger) > 1:
            total = sum(len(e.text) for e in self._ledger)
            if total <= self._ledger_char_limit:
                break
            self._ledger.pop(0)

    def _render_ledger_line(self, entry: _LedgerEntry) -> str:
        if entry.is_own_reply:
            return f"[第{entry.seq}轮][{_ROLE_LABELS.get(self._role_type, self._role_type)}] {self._role_display_name}（就是你）：{entry.text}"
        if self._directory is not None and self._directory.known(entry.source):
            label = self._directory.role_label(entry.source)
            display = self._directory.display_name(entry.source)
            return f"[第{entry.seq}轮][{label}] {display}：{entry.text}"
        return f"[第{entry.seq}轮][系统] {entry.source or '系统'}：{entry.text}"

    def _render_transcript(self) -> str:
        lines = [self._render_ledger_line(entry) for entry in self._ledger]
        truncated_note = ""
        if self._ledger and self._ledger[0].seq > 1:
            truncated_note = "（更早的发言原文已省略，如需引用请使用中性表述）\n"
        roster = ""
        if self._directory is not None:
            roster_lines = self._directory.roster_lines(self_name=self.name)
            if roster_lines:
                roster = "\n".join(roster_lines)
        last_speaker = ""
        for entry in reversed(self._ledger):
            if not entry.is_own_reply:
                last_speaker = self._format_entry_name(entry)
                break
        return (
            "【本场讨论发言台账（结构化真实记录，谁说了什么一目了然）】\n"
            f"{truncated_note}"
            + ("\n".join(lines) if lines else "（讨论尚未开始）")
            + (f"\n\n【在场参与者名单】\n{roster}" if roster else "")
            + (f"\n\n上一位实际发言者：{last_speaker}" if last_speaker else "")
            + "\n\n引用规则：说“X刚才说……”之前，必须先在上面的台账中确认那句话在 X 名下；"
            "查不到就改说“有同学提到”；绝不把 A 的话安到 B 头上，也绝不引用未发言者。"
        )

    def _format_entry_name(self, entry: _LedgerEntry) -> str:
        if self._directory is not None and self._directory.known(entry.source):
            return self._directory.display_name(entry.source)
        return entry.source or "系统"

    def _render_turn_instruction(self) -> str:
        label = _ROLE_LABELS.get(self._role_type, self._role_type)
        if self._role_type == ROLE_MODERATOR:
            duty = (
                "你现在的任务：以李老师（主持人）身份推进讨论——串联、点名、控场、必要时收尾。"
                "发言末尾如需点名，请用明确的中文点名句式（例如“小探同学，请说说你的想法”）。"
            )
        elif self._role_type == ROLE_THINKER:
            duty = "你现在的任务：以这位思想家本人的口吻给出有洞察、有画面感的发言，可回应台账中任一观点，但归属必须准确。"
        else:
            duty = "你现在的任务：以这位同学的口吻给出自然、口语化的发言，可回应台账中任一观点，但归属必须准确。"
        return (
            f"现在轮到你——[{label}] {self._role_display_name} 发言。\n"
            f"{duty}\n"
            "严格要求：\n"
            f"1. 你只能以“{self._role_display_name}”的身份说话，第一人称用“我”；\n"
            "2. 不得替其他任何角色发言，不得抢老师的主持台词（除非你就是老师）；\n"
            "3. 台账中标注“（就是你）”的是你自己的发言，引用时用“我刚才……”；\n"
            "4. 直接输出发言正文，不要输出任何解释、标题或格式标记。\n"
            "请开始你的发言："
        )

    # 供测试与诊断使用 ------------------------------------------------- #

    @property
    def ledger_entries(self) -> list[tuple[str, str, bool]]:
        """(source, text, is_own) 列表，用于测试断言。"""
        return [(e.source, e.text, e.is_own_reply) for e in self._ledger]

    # ------------------------------------------------------------------ #
    # 核心覆盖：隔离上下文生成
    # ------------------------------------------------------------------ #

    async def on_messages_stream(
        self,
        messages: Sequence[BaseChatMessage],
        cancellation_token: CancellationToken,
    ):
        # 1) 登记新消息（不污染自身模型上下文）
        self._ingest(messages)

        # 2) 构建一次性隔离上下文
        isolated_context = UnboundedChatCompletionContext()
        inner_messages: list[BaseAgentEvent | BaseChatMessage] = []
        for event_msg in await self._update_model_context_with_memory(
            memory=self._memory,
            model_context=isolated_context,
            agent_name=self.name,
        ):
            inner_messages.append(event_msg)

        last_source = ""
        for entry in reversed(self._ledger):
            if not entry.is_own_reply:
                last_source = entry.source
                break
        await isolated_context.add_message(
            UserMessage(
                content=self._render_transcript() + "\n\n" + self._render_turn_instruction(),
                source=last_source or "系统",
            )
        )

        system_messages = list(self._system_messages or [])
        system_messages.append(SystemMessage(content=SUB_AGENT_SYSTEM_APPENDIX))

        # 3) 调用 LLM（复用父类通道，保留流式事件）
        model_result: CreateResult | None = None
        async for output in self._call_llm(
            model_client=self._model_client,
            model_client_stream=self._model_client_stream,
            system_messages=system_messages,
            model_context=isolated_context,
            workbench=self._workbench,
            handoff_tools=self._handoff_tools,
            agent_name=self.name,
            cancellation_token=cancellation_token,
            output_content_type=self._output_content_type,
            message_id=str(uuid.uuid4()),
        ):
            if isinstance(output, CreateResult):
                model_result = output
            else:
                yield output

        if model_result is None:
            raise RuntimeError(f"[RoleSubAgent] {self.name} 未获得模型结果")

        content = model_result.content
        if not isinstance(content, str):
            content = str(content)

        # 4) 登记自己的发言
        self._record_own_reply(content)

        logger.debug(
            "[RoleSubAgent] %s(%s) 发言 %d 字，台账 %d 条",
            self.name,
            self._role_display_name,
            len(content),
            len(self._ledger),
        )

        # 5) 返回标准响应（不向自身 _model_context 写入任何群聊消息）
        yield Response(
            chat_message=TextMessage(
                content=content,
                source=self.name,
                models_usage=model_result.usage,
            ),
            inner_messages=inner_messages,
        )
