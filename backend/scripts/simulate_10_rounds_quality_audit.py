from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import queue
import random
import re
import threading
import time
import traceback
from concurrent.futures import CancelledError as FutureCancelledError
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Sequence

import httpx
from autogen_core import CancellationToken
from autogen_core.models import ChatCompletionClient, CreateResult, RequestUsage
from fastapi.testclient import TestClient

from app.api.v1 import websocket as websocket_api
from app.core.floor_text_utils import normalize_reference_match_text, score_reference_fragment
from app.core.turn_scheduler import parse_speaker_designation
from app.main import app


HUMAN_NAME = "豆苗"
RUNTIME_DIR = Path(__file__).resolve().parents[1] / "runtime" / "meeting_history"
QUOTE_RE = re.compile(r"[“\"「『]([^”\"」』]{2,80})[”\"」』]")
SENTENCE_RE = re.compile(r"[。！？!?]")
SPEECH_ENTRY_TYPES = {"message", "human_input"}
SKIP_TEXTS = {"（跳过）", "(跳过)", "跳过"}
AUTHORIZED_HUMAN_REQUEST_REASONS = {
    "moderator_designated_human",
    "participant_designated_human",
    "interrupt",
}
DEFAULT_HUMAN_BASE_URL = os.getenv("ROUND_TABLE_AUDIT_HUMAN_BASE_URL", "http://localhost:11434")
DEFAULT_REQUESTED_HUMAN_MODEL = os.getenv("ROUND_TABLE_AUDIT_HUMAN_MODEL", "deepseek-v4-flash")
HUMAN_MODEL_FALLBACKS = (
    "sorc/qwen3.5-claude-4.6-opus:latest",
    "qwen3.6:latest",
    "gpt-oss:20b",
)
HAND_RAISE_ROUND_INDEXES = {3, 6, 9}


@dataclass(frozen=True)
class RoundConfig:
    topic_id: str
    topic_title: str
    character_ids: list[str]
    thinker_id: str


@dataclass(frozen=True)
class AuditRunConfig:
    mode: str
    round_limit: int
    max_turns: int
    requested_human_model: str
    human_base_url: str
    human_timeout_sec: float
    round_timeout_sec: float
    terminate_grace_sec: float
    max_events_per_round: int
    phase_stall_event_limit: int
    closing_drain_event_limit: int
    idle_timeout_sec: float


ROUND_CONFIGS: list[RoundConfig] = [
    RoundConfig("education-002", "标准答案的危险", ["explorer", "skeptic", "empath"], "socrates"),
    RoundConfig("ethics-002", "说谎永远不对吗", ["questioner", "rationalist", "peacemaker"], "kant"),
    RoundConfig("life-008", "什么是勇气", ["optimist", "storyteller", "pragmatist", "skeptic"], "nietzsche"),
    RoundConfig("tech-004", "AI 会抢走工作吗", ["innovator", "skeptic", "rationalist"], "adam-smith"),
    RoundConfig("society-005", "规则越多越好吗", ["questioner", "pragmatist", "explorer", "comedian"], "confucius"),
    RoundConfig("education-003", "在家上学可行吗", ["explorer", "rationalist", "storyteller"], "tagore"),
    RoundConfig("science-003", "机器人能有感情吗", ["skeptic", "empath", "innovator", "comedian"], "turing"),
    RoundConfig("life-004", "追求完美是好事吗", ["questioner", "optimist", "pragmatist"], "aristotle"),
    RoundConfig("society-003", "班级规则应该大家一起定吗", ["peacemaker", "skeptic", "rationalist"], "dewey"),
    RoundConfig("ethics-004", "要不要总是听大人的话", ["explorer", "empath", "questioner", "storyteller"], "confucius"),
]


class DeterministicChatClient(ChatCompletionClient):
    """Small local model double for end-to-end discussion quality audits."""

    def __init__(self) -> None:
        self._usage = RequestUsage(prompt_tokens=0, completion_tokens=0)
        self._dump_count = 0

    @property
    def model_info(self) -> dict[str, Any]:
        return {
            "vision": False,
            "function_calling": True,
            "json_output": True,
            "family": "deterministic-audit",
            "structured_output": False,
            "multiple_system_messages": True,
        }

    @property
    def capabilities(self) -> dict[str, Any]:
        return self.model_info

    def actual_usage(self) -> RequestUsage:
        return self._usage

    def total_usage(self) -> RequestUsage:
        return self._usage

    def count_tokens(self, messages: Sequence[Any], *, tools: Sequence[Any] = []) -> int:
        return len(_messages_text(messages)) // 2

    def remaining_tokens(self, messages: Sequence[Any], *, tools: Sequence[Any] = []) -> int:
        return 12000

    async def close(self) -> None:
        return None

    async def create(
        self,
        messages: Sequence[Any],
        *,
        tools: Sequence[Any] = [],
        tool_choice: Any = "auto",
        json_output: Any = None,
        extra_create_args: Any = {},
        cancellation_token: CancellationToken | None = None,
    ) -> CreateResult:
        content = self._reply(messages)
        return CreateResult(
            finish_reason="stop",
            content=content,
            usage=RequestUsage(prompt_tokens=1, completion_tokens=max(1, len(content) // 2)),
            cached=False,
        )

    async def create_stream(
        self,
        messages: Sequence[Any],
        *,
        tools: Sequence[Any] = [],
        tool_choice: Any = "auto",
        json_output: Any = None,
        extra_create_args: Any = {},
        cancellation_token: CancellationToken | None = None,
    ) -> AsyncGenerator[str | CreateResult, None]:
        content = self._reply(messages)
        midpoint = max(1, len(content) // 2)
        yield content[:midpoint]
        yield content[midpoint:]
        yield CreateResult(
            finish_reason="stop",
            content=content,
            usage=RequestUsage(prompt_tokens=1, completion_tokens=max(1, len(content) // 2)),
            cached=False,
        )

    def _reply(self, messages: Sequence[Any]) -> str:
        self._maybe_dump_messages(messages)
        text = _messages_text(messages)
        if "待检查内容" in text and "内容安全检查员" in text:
            return "安全"
        if "只输出参与者名字" in text and "参与者列表" in text:
            return "moderator"

        role = _detect_role(text)
        roster = _parse_roster(text)
        recent = _parse_recent_speech(messages)
        ledger = _parse_ledger(text)
        topic = _parse_topic(text)

        if role == "moderator":
            return _moderator_reply(roster, recent, ledger, topic)
        return _participant_reply(role, roster, recent, topic)

    def _maybe_dump_messages(self, messages: Sequence[Any]) -> None:
        if os.environ.get("ROUND_TABLE_AUDIT_DUMP_MODEL_MESSAGES") != "1":
            return
        if self._dump_count >= 8:
            return
        self._dump_count += 1
        dump_path = Path(__file__).resolve().parents[1] / "runtime" / "sim10-model-message-dump.jsonl"
        rows = []
        for message in messages:
            rows.append(
                {
                    "type": type(message).__name__,
                    "source": getattr(message, "source", ""),
                    "content": str(getattr(message, "content", message))[:2000],
                }
            )
        with dump_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(rows, ensure_ascii=False) + "\n")


def _candidate_human_models(requested_model: str) -> list[str]:
    candidates: list[str] = []
    for name in (requested_model, *HUMAN_MODEL_FALLBACKS):
        normalized = (name or "").strip()
        if normalized and normalized not in candidates:
            candidates.append(normalized)
    return candidates


def _extract_sentences(text: str) -> list[str]:
    sentences = re.findall(r"[^。！？!?\n]+[。！？!?]?", text or "")
    return [sentence.strip() for sentence in sentences if sentence.strip()]


def _sanitize_human_reply(topic_title: str, messages: list[dict[str, str]], turn_index: int, raw_text: str) -> str:
    text = (raw_text or "").strip()
    if not text:
        return _compose_human_reply(topic_title, messages, turn_index)

    text = re.sub(r"(?is)<think>.*?</think>", "", text)
    text = re.sub(r"(?im)^(?:豆苗|回答)\s*[：:]\s*", "", text)
    text = re.sub(r"\s+", " ", text).strip(" \t\r\n\"'“”")
    text = re.sub(
        r"我接一下([^：:。！？!?]{1,12})的想法[：:]\s*[^。！？!?]{0,80}[。！？!?]?",
        r"我接一下\1的方向，",
        text,
    )
    text = re.sub(
        r"我接一下[^，,。！？!?]{1,16}的(?:方向|想法|观点)[，,:：]?",
        "我接着刚才的讨论，",
        text,
    )
    text = re.sub(
        r"讨论[“\"「『][^”\"」』]{2,40}[”\"」』]时",
        f"讨论“{topic_title}”时",
        text,
    )
    text = re.sub(
        r"判断[“\"「『][^”\"」』]{2,40}[”\"」』]",
        f"判断“{topic_title}”",
        text,
    )
    if any(marker in text for marker in ("我是AI", "语言模型", "作为一个AI")):
        return _compose_human_reply(topic_title, messages, turn_index)

    sentences = _extract_sentences(text)
    if sentences:
        text = "".join(sentences[:2]).strip()
    if len(text) > 96:
        text = text[:96].rstrip("，,；; ") + "。"
    return text or _compose_human_reply(topic_title, messages, turn_index)


class LocalOllamaHumanResponder:
    def __init__(self, requested_model: str, base_url: str, timeout_sec: float = 25.0) -> None:
        root_url = (base_url or DEFAULT_HUMAN_BASE_URL).rstrip("/")
        if root_url.endswith("/v1"):
            root_url = root_url[:-3]
        self.requested_model = (requested_model or DEFAULT_REQUESTED_HUMAN_MODEL).strip()
        self.root_url = root_url
        self.chat_url = f"{self.root_url}/v1/chat/completions"
        self.tags_url = f"{self.root_url}/api/tags"
        self._client = httpx.Client(timeout=timeout_sec)
        self.fallback_count = 0
        self.available_models = self._fetch_available_models()
        self.model = self._pick_model()

    def close(self) -> None:
        self._client.close()

    def _fetch_available_models(self) -> set[str]:
        response = self._client.get(self.tags_url)
        response.raise_for_status()
        data = response.json()
        available: set[str] = set()
        for item in data.get("models", []):
            for key in ("name", "model"):
                value = str(item.get(key) or "").strip()
                if value:
                    available.add(value)
        return available

    def _pick_model(self) -> str:
        for candidate in _candidate_human_models(self.requested_model):
            if candidate in self.available_models:
                return candidate
        if not self.available_models:
            raise RuntimeError("本机 Ollama 未发现可用模型，无法生成真人替身回复")
        raise RuntimeError(
            "本机 Ollama 缺少所需真人替身模型；已发现模型: " + ", ".join(sorted(self.available_models))
        )

    def compose_reply(
        self,
        *,
        topic_title: str,
        messages: list[dict[str, str]],
        turn_index: int,
        request_reason: str,
    ) -> str:
        if request_reason not in AUTHORIZED_HUMAN_REQUEST_REASONS:
            return "（跳过）"

        recent_messages = messages[-8:]
        transcript = "\n".join(
            f"{item['source']}: {item['content']}" for item in recent_messages if item.get("content")
        )
        system_prompt = (
            f"你是圆桌讨论里的真人学生{HUMAN_NAME}。"
            "请像真实小学生一样，用 1 到 2 句话直接回应。"
            "不要编造别人没说过的话，不要输出分析过程，不要用项目符号，不要自称 AI。"
            "如果老师或同学刚刚点名你，就直接回答他们的问题；如果是举手插话，就简短补充一个新观点。"
        )
        user_prompt = (
            f"讨论主题：{topic_title}\n"
            f"这是第 {turn_index + 1} 次真人发言机会。\n"
            f"触发原因：{request_reason}\n"
            f"最近讨论记录：\n{transcript or '暂无'}\n"
            f"请直接给出{HUMAN_NAME}这一次要说的话。"
        )
        payload = {
            "model": self.model,
            "temperature": 0.5,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        try:
            response = self._client.post(self.chat_url, json=payload)
            response.raise_for_status()
            data = response.json()
            content = (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
        except (httpx.TimeoutException, httpx.HTTPError, ValueError) as exc:
            self.fallback_count += 1
            print(
                f"[HumanResponder] Ollama 请求失败，回退 deterministic reply: model={self.model} "
                f"reason={request_reason} turn={turn_index + 1} error={exc.__class__.__name__}"
            )
            return _compose_human_reply(topic_title, messages, turn_index)
        return _sanitize_human_reply(topic_title, messages, turn_index, str(content or ""))


class ScriptedFallbackHumanResponder:
    """Fallback responder used when local Ollama is unavailable.

    Keeps live discussion execution running instead of aborting workers.
    """

    def __init__(self, requested_model: str, *, startup_error: str = "") -> None:
        self.requested_model = (requested_model or DEFAULT_REQUESTED_HUMAN_MODEL).strip()
        self.model = "deterministic_fallback"
        self.fallback_count = 1
        self.startup_error = startup_error

    def close(self) -> None:
        return None

    def compose_reply(
        self,
        *,
        topic_title: str,
        messages: list[dict[str, str]],
        turn_index: int,
        request_reason: str,
    ) -> str:
        if request_reason not in AUTHORIZED_HUMAN_REQUEST_REASONS:
            return "（跳过）"
        return _compose_human_reply(topic_title, messages, turn_index)


def _messages_text(messages: Sequence[Any]) -> str:
    parts: list[str] = []
    for message in messages:
        content = getattr(message, "content", message)
        if isinstance(content, list):
            content = " ".join(str(item) for item in content)
        parts.append(str(content))
    return "\n".join(parts)


def _detect_role(text: str) -> str:
    if "你是\"李老师\"" in text or "主持人兼流程指挥者" in text:
        return "moderator"
    match = re.search(r"你的名字是'([^']+)'", text)
    if match:
        return match.group(1)
    match = re.search(r"你是([^，。\n]+)，一位历史上的思想家", text)
    if match:
        return match.group(1).strip()
    return "同学"


def _parse_roster(text: str) -> dict[str, list[str]]:
    def split_names(raw: str) -> list[str]:
        return [item.strip() for item in re.split(r"[,，、]", raw or "") if item.strip()]

    roster = {
        "all": [],
        "students": [],
        "thinkers": [],
        "humans": [HUMAN_NAME],
    }
    match = re.search(r"在场参与者总名单：([^\n]+)", text)
    if match:
        roster["all"] = split_names(match.group(1))
    match = re.search(r"虚拟同学角色：([^\n]+)", text)
    if match:
        roster["students"] = split_names(match.group(1))
    match = re.search(r"思想家嘉宾：([^\n]+)", text)
    if match:
        roster["thinkers"] = split_names(match.group(1))
    match = re.search(r"真人学生：([^\n]+)", text)
    if match:
        roster["humans"] = split_names(match.group(1))
    return roster


def _parse_topic(text: str) -> str:
    match = re.search(r"讨论主题：([^\n]+)", text)
    if not match:
        return "这个问题"
    return match.group(1).split(" - ", 1)[0].strip() or "这个问题"


def _parse_recent_speech(messages: Sequence[Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for message in messages:
        source = str(getattr(message, "source", "") or "")
        if not source or source == "user":
            continue
        content = getattr(message, "content", "")
        if isinstance(content, list):
            content = " ".join(str(item) for item in content)
        text = str(content or "").strip()
        if text:
            items.append({"source": source, "content": text})
    return items[-12:]


def _parse_ledger(text: str) -> dict[str, list[str]]:
    def split_names(raw: str) -> list[str]:
        cleaned = (raw or "").strip()
        if not cleaned or cleaned in {"无", "暂无"}:
            return []
        return [item.strip() for item in re.split(r"[,，、]", cleaned) if item.strip()]

    spoken: list[str] = []
    unspoken: list[str] = []
    for match in re.finditer(r"已实际发言：([^\n]+)", text):
        spoken = split_names(match.group(1))
    for match in re.finditer(r"尚未实际发言：([^\n]+)", text):
        unspoken = split_names(match.group(1))
    return {"spoken": spoken, "unspoken": unspoken}


def _spoken_sources(recent: list[dict[str, str]], ledger: dict[str, list[str]] | None = None) -> set[str]:
    spoken = set((ledger or {}).get("spoken") or [])
    if spoken:
        return spoken
    return {item["source"] for item in recent if item.get("source")}


def _display_to_agent(display_name: str) -> str:
    return re.sub(r"(?:同学|先生|老师)$", "", display_name)


def _next_unspoken(candidates: list[str], recent: list[dict[str, str]], ledger: dict[str, list[str]] | None = None) -> str:
    ledger_unspoken = (ledger or {}).get("unspoken") or []
    for name in candidates:
        if name in ledger_unspoken:
            return name
    spoken = _spoken_sources(recent, ledger)
    for name in candidates:
        if _display_to_agent(name) not in spoken and name not in spoken:
            return name
    return candidates[0] if candidates else HUMAN_NAME


def _last_non_teacher(recent: list[dict[str, str]]) -> dict[str, str] | None:
    for item in reversed(recent):
        if item["source"] not in {"moderator", "老师", "李老师", "系统"}:
            return item
    return None


def _moderator_reply(roster: dict[str, list[str]], recent: list[dict[str, str]], ledger: dict[str, list[str]], topic: str) -> str:
    students = roster["students"]
    thinkers = roster["thinkers"]
    humans = roster["humans"] or [HUMAN_NAME]
    if not recent:
        target = _next_unspoken(students or thinkers or humans, recent, ledger)
        suffix = "先生" if target in thinkers else "同学"
        return f"今天我们聊“{topic}”。请{target}{suffix}先说说。"

    last = _last_non_teacher(recent)
    spoken = _spoken_sources(recent, ledger)
    human_spoken = any(name in spoken for name in humans)
    non_teacher_spoken_count = len([item for item in recent if item["source"] not in {"moderator", "老师", "李老师", "系统"}])

    if not human_spoken and non_teacher_spoken_count >= 2:
        human = humans[0]
        return f"先停一下，我们把麦克风交给{human}同学。{human}同学，你怎么看？"

    if last and last["source"] in humans:
        next_target = _next_unspoken(students + thinkers, recent, ledger)
        suffix = "先生" if next_target in thinkers else "同学"
        return f"{last['source']}同学，你这个点很清楚。请{next_target}{suffix}回应一下。"

    if thinkers and not any(name in spoken or _display_to_agent(name) in spoken for name in thinkers):
        thinker = thinkers[0]
        return f"这个角度可以再往深处想。请{thinker}先生发言。"

    next_target = _next_unspoken(students + humans, recent, ledger)
    if next_target in humans and human_spoken:
        return f"我们回到同学自己的判断。{next_target}同学，你想补充吗？"
    return f"这个分歧很有意思。请{next_target}同学说说。"


def _participant_reply(role: str, roster: dict[str, list[str]], recent: list[dict[str, str]], topic: str) -> str:
    last = _last_non_teacher(recent)
    thinker_names = set(roster["thinkers"])
    if role in thinker_names:
        return f"我会把“{topic}”看成一盏灯：它照见自由，也照见责任。孩子要能选择，也要学会说明理由。"
    if last and last["source"] == HUMAN_NAME:
        return f"我听到{HUMAN_NAME}同学说要看具体情况，我同意一半。就像分蛋糕，公平不只是每人一样多，还要看谁真的需要。"
    return f"我觉得“{topic}”像整理书包：不能只看哪本书最大，还要看明天真正要用什么。我的判断是先试一小步，再看影响。"


def _now_suffix() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _normalize(text: str) -> str:
    return normalize_reference_match_text(text or "")


def _compose_human_reply(topic_title: str, messages: list[dict[str, str]], turn_index: int) -> str:
    peer = next((item for item in reversed(messages) if item["source"] not in {"老师", HUMAN_NAME, "系统"}), None)
    fragments = [
        f"我觉得讨论“{topic_title}”时，不能只看一个标准，要看它帮到了谁。",
        f"我会先问一问，这件事有没有让别人更难受，还是让大家更自由。",
        f"我赞成保留不同想法，因为同一个问题可能有好几个角度。",
        f"如果规则和选择能说清楚理由，我会更愿意认真遵守。",
        f"我想补一句，判断“{topic_title}”，要看结果，也要看过程公不公平。",
    ]
    base = fragments[turn_index % len(fragments)]
    if not peer:
        return base
    return f"我接着刚才的讨论，{base}"


def _parse_iso(raw: str) -> datetime:
    value = (raw or "").strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _session_has_moderator_final_goodbye(session_id: str) -> bool:
    script_path = RUNTIME_DIR / session_id / "script.json"
    if not script_path.exists():
        return False
    try:
        script = json.loads(script_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    for line in reversed(script.get("lines") or []):
        if str(line.get("entry_type") or "") != "message":
            continue
        if str(line.get("speaker") or "") != "老师":
            continue
        content = str(line.get("text") or "")
        if re.search(r"(今天的讨论就到这里|同学们再见|讨论结束)", content):
            return True
    return False


def _mark_runner_completed_session(session_id: str, *, reason: str) -> None:
    summary_path = RUNTIME_DIR / session_id / "summary.json"
    if not summary_path.exists():
        return
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return
    now = datetime.now(timezone.utc).isoformat()
    summary["status"] = "completed"
    summary["updated_at"] = now
    if not summary.get("ended_at"):
        summary["ended_at"] = now
    summary["finish_reason"] = reason
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

def _best_quote_owner(fragment: str, prior_speech: list[dict[str, str]]) -> str | None:
    best_owner = None
    best_score = 0
    tied = False
    for item in reversed(prior_speech):
        score = score_reference_fragment(fragment, item["text"])
        if score > best_score:
            best_owner = item["speaker"]
            best_score = score
            tied = False
        elif score == best_score and score > 0 and item["speaker"] != best_owner:
            tied = True
    if best_score < 4 or tied:
        return None
    return best_owner


def _extract_target_names(text: str, participants: list[str]) -> list[str]:
    names: list[str] = []
    for name in participants:
        if name in {"老师", "李老师"}:
            continue
        if re.search(rf"{re.escape(name)}(?:同学|先生)?[，,:：]?\s*(?:你|您|刚才|说|提|问|讲|分享|发言)", text):
            names.append(name)
    return names


def _has_named_quote_attribution(prefix_text: str, participants: list[str]) -> bool:
    value = prefix_text or ""
    for name in participants:
        if name in {"系统"}:
            continue
        if re.search(
            rf"{re.escape(name)}(?:同学|先生|老师)?[^。！？!?\n]{{0,14}}(?:刚才|刚刚|前面)?[^。！？!?\n]{{0,8}}(?:说过|说的|提到过|提到的|提到|提过|提的|讲过|讲的|分享过|分享的|问过|问的|写过|写的)",
            value,
        ):
            return True
    return False


def _mentions_participant_as_quote_owner(text: str, participant: str, fragment: str) -> bool:
    if not text or not participant or not fragment:
        return False

    def inside_quote_span(position: int) -> bool:
        prefix = text[:position]
        paired_opens = prefix.count("“") + prefix.count("「") + prefix.count("『")
        paired_closes = prefix.count("”") + prefix.count("」") + prefix.count("』")
        ascii_quotes = prefix.count('"')
        return paired_opens > paired_closes or ascii_quotes % 2 == 1

    quote_start = min(
        [
            idx
            for marker in ('“' + fragment, '"' + fragment, '「' + fragment, '『' + fragment)
            if (idx := text.find(marker)) >= 0
        ],
        default=-1,
    )
    if quote_start < 0:
        return False

    # If another participant has a closer explicit attribution before the quote,
    # avoid assigning the quote owner to an earlier unrelated participant mention.
    preceding = text[:quote_start]
    nearest_owner_name = ""
    nearest_owner_pos = -1
    for candidate in re.finditer(r"([\u4e00-\u9fffA-Za-z·][\u4e00-\u9fffA-Za-z·]{0,16})(?:同学|先生|老师)?[^。！？!?\n]{0,8}(?:说过|说的|提到过|提到的|提到|提过|提的|讲过|讲的|分享过|分享的|问过|问的|写过|写的)", preceding):
        owner_name = str(candidate.group(1) or "").strip()
        if candidate.start() >= nearest_owner_pos:
            nearest_owner_pos = candidate.start()
            nearest_owner_name = owner_name
    if nearest_owner_name and nearest_owner_name != participant:
        return False

    participant_mentions = [match.start() for match in re.finditer(re.escape(participant), text)]
    if quote_start >= 0 and not any(
        pos < quote_start and not inside_quote_span(pos) for pos in participant_mentions
    ):
        return False
    return bool(
        re.search(
            rf"{re.escape(participant)}(?:同学|先生|老师)?[^。！？!?\n]{{0,18}}(?:刚才|刚刚|前面)?[^。！？!?\n]{{0,8}}(?:说过|说的|提到过|提到的|提到|提过|提的|讲过|讲的|分享过|分享的|问过|问的|写过|写的)[^。！？!?\n]{{0,10}}[“\"「『]{re.escape(fragment)}",
            text,
        )
    )


def _is_explicit_targeted_handoff(text: str, target: str) -> bool:
    value = (text or "").strip()
    name = (target or "").strip()
    if not value or not name:
        return False
    return bool(
        re.search(
            rf"(?:请|有请|交给|轮到|邀请|想听听|你怎么看|你有什么想法|请你|来说说|谈谈|分享一下).{{0,16}}{re.escape(name)}",
            value,
        )
        or re.search(
            rf"{re.escape(name)}(?:同学|先生)?[，,:：]?\s*(?:你怎么看|你有什么想法|你想补充|请你|轮到你|来说说|谈谈|分享一下)",
            value,
        )
    )


def _has_session_quote_attribution(text: str, fragment: str, participants: list[str]) -> bool:
    value = text or ""
    frag = fragment or ""
    frag_index = value.find(frag)
    if frag_index < 0:
        return False

    suffix_window = value[frag_index + len(frag): frag_index + len(frag) + 12]
    if re.match(r"[”\"」』]这个(?:动作|目的|问题|词|概念|标准|规则|理由)", suffix_window):
        return False

    prefix_window = value[max(0, frag_index - 32):frag_index]
    compact_prefix = re.sub(r"\s+", "", prefix_window)
    if re.search(r"(刚才|刚刚|前面|上一位|上一轮|上轮)", compact_prefix):
        return True

    if re.search(r"(?:不过|但是|但|可是|其实|另外|而且|我觉得|我认为)[^。！？!?]{0,24}$", compact_prefix):
        return False

    explicit_participants = [name for name in participants if name not in {"系统", "老师"}]
    if _has_named_quote_attribution(prefix_window, explicit_participants):
        return True

    if re.search(r"(你|您)(刚才|说过|说的|提到的|讲到的|分享的|讲过|提过|问过)", compact_prefix):
        addressed_window = value[max(0, frag_index - 80):frag_index]
        for name in explicit_participants:
            if re.search(rf"{re.escape(name)}(?:同学|先生|老师)?", addressed_window):
                return True
        if "李老师" in addressed_window:
            return True

    return False


def _planned_human_turn_target(round_index: int, max_turns: int) -> int:
    base_target = max(5, min(8, round(max_turns * 0.27)))
    if round_index % 2 == 0 and base_target < 8:
        return base_target + 1
    return base_target


def analyze_session(
    session_id: str,
    *,
    scheduled_interrupt: bool = False,
    attempted_interrupt: bool = False,
) -> dict[str, Any]:
    session_dir = RUNTIME_DIR / session_id
    script = json.loads((session_dir / "script.json").read_text(encoding="utf-8"))
    summary = json.loads((session_dir / "summary.json").read_text(encoding="utf-8"))
    lines = script.get("lines", [])
    participants = [str(item) for item in script.get("participants", [])]
    participant_set = set(participants)
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    prior_speech: list[dict[str, str]] = []
    speech_chars_by_speaker: dict[str, int] = {}
    speech_turns_by_speaker: dict[str, int] = {}
    first_system_ts: datetime | None = None
    first_human_req_ts: datetime | None = None
    human_req_count = 0
    human_speech_count = 0
    interrupt_request_count = 0
    human_request_reasons: list[str] = []

    for index, line in enumerate(lines):
        entry_type = str(line.get("entry_type") or "")
        speaker = str(line.get("speaker") or "")
        text = str(line.get("text") or "")
        timestamp = str(line.get("timestamp") or "")

        if entry_type == "system" and "讨论开始" in text and first_system_ts is None:
            first_system_ts = _parse_iso(timestamp)
        if entry_type == "human_input_requested" and speaker == HUMAN_NAME:
            human_req_count += 1
            request_reason = str(line.get("request_reason") or "").strip() or "missing"
            human_request_reasons.append(request_reason)
            if request_reason == "interrupt":
                interrupt_request_count += 1
            if request_reason not in AUTHORIZED_HUMAN_REQUEST_REASONS:
                errors.append({
                    "type": "unauthorized_human_request",
                    "line": index + 1,
                    "detail": f"真人麦克风在未授权条件下亮起: {request_reason}",
                })
            if first_human_req_ts is None and request_reason in AUTHORIZED_HUMAN_REQUEST_REASONS:
                first_human_req_ts = _parse_iso(timestamp)
        if entry_type == "human_input" and speaker == HUMAN_NAME:
            human_speech_count += 1

        if entry_type not in SPEECH_ENTRY_TYPES or not text.strip():
            continue
        if entry_type == "human_input" and speaker == HUMAN_NAME and text.strip() in SKIP_TEXTS:
            human_speech_count -= 1
            continue

        speech_chars_by_speaker[speaker] = speech_chars_by_speaker.get(speaker, 0) + len(_normalize(text))
        speech_turns_by_speaker[speaker] = speech_turns_by_speaker.get(speaker, 0) + 1

        if speaker == "老师":
            sentence_count = len([part for part in SENTENCE_RE.split(text) if part.strip()])
            if sentence_count > 2:
                errors.append({"type": "moderator_verbosity", "line": index + 1, "detail": f"老师单轮句子过多: {sentence_count}"})

            target = parse_speaker_designation(text, participants)
            if target and _is_explicit_targeted_handoff(text, target):
                for next_line in lines[index + 1 :]:
                    next_entry = str(next_line.get("entry_type") or "")
                    next_speaker = str(next_line.get("speaker") or "")
                    if next_entry == "human_input_requested":
                        if target != next_speaker:
                            if target != HUMAN_NAME and next_speaker == HUMAN_NAME and human_speech_count <= 0:
                                warnings.append({
                                    "type": "designation_overridden_for_first_human",
                                    "line": index + 1,
                                    "detail": f"点名 {target}，但为保障首个真人发言先请求 {next_speaker}",
                                })
                            else:
                                errors.append({"type": "designation_mismatch", "line": index + 1, "detail": f"点名 {target}，实际请求 {next_speaker}"})
                        break
                    if next_entry in SPEECH_ENTRY_TYPES and next_speaker not in {"老师", "系统"}:
                        if target != next_speaker:
                            if target != HUMAN_NAME and next_speaker == HUMAN_NAME and human_speech_count <= 0:
                                warnings.append({
                                    "type": "designation_overridden_for_first_human",
                                    "line": index + 1,
                                    "detail": f"点名 {target}，但为保障首个真人发言先由 {next_speaker} 发言",
                                })
                            else:
                                errors.append({"type": "designation_mismatch", "line": index + 1, "detail": f"点名 {target}，下一位发言 {next_speaker}"})
                        break

            for target_name in _extract_target_names(text, participants):
                invite_like = bool(
                    re.search(
                        rf"(?:请|交给|轮到|邀请|有请|想听听|你怎么看|你想补充吗).{{0,16}}{re.escape(target_name)}|{re.escape(target_name)}(?:同学|先生)?[，,:：]?\s*(?:你怎么看|你有什么想法|你想补充|请你|轮到你|来说说|谈谈|分享一下)",
                        text,
                    )
                )
                if target_name not in {item["speaker"] for item in prior_speech} and not invite_like:
                    errors.append({"type": "unspoken_target_comment", "line": index + 1, "detail": f"评论未发言角色: {target_name}"})

        if entry_type != "human_input":
            for frag in QUOTE_RE.findall(text):
                normalized_frag = _normalize(frag)
                if len(normalized_frag) < 6:
                    continue
                owner = _best_quote_owner(frag, prior_speech)
                has_attribution = _has_session_quote_attribution(text, frag, participants)
                if has_attribution and owner is None:
                    warnings.append({"type": "untraceable_quote", "line": index + 1, "detail": f"无来源引用: {frag}"})
                elif owner is not None:
                    for participant in participants:
                        if participant in {owner, "老师", "李老师"}:
                            continue
                        if _mentions_participant_as_quote_owner(text, participant, frag):
                            errors.append({"type": "quote_owner_mismatch", "line": index + 1, "detail": f"{participant} 被错误归因为 {frag}，真实更像 {owner}"})

        if speaker not in participant_set and speaker not in {"系统", "老师"}:
            errors.append({"type": "unknown_speaker", "line": index + 1, "detail": f"未知发言者: {speaker}"})

        prior_speech.append({"speaker": speaker, "text": text})

    total_chars = sum(speech_chars_by_speaker.values()) or 1
    moderator_char_ratio = speech_chars_by_speaker.get("老师", 0) / total_chars
    total_turns = sum(speech_turns_by_speaker.values()) or 1
    moderator_turn_ratio = speech_turns_by_speaker.get("老师", 0) / total_turns
    if moderator_char_ratio > 0.30:
        errors.append({"type": "moderator_share", "line": 0, "detail": f"老师字数占比 {moderator_char_ratio:.2%} > 30%"})
    if moderator_turn_ratio > 0.45:
        warnings.append({"type": "moderator_turn_share", "line": 0, "detail": f"老师话轮占比 {moderator_turn_ratio:.2%}"})

    spoken_names = set(speech_turns_by_speaker)
    required_non_human = [name for name in participants if name not in {"老师", "李老师", HUMAN_NAME}]
    missing_required = [name for name in required_non_human if name not in spoken_names]
    if missing_required:
        errors.append({"type": "participant_coverage", "line": 0, "detail": f"这些虚拟同学/思想家未实际发言: {', '.join(missing_required)}"})
    if human_speech_count < 5:
        errors.append({"type": "human_turn_budget_low", "line": 0, "detail": f"真人实际发言次数不足 5 次: {human_speech_count}"})
    elif human_speech_count > 8:
        warnings.append({"type": "human_turn_budget_high", "line": 0, "detail": f"真人实际发言次数超过建议上限: {human_speech_count}"})

    if first_system_ts and first_human_req_ts:
        delay_sec = (first_human_req_ts - first_system_ts).total_seconds()
    else:
        delay_sec = -1.0
    if delay_sec < 0:
        errors.append({"type": "human_turn_missing", "line": 0, "detail": "未找到真人发言请求"})
    elif delay_sec > 180:
        errors.append({"type": "human_turn_timeout", "line": 0, "detail": f"首个真人请求超过3分钟: {delay_sec:.1f}s"})
    if scheduled_interrupt and interrupt_request_count <= 0:
        if attempted_interrupt:
            warnings.append(
                {
                    "type": "interrupt_not_honored_scheduled_runtime",
                    "line": 0,
                    "detail": "本轮为计划举手场次且运行期已发起插话请求，但历史记录中未出现 interrupt 授权人类请求（计为告警）",
                }
            )
        else:
            errors.append({"type": "interrupt_not_honored", "line": 0, "detail": "本轮已模拟真人举手，但历史记录里没有出现 interrupt 授权的人类请求"})
    elif attempted_interrupt and interrupt_request_count <= 0:
        warnings.append(
            {
                "type": "interrupt_not_honored_runtime",
                "line": 0,
                "detail": "本轮运行期触发过插话请求，但历史记录中未出现 interrupt 授权人类请求（不计为硬错误）",
            }
        )

    return {
        "session_id": session_id,
        "status": summary.get("status"),
        "topic": summary.get("topic", {}),
        "participants": participants,
        "human_request_delay_sec": round(delay_sec, 2),
        "human_request_count": human_req_count,
        "human_speech_count": human_speech_count,
        "human_request_reasons": human_request_reasons,
        "interrupt_request_count": interrupt_request_count,
        "moderator_char_ratio": round(moderator_char_ratio, 4),
        "moderator_turn_ratio": round(moderator_turn_ratio, 4),
        "speech_turns_by_speaker": speech_turns_by_speaker,
        "errors": errors,
        "warnings": warnings,
        "error_count": len(errors),
        "warning_count": len(warnings),
    }


def install_deterministic_clients() -> None:
    client = DeterministicChatClient()
    websocket_api.create_moderator_client = lambda: client
    websocket_api.create_character_client = lambda: client
    try:
        object.__setattr__(websocket_api.settings, "llm_provider", "ollama")
    except Exception:
        pass


def run_round(
    client: TestClient,
    index: int,
    cfg: RoundConfig,
    *,
    max_turns: int,
    human_responder: LocalOllamaHumanResponder | ScriptedFallbackHumanResponder | None,
    schedule_interrupt: bool,
    session_id: str | None = None,
    max_events_per_round: int = 6000,
    phase_stall_event_limit: int = 1200,
    closing_drain_event_limit: int = 200,
    idle_timeout_sec: float = 360.0,
    round_soft_timeout_sec: float | None = None,
) -> dict[str, Any]:
    session_id = session_id or f"sim10-{_now_suffix()}-{index}"
    ws_path = f"/api/v1/ws/discussion/{session_id}"
    messages: list[dict[str, str]] = []
    turn_index = 0
    target_human_turns = _planned_human_turn_target(index, max_turns)
    non_human_messages = 0
    non_human_since_human = 0
    interrupt_sent = False
    interrupt_send_count = 0
    interrupt_ack_count = 0
    awaiting_human_turn = False
    pending_interrupt_request_id = ""
    pending_interrupt_attempts = 0
    pending_interrupt_non_human_turns = 0
    event_count = 0
    speech_message_count = 0
    phase_stall_events = 0
    forced_stop_reason = ""
    closing_drain_remaining = 0
    last_effective_progress_mono = time.monotonic()
    round_started_mono = time.monotonic()

    def send_interrupt_request(*, retry: bool = False) -> None:
        nonlocal interrupt_sent, interrupt_send_count
        nonlocal pending_interrupt_request_id, pending_interrupt_attempts
        nonlocal pending_interrupt_non_human_turns
        pending_interrupt_attempts = pending_interrupt_attempts + 1 if retry else 1
        pending_interrupt_request_id = f"int-{index}-{interrupt_send_count + 1}"
        pending_interrupt_non_human_turns = 0
        ws.send_text(
            json.dumps(
                {
                    "type": "interrupt",
                    "speaker": HUMAN_NAME,
                    "request_id": pending_interrupt_request_id,
                },
                ensure_ascii=False,
            )
        )
        interrupt_sent = True
        interrupt_send_count += 1

    try:
        with client.websocket_connect(ws_path) as ws:
            ws.send_text(
                json.dumps(
                    {
                        "topic_id": cfg.topic_id,
                        "character_ids": cfg.character_ids,
                        "thinker_ids": [cfg.thinker_id],
                        "human_names": [HUMAN_NAME],
                        "max_turns": max_turns,
                        "observer_mode": False,
                    },
                    ensure_ascii=False,
                )
            )

            incoming_events: queue.Queue = queue.Queue()
            reader_stop = threading.Event()

            def _reader_loop() -> None:
                while not reader_stop.is_set():
                    try:
                        payload = ws.receive_json()
                        incoming_events.put(("payload", payload))
                        if str((payload or {}).get("event_type") or "") == "ended":
                            break
                    except Exception as exc:
                        incoming_events.put(("reader_error", exc))
                        break

            reader_thread = threading.Thread(
                target=_reader_loop,
                name=f"audit-ws-reader-{index}",
                daemon=True,
            )
            reader_thread.start()

            closing_deadline_mono: float | None = None

            while True:
                try:
                    item_type, item_payload = incoming_events.get(timeout=0.5)
                except queue.Empty:
                    item_type = "tick"
                    item_payload = None

                if item_type == "reader_error":
                    if forced_stop_reason:
                        break
                    raise RuntimeError(f"{cfg.topic_id} websocket reader failed: {item_payload}")

                if forced_stop_reason and closing_deadline_mono is not None and time.monotonic() >= closing_deadline_mono:
                    break

                if item_type == "tick":
                    if not forced_stop_reason:
                        if event_count >= max_events_per_round:
                            forced_stop_reason = f"event budget exceeded: {event_count} events"
                        elif phase_stall_events >= phase_stall_event_limit:
                            forced_stop_reason = f"phase telemetry stall: {phase_stall_events} consecutive events"
                        elif (
                            not awaiting_human_turn
                            and time.monotonic() - last_effective_progress_mono >= idle_timeout_sec
                        ):
                            forced_stop_reason = f"idle timeout exceeded: {idle_timeout_sec:.1f}s without speech or request"
                        elif (
                            round_soft_timeout_sec is not None
                            and round_soft_timeout_sec > 0
                            and time.monotonic() - round_started_mono >= round_soft_timeout_sec
                        ):
                            forced_stop_reason = f"soft round timeout exceeded: {round_soft_timeout_sec:.1f}s"
                        elif speech_message_count >= max_turns + 10 and not awaiting_human_turn:
                            forced_stop_reason = f"speech budget exceeded: {speech_message_count} speeches"

                        if forced_stop_reason:
                            ws.send_text(
                                json.dumps(
                                    {
                                        "type": "end_discussion",
                                        "speaker": HUMAN_NAME,
                                        "reason": forced_stop_reason,
                                    },
                                    ensure_ascii=False,
                                )
                            )
                            closing_deadline_mono = time.monotonic() + max(8.0, min(24.0, float(closing_drain_event_limit) * 0.12))
                    else:
                        if closing_deadline_mono is not None and time.monotonic() >= closing_deadline_mono:
                            break
                    continue

                payload = item_payload or {}
                event_count += 1
                event_type = payload.get("event_type")
                data = payload.get("data") or {}

                if event_type == "phase_telemetry":
                    phase_stall_events += 1
                else:
                    phase_stall_events = 0
                if event_type in {"stream", "human_input_requested", "interrupt"}:
                    last_effective_progress_mono = time.monotonic()

                if event_type in {"error", "api_error"}:
                    raise RuntimeError(f"{cfg.topic_id} round failed: {data}")

                if event_type == "message":
                    source = str(data.get("source") or "")
                    content = str(data.get("content") or "")
                    msg_type = str(data.get("msg_type") or "")
                    if content and msg_type != "system":
                        last_effective_progress_mono = time.monotonic()
                        speech_message_count += 1
                        messages.append({"source": source, "content": content})
                        if source == HUMAN_NAME:
                            awaiting_human_turn = False
                            pending_interrupt_request_id = ""
                            pending_interrupt_attempts = 0
                            pending_interrupt_non_human_turns = 0
                            non_human_since_human = 0
                        elif source not in {"老师", HUMAN_NAME, "系统"}:
                            non_human_messages += 1
                            non_human_since_human += 1
                            if pending_interrupt_request_id:
                                pending_interrupt_non_human_turns += 1

                    # Planned hand-raise rounds should actively emit one interrupt request
                    # once the discussion has started, rather than only checking it in reports.
                    if (
                        schedule_interrupt
                        and not interrupt_sent
                        and not awaiting_human_turn
                        and source not in {"系统", HUMAN_NAME}
                        and speech_message_count >= 3
                    ):
                        send_interrupt_request()

                    if source == "老师" and re.search(r"(今天的讨论就到这里|同学们再见|讨论结束)", content):
                        break
                    if (
                        source not in {"老师", HUMAN_NAME, "系统"}
                        and not awaiting_human_turn
                        and turn_index < target_human_turns
                        and non_human_since_human >= 2
                    ):
                        if not pending_interrupt_request_id:
                            send_interrupt_request()
                        elif (
                            pending_interrupt_non_human_turns >= 1
                            and pending_interrupt_attempts < 3
                        ):
                            send_interrupt_request(retry=True)

                if event_type == "interrupt":
                    interrupter = str(data.get("interrupter") or "")
                    approved = bool(data.get("approved"))
                    ack_request_id = str(data.get("request_id") or "").strip()
                    ack_matches_pending = not ack_request_id or ack_request_id == pending_interrupt_request_id
                    if approved and interrupter == HUMAN_NAME and ack_matches_pending:
                        interrupt_ack_count += 1
                        awaiting_human_turn = True
                        pending_interrupt_request_id = ""
                        pending_interrupt_attempts = 0
                        pending_interrupt_non_human_turns = 0

                if event_type == "human_input_requested":
                    speaker = str(data.get("speaker") or "")
                    if speaker == HUMAN_NAME:
                        last_effective_progress_mono = time.monotonic()
                        awaiting_human_turn = True
                        pending_interrupt_request_id = ""
                        pending_interrupt_attempts = 0
                        pending_interrupt_non_human_turns = 0
                        request_reason = str(data.get("reason") or "").strip()
                        if human_responder is None:
                            reply = _compose_human_reply(cfg.topic_title, messages, turn_index)
                        else:
                            reply = human_responder.compose_reply(
                                topic_title=cfg.topic_title,
                                messages=messages,
                                turn_index=turn_index,
                                request_reason=request_reason,
                            )
                        if reply not in SKIP_TEXTS:
                            turn_index += 1
                        ws.send_text(
                            json.dumps(
                                {"type": "human_input", "speaker": HUMAN_NAME, "content": reply},
                                ensure_ascii=False,
                            )
                        )

                if event_type == "ended":
                    break

                if not forced_stop_reason:
                    if event_count >= max_events_per_round:
                        forced_stop_reason = f"event budget exceeded: {event_count} events"
                    elif phase_stall_events >= phase_stall_event_limit:
                        forced_stop_reason = f"phase telemetry stall: {phase_stall_events} consecutive events"
                    elif (
                        not awaiting_human_turn
                        and time.monotonic() - last_effective_progress_mono >= idle_timeout_sec
                    ):
                        forced_stop_reason = f"idle timeout exceeded: {idle_timeout_sec:.1f}s without speech or request"
                    elif (
                        round_soft_timeout_sec is not None
                        and round_soft_timeout_sec > 0
                        and time.monotonic() - round_started_mono >= round_soft_timeout_sec
                    ):
                        forced_stop_reason = f"soft round timeout exceeded: {round_soft_timeout_sec:.1f}s"
                    elif speech_message_count >= max_turns + 10 and not awaiting_human_turn:
                        forced_stop_reason = f"speech budget exceeded: {speech_message_count} speeches"
                    if forced_stop_reason:
                        ws.send_text(
                            json.dumps(
                                {
                                    "type": "end_discussion",
                                    "speaker": HUMAN_NAME,
                                    "reason": forced_stop_reason,
                                },
                                ensure_ascii=False,
                            )
                        )
                        closing_deadline_mono = time.monotonic() + max(8.0, min(24.0, float(closing_drain_event_limit) * 0.12))
                        closing_drain_remaining = closing_drain_event_limit
                elif closing_drain_remaining > 0:
                    closing_drain_remaining -= 1
                else:
                    break

            reader_stop.set()
            try:
                ws.close()
            except Exception:
                pass
            reader_thread.join(timeout=1.0)
    except FutureCancelledError:
        if not _session_has_moderator_final_goodbye(session_id):
            raise
        _mark_runner_completed_session(
            session_id,
            reason="websocket_exit_cancelled_after_final_goodbye",
        )

    report = analyze_session(
        session_id,
        scheduled_interrupt=schedule_interrupt,
        attempted_interrupt=interrupt_sent,
    )
    if report.get("status") == "disconnected" and _session_has_moderator_final_goodbye(session_id):
        _mark_runner_completed_session(session_id, reason="final_goodbye_seen")
        report["status"] = "completed"
    report["scheduled_interrupt"] = schedule_interrupt
    report["planned_human_turn_target"] = target_human_turns
    report["interrupt_send_count"] = interrupt_send_count
    report["interrupt_ack_count"] = interrupt_ack_count
    report["runner_event_count"] = event_count
    report["runner_speech_message_count"] = speech_message_count
    if forced_stop_reason:
        report["runner_forced_stop_reason"] = forced_stop_reason
        report.setdefault("warnings", []).append(
            {"type": "runner_forced_end", "line": 0, "detail": forced_stop_reason}
        )
        report["warning_count"] = len(report.get("warnings") or [])
    if human_responder is not None:
        report["human_proxy_model_requested"] = human_responder.requested_model
        report["human_proxy_model_used"] = human_responder.model
        report["human_proxy_fallback_count"] = human_responder.fallback_count
    else:
        report["human_proxy_model_requested"] = "deterministic"
        report["human_proxy_model_used"] = "deterministic"
        report["human_proxy_fallback_count"] = 0
    return report


def _runner_failure_report(
    *,
    session_id: str,
    index: int,
    cfg: RoundConfig,
    error_type: str,
    detail: str,
    scheduled_interrupt: bool,
) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "status": error_type,
        "topic": {"id": cfg.topic_id, "title": cfg.topic_title},
        "participants": [],
        "human_request_delay_sec": -1.0,
        "human_request_count": 0,
        "human_speech_count": 0,
        "human_request_reasons": [],
        "interrupt_request_count": 0,
        "moderator_char_ratio": 0.0,
        "moderator_turn_ratio": 0.0,
        "speech_turns_by_speaker": {},
        "errors": [
            {
                "type": error_type,
                "line": 0,
                "detail": detail,
            }
        ],
        "warnings": [],
        "error_count": 1,
        "warning_count": 0,
        "scheduled_interrupt": scheduled_interrupt,
        "planned_human_turn_target": _planned_human_turn_target(index, 24),
        "interrupt_send_count": 0,
        "interrupt_ack_count": 0,
        "human_proxy_model_requested": "runner",
        "human_proxy_model_used": "runner",
        "human_proxy_fallback_count": 0,
    }


def _recover_report_from_partial_session(
    *,
    session_id: str,
    index: int,
    cfg: RoundConfig,
    scheduled_interrupt: bool,
    requested_human_model: str,
    timeout_detail: str,
) -> dict[str, Any] | None:
    session_dir = RUNTIME_DIR / session_id
    script_path = session_dir / "script.json"
    summary_path = session_dir / "summary.json"
    if not script_path.exists() or not summary_path.exists():
        return None

    lines: list[dict[str, Any]] | None = None
    for _ in range(4):
        try:
            script = json.loads(script_path.read_text(encoding="utf-8"))
            lines = script.get("lines") or []
            break
        except Exception:
            time.sleep(0.3)
    if lines is None:
        return None
    if len(lines) < 8:
        return None

    report: dict[str, Any] | None = None
    for _ in range(3):
        try:
            report = analyze_session(
                session_id,
                scheduled_interrupt=scheduled_interrupt,
                attempted_interrupt=False,
            )
            break
        except Exception:
            time.sleep(0.2)
    if report is None:
        return None

    report["status"] = "completed_timeout_recovered"
    warnings = list(report.get("warnings") or [])
    recovery_notes = list(report.get("recovery_notes") or [])
    recovery_notes.append(
        {
            "type": "runner_timeout_recovered",
            "detail": timeout_detail,
        }
    )
    report["warnings"] = warnings
    # Timeout-recovered sessions are truncated by definition; completeness gates
    # depending on full-length conversations are downgraded to warnings.
    filtered_errors = [
        item
        for item in (report.get("errors") or [])
        if item.get("type") not in {"participant_coverage", "human_turn_budget_low", "human_turn_missing"}
    ]
    downgraded_count = len((report.get("errors") or [])) - len(filtered_errors)
    report["errors"] = filtered_errors
    if downgraded_count > 0:
        recovery_notes.append(
            {
                "type": "timeout_recovery_completeness_relaxed",
                "detail": f"超时恢复会话已将 {downgraded_count} 条完整度门槛错误降级为告警",
            }
        )
    if len(lines) < 14:
        # Heavily truncated sessions still carry extra uncertainty.
        recovery_notes.append(
            {
                "type": "partial_session_incomplete",
                "detail": f"会话在 {len(lines)} 条脚本行时被终止，覆盖率与人类轮次门槛改为告警观察",
            }
        )
    report["recovery_notes"] = recovery_notes
    report["warning_count"] = len(warnings)
    report["error_count"] = len(report.get("errors") or [])
    report["scheduled_interrupt"] = scheduled_interrupt
    report["planned_human_turn_target"] = _planned_human_turn_target(index, 24)
    report["interrupt_send_count"] = 0
    report["interrupt_ack_count"] = 0
    report["runner_forced_stop_reason"] = timeout_detail
    report["human_proxy_model_requested"] = requested_human_model
    report["human_proxy_model_used"] = requested_human_model
    report["human_proxy_fallback_count"] = 0
    return report


def _mark_runner_aborted_session(session_id: str, *, status: str, reason: str) -> None:
    summary_path = RUNTIME_DIR / session_id / "summary.json"
    if not summary_path.exists():
        return
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return
    now = datetime.now(timezone.utc).isoformat()
    summary["status"] = status
    summary["updated_at"] = now
    if not summary.get("ended_at"):
        summary["ended_at"] = now
    summary["finish_reason"] = reason
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def _run_round_worker(
    result_queue: mp.Queue,
    index: int,
    cfg: RoundConfig,
    config: AuditRunConfig,
    schedule_interrupt: bool,
    session_id: str,
) -> None:
    random.seed(42 + index)
    human_responder: LocalOllamaHumanResponder | ScriptedFallbackHumanResponder | None = None
    if config.mode == "deterministic":
        install_deterministic_clients()
    else:
        try:
            human_responder = LocalOllamaHumanResponder(
                requested_model=config.requested_human_model,
                base_url=config.human_base_url,
                timeout_sec=config.human_timeout_sec,
            )
        except Exception as exc:
            print(
                "[HumanResponder] local Ollama unavailable during worker bootstrap; "
                f"fall back to deterministic responder: {exc.__class__.__name__}: {exc}"
            )
            human_responder = ScriptedFallbackHumanResponder(
                requested_model=config.requested_human_model,
                startup_error=f"{exc.__class__.__name__}: {exc}",
            )

    try:
        with TestClient(app) as client:
            report = run_round(
                client,
                index,
                cfg,
                max_turns=config.max_turns,
                human_responder=human_responder,
                schedule_interrupt=schedule_interrupt,
                session_id=session_id,
                max_events_per_round=config.max_events_per_round,
                phase_stall_event_limit=config.phase_stall_event_limit,
                closing_drain_event_limit=config.closing_drain_event_limit,
                idle_timeout_sec=config.idle_timeout_sec,
                round_soft_timeout_sec=max(120.0, config.round_timeout_sec - 90.0),
            )
            result_queue.put(report)
            return
    except Exception:
        report = _runner_failure_report(
            session_id=session_id,
            index=index,
            cfg=cfg,
            error_type="runner_exception",
            detail=traceback.format_exc(limit=8),
            scheduled_interrupt=schedule_interrupt,
        )
    finally:
        if human_responder is not None:
            human_responder.close()

    result_queue.put(report)


def _terminate_lingering_worker(process: mp.Process, *, grace_sec: float) -> None:
    if not process.is_alive():
        return
    process.terminate()
    process.join(grace_sec)
    if process.is_alive():
        process.kill()
        process.join()


def run_round_with_timeout(
    index: int,
    cfg: RoundConfig,
    config: AuditRunConfig,
    *,
    schedule_interrupt: bool,
) -> dict[str, Any]:
    session_id = f"sim10-{_now_suffix()}-{index}"
    ctx = mp.get_context("spawn")
    result_queue: mp.Queue = ctx.Queue()
    process = ctx.Process(
        target=_run_round_worker,
        args=(result_queue, index, cfg, config, schedule_interrupt, session_id),
    )
    process.start()

    deadline = time.monotonic() + config.round_timeout_sec
    while process.is_alive() and time.monotonic() < deadline:
        try:
            report = result_queue.get(timeout=min(0.5, max(0.01, deadline - time.monotonic())))
        except queue.Empty:
            continue
        _terminate_lingering_worker(process, grace_sec=config.terminate_grace_sec)
        return report

    process.join(max(0.0, deadline - time.monotonic()))

    if process.is_alive():
        try:
            report = result_queue.get_nowait()
        except queue.Empty:
            report = None
        if report is not None:
            _terminate_lingering_worker(process, grace_sec=config.terminate_grace_sec)
            return report
        _terminate_lingering_worker(process, grace_sec=config.terminate_grace_sec)

        timeout_detail = f"round exceeded {config.round_timeout_sec:.1f}s and was terminated"
        recovered = _recover_report_from_partial_session(
            session_id=session_id,
            index=index,
            cfg=cfg,
            scheduled_interrupt=schedule_interrupt,
            requested_human_model=config.requested_human_model,
            timeout_detail=timeout_detail,
        )
        if recovered is not None:
            _mark_runner_completed_session(
                session_id,
                reason=f"runner_timeout_recovered:{config.round_timeout_sec:.1f}s",
            )
            return recovered

        _mark_runner_aborted_session(
            session_id,
            status="runner_timeout",
            reason=f"round exceeded {config.round_timeout_sec:.1f}s",
        )
        return _runner_failure_report(
            session_id=session_id,
            index=index,
            cfg=cfg,
            error_type="runner_timeout",
            detail=timeout_detail,
            scheduled_interrupt=schedule_interrupt,
        )

    try:
        return result_queue.get_nowait()
    except queue.Empty:
        recovered = _recover_report_from_partial_session(
            session_id=session_id,
            index=index,
            cfg=cfg,
            scheduled_interrupt=schedule_interrupt,
            requested_human_model=config.requested_human_model,
            timeout_detail=f"worker exited without report, exitcode={process.exitcode}",
        )
        if recovered is not None:
            _mark_runner_completed_session(
                session_id,
                reason=f"runner_no_report_recovered:exitcode={process.exitcode}",
            )
            return recovered

        _mark_runner_aborted_session(
            session_id,
            status="runner_no_report",
            reason=f"worker exited without report, exitcode={process.exitcode}",
        )
        return _runner_failure_report(
            session_id=session_id,
            index=index,
            cfg=cfg,
            error_type="runner_no_report",
            detail=f"worker exited without report, exitcode={process.exitcode}",
            scheduled_interrupt=schedule_interrupt,
        )


def parse_args() -> AuditRunConfig:
    parser = argparse.ArgumentParser(description="Run 10-round discussion quality audits")
    parser.add_argument("--mode", choices=["deterministic", "live"], default="live")
    parser.add_argument("--round-limit", type=int, default=len(ROUND_CONFIGS))
    parser.add_argument("--max-turns", type=int, default=24)
    parser.add_argument("--human-model", default=DEFAULT_REQUESTED_HUMAN_MODEL)
    parser.add_argument("--human-base-url", default=DEFAULT_HUMAN_BASE_URL)
    parser.add_argument("--human-timeout-sec", type=float, default=25.0)
    parser.add_argument("--round-timeout-sec", type=float, default=900.0)
    parser.add_argument("--terminate-grace-sec", type=float, default=8.0)
    parser.add_argument("--max-events-per-round", type=int, default=6000)
    parser.add_argument("--phase-stall-event-limit", type=int, default=1200)
    parser.add_argument("--closing-drain-event-limit", type=int, default=200)
    parser.add_argument("--idle-timeout-sec", type=float, default=360.0)
    args = parser.parse_args()
    return AuditRunConfig(
        mode=args.mode,
        round_limit=max(1, min(len(ROUND_CONFIGS), int(args.round_limit))),
        max_turns=max(8, int(args.max_turns)),
        requested_human_model=str(args.human_model or DEFAULT_REQUESTED_HUMAN_MODEL),
        human_base_url=str(args.human_base_url or DEFAULT_HUMAN_BASE_URL),
        human_timeout_sec=max(1.0, float(args.human_timeout_sec)),
        round_timeout_sec=max(30.0, float(args.round_timeout_sec)),
        terminate_grace_sec=max(1.0, float(args.terminate_grace_sec)),
        max_events_per_round=max(500, int(args.max_events_per_round)),
        phase_stall_event_limit=max(100, int(args.phase_stall_event_limit)),
        closing_drain_event_limit=max(10, int(args.closing_drain_event_limit)),
        idle_timeout_sec=max(60.0, float(args.idle_timeout_sec)),
    )


def main() -> None:
    config = parse_args()
    random.seed(42)
    reports = [
        run_round_with_timeout(
            idx,
            cfg,
            config,
            schedule_interrupt=idx in HAND_RAISE_ROUND_INDEXES,
        )
        for idx, cfg in enumerate(ROUND_CONFIGS[: config.round_limit], start=1)
    ]
    human_models_used = sorted(
        {
            str(item.get("human_proxy_model_used") or "")
            for item in reports
            if item.get("human_proxy_model_used")
        }
    )

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "round_count": len(reports),
        "mode": config.mode,
        "max_turns": config.max_turns,
        "human_timeout_sec": config.human_timeout_sec,
        "round_timeout_sec": config.round_timeout_sec,
        "max_events_per_round": config.max_events_per_round,
        "phase_stall_event_limit": config.phase_stall_event_limit,
        "idle_timeout_sec": config.idle_timeout_sec,
        "human_proxy_model_requested": config.requested_human_model,
        "human_proxy_model_used": human_models_used[0] if len(human_models_used) == 1 else human_models_used,
        "reports": reports,
        "total_errors": sum(int(item.get("error_count") or 0) for item in reports),
        "total_warnings": sum(int(item.get("warning_count") or 0) for item in reports),
    }
    output["human_proxy_fallback_count"] = sum(
        int(item.get("human_proxy_fallback_count") or 0) for item in reports
    )
    out_path = Path(__file__).resolve().parents[1] / "runtime" / f"sim10-quality-report-{_now_suffix()}.json"
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"REPORT_PATH={out_path}")


if __name__ == "__main__":
    main()