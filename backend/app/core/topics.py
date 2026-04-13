"""预设思辨话题库

为小学生提供适合年龄的思辨讨论话题，
覆盖科学、伦理、社会、文学等领域。
"""

from __future__ import annotations

from app.models.session import Topic

# 预设话题列表
PRESET_TOPICS: list[Topic] = [
    Topic(
        id="school-homework",
        title="小学生应该有家庭作业吗？",
        description="有些同学觉得放学后应该好好玩，有些同学觉得做作业可以巩固学到的知识。你觉得呢？",
        category="教育",
        age_range="8-12",
        guide_questions=[
            "家庭作业帮你学到了什么？",
            "如果没有作业，你会怎么安排放学后的时间？",
            "你觉得多少作业是合适的？",
        ],
        tags=["学习", "时间管理", "自我管理"],
    ),
    Topic(
        id="animal-zoo",
        title="动物应该住在动物园里吗？",
        description="动物园让更多人看到了各种动物，但动物可能更喜欢自由。你怎么看？",
        category="伦理",
        age_range="8-12",
        guide_questions=[
            "去动物园的时候你看到了什么？",
            "如果你是动物，你愿意住在动物园吗？",
            "有没有既能保护动物又让它们自由的方法？",
        ],
        tags=["动物保护", "自由", "责任"],
    ),
    Topic(
        id="screen-time",
        title="我们应该限制看电子屏幕的时间吗？",
        description="手机和平板让我们的生活更方便，但看太久可能对眼睛和身体不好。你觉得应该怎么平衡？",
        category="健康",
        age_range="8-12",
        guide_questions=[
            "你平时用电子设备做什么？",
            "看屏幕太久有什么不好的影响？",
            "你能想到一些减少看屏幕时间的好办法吗？",
        ],
        tags=["健康", "自律", "科技"],
    ),
    Topic(
        id="school-rules",
        title="学校里应该有更多规则还是更少规则？",
        description="规则可以帮助大家有序地学习和生活，但太多规则可能让人觉得不自由。你觉得呢？",
        category="社会",
        age_range="8-12",
        guide_questions=[
            "你觉得学校里最重要的规则是什么？",
            "有没有你觉得不必要的规则？",
            "如果让大家自己制定规则，会怎么样？",
        ],
        tags=["规则", "自由", "秩序"],
    ),
    Topic(
        id="sharing-invention",
        title="如果你发明了一个很棒的东西，会免费分享还是卖钱？",
        description="免费分享可以让更多人受益，但卖钱可以激励更多人去发明创造。你选哪个？",
        category="伦理",
        age_range="8-12",
        guide_questions=[
            "你有没有自己发明或创造过什么东西？",
            "如果你花了很久才发明一样东西，你会怎么决定？",
            "有没有既能分享又能激励发明者的方法？",
        ],
        tags=["分享", "创造", "公平"],
    ),
    Topic(
        id="lie-to-help",
        title="说谎有时候是正确的吗？",
        description="我们都知道不应该说谎，但有时候说谎可能是为了帮助别人。你怎么看？",
        category="伦理",
        age_range="8-12",
        guide_questions=[
            "你有没有遇到过说谎反而帮助了别人的情况？",
            "善意的谎言和恶意的谎言有什么区别？",
            "如果朋友问你'我好看吗？'，你会怎么回答？",
        ],
        tags=["诚实", "善意", "判断力"],
    ),
    Topic(
        id="robot-friend",
        title="机器人能成为真正的朋友吗？",
        description="现在的机器人越来越聪明，可以陪我们聊天、玩游戏。但它们能算是真正的朋友吗？",
        category="科技",
        age_range="8-12",
        guide_questions=[
            "你觉得朋友最重要的特质是什么？",
            "机器人能做到这些特质吗？哪些能，哪些不能？",
            "如果你有一个机器人朋友，你希望它能做什么？",
        ],
        tags=["友谊", "科技", "情感"],
    ),
    Topic(
        id="everyone-winner",
        title="比赛中每个人都应该得奖吗？",
        description="有人说参与最重要，每个人都应该得到鼓励。也有人说只有最优秀的人才应该得奖，这样才有动力。你怎么看？",
        category="社会",
        age_range="8-12",
        guide_questions=[
            "你参加过什么比赛？得了奖吗？感觉怎么样？",
            "如果每个人都得奖，比赛还有什么意义？",
            "有没有办法既鼓励参与又奖励优秀？",
        ],
        tags=["竞争", "公平", "鼓励"],
    ),
    Topic(
        id="save-money-or-spend",
        title="应该把零花钱存起来还是花掉？",
        description="存钱可以买到更贵的东西，花掉可以马上享受快乐。你会怎么选？",
        category="生活",
        age_range="8-12",
        guide_questions=[
            "你有零花钱吗？你通常会怎么用？",
            "你有没有为了买一样东西而存了很久的钱？值得吗？",
            "存钱和花钱之间，你能想到一个好的平衡方法吗？",
        ],
        tags=["理财", "选择", "延迟满足"],
    ),
    Topic(
        id="book-vs-movie",
        title="书和电影哪个更好？",
        description="书可以让你想象画面，电影能直接看到精彩的故事。你喜欢哪一个？",
        category="文学",
        age_range="8-12",
        guide_questions=[
            "你最近读了什么好书或看了什么好电影？",
            "有没有一本书改编成电影后你觉得更好或更差的？",
            "书能做到什么电影做不到的事？反过来呢？",
        ],
        tags=["阅读", "想象力", "艺术"],
    ),
]


def get_all_topics() -> list[Topic]:
    """获取所有预设话题。"""
    return PRESET_TOPICS


def get_topic_by_id(topic_id: str) -> Topic | None:
    """根据 ID 获取话题。"""
    for topic in PRESET_TOPICS:
        if topic.id == topic_id:
            return topic
    return None