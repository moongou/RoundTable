"""Friendly AI model error classification.

The OpenAI-compatible client stack often includes long tracebacks in exception
strings.  UI/history should get concise, actionable messages; logs can keep the
full traceback via ``logger.exception`` at the catch site.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelErrorInfo:
    message: str
    technical_detail: str
    kind: str
    recoverable: bool
    is_model_error: bool = True

    def to_event_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "message": self.message,
            "recoverable": self.recoverable,
            "kind": self.kind,
        }
        if self.technical_detail:
            data["technical_detail"] = self.technical_detail
            data["original_error"] = self.technical_detail
        return data


def _compact_error_text(exc: BaseException | str) -> str:
    text = str(exc or "").strip()
    if not text:
        return ""

    traceback_markers = (
        "\nTraceback:",
        "\nTraceback (most recent call last):",
        "\n  File ",
    )
    for marker in traceback_markers:
        if marker in text:
            text = text.split(marker, 1)[0].strip()

    text = re.sub(r"\s+", " ", text).strip()
    return text[:900]


def describe_model_error(exc: BaseException | str) -> ModelErrorInfo:
    detail = _compact_error_text(exc)
    lower = detail.lower()

    if not detail:
        return ModelErrorInfo(
            message="讨论过程出现异常，但没有返回具体错误信息。请稍后重试，或切换模型提供商。",
            technical_detail="",
            kind="unknown",
            recoverable=True,
            is_model_error=False,
        )

    if any(marker in lower for marker in ("api_key", "authentication", "unauthorized", "401")):
        return ModelErrorInfo(
            message="AI 模型认证失败：API Key 无效、缺失或已过期。请在设置页检查当前模型提供商的 API Key。",
            technical_detail=detail,
            kind="auth_failed",
            recoverable=False,
        )

    if "cloudflare tunnel" in lower or "error code: 530" in lower:
        return ModelErrorInfo(
            message=(
                "AI 模型服务暂时无法访问：当前 Base URL 使用的 Cloudflare Tunnel "
                "无法连接到后端服务。请确认模型服务已启动、Tunnel 在线、Base URL 正确，"
                "或先切换到其他模型提供商后重试。"
            ),
            technical_detail=detail,
            kind="provider_unreachable",
            recoverable=True,
        )

    if any(
        marker in lower
        for marker in (
            "insufficient_quota",
            "insufficient quota",
            "quota",
            "billing",
            "balance",
            "余额",
            "credit",
            "token不足",
            "tokens不足",
        )
    ):
        return ModelErrorInfo(
            message=(
                "AI 模型账户额度不足或 Token 已用尽，讨论暂时无法继续。请充值当前账户，"
                "或在设置页切换到仍有余额的模型提供商后重试。"
            ),
            technical_detail=detail,
            kind="quota_or_tokens_exhausted",
            recoverable=True,
        )

    if any(marker in lower for marker in ("rate limit", "rate_limit", "too many requests", "429")):
        return ModelErrorInfo(
            message="AI 模型调用频率受限。请稍等一会儿再继续，或切换到其他模型提供商。",
            technical_detail=detail,
            kind="rate_limited",
            recoverable=True,
        )

    if any(
        marker in lower
        for marker in (
            "context length",
            "maximum context",
            "max context",
            "tokens requested",
            "reduce the length",
            "prompt is too long",
            "context_length_exceeded",
        )
    ):
        return ModelErrorInfo(
            message=(
                "AI 模型上下文长度不足，本次讨论内容或角色提示超过了模型可处理的 Token 上限。"
                "请减少参与角色/轮数，或切换到更长上下文的模型后重试。"
            ),
            technical_detail=detail,
            kind="context_length_exceeded",
            recoverable=True,
        )

    if "model" in lower and any(
        marker in lower for marker in ("not found", "not exist", "does not exist", "unknown model")
    ):
        return ModelErrorInfo(
            message="AI 模型名称不可用或不存在。请在设置页检查模型名称，或切换到该服务支持的模型。",
            technical_detail=detail,
            kind="model_not_found",
            recoverable=False,
        )

    if any(marker in lower for marker in ("permission", "forbidden", "access denied", "403")):
        return ModelErrorInfo(
            message="当前 API Key 没有访问这个 AI 模型的权限。请更换有权限的 Key，或切换到其他模型。",
            technical_detail=detail,
            kind="permission_denied",
            recoverable=False,
        )

    if isinstance(
        exc,
        (
            AttributeError,
            TypeError,
            NameError,
            KeyError,
            IndexError,
            AssertionError,
            UnboundLocalError,
            SyntaxError,
        ),
    ) or any(
        marker in lower
        for marker in (
            "object has no attribute",
            "attributeerror",
            "typeerror",
            "nameerror",
            "keyerror",
            "indexerror",
            "assertionerror",
            "unboundlocalerror",
            "syntaxerror",
        )
    ):
        return ModelErrorInfo(
            message="讨论运行时出现内部异常，当前请求没有正确完成。请稍后重试；如果持续出现，请检查最近的代码变更。",
            technical_detail=detail,
            kind="internal_runtime_error",
            recoverable=True,
            is_model_error=False,
        )

    if any(
        marker in lower
        for marker in (
            "connection",
            "connect",
            "timeout",
            "timed out",
            "network",
            "dns",
            "ssl",
            "httpcore",
            "httpx",
        )
    ):
        return ModelErrorInfo(
            message="无法连接到 AI 模型服务。请检查网络、Base URL、本地/远端模型服务是否启动，然后重试。",
            technical_detail=detail,
            kind="network_or_timeout",
            recoverable=True,
        )

    if any(marker in lower for marker in ("content_filter", "content policy", "safety")):
        return ModelErrorInfo(
            message="AI 模型拒绝了本次请求内容。请调整话题或发言内容后重试。",
            technical_detail=detail,
            kind="content_rejected",
            recoverable=True,
        )

    if any(marker in lower for marker in ("internalservererror", "internal server error", " 5", "500", "502", "503", "504")):
        return ModelErrorInfo(
            message="AI 模型服务端暂时异常，当前请求没有获得内容。请稍后重试，或切换到其他模型提供商。",
            technical_detail=detail,
            kind="provider_server_error",
            recoverable=True,
        )

    return ModelErrorInfo(
        message="讨论运行时出现异常，系统没有获得 AI 模型返回内容。请稍后重试，或切换模型提供商。",
        technical_detail=detail,
        kind="unknown",
        recoverable=True,
        is_model_error=False,
    )