"""讨论角色数量限制

每场讨论固定阵容上限：最多 2 位虚拟同学 + 1 位老师（主持人）+ 1 位思想家。
老师由系统固定提供（恰好 1 位），因此这里只校验同学与思想家数量。
"""

from __future__ import annotations

MAX_STUDENTS = 2
MAX_THINKERS = 1
MODERATOR_COUNT = 1

ROLE_LIMIT_SUMMARY = "每场讨论最多 2 位同学 + 1 位老师（主持人）+ 1 位思想家"


def validate_role_roster(
    character_ids: list[str] | None,
    thinker_ids: list[str] | None,
) -> tuple[bool, str]:
    """校验角色阵容是否满足数量限制。

    Args:
        character_ids: 虚拟角色模板 ID 列表（可含 "moderator"，主持人不计入同学数）。
        thinker_ids: 思想家 ID 列表。

    Returns:
        (是否合法, 错误消息)。合法时错误消息为空字符串。
    """
    student_ids = [c for c in (character_ids or []) if c != "moderator"]
    thinkers = thinker_ids or []

    if len(student_ids) > MAX_STUDENTS:
        return (
            False,
            f"虚拟同学最多 {MAX_STUDENTS} 位（当前选择了 {len(student_ids)} 位），"
            f"请减少选择。{ROLE_LIMIT_SUMMARY}。",
        )

    if len(thinkers) > MAX_THINKERS:
        return (
            False,
            f"思想家最多 {MAX_THINKERS} 位（当前选择了 {len(thinkers)} 位），"
            f"请减少选择。{ROLE_LIMIT_SUMMARY}。",
        )

    return True, ""
