from __future__ import annotations

import base64
from dataclasses import dataclass
import ipaddress
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import webbrowser
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace

from cli.services.mobile import prepare_server_mobile_gateway
from cli.services.terminal_qr import render_terminal_qr, write_terminal_qr_png
from mobile_gateway import mobile_host_state_dir, parse_listen_address


TAILSCALE_DOWNLOAD_URL = "https://tailscale.com/download"
TAILSCALE_LOGIN_URL = "https://login.tailscale.com/start"
DEFAULT_MOBILE_GATEWAY_LISTEN = "127.0.0.1:8787"
MOBILE_CONNECTION_CODE_PREFIX = "ccb1_"
MOBILE_COMPACT_RELAY_QR_PREFIX = "ccbr1_"
CCB_MOBILE_APP_DOWNLOAD_URL_ENV = "CCB_MOBILE_APP_DOWNLOAD_URL"
CCB_MOBILE_PAIRING_QR_OUTPUT_ENV = "CCB_MOBILE_PAIRING_QR_OUTPUT"
MAX_INLINE_TERMINAL_QR_COLUMNS = 100
DEFAULT_CCB_MOBILE_APP_DOWNLOAD_URL = (
    "https://github.com/SeemSeam/claude_codex_bridge/releases/download/"
    "v8.6.10/ccb-mobile-v8.6.10.apk"
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
    listen: str = DEFAULT_MOBILE_GATEWAY_LISTEN,
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
    commands = build_tailnet_onboarding_commands(status=status, listen=listen)
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
        _print_pairing_qr(
            qr_payload,
            print_fn=print_fn,
            qr_ansi=qr_ansi,
            environ=env,
        )
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
    _print_pairing_qr(
        qr_payload,
        print_fn=print_fn,
        qr_ansi=qr_ansi,
        environ=env,
    )
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


def run_mobile_relay_onboarding(
    *,
    start_service_fn: Callable[[], Mapping[str, object]],
    environ: Mapping[str, str] | None = None,
    print_fn: Callable[[str], None] = print,
    qr_ansi: bool | None = None,
) -> int:
    env = os.environ if environ is None else environ
    print_fn("CCB Mobile Relay setup")
    print_fn("Security: loopback-only gateway with an outbound encrypted Relay connector.")
    print_fn("")
    print_fn("Starting or refreshing the loopback-only CCB Mobile gateway:")
    try:
        service = start_service_fn()
        if not isinstance(service, Mapping):
            raise TypeError('mobile service starter must return a mapping')
        if str(service.get('route_provider') or '') != 'relay':
            raise ValueError('mobile service did not start in relay mode')
        _print_mobile_service_summary(print_fn, service)
        qr_payload = _pairing_qr_text(service)
    except Exception as exc:
        print_fn(f"❌ CCB Mobile gateway update failed: {type(exc).__name__}: {exc}")
        return 1
    print_fn("")
    print_fn("On your phone:")
    print_fn("   1. Install or update CCB Mobile.")
    print_fn("   2. Open CCB Mobile.")
    print_fn(
        "   3. Scan the complete Relay QR, or paste the connection code below."
    )
    print_fn(
        "   The one-time Relay invitation stays on the computer "
        "and is not part of this QR."
    )
    app_download_url = (
        _clean_text(env.get(CCB_MOBILE_APP_DOWNLOAD_URL_ENV))
        or DEFAULT_CCB_MOBILE_APP_DOWNLOAD_URL
    )
    print_fn(f"   APK: {app_download_url}")
    print_fn("")
    print_fn("Scan this QR in CCB Mobile:")
    _print_pairing_qr(
        qr_payload,
        print_fn=print_fn,
        qr_ansi=qr_ansi,
        environ=env,
    )
    print_fn("")
    _print_pairing_fallback(service, print_fn=print_fn)
    print_fn(
        "The QR and connection code contain the same host fingerprint "
        "and single-use bootstrap."
    )
    print_fn("Run this command again when that bootstrap expires or has already been used.")
    return 0


def run_mobile_lan_onboarding(
    *,
    start_service_fn: Callable[[], Mapping[str, object]],
    listen: str,
    environ: Mapping[str, str] | None = None,
    print_fn: Callable[[str], None] = print,
    qr_ansi: bool | None = None,
) -> int:
    try:
        parsed = parse_listen_address(listen, allow_lan=True)
    except ValueError as exc:
        print_fn(f"Invalid LAN listen address: {exc}")
        return 1
    if parsed.host.strip().lower() in {"127.0.0.1", "localhost", "::1"}:
        print_fn(
            "LAN setup requires a specific private interface address, "
            "not a loopback address."
        )
        return 1
    return _run_mobile_direct_route_onboarding(
        route_provider="lan",
        title="CCB Mobile local network setup",
        security_text=(
            "Security: direct HTTP on a trusted local network. "
            "Do not expose this listener to the public Internet."
        ),
        phone_steps=(
            (
                "Connect the phone and computer to the same trusted Wi-Fi, "
                "wired LAN, or phone hotspot."
            ),
            (
                "Do not use a guest/client-isolated Wi-Fi; if a VPN blocks "
                "local traffic, allow LAN access or pause it."
            ),
            "Open CCB Mobile.",
            "Scan the complete LAN QR or paste the connection code below.",
            (
                "If this computer's LAN IP changes, run ccb update mobile "
                "again and scan the new code."
            ),
        ),
        start_service_fn=start_service_fn,
        environ=environ,
        print_fn=print_fn,
        qr_ansi=qr_ansi,
    )


def run_mobile_cloudflare_onboarding(
    *,
    start_service_fn: Callable[[], Mapping[str, object]],
    environ: Mapping[str, str] | None = None,
    print_fn: Callable[[str], None] = print,
    qr_ansi: bool | None = None,
) -> int:
    return _run_mobile_direct_route_onboarding(
        route_provider="cloudflare_tunnel",
        title="CCB Mobile Cloudflare Tunnel setup",
        security_text=(
            "Security: advanced route. Configure an authenticated HTTPS/WebSocket "
            "tunnel to the loopback-only gateway before pairing."
        ),
        phone_steps=(
            "Confirm the named Cloudflare Tunnel is running.",
            "Open CCB Mobile.",
            "Scan the complete tunnel QR or paste the connection code below.",
        ),
        start_service_fn=start_service_fn,
        environ=environ,
        print_fn=print_fn,
        qr_ansi=qr_ansi,
    )


def _run_mobile_direct_route_onboarding(
    *,
    route_provider: str,
    title: str,
    security_text: str,
    phone_steps: Sequence[str],
    start_service_fn: Callable[[], Mapping[str, object]],
    environ: Mapping[str, str] | None,
    print_fn: Callable[[str], None],
    qr_ansi: bool | None,
) -> int:
    env = os.environ if environ is None else environ
    print_fn(title)
    print_fn(security_text)
    print_fn("")
    print_fn("Starting or refreshing the server-wide CCB Mobile gateway:")
    try:
        service = start_service_fn()
        if not isinstance(service, Mapping):
            raise TypeError("mobile service starter must return a mapping")
        if str(service.get("route_provider") or "") != route_provider:
            raise ValueError(
                f"mobile service did not start in {route_provider} mode"
            )
        _print_mobile_service_summary(print_fn, service)
        qr_payload = _pairing_qr_text(service)
    except Exception as exc:
        print_fn(f"❌ CCB Mobile gateway update failed: {type(exc).__name__}: {exc}")
        return 1
    print_fn("")
    print_fn("On your phone:")
    for index, step in enumerate(phone_steps, start=1):
        print_fn(f"   {index}. {step}")
    app_download_url = (
        _clean_text(env.get(CCB_MOBILE_APP_DOWNLOAD_URL_ENV))
        or DEFAULT_CCB_MOBILE_APP_DOWNLOAD_URL
    )
    print_fn(f"   APK: {app_download_url}")
    print_fn("")
    print_fn("Scan this QR in CCB Mobile:")
    _print_pairing_qr(
        qr_payload,
        print_fn=print_fn,
        qr_ansi=qr_ansi,
        environ=env,
    )
    print_fn("")
    _print_pairing_fallback(service, print_fn=print_fn)
    return 0


def suggest_lan_listen_address(
    *,
    port: int = 8787,
    socket_factory: Callable[..., socket.socket] = socket.socket,
    hostname_fn: Callable[[], str] = socket.gethostname,
    getaddrinfo_fn: Callable[..., Sequence[tuple[object, ...]]] = socket.getaddrinfo,
    run_fn: Callable[..., subprocess.CompletedProcess[object]] = subprocess.run,
    system_fn: Callable[[], str] = platform.system,
) -> str | None:
    candidates = _lan_interface_ipv4_candidates(
        run_fn=run_fn,
        system_name=system_fn(),
    )
    known_interfaces: dict[str, list[str]] = {}
    for interface, address in candidates:
        known_interfaces.setdefault(address.strip(), []).append(interface)
    route_socket = None
    try:
        route_socket = socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        route_socket.connect(("192.0.2.1", 9))
        candidates.append(("", str(route_socket.getsockname()[0] or "")))
    except OSError:
        pass
    finally:
        if route_socket is not None:
            try:
                route_socket.close()
            except OSError:
                pass
    try:
        records = getaddrinfo_fn(
            hostname_fn(),
            None,
            family=socket.AF_INET,
            type=socket.SOCK_STREAM,
        )
    except OSError:
        records = ()
    for record in records:
        try:
            candidates.append(("", str(record[4][0])))
        except (IndexError, TypeError):
            continue
    ranked = sorted(
        enumerate(candidates),
        key=lambda item: _lan_candidate_rank(
            interface=item[1][0],
            address=item[1][1],
            original_index=item[0],
        ),
    )
    for _index, (_interface, candidate) in ranked:
        text = candidate.strip()
        if not text or not _is_preferred_lan_address(text):
            continue
        if _interface and _is_virtual_network_interface(_interface):
            continue
        interfaces = known_interfaces.get(text, ())
        if (
            not _interface
            and interfaces
            and all(_is_virtual_network_interface(name) for name in interfaces)
        ):
            continue
        try:
            parsed = parse_listen_address(f"{text}:{port}", allow_lan=True)
        except ValueError:
            continue
        if parsed.host.strip().lower() in {"127.0.0.1", "localhost", "::1"}:
            continue
        return parsed.text
    return None


def _lan_interface_ipv4_candidates(
    *,
    run_fn: Callable[..., subprocess.CompletedProcess[object]],
    system_name: str,
) -> list[tuple[str, str]]:
    if system_name == "Linux":
        command = ("ip", "-o", "-4", "addr", "show", "scope", "global")
    elif system_name == "Darwin":
        command = ("ifconfig",)
    else:
        return []
    try:
        result = run_fn(
            command,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return []
    if result.returncode != 0:
        return []
    text = str(result.stdout or "")
    if system_name == "Linux":
        candidates: list[tuple[str, str]] = []
        for line in text.splitlines():
            match = re.search(
                r"^\d+:\s+([^\s:]+)(?:@[^\s]+)?\s+inet\s+([0-9.]+)/\d+",
                line.strip(),
            )
            if match:
                candidates.append((match.group(1), match.group(2)))
        return candidates
    candidates = []
    interface = ""
    for line in text.splitlines():
        if line and not line[0].isspace() and ":" in line:
            interface = line.split(":", 1)[0].strip()
            continue
        match = re.match(r"\s*inet\s+([0-9.]+)\s", line)
        if match:
            candidates.append((interface, match.group(1)))
    return candidates


def _lan_candidate_rank(
    *,
    interface: str,
    address: str,
    original_index: int,
) -> tuple[int, int, int]:
    virtual = _is_virtual_network_interface(interface)
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return (3, 3, original_index)
    if parsed in ipaddress.ip_network("192.168.0.0/16"):
        range_rank = 0
    elif parsed in ipaddress.ip_network("172.16.0.0/12"):
        range_rank = 1
    elif parsed in ipaddress.ip_network("10.0.0.0/8"):
        range_rank = 2
    else:
        range_rank = 3
    return (1 if virtual else 0, range_rank, original_index)


def _is_virtual_network_interface(name: str) -> bool:
    normalized = str(name or "").strip().lower()
    return normalized.startswith(
        (
            "br-",
            "docker",
            "ham",
            "mihomo",
            "tailscale",
            "tap",
            "tun",
            "utun",
            "veth",
            "virbr",
            "vmnet",
            "wg",
            "zt",
            "zerotier",
        )
    )


def _is_preferred_lan_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(
        address.version == 4
        and (
            address in ipaddress.ip_network("10.0.0.0/8")
            or address in ipaddress.ip_network("172.16.0.0/12")
            or address in ipaddress.ip_network("192.168.0.0/16")
            or address.is_link_local
        )
        and not address.is_loopback
        and not address.is_unspecified
        and not address.is_multicast
    )


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


def _print_pairing_qr(
    payload: str,
    *,
    print_fn: Callable[[str], None],
    qr_ansi: bool | None,
    environ: Mapping[str, str],
    terminal_columns: int | None = None,
    qr_image_path: str | Path | None = None,
) -> Path | None:
    display_payload = payload
    plain_lines = render_terminal_qr(
        payload,
        ansi=False,
        quiet_zone=2,
        compact=True,
    )
    required_columns = max((len(line) for line in plain_lines), default=0)
    available_columns = (
        _terminal_columns(print_fn)
        if terminal_columns is None
        else max(1, int(terminal_columns))
    )
    inline_limit = (
        min(available_columns, MAX_INLINE_TERMINAL_QR_COLUMNS)
        if available_columns is not None
        else None
    )
    image_path: Path | None = None
    if inline_limit is not None and required_columns > inline_limit:
        compact_payload = build_compact_relay_qr_payload(payload)
        if compact_payload is not None:
            compact_lines = render_terminal_qr(
                compact_payload,
                ansi=False,
                quiet_zone=2,
                compact=True,
            )
            compact_columns = max((len(line) for line in compact_lines), default=0)
            if compact_columns <= inline_limit:
                display_payload = compact_payload
                plain_lines = compact_lines
                required_columns = compact_columns

        image_path = Path(qr_image_path).expanduser() if qr_image_path else (
            _pairing_qr_image_path(environ)
        )
        write_terminal_qr_png(
            image_path,
            payload,
            quiet_zone=4,
            module_size=8,
        )
        if required_columns > inline_limit:
            if required_columns > available_columns:
                reason = (
                    f"{required_columns} columns required; "
                    f"{available_columns} available"
                )
            else:
                reason = (
                    f"{required_columns} columns exceeds the "
                    f"{MAX_INLINE_TERMINAL_QR_COLUMNS}-column safe inline limit"
                )
            print_fn(f"Terminal QR omitted to keep it scannable ({reason}).")
            print_fn(f"Pairing QR image: {image_path}")
            print_fn(
                "Open that owner-only PNG at normal size and scan it with CCB Mobile."
            )
            return image_path

        print_fn(
            f"Compact Relay QR shown in {required_columns} columns; "
            "the complete compatibility QR is also saved below."
        )

    use_ansi = (
        (
            print_fn is print
            and sys.stdout.isatty()
            and _terminal_supports_ansi(environ)
        )
        if qr_ansi is None
        else qr_ansi
    )
    lines = (
        render_terminal_qr(
            display_payload,
            ansi=True,
            quiet_zone=2,
            compact=True,
        )
        if use_ansi
        else plain_lines
    )
    for line in lines:
        print_fn(line)
    if image_path is not None:
        print_fn(f"Pairing QR image: {image_path}")
        print_fn(
            "The PNG keeps the complete legacy-compatible payload and is owner-only."
        )
    return image_path


def build_compact_relay_qr_payload(pairing_payload: str) -> str | None:
    try:
        payload = json.loads(str(pairing_payload or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, Mapping):
        return None
    if _clean_text(payload.get("route_provider")).lower() != "relay":
        return None
    mode = _clean_text(payload.get("relay_mode")).lower().replace("-", "_")
    mode_code = {
        "official": "o",
        "self_hosted": "s",
    }.get(mode)
    if mode_code is None:
        return None
    fields = (
        _clean_text(payload.get("pairing_code")),
        _clean_text(payload.get("relay_client_private_key_b64")),
        _clean_text(payload.get("server_fingerprint")),
        mode_code,
        _clean_text(payload.get("relay_rendezvous_capability")),
    )
    if not all(fields) or any("|" in value for value in fields):
        return None
    return f"{MOBILE_COMPACT_RELAY_QR_PREFIX}{'|'.join(fields)}"


def _terminal_columns(print_fn: Callable[[str], None]) -> int | None:
    if print_fn is not print:
        return None
    return max(1, int(shutil.get_terminal_size(fallback=(80, 24)).columns))


def _terminal_supports_ansi(environ: Mapping[str, str]) -> bool:
    terminal = _clean_text(environ.get("TERM")).lower()
    return terminal not in {"", "dumb"} and "NO_COLOR" not in environ


def _pairing_qr_image_path(environ: Mapping[str, str]) -> Path:
    configured = _clean_text(environ.get(CCB_MOBILE_PAIRING_QR_OUTPUT_ENV))
    if configured:
        return Path(configured).expanduser()
    return mobile_host_state_dir() / "pairing-qr.png"


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
    for key in (
        "project_id",
        "host_id",
        "relay_mode",
        "expires_at",
        "websocket_url",
        "server_fingerprint",
        "relay_session_id",
        "relay_client_private_key_b64",
        "relay_phone_nonce_b64",
        "relay_rendezvous_capability",
        "relay_bootstrap_expires_at",
        "relay_bootstrap_single_use",
    ):
        value = pairing.get(key)
        if value is not None and value != "":
            payload[key] = value
    if (
        not payload["pairing_code"]
        or not payload["claim_endpoint"]
        or not payload["gateway_url"]
    ):
        raise ValueError("mobile gateway pairing payload is incomplete")
    return json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def build_mobile_connection_code(pairing_payload: str) -> str:
    payload = str(pairing_payload or "").strip()
    if not payload:
        raise ValueError("mobile pairing payload is empty")
    encoded = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")
    return f"{MOBILE_CONNECTION_CODE_PREFIX}{encoded.rstrip('=')}"


def _print_pairing_fallback(
    summary: Mapping[str, object], *, print_fn: Callable[[str], None]
) -> None:
    connection_code = build_mobile_connection_code(_pairing_qr_text(summary))
    print_fn("If scanning is unavailable, paste this connection code in CCB Mobile:")
    print_fn(connection_code)


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
        print_fn(
            "   4. Open CCB Mobile, then scan the QR or paste the connection code below."
        )
    else:
        print_fn(
            "   4. After the next `ccb update mobile` prints a QR and connection code, open CCB Mobile."
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
        if pairing.get("expires_at"):
            print_fn(f"   pairing_expires_at: {pairing.get('expires_at')}")


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
    "MOBILE_CONNECTION_CODE_PREFIX",
    "CCB_MOBILE_APP_DOWNLOAD_URL_ENV",
    "DEFAULT_CCB_MOBILE_APP_DOWNLOAD_URL",
    "TAILSCALE_LINUX_INSTALL_COMMAND",
    "TAILSCALE_DOWNLOAD_URL",
    "TAILSCALE_LOGIN_URL",
    "TailnetOnboardingCommands",
    "TailscaleStatus",
    "build_mobile_connection_code",
    "build_tailnet_onboarding_commands",
    "detect_tailscale",
    "install_tailscale",
    "run_mobile_cloudflare_onboarding",
    "run_mobile_lan_onboarding",
    "run_mobile_relay_onboarding",
    "run_mobile_update_onboarding",
    "suggest_lan_listen_address",
]
