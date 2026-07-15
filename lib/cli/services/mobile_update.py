from __future__ import annotations

from dataclasses import dataclass
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import webbrowser
from collections.abc import Callable, Mapping, Sequence
from types import SimpleNamespace

from cli.services.mobile import prepare_server_mobile_gateway
from cli.services.terminal_qr import render_terminal_qr


TAILSCALE_DOWNLOAD_URL = "https://tailscale.com/download"
TAILSCALE_LOGIN_URL = "https://login.tailscale.com/start"
DEFAULT_MOBILE_GATEWAY_LISTEN = "127.0.0.1:8787"
CCB_MOBILE_APP_DOWNLOAD_URL_ENV = "CCB_MOBILE_APP_DOWNLOAD_URL"
DEFAULT_CCB_MOBILE_APP_DOWNLOAD_URL = (
    "https://github.com/SeemSeam/claude_codex_bridge/releases/download/"
    "v8.1.4/ccb-mobile-v8.1.4.apk"
)
TAILSCALE_LINUX_INSTALL_COMMAND = (
    "sh",
    "-c",
    "curl -fsSL https://tailscale.com/install.sh | sh",
)
TAILSCALE_SERVE_ENABLE_URL_RE = re.compile(r"https://login\.tailscale\.com/\S+")


@dataclass(frozen=True)
class TailscaleStatus:
    installed: bool
    path: str | None = None
    logged_in: bool = False
    hostname: str | None = None
    tailnet: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class TailnetOnboardingCommands:
    mobile_serve: tuple[str, ...]
    tailscale_serve: tuple[str, ...]
    health_smoke: tuple[str, ...]
    route_diagnostics_smoke: tuple[str, ...]
    terminal_websocket_smoke: tuple[str, ...]
    revoke_gate_smoke: tuple[str, ...]


def run_mobile_update_onboarding(
    *,
    detect_tailscale_fn: Callable[[], TailscaleStatus] | None = None,
    install_tailscale_fn: Callable[[], int] | None = None,
    prepare_gateway_fn: Callable[..., object] | None = None,
    run_fn: Callable[..., subprocess.CompletedProcess[object]] | None = None,
    open_url_fn: Callable[[str], object] | None = None,
    prompt_fn: Callable[[str], str] | None = None,
    start_service_fn: Callable[[TailnetOnboardingCommands, TailscaleStatus], Mapping[str, object]] | None = None,
    environ: Mapping[str, str] | None = None,
    print_fn: Callable[[str], None] = print,
    serve_forever: bool = True,
    qr_ansi: bool | None = None,
) -> int:
    detect_tailscale_fn = detect_tailscale_fn or detect_tailscale
    install_tailscale_fn = install_tailscale_fn or install_tailscale
    prepare_gateway_fn = prepare_gateway_fn or prepare_server_mobile_gateway
    run_fn = run_fn or subprocess.run
    env = os.environ if environ is None else environ
    open_url_fn = open_url_fn or webbrowser.open
    status = detect_tailscale_fn()

    print_fn("CCB Mobile setup")
    print_fn("This command prepares your computer for CCB Mobile pairing.")
    print_fn(
        "Security: loopback-only gateway through Tailscale Serve; no Funnel, tokens, ACLs, or grants."
    )
    print_fn("")

    if not status.installed:
        print_fn("Step 1/3: install Tailscale on this computer.")
        print_fn(f"Download: {TAILSCALE_DOWNLOAD_URL}")
        print_fn(f"Suggested command: {_tailscale_install_hint()}")
        install_result = _maybe_install_tailscale(
            environ=env,
            install_tailscale_fn=install_tailscale_fn,
            open_url_fn=open_url_fn,
            prompt_fn=prompt_fn,
            print_fn=print_fn,
        )
        if install_result is not None and install_result != 0:
            return install_result
        print_fn("Next: run `tailscale up`, then run `ccb update mobile` again.")
        print_fn("The QR appears after this computer is signed in to Tailscale.")
        print_fn("")
        _print_mobile_app_steps(print_fn, environ=env, qr_ready=False)
        return 0

    print_fn(f"Tailscale: {status.path or 'tailscale'}")
    if not status.logged_in:
        print_fn("Step 1/3: sign in to Tailscale on this computer.")
        print_fn("Run: tailscale up")
        print_fn(f"Login/register: {TAILSCALE_LOGIN_URL}")
        print_fn("Next: run `ccb update mobile` again.")
        print_fn("The next run starts the gateway and prints the QR.")
        if _should_open_login(env):
            open_url_fn(TAILSCALE_LOGIN_URL)
            print_fn("Opened the Tailscale login/register page.")
        print_fn("")
        _print_mobile_app_steps(print_fn, environ=env, qr_ready=False)
        return 0

    print_fn("Tailscale: logged in")
    commands = build_tailnet_onboarding_commands(status=status)
    if start_service_fn is not None:
        print_fn("")
        print_fn("Starting or refreshing the loopback-only CCB Mobile gateway:")
        try:
            service = start_service_fn(commands, status)
            if not isinstance(service, Mapping):
                raise TypeError('mobile service starter must return a mapping')
            _print_mobile_service_summary(print_fn, service)
            qr_payload = _pairing_qr_text(service)
        except Exception as exc:
            print_fn(f"❌ CCB Mobile gateway update failed: {type(exc).__name__}: {exc}")
            return 1
        print_fn("")
        print_fn("Expose that loopback gateway to your tailnet:")
        print_fn(f"   {_shell_join(commands.tailscale_serve)}")
        print_fn("   This uses Tailscale Serve only; it does not enable Funnel.")
        print_fn("")
        _print_mobile_app_steps(print_fn, environ=env, qr_ready=True)
        print_fn("")
        print_fn("Scan this QR in CCB Mobile:")
        use_ansi = (
            (print_fn is print and sys.stdout.isatty()) if qr_ansi is None else qr_ansi
        )
        for line in render_terminal_qr(
            qr_payload,
            ansi=use_ansi,
            quiet_zone=2,
            compact=True,
        ):
            print_fn(line)
        print_fn("")
        _print_pairing_fallback(service, print_fn=print_fn)
        print_fn("")
        print_fn("Dry-run/simulated smoke command shapes:")
        print_fn(f"   health:       {_shell_join(commands.health_smoke)}")
        print_fn(f"   diagnostics:  {_shell_join(commands.route_diagnostics_smoke)}")
        print_fn(f"   terminal WS:  {_shell_join(commands.terminal_websocket_smoke)}")
        print_fn(f"   revoke gate:  {_shell_join(commands.revoke_gate_smoke)}")
        return 0

    public_url = _public_url_from_commands(commands)
    handle = None
    try:
        handle = prepare_gateway_fn(
            SimpleNamespace(
                listen=DEFAULT_MOBILE_GATEWAY_LISTEN,
                public_url=public_url,
                route_provider="tailnet",
            )
        )
    except Exception as exc:
        print_fn(f"Could not start CCB Mobile gateway: {exc}")
        return 1
    try:
        serve_result = _run_tailscale_serve(commands.tailscale_serve, run_fn=run_fn)
    except Exception as exc:
        _close_handle(handle)
        print_fn(f"Could not start Tailscale Serve: {type(exc).__name__}: {exc}")
        return 1
    if serve_result.returncode != 0:
        _close_handle(handle)
        serve_enable_url = _tailscale_serve_enable_url(
            _completed_process_text(serve_result)
        )
        if serve_enable_url:
            print_fn("Step 2/3: enable Tailscale Serve for this computer.")
            print_fn(
                "Tailscale requires one-time approval before CCB Mobile can use your tailnet URL."
            )
            print_fn(f"Open: {serve_enable_url}")
            opened = open_url_fn(serve_enable_url)
            if opened:
                print_fn("Opened the Tailscale Serve enable page.")
            print_fn("After approving, run `ccb update mobile` again.")
            print_fn("The next run starts the gateway and prints the pairing QR.")
            print_fn("")
            _print_mobile_app_steps(print_fn, environ=env, qr_ready=False)
            return 0
        detail = _completed_process_detail(serve_result)
        print_fn(
            f"Could not start Tailscale Serve: exit {serve_result.returncode}{detail}"
        )
        return int(serve_result.returncode or 1)

    try:
        qr_payload = _pairing_qr_text(handle.summary)
    except ValueError as exc:
        _close_handle(handle)
        print_fn(f"Could not generate CCB Mobile pairing QR: {exc}")
        return 1

    print_fn("")
    print_fn("CCB Mobile is ready.")
    _print_ready_summary(handle.summary, print_fn=print_fn)
    print_fn("")
    _print_mobile_app_steps(print_fn, environ=env, qr_ready=True)
    print_fn("")
    print_fn("Scan this QR in CCB Mobile:")
    use_ansi = (
        (print_fn is print and sys.stdout.isatty()) if qr_ansi is None else qr_ansi
    )
    for line in render_terminal_qr(
        qr_payload, ansi=use_ansi, quiet_zone=2, compact=True
    ):
        print_fn(line)
    print_fn("")
    _print_pairing_fallback(handle.summary, print_fn=print_fn)
    if serve_forever:
        try:
            handle.serve_forever()
        except KeyboardInterrupt:
            return 0
        finally:
            _close_handle(handle)
    else:
        _close_handle(handle)
    return 0


def detect_tailscale(
    *,
    which_fn: Callable[[str], str | None] = shutil.which,
    run_fn: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> TailscaleStatus:
    path = which_fn("tailscale")
    if not path:
        return TailscaleStatus(installed=False)
    try:
        result = run_fn(
            [path, "status", "--json"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:
        return TailscaleStatus(
            installed=True,
            path=path,
            detail=f"{type(exc).__name__}: {exc}",
        )
    if result.returncode != 0:
        detail = (
            result.stderr or result.stdout or ""
        ).strip() or f"tailscale status exited {result.returncode}"
        return TailscaleStatus(
            installed=True, path=path, logged_in=False, detail=detail
        )
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        return TailscaleStatus(
            installed=True,
            path=path,
            logged_in=False,
            detail=f"invalid status json: {exc}",
        )
    backend_state = str(payload.get("BackendState") or "").strip().lower()
    self_node = payload.get("Self") if isinstance(payload.get("Self"), dict) else {}
    hostname = _clean_dns_name(self_node.get("DNSName")) or _clean_text(
        self_node.get("HostName")
    )
    tailnet_record = (
        payload.get("CurrentTailnet")
        if isinstance(payload.get("CurrentTailnet"), dict)
        else {}
    )
    tailnet = _clean_text(tailnet_record.get("Name")) or _clean_text(
        tailnet_record.get("MagicDNSSuffix")
    )
    logged_in = backend_state == "running" and bool(self_node)
    return TailscaleStatus(
        installed=True,
        path=path,
        logged_in=logged_in,
        hostname=hostname,
        tailnet=tailnet,
        detail=backend_state or None,
    )


def install_tailscale(
    *,
    run_fn: Callable[..., subprocess.CompletedProcess[object]] = subprocess.run,
) -> int:
    command = _tailscale_install_command()
    if command is None:
        return 2
    return run_fn(command).returncode


def build_tailnet_onboarding_commands(
    *,
    status: TailscaleStatus,
    listen: str = DEFAULT_MOBILE_GATEWAY_LISTEN,
) -> TailnetOnboardingCommands:
    host, port = _split_loopback_listen(listen)
    public_url = _tailnet_public_url(status, port=port)
    mobile_serve = (
        "ccb",
        "mobile",
        "serve",
        "--listen",
        f"{host}:{port}",
        "--public-url",
        public_url,
        "--route-provider",
        "tailnet",
    )
    tailscale_serve = (
        "tailscale",
        "serve",
        "--bg",
        f"--https={port}",
        f"http://{host}:{port}",
    )
    return TailnetOnboardingCommands(
        mobile_serve=mobile_serve,
        tailscale_serve=tailscale_serve,
        health_smoke=("curl", "-fsS", f"http://{host}:{port}/v1/health"),
        route_diagnostics_smoke=("tailscale", "serve", "status"),
        terminal_websocket_smoke=(
            "python",
            "-m",
            "websockets",
            f"ws://{host}:{port}/v1/terminals/<terminal_id>",
        ),
        revoke_gate_smoke=("ccb", "mobile", "revoke", "<device_id>"),
    )


def _tailnet_public_url(status: TailscaleStatus, *, port: str) -> str:
    hostname = _clean_dns_name(status.hostname) or "your-device.tailnet.ts.net"
    return f"https://{hostname}:{port}"


def _public_url_from_commands(commands: TailnetOnboardingCommands) -> str:
    return commands.mobile_serve[commands.mobile_serve.index("--public-url") + 1]


def _run_tailscale_serve(
    command: tuple[str, ...],
    *,
    run_fn: Callable[..., subprocess.CompletedProcess[object]],
) -> subprocess.CompletedProcess[object]:
    if _tailscale_serve_status_matches(command, run_fn=run_fn):
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
    result = _invoke_tailscale_serve(command, run_fn=run_fn)
    if result.returncode != 0 and _tailscale_serve_status_matches(command, run_fn=run_fn):
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
    return result


def _invoke_tailscale_serve(
    command: tuple[str, ...],
    *,
    run_fn: Callable[..., subprocess.CompletedProcess[object]],
) -> subprocess.CompletedProcess[object]:
    try:
        return run_fn(
            command,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired as exc:
        text = _timeout_expired_text(exc)
        if _tailscale_serve_enable_url(text):
            return subprocess.CompletedProcess(command, 1, stdout=text, stderr="")
        raise


def _tailscale_serve_status_matches(
    command: tuple[str, ...],
    *,
    run_fn: Callable[..., subprocess.CompletedProcess[object]],
) -> bool:
    expected = _tailscale_serve_expected_target(command)
    if expected is None:
        return False
    port, target = expected
    try:
        result = run_fn(
            (command[0], "serve", "status", "--json"),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return False
    if result.returncode != 0:
        return False
    try:
        payload = json.loads(str(result.stdout or "{}"))
    except json.JSONDecodeError:
        return False
    tcp = payload.get("TCP")
    if not isinstance(tcp, Mapping):
        return False
    tcp_entry = tcp.get(port)
    if not isinstance(tcp_entry, Mapping) or tcp_entry.get("HTTPS") is not True:
        return False
    web = payload.get("Web")
    if not isinstance(web, Mapping):
        return False
    expected_proxy = _normalize_serve_proxy(target)
    for site in web.values():
        if not isinstance(site, Mapping):
            continue
        handlers = site.get("Handlers")
        if not isinstance(handlers, Mapping):
            continue
        root_handler = handlers.get("/")
        if not isinstance(root_handler, Mapping):
            continue
        if _normalize_serve_proxy(root_handler.get("Proxy")) == expected_proxy:
            return True
    return False


def _tailscale_serve_expected_target(command: tuple[str, ...]) -> tuple[str, str] | None:
    port = None
    for item in command:
        if item.startswith("--https="):
            port = item.split("=", 1)[1].strip()
            break
    target = command[-1] if command else ""
    if not port or not target or target.startswith("-"):
        return None
    return port, target


def _normalize_serve_proxy(value: object) -> str:
    return str(value or "").strip().rstrip("/")


def _completed_process_detail(result: subprocess.CompletedProcess[object]) -> str:
    text = _completed_process_text(result).strip()
    return f": {text}" if text else ""


def _completed_process_text(result: subprocess.CompletedProcess[object]) -> str:
    return str(getattr(result, "stderr", "") or getattr(result, "stdout", "") or "")


def _timeout_expired_text(exc: subprocess.TimeoutExpired) -> str:
    values: list[str] = []
    for value in (
        getattr(exc, "stderr", None),
        getattr(exc, "stdout", None),
        getattr(exc, "output", None),
    ):
        text = _process_output_text(value)
        if text and text not in values:
            values.append(text)
    return "\n".join(values)


def _process_output_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)


def _tailscale_serve_enable_url(text: str) -> str | None:
    match = TAILSCALE_SERVE_ENABLE_URL_RE.search(text)
    return match.group(0).rstrip(".,)") if match else None


def _close_handle(handle: object) -> None:
    close = getattr(handle, "close", None)
    if callable(close):
        close()


def _print_ready_summary(
    summary: Mapping[str, object], *, print_fn: Callable[[str], None]
) -> None:
    print_fn(f"Computer gateway: {summary.get('gateway_url', '')}")
    print_fn(f"Mounted projects available in the app: {summary.get('project_count', 0)}")


def _pairing_qr_text(summary: Mapping[str, object]) -> str:
    pairing = summary.get("pairing")
    if not isinstance(pairing, Mapping):
        raise ValueError("mobile gateway did not return a pairing payload")
    payload = {
        "pairing_code": str(pairing.get("pairing_code") or ""),
        "claim_endpoint": str(pairing.get("claim_endpoint") or ""),
        "route_provider": str(
            pairing.get("route_provider") or summary.get("route_provider") or "tailnet"
        ),
        "gateway_url": str(
            pairing.get("gateway_url") or summary.get("gateway_url") or ""
        ),
        "scopes": list(pairing.get("scopes") or []),
    }
    if (
        not payload["pairing_code"]
        or not payload["claim_endpoint"]
        or not payload["gateway_url"]
    ):
        raise ValueError("mobile gateway pairing payload is incomplete")
    return json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def _print_pairing_fallback(
    summary: Mapping[str, object], *, print_fn: Callable[[str], None]
) -> None:
    pairing = summary.get("pairing")
    if not isinstance(pairing, Mapping):
        return
    print_fn("If scanning fails, use Manual Pairing in CCB Mobile:")
    print_fn(
        f"  Gateway URL: {pairing.get('gateway_url') or summary.get('gateway_url') or ''}"
    )
    print_fn(f"  Pairing Code: {pairing.get('pairing_code') or ''}")


def _split_loopback_listen(value: str) -> tuple[str, str]:
    text = str(value or "").strip()
    if ":" not in text:
        raise ValueError("listen must be host:port")
    host, port = text.rsplit(":", 1)
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("mobile tailnet onboarding keeps the gateway loopback-only")
    if not port.isdigit() or int(port) <= 0:
        raise ValueError("listen port must be a positive integer")
    return host, port


def _tailscale_install_hint() -> str:
    system = platform.system()
    if system == "Darwin":
        return "brew install --cask tailscale  # or install from tailscale.com/download"
    if system == "Linux":
        return _shell_join(TAILSCALE_LINUX_INSTALL_COMMAND)
    return "install Tailscale from tailscale.com/download"


def _tailscale_install_command() -> tuple[str, ...] | None:
    if platform.system() == "Linux":
        return TAILSCALE_LINUX_INSTALL_COMMAND
    return None


def _maybe_install_tailscale(
    *,
    environ: Mapping[str, str],
    install_tailscale_fn: Callable[[], int],
    open_url_fn: Callable[[str], object],
    prompt_fn: Callable[[str], str] | None,
    print_fn: Callable[[str], None],
) -> int | None:
    command = _tailscale_install_command()
    if command is None:
        if _confirm_tailscale_install(environ=environ, prompt_fn=prompt_fn):
            open_url_fn(TAILSCALE_DOWNLOAD_URL)
            print_fn("   Opened the Tailscale download page.")
        else:
            _print_install_confirmation_hint(print_fn)
        return None

    print_fn("   You can install Tailscale now from this command.")
    print_fn("   This runs the official Tailscale install script and may ask for sudo.")
    print_fn(f"   Command: {_shell_join(command)}")
    if not _confirm_tailscale_install(environ=environ, prompt_fn=prompt_fn):
        _print_install_confirmation_hint(print_fn)
        return None

    if _install_forced_by_env(environ):
        print_fn("   Installing because CCB_UPDATE_MOBILE_INSTALL_TAILSCALE=1 is set.")
    print_fn("   Installing Tailscale...")
    result = install_tailscale_fn()
    if result == 0:
        print_fn("✅ Tailscale install command completed.")
    else:
        print_fn(f"❌ Tailscale install command failed with exit code {result}.")
    return result


def _confirm_tailscale_install(
    *,
    environ: Mapping[str, str],
    prompt_fn: Callable[[str], str] | None,
) -> bool:
    if _install_forced_by_env(environ):
        return True
    force_value = _clean_text(environ.get("CCB_UPDATE_MOBILE_INSTALL_TAILSCALE"))
    if force_value and force_value.lower() in {"0", "false", "no", "off"}:
        return False
    if prompt_fn is None and not sys.stdin.isatty():
        return False
    answer = (
        prompt_fn("Install Tailscale now? [y/N] ")
        if prompt_fn
        else input("Install Tailscale now? [y/N] ")
    )
    return str(answer or "").strip().lower() in {"y", "yes"}


def _install_forced_by_env(environ: Mapping[str, str]) -> bool:
    return str(
        environ.get("CCB_UPDATE_MOBILE_INSTALL_TAILSCALE") or ""
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _print_install_confirmation_hint(print_fn: Callable[[str], None]) -> None:
    print_fn("   Skipping automatic install.")
    print_fn(
        "   Re-run in an interactive terminal, or set CCB_UPDATE_MOBILE_INSTALL_TAILSCALE=1 to install."
    )


def _print_mobile_app_steps(
    print_fn: Callable[[str], None], *, environ: Mapping[str, str], qr_ready: bool
) -> None:
    app_download_url = (
        _clean_text(environ.get(CCB_MOBILE_APP_DOWNLOAD_URL_ENV))
        or DEFAULT_CCB_MOBILE_APP_DOWNLOAD_URL
    )
    print_fn("On your phone:")
    print_fn("   1. Install Tailscale and sign in to the same tailnet.")
    print_fn("   2. Install CCB Mobile:")
    print_fn(f"      Download APK: {app_download_url}")
    print_fn(
        f"      Override this link with {CCB_MOBILE_APP_DOWNLOAD_URL_ENV} if your team mirrors the APK."
    )
    print_fn("   3. Turn on the Tailscale VPN.")
    if qr_ready:
        print_fn("   4. Open CCB Mobile, tap Scan computer QR, and scan the QR below.")
    else:
        print_fn(
            "   4. After the next `ccb update mobile` prints a QR, open CCB Mobile and scan it."
        )


def _print_mobile_service_summary(print_fn: Callable[[str], None], service: Mapping[str, object]) -> None:
    print_fn(f"   status: {service.get('service_status') or service.get('mobile_status') or 'unknown'}")
    if service.get("pid"):
        print_fn(f"   pid: {service.get('pid')}")
    if service.get("listen"):
        print_fn(f"   listen: {service.get('listen')}")
    if service.get("gateway_url"):
        print_fn(f"   gateway_url: {service.get('gateway_url')}")
    if service.get("local_gateway_url"):
        print_fn(f"   local_gateway_url: {service.get('local_gateway_url')}")
    if service.get("route_provider"):
        print_fn(f"   route_provider: {service.get('route_provider')}")
    if service.get("mobile_state_dir"):
        print_fn(f"   mobile_state_dir: {service.get('mobile_state_dir')}")
    if service.get("service_log_path"):
        print_fn(f"   service_log: {service.get('service_log_path')}")
    if service.get("replaced_pid"):
        print_fn(f"   replaced_pid: {service.get('replaced_pid')}")
    pairing = service.get("pairing")
    if isinstance(pairing, Mapping):
        if pairing.get("pairing_code"):
            print_fn(f"   pairing_code: {pairing.get('pairing_code')}")
        if pairing.get("expires_at"):
            print_fn(f"   pairing_expires_at: {pairing.get('expires_at')}")
        if pairing.get("claim_endpoint"):
            print_fn(f"   pairing_claim_endpoint: {pairing.get('claim_endpoint')}")


def _should_open_login(environ: Mapping[str, str]) -> bool:
    return str(environ.get("CCB_UPDATE_MOBILE_OPEN_LOGIN") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _clean_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _clean_dns_name(value: object) -> str | None:
    text = _clean_text(value)
    if text:
        return text.rstrip(".")
    return None


def _shell_join(command: Sequence[str]) -> str:
    return " ".join(_quote_shell_part(part) for part in command)


def _quote_shell_part(value: object) -> str:
    text = str(value)
    if not text:
        return "''"
    safe = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_+-=.,/:@%"
    if all(ch in safe for ch in text):
        return text
    return "'" + text.replace("'", "'\"'\"'") + "'"


__all__ = [
    "DEFAULT_MOBILE_GATEWAY_LISTEN",
    "CCB_MOBILE_APP_DOWNLOAD_URL_ENV",
    "DEFAULT_CCB_MOBILE_APP_DOWNLOAD_URL",
    "TAILSCALE_LINUX_INSTALL_COMMAND",
    "TAILSCALE_DOWNLOAD_URL",
    "TAILSCALE_LOGIN_URL",
    "TailnetOnboardingCommands",
    "TailscaleStatus",
    "build_tailnet_onboarding_commands",
    "detect_tailscale",
    "install_tailscale",
    "run_mobile_update_onboarding",
]
