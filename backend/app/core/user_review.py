from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping

from app.core.golden_quotes import collect_quote_material

MIN_REVIEW_MESSAGE_COUNT = 4
MIN_REVIEW_SPEAKER_COUNT = 3
MAX_REVIEW_TRANSCRIPT_MESSAGES = 24


def collect_user_review_material(
    messages: Iterable[Mapping[str, object]],
    *,
    human_name: str,
) -> list[dict[str, str]]:
    normalized_human = (human_name or '').strip()
    if not normalized_human:
        return []

    material = collect_quote_material(messages)
    filtered: list[dict[str, str]] = []
    for message in material:
        source = str(message.get('source', '') or '').strip()
        content = str(message.get('content', '') or '').strip()
        if not source or not content or content in {'（跳过）', '(跳过)', '跳过'}:
            continue
        filtered.append(
            {
                'source': source,
                'content': content,
                'type': str(message.get('type', 'text') or 'text').strip() or 'text',
            }
        )
    return filtered[-MAX_REVIEW_TRANSCRIPT_MESSAGES:]


def has_enough_user_review_material(
    messages: Iterable[Mapping[str, object]],
    *,
    human_name: str,
) -> bool:
    normalized_human = (human_name or '').strip()
    if not normalized_human:
        return False

    material = collect_user_review_material(messages, human_name=normalized_human)
    if len(material) < MIN_REVIEW_MESSAGE_COUNT:
        return False

    speakers = {message['source'] for message in material}
    if len(speakers) < MIN_REVIEW_SPEAKER_COUNT:
        return False

    return any(message['source'] == normalized_human for message in material)


def build_user_review_prompt(
    topic_title: str,
    human_name: str,
    messages: Iterable[Mapping[str, object]],
) -> str:
    normalized_human = (human_name or '').strip() or '这位同学'
    material = collect_user_review_material(messages, human_name=normalized_human)
    transcript = '\n'.join(
        f"{index + 1}. {'[真人学生]' if message['source'] == normalized_human else '[讨论成员]'} {message['source']}：{message['content'][:180]}"
        for index, message in enumerate(material)
    )

    return f"""你是一位儿童圆桌讨论结束后的主持老师。

请根据下面这段讨论记录，给真人学生“{normalized_human}”写一段会后点评。

要求：
- 只点评这位真人学生的参与表现，不要点评其他角色。
- 全文控制在 100 到 200 个汉字之间。
- 语气温和、具体、可信，像老师在讨论结束后当面对孩子说的话。
- 必须点出 1 到 2 个真实表现亮点，再给出 1 个可以立刻尝试的小建议。
- 必须紧扣讨论中已经出现过的具体表达、追问、比喻或转折，不要编造新情节。
- 使用第二人称“你”，不要写标题，不要编号，不要分点。
- 不要写成空泛夸奖，也不要写成说教总结。
- 如果材料不足以支持点评，请返回空字符串。

请只返回 JSON 对象，格式如下：
{{"review": "..."}}

讨论主题：{topic_title.strip() or '未命名话题'}
讨论记录：
{transcript}
"""


def parse_user_review_response(raw: str) -> str:
    text = (raw or '').strip()
    if not text:
        return ''

    candidates: list[str] = []
    code_blocks = re.findall(r'```(?:json)?\s*(.*?)```', text, flags=re.DOTALL | re.IGNORECASE)
    candidates.extend(block.strip() for block in code_blocks if block.strip())

    first_object = _slice_balanced_json(text, '{', '}')
    if first_object:
        candidates.append(first_object)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        normalized = _extract_review(parsed)
        if normalized:
            return normalized

    return _normalize_review_text(text)


def _extract_review(payload: object) -> str:
    if isinstance(payload, dict):
        for key in ('review', 'comment', 'feedback'):
            value = payload.get(key)
            if isinstance(value, str):
                normalized = _normalize_review_text(value)
                if normalized:
                    return normalized
        return ''

    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, str):
                normalized = _normalize_review_text(item)
                if normalized:
                    return normalized
        return ''

    if isinstance(payload, str):
        return _normalize_review_text(payload)
    return ''


def _normalize_review_text(raw: str) -> str:
    text = (raw or '').strip()
    if not text:
        return ''

    text = text.strip('"“”\'')
    text = re.sub(r'^(?:老师点评|点评|评语|会后点评)\s*[：:]\s*', '', text)
    text = re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])', '', text)
    text = re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[，。！？；：])', '', text)
    text = re.sub(r'(?<=[，。！？；：])\s+(?=[\u4e00-\u9fff])', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) > 220:
        text = _soft_trim_review(text, target=200)
    return text


def _soft_trim_review(text: str, *, target: int) -> str:
    if len(text) <= target:
        return text

    search_end = min(len(text), target + 20)
    for index in range(target, search_end):
        if text[index] in '。！？!?':
            return text[: index + 1].strip()

    trimmed = text[:target].rstrip('，,、；;：: ')
    if trimmed and trimmed[-1] not in '。！？!?':
        trimmed += '。'
    return trimmed


def _slice_balanced_json(text: str, open_char: str, close_char: str) -> str | None:
    start = text.find(open_char)
    if start < 0:
        return None
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return None