"""Transactional native Windows updates. Run with Python OUTSIDE the install root.

Only the installation directory is switched. User homes, projects, PATH and
shortcuts are not modified. The previous installation remains available until
the next successful update; interrupted switches require explicit recovery.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from urllib.request import Request, urlopen
import uuid
import zipfile

REPOSITORY = 'SeemSeam/claude_codex_bridge'
ARCHIVE = 'ccb-windows-x86_64.zip'
OWNED = {'assets', 'bin', 'inherit_skills', 'lib', 'mcp', 'useful_tools', 'platforms',
         'ccb.py', 'VERSION', 'BUILD_INFO.json', 'WINDOWS_MANIFEST.json', 'LICENSE',
         'README.md', 'install.cmd', 'install.ps1', 'package.json', 'LOCAL_PATCHES.json'}


def save(path: Path, value: dict) -> None:
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def metadata(root: Path) -> dict:
    data = json.loads((root / 'WINDOWS_MANIFEST.json').read_text(encoding='utf-8-sig'))
    version = (root / 'VERSION').read_text(encoding='utf-8-sig').strip()
    if data.get('platform') != 'windows' or data.get('arch') != 'x86_64' or data.get('version') != version:
        raise ValueError('Not a matching native Windows x64 installation manifest')
    for name in ('bin/ccb.exe', 'lib', 'config'):
        if not (root / name).exists():
            raise ValueError('Incomplete native Windows payload: ' + name)
    if not any((root / name).is_file() for name in ('ccb.py', 'platforms/windows/ccb.py')):
        raise ValueError('Native Python entrypoint is missing')
    if (root / 'bin/ccb.exe').read_bytes()[:2] != b'MZ':
        raise ValueError('Native Windows launcher is missing its PE header')
    return data


def reject_links(root: Path) -> None:
    for current, dirs, files in os.walk(root, followlinks=False):
        for name in [*dirs, *files]:
            path = Path(current, name)
            info = path.lstat()
            if path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('Reparse points are not supported in managed installs: ' + str(path))


def require_stopped(root: Path) -> None:
    # Query by image and command line: daemons may use an external Python.
    script = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$root = $env:CCB_MAINTENANCE_ROOT
$self = $PID
$processes = @(Get-CimInstance Win32_Process | Where-Object {
  $_.ProcessId -ne $self -and (
    ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)) -or
    ($_.CommandLine -and $_.CommandLine.IndexOf($root + '\', [StringComparison]::OrdinalIgnoreCase) -ge 0) -or
    ($_.CommandLine -and $_.CommandLine.IndexOf($root.Replace('\', '/') + '/', [StringComparison]::OrdinalIgnoreCase) -ge 0)
  )
} | Select-Object ProcessId, Name)
ConvertTo-Json -InputObject $processes -Compress
'''
    env = dict(os.environ, CCB_MAINTENANCE_ROOT=str(root))
    for key in tuple(env):
        if key.upper() == 'PSMODULEPATH':
            env.pop(key)
    code = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', code],
                            env=env, capture_output=True, encoding='utf-8', timeout=30,
                            creationflags=subprocess.CREATE_NO_WINDOW, check=True)
    processes = json.loads(result.stdout.lstrip('\ufeff'))
    if processes:
        raise RuntimeError('Stop CCB processes using this installation before maintenance: ' +
                           ', '.join(f"{p['Name']} ({p['ProcessId']})" for p in processes))


def smoke(root: Path, python: Path) -> None:
    selected = root / '.venv/Scripts/python.exe'
    if not selected.is_file():
        selected = python
    env = dict(os.environ, CCB_PYTHON=str(selected), PYTHONUTF8='1',
               PROCESSOR_ARCHITECTURE='AMD64')
    for key in ('PYTHONHOME', 'PYTHONPATH', 'CCB_INSTALL_PREFIX', 'CCB_WINDOWS_LAUNCHER'):
        env.pop(key, None)
    commands = [([str(selected), '-c', 'import aiohttp, cryptography, watchdog'], None),
                ([str(root / 'bin/ccb.exe'), '--print-version'], 'v' + metadata(root)['version']),
                ([str(root / 'bin/ccb.exe'), '--help'], None)]
    for command, expected in commands:
        result = subprocess.run(command, cwd=root.parent, env=env, capture_output=True,
                                encoding='utf-8', errors='replace', timeout=60,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode or (expected and result.stdout.strip() != expected):
            # Output is intentionally not included: third-party startup may print credentials.
            raise RuntimeError('Candidate smoke failed: ' + Path(command[0]).name + ' ' + command[1])


def extract(archive: Path, digest: str, destination: Path) -> Path:
    if not re.fullmatch('[a-fA-F0-9]{64}', digest):
        raise ValueError('A 64-character SHA256 checksum is required')
    hasher = hashlib.sha256()
    with archive.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(block)
    if hasher.hexdigest() != digest.lower():
        raise ValueError('Windows archive SHA256 mismatch; installation was not changed')
    with zipfile.ZipFile(archive) as bundle:
        seen = set()
        total = 0
        for entry in bundle.infolist():
            raw_name = entry.orig_filename
            path = PurePosixPath(raw_name)
            parts = raw_name.rstrip('/').split('/')
            if (not parts or path.is_absolute() or re.search(r'[\\<>:"|?*\x00-\x1f]', raw_name)
                    or any(p in {'', '.', '..'} or p.rstrip(' .') != p or
                           re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', p) for p in parts)
                    or stat.S_ISLNK(entry.external_attr >> 16)):
                raise ValueError('Unsafe Windows ZIP entry')
            key = '/'.join(parts).lower()
            if key in seen:
                raise ValueError('Duplicate Windows ZIP entry')
            seen.add(key)
            total += entry.file_size
            if total > 2 * 1024**3:
                raise ValueError('Windows archive exceeds the extraction limit')
        bundle.extractall(destination)
    roots = list(destination.iterdir())
    if len(roots) != 1 or not roots[0].is_dir():
        raise ValueError('Archive must contain exactly one native payload directory')
    metadata(roots[0])
    if not (roots[0] / 'platforms/windows/ccb.py').is_file():
        raise ValueError('Update archive must include the isolated native entrypoint')
    return roots[0]


def download_release(tag: str, destination: Path) -> tuple[Path, str]:
    if tag != 'latest' and not re.fullmatch(r'v\d+\.\d+\.\d+', tag):
        raise ValueError('Select latest or an explicit stable vMAJOR.MINOR.PATCH tag')
    endpoint = 'latest' if tag == 'latest' else 'tags/' + tag
    def fetch(url, target=None):
        request = Request(url, headers={'User-Agent': 'ccb-native-windows-maintenance'})
        with urlopen(request, timeout=60) as response:
            if target is None:
                return response.read(2 * 1024 * 1024)
            with target.open('wb') as output:
                shutil.copyfileobj(response, output)
    release = json.loads(fetch(f'https://api.github.com/repos/{REPOSITORY}/releases/{endpoint}'))
    if release.get('draft') or release.get('prerelease'):
        raise ValueError('Only published stable releases are accepted')
    assets = {item['name']: item['browser_download_url'] for item in release['assets']}
    urls = [assets[name] for name in (ARCHIVE, ARCHIVE + '.sha256')]
    expected_prefix = f'https://github.com/{REPOSITORY}/releases/download/{release["tag_name"]}/'
    if not all(url.startswith(expected_prefix) for url in urls):
        raise ValueError('Unexpected release asset origin')
    checksum = fetch(urls[1]).decode('utf-8').strip().split()
    if len(checksum) != 2 or checksum[1].lstrip('*') != ARCHIVE:
        raise ValueError('Unexpected checksum asset format')
    target = destination / ARCHIVE
    fetch(urls[0], target)
    return target, checksum[0]


class Installation:
    def __init__(self, root: Path, python: Path):
        raw = root.absolute()
        self.root = raw.resolve()
        self.python = python.resolve()
        if raw != self.root or self.root == Path(self.root.anchor) or self.root == Path.home().resolve():
            raise ValueError('Install root must be a dedicated directory, not a root, home or reparse point')
        if Path.cwd().resolve().is_relative_to(self.root) or self.python.is_relative_to(self.root):
            raise ValueError('Run maintenance from outside the installation, using an external Python')
        if any((self.root / p).exists() for p in ('.git', '.ccb')):
            raise ValueError('Refusing to switch a source checkout or project directory')
        self.storage = self.root.parent / ('.' + self.root.name + '.ccb-maintenance')
        self.storage.mkdir(exist_ok=True)
        if self.storage.resolve() != self.storage:
            raise ValueError('Maintenance directory cannot be a reparse point')
        self.journal = self.storage / 'transaction.json'

    @contextmanager
    def lock(self):
        import msvcrt
        with (self.storage / 'lock').open('a+b') as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError('Another Windows maintenance operation is running') from exc
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

    def transaction(self) -> dict | None:
        if not self.journal.exists():
            return None
        data = json.loads(self.journal.read_text(encoding='utf-8'))
        if data.get('root') != str(self.root):
            raise ValueError('Maintenance journal belongs to a different installation')
        for key in ('backup', 'candidate', 'displaced'):
            name = data.get(key)
            if name is not None and not re.fullmatch(r'[a-z]+-[a-f0-9]{32}', name):
                raise ValueError('Invalid maintenance journal path')
            if name and (self.storage / name).resolve().parent != self.storage:
                raise ValueError('Maintenance journal path escapes its directory')
        return data

    def preflight(self):
        metadata(self.root)
        reject_links(self.root)
        require_stopped(self.root)

    def switch(self, candidate: Path, *, operation: str) -> dict:
        self.preflight()
        smoke(candidate, self.python)
        require_stopped(self.root)
        backup = self.storage / ('backup-' + uuid.uuid4().hex)
        data = dict(root=str(self.root), backup=backup.name, candidate=candidate.name,
                    operation=operation, phase='prepared')
        save(self.journal, data)
        try:
            self.root.rename(backup)
            candidate.rename(self.root)
            smoke(self.root, self.python)
        except Exception:
            self.recover()
            raise
        data['phase'] = 'complete'
        save(self.journal, data)
        return dict(status='updated' if operation == 'update' else 'rolled-back',
                    version=metadata(self.root)['version'], backup=str(backup))

    def recover(self) -> dict:
        data = self.transaction()
        if not data or data['phase'] != 'prepared':
            raise ValueError('No interrupted switch to recover')
        backup = self.storage / data['backup']
        if backup.exists():
            metadata(backup)
            reject_links(backup)
            require_stopped(self.root)
            if self.root.exists():
                # Preserve the failed candidate for diagnosis; never recursively delete it.
                if not data.get('displaced'):
                    data['displaced'] = 'failed-' + uuid.uuid4().hex
                    save(self.journal, data)
                self.root.rename(self.storage / data['displaced'])
            backup.rename(self.root)
        elif not self.root.exists():
            raise RuntimeError('Recovery backup is missing; no files were removed')
        data['phase'] = 'recovered'
        save(self.journal, data)
        return dict(status='recovered', version=metadata(self.root)['version'])

    def update(self, archive: Path, digest: str) -> dict:
        old = self.transaction()
        if old and old['phase'] == 'prepared':
            raise RuntimeError('Interrupted update: run recover before retrying')
        self.preflight()
        with tempfile.TemporaryDirectory(prefix='extract-', dir=self.storage) as scratch:
            payload = extract(archive, digest, Path(scratch))
            candidate = self.storage / ('candidate-' + uuid.uuid4().hex)
            shutil.copytree(payload, candidate)
        self.preserve_local(candidate)
        return self.switch(candidate, operation='update')

    def preserve_local(self, candidate: Path) -> None:
        # Only freshly extracted/copied candidates can be overwritten here.
        if candidate.parent != self.storage or not re.fullmatch(r'candidate-[a-f0-9]{32}', candidate.name):
            raise ValueError('Invalid staging directory')
        reject_links(candidate)
        for item in self.root.iterdir():
            if item.name in OWNED:
                continue
            target = candidate / item.name
            if target.exists():
                # Only the freshly extracted candidate is disposable here.
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)
        for item in (self.root / 'bin').glob('*'):
            if item.suffix.lower() in {'.cmd', '.bat'} and not (candidate / 'bin' / item.name).exists():
                shutil.copy2(item, candidate / 'bin' / item.name)
        # Older installations' fallback scripts target the shared entrypoint.
        # Route owned wrappers through the matching EXE, including on rollback.
        for name in ('ccb', 'ask', 'autonew', 'ctx-transfer'):
            if (candidate / 'bin' / (name + '.exe')).is_file():
                for extension in ('.cmd', '.bat'):
                    (candidate / 'bin' / (name + extension)).write_text(
                        '@echo off\n"%~dp0' + name + '.exe" %*\nexit /b %errorlevel%\n',
                        encoding='utf-8')

    def rollback(self) -> dict:
        self.preflight()
        data = self.transaction()
        if not data or data['phase'] != 'complete':
            raise ValueError('No completed update to roll back; use recover for an interrupted switch')
        backup = self.storage / data['backup']
        metadata(backup)
        reject_links(backup)
        candidate = self.storage / ('candidate-' + uuid.uuid4().hex)
        shutil.copytree(backup, candidate)
        self.preserve_local(candidate)
        return self.switch(candidate, operation='rollback')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('update', 'rollback', 'recover', 'status'))
    parser.add_argument('--install-root', type=Path, required=True)
    parser.add_argument('--python', type=Path, default=Path(sys.executable))
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--sha256')
    parser.add_argument('--release', default='latest')
    args = parser.parse_args(argv)
    try:
        if os.name != 'nt':
            raise ValueError('This maintenance entrypoint requires native Windows')
        if Path(sys.executable).resolve().is_relative_to(args.install_root.resolve()):
            raise ValueError('Maintenance itself must run under an external Python')
        install = Installation(args.install_root, args.python)
        with install.lock():
            if args.command == 'status':
                result = dict(version=metadata(install.root)['version'] if install.root.exists() else None,
                              transaction=install.transaction())
            elif args.command == 'update':
                with tempfile.TemporaryDirectory(prefix='download-', dir=install.storage) as directory:
                    if args.archive:
                        archive, digest = args.archive.resolve(), args.sha256 or ''
                    else:
                        archive, digest = download_release(args.release, Path(directory))
                    result = install.update(archive, digest)
            elif args.command == 'rollback':
                result = install.rollback()
            else:
                result = install.recover()
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, zipfile.BadZipFile, KeyError) as exc:
        print('Windows maintenance failed: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
