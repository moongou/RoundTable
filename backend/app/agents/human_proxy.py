"""人类参与者 UserProxyAgent 桥接模块

将 STT/WebSocket 输入桥接到 AutoGen 的 UserProxyAgent。
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Awaitable, Callable, Optional

from autogen_agentchat.agents import UserProxyAgent
from autogen_core import CancellationToken

from app.models.session import ParticipantType

logger = logging.getLogger(__name__)


# 存储每个人类参与者的输入队列
# 键: 显示名字 (display_name), 值: asyncio.Queue[str]
_human_input_queues: dict[str, asyncio.Queue[str]] = {}
_human_name_aliases: dict[str, str] = {}


def normalize_display_name(name: str) -> str:
    """归一化显示名，避免前后空白导致队列查找失败。"""
    return (name or "").strip()


def _resolve_queue_name(name: str) -> str | None:
    normalized = normalize_display_name(name)
    if normalized in _human_input_queues:
        return normalized
    if name in _human_input_queues:
        return name

    alias = _human_name_aliases.get(normalized) or _human_name_aliases.get(name)
    if alias:
        return alias

    if normalized:
        normalized_no_space = normalized.replace(" ", "")
        for key in _human_input_queues:
            if key.replace(" ", "") == normalized_no_space:
                return key
    return None


def safe_agent_name(name: str) -> str:
    """将任意名字转为合法 Python 标识符（AutoGen 要求 str.isidentifier()）。

    - 连字符 '-' → 下划线 '_'
    - 其他非 ASCII / 非标识符字符 → u{hex} 编码
    - 若首字符为数字，前缀 '_'
    例如: "henry-ford" → "henry_ford", "同学" → "u540cu5b66"
    """
    safe = re.sub(
        r"[^a-zA-Z0-9_]",
        lambda m: "_" if m.group() == "-" else f"u{ord(m.group()):04x}",
        name,
    )
    if safe and safe[0].isdigit():
        safe = "_" + safe
    return safe if safe else "human"


def make_human_input_func(name: str, timeout: float = 120.0) -> Callable[[str, Optional[CancellationToken]], Awaitable[str]]:
    """为指定的人类参与者创建 input_func。

    该函数会阻塞等待 STT/WebSocket 将转录文本放入队列。
    若超时，自动返回跳过消息，避免讨论无限期挂起。

    Args:
        name: 参与者名字。
        timeout: 等待超时秒数（默认 120 秒）。

    Returns:
        异步 input_func，供 UserProxyAgent 使用。
    """

    async def input_func(prompt: str, cancellation_token: Optional[CancellationToken] = None) -> str:
        queue_name = _resolve_queue_name(name)
        if queue_name is None:
            queue_name = normalize_display_name(name) or name or "同学"
            logger.warning("参与者 '%s' 的输入队列不存在，已自动创建并回退", name)
            _human_input_queues[queue_name] = asyncio.Queue()

        queue = _human_input_queues.get(queue_name)
        if queue is None:
            logger.warning("参与者 '%s' 的输入队列获取失败，自动跳过本轮", name)
            return "（我先听听大家的意见）"
        try:
            text = await asyncio.wait_for(queue.get(), timeout=timeout)
            normalized = (text or "").strip()
            return normalized if normalized else "（我先听听大家的意见）"
        except asyncio.TimeoutError:
            logger.warning(f"参与者 '{name}' 等待超时（{timeout}s），自动跳过本轮")
            return "（我先听听大家的意见）"

    return input_func


def create_human_proxy(display_name: str, description: str = "") -> UserProxyAgent:
    """创建人类参与者 UserProxyAgent。

    Args:
        display_name: 参与者显示名字（如"小明"，可含中文）。
        description: 参与者描述。

    Returns:
        配置好的 UserProxyAgent，其 input_func 从 asyncio.Queue 读取。
        Agent 内部 name 为 AutoGen 兼容的 ASCII 格式；显示名保存在 description 中。
    """
    display_name = normalize_display_name(display_name) or "同学"
    agent_name = safe_agent_name(display_name)
    if not description:
        description = f"学生{display_name}，真人参与者"

    # 输入队列以显示名为键，方便 put_human_input 按原始名称写入
    _human_input_queues[display_name] = asyncio.Queue()
    _human_name_aliases[display_name] = display_name

    return UserProxyAgent(
        name=agent_name,
        description=description,
        input_func=make_human_input_func(display_name),
    )


def get_human_queue(name: str) -> asyncio.Queue[str]:
    """获取指定参与者的输入队列，用于从 WebSocket/STT 推送文本。"""
    queue_name = _resolve_queue_name(name)
    if queue_name is None:
        normalized = normalize_display_name(name)
        raise KeyError(f"参与者 '{normalized or name}' 的输入队列不存在。请先调用 create_human_proxy。")
    return _human_input_queues[queue_name]


async def put_human_input(name: str, text: str) -> None:
    """向指定参与者的输入队列推送文本。

    当 WebSocket 收到人类消息时调用此函数。
    """
    normalized_name = normalize_display_name(name)
    queue_name = _resolve_queue_name(normalized_name)
    if queue_name is None:
        queue_name = normalized_name or name or "同学"
        logger.warning("参与者 '%s' 的输入队列不存在，已自动创建", name)
        _human_input_queues[queue_name] = asyncio.Queue()
        _human_name_aliases[queue_name] = queue_name

    queue = _human_input_queues.get(queue_name)
    if queue is None:
        raise KeyError(f"参与者 '{queue_name}' 的输入队列不存在")
    await queue.put((text or "").strip())


def clear_human_queues(names: list[str] | None = None) -> None:
    """清除人类输入队列。

    Args:
        names: 要清除的参与者名字列表。若为 None，清除所有队列（仅在服务关闭时使用）。
    """
    global _human_input_queues
    if names is None:
        _human_input_queues.clear()
        _human_name_aliases.clear()
    else:
        for name in names:
            normalized = normalize_display_name(name)
            queue_name = _resolve_queue_name(normalized) or normalized
            _human_input_queues.pop(queue_name, None)
            _human_name_aliases.pop(normalized, None)