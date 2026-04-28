from __future__ import annotations

from typing import Any

from autogen_core.memory import (
    ListMemory,
    MemoryContent,
    MemoryMimeType,
    MemoryQueryResult,
    UpdateContextResult,
)
from autogen_core.model_context import ChatCompletionContext
from autogen_core.models import SystemMessage


class RollingSummaryMemory(ListMemory):
    """Shared rolling memory that injects the last few turn summaries.

    Each agent query receives a short speaker + core-point recap so reference
    attribution has a concrete, recent anchor instead of relying on raw history
    alone.
    """

    def __init__(self, name: str = 'recent_turn_summaries', max_entries: int = 3) -> None:
        super().__init__(name=name)
        self._max_entries = max_entries
        self._turn_summaries: list[tuple[str, str]] = []
        self._spoken_names: list[str] = []
        self._unspoken_names: list[str] = []
        self._recent_quotes: list[tuple[str, str]] = []

    async def replace_turn_summaries(
        self,
        turns: list[tuple[str, str]],
        *,
        spoken_names: list[str] | None = None,
        unspoken_names: list[str] | None = None,
        recent_quotes: list[tuple[str, str]] | None = None,
    ) -> None:
        self._turn_summaries = turns[-self._max_entries :]
        if spoken_names is not None:
            self._spoken_names = [name for name in spoken_names if str(name).strip()]
        if unspoken_names is not None:
            self._unspoken_names = [name for name in unspoken_names if str(name).strip()]
        if recent_quotes is not None:
            self._recent_quotes = recent_quotes[-max(self._max_entries + 1, 4) :]
        self.content = [
            MemoryContent(
                content={'speaker': speaker, 'summary': summary},
                mime_type=MemoryMimeType.JSON,
                metadata={'speaker': speaker},
            )
            for speaker, summary in self._turn_summaries
        ]

    async def update_context(
        self,
        model_context: ChatCompletionContext,
    ) -> UpdateContextResult:
        if not self.content:
            return UpdateContextResult(memories=MemoryQueryResult(results=[]))

        lines: list[str] = []
        for index, memory in enumerate(self.content, 1):
            payload: Any = memory.content
            if isinstance(payload, dict):
                speaker = str(payload.get('speaker', '')).strip()
                summary = str(payload.get('summary', '')).strip()
                if speaker and summary:
                    lines.append(f'{index}. {speaker}：{summary}')
            else:
                text = str(payload).strip()
                if text:
                    lines.append(f'{index}. {text}')

        if not lines:
            return UpdateContextResult(memories=MemoryQueryResult(results=self.content))

        ledger_lines: list[str] = []
        if self._spoken_names:
            ledger_lines.append('已实际发言：' + '、'.join(self._spoken_names))
        if self._unspoken_names:
            ledger_lines.append('尚未实际发言：' + '、'.join(self._unspoken_names))
        latest_speaker = ''
        for memory in reversed(self.content):
            payload = memory.content
            if isinstance(payload, dict):
                latest_speaker = str(payload.get('speaker', '')).strip()
                if latest_speaker:
                    break
        if latest_speaker:
            ledger_lines.append('上一位实际发言（不含当前输出者）：' + latest_speaker)

        quote_lines = [
            f'{index}. {speaker}：{quote}'
            for index, (speaker, quote) in enumerate(self._recent_quotes, 1)
            if str(speaker).strip() and str(quote).strip()
        ]

        await model_context.add_message(
            SystemMessage(
                content=(
                    '最近讨论台账（严格用于核对“是谁说过什么”，不得编造或错配归属）：\n'
                    + ('\n'.join(ledger_lines) + '\n' if ledger_lines else '')
                    + '最近3轮发言摘要：\n'
                    + '\n'.join(lines)
                    + (
                        '\n最近原句摘录：\n' + '\n'.join(quote_lines)
                        if quote_lines
                        else ''
                    )
                    + '\n引用规则：只有当某人的摘录或真实历史中确有对应观点时，才能说“X刚才说”。'
                    + '点评或复述时优先核对“上一位实际发言”，不要把主持人、真人学生或尚未发言者误当成发言来源。'
                    + '不要因为某人是真人学生，就把别人的观点挂到他/她名下。'
                    + '如果一句话综合了多位发言者，必须分别说明，或改说“有同学提到”。'
                    + '如果归属拿不准，宁可使用中性表述，也不要张冠李戴。\n'
                )
            )
        )
        return UpdateContextResult(memories=MemoryQueryResult(results=self.content))


class HumanResponseGuidanceMemory(ListMemory):
    """Moderator-only memory for hidden guidance on how to handle human turns."""

    def __init__(self, name: str = 'human_response_guidance', max_entries: int = 1) -> None:
        super().__init__(name=name)
        self._max_entries = max_entries
        self._guidance_entries: list[dict[str, Any]] = []

    async def replace_guidance(self, entries: list[dict[str, Any]]) -> None:
        cleaned_entries = [entry for entry in entries if isinstance(entry, dict)]
        self._guidance_entries = cleaned_entries[-self._max_entries :]
        self.content = [
            MemoryContent(
                content=entry,
                mime_type=MemoryMimeType.JSON,
                metadata={
                    'speaker': str(entry.get('speaker', '')).strip(),
                    'assessment': str(entry.get('assessment', '')).strip(),
                },
            )
            for entry in self._guidance_entries
        ]

    async def clear(self) -> None:
        self._guidance_entries = []
        self.content = []

    async def update_context(
        self,
        model_context: ChatCompletionContext,
    ) -> UpdateContextResult:
        if not self.content or not self._guidance_entries:
            return UpdateContextResult(memories=MemoryQueryResult(results=[]))

        latest = self._guidance_entries[-1]
        speaker = str(latest.get('speaker', '')).strip()
        summary = str(latest.get('summary', '')).strip()
        assessment = str(latest.get('assessment', '')).strip()
        topic_focus = str(latest.get('topic_focus', '')).strip()
        suggested_peer_name = str(latest.get('suggested_peer_name', '')).strip()
        suggested_peer_summary = str(latest.get('suggested_peer_summary', '')).strip()

        assessment_label = {
            'weak': '这句内容偏弱或过于笼统，不要硬夸，也不要直接略过。',
            'off_topic': '这句明显偏离当前讨论主线，不要顺着离题内容继续展开。',
        }.get(assessment, '这句需要老师额外留意回应方式。')

        guidance_lines = [
            '真人学生回应提醒（仅供李老师内部把握，不要直接念出这段后台说明）：',
            f'- 当前需要回应的学生：{speaker}',
            f'- 学生刚才的大意：{summary or "（无摘要）"}',
            f'- 后台判断：{assessment_label}',
        ]
        if topic_focus:
            guidance_lines.append(
                f'- 回应策略：先简短接住，再温和把话题拉回“{topic_focus}”。'
            )
        else:
            guidance_lines.append('- 回应策略：先简短接住，再温和把话题拉回本轮讨论主题。')
        if suggested_peer_name and suggested_peer_summary:
            guidance_lines.append(
                f'- 可用追问：请学生接着回应{suggested_peer_name}刚才提到的“{suggested_peer_summary}”，再问“你怎么看”。'
            )
        elif suggested_peer_name:
            guidance_lines.append(
                f'- 可用追问：可以请学生接着回应{suggested_peer_name}刚才的观点，你怎么看。'
            )
        guidance_lines.append('- 语气要求：保持温和、具体、邀请式，不要训斥，也不要把离题内容夸成很深刻。')

        guidance_text = '\n'.join(guidance_lines)

        await model_context.add_message(SystemMessage(content=guidance_text))
        return UpdateContextResult(memories=MemoryQueryResult(results=self.content))