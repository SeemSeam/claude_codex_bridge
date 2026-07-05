from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from .models import PaneCompletionEvidence


ACTIVE_STATES = frozenset({"working", "tool_running", "reconnecting"})
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
STATUS_MARKER_RE = r"[•◦]"
WORKED_FOR_RE = re.compile(
    r"^[•✔✓]\s*worked\s+for\s+(?:\d+\s*h\s*)?(?:\d+\s*m\s*)?\d+\s*s\b",
    re.IGNORECASE,
)
CODEX_STATUS_LINE_RE = re.compile(rf"^{STATUS_MARKER_RE}")
CODEX_RECONNECT_LINE_RE = re.compile(rf"^{STATUS_MARKER_RE}\s*reconnecting\b", re.IGNORECASE)
CODEX_WORKING_LINE_RE = re.compile(
    rf"^{STATUS_MARKER_RE}\s*(?:working|running|booting mcp server:?)\b.*\((?:\d+\s*h\s*)?(?:\d+\s*m\s*)?\d+\s*s\b[^)]*(?:esc to interrupt|interrupt)[^)]*\)",
    re.IGNORECASE,
)
CODEX_TOOL_LINE_RE = re.compile(
    rf"^{STATUS_MARKER_RE}\s*(?:working|running)\b.*(?:\d+\s+background terminals? running|background terminals? running|/ps to view|shells? still running|running scheduled task)",
    re.IGNORECASE,
)

# --- marker tiers --------------------------------------------------------
# Pane-text markers are split into two tiers to prevent false-positive
# terminalization of healthy agents whose output merely *discusses* provider
# limits/errors (e.g. an agent researching the usage-limit classification).
#
# HIGH_CONFIDENCE_*  : specific multi-word provider banners. The ONLY tier that
#                      may drive AUTO-TERMINALIZATION (B.1 poll path) and the
#                      ONLY tier that may flip health to usage-limited /
#                      auth-failed / api-error / config-error (B.2 health
#                      bridge). Each entry is a phrase that does not appear in
#                      ordinary conversational agent output.
#
# The broad *_MARKERS tuples below remain available for the DIAGNOSTICS-only
# path (_delivery_pane_signal / _PANE_SIGNAL_ERROR_KIND): when a delivery has
# ALREADY failed for other reasons, broad attribution is acceptable, but it
# must never be the sole trigger for terminalization.
#
# Keep HIGH_CONFIDENCE_* and *_MARKERS in sync: every high-confidence marker
# is also a member of the broad set (so diagnostics still catch the specific
# case), but the broad set additionally contains generic words ("quota",
# "usage limit", "rate limit", "api error", ...) that fire on normal output.

ERROR_MARKERS = (
    "stream disconnected before completion",
    "error sending request for url",
    "failed to connect",
    "could not connect",
    "connection refused",
    "connection reset",
    "connection closed",
    "connection timed out",
    "request timed out",
    "model unavailable",
    "model_not_found",
    "rate limit",
    "rate_limit",
    "too many requests",
    "overloaded",
    "api error",
    "internal server error",
)
AUTH_REQUIRED_MARKERS = (
    "sign in with chatgpt",
    "sign in with device code",
    "provide your own api key",
    "connect an api key",
    "usage-based billing",
    "not logged in",
    "login required",
    "please sign in",
    "run codex login",
)
# Broad auth-failed markers (diagnostics-only). "unauthorized" alone is dropped
# from the high-confidence tier because it appears in normal output (e.g. an
# agent reviewing authz rules).
AUTH_FAILED_MARKERS = (
    "invalid api key",
    "incorrect api key",
    "authentication failed",
    "unauthorized",
    "401 unauthorized",
    "no api key provided",
)
# Broad api-error markers (diagnostics-only). Generic words like "rate limit",
# "api error", "overloaded", "model unavailable" are deliberately absent from
# the high-confidence tier: they routinely appear in agent output that is
# *discussing* rate limits or API errors.
API_ERROR_MARKERS = (
    "error sending request for url",
    "failed to connect",
    "could not connect",
    "connection refused",
    "connection reset",
    "connection closed",
    "connection timed out",
    "request timed out",
    "model unavailable",
    "model_not_found",
    "rate limit",
    "rate_limit",
    "too many requests",
    "overloaded",
    "api error",
    "internal server error",
    "bad request",
    "request failed",
)
# Broad config-error markers (diagnostics-only). "config.toml" and
# "model provider" alone are too generic for terminalization.
CONFIG_ERROR_MARKERS = (
    "failed to parse config",
    "invalid config",
    "invalid configuration",
    "unknown model provider",
    "model provider",
    "config.toml",
)
# Broad usage-limit markers (diagnostics-only). The generic words "usage
# limit", "quota", "try again at", "plan limit" are dropped from the
# high-confidence tier because an agent researching quota/usage-limit topics
# legitimately prints them in its output.
USAGE_LIMIT_MARKERS = (
    "hit your usage limit",
    "usage limit",
    "purchase more credits",
    "out of credits",
    "try again at",
    "quota",
    "plan limit",
)

# --- high-confidence tiers (may drive terminalization + health flip) ------
# usage_limit: only specific multi-word banners. Dropped: "usage limit",
# "quota", "try again at", "plan limit" — all observed in normal research
# output. Kept: "hit your usage limit" (codex banner), "purchase more
# credits" (banner CTA), "out of credits" (banner).
HIGH_CONFIDENCE_USAGE_LIMIT_MARKERS = (
    "hit your usage limit",
    "purchase more credits",
    "out of credits",
)
# auth_failed: keep concrete rejection phrases; drop bare "unauthorized".
HIGH_CONFIDENCE_AUTH_FAILED_MARKERS = (
    "invalid api key",
    "incorrect api key",
    "authentication failed",
    "401 unauthorized",
    "no api key provided",
)
# api_error: keep concrete transport/server-failure phrases that an agent
# would not emit while merely discussing errors; drop "rate limit", "api
# error", "overloaded", "model unavailable", "too many requests".
HIGH_CONFIDENCE_API_ERROR_MARKERS = (
    "stream disconnected before completion",
    "error sending request for url",
    "connection refused",
    "connection reset",
    "connection closed",
    "connection timed out",
    "request timed out",
    "internal server error",
    "bad gateway",
    "service unavailable",
)
# config_error: keep parse/validation phrases; drop bare "config.toml" /
# "model provider".
HIGH_CONFIDENCE_CONFIG_ERROR_MARKERS = (
    "failed to parse config",
    "invalid config",
    "invalid configuration",
    "unknown model provider",
)

# Map broad marker tuple -> high-confidence marker tuple, for strict mode.
_HIGH_CONFIDENCE_MARKERS = {
    USAGE_LIMIT_MARKERS: HIGH_CONFIDENCE_USAGE_LIMIT_MARKERS,
    AUTH_FAILED_MARKERS: HIGH_CONFIDENCE_AUTH_FAILED_MARKERS,
    API_ERROR_MARKERS: HIGH_CONFIDENCE_API_ERROR_MARKERS,
    CONFIG_ERROR_MARKERS: HIGH_CONFIDENCE_CONFIG_ERROR_MARKERS,
}

# Pane states that high_confidence_signal() / strict mode may surface as a
# terminal provider failure. Keeps the helper decoupled from marker tuples.
_HIGH_CONFIDENCE_TERMINAL_STATES = frozenset(
    {"usage_limit", "auth_failed", "api_error", "config_error"}
)
WAITING_MARKERS = (
    "do you trust the contents of this directory?",
    "press enter to continue",
    "approval required",
    "requires approval",
    "allow this command",
)
STATUS_CATALOG: dict[str, str] = {
    "completed": "Codex shows an explicit terminal summary such as Worked for.",
    "working": "Codex reports model/runtime work, usually with a Working/Running timer.",
    "tool_running": "A foreground or background tool/terminal is visibly running.",
    "waiting_for_user": "Codex is waiting for user confirmation, approval, trust, or menu input; auth prompts win when markers overlap.",
    "auth_required": "Codex is not logged in or is waiting for sign-in/API-key setup; this is checked before generic waiting text.",
    "auth_failed": "Codex reports authentication or API-key rejection.",
    "api_error": "Codex reports provider/API/model/rate-limit/server failure text.",
    "config_error": "Codex reports invalid provider/configuration text.",
    "usage_limit": "Account/quota exhaustion banner (usage limit / out of credits); terminal until reset or top-up.",
    "reconnecting": "Codex reports stream recovery or retrying connection; recoverable active state.",
    "failed": "Generic visible provider/runtime failure not classified above.",
    "pane_dead": "The tmux pane or server is gone.",
    "unknown": "Pane evidence is empty, contradictory, or not yet classified.",
}


@dataclass(frozen=True)
class PaneStatus:
    state: str
    reason: str
    matched_patterns: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    terminal_outcome: str | None = None
    completion_evidence: PaneCompletionEvidence | None = None
    retry_after: str | None = None

    def to_record(self) -> dict[str, object]:
        record: dict[str, object] = {
            "state": self.state,
            "reason": self.reason,
            "matched_patterns": list(self.matched_patterns),
            "notes": list(self.notes),
        }
        if self.terminal_outcome is not None:
            record["terminal_outcome"] = self.terminal_outcome
        if self.completion_evidence is not None:
            record["completion_evidence"] = self.completion_evidence.to_record()
        if self.retry_after is not None:
            record["retry_after"] = self.retry_after
        return record


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text or "")


def normalize_screen(text: str) -> str:
    cleaned = strip_ansi(text).replace("\r", "\n").replace("\xa0", " ")
    lines = [line.rstrip() for line in cleaned.splitlines()]
    return "\n".join(lines)


def parse_codex_pane_status(
    pane_text: str | None,
    *,
    pane_dead: bool = False,
    strict: bool = False,
) -> PaneStatus:
    """Classify visible Codex pane text using explicit tail-most evidence only.

    Active states must come from Codex status-line shaped rows such as
    "• Working (... esc to interrupt)"; body text containing the same words is
    ignored. Terminal completion is only an explicit terminal summary that
    appears no earlier than the last active status line. Everything else stays
    unknown instead of being inferred from quiet output or prompt visibility.

    ``strict`` selects the marker tier used for the provider-failure states
    (usage_limit / auth_failed / api_error / config_error):

    * ``strict=False`` (default, backwards compatible): uses the broad
      ``*_MARKERS`` tuples. Suitable for DIAGNOSTICS only (e.g. delivery-failure
      attribution), where a failure has already been established for other
      reasons and a broad attribution is acceptable.
    * ``strict=True``: uses only the ``HIGH_CONFIDENCE_*_MARKERS`` tuples. This
      is the ONLY mode that may drive AUTO-TERMINALIZATION (poll-path early
      termination) and health flips, because it will not match a healthy agent
      whose output merely *discusses* usage limits / quotas / api errors.

    The strict tier drops generic words ("usage limit", "quota", "rate limit",
    "api error", "overloaded", "unauthorized", "config.toml", ...) that have
    been observed to false-positive on agents researching those topics, and
    keeps only specific multi-word provider banners.
    """
    if pane_dead:
        return PaneStatus(
            "pane_dead",
            "pane_dead",
            ("pane_dead",),
            terminal_outcome="pane_dead",
            completion_evidence=PaneCompletionEvidence(
                "pane_dead",
                "codex_pane",
                "pane_dead",
            ),
        )

    normalized = normalize_screen(pane_text or "")
    recent_lines = [line.rstrip() for line in normalized.splitlines() if line.strip()]
    recent = " ".join(line.strip() for line in recent_lines[-20:]).lower()
    worked_for_index = _last_worked_for_index(recent_lines)
    active_status = _last_active_status(recent_lines)
    active_index = active_status[0]
    completed = worked_for_index >= 0 and worked_for_index >= active_index

    if not recent:
        return PaneStatus("unknown", "empty_capture")

    if completed:
        return PaneStatus(
            "completed",
            "codex_worked_for_terminal_summary",
            ("worked_for",),
            terminal_outcome="completed",
            completion_evidence=PaneCompletionEvidence(
                "completed",
                "codex_pane",
                "codex_worked_for_terminal_summary",
            ),
        )

    usage_markers = _strict_markers(USAGE_LIMIT_MARKERS) if strict else USAGE_LIMIT_MARKERS
    matches = _matched(usage_markers, recent)
    if matches:
        return PaneStatus(
            "usage_limit",
            "provider_usage_limit",
            matches,
            terminal_outcome="failed",
            retry_after=parse_retry_after(recent),
            completion_evidence=PaneCompletionEvidence(
                "failed",
                "codex_pane",
                "provider_usage_limit",
            ),
        )

    matches = _matched(AUTH_REQUIRED_MARKERS, recent)
    if matches:
        return PaneStatus(
            "auth_required",
            "codex_auth_required",
            matches,
        )

    auth_failed_markers = _strict_markers(AUTH_FAILED_MARKERS) if strict else AUTH_FAILED_MARKERS
    matches = _matched(auth_failed_markers, recent)
    if matches:
        return PaneStatus(
            "auth_failed",
            "provider_auth_failed",
            matches,
            terminal_outcome="failed",
            completion_evidence=PaneCompletionEvidence(
                "failed",
                "codex_pane",
                "provider_auth_failed",
            ),
        )

    config_markers = _strict_markers(CONFIG_ERROR_MARKERS) if strict else CONFIG_ERROR_MARKERS
    matches = _matched(config_markers, recent)
    if matches:
        return PaneStatus(
            "config_error",
            "provider_config_error",
            matches,
            terminal_outcome="failed",
            completion_evidence=PaneCompletionEvidence(
                "failed",
                "codex_pane",
                "provider_config_error",
            ),
        )

    if active_status[1] == "reconnecting":
        return PaneStatus(
            "reconnecting",
            "provider_reconnecting",
            ("reconnecting_status_line",),
            ("recoverable_stream_retry_visible",),
        )

    api_markers = _strict_markers(API_ERROR_MARKERS) if strict else API_ERROR_MARKERS
    matches = _matched(api_markers, recent)
    if matches:
        return PaneStatus(
            "api_error",
            "provider_api_error",
            matches,
            terminal_outcome="failed",
            completion_evidence=PaneCompletionEvidence(
                "failed",
                "codex_pane",
                "provider_api_error",
            ),
        )

    # Generic ERROR_MARKERS fallback. There is no high-confidence tier for this
    # catch-all: in strict mode we skip it entirely, because it contains the
    # exact generic words ("rate limit", "api error", "model unavailable",
    # "overloaded") that false-positive on agents researching those topics.
    # Specific transport failures are already captured above via
    # HIGH_CONFIDENCE_API_ERROR_MARKERS (surfacing as `api_error`), so skipping
    # the generic `failed` block in strict mode loses no real terminal signal.
    if not strict:
        matches = _matched(ERROR_MARKERS, recent)
        if matches:
            return PaneStatus(
                "failed",
                "provider_error_text",
                matches,
                terminal_outcome="failed",
                completion_evidence=PaneCompletionEvidence(
                    "failed",
                    "codex_pane",
                    "provider_error_text",
                ),
            )

    if active_status[1] == "tool_running":
        return PaneStatus(
            "tool_running",
            "provider_tool_running",
            ("tool_status_line",),
        )

    if active_status[1] == "working":
        return PaneStatus(
            "working",
            "codex_working_status_line",
            ("running_status_time", "working_status_line"),
        )

    matches = _matched(WAITING_MARKERS, recent)
    if matches and "press esc to interrupt" not in recent and _waiting_prompt_near_tail(recent_lines):
        return PaneStatus("waiting_for_user", "provider_waiting_for_user", matches)

    return PaneStatus("unknown", "no_known_status_pattern")


def _matched(markers: tuple[str, ...], text: str) -> tuple[str, ...]:
    return tuple(marker for marker in markers if marker in text)


def _strict_markers(broad_markers: tuple[str, ...]) -> tuple[str, ...]:
    """Return the high-confidence marker subset for a broad marker tuple."""
    return _HIGH_CONFIDENCE_MARKERS.get(broad_markers, broad_markers)


def high_confidence_signal(
    pane_text: str | None,
    *,
    pane_dead: bool = False,
) -> PaneStatus | None:
    """Return a PaneStatus ONLY when pane text shows a HIGH-CONFIDENCE provider
    failure banner, else None.

    This is the safe entry point for AUTO-TERMINALIZATION decisions: it never
    matches the broad marker set, so a healthy agent whose output merely
    discusses usage limits / quotas / api errors is never terminalized. Use
    :func:`parse_codex_pane_status` (broad mode) for diagnostics.
    """
    parsed = parse_codex_pane_status(pane_text, pane_dead=pane_dead, strict=True)
    if parsed.state in _HIGH_CONFIDENCE_TERMINAL_STATES:
        return parsed
    return None


# "try again at Jul 2nd, 2026 10:21 AM" -> capture the date/time tail.
RETRY_AFTER_RE = re.compile(
    r"try\s+again\s+at\s+([a-z]+\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\s+\d{1,2}:\d{2}\s*(?:am|pm))",
    re.IGNORECASE,
)


def parse_retry_after(text: str) -> str | None:
    """Parse a "try again at <Mon Nth, YYYY H:MM AM/PM>" banner into an ISO timestamp.

    Returns the ISO-8601 string (e.g. "2026-07-02T10:21:00") or None if no
    recognizable reset timestamp is present or parsing fails. Ordinal suffixes
    (2nd, 3rd, etc.) are stripped before strptime.
    """
    if not text:
        return None
    match = RETRY_AFTER_RE.search(text)
    if match is None:
        return None
    raw = match.group(1).strip()
    # Strip ordinal suffixes (1st, 2nd, 3rd, 4th) -> 1, 2, 3, 4 for strptime "%d".
    cleaned = re.sub(r"(\d{1,2})(st|nd|rd|th)", r"\1", raw, flags=re.IGNORECASE)
    # Normalize stray commas/spaces; strptime is strict about a single space.
    cleaned = re.sub(r"\s+", " ", cleaned).replace(",", ", ").replace(",  ", ", ")
    for fmt in ("%b %d, %Y %I:%M %p", "%B %d, %Y %I:%M %p"):
        try:
            return datetime.strptime(cleaned, fmt).isoformat()
        except ValueError:
            continue
    return None


def _last_worked_for_index(lines: list[str]) -> int:
    for index in range(len(lines) - 1, max(-1, len(lines) - 13), -1):
        line = lines[index]
        if WORKED_FOR_RE.search(line):
            return index
    return -1


def _last_active_status(lines: list[str]) -> tuple[int, str | None, str]:
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index]
        if not CODEX_STATUS_LINE_RE.search(line):
            continue
        if CODEX_RECONNECT_LINE_RE.search(line):
            return index, "reconnecting", line
        if CODEX_TOOL_LINE_RE.search(line):
            return index, "tool_running", line
        if CODEX_WORKING_LINE_RE.search(line):
            return index, "working", line
    return -1, None, ""


def _waiting_prompt_near_tail(lines: list[str]) -> bool:
    tail = " ".join(lines[-12:]).lower()
    return bool(_matched(WAITING_MARKERS, tail))


__all__ = [
    "ACTIVE_STATES",
    "AUTH_FAILED_MARKERS",
    "API_ERROR_MARKERS",
    "CONFIG_ERROR_MARKERS",
    "ERROR_MARKERS",
    "HIGH_CONFIDENCE_API_ERROR_MARKERS",
    "HIGH_CONFIDENCE_AUTH_FAILED_MARKERS",
    "HIGH_CONFIDENCE_CONFIG_ERROR_MARKERS",
    "HIGH_CONFIDENCE_USAGE_LIMIT_MARKERS",
    "PaneCompletionEvidence",
    "PaneStatus",
    "RETRY_AFTER_RE",
    "STATUS_CATALOG",
    "USAGE_LIMIT_MARKERS",
    "high_confidence_signal",
    "normalize_screen",
    "parse_codex_pane_status",
    "parse_retry_after",
    "strip_ansi",
]
