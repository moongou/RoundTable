from app.core.topics import FREE_TOPIC_CATEGORY_ID
from app.core.topics import build_free_topic_brief


def test_build_free_topic_brief_condenses_long_description_into_one_sentence() -> None:
    result = build_free_topic_brief(
        "我想讨论一个话题，就是现在很多小学生放学以后都会一直刷短视频，作业和睡觉都受影响，我们是不是应该限制他们使用手机？"
    )

    assert result["category"] == FREE_TOPIC_CATEGORY_ID
    assert "小学生" in result["title"]
    assert result["title"].endswith("？")
    assert "刷短视频" in result["description"]


def test_build_free_topic_brief_keeps_statement_readable_when_not_question() -> None:
    result = build_free_topic_brief(
        "今天我想聊的是，学校能不能在每天最后一节课留十分钟，让孩子自己安静整理这一天最重要的收获和疑问"
    )

    assert result["title"]
    assert result["title"].endswith(("？", "。", "…"))
    assert "整理这一天最重要的收获和疑问" in result["description"]