from app.core.discussion_rules import (
    CitationClaim,
    HumanMicRequest,
    OpeningEnvelope,
    SpeakerBalanceSnapshot,
    can_open_human_mic,
    closing_review_required,
    opening_envelope,
    speaker_balance_warnings,
    validate_citation_claim,
)


def test_opening_envelope_requires_twenty_to_forty_seconds_with_context() -> None:
    short = opening_envelope(
        "同学们好，今天聊标准答案。小探同学，请。",
        chars_per_second=5.5,
    )
    assert short.has_context is False
    assert short.estimated_seconds < 20.0
    assert short.within_target is False

    grounded = opening_envelope(
        "同学们好，我是李老师。今天我们来聊标准答案的危险。这个话题来自课堂里常见的情况："
        "有些题目看起来只有一个答案，但真实生活里还需要讲理由、看情境。"
        "所以我们要讨论的是，什么时候标准答案能帮我们，什么时候又会限制思考。"
        "大家可以从考试、课堂讨论和生活选择三个角度慢慢说。",
        chars_per_second=5.5,
    )
    assert grounded.has_context is True
    assert 20.0 <= grounded.estimated_seconds <= 40.0
    assert grounded.within_target is True


def test_human_mic_only_opens_after_authorized_nomination_or_interrupt() -> None:
    no_nomination = HumanMicRequest(
        target_name="豆苗",
        reason="normal",
        triggering_text="大家还有什么想法？",
        human_names={"豆苗"},
    )
    assert can_open_human_mic(no_nomination) is False

    explicit_teacher_nomination = HumanMicRequest(
        target_name="豆苗",
        reason="moderator_designated_human",
        triggering_text="豆苗同学，你怎么看？",
        human_names={"豆苗"},
    )
    assert can_open_human_mic(explicit_teacher_nomination) is True

    hand_raise = HumanMicRequest(
        target_name="豆苗",
        reason="interrupt",
        triggering_text="",
        human_names={"豆苗"},
    )
    assert can_open_human_mic(hand_raise) is True


def test_citation_claim_requires_real_matching_owner() -> None:
    history = [
        ("小探", "我觉得规则像游戏说明书，能让大家知道怎么玩。"),
        ("豆苗", "可是有时候规则太多，也会让人不敢尝试。"),
    ]

    correct = CitationClaim(owner="豆苗", quote="规则太多", history=history)
    assert validate_citation_claim(correct) is True

    wrong_owner = CitationClaim(owner="小探", quote="规则太多", history=history)
    assert validate_citation_claim(wrong_owner) is False

    invented = CitationClaim(owner="小爱", quote="规则会保护弱小的人", history=history)
    assert validate_citation_claim(invented) is False


def test_speaker_balance_warns_when_teacher_or_virtual_roles_are_unbalanced() -> None:
    snapshot = SpeakerBalanceSnapshot(
        counts={"李老师": 7, "小探": 4, "小疑": 1, "豆苗": 5, "苏格拉底": 3},
        moderator_name="李老师",
        human_names={"豆苗"},
        virtual_names={"小探", "小疑", "苏格拉底"},
    )

    warnings = speaker_balance_warnings(snapshot)

    assert "moderator_share_over_35_percent" in warnings
    assert "virtual_role_under_participated:小疑" in warnings


def test_closing_review_required_only_after_real_human_speech() -> None:
    assert closing_review_required(
        [("李老师", "今天讨论到这里。"), ("小探", "我觉得规则要公平。")],
        human_names={"豆苗"},
    ) is False

    assert closing_review_required(
        [("李老师", "今天讨论到这里。"), ("豆苗", "我觉得规则要能解释清楚。")],
        human_names={"豆苗"},
    ) is True
