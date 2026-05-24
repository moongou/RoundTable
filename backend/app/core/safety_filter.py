"""安全过滤模块

对 AI 输出和人类输入进行内容安全检查，确保适合小学生。
"""

from __future__ import annotations

import asyncio
import logging

from autogen_core.models import ChatCompletionClient

logger = logging.getLogger(__name__)

# 安全检查最长等待时间（秒）。超时后默认放行，避免卡住整个讨论流程。
_SAFETY_CHECK_TIMEOUT_SEC = 8.0

SAFETY_CHECK_PROMPT = """你是面向中国小学生讨论的内容安全检查员。

判定规则（非常重要）：
- 默认**放行**，仅当内容**明确**含有以下情况时才判定为"不安全"：
  1. 明确、具体的暴力/血腥/色情描写
  2. 怂恿伤害自己或他人的内容
  3. 明显的政治敏感个案（默认不包含日常话题）
  4. 明显的种族/性别/地域歧视攻击
  5. 明确教唆违法犯罪

以下内容**一律视为安全**：
- 讨论哲学、历史、科学、伦理、成长烦恼、家庭关系、死亡意义、宗教文化等开放话题
- 有深度但不低俗的思辨表达
- 文学、艺术、故事、寓言引用
- 可能有争议但属于常识范畴的观点

回答格式：
- 安全 → 只回答"安全"
- 不安全 → 回答"不安全：[非常简短的原因]"

待检查内容：{content}"""


class SafetyFilter:
    """内容安全过滤器。

    使用 LLM 对内容进行安全检查。为了降低延迟，
    输出检查在 TTS 之前异步执行，大部分内容通过零延迟。
    """

    def __init__(self, model_client: ChatCompletionClient):
        self.model_client = model_client

    async def check(self, content: str) -> tuple[bool, str]:
        """检查内容是否安全。

        Args:
            content: 待检查的文本内容。

        Returns:
            (is_safe, reason) 元组。如果安全，reason 为空字符串。
        """
        if not content or not content.strip():
            return True, ""

        prompt = SAFETY_CHECK_PROMPT.format(content=content[:500])
        try:
            from autogen_core.models import UserMessage

            response = await asyncio.wait_for(
                self.model_client.create([UserMessage(content=prompt, source="user")]),
                timeout=_SAFETY_CHECK_TIMEOUT_SEC,
            )
            result = response.content.strip()
            if result.startswith("安全"):
                return True, ""
            else:
                return False, result
        except asyncio.TimeoutError:
            logger.warning(
                "[SafetyFilter] 安全检查超时（%.1fs），默认视为安全以避免阻断讨论",
                _SAFETY_CHECK_TIMEOUT_SEC,
            )
            return True, ""
        except Exception:
            # 如果安全检查本身失败，默认放行（避免因检查服务故障阻断正常讨论）
            return True, ""

    async def filter_or_rewrite(self, content: str) -> str:
        """检查内容安全性，如果不安全则替换为安全消息。

        Args:
            content: 待检查的文本内容。

        Returns:
            原始内容（如果安全）或替换消息（如果不安全）。
        """
        is_safe, reason = await self.check(content)
        if is_safe:
            return content
        return "（这段内容不太适合讨论，让我们换个角度继续吧。）"

    async def check_human_input(self, content: str) -> tuple[bool, str]:
        """检查人类输入是否安全。

        与 check() 类似，但对人类输入更宽松（如简单的口语表达不应被误拦）。
        """
        return await self.check(content)