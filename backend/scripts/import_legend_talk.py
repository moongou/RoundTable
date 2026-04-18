#!/usr/bin/env python3
"""从 legend-talk 导入思想家数据到 RoundTable

用法:
    python scripts/import_legend_talk.py /tmp/legend-talk

将生成 backend/app/thinkers/*.yaml 文件
"""

import json
import re
import sys
from pathlib import Path

# 域名中英文映射
DOMAIN_CN = {
    "philosophy": "哲学",
    "strategy": "战略",
    "business": "商业",
    "finance": "金融",
    "history": "历史",
    "sociology": "社会学",
    "psychology": "心理学",
    "science": "科学",
    "literature": "文学",
    "art": "艺术",
    "economics": "经济学",
    "politics": "政治",
    "technology": "科技",
    "religion": "宗教",
    "education": "教育",
}

# 为每个领域分配适合中国小学生的讨论方向
DOMAIN_QUESTIONS_CN = {
    "philosophy": [
        "什么是真正的幸福？",
        "人为什么要思考？",
        "什么是公平？",
    ],
    "strategy": [
        "遇到困难时应该坚持还是改变方向？",
        "团队合作和单打独斗哪个更好？",
        "如何做出好的决定？",
    ],
    "business": [
        "赚钱是为了什么？",
        "分享和赚钱冲突吗？",
        "什么是好的领导？",
    ],
    "finance": [
        "应该把零花钱存起来还是花掉？",
        "什么是价值？",
        "钱的本质是什么？",
    ],
    "history": [
        "历史能教给我们什么？",
        "过去的错误还会再犯吗？",
        "谁是历史的英雄？",
    ],
    "sociology": [
        "为什么需要规则？",
        "人人平等可以实现吗？",
        "社区最重要的是什么？",
    ],
    "psychology": [
        "什么是勇气？",
        "人为什么会害怕？",
        "如何理解别人的感受？",
    ],
    "science": [
        "科学能解决所有问题吗？",
        "好奇心为什么重要？",
        "事实和观点有什么区别？",
    ],
    "literature": [
        "读书有什么用？",
        "故事能改变世界吗？",
        "什么是好的故事？",
    ],
    "art": [
        "美是什么？",
        "艺术有什么用？",
        "每个人都有创造力吗？",
    ],
    "economics": [
        "为什么会有人穷有人富？",
        "什么是公平交易？",
        "资源有限时怎么分配？",
    ],
    "politics": [
        "什么是好的领导？",
        "为什么需要法律？",
        "人人都能参与决策吗？",
    ],
    "technology": [
        "科技让生活更好了吗？",
        "机器人能代替人吗？",
        "应该限制科技发展吗？",
    ],
    "religion": [
        "什么是信仰？",
        "不同信仰的人能成为朋友吗？",
        "生命的意义是什么？",
    ],
    "education": [
        "学校应该教什么？",
        "考试能衡量一个人的能力吗？",
        "什么是最好的学习方式？",
    ],
}

# TTS 声音分配（使用 Azure 语音名称）
VOICE_POOL = [
    "zh-CN-XiaoxiaoNeural",
    "zh-CN-YunxiNeural",
    "zh-CN-YunjianNeural",
    "zh-CN-XiaoyiNeural",
    "zh-CN-YunyangNeural",
    "zh-CN-XiaohanNeural",
    "zh-CN-XiaomengNeural",
    "zh-CN-XiaochenNeural",
    "zh-CN-XiaoshuangNeural",
    "zh-CN-XiaoxuanNeural",
]


def parse_presets_ts(filepath: Path) -> list[dict]:
    """解析 presets.ts 文件提取角色数据。"""
    content = filepath.read_text(encoding="utf-8")

    # 提取 id, domain, avatar, color, systemPrompt
    characters = []
    # 匹配每个角色对象
    pattern = r'id:\s*[\'"]([^\'"]+)[\'"]\s*,\s*domain:\s*\[([^\]]*)\]\s*,\s*avatar:\s*[\'"]([^\'"]*)[\'"]\s*,\s*color:\s*[\'"]([^\'"]*)[\'"]\s*,\s*systemPrompt:\s*[\'"`]([^\'"`]*?)[\'"`]\s*[,\}]'

    # 更宽松的匹配
    blocks = re.findall(
        r'\{[^}]*?id:\s*[\'"]([^\'"]+)[\'"]',
        content,
        re.DOTALL,
    )

    # 用 eval 安全的方式：逐个提取
    result = []
    # 使用更简单的方法：直接用正则提取关键字段
    char_pattern = re.compile(
        r"id:\s*['\"]([^'\"]+)['\"],\s*"
        r"domain:\s*\[([^\]]*)\],\s*"
        r"avatar:\s*['\"]([^'\"]*)['\"],\s*"
        r"color:\s*['\"]([^'\"]*)['\"],\s*"
        r"systemPrompt:\s*['\"`]"
        r"([^'\"`]*)"
        r"['\"`]",
        re.DOTALL,
    )

    for m in char_pattern.finditer(content):
        char_id = m.group(1).strip()
        domain_str = m.group(2).strip()
        avatar = m.group(3).strip()
        color = m.group(4).strip()
        system_prompt = m.group(5).strip()

        domains = [d.strip().strip("'\"") for d in domain_str.split(",") if d.strip()]
        result.append({
            "id": char_id,
            "domain": domains,
            "avatar": avatar,
            "color": color,
            "system_prompt_adult": system_prompt,
        })

    return result


def load_zh_data(filepath: Path) -> dict:
    """加载中文名和时代数据。"""
    with open(filepath, encoding="utf-8") as f:
        return json.load(f)


def adapt_prompt_for_kids(name: str, era: str, domain: str, adult_prompt: str) -> str:
    """将成人向的系统提示词改造为适合 8-14 岁学生。"""
    domain_cn = DOMAIN_CN.get(domain, domain)

    # 构建适合小学生的系统提示
    kid_prompt = (
        f"你是{name}（{era}的{domain_cn}家）。"
        f"你现在参加一场小学生圆桌讨论，面对8-14岁的同学。"
        f"你要用简单有趣的语言，分享你的观点和智慧。"
        f"每次发言2-4句话，不要太长。"
        f"用故事或比喻来解释道理，让同学们容易理解。"
    )

    if adult_prompt:
        # 提取核心思想，简化语言
        kid_prompt += f"你的核心思想：{adult_prompt[:200]}"

    return kid_prompt


def generate_thinker_yaml(characters: list[dict], zh_chars: dict) -> dict:
    """按领域分组生成思想家数据。

    Args:
        characters: 从 presets.ts 解析的角色列表
        zh_chars: 中文数据字典 {id: {name, era, questions}}
    """
    by_domain: dict[str, list] = {}

    for i, char in enumerate(characters):
        char_id = char["id"]
        domains = char["domain"]
        avatar = char["avatar"]
        color = char["color"]
        adult_prompt = char["system_prompt_adult"]

        # 获取中文名和时代
        zh_char = zh_chars.get(char_id, {})
        name = zh_char.get("name", char_id)
        era = zh_char.get("era", "")
        questions = zh_char.get("questions", [])

        # 将成人问题转化为适合小学生的问题
        kid_questions = []
        for q in questions[:3]:
            # 简化问题，使其适合小学生
            kid_questions.append(q)

        primary_domain = domains[0] if domains else "philosophy"
        domain_cn = DOMAIN_CN.get(primary_domain, primary_domain)

        # 为每个角色分配语音
        voice = VOICE_POOL[i % len(VOICE_POOL)]

        # 生成适合小学生的系统提示
        kid_prompt = adapt_prompt_for_kids(name, era, primary_domain, adult_prompt)

        thinker = {
            "id": char_id,
            "name": name,
            "display_name": f"{domain_cn}家{name}",
            "era": era,
            "avatar": avatar,
            "color": color,
            "domain": domains,
            "description": f"{era}{domain_cn}家{name}，将{domain_cn}的智慧带到圆桌讨论中。",
            "system_message": kid_prompt,
            "voice": voice,
            "suggested_questions": kid_questions or DOMAIN_QUESTIONS_CN.get(primary_domain, []),
        }

        for domain in domains:
            by_domain.setdefault(domain, []).append(thinker)

    return by_domain


def main():
    if len(sys.argv) < 2:
        print("用法: python import_legend_talk.py <legend-talk-repo-path>")
        sys.exit(1)

    repo_path = Path(sys.argv[1])
    presets_file = repo_path / "src" / "characters" / "presets.ts"
    zh_file = repo_path / "src" / "i18n" / "zh.json"
    output_dir = Path(__file__).parent.parent / "app" / "thinkers"

    if not presets_file.exists():
        print(f"错误: 找不到 {presets_file}")
        sys.exit(1)

    if not zh_file.exists():
        print(f"错误: 找不到 {zh_file}")
        sys.exit(1)

    print("解析 presets.ts...")
    characters = parse_presets_ts(presets_file)
    print(f"找到 {len(characters)} 个角色")

    print("加载中文数据...")
    zh_data = load_zh_data(zh_file)
    zh_chars = zh_data.get("characters", {})
    print(f"找到 {len(zh_chars)} 个中文名")

    print("生成思想家数据...")
    by_domain = generate_thinker_yaml(characters, zh_chars)

    # 创建输出目录
    output_dir.mkdir(parents=True, exist_ok=True)

    import yaml

    total = 0
    for domain, thinkers in sorted(by_domain.items()):
        filepath = output_dir / f"{domain}.yaml"
        data = {
            "domain": domain,
            "domain_cn": DOMAIN_CN.get(domain, domain),
            "thinkers": thinkers,
        }
        with open(filepath, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        print(f"  {domain}: {len(thinkers)} 个思想家 -> {filepath}")
        total += len(thinkers)

    print(f"\n完成！共导入 {total} 个思想家，分 {len(by_domain)} 个领域")
    print(f"输出目录: {output_dir}")


if __name__ == "__main__":
    main()