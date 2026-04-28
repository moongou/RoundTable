"""思想家 API 端点"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Query

from app.core.thinkers import get_all_domains, get_thinker, get_thinkers_by_domain, load_all_thinkers, search_thinkers

router = APIRouter(prefix="/thinkers", tags=["thinkers"])


# 话题分类 → 优先匹配的思想家领域。
# 用于"思想家推荐"算法的第一阶段：先按主题大类圈出 2-3 个最契合的思想流派，
# 再叠加文本相似度细排。
_CATEGORY_TO_DOMAINS: dict[str, list[str]] = {
    "education": ["education", "philosophy", "psychology"],
    "ethics": ["philosophy", "religion", "sociology"],
    "society": ["sociology", "politics", "economics", "philosophy"],
    "tech": ["technology", "business", "economics"],
    "science": ["technology", "philosophy"],
    "environment": ["science", "philosophy", "sociology"],
    "health": ["psychology", "science"],
    "life": ["philosophy", "psychology", "literature"],
    "literature": ["literature", "art", "philosophy"],
    "art": ["art", "literature", "philosophy"],
    "history": ["history", "politics", "philosophy"],
    "spark": [],  # 自由话题 → 不预设，全凭文本匹配
}


def _score_thinker(thinker: dict[str, Any], category: str, text_blob: str) -> float:
    """给单个思想家打分。分越高越契合。"""
    score = 0.0
    # 1) 类别 → 领域 命中：基础分 +5
    preferred_domains = _CATEGORY_TO_DOMAINS.get(category or "", [])
    raw_domain = thinker.get("domain", "")
    domain_str = raw_domain if isinstance(raw_domain, str) else (raw_domain[0] if raw_domain else "")
    if domain_str and domain_str in preferred_domains:
        # 越靠前权重越大（5 -> 4 -> 3）
        score += max(5 - preferred_domains.index(domain_str), 3)

    # 2) 文本相似度：对 description / domain_cn / name / era 做粗糙的子串匹配
    blob = text_blob.lower()
    if not blob:
        return score

    description = str(thinker.get("description", "") or "").lower()
    domain_cn = str(thinker.get("domain_cn", "") or "").lower()
    name = str(thinker.get("name", "") or "").lower()
    era = str(thinker.get("era", "") or "").lower()

    # 中文常见关键词（2 字以上的子串才算命中，避免误匹配）
    def _overlap_score(target: str, weight: float) -> float:
        if not target:
            return 0.0
        # 把 target 拆成 2-gram 列表，看有多少在 blob 里
        tokens = {target[i:i + 2] for i in range(len(target) - 1) if len(target[i:i + 2].strip()) == 2}
        return sum(weight for tok in tokens if tok in blob)

    score += _overlap_score(domain_cn, 1.5)
    score += _overlap_score(description, 0.4)
    score += _overlap_score(name, 1.0)
    if era and era in blob:
        score += 0.5

    return score


@router.post("/recommend")
async def recommend_thinkers(
    payload: dict[str, Any] = Body(default_factory=dict),
):
    """根据话题给出 1-3 名最契合的思想家推荐。

    请求体字段（全部可选）：
    - title:        话题标题
    - description:  话题描述
    - category:     话题分类（与 topics yaml 对齐，如 "society"/"tech"/"life"）
    - tags:         话题标签数组
    - guide_questions: 引导问题数组
    - exclude_ids:  已选思想家 ID 列表（不再重复推荐）
    - limit:        返回数量上限，默认 3，最少 1

    返回：[{id, name, avatar, domain, domain_cn, score, reason}]
    """
    title = str(payload.get("title", "") or "").strip()
    description = str(payload.get("description", "") or "").strip()
    category = str(payload.get("category", "") or "").strip()
    tags = payload.get("tags") or []
    guide_questions = payload.get("guide_questions") or []
    exclude_ids = set(payload.get("exclude_ids") or [])
    try:
        limit = int(payload.get("limit", 3) or 3)
    except (TypeError, ValueError):
        limit = 3
    limit = max(1, min(limit, 5))

    text_blob = " ".join(
        [
            title,
            description,
            " ".join(str(t) for t in tags if t),
            " ".join(str(q) for q in guide_questions if q),
        ]
    ).strip()

    if not title and not description and not category and not text_blob:
        return {"items": [], "reason": "empty_topic"}

    all_thinkers = load_all_thinkers()
    scored: list[tuple[float, dict[str, Any]]] = []
    for tid, thinker in all_thinkers.items():
        if tid in exclude_ids:
            continue
        s = _score_thinker(thinker, category, text_blob)
        if s > 0:
            scored.append((s, thinker))

    scored.sort(key=lambda x: x[0], reverse=True)

    # 至少返回 1 名：若 scored 为空（话题与所有思想家都不沾边），
    # 退回到 category 默认领域的第一位，或首位思想家。
    if not scored:
        fallback: list[dict[str, Any]] = []
        for d in _CATEGORY_TO_DOMAINS.get(category, []):
            for t in get_thinkers_by_domain(d):
                if t.get("id") not in exclude_ids:
                    fallback.append(t)
                    break
            if fallback:
                break
        if not fallback:
            for t in all_thinkers.values():
                if t.get("id") not in exclude_ids:
                    fallback.append(t)
                    break
        items = [
            {
                "id": t.get("id"),
                "name": t.get("name"),
                "avatar": t.get("avatar"),
                "domain": t.get("domain"),
                "domain_cn": t.get("domain_cn"),
                "score": 0.0,
                "reason": "fallback",
            }
            for t in fallback[:1]
        ]
        return {"items": items, "reason": "fallback"}

    top = scored[: max(1, limit)]
    items = [
        {
            "id": t.get("id"),
            "name": t.get("name"),
            "avatar": t.get("avatar"),
            "domain": t.get("domain"),
            "domain_cn": t.get("domain_cn"),
            "score": round(s, 2),
            "reason": "matched",
        }
        for s, t in top
    ]
    return {"items": items, "reason": "matched"}


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