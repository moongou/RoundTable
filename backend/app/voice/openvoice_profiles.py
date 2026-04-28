"""Fixed OpenVoice profile registry for RoundTable roles.

Each profile corresponds to a stable role-specific reference audio sample used
for OpenVoice cloning. This keeps the teacher, thinkers, and ten student roles
on deterministic voices across sessions.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


OPENVOICE_PROFILE_PREFIX = "ov:"
OPENVOICE_REFERENCE_DIR = (
    Path(__file__).resolve().parents[1] / "voice_profiles" / "openvoice"
)
OPENVOICE_TEACHER_REFERENCE_FILENAME = "student_xiaoxiang.wav"


@dataclass(frozen=True)
class OpenVoiceProfile:
    profile_id: str
    label: str
    role: str
    base_speaker: str
    reference_audio: Path
    fallback_voice: str


def _profile(
    profile_id: str,
    *,
    label: str,
    role: str,
    filename: str,
    fallback_voice: str,
    base_speaker: str = "zh",
) -> OpenVoiceProfile:
    return OpenVoiceProfile(
        profile_id=profile_id,
        label=label,
        role=role,
        base_speaker=base_speaker,
        reference_audio=OPENVOICE_REFERENCE_DIR / filename,
        fallback_voice=fallback_voice,
    )


OPENVOICE_PROFILES = {
    "ov:teacher_li": _profile(
        "ov:teacher_li",
        label="李老师",
        role="teacher",
        filename=OPENVOICE_TEACHER_REFERENCE_FILENAME,
        fallback_voice="zh-CN-XiaoxiaoNeural",
    ),
    "ov:student_xiaotan": _profile(
        "ov:student_xiaotan",
        label="小探",
        role="student_male",
        filename="student_xiaotan.wav",
        fallback_voice="zh-CN-YunxiNeural",
    ),
    "ov:student_xiaoyi": _profile(
        "ov:student_xiaoyi",
        label="小疑",
        role="student_female",
        filename="student_xiaoyi.wav",
        fallback_voice="zh-CN-XiaoyiNeural",
    ),
    "ov:student_xiaohe": _profile(
        "ov:student_xiaohe",
        label="小和",
        role="student_male",
        filename="student_xiaohe.wav",
        fallback_voice="zh-CN-YunhaoNeural",
    ),
    "ov:student_xiaoshuo": _profile(
        "ov:student_xiaoshuo",
        label="小说",
        role="student_female",
        filename="student_xiaoshuo.wav",
        fallback_voice="zh-CN-XiaohanNeural",
    ),
    "ov:student_xiaoming": _profile(
        "ov:student_xiaoming",
        label="小明",
        role="student_male",
        filename="student_xiaoming.wav",
        fallback_voice="zh-CN-YunjieNeural",
    ),
    "ov:student_xiaosi": _profile(
        "ov:student_xiaosi",
        label="小思",
        role="student_male",
        filename="student_xiaosi.wav",
        fallback_voice="zh-CN-YunxiaNeural",
    ),
    "ov:student_xiaoli": _profile(
        "ov:student_xiaoli",
        label="小理",
        role="student_male",
        filename="student_xiaoli.wav",
        fallback_voice="zh-CN-YunyangNeural",
    ),
    "ov:student_xiaoai": _profile(
        "ov:student_xiaoai",
        label="小爱",
        role="student_female",
        filename="student_xiaoai.wav",
        fallback_voice="zh-CN-XiaoyouNeural",
    ),
    "ov:student_xiaoxiang": _profile(
        "ov:student_xiaoxiang",
        label="小想",
        role="student_female",
        filename="student_xiaoxiang.wav",
        fallback_voice="zh-CN-XiaoxuanNeural",
    ),
    "ov:student_xiaoxing": _profile(
        "ov:student_xiaoxing",
        label="小行",
        role="student_male",
        filename="student_xiaoxing.wav",
        fallback_voice="zh-CN-YunfengNeural",
    ),
    "ov:thinker_elder": _profile(
        "ov:thinker_elder",
        label="思想家",
        role="thinker_male",
        filename="thinker_elder.wav",
        fallback_voice="zh-CN-YunzeNeural",
    ),
}

OPENVOICE_PROFILE_IDS = tuple(OPENVOICE_PROFILES.keys())
OPENVOICE_CHILD_PROFILE_IDS = tuple(
    profile_id
    for profile_id, profile in OPENVOICE_PROFILES.items()
    if profile.role.startswith("student_")
)

CHARACTER_ID_TO_OPENVOICE_PROFILE = {
    "moderator": "ov:teacher_li",
    "explorer": "ov:student_xiaotan",
    "skeptic": "ov:student_xiaoyi",
    "peacemaker": "ov:student_xiaohe",
    "storyteller": "ov:student_xiaoshuo",
    "optimist": "ov:student_xiaoming",
    "questioner": "ov:student_xiaosi",
    "rationalist": "ov:student_xiaoli",
    "empath": "ov:student_xiaoai",
    "innovator": "ov:student_xiaoxiang",
    "pragmatist": "ov:student_xiaoxing",
    # 可乐 是一年级"小不点"，复用萌趣的童声 OpenVoice profile
    "comedian": "ov:student_xiaotan",
}


def get_openvoice_profile(profile_id: str | None) -> OpenVoiceProfile | None:
    if not profile_id:
        return None
    return OPENVOICE_PROFILES.get(profile_id.strip())


def list_openvoice_profile_ids() -> list[str]:
    return list(OPENVOICE_PROFILE_IDS)


def openvoice_profile_for_character_id(character_id: str | None) -> str | None:
    cid = (character_id or "").strip().lower()
    if not cid:
        return None
    return CHARACTER_ID_TO_OPENVOICE_PROFILE.get(cid)


def is_openvoice_profile_id(voice: str | None) -> bool:
    return bool(voice and voice.strip().startswith(OPENVOICE_PROFILE_PREFIX))