from __future__ import annotations

from enum import Enum
from typing import Any

from completion.models import CompletionDecision, CompletionStatus


class NoReplyReason(str, Enum):
    """MECE classification for a job that reached a non-success terminal state.

    Each value is the stable persistence key used in terminal decision
    diagnostics and in the ``JobRecord``.
    """

    submit_failed = "submit_failed"
    agent_unreachable_dead = "agent_unreachable_dead"
    agent_parked = "agent_parked"
    agent_busy_queue_blocked = "agent_busy_queue_blocked"
    dispatch_handoff_skipped_degraded = "dispatch_handoff_skipped_degraded"
    dispatch_handoff_failed = "dispatch_handoff_failed"
    provider_usage_limit = "provider_usage_limit"
    provider_auth_failed = "provider_auth_failed"
    provider_api_error = "provider_api_error"
    provider_config_error = "provider_config_error"
    provider_empty_output = "provider_empty_output"
    provider_crashed = "provider_crashed"
    provider_waiting_for_user = "provider_waiting_for_user"
    completion_detection_gap = "completion_detection_gap"
    premature_completion = "premature_completion"
    reply_delivery_stalled = "reply_delivery_stalled"
    sender_mailbox_missed = "sender_mailbox_missed"


_NO_REPLY_REASON_META: dict[NoReplyReason, tuple[str, str]] = {
    NoReplyReason.submit_failed: (
        "提交失败 / submit failed",
        "检查目标 agent 是否在线、provider pane 是否可用；必要时重试或换 agent。",
    ),
    NoReplyReason.agent_unreachable_dead: (
        "agent 运行时不可达或已死亡 / agent runtime unreachable or dead",
        "检查 ccbd、agent pane、tmux 会话和主机进程；必要时重启 agent。",
    ),
    NoReplyReason.agent_parked: (
        "agent 已停放 / agent parked",
        "该 agent 处于 parked 状态，先 `ccb agent resume <agent>` 再重试。",
    ),
    NoReplyReason.agent_busy_queue_blocked: (
        "agent 正忙或队列阻塞 / agent busy or queue blocked",
        "等待当前任务完成，或检查是否有未消费的回复阻塞队列。",
    ),
    NoReplyReason.dispatch_handoff_skipped_degraded: (
        "dispatch handoff 因降级被跳过 / dispatch handoff skipped degraded",
        "dispatch 路径被降级跳过；检查 dispatcher 健康和 handoff 策略。",
    ),
    NoReplyReason.dispatch_handoff_failed: (
        "dispatch handoff 失败 / dispatch handoff failed",
        "检查 dispatcher 日志、网络连通性和目标 agent 注册状态。",
    ),
    NoReplyReason.provider_usage_limit: (
        "provider 用量限制 / provider usage limit",
        "等待 retry_after 时间后重试，或切换到其他 provider/account。",
    ),
    NoReplyReason.provider_auth_failed: (
        "provider 认证失败 / provider auth failed",
        "检查 provider 登录状态、API key、token；执行 `codex login` 或等效认证。",
    ),
    NoReplyReason.provider_api_error: (
        "provider API 错误 / provider api error",
        "这是 provider 侧 API 错误；等待后重试或联系 provider 支持。",
    ),
    NoReplyReason.provider_config_error: (
        "provider 配置错误 / provider config error",
        "检查 provider 配置、模型名、代理设置和必要环境变量。",
    ),
    NoReplyReason.provider_empty_output: (
        "provider 空输出 / provider empty output",
        "provider 返回了空回复；检查输入、provider 状态和日志。",
    ),
    NoReplyReason.provider_crashed: (
        "provider 进程崩溃 / provider crashed",
        "provider pane 或 runtime_pid 已消失；需要重启 provider/agent。",
    ),
    NoReplyReason.provider_waiting_for_user: (
        "provider 等待用户操作 / provider waiting for user",
        "provider 需要用户交互（如登录确认）；查看 pane 提示并完成操作。",
    ),
    NoReplyReason.completion_detection_gap: (
        "完成检测缺口 / completion detection gap",
        "未在超时前检测到完成信号；可检查 pane 输出或增大检测窗口后重试。",
    ),
    NoReplyReason.premature_completion: (
        "过早完成 / premature completion",
        "provider 提前标记完成但缺少有效回复；检查输入和 provider 行为。",
    ),
    NoReplyReason.reply_delivery_stalled: (
        "回复投递卡住 / reply delivery stalled",
        "回复已生成但无法投递到发送者邮箱；检查 mailbox/kernel 和投递路径。",
    ),
    NoReplyReason.sender_mailbox_missed: (
        "发送者邮箱未命中 / sender mailbox missed",
        "发送者未在约定窗口内消费回复；检查发送者状态或回调配置。",
    ),
}


_ERROR_KIND_MAP: dict[str, NoReplyReason] = {
    "provider_usage_limit": NoReplyReason.provider_usage_limit,
    "provider_auth_failed": NoReplyReason.provider_auth_failed,
    "provider_api_error": NoReplyReason.provider_api_error,
    "provider_config_error": NoReplyReason.provider_config_error,
    "provider_auth_required": NoReplyReason.provider_waiting_for_user,
    "provider_crashed": NoReplyReason.provider_crashed,
    "provider_error_text": NoReplyReason.provider_api_error,
    "provider_no_reply": NoReplyReason.provider_empty_output,
    "no_captured_reply": NoReplyReason.provider_empty_output,
}


_REASON_MAP: dict[str, NoReplyReason] = {
    "codex_prompt_delivery_failed": NoReplyReason.submit_failed,
    "pane_dead": NoReplyReason.provider_crashed,
    "runtime_state_corrupt": NoReplyReason.agent_unreachable_dead,
    "transport_error": NoReplyReason.agent_unreachable_dead,
    "runtime_unavailable": NoReplyReason.agent_unreachable_dead,
    "pane_unavailable": NoReplyReason.agent_unreachable_dead,
    "backend_unavailable": NoReplyReason.agent_unreachable_dead,
    "completion_timeout": NoReplyReason.completion_detection_gap,
    "project_shutdown": NoReplyReason.agent_unreachable_dead,
    "ccbd_restart_requires_resubmit": NoReplyReason.agent_unreachable_dead,
    "heartbeat_timeout": NoReplyReason.agent_unreachable_dead,
    "reply_delivery_restart_requeued": NoReplyReason.reply_delivery_stalled,
    "reply_delivery_transport_unavailable": NoReplyReason.reply_delivery_stalled,
    "reply_delivery_does_not_resume": NoReplyReason.reply_delivery_stalled,
    "callback_timeout": NoReplyReason.sender_mailbox_missed,
    "callback_continuation_submit_failed": NoReplyReason.sender_mailbox_missed,
    "turn_aborted": NoReplyReason.provider_api_error,
    "agent_busy": NoReplyReason.agent_busy_queue_blocked,
    "agent_parked": NoReplyReason.agent_parked,
    "dispatch_handoff_skipped_degraded": NoReplyReason.dispatch_handoff_skipped_degraded,
    "dispatch_handoff_failed": NoReplyReason.dispatch_handoff_failed,
    "premature_completion": NoReplyReason.premature_completion,
    "api_error": NoReplyReason.provider_api_error,
    "auth_failed": NoReplyReason.provider_auth_failed,
    "config_error": NoReplyReason.provider_config_error,
    "usage_limit": NoReplyReason.provider_usage_limit,
    "incomplete": NoReplyReason.completion_detection_gap,
    "failed": NoReplyReason.provider_api_error,
}


def describe_reason(reason: NoReplyReason) -> tuple[str, str]:
    """Return (zh label, triage hint) for a no-reply reason."""
    return _NO_REPLY_REASON_META.get(reason, (str(reason), "请联系运维 / contact operator."))


def resolve_no_reply_reason(
    decision: CompletionDecision,
) -> tuple[NoReplyReason, dict[str, Any]] | None:
    """Map a terminal ``CompletionDecision`` to a ``NoReplyReason``.

    Returns ``None`` for successful terminals (``status == completed``).
    Returns a tuple of ``(reason, detail)`` for non-success terminals.
    Raises ``ValueError`` when no mapping can be derived so that new failure
    modes are forced to declare a classification.
    """
    if decision.status is CompletionStatus.COMPLETED:
        return None

    diagnostics = dict(decision.diagnostics or {})

    explicit = str(diagnostics.get("no_reply_reason") or "").strip()
    if explicit:
        try:
            detail = {"source": "explicit", "no_reply_reason": explicit}
            provider_detail = diagnostics.get("no_reply_detail")
            if isinstance(provider_detail, dict):
                detail.update(provider_detail)
            return NoReplyReason(explicit), detail
        except ValueError:
            pass

    error_kind = str(diagnostics.get("error_kind") or "").strip().lower()
    if error_kind in _ERROR_KIND_MAP:
        return (
            _ERROR_KIND_MAP[error_kind],
            {"source": "error_kind", "error_kind": error_kind},
        )

    for diagnostic_key in ("error_type", "error_code"):
        raw = str(diagnostics.get(diagnostic_key) or "").strip().lower()
        if raw in _ERROR_KIND_MAP:
            return (
                _ERROR_KIND_MAP[raw],
                {"source": diagnostic_key, diagnostic_key: raw},
            )
        candidate = f"provider_{raw}"
        if candidate in _ERROR_KIND_MAP:
            return (
                _ERROR_KIND_MAP[candidate],
                {"source": diagnostic_key, diagnostic_key: raw},
            )

    decision_reason = str(decision.reason or "").strip().lower()
    if decision_reason in _REASON_MAP:
        return (
            _REASON_MAP[decision_reason],
            {"source": "reason", "reason": decision.reason},
        )

    if "reply_delivery" in decision_reason:
        return (
            NoReplyReason.reply_delivery_stalled,
            {"source": "reason", "reason": decision.reason},
        )
    if "callback" in decision_reason:
        return (
            NoReplyReason.sender_mailbox_missed,
            {"source": "reason", "reason": decision.reason},
        )
    if "handoff" in decision_reason:
        return (
            NoReplyReason.dispatch_handoff_failed,
            {"source": "reason", "reason": decision.reason},
        )

    reply = str(decision.reply or "").strip()
    if not reply and (
        diagnostics.get("provider_no_reply")
        or diagnostics.get("no_captured_reply")
        or diagnostics.get("reply_chars") == 0
    ):
        return (
            NoReplyReason.provider_empty_output,
            {"source": "empty_reply", "reply_chars": 0},
        )

    # Broad fallback so every non-success terminal job carries a classification.
    if decision.status is CompletionStatus.INCOMPLETE:
        return (
            NoReplyReason.completion_detection_gap,
            {"source": "fallback", "reason": decision.reason},
        )
    if decision.status is CompletionStatus.CANCELLED:
        return (
            NoReplyReason.agent_unreachable_dead,
            {"source": "fallback", "reason": decision.reason},
        )
    return (
        NoReplyReason.provider_api_error,
        {"source": "fallback", "reason": decision.reason},
    )


__all__ = ["NoReplyReason", "describe_reason", "resolve_no_reply_reason"]
