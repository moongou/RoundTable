"""话题数据加载器

从 YAML 文件加载话题数据，支持分类过滤和搜索。
话题存储在 topics/ 目录的 YAML 文件中。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import yaml

from app.models.session import Topic

logger = logging.getLogger(__name__)

TOPICS_DIR = Path(__file__).parent.parent / "topics"
_topics_cache: list[dict] | None = None


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


def get_all_categories() -> list[dict]:
    """获取所有分类及其话题数量。"""
    all_data = _load_all_topic_data()

    category_names = {
        "science": "科学", "ethics": "伦理", "society": "社会",
        "literature": "文学", "health": "健康", "education": "教育",
        "tech": "科技", "life": "生活",
    }

    cat_count: dict[str, int] = {}
    for t in all_data:
        cat = t.get("category", "其他")
        cat_count[cat] = cat_count.get(cat, 0) + 1

    return [
        {"id": cat, "name_cn": category_names.get(cat, cat), "count": count}
        for cat, count in sorted(cat_count.items(), key=lambda x: -x[1])
    ]