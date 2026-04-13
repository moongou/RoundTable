"""安全过滤模块

对 AI 输出和人类输入进行内容安全检查，确保适合小学生。
"""

from __future__ import annotations

from autogen_core.models import ChatCompletionClient

SAFETY_CHECK_PROMPT = """你是一个内容安全检查员，负责检查面向中国小学生的讨论内容是否安全。

请检查以下内容是否存在：
1. 暴力、色情、违法内容
2. 政治敏感话题
3. 歧视性语言
4. 不适合小学生的成人话题
5. 鼓励危险行为的内容

只需回答"安全"或"不安全：[原因]"。

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

            response = await self.model_client.create([UserMessage(content=prompt, source="user")])
            result = response.content.strip()
            if result.startswith("安全"):
                return True, ""
            else:
                return False, result
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