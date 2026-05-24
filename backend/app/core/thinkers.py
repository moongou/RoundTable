"""思想家数据加载器

从 YAML 文件加载 legend-talk 导入的思想家数据。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

THINKERS_DIR = Path(__file__).parent.parent / "thinkers"
_thinkers_cache: dict[str, dict] | None = None
_thinkers_by_domain: dict[str, list[dict]] | None = None


def _clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_questions(raw_questions: object) -> list[str]:
    if not isinstance(raw_questions, list):
        return []
    questions: list[str] = []
    for item in raw_questions:
        question = _clean_text(item)
        if question:
            questions.append(question)
    return questions


def _join_focus_points(questions: list[str]) -> str:
    if not questions:
        return "如何把抽象的道理放回真实生活里慢慢想清楚"
    focus_points = [
        _clean_text(question).rstrip("？?。！!")
        for question in questions[:3]
        if _clean_text(question)
    ]
    if not focus_points:
        return "如何把抽象的道理放回真实生活里慢慢想清楚"
    if len(focus_points) == 1:
        return focus_points[0]
    if len(focus_points) == 2:
        return f"{focus_points[0]}，以及{focus_points[1]}"
    return f"{focus_points[0]}、{focus_points[1]}，以及{focus_points[2]}"


def _extract_core_idea(thinker: dict, *, fallback_domain_cn: str) -> str:
    system_message = _clean_text(thinker.get("system_message"))
    if "你的核心思想：" in system_message:
        system_message = system_message.split("你的核心思想：", 1)[1].strip()
    system_message = re.sub(r"^You are\s+", "", system_message, flags=re.IGNORECASE)
    system_message = re.sub(r"^You\s+", "", system_message, flags=re.IGNORECASE)
    system_message = re.sub(r"^[A-Za-z][A-Za-z0-9\s,'./:;()\-–—]{0,120}$", "", system_message)
    system_message = _clean_text(system_message)
    if system_message:
        if len(system_message) > 88:
            system_message = system_message[:88].rstrip(" ,;:，；：") + "…"
        return system_message
    return f"把{fallback_domain_cn}中的关键问题讲清楚，并把思考重新带回具体生活处境。"


def _build_core_summary(thinker: dict, *, fallback_domain_cn: str) -> str:
    name = _clean_text(thinker.get("name") or thinker.get("display_name") or "这位思想家")
    focus = _join_focus_points(_normalize_questions(thinker.get("suggested_questions")))
    core_idea = _extract_core_idea(thinker, fallback_domain_cn=fallback_domain_cn)
    return (
        f"{name}常从“{focus}”这些问题切入，强调{fallback_domain_cn}不是死记结论，"
        f"而是要在真实处境里学会观察、判断、提问与行动。其讨论风格更看重{core_idea}"
    )


def _build_biography(thinker: dict, *, fallback_domain_cn: str) -> str:
    name = _clean_text(thinker.get("name") or thinker.get("display_name") or "这位思想家")
    era = _clean_text(thinker.get("era") or "不同时代")
    description = _clean_text(thinker.get("description"))
    focus = _join_focus_points(_normalize_questions(thinker.get("suggested_questions")))
    core_summary = _build_core_summary(thinker, fallback_domain_cn=fallback_domain_cn)
    intro = (
        f"{name}通常被放在{era}的{fallback_domain_cn}脉络中来理解。"
        f"{description or f'在后人眼中，{name}是一位极具代表性的{fallback_domain_cn}思想人物。'}"
    )
    impact = (
        f"如果用今天的眼光回看，{name}最可贵的地方，不只是提出了某几个结论，"
        f"而是不断提醒人们：面对复杂问题时，要先看清背景、人的处境与行动后果，"
        f"再决定自己赞成什么、反对什么。"
    )
    reflection = (
        f"{name}尤其会把注意力放在“{focus}”这一类问题上，"
        f"因为这些问题往往没有唯一标准答案，却最能检验一个人的判断力、同理心与实践能力。"
    )
    roundtable = (
        f"放到圆桌讨论里，{name}带来的价值，是把抽象观念翻译成孩子也能抓住的思考路径："
        f"{core_summary}。因此，TA不是替大家直接下结论，而是帮助同学们把问题想深一层、想慢一点、想完整一些。"
    )
    biography = _clean_text(f"{intro}{impact}{reflection}{roundtable}")
    if len(biography) < 300:
        biography = _clean_text(
            biography
            + f" 当大家讨论学习、规则、选择、关系或社会现象时，{name}提供的往往不是现成口号，"
            f"而是一种更有层次的观察方法：先分辨问题，再比较立场，最后把观点落实到现实生活。"
        )
    if len(biography) > 600:
        biography = biography[:600].rstrip("，；：,.!?！？ ") + "。"
    return biography


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
                    thinker["core_summary"] = _build_core_summary(
                        thinker,
                        fallback_domain_cn=file_domain_cn,
                    )
                    thinker["biography"] = _build_biography(
                        thinker,
                        fallback_domain_cn=file_domain_cn,
                    )
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
        raw_domain = t.get("domain", [])
        if isinstance(raw_domain, list):
            domains = [str(d).lower() for d in raw_domain]
        elif raw_domain:
            domains = [str(raw_domain).lower()]
        else:
            domains = []

        if (query_lower in name or
            query_lower in display_name or
            query_lower in era or
            any(query_lower in d for d in domains)):
            results.append(t)
            if len(results) >= limit:
                break

    return results
