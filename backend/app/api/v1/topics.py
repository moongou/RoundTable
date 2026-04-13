"""话题相关 API"""

from fastapi import APIRouter, HTTPException

from app.core.topics import get_all_topics, get_topic_by_id
from app.models.session import Topic

router = APIRouter(prefix="/topics", tags=["topics"])


@router.get("/", response_model=list[Topic])
async def list_topics(category: str | None = None):
    """获取所有话题，可按分类筛选。"""
    topics = get_all_topics()
    if category:
        topics = [t for t in topics if t.category == category]
    return topics


@router.get("/{topic_id}", response_model=Topic)
async def get_topic(topic_id: str):
    """根据 ID 获取话题。"""
    topic = get_topic_by_id(topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail=f"话题 '{topic_id}' 不存在")
    return topic