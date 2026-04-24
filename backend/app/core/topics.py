"""话题数据加载器

从 YAML 文件加载话题数据，支持分类过滤和搜索。
话题存储在 topics/ 目录的 YAML 文件中。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

import yaml

from app.models.session import Topic

logger = logging.getLogger(__name__)

TOPICS_DIR = Path(__file__).parent.parent / "topics"
_topics_cache: list[dict] | None = None
FREE_TOPIC_CATEGORY_ID = "spark"
FREE_TOPIC_CATEGORY_NAME = "火花"

_FREE_TOPIC_LEADING_PATTERNS = [
    re.compile(r"^(我想(讨论|聊|说)(一下|一个)?(的话题)?[:：,， ]*)"),
    re.compile(r"^(我有一个(问题|想法|话题)(就是|想聊)?[:：,， ]*)"),
    re.compile(r"^(今天(我)?想(讨论|聊|说)(的是|一个)?[:：,， ]*)"),
    re.compile(r"^(最近我一直在想[:：,， ]*)"),
]
_FREE_TOPIC_FILLER_RE = re.compile(r"(嗯|呃|啊|那个|就是)(\s*\1)+")
_FREE_TOPIC_DEDUP_PUNCT_RE = re.compile(r"([，。！？；,.!?;])\1+")
_FREE_TOPIC_QUESTION_HINTS = (
    "该不该",
    "要不要",
    "应不应该",
    "能不能",
    "可不可以",
    "值不值得",
    "有没有必要",
    "是不是",
    "是否",
    "会不会",
)
_FREE_TOPIC_CLAUSE_BREAKS = (
    "因为",
    "比如",
    "例如",
    "然后",
    "就是",
    "如果",
    "但是",
    "所以",
)


def normalize_free_topic_text(text: str) -> str:
    """清理自由话题原始描述，保留语义但去掉口语噪声。"""
    value = (text or "").strip()
    if not value:
        return ""

    value = re.sub(r"\s+", " ", value)
    value = _FREE_TOPIC_DEDUP_PUNCT_RE.sub(r"\1", value)
    value = _FREE_TOPIC_FILLER_RE.sub(r"\1", value)
    value = re.sub(r"(我觉得){2,}", "我觉得", value)
    value = re.sub(r"(然后){2,}", "然后", value)
    value = value.strip(" ，,；;。！？!?")
    if not value:
        return ""
    if not re.search(r"[。！？!?]$", value):
        value += "。"
    return value


def build_free_topic_brief(text: str) -> dict[str, str]:
    """把冗长的自由描述压成一句可展示的话题标题，并保留整理后的原始描述。"""
    description = normalize_free_topic_text(text)
    if not description:
        return {
            "title": "",
            "description": "",
            "category": FREE_TOPIC_CATEGORY_ID,
        }

    title_source = description.rstrip("。！？!? ")
    for pattern in _FREE_TOPIC_LEADING_PATTERNS:
        title_source = pattern.sub("", title_source)
    title_source = title_source.strip(" ，,；;。！？!?") or description.rstrip("。！？!? ")

    sentences = [
        segment.strip(" ，,；;。！？!?")
        for segment in re.split(r"[。！？!?]", title_source)
        if segment.strip(" ，,；;。！？!?")
    ]
    candidate = sentences[0] if sentences else title_source
    if len(candidate) > 22:
        for marker in _FREE_TOPIC_CLAUSE_BREAKS:
            index = candidate.find(marker)
            if index >= 10:
                candidate = candidate[:index].rstrip(" ，,；;、")
                break

    question_like = any(marker in title_source for marker in _FREE_TOPIC_QUESTION_HINTS)
    if len(candidate) > 42:
        candidate = candidate[:42].rstrip(" ，,；;、") + ("？" if question_like else "…")
    elif not re.search(r"[。！？!?]$", candidate):
        candidate += "？" if question_like else "。"

    return {
        "title": candidate,
        "description": description,
        "category": FREE_TOPIC_CATEGORY_ID,
    }


def _load_all_topic_data() -> list[dict]:
    """加载所有话题原始数据（带缓存）。"""
    global _topics_cache

    if _topics_cache is not None:
        return _topics_cache

    _topics_cache = []

    if not TOPICS_DIR.exists():
        logger.warning(f"话题目录不存在: {TOPICS_DIR}")
        return _topics_cache

    for yaml_file in sorted(TOPICS_DIR.glob("*.yaml")):
        try:
            with open(yaml_file, encoding="utf-8") as f:
                data = yaml.safe_load(f)

            for topic in data.get("topics", []):
                _topics_cache.append(topic)
        except Exception as e:
            logger.error(f"加载话题文件失败 {yaml_file}: {e}")

    logger.info(f"加载了 {len(_topics_cache)} 个话题")
    return _topics_cache


def get_all_topics(category: str | None = None) -> list[Topic]:
    """获取所有话题，可选按分类过滤。"""
    all_data = _load_all_topic_data()

    if category:
        all_data = [t for t in all_data if t.get("category") == category]

    topics = []
    for t in all_data:
        topics.append(Topic(
            id=t.get("id", ""),
            title=t.get("title", ""),
            description=t.get("description", ""),
            category=t.get("category", ""),
            age_range=t.get("age_range", "8-12"),
            guide_questions=t.get("guide_questions", []),
            tags=t.get("tags", []),
            story=t.get("story", ""),
            story_source=t.get("story_source", ""),
            suggested_thinkers=t.get("suggested_thinkers", []),
        ))

    return topics


def get_topic_by_id(topic_id: str) -> Topic | None:
    """根据 ID 获取话题。"""
    for topic in get_all_topics():
        if topic.id == topic_id:
            return topic
    return None


def get_topic_categories() -> list[dict]:
    """获取所有话题分类及其统计信息。"""
    all_data = _load_all_topic_data()
    categories: dict[str, dict] = {}

    # 分类中文名映射
    category_names = {}
    for yaml_file in sorted(TOPICS_DIR.glob("*.yaml")):
        try:
            with open(yaml_file, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            cat_id = data.get("category", "")
            cat_cn = data.get("category_cn", cat_id)
            if cat_id:
                category_names[cat_id] = cat_cn
        except Exception:
            pass

    for t in all_data:
        cat = t.get("category", "unknown")
        if cat not in categories:
            categories[cat] = {
                "id": cat,
                "name": category_names.get(cat, cat),
                "count": 0,
            }
        categories[cat]["count"] += 1

    return list(categories.values())


def get_all_categories() -> list[dict]:
    """获取所有分类及其话题数量。"""
    all_data = _load_all_topic_data()

    category_names = {
        "science": "科学", "ethics": "伦理", "society": "社会",
        "literature": "文学", "health": "健康", "education": "教育",
        "tech": "科技", "life": "生活", FREE_TOPIC_CATEGORY_ID: FREE_TOPIC_CATEGORY_NAME,
    }

    cat_count: dict[str, int] = {}
    for t in all_data:
        cat = t.get("category", "其他")
        cat_count[cat] = cat_count.get(cat, 0) + 1

    return [
        {"id": cat, "name_cn": category_names.get(cat, cat), "count": count}
        for cat, count in sorted(cat_count.items(), key=lambda x: -x[1])
    ]