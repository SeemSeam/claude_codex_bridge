from __future__ import annotations

from .options import normalize_expected_user_options, normalize_user_option_names, pane_matches_expected


def pane_exists(service, pane_id: str) -> bool:
    if not service.looks_like_pane_id_fn(pane_id):
        return False
    cp = run_tmux_capture(service, ["display-message", "-p", "-t", pane_id, "#{pane_id}"], timeout=0.5)
    if cp is None:
        return False
    return getattr(cp, "returncode", 1) == 0 and service.pane_exists_output_fn(getattr(cp, "stdout", "") or "")


def get_current_pane_id(service, *, env_pane: str) -> str:
    env_pane = (env_pane or "").strip()
    if service.looks_like_pane_id_fn(env_pane) and service.pane_exists(env_pane):
        return env_pane
    pane_id = current_pane_from_tmux(service)
    if pane_id is not None:
        return pane_id
    raise RuntimeError("tmux current pane id not available")


def find_pane_by_title_marker(service, marker: str) -> str | None:
    marker = (marker or "").strip()
    if not marker:
        return None
    cp = service.tmux_run_fn(["list-panes", "-a", "-F", "#{pane_id}\t#{pane_title}"], capture=True)
    if getattr(cp, "returncode", 1) != 0:
        return None
    return service.pane_id_by_title_marker_output_fn(getattr(cp, "stdout", "") or "", marker)


def list_panes_by_user_options(service, expected: dict[str, str]) -> list[str]:
    normalized = normalize_expected_user_options(service, expected)
    if not normalized:
        return []
    cp = service.tmux_run_fn(["list-panes", "-a", "-F", list_panes_format(normalized)], capture=True)
    if getattr(cp, "returncode", 1) != 0:
        return []
    return matching_pane_ids(service, getattr(cp, "stdout", "") or "", normalized)


def describe_pane(service, pane_id: str, *, user_options: tuple[str, ...] = ()) -> dict[str, str] | None:
    if not service.looks_like_pane_id_fn(pane_id):
        return None
    normalized_options = normalize_user_option_names(service, user_options)
    format_parts = describe_pane_fields(normalized_options)
    cp = run_tmux_capture(
        service,
        ["display-message", "-p", "-t", pane_id, "\t".join(format_parts)],
        timeout=0.5,
    )
    if cp is None or getattr(cp, "returncode", 1) != 0:
        return None
    return describe_pane_output(getattr(cp, "stdout", "") or "", normalized_options)


def get_pane_content(service, pane_id: str, *, lines: int = 20) -> str | None:
    if not pane_id:
        return None
    n = max(1, int(lines))
    cp = run_tmux_capture(
        service,
        ["capture-pane", "-t", pane_id, "-p", "-S", f"-{n}"],
        timeout=2.0,
    )
    if cp is None:
        return None
    if getattr(cp, "returncode", 1) != 0:
        return None
    return service.strip_ansi_fn(getattr(cp, "stdout", "") or "")


def is_pane_alive(service, pane_id: str) -> bool:
    if not pane_id:
        return False
    cp = run_tmux_capture(
        service,
        ["display-message", "-p", "-t", pane_id, "#{pane_dead}"],
        timeout=2.0,
    )
    if cp is None:
        return False
    if getattr(cp, "returncode", 1) != 0:
        return False
    return service.pane_is_alive_fn(getattr(cp, "stdout", "") or "")


def run_tmux_capture(service, args: list[str], *, timeout: float | None = None):
    try:
        return service.tmux_run_fn(args, capture=True, timeout=timeout)
    except Exception:
        return None


def current_pane_from_tmux(service) -> str | None:
    cp = run_tmux_capture(service, ["display-message", "-p", "#{pane_id}"], timeout=0.5)
    if cp is None:
        return None
    out = (getattr(cp, "stdout", "") or "").strip()
    if service.looks_like_pane_id_fn(out) and service.pane_exists(out):
        return out
    return None


def list_panes_format(normalized: list[tuple[str, str]]) -> str:
    format_parts = ["#{pane_id}", *(f"#{{{opt}}}" for opt, _ in normalized)]
    return "\t".join(format_parts)


def matching_pane_ids(service, stdout: str, normalized: list[tuple[str, str]]) -> list[str]:
    matches: list[str] = []
    for line in stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != len(normalized) + 1:
            continue
        pane_id = parts[0].strip()
        if not service.looks_like_pane_id_fn(pane_id):
            continue
        if pane_matches_expected(parts, normalized):
            matches.append(pane_id)
    return matches


def describe_pane_fields(normalized_options: list[str]) -> list[str]:
    return ["#{pane_id}", "#{pane_title}", "#{pane_dead}", *(f"#{{{opt}}}" for opt in normalized_options)]


def describe_pane_output(stdout: str, normalized_options: list[str]) -> dict[str, str] | None:
    format_size = len(normalized_options) + 3
    line = (stdout.splitlines() or [""])[0]
    parts = line.split("\t")
    if len(parts) != format_size:
        return None
    described = {
        "pane_id": parts[0].strip(),
        "pane_title": parts[1],
        "pane_dead": parts[2].strip(),
    }
    for index, opt in enumerate(normalized_options, start=3):
        described[opt] = (parts[index] or '').strip()
    return described


__all__ = [
    'describe_pane',
    'find_pane_by_title_marker',
    'get_current_pane_id',
    'get_pane_content',
    'is_pane_alive',
    'list_panes_by_user_options',
    'pane_exists',
]
