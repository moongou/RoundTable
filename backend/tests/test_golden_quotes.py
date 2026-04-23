from __future__ import annotations

from app.core.golden_quotes import (
    build_golden_quotes_prompt,
    has_enough_quote_material,
    parse_golden_quotes_response,
)


def test_has_enough_quote_material_requires_multi_speaker_discussion() -> None:
    early_messages = [
        {'source': '李老师', 'content': '同学们好，我是李老师。', 'type': 'text'},
        {'source': '李老师', 'content': '今天我们聊聊分数。', 'type': 'text'},
        {'source': '小探', 'content': '我觉得分数像温度计。', 'type': 'text'},
    ]
    rich_messages = [
        {'source': '李老师', 'content': '同学们好，我们来聊聊分数。', 'type': 'text'},
        {'source': '小探', 'content': '我觉得分数只能看到表面。', 'type': 'text'},
        {'source': '豆苗', 'content': '有时候做对了也不代表真懂。', 'type': 'text'},
        {'source': '小理', 'content': '所以还要看解释过程和举一反三。', 'type': 'text'},
    ]

    assert has_enough_quote_material(early_messages) is False
    assert has_enough_quote_material(rich_messages) is True


def test_build_prompt_requires_model_to_return_refined_json_quotes() -> None:
    prompt = build_golden_quotes_prompt(
        '分数能衡量学习吗',
        [
            {'source': '李老师', 'content': '我们来聊聊分数。', 'type': 'text'},
            {'source': '小探', 'content': '我觉得分数像温度计。', 'type': 'text'},
            {'source': '豆苗', 'content': '有时做对只是碰巧。', 'type': 'text'},
            {'source': '小理', 'content': '理解过程比结果更重要。', 'type': 'text'},
        ],
        max_quotes=5,
    )

    assert '不能直接照抄原话' in prompt
    assert '每条 10 到 22 个字' in prompt
    assert '优先写成前后两小截互相碰撞的一句短句' in prompt
    assert '不要凭空引入无关画面' in prompt
    assert '分数会亮灯，理解才会发光' in prompt
    assert '分数记结果，脑子记火花' in prompt
    assert '分数记答卷，理解记脑海里的灯' in prompt
    assert '{"quotes": ["...", "..."]}' in prompt
    assert '分数能衡量学习吗' in prompt


def test_parse_golden_quotes_response_prefers_refined_json_payload() -> None:
    raw = '{"quotes": ["分数能看见结果，却照不亮真正的理解过程", "真正的学习，不只会答对，还要会说明为什么"]}'

    assert parse_golden_quotes_response(raw, max_quotes=6) == [
        '分数能看见结果，却照不亮真正的理解过程',
        '真正的学习，不只会答对，还要会说明为什么',
    ]


def test_parse_golden_quotes_response_cleans_bullets_and_speaker_prefixes() -> None:
    raw = '1. 李老师：分数只是学习旅程里的路标\n2. 小探：真正重要的是能把道理讲明白'

    assert parse_golden_quotes_response(raw, max_quotes=6) == [
        '分数只是学习旅程里的路标',
        '真正重要的是能把道理讲明白',
    ]


def test_parse_golden_quotes_response_drops_summaryish_lines() -> None:
    raw = '{"quotes": ["这说明分数不能代表全部", "分数会亮灯，理解才会发光"]}'

    assert parse_golden_quotes_response(raw, max_quotes=6) == [
        '分数会亮灯，理解才会发光',
    ]


def test_parse_golden_quotes_response_removes_stray_spaces_between_cjk_chars() -> None:
    raw = '{"quotes": ["做对只是起点，讲明 白才算抵达", "分数测量 结果，理解照亮过程"]}'

    assert parse_golden_quotes_response(raw, max_quotes=6) == [
        '做对只是起点，讲明白才算抵达',
        '分数测量结果，理解照亮过程',
    ]