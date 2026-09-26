"""流式 <think> 推理区间剥离测试（session-9 故障回归）

故障现象（session-9，2026-09-26）：
- 用户"入座开始"后只看到内部准备提示词被当作台词推送；
- 正式对话没有语音播报、页面没有正文字幕；
- 最终 message 的 tts_text 只剩流缓冲尾巴（如"你们觉得呢……"），与 content 完全脱节。

根因：AutoGen 的 OpenAI 兼容客户端把推理模型（reasoning_content）的思考
包装成 <think>…</think> 与正文一起 yield 进流式通道；FloorManager 的
黑名单只能拦住带标签的第一句，导致：
  1) 中间思考句（无标签）泄漏进 tts_segments → 前端播报/显示内部思考；
  2) 正文首句与 </think> 粘连 → 被黑名单整句误杀 → 正文字幕缺失；
  3) emitted_prefix 被思考句污染 → message 尾段对齐失败 → tts_text 错乱。

修复：`_strip_think_blocks_from_stream` 用 per-source 状态机在切句之前
结构性剥离整个 <think>…</think> 区间（跨 chunk 拆分安全）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from autogen_agentchat.messages import ModelClientStreamingChunkEvent, TextMessage

from app.core.floor_manager import FloorManager


class _TeamStub:
    def pause(self) -> None:
        return None

    def resume(self) -> None:
        return None


class _SafetyFilterStub:
    async def filter_or_rewrite(self, content: str) -> str:
        return content

    async def check_human_input(self, _content: str) -> tuple[bool, str]:
        return True, ""


def _build_floor_manager() -> FloorManager:
    return FloorManager(
        team=_TeamStub(),
        ai_agents=[
            SimpleNamespace(name="moderator"),
            SimpleNamespace(name="questioner"),
        ],
        human_agents=[SimpleNamespace(name="豆苗")],
        safety_filter=_SafetyFilterStub(),
    )


async def _feed_stream(fm: FloorManager, source: str, text: str, chunk_size: int = 1):
    """按 chunk 投递流式文本，返回全部非 None 事件。"""
    events = []
    for i in range(0, len(text), chunk_size):
        event = await fm._process_event(
            ModelClientStreamingChunkEvent(source=source, content=text[i : i + chunk_size])
        )
        if event is not None:
            events.append(event)
    return events


def _all_tts_segments(events: list[dict]) -> list[str]:
    segments: list[str] = []
    for event in events:
        if event.get("event_type") == "stream":
            segments.extend(event["data"].get("tts_segments", []))
    return segments


# --------------------------------------------------------------------- #
# 1. session-9 复现：思考句绝不泄漏，正文（含首句）完整播报
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_reasoning_stream_never_leaks_into_tts_or_subtitles() -> None:
    """session-9 真实场景：逐字流 <think>思考</think>正文。

    - 思考句（含黑名单拦不住的"我需要独立给出我的第一反应和理由…"）
      不得进入任何 stream 事件的 tts_segments / content；
    - 正文第一句（修复前与 </think> 粘连被误杀）必须正常播报；
    - message 的 tts_text 是正文的未播尾巴，而非缓冲残留。
    """
    fm = _build_floor_manager()

    reasoning = (
        "我需要独立给出我的第一反应和理由，不要引用他人观点。"
        "我要保持谨慎质疑的性格，用第一人称，2-3句话，语言像小学生。"
        "好，来写：2-3句，第一人称，带故事，留个反问。"
    )
    content = (
        "嗯，等一下，我先想问一个怪问题：大家说的到底是“短视频”不好，"
        "还是“停不下来”不好呀？我上周刷到一个教折纸鹤的视频，跟着折了半小时，"
        "还挺开心的——这算迷恋吗？"
    )
    stream_text = f"<think>{reasoning}</think>{content}你们觉得呢"

    events = await _feed_stream(fm, "questioner", stream_text)

    tts_segments = _all_tts_segments(events)
    joined = "".join(tts_segments)

    # 思考文本绝不泄漏
    assert "我需要独立给出我的第一反应" not in joined
    assert "保持谨慎质疑的性格" not in joined
    assert "好，来写" not in joined
    assert "<think>" not in joined
    assert "</think>" not in joined
    for event in events:
        payload_content = event["data"].get("content", "")
        assert "我需要独立" not in payload_content

    # 正文首句必须完整保留（修复前被 </think> 粘连误杀）
    assert "嗯，等一下，我先想问一个怪问题" in joined
    assert "教折纸鹤的视频" in joined

    message_event = await fm._process_event(
        TextMessage(source="questioner", content=f"{content}你们觉得呢")
    )
    assert message_event is not None
    assert message_event["event_type"] == "message"
    # message 正文完整
    assert message_event["data"]["content"].startswith("嗯，等一下")
    # tts_text 是正文的未播尾巴（缓冲残留的正文），不再是与正文脱节的碎片
    tts_text = message_event["data"]["tts_text"]
    assert tts_text.strip() == "你们觉得呢"
    assert "我需要独立" not in tts_text


# --------------------------------------------------------------------- #
# 2. 跨 chunk 标签拆分安全
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_think_tag_split_across_chunks_is_fully_stripped() -> None:
    """标签被拆分到不同 chunk（<th + ink>、</thi + nk>）时思考仍被剥离。"""
    fm = _build_floor_manager()

    chunks = [
        "<th",
        "ink>我需要独立给出我的第一反应和理由，不要引用他人观点。",
        "还要再想想。",
        "</th",
        "ink>",
        "先看规则。再想",
    ]
    events = []
    for chunk in chunks:
        event = await fm._process_event(
            ModelClientStreamingChunkEvent(source="questioner", content=chunk)
        )
        if event is not None:
            events.append(event)

    tts_segments = _all_tts_segments(events)
    joined = "".join(tts_segments)
    assert "我需要独立" not in joined
    assert "还要再想想" not in joined
    assert "先看规则。" in joined

    message_event = await fm._process_event(
        TextMessage(source="questioner", content="先看规则。再想一想。")
    )
    assert message_event is not None
    assert message_event["data"]["tts_text"] == "再想一想。"


# --------------------------------------------------------------------- #
# 3. 流中断（未闭合 think）后状态重置，下一轮不受污染
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_unclosed_think_block_resets_before_next_turn() -> None:
    """流在思考区间内被中断（无 </think>），下一轮发言的正文不被误杀。

    同一学生角色不允许连续两轮发言（既有守卫会拦截），因此第二轮
    由老师接手——正对应 session-9 中思考流卡死、超时恢复指定老师
    继续的真实路径。
    """
    fm = _build_floor_manager()

    # 第一轮：思考流中断，随后 message 结束本轮
    await _feed_stream(fm, "questioner", "<think>我需要独立给出第一反应，还没想完")
    message_event = await fm._process_event(
        TextMessage(source="questioner", content="我先说结论：规则要先看清楚。")
    )
    assert message_event is not None

    # 第二轮：老师接手，正文流（无 think 标签）必须完整播报
    events = await _feed_stream(fm, "moderator", "这次我先看规则。再想")
    tts_segments = _all_tts_segments(events)
    assert "这次我先看规则。" in tts_segments

    message_event = await fm._process_event(
        TextMessage(source="moderator", content="这次我先看规则。再想一想。")
    )
    # 注：老师消息受既有守卫改写（真人参与不足时强制替换为点名真人），
    # 此处只验证消息正常产出、思考文本不回流。
    assert message_event is not None
    assert "我需要独立" not in message_event["data"]["tts_text"]
    assert "我需要独立" not in message_event["data"]["content"]


# --------------------------------------------------------------------- #
# 4. 无 think 标签的普通流不受影响（行为兼容）
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_plain_stream_without_think_tags_keeps_existing_behavior() -> None:
    fm = _build_floor_manager()

    events = await _feed_stream(fm, "questioner", "先看规则。再想")
    assert _all_tts_segments(events) == ["先看规则。"]

    message_event = await fm._process_event(
        TextMessage(source="questioner", content="先看规则。再想一想。")
    )
    assert message_event is not None
    assert message_event["data"]["tts_text"] == "再想一想。"


# --------------------------------------------------------------------- #
# 5. 状态机单元行为
# --------------------------------------------------------------------- #


def test_strip_think_blocks_state_machine_direct() -> None:
    fm = _build_floor_manager()

    # 进入思考区间：只放行标签前的正文
    assert fm._strip_think_blocks_from_stream("q", "开场。<think>我要开始想") == "开场。"
    # 区间内全部丢弃
    assert fm._strip_think_blocks_from_stream("q", "思考内容一。思考内容二。") == ""
    # 结束标签与正文首句同 chunk：正文保留
    assert fm._strip_think_blocks_from_stream("q", "</think>嗯，等一下。") == "嗯，等一下。"
    # 状态已复位为非思考区间
    assert fm._strip_think_blocks_from_stream("q", "后续正文。") == "后续正文。"
    # 清理出口
    fm._pop_streaming_message_tail("q", "")
    assert fm._strip_think_blocks_from_stream("q", "<think>重新进入思考") == ""


def test_partial_tag_suffix_matches_only_true_prefixes() -> None:
    assert FloorManager._partial_tag_suffix("xx<th", "<think>") == "<th"
    assert FloorManager._partial_tag_suffix("</thi", "</think>") == "</thi"
    assert FloorManager._partial_tag_suffix("普通正文", "<think>") == ""
    assert FloorManager._partial_tag_suffix("<", "<think>") == "<"
    # 完整标签是真前缀语义下也成立（主循环会先行匹配，此处仅验证语义）
    assert FloorManager._partial_tag_suffix("<think", "<think>") == "<think"
