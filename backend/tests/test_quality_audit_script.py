from scripts.simulate_10_rounds_quality_audit import (
    _has_session_quote_attribution,
    _mentions_participant_as_quote_owner,
)


def test_quote_attribution_requires_explicit_owner_verb() -> None:
    text = (
        '豆苗同学这个角度很关键啊——"有网有烦恼"还是"有网有惊喜"，'
        '你抓住了小疑想表达的那个矛盾点。'
    )

    assert _has_session_quote_attribution(text, '有网有烦恼', ['老师', '小疑', '豆苗']) is False


def test_quote_owner_mismatch_only_triggers_on_explicit_misattribution() -> None:
    text = '老师，我想到刚才小爱说“有时候讲道理行不通”，其实大人说的也不一定全对呀。'

    assert _mentions_participant_as_quote_owner(text, '小爱', '有时候讲道理行不通') is True


def test_quote_owner_mismatch_ignores_named_topic_without_quote_ownership() -> None:
    text = '豆苗同学这个角度很关键啊——"有网有烦恼"还是"有网有惊喜"，你抓住了小疑想表达的那个矛盾点。'

    assert _mentions_participant_as_quote_owner(text, '小疑', '有网有烦恼') is False