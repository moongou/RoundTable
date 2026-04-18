"""思想家 API 端点"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.core.thinkers import get_all_domains, get_thinker, get_thinkers_by_domain, load_all_thinkers, search_thinkers

router = APIRouter(prefix="/thinkers", tags=["thinkers"])


@router.get("/")
async def list_thinkers(
    domain: str | None = Query(None, description="按领域过滤"),
    q: str | None = Query(None, description="搜索关键词"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
):
    """列出所有思想家，支持分页、搜索和领域过滤。"""
    if q:
        thinkers = search_thinkers(q, limit=size * page)
    elif domain:
        thinkers = get_thinkers_by_domain(domain)
    else:
        all_thinkers = load_all_thinkers()
        thinkers = list(all_thinkers.values())

    # 分页
    start = (page - 1) * size
    end = start + size
    page_thinkers = thinkers[start:end]

    return {
        "total": len(thinkers),
        "page": page,
        "size": size,
        "items": page_thinkers,
    }


@router.get("/domains")
async def list_domains():
    """列出所有领域及其思想家数量。"""
    return get_all_domains()


@router.get("/{thinker_id}")
async def get_thinker_detail(thinker_id: str):
    """获取单个思想家详情。"""
    thinker = get_thinker(thinker_id)
    if not thinker:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"思想家 '{thinker_id}' 不存在")
    return thinker