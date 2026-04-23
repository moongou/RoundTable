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

    async def replace_turn_summaries(self, turns: list[tuple[str, str]]) -> None:
        self.content = [
            MemoryContent(
                content={'speaker': speaker, 'summary': summary},
                mime_type=MemoryMimeType.JSON,
                metadata={'speaker': speaker},
            )
            for speaker, summary in turns[-self._max_entries :]
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

        await model_context.add_message(
            SystemMessage(
                content=(
                    '最近3轮发言摘要（仅供核对引用与上下文，不得编造扩写）：\n'
                    + '\n'.join(lines)
                    + '\n请优先基于这些已发生的发言回应；若归属不确定，使用“有同学提到”或“有先生提到”。\n'
                )
            )
        )
        return UpdateContextResult(memories=MemoryQueryResult(results=self.content))