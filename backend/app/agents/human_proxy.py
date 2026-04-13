"""人类参与者 UserProxyAgent 桥接模块

将 STT/WebSocket 输入桥接到 AutoGen 的 UserProxyAgent。
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Optional

from autogen_agentchat.agents import UserProxyAgent
from autogen_core import CancellationToken

from app.models.session import ParticipantType


# 存储每个人类参与者的输入队列
# 键: 参与者名字, 值: asyncio.Queue[str]
_human_input_queues: dict[str, asyncio.Queue[str]] = {}


def make_human_input_func(name: str) -> Callable[[str, Optional[CancellationToken]], Awaitable[str]]:
    """为指定的人类参与者创建 input_func。

    该函数会阻塞等待 STT/WebSocket 将转录文本放入队列。

    Args:
        name: 参与者名字。

    Returns:
        异步 input_func，供 UserProxyAgent 使用。
    """

    async def input_func(prompt: str, cancellation_token: Optional[CancellationToken] = None) -> str:
        queue = _human_input_queues.get(name)
        if queue is None:
            raise RuntimeError(f"参与者 '{name}' 的输入队列不存在")
        return await queue.get()

    return input_func


def create_human_proxy(name: str, description: str = "") -> UserProxyAgent:
    """创建人类参与者 UserProxyAgent。

    Args:
        name: 参与者名字（如"小明"）。
        description: 参与者描述。

    Returns:
        配置好的 UserProxyAgent，其 input_func 从 asyncio.Queue 读取。
    """
    if not description:
        description = f"学生{name}，真人参与者"

    # 创建该参与者的输入队列
    _human_input_queues[name] = asyncio.Queue()

    return UserProxyAgent(
        name=name,
        description=description,
        input_func=make_human_input_func(name),
    )


def get_human_queue(name: str) -> asyncio.Queue[str]:
    """获取指定参与者的输入队列，用于从 WebSocket/STT 推送文本。"""
    if name not in _human_input_queues:
        raise KeyError(f"参与者 '{name}' 的输入队列不存在。请先调用 create_human_proxy。")
    return _human_input_queues[name]


async def put_human_input(name: str, text: str) -> None:
    """向指定参与者的输入队列推送文本。

    当 WebSocket 收到人类消息时调用此函数。
    """
    queue = _human_input_queues.get(name)
    if queue is None:
        raise KeyError(f"参与者 '{name}' 的输入队列不存在")
    await queue.put(text)


def clear_human_queues() -> None:
    """清除所有人类输入队列。在会话结束时调用。"""
    global _human_input_queues
    _human_input_queues.clear()