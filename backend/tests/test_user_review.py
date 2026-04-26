from app.core.user_review import (
    build_user_review_prompt,
    has_enough_user_review_material,
    parse_user_review_response,
)


def test_user_review_material_requires_real_human_turn() -> None:
    messages = [
        {'source': '李老师', 'content': '今天我们聊聊分数背后的理解。', 'type': 'text'},
        {'source': '小探', 'content': '我觉得分数像温度计。', 'type': 'text'},
        {'source': '小理', 'content': '它会亮，但不一定会解释。', 'type': 'text'},
        {'source': '豆苗', 'content': '我发现自己会做题时，也想试着讲为什么。', 'type': 'text'},
    ]

    assert has_enough_user_review_material(messages, human_name='豆苗') is True
    assert has_enough_user_review_material(messages[:-1], human_name='豆苗') is False


def test_build_user_review_prompt_marks_human_lines() -> None:
    prompt = build_user_review_prompt(
        '分数和理解',
        '豆苗',
        [
            {'source': '李老师', 'content': '今天我们聊聊分数和理解。', 'type': 'text'},
            {'source': '豆苗', 'content': '我想知道做对题是不是就等于真懂。', 'type': 'text'},
            {'source': '小探', 'content': '也许会做只是第一步。', 'type': 'text'},
            {'source': '小理', 'content': '如果能讲清楚为什么，才更像真懂。', 'type': 'text'},
        ],
    )

    assert '给真人学生“豆苗”写一段会后点评' in prompt
    assert '[真人学生] 豆苗：我想知道做对题是不是就等于真懂。' in prompt


def test_parse_user_review_response_prefers_json_payload() -> None:
    raw = '''```json
    {"review": "点评：你在这次讨论里没有急着抢答案，而是先把‘会做题是不是就等于真懂’这个关键问题提了出来，这说明你能抓住讨论的核心。后来你又愿意顺着大家的话继续追问，让自己的想法一步步变清楚了。下次如果你能再多举一个自己做题时的真实小例子，你的观点会更有力量。"}
    ```'''

    assert parse_user_review_response(raw).startswith('你在这次讨论里没有急着抢答案')