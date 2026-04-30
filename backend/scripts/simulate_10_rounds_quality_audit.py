from __future__ import annotations

import argparse
import json
import os
import random
import re
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
        "我觉得要先试一小段时间，再看有没有真的帮助学习。",
        "我会担心自由太多以后，自己反而拖延，所以需要一个提醒办法。",
        "我赞成保留选择，但也要有同伴交流，不然容易只听见自己的想法。",
        "如果规则能一起商量，我会更愿意遵守，因为我知道它为什么存在。",
        "我想补一句，判断一件事好不好，要看它帮到了谁，也要看有没有伤到谁。",
    ]
    base = fragments[turn_index % len(fragments)]
    if not peer:
        return f"围绕“{topic_title}”，{base}"
    first_sentence = re.split(r"[。！？!?]", peer["content"], maxsplit=1)[0].strip()[:24]
    return f"我接一下{peer['source']}的想法：{first_sentence}。{base}"


def _parse_iso(raw: str) -> datetime:
    value = (raw or "").strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


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
            rf"{re.escape(name)}(?:同学|先生|老师)?[^。！？!?\n]{{0,14}}(?:刚才|刚刚|前面)?[^。！？!?\n]{{0,8}}(?:说过|说的|说|提到过|提到的|提到|提过|提的|提|讲过|讲的|讲|分享过|分享的|分享|问过|问的|问|写过|写的|写)",
            value,
        ):
            return True
    return False


def _mentions_participant_as_quote_owner(text: str, participant: str, fragment: str) -> bool:
    if not text or not participant or not fragment:
        return False
    return bool(
        re.search(
            rf"{re.escape(participant)}(?:同学|先生|老师)?[^。！？!?\n]{{0,18}}(?:刚才|刚刚|前面)?[^。！？!?\n]{{0,8}}(?:说过|说的|说|提到过|提到的|提到|提过|提的|提|讲过|讲的|讲|分享过|分享的|分享|问过|问的|问|写过|写的|写)[^。！？!?\n]{{0,10}}[“\"「『]{re.escape(fragment)}",
            text,
        )
    )


def _has_session_quote_attribution(text: str, fragment: str, participants: list[str]) -> bool:
    value = text or ""
    frag = fragment or ""
    frag_index = value.find(frag)
    if frag_index < 0:
        return False

    prefix_window = value[max(0, frag_index - 32):frag_index]
    compact_prefix = re.sub(r"\s+", "", prefix_window)
    if re.search(r"(刚才|刚刚|前面|上一位|上一轮|上轮)", compact_prefix):
        return True

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


def analyze_session(session_id: str, *, expected_interrupt: bool = False) -> dict[str, Any]:
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
            if target:
                for next_line in lines[index + 1 :]:
                    next_entry = str(next_line.get("entry_type") or "")
                    next_speaker = str(next_line.get("speaker") or "")
                    if next_entry == "human_input_requested":
                        if target != next_speaker:
                            errors.append({"type": "designation_mismatch", "line": index + 1, "detail": f"点名 {target}，实际请求 {next_speaker}"})
                        break
                    if next_entry in SPEECH_ENTRY_TYPES and next_speaker not in {"老师", "系统"}:
                        if target != next_speaker:
                            errors.append({"type": "designation_mismatch", "line": index + 1, "detail": f"点名 {target}，下一位发言 {next_speaker}"})
                        break

            for target_name in _extract_target_names(text, participants):
                invite_like = bool(
                    re.search(
                        rf"(?:请|交给|轮到|邀请|有请|你怎么看|你想补充吗).{{0,16}}{re.escape(target_name)}|{re.escape(target_name)}(?:同学|先生)?[，,:：]?\s*(?:你怎么看|你想补充|请你|轮到你)",
                        text,
                    )
                )
                if target_name not in {item["speaker"] for item in prior_speech} and not invite_like:
                    errors.append({"type": "unspoken_target_comment", "line": index + 1, "detail": f"评论未发言角色: {target_name}"})

        for frag in QUOTE_RE.findall(text):
            normalized_frag = _normalize(frag)
            if len(normalized_frag) < 4:
                continue
            owner = _best_quote_owner(frag, prior_speech)
            has_attribution = _has_session_quote_attribution(text, frag, participants)
            if has_attribution and owner is None:
                errors.append({"type": "untraceable_quote", "line": index + 1, "detail": f"无来源引用: {frag}"})
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
    if moderator_turn_ratio > 0.38:
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
    if expected_interrupt and interrupt_request_count <= 0:
        errors.append({"type": "interrupt_not_honored", "line": 0, "detail": "本轮已模拟真人举手，但历史记录里没有出现 interrupt 授权的人类请求"})

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
    human_responder: LocalOllamaHumanResponder | None,
    schedule_interrupt: bool,
) -> dict[str, Any]:
    session_id = f"sim10-{_now_suffix()}-{index}"
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

        while True:
            payload = ws.receive_json()
            event_type = payload.get("event_type")
            data = payload.get("data") or {}

            if event_type in {"error", "api_error"}:
                raise RuntimeError(f"{cfg.topic_id} round failed: {data}")

            if event_type == "message":
                source = str(data.get("source") or "")
                content = str(data.get("content") or "")
                msg_type = str(data.get("msg_type") or "")
                if content and msg_type != "system":
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

    report = analyze_session(session_id, expected_interrupt=interrupt_sent or schedule_interrupt)
    report["scheduled_interrupt"] = schedule_interrupt
    report["planned_human_turn_target"] = target_human_turns
    report["interrupt_send_count"] = interrupt_send_count
    report["interrupt_ack_count"] = interrupt_ack_count
    if human_responder is not None:
        report["human_proxy_model_requested"] = human_responder.requested_model
        report["human_proxy_model_used"] = human_responder.model
        report["human_proxy_fallback_count"] = human_responder.fallback_count
    else:
        report["human_proxy_model_requested"] = "deterministic"
        report["human_proxy_model_used"] = "deterministic"
        report["human_proxy_fallback_count"] = 0
    return report


def parse_args() -> AuditRunConfig:
    parser = argparse.ArgumentParser(description="Run 10-round discussion quality audits")
    parser.add_argument("--mode", choices=["deterministic", "live"], default="live")
    parser.add_argument("--round-limit", type=int, default=len(ROUND_CONFIGS))
    parser.add_argument("--max-turns", type=int, default=24)
    parser.add_argument("--human-model", default=DEFAULT_REQUESTED_HUMAN_MODEL)
    parser.add_argument("--human-base-url", default=DEFAULT_HUMAN_BASE_URL)
    parser.add_argument("--human-timeout-sec", type=float, default=25.0)
    args = parser.parse_args()
    return AuditRunConfig(
        mode=args.mode,
        round_limit=max(1, min(len(ROUND_CONFIGS), int(args.round_limit))),
        max_turns=max(8, int(args.max_turns)),
        requested_human_model=str(args.human_model or DEFAULT_REQUESTED_HUMAN_MODEL),
        human_base_url=str(args.human_base_url or DEFAULT_HUMAN_BASE_URL),
        human_timeout_sec=max(1.0, float(args.human_timeout_sec)),
    )


def main() -> None:
    config = parse_args()
    random.seed(42)
    human_responder: LocalOllamaHumanResponder | None = None
    if config.mode == "deterministic":
        install_deterministic_clients()
    else:
        human_responder = LocalOllamaHumanResponder(
            requested_model=config.requested_human_model,
            base_url=config.human_base_url,
            timeout_sec=config.human_timeout_sec,
        )

    try:
        with TestClient(app) as client:
            reports = [
                run_round(
                    client,
                    idx,
                    cfg,
                    max_turns=config.max_turns,
                    human_responder=human_responder,
                    schedule_interrupt=idx in HAND_RAISE_ROUND_INDEXES,
                )
                for idx, cfg in enumerate(ROUND_CONFIGS[: config.round_limit], start=1)
            ]
    finally:
        if human_responder is not None:
            human_responder.close()

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "round_count": len(reports),
        "mode": config.mode,
        "max_turns": config.max_turns,
        "human_timeout_sec": config.human_timeout_sec,
        "human_proxy_model_requested": config.requested_human_model,
        "human_proxy_model_used": human_responder.model if human_responder is not None else "deterministic",
        "reports": reports,
        "total_errors": sum(item["error_count"] for item in reports),
        "total_warnings": sum(item["warning_count"] for item in reports),
    }
    if human_responder is not None:
        output["human_proxy_fallback_count"] = human_responder.fallback_count
    out_path = Path(__file__).resolve().parents[1] / "runtime" / f"sim10-quality-report-{_now_suffix()}.json"
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"REPORT_PATH={out_path}")


if __name__ == "__main__":
    main()