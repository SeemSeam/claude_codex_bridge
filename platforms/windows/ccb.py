"""Native launcher entrypoint; shared source files remain unchanged."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'lib'))

import ccb
from platforms.windows.herdr.entrypoint import run_native_cli_entrypoint


def main():
    ccb.mark_ccb_main(ccb.time.perf_counter_ns())
    argv = sys.argv[1:]
    allowed, reason = ccb._source_runtime_allowed(ROOT, Path.cwd(), argv)
    if not allowed:
        print(reason, file=sys.stderr)
        return 1
    if argv[:1] == ['windows-terminal-check']:
        from platforms.windows.terminal_health import main as check_terminal
        return check_terminal(argv[1:])
    if not ccb._herdr_ok and not ccb._is_safe_introspection(argv):
        print('[CCB] Herdr is not ready; install and configure Herdr before starting.', file=sys.stderr)
        return 1
    return run_native_cli_entrypoint(argv, version=ccb.VERSION, script_root=ROOT,
                                     cwd=Path.cwd(), stdout=sys.stdout, stderr=sys.stderr)


if __name__ == '__main__':
    raise SystemExit(main())
