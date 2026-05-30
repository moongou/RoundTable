"""Tests for the resumable discussion session hub.

Covers the disconnect/reconnect lifecycle that keeps a discussion alive while
the browser tab is briefly backgrounded or the socket drops:
buffering + replay, attach/detach state, grace-window finalize, and the hub
registry's ``get_active`` semantics.
"""

import asyncio

import pytest

from app.core.discussion_session_hub import (
    DiscussionSessionHub,
    LiveDiscussion,
    SocketTransport,
)


class FakeWebSocket:
    """Minimal stand-in for a FastAPI WebSocket with a controllable failure."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.fail = False

    async def send_json(self, payload: dict) -> None:
        if self.fail:
            raise RuntimeError("socket closed")
        self.sent.append(payload)


def _live(ws: FakeWebSocket, *, grace_sec: float = 180.0) -> LiveDiscussion:
    return LiveDiscussion(
        "session-x", SocketTransport(ws), grace_sec=grace_sec, buffer_limit=10
    )


@pytest.mark.asyncio
async def test_deliver_sends_when_attached_and_buffers():
    ws = FakeWebSocket()
    live = _live(ws)

    delivered = await live.deliver({"event_type": "system", "event_seq": 1, "data": {}})

    assert delivered is True
    assert ws.sent == [{"event_type": "system", "event_seq": 1, "data": {}}]
    # Buffer retains the event for potential replay.
    assert live.replay_after(0) == [{"event_type": "system", "event_seq": 1, "data": {}}]


@pytest.mark.asyncio
async def test_detached_buffers_without_sending_then_replays_on_reattach():
    ws1 = FakeWebSocket()
    live = _live(ws1)

    # First event goes out on the live socket.
    await live.deliver({"event_type": "a", "event_seq": 1, "data": {}})
    assert len(ws1.sent) == 1

    # Browser tab switch -> socket drops.
    live.detach()
    assert live.attached is False
    assert live.within_grace() is True
    assert live.is_connected() is True  # grace keeps FloorManager Guard-2 quiet

    # Events produced while detached are buffered, not sent.
    delivered = await live.deliver({"event_type": "b", "event_seq": 2, "data": {}})
    assert delivered is False
    assert len(ws1.sent) == 1

    # Reconnect with a fresh socket; client last saw seq=1.
    ws2 = FakeWebSocket()
    live.attach(SocketTransport(ws2))
    assert live.attached is True
    missed = live.replay_after(1)
    assert missed == [{"event_type": "b", "event_seq": 2, "data": {}}]

    # New events flow to the new socket.
    await live.deliver({"event_type": "c", "event_seq": 3, "data": {}})
    assert ws2.sent[-1]["event_type"] == "c"


@pytest.mark.asyncio
async def test_deliver_failure_marks_transport_closed():
    ws = FakeWebSocket()
    ws.fail = True
    live = _live(ws)

    delivered = await live.deliver({"event_type": "a", "event_seq": 1, "data": {}})

    assert delivered is False
    assert live.transport is not None and live.transport.closed is True


@pytest.mark.asyncio
async def test_grace_cleanup_finalizes_when_not_reattached():
    ws = FakeWebSocket()
    live = _live(ws, grace_sec=0.05)

    async def _never_ends():
        await asyncio.sleep(10)

    live.discussion_task = asyncio.create_task(_never_ends())

    called = asyncio.Event()

    async def _finalize():
        called.set()

    live.detach()
    live.schedule_grace_cleanup(_finalize)

    await asyncio.wait_for(called.wait(), timeout=2.0)
    assert called.is_set()

    live.discussion_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await live.discussion_task


@pytest.mark.asyncio
async def test_grace_cleanup_cancelled_on_reattach():
    ws = FakeWebSocket()
    live = _live(ws, grace_sec=5.0)

    async def _never_ends():
        await asyncio.sleep(10)

    live.discussion_task = asyncio.create_task(_never_ends())

    called = asyncio.Event()

    async def _finalize():
        called.set()

    live.detach()
    live.schedule_grace_cleanup(_finalize)
    # Reconnect quickly -> grace timer is cancelled, finalize never runs.
    live.attach(SocketTransport(FakeWebSocket()))

    await asyncio.sleep(0.1)
    assert called.is_set() is False

    live.discussion_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await live.discussion_task


@pytest.mark.asyncio
async def test_grace_cleanup_runs_when_task_finishes_while_detached():
    ws = FakeWebSocket()
    live = _live(ws, grace_sec=30.0)

    async def _quick():
        await asyncio.sleep(0.05)

    live.discussion_task = asyncio.create_task(_quick())

    called = asyncio.Event()

    async def _finalize():
        called.set()

    live.detach()
    live.schedule_grace_cleanup(_finalize)

    # Even though the grace window is long, finalize fires as soon as the
    # discussion task completes while detached.
    await asyncio.wait_for(called.wait(), timeout=2.0)
    assert called.is_set()


@pytest.mark.asyncio
async def test_hub_get_active_only_returns_running_sessions():
    hub = DiscussionSessionHub()
    ws = FakeWebSocket()
    live = _live(ws)

    async def _never_ends():
        await asyncio.sleep(10)

    live.discussion_task = asyncio.create_task(_never_ends())
    hub.register(live)

    assert hub.get_active("session-x") is live

    # Once closed/discarded it is no longer resumable.
    hub.discard("session-x", live)
    assert hub.get_active("session-x") is None
    assert live.is_active() is False

    live.discussion_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await live.discussion_task


@pytest.mark.asyncio
async def test_buffer_is_bounded():
    ws = FakeWebSocket()
    live = _live(ws)  # buffer_limit=10

    for seq in range(1, 16):
        await live.deliver({"event_type": "x", "event_seq": seq, "data": {}})

    # Only the most recent 10 events are retained for replay.
    replayed = live.replay_after(0)
    assert len(replayed) == 10
    assert replayed[0]["event_seq"] == 6
    assert replayed[-1]["event_seq"] == 15
