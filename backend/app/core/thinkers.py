"""思想家数据加载器

从 YAML 文件加载 legend-talk 导入的思想家数据。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

THINKERS_DIR = Path(__file__).parent.parent / "thinkers"
_thinkers_cache: dict[str, dict] | None = None
_thinkers_by_domain: dict[str, list[dict]] | None = None


def load_all_thinkers() -> dict[str, dict]:
    """加载所有思想家数据（带缓存）。"""
    global _thinkers_cache

    if _thinkers_cache is not None:
        return _thinkers_cache

    _thinkers_cache = {}

    if not THINKERS_DIR.exists():
        logger.warning(f"思想家目录不存在: {THINKERS_DIR}")
        return _thinkers_cache

    for yaml_file in THINKERS_DIR.glob("*.yaml"):
        try:
            with open(yaml_file, encoding="utf-8") as f:
                data = yaml.safe_load(f)

            file_domain = data.get("domain", "")
            file_domain_cn = data.get("domain_cn", file_domain)

            for thinker in data.get("thinkers", []):
                tid = thinker.get("id")
                if tid:
                    # Ensure domain is a flat string for frontend consumption
                    raw_domain = thinker.get("domain", [])
                    if isinstance(raw_domain, list):
                        thinker["domain"] = raw_domain[0] if raw_domain else file_domain
                    elif not raw_domain:
                        thinker["domain"] = file_domain
                    # Inject domain_cn from file header
                    if "domain_cn" not in thinker or not thinker["domain_cn"]:
                        thinker["domain_cn"] = file_domain_cn
                    _thinkers_cache[tid] = thinker
        except Exception as e:
            logger.error(f"加载思想家文件失败 {yaml_file}: {e}")

    logger.info(f"加载了 {len(_thinkers_cache)} 个思想家")
    return _thinkers_cache


def get_thinker(thinker_id: str) -> Optional[dict]:
    """根据 ID 获取思想家。"""
    thinkers = load_all_thinkers()
    return thinkers.get(thinker_id)


def thinker_label(thinker_id: str, thinker: Optional[dict] = None) -> str:
    """返回会话内用于展示和引用的稳定思想家名字。"""
    data = thinker or get_thinker(thinker_id) or {}
    for key in ("name", "display_name"):
        value = str(data.get(key, "") or "").strip()
        if value:
            return value
    return thinker_id


def get_thinkers_by_domain(domain: str) -> list[dict]:
    """获取指定领域的思想家列表。"""
    thinkers = load_all_thinkers()
    return [t for t in thinkers.values() if t.get("domain") == domain]


def get_all_domains() -> list[dict]:
    """获取所有领域及其统计。"""
    thinkers = load_all_thinkers()
    domain_count: dict[str, int] = {}
    domain_cn: dict[str, str] = {}

    for t in thinkers.values():
        d = t.get("domain", "")
        if d:
            domain_count[d] = domain_count.get(d, 0) + 1
            if d not in domain_cn:
                domain_cn[d] = t.get("domain_cn", d)

    # 从 YAML 文件头获取中文名
    if THINKERS_DIR.exists():
        for yaml_file in THINKERS_DIR.glob("*.yaml"):
            try:
                with open(yaml_file, encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                d = data.get("domain", "")
                if d:
                    domain_cn[d] = data.get("domain_cn", d)
            except Exception:
                pass

    return [
        {"id": d, "name_cn": domain_cn.get(d, d), "count": c}
        for d, c in sorted(domain_count.items(), key=lambda x: -x[1])
    ]


def search_thinkers(query: str, limit: int = 20) -> list[dict]:
    """搜索思想家（按名字、时代、领域匹配）。"""
    thinkers = load_all_thinkers()
    query_lower = query.lower()
    results = []

    for t in thinkers.values():
        name = t.get("name", "").lower()
        display_name = t.get("display_name", "").lower()
        era = t.get("era", "").lower()
        domains = [d.lower() for d in t.get("domain", [])]

        if (query_lower in name or
            query_lower in display_name or
            query_lower in era or
            any(query_lower in d for d in domains)):
            results.append(t)
            if len(results) >= limit:
                break

    return results