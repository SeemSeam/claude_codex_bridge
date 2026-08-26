#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB_ROOT = REPO_ROOT / "lib"
if str(LIB_ROOT) not in sys.path:
    sys.path.insert(0, str(LIB_ROOT))

SCHEMA_VERSION = 1
DEFAULT_RESULT_PATH = REPO_ROOT / "dev_tools" / "perf_results" / "python_rust_phase0_baseline.json"


@dataclass(frozen=True)
class Phase0Options:
    result_path: Path = DEFAULT_RESULT_PATH
    fixture_root: Path | None = None
    iterations: int = 10
    rows: int = 2000
    agents: int = 6
    processes: int = 80
    keep_fixtures: bool = False


def run_phase0_baseline(options: Phase0Options) -> dict[str, Any]:
    result_path = Path(options.result_path)
    fixture_root, cleanup = _fixture_root(options.fixture_root)
    try:
        project_root = fixture_root / "project"
        _generate_fixtures(
            fixture_root=fixture_root,
            project_root=project_root,
            rows=max(1, int(options.rows)),
            agents=max(1, int(options.agents)),
            processes=max(1, int(options.processes)),
        )
        results = {
            "schema_version": SCHEMA_VERSION,
            "plan": "python-rust-hybrid-performance",
            "phase": "phase0_baseline",
            "generated_at": _utc_now(),
            "repo_root": str(REPO_ROOT),
            "fixture_root": str(fixture_root),
            "result_path": str(result_path),
            "python": {
                "version": platform.python_version(),
                "executable": sys.executable,
            },
            "platform": {
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
            },
            "parameters": {
                "iterations": int(options.iterations),
                "rows": int(options.rows),
                "agents": int(options.agents),
                "processes": int(options.processes),
            },
            "rust_toolchain": _rust_toolchain_probe(),
            "metrics": _measure_all(
                fixture_root=fixture_root,
                project_root=project_root,
                iterations=max(1, int(options.iterations)),
            ),
        }
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return results
    finally:
        if cleanup is not None and not options.keep_fixtures:
            cleanup.cleanup()


def _fixture_root(requested: Path | None) -> tuple[Path, tempfile.TemporaryDirectory[str] | None]:
    if requested is None:
        tmp = tempfile.TemporaryDirectory(prefix="ccb-phase0-perf-")
        root = Path(tmp.name)
        return root, tmp
    root = Path(requested).expanduser()
    _reject_active_runtime_fixture_root(root)
    root.mkdir(parents=True, exist_ok=True)
    return root, None


def _reject_active_runtime_fixture_root(root: Path) -> None:
    active_ccb = (REPO_ROOT / ".ccb").resolve()
    try:
        resolved = root.resolve()
    except Exception:
        resolved = root.absolute()
    if resolved == active_ccb or active_ccb in resolved.parents:
        raise ValueError(f"fixture root must not be inside active runtime state: {active_ccb}")


def _generate_fixtures(*, fixture_root: Path, project_root: Path, rows: int, agents: int, processes: int) -> None:
    project_root.mkdir(parents=True, exist_ok=True)
    _write_json(project_root / ".ccb" / "ccb.config", {"version": 1, "agents": agents})
    _generate_jsonl_fixture(fixture_root / "queue-watch.jsonl", rows=rows)
    _generate_native_output_fixture(fixture_root / "native-output.jsonl", rows=rows)
    _generate_storage_fixture(project_root, agents=agents, rows=max(10, rows // 10))
    _generate_fake_proc_fixture(fixture_root / "proc", project_root=project_root, processes=processes)


def _generate_jsonl_fixture(path: Path, *, rows: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for index in range(rows):
            handle.write(
                json.dumps(
                    {
                        "seq": index,
                        "job_id": f"job_{index:06d}",
                        "agent": f"agent{index % 6}",
                        "status": "completed" if index % 7 else "running",
                        "payload": "x" * 48,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


def _generate_native_output_fixture(path: Path, *, rows: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for index in range(rows):
            handle.write(
                json.dumps(
                    {
                        "type": "assistant_delta",
                        "role": "assistant",
                        "text": f"chunk-{index};",
                        "id": f"turn-{index}",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        handle.write(
            json.dumps(
                {
                    "type": "completion",
                    "role": "assistant",
                    "status": "completed",
                    "finish_reason": "end_turn",
                    "text": "final",
                    "id": "turn-final",
                },
                ensure_ascii=False,
            )
            + "\n"
        )


def _generate_storage_fixture(project_root: Path, *, agents: int, rows: int) -> None:
    ccb = project_root / ".ccb"
    for index in range(agents):
        agent = f"agent{index}"
        _write_json(ccb / "agents" / agent / "agent.json", {"name": agent, "provider": "codex"})
        _write_json(ccb / "agents" / agent / "runtime.json", {"state": "idle", "pane_id": f"%{index + 1}"})
        _generate_jsonl_fixture(ccb / "agents" / agent / "jobs.jsonl", rows=rows)
        session_dir = ccb / "agents" / agent / "provider-state" / "codex" / "home" / "sessions" / "2026" / "06" / "15"
        _generate_jsonl_fixture(session_dir / f"{agent}-session.jsonl", rows=rows)
    _generate_jsonl_fixture(ccb / "ccbd" / "lifecycle.jsonl", rows=rows)
    _write_json(ccb / "ccbd" / "state.json", {"state": "mounted"})


def _generate_fake_proc_fixture(proc_root: Path, *, project_root: Path, processes: int) -> None:
    proc_root.mkdir(parents=True, exist_ok=True)
    marker = str(project_root / ".ccb")
    cmdlines: dict[str, str] = {}
    for offset in range(processes):
        pid = 10_000 + offset
        (proc_root / str(pid)).mkdir(parents=True, exist_ok=True)
        if offset % 4 == 0:
            cmdlines[str(pid)] = f"python ccbd/main.py --project {project_root} {marker}"
        else:
            cmdlines[str(pid)] = f"python unrelated-{offset}.py"
    _write_json(proc_root / "cmdlines.json", cmdlines)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def _measure_all(*, fixture_root: Path, project_root: Path, iterations: int) -> dict[str, Any]:
    return {
        "project_view_build": _measure_project_view(project_root=project_root, iterations=iterations),
        "queue_watch_jsonl_tail": _measure_jsonl_tail(fixture_root=fixture_root, iterations=iterations),
        "storage_classification_scan": _measure_storage_scan(project_root=project_root, iterations=iterations),
        "native_provider_output_parse": _measure_native_output_parse(fixture_root=fixture_root, iterations=iterations),
        "cleanup_process_inspection": _measure_process_inspection(fixture_root=fixture_root, project_root=project_root, iterations=iterations),
        "helper_subprocess_startup": _measure_subprocess_startup(iterations=iterations),
    }


def _measure_project_view(*, project_root: Path, iterations: int) -> dict[str, Any]:
    try:
        from agents.models import (
            AgentSpec,
            PermissionMode,
            ProjectConfig,
            QueuePolicy,
            RestoreMode,
            RuntimeMode,
            SCHEMA_VERSION as CONFIG_SCHEMA_VERSION,
            WorkspaceMode,
        )
        from ccbd.project_view import ProjectViewDependencies, ProjectViewService
    except Exception as exc:
        return _skipped(f"import_failed:{exc}")

    class EmptyMountManager:
        def load_state(self):
            return None

    class EmptyNamespaceStore:
        def load(self):
            return None

    class EmptyRegistry:
        def get(self, agent_name):
            del agent_name
            return None

    class EmptyDispatcherState:
        def active_items(self):
            return ()

        def slots(self):
            return ()

        def queued_items_for(self, target_kind, target_name):
            del target_kind, target_name
            return ()

    class EmptyDispatcher:
        _state = EmptyDispatcherState()
        _job_store = None
        _message_bureau = None
        _message_bureau_control = None
        _execution_service = None

        def __init__(self, config):
            self._config = config

        def get(self, job_id):
            del job_id
            return None

    try:
        agents = {
            f"agent{index}": AgentSpec(
                name=f"agent{index}",
                provider="codex",
                target="codex",
                workspace_mode=WorkspaceMode.INPLACE,
                workspace_root=None,
                runtime_mode=RuntimeMode.PANE_BACKED,
                restore_default=RestoreMode.AUTO,
                permission_default=PermissionMode.MANUAL,
                queue_policy=QueuePolicy.SERIAL_PER_AGENT,
            )
            for index in range(6)
        }
        config = ProjectConfig(
            version=CONFIG_SCHEMA_VERSION,
            default_agents=tuple(agents),
            agents=agents,
            cmd_enabled=True,
        )
        service = ProjectViewService(
            ProjectViewDependencies(
                project_root=project_root,
                project_id="phase0-project",
                config=config,
                registry=EmptyRegistry(),
                mount_manager=EmptyMountManager(),
                namespace_state_store=EmptyNamespaceStore(),
                dispatcher=EmptyDispatcher(config),
                clock=_utc_now,
                cache_ttl_ms=0,
            )
        )
        sample_response = service.build_response()
        summary = _time_samples(lambda: service.build_response(), iterations=iterations)
        summary["details"] = {
            "agent_count": len(sample_response["view"]["agents"]),
            "window_count": len(sample_response["view"]["windows"]),
            "comms_count": len(sample_response["view"]["comms"]),
        }
        return summary
    except Exception as exc:
        return _skipped(f"measurement_failed:{exc}")


def _measure_jsonl_tail(*, fixture_root: Path, iterations: int) -> dict[str, Any]:
    try:
        from storage.jsonl_store import JsonlStore
    except Exception as exc:
        return _skipped(f"import_failed:{exc}")
    store = JsonlStore()
    path = fixture_root / "queue-watch.jsonl"

    def run() -> None:
        tail = store.read_tail(path, 200)
        found = store.find_last(path, lambda row: row.get("status") == "running")
        if not tail or found is None:
            raise RuntimeError("jsonl fixture probe returned no rows")

    summary = _time_samples(run, iterations=iterations)
    summary["details"] = {"path": str(path), "tail_limit": 200}
    return summary


def _measure_storage_scan(*, project_root: Path, iterations: int) -> dict[str, Any]:
    try:
        from storage.paths import PathLayout
        from storage_classification import summarize_storage
    except Exception as exc:
        return _skipped(f"import_failed:{exc}")
    layout = PathLayout(project_root)
    sample = summarize_storage(layout)
    summary = _time_samples(lambda: summarize_storage(layout), iterations=iterations)
    summary["details"] = {
        "project_root": str(project_root),
        "total_count": sample.get("total_count"),
        "total_bytes": sample.get("total_bytes"),
    }
    return summary


def _measure_native_output_parse(*, fixture_root: Path, iterations: int) -> dict[str, Any]:
    try:
        from provider_backends.native_cli_support import observe_jsonl_output
    except Exception as exc:
        return _skipped(f"import_failed:{exc}")
    path = fixture_root / "native-output.jsonl"
    sample = observe_jsonl_output(path)
    summary = _time_samples(lambda: observe_jsonl_output(path), iterations=iterations)
    summary["details"] = {
        "path": str(path),
        "reply_chars": len(sample.text or ""),
        "finished": bool(sample.finished),
        "finish_reason": sample.finish_reason,
    }
    return summary


def _measure_process_inspection(*, fixture_root: Path, project_root: Path, iterations: int) -> dict[str, Any]:
    try:
        from runtime_pid_cleanup import collect_project_process_candidates
    except Exception as exc:
        return _skipped(f"import_failed:{exc}")
    proc_root = fixture_root / "proc"
    cmdlines = json.loads((proc_root / "cmdlines.json").read_text(encoding="utf-8"))

    def read_cmdline(pid: int) -> str:
        return str(cmdlines.get(str(pid), ""))

    sample = collect_project_process_candidates(project_root, proc_root=proc_root, read_proc_cmdline_fn=read_cmdline, current_pid=-1)
    summary = _time_samples(
        lambda: collect_project_process_candidates(project_root, proc_root=proc_root, read_proc_cmdline_fn=read_cmdline, current_pid=-1),
        iterations=iterations,
    )
    summary["details"] = {
        "proc_root": str(proc_root),
        "candidate_count": len(sample),
    }
    return summary


def _measure_subprocess_startup(*, iterations: int) -> dict[str, Any]:
    command = [sys.executable, "-c", ""]

    def run() -> None:
        subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=5)

    summary = _time_samples(run, iterations=iterations)
    summary["details"] = {"command": command, "command_kind": "python_empty_process"}
    return summary


def _rust_toolchain_probe() -> dict[str, Any]:
    return {
        "cargo": _tool_probe(["cargo", "version"]),
        "rustup": _tool_probe(["rustup", "show"]),
    }


def _tool_probe(command: list[str]) -> dict[str, Any]:
    executable = shutil.which(command[0])
    if executable is None:
        return {"status": "missing", "command": command, "path": None}
    try:
        cp = subprocess.run(command, text=True, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    except Exception as exc:
        return {"status": "error", "command": command, "path": executable, "error": str(exc)}
    return {
        "status": "available" if cp.returncode == 0 else "error",
        "command": command,
        "path": executable,
        "returncode": cp.returncode,
        "stdout": _short_text(cp.stdout),
        "stderr": _short_text(cp.stderr),
    }


def _time_samples(fn: Callable[[], Any], *, iterations: int) -> dict[str, Any]:
    samples: list[float] = []
    for _ in range(max(1, iterations)):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000.0)
    return {
        "status": "measured",
        "iterations": len(samples),
        "samples_ms": [round(item, 6) for item in samples],
        "min_ms": round(min(samples), 6),
        "max_ms": round(max(samples), 6),
        "mean_ms": round(statistics.fmean(samples), 6),
        "p50_ms": round(_percentile(samples, 50), 6),
        "p95_ms": round(_percentile(samples, 95), 6),
    }


def _percentile(samples: list[float], percentile: int) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (percentile / 100.0)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _skipped(reason: str) -> dict[str, Any]:
    return {"status": "skipped", "reason": reason}


def _short_text(value: str, *, limit: int = 2000) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[:limit] + "...<truncated>"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_args(argv: list[str]) -> Phase0Options:
    parser = argparse.ArgumentParser(description="Run CCB Python/Rust Phase 0 baseline measurements.")
    parser.add_argument("--result-path", type=Path, default=DEFAULT_RESULT_PATH)
    parser.add_argument("--fixture-root", type=Path, default=None)
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--rows", type=int, default=2000)
    parser.add_argument("--agents", type=int, default=6)
    parser.add_argument("--processes", type=int, default=80)
    parser.add_argument("--keep-fixtures", action="store_true")
    args = parser.parse_args(argv)
    return Phase0Options(
        result_path=args.result_path,
        fixture_root=args.fixture_root,
        iterations=args.iterations,
        rows=args.rows,
        agents=args.agents,
        processes=args.processes,
        keep_fixtures=args.keep_fixtures,
    )


def main(argv: list[str] | None = None) -> int:
    result = run_phase0_baseline(_parse_args(list(argv or sys.argv[1:])))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
