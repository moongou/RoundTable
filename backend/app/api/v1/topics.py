"""话题相关 API"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.topics import (
    FREE_TOPIC_CATEGORY_ID,
    build_free_topic_brief,
    get_all_topics,
    get_topic_by_id,
    get_topic_categories,
)
from app.models.session import Topic

router = APIRouter(prefix="/topics", tags=["topics"])


class FreeTopicRefineRequest(BaseModel):
    text: str


class FreeTopicRefineResponse(BaseModel):
    title: str
    description: str
    category: str = FREE_TOPIC_CATEGORY_ID


@router.get("/", response_model=list[Topic])
async def list_topics(category: str | None = None):
    """获取所有话题，可按分类筛选。"""
    topics = get_all_topics()
    if category:
        topics = [t for t in topics if t.category == category]
    return topics


@router.get("/categories/")
async def list_categories():
    """获取所有话题分类及其话题数量。"""
    return get_topic_categories()


@router.post("/refine-free-topic", response_model=FreeTopicRefineResponse)
async def refine_free_topic(request: FreeTopicRefineRequest):
    """把自由话题的长描述提炼成一句适合展示的标题。"""
    result = build_free_topic_brief(request.text)
    return FreeTopicRefineResponse(**result)


@router.get("/{topic_id}", response_model=Topic)
async def get_topic(topic_id: str):
    """根据 ID 获取话题。"""
    topic = get_topic_by_id(topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail=f"话题 '{topic_id}' 不存在")
    return topic