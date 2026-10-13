"""Real native EXE upgrade/rollback acceptance in an owned disposable directory."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile

import manage_install as maintenance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='native-maintenance-', dir=args.output.resolve()))
    root = work / '中文 runtime'
    shutil.copytree(args.baseline, root, ignore=shutil.ignore_patterns('__pycache__'))
    maintenance.metadata(root)
    (root / 'config/e2e-local.txt').write_text('配置 preserved', encoding='utf-8')
    original = (root / 'VERSION').read_bytes()
    digest = hashlib.sha256(args.archive.read_bytes()).hexdigest()
    report = []
    tool = Path(maintenance.__file__).resolve()
    env = dict(os.environ, PYTHONUTF8='1', PROCESSOR_ARCHITECTURE='AMD64')

    def run(*tokens):
        return subprocess.run([sys.executable, str(tool), *tokens, '--install-root', str(root)],
                              cwd=work, env=env, capture_output=True, encoding='utf-8',
                              errors='replace', timeout=180, creationflags=subprocess.CREATE_NO_WINDOW)

    def record(name, passed, **details):
        row = dict(name=name, passed=bool(passed), **details)
        report.append(row)
        (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(row, ensure_ascii=False), flush=True)
        if not passed:
            raise RuntimeError('Acceptance failed: ' + name)

    rejected = run('update', '--archive', str(args.archive), '--sha256', '0' * 64)
    record('checksum_rejection_preserves_install', rejected.returncode != 0 and (root / 'VERSION').read_bytes() == original)
    holder_script = root / 'e2e-holder.py'
    holder_script.write_text('import time\ntime.sleep(120)\n')
    holder = subprocess.Popen([sys.executable, str(holder_script)], cwd=work,
                              creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        busy = run('update', '--archive', str(args.archive), '--sha256', digest)
        record('active_process_refused', busy.returncode != 0 and 'Stop CCB processes' in busy.stderr)
    finally:
        holder.terminate()
        holder.wait(timeout=10)
        holder_script.unlink()
    updated = run('update', '--archive', str(args.archive), '--sha256', digest)
    record('real_archive_upgrade', updated.returncode == 0, exit_code=updated.returncode,
           diagnostic=updated.stderr[-500:])
    record('configuration_survives_upgrade', (root / 'config/e2e-local.txt').read_text(encoding='utf-8') == '配置 preserved')
    (root / 'config/e2e-local.txt').write_text('edit after upgrade')
    command = subprocess.run(['cmd.exe', '/d', '/c', str(root / 'bin/ccb.cmd'), '--print-version'],
                             cwd=work, env=dict(env, CCB_PYTHON=sys.executable), capture_output=True,
                             encoding='utf-8', timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    record('native_cmd_wrapper', command.returncode == 0 and '8.7.10' in command.stdout)
    rolled = run('rollback')
    record('real_archive_rollback', rolled.returncode == 0 and (root / 'VERSION').read_bytes() == original)
    record('configuration_survives_rollback', (root / 'config/e2e-local.txt').read_text() == 'edit after upgrade')

    # Actual candidate EXE succeeds while staged, fails only after the switch.
    failing = work / 'post-switch-failure.zip'
    with zipfile.ZipFile(args.archive) as source, zipfile.ZipFile(failing, 'w', zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            content = source.read(item)
            if item.filename.endswith('/platforms/windows/ccb.py'):
                guard = (f"import pathlib, sys\nif pathlib.Path(__file__).resolve().parents[2].name == {root.name!r}:\n    sys.exit(17)\n")
                content = guard.encode() + content
            target.writestr(item, content)
    failed = run('update', '--archive', str(failing), '--sha256', hashlib.sha256(failing.read_bytes()).hexdigest())
    record('real_post_switch_failure_recovers', failed.returncode != 0 and (root / 'VERSION').read_bytes() == original)
    # Create precisely the on-disk state left by termination after old-root move.
    install = maintenance.Installation(root, Path(sys.executable))
    backup = install.storage / ('backup-' + uuid.uuid4().hex)
    maintenance.save(install.journal, dict(root=str(root), backup=backup.name,
                                         candidate='candidate-' + uuid.uuid4().hex,
                                         operation='update', phase='prepared'))
    root.rename(backup)
    recovered = run('recover')
    record('interrupted_switch_recovers', recovered.returncode == 0 and (root / 'VERSION').read_bytes() == original)
    metadata = maintenance.metadata(root)
    record('baseline_identity_restored', metadata['version'] == original.decode().strip())
    print('Disposable acceptance installation: ' + str(root))


if __name__ == '__main__':
    main()
