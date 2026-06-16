#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_PREFIX="${CODEX_INSTALL_PREFIX:-$HOME/.local/share/codex-dual}"
BIN_DIR="${CODEX_BIN_DIR:-$HOME/.local/bin}"
readonly REPO_ROOT INSTALL_PREFIX BIN_DIR

require_supported_bash() {
  if [[ -z "${BASH_VERSION:-}" ]]; then
    echo "ERROR: install.sh must be run with bash. Try: bash ./install.sh install" >&2
    exit 1
  fi
  if (( BASH_VERSINFO[0] < 3 || (BASH_VERSINFO[0] == 3 && BASH_VERSINFO[1] < 2) )); then
    echo "ERROR: bash ${BASH_VERSION} is too old for install.sh." >&2
    echo "   Please run with bash 3.2+ or install a newer bash, then retry." >&2
    exit 1
  fi
}

require_supported_bash

# i18n support
detect_lang() {
  local lang="${CCB_LANG:-auto}"
  case "$lang" in
    zh|cn|chinese) echo "zh" ;;
    en|english) echo "en" ;;
    *)
      local sys_lang="${LANG:-${LC_ALL:-${LC_MESSAGES:-}}}"
      if [[ "$sys_lang" == zh* ]] || [[ "$sys_lang" == *chinese* ]]; then
        echo "zh"
      else
        echo "en"
      fi
      ;;
  esac
}

CCB_LANG_DETECTED="$(detect_lang)"

# Message function
msg() {
  local key="$1"
  shift
  local en_msg zh_msg
  case "$key" in
    install_complete)
      en_msg="Installation complete"
      zh_msg="安装完成" ;;
    uninstall_complete)
      en_msg="Uninstall complete"
      zh_msg="卸载完成" ;;
    python_version_old)
      en_msg="Python version too old: $1"
      zh_msg="Python 版本过旧: $1" ;;
    requires_python)
      en_msg="Requires Python 3.10+"
      zh_msg="需要 Python 3.10+" ;;
    missing_dep)
      en_msg="Missing dependency: $1"
      zh_msg="缺少依赖: $1" ;;
    detected_env)
      en_msg="Detected $1 environment"
      zh_msg="检测到 $1 环境" ;;
    confirm_wsl)
      en_msg="Confirm continue installing in WSL? (y/N)"
      zh_msg="确认继续在 WSL 中安装？(y/N)" ;;
    cancelled)
      en_msg="Installation cancelled"
      zh_msg="安装已取消" ;;
    wsl_warning)
      en_msg="Detected WSL environment"
      zh_msg="检测到 WSL 环境" ;;
    same_env_required)
      en_msg="ccb, ccb ask, ccb ping, and ccb pend must run in the same environment as codex/gemini."
      zh_msg="ccb、ccb ask、ccb ping、ccb pend 必须与 codex/gemini 在同一环境运行。" ;;
    confirm_wsl_native)
      en_msg="Please confirm: you will install and run codex/gemini in WSL (not Windows native)."
      zh_msg="请确认：你将在 WSL 中安装并运行 codex/gemini（不是 Windows 原生）。" ;;
    watchdog_installing)
      en_msg="Installing Python dependency: watchdog"
      zh_msg="正在安装 Python 依赖: watchdog" ;;
    watchdog_installed)
      en_msg="OK: watchdog installed"
      zh_msg="OK: watchdog 已安装" ;;
    watchdog_failed)
      en_msg="WARN: watchdog install failed; continuing without optional file watchers"
      zh_msg="警告：watchdog 安装失败；将不启用可选文件监听" ;;
    watchdog_optional)
      en_msg="INFO: watchdog is optional. ccb will still install and use polling/readback paths when watchers are unavailable."
      zh_msg="信息：watchdog 是可选依赖。未启用监听时，ccb 仍会安装并使用轮询/回读路径。" ;;
    watchdog_skipped)
      en_msg="INFO: watchdog auto-install skipped by CCB_INSTALL_WATCHDOG=0"
      zh_msg="信息：已通过 CCB_INSTALL_WATCHDOG=0 跳过 watchdog 自动安装" ;;
    watchdog_python_missing)
      en_msg="WARN: python not available; skipping optional watchdog install"
      zh_msg="警告：未找到 Python；跳过可选 watchdog 安装" ;;
    tomli_installing)
      en_msg="Installing Python dependency: tomli"
      zh_msg="正在安装 Python 依赖: tomli" ;;
    tomli_installed)
      en_msg="OK: TOML parser available"
      zh_msg="OK: TOML 解析器可用" ;;
    tomli_failed)
      en_msg="WARN: tomli install failed; rich TOML config requires Python 3.11+ or tomli/toml"
      zh_msg="警告：tomli 安装失败；rich TOML config 需要 Python 3.11+ 或 tomli/toml" ;;
    tomli_skipped)
      en_msg="INFO: tomli auto-install skipped by CCB_INSTALL_TOMLI=0"
      zh_msg="信息：已通过 CCB_INSTALL_TOMLI=0 跳过 tomli 自动安装" ;;
    tomli_python_missing)
      en_msg="WARN: python not available; skipping tomli install"
      zh_msg="警告：未找到 Python；跳过 tomli 安装" ;;
    pip_missing)
      en_msg="WARN: pip not available for selected Python"
      zh_msg="警告：当前 Python 未提供 pip" ;;
    root_error)
      en_msg="ERROR: Do not run as root/sudo. Please run as normal user."
      zh_msg="错误：请勿以 root/sudo 身份运行。请使用普通用户执行。" ;;
    install_notice_source_title)
      en_msg="WARN: Development/source install detected"
      zh_msg="警告：检测到开发源码安装" ;;
    install_notice_source_body)
      en_msg="This is a development install, not an official release package."
      zh_msg="这是开发安装，不是正式 release 包。" ;;
    install_notice_release)
      en_msg="INFO: Official release package install detected"
      zh_msg="信息：检测到正式 release 包安装" ;;
    install_notice_preview_title)
      en_msg="WARN: Preview release package install detected"
      zh_msg="警告：检测到预览版 release 包安装" ;;
    install_notice_preview_body)
      en_msg="This package was built from a preview/dirty source snapshot, not an official stable release."
      zh_msg="该安装包来自预览或脏工作区快照，不是正式稳定 release。" ;;
    *)
      en_msg="$key"
      zh_msg="$key" ;;
  esac
  if [[ "$CCB_LANG_DETECTED" == "zh" ]]; then
    echo "$zh_msg"
  else
    echo "$en_msg"
  fi
}

current_effective_uid() {
  if [[ -n "${CCB_TEST_EUID:-}" ]]; then
    echo "$CCB_TEST_EUID"
    return
  fi
  if [[ -n "${EUID:-}" ]]; then
    echo "$EUID"
    return
  fi
  id -u
}

current_effective_user_name() {
  if [[ -n "${CCB_TEST_USER_NAME:-}" ]]; then
    echo "$CCB_TEST_USER_NAME"
    return
  fi
  id -un 2>/dev/null || echo "unknown"
}

install_stdin_is_tty() {
  if [[ "${CCB_TEST_STDIN_TTY:-}" == "1" ]]; then
    return 0
  fi
  [[ -t 0 ]]
}

ccb_data_home() {
  if [[ -n "${XDG_DATA_HOME:-}" ]]; then
    echo "$XDG_DATA_HOME"
  else
    echo "$HOME/.local/share"
  fi
}

print_root_install_warning() {
  local data_home sudo_user
  data_home="$(ccb_data_home)"
  sudo_user="${SUDO_USER:-}"
  echo "WARN: Root install is not recommended." >&2
  echo >&2
  echo "You are installing CCB as root." >&2
  echo >&2
  echo "This will install and run CCB in root's own profile:" >&2
  echo "  install prefix : $INSTALL_PREFIX" >&2
  echo "  bin directory  : $BIN_DIR" >&2
  echo "  role store     : $data_home/ccb/roles" >&2
  echo "  tool store     : $data_home/ccb/tools" >&2
  echo "  provider auth  : root-owned provider homes and credentials" >&2
  echo >&2
  if [[ -n "$sudo_user" && "$sudo_user" != "root" ]]; then
    echo "Detected sudo user: $sudo_user" >&2
    echo "This will not install CCB for $sudo_user; it will install for root." >&2
    echo >&2
  fi
  echo "Do not use root unless you intentionally run Codex/Claude/Gemini as root." >&2
  echo "If this command was started with sudo by mistake, cancel now and rerun as your normal user." >&2
  echo >&2
}

confirm_root_install_if_needed() {
  local uid
  uid="$(current_effective_uid)"
  if [[ "$uid" != "0" ]]; then
    return 0
  fi

  if [[ "${CCB_ALLOW_ROOT_INSTALL:-}" == "1" ]]; then
    echo "WARN: Continuing root install because CCB_ALLOW_ROOT_INSTALL=1 is set." >&2
    if [[ -n "${SUDO_USER:-}" && "${SUDO_USER:-}" != "root" ]]; then
      echo "WARN: Detected sudo user ${SUDO_USER}; this will install for root, not ${SUDO_USER}." >&2
    fi
    return 0
  fi

  print_root_install_warning

  if ! install_stdin_is_tty; then
    echo "ERROR: Root install requires explicit confirmation." >&2
    echo "   Re-run with CCB_ALLOW_ROOT_INSTALL=1 only if this is intentional." >&2
    exit 1
  fi

  local reply
  read -r -p "Continue root install? (y/N): " reply
  case "$reply" in
    y|Y|yes|YES)
      return 0
      ;;
    *)
      echo "Installation cancelled" >&2
      exit 1
      ;;
  esac
}

require_non_root_execution() {
  confirm_root_install_if_needed
}

canonical_existing_parent_path() {
  local path="$1"
  if [[ -d "$path" ]]; then
    (cd "$path" && pwd -P)
    return
  fi
  local dir base
  dir="$(dirname "$path")"
  base="$(basename "$path")"
  if [[ -d "$dir" ]]; then
    printf '%s/%s\n' "$(cd "$dir" && pwd -P)" "$base"
  else
    printf '%s\n' "$path"
  fi
}

path_is_within() {
  local root="$1"
  local candidate="$2"
  [[ "$candidate" == "$root" || "$candidate" == "$root/"* ]]
}

path_is_temporary_rooted() {
  local path="$1"
  case "$path" in
    /tmp|/tmp/*|/var/tmp|/var/tmp/*|/dev/shm|/dev/shm/*|/private/tmp|/private/tmp/*)
      return 0
      ;;
  esac
  local tmp_root=""
  tmp_root="$(canonical_existing_parent_path "${TMPDIR:-/tmp}" 2>/dev/null || true)"
  if [[ -n "$tmp_root" && ( "$path" == "$tmp_root" || "$path" == "$tmp_root/"* ) ]]; then
    return 0
  fi
  return 1
}

validate_temporary_install_scope() {
  if env_value_is_true "${CCB_ALLOW_TEMP_INSTALL_GLOBAL_BIN:-}"; then
    return 0
  fi

  local prefix_path bin_path home_path
  prefix_path="$(canonical_existing_parent_path "$INSTALL_PREFIX")"
  bin_path="$(canonical_existing_parent_path "$BIN_DIR")"
  home_path="$(canonical_existing_parent_path "$HOME")"

  if ! path_is_temporary_rooted "$prefix_path"; then
    return 0
  fi
  if path_is_within "$prefix_path" "$bin_path"; then
    return 0
  fi
  if path_is_temporary_rooted "$home_path" && path_is_within "$home_path" "$bin_path"; then
    return 0
  fi

  echo "ERROR: Refusing to install a temporary CODEX_INSTALL_PREFIX into an external bin directory." >&2
  echo "   install prefix : $INSTALL_PREFIX" >&2
  echo "   bin directory  : $BIN_DIR" >&2
  echo "   Use an isolated CODEX_BIN_DIR under the same temp prefix/home, or set CCB_ALLOW_TEMP_INSTALL_GLOBAL_BIN=1 if intentional." >&2
  exit 1
}

SCRIPTS_TO_LINK=(
  bin/_ccb-python
  bin/ask
  bin/autonew
  bin/build-ccb-agent-sidebar
  bin/ccb-agent-sidebar
  bin/ccb-provider-activity-hook
  bin/ctx-transfer
  ccb
)

CLAUDE_MARKDOWN=(
  # Old CCB command markdown removed; managed CCB workflows install as skills.
)

LEGACY_SCRIPTS=(
  bask
  bpend
  bping
  cask
  cpend
  cping
  ccb-mounted
  ccb-ping
  ping
  dask
  dpend
  dping
  gask
  gpend
  gping
  hask
  hpend
  hping
  lask
  lpend
  lping
  oask
  opend
  oping
  pend
  qask
  qpend
  qping
  cast
  cast-w
  codex-ask
  codex-pending
  codex-ping
  claude-codex-dual
  claude_codex
  claude_ai
  claude_bridge
  caskd
  gaskd
  oaskd
  laskd
  daskd
)

usage() {
  cat <<'USAGE'
Usage:
  ./install.sh install    # Install or update Codex dual-window tools
  ./install.sh uninstall  # Uninstall installed content

Optional environment variables:
  CODEX_INSTALL_PREFIX     Install directory (default: ~/.local/share/codex-dual)
  CODEX_BIN_DIR            Executable directory (default: ~/.local/bin)
  CODEX_CLAUDE_COMMAND_DIR Custom Claude commands directory (default: auto-detect)
  CCB_DROID_AUTOINSTALL    Auto-register Droid MCP tools if droid exists (default: 1)
  CCB_DROID_AUTOINSTALL_FORCE Re-register Droid MCP tools (default: 0)
  CCB_DROID_AUTOINSTALL_TIMEOUT_S Timeout for Droid MCP registration (default: 10)
  CCB_BUILD_CHANNEL        Override build channel metadata (e.g. stable, preview, dev)
  CCB_BUILD_PLATFORM       Override build platform metadata (default: detected platform)
  CCB_BUILD_ARCH           Override build arch metadata (default: uname -m)
  CCB_BUILD_TIME           Override build timestamp metadata (default: current UTC time)
  CCB_SOURCE_KIND          Override source kind metadata (default: source if .git exists, else release)
  CCB_PYTHON_BIN           Python 3.10+ executable to use for install-time checks and wrappers
  CCB_USE_MANAGED_VENV     Use install-local Python venv: auto (default), 1, or 0
                           auto = enabled for macOS release installs, disabled for source/dev installs
  CCB_INSTALL_TOMLI        Auto-install tomli on Python versions without tomllib (default: 1; set 0 to skip)
  CCB_INSTALL_WATCHDOG     Auto-install optional watchdog dependency (default: 1; set 0 to skip)
  CCB_INSTALL_ROLES        Install catalog Role Packs and dependencies: auto soft (default), 1 required, 0 skip
  CCB_ALLOW_ROOT_INSTALL   Set to 1 to explicitly allow a root-owned install
  CCB_ALLOW_TEMP_INSTALL_GLOBAL_BIN Set to 1 to allow a temporary install prefix to write outside its isolated bin/home
  CCB_CONFIRM_MAJOR_UPGRADE Set to 1 to confirm replacing a pre-v6 install with v6+
USAGE
}

detect_claude_dir() {
  if [[ -n "${CODEX_CLAUDE_COMMAND_DIR:-}" ]]; then
    echo "$CODEX_CLAUDE_COMMAND_DIR"
    return
  fi

  local candidates=(
    "$HOME/.claude/commands"
    "$HOME/.config/claude/commands"
    "$HOME/.local/share/claude/commands"
  )

  for dir in "${candidates[@]}"; do
    if [[ -d "$dir" ]]; then
      echo "$dir"
      return
    fi
  done

  local fallback="$HOME/.claude/commands"
  mkdir -p "$fallback"
  echo "$fallback"
}

require_command() {
  local cmd="$1"
  local pkg="${2:-$1}"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "ERROR: Missing dependency: $cmd"
    echo "   Please install $pkg first, then re-run install.sh"
    exit 1
  fi
}

env_value_is_true() {
  case "${1:-}" in
    1|true|TRUE|True|yes|YES|Yes|on|ON|On) return 0 ;;
    *) return 1 ;;
  esac
}

env_value_is_false() {
  case "${1:-}" in
    0|false|FALSE|False|no|NO|No|off|OFF|Off) return 0 ;;
    *) return 1 ;;
  esac
}

PYTHON_BIN="${CCB_PYTHON_BIN:-}"
PYTHON_CANDIDATE_COMMANDS=(
  python3
  python3.14
  python3.13
  python3.12
  python3.11
  python3.10
  python
)

_python_check_310() {
  local cmd="$1"
  command -v "$cmd" >/dev/null 2>&1 || return 1
  "$cmd" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

pick_python_bin() {
  if [[ -n "${PYTHON_BIN}" ]] && _python_check_310 "${PYTHON_BIN}"; then
    return 0
  fi
  for cmd in "${PYTHON_CANDIDATE_COMMANDS[@]}"; do
    if _python_check_310 "$cmd"; then
      PYTHON_BIN="$cmd"
      return 0
    fi
  done
  return 1
}

pick_any_python_bin() {
  if [[ -n "${PYTHON_BIN}" ]] && command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
    return 0
  fi
  for cmd in "${PYTHON_CANDIDATE_COMMANDS[@]}"; do
    if command -v "$cmd" >/dev/null 2>&1; then
      PYTHON_BIN="$cmd"
      return 0
    fi
  done
  return 1
}

require_python_version() {
  # ccb requires Python 3.10+ (PEP 604 type unions: `str | None`, etc.)
  if ! pick_python_bin; then
    echo "ERROR: Missing dependency: python (3.10+ required)"
    echo "   Please install Python 3.10+ and ensure it is on PATH, then re-run install.sh"
    exit 1
  fi
  local version
  version="$("$PYTHON_BIN" -c 'import sys; print("{}.{}.{}".format(sys.version_info[0], sys.version_info[1], sys.version_info[2]))' 2>/dev/null || echo unknown)"
  if ! _python_check_310 "$PYTHON_BIN"; then
    echo "ERROR: Python version too old: $version"
    echo "   Requires Python 3.10+, please upgrade and retry"
    exit 1
  fi
  echo "OK: Python $version ($PYTHON_BIN)"
}

print_git_install_hint() {
  local platform
  platform="$(detect_platform)"
  case "$platform" in
    macos)
      if command -v brew >/dev/null 2>&1; then
        echo "   macOS: brew install git"
      else
        echo "   macOS: install Xcode Command Line Tools with 'xcode-select --install', or install Homebrew then run 'brew install git'"
      fi
      ;;
    linux)
      if command -v apt-get >/dev/null 2>&1; then
        echo "   Debian/Ubuntu: sudo apt-get update && sudo apt-get install -y git"
      elif command -v dnf >/dev/null 2>&1; then
        echo "   Fedora/CentOS/RHEL: sudo dnf install -y git"
      elif command -v yum >/dev/null 2>&1; then
        echo "   CentOS/RHEL: sudo yum install -y git"
      elif command -v pacman >/dev/null 2>&1; then
        echo "   Arch/Manjaro: sudo pacman -S git"
      elif command -v apk >/dev/null 2>&1; then
        echo "   Alpine: sudo apk add git"
      elif command -v zypper >/dev/null 2>&1; then
        echo "   openSUSE: sudo zypper install -y git"
      else
        echo "   Linux: install git with your distro's package manager"
      fi
      ;;
    *)
      echo "   Install git and ensure it is on PATH"
      ;;
  esac
}

print_npm_install_hint() {
  local platform
  platform="$(detect_platform)"
  case "$platform" in
    macos)
      if command -v brew >/dev/null 2>&1; then
        echo "   macOS: brew install node"
      else
        echo "   macOS: install Node.js/npm from https://nodejs.org/ or with Homebrew"
      fi
      ;;
    linux)
      if command -v apt-get >/dev/null 2>&1; then
        echo "   Debian/Ubuntu: sudo apt-get update && sudo apt-get install -y nodejs npm"
      elif command -v dnf >/dev/null 2>&1; then
        echo "   Fedora/CentOS/RHEL: sudo dnf install -y nodejs npm"
      elif command -v yum >/dev/null 2>&1; then
        echo "   CentOS/RHEL: sudo yum install -y nodejs npm"
      elif command -v pacman >/dev/null 2>&1; then
        echo "   Arch/Manjaro: sudo pacman -S nodejs npm"
      elif command -v apk >/dev/null 2>&1; then
        echo "   Alpine: sudo apk add nodejs npm"
      elif command -v zypper >/dev/null 2>&1; then
        echo "   openSUSE: sudo zypper install -y nodejs npm"
      else
        echo "   Linux: install Node.js/npm with your distro's package manager"
      fi
      ;;
    *)
      echo "   Install Node.js/npm and ensure npm is on PATH"
      ;;
  esac
}

check_role_pack_dependencies() {
  local mode="${1:-warn}"
  local label="WARN"
  if [[ "$mode" == "required" ]]; then
    label="ERROR"
  fi
  local missing=0
  if ! command -v git >/dev/null 2>&1; then
    echo "$label: Missing dependency for Role Pack provisioning: git"
    print_git_install_hint
    missing=1
  fi
  if ! command -v npm >/dev/null 2>&1; then
    echo "$label: Missing dependency for Role Pack provisioning: npm"
    print_npm_install_hint
    missing=1
  fi
  if [[ "$missing" -eq 0 ]]; then
    return 0
  fi
  echo "   Install the missing dependencies above, then re-run ./install.sh install."
  echo "   To install CCB without Role Pack provisioning now, set CCB_INSTALL_ROLES=0."
  return 1
}

selected_python_executable() {
  if ! pick_python_bin; then
    echo "ERROR: Missing dependency: python (3.10+ required)" >&2
    return 1
  fi
  "$PYTHON_BIN" -c 'import sys; print(sys.executable)'
}

python_has_module() {
  local module="$1"
  if ! pick_any_python_bin; then
    return 1
  fi
  "$PYTHON_BIN" - <<PY >/dev/null 2>&1
import importlib.util
import sys
sys.exit(0 if importlib.util.find_spec("${module}") else 1)
PY
}

python_has_toml_reader() {
  if ! pick_any_python_bin; then
    return 1
  fi
  "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import importlib.util
import sys

for module_name in ("tomllib", "tomli", "toml"):
    if importlib.util.find_spec(module_name) is not None:
        raise SystemExit(0)
raise SystemExit(1)
PY
}

python_is_virtual_environment() {
  local python_path="${1:-$PYTHON_BIN}"
  "$python_path" - <<'PY' >/dev/null 2>&1
import sys

is_virtualenv = getattr(sys, "base_prefix", sys.prefix) != sys.prefix or hasattr(sys, "real_prefix")
raise SystemExit(0 if is_virtualenv else 1)
PY
}

tomli_manual_install_command() {
  local use_venv_scope="${1:-0}"
  if [[ "$use_venv_scope" == "1" ]]; then
    echo "   $PYTHON_BIN -m pip install 'tomli>=2.0.0'"
  else
    echo "   $PYTHON_BIN -m pip install --user 'tomli>=2.0.0'"
  fi
}

install_tomli_into_virtualenv() {
  local python_version python_path
  python_version="$("$PYTHON_BIN" -c 'import sys; print("{}.{}.{}".format(sys.version_info[0], sys.version_info[1], sys.version_info[2]))' 2>/dev/null || echo unknown)"
  python_path="$("$PYTHON_BIN" -c 'import sys; print(sys.executable)' 2>/dev/null || command -v "$PYTHON_BIN" 2>/dev/null || echo "$PYTHON_BIN")"

  local pip_log pip_log_cleanup=0 last_failure=""
  pip_log="$(mktemp "${TMPDIR:-/tmp}/ccb-tomli-pip.XXXXXX.log" 2>/dev/null || mktemp "/tmp/ccb-tomli-pip.XXXXXX.log" 2>/dev/null || true)"
  if [[ -z "$pip_log" ]]; then
    pip_log="/dev/null"
  else
    pip_log_cleanup=1
  fi

  if "$PYTHON_BIN" -m pip install "tomli>=2.0.0" >"$pip_log" 2>&1; then
    if python_has_toml_reader; then
      if [[ "$pip_log_cleanup" -eq 1 ]]; then
        rm -f "$pip_log"
      fi
      msg tomli_installed
      return 0
    fi
    last_failure="pip install succeeded, but Python $python_path still cannot import tomli"
  else
    last_failure="$PYTHON_BIN -m pip install 'tomli>=2.0.0' failed"
  fi

  msg tomli_failed
  if [[ -n "$last_failure" ]]; then
    echo "   Last failure: $last_failure"
  fi
  if [[ "$pip_log_cleanup" -eq 1 && -s "$pip_log" ]]; then
    echo "   pip output:"
    tail -20 "$pip_log" | sed 's/^/     /'
  fi
  if [[ "$pip_log_cleanup" -eq 1 ]]; then
    rm -f "$pip_log"
  fi
  echo "   Manual install:"
  tomli_manual_install_command 1
  return 0
}

install_tomli() {
  if [[ "${CCB_INSTALL_TOMLI:-1}" == "0" ]]; then
    msg tomli_skipped
    return 0
  fi
  if python_has_toml_reader; then
    msg tomli_installed
    return 0
  fi
  if ! pick_any_python_bin; then
    msg tomli_python_missing
    return 0
  fi
  local python_version python_path
  python_version="$("$PYTHON_BIN" -c 'import sys; print("{}.{}.{}".format(sys.version_info[0], sys.version_info[1], sys.version_info[2]))' 2>/dev/null || echo unknown)"
  python_path="$("$PYTHON_BIN" -c 'import sys; print(sys.executable)' 2>/dev/null || command -v "$PYTHON_BIN" 2>/dev/null || echo "$PYTHON_BIN")"
  msg tomli_installing
  echo "   Python: $python_path ($python_version)"

  if python_is_virtual_environment; then
    install_tomli_into_virtualenv
    return 0
  fi

  local last_failure=""
  if command -v uv >/dev/null 2>&1; then
    if uv pip install --system "tomli>=2.0.0" >/dev/null 2>&1 || \
       uv pip install "tomli>=2.0.0" >/dev/null 2>&1; then
      if python_has_toml_reader; then
        msg tomli_installed
        return 0
      fi
      last_failure="uv installed tomli, but Python $python_path still cannot import it"
    else
      last_failure="uv pip install tomli>=2.0.0 failed"
    fi
  fi

  if ! "$PYTHON_BIN" -m pip --version >/dev/null 2>&1; then
    msg pip_missing
    echo "   Install manually for this Python if rich TOML config is needed:"
    echo "   $PYTHON_BIN -m ensurepip --upgrade"
    tomli_manual_install_command 0
    return 0
  fi

  local pip_log pip_log_cleanup=0
  pip_log="$(mktemp "${TMPDIR:-/tmp}/ccb-tomli-pip.XXXXXX.log" 2>/dev/null || mktemp "/tmp/ccb-tomli-pip.XXXXXX.log" 2>/dev/null || true)"
  if [[ -z "$pip_log" ]]; then
    pip_log="/dev/null"
  else
    pip_log_cleanup=1
  fi
  if "$PYTHON_BIN" -m pip install --user "tomli>=2.0.0" >"$pip_log" 2>&1; then
    if python_has_toml_reader; then
      if [[ "$pip_log_cleanup" -eq 1 ]]; then
        rm -f "$pip_log"
      fi
      msg tomli_installed
      return 0
    fi
    last_failure="pip install --user succeeded, but Python $python_path still cannot import tomli"
  else
    last_failure="$PYTHON_BIN -m pip install --user 'tomli>=2.0.0' failed"
  fi

  if "$PYTHON_BIN" -m pip install --user --break-system-packages "tomli>=2.0.0" >"$pip_log" 2>&1; then
    if python_has_toml_reader; then
      if [[ "$pip_log_cleanup" -eq 1 ]]; then
        rm -f "$pip_log"
      fi
      msg tomli_installed
      return 0
    fi
    last_failure="pip install --user --break-system-packages succeeded, but Python $python_path still cannot import tomli"
  else
    last_failure="$PYTHON_BIN -m pip install --user --break-system-packages 'tomli>=2.0.0' failed"
  fi

  msg tomli_failed
  if [[ -n "$last_failure" ]]; then
    echo "   Last failure: $last_failure"
  fi
  if [[ "$pip_log_cleanup" -eq 1 && -s "$pip_log" ]]; then
    echo "   pip output:"
    tail -20 "$pip_log" | sed 's/^/     /'
  fi
  if [[ "$pip_log_cleanup" -eq 1 ]]; then
    rm -f "$pip_log"
  fi
  echo "   Manual install:"
  tomli_manual_install_command 0
  return 0
}

install_tomli_for_python() {
  local python_cmd="$1"
  local previous_python_bin="$PYTHON_BIN"
  PYTHON_BIN="$python_cmd"
  install_tomli
  PYTHON_BIN="$previous_python_bin"
}

watchdog_manual_install_command() {
  local use_venv_scope="${1:-0}"
  if [[ "$use_venv_scope" == "1" ]]; then
    echo "   $PYTHON_BIN -m pip install 'watchdog>=2.1.0'"
  else
    echo "   $PYTHON_BIN -m pip install --user 'watchdog>=2.1.0'"
  fi
}

install_watchdog_into_virtualenv() {
  local python_version python_path
  python_version="$("$PYTHON_BIN" -c 'import sys; print("{}.{}.{}".format(sys.version_info[0], sys.version_info[1], sys.version_info[2]))' 2>/dev/null || echo unknown)"
  python_path="$("$PYTHON_BIN" -c 'import sys; print(sys.executable)' 2>/dev/null || command -v "$PYTHON_BIN" 2>/dev/null || echo "$PYTHON_BIN")"

  local pip_log pip_log_cleanup=0 last_failure=""
  pip_log="$(mktemp "${TMPDIR:-/tmp}/ccb-watchdog-pip.XXXXXX.log" 2>/dev/null || mktemp "/tmp/ccb-watchdog-pip.XXXXXX.log" 2>/dev/null || true)"
  if [[ -z "$pip_log" ]]; then
    pip_log="/dev/null"
  else
    pip_log_cleanup=1
  fi

  if "$PYTHON_BIN" -m pip install "watchdog>=2.1.0" >"$pip_log" 2>&1; then
    if python_has_module "watchdog"; then
      if [[ "$pip_log_cleanup" -eq 1 ]]; then
        rm -f "$pip_log"
      fi
      msg watchdog_installed
      return 0
    fi
    last_failure="pip install succeeded, but Python $python_path still cannot import watchdog"
  else
    last_failure="$PYTHON_BIN -m pip install 'watchdog>=2.1.0' failed"
  fi

  msg watchdog_failed
  if [[ -n "$last_failure" ]]; then
    echo "   Last failure: $last_failure"
  fi
  if [[ "$pip_log_cleanup" -eq 1 && -s "$pip_log" ]]; then
    echo "   pip output:"
    tail -20 "$pip_log" | sed 's/^/     /'
  fi
  if [[ "$pip_log_cleanup" -eq 1 ]]; then
    rm -f "$pip_log"
  fi
  echo "   Manual optional install:"
  watchdog_manual_install_command 1
  msg watchdog_optional
  return 0
}

install_watchdog() {
  if [[ "${CCB_INSTALL_WATCHDOG:-1}" == "0" ]]; then
    msg watchdog_skipped
    return 0
  fi
  if python_has_module "watchdog"; then
    msg watchdog_installed
    return 0
  fi
  if ! pick_any_python_bin; then
    msg watchdog_python_missing
    msg watchdog_optional
    return 0
  fi
  local python_version python_path
  python_version="$("$PYTHON_BIN" -c 'import sys; print("{}.{}.{}".format(sys.version_info[0], sys.version_info[1], sys.version_info[2]))' 2>/dev/null || echo unknown)"
  python_path="$("$PYTHON_BIN" -c 'import sys; print(sys.executable)' 2>/dev/null || command -v "$PYTHON_BIN" 2>/dev/null || echo "$PYTHON_BIN")"
  msg watchdog_installing
  echo "   Python: $python_path ($python_version)"

  if python_is_virtual_environment; then
    install_watchdog_into_virtualenv
    return 0
  fi

  local last_failure=""
  # 1. Try uv (fast, no PEP 668 issues)
  if command -v uv >/dev/null 2>&1; then
    if uv pip install --system "watchdog>=2.1.0" >/dev/null 2>&1 || \
       uv pip install "watchdog>=2.1.0" >/dev/null 2>&1; then
      if python_has_module "watchdog"; then
        msg watchdog_installed
        return 0
      fi
      last_failure="uv installed watchdog, but Python $python_path still cannot import it"
    else
      last_failure="uv pip install watchdog>=2.1.0 failed"
    fi
  fi

  if ! "$PYTHON_BIN" -m pip --version >/dev/null 2>&1; then
    msg pip_missing
    echo "   Install manually for this Python if file watchers are desired:"
    echo "   $PYTHON_BIN -m ensurepip --upgrade"
    watchdog_manual_install_command 0
    msg watchdog_optional
    return 0
  fi

  # 2. Try standard pip install --user
  local pip_log pip_log_cleanup=0
  pip_log="$(mktemp "${TMPDIR:-/tmp}/ccb-watchdog-pip.XXXXXX.log" 2>/dev/null || mktemp "/tmp/ccb-watchdog-pip.XXXXXX.log" 2>/dev/null || true)"
  if [[ -z "$pip_log" ]]; then
    pip_log="/dev/null"
  else
    pip_log_cleanup=1
  fi
  if "$PYTHON_BIN" -m pip install --user "watchdog>=2.1.0" >"$pip_log" 2>&1; then
    if python_has_module "watchdog"; then
      if [[ "$pip_log_cleanup" -eq 1 ]]; then
        rm -f "$pip_log"
      fi
      msg watchdog_installed
      return 0
    fi
    last_failure="pip install --user succeeded, but Python $python_path still cannot import watchdog"
  else
    last_failure="$PYTHON_BIN -m pip install --user 'watchdog>=2.1.0' failed"
  fi

  # 3. PEP 668 fallback: --break-system-packages (Homebrew Python, Debian 12+, etc.)
  if "$PYTHON_BIN" -m pip install --user --break-system-packages "watchdog>=2.1.0" >"$pip_log" 2>&1; then
    if python_has_module "watchdog"; then
      if [[ "$pip_log_cleanup" -eq 1 ]]; then
        rm -f "$pip_log"
      fi
      msg watchdog_installed
      return 0
    fi
    last_failure="pip install --user --break-system-packages succeeded, but Python $python_path still cannot import watchdog"
  else
    last_failure="$PYTHON_BIN -m pip install --user --break-system-packages 'watchdog>=2.1.0' failed"
  fi

  # 4. Try pipx inject into a shared venv as last resort
  if command -v pipx >/dev/null 2>&1; then
    if pipx install watchdog >/dev/null 2>&1; then
      if python_has_module "watchdog"; then
        if [[ "$pip_log_cleanup" -eq 1 ]]; then
          rm -f "$pip_log"
        fi
        msg watchdog_installed
        return 0
      fi
      last_failure="pipx installed watchdog, but Python $python_path still cannot import it"
    else
      last_failure="pipx install watchdog failed"
    fi
  fi

  msg watchdog_failed
  if [[ -n "$last_failure" ]]; then
    echo "   Last failure: $last_failure"
  fi
  if [[ "$pip_log_cleanup" -eq 1 && -s "$pip_log" ]]; then
    echo "   pip output:"
    tail -20 "$pip_log" | sed 's/^/     /'
  fi
  if [[ "$pip_log_cleanup" -eq 1 ]]; then
    rm -f "$pip_log"
  fi
  echo "   Manual optional install:"
  watchdog_manual_install_command 0
  msg watchdog_optional
  return 0
}

install_watchdog_for_python() {
  local python_cmd="$1"
  local previous_python_bin="$PYTHON_BIN"
  PYTHON_BIN="$python_cmd"
  install_watchdog
  PYTHON_BIN="$previous_python_bin"
}

# Return linux / macos / unknown based on uname
detect_platform() {
  local name
  name="$(uname -s 2>/dev/null || echo unknown)"
  case "$name" in
    Linux) echo "linux" ;;
    Darwin) echo "macos" ;;
    *) echo "unknown" ;;
  esac
}


is_wsl() {
  [[ -f /proc/version ]] && grep -qi microsoft /proc/version 2>/dev/null
}

get_wsl_version() {
  if [[ -n "${WSL_INTEROP:-}" ]]; then
    echo 2
  else
    echo 1
  fi
}

current_utc_timestamp() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

read_embedded_assignment() {
  local file="$1"
  local key="$2"
  if [[ ! -f "$file" ]]; then
    return 0
  fi
  if ! pick_any_python_bin; then
    return 0
  fi
  "$PYTHON_BIN" - <<PY
from pathlib import Path

text = Path("$file").read_text(encoding="utf-8", errors="replace")
target = "${key}"
for raw_line in text.splitlines():
    line = raw_line.strip()
    if "=" not in line:
        continue
    name, value = line.split("=", 1)
    if name.strip() != target:
        continue
    resolved = value.strip().strip('"').strip("'")
    if resolved:
        print(resolved)
    break
PY
}

read_source_build_info_field() {
  local key="$1"
  if [[ ! -f "$REPO_ROOT/BUILD_INFO.json" ]]; then
    return 0
  fi
  if ! pick_any_python_bin; then
    return 0
  fi
  "$PYTHON_BIN" - <<PY
from pathlib import Path
import json

payload = json.loads(Path("$REPO_ROOT/BUILD_INFO.json").read_text(encoding="utf-8", errors="replace"))
value = payload.get("${key}") if isinstance(payload, dict) else None
if value not in (None, ""):
    print(str(value).strip())
PY
}

resolve_install_version() {
  if [[ -n "${CCB_BUILD_VERSION:-}" ]]; then
    echo "$CCB_BUILD_VERSION"
    return
  fi
  local build_info_version
  build_info_version="$(read_source_build_info_field "version")"
  if [[ -n "$build_info_version" ]]; then
    echo "$build_info_version"
    return
  fi
  if [[ -f "$REPO_ROOT/VERSION" ]]; then
    tr -d '[:space:]' < "$REPO_ROOT/VERSION"
    return
  fi
  read_embedded_assignment "$REPO_ROOT/ccb" "VERSION"
}

resolve_source_kind() {
  if [[ -n "${CCB_SOURCE_KIND:-}" ]]; then
    echo "$CCB_SOURCE_KIND"
    return
  fi
  local build_info_source_kind
  build_info_source_kind="$(read_source_build_info_field "source_kind")"
  if [[ -n "$build_info_source_kind" ]]; then
    echo "$build_info_source_kind"
    return
  fi
  if [[ -d "$REPO_ROOT/.git" ]]; then
    echo "source"
  else
    echo "release"
  fi
}

resolve_build_channel() {
  if [[ -n "${CCB_BUILD_CHANNEL:-}" ]]; then
    echo "$CCB_BUILD_CHANNEL"
    return
  fi
  local build_info_channel
  build_info_channel="$(read_source_build_info_field "channel")"
  if [[ -n "$build_info_channel" ]]; then
    echo "$build_info_channel"
    return
  fi
  local source_kind
  source_kind="$(resolve_source_kind)"
  if [[ "$source_kind" == "source" ]]; then
    echo "dev"
  else
    echo "stable"
  fi
}

resolve_install_mode() {
  local source_kind
  source_kind="$(resolve_source_kind)"
  if [[ "$source_kind" == "source" ]]; then
    echo "source"
  else
    echo "release"
  fi
}

install_uses_live_source() {
  [[ "$(resolve_install_mode)" == "source" ]]
}

managed_venv_path() {
  echo "$INSTALL_PREFIX/.venv"
}

managed_venv_python() {
  echo "$(managed_venv_path)/bin/python"
}

use_managed_venv() {
  local requested="${CCB_USE_MANAGED_VENV:-auto}"
  if install_uses_live_source; then
    return 1
  fi
  case "$requested" in
    1|true|yes|on) return 0 ;;
    0|false|no|off) return 1 ;;
  esac
  [[ "$(resolve_install_mode)" == "release" && "$(detect_platform)" == "macos" ]]
}

resolve_live_source_root() {
  local root="${CCB_SOURCE_ROOT:-$REPO_ROOT}"
  echo "$root"
}

resolve_install_asset_root() {
  if install_uses_live_source; then
    resolve_live_source_root
  else
    echo "$INSTALL_PREFIX"
  fi
}

resolve_inherit_skills_root() {
  local asset_root
  asset_root="$(resolve_install_asset_root)"
  echo "$asset_root/inherit_skills"
}

looks_like_ccb_codex_home() {
  local path="$1"
  [[ "$path" == */.ccb/agents/*/provider-state/codex/home ]]
}

resolve_codex_source_home() {
  local raw="${CODEX_HOME:-}"
  if [[ -n "$raw" ]]; then
    if ! looks_like_ccb_codex_home "$raw"; then
      echo "$raw"
      return
    fi
  fi
  echo "$HOME/.codex"
}

read_simple_json_string_field() {
  local file="$1"
  local key="$2"
  if [[ ! -f "$file" ]]; then
    return 0
  fi
  grep -o "\"${key}\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" "$file" 2>/dev/null | head -1 | sed -E "s/.*:[[:space:]]*\"([^\"]*)\"/\1/"
}

json_string_literal() {
  local value="$1"
  if ! pick_any_python_bin; then
    echo "ERROR: python required to encode install metadata" >&2
    exit 1
  fi
  JSON_LITERAL_VALUE="$value" "$PYTHON_BIN" - <<'PY'
import json
import os

print(json.dumps(os.environ.get("JSON_LITERAL_VALUE", ""), ensure_ascii=True))
PY
}

read_installed_version() {
  if [[ -f "$INSTALL_PREFIX/VERSION" ]]; then
    tr -d '[:space:]' < "$INSTALL_PREFIX/VERSION"
    return
  fi
  local build_info_version
  build_info_version="$(read_simple_json_string_field "$INSTALL_PREFIX/BUILD_INFO.json" "version")"
  if [[ -n "$build_info_version" ]]; then
    echo "$build_info_version"
    return
  fi
  if [[ -f "$INSTALL_PREFIX/ccb.py" ]]; then
    sed -n 's/^VERSION[[:space:]]*=[[:space:]]*"\(.*\)"/\1/p' "$INSTALL_PREFIX/ccb.py" | head -1
  fi
}

version_major() {
  local version_text="${1:-}"
  if [[ "$version_text" =~ ^([0-9]+)(\..*)?$ ]]; then
    echo "${BASH_REMATCH[1]}"
  fi
}

require_major_upgrade_confirmation() {
  local target_version existing_version target_major existing_major
  target_version="$(resolve_install_version)"
  existing_version="$(read_installed_version)"

  if [[ -z "$target_version" || -z "$existing_version" ]]; then
    return 0
  fi

  target_major="$(version_major "$target_version")"
  existing_major="$(version_major "$existing_version")"
  if [[ -z "$target_major" || -z "$existing_major" ]]; then
    return 0
  fi

  if (( target_major < 6 || existing_major >= 6 )); then
    return 0
  fi

  if [[ "${CCB_CONFIRM_MAJOR_UPGRADE:-}" == "1" || "${CCB_INSTALL_ASSUME_YES:-}" == "1" ]]; then
    return 0
  fi

  echo
  echo "================================================================"
  echo "WARN: Major upgrade confirmation required"
  echo "================================================================"
  echo "Detected existing install : v$existing_version"
  echo "Incoming install version  : v$target_version"
  echo
  echo "CCB v6 replaces the old source-era update path and rebuilds runtime behavior."
  echo "To avoid accidental upgrades, this install stops until you confirm explicitly."
  echo
  echo "Continue options:"
  echo "  1. Interactive shell: rerun and answer the prompt"
  echo "  2. Non-interactive : CCB_CONFIRM_MAJOR_UPGRADE=1 ccb update"
  echo "  3. Direct install  : CCB_CONFIRM_MAJOR_UPGRADE=1 ./install.sh install"
  echo "================================================================"

  if [[ ! -t 0 ]]; then
    echo "ERROR: Aborting major upgrade in non-interactive mode without confirmation."
    return 1
  fi

  local reply
  read -r -p "Confirm replacing the existing pre-v6 install with CCB v${target_version}? (y/N): " reply
  case "$reply" in
    y|Y|yes|YES)
      return 0
      ;;
    *)
      echo "Installation cancelled"
      return 1
      ;;
  esac
}

print_install_identity_summary() {
  local install_mode source_kind channel version
  install_mode="$(resolve_install_mode)"
  source_kind="$(resolve_source_kind)"
  channel="$(resolve_build_channel)"
  version="$(resolve_install_version)"
  echo "   install_mode=$install_mode"
  echo "   source_kind=$source_kind"
  echo "   channel=$channel"
  if [[ -n "$version" ]]; then
    echo "   version=$version"
  fi
}

print_install_identity_notice() {
  local source_kind
  source_kind="$(resolve_source_kind)"
  case "$source_kind" in
    source)
      msg install_notice_source_title
      echo "   $(msg install_notice_source_body)"
      ;;
    preview)
      msg install_notice_preview_title
      echo "   $(msg install_notice_preview_body)"
      ;;
    *)
      msg install_notice_release
      ;;
  esac
}

write_install_metadata() {
  local version commit date build_time installed_at platform_name arch_name channel source_kind install_mode
  local install_user_id install_user_name sudo_user root_install_json install_user_id_json
  version="$(resolve_install_version)"
  commit="$(read_embedded_assignment "$INSTALL_PREFIX/ccb.py" "GIT_COMMIT")"
  date="$(read_embedded_assignment "$INSTALL_PREFIX/ccb.py" "GIT_DATE")"
  if [[ -z "$commit" ]]; then
    commit="$(read_source_build_info_field "commit")"
  fi
  if [[ -z "$date" ]]; then
    date="$(read_source_build_info_field "date")"
  fi
  build_time="${CCB_BUILD_TIME:-$(read_source_build_info_field "build_time")}"
  if [[ -z "$build_time" ]]; then
    build_time="$(current_utc_timestamp)"
  fi
  installed_at="$(current_utc_timestamp)"
  platform_name="${CCB_BUILD_PLATFORM:-$(read_source_build_info_field "platform")}"
  if [[ -z "$platform_name" ]]; then
    platform_name="$(detect_platform)"
  fi
  arch_name="${CCB_BUILD_ARCH:-$(read_source_build_info_field "arch")}"
  if [[ -z "$arch_name" ]]; then
    arch_name="$(uname -m 2>/dev/null || echo unknown)"
  fi
  source_kind="$(resolve_source_kind)"
  channel="$(resolve_build_channel)"
  install_mode="$(resolve_install_mode)"
  install_user_id="$(current_effective_uid)"
  install_user_name="$(current_effective_user_name)"
  sudo_user="${SUDO_USER:-}"
  if [[ "$install_user_id" =~ ^[0-9]+$ ]]; then
    install_user_id_json="$install_user_id"
  else
    install_user_id_json="None"
  fi
  if [[ "$install_user_id" == "0" ]]; then
    root_install_json="True"
  else
    root_install_json="False"
  fi

  if ! pick_any_python_bin; then
    echo "WARN: python required to write VERSION/BUILD_INFO metadata"
    return
  fi
  local version_json commit_json date_json build_time_json platform_json arch_json channel_json source_kind_json install_mode_json installed_at_json
  local install_user_name_json sudo_user_json
  version_json="$(json_string_literal "$version")"
  commit_json="$(json_string_literal "$commit")"
  date_json="$(json_string_literal "$date")"
  build_time_json="$(json_string_literal "$build_time")"
  platform_json="$(json_string_literal "$platform_name")"
  arch_json="$(json_string_literal "$arch_name")"
  channel_json="$(json_string_literal "$channel")"
  source_kind_json="$(json_string_literal "$source_kind")"
  install_mode_json="$(json_string_literal "$install_mode")"
  installed_at_json="$(json_string_literal "$installed_at")"
  install_user_name_json="$(json_string_literal "$install_user_name")"
  sudo_user_json="$(json_string_literal "$sudo_user")"

  "$PYTHON_BIN" - <<PY
from pathlib import Path
import json

install_prefix = Path("$INSTALL_PREFIX")
payload = {
    "version": ${version_json},
    "commit": ${commit_json},
    "date": ${date_json},
    "build_time": ${build_time_json},
    "platform": ${platform_json},
    "arch": ${arch_json},
    "channel": ${channel_json},
    "source_kind": ${source_kind_json},
    "install_mode": ${install_mode_json},
    "installed_at": ${installed_at_json},
    "install_user_id": ${install_user_id_json},
    "install_user_name": ${install_user_name_json},
    "root_install": ${root_install_json},
    "sudo_user": ${sudo_user_json},
}

version_text = str(payload["version"] or "").strip()
if version_text:
    (install_prefix / "VERSION").write_text(version_text + "\\n", encoding="utf-8")
(install_prefix / "BUILD_INFO.json").write_text(
    json.dumps(payload, ensure_ascii=True, indent=2) + "\\n",
    encoding="utf-8",
)
PY
}

check_wsl_compatibility() {
  if is_wsl; then
    local ver
    ver="$(get_wsl_version)"
    echo "OK: Detected WSL $ver environment"
  fi
}

confirm_backend_env_wsl() {
  if ! is_wsl; then
    return
  fi

  if [[ "${CCB_INSTALL_ASSUME_YES:-}" == "1" ]]; then
    return
  fi

  if [[ ! -t 0 ]]; then
    echo "ERROR: Installing in WSL but detected non-interactive terminal; aborted to avoid env mismatch."
    echo "   If you confirm codex/gemini will be installed and run in WSL:"
    echo "   Re-run: CCB_INSTALL_ASSUME_YES=1 ./install.sh install"
    exit 1
  fi

  echo
  echo "================================================================"
  echo "WARN: Detected WSL environment"
  echo "================================================================"
  echo "ccb/ask/ping/pend must run in the same environment as codex/gemini."
  echo
  echo "Please confirm: you will install and run codex/gemini in WSL (not Windows native)."
  echo "If you plan to run codex/gemini in Windows native, exit and run on Windows side:"
  echo "   powershell -ExecutionPolicy Bypass -File .\\install.ps1 install"
  echo "================================================================"
  echo
  read -r -p "Confirm continue installing in WSL? (y/N): " reply
  case "$reply" in
    y|Y|yes|YES) ;;
    *) echo "Installation cancelled"; exit 1 ;;
  esac
}

print_tmux_install_hint() {
  local platform
  platform="$(detect_platform)"
  case "$platform" in
    macos)
      if command -v brew >/dev/null 2>&1; then
        echo "   macOS: Run 'brew install tmux'"
      else
        echo "   macOS: Homebrew not detected, install from https://brew.sh then run 'brew install tmux'"
      fi
      ;;
    linux)
      if command -v apt-get >/dev/null 2>&1; then
        echo "   Debian/Ubuntu: sudo apt-get update && sudo apt-get install -y tmux"
      elif command -v dnf >/dev/null 2>&1; then
        echo "   Fedora/CentOS/RHEL: sudo dnf install -y tmux"
      elif command -v yum >/dev/null 2>&1; then
        echo "   CentOS/RHEL: sudo yum install -y tmux"
      elif command -v pacman >/dev/null 2>&1; then
        echo "   Arch/Manjaro: sudo pacman -S tmux"
      elif command -v apk >/dev/null 2>&1; then
        echo "   Alpine: sudo apk add tmux"
      elif command -v zypper >/dev/null 2>&1; then
        echo "   openSUSE: sudo zypper install -y tmux"
      else
        echo "   Linux: Please use your distro's package manager to install tmux"
      fi
      ;;
    *)
      echo "   See https://github.com/tmux/tmux/wiki/Installing for tmux installation"
      ;;
  esac
}

require_terminal_backend() {
  local platform
  platform="$(detect_platform)"

  if [[ "$platform" == "macos" ]] && ! command -v brew >/dev/null 2>&1; then
    echo "WARN: Homebrew not found on macOS. Install from https://brew.sh before installing tmux and other dependencies."
  fi

  if [[ -n "${TMUX:-}" ]]; then
    echo "OK: Detected tmux environment"
    return
  fi

  if command -v tmux >/dev/null 2>&1; then
    echo "OK: Detected tmux"
    return
  fi

  echo "ERROR: Missing dependency: tmux"

  if [[ "$platform" == "macos" ]]; then
    echo
    echo "NOTE: macOS user recommended options:"
    echo "   - Install tmux: brew install tmux"
  fi

  print_tmux_install_hint
  exit 1
}

copy_project() {
  local staging
  staging="$(mktemp -d)"
  trap 'rm -rf "$staging"' EXIT

  if command -v rsync >/dev/null 2>&1; then
    rsync -a \
      --exclude '.git/' \
      --exclude '__pycache__/' \
      --exclude '.pytest_cache/' \
      --exclude '.mypy_cache/' \
      --exclude '.venv/' \
      --exclude 'target/' \
      --exclude 'lib/web/' \
      --exclude 'bin/ccb-web' \
      "$REPO_ROOT"/ "$staging"/
  else
    tar -C "$REPO_ROOT" \
      --exclude '.git' \
      --exclude '__pycache__' \
      --exclude '.pytest_cache' \
      --exclude '.mypy_cache' \
      --exclude '.venv' \
      --exclude 'target' \
      --exclude 'lib/web' \
      --exclude 'bin/ccb-web' \
      -cf - . | tar -C "$staging" -xf -
  fi

  rm -rf "$INSTALL_PREFIX"
  mkdir -p "$(dirname "$INSTALL_PREFIX")"
  mv "$staging" "$INSTALL_PREFIX"
  trap - EXIT

  # Update GIT_COMMIT and GIT_DATE in ccb file
  local git_commit="" git_date=""

  # Method 1: From git repo or git worktree
  if command -v git >/dev/null 2>&1 && git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git_commit=$(git -C "$REPO_ROOT" log -1 --format='%h' 2>/dev/null || echo "")
    git_date=$(git -C "$REPO_ROOT" log -1 --format='%cs' 2>/dev/null || echo "")
  fi

  # Method 2: From source BUILD_INFO.json (release artifact source of truth)
  if [[ -z "$git_commit" ]]; then
    git_commit="$(read_source_build_info_field "commit")"
    git_date="$(read_source_build_info_field "date")"
  fi

  # Method 3: From environment variables (set by ccb update)
  if [[ -z "$git_commit" && -n "${CCB_GIT_COMMIT:-}" ]]; then
    git_commit="$CCB_GIT_COMMIT"
    git_date="${CCB_GIT_DATE:-}"
  fi

  # Method 4: From embedded package metadata
  if [[ -z "$git_commit" && -f "$INSTALL_PREFIX/ccb.py" ]]; then
    git_commit=$(sed -n 's/^GIT_COMMIT = "\(.*\)"/\1/p' "$INSTALL_PREFIX/ccb.py" | head -1)
    git_date=$(sed -n 's/^GIT_DATE = "\(.*\)"/\1/p' "$INSTALL_PREFIX/ccb.py" | head -1)
  fi

  # Method 5: From GitHub API (fallback)
  if [[ -z "$git_commit" ]] && command -v curl >/dev/null 2>&1; then
    local api_response
    api_response=$(curl -fsSL "https://api.github.com/repos/bfly123/claude_code_bridge/commits/main" 2>/dev/null || echo "")
    if [[ -n "$api_response" ]]; then
      git_commit=$(echo "$api_response" | grep -o '"sha": "[^"]*"' | head -1 | cut -d'"' -f4 | cut -c1-7)
      git_date=$(echo "$api_response" | grep -o '"date": "[^"]*"' | head -1 | cut -d'"' -f4 | cut -c1-10)
    fi
  fi

  if [[ -n "$git_commit" && -f "$INSTALL_PREFIX/ccb.py" ]]; then
    sed -i.bak "s/^GIT_COMMIT = .*/GIT_COMMIT = \"$git_commit\"/" "$INSTALL_PREFIX/ccb.py"
    sed -i.bak "s/^GIT_DATE = .*/GIT_DATE = \"$git_date\"/" "$INSTALL_PREFIX/ccb.py"
    rm -f "$INSTALL_PREFIX/ccb.py.bak"
  fi
}

prepare_install_tree() {
  if install_uses_live_source; then
    local live_root
    live_root="$(resolve_live_source_root)"
    if [[ ! -f "$live_root/ccb" ]]; then
      echo "ERROR: Live source root missing ccb entrypoint: $live_root"
      exit 1
    fi
    echo "Using live source tree: $live_root"
    return 0
  fi
  copy_project
}

install_managed_venv() {
  if ! use_managed_venv; then
    return 0
  fi
  if ! pick_python_bin; then
    echo "ERROR: Missing dependency: python (3.10+ required)" >&2
    echo "   Please install Python 3.10+ and ensure it is on PATH, then retry." >&2
    exit 1
  fi
  local venv_dir venv_python
  venv_dir="$(managed_venv_path)"
  venv_python="$(managed_venv_python)"
  echo "Creating managed Python venv: $venv_dir"
  rm -rf "$venv_dir"
  if ! "$PYTHON_BIN" -m venv "$venv_dir"; then
    echo "ERROR: Failed to create managed Python venv at $venv_dir"
    case "$(detect_platform)" in
      macos)
        echo "   macOS: install or repair Homebrew Python: brew install python"
        ;;
      linux)
        echo "   Debian/Ubuntu: sudo apt-get install -y python3-venv"
        ;;
    esac
    exit 1
  fi
  if [[ ! -x "$venv_python" ]]; then
    echo "ERROR: Managed venv Python not executable: $venv_python"
    exit 1
  fi
  if ! "$venv_python" -m pip install --upgrade pip >/dev/null 2>&1; then
    echo "WARN: unable to upgrade pip inside managed venv; continuing"
  fi
  install_tomli_for_python "$venv_python"
  install_watchdog_for_python "$venv_python"
  echo "OK: Managed Python venv ready"
}

write_live_source_wrapper() {
  local target="$1"
  local wrapper_path="$2"
  local quoted_target
  printf -v quoted_target '%q' "$target"
  cat > "$wrapper_path" <<EOF
#!/usr/bin/env bash
exec ${quoted_target} "\$@"
EOF
  chmod +x "$wrapper_path" 2>/dev/null || true
}

clear_installed_path() {
  local path="$1"
  if [[ -L "$path" || -f "$path" ]]; then
    rm -f "$path"
    return 0
  fi
  if [[ -d "$path" ]]; then
    rm -rf "$path"
  fi
}

install_owned_file() {
  local source_path="$1"
  local destination_path="$2"
  local file_mode="${3:-}"

  if [[ ! -f "$source_path" ]]; then
    echo "WARN: File not found $source_path, skipping"
    return 1
  fi

  mkdir -p "$(dirname "$destination_path")"
  clear_installed_path "$destination_path"

  if install_uses_live_source; then
    if ! ln -s "$source_path" "$destination_path" 2>/dev/null; then
      echo "ERROR: source dev install requires symlink support for $destination_path"
      exit 1
    fi
    return 0
  fi

  cp -f "$source_path" "$destination_path"
  if [[ -n "$file_mode" ]]; then
    chmod "$file_mode" "$destination_path" 2>/dev/null || true
  fi
}

install_owned_directory() {
  local source_path="$1"
  local destination_path="$2"

  if [[ ! -d "$source_path" ]]; then
    echo "WARN: Directory not found $source_path, skipping"
    return 1
  fi

  mkdir -p "$(dirname "$destination_path")"
  clear_installed_path "$destination_path"

  if install_uses_live_source; then
    if ! ln -s "$source_path" "$destination_path" 2>/dev/null; then
      echo "ERROR: source dev install requires symlink support for $destination_path"
      exit 1
    fi
    return 0
  fi

  cp -rf "$source_path" "$destination_path"
}

install_owned_executable() {
  local source_path="$1"
  local destination_path="$2"

  if [[ ! -f "$source_path" ]]; then
    echo "WARN: Script not found $source_path, skipping"
    return 1
  fi

  mkdir -p "$(dirname "$destination_path")"
  chmod +x "$source_path" 2>/dev/null || true
  clear_installed_path "$destination_path"

  if ln -s "$source_path" "$destination_path" 2>/dev/null; then
    return 0
  fi

  if install_uses_live_source; then
    write_live_source_wrapper "$source_path" "$destination_path"
    return 0
  fi

  cp -f "$source_path" "$destination_path"
  chmod +x "$destination_path" 2>/dev/null || true
}

write_python_entrypoint_wrapper() {
  local python_path="$1"
  local source_path="$2"
  local destination_path="$3"
  local absolute_source="$source_path"
  if [[ "$absolute_source" != /* ]]; then
    absolute_source="$(cd "$(dirname "$source_path")" && pwd)/$(basename "$source_path")"
  fi
  mkdir -p "$(dirname "$destination_path")"
  clear_installed_path "$destination_path"
  cat > "$destination_path" <<EOF
#!/usr/bin/env bash
if [[ "\${TERM:-}" == "xterm-ghostty" ]]; then
  export TERM=xterm-256color
fi
exec "$python_path" "$absolute_source" "\$@"
EOF
  chmod +x "$destination_path" 2>/dev/null || true
}

write_managed_venv_python_wrapper() {
  local source_path="$1"
  local destination_path="$2"
  write_python_entrypoint_wrapper "$(managed_venv_python)" "$source_path" "$destination_path"
}

is_python_entrypoint() {
  local source_path="$1"
  local first_line=""
  if [[ -f "$source_path" ]]; then
    IFS= read -r first_line < "$source_path" || true
  fi
  [[ "$first_line" == '#!'*python* ]]
}

# Detects the new bash-launcher form (#!/usr/bin/env bash + exec ".../_ccb-python" ...)
# that wraps a sibling .py body. These are present at the top-level (ccb -> ccb.py)
# and under bin/ (ask -> bin/ask.py, etc).
is_ccb_launcher_entrypoint() {
  local source_path="$1"
  [[ -f "$source_path" ]] || return 1
  local first_line=""
  IFS= read -r first_line < "$source_path" || true
  [[ "$first_line" == '#!'*bash* ]] || return 1
  grep -q '_ccb-python' "$source_path" 2>/dev/null
}

# Resolve the .py body name a launcher targets, e.g. "ask" for bin/ask -> ask.py.
ccb_launcher_target_name() {
  local source_path="$1"
  basename "$source_path"
}

write_ccb_launcher_release_wrapper() {
  local source_path="$1"
  local destination_path="$2"
  local target_name
  target_name="$(ccb_launcher_target_name "$source_path")"
  local body_name="$target_name.py"
  local launcher_path body_path
  if [[ "$source_path" == */bin/* ]]; then
    launcher_path="$INSTALL_PREFIX/bin/_ccb-python"
    body_path="$INSTALL_PREFIX/bin/$body_name"
  else
    launcher_path="$INSTALL_PREFIX/bin/_ccb-python"
    body_path="$INSTALL_PREFIX/$body_name"
  fi
  mkdir -p "$(dirname "$destination_path")"
  clear_installed_path "$destination_path"
  cat > "$destination_path" <<EOF
#!/usr/bin/env bash
if [[ "\${TERM:-}" == "xterm-ghostty" ]]; then
  export TERM=xterm-256color
fi
exec "$launcher_path" "$body_path" "\$@"
EOF
  chmod +x "$destination_path" 2>/dev/null || true
}

install_entrypoint_executable() {
  local source_path="$1"
  local destination_path="$2"

  if [[ ! -f "$source_path" ]]; then
    echo "WARN: Script not found $source_path, skipping"
    return 1
  fi

  local absolute_source="$source_path"
  if [[ "$absolute_source" != /* ]]; then
    absolute_source="$(cd "$(dirname "$source_path")" && pwd)/$(basename "$source_path")"
  fi

  # New-form launchers (bash, exec _ccb-python <name>.py): under live source we
  # symlink straight back so the launcher resolves the source tree itself; under
  # release install we emit a wrapper pinned to INSTALL_PREFIX paths.
  if is_ccb_launcher_entrypoint "$absolute_source"; then
    if install_uses_live_source; then
      install_owned_executable "$source_path" "$destination_path"
      return 0
    fi
    write_ccb_launcher_release_wrapper "$absolute_source" "$destination_path"
    return 0
  fi

  if ! is_python_entrypoint "$absolute_source"; then
    install_owned_executable "$source_path" "$destination_path"
    return 0
  fi
  if use_managed_venv && [[ "$absolute_source" == "$INSTALL_PREFIX/"* ]]; then
    write_managed_venv_python_wrapper "$absolute_source" "$destination_path"
    return 0
  fi

  if install_uses_live_source; then
    local python_path
    if ! python_path="$(selected_python_executable)"; then
      exit 1
    fi
    write_python_entrypoint_wrapper "$python_path" "$absolute_source" "$destination_path"
    return 0
  fi

  install_owned_executable "$source_path" "$destination_path"
}

is_sidebar_wrapper() {
  local path="$1"
  [[ -f "$path" ]] && grep -q 'CCB_AGENT_SIDEBAR_WRAPPER' "$path" 2>/dev/null
}

sidebar_helper_runs_on_this_host() {
  local binary="$1"
  [[ -x "$binary" ]] || return 1
  "$binary" --help >/dev/null 2>&1
  case "$?" in
    0|2) return 0 ;;
    *) return 1 ;;
  esac
}

require_sidebar_rust_toolchain() {
  local missing=()
  if ! command -v cargo >/dev/null 2>&1; then
    missing+=(cargo)
  fi
  if ! command -v rustc >/dev/null 2>&1; then
    missing+=(rustc)
  fi
  if [[ ${#missing[@]} -eq 0 ]]; then
    return 0
  fi

  echo "ERROR: Rust toolchain required to build ccb-agent-sidebar"
  echo "   Missing: ${missing[*]}"
  echo "   Sidebar panes require bin/ccb-agent-sidebar; install Rust or use a release package with a prebuilt helper."
  case "$(detect_platform)" in
    macos)
      echo "   macOS: brew install rust"
      ;;
    linux)
      echo "   Debian/Ubuntu: sudo apt-get install -y cargo rustc"
      ;;
  esac
  echo "   Rustup: https://rustup.rs/"
  exit 1
}

sidebar_helper_unavailable_error() {
  echo "ERROR: ccb-agent-sidebar binary not available"
  echo "   Sidebar panes will not work without a runnable helper."
  echo "   Install Rust and re-run install.sh, or install an official release package with a prebuilt helper."
  exit 1
}

install_prebuilt_sidebar_helper() {
  local binary="$1"
  local target="$2"
  if ! sidebar_helper_runs_on_this_host "$binary"; then
    return 1
  fi
  cp -f "$binary" "$target"
  chmod +x "$target" 2>/dev/null || true
  if ! sidebar_helper_runs_on_this_host "$target"; then
    rm -f "$target"
    return 1
  fi
  echo "Installed prebuilt ccb-agent-sidebar"
  return 0
}

build_sidebar_helper_if_possible() {
  local asset_root crate_dir binary target
  asset_root="$(resolve_install_asset_root)"
  crate_dir="$asset_root/tools/ccb-agent-sidebar"
  binary="$crate_dir/target/release/ccb-agent-sidebar"
  target="$asset_root/bin/ccb-agent-sidebar"

  if [[ ! -f "$crate_dir/Cargo.toml" ]]; then
    if [[ -x "$target" ]] && ! is_sidebar_wrapper "$target" && sidebar_helper_runs_on_this_host "$target"; then
      return
    fi
    sidebar_helper_unavailable_error
  fi

  if install_uses_live_source; then
    if [[ -x "$binary" ]] && sidebar_helper_runs_on_this_host "$binary"; then
      return
    fi
    require_sidebar_rust_toolchain
    echo "Building ccb-agent-sidebar..."
    if cargo build --release --manifest-path "$crate_dir/Cargo.toml" >/dev/null 2>&1 && [[ -x "$binary" ]]; then
      echo "Built ccb-agent-sidebar"
      return
    fi
    sidebar_helper_unavailable_error
  fi

  mkdir -p "$asset_root/bin"
  if [[ -x "$target" ]] && ! is_sidebar_wrapper "$target" && sidebar_helper_runs_on_this_host "$target"; then
    return
  fi

  if [[ -x "$binary" ]] && install_prebuilt_sidebar_helper "$binary" "$target"; then
    return
  fi

  require_sidebar_rust_toolchain
  echo "Building ccb-agent-sidebar..."
  if cargo build --release --manifest-path "$crate_dir/Cargo.toml" >/dev/null 2>&1 && [[ -x "$binary" ]]; then
    cp -f "$binary" "$target"
    chmod +x "$target" 2>/dev/null || true
    if sidebar_helper_runs_on_this_host "$target"; then
      echo "Built ccb-agent-sidebar"
      return
    fi
    rm -f "$target"
  fi

  sidebar_helper_unavailable_error
}

install_bin_links() {
  mkdir -p "$BIN_DIR"
  local target_root
  target_root="$(resolve_install_asset_root)"

  for path in "${SCRIPTS_TO_LINK[@]}"; do
    local name
    name="$(basename "$path")"
    local target_path="$target_root/$path"
    if ! install_entrypoint_executable "$target_path" "$BIN_DIR/$name"; then
      case "$path" in
        bin/build-ccb-agent-sidebar|bin/ccb-agent-sidebar)
          ;;
        *)
          return 1
          ;;
      esac
    fi
  done

  for legacy in "${LEGACY_SCRIPTS[@]}"; do
    rm -f "$BIN_DIR/$legacy"
  done

  echo "Created executable links in $BIN_DIR"
}

verify_installed_entrypoints() {
  if ! "$BIN_DIR/ccb" --print-version >/dev/null 2>&1; then
    echo "ERROR: installed ccb entrypoint failed runtime smoke check"
    echo "   Path: $BIN_DIR/ccb"
    exit 1
  fi
  if ! "$BIN_DIR/ask" --help >/dev/null 2>&1; then
    echo "ERROR: installed ask entrypoint failed runtime smoke check"
    echo "   Path: $BIN_DIR/ask"
    exit 1
  fi
  echo "OK: Installed entrypoints passed runtime smoke check"
}

ensure_path_configured() {
  # Check if BIN_DIR is already in PATH
  if [[ ":$PATH:" == *":$BIN_DIR:"* ]]; then
    return
  fi

  local shell_rc=""
  local current_shell
  current_shell="$(basename "${SHELL:-/bin/bash}")"

  case "$current_shell" in
    zsh)  shell_rc="$HOME/.zshrc" ;;
    bash)
      if [[ -f "$HOME/.bash_profile" ]]; then
        shell_rc="$HOME/.bash_profile"
      else
        shell_rc="$HOME/.bashrc"
      fi
      ;;
    *)    shell_rc="$HOME/.profile" ;;
  esac

  local path_line="export PATH=\"${BIN_DIR}:\$PATH\""

  # Check if already configured in shell rc
  if [[ -f "$shell_rc" ]] && grep -qF "$BIN_DIR" "$shell_rc" 2>/dev/null; then
    echo "PATH already configured in $shell_rc (restart terminal to apply)"
    return
  fi

  # Add to shell rc
  echo "" >> "$shell_rc"
  echo "# Added by ccb installer" >> "$shell_rc"
  echo "$path_line" >> "$shell_rc"
  echo "OK: Added $BIN_DIR to PATH in $shell_rc"
  echo "   Run: source $shell_rc  (or restart terminal)"
}

install_claude_commands() {
  local claude_dir
  claude_dir="$(detect_claude_dir)"
  mkdir -p "$claude_dir"
  local asset_root
  asset_root="$(resolve_install_asset_root)"

  # Clean up obsolete CCB commands (replaced by unified ask/ping/pend)
  local obsolete_cmds="bask.md bpend.md bping.md cask.md cpend.md cping.md dask.md dpend.md dping.md gask.md gpend.md gping.md hask.md hpend.md hping.md lask.md lpend.md lping.md oask.md opend.md oping.md qask.md qpend.md qping.md"
  for obs_cmd in $obsolete_cmds; do
    if [[ -f "$claude_dir/$obs_cmd" ]]; then
      rm -f "$claude_dir/$obs_cmd"
      echo "  Removed obsolete command: $obs_cmd"
    fi
  done

  for doc in "${CLAUDE_MARKDOWN[@]+"${CLAUDE_MARKDOWN[@]}"}"; do
    install_owned_file "$asset_root/commands/$doc" "$claude_dir/$doc" 0644
  done

  echo "Updated Claude commands directory: $claude_dir"
}

install_skill_entry() {
  local skill_dir="$1"
  local destination_dir="$2"
  local src_skill_md=""

  if [[ -f "$skill_dir/SKILL.md.bash" ]]; then
    src_skill_md="$skill_dir/SKILL.md.bash"
  elif [[ -f "$skill_dir/SKILL.md" ]]; then
    src_skill_md="$skill_dir/SKILL.md"
  else
    return 1
  fi

  clear_installed_path "$destination_dir"
  mkdir -p "$destination_dir"
  cp -f "$src_skill_md" "$destination_dir/SKILL.md"
  chmod 0644 "$destination_dir/SKILL.md" 2>/dev/null || true

  local child
  for child in "$skill_dir"/*; do
    [[ -e "$child" ]] || continue
    local child_name
    child_name="$(basename "$child")"
    if [[ "$child_name" == "SKILL.md" || "$child_name" == "SKILL.md.bash" ]]; then
      continue
    fi
    if [[ -d "$child" ]]; then
      clear_installed_path "$destination_dir/$child_name"
      cp -rf "$child" "$destination_dir/$child_name"
    elif [[ -f "$child" ]]; then
      cp -f "$child" "$destination_dir/$child_name"
      chmod 0644 "$destination_dir/$child_name" 2>/dev/null || true
    fi
  done
}

install_claude_skills() {
  local skills_root
  skills_root="$(resolve_inherit_skills_root)"
  local skills_src="$skills_root/claude_skills"
  local skills_dst="$HOME/.claude/skills"

  if [[ ! -d "$skills_src" ]]; then
    return
  fi

  mkdir -p "$skills_dst"

  rm -rf "$skills_dst/ccb_config"

  # Clean up legacy wrapper/provider skills silently; only current inherited
  # skills should appear in install output.
  local legacy_skills="ccb-config bask bpend bping cask cpend cping dask dpend dping gask gpend gping hask hpend hping lask lpend lping mounted oask opend oping qask qpend qping auto ping pend autonew all-plan docs tp tr file-op review continue"
  for legacy_skill in $legacy_skills; do
    rm -rf "$skills_dst/$legacy_skill"
  done

  echo "Installing inherited Claude skills (bash SKILL.md template)..."
  for skill_dir in "$skills_src"/*/; do
    [[ -d "$skill_dir" ]] || continue
    local skill_name
    skill_name=$(basename "$skill_dir")

    if [[ ! -f "$skill_dir/SKILL.md.bash" && ! -f "$skill_dir/SKILL.md" ]]; then
      continue
    fi

    local dst_dir="$skills_dst/$skill_name"
    install_skill_entry "${skill_dir%/}" "$dst_dir"

    echo "  Updated skill: $skill_name"
  done

  echo "Updated Claude skills directory: $skills_dst"
}

install_codex_skills() {
  local skills_root
  skills_root="$(resolve_inherit_skills_root)"
  local skills_src="$skills_root/codex_skills"
  local skills_dst
  skills_dst="$(resolve_codex_source_home)/skills"

  if [[ ! -d "$skills_src" ]]; then
    return
  fi

  mkdir -p "$skills_dst"

  rm -rf "$skills_dst/ccb_config"

  # Clean up legacy wrapper/provider skills silently; only current inherited
  # skills should appear in install output.
  local legacy_skills="ccb-config bask bpend bping cask cpend cping dask dpend dping gask gpend gping hask hpend hping lask lpend lping mounted oask opend oping qask qpend qping ping pend autonew all-plan file-op"
  for legacy_skill in $legacy_skills; do
    rm -rf "$skills_dst/$legacy_skill"
  done

  echo "Installing inherited Codex skills (bash SKILL.md template)..."
  for skill_dir in "$skills_src"/*/; do
    [[ -d "$skill_dir" ]] || continue
    local skill_name
    skill_name=$(basename "$skill_dir")

    if [[ ! -f "$skill_dir/SKILL.md.bash" && ! -f "$skill_dir/SKILL.md" ]]; then
      continue
    fi

    local dst_dir="$skills_dst/$skill_name"
    install_skill_entry "${skill_dir%/}" "$dst_dir"

    echo "  Updated Codex skill: $skill_name"
  done
  echo "Updated Codex skills directory: $skills_dst"
}

install_droid_skills() {
  local skills_root
  skills_root="$(resolve_inherit_skills_root)"
  local skills_src="$skills_root/droid_skills"
  local skills_dst="${FACTORY_HOME:-$HOME/.factory}/skills"

  if [[ ! -d "$skills_src" ]]; then
    return
  fi

  if ! command -v droid >/dev/null 2>&1; then
    return
  fi

  mkdir -p "$skills_dst"

  # Clean up legacy wrapper/provider skills silently; only current inherited
  # skills should appear in install output.
  local legacy_skills="bask bpend bping cask cpend cping dask dpend dping gask gpend gping hask hpend hping lask lpend lping mounted oask opend oping qask qpend qping ping pend autonew all-plan"
  for legacy_skill in $legacy_skills; do
    rm -rf "$skills_dst/$legacy_skill"
  done

  echo "Installing Droid/Factory ask skill..."
  for skill_dir in "$skills_src"/*/; do
    [[ -d "$skill_dir" ]] || continue
    local skill_name
    skill_name=$(basename "$skill_dir")
    [[ "$skill_name" == "ask" ]] || continue

    if [[ ! -f "$skill_dir/SKILL.md" ]]; then
      continue
    fi

    local dst_dir="$skills_dst/$skill_name"
    install_skill_entry "${skill_dir%/}" "$dst_dir"

    echo "  Updated Factory skill: $skill_name"
  done
  echo "Updated Factory skills directory: $skills_dst"
}

droid_command_with_timeout() {
  local python_runner="$1"
  local timeout_s="$2"
  shift 2
  "$python_runner" - "$timeout_s" "$@" <<'PY' >/dev/null 2>&1
from __future__ import annotations

import subprocess
import sys

try:
    timeout_s = float(sys.argv[1])
except Exception:
    timeout_s = 10.0
cmd = sys.argv[2:]
try:
    result = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=max(timeout_s, 0.1),
    )
except subprocess.TimeoutExpired:
    raise SystemExit(124)
except FileNotFoundError:
    raise SystemExit(127)
raise SystemExit(result.returncode)
PY
}

install_droid_delegation() {
  if [[ "${CCB_DROID_AUTOINSTALL:-1}" == "0" ]]; then
    return
  fi
  if ! command -v droid >/dev/null 2>&1; then
    return
  fi
  local py
  if ! py="$(selected_python_executable)"; then
    echo "WARN: Python 3.10+ required for Droid MCP setup; skipping"
    return
  fi
  local timeout_s="${CCB_DROID_AUTOINSTALL_TIMEOUT_S:-10}"
  local asset_root
  asset_root="$(resolve_install_asset_root)"
  local server="$asset_root/mcp/ccb-delegation/server.py"
  if [[ ! -f "$server" ]]; then
    echo "WARN: Droid MCP server not found at $server; skipping"
    return
  fi
  if [[ "${CCB_DROID_AUTOINSTALL_FORCE:-0}" == "1" ]]; then
    droid_command_with_timeout "$py" "$timeout_s" droid mcp remove ccb-delegation || true
  fi
  if droid_command_with_timeout "$py" "$timeout_s" droid mcp add ccb-delegation --type stdio "$py" "$server"; then
    echo "OK: Droid MCP delegation registered"
  else
    echo "WARN: Failed to register Droid MCP delegation within ${timeout_s}s (already registered, unavailable, or timed out)"
  fi
}

CCB_START_MARKER="<!-- CCB_CONFIG_START -->"
CCB_END_MARKER="<!-- CCB_CONFIG_END -->"
CCB_ROLES_START_MARKER="<!-- CCB_ROLES_START -->"
CCB_ROLES_END_MARKER="<!-- CCB_ROLES_END -->"
CCB_RUBRICS_START_MARKER="<!-- REVIEW_RUBRICS_START -->"
CCB_RUBRICS_END_MARKER="<!-- REVIEW_RUBRICS_END -->"
LEGACY_RULE_MARKER="## Codex 协作规则"

file_has_ccb_memory_marker() {
  local file_path="$1"

  grep -q "$CCB_START_MARKER" "$file_path" 2>/dev/null || \
    grep -q "$CCB_ROLES_START_MARKER" "$file_path" 2>/dev/null || \
    grep -q "$CCB_RUBRICS_START_MARKER" "$file_path" 2>/dev/null || \
    grep -q "<!-- CODEX_REVIEW_START -->" "$file_path" 2>/dev/null || \
    grep -q "<!-- GEMINI_INSPIRATION_START -->" "$file_path" 2>/dev/null
}

remove_ccb_owned_memory_file() {
  local file_path="$1"
  local label="$2"

  if [[ ! -f "$file_path" ]]; then
    return 0
  fi

  if file_has_ccb_memory_marker "$file_path"; then
    rm -f "$file_path"
    echo "Removed CCB-owned $label: $file_path"
  else
    echo "Preserved non-CCB $label: $file_path"
  fi
}

remove_codex_mcp() {
  local claude_config="$HOME/.claude.json"

  if [[ ! -f "$claude_config" ]]; then
    return
  fi

  if ! pick_python_bin; then
    echo "WARN: python required to detect MCP configuration"
    return
  fi

  local has_codex_mcp
  has_codex_mcp=$("$PYTHON_BIN" -c "
import json

try:
    with open('$claude_config', 'r', encoding='utf-8') as f:
        data = json.load(f)
    projects = data.get('projects', {}) if isinstance(data, dict) else {}
    found = False
    if isinstance(projects, dict):
        for _proj, cfg in projects.items():
            if not isinstance(cfg, dict):
                continue
            servers = cfg.get('mcpServers', {})
            if not isinstance(servers, dict):
                continue
            for name in list(servers.keys()):
                if 'codex' in str(name).lower():
                    found = True
                    break
            if found:
                break
    print('yes' if found else 'no')
except Exception:
    print('no')
" 2>/dev/null)

  if [[ "$has_codex_mcp" == "yes" ]]; then
    echo "WARN: Detected codex-related MCP configuration, removing to avoid conflicts..."
    "$PYTHON_BIN" -c "
import json
import sys

try:
    with open('$claude_config', 'r', encoding='utf-8') as f:
        data = json.load(f)
    removed = []
    projects = data.get('projects', {}) if isinstance(data, dict) else {}
    if isinstance(projects, dict):
        for proj, cfg in projects.items():
            if not isinstance(cfg, dict):
                continue
            servers = cfg.get('mcpServers')
            if not isinstance(servers, dict):
                continue
            for name in list(servers.keys()):
                if 'codex' in str(name).lower():
                    del servers[name]
                    removed.append(f'{proj}: {name}')
    with open('$claude_config', 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    if removed:
        print('Removed the following MCP configurations:')
        for r in removed:
            print(f'  - {r}')
except Exception as e:
    sys.stderr.write(f'WARN: failed cleaning MCP config: {e}\\n')
    sys.exit(0)
"
    echo "OK: Codex MCP configuration cleaned"
  fi
}

install_claude_md_config() {
  local claude_md="$HOME/.claude/CLAUDE.md"
  local md_mode="${CCB_CLAUDE_MD_MODE:-inline}"
  local asset_root
  asset_root="$(resolve_install_asset_root)"
  local full_template="$asset_root/config/claude-md-ccb.md"
  local route_template="$asset_root/config/claude-md-ccb-route.md"
  local external_config="$HOME/.claude/rules/ccb-config.md"

  # Select template based on mode
  local template
  if [[ "$md_mode" == "route" ]]; then
    template="$route_template"
  else
    template="$full_template"
  fi

  mkdir -p "$HOME/.claude"
  if ! pick_python_bin; then
    echo "ERROR: python required to update CLAUDE.md"
    return 1
  fi

  if [[ ! -f "$template" ]]; then
    echo "WARN: Template not found: $template; skipping CLAUDE.md injection"
    return 1
  fi

  # In route mode, write full config to external file
  if [[ "$md_mode" == "route" ]]; then
    remove_ccb_owned_memory_file "$external_config" "external CCB config" || true
    echo "Route mode no longer writes $external_config; using compact CLAUDE.md guidance only."
  fi

  local ccb_content
  ccb_content="$(cat "$template")"

  if [[ -f "$claude_md" ]]; then
    if grep -q "$CCB_START_MARKER" "$claude_md" 2>/dev/null; then
      echo "Updating existing CCB config block (mode: $md_mode)..."
      "$PYTHON_BIN" -c "
import re, sys

with open(sys.argv[1], 'r', encoding='utf-8') as f:
    content = f.read()
with open(sys.argv[2], 'r', encoding='utf-8') as f:
    new_block = f.read().strip()
pattern = r'<!-- CCB_CONFIG_START -->.*?<!-- CCB_CONFIG_END -->'
content = re.sub(pattern, new_block, content, flags=re.DOTALL)
with open(sys.argv[1], 'w', encoding='utf-8') as f:
    f.write(content)
" "$claude_md" "$template"
    elif grep -qE "$LEGACY_RULE_MARKER|## Codex Collaboration Rules|## Gemini|## OpenCode" "$claude_md" 2>/dev/null; then
      echo "Removing legacy rules and adding new CCB config block..."
      "$PYTHON_BIN" -c "
import re, sys

with open(sys.argv[1], 'r', encoding='utf-8') as f:
    content = f.read()
patterns = [
    r'## Codex Collaboration Rules.*?(?=\n## (?!Gemini)|\Z)',
    r'## Codex 协作规则.*?(?=\n## |\Z)',
    r'## Gemini Collaboration Rules.*?(?=\n## |\Z)',
    r'## Gemini 协作规则.*?(?=\n## |\Z)',
    r'## OpenCode Collaboration Rules.*?(?=\n## |\Z)',
    r'## OpenCode 协作规则.*?(?=\n## |\Z)',
]
for p in patterns:
    content = re.sub(p, '', content, flags=re.DOTALL)
content = content.rstrip() + '\n'
with open(sys.argv[1], 'w', encoding='utf-8') as f:
    f.write(content)
" "$claude_md"
      cat "$template" >> "$claude_md"
    else
      echo "" >> "$claude_md"
      cat "$template" >> "$claude_md"
    fi
  else
    cat "$template" > "$claude_md"
  fi

  echo "Updated AI collaboration rules in $claude_md (mode: $md_mode)"
}

install_agents_md_config() {
  if install_uses_live_source; then
    echo "Skipping AGENTS.md injection for source dev install (avoid mutating live repo)."
    return 0
  fi
  local agents_md="$INSTALL_PREFIX/AGENTS.md"
  local template="$INSTALL_PREFIX/config/agents-md-ccb.md"

  if ! pick_python_bin; then
    echo "WARN: python required to update AGENTS.md; skipping"
    return 1
  fi
  if [[ ! -f "$template" ]]; then
    echo "WARN: Template not found: $template; skipping AGENTS.md injection"
    return 1
  fi

  if [[ -f "$agents_md" ]]; then
    # Replace existing CCB blocks if present
    local updated=false
    if grep -q "$CCB_ROLES_START_MARKER" "$agents_md" 2>/dev/null || \
       grep -q "$CCB_RUBRICS_START_MARKER" "$agents_md" 2>/dev/null; then
      echo "Updating existing CCB blocks in AGENTS.md..."
      "$PYTHON_BIN" -c "
import re, sys

with open(sys.argv[1], 'r', encoding='utf-8') as f:
    content = f.read()
with open(sys.argv[2], 'r', encoding='utf-8') as f:
    new_block = f.read().strip()

# Remove old roles block
content = re.sub(
    r'<!-- CCB_ROLES_START -->.*?<!-- CCB_ROLES_END -->',
    '', content, flags=re.DOTALL)
# Remove old rubrics block
content = re.sub(
    r'<!-- REVIEW_RUBRICS_START -->.*?<!-- REVIEW_RUBRICS_END -->',
    '', content, flags=re.DOTALL)
content = content.rstrip() + '\n\n' + new_block + '\n'
with open(sys.argv[1], 'w', encoding='utf-8') as f:
    f.write(content)
" "$agents_md" "$template"
      updated=true
    fi
    if ! $updated; then
      echo "" >> "$agents_md"
      cat "$template" >> "$agents_md"
    fi
  else
    cat "$template" > "$agents_md"
  fi

  echo "Updated AGENTS.md: $agents_md"
}

install_clinerules_config() {
  if install_uses_live_source; then
    echo "Skipping .clinerules injection for source dev install (avoid mutating live repo)."
    return 0
  fi
  local clinerules="$INSTALL_PREFIX/.clinerules"
  local template="$INSTALL_PREFIX/config/clinerules-ccb.md"

  if ! pick_python_bin; then
    echo "WARN: python required to update .clinerules; skipping"
    return 1
  fi
  if [[ ! -f "$template" ]]; then
    echo "WARN: Template not found: $template; skipping .clinerules injection"
    return 1
  fi

  if [[ -f "$clinerules" ]]; then
    if grep -q "$CCB_ROLES_START_MARKER" "$clinerules" 2>/dev/null; then
      echo "Updating existing CCB roles block in .clinerules..."
      "$PYTHON_BIN" -c "
import re, sys

with open(sys.argv[1], 'r', encoding='utf-8') as f:
    content = f.read()
with open(sys.argv[2], 'r', encoding='utf-8') as f:
    new_block = f.read().strip()

content = re.sub(
    r'<!-- CCB_ROLES_START -->.*?<!-- CCB_ROLES_END -->',
    new_block, content, flags=re.DOTALL)
with open(sys.argv[1], 'w', encoding='utf-8') as f:
    f.write(content)
" "$clinerules" "$template"
    else
      echo "" >> "$clinerules"
      cat "$template" >> "$clinerules"
    fi
  else
    cat "$template" > "$clinerules"
  fi

  echo "Updated .clinerules: $clinerules"
}

cleanup_marked_memory_file() {
  local file_path="$1"
  local start_marker="$2"
  local end_marker="$3"
  local label="$4"

  if [[ ! -f "$file_path" ]]; then
    return 0
  fi
  if ! grep -q "$start_marker" "$file_path" 2>/dev/null; then
    return 0
  fi
  if ! pick_python_bin; then
    echo "WARN: python required to clean $label; skipping"
    return 1
  fi

  "$PYTHON_BIN" - "$file_path" "$start_marker" "$end_marker" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
start = re.escape(sys.argv[2])
end = re.escape(sys.argv[3])
content = path.read_text(encoding="utf-8")
content = re.sub(rf"\n?{start}.*?{end}\n?", "\n", content, flags=re.DOTALL)
path.write_text(content.strip() + "\n", encoding="utf-8")
PY
  echo "Removed CCB memory block from $label"
}

cleanup_memory_injections() {
  uninstall_claude_md_config
  if install_uses_live_source; then
    return 0
  fi
  cleanup_marked_memory_file "$INSTALL_PREFIX/AGENTS.md" "$CCB_ROLES_START_MARKER" "$CCB_ROLES_END_MARKER" "AGENTS.md" || true
  cleanup_marked_memory_file "$INSTALL_PREFIX/AGENTS.md" "$CCB_RUBRICS_START_MARKER" "$CCB_RUBRICS_END_MARKER" "AGENTS.md" || true
  cleanup_marked_memory_file "$INSTALL_PREFIX/.clinerules" "$CCB_ROLES_START_MARKER" "$CCB_ROLES_END_MARKER" ".clinerules" || true
}

install_settings_permissions() {
  local settings_file="$HOME/.claude/settings.json"
  mkdir -p "$HOME/.claude"

  local perms_to_add=(
    'Bash(ccb ask *)'
    'Bash(ccb clear *)'
    'Bash(ccb ping *)'
    'Bash(ccb pend *)'
  )

  if [[ ! -f "$settings_file" ]]; then
    cat > "$settings_file" << 'SETTINGS'
{
	  "permissions": {
	    "allow": [
	      "Bash(ccb ask *)",
	      "Bash(ccb clear *)",
	      "Bash(ccb ping *)",
	      "Bash(ccb pend *)"
	    ],
    "deny": []
  }
}
SETTINGS
    echo "Created $settings_file with permissions"
    return
  fi

  local perms_to_remove=(
    'Bash(ask *)'
    'Bash(ccb provider ping *)'
    'Bash(ccb provider pend *)'
    'Bash(ping *)'
    'Bash(ccb-ping *)'
    'Bash(pend *)'
  )
  if pick_python_bin; then
    local add_json remove_json
    add_json="$(printf '%s\n' "${perms_to_add[@]}" | "$PYTHON_BIN" -c 'import json,sys; print(json.dumps([line.rstrip("\n") for line in sys.stdin]))')"
    remove_json="$(printf '%s\n' "${perms_to_remove[@]}" | "$PYTHON_BIN" -c 'import json,sys; print(json.dumps([line.rstrip("\n") for line in sys.stdin]))')"
    "$PYTHON_BIN" -c "
import json

path = '$settings_file'
perms_to_add = json.loads('''$add_json''')
perms_to_remove = set(json.loads('''$remove_json'''))

try:
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
except Exception:
    data = {}

if not isinstance(data, dict):
    data = {}
perms = data.get('permissions')
if not isinstance(perms, dict):
    perms = {}
    data['permissions'] = perms
allow = perms.get('allow')
if not isinstance(allow, list):
    allow = []
deny = perms.get('deny')
if not isinstance(deny, list):
    deny = []

new_allow = [entry for entry in allow if entry not in perms_to_remove]
for entry in perms_to_add:
    if entry not in new_allow:
        new_allow.append(entry)

perms['allow'] = new_allow
perms['deny'] = deny

with open(path, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write('\n')
" || return 1
    echo "Updated $settings_file permissions"
  else
    echo "WARN: python required to update $settings_file permissions"
  fi
}

CCB_TMUX_MARKER="# CCB (Claude Code Bridge) tmux configuration"
CCB_TMUX_MARKER_LEGACY="# CCB tmux configuration"

remove_ccb_tmux_block_from_file() {
  local target_conf="$1"

  if [[ ! -f "$target_conf" ]]; then
    return 0
  fi

  if ! grep -q "$CCB_TMUX_MARKER" "$target_conf" 2>/dev/null && \
     ! grep -q "$CCB_TMUX_MARKER_LEGACY" "$target_conf" 2>/dev/null; then
    return 0
  fi

  if ! pick_any_python_bin; then
    return 1
  fi

  "$PYTHON_BIN" -c "
import re
path = '$target_conf'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()
# Remove CCB tmux config block (both new and legacy markers)
pattern = r'\n*# =+\n# CCB \(Claude Code Bridge\) tmux configuration.*?# =+\n# End of CCB tmux configuration\n# =+'
content = re.sub(pattern, '', content, flags=re.DOTALL)
pattern = r'\n*# CCB tmux configuration.*'
content = re.sub(pattern, '', content, flags=re.DOTALL)
with open(path, 'w', encoding='utf-8') as f:
    f.write(content.strip() + '\n' if content.strip() else '')
"
}

install_tmux_config() {
  local tmux_conf_main="$HOME/.tmux.conf"
  local tmux_conf_local="$HOME/.tmux.conf.local"
  local tmux_conf="$tmux_conf_main"
  local reload_conf="$tmux_conf_main"
  local asset_root
  asset_root="$(resolve_install_asset_root)"
  local ccb_tmux_conf="$asset_root/config/tmux-ccb.conf"
  local ccb_status_script="$asset_root/config/ccb-status.sh"
  local status_install_path="$BIN_DIR/ccb-status.sh"

  if [[ ! -f "$ccb_tmux_conf" ]]; then
    return
  fi

  mkdir -p "$BIN_DIR"

  # Install ccb-status.sh script
  if [[ -f "$ccb_status_script" ]]; then
    install_owned_executable "$ccb_status_script" "$status_install_path"
    echo "Installed: $status_install_path"
  fi

  # Install ccb-border.sh script (dynamic pane border colors)
  local ccb_border_script="$asset_root/config/ccb-border.sh"
  local border_install_path="$BIN_DIR/ccb-border.sh"
  if [[ -f "$ccb_border_script" ]]; then
    install_owned_executable "$ccb_border_script" "$border_install_path"
    echo "Installed: $border_install_path"
  fi

  # Install ccb-git.sh script (cached git status for tmux status line)
  local ccb_git_script="$asset_root/config/ccb-git.sh"
  local git_install_path="$BIN_DIR/ccb-git.sh"
  if [[ -f "$ccb_git_script" ]]; then
    install_owned_executable "$ccb_git_script" "$git_install_path"
    echo "Installed: $git_install_path"
  fi

  # Install tmux UI toggle scripts (enable/disable CCB theming per-session)
  local ccb_tmux_on_script="$asset_root/config/ccb-tmux-on.sh"
  local ccb_tmux_off_script="$asset_root/config/ccb-tmux-off.sh"
  if [[ -f "$ccb_tmux_on_script" ]]; then
    install_owned_executable "$ccb_tmux_on_script" "$BIN_DIR/ccb-tmux-on.sh"
    echo "Installed: $BIN_DIR/ccb-tmux-on.sh"
  fi
  if [[ -f "$ccb_tmux_off_script" ]]; then
    install_owned_executable "$ccb_tmux_off_script" "$BIN_DIR/ccb-tmux-off.sh"
    echo "Installed: $BIN_DIR/ccb-tmux-off.sh"
  fi

  # Oh-My-Tmux keeps user customizations in ~/.tmux.conf.local.
  # Appending to ~/.tmux.conf can break its internal _apply_configuration script.
  if [[ -f "$tmux_conf_main" ]] && grep -q 'TMUX_CONF_LOCAL' "$tmux_conf_main" 2>/dev/null; then
    tmux_conf="$tmux_conf_local"
    reload_conf="$tmux_conf_main"
    if [[ ! -f "$tmux_conf_local" ]]; then
      touch "$tmux_conf_local"
    fi
  else
    reload_conf="$tmux_conf"
  fi

  # Check if already configured (new or legacy marker) in either main/local config.
  local already_configured=false
  for conf in "$tmux_conf_main" "$tmux_conf_local"; do
    if [[ -f "$conf" ]] && \
      (grep -q "$CCB_TMUX_MARKER" "$conf" 2>/dev/null || \
       grep -q "$CCB_TMUX_MARKER_LEGACY" "$conf" 2>/dev/null); then
      already_configured=true
      break
    fi
  done

  if $already_configured; then
    # Update existing config: remove old CCB block(s) and re-add at target location.
    echo "Updating CCB tmux configuration..."
    remove_ccb_tmux_block_from_file "$tmux_conf_main" || true
    remove_ccb_tmux_block_from_file "$tmux_conf_local" || true
  else
    # Backup existing config if present
    if [[ -f "$tmux_conf" ]]; then
      cp "$tmux_conf" "$tmux_conf.bak.$(date +%Y%m%d%H%M%S)"
    fi
  fi

  # Append CCB tmux config (fill in BIN_DIR placeholders)
  {
    echo ""
    if pick_any_python_bin; then
      "$PYTHON_BIN" -c "
import sys

path = '$ccb_tmux_conf'
bin_dir = '$BIN_DIR'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()
sys.stdout.write(content.replace('@CCB_BIN_DIR@', bin_dir))
" 2>/dev/null || cat "$ccb_tmux_conf"
    else
      cat "$ccb_tmux_conf"
    fi
  } >> "$tmux_conf"

  echo "Updated tmux configuration: $tmux_conf"
  echo "   - CCB tmux integration (copy mode, mouse, pane management)"
  echo "   - CCB theme is enabled only while CCB is running (auto restore on exit)"
  echo "   - Vi-style pane management with h/j/k/l"
  echo "   - Mouse support and better copy mode"
  echo "   - Run 'tmux source $reload_conf' to apply (or restart tmux)"

  # Best-effort: if a tmux server is already running, reload config automatically.
  # (Avoid spawning a new server when tmux isn't running.)
  if command -v tmux >/dev/null 2>&1; then
    if tmux list-sessions >/dev/null 2>&1; then
      if tmux source-file "$reload_conf" >/dev/null 2>&1; then
        echo "Reloaded tmux configuration in running server."
      else
        echo "WARN: Failed to reload tmux configuration automatically; run: tmux source $reload_conf"
      fi
    fi
  fi
}

uninstall_tmux_config() {
  local tmux_conf_main="$HOME/.tmux.conf"
  local tmux_conf_local="$HOME/.tmux.conf.local"
  local status_script="$BIN_DIR/ccb-status.sh"
  local border_script="$BIN_DIR/ccb-border.sh"
  local tmux_on_script="$BIN_DIR/ccb-tmux-on.sh"
  local tmux_off_script="$BIN_DIR/ccb-tmux-off.sh"

  # Remove ccb-status.sh script
  if [[ -f "$status_script" ]]; then
    rm -f "$status_script"
    echo "Removed: $status_script"
  fi

  # Remove ccb-border.sh script
  if [[ -f "$border_script" ]]; then
    rm -f "$border_script"
    echo "Removed: $border_script"
  fi

  # Remove tmux UI toggle scripts
  if [[ -f "$tmux_on_script" ]]; then
    rm -f "$tmux_on_script"
    echo "Removed: $tmux_on_script"
  fi
  if [[ -f "$tmux_off_script" ]]; then
    rm -f "$tmux_off_script"
    echo "Removed: $tmux_off_script"
  fi

  local removed_any=false
  for conf in "$tmux_conf_main" "$tmux_conf_local"; do
    if [[ -f "$conf" ]] && \
      (grep -q "$CCB_TMUX_MARKER" "$conf" 2>/dev/null || \
       grep -q "$CCB_TMUX_MARKER_LEGACY" "$conf" 2>/dev/null); then
      echo "Removing CCB tmux configuration from $conf..."
      if remove_ccb_tmux_block_from_file "$conf"; then
        echo "Removed CCB tmux configuration from $conf"
        removed_any=true
      fi
    fi
  done

  if ! $removed_any; then
    return
  fi
}

install_requirements() {
  check_wsl_compatibility
  confirm_backend_env_wsl
  require_python_version
  if env_value_is_true "${CCB_INSTALL_ROLES:-auto}"; then
    check_role_pack_dependencies required
  fi
  if use_managed_venv; then
    echo "INFO: Python package dependencies will be installed inside the managed Python venv"
  else
    install_tomli
    install_watchdog
  fi
  require_terminal_backend
}

# Clean up legacy daemon files from the pre-ccbd era
cleanup_legacy_files() {
  echo "Cleaning up legacy files..."
  local cleaned=0

  # Legacy daemon scripts in bin/
  local legacy_daemons="caskd gaskd oaskd laskd daskd"
  for daemon in $legacy_daemons; do
    if [[ -f "$BIN_DIR/$daemon" ]]; then
      rm -f "$BIN_DIR/$daemon"
      echo "  Removed legacy daemon script: $BIN_DIR/$daemon"
      cleaned=$((cleaned + 1))
    fi
    # Also check install prefix bin
    if [[ -f "$INSTALL_PREFIX/bin/$daemon" ]]; then
      rm -f "$INSTALL_PREFIX/bin/$daemon"
      echo "  Removed legacy daemon script: $INSTALL_PREFIX/bin/$daemon"
      cleaned=$((cleaned + 1))
    fi
  done

  # Legacy daemon state files in ~/.cache/ccb/
  local cache_dir="${XDG_CACHE_HOME:-$HOME/.cache}/ccb"
  local legacy_states="caskd.json gaskd.json oaskd.json laskd.json daskd.json"
  for state in $legacy_states; do
    if [[ -f "$cache_dir/$state" ]]; then
      rm -f "$cache_dir/$state"
      echo "  Removed legacy state file: $cache_dir/$state"
      cleaned=$((cleaned + 1))
    fi
  done

  # Legacy daemon module files in lib/
  local legacy_modules="caskd_daemon.py gaskd_daemon.py oaskd_daemon.py laskd_daemon.py daskd_daemon.py"
  for module in $legacy_modules; do
    if [[ -f "$INSTALL_PREFIX/lib/$module" ]]; then
      rm -f "$INSTALL_PREFIX/lib/$module"
      echo "  Removed legacy module: $INSTALL_PREFIX/lib/$module"
      cleaned=$((cleaned + 1))
    fi
  done

  if [[ $cleaned -eq 0 ]]; then
    echo "  No legacy files found"
  else
    echo "  Cleaned up $cleaned legacy file(s)"
  fi
}

cleanup_legacy_neovim_tool() {
  local data_home state_home root state_root link target
  data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
  state_home="${XDG_STATE_HOME:-$HOME/.local/state}"
  root="$data_home/ccb/tools/neovim"
  state_root="$state_home/ccb/tools/neovim"
  link="$BIN_DIR/ccb-nvim"

  if [[ -L "$link" ]]; then
    target="$(readlink "$link" 2>/dev/null || true)"
    case "$target" in
      "$root"|"$root"/*)
        rm -f "$link"
        ;;
    esac
  elif [[ -f "$link" ]] && grep -q 'NVIM_APPNAME=nvim' "$link" 2>/dev/null && grep -q 'ccb/tools/neovim' "$link" 2>/dev/null; then
    rm -f "$link"
  fi

  rm -rf "$root" "$state_root"
}

install_all() {
  validate_temporary_install_scope
  require_major_upgrade_confirmation
  install_requirements
  remove_codex_mcp
  cleanup_legacy_files
  cleanup_legacy_neovim_tool
  prepare_install_tree
  install_managed_venv
  if ! install_uses_live_source; then
    write_install_metadata
  fi
  build_sidebar_helper_if_possible
  install_bin_links
  verify_installed_entrypoints
  ensure_path_configured
  install_claude_commands
  install_claude_skills
  install_codex_skills
  install_droid_skills
  install_droid_delegation
  cleanup_memory_injections
  install_settings_permissions
  install_tmux_config
  provision_role_packs
  echo "OK: Installation complete"
  echo "   Executable dir : $BIN_DIR"
  if install_uses_live_source; then
    echo "   Live source dir: $(resolve_live_source_root)"
    echo "   Managed dir    : $INSTALL_PREFIX"
  else
    echo "   Project dir    : $INSTALL_PREFIX"
  fi
  print_install_identity_summary
  echo "   Claude commands updated"
  echo "   Global memory files left unmodified; old CCB memory blocks cleaned when present"
  if use_managed_venv; then
    echo "   Managed Python: $(managed_venv_python)"
  fi
  echo "   Global settings.json permissions added"
  print_install_identity_notice
}

provision_role_packs() {
  local requested="${CCB_INSTALL_ROLES:-auto}"
  if env_value_is_false "$requested"; then
    echo "INFO: Role Pack provisioning skipped by CCB_INSTALL_ROLES=0"
    return 0
  fi
  local required=0
  if env_value_is_true "$requested"; then
    required=1
  else
    echo "INFO: Role Pack provisioning enabled by default; set CCB_INSTALL_ROLES=0 to skip."
  fi
  local dependency_mode="warn"
  if [[ "$required" == "1" ]]; then
    dependency_mode="required"
  fi
  if ! check_role_pack_dependencies "$dependency_mode"; then
    [[ "$required" == "1" ]] && return 1 || return 0
  fi
  local ccb_entry
  if install_uses_live_source; then
    ccb_entry="$(resolve_live_source_root)/ccb"
  else
    ccb_entry="$INSTALL_PREFIX/ccb"
  fi
  if [[ ! -x "$ccb_entry" ]]; then
    echo "WARN: Role Pack provisioning skipped; ccb entrypoint not executable: $ccb_entry"
    return 0
  fi
  local failures=0
  local role_id
  for role_id in agentroles.archi agentroles.ccb_self; do
    if provision_default_role_pack "$ccb_entry" "$role_id"; then
      continue
    fi
    failures=$((failures + 1))
  done
  if [[ "$failures" == "0" ]]; then
    echo "OK: Role Packs ready"
    return 0
  fi
  echo "WARN: Role Pack provisioning failed for $failures default role(s)"
  [[ "$required" == "1" ]] && return 1 || return 0
}

provision_default_role_pack() {
  local ccb_entry="$1"
  local role_id="$2"
  local log_file
  log_file="$(mktemp "${TMPDIR:-/tmp}/ccb-roles-install.XXXXXX")"
  if CODEX_BIN_DIR="$BIN_DIR" "$ccb_entry" roles update "$role_id" >"$log_file" 2>&1; then
    rm -f "$log_file"
    echo "OK: Role Pack ready: $role_id"
    return 0
  fi
  if grep -qiE 'role .*not installed|run .*roles install|run agent-roles install' "$log_file" 2>/dev/null; then
    echo "INFO: Role Pack not installed yet; installing $role_id."
    if CODEX_BIN_DIR="$BIN_DIR" "$ccb_entry" roles install "$role_id" >"$log_file" 2>&1; then
      rm -f "$log_file"
      echo "OK: Role Pack ready: $role_id"
      return 0
    fi
  fi
  echo "WARN: Role Pack provisioning failed: $role_id"
  sed 's/^/   /' "$log_file" 2>/dev/null || true
  rm -f "$log_file"
  return 1
}

uninstall_claude_md_config() {
  local claude_md="$HOME/.claude/CLAUDE.md"

  if [[ -f "$claude_md" ]] && grep -q "$CCB_START_MARKER" "$claude_md" 2>/dev/null; then
    echo "Removing CCB config block from CLAUDE.md..."
    if pick_any_python_bin; then
      "$PYTHON_BIN" -c "
import re

with open('$claude_md', 'r', encoding='utf-8') as f:
    content = f.read()
pattern = r'\\n?<!-- CCB_CONFIG_START -->.*?<!-- CCB_CONFIG_END -->\\n?'
content = re.sub(pattern, '\\n', content, flags=re.DOTALL)
content = content.strip() + '\\n'
with open('$claude_md', 'w', encoding='utf-8') as f:
    f.write(content)
"
      echo "Removed CCB config from CLAUDE.md"
    else
      echo "WARN: python required to clean CLAUDE.md, please manually remove CCB_CONFIG block"
    fi
  elif [[ -f "$claude_md" ]] && grep -qE "$LEGACY_RULE_MARKER|## Codex Collaboration Rules|## Gemini|## OpenCode" "$claude_md" 2>/dev/null; then
    echo "Removing legacy collaboration rules from CLAUDE.md..."
    if pick_any_python_bin; then
      "$PYTHON_BIN" -c "
import re

with open('$claude_md', 'r', encoding='utf-8') as f:
    content = f.read()
patterns = [
    r'## Codex Collaboration Rules.*?(?=\\n## (?!Gemini)|\\Z)',
    r'## Codex 协作规则.*?(?=\\n## |\\Z)',
    r'## Gemini Collaboration Rules.*?(?=\\n## |\\Z)',
    r'## Gemini 协作规则.*?(?=\\n## |\\Z)',
    r'## OpenCode Collaboration Rules.*?(?=\\n## |\\Z)',
    r'## OpenCode 协作规则.*?(?=\\n## |\\Z)',
]
for p in patterns:
    content = re.sub(p, '', content, flags=re.DOTALL)
content = content.rstrip() + '\\n'
with open('$claude_md', 'w', encoding='utf-8') as f:
    f.write(content)
"
      echo "Removed collaboration rules from CLAUDE.md"
    else
      echo "WARN: python required to clean CLAUDE.md, please manually remove collaboration rules"
    fi
  fi

  # Clean up external config file if it exists (route mode)
  local external_config="$HOME/.claude/rules/ccb-config.md"
  remove_ccb_owned_memory_file "$external_config" "external CCB config" || true
}

uninstall_settings_permissions() {
  local settings_file="$HOME/.claude/settings.json"

  if [[ ! -f "$settings_file" ]]; then
    return
  fi

  local perms_to_remove=(
    'Bash(ccb ask *)'
    'Bash(ccb clear *)'
    'Bash(ccb ping *)'
    'Bash(ccb pend *)'
    'Bash(ask *)'
    'Bash(ccb provider ping *)'
    'Bash(ccb provider pend *)'
    'Bash(ping *)'
    'Bash(ccb-ping *)'
    'Bash(pend *)'
    'Bash(bask:*)'
    'Bash(bpend)'
    'Bash(bping)'
    'Bash(cask:*)'
    'Bash(cpend)'
    'Bash(cping)'
    'Bash(dask:*)'
    'Bash(dpend)'
    'Bash(dping)'
    'Bash(gask:*)'
    'Bash(gpend)'
    'Bash(gping)'
    'Bash(hask:*)'
    'Bash(hpend)'
    'Bash(hping)'
    'Bash(lask:*)'
    'Bash(lpend)'
    'Bash(lping)'
    'Bash(oask:*)'
    'Bash(opend)'
    'Bash(oping)'
    'Bash(qask:*)'
    'Bash(qpend)'
    'Bash(qping)'
  )

  if pick_any_python_bin; then
    local has_perms=0
    for perm in "${perms_to_remove[@]}"; do
      if grep -q "$perm" "$settings_file" 2>/dev/null; then
        has_perms=1
        break
      fi
    done

    if [[ $has_perms -eq 1 ]]; then
      echo "Removing permission configuration from settings.json..."
      "$PYTHON_BIN" -c "
import json
import sys

path = '$settings_file'
perms_to_remove = [
    'Bash(ccb ask *)',
    'Bash(ccb clear *)',
    'Bash(ccb ping *)',
    'Bash(ccb pend *)',
    'Bash(ask *)',
    'Bash(ccb provider ping *)',
    'Bash(ccb provider pend *)',
    'Bash(ping *)',
    'Bash(ccb-ping *)',
    'Bash(pend *)',
    'Bash(bask:*)',
    'Bash(bpend)',
    'Bash(bping)',
    'Bash(cask:*)',
    'Bash(cpend)',
    'Bash(cping)',
    'Bash(dask:*)',
    'Bash(dpend)',
    'Bash(dping)',
    'Bash(gask:*)',
    'Bash(gpend)',
    'Bash(gping)',
    'Bash(hask:*)',
    'Bash(hpend)',
    'Bash(hping)',
    'Bash(lask:*)',
    'Bash(lpend)',
    'Bash(lping)',
    'Bash(oask:*)',
    'Bash(opend)',
    'Bash(oping)',
    'Bash(qask:*)',
    'Bash(qpend)',
    'Bash(qping)',
]
try:
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if not isinstance(data, dict):
        sys.exit(0)
    perms = data.get('permissions')
    if not isinstance(perms, dict):
        sys.exit(0)
    allow = perms.get('allow')
    if not isinstance(allow, list):
        sys.exit(0)
    perms['allow'] = [p for p in allow if p not in perms_to_remove]
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
except Exception:
    sys.exit(0)
"
      echo "Removed permission configuration from settings.json"
    fi
  else
    echo "WARN: python required to clean settings.json, please manually remove related permissions"
  fi
}

uninstall_claude_skills() {
  local skills_dst="$HOME/.claude/skills"
  local ccb_skills="ask ccb-config ccb-clear"
  local legacy_skills="ccb_config ping pend autonew all-plan docs tp tr file-op review continue"

  if [[ ! -d "$skills_dst" ]]; then
    return
  fi

  echo "Removing CCB Claude skills..."
  for skill in $legacy_skills; do
    rm -rf "$skills_dst/$skill"
  done
  for skill in $ccb_skills; do
    if [[ -d "$skills_dst/$skill" ]]; then
      rm -rf "$skills_dst/$skill"
      echo "  Removed skill: $skill"
    fi
  done
}

uninstall_codex_skills() {
  local skills_dst
  skills_dst="$(resolve_codex_source_home)/skills"
  local ccb_skills="ask ccb-config ccb-clear"
  local legacy_skills="ccb_config ping pend autonew all-plan file-op"

  if [[ ! -d "$skills_dst" ]]; then
    return
  fi

  echo "Removing CCB Codex skills..."
  for skill in $legacy_skills; do
    rm -rf "$skills_dst/$skill"
  done
  for skill in $ccb_skills; do
    if [[ -d "$skills_dst/$skill" ]]; then
      rm -rf "$skills_dst/$skill"
      echo "  Removed skill: $skill"
    fi
  done
}

uninstall_droid_skills() {
  local skills_dst="${FACTORY_HOME:-$HOME/.factory}/skills"
  local ccb_skills="ask"
  local legacy_skills="ping pend autonew all-plan"

  if [[ ! -d "$skills_dst" ]]; then
    return
  fi

  echo "Removing CCB Droid skills..."
  for skill in $legacy_skills; do
    rm -rf "$skills_dst/$skill"
  done
  for skill in $ccb_skills; do
    if [[ -d "$skills_dst/$skill" ]]; then
      rm -rf "$skills_dst/$skill"
      echo "  Removed skill: $skill"
    fi
  done
}

uninstall_droid_delegation() {
  if ! command -v droid >/dev/null 2>&1; then
    return
  fi

  echo "Removing Droid MCP delegation..."
  if droid mcp remove ccb-delegation >/dev/null 2>&1; then
    echo "  Removed ccb-delegation MCP"
  fi
}

uninstall_droid_commands() {
  local cmds_dst="${FACTORY_HOME:-$HOME/.factory}/commands"
  local ccb_cmds="ask.md ping.md pend.md"

  if [[ ! -d "$cmds_dst" ]]; then
    return
  fi

  echo "Removing CCB Droid commands..."
  for cmd in $ccb_cmds; do
    if [[ -f "$cmds_dst/$cmd" ]]; then
      rm -f "$cmds_dst/$cmd"
      echo "  Removed command: $cmd"
    fi
  done
}

uninstall_all() {
  echo "INFO: Starting ccb uninstall..."

  # 1. Remove project directory
  if [[ -d "$INSTALL_PREFIX" ]]; then
    rm -rf "$INSTALL_PREFIX"
    echo "Removed project directory: $INSTALL_PREFIX"
  fi

  # 2. Remove bin links
  for path in "${SCRIPTS_TO_LINK[@]}"; do
    local name
    name="$(basename "$path")"
    if [[ -L "$BIN_DIR/$name" || -f "$BIN_DIR/$name" ]]; then
      rm -f "$BIN_DIR/$name"
    fi
  done
  for legacy in "${LEGACY_SCRIPTS[@]}"; do
    rm -f "$BIN_DIR/$legacy"
  done
  echo "Removed bin links: $BIN_DIR"

  # 3. Remove Claude command files (clean all possible locations)
  local cmd_dirs=(
    "$HOME/.claude/commands"
    "$HOME/.config/claude/commands"
    "$HOME/.local/share/claude/commands"
  )
  for dir in "${cmd_dirs[@]}"; do
    if [[ -d "$dir" ]]; then
      for doc in "${CLAUDE_MARKDOWN[@]+"${CLAUDE_MARKDOWN[@]}"}"; do
        rm -f "$dir/$doc"
      done
      echo "Cleaned commands directory: $dir"
    fi
  done

  # 4. Remove collaboration rules from CLAUDE.md
  uninstall_claude_md_config

  # 5. Remove permission configuration from settings.json
  uninstall_settings_permissions

  # 6. Remove tmux configuration
  uninstall_tmux_config

  # 7. Remove Claude skills
  uninstall_claude_skills

  # 8. Remove Codex skills
  uninstall_codex_skills

  # 9. Remove Droid skills
  uninstall_droid_skills

  # 10. Remove Droid MCP delegation
  uninstall_droid_delegation

  # 11. Remove Droid commands
  uninstall_droid_commands

  echo "OK: Uninstall complete"
  echo "   NOTE: Dependencies (python, tmux) were not removed"
}

main() {
  if [[ $# -ne 1 ]]; then
    usage
    exit 1
  fi

  case "$1" in
    install)
      confirm_root_install_if_needed
      install_all
      ;;
    uninstall)
      uninstall_all
      ;;
    *)
      usage
      exit 1
      ;;
  esac
}

# Test harness split marker: main "$@"
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "${@}"
fi
