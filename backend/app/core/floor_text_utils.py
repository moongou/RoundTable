"""FloorManager 文本处理工具函数。"""

from __future__ import annotations

import re

_NON_SUBSTANTIVE_TURNS = {
    "（跳过）",
    "(跳过)",
    "跳过",
    "（旁听）",
    "(旁听)",
    "旁听",
    "（我先听听大家的意见）",
    "(我先听听大家的意见)",
    "我先听听大家的意见",
}


def is_non_substantive_turn(content: str) -> bool:
    normalized = re.sub(r"\s+", "", (content or "").strip())
    return normalized in _NON_SUBSTANTIVE_TURNS


def extract_core_viewpoint(content: str) -> str:
    text = re.sub(r"（[^）]{0,24}）", "", content or "")
    text = re.sub(r"\([^)]{0,24}\)", "", text)
    text = re.sub(r"\s+", " ", text).strip(" ，,。！？!?；;:：")
    if not text:
        return ""
    first_sentence = re.split(r"[。！？!?；;]", text, maxsplit=1)[0].strip()
    summary = first_sentence or text
    if len(summary) > 42:
        summary = summary[:42].rstrip("，,；;、 ") + "…"
    return summary


def extract_reference_quote(content: str) -> str:
    text = re.sub(r"（[^）]{0,24}）", "", content or "")
    text = re.sub(r"\([^)]{0,24}\)", "", text)
    text = re.sub(r"\s+", " ", text).strip(" ，,。！？!?；;:：")
    if not text:
        return ""
    if len(text) > 96:
        text = text[:96].rstrip("，,；;、 ") + "…"
    return text


def normalize_reference_match_text(text: str) -> str:
    value = re.sub(r"（[^）]{0,24}）", "", text or "")
    value = re.sub(r"\([^)]{0,24}\)", "", value)
    value = value.lower()
    value = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value)
    return value.strip()


def build_reference_bigrams(text: str) -> set[str]:
    if len(text) < 2:
        return set()
    return {text[index : index + 2] for index in range(len(text) - 1)}


def score_reference_fragment(fragment: str, candidate: str) -> int:
    fragment_norm = normalize_reference_match_text(fragment)
    candidate_norm = normalize_reference_match_text(candidate)
    if not fragment_norm or not candidate_norm:
        return 0
    if len(fragment_norm) <= 2:
        return 8 if fragment_norm in candidate_norm else 0

    score = 0
    if fragment_norm in candidate_norm:
        score += max(8, min(len(fragment_norm), 16))
    score += len(set(fragment_norm) & set(candidate_norm))
    score += len(build_reference_bigrams(fragment_norm) & build_reference_bigrams(candidate_norm)) * 2
    return score
