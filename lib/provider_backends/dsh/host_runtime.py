"""Managed process wrapper for the official DSH Web host.

The wrapper exists only to publish the loopback endpoint selected by
``dsh web --port 0`` and to make lifecycle signals reach the real host.  It is
not a reply parser and never sends prompts through the terminal.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys

from storage.atomic import atomic_write_json


_READY_RE = re.compile(r"\bdsh\s+web:\s+(https?://(?:127\.0\.0\.1|localhost):\d+)\s*$")
_CHILD: subprocess.Popen[str] | None = None
_SIGNAL: int | None = None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='ccb-dsh-host')
    parser.add_argument('--state-file', required=True)
    parser.add_argument('--instance-id', required=True)
    command = parser.add_mutually_exclusive_group(required=True)
    command.add_argument('--command-json')
    command.add_argument('--shell-command')
    args = parser.parse_args(argv)

    state_file = Path(args.state_file).expanduser()
    try:
        child_command, shell = _child_command(args.command_json, args.shell_command)
    except Exception as exc:
        _publish(
            state_file,
            instance_id=args.instance_id,
            status='failed',
            endpoint=None,
            child_pid=None,
            detail=f'{type(exc).__name__}: {exc}',
        )
        print(f'ccb dsh host failed: {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
        return 2
    _publish(
        state_file,
        instance_id=args.instance_id,
        status='starting',
        endpoint=None,
        child_pid=None,
        detail=None,
    )

    _install_signal_handlers()
    global _CHILD
    try:
        _CHILD = subprocess.Popen(
            child_command,
            shell=shell,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
    except Exception as exc:
        _publish(
            state_file,
            instance_id=args.instance_id,
            status='failed',
            endpoint=None,
            child_pid=None,
            detail=f'{type(exc).__name__}: {exc}',
        )
        print(f'ccb dsh host failed: {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
        return 1

    endpoint: str | None = None
    assert _CHILD.stdout is not None
    try:
        for line in _CHILD.stdout:
            print(line, end='', flush=True)
            match = _READY_RE.search(line.strip())
            if match is not None and endpoint is None:
                endpoint = match.group(1).replace('localhost', '127.0.0.1')
                _publish(
                    state_file,
                    instance_id=args.instance_id,
                    status='ready',
                    endpoint=endpoint,
                    child_pid=_CHILD.pid,
                    detail=None,
                )
            if _SIGNAL is not None and _CHILD.poll() is None:
                _forward_signal(_CHILD, _SIGNAL)
        returncode = int(_CHILD.wait())
    except KeyboardInterrupt:
        _forward_signal(_CHILD, signal.SIGINT)
        returncode = int(_CHILD.wait())
    finally:
        _CHILD = None

    status = 'stopped' if returncode == 0 or _SIGNAL is not None else 'failed'
    detail = None if status == 'stopped' else f'dsh host exited with code {returncode}'
    if endpoint is None and detail is None:
        detail = 'dsh host exited before publishing a loopback endpoint'
    _publish(
        state_file,
        instance_id=args.instance_id,
        status=status,
        endpoint=None,
        child_pid=None,
        detail=detail,
        returncode=returncode,
    )
    return returncode


def _child_command(command_json: str | None, shell_command: str | None):
    if shell_command is not None:
        rendered = str(shell_command).strip()
        if not rendered:
            raise ValueError('DSH shell command cannot be empty')
        return rendered, True
    try:
        payload = json.loads(str(command_json or ''))
    except json.JSONDecodeError as exc:
        raise ValueError('invalid DSH command JSON') from exc
    if not isinstance(payload, list) or not payload or not all(isinstance(item, str) and item for item in payload):
        raise ValueError('DSH command JSON must be a non-empty string array')
    return payload, False


def _install_signal_handlers() -> None:
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        try:
            signal.signal(signum, _handle_signal)
        except (AttributeError, OSError, ValueError):
            continue


def _handle_signal(signum, _frame) -> None:
    global _SIGNAL
    _SIGNAL = int(signum)
    child = _CHILD
    if child is not None and child.poll() is None:
        _forward_signal(child, int(signum))


def _forward_signal(child: subprocess.Popen[str], signum: int) -> None:
    try:
        os.killpg(child.pid, signum)
    except Exception:
        try:
            child.send_signal(signum)
        except Exception:
            return


def _publish(
    path: Path,
    *,
    instance_id: str,
    status: str,
    endpoint: str | None,
    child_pid: int | None,
    detail: str | None,
    returncode: int | None = None,
) -> None:
    if not _owns_state(path, instance_id):
        return
    payload: dict[str, object] = {
        'schema_version': 1,
        'record_type': 'dsh_host_state',
        'provider': 'dsh',
        'host_instance_id': instance_id,
        'status': status,
        'endpoint': endpoint,
        'wrapper_pid': os.getpid(),
        'child_pid': child_pid,
        'updated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
    }
    if detail:
        payload['detail'] = detail[:500]
    if returncode is not None:
        payload['returncode'] = int(returncode)
    atomic_write_json(path, payload)


def _owns_state(path: Path, instance_id: str) -> bool:
    """Fence state writes from a host wrapper replaced by a newer launch."""

    try:
        payload = json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return True
    except (OSError, json.JSONDecodeError, TypeError):
        return False
    if not isinstance(payload, dict):
        return False
    current = str(payload.get('host_instance_id') or '').strip()
    return not current or current == str(instance_id or '').strip()


if __name__ == '__main__':  # pragma: no cover - exercised through subprocess tests
    raise SystemExit(main())
