param(
  [Parameter(Position = 0)]
  [ValidateSet("install", "uninstall", "help")]
  [string]$Command = "help",
  [string]$InstallPrefix = "$env:LOCALAPPDATA\codex-dual",
  [string]$SourceArchive = "",
  [string]$ExpectedSha256 = "",
  [switch]$Yes
)

# --- UTF-8 / BOM compatibility (Windows PowerShell 5.1) ---
# Keep this near the top so Chinese/emoji output is rendered correctly.
try {
  $script:utf8NoBom = [System.Text.UTF8Encoding]::new($false)
} catch {
  $script:utf8NoBom = [System.Text.Encoding]::UTF8
}
try { $OutputEncoding = $script:utf8NoBom } catch {}
try { [Console]::OutputEncoding = $script:utf8NoBom } catch {}
try { [Console]::InputEncoding = $script:utf8NoBom } catch {}
try { chcp 65001 | Out-Null } catch {}

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot 'config_io.ps1')
$script:extractedReleaseRoot = $null
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path

function Resolve-WindowsReleaseRoot {
  param(
    [string]$ArchivePath,
    [string]$ExpectedDigest
  )
  if ([string]::IsNullOrWhiteSpace($ArchivePath)) {
    return $repoRoot
  }
  $resolvedArchive = (Resolve-Path -LiteralPath $ArchivePath -ErrorAction Stop).Path
  if ([System.IO.Path]::GetExtension($resolvedArchive).ToLowerInvariant() -ne ".zip") {
    throw "Windows release archive must be a .zip file: $resolvedArchive"
  }
  if (-not [string]::IsNullOrWhiteSpace($ExpectedDigest)) {
    $actualDigest = (Get-FileHash -LiteralPath $resolvedArchive -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualDigest -ne $ExpectedDigest.Trim().ToLowerInvariant()) {
      throw "Windows release SHA256 mismatch: expected $ExpectedDigest, got $actualDigest"
    }
  }
  $extractRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("ccb-windows-release-" + [Guid]::NewGuid().ToString("N"))
  New-Item -ItemType Directory -Path $extractRoot -Force | Out-Null
  Expand-Archive -LiteralPath $resolvedArchive -DestinationPath $extractRoot -Force
  $candidates = @(Get-ChildItem -LiteralPath $extractRoot -Directory | Where-Object {
    (Test-Path -LiteralPath (Join-Path $_.FullName "WINDOWS_MANIFEST.json")) -and
    (Test-Path -LiteralPath (Join-Path $_.FullName "bin\ccb.exe")) -and
    (Test-Path -LiteralPath (Join-Path $_.FullName "VERSION"))
  })
  if ($candidates.Count -ne 1) {
    Remove-Item -LiteralPath $extractRoot -Recurse -Force -ErrorAction SilentlyContinue
    throw "Windows release archive must contain exactly one validated payload root"
  }
  $script:extractedReleaseRoot = $extractRoot
  return $candidates[0].FullName
}

$repoRoot = Resolve-WindowsReleaseRoot -ArchivePath $SourceArchive -ExpectedDigest $ExpectedSha256

function Get-WindowsX64ReleaseSurfaceProjection {
  param([string]$Root = $repoRoot)
  $projectionPath = Join-Path $Root "lib\platforms\windows\release\projection.json"
  if (-not (Test-Path $projectionPath)) {
    return $null
  }
  return Get-Content $projectionPath -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Test-WindowsX64ReleaseHostGate {
  param(
    [Parameter(Mandatory = $true)]$Projection,
    [hashtable]$HostEvidence = @{}
  )

  $gate = $Projection.host_gate
  if (-not $gate -or -not $gate.rules -or -not $gate.default_failure_reason -or -not $gate.default_next_action) {
    return @{
      allowed = $false
      failure_reason = "projection-schema-invalid"
      diagnostic = "Windows x64 release-surface host_gate is invalid."
      next_action = "Regenerate the Windows x64 release-surface projection."
    }
  }

  foreach ($rule in @($gate.rules)) {
    $propertyNames = @($rule.PSObject.Properties.Name)
    $validShape = (
      ($propertyNames -contains "field") -and
      ($propertyNames -contains "op") -and
      ($propertyNames -contains "failure_reason") -and
      ($propertyNames -contains "diagnostic") -and
      ($propertyNames -contains "next_action")
    )
    if (-not $validShape) {
      return @{
        allowed = $false
        failure_reason = $gate.default_failure_reason
        diagnostic = "Windows x64 release-surface host_gate rule is invalid."
        next_action = $gate.default_next_action
      }
    }
    if ($rule.op -in @("equals", "not_equals") -and -not ($propertyNames -contains "value")) {
      return @{
        allowed = $false
        failure_reason = $gate.default_failure_reason
        diagnostic = "Windows x64 release-surface host_gate rule is invalid."
        next_action = $gate.default_next_action
      }
    }
    if ($rule.op -eq "in" -and -not ($rule.value -is [array])) {
      return @{
        allowed = $false
        failure_reason = $gate.default_failure_reason
        diagnostic = "Windows x64 release-surface host_gate rule is invalid."
        next_action = $gate.default_next_action
      }
    }
    $value = $HostEvidence[$rule.field]
    $ok = $false
    switch ($rule.op) {
      "exists" {
        $ok = Test-WindowsX64ReleaseHostGateValuePresent $value
      }
      "is_false" {
        $ok = ($value -is [bool] -and $value -eq $false)
      }
      "in" {
        $allowed = @($rule.value | ForEach-Object { Convert-WindowsX64ReleaseHostGateValue $_ })
        $ok = $allowed -contains (Convert-WindowsX64ReleaseHostGateValue $value)
      }
      "equals" {
        $ok = (Convert-WindowsX64ReleaseHostGateValue $value) -eq (Convert-WindowsX64ReleaseHostGateValue $rule.value)
      }
      "not_equals" {
        $ok = (Convert-WindowsX64ReleaseHostGateValue $value) -ne (Convert-WindowsX64ReleaseHostGateValue $rule.value)
      }
      default {
        return @{
          allowed = $false
          failure_reason = $gate.default_failure_reason
          diagnostic = "Windows x64 release-surface host_gate rule is invalid."
          next_action = $gate.default_next_action
        }
      }
    }
    if (-not $ok) {
      return @{
        allowed = $false
        failure_reason = $rule.failure_reason
        diagnostic = $rule.diagnostic
        next_action = $rule.next_action
      }
    }
  }

  return @{
    allowed = $true
    failure_reason = $null
    diagnostic = $Projection.diagnostic
    next_action = $Projection.next_action
  }
}

function Get-WindowsX64ReleaseHostEvidence {
  $cpuArch = $env:PROCESSOR_ARCHITECTURE
  if ($cpuArch -eq "AMD64") { $cpuArch = "x64" }
  elseif ($cpuArch -eq "ARM64") { $cpuArch = "arm64" }
  elseif ($cpuArch -eq "x86") { $cpuArch = "ia32" }
  elseif ([string]::IsNullOrWhiteSpace($cpuArch)) { $cpuArch = "unknown" }
  else { $cpuArch = $cpuArch.ToLowerInvariant() }

  return @{
    os_platform = "win32"
    cpu_arch = $cpuArch
    process_arch = $cpuArch
    wow64 = -not [string]::IsNullOrWhiteSpace($env:PROCESSOR_ARCHITEW6432)
    installer_entrypoint = "install_ps1"
  }
}

function Show-WindowsX64ReleaseSurfaceProjection {
  try {
    $projection = Get-WindowsX64ReleaseSurfaceProjection
    if ($null -eq $projection) {
      Write-Host "Windows x64 release surface: projection=missing"
      Write-Host "Windows x64 release surface diagnostic: projection file is missing or unreadable."
      Write-Host "Windows x64 release surface next_action: Regenerate the Windows x64 release-surface projection."
      return
    }
    $gate = Test-WindowsX64ReleaseHostGate -Projection $projection -HostEvidence (Get-WindowsX64ReleaseHostEvidence)
    $effectiveState = if ($gate.allowed) { $projection.surface_state } else { "blocked" }
    Write-Host "Windows x64 release surface: state=$effectiveState release_install_entry=$($projection.release_install_entry) source_install_allowed=$($projection.source_install_allowed) source_install_entry=$($projection.source_install_entry)"
    if (-not $gate.allowed) {
      Write-Host "Windows x64 release surface diagnostic: $($gate.diagnostic)"
      Write-Host "Windows x64 release surface next_action: $($gate.next_action)"
      return
    }
    if ($projection.diagnostic) {
      Write-Host "Windows x64 release surface diagnostic: $($projection.diagnostic)"
    }
    if ($projection.next_action) {
      Write-Host "Windows x64 release surface next_action: $($projection.next_action)"
    }
  } catch {
    Write-Host "WARN: Windows x64 release surface projection unavailable: $_"
  }
}

function Test-WindowsX64ReleaseHostGateValuePresent {
  param($Value)
  if ($null -eq $Value) { return $false }
  if ($Value -is [string]) { return -not [string]::IsNullOrWhiteSpace($Value) }
  return $true
}

function Convert-WindowsX64ReleaseHostGateValue {
  param($Value)
  if ($Value -is [string]) { return $Value.Trim().ToLowerInvariant() }
  return $Value
}

# Constants
$script:CCB_START_MARKER = "<!-- CCB_CONFIG_START -->"
$script:CCB_END_MARKER = "<!-- CCB_CONFIG_END -->"
$script:CCB_ROLES_START_MARKER = "<!-- CCB_ROLES_START -->"
$script:CCB_ROLES_END_MARKER = "<!-- CCB_ROLES_END -->"
$script:CCB_RUBRICS_START_MARKER = "<!-- REVIEW_RUBRICS_START -->"
$script:CCB_RUBRICS_END_MARKER = "<!-- REVIEW_RUBRICS_END -->"

$script:SCRIPTS_TO_LINK = @(
  "ccb",
  "ask", "autonew", "ctx-transfer"
)

$script:CLAUDE_MARKDOWN = @(
  # Old CCB command markdown removed; managed CCB workflows install as skills.
)

$script:LEGACY_SCRIPTS = @(
  "cast", "cast-w", "codex-ask", "codex-pending", "codex-ping",
  "claude-codex-dual", "claude_codex", "claude_ai", "claude_bridge"
)

# i18n support
function Get-CCBLang {
  $lang = $env:CCB_LANG
  if ($lang -in @("zh", "cn", "chinese")) { return "zh" }
  if ($lang -in @("en", "english")) { return "en" }
  # Auto-detect from system
  try {
    $culture = (Get-Culture).Name
    if ($culture -like "zh*") { return "zh" }
  } catch {}
  return "en"
}

$script:CCBLang = Get-CCBLang

function Get-Msg {
  param([string]$Key, [string]$Arg1 = "", [string]$Arg2 = "")
  $msgs = @{
    "install_complete" = @{ en = "Installation complete"; zh = "安装完成" }
    "uninstall_complete" = @{ en = "Uninstall complete"; zh = "卸载完成" }
    "python_old" = @{ en = "Python version too old: $Arg1"; zh = "Python 版本过旧: $Arg1" }
    "requires_python" = @{ en = "ccb requires Python 3.10+"; zh = "ccb 需要 Python 3.10+" }
    "confirm_windows" = @{ en = "Continue installation in Windows? (y/N)"; zh = "确认继续在 Windows 中安装？(y/N)" }
    "cancelled" = @{ en = "Installation cancelled"; zh = "安装已取消" }
    "windows_warning" = @{ en = "You are installing ccb in native Windows environment"; zh = "你正在 Windows 原生环境安装 ccb" }
    "same_env" = @{ en = "ccb/ask/ping/pend must run in the same environment as codex/gemini."; zh = "ccb/ask/ping/pend 必须与 codex/gemini 在同一环境运行。" }
    "herdr_required" = @{ en = "Native Windows CCB requires Herdr >= v0.8.0 as terminal backend"; zh = "Windows 原生 CCB 需要 Herdr >= v0.8.0 作为终端后端" }
    "herdr_not_found" = @{ en = "Herdr not found. Please install Herdr first."; zh = "未找到 Herdr，请先安装 Herdr。" }
    "herdr_version_old" = @{ en = "Herdr version too old: $Arg1. Minimum required: >= v0.8.0 stable or preview >= 2026-08-04-d78e3d3b5126"; zh = "Herdr 版本过旧: $Arg1。最低要求: >= v0.8.0 稳定版或预览版 >= 2026-08-04-d78e3d3b5126" }
    "herdr_version_ok" = @{ en = "Herdr $Arg1 is ready"; zh = "Herdr $Arg1 已就绪" }
    "herdr_version_unknown" = @{ en = "Herdr found at $Arg1 but version cannot be determined"; zh = "Herdr 已找到 ($Arg1)，但无法确定版本" }
    "herdr_download" = @{ en = "Download: https://herdr.dev/ or https://github.com/herdrdev/herdr"; zh = "下载: https://herdr.dev/ 或 https://github.com/herdrdev/herdr" }
    "herdr_setup_steps" = @{ en = "Setup steps:`n  1. Download Herdr from https://herdr.dev/`n  2. Install to a known location (e.g. %LOCALAPPDATA%\Programs\Herdr)`n  3. Ensure herdr.exe is in PATH`n  4. Verify: herdr --version"; zh = "配置步骤:`n  1. 从 https://herdr.dev/ 下载 Herdr`n  2. 安装到已知位置 (如 %LOCALAPPDATA%\Programs\Herdr)`n  3. 确保 herdr.exe 在 PATH 中`n  4. 验证: herdr --version" }
  }
  if ($msgs.ContainsKey($Key)) {
    return $msgs[$Key][$script:CCBLang]
  }
  return $Key
}

function Show-Usage {
  Write-Host "Usage:"
  Write-Host "  .\install.ps1 install    # Install or update"
  Write-Host "  .\install.ps1 uninstall  # Uninstall"
  Write-Host ""
  Write-Host "Options:"
  Write-Host "  -InstallPrefix <path>    # Custom install location (default: $env:LOCALAPPDATA\codex-dual)"
  Write-Host ""
  Write-Host "Requirements:"
  Write-Host "  - Python 3.10+"
}

function Resolve-CodexSourceHome {
  if ($env:CODEX_HOME -and ($env:CODEX_HOME -notmatch "[/\\]\.ccb[/\\]agents[/\\][^/\\]+[/\\]provider-state[/\\]codex[/\\]home$")) {
    return $env:CODEX_HOME
  }
  return (Join-Path $env:USERPROFILE ".codex")
}

function Test-IsWindowsStoreAliasPath {
  param([string]$PathText)
  if ([string]::IsNullOrWhiteSpace($PathText)) {
    return $false
  }
  $normalized = $PathText.Trim().Trim('"').ToLowerInvariant()
  return (
    $normalized -like "*\microsoft\windowsapps\python.exe" -or
    $normalized -like "*\microsoft\windowsapps\python3.exe"
  )
}

function Get-PythonVersionInfo {
  param([string]$PythonCmd)

  if ([string]::IsNullOrWhiteSpace($PythonCmd)) {
    throw "Python command is empty"
  }
  # Handle commands with arguments (e.g., "py -3")
  $cmdParts = $PythonCmd -split ' ', 2
  $fileName = $cmdParts[0]
  $baseArgs = if ($cmdParts.Length -gt 1) { $cmdParts[1] } else { "" }

  # Use ProcessStartInfo for reliable execution across different Python installations
  # (e.g., Miniconda, custom paths). The & operator can fail in some environments.
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $fileName
  try {
    # Combine base arguments with Python code arguments
    if ($baseArgs) {
      $psi.Arguments = "$baseArgs -c `"import sys; v=sys.version_info; print(f'{v.major}.{v.minor}.{v.micro}|{v.major}|{v.minor}|{sys.executable}')`""
    } else {
      $psi.Arguments = "-c `"import sys; v=sys.version_info; print(f'{v.major}.{v.minor}.{v.micro}|{v.major}|{v.minor}|{sys.executable}')`""
    }
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $psi
    $process.Start() | Out-Null
    $process.WaitForExit()

    $vinfo = $process.StandardOutput.ReadToEnd().Trim()
    if ($process.ExitCode -ne 0 -or [string]::IsNullOrEmpty($vinfo)) {
      $stderr = $process.StandardError.ReadToEnd().Trim()
      if ([string]::IsNullOrWhiteSpace($stderr)) {
        $stderr = "exit code $($process.ExitCode)"
      }
      throw $stderr
    }

    $vparts = $vinfo -split "\|", 4
    if ($vparts.Length -lt 4) {
      throw "Unexpected version output: $vinfo"
    }

    $version = $vparts[0]
    $major = [int]$vparts[1]
    $minor = [int]$vparts[2]
    return @{
      Command = $PythonCmd
      Version = $version
      Major = $major
      Minor = $minor
      Executable = $vparts[3]
    }
  } catch {
    $errorText = $_.Exception.Message
    if ([string]::IsNullOrWhiteSpace($errorText)) {
      $errorText = [string]$_
    }
    throw "Failed to query Python version using '$PythonCmd': $errorText"
  }
}

function Get-PythonCandidates {
  $candidates = New-Object System.Collections.Generic.List[string]

  function Add-Candidate {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return }
    $trimmed = $Value.Trim()
    if (Test-IsWindowsStoreAliasPath $trimmed) { return }
    if ($candidates -notcontains $trimmed) {
      $candidates.Add($trimmed)
    }
  }

  Add-Candidate $env:CCB_PYTHON_CMD
  Add-Candidate "py -3"
  Add-Candidate "python"
  Add-Candidate "python3"

  try {
    $wherePython = & where.exe python 2>$null
    foreach ($item in @($wherePython)) {
      $candidatePath = [string]$item
      if ([string]::IsNullOrWhiteSpace($candidatePath)) { continue }
      Add-Candidate $candidatePath.Trim()
    }
  } catch {}

  $globPatterns = @(
    "$env:LOCALAPPDATA\Programs\Python\Python*\python.exe",
    "$env:ProgramFiles\Python*\python.exe",
    "$env:ProgramFiles\Python\Python*\python.exe",
    "$env:ProgramFiles(x86)\Python*\python.exe",
    "$env:ProgramFiles(x86)\Python\Python*\python.exe"
  )
  foreach ($pattern in $globPatterns) {
    try {
      Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue | ForEach-Object {
        Add-Candidate $_.FullName
      }
    } catch {}
  }

  return @($candidates)
}

function Find-Python {
  foreach ($candidate in Get-PythonCandidates) {
    try {
      $info = Get-PythonVersionInfo -PythonCmd $candidate
      if (
        $info.Major -eq 3 -and
        $info.Minor -ge 10 -and
        -not (Test-IsWindowsStoreAliasPath $info.Executable)
      ) {
        return $candidate
      }
    } catch {}
  }
  return $null
}

function Require-Python310 {
  param([string]$PythonCmd)

  try {
    $info = Get-PythonVersionInfo -PythonCmd $PythonCmd
  } catch {
    Write-Host "[ERROR] $($_.Exception.Message)"
    exit 1
  }

  if (($info.Major -ne 3) -or ($info.Minor -lt 10)) {
    Write-Host "[ERROR] Python version too old: $($info.Version)"
    Write-Host "   ccb requires Python 3.10+"
    Write-Host "   Download: https://www.python.org/downloads/"
    exit 1
  }
  Write-Host "[OK] Python $($info.Version) ($($info.Executable))"
}

function Test-PythonTomlReader {
  param([string]$PythonCmd)

  try {
    $cmdParts = $PythonCmd -split ' ', 2
    $fileName = $cmdParts[0]
    $baseArgs = if ($cmdParts.Length -gt 1) { $cmdParts[1] } else { "" }
    $code = "import importlib.util, sys; sys.exit(0 if any(importlib.util.find_spec(m) for m in ('tomllib','tomli','toml')) else 1)"

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $fileName
    if ($baseArgs) {
      $psi.Arguments = "$baseArgs -c `"$code`""
    } else {
      $psi.Arguments = "-c `"$code`""
    }
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $psi
    $process.Start() | Out-Null
    $process.WaitForExit()
    return ($process.ExitCode -eq 0)
  } catch {
    return $false
  }
}

function Install-Tomli {
  param([string]$PythonCmd)

  if ($env:CCB_INSTALL_TOMLI -eq "0") {
    Write-Host "INFO: tomli auto-install skipped by CCB_INSTALL_TOMLI=0"
    return
  }
  if (Test-PythonTomlReader -PythonCmd $PythonCmd) {
    Write-Host "[OK] TOML parser available"
    return
  }

  Write-Host "Installing Python dependency: tomli"
  $cmdParts = $PythonCmd -split ' ', 2
  $fileName = $cmdParts[0]
  $baseArgs = if ($cmdParts.Length -gt 1) { $cmdParts[1] } else { "" }
  $argVariants = @()
  if ($baseArgs) {
    $argVariants += "$baseArgs -m pip install --user tomli>=2.0.0"
    $argVariants += "$baseArgs -m pip install --user --break-system-packages tomli>=2.0.0"
  } else {
    $argVariants += "-m pip install --user tomli>=2.0.0"
    $argVariants += "-m pip install --user --break-system-packages tomli>=2.0.0"
  }

  $lastError = ""
  foreach ($args in $argVariants) {
    try {
      $psi = New-Object System.Diagnostics.ProcessStartInfo
      $psi.FileName = $fileName
      $psi.Arguments = $args
      $psi.RedirectStandardOutput = $true
      $psi.RedirectStandardError = $true
      $psi.UseShellExecute = $false
      $psi.CreateNoWindow = $true

      $process = New-Object System.Diagnostics.Process
      $process.StartInfo = $psi
      $process.Start() | Out-Null
      $stdout = $process.StandardOutput.ReadToEnd()
      $stderr = $process.StandardError.ReadToEnd()
      $process.WaitForExit()
      if ($process.ExitCode -eq 0 -and (Test-PythonTomlReader -PythonCmd $PythonCmd)) {
        Write-Host "[OK] TOML parser available"
        return
      }
      $lastError = $stderr.Trim()
      if ([string]::IsNullOrWhiteSpace($lastError)) {
        $lastError = $stdout.Trim()
      }
    } catch {
      $lastError = $_.Exception.Message
    }
  }

  Write-Host "WARN: tomli install failed; rich TOML config requires Python 3.11+ or tomli/toml"
  if (-not [string]::IsNullOrWhiteSpace($lastError)) {
    Write-Host "   Last failure: $lastError"
  }
  Write-Host "   Manual install:"
  Write-Host "   $PythonCmd -m pip install --user tomli>=2.0.0"
}

function Install-ManagedPythonRuntime {
  param(
    [Parameter(Mandatory = $true)][string]$BasePython,
    [Parameter(Mandatory = $true)][string]$InstallPrefix,
    [Parameter(Mandatory = $true)][string]$SourceRoot
  )

  $requirements = Join-Path $SourceRoot "platforms\windows\installer\requirements.txt"
  if (-not (Test-Path -LiteralPath $requirements)) {
    throw "Windows managed Python requirements are missing: $requirements"
  }

  $venvDir = Join-Path $InstallPrefix ".venv"
  $venvPython = Join-Path $venvDir "Scripts\python.exe"
  if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "Creating managed Python runtime: $venvDir"
    & $BasePython -m venv $venvDir | Out-Host
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $venvPython)) {
      throw "Failed to create the managed Python runtime at $venvDir"
    }
  } else {
    Write-Host "Reusing managed Python runtime: $venvDir"
  }

  Write-Host "Installing managed Python dependencies..."
  & $venvPython -m pip install --disable-pip-version-check --upgrade --requirement $requirements | Out-Host
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to install Windows managed Python dependencies"
  }
  & $venvPython -c "import aiohttp, cryptography, watchdog; print('managed Python dependencies ready')" | Out-Host
  if ($LASTEXITCODE -ne 0) {
    throw "Windows managed Python dependency smoke check failed"
  }
  return $venvPython
}

# ── OS Platform Detection ─────────────────────────────────────────────────

function Detect-OSPlatform {
  <#
  .SYNOPSIS
    Detect the current OS platform.
  .DESCRIPTION
    Returns one of: NativeWindows, WSL, Linux, macOS, Unknown
  #>
  # Check for WSL environment markers (can be set even on Windows-side processes)
  $isWslEnv = $env:WSL_DISTRO_NAME -or $env:WSL_INTEROP

  # If running on Windows, distinguish WSL-interop vs Native Windows
  if ($isWslEnv) {
    # WSL environment variables are set — likely running under WSL interop
    # or a Windows process launched from WSL
    return "WSL"
  }

  # Check if running on actual Windows
  if ($env:OS -eq "Windows_NT" -or [Environment]::OSVersion.Platform -eq "Win32NT") {
    return "NativeWindows"
  }

  return "Unknown"
}

# ── Herdr Detection & Version Check ───────────────────────────────────────

function Get-HerdrExecutable {
  param([string]$ExplicitPath = "")

  if ($ExplicitPath -and (Test-Path -LiteralPath $ExplicitPath)) {
    return $ExplicitPath
  }

  if ($env:CCB_HERDR_EXE -and (Test-Path -LiteralPath $env:CCB_HERDR_EXE)) {
    return $env:CCB_HERDR_EXE
  }

  # Check PATH
  $pathExe = (Get-Command herdr -ErrorAction SilentlyContinue).Source
  if ($pathExe) {
    return $pathExe
  }

  # Known install locations
  $candidates = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Herdr\herdr.exe"),
    (Join-Path $env:ProgramFiles "Herdr\herdr.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Herdr\herdr.exe"),
    (Join-Path $env:USERPROFILE "AppData\Local\Programs\Herdr\herdr.exe")
  )
  foreach ($candidate in $candidates) {
    if (Test-Path -LiteralPath $candidate) {
      return $candidate
    }
  }

  return $null
}

function Get-HerdrVersion {
  param([string]$ExePath)

  if (-not $ExePath -or -not (Test-Path -LiteralPath $ExePath)) {
    return $null
  }

  try {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $ExePath
    $psi.Arguments = "--version"
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $psi
    [void] $process.Start()
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit(10000) | Out-Null

    $output = if ($stdout) { $stdout } else { $stderr }
    return $output.Trim()
  } catch {
    return $null
  }
}

function Test-HerdrVersionOk {
  <#
  .SYNOPSIS
    Check if Herdr meets the minimum version requirement.
  .DESCRIPTION
    Returns a hashtable with: ok, message, version, exe
  #>
  param([string]$ExePath = "")

  $exe = Get-HerdrExecutable -ExplicitPath $ExePath
  if (-not $exe) {
    return @{
      ok = $false
      message = (Get-Msg "herdr_not_found") + "`n" + (Get-Msg "herdr_download") + "`n" + (Get-Msg "herdr_setup_steps")
      version = $null
      exe = $null
    }
  }

  $rawVersion = Get-HerdrVersion -ExePath $exe
  if (-not $rawVersion) {
    return @{
      ok = $false
      message = (Get-Msg "herdr_version_unknown" -Arg1 $exe)
      version = $null
      exe = $exe
    }
  }

  # Parse version: stable "v0.8.0", preview "0.8.0-preview.YYYY-MM-DD-COMMIT" or "preview YYYY-MM-DD-COMMIT"
  $minStable = @(0, 8, 0)
  $minPreviewDate = "2026-08-04"
  $minPreviewCommitPrefix = "d78e3d3b"

  # Try dotted preview pattern first: "0.8.0-preview.YYYY-MM-DD-COMMIT"
  if ($rawVersion -match 'preview\.(\d{4}-\d{2}-\d{2})-([a-f0-9]{7,})') {
    $previewDate = $Matches[1]
    $previewCommit = $Matches[2]
    if (($previewDate -ge $minPreviewDate) -or ($previewCommit.StartsWith($minPreviewCommitPrefix))) {
      return @{
        ok = $true
        message = (Get-Msg "herdr_version_ok" -Arg1 $rawVersion)
        version = $rawVersion
        exe = $exe
      }
    }
    return @{
      ok = $false
      message = (Get-Msg "herdr_version_old" -Arg1 $rawVersion) + "`n" + (Get-Msg "herdr_download")
      version = $rawVersion
      exe = $exe
    }
  }

  # Try space-separated preview pattern: "preview YYYY-MM-DD-COMMIT"
  if ($rawVersion -match 'preview\s+(\d{4}-\d{2}-\d{2})-([a-f0-9]{7,})') {
    $previewDate = $Matches[1]
    $previewCommit = $Matches[2]
    if (($previewDate -ge $minPreviewDate) -or ($previewCommit.StartsWith($minPreviewCommitPrefix))) {
      return @{
        ok = $true
        message = (Get-Msg "herdr_version_ok" -Arg1 $rawVersion)
        version = $rawVersion
        exe = $exe
      }
    }
    return @{
      ok = $false
      message = (Get-Msg "herdr_version_old" -Arg1 $rawVersion) + "`n" + (Get-Msg "herdr_download")
      version = $rawVersion
      exe = $exe
    }
  }

  # Try stable pattern
  if ($rawVersion -match 'v?(\d+)\.(\d+)\.(\d+)') {
    $major = [int]$Matches[1]
    $minor = [int]$Matches[2]
    $patch = [int]$Matches[3]
    $ver = @($major, $minor, $patch)

    if (($ver[0] -gt $minStable[0]) -or
        ($ver[0] -eq $minStable[0] -and $ver[1] -gt $minStable[1]) -or
        ($ver[0] -eq $minStable[0] -and $ver[1] -eq $minStable[1] -and $ver[2] -ge $minStable[2])) {
      return @{
        ok = $true
        message = (Get-Msg "herdr_version_ok" -Arg1 $rawVersion)
        version = $rawVersion
        exe = $exe
      }
    }
    return @{
      ok = $false
      message = (Get-Msg "herdr_version_old" -Arg1 $rawVersion) + "`n" + (Get-Msg "herdr_download")
      version = $rawVersion
      exe = $exe
    }
  }

  # Unknown format
  return @{
    ok = $false
    message = (Get-Msg "herdr_version_unknown" -Arg1 $exe) + "`n  raw: $rawVersion"
    version = $rawVersion
    exe = $exe
  }
}

function Confirm-BackendEnv {
  if ($Yes -or $env:CCB_INSTALL_ASSUME_YES -eq "1") { return }

  if (-not [Environment]::UserInteractive) {
    Write-Host "[ERROR] Non-interactive environment detected, aborting to prevent Windows/WSL mismatch."
    Write-Host "   If codex/gemini will run in native Windows:"
    Write-Host "   Re-run: powershell -ExecutionPolicy Bypass -File .\install.ps1 install -Yes"
    exit 1
  }

  Write-Host ""
  Write-Host "================================================================"
  Write-Host "[WARNING] You are installing ccb in native Windows environment"
  Write-Host "================================================================"
  Write-Host "ccb/ask/ping/pend must run in the same environment as codex/gemini."
  Write-Host ""
  Write-Host "Please confirm: You will install and run codex/gemini in native Windows (not WSL)."
  Write-Host "If you plan to run codex/gemini in WSL, exit and run in WSL:"
  Write-Host "   ./install.sh install"
  Write-Host "================================================================"
  $reply = Read-Host "Continue installation in Windows? (y/N)"
  if ($reply.Trim().ToLower() -notin @("y", "yes")) {
    Write-Host "Installation cancelled"
    exit 1
  }
}

function Confirm-HerdrReady {
  <#
  .SYNOPSIS
    Check Herdr availability and version, prompt user if not ready.
  #>
  $platform = Detect-OSPlatform
  if ($platform -ne "NativeWindows") {
    return
  }

  Write-Host ""
  Write-Host (Get-Msg "herdr_required")
  Write-Host "  最低要求: >= v0.8.0 (稳定版) 或 preview >= 2026-08-04-d78e3d3b5126"
  Write-Host ""

  $herdr = Test-HerdrVersionOk
  if ($herdr.ok) {
    Write-Host "[OK] $($herdr.message)"
    Write-Host "  位置: $($herdr.exe)"
    return
  }

  Write-Host "[WARN] $($herdr.message)"
  if ($herdr.exe) {
    Write-Host "  找到位置: $($herdr.exe)"
  }
  Write-Host ""

  # -Yes / CCB_INSTALL_ASSUME_YES acknowledges the same guarded continuation
  # offered by the prompt below. This is required for archive installation in
  # CI, where only the safe --help/--version launcher surface is exercised and
  # Herdr is intentionally not provisioned.
  if ($Yes -or $env:CCB_INSTALL_ASSUME_YES -eq "1") {
    Write-Host "[INFO] Herdr requirement acknowledged by non-interactive install; continuing."
    return
  }

  # In non-interactive mode, just warn
  if (-not [Environment]::UserInteractive) {
    Write-Host "[INFO] 非交互模式: 请先手动安装/升级 Herdr 后重试。"
    Write-Host "[INFO] 设置 CCB_SKIP_HERDR_CHECK=1 可跳过此检查（仅限诊断）"
    return
  }

  Write-Host "Herdr 未满足最低版本要求。是否继续安装 CCB？"
  Write-Host "  注意: 没有 Herdr 时 CCB 启动将失败（除非使用 --help/--version）。"
  $reply = [string](Read-Host "继续安装? (y/N)")
  if ($reply.Trim().ToLower() -notin @("y", "yes")) {
    Write-Host "安装已取消"
    Write-Host ""
    Write-Host (Get-Msg "herdr_setup_steps")
    exit 1
  }
  Write-Host "[INFO] 继续安装 CCB，但启动时可能需要先配置 Herdr。"
}

function Install-Native {
  # Reject malformed user configuration before changing the installation.
  $null = Read-CCBSettings -Path (Join-Path $env:USERPROFILE '.claude\settings.json')
  Confirm-BackendEnv
  if ($SourceArchive -and (Test-Path -LiteralPath (Join-Path $InstallPrefix 'WINDOWS_MANIFEST.json'))) {
    # Archive upgrades use the transactional Windows lane, not in-place copies.
    if (-not $ExpectedSha256) { throw 'Archive upgrades require -ExpectedSha256.' }
    $maintenancePython = Find-Python
    $maintenanceInfo = Get-PythonVersionInfo -PythonCmd $maintenancePython
    & $maintenanceInfo.Executable (Join-Path $PSScriptRoot '..\tools\manage_install.py') update `
      --install-root $InstallPrefix --archive $SourceArchive --sha256 $ExpectedSha256
    if ($LASTEXITCODE -ne 0) { throw 'Windows transactional update failed; see the maintenance diagnostic.' }
    return
  }
  Confirm-HerdrReady

  Show-WindowsX64ReleaseSurfaceProjection

  $binDir = Join-Path $InstallPrefix "bin"
  $pythonCmd = Find-Python

  if (-not $pythonCmd) {
    Write-Host "Python not found. Please install Python and add it to PATH."
    Write-Host "Download: https://www.python.org/downloads/"
    exit 1
  }

  Require-Python310 -PythonCmd $pythonCmd
  Write-Host "Installing ccb to $InstallPrefix ..."
  Write-Host "Using Python: $pythonCmd"
  $pythonInfo = Get-PythonVersionInfo -PythonCmd $pythonCmd
  $pythonExecutable = $pythonInfo.Executable
  if ([string]::IsNullOrWhiteSpace($pythonExecutable)) {
    Write-Host "[ERROR] Failed to resolve a concrete Python executable."
    exit 1
  }

  $cleanInstall = $false
  $cleanEnv = ($env:CCB_CLEAN_INSTALL -as [string])
  if ($cleanEnv -and $cleanEnv.Trim() -notin @("0", "false", "no", "off")) {
    $cleanInstall = $true
  }
  if ($cleanInstall -and (Test-Path $InstallPrefix)) {
    $repoRootResolved = $repoRoot
    $installResolved = $InstallPrefix
    try { $repoRootResolved = (Resolve-Path $repoRoot).Path } catch {}
    try { $installResolved = (Resolve-Path $InstallPrefix).Path } catch {}
    if ($repoRootResolved -ne $installResolved) {
      Remove-Item -Recurse -Force $InstallPrefix
    }
  }

  if (-not (Test-Path $InstallPrefix)) {
    New-Item -ItemType Directory -Path $InstallPrefix -Force | Out-Null
  }
  if (-not (Test-Path $binDir)) {
    New-Item -ItemType Directory -Path $binDir -Force | Out-Null
  }

  # ccb.py is the Python entrypoint that the installed `ccb` bash launcher execs
  # (source layout resolves symlinks then runs ccb.py).  Omitting it produced an
  # installed tree whose `ccb` launcher failed with a Python SyntaxError because
  # the wrapper pointed at the bash script itself.  Keep `ccb` (launcher) and
  # `ccb.py` (entrypoint) together so installed layout matches the launcher.
  $items = @(
    "ccb.py", "lib", "bin", "commands", "mcp", "inherit_skills",
    "platforms\windows",
    "assets", "config", "useful_tools", "VERSION", "BUILD_INFO.json", "WINDOWS_MANIFEST.json"
  )
  foreach ($item in $items) {
    $src = Join-Path $repoRoot $item
    $dst = Join-Path $InstallPrefix $item
    if (Test-Path $src) {
      if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
      New-Item -ItemType Directory -Path (Split-Path -Parent $dst) -Force | Out-Null
      Copy-Item -Recurse -Force $src $dst
    }
  }

  $pythonExecutable = Install-ManagedPythonRuntime `
    -BasePython $pythonExecutable `
    -InstallPrefix $InstallPrefix `
    -SourceRoot $repoRoot

  # Exclude web UI code from installation (CLI-only mail setup)
  $webDir = Join-Path $InstallPrefix "lib\\web"
  if (Test-Path $webDir) { Remove-Item -Recurse -Force $webDir }
  $ccbWeb = Join-Path $InstallPrefix "bin\\ccb-web"
  if (Test-Path $ccbWeb) { Remove-Item -Force $ccbWeb }

  function Fix-PythonShebang {
    param([string]$TargetPath)
    if (-not $TargetPath -or -not (Test-Path $TargetPath)) { return }
    try {
      $text = [System.IO.File]::ReadAllText($TargetPath, [System.Text.Encoding]::UTF8)
      if ($text -match '^\#\!/usr/bin/env python3') {
        $text = $text -replace '^\#\!/usr/bin/env python3', '#!/usr/bin/env python'
        [System.IO.File]::WriteAllText($TargetPath, $text, $script:utf8NoBom)
      }
    } catch {
      return
    }
  }

  $scripts = @(
    "ccb",
    "ask", "autonew", "ctx-transfer"
  )

  # In MSYS/Git-Bash, invoking the script file directly will honor the shebang.
  # Windows typically has `python` but not `python3`, so rewrite shebangs for compatibility.
  foreach ($script in $scripts) {
    if ($script -eq "ccb") {
      # Native and script launchers share the Windows-owned Python entrypoint.
      Fix-PythonShebang (Join-Path $InstallPrefix "platforms\windows\ccb.py")
    } else {
      Fix-PythonShebang (Join-Path $InstallPrefix ("bin\\" + $script))
    }
  }

  foreach ($script in $scripts) {
    $batPath = Join-Path $binDir "$script.bat"
    $cmdPath = Join-Path $binDir "$script.cmd"
    if ($script -eq "ccb") {
      # Match ccb.exe so fallback launchers retain native CLI composition.
      $relPath = "..\platforms\windows\ccb.py"
    } else {
      # Script is installed alongside the wrapper under $InstallPrefix\bin
      $relPath = $script
    }
    $escapedPythonExecutable = $pythonExecutable.Replace('"', '""')
    $wrapperContent = "@echo off`r`nset `"PYTHON=$escapedPythonExecutable`"`r`nif not exist `"%PYTHON%`" set `"PYTHON=python`"`r`nwhere python >NUL 2>&1 || if /I `"%PYTHON%`"==`"python`" set `"PYTHON=py -3`"`r`nstart `"`" /B /WAIT %PYTHON% `"%~dp0$relPath`" %*"
    [System.IO.File]::WriteAllText($batPath, $wrapperContent, $script:utf8NoBom)
    # .cmd wrapper for PowerShell/CMD users (and tools preferring .cmd over raw shebang scripts)
    [System.IO.File]::WriteAllText($cmdPath, $wrapperContent, $script:utf8NoBom)
  }

  $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
  $pathList = if ($userPath) { $userPath -split ";" | Where-Object { $_ } } else { @() }
  $binDirLower = $binDir.ToLower()
  $alreadyInPath = $pathList | Where-Object { $_.ToLower() -eq $binDirLower }
  if (-not $alreadyInPath) {
    $newPath = ($pathList + $binDir) -join ";"
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    Write-Host "Added $binDir to user PATH"
  }

  # Git version injection
  function Get-GitVersionInfo {
    param([string]$RepoRoot)

    $commit = ""
    $date = ""

    # 方法1: 本地 Git
    if (Get-Command git -ErrorAction SilentlyContinue) {
      if (Test-Path (Join-Path $RepoRoot ".git")) {
        try {
          $commit = (git -C $RepoRoot log -1 --format='%h' 2>$null)
          $date = (git -C $RepoRoot log -1 --format='%cs' 2>$null)
        } catch {}
      }
    }

    # 方法2: 环境变量
    if (-not $commit -and $env:CCB_GIT_COMMIT) {
      $commit = $env:CCB_GIT_COMMIT
      $date = $env:CCB_GIT_DATE
    }

    # 方法3: GitHub API
    if (-not $commit) {
      try {
        $api = "https://api.github.com/repos/bfly123/claude_code_bridge/commits/main"
        $response = Invoke-RestMethod -Uri $api -TimeoutSec 5 -ErrorAction Stop
        $commit = $response.sha.Substring(0,7)
        $date = $response.commit.committer.date.Substring(0,10)
      } catch {}
    }

    return @{Commit=$commit; Date=$date}
  }

  # 注入版本信息到 ccb 文件
  $verInfo = Get-GitVersionInfo -RepoRoot $repoRoot
  if ($verInfo.Commit) {
    $ccbPath = Join-Path $InstallPrefix "ccb"
    if (Test-Path $ccbPath) {
      try {
        $content = Get-Content $ccbPath -Raw -Encoding UTF8
        $content = $content -replace 'GIT_COMMIT = ""', "GIT_COMMIT = `"$($verInfo.Commit)`""
        $content = $content -replace 'GIT_DATE = ""', "GIT_DATE = `"$($verInfo.Date)`""
        [System.IO.File]::WriteAllText($ccbPath, $content, [System.Text.UTF8Encoding]::new($false))
        Write-Host "Injected version info: $($verInfo.Commit) $($verInfo.Date)"
      } catch {
        Write-Warning "Failed to inject version info: $_"
      }
    }
  }
  Install-CodexSkills
  Install-ClaudeConfig
  Install-DroidSkills
  Install-DroidDelegation -PythonCmd $pythonCmd -InstallPrefix $InstallPrefix
  Cleanup-LegacyFiles -InstallPrefix $InstallPrefix

  $nativeLauncher = Join-Path $binDir "ccb.exe"
  if (Test-Path -LiteralPath $nativeLauncher) {
    $versionOutput = & $nativeLauncher --print-version 2>&1
    if ($LASTEXITCODE -ne 0) {
      throw "Installed ccb.exe smoke check failed: $versionOutput"
    }
    Write-Host "Verified native launcher: $nativeLauncher ($versionOutput)"
  }

  Write-Host ""
  Write-Host "Installation complete!"
  Write-Host "Restart your terminal for PATH changes to take effect."
  Write-Host ""
  Write-Host "Quick start:"
  Write-Host "  ccb             # Start providers from ccb.config (default: all four)"
  Write-Host "  ccb codex       # Start with Codex backend"
  Write-Host "  ccb gemini      # Start with Gemini backend"
  Write-Host "  ccb opencode    # Start with OpenCode backend"
  Write-Host "  ccb claude      # Start with Claude backend"
}

# Clean up legacy daemon files from the pre-ccbd era
function Cleanup-LegacyFiles {
  param([string]$InstallPrefix)

  Write-Host "Cleaning up legacy files..."
  $cleaned = 0

  # Legacy daemon scripts in bin/
  $legacyDaemons = @("caskd", "gaskd", "oaskd", "laskd", "daskd")
  $binDir = Join-Path $InstallPrefix "bin"

  foreach ($daemon in $legacyDaemons) {
    $daemonPath = Join-Path $binDir $daemon
    if (Test-Path $daemonPath) {
      Remove-Item -Force $daemonPath
      Write-Host "  Removed legacy daemon script: $daemonPath"
      $cleaned++
    }
  }

  # Legacy daemon state files in cache
  $cacheDir = Join-Path $env:LOCALAPPDATA "ccb"
  $legacyStates = @("caskd.json", "gaskd.json", "oaskd.json", "laskd.json", "daskd.json")

  foreach ($state in $legacyStates) {
    $statePath = Join-Path $cacheDir $state
    if (Test-Path $statePath) {
      Remove-Item -Force $statePath
      Write-Host "  Removed legacy state file: $statePath"
      $cleaned++
    }
  }

  # Legacy daemon module files in lib/
  $libDir = Join-Path $InstallPrefix "lib"
  $legacyModules = @("caskd_daemon.py", "gaskd_daemon.py", "oaskd_daemon.py", "laskd_daemon.py", "daskd_daemon.py")

  foreach ($module in $legacyModules) {
    $modulePath = Join-Path $libDir $module
    if (Test-Path $modulePath) {
      Remove-Item -Force $modulePath
      Write-Host "  Removed legacy module: $modulePath"
      $cleaned++
    }
  }

  if ($cleaned -eq 0) {
    Write-Host "  No legacy files found"
  } else {
    Write-Host "  Cleaned up $cleaned legacy file(s)"
  }
}

function Install-CodexSkills {
  $skillsSrc = Join-Path (Join-Path $repoRoot "inherit_skills") "codex_skills"
  $codexHome = Resolve-CodexSourceHome
  $skillsDst = Join-Path $codexHome "skills"

  if (-not (Test-Path $skillsSrc)) {
    return
  }

  if (-not (Test-Path $skillsDst)) {
    New-Item -ItemType Directory -Path $skillsDst -Force | Out-Null
  }

  Remove-Item -Recurse -Force (Join-Path $skillsDst "ccb_config") -ErrorAction SilentlyContinue

  $legacySkills = @("ccb-config", "ping", "pend", "autonew", "all-plan", "file-op")
  foreach ($skill in $legacySkills) {
    Remove-Item -Recurse -Force (Join-Path $skillsDst $skill) -ErrorAction SilentlyContinue
  }

  Write-Host "Installing inherited Codex skills (PowerShell SKILL.md template)..."
  Get-ChildItem -Path $skillsSrc -Directory | Where-Object { $_.Name -ne "reconnect" } | ForEach-Object {
    $skillName = $_.Name
    $srcDir = $_.FullName
    $dstDir = Join-Path $skillsDst $skillName
    $dstSkillMd = Join-Path $dstDir "SKILL.md"

    if (Test-Path $dstDir) {
      Remove-Item -Recurse -Force $dstDir
    }
    New-Item -ItemType Directory -Path $dstDir -Force | Out-Null

    $srcSkillMd = Join-Path $srcDir "SKILL.md.powershell"
    if (-not (Test-Path $srcSkillMd)) {
      $srcSkillMd = Join-Path $srcDir "SKILL.md"
    }
    if (-not (Test-Path $srcSkillMd)) {
      return
    }

    Copy-Item -Force $srcSkillMd $dstSkillMd

    # Copy additional subdirectories (e.g., references/) if they exist
    Get-ChildItem -Path $srcDir -Directory | ForEach-Object {
      $subDirName = $_.Name
      $srcSubDir = $_.FullName
      $dstSubDir = Join-Path $dstDir $subDirName
      if (Test-Path $dstSubDir) {
        Remove-Item -Recurse -Force $dstSubDir
      }
      Copy-Item -Recurse -Force $srcSubDir $dstSubDir
    }

    Write-Host "  Updated Codex skill: $skillName"
  }
  Write-Host "Updated Codex skills directory: $skillsDst"
}

function Install-DroidSkills {
  $skillsSrc = Join-Path (Join-Path $repoRoot "inherit_skills") "droid_skills"
  $factoryHome = if ($env:FACTORY_HOME) { $env:FACTORY_HOME } else { Join-Path $env:USERPROFILE ".factory" }
  $skillsDst = Join-Path $factoryHome "skills"

  if (-not (Test-Path $skillsSrc)) {
    return
  }

  if (-not (Get-Command droid -ErrorAction SilentlyContinue)) {
    return
  }

  if (-not (Test-Path $skillsDst)) {
    New-Item -ItemType Directory -Path $skillsDst -Force | Out-Null
  }

  $legacySkills = @("ping", "pend", "autonew", "all-plan")
  foreach ($skill in $legacySkills) {
    Remove-Item -Recurse -Force (Join-Path $skillsDst $skill) -ErrorAction SilentlyContinue
  }

  Write-Host "Installing Droid/Factory control skills..."
  Get-ChildItem -Path $skillsSrc -Directory | ForEach-Object {
    $skillName = $_.Name
    $srcDir = $_.FullName
    $dstDir = Join-Path $skillsDst $skillName

    $srcSkillMd = Join-Path $srcDir "SKILL.md"
    if (-not (Test-Path $srcSkillMd)) {
      return
    }

    if (-not (Test-Path $dstDir)) {
      New-Item -ItemType Directory -Path $dstDir -Force | Out-Null
    }

    Copy-Item -Force $srcSkillMd (Join-Path $dstDir "SKILL.md")

    # Copy additional subdirectories
    Get-ChildItem -Path $srcDir -Directory | ForEach-Object {
      Copy-Item -Recurse -Force $_.FullName (Join-Path $dstDir $_.Name)
    }

    Write-Host "  Updated Factory skill: $skillName"
  }
  Write-Host "Updated Factory skills directory: $skillsDst"
}

function Install-DroidDelegation {
  param(
    [string]$PythonCmd,
    [string]$InstallPrefix
  )

  if ($env:CCB_DROID_AUTOINSTALL -eq "0") {
    return
  }
  $droidCmd = Get-Command droid -ErrorAction SilentlyContinue
  if (-not $droidCmd) {
    return
  }
  $serverPath = Join-Path $InstallPrefix "mcp\\ccb-delegation\\server.py"
  if (-not (Test-Path $serverPath)) {
    Write-Host "WARN: Droid MCP server not found at $serverPath; skipping"
    return
  }
  if ($env:CCB_DROID_AUTOINSTALL_FORCE -eq "1") {
    try { & $droidCmd.Source "mcp" "remove" "ccb-delegation" | Out-Null } catch {}
  }
  try {
    & $droidCmd.Source "mcp" "add" "ccb-delegation" "--type" "stdio" $PythonCmd $serverPath | Out-Null
    Write-Host "OK: Droid MCP delegation registered"
  } catch {
    Write-Warning "Droid MCP delegation setup failed: $_"
  }
}

function Install-ClaudeConfig {
  $claudeDir = Join-Path $env:USERPROFILE ".claude"
  $commandsDir = Join-Path $claudeDir "commands"
  $settingsJson = Join-Path $claudeDir "settings.json"

  if (-not (Test-Path $claudeDir)) {
    New-Item -ItemType Directory -Path $claudeDir -Force | Out-Null
  }
  if (-not (Test-Path $commandsDir)) {
    New-Item -ItemType Directory -Path $commandsDir -Force | Out-Null
  }

  $srcCommands = Join-Path $repoRoot "commands"
  if (Test-Path $srcCommands) {
    Get-ChildItem -Path $srcCommands -Filter "*.md" | ForEach-Object {
      Copy-Item -Force $_.FullName (Join-Path $commandsDir $_.Name)
    }
  }

  # Install skills
  $skillsDir = Join-Path $claudeDir "skills"
  $srcSkills = Join-Path (Join-Path $repoRoot "inherit_skills") "claude_skills"
  if (Test-Path $srcSkills) {
    if (-not (Test-Path $skillsDir)) {
      New-Item -ItemType Directory -Path $skillsDir -Force | Out-Null
    }

    Remove-Item -Recurse -Force (Join-Path $skillsDir "ccb_config") -ErrorAction SilentlyContinue

    $legacySkills = @("ccb-config", "ping", "pend", "autonew", "all-plan", "docs", "tp", "tr", "file-op", "review", "continue")
    foreach ($skill in $legacySkills) {
      Remove-Item -Recurse -Force (Join-Path $skillsDir $skill) -ErrorAction SilentlyContinue
    }

    Write-Host "Installing inherited Claude skills (PowerShell SKILL.md template)..."
    Get-ChildItem -Path $srcSkills -Directory | ForEach-Object {
      $skillName = $_.Name
      $srcDir = $_.FullName
      $dstDir = Join-Path $skillsDir $skillName
      $dstSkillMd = Join-Path $dstDir "SKILL.md"

      if (Test-Path $dstDir) {
        Remove-Item -Recurse -Force $dstDir
      }
      New-Item -ItemType Directory -Path $dstDir -Force | Out-Null

      $srcSkillMd = Join-Path $srcDir "SKILL.md.powershell"
      if (-not (Test-Path $srcSkillMd)) {
        $srcSkillMd = Join-Path $srcDir "SKILL.md"
      }
      if (-not (Test-Path $srcSkillMd)) {
        return
      }

      Copy-Item -Force $srcSkillMd $dstSkillMd

      # Copy additional subdirectories (e.g., references/) if they exist
      Get-ChildItem -Path $srcDir -Directory | ForEach-Object {
        $subDirName = $_.Name
        $srcSubDir = $_.FullName
        $dstSubDir = Join-Path $dstDir $subDirName
        if (Test-Path $dstSubDir) {
          Remove-Item -Recurse -Force $dstSubDir
        }
        Copy-Item -Recurse -Force $srcSubDir $dstSubDir
      }

      Write-Host "  Updated skill: $skillName"
    }
  }

  Remove-CCBMemoryInjections

  $allowList = @(
    "Bash(ccb ask *)", "Bash(ccb clear *)", "Bash(ccb ping *)", "Bash(ccb pend *)"
  )

  $settings = Read-CCBSettings -Path $settingsJson

  if (-not $settings.permissions) {
    $settings | Add-Member -NotePropertyName "permissions" -NotePropertyValue @{} -Force
  }
  if (-not $settings.permissions.allow) {
    $settings.permissions | Add-Member -NotePropertyName "allow" -NotePropertyValue @() -Force
  }

  $currentAllow = [System.Collections.ArrayList]@($settings.permissions.allow)
  $updated = $false
  foreach ($item in $allowList) {
    if ($currentAllow -notcontains $item) {
      $currentAllow.Add($item) | Out-Null
      $updated = $true
    }
  }

  if ($updated) {
    $settings.permissions.allow = $currentAllow.ToArray()
    Write-CCBSettings -Path $settingsJson -Value $settings
    Write-Host "Updated settings.json with permissions"
  }

}

function Remove-MarkedMemoryBlock {
  param(
    [string]$Path,
    [string]$StartMarker,
    [string]$EndMarker,
    [string]$Label
  )
  if (-not (Test-Path $Path)) { return }
  $content = Get-Content -Raw $Path -Encoding UTF8
  if (-not $content.Contains($StartMarker)) { return }
  $pattern = "(?s)\r?\n?$([regex]::Escape($StartMarker)).*?$([regex]::Escape($EndMarker))\r?\n?"
  $content = [regex]::Replace($content, $pattern, "`n").Trim() + "`n"
  [System.IO.File]::WriteAllText($Path, $content, $script:utf8NoBom)
  Write-Host "Removed CCB memory block from $Label"
}

function Remove-CCBMemoryInjections {
  $claudeMd = Join-Path $env:USERPROFILE ".claude\CLAUDE.md"
  Remove-MarkedMemoryBlock -Path $claudeMd -StartMarker $script:CCB_START_MARKER -EndMarker $script:CCB_END_MARKER -Label "CLAUDE.md"

  $agentsMd = Join-Path $installPrefix "AGENTS.md"
  Remove-MarkedMemoryBlock -Path $agentsMd -StartMarker $script:CCB_ROLES_START_MARKER -EndMarker $script:CCB_ROLES_END_MARKER -Label "AGENTS.md"
  Remove-MarkedMemoryBlock -Path $agentsMd -StartMarker $script:CCB_RUBRICS_START_MARKER -EndMarker $script:CCB_RUBRICS_END_MARKER -Label "AGENTS.md"

  $clinerules = Join-Path $installPrefix ".clinerules"
  Remove-MarkedMemoryBlock -Path $clinerules -StartMarker $script:CCB_ROLES_START_MARKER -EndMarker $script:CCB_ROLES_END_MARKER -Label ".clinerules"
}

function Uninstall-Native {
  $settingsFile = Join-Path $env:USERPROFILE '.claude\settings.json'
  $settings = Read-CCBSettings -Path $settingsFile
  $binDir = Join-Path $InstallPrefix "bin"

  # 1. Remove project directory
  if (Test-Path $InstallPrefix) {
    Remove-Item -Recurse -Force $InstallPrefix
    Write-Host "Removed $InstallPrefix"
  }

  # 2. Remove from user PATH
  $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
  if ($userPath) {
    $pathList = $userPath -split ";" | Where-Object { $_ }
    $binDirLower = $binDir.ToLower()
    $newPathList = $pathList | Where-Object { $_.ToLower() -ne $binDirLower }
    if ($newPathList.Count -ne $pathList.Count) {
      $newPath = $newPathList -join ";"
      [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
      Write-Host "Removed $binDir from user PATH"
    }
  }

  # 3. Remove Claude skills
  $claudeSkillsDir = Join-Path $env:USERPROFILE ".claude\skills"
  $ccbSkills = @("ask", "ccb-config", "ccb-clear", "ccb-compact", "ccb-diagnose", "reconnect")
  $legacySkills = @("ccb_config", "ping", "pend", "autonew", "all-plan", "docs", "tp", "tr", "file-op", "review", "continue")
  if (Test-Path $claudeSkillsDir) {
    Write-Host "Removing CCB Claude skills..."
    foreach ($skill in $legacySkills) {
      Remove-Item -Recurse -Force (Join-Path $claudeSkillsDir $skill) -ErrorAction SilentlyContinue
    }
    foreach ($skill in $ccbSkills) {
      $skillPath = Join-Path $claudeSkillsDir $skill
      if (Test-Path $skillPath) {
        Remove-Item -Recurse -Force $skillPath
        Write-Host "  Removed skill: $skill"
      }
    }
  }

  # 4. Remove CLAUDE.md CCB config block
  $claudeMd = Join-Path $env:USERPROFILE ".claude\CLAUDE.md"
  if (Test-Path $claudeMd) {
    $content = Get-Content $claudeMd -Raw -Encoding UTF8
    if ($content -match $script:CCB_START_MARKER) {
      Write-Host "Removing CCB config from CLAUDE.md..."
      $pattern = "(?s)$([regex]::Escape($script:CCB_START_MARKER)).*?$([regex]::Escape($script:CCB_END_MARKER))\r?\n?"
      $content = $content -replace $pattern, ""
      $content = $content.Trim() + "`n"
      [System.IO.File]::WriteAllText($claudeMd, $content, $script:utf8NoBom)
      Write-Host "  Removed CCB config block"
    }
  }

  # 5. Remove settings.json permissions
  $settingsFile = Join-Path $env:USERPROFILE ".claude\settings.json"
  if (Test-Path $settingsFile) {
    $permsToRemove = @(
      "Bash(ccb ask *)", "Bash(ccb clear *)", "Bash(ccb ping *)", "Bash(ccb pend *)",
      "Bash(ask:*)", "Bash(ping:*)", "Bash(ccb-ping:*)", "Bash(pend:*)"
    )
    try {
      if ($settings.permissions -and $settings.permissions.allow) {
        $originalCount = $settings.permissions.allow.Count
        $settings.permissions.allow = @($settings.permissions.allow | Where-Object { $_ -notin $permsToRemove })
        if ($settings.permissions.allow.Count -ne $originalCount) {
          Write-CCBSettings -Path $settingsFile -Value $settings
          Write-Host "Removed CCB permissions from settings.json"
        }
      }
    } catch {
      throw "Could not safely clean settings.json: $_"
    }
  }

  # 6. Remove Codex skills
  $codexHome = Resolve-CodexSourceHome
  $codexSkillsDir = Join-Path $codexHome "skills"
  if (Test-Path $codexSkillsDir) {
    Write-Host "Removing CCB Codex skills..."
    foreach ($skill in $legacySkills) {
      Remove-Item -Recurse -Force (Join-Path $codexSkillsDir $skill) -ErrorAction SilentlyContinue
    }
    foreach ($skill in $ccbSkills) {
      $skillPath = Join-Path $codexSkillsDir $skill
      if (Test-Path $skillPath) {
        Remove-Item -Recurse -Force $skillPath
        Write-Host "  Removed skill: $skill"
      }
    }
  }

  # 7. Remove Droid skills
  $factoryHome = if ($env:FACTORY_HOME) { $env:FACTORY_HOME } else { Join-Path $env:USERPROFILE ".factory" }
  $droidSkillsDir = Join-Path $factoryHome "skills"
  $droidSkills = @("ask", "ccb-clear", "ccb-compact", "ccb-diagnose")
  $legacyDroidSkills = @("ping", "pend", "autonew", "all-plan")
  if (Test-Path $droidSkillsDir) {
    Write-Host "Removing CCB Droid skills..."
    foreach ($skill in $legacyDroidSkills) {
      Remove-Item -Recurse -Force (Join-Path $droidSkillsDir $skill) -ErrorAction SilentlyContinue
    }
    foreach ($skill in $droidSkills) {
      $skillPath = Join-Path $droidSkillsDir $skill
      if (Test-Path $skillPath) {
        Remove-Item -Recurse -Force $skillPath
        Write-Host "  Removed skill: $skill"
      }
    }
  }

  Write-Host "Uninstall complete."
}

if ($Command -eq "help") {
  Show-Usage
  exit 0
}

if ($Command -eq "install") {
  try {
    Install-Native
  } finally {
    if ($script:extractedReleaseRoot -and (Test-Path -LiteralPath $script:extractedReleaseRoot)) {
      Remove-Item -LiteralPath $script:extractedReleaseRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
  }
  exit 0
}

if ($Command -eq "uninstall") {
  Uninstall-Native
  exit 0
}
