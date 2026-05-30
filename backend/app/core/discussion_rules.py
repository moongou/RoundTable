from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.floor_text_utils import normalize_reference_match_text, score_reference_fragment
from app.core.turn_scheduler import parse_speaker_designation


@dataclass(frozen=True)
class OpeningEnvelope:
    has_context: bool
    estimated_seconds: float
    within_target: bool


@dataclass(frozen=True)
class HumanMicRequest:
    target_name: str
    reason: str
    triggering_text: str
    human_names: set[str]


@dataclass(frozen=True)
class CitationClaim:
    owner: str
    quote: str
    history: list[tuple[str, str]]


@dataclass(frozen=True)
class SpeakerBalanceSnapshot:
    counts: dict[str, int]
    moderator_name: str
    human_names: set[str]
    virtual_names: set[str]


_AUTHORIZED_HUMAN_REQUEST_REASONS = frozenset(
    {
        "moderator_designated_human",
        "participant_designated_human",
        "interrupt",
    }
)
_CONTEXT_PATTERNS = (
    re.compile(r"来源|来自|生活里|现实中|课堂上|新闻里"),
    re.compile(r"定义|是指|意思是|所谓|我们要讨论的是|核心问题"),
    re.compile(r"背景|争议|分歧|矛盾|基本情况|该不该|要不要|值不值得|为什么|是什么|会不会|能不能"),
)
_SKIP_TEXTS = {"（跳过）", "(跳过)", "跳过"}


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def opening_envelope(text: str, *, chars_per_second: float = 5.5) -> OpeningEnvelope:
    compact = _compact_text(text)
    cps = max(chars_per_second, 1.0)
    estimated_seconds = round(len(compact) / cps, 1)
    has_context = any(pattern.search(compact) for pattern in _CONTEXT_PATTERNS)
    return OpeningEnvelope(
        has_context=has_context,
        estimated_seconds=estimated_seconds,
        within_target=has_context and 20.0 <= estimated_seconds <= 40.0,
    )


def can_open_human_mic(request: HumanMicRequest) -> bool:
    target = (request.target_name or "").strip()
    if target not in request.human_names:
        return False
    if request.reason not in _AUTHORIZED_HUMAN_REQUEST_REASONS:
        return False
    if request.reason == "interrupt":
        return True
    designated = parse_speaker_designation(
        request.triggering_text,
        sorted(request.human_names),
    )
    return designated == target


def validate_citation_claim(claim: CitationClaim) -> bool:
    owner = (claim.owner or "").strip()
    quote = normalize_reference_match_text(claim.quote or "")
    if not owner or not quote:
        return False
    for speaker, content in reversed(claim.history):
        if speaker != owner:
            continue
        if (content or "").strip() in _SKIP_TEXTS:
            continue
        normalized_content = normalize_reference_match_text(content or "")
        if len(quote) < 8:
            if quote in normalized_content:
                return True
            continue
        if quote in normalized_content or score_reference_fragment(quote, normalized_content) >= 0.86:
            return True
    return False


def speaker_balance_warnings(snapshot: SpeakerBalanceSnapshot) -> list[str]:
    warnings: list[str] = []
    total = sum(max(count, 0) for count in snapshot.counts.values())
    if total <= 0:
        return warnings

    moderator_count = max(snapshot.counts.get(snapshot.moderator_name, 0), 0)
    if moderator_count / total >= 0.35:
        warnings.append("moderator_share_over_35_percent")

    active_virtual_counts = [
        max(snapshot.counts.get(name, 0), 0)
        for name in snapshot.virtual_names
        if snapshot.counts.get(name, 0) > 0
    ]
    if not active_virtual_counts:
        return warnings
    expected_floor = max(1, min(active_virtual_counts) + 1)
    for name in sorted(snapshot.virtual_names):
        if max(snapshot.counts.get(name, 0), 0) < expected_floor:
            warnings.append(f"virtual_role_under_participated:{name}")
    return warnings


def closing_review_required(
    history: list[tuple[str, str]],
    *,
    human_names: set[str],
) -> bool:
    for speaker, content in history:
        if speaker not in human_names:
            continue
        text = (content or "").strip()
        if text and text not in _SKIP_TEXTS:
            return True
    return False
