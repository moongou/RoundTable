from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.main import app

HUMAN_NAME = "豆苗"
RUNTIME_DIR = Path(__file__).resolve().parents[2] / "backend" / "runtime" / "meeting_history"


@dataclass(frozen=True)
class RoundConfig:
    topic_id: str
    topic_title: str
    character_ids: list[str]
    thinker_id: str


ROUND_CONFIGS: list[RoundConfig] = [
    RoundConfig("education-002", "标准答案的危险", ["explorer", "skeptic", "empath"], "socrates"),
    RoundConfig("ethics-002", "说谎永远不对吗", ["questioner", "rationalist", "peacemaker"], "kant"),
    RoundConfig("life-008", "什么是勇气", ["optimist", "storyteller", "pragmatist", "skeptic"], "nietzsche"),
    RoundConfig("tech-004", "AI 会抢走工作吗", ["innovator", "skeptic", "rationalist"], "adam-smith"),
    RoundConfig("society-005", "规则越多越好吗", ["questioner", "pragmatist", "explorer", "comedian"], "confucius"),
]

QUOTE_RE = re.compile(r"[“\"「『]([^”\"」』]{2,80})[”\"」』]")
VOCATIVE_RE = re.compile(r"(小[\u4e00-\u9fff]{1,2}(?:同学|哥哥|姐姐|先生)|豆苗同学|豆苗)")
DESIGNATE_RE = re.compile(r"请\s*([\u4e00-\u9fffA-Za-z0-9_]+)(?:同学|先生)?\s*发言")
SENTENCE_RE = re.compile(r"[。！？!?]")


def _now_suffix() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", (text or "")).strip().lower()


def _compose_human_reply(topic_title: str, messages: list[dict[str, str]], turn_index: int) -> str:
    last_peer = ""
    for item in reversed(messages):
        if item["source"] not in {"老师", HUMAN_NAME, "系统"}:
            last_peer = item["content"]
            break

    fragments = [
        "我同意刚才那个角度，但我想补一句：演员要先学会观察人，再学会表达。",
        "如果让我试，我会先从一个很短的角色开始，先练眼神和停顿，不急着追求完美。",
        "我觉得勇气和耐心要一起练，不然只敢上台却撑不住，或者只会准备却不敢演。",
        "我会把角色拆成三个问题：他怕什么、他想要什么、他现在在隐藏什么。",
        "刚才大家都提到投入，我补充一点：投入以后也要会抽离，演完要回到自己。",
    ]
    base = fragments[turn_index % len(fragments)]

    if last_peer:
        quoted = ""
        match = QUOTE_RE.search(last_peer)
        if match:
            quoted = match.group(1)
        if quoted:
            return f"我听到刚才有人提到“{quoted}”，这个点我认同。{base}"
        short_peer = re.split(r"[。！？!?]", last_peer, maxsplit=1)[0].strip()
        short_peer = short_peer[:22]
        return f"我接一下刚才的想法：{short_peer}。{base}"

    return f"围绕“{topic_title}”，{base}"


def _parse_iso(s: str) -> datetime:
    raw = (s or "").strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    return datetime.fromisoformat(raw)


def _analyze_session(session_id: str) -> dict[str, Any]:
    session_dir = RUNTIME_DIR / session_id
    script = json.loads((session_dir / "script.json").read_text(encoding="utf-8"))
    summary = json.loads((session_dir / "summary.json").read_text(encoding="utf-8"))
    lines = script.get("lines", [])

    participants = set(script.get("participants", []))
    errors: list[dict[str, Any]] = []

    first_system_ts: datetime | None = None
    first_human_req_ts: datetime | None = None
    human_req_count = 0
    human_speech_count = 0

    messages_so_far: list[dict[str, str]] = []

    for index, line in enumerate(lines):
        entry_type = str(line.get("entry_type") or "")
        speaker = str(line.get("speaker") or "")
        text = str(line.get("text") or "")
        timestamp = str(line.get("timestamp") or "")

        if entry_type == "system" and "讨论开始" in text and not first_system_ts:
            first_system_ts = _parse_iso(timestamp)

        if entry_type == "human_input_requested" and speaker == HUMAN_NAME:
            human_req_count += 1
            if not first_human_req_ts:
                first_human_req_ts = _parse_iso(timestamp)

        if entry_type == "human_input" and speaker == HUMAN_NAME:
            human_speech_count += 1

        if entry_type == "message":
            messages_so_far.append({"source": speaker, "content": text})

            if speaker == "老师":
                sentence_count = len([s for s in SENTENCE_RE.split(text) if s.strip()])
                if sentence_count > 4:
                    errors.append({
                        "type": "verbosity",
                        "line": index + 1,
                        "detail": f"老师句子过多({sentence_count})",
                    })

                # 指向错误：老师点名不存在参与者
                for match in VOCATIVE_RE.findall(text):
                    name = match.replace("同学", "").replace("哥哥", "").replace("姐姐", "").replace("先生", "")
                    if name and name not in participants and name not in {"老师", "李老师"}:
                        errors.append({
                            "type": "wrong_target",
                            "line": index + 1,
                            "detail": f"点名对象不存在: {match}",
                        })

                # 引用错误：有同学提到“...”但找不到来源
                if "有同学" in text and ("提到“" in text or "说“" in text):
                    prev_non_teacher = "\n".join(
                        m["content"] for m in messages_so_far[:-1] if m["source"] not in {"老师", "系统"}
                    )
                    for frag in QUOTE_RE.findall(text):
                        if len(_normalize(frag)) >= 4 and _normalize(frag) not in _normalize(prev_non_teacher):
                            errors.append({
                                "type": "quote_mismatch",
                                "line": index + 1,
                                "detail": f"疑似无来源引用: {frag}",
                            })

                # 点名与请求错位：本句明确请某人发言，但紧随其后 request 不是该人
                dm = DESIGNATE_RE.search(text)
                if dm:
                    target = dm.group(1)
                    for next_line in lines[index + 1 : index + 4]:
                        if str(next_line.get("entry_type") or "") == "human_input_requested":
                            req_speaker = str(next_line.get("speaker") or "")
                            if target != req_speaker and target != req_speaker.replace("同学", ""):
                                errors.append({
                                    "type": "designation_mismatch",
                                    "line": index + 1,
                                    "detail": f"点名{target}，实际请求{req_speaker}",
                                })
                            break

    if first_system_ts and first_human_req_ts:
        delay_sec = (first_human_req_ts - first_system_ts).total_seconds()
    else:
        delay_sec = -1.0

    if delay_sec < 0:
        errors.append({"type": "human_turn_missing", "line": 0, "detail": "未找到人类发言请求"})
    elif delay_sec > 180:
        errors.append({
            "type": "human_turn_timeout",
            "line": 0,
            "detail": f"首个人类请求超过3分钟: {delay_sec:.1f}s",
        })

    last_teacher = ""
    for line in reversed(lines):
        if str(line.get("entry_type") or "") == "message" and str(line.get("speaker") or "") == "老师":
            last_teacher = str(line.get("text") or "")
            break
    if last_teacher and "再见" not in _normalize(last_teacher):
        errors.append({"type": "missing_goodbye", "line": 0, "detail": "结束前老师未明确再见"})

    return {
        "session_id": session_id,
        "status": summary.get("status"),
        "topic": summary.get("topic", {}),
        "participants": summary.get("participants", []),
        "event_count": summary.get("event_count", 0),
        "human_request_delay_sec": round(delay_sec, 2),
        "human_request_count": human_req_count,
        "human_speech_count": human_speech_count,
        "errors": errors,
        "error_count": len(errors),
    }


def run_round(client: TestClient, index: int, cfg: RoundConfig) -> dict[str, Any]:
    session_id = f"sim5-{_now_suffix()}-{index}"
    ws_path = f"/api/v1/ws/discussion/{session_id}"
    messages: list[dict[str, str]] = []
    turn_index = 0

    with client.websocket_connect(ws_path) as ws:
        ws.send_text(
            json.dumps(
                {
                    "topic_id": cfg.topic_id,
                    "character_ids": cfg.character_ids,
                    "thinker_ids": [cfg.thinker_id],
                    "human_names": [HUMAN_NAME],
                    "max_turns": 20,
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

            if event_type == "human_input_requested":
                speaker = str(data.get("speaker") or "")
                if speaker == HUMAN_NAME:
                    reply = _compose_human_reply(cfg.topic_title, messages, turn_index)
                    turn_index += 1
                    ws.send_text(
                        json.dumps(
                            {
                                "type": "human_input",
                                "speaker": HUMAN_NAME,
                                "content": reply,
                            },
                            ensure_ascii=False,
                        )
                    )

            if event_type == "ended":
                break

    return _analyze_session(session_id)


def main() -> None:
    random.seed(42)
    client = TestClient(app)
    reports: list[dict[str, Any]] = []

    for idx, cfg in enumerate(ROUND_CONFIGS, start=1):
        report = run_round(client, idx, cfg)
        reports.append(report)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "round_count": len(reports),
        "reports": reports,
        "total_errors": sum(item["error_count"] for item in reports),
    }

    out_path = Path(__file__).resolve().parents[1] / "runtime" / f"sim5-report-{_now_suffix()}.json"
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"REPORT_PATH={out_path}")


if __name__ == "__main__":
    main()
