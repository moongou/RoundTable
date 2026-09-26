"""语气／情绪映射工具

把中文台词和角色身份投射成统一的情绪标签，再按各家 provider 的能力翻译成各自的参数：

- ElevenLabs v3：支持 audio tag（如 `[excited]`、`[whispers]`），同时可用 voice_settings 微调。
- MiniMax Speech：voice_setting.emotion 支持 9 种固定取值。
- 其余 provider：忽略情绪，仅保留文本与音色（由 base.TTSProvider.synthesize_safe 负责降级）。

设计原则：**本地规则、零额外延迟**。情绪直接从台词文本与角色身份推导，
不额外调用 LLM，避免拉长首包时间。
"""

from __future__ import annotations

import re

# ── 统一情绪标签 ────────────────────────────────────────────────────────────
# 取值刻意保持语义中立，便于向不同 provider 翻译。

NEUTRAL = "neutral"
CALM = "calm"
GENTLE = "gentle"
SERIOUS = "serious"
CURIOUS = "curious"
UNSURE = "unsure"
HAPPY = "happy"
EXCITED = "excited"
SAD = "sad"
ANGRY = "angry"
FEARFUL = "fearful"
SURPRISED = "surprised"
DISGUSTED = "disgusted"
WHISPER = "whisper"

CANONICAL_EMOTIONS = (
    NEUTRAL,
    CALM,
    GENTLE,
    SERIOUS,
    CURIOUS,
    UNSURE,
    HAPPY,
    EXCITED,
    SAD,
    ANGRY,
    FEARFUL,
    SURPRISED,
    DISGUSTED,
    WHISPER,
)

# ── 台词关键词 → 情绪（按优先级排列，先命中先生效）────────────────────────────

_KEYWORD_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("哈哈哈", "笑死", "实在太搞笑了", "太逗了"), EXCITED),
    (("太好了", "太棒了", "好极了", "真厉害", "太厉害", "棒极了", "好耶", "我赢", "欢呼"), EXCITED),
    (("不可思议", "竟然", "居然", "没想到", "天哪", "天啊", "真的吗", "难以置信", "哇"), SURPRISED),
    (("不公平", "太过分", "凭什么", "生气", "愤怒", "气死", "我抗议"), ANGRY),
    (("难过", "伤心", "可惜", "遗憾", "沮丧", "呜呜", "好失落", "想哭"), SAD),
    (("害怕", "担心", "紧张", "可怕", "怎么办", "万一", "会不会"), FEARFUL),
    (("好恶心", "才不要", "讨厌死了"), DISGUSTED),
    (("开心", "高兴", "喜欢", "真有意思", "真好玩", "谢谢"), HAPPY),
    (("让我想想", "我觉得吧", "你的意思是", "有没有可能", "难道说", "为什么", "是什么呢"), CURIOUS),
    (("也许是", "可能吧", "我不确定", "好像"), UNSURE),
    (("请安静", "轻声", "悄悄", "嘘"), WHISPER),
)

# 舞台提示 → 情绪，例如（兴奋地）"…"、（轻声）"…"
_STAGE_HINT_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("兴奋", "激动", "雀跃", "欢呼"), EXCITED),
    (("开心", "笑", "愉快", "高兴"), HAPPY),
    (("难过", "伤心", "失落", "哽咽", "哭"), SAD),
    (("生气", "愤怒", "不满", "抗议"), ANGRY),
    (("害怕", "担心", "紧张"), FEARFUL),
    (("惊讶", "震惊", "意外"), SURPRISED),
    (("轻声", "悄悄", "低语", "小声", "耳语"), WHISPER),
    (("严肃", "认真", "郑重"), SERIOUS),
    (("温柔", "亲切", "和蔼", "安抚"), GENTLE),
    (("平静", "从容", "沉稳"), CALM),
    (("好奇", "疑惑", "追问"), CURIOUS),
)

# 角色身份 → 默认情绪基调（仅在台词本身没有明显情绪信号时使用）
_ROLE_BIAS: dict[str, str] = {
    "moderator": GENTLE,
    "teacher": GENTLE,
    "thinker": SERIOUS,
    "student": CURIOUS,
    "child": CURIOUS,
    "human": NEUTRAL,
    "user": NEUTRAL,
}

_STAGE_HINT_PATTERN = re.compile(r"[（(\[【]([^）)\]】]{1,12})[）)\]】]")
_LEADING_STAGE_HINT = re.compile(r"^\s*[（(\[【]([^）)\]】]{1,12})[）)\]】]\s*")


def _match_keywords(text: str, rules: tuple[tuple[tuple[str, ...], str], ...]) -> str | None:
    for keywords, emotion in rules:
        if any(keyword in text for keyword in keywords):
            return emotion
    return None


def stage_hint_emotion(text: str) -> str | None:
    """从舞台提示（如「（兴奋地）」）推导情绪，并不过滤文本。"""
    for hint in _STAGE_HINT_PATTERN.findall(text or ""):
        emotion = _match_keywords(hint, _STAGE_HINT_RULES)
        if emotion:
            return emotion
    return None


def detect_emotion(text: str, role: str | None = None) -> str | None:
    """推导一句台词的情绪标签。

    Args:
        text: 台词正文（可含舞台提示）。
        role: 角色身份（moderator / teacher / student / thinker …）。

    Returns:
        统一情绪标签；无法判定时返回 None，交由 provider 使用音色自身的自然表达。
    """
    body = (text or "").strip()
    if not body:
        return None

    # 1) 舞台提示最优先（作者意图最明确）
    hint = stage_hint_emotion(body)
    if hint:
        return hint

    # 2) 台词关键词
    keyword_hit = _match_keywords(body, _KEYWORD_RULES)
    if keyword_hit:
        return keyword_hit

    # 3) 标点强信号
    if body.count("!") + body.count("！") >= 1 and len(body) <= 40:
        return EXCITED
    if "？" in body and body.rstrip().endswith("？") and len(body) <= 30:
        return CURIOUS

    # 4) 角色基调兜底
    return _ROLE_BIAS.get((role or "").strip().lower())


# ── 各家 provider 的翻译表 ──────────────────────────────────────────────────

# MiniMax Speech：仅支持 9 种固定取值
_MINIMAX_EMOTIONS = {
    HAPPY: "happy",
    SAD: "sad",
    ANGRY: "angry",
    FEARFUL: "fearful",
    DISGUSTED: "disgusted",
    SURPRISED: "surprised",
    CALM: "calm",
    NEUTRAL: "calm",
    EXCITED: "fluent",  # 「生动」最接近兴奋
    GENTLE: "calm",
    SERIOUS: "calm",
    CURIOUS: "fluent",
    UNSURE: "fluent",
    WHISPER: "whisper",
}


def to_minimax_emotion(canonical: str | None, model: str = "") -> str | None:
    """翻译为 MiniMax 的 voice_setting.emotion 取值。

    注意：speech-2.8 系列不支持 whisper，遇到时直接丢弃该参数。
    """
    if not canonical:
        return None
    value = _MINIMAX_EMOTIONS.get(canonical)
    if not value:
        return None
    if value == "whisper" and "2.8" in (model or ""):
        return None
    return value


# ElevenLabs v3：audio tag 直接写在文本里，比参数更细粒度
_ELEVENLABS_TAGS = {
    EXCITED: "excited",
    HAPPY: "cheerfully",
    SAD: "sadly",
    ANGRY: "angrily",
    FEARFUL: "nervously",
    SURPRISED: "surprised",
    DISGUSTED: "disgusted",
    WHISPER: "whispers",
    GENTLE: "softly",
    CURIOUS: "curious",
    UNSURE: "hesitantly",
    SERIOUS: "serious",
    CALM: "calmly",
}


def elevenlabs_audio_tag(canonical: str | None) -> str | None:
    """返回 ElevenLabs v3 的 audio tag 名（不含方括号）。"""
    if not canonical or canonical == NEUTRAL:
        return None
    return _ELEVENLABS_TAGS.get(canonical)


def with_elevenlabs_tag(text: str, canonical: str | None) -> str:
    """把 audio tag 前置到台词里，例如 `[excited] 今天太开心了！`。"""
    tag = elevenlabs_audio_tag(canonical)
    if not tag:
        return text
    return f"[{tag}] {text}"


# ElevenLabs voice_settings：stability / similarity / style 三档手感
_ELEVENLABS_SETTINGS = {
    EXCITED: {"stability": 0.35, "similarity_boost": 0.75, "style": 0.60},
    HAPPY: {"stability": 0.40, "similarity_boost": 0.75, "style": 0.45},
    ANGRY: {"stability": 0.30, "similarity_boost": 0.70, "style": 0.70},
    SAD: {"stability": 0.45, "similarity_boost": 0.80, "style": 0.40},
    FEARFUL: {"stability": 0.35, "similarity_boost": 0.75, "style": 0.50},
    SURPRISED: {"stability": 0.30, "similarity_boost": 0.70, "style": 0.65},
    DISGUSTED: {"stability": 0.40, "similarity_boost": 0.75, "style": 0.50},
    WHISPER: {"stability": 0.55, "similarity_boost": 0.85, "style": 0.35},
    GENTLE: {"stability": 0.60, "similarity_boost": 0.85, "style": 0.30},
    CURIOUS: {"stability": 0.40, "similarity_boost": 0.75, "style": 0.45},
    UNSURE: {"stability": 0.45, "similarity_boost": 0.75, "style": 0.40},
    SERIOUS: {"stability": 0.70, "similarity_boost": 0.85, "style": 0.25},
    CALM: {"stability": 0.65, "similarity_boost": 0.85, "style": 0.25},
}


def elevenlabs_voice_settings(canonical: str | None) -> dict:
    """返回与情绪匹配的 ElevenLabs voice_settings 片段。"""
    if not canonical:
        return {}
    return dict(_ELEVENLABS_SETTINGS.get(canonical, {}))


def strip_stage_hints(text: str) -> str:
    """去掉台词开头的舞台提示，避免被朗读出来。"""
    return _LEADING_STAGE_HINT.sub("", (text or "").strip())
