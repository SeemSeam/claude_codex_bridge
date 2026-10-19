"""Read-only checks of the actual native terminal host and its ConPTY pair."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import sys

VERIFIED_PAIR = (1, 24, 2607, 10001)

_PROBE = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$terminal = $null
$cursor = $PID
$seen = @{}
while ($cursor -gt 0 -and -not $seen.ContainsKey($cursor)) {
  $seen[$cursor] = $true
  $process = Get-CimInstance Win32_Process -Filter "ProcessId=$cursor"
  if (-not $process) { break }
  if ($process.Name -in @('wezterm-gui.exe', 'wezterm.exe')) {
    $terminal = $process.ExecutablePath
    break
  }
  $cursor = [int]$process.ParentProcessId
}
$source = 'ancestor'
if ($env:CCB_CHECK_TERMINAL) { $terminal = $env:CCB_CHECK_TERMINAL; $source = 'explicit' }
if (-not $terminal -and $env:WEZTERM_EXECUTABLE) { $terminal = $env:WEZTERM_EXECUTABLE; $source = 'environment' }
if (-not $terminal) { @{ source = 'unknown'; components = @() } | ConvertTo-Json -Compress; exit 0 }
$root = Split-Path -Parent $terminal
$components = @()
foreach ($name in @('conpty.dll', 'OpenConsole.exe', 'x64\OpenConsole.exe')) {
  $path = Join-Path $root $name
  if (Test-Path -LiteralPath $path -PathType Leaf) {
    $file = Get-Item -LiteralPath $path
    $v = $file.VersionInfo
    $signature = Get-AuthenticodeSignature -LiteralPath $path
    $components += @{
      name = $name
      version = @($v.FileMajorPart, $v.FileMinorPart, $v.FileBuildPart, $v.FilePrivatePart)
      microsoft_signed = ($signature.Status -eq 'Valid' -and $signature.SignerCertificate.Subject -match 'O=Microsoft Corporation')
    }
  }
}
@{ terminal = $terminal; exists = (Test-Path -LiteralPath $terminal -PathType Leaf); source = $source; components = $components } | ConvertTo-Json -Depth 5 -Compress
'''


def classify(evidence: dict) -> dict:
    result = dict(evidence, status='unknown', message='Terminal host could not be identified; input compatibility is unverified.')
    if not evidence.get('terminal'):
        return result
    components = {c['name']: c for c in evidence.get('components', [])}
    dll = components.get('conpty.dll')
    hosts = [components[n] for n in ('OpenConsole.exe', 'x64\\OpenConsole.exe') if n in components]
    if not evidence.get('exists'):
        return dict(result, status='missing', message='The selected WezTerm executable does not exist.')
    if not dll or not hosts:
        return dict(result, message='Bundled ConPTY pair is missing; system-host compatibility is unverified.')
    version = tuple(dll['version'])
    if any(tuple(h['version']) != version for h in hosts):
        return dict(result, status='incompatible', message='ConPTY and OpenConsole versions do not match.')
    if version < VERIFIED_PAIR:
        return dict(result, status='known-risk', message='Older ConPTY pair: control-report text leakage was observed with the 2024 WezTerm bundle.')
    if not all(c['microsoft_signed'] for c in (dll, *hosts)):
        return dict(result, status='unverified-signature', message='The ConPTY pair could not be verified as Microsoft-signed.')
    if version == VERIFIED_PAIR:
        return dict(result, status='verified-pair', message='Matching signed ConPTY 1.24.2607.10001 pair; local input regression passed for this pair. IME still needs separate validation.')
    return dict(result, status='newer-pair', message='Matching signed newer ConPTY pair; this version has not been qualified by the local input regression.')


def inspect_terminal(wezterm: Path | None = None) -> dict:
    if os.name != 'nt':
        return dict(status='unknown', message='Native Windows terminal inspection requires Windows.')
    env = dict(os.environ)
    # PowerShell 7's module path breaks Windows PowerShell 5.1 security types.
    # Let the child construct its own default module search path.
    for key in tuple(env):
        if key.upper() == 'PSMODULEPATH':
            env.pop(key)
    env.pop('CCB_CHECK_TERMINAL', None)
    if wezterm:
        env['CCB_CHECK_TERMINAL'] = str(wezterm.resolve())
    encoded = base64.b64encode(_PROBE.encode('utf-16-le')).decode('ascii')
    try:
        run = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', encoded],
                             env=env, capture_output=True, encoding='utf-8', errors='replace',
                             timeout=25, creationflags=subprocess.CREATE_NO_WINDOW, check=True)
        return classify(json.loads(run.stdout.lstrip('\ufeff')))
    except (OSError, ValueError, subprocess.SubprocessError):
        return dict(status='unknown', message='Terminal inspection failed or timed out; run windows-terminal-check explicitly.')


def check_before_attach(stderr) -> None:
    report = inspect_terminal()
    print('Windows terminal: ' + report['message'], file=stderr)
    if report['status'] in {'known-risk', 'incompatible', 'unverified-signature'}:
        raise RuntimeError('Terminal compatibility check blocked attachment. Use platforms/windows/tools/prepare_wezterm.ps1 to prepare an isolated signed pair, then reopen with that terminal.')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wezterm', type=Path, help='Inspect this executable instead of the current terminal ancestor')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    report = inspect_terminal(args.wezterm)
    print(json.dumps(report, ensure_ascii=False) if args.json else report['message'])
    return 0 if report['status'] in {'verified-pair', 'newer-pair'} else 1
