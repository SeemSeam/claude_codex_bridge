from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import platform
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import urllib.request
from typing import TextIO
import zipfile

from terminal_runtime.ui_theme import default_theme_preference, preference_for_theme, save_theme_preference, theme_config_path

SCHEMA_VERSION = 1
DEFAULT_PROFILE = 'rich'
GENERATED_MARKER = '# CCB managed workbench file'
RICH_AUTO_START_ENV = 'CCB_RICH_AUTO_START'
DETACHED_TMUX_ENV_KEYS = (
    'TMUX',
    'TMUX_PANE',
    'CCB_TMUX_SOCKET',
    'CCB_TMUX_SOCKET_PATH',
)
XCURSOR_COMPAT_ASSET_NAMES = ('pointer', 'hand2', 'left_ptr')
XCURSOR_COMPAT_FALLBACK_THEMES = ('default', 'Adwaita', 'breeze_cursors', 'Breeze')
XCURSOR_COMPAT_MAX_ASSET_BYTES = 4 * 1024 * 1024
RICH_DEPENDENCIES: tuple[dict[str, object], ...] = (
    {
        'id': 'wezterm',
        'commands': ('wezterm',),
        'packages': {
            'apt': ('wezterm',),
            'dnf': ('wezterm',),
            'yum': ('wezterm',),
            'pacman': ('wezterm',),
            'zypper': ('wezterm',),
            'apk': ('wezterm',),
            'brew_cask': ('wezterm',),
        },
    },
    {
        'id': 'yazi',
        'commands': ('yazi', 'ya'),
        'require_all': True,
        'packages': {
            'apt': ('yazi',),
            'dnf': ('yazi',),
            'yum': ('yazi',),
            'pacman': ('yazi',),
            'zypper': ('yazi',),
            'apk': ('yazi',),
            'brew': ('yazi',),
        },
    },
    {
        'id': 'markdown',
        'commands': ('python3',),
        'python_module': 'rich',
        'packages': {
            'apt': ('python3-rich',),
            'dnf': ('python3-rich',),
            'yum': ('python3-rich',),
            'pacman': ('python-rich',),
            'zypper': ('python3-rich',),
            'apk': ('py3-rich',),
            'brew': ('glow',),
        },
    },
    {
        'id': 'image_preview',
        'commands': ('chafa',),
        'packages': {
            'apt': ('chafa',),
            'dnf': ('chafa',),
            'yum': ('chafa',),
            'pacman': ('chafa',),
            'zypper': ('chafa',),
            'apk': ('chafa',),
            'brew': ('chafa',),
        },
    },
    {
        'id': 'image_metadata',
        'commands': ('identify',),
        'packages': {
            'apt': ('imagemagick',),
            'dnf': ('ImageMagick',),
            'yum': ('ImageMagick',),
            'pacman': ('imagemagick',),
            'zypper': ('ImageMagick',),
            'apk': ('imagemagick',),
            'brew': ('imagemagick',),
        },
    },
    {
        'id': 'pdf',
        'commands': ('pdfinfo', 'pdftotext', 'pdftoppm'),
        'require_all': True,
        'packages': {
            'apt': ('poppler-utils',),
            'dnf': ('poppler-utils',),
            'yum': ('poppler-utils',),
            'pacman': ('poppler',),
            'zypper': ('poppler-tools',),
            'apk': ('poppler-utils',),
            'brew': ('poppler',),
        },
    },
    {
        'id': 'video',
        'commands': ('ffprobe', 'ffmpeg'),
        'require_all': True,
        'packages': {
            'apt': ('ffmpeg',),
            'dnf': ('ffmpeg',),
            'yum': ('ffmpeg',),
            'pacman': ('ffmpeg',),
            'zypper': ('ffmpeg',),
            'apk': ('ffmpeg',),
            'brew': ('ffmpeg',),
        },
    },
)
RICH_FONT_DEPENDENCIES: tuple[dict[str, object], ...] = (
    {
        'id': 'font_jetbrains_mono',
        'families': ('JetBrains Mono',),
        'packages': {
            'apt': ('fonts-jetbrains-mono',),
            'dnf': ('jetbrains-mono-fonts',),
            'yum': ('jetbrains-mono-fonts',),
            'pacman': ('ttf-jetbrains-mono',),
            'zypper': ('jetbrains-mono-fonts',),
            'apk': ('font-jetbrains-mono',),
            'brew_cask': ('font-jetbrains-mono',),
        },
    },
    {
        'id': 'font_symbols',
        'families': ('Symbols Nerd Font Mono', 'Symbols Nerd Font', 'Noto Sans Symbols2'),
        'packages': {
            'apt': ('fonts-noto', 'fonts-symbola'),
            'dnf': ('google-noto-symbols-fonts',),
            'yum': ('google-noto-symbols-fonts',),
            'pacman': ('noto-fonts', 'ttf-jetbrains-mono-nerd'),
            'zypper': ('noto-sans-symbols-fonts',),
            'apk': ('font-noto',),
            'brew_cask': ('font-symbols-only-nerd-font',),
        },
    },
    {
        'id': 'font_cjk',
        'families': ('Noto Sans Mono CJK SC', 'Noto Sans CJK SC', 'Noto Sans CJK'),
        'packages': {
            'apt': ('fonts-noto-cjk',),
            'dnf': ('google-noto-sans-cjk-fonts',),
            'yum': ('google-noto-sans-cjk-fonts',),
            'pacman': ('noto-fonts-cjk',),
            'zypper': ('noto-sans-cjk-fonts',),
            'apk': ('font-noto-cjk',),
            'brew_cask': ('font-noto-sans-cjk-sc',),
        },
    },
    {
        'id': 'font_emoji',
        'families': ('Noto Color Emoji', 'Apple Color Emoji', 'Segoe UI Emoji'),
        'packages': {
            'apt': ('fonts-noto-color-emoji',),
            'dnf': ('google-noto-emoji-color-fonts',),
            'yum': ('google-noto-emoji-color-fonts',),
            'pacman': ('noto-fonts-emoji',),
            'zypper': ('noto-coloremoji-fonts',),
            'apk': ('font-noto-emoji',),
            'brew_cask': ('font-noto-color-emoji',),
        },
    },
)
YAZI_RELEASE_API_URL = 'https://api.github.com/repos/sxyazi/yazi/releases/latest'
DOWNLOAD_TIMEOUT_S = 120.0


def cmd_tools(
    argv: list[str],
    *,
    script_root: Path | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    del script_root
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    if not argv or argv[0] in {'-h', '--help', 'help'}:
        _print_help(stdout)
        return 0
    if len(argv) < 2:
        _print_help(stdout)
        return 2
    action, tool = argv[0], argv[1]
    if tool != 'workbench':
        print(f'ERROR: unsupported tool: {tool}', file=stderr)
        return 2
    options = _parse_options(argv[2:])
    if options.get('error'):
        print(f"ERROR: {options['error']}", file=stderr)
        return 2
    profile = str(options.get('profile') or DEFAULT_PROFILE)
    if action == 'doctor':
        status = workbench_status(profile=profile)
        _print_status(status, stdout)
        return 0 if status.get('status') in {'ok', 'degraded', 'missing'} else 1
    if action in {'install', 'update'}:
        result = provision_workbench(profile=profile)
        _print_status(result, stdout)
        return 0 if result.get('status') in {'ok', 'degraded'} else 1
    if action == 'enable':
        result = enable_workbench(profile=profile)
        _print_status(result, stdout)
        return 0 if result.get('status') in {'ok', 'degraded'} else 1
    if action in {'disable', 'close'}:
        result = disable_workbench(profile=profile, close=True)
        _print_status(result, stdout)
        return 0 if result.get('status') in {'ok', 'degraded', 'missing'} else 1
    if action == 'launch':
        result = launch_workbench(profile=profile, dry_run=bool(options.get('dry_run')))
        _print_status(result, stdout)
        return 0 if result.get('status') in {'ok', 'degraded'} else 1
    if action == 'uninstall':
        result = uninstall_workbench(profile=profile, remove_cache=bool(options.get('remove_cache')))
        _print_status(result, stdout)
        return 0 if result.get('status') in {'ok', 'missing'} else 1
    print(f'ERROR: unsupported tools action: {action}', file=stderr)
    return 2


def cmd_rich(
    *,
    script_root: Path,
    cwd: Path,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    result = launch_rich_ccb(script_root=script_root, cwd=cwd)
    _print_status(result, stdout)
    if result.get('status') not in {'ok', 'degraded'}:
        if result.get('reason'):
            print(f"ERROR: {result['reason']}", file=stderr)
        return 1
    return 0 if result.get('launch_status') == 'started' else 1


def update_rich_workbench() -> dict[str, object]:
    paths = _paths()
    binary_result = install_bundled_rich_binaries(paths=paths)
    dependency_result = install_rich_dependencies()
    result = provision_workbench(profile='rich', binary_result=binary_result)
    if result.get('status') not in {'ok', 'degraded'}:
        result['rich_update_status'] = result.get('status')
        _merge_binary_install_result(result, binary_result)
        _merge_dependency_install_result(result, dependency_result)
        return result
    enabled = enable_workbench(profile='rich')
    enabled['rich_update_status'] = result.get('status')
    _merge_binary_install_result(enabled, binary_result)
    _merge_dependency_install_result(enabled, dependency_result)
    _write_manifest(paths, enabled)
    return enabled


def install_bundled_rich_binaries(*, paths: dict[str, Path] | None = None) -> dict[str, object]:
    if _env_false('CCB_RICH_DOWNLOAD_BINARIES'):
        return {'status': 'skipped', 'reason': 'skipped by CCB_RICH_DOWNLOAD_BINARIES=0'}
    paths = paths or _paths()
    _ensure_dirs(paths)
    result = _ensure_bundled_yazi(paths)
    return result


def install_rich_dependencies() -> dict[str, object]:
    if _env_false('CCB_RICH_INSTALL_DEPS'):
        return {'status': 'skipped', 'reason': 'skipped by CCB_RICH_INSTALL_DEPS=0'}
    missing = _missing_rich_dependencies(include_unknown_fonts=True)
    if not missing:
        return {'status': 'ok', 'reason': 'all rich dependencies are already available'}
    manager = _detect_package_manager()
    if not manager:
        return {
            'status': 'degraded',
            'reason': 'no supported package manager found for automatic rich dependency installation',
            'missing': ','.join(_dependency_ids(missing)),
        }
    install_items = _dependency_install_items(missing, manager)
    if not install_items:
        return {
            'status': 'degraded',
            'tool': manager,
            'reason': 'no package mapping for missing rich dependencies on this platform',
            'missing': ','.join(_dependency_ids(missing)),
        }
    sudo = _sudo_prefix(manager)
    if sudo is None:
        return {
            'status': 'degraded',
            'tool': manager,
            'reason': 'automatic dependency installation requires root or sudo',
            'missing': ','.join(_dependency_ids(missing)),
        }
    commands = _dependency_install_commands(manager, install_items, sudo=sudo)
    print(f'🔧 Installing rich dependencies with {manager}: {", ".join(_format_install_item(item) for item in install_items)}')
    failed: list[str] = []
    installed: list[str] = []
    for command, label in commands:
        completed = subprocess.run(command)
        if completed.returncode == 0:
            installed.append(label)
        else:
            failed.append(label)
    remaining = _missing_rich_dependencies(include_unknown_fonts=False)
    status = 'ok' if not remaining else 'degraded'
    result: dict[str, object] = {
        'status': status,
        'tool': manager,
        'packages': ','.join(_format_install_item(item) for item in install_items),
    }
    if installed:
        result['installed_packages'] = ','.join(installed)
    if failed:
        result['failed_packages'] = ','.join(failed)
    if remaining:
        result['missing'] = ','.join(_dependency_ids(remaining))
        result['reason'] = 'some rich dependencies are still unavailable after automatic install'
    elif failed:
        result['reason'] = 'all required rich dependencies are available; some optional package installs failed'
    return result


def print_workbench_status(status: dict[str, object], stdout: TextIO | None = None) -> None:
    _print_status(status, stdout or sys.stdout)


def _missing_rich_dependencies(*, include_unknown_fonts: bool) -> list[dict[str, object]]:
    missing: list[dict[str, object]] = []
    for spec in RICH_DEPENDENCIES:
        if _command_dependency_missing(spec):
            missing.append(spec)
    for spec in RICH_FONT_DEPENDENCIES:
        font_missing = _font_dependency_missing(spec)
        if font_missing is True or (font_missing is None and include_unknown_fonts):
            missing.append(spec)
    return missing


def _command_dependency_missing(spec: dict[str, object]) -> bool:
    if spec.get('id') == 'markdown':
        return _markdown_dependency_missing()
    if spec.get('id') == 'wezterm':
        return _wezterm_command() is None
    commands = tuple(str(item) for item in spec.get('commands', ()) or ())
    require_all = bool(spec.get('require_all', False))
    found = [command for command in commands if _which_workbench_command(command)]
    if commands:
        if require_all and len(found) != len(commands):
            return True
        if not require_all and not found:
            return True
    module = str(spec.get('python_module') or '').strip()
    if module:
        python = _which_workbench_command('python3')
        if not python or not _python_module_available(module, python=python):
            return True
    return False


def _markdown_dependency_missing() -> bool:
    if _which_workbench_command('glow') or _which_workbench_command('mdcat'):
        return False
    python = _which_workbench_command('python3')
    return not (python and _python_module_available('rich', python=python))


def _python_module_available(module: str, *, python: str) -> bool:
    try:
        completed = subprocess.run(
            [python, '-c', f'import {module}'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except Exception:
        return False
    return completed.returncode == 0


def _font_dependency_missing(spec: dict[str, object]) -> bool | None:
    results = [_font_family_available(str(family)) for family in tuple(spec.get('families', ()) or ())]
    if not results or all(result is None for result in results):
        return None
    return not any(result is True for result in results)


def _font_family_available(family: str) -> bool | None:
    fc_match = shutil.which('fc-match')
    if not fc_match:
        return None
    try:
        completed = subprocess.run(
            [fc_match, '-f', '%{family}\n', family],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    requested = family.lower().replace(' ', '')
    families = (completed.stdout or '').lower().replace(' ', '')
    return requested in families


def _detect_package_manager() -> str | None:
    if platform.system() == 'Darwin' and shutil.which('brew'):
        return 'brew'
    for manager in ('apt-get', 'dnf', 'yum', 'pacman', 'zypper', 'apk'):
        if shutil.which(manager):
            return 'apt' if manager == 'apt-get' else manager
    return None


def _dependency_install_items(missing: list[dict[str, object]], manager: str) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    result: list[tuple[str, str]] = []
    package_keys = ('brew', 'brew_cask') if manager == 'brew' else (manager,)
    for spec in missing:
        packages = spec.get('packages')
        if not isinstance(packages, dict):
            continue
        for key in package_keys:
            values = tuple(str(item) for item in packages.get(key, ()) or ())
            kind = 'cask' if key == 'brew_cask' else 'package'
            if manager == 'brew' and key == 'brew':
                kind = 'formula'
            for package in values:
                item = (kind, package)
                if item not in seen:
                    seen.add(item)
                    result.append(item)
    return result


def _dependency_install_commands(
    manager: str,
    items: list[tuple[str, str]],
    *,
    sudo: list[str],
) -> list[tuple[list[str], str]]:
    commands: list[tuple[list[str], str]] = []
    if manager == 'apt':
        commands.append((sudo + ['apt-get', 'update'], 'apt-get update'))
        for _kind, package in items:
            commands.append((sudo + ['apt-get', 'install', '-y', package], package))
    elif manager in {'dnf', 'yum'}:
        for _kind, package in items:
            commands.append((sudo + [manager, 'install', '-y', package], package))
    elif manager == 'pacman':
        for _kind, package in items:
            commands.append((sudo + ['pacman', '-Sy', '--needed', '--noconfirm', package], package))
    elif manager == 'zypper':
        for _kind, package in items:
            commands.append((sudo + ['zypper', '--non-interactive', 'install', package], package))
    elif manager == 'apk':
        for _kind, package in items:
            commands.append((sudo + ['apk', 'add', package], package))
    elif manager == 'brew':
        for kind, package in items:
            if kind == 'cask':
                commands.append((['brew', 'install', '--cask', package], f'cask:{package}'))
            else:
                commands.append((['brew', 'install', package], package))
    return commands


def _sudo_prefix(manager: str) -> list[str] | None:
    if manager == 'brew':
        return []
    geteuid = getattr(os, 'geteuid', None)
    if callable(geteuid) and geteuid() == 0:
        return []
    if not shutil.which('sudo'):
        return None
    return ['sudo'] if sys.stdin.isatty() else ['sudo', '-n']


def _dependency_ids(items: list[dict[str, object]]) -> list[str]:
    return [str(item.get('id') or '').strip() for item in items if str(item.get('id') or '').strip()]


def _format_install_item(item: tuple[str, str]) -> str:
    kind, package = item
    return f'{kind}:{package}' if kind in {'cask', 'formula'} else package


def _merge_dependency_install_result(status: dict[str, object], dependency_result: dict[str, object]) -> None:
    prefix = 'dependency_install'
    status[f'{prefix}_status'] = dependency_result.get('status')
    for key in ('tool', 'packages', 'installed_packages', 'failed_packages', 'missing', 'reason'):
        value = dependency_result.get(key)
        if value not in (None, '', [], {}):
            status[f'{prefix}_{key}'] = value


def _merge_binary_install_result(status: dict[str, object], binary_result: dict[str, object]) -> None:
    prefix = 'binary_install'
    status[f'{prefix}_status'] = binary_result.get('status')
    for key in ('tool', 'packages', 'version', 'asset', 'missing', 'reason'):
        value = binary_result.get(key)
        if value not in (None, '', [], {}):
            status[f'{prefix}_{key}'] = value


def _env_false(name: str, *, environ: dict[str, str] | None = None) -> bool:
    env = environ if environ is not None else os.environ
    value = str(env.get(name) or '').strip().lower()
    return value in {'0', 'false', 'off', 'no'}


def _which_workbench_command(command: str) -> str | None:
    bin_dir = _paths()['bin_dir']
    candidates = [bin_dir / command]
    if platform.system() == 'Windows' and not command.lower().endswith('.exe'):
        candidates.append(bin_dir / f'{command}.exe')
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return shutil.which(command)


def _wezterm_command() -> str | None:
    if _is_wsl():
        return _windows_wezterm_exe() or shutil.which('wezterm')
    return shutil.which('wezterm') or shutil.which('wezterm.exe')


def _is_wsl() -> bool:
    if os.environ.get('WSL_DISTRO_NAME') or os.environ.get('WSL_INTEROP'):
        return True
    try:
        return 'microsoft' in Path('/proc/version').read_text(encoding='utf-8', errors='ignore').lower()
    except Exception:
        return False


def _windows_wezterm_exe() -> str | None:
    configured = str(os.environ.get('CCB_WORKBENCH_WEZTERM_EXE') or '').strip()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))
    discovered = shutil.which('wezterm.exe')
    if discovered:
        candidates.append(Path(discovered))
    candidates.extend(
        [
            Path('/mnt/c/Program Files/WezTerm/wezterm.exe'),
            Path('/mnt/c/Program Files (x86)/WezTerm/wezterm.exe'),
        ]
    )
    users_dir = Path('/mnt/c/Users')
    try:
        candidates.extend(users_dir.glob('*/AppData/Local/Programs/WezTerm/wezterm.exe'))
    except Exception:
        pass
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file():
            return key
    return None


def _ensure_bundled_yazi(paths: dict[str, Path]) -> dict[str, object]:
    asset_names = _yazi_asset_names()
    if not asset_names:
        return {'status': 'skipped', 'reason': 'no Yazi binary asset mapping for this platform'}
    manifest = _read_binary_manifest(paths)
    current = manifest.get('yazi') if isinstance(manifest.get('yazi'), dict) else {}
    if (
        paths['yazi_binary'].is_file()
        and paths['ya_binary'].is_file()
        and os.access(paths['yazi_binary'], os.X_OK)
        and os.access(paths['ya_binary'], os.X_OK)
        and current.get('asset') in asset_names
        and _yazi_bundle_runs(paths)
    ):
        asset_name = str(current.get('asset') or asset_names[0])
        return {
            'status': 'ok',
            'tool': 'github-release',
            'packages': f'yazi:{current.get("version") or "current"}',
            'version': current.get('version'),
            'asset': asset_name,
            'reason': 'bundled Yazi already available',
        }
    release: dict[str, object] = {}
    last_reason = ''
    try:
        release = _github_latest_release(YAZI_RELEASE_API_URL)
    except Exception as exc:
        _remove_invalid_bundled_yazi(paths)
        return {'status': 'degraded', 'tool': 'github-release', 'reason': f'Yazi release lookup failed: {type(exc).__name__}: {exc}'}
    for asset_name in asset_names:
        asset = _release_asset(release, asset_name)
        if asset is None:
            last_reason = f'Yazi release asset not found: {asset_name}'
            continue
        try:
            with tempfile.TemporaryDirectory(prefix='ccb-yazi-') as tmp:
                archive_path = Path(tmp) / asset_name
                _download_asset(asset, archive_path)
                extract_dir = Path(tmp) / 'extract'
                extract_dir.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(archive_path) as archive:
                    archive.extractall(extract_dir)
                yazi_source = _find_extracted_binary(extract_dir, 'yazi')
                ya_source = _find_extracted_binary(extract_dir, 'ya')
                if yazi_source is None or ya_source is None:
                    last_reason = f'{asset_name} did not contain yazi and ya binaries'
                    continue
                yazi_source.chmod(0o755)
                ya_source.chmod(0o755)
                yazi_ok, yazi_reason = _binary_runs(yazi_source)
                ya_ok, ya_reason = _binary_runs(ya_source)
                if not yazi_ok or not ya_ok:
                    last_reason = f'{asset_name} failed validation: {yazi_reason or ya_reason}'
                    continue
                shutil.copy2(yazi_source, paths['yazi_binary'])
                shutil.copy2(ya_source, paths['ya_binary'])
                paths['yazi_binary'].chmod(0o755)
                paths['ya_binary'].chmod(0o755)
        except Exception as exc:
            last_reason = f'{asset_name} failed: {type(exc).__name__}: {exc}'
            continue
        version = str(release.get('tag_name') or '').strip()
        manifest['yazi'] = {
            'version': version,
            'asset': asset_name,
            'installed_at': _now(),
            'yazi': str(paths['yazi_binary']),
            'ya': str(paths['ya_binary']),
        }
        _write_binary_manifest(paths, manifest)
        return {
            'status': 'ok',
            'tool': 'github-release',
            'packages': f'yazi:{version or asset_name}',
            'version': version,
            'asset': asset_name,
        }
    _remove_invalid_bundled_yazi(paths)
    return {'status': 'degraded', 'tool': 'github-release', 'reason': f'Yazi binary download failed: {last_reason or "no compatible asset found"}'}


def _yazi_asset_name() -> str | None:
    names = _yazi_asset_names()
    return names[0] if names else None


def _yazi_asset_names() -> tuple[str, ...]:
    system = platform.system()
    machine = platform.machine().lower()
    if machine in {'x86_64', 'amd64'}:
        arch = 'x86_64'
    elif machine in {'aarch64', 'arm64'}:
        arch = 'aarch64'
    elif machine in {'i386', 'i686'}:
        arch = 'i686'
    else:
        return ()
    if system == 'Linux':
        if arch in {'x86_64', 'aarch64'}:
            return (f'yazi-{arch}-unknown-linux-musl.zip', f'yazi-{arch}-unknown-linux-gnu.zip')
        return (f'yazi-{arch}-unknown-linux-gnu.zip',)
    if system == 'Darwin':
        if arch not in {'x86_64', 'aarch64'}:
            return ()
        return (f'yazi-{arch}-apple-darwin.zip',)
    if system == 'Windows':
        if arch not in {'x86_64', 'aarch64'}:
            return ()
        return (f'yazi-{arch}-pc-windows-msvc.zip',)
    return ()


def _yazi_bundle_runs(paths: dict[str, Path]) -> bool:
    yazi_ok, _ = _binary_runs(paths['yazi_binary'])
    ya_ok, _ = _binary_runs(paths['ya_binary'])
    return yazi_ok and ya_ok


def _binary_runs(path: Path) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            [str(path), '--version'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
    except Exception as exc:
        return False, f'{path.name}: {type(exc).__name__}: {exc}'
    if completed.returncode == 0:
        return True, ''
    output = (completed.stderr or completed.stdout or '').strip().splitlines()
    detail = output[0] if output else f'exit {completed.returncode}'
    return False, f'{path.name}: {detail}'


def _remove_invalid_bundled_yazi(paths: dict[str, Path]) -> None:
    for key in ('yazi_binary', 'ya_binary'):
        try:
            paths[key].unlink()
        except FileNotFoundError:
            pass
        except Exception:
            continue


def _github_latest_release(url: str) -> dict[str, object]:
    request = urllib.request.Request(url, headers={'User-Agent': 'ccb-rich-workbench'})
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_S) as response:
        payload = json.loads(response.read().decode('utf-8'))
    return payload if isinstance(payload, dict) else {}


def _release_asset(release: dict[str, object], name: str) -> dict[str, object] | None:
    assets = release.get('assets')
    if not isinstance(assets, list):
        return None
    for asset in assets:
        if isinstance(asset, dict) and asset.get('name') == name:
            return asset
    return None


def _download_asset(asset: dict[str, object], destination: Path) -> None:
    url = str(asset.get('browser_download_url') or '').strip()
    if not url:
        raise RuntimeError('missing browser_download_url')
    request = urllib.request.Request(url, headers={'User-Agent': 'ccb-rich-workbench'})
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_S) as response:
        data = response.read()
    digest = str(asset.get('digest') or '').strip()
    if digest.startswith('sha256:'):
        expected = digest.split(':', 1)[1].lower()
        actual = hashlib.sha256(data).hexdigest().lower()
        if actual != expected:
            raise RuntimeError(f'sha256 mismatch for {asset.get("name")}: expected {expected}, got {actual}')
    destination.write_bytes(data)


def _find_extracted_binary(root: Path, name: str) -> Path | None:
    candidates = [path for path in root.rglob(name) if path.is_file()]
    if platform.system() == 'Windows':
        candidates.extend(path for path in root.rglob(name + '.exe') if path.is_file())
    if not candidates:
        return None
    candidates.sort(key=lambda path: (len(path.parts), str(path)))
    return candidates[0]


def _read_binary_manifest(paths: dict[str, Path]) -> dict[str, object]:
    try:
        payload = json.loads(paths['binary_manifest'].read_text(encoding='utf-8'))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _write_binary_manifest(paths: dict[str, Path], payload: dict[str, object]) -> None:
    paths['binary_manifest'].parent.mkdir(parents=True, exist_ok=True)
    paths['binary_manifest'].write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def provision_workbench(*, profile: str = DEFAULT_PROFILE, binary_result: dict[str, object] | None = None) -> dict[str, object]:
    paths = _paths()
    _ensure_dirs(paths)
    if binary_result is None:
        binary_result = install_bundled_rich_binaries(paths=paths) if profile == 'rich' else {'status': 'skipped'}
    legacy_neovim_cleanup = cleanup_legacy_neovim_tool(remove_cache=False)
    _ensure_theme_preference(paths)
    _write_preview_helpers(paths)
    _write_piper_plugin(paths['yazi_safe_profile'] / 'plugins' / 'piper.yazi')
    _write_piper_plugin(paths['yazi_rich_profile'] / 'plugins' / 'piper.yazi')
    _write_yazi_config(paths, rich=False)
    _write_yazi_config(paths, rich=True)
    _write_wezterm_config(paths)
    _prepare_wayland_cursor_compat(paths)
    _write_wrappers(paths)
    _write_bin_links(paths)
    status = _build_status(paths, profile=profile, installed=True)
    if legacy_neovim_cleanup.get('status') == 'ok' and legacy_neovim_cleanup.get('removed'):
        status['legacy_editor_cleanup_status'] = 'ok'
    _merge_binary_install_result(status, binary_result)
    _write_manifest(paths, status)
    return status


def workbench_status(*, profile: str = DEFAULT_PROFILE) -> dict[str, object]:
    paths = _paths()
    manifest = _read_manifest(paths)
    if not paths['manifest'].is_file() or not paths['wrapper'].is_file():
        return {
            'status': 'missing',
            'reason': 'ccb workbench bundle is not installed',
            'profile': profile,
            **_status_paths(paths),
            **_component_statuses(paths, profile=profile, manifest=manifest),
        }
    enabled = bool(manifest.get('enabled', False))
    status = _build_status(paths, profile=profile, installed=True, enabled=enabled)
    status['installed_at'] = manifest.get('installed_at')
    status['enabled_at'] = manifest.get('enabled_at')
    status['disabled_at'] = manifest.get('disabled_at')
    return status


def enable_workbench(*, profile: str = DEFAULT_PROFILE) -> dict[str, object]:
    paths = _paths()
    manifest = _read_manifest(paths)
    if not paths['manifest'].is_file():
        manifest = provision_workbench(profile=profile)
    else:
        manifest = workbench_status(profile=profile)
    manifest['enabled'] = True
    manifest['enabled_at'] = _now()
    manifest.pop('disabled_at', None)
    _write_manifest(paths, manifest)
    return manifest


def disable_workbench(*, profile: str = DEFAULT_PROFILE, close: bool = True) -> dict[str, object]:
    paths = _paths()
    if not paths['manifest'].is_file():
        return {
            'status': 'missing',
            'reason': 'ccb workbench bundle is not installed',
            'profile': profile,
            **_status_paths(paths),
        }
    manifest = workbench_status(profile=profile)
    close_result = _close_recorded_processes(paths) if close else {'closed_processes': 0}
    manifest['enabled'] = False
    manifest['disabled_at'] = _now()
    manifest['close_status'] = close_result.get('status', 'ok')
    manifest['closed_processes'] = close_result.get('closed_processes', 0)
    if close_result.get('reason'):
        manifest['close_reason'] = close_result.get('reason')
    _write_manifest(paths, manifest)
    return manifest


def launch_workbench(*, profile: str = DEFAULT_PROFILE, dry_run: bool = False) -> dict[str, object]:
    paths = _paths()
    status = workbench_status(profile=profile)
    if status.get('status') == 'missing':
        status['reason'] = 'install the workbench bundle before launching it'
        return status
    commands = _launch_commands(paths)
    status['launch_commands'] = commands
    if dry_run:
        status['launch_status'] = 'dry_run'
        return status
    if not status.get('enabled'):
        status['status'] = 'failed'
        status['reason'] = 'workbench bundle is disabled; run `ccb tools enable workbench --profile rich` first'
        status['launch_status'] = 'disabled'
        return status
    completed = _spawn_detached_terminal(
        [str(paths['wrapper']), 'terminal'],
        env=_detached_terminal_env(),
    )
    _record_launch(paths, pid=completed.pid, command=[str(paths['wrapper']), 'terminal'])
    status['launch_status'] = 'started'
    status['launch_pid'] = completed.pid
    return status


def launch_rich_ccb(*, script_root: Path, cwd: Path, start_args: list[str] | tuple[str, ...] | None = None) -> dict[str, object]:
    status = workbench_status(profile='rich')
    if status.get('status') == 'missing':
        status['status'] = 'failed'
        status['reason'] = 'rich bundle is not installed; run `ccb update rich` first'
        status['launch_status'] = 'missing_rich_bundle'
        return status
    if not status.get('enabled'):
        status['status'] = 'failed'
        status['reason'] = 'rich bundle is disabled; run `ccb update rich` first'
        status['launch_status'] = 'disabled'
        return status
    if status.get('wezterm_status') != 'ok':
        status['status'] = 'failed'
        status['reason'] = 'rich startup requires WezTerm; install WezTerm or use normal `ccb`'
        status['launch_status'] = 'missing_wezterm'
        return status
    paths = _paths()
    entrypoint = _ccb_entrypoint(script_root)
    entrypoint_command = ' '.join(
        [_shell_quote(str(entrypoint)), *(_shell_quote(str(item)) for item in tuple(start_args or ()))]
    )
    command = [
        str(paths['wrapper']),
        'terminal',
        '/bin/sh',
        '-lc',
        f'{entrypoint_command}; exec "${{SHELL:-/bin/sh}}" -l',
    ]
    process = _spawn_detached_terminal(
        command,
        cwd=cwd,
        env=_detached_terminal_env(),
    )
    _record_launch(paths, pid=process.pid, command=command)
    status['launch_status'] = 'started'
    status['launch_pid'] = process.pid
    status['launch_command'] = ' '.join(_shell_quote(item) for item in command)
    return status


def rich_workbench_enabled() -> bool:
    status = workbench_status(profile='rich')
    return status.get('status') != 'missing' and bool(status.get('enabled'))


def rich_auto_start_allowed(environ: dict[str, str] | None = None) -> bool:
    env = environ if environ is not None else os.environ
    if _env_false(RICH_AUTO_START_ENV, environ=env):
        return False
    if _in_rich_terminal_context(env):
        return False
    return rich_workbench_enabled()


def _in_rich_terminal_context(env: dict[str, str]) -> bool:
    if str(env.get('CCB_WORKBENCH_FORCE_RICH') or '').strip():
        return True
    if str(env.get('CCB_WORKBENCH_PROFILE') or '').strip().lower() == 'rich':
        return True
    if str(env.get('CCB_WORKBENCH_ROOT') or '').strip():
        return True
    return False


def uninstall_workbench(*, profile: str = DEFAULT_PROFILE, remove_cache: bool = False) -> dict[str, object]:
    paths = _paths()
    if not paths['root'].exists() and not paths['manifest'].exists():
        return {
            'status': 'missing',
            'reason': 'ccb workbench bundle is not installed',
            'profile': profile,
            **_status_paths(paths),
        }
    disable_workbench(profile=profile, close=True)
    _remove_bin_links(paths)
    shutil.rmtree(paths['root'], ignore_errors=True)
    shutil.rmtree(paths['state_root'], ignore_errors=True)
    if remove_cache:
        shutil.rmtree(paths['cache_root'], ignore_errors=True)
    return {
        'status': 'ok',
        'profile': profile,
        'uninstalled': True,
        'cache_removed': remove_cache,
        **_status_paths(paths),
    }


def cleanup_legacy_neovim_tool(*, remove_cache: bool = False) -> dict[str, object]:
    paths = _legacy_neovim_paths()
    removed: list[str] = []
    link = paths['bin_link']
    root = paths['root']
    try:
        if link.is_symlink():
            target = link.resolve(strict=False)
            if _path_is_under(target, root):
                link.unlink()
                removed.append(str(link))
        elif link.is_file():
            text = link.read_text(encoding='utf-8', errors='ignore')[:1000]
            if 'NVIM_APPNAME=nvim' in text and 'ccb/tools/neovim' in text:
                link.unlink()
                removed.append(str(link))
    except Exception:
        pass
    for key in ('root', 'state_root'):
        path = paths[key]
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
            removed.append(str(path))
    if remove_cache and paths['cache_root'].exists():
        shutil.rmtree(paths['cache_root'], ignore_errors=True)
        removed.append(str(paths['cache_root']))
    return {
        'status': 'ok',
        'removed': removed,
        'cache_removed': remove_cache,
    }


def _legacy_neovim_paths() -> dict[str, Path]:
    data_home = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share')
    state_home = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local' / 'state')
    cache_home = Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache')
    return {
        'root': data_home / 'ccb' / 'tools' / 'neovim',
        'state_root': state_home / 'ccb' / 'tools' / 'neovim',
        'cache_root': cache_home / 'ccb' / 'tools' / 'neovim',
        'bin_link': Path(os.environ.get('CODEX_BIN_DIR') or Path.home() / '.local' / 'bin') / 'ccb-nvim',
    }


def _path_is_under(path: Path, root: Path) -> bool:
    try:
        resolved_path = path.resolve(strict=False)
        resolved_root = root.resolve(strict=False)
    except Exception:
        resolved_path = path.absolute()
        resolved_root = root.absolute()
    return resolved_path == resolved_root or resolved_root in resolved_path.parents


def _paths() -> dict[str, Path]:
    data_home = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share')
    state_home = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local' / 'state')
    cache_home = Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache')
    root = data_home / 'ccb' / 'tools' / 'workbench'
    bin_dir = root / 'bin'
    profiles = root / 'profiles'
    bin_link_dir = Path(os.environ.get('CODEX_BIN_DIR') or Path.home() / '.local' / 'bin')
    return {
        'root': root,
        'bin_dir': bin_dir,
        'bin_link_dir': bin_link_dir,
        'wrapper': bin_dir / 'ccb-workbench',
        'yazi_binary': bin_dir / 'yazi',
        'ya_binary': bin_dir / 'ya',
        'yazi_wrapper': bin_dir / 'ccb-yazi',
        'yazi_rich_wrapper': bin_dir / 'ccb-yazi-rich',
        'md_preview': bin_dir / 'ccb-md-preview',
        'image_preview': bin_dir / 'ccb-image-preview',
        'pdf_preview': bin_dir / 'ccb-pdf-preview',
        'video_preview': bin_dir / 'ccb-video-preview',
        'file_opener': bin_dir / 'ccb-file-open',
        'wrapper_link': bin_link_dir / 'ccb-workbench',
        'yazi_link': bin_link_dir / 'ccb-yazi',
        'yazi_rich_link': bin_link_dir / 'ccb-yazi-rich',
        'md_preview_link': bin_link_dir / 'ccb-md-preview',
        'image_preview_link': bin_link_dir / 'ccb-image-preview',
        'pdf_preview_link': bin_link_dir / 'ccb-pdf-preview',
        'video_preview_link': bin_link_dir / 'ccb-video-preview',
        'profiles': profiles,
        'yazi_safe_profile': profiles / 'yazi-safe',
        'yazi_rich_profile': profiles / 'yazi-rich',
        'wezterm_profile': profiles / 'wezterm',
        'wezterm_config': profiles / 'wezterm' / 'wezterm.lua',
        'wezterm_xcursor_root': profiles / 'wezterm' / 'xcursor',
        'wezterm_cursor_asset': profiles / 'wezterm' / 'xcursor' / '.ccb-assets' / 'hand',
        'manifest': root / 'manifest.json',
        'binary_manifest': root / 'binary-bundles.json',
        'state_root': state_home / 'ccb' / 'tools' / 'workbench',
        'theme_config': theme_config_path(),
        'launches': state_home / 'ccb' / 'tools' / 'workbench' / 'launches.json',
        'cache_root': cache_home / 'ccb' / 'tools' / 'workbench',
    }


def _detached_terminal_env(environ: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(environ if environ is not None else os.environ)
    for key in DETACHED_TMUX_ENV_KEYS:
        env.pop(key, None)
    return env


def _spawn_detached_terminal(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.Popen:
    """Start a GUI terminal without inheriting shell job-control or stdio."""
    return subprocess.Popen(
        command,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )


def _ensure_dirs(paths: dict[str, Path]) -> None:
    for key in (
        'bin_dir',
        'bin_link_dir',
        'yazi_safe_profile',
        'yazi_rich_profile',
        'wezterm_profile',
        'wezterm_xcursor_root',
        'state_root',
        'cache_root',
    ):
        paths[key].mkdir(parents=True, exist_ok=True)


def _prepare_wayland_cursor_compat(
    paths: dict[str, Path],
    *,
    environ: dict[str, str] | None = None,
) -> Path | None:
    """Keep a private valid cursor asset for WezTerm's Wayland `hand` lookup."""
    if platform.system() != 'Linux':
        return None
    asset = paths['wezterm_cursor_asset']
    source = _find_xcursor_pointer(environ=environ)
    if source is None:
        return asset if _valid_xcursor_asset(asset) else None
    asset.parent.mkdir(parents=True, exist_ok=True)
    temporary = asset.with_name(f'.{asset.name}.{os.getpid()}.tmp')
    try:
        shutil.copyfile(source, temporary)
        temporary.chmod(0o644)
        os.replace(temporary, asset)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass
    return asset


def _find_xcursor_pointer(*, environ: dict[str, str] | None = None) -> Path | None:
    env = environ if environ is not None else os.environ
    roots = _xcursor_search_roots(env)
    requested = _safe_xcursor_theme_name(str(env.get('XCURSOR_THEME') or 'default'))
    themes = [requested, *XCURSOR_COMPAT_FALLBACK_THEMES]
    pending = [theme for index, theme in enumerate(themes) if theme and theme not in themes[:index]]
    visited: set[str] = set()
    while pending:
        theme = pending.pop(0)
        if theme in visited:
            continue
        visited.add(theme)
        for root in roots:
            cursor_dir = root / theme / 'cursors'
            for name in XCURSOR_COMPAT_ASSET_NAMES:
                candidate = cursor_dir / name
                if _valid_xcursor_asset(candidate):
                    try:
                        return candidate.resolve(strict=True)
                    except OSError:
                        continue
            for inherited in _xcursor_inherited_themes(root / theme / 'index.theme'):
                if inherited not in visited and inherited not in pending:
                    pending.append(inherited)
    return None


def _xcursor_search_roots(environ: dict[str, str]) -> tuple[Path, ...]:
    configured = str(environ.get('XCURSOR_PATH') or '').strip()
    home = Path(str(environ.get('HOME') or Path.home())).expanduser()
    data_home = Path(str(environ.get('XDG_DATA_HOME') or home / '.local' / 'share')).expanduser()
    candidates = [Path(value).expanduser() for value in configured.split(':') if value]
    candidates.extend(
        [
            home / '.icons',
            data_home / 'icons',
            Path('/usr/share/icons'),
            Path('/usr/share/pixmaps'),
        ]
    )
    roots: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        roots.append(candidate)
    return tuple(roots)


def _safe_xcursor_theme_name(value: str) -> str:
    text = str(value or '').strip()
    if text and all(character.isalnum() or character in '._+-' for character in text):
        return text
    return 'default'


def _xcursor_inherited_themes(index_path: Path) -> tuple[str, ...]:
    try:
        lines = index_path.read_text(encoding='utf-8', errors='ignore').splitlines()
    except OSError:
        return ()
    for line in lines:
        key, separator, value = line.partition('=')
        if separator and key.strip().lower() == 'inherits':
            themes = [_safe_xcursor_theme_name(item) for item in value.split(',')]
            return tuple(theme for index, theme in enumerate(themes) if theme and theme not in themes[:index])
    return ()


def _valid_xcursor_asset(path: Path) -> bool:
    try:
        size = path.stat().st_size
    except OSError:
        return False
    return path.is_file() and 0 < size <= XCURSOR_COMPAT_MAX_ASSET_BYTES


def _normalize_workbench_theme(value: str | None) -> str:
    key = str(value or '').strip().lower().replace('_', '-').replace(' ', '-')
    if key in {'system', 'system-default', 'auto', 'os'}:
        return 'system'
    if key in {'', 'default', 'dark', 'nord', 'contrast'}:
        return 'dark'
    if key in {'light', 'latte', 'catppuccin-latte'}:
        return 'latte'
    if key in {'solarized', 'solarized-light'}:
        return 'solarized_light'
    if key in {'tokyo', 'tokyo-light', 'tokyo-night-light', 'tokyo-night-day'}:
        return 'tokyo_night_light'
    if key in {'gruvbox', 'gruvbox-light'}:
        return 'gruvbox_light'
    if key == 'rose-pine-dawn':
        return 'rose_pine_dawn'
    return 'dark'


def _ensure_theme_preference(paths: dict[str, Path]) -> None:
    if paths['theme_config'].exists():
        return
    requested = os.environ.get('CCB_WORKBENCH_THEME') or os.environ.get('CCB_TMUX_THEME_PROFILE') or 'dark'
    save_theme_preference(preference_for_theme(requested) or default_theme_preference())


def _wezterm_theme_config_candidates(path: Path) -> tuple[str, ...]:
    candidates = [str(path)]
    configured = str(
        os.environ.get('CCB_WORKBENCH_THEME_CONFIG_WINDOWS') or ''
    ).strip()
    if configured:
        candidates.append(configured)
    elif _is_wsl():
        wslpath = shutil.which('wslpath')
        if wslpath:
            try:
                completed = subprocess.run(
                    [wslpath, '-w', str(path)],
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    timeout=2,
                )
            except (OSError, subprocess.SubprocessError):
                completed = None
            converted = (
                completed.stdout.strip()
                if completed is not None and completed.returncode == 0
                else ''
            )
            if converted:
                candidates.append(converted)
    return tuple(dict.fromkeys(candidates))


def _write_preview_helpers(paths: dict[str, Path]) -> None:
    _write_executable(
        paths['md_preview'],
        f'''#!/usr/bin/env sh
{GENERATED_MARKER}
set -eu
file="${{1:-}}"
width="${{w:-100}}"
if [ -z "$file" ]; then
  exit 2
fi
if command -v glow >/dev/null 2>&1; then
  exec glow -w "$width" -s dark "$file"
fi
if command -v mdcat >/dev/null 2>&1; then
  exec mdcat "$file"
fi
exec python3 - "$file" "$width" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
try:
    width = max(40, int(float(sys.argv[2])))
except Exception:
    width = 100
try:
    text = path.read_text(encoding="utf-8", errors="replace")
except Exception as exc:
    sys.stderr.write(f"markdown preview failed: {{exc}}\\n")
    raise SystemExit(1)
try:
    from rich.console import Console
    from rich.markdown import Markdown
    console = Console(force_terminal=True, color_system="256", width=width)
    console.print(Markdown(text))
except Exception:
    sys.stdout.write(text)
PY
''',
    )
    _write_executable(
        paths['image_preview'],
        f'''#!/usr/bin/env sh
{GENERATED_MARKER}
set -eu
file="${{1:-}}"
width="${{w:-80}}"
height="${{h:-24}}"
if [ -z "$file" ]; then
  exit 2
fi
echo "Image preview: $file"
echo
if command -v identify >/dev/null 2>&1; then
  identify "$file" 2>/dev/null || true
elif command -v file >/dev/null 2>&1; then
  file "$file" 2>/dev/null || true
else
  echo "No image preview helper found."
fi
echo
echo "Inline image preview requires the rich Yazi profile in a terminal with image protocol support."
''',
    )
    _write_executable(
        paths['pdf_preview'],
        f'''#!/usr/bin/env sh
{GENERATED_MARKER}
set -eu
file="${{1:-}}"
if [ -z "$file" ]; then
  exit 2
fi
echo "PDF preview: $file"
echo
if command -v pdfinfo >/dev/null 2>&1; then
  pdfinfo "$file" 2>/dev/null | sed -n '1,24p' || true
else
  echo "pdfinfo: missing"
fi
echo
echo "--- text, first 3 pages ---"
if command -v pdftotext >/dev/null 2>&1; then
  pdftotext -layout -f 1 -l 3 "$file" - 2>/dev/null | sed -n '1,120p' || true
else
  echo "pdftotext: missing"
fi
''',
    )
    _write_executable(
        paths['video_preview'],
        f'''#!/usr/bin/env sh
{GENERATED_MARKER}
set -eu
file="${{1:-}}"
if [ -z "$file" ]; then
  exit 2
fi
echo "Video preview: $file"
echo
if command -v ffprobe >/dev/null 2>&1; then
  ffprobe -hide_banner -v error -show_format -show_streams "$file" 2>/dev/null | sed -n '1,140p' || true
else
  echo "ffprobe: missing"
fi
''',
    )


def _write_yazi_config(paths: dict[str, Path], *, rich: bool) -> None:
    profile = paths['yazi_rich_profile'] if rich else paths['yazi_safe_profile']
    _write_file_opener(paths['file_opener'])
    open_command = json.dumps(_shell_quote(str(paths['file_opener'])) + ' %s')
    md = _shell_double_quote(str(paths['md_preview']))
    image = _shell_double_quote(str(paths['image_preview']))
    pdf = _shell_double_quote(str(paths['pdf_preview']))
    video = _shell_double_quote(str(paths['video_preview']))
    previewers_key = 'prepend_previewers' if rich else 'previewers'
    lines = [
        '# CCB managed Yazi profile. Do not edit; regenerate with `ccb tools install workbench`.',
        '',
    ]
    if rich:
        lines.extend(
            [
                '[mgr]',
                'ratio = [0, 4, 3]',
                '',
            ]
        )
    lines.extend(
        [
            '[opener]',
            'system = [',
            f'  {{ run = {open_command}, desc = "System default", for = "unix" }},',
            ']',
            '',
            '[open]',
            'prepend_rules = [',
            '  { url = "local://*", use = "system" },',
            ']',
            '',
            '[preview]',
            'wrap = "yes"',
            'tab_size = 2',
            'max_width = 1200',
            'max_height = 1600',
            'image_delay = 20',
            'image_filter = "triangle"',
            'image_quality = 75',
            '',
            '[plugin]',
            f'{previewers_key} = [',
            f'  {{ url = "*.md", run = \'piper -- {md} "$1"\' }},',
            f'  {{ url = "*.markdown", run = \'piper -- {md} "$1"\' }},',
        ]
    )
    if rich:
        lines.extend(
            [
                '  { mime = "image/*", run = "image" },',
                '  { mime = "application/pdf", run = "pdf" },',
                '  { url = "*.pdf", run = "pdf" },',
                '  { mime = "video/*", run = "video" },',
            ]
        )
    if not rich:
        lines.extend(
            [
                f'  {{ url = "*.png", run = \'piper -- {image} "$1"\' }},',
                f'  {{ url = "*.jpg", run = \'piper -- {image} "$1"\' }},',
                f'  {{ url = "*.jpeg", run = \'piper -- {image} "$1"\' }},',
                f'  {{ url = "*.gif", run = \'piper -- {image} "$1"\' }},',
                f'  {{ url = "*.webp", run = \'piper -- {image} "$1"\' }},',
                f'  {{ url = "*.bmp", run = \'piper -- {image} "$1"\' }},',
                f'  {{ url = "*.tif", run = \'piper -- {image} "$1"\' }},',
                f'  {{ url = "*.tiff", run = \'piper -- {image} "$1"\' }},',
                f'  {{ url = "*.pdf", run = \'piper -- {pdf} "$1"\' }},',
                f'  {{ url = "*.mp4", run = \'piper -- {video} "$1"\' }},',
                f'  {{ url = "*.mkv", run = \'piper -- {video} "$1"\' }},',
                f'  {{ url = "*.mov", run = \'piper -- {video} "$1"\' }},',
                f'  {{ url = "*.webm", run = \'piper -- {video} "$1"\' }},',
                f'  {{ url = "*.avi", run = \'piper -- {video} "$1"\' }},',
                '  { url = "*/", run = "folder" },',
                '  { mime = "text/*", run = "code" },',
                '  { mime = "application/{mbox,javascript,wine-extension-ini}", run = "code" },',
                '  { mime = "application/{json,ndjson}", run = "json" },',
                '  { mime = "application/{zip,rar,7z*,tar,gzip,xz,zstd,bzip*,lzma,compress,archive,cpio,arj,xar,ms-cab*}", run = "archive" },',
                '  { mime = "application/{debian*-package,redhat-package-manager,rpm,android.package-archive}", run = "archive" },',
                '  { url = "*.{AppImage,appimage}", run = "archive" },',
                '  { mime = "application/{iso9660-image,qemu-disk,ms-wim,apple-diskimage}", run = "archive" },',
                '  { mime = "application/virtualbox-{vhd,vhdx}", run = "archive" },',
                '  { url = "*.{img,fat,ext,ext2,ext3,ext4,squashfs,ntfs,hfs,hfsx}", run = "archive" },',
                '  { mime = "font/*", run = "font" },',
                '  { mime = "application/ms-opentype", run = "font" },',
                '  { mime = "inode/empty", run = "empty" },',
                '  { mime = "vfs/*", run = "vfs" },',
                '  { mime = "null/*", run = "null" },',
                '  { url = "*", run = "file" },',
            ]
        )
    lines.extend([']'])
    if not rich:
        lines.extend(
            [
                '',
                'preloaders = []',
            ]
        )
    lines.append('')
    (profile / 'yazi.toml').write_text('\n'.join(lines), encoding='utf-8')
    (profile / 'keymap.toml').write_text('''# CCB managed Yazi keymap.
[mgr]
prepend_keymap = [
  { on = "<Enter>", run = "plugin ccb-open", desc = "Enter folder or open with system default" },
]
''', encoding='utf-8')
    plugin = profile / 'plugins' / 'ccb-open.yazi'
    plugin.mkdir(parents=True, exist_ok=True)
    (plugin / 'main.lua').write_text('''--- @sync entry
return {
  entry = function()
    local hovered = cx.active.current.hovered
    if not hovered then return end
    if hovered.cha.is_dir then
      ya.emit("enter", {})
    else
      ya.emit("open", {})
    end
  end,
}
''', encoding='utf-8')
    (profile / 'init.lua').write_text('''-- CCB managed Yazi mouse behavior.
function Entity:click(event, up)
  if up or event.is_middle then return end
  ya.emit("reveal", { self._file.url })
  if self._file.cha.is_dir then
    ya.emit("cd", { self._file.url })
  else
    -- Use the clicked URL, not a previous hover or unrelated selection.
    ya.emit("open", { self._file.url })
  end
end
''', encoding='utf-8')


def _write_file_opener(target: Path) -> None:
    _write_executable(target, '''#!/bin/sh
# CCB managed desktop opener. Never evaluate a filename as shell code.
set -eu
system=$(uname -s)
case "$system" in
  Darwin) exec open "$@" ;;
  Linux)
    release=$(uname -r)
    case "$release" in
      *[Mm]icrosoft*|*WSL*) wsl=1 ;;
      *) wsl=0 ;;
    esac
    if [ -n "${WSL_INTEROP:-}${WSL_DISTRO_NAME:-}" ]; then wsl=1; fi
    if [ "$wsl" = 1 ]; then
      if ! command -v wslview >/dev/null 2>&1; then
        printf '%s\\n' 'CCB: Windows default opening from WSL requires wslview (wslu) and enabled WSL interop.' >&2
        exit 127
      fi
      for path do wslview "$path"; done
    elif command -v gio >/dev/null 2>&1; then
      for path do gio open "$path"; done
    else
      for path do xdg-open "$path"; done
    fi
    ;;
  *) printf 'CCB: unsupported desktop opener platform: %s\\n' "$system" >&2; exit 1 ;;
esac
''')


def _write_piper_plugin(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    (target / 'main.lua').write_text(
        '''-- CCB managed minimal piper-compatible previewer.
-- The interface follows yazi-rs/plugins:piper so generated profiles remain
-- independent from the user's personal Yazi plugin directory.
local M = {}

local function fail(job, text)
  ya.preview_widget(job, ui.Text.parse(text):area(job.area):wrap(ui.Wrap.YES))
end

function M:peek(job)
  local child, err = Command("sh")
    :arg({ "-c", job.args[1], "sh", tostring(job.file.path) })
    :env("w", job.area.w)
    :env("h", job.area.h)
    :stdout(Command.PIPED)
    :stderr(Command.PIPED)
    :spawn()
  if not child then
    return fail(job, "sh: " .. err)
  end
  local limit = job.area.h
  local i, outs, errs = 0, {}, {}
  repeat
    local next, event = child:read_line()
    if event == 1 then
      errs[#errs + 1] = next
    elseif event ~= 0 then
      break
    end
    i = i + 1
    if i > job.skip then
      outs[#outs + 1] = next
    end
  until i >= job.skip + limit
  child:start_kill()
  if #errs > 0 then
    fail(job, table.concat(errs, ""))
  else
    local text = table.concat(outs, ""):gsub("\\t", string.rep(" ", rt.preview.tab_size))
    ya.preview_widget(job, ui.Text.parse(text):area(job.area))
  end
end

function M:seek(job)
  require("code"):seek(job)
end

return M
''',
        encoding='utf-8',
    )


def _write_wezterm_config(paths: dict[str, Path]) -> None:
    theme_config_candidates = '\n'.join(
        f'  "{_lua_string(candidate)}",'
        for candidate in _wezterm_theme_config_candidates(paths['theme_config'])
    )
    paths['wezterm_config'].write_text(
        f'''-- {GENERATED_MARKER}
local wezterm = require("wezterm")
local config = wezterm.config_builder and wezterm.config_builder() or {{}}

config.automatically_reload_config = true
config.check_for_updates = false
config.window_close_confirmation = "NeverPrompt"
config.warn_about_missing_glyphs = false
config.use_ime = true
local theme_config_paths = {{
{theme_config_candidates}
}}
local theme_config_path = theme_config_paths[1]
for _, candidate in ipairs(theme_config_paths) do
  local file = io.open(candidate, "r")
  if file then
    file:close()
    theme_config_path = candidate
    break
  end
end
if wezterm.add_to_config_reload_watch_list then
  wezterm.add_to_config_reload_watch_list(theme_config_path)
end
local xmodifiers = os.getenv("XMODIFIERS") or ""
local xim_im_name = xmodifiers:match("@im=([^%s]+)")
if xim_im_name and xim_im_name ~= "" then
  config.xim_im_name = xim_im_name
end
local themes = {{
  dark = {{
    tmux_profile = "default",
    ansi = {{ "#3b4252", "#bf616a", "#a3be8c", "#ebcb8b", "#81a1c1", "#b48ead", "#88c0d0", "#e5e9f0" }},
    brights = {{ "#4c566a", "#bf616a", "#a3be8c", "#ebcb8b", "#81a1c1", "#b48ead", "#8fbcbb", "#eceff4" }},
    foreground = "#d8dee9",
    background = "#1f2328",
    cursor_bg = "#88c0d0",
    cursor_fg = "#1f2328",
    cursor_border = "#88c0d0",
    selection_fg = "#eceff4",
    selection_bg = "#3b4252",
    split = "#4c566a",
    frame_active = "#1f2328",
    frame_inactive = "#181c22",
    tab_bar_bg = "#1f2328",
    active_tab_bg = "#2e3440",
    active_tab_fg = "#eceff4",
    inactive_tab_bg = "#242933",
    inactive_tab_fg = "#a7b0be",
    inactive_tab_hover_bg = "#303846",
    inactive_tab_hover_fg = "#eceff4",
    new_tab_bg = "#1f2328",
    new_tab_fg = "#a7b0be",
  }},
  latte = {{
    tmux_profile = "light",
    ansi = {{ "#5c5f77", "#d20f39", "#40a02b", "#df8e1d", "#1e66f5", "#8839ef", "#179299", "#acb0be" }},
    brights = {{ "#6c6f85", "#d20f39", "#40a02b", "#df8e1d", "#1e66f5", "#8839ef", "#179299", "#4c4f69" }},
    foreground = "#4c4f69",
    background = "#eff1f5",
    cursor_bg = "#4c4f69",
    cursor_fg = "#eff1f5",
    cursor_border = "#4c4f69",
    selection_fg = "#4c4f69",
    selection_bg = "#ccd0da",
    split = "#bcc0cc",
    frame_active = "#eff1f5",
    frame_inactive = "#e6e9ef",
    tab_bar_bg = "#eff1f5",
    active_tab_bg = "#ccd0da",
    active_tab_fg = "#4c4f69",
    inactive_tab_bg = "#e6e9ef",
    inactive_tab_fg = "#6c6f85",
    inactive_tab_hover_bg = "#dce0e8",
    inactive_tab_hover_fg = "#4c4f69",
    new_tab_bg = "#eff1f5",
    new_tab_fg = "#6c6f85",
  }},
  solarized_light = {{
    tmux_profile = "light",
    ansi = {{ "#073642", "#dc322f", "#859900", "#b58900", "#268bd2", "#d33682", "#2aa198", "#eee8d5" }},
    brights = {{ "#002b36", "#cb4b16", "#586e75", "#657b83", "#839496", "#6c71c4", "#93a1a1", "#fdf6e3" }},
    foreground = "#657b83",
    background = "#fdf6e3",
    cursor_bg = "#586e75",
    cursor_fg = "#fdf6e3",
    cursor_border = "#586e75",
    selection_fg = "#586e75",
    selection_bg = "#eee8d5",
    split = "#93a1a1",
    frame_active = "#fdf6e3",
    frame_inactive = "#eee8d5",
    tab_bar_bg = "#fdf6e3",
    active_tab_bg = "#eee8d5",
    active_tab_fg = "#586e75",
    inactive_tab_bg = "#f5efdc",
    inactive_tab_fg = "#657b83",
    inactive_tab_hover_bg = "#eee8d5",
    inactive_tab_hover_fg = "#586e75",
    new_tab_bg = "#fdf6e3",
    new_tab_fg = "#657b83",
  }},
  tokyo_night_light = {{
    tmux_profile = "light",
    ansi = {{ "#0f0f14", "#8c4351", "#485e30", "#8f5e15", "#34548a", "#5a4a78", "#0f4b6e", "#828594" }},
    brights = {{ "#9699a3", "#8c4351", "#485e30", "#8f5e15", "#34548a", "#5a4a78", "#0f4b6e", "#343b58" }},
    foreground = "#343b58",
    background = "#d5d6db",
    cursor_bg = "#34548a",
    cursor_fg = "#d5d6db",
    cursor_border = "#34548a",
    selection_fg = "#343b58",
    selection_bg = "#b7c1e3",
    split = "#9699a3",
    frame_active = "#d5d6db",
    frame_inactive = "#c4c8da",
    tab_bar_bg = "#d5d6db",
    active_tab_bg = "#b7c1e3",
    active_tab_fg = "#343b58",
    inactive_tab_bg = "#c4c8da",
    inactive_tab_fg = "#5a607a",
    inactive_tab_hover_bg = "#b7c1e3",
    inactive_tab_hover_fg = "#343b58",
    new_tab_bg = "#d5d6db",
    new_tab_fg = "#5a607a",
  }},
  gruvbox_light = {{
    tmux_profile = "light",
    ansi = {{ "#fbf1c7", "#cc241d", "#98971a", "#d79921", "#458588", "#b16286", "#689d6a", "#7c6f64" }},
    brights = {{ "#928374", "#9d0006", "#79740e", "#b57614", "#076678", "#8f3f71", "#427b58", "#3c3836" }},
    foreground = "#3c3836",
    background = "#fbf1c7",
    cursor_bg = "#3c3836",
    cursor_fg = "#fbf1c7",
    cursor_border = "#3c3836",
    selection_fg = "#3c3836",
    selection_bg = "#ebdbb2",
    split = "#928374",
    frame_active = "#fbf1c7",
    frame_inactive = "#f2e5bc",
    tab_bar_bg = "#fbf1c7",
    active_tab_bg = "#ebdbb2",
    active_tab_fg = "#3c3836",
    inactive_tab_bg = "#f2e5bc",
    inactive_tab_fg = "#665c54",
    inactive_tab_hover_bg = "#d5c4a1",
    inactive_tab_hover_fg = "#3c3836",
    new_tab_bg = "#fbf1c7",
    new_tab_fg = "#665c54",
  }},
  rose_pine_dawn = {{
    tmux_profile = "light",
    ansi = {{ "#f2e9e1", "#b4637a", "#286983", "#ea9d34", "#56949f", "#907aa9", "#d7827e", "#797593" }},
    brights = {{ "#9893a5", "#b4637a", "#286983", "#ea9d34", "#56949f", "#907aa9", "#d7827e", "#575279" }},
    foreground = "#575279",
    background = "#faf4ed",
    cursor_bg = "#575279",
    cursor_fg = "#faf4ed",
    cursor_border = "#575279",
    selection_fg = "#575279",
    selection_bg = "#dfdad9",
    split = "#cecacd",
    frame_active = "#faf4ed",
    frame_inactive = "#f2e9e1",
    tab_bar_bg = "#faf4ed",
    active_tab_bg = "#dfdad9",
    active_tab_fg = "#575279",
    inactive_tab_bg = "#f2e9e1",
    inactive_tab_fg = "#797593",
    inactive_tab_hover_bg = "#e6e1e0",
    inactive_tab_hover_fg = "#575279",
    new_tab_bg = "#faf4ed",
    new_tab_fg = "#797593",
  }},
}}
local theme_aliases = {{
  ["system"] = "system",
  ["system-default"] = "system",
  ["auto"] = "system",
  ["os"] = "system",
  [""] = "dark",
  ["default"] = "dark",
  ["dark"] = "dark",
  ["nord"] = "dark",
  ["contrast"] = "dark",
  ["light"] = "latte",
  ["latte"] = "latte",
  ["catppuccin-latte"] = "latte",
  ["catppuccin_latte"] = "latte",
  ["solarized"] = "solarized_light",
  ["solarized-light"] = "solarized_light",
  ["solarized_light"] = "solarized_light",
  ["tokyo"] = "tokyo_night_light",
  ["tokyo-light"] = "tokyo_night_light",
  ["tokyo_light"] = "tokyo_night_light",
  ["tokyo-night-light"] = "tokyo_night_light",
  ["tokyo_night_light"] = "tokyo_night_light",
  ["tokyo-night-day"] = "tokyo_night_light",
  ["gruvbox"] = "gruvbox_light",
  ["gruvbox-light"] = "gruvbox_light",
  ["gruvbox_light"] = "gruvbox_light",
  ["rose-pine-dawn"] = "rose_pine_dawn",
  ["rose_pine_dawn"] = "rose_pine_dawn",
}}
local function normalize_theme(value)
  local key = string.lower(tostring(value or "")):gsub("%s+", "-")
  return theme_aliases[key] or "dark"
end
local function system_theme()
  if wezterm.gui and wezterm.gui.get_appearance then
    local ok, appearance = pcall(wezterm.gui.get_appearance)
    if ok and tostring(appearance):find("Dark") then
      return "dark"
    end
    if ok and tostring(appearance) ~= "" then
      return "latte"
    end
  end
  local fallback = string.lower(tostring(os.getenv("CCB_SYSTEM_THEME") or ""))
  if fallback:find("light") then
    return "latte"
  end
  return "dark"
end
local function read_theme_file(path)
  local file = io.open(path, "r")
  if not file then
    return nil
  end
  local value = file:read("*a")
  file:close()
  if value == nil or value == "" then
    return nil
  end
  if wezterm.json_parse then
    local ok, payload = pcall(wezterm.json_parse, value)
    if ok and payload then
      if payload.palette and payload.palette ~= "" then
        return payload.palette
      end
      if payload.theme and payload.theme ~= "" then
        return payload.theme
      end
    end
  end
  return value:match('"palette"%s*:%s*"([^"]+)"') or value:match('"theme"%s*:%s*"([^"]+)"') or value:match("^%s*([^%s]+)")
end
local requested_theme = read_theme_file(theme_config_path) or os.getenv("CCB_WORKBENCH_THEME") or os.getenv("CCB_TMUX_THEME_PROFILE") or "dark"
local theme_name = normalize_theme(requested_theme)
if theme_name == "system" then
  theme_name = system_theme()
end
local theme = themes[theme_name] or themes.dark
config.font = wezterm.font_with_fallback({{
  "JetBrains Mono",
  "Fira Code",
  "Noto Sans Mono",
  "Noto Sans Mono CJK SC",
  "Noto Sans Symbols2",
  "Symbols Nerd Font Mono",
  "Symbols Nerd Font",
  "Unifont CSUR",
  "Apple Color Emoji",
  "Segoe UI Emoji",
  "Noto Color Emoji",
  "monospace",
}})
config.harfbuzz_features = {{ "calt=0", "clig=0", "liga=0" }}
config.font_size = 10.5
config.line_height = 1.05
config.cell_width = 1.0
config.initial_cols = 132
config.initial_rows = 38
config.enable_scroll_bar = false
config.enable_tab_bar = false
config.use_fancy_tab_bar = false
config.hide_tab_bar_if_only_one_tab = true
config.window_padding = {{
  left = 4,
  right = 4,
  top = 2,
  bottom = 2,
}}
config.window_frame = {{
  font = wezterm.font("JetBrains Mono"),
  font_size = 9.5,
  active_titlebar_bg = theme.frame_active,
  inactive_titlebar_bg = theme.frame_inactive,
}}
config.colors = {{
  ansi = theme.ansi,
  brights = theme.brights,
  foreground = theme.foreground,
  background = theme.background,
  cursor_bg = theme.cursor_bg,
  cursor_fg = theme.cursor_fg,
  cursor_border = theme.cursor_border,
  selection_fg = theme.selection_fg,
  selection_bg = theme.selection_bg,
  split = theme.split,
  tab_bar = {{
    background = theme.tab_bar_bg,
    active_tab = {{
      bg_color = theme.active_tab_bg,
      fg_color = theme.active_tab_fg,
    }},
    inactive_tab = {{
      bg_color = theme.inactive_tab_bg,
      fg_color = theme.inactive_tab_fg,
    }},
    inactive_tab_hover = {{
      bg_color = theme.inactive_tab_hover_bg,
      fg_color = theme.inactive_tab_hover_fg,
    }},
    new_tab = {{
      bg_color = theme.new_tab_bg,
      fg_color = theme.new_tab_fg,
    }},
  }},
}}
config.set_environment_variables = {{
  CCB_WORKBENCH_PROFILE = "rich",
  CCB_WORKBENCH_THEME = theme_name,
  CCB_WORKBENCH_ROOT = "{_lua_string(str(paths['root']))}",
  CCB_WORKBENCH_TERMINAL_PROGRAM = "WezTerm",
  CCB_WORKBENCH_TERMINAL_PROGRAM_VERSION = wezterm.version,
  CCB_TMUX_THEME_PROFILE = theme.tmux_profile,
  CCB_SIDEBAR_THEME_PROFILE = theme.tmux_profile,
  CCB_WORKBENCH_YAZI_SAFE_CONFIG = "{_lua_string(str(paths['yazi_safe_profile']))}",
  CCB_WORKBENCH_YAZI_RICH_CONFIG = "{_lua_string(str(paths['yazi_rich_profile']))}",
}}

return config
''',
        encoding='utf-8',
    )


def _write_wrappers(paths: dict[str, Path]) -> None:
    path_prefix = _shell_quote(str(paths['bin_dir']))
    _write_executable(
        paths['yazi_wrapper'],
        f'''#!/usr/bin/env sh
{GENERATED_MARKER}
set -eu
export CCB_WORKBENCH_ROOT={_shell_quote(str(paths['root']))}
export YAZI_CONFIG_HOME={_shell_quote(str(paths['yazi_safe_profile']))}
export PATH={path_prefix}${{PATH:+":$PATH"}}
exec yazi "$@"
''',
    )
    _write_executable(
        paths['yazi_rich_wrapper'],
        f'''#!/usr/bin/env sh
{GENERATED_MARKER}
set -eu
export CCB_WORKBENCH_ROOT={_shell_quote(str(paths['root']))}
export PATH={path_prefix}${{PATH:+":$PATH"}}
case "${{CCB_WORKBENCH_FORCE_RICH:-}}" in
  1|true|yes) rich=1 ;;
  *) rich=0 ;;
esac
term_program="$(printf '%s' "${{TERM_PROGRAM:-}}" | tr '[:upper:]' '[:lower:]')"
if [ "$rich" = 0 ] && [ -z "${{TMUX:-}}" ]; then
  case "$term_program" in
    *wezterm*|*kitty*|*ghostty*) rich=1 ;;
  esac
fi
if [ "$rich" = 0 ] && [ -n "${{KITTY_WINDOW_ID:-}}" ] && [ -z "${{TMUX:-}}" ]; then
  rich=1
fi
if [ "$rich" = 1 ]; then
  case "${{TERM_PROGRAM:-}}" in
    ""|tmux)
      if [ -n "${{CCB_WORKBENCH_TERMINAL_PROGRAM:-}}" ]; then
        export TERM_PROGRAM="${{CCB_WORKBENCH_TERMINAL_PROGRAM}}"
      fi
      if [ -n "${{CCB_WORKBENCH_TERMINAL_PROGRAM_VERSION:-}}" ]; then
        export TERM_PROGRAM_VERSION="${{CCB_WORKBENCH_TERMINAL_PROGRAM_VERSION}}"
      fi
      ;;
  esac
  export YAZI_CONFIG_HOME={_shell_quote(str(paths['yazi_rich_profile']))}
else
  export YAZI_CONFIG_HOME={_shell_quote(str(paths['yazi_safe_profile']))}
fi
exec yazi "$@"
''',
    )
    _write_executable(
        paths['wrapper'],
        f'''#!/usr/bin/env sh
{GENERATED_MARKER}
set -eu
export CCB_WORKBENCH_ROOT={_shell_quote(str(paths['root']))}
export PATH={path_prefix}${{PATH:+":$PATH"}}
cmd="${{1:-files}}"
configure_input_method_env() {{
  if [ -z "${{XMODIFIERS:-}}" ]; then
    if command -v pgrep >/dev/null 2>&1 && pgrep -x fcitx5 >/dev/null 2>&1; then
      export XMODIFIERS='@im=fcitx'
    elif command -v pgrep >/dev/null 2>&1 && pgrep -x fcitx >/dev/null 2>&1; then
      export XMODIFIERS='@im=fcitx'
    elif command -v pgrep >/dev/null 2>&1 && pgrep -x ibus-daemon >/dev/null 2>&1; then
      export XMODIFIERS='@im=ibus'
    elif command -v fcitx5 >/dev/null 2>&1 || command -v fcitx >/dev/null 2>&1; then
      export XMODIFIERS='@im=fcitx'
    elif command -v ibus-daemon >/dev/null 2>&1; then
      export XMODIFIERS='@im=ibus'
    fi
  fi
  case "${{XMODIFIERS:-}}" in
    *@im=fcitx*)
      [ -n "${{GTK_IM_MODULE:-}}" ] || export GTK_IM_MODULE=fcitx
      [ -n "${{QT_IM_MODULE:-}}" ] || export QT_IM_MODULE=fcitx
      ;;
    *@im=ibus*)
      [ -n "${{GTK_IM_MODULE:-}}" ] || export GTK_IM_MODULE=ibus
      [ -n "${{QT_IM_MODULE:-}}" ] || export QT_IM_MODULE=ibus
      ;;
  esac
}}
configure_wayland_cursor_env() {{
  [ "${{is_wsl:-0}}" = 0 ] || return 0
  if [ -z "${{WAYLAND_DISPLAY:-}}" ] && [ "${{XDG_SESSION_TYPE:-}}" != wayland ]; then
    return 0
  fi
  cursor_asset={_shell_quote(str(paths['wezterm_cursor_asset']))}
  cursor_overlay_root={_shell_quote(str(paths['wezterm_xcursor_root']))}
  [ -r "$cursor_asset" ] || return 0
  cursor_theme="${{XCURSOR_THEME:-default}}"
  case "$cursor_theme" in
    ""|*[!A-Za-z0-9._+-]*) cursor_theme=default ;;
  esac
  cursor_dir="$cursor_overlay_root/$cursor_theme/cursors"
  if [ ! -r "$cursor_dir/hand" ]; then
    mkdir -p "$cursor_dir" || return 0
    cursor_tmp="$cursor_dir/.hand.$$"
    if cp "$cursor_asset" "$cursor_tmp" 2>/dev/null; then
      mv -f "$cursor_tmp" "$cursor_dir/hand"
    else
      rm -f "$cursor_tmp"
      return 0
    fi
  fi
  if [ -n "${{XCURSOR_PATH:-}}" ]; then
    case ":$XCURSOR_PATH:" in
      *":$cursor_overlay_root:"*) ;;
      *) export XCURSOR_PATH="$cursor_overlay_root:$XCURSOR_PATH" ;;
    esac
  else
    cursor_data_home="${{XDG_DATA_HOME:-${{HOME:-}}/.local/share}}"
    export XCURSOR_PATH="$cursor_overlay_root:${{HOME:-}}/.icons:$cursor_data_home/icons:/usr/share/icons:/usr/share/pixmaps"
  fi
  export CCB_WORKBENCH_XCURSOR_COMPAT=1
}}
check_linux_inotify_capacity() {{
  if ! command -v python3 >/dev/null 2>&1; then
    return 0
  fi
  probe_status=0
  python3 -c 'import ctypes, errno, os, sys
libc = ctypes.CDLL(None, use_errno=True)
fd = libc.inotify_init1(0o2000000)
if fd >= 0:
    os.close(fd)
    raise SystemExit(0)
err = ctypes.get_errno()
raise SystemExit(75 if err in (errno.EMFILE, errno.ENFILE) else 0)
' >/dev/null 2>&1 || probe_status=$?
  if [ "$probe_status" = 75 ]; then
    proc_root="${{CCB_WORKBENCH_PROC_ROOT:-/proc}}"
    limit_file="$proc_root/sys/fs/inotify/max_user_instances"
    limit="unknown"
    if [ -r "$limit_file" ]; then
      limit="$(cat "$limit_file" 2>/dev/null || printf '%s' unknown)"
    fi
    printf '%s\\n' "ccb-workbench terminal cannot start a new WezTerm window: Linux inotify instance allocation failed (max_user_instances=$limit)." >&2
    printf '%s\\n' "temporary_fix: sudo sysctl -w fs.inotify.max_user_instances=1024" >&2
    printf '%s\\n' "persistent_fix: echo fs.inotify.max_user_instances=1024 | sudo tee /etc/sysctl.d/99-ccb-inotify.conf && sudo sysctl --system" >&2
    printf '%s\\n' "workaround: reuse an existing CCB WezTerm window or close idle file-manager/Codex/CCB windows." >&2
    return 75
  fi
  return 0
}}
normalize_workbench_theme() {{
  key="$(printf '%s' "${{1:-}}" | tr '[:upper:]' '[:lower:]' | tr '_' '-' | tr ' ' '-')"
  case "$key" in
    system|system-default|auto|os) printf '%s\\n' system ;;
    ""|default|dark|nord|contrast) printf '%s\\n' dark ;;
    light|latte|catppuccin-latte) printf '%s\\n' latte ;;
    solarized|solarized-light) printf '%s\\n' solarized_light ;;
    tokyo|tokyo-light|tokyo-night-light|tokyo-night-day) printf '%s\\n' tokyo_night_light ;;
    gruvbox|gruvbox-light) printf '%s\\n' gruvbox_light ;;
    rose-pine-dawn) printf '%s\\n' rose_pine_dawn ;;
    *) printf '%s\\n' dark ;;
  esac
}}
detect_system_workbench_theme() {{
  explicit="$(printf '%s' "${{CCB_SYSTEM_THEME:-}}" | tr '[:upper:]' '[:lower:]')"
  case "$explicit" in
    *light*) printf '%s\\n' latte; return 0 ;;
    *dark*) printf '%s\\n' dark; return 0 ;;
  esac
  case "$(printf '%s' "${{GTK_THEME:-${{QT_STYLE_OVERRIDE:-}}}}" | tr '[:upper:]' '[:lower:]')" in
    *dark*) printf '%s\\n' dark; return 0 ;;
  esac
  if command -v powershell.exe >/dev/null 2>&1; then
    windows_light="$(powershell.exe -NoProfile -NonInteractive -Command "(Get-ItemProperty 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize' -Name AppsUseLightTheme).AppsUseLightTheme" 2>/dev/null | tr -d '\\r\\n ' || true)"
    case "$windows_light" in
      0) printf '%s\\n' dark; return 0 ;;
      1) printf '%s\\n' latte; return 0 ;;
    esac
  fi
  if command -v defaults >/dev/null 2>&1; then
    if defaults read -g AppleInterfaceStyle 2>/dev/null | grep -qi dark; then
      printf '%s\\n' dark
    else
      printf '%s\\n' latte
    fi
    return 0
  fi
  if command -v gsettings >/dev/null 2>&1; then
    system_scheme="$(gsettings get org.gnome.desktop.interface color-scheme 2>/dev/null || true)"
    if printf '%s' "$system_scheme" | grep -qi dark; then
      printf '%s\\n' dark
      return 0
    fi
    gtk_theme="$(gsettings get org.gnome.desktop.interface gtk-theme 2>/dev/null || true)"
    if [ -n "$gtk_theme" ]; then
      if printf '%s' "$gtk_theme" | grep -qi dark; then
        printf '%s\\n' dark
      else
        printf '%s\\n' latte
      fi
      return 0
    fi
  fi
  printf '%s\\n' dark
}}
theme_config_file={_shell_quote(str(paths['theme_config']))}
read_workbench_theme_config() {{
  if [ ! -r "$theme_config_file" ]; then
    return 0
  fi
  value="$(sed -n 's/.*"palette"[[:space:]]*:[[:space:]]*"\\([^"]*\\)".*/\\1/p' "$theme_config_file" 2>/dev/null | sed -n '1p')"
  if [ -n "$value" ]; then
    printf '%s\\n' "$value"
    return 0
  fi
  sed -n 's/.*"theme"[[:space:]]*:[[:space:]]*"\\([^"]*\\)".*/\\1/p' "$theme_config_file" 2>/dev/null | sed -n '1p'
}}
requested_theme="$(read_workbench_theme_config)"
if [ -z "$requested_theme" ]; then
  requested_theme="${{CCB_WORKBENCH_THEME:-${{CCB_TMUX_THEME_PROFILE:-}}}}"
fi
workbench_theme="$(normalize_workbench_theme "${{requested_theme:-dark}}")"
if [ "$workbench_theme" = system ]; then
  workbench_theme="$(detect_system_workbench_theme)"
fi
case "$workbench_theme" in
  dark) workbench_tmux_theme=default ;;
  *) workbench_tmux_theme=light ;;
esac
export CCB_WORKBENCH_THEME="$workbench_theme"
export CCB_TMUX_THEME_PROFILE="$workbench_tmux_theme"
export CCB_SIDEBAR_THEME_PROFILE="$workbench_tmux_theme"
case "$cmd" in
  files|yazi)
    shift || true
    exec ccb-yazi-rich "$@"
    ;;
  commands|--print-commands)
    printf '%s\\n' 'ccb-yazi-rich "$PWD"'
    ;;
  terminal|wezterm)
    shift || true
    is_wsl=0
    if [ -n "${{WSL_DISTRO_NAME:-}}" ] || [ -n "${{WSL_INTEROP:-}}" ]; then
      is_wsl=1
    elif [ -r /proc/version ] && grep -qi microsoft /proc/version 2>/dev/null; then
      is_wsl=1
    fi
    find_windows_wezterm() {{
      if command -v wezterm.exe >/dev/null 2>&1; then
        command -v wezterm.exe
        return 0
      fi
      if [ -n "${{CCB_WORKBENCH_WEZTERM_EXE:-}}" ] && [ -f "${{CCB_WORKBENCH_WEZTERM_EXE}}" ]; then
        printf '%s\\n' "${{CCB_WORKBENCH_WEZTERM_EXE}}"
        return 0
      fi
      for candidate in \
        '/mnt/c/Program Files/WezTerm/wezterm.exe' \
        '/mnt/c/Program Files (x86)/WezTerm/wezterm.exe'
      do
        if [ -f "$candidate" ]; then
          printf '%s\\n' "$candidate"
          return 0
        fi
      done
      for candidate in /mnt/c/Users/*/AppData/Local/Programs/WezTerm/wezterm.exe; do
        if [ -f "$candidate" ]; then
          printf '%s\\n' "$candidate"
          return 0
        fi
      done
      return 1
    }}
    wsl_to_windows_path() {{
      if command -v wslpath >/dev/null 2>&1; then
        wslpath -w "$1" 2>/dev/null || printf '%s\\n' "$1"
      else
        printf '%s\\n' "$1"
      fi
    }}
    wezterm_bin=""
    wezterm_windows=""
    if [ "$is_wsl" = 1 ]; then
      wezterm_windows="$(find_windows_wezterm || true)"
    fi
    if [ -z "$wezterm_windows" ] && command -v wezterm >/dev/null 2>&1; then
      wezterm_bin="$(command -v wezterm)"
    fi
    if [ -z "$wezterm_windows" ] && [ -z "$wezterm_bin" ]; then
      printf '%s\\n' 'ccb-workbench terminal requires WezTerm or Windows wezterm.exe under WSL' >&2
      exit 127
    fi
    configure_input_method_env
    configure_wayland_cursor_env
    if [ "$#" -eq 0 ]; then
      set -- "${{SHELL:-/bin/sh}}" -lc 'ccb-yazi-rich "$PWD"'
    fi
    term_program="$(printf '%s' "${{TERM_PROGRAM:-}}" | tr '[:upper:]' '[:lower:]')"
    workbench_terminal="$(printf '%s' "${{CCB_WORKBENCH_TERMINAL_PROGRAM:-}}" | tr '[:upper:]' '[:lower:]')"
    in_wezterm=0
    if [ -n "${{WEZTERM_PANE:-}}" ] || [ -n "${{WEZTERM_EXECUTABLE:-}}" ] || [ -n "${{WEZTERM_UNIX_SOCKET:-}}" ] || [ "$term_program" = "wezterm" ] || [ "$workbench_terminal" = "wezterm" ]; then
      in_wezterm=1
    fi
    reuse_current_wezterm=0
    if [ "$in_wezterm" = 1 ]; then
      current_workbench_root="${{CCB_WORKBENCH_ROOT:-}}"
      current_workbench_profile="$(printf '%s' "${{CCB_WORKBENCH_PROFILE:-}}" | tr '[:upper:]' '[:lower:]')"
      if [ "$current_workbench_profile" = "rich" ] && [ "$current_workbench_root" = {_shell_quote(str(paths['root']))} ]; then
        reuse_current_wezterm=1
      fi
    fi
    if [ -n "$wezterm_windows" ]; then
      config_win="$(wsl_to_windows_path {_shell_quote(str(paths['wezterm_config']))})"
      if [ "$reuse_current_wezterm" = 1 ]; then
        if [ -n "${{WSL_DISTRO_NAME:-}}" ]; then
          exec "$wezterm_windows" cli spawn -- wsl.exe -d "$WSL_DISTRO_NAME" --cd "$PWD" -- env \
            -u TMUX \
            -u TMUX_PANE \
            -u CCB_TMUX_SOCKET \
            -u CCB_TMUX_SOCKET_PATH \
            PATH="$PATH" \
            CCB_WORKBENCH_PROFILE=rich \
            CCB_WORKBENCH_THEME="$workbench_theme" \
            CCB_WORKBENCH_ROOT={_shell_quote(str(paths['root']))} \
            CCB_WORKBENCH_TERMINAL_PROGRAM=WezTerm \
            CCB_TMUX_THEME_PROFILE="$workbench_tmux_theme" \
            CCB_SIDEBAR_THEME_PROFILE="$workbench_tmux_theme" \
            CCB_WORKBENCH_YAZI_SAFE_CONFIG={_shell_quote(str(paths['yazi_safe_profile']))} \
            CCB_WORKBENCH_YAZI_RICH_CONFIG={_shell_quote(str(paths['yazi_rich_profile']))} \
            CCB_WORKBENCH_FORCE_RICH=1 \
            "$@"
        fi
        exec "$wezterm_windows" cli spawn -- wsl.exe --cd "$PWD" -- env \
          -u TMUX \
          -u TMUX_PANE \
          -u CCB_TMUX_SOCKET \
          -u CCB_TMUX_SOCKET_PATH \
          PATH="$PATH" \
          CCB_WORKBENCH_PROFILE=rich \
          CCB_WORKBENCH_THEME="$workbench_theme" \
          CCB_WORKBENCH_ROOT={_shell_quote(str(paths['root']))} \
          CCB_WORKBENCH_TERMINAL_PROGRAM=WezTerm \
          CCB_TMUX_THEME_PROFILE="$workbench_tmux_theme" \
          CCB_SIDEBAR_THEME_PROFILE="$workbench_tmux_theme" \
          CCB_WORKBENCH_YAZI_SAFE_CONFIG={_shell_quote(str(paths['yazi_safe_profile']))} \
          CCB_WORKBENCH_YAZI_RICH_CONFIG={_shell_quote(str(paths['yazi_rich_profile']))} \
          CCB_WORKBENCH_FORCE_RICH=1 \
          "$@"
      fi
      if [ -n "${{WSL_DISTRO_NAME:-}}" ]; then
        exec "$wezterm_windows" --config-file "$config_win" \
          start --always-new-process --no-auto-connect -- wsl.exe -d "$WSL_DISTRO_NAME" --cd "$PWD" -- env \
          -u TMUX \
          -u TMUX_PANE \
          -u CCB_TMUX_SOCKET \
          -u CCB_TMUX_SOCKET_PATH \
          PATH="$PATH" \
          CCB_WORKBENCH_PROFILE=rich \
          CCB_WORKBENCH_THEME="$workbench_theme" \
          CCB_WORKBENCH_ROOT={_shell_quote(str(paths['root']))} \
          CCB_WORKBENCH_TERMINAL_PROGRAM=WezTerm \
          CCB_TMUX_THEME_PROFILE="$workbench_tmux_theme" \
          CCB_SIDEBAR_THEME_PROFILE="$workbench_tmux_theme" \
          CCB_WORKBENCH_YAZI_SAFE_CONFIG={_shell_quote(str(paths['yazi_safe_profile']))} \
          CCB_WORKBENCH_YAZI_RICH_CONFIG={_shell_quote(str(paths['yazi_rich_profile']))} \
          CCB_WORKBENCH_FORCE_RICH=1 \
          "$@"
      fi
      exec "$wezterm_windows" --config-file "$config_win" \
        start --always-new-process --no-auto-connect -- wsl.exe --cd "$PWD" -- env \
        -u TMUX \
        -u TMUX_PANE \
        -u CCB_TMUX_SOCKET \
        -u CCB_TMUX_SOCKET_PATH \
        PATH="$PATH" \
        CCB_WORKBENCH_PROFILE=rich \
        CCB_WORKBENCH_THEME="$workbench_theme" \
        CCB_WORKBENCH_ROOT={_shell_quote(str(paths['root']))} \
        CCB_WORKBENCH_TERMINAL_PROGRAM=WezTerm \
        CCB_TMUX_THEME_PROFILE="$workbench_tmux_theme" \
        CCB_SIDEBAR_THEME_PROFILE="$workbench_tmux_theme" \
        CCB_WORKBENCH_YAZI_SAFE_CONFIG={_shell_quote(str(paths['yazi_safe_profile']))} \
        CCB_WORKBENCH_YAZI_RICH_CONFIG={_shell_quote(str(paths['yazi_rich_profile']))} \
        CCB_WORKBENCH_FORCE_RICH=1 \
        "$@"
    fi
    if [ "$reuse_current_wezterm" = 1 ]; then
      exec "$wezterm_bin" cli spawn --cwd "$PWD" -- env \
        -u TMUX \
        -u TMUX_PANE \
        -u CCB_TMUX_SOCKET \
        -u CCB_TMUX_SOCKET_PATH \
        CCB_WORKBENCH_PROFILE=rich \
        CCB_WORKBENCH_THEME="$workbench_theme" \
        CCB_WORKBENCH_ROOT={_shell_quote(str(paths['root']))} \
        CCB_WORKBENCH_TERMINAL_PROGRAM=WezTerm \
        CCB_TMUX_THEME_PROFILE="$workbench_tmux_theme" \
        CCB_SIDEBAR_THEME_PROFILE="$workbench_tmux_theme" \
        CCB_WORKBENCH_YAZI_SAFE_CONFIG={_shell_quote(str(paths['yazi_safe_profile']))} \
        CCB_WORKBENCH_YAZI_RICH_CONFIG={_shell_quote(str(paths['yazi_rich_profile']))} \
        CCB_WORKBENCH_FORCE_RICH=1 \
        "$@"
    fi
    check_linux_inotify_capacity
    exec "$wezterm_bin" --config-file {_shell_quote(str(paths['wezterm_config']))} \
      start --always-new-process --no-auto-connect --cwd "$PWD" -- env \
      -u TMUX \
      -u TMUX_PANE \
      -u CCB_TMUX_SOCKET \
      -u CCB_TMUX_SOCKET_PATH \
      CCB_WORKBENCH_PROFILE=rich \
      CCB_WORKBENCH_THEME="$workbench_theme" \
      CCB_WORKBENCH_ROOT={_shell_quote(str(paths['root']))} \
      CCB_WORKBENCH_TERMINAL_PROGRAM=WezTerm \
      CCB_TMUX_THEME_PROFILE="$workbench_tmux_theme" \
      CCB_SIDEBAR_THEME_PROFILE="$workbench_tmux_theme" \
      CCB_WORKBENCH_YAZI_SAFE_CONFIG={_shell_quote(str(paths['yazi_safe_profile']))} \
      CCB_WORKBENCH_YAZI_RICH_CONFIG={_shell_quote(str(paths['yazi_rich_profile']))} \
      CCB_WORKBENCH_FORCE_RICH=1 \
      "$@"
    ;;
  *)
    exec ccb-yazi-rich "$@"
    ;;
esac
''',
    )


def _write_executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    path.chmod(0o755)


def _write_bin_links(paths: dict[str, Path]) -> None:
    for key, link_key in (
        ('wrapper', 'wrapper_link'),
        ('yazi_wrapper', 'yazi_link'),
        ('yazi_rich_wrapper', 'yazi_rich_link'),
        ('md_preview', 'md_preview_link'),
        ('image_preview', 'image_preview_link'),
        ('pdf_preview', 'pdf_preview_link'),
        ('video_preview', 'video_preview_link'),
    ):
        _activate_link(paths[key], paths[link_key])


def _activate_link(target: Path, link: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(target)
    except Exception:
        shutil.copy2(target, link)
        link.chmod(0o755)


def _remove_bin_links(paths: dict[str, Path]) -> None:
    for link_key in (
        'wrapper_link',
        'yazi_link',
        'yazi_rich_link',
        'md_preview_link',
        'image_preview_link',
        'pdf_preview_link',
        'video_preview_link',
    ):
        link = paths[link_key]
        try:
            if link.is_symlink():
                target = link.resolve(strict=False)
                if str(target).startswith(str(paths['root'])):
                    link.unlink()
                continue
            if link.is_file() and GENERATED_MARKER in link.read_text(encoding='utf-8', errors='ignore')[:300]:
                link.unlink()
        except Exception:
            continue


def _build_status(
    paths: dict[str, Path],
    *,
    profile: str,
    installed: bool,
    enabled: bool | None = None,
) -> dict[str, object]:
    manifest = _read_manifest(paths)
    component_status = _component_statuses(paths, profile=profile, manifest=manifest)
    status_value, degraded_reasons = _rollup_status(component_status)
    if not installed:
        status_value = 'missing'
    if enabled is None:
        enabled = bool(manifest.get('enabled', False))
    return {
        'schema_version': SCHEMA_VERSION,
        'status': status_value,
        'profile': profile,
        'enabled': enabled,
        'installed': installed,
        'installed_at': manifest.get('installed_at') or _now(),
        'updated_at': _now(),
        'degraded_reasons': degraded_reasons,
        'components': component_status,
        'paths': _status_paths(paths),
        **_flatten_component_status(component_status),
        **_status_paths(paths),
    }


def _component_statuses(
    paths: dict[str, Path],
    *,
    profile: str,
    manifest: dict[str, object],
) -> dict[str, dict[str, object]]:
    del profile, manifest
    yazi = _tool_component('yazi', ('yazi',))
    ya = _tool_component('ya', ('ya',))
    wezterm = _wezterm_component()
    wayland_cursor = _wayland_cursor_component(paths)
    pdf_text = _tool_component('pdf_text', ('pdftotext', 'pdfinfo'), require_all=True)
    pdf_image = _tool_component('pdf_image', ('pdftoppm', 'pdftocairo'))
    image_preview = _image_component(paths)
    video_metadata = _tool_component('video_metadata', ('ffprobe',))
    video_thumbnail = _tool_component('video_thumbnail', ('ffmpeg',))
    terminal = _terminal_component()
    markdown = _markdown_component(paths)
    config = _config_component(paths)
    return {
        'config': config,
        'terminal': terminal,
        'wezterm': wezterm,
        'wayland_cursor': wayland_cursor,
        'yazi': yazi,
        'ya': ya,
        'markdown': markdown,
        'image_preview': image_preview,
        'pdf_text': pdf_text,
        'pdf_image': pdf_image,
        'video_metadata': video_metadata,
        'video_thumbnail': video_thumbnail,
    }


def _tool_component(name: str, commands: tuple[str, ...], *, require_all: bool = False) -> dict[str, object]:
    found: list[str] = []
    missing: list[str] = []
    for command in commands:
        path = _which_workbench_command(command)
        if path:
            found.append(f'{command}:{path}')
        else:
            missing.append(command)
    if require_all:
        ok = not missing
    else:
        ok = bool(found)
    status = 'ok' if ok else 'missing'
    result: dict[str, object] = {'status': status}
    if found:
        result['tools'] = ','.join(found)
    if missing:
        result['missing'] = ','.join(missing)
    if status != 'ok':
        result['reason'] = f'{name} helper not found'
    return result


def _wezterm_component() -> dict[str, object]:
    path = _wezterm_command()
    if path:
        result: dict[str, object] = {'status': 'ok', 'tools': f'wezterm:{path}'}
        if _is_wsl() and path.lower().endswith('wezterm.exe'):
            result['tool'] = 'windows-native'
        return result
    return {'status': 'missing', 'missing': 'wezterm', 'reason': 'wezterm helper not found'}


def _wayland_cursor_component(paths: dict[str, Path]) -> dict[str, object]:
    if platform.system() != 'Linux':
        return {'status': 'not_applicable'}
    asset = paths['wezterm_cursor_asset']
    if _valid_xcursor_asset(asset):
        return {'status': 'ok', 'tool': str(asset)}
    return {
        'status': 'unavailable',
        'reason': 'no local XCursor pointer asset was available for the optional Wayland hand-cursor overlay',
    }


def _terminal_component() -> dict[str, object]:
    term = str(os.environ.get('TERM') or '')
    term_program = str(os.environ.get('TERM_PROGRAM') or '')
    term_lower = term_program.lower()
    in_tmux = bool(os.environ.get('TMUX')) or term.startswith('tmux')
    rich_candidate = bool(os.environ.get('KITTY_WINDOW_ID')) or any(value in term_lower for value in ('wezterm', 'kitty', 'ghostty'))
    if rich_candidate and not in_tmux:
        return {
            'status': 'ok',
            'terminal_program': term_program or term or 'unknown',
            'image_protocol': 'candidate',
        }
    reason = 'tmux image passthrough is not verified' if in_tmux else 'no rich terminal image protocol detected'
    return {
        'status': 'degraded',
        'terminal_program': term_program or term or 'unknown',
        'image_protocol': 'degraded',
        'reason': reason,
    }


def _markdown_component(paths: dict[str, Path]) -> dict[str, object]:
    if _which_workbench_command('glow'):
        return {'status': 'ok', 'tool': 'glow'}
    if _which_workbench_command('mdcat'):
        return {'status': 'ok', 'tool': 'mdcat'}
    if _which_workbench_command('python3') and paths['md_preview'].is_file():
        return {'status': 'ok', 'tool': 'python3-rich-or-plain'}
    return {'status': 'missing', 'reason': 'no Markdown renderer helper found'}


def _image_component(paths: dict[str, Path]) -> dict[str, object]:
    if not paths['image_preview'].is_file():
        return {'status': 'missing', 'reason': 'generated image preview helper not found'}
    found: list[str] = []
    for command in ('chafa', 'identify', 'file'):
        path = _which_workbench_command(command)
        if path:
            found.append(f'{command}:{path}')
    if found:
        return {'status': 'ok', 'tools': ','.join(found)}
    return {'status': 'missing', 'reason': 'no image preview helper found'}


def _config_component(paths: dict[str, Path]) -> dict[str, object]:
    required = (
        paths['wrapper'],
        paths['yazi_wrapper'],
        paths['yazi_rich_wrapper'],
        paths['md_preview'],
        paths['image_preview'],
        paths['pdf_preview'],
        paths['video_preview'],
        paths['file_opener'],
        paths['yazi_safe_profile'] / 'yazi.toml',
        paths['yazi_rich_profile'] / 'yazi.toml',
        paths['yazi_safe_profile'] / 'keymap.toml',
        paths['yazi_rich_profile'] / 'keymap.toml',
        paths['yazi_safe_profile'] / 'init.lua',
        paths['yazi_rich_profile'] / 'init.lua',
        paths['yazi_safe_profile'] / 'plugins' / 'ccb-open.yazi' / 'main.lua',
        paths['yazi_rich_profile'] / 'plugins' / 'ccb-open.yazi' / 'main.lua',
        paths['yazi_safe_profile'] / 'plugins' / 'piper.yazi' / 'main.lua',
        paths['yazi_rich_profile'] / 'plugins' / 'piper.yazi' / 'main.lua',
        paths['wezterm_config'],
    )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        return {'status': 'missing', 'reason': 'generated workbench config is incomplete', 'missing': ';'.join(missing)}
    return {'status': 'ok'}


def _rollup_status(components: dict[str, dict[str, object]]) -> tuple[str, list[str]]:
    required = ('config', 'yazi', 'markdown')
    missing_required = [
        name
        for name in required
        if components.get(name, {}).get('status') not in {'ok', 'degraded'}
    ]
    reasons: list[str] = []
    for name, payload in components.items():
        if payload.get('status') in {'missing', 'failed', 'degraded'} and payload.get('reason'):
            reasons.append(f'{name}: {payload["reason"]}')
    if missing_required:
        return 'degraded', reasons or [f'missing required component: {",".join(missing_required)}']
    if reasons:
        return 'degraded', reasons
    return 'ok', []


def _flatten_component_status(components: dict[str, dict[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, payload in components.items():
        result[f'{name}_status'] = payload.get('status')
        for key in ('tool', 'tools', 'wrapper', 'terminal_program', 'image_protocol', 'reason', 'missing'):
            if payload.get(key):
                result[f'{name}_{key}'] = payload.get(key)
    return result


def _status_paths(paths: dict[str, Path]) -> dict[str, object]:
    return {
        'root': str(paths['root']),
        'manifest': str(paths['manifest']),
        'bin_dir': str(paths['bin_dir']),
        'bin_link_dir': str(paths['bin_link_dir']),
        'yazi_safe_config': str(paths['yazi_safe_profile']),
        'yazi_rich_config': str(paths['yazi_rich_profile']),
        'wezterm_config': str(paths['wezterm_config']),
        'wezterm_xcursor_root': str(paths['wezterm_xcursor_root']),
        'state_root': str(paths['state_root']),
        'theme_config': str(paths['theme_config']),
        'cache_root': str(paths['cache_root']),
    }


def _launch_commands(paths: dict[str, Path]) -> list[str]:
    return [
        f'{paths["wrapper"]} terminal',
        f'{paths["yazi_rich_wrapper"]} "$PWD"',
    ]


def _ccb_entrypoint(script_root: Path) -> Path:
    if os.environ.get('CCB_TEST_ENTRYPOINT') == '1':
        test_wrapper = script_root / 'ccb_test'
        if test_wrapper.exists():
            return test_wrapper
    return script_root / 'ccb'


def _record_launch(paths: dict[str, Path], *, pid: int, command: list[str]) -> None:
    records = _read_launches(paths)
    records.append({'pid': pid, 'command': command, 'started_at': _now()})
    paths['launches'].parent.mkdir(parents=True, exist_ok=True)
    paths['launches'].write_text(json.dumps(records, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def _read_launches(paths: dict[str, Path]) -> list[dict[str, object]]:
    try:
        value = json.loads(paths['launches'].read_text(encoding='utf-8'))
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    except Exception:
        pass
    return []


def _close_recorded_processes(paths: dict[str, Path]) -> dict[str, object]:
    records = _read_launches(paths)
    closed = 0
    survivors: list[dict[str, object]] = []
    for record in records:
        try:
            pid = int(record.get('pid', 0))
        except Exception:
            continue
        if pid <= 0:
            continue
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            survivors.append(record)
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            closed += 1
        except ProcessLookupError:
            pass
        except PermissionError:
            survivors.append(record)
    paths['launches'].parent.mkdir(parents=True, exist_ok=True)
    paths['launches'].write_text(json.dumps(survivors, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    if survivors:
        return {'status': 'degraded', 'closed_processes': closed, 'reason': 'some recorded workbench processes could not be closed'}
    return {'status': 'ok', 'closed_processes': closed}


def _read_manifest(paths: dict[str, Path]) -> dict[str, object]:
    try:
        value = json.loads(paths['manifest'].read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _write_manifest(paths: dict[str, Path], payload: dict[str, object]) -> None:
    paths['manifest'].parent.mkdir(parents=True, exist_ok=True)
    paths['manifest'].write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def _parse_options(args: list[str]) -> dict[str, object]:
    result: dict[str, object] = {'profile': DEFAULT_PROFILE}
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == '--profile':
            if index + 1 >= len(args):
                return {'error': '--profile requires a value'}
            result['profile'] = args[index + 1]
            index += 2
            continue
        if arg.startswith('--profile='):
            result['profile'] = arg.split('=', 1)[1]
            index += 1
            continue
        if arg == '--dry-run':
            result['dry_run'] = True
            index += 1
            continue
        if arg == '--remove-cache':
            result['remove_cache'] = True
            index += 1
            continue
        return {'error': f'unknown option: {arg}'}
    if str(result.get('profile') or '') not in {'safe', 'rich'}:
        return {'error': 'profile must be safe or rich'}
    return result


def _print_status(status: dict[str, object], stdout: TextIO) -> None:
    print(f"workbench_status: {status.get('status')}", file=stdout)
    for key in (
        'reason',
        'profile',
        'enabled',
        'installed',
        'installed_at',
        'updated_at',
        'enabled_at',
        'disabled_at',
        'close_status',
        'closed_processes',
        'binary_install_status',
        'binary_install_tool',
        'binary_install_packages',
        'binary_install_version',
        'binary_install_asset',
        'binary_install_missing',
        'binary_install_reason',
        'dependency_install_status',
        'dependency_install_tool',
        'dependency_install_packages',
        'dependency_install_installed_packages',
        'dependency_install_failed_packages',
        'dependency_install_missing',
        'dependency_install_reason',
        'rich_update_status',
        'launch_status',
        'launch_pid',
        'launch_command',
        'config_status',
        'terminal_status',
        'terminal_terminal_program',
        'terminal_image_protocol',
        'terminal_reason',
        'wezterm_status',
        'wezterm_tool',
        'wezterm_tools',
        'wezterm_reason',
        'wayland_cursor_status',
        'wayland_cursor_tool',
        'wayland_cursor_reason',
        'yazi_status',
        'yazi_tools',
        'yazi_reason',
        'ya_status',
        'ya_tools',
        'markdown_status',
        'markdown_tool',
        'markdown_reason',
        'image_preview_status',
        'image_preview_tools',
        'image_preview_reason',
        'pdf_text_status',
        'pdf_text_tools',
        'pdf_image_status',
        'pdf_image_tools',
        'pdf_image_reason',
        'video_metadata_status',
        'video_metadata_tools',
        'video_thumbnail_status',
        'video_thumbnail_tools',
        'root',
        'manifest',
        'bin_dir',
        'bin_link_dir',
        'yazi_safe_config',
        'yazi_rich_config',
        'wezterm_config',
        'wezterm_xcursor_root',
        'state_root',
        'cache_root',
    ):
        value = status.get(key)
        if value not in (None, '', [], {}):
            print(f'{key}: {value}', file=stdout)
    reasons = status.get('degraded_reasons')
    if isinstance(reasons, list) and reasons:
        print('degraded_reasons: ' + ' | '.join(str(reason) for reason in reasons), file=stdout)
    commands = status.get('launch_commands')
    if isinstance(commands, list) and commands:
        for command in commands:
            print(f'launch_command: {command}', file=stdout)


def _print_help(stdout: TextIO) -> None:
    print('usage: ccb tools <doctor|install|update|enable|disable|launch|uninstall> workbench [--profile safe|rich]', file=stdout)


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _shell_double_quote(value: str) -> str:
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$').replace('`', '\\`') + '"'


def _lua_string(value: str) -> str:
    return value.replace('\\', '\\\\').replace('"', '\\"')


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


__all__ = [
    'cmd_tools',
    'cmd_rich',
    'cleanup_legacy_neovim_tool',
    'disable_workbench',
    'enable_workbench',
    'install_bundled_rich_binaries',
    'install_rich_dependencies',
    'launch_workbench',
    'launch_rich_ccb',
    'print_workbench_status',
    'provision_workbench',
    'rich_auto_start_allowed',
    'rich_workbench_enabled',
    'uninstall_workbench',
    'update_rich_workbench',
    'workbench_status',
]
