"""Resumable discussion session hub.

Keeps a running discussion (FloorManager + its asyncio task) alive across
short-lived WebSocket disconnects so that switching browser tabs / brief
network drops do not kill the meeting. Outbound events are buffered in a
bounded replay log; on reconnect the client resumes from its last seen
``event_seq`` and the live transport is swapped to the new socket.

Design notes
------------
* OUTBOUND (all server -> client events) is routed through ``LiveDiscussion``
  so the still-running discussion task can be re-pointed at a new socket
  simply by swapping ``transport``.
* INBOUND (client -> server) is handled per-connection by the WebSocket
  handler and forwarded to ``floor_manager``.
* While DETACHED (socket gone, within grace window) events are still produced
  and appended to the buffer; they are replayed on reconnect.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any, Awaitable, Callable, Deque, Optional

logger = logging.getLogger(__name__)

# Default grace window: how long a discussion keeps running with no attached
# socket before it is force-finalized.
DEFAULT_GRACE_SEC = 180.0
# Bounded replay buffer size (number of outbound events retained for resume).
DEFAULT_BUFFER_LIMIT = 800


class SocketTransport:
    """Thin wrapper around a FastAPI WebSocket with a ``closed`` flag."""

    def __init__(self, websocket: Any) -> None:
        self._websocket = websocket
        self.closed = False

    async def send_json(self, payload: dict) -> None:
        await self._websocket.send_json(payload)

    def mark_closed(self) -> None:
        self.closed = True


class LiveDiscussion:
    """Holds the resumable state for a single discussion session."""

    def __init__(
        self,
        session_id: str,
        transport: Optional[SocketTransport],
        *,
        grace_sec: float = DEFAULT_GRACE_SEC,
        buffer_limit: int = DEFAULT_BUFFER_LIMIT,
    ) -> None:
        self.session_id = session_id
        self.transport: Optional[SocketTransport] = transport
        self.buffer: Deque[tuple[int, dict]] = deque(maxlen=buffer_limit)
        self.event_seq = 0
        self.grace_sec = grace_sec

        self._attached = transport is not None
        self.detached_since: Optional[float] = None

        # Wired up by the handler once the discussion task exists.
        self.floor_manager: Any = None
        self.discussion_task: Optional[asyncio.Task] = None
        self.agent_display_map: dict[str, str] = {}
        self.pending_human_request_ts: dict[str, float] = {}
        self.pending_human_request_id: dict[str, str] = {}
        self.send_event: Optional[Callable[[str, dict], Awaitable[bool]]] = None
        self.human_name: str = ""
        # Teardown coroutine factory set by the handler; invoked when the grace
        # window elapses or the discussion finishes while detached.
        self.finalize: Optional[Callable[[], Awaitable[None]]] = None

        self._grace_task: Optional[asyncio.Task] = None
        self._closed = False

    # -- state -----------------------------------------------------------
    @property
    def attached(self) -> bool:
        return self._attached

    def within_grace(self) -> bool:
        if self._attached or self.detached_since is None:
            return False
        return (time.monotonic() - self.detached_since) < self.grace_sec

    def is_connected(self) -> bool:
        """Used as FloorManager ``is_connected`` so Guard-2 does not force-end
        during the reconnect grace window."""
        return self._attached or self.within_grace()

    def is_active(self) -> bool:
        if self._closed:
            return False
        task = self.discussion_task
        return task is not None and not task.done()

    # -- outbound --------------------------------------------------------
    def next_seq(self) -> int:
        self.event_seq += 1
        return self.event_seq

    async def deliver(self, payload: dict) -> bool:
        """Buffer the payload and, if a live socket is attached, send it.

        Returns True only when the event was actually written to a socket.
        """
        seq = payload.get("event_seq")
        if isinstance(seq, int):
            self.buffer.append((seq, payload))
        transport = self.transport
        if not self._attached or transport is None or transport.closed:
            return False
        try:
            await transport.send_json(payload)
            return True
        except Exception:
            transport.mark_closed()
            return False

    def replay_after(self, last_seq: int) -> list[dict]:
        return [payload for seq, payload in list(self.buffer) if seq > last_seq]

    # -- attach / detach -------------------------------------------------
    def attach(self, transport: SocketTransport) -> None:
        if self._grace_task is not None and not self._grace_task.done():
            self._grace_task.cancel()
        self._grace_task = None
        self.transport = transport
        self._attached = True
        self.detached_since = None

    def detach(self) -> None:
        self._attached = False
        self.detached_since = time.monotonic()
        if self.transport is not None:
            self.transport.mark_closed()
        self.transport = None

    def schedule_grace_cleanup(self, finalize: Optional[Callable[[], Awaitable[None]]]) -> None:
        """Start (or restart) the grace timer; ``finalize`` runs if the
        socket is not re-attached before the window elapses, or as soon as the
        discussion task finishes while detached."""

        if finalize is None:
            return
        if self._grace_task is not None and not self._grace_task.done():
            self._grace_task.cancel()

        async def _runner() -> None:
            try:
                deadline = time.monotonic() + self.grace_sec
                while True:
                    if self._attached or self._closed:
                        return
                    task = self.discussion_task
                    if task is not None and task.done():
                        break
                    if time.monotonic() >= deadline:
                        break
                    await asyncio.sleep(1.0)
                if self._attached or self._closed:
                    return
                await finalize()
            except asyncio.CancelledError:  # re-attached
                raise
            except Exception:  # pragma: no cover - defensive
                logger.debug("grace cleanup failed session=%s", self.session_id, exc_info=True)

        self._grace_task = asyncio.create_task(_runner())

    def mark_closed(self) -> None:
        self._closed = True
        self._attached = False
        if self._grace_task is not None and not self._grace_task.done():
            self._grace_task.cancel()
        self._grace_task = None


class DiscussionSessionHub:
    """Process-wide registry of resumable discussions keyed by session id."""

    def __init__(self) -> None:
        self._live: dict[str, LiveDiscussion] = {}

    def get(self, session_id: str) -> Optional[LiveDiscussion]:
        return self._live.get(session_id)

    def get_active(self, session_id: str) -> Optional[LiveDiscussion]:
        live = self._live.get(session_id)
        if live is not None and live.is_active():
            return live
        return None

    def register(self, live: LiveDiscussion) -> None:
        self._live[live.session_id] = live

    def discard(self, session_id: str, live: Optional[LiveDiscussion] = None) -> None:
        existing = self._live.get(session_id)
        if existing is None:
            return
        if live is not None and existing is not live:
            return
        existing.mark_closed()
        self._live.pop(session_id, None)


# Singleton used by the websocket handler.
SESSION_HUB = DiscussionSessionHub()
