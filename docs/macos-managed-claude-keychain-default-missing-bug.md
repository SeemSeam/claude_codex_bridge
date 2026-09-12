# macOS 隔离 HOME 下默认钥匙串丢失导致启动弹窗（managed Claude）

- 状态：已修复（方案 A1，agent-private keychain；分支 `fix/macos-managed-keychain-default-missing`）
- 发现日期：2026-09-04
- 修复日期：2026-09-12
- 影响版本：v8.6.12（2026-09-02），macOS（新版，无磁盘 `~/Library/Preferences/com.apple.security.plist`）
- 影响 provider：`claude`（每个 managed home 都会触发；`codex` 未在本次排查范围内）
- 涉及源码：`lib/provider_backends/claude/launcher_runtime/home.py`

---

## 0. 修复摘要（2026-09-12）

采用下文「方案 A1」：在隔离 HOME 内为每个 agent 物化一个 agent-private keychain 并设为默认，
用户真实 login keychain 仅以**只读**方式保留在搜索列表中用于凭证发现。

实现要点（均在 `lib/provider_backends/claude/launcher_runtime/home.py`）：

- `_materialize_macos_keychain_preferences()`：
  - 继续先 detach legacy `Library/Keychains` 软链、删除 legacy `com.apple.security.plist`（不回退旧的可写链接行为）。
  - 当 `_inherits_external_auth(profile)` 为真时，调用新函数 `_ensure_managed_private_keychain()`；
    否则调用 `_remove_managed_private_keychain()` 清理上一轮残留。
- `_ensure_managed_private_keychain()`（新增）：
  - 创建 `Library/Keychains/ccb-agent.keychain-db`（空密码，仅存 copy-only managed 凭证）。
  - 所有 `security` 调用都通过 `_run_security(..., env_home=<managed home>)` 以 `HOME=<managed>` 执行，
    因此 `list-keychains -s` / `default-keychain -s` 只会写隔离 HOME 内的
    `Library/Preferences/com.apple.security.plist`，**实测不改变真实用户的全局钥匙串设置**。
    （前提：必须先 `mkdir -p Library/Preferences`，否则 `security ... -s` 在隔离 HOME 下会静默丢弃。）
  - 搜索列表顺序：`<private keychain>`（默认写落点）→ 真实 `login.keychain-db`（只读凭证发现）
    → `/Library/Keychains/System.keychain`；默认钥匙串显式设为 private keychain。
- `_sync_managed_macos_keychain_auth()` / `_remove_managed_macos_keychain_auth()`：
  managed-suffix 条目的 `find/add/delete-generic-password` 现在显式带 private keychain 文件路径，
  即使 CCB 父进程 HOME 仍指向真实用户 home，managed 条目也绝不落入 login keychain。
- 容错：`security` CLI 缺失或任何 provisioning 步骤失败均为 best-effort（不阻断启动；
  凭证仍通过 `.credentials.json` copy-only 投影）。

关键安全边界（已被测试锁定）：

1. managed logout / 禁用继承只删除 agent-private keychain 与隔离 HOME 内 plist，绝不 unlock/delete/modify 真实 `login.keychain-db`。
2. 不再恢复「整个 `~/Library/Keychains` 软链进隔离 HOME 且可写」的旧行为；private keychain 是隔离 HOME 内的常规文件。
3. `Library/Keychains` 在 `ccb doctor storage` 仍归类为 SECRET（`storage_classification/provider_home.py`）。

测试：`test/test_provider_profiles.py` 新增/更新用例覆盖
private keychain 创建与默认/搜索列表设置、managed 条目只写入 private keychain、
禁用继承后删除 private keychain 与 plist、provisioning 失败不阻断、legacy 链接仍被 detach 等。
本机端到端验证：真实物化后 `HOME=<managed> security default-keychain` 可解析、
`add/find-generic-password` 静默成功无 GUI、真实用户全局 keychain 设置全程不变。

---

## 1. 现象

每个 managed Claude agent（`.ccb/agents/<agent>/provider-state/claude/home`）启动、provider 物化阶段访问 macOS 钥匙串的瞬间，系统弹出 GUI 对话框：

> **找不到钥匙串**
> 找不到用于存储 “<用户名>” 的钥匙串。
> [取消]  [还原为默认]

- 弹窗是**短暂出现**（provider 启动 / 凭证物化时闪一下），点「取消」或「还原为默认」都能继续。
- 功能上通常不致命：Claude 凭证继承是 copy-only，会写入 managed home 的 `.claude/.credentials.json`，agent 仍能正常工作。
- 但每次新 agent / 新项目首次物化、或触发 keychain seed 时都可能弹，体验差，且 `security` 命令在该 HOME 下行为退化（见下）。

## 2. 根因

managed provider 启动时把 `HOME` 重写到隔离目录 `<repo>/.ccb/agents/<agent>/provider-state/claude/home`。在该 HOME 下：

1. **钥匙串搜索列表丢失**：`security list-keychains` 只剩 `/Library/Keychains/System.keychain`，用户的 `login.keychain-db` 不在列表内。
2. **默认钥匙串解析失败**：`security default-keychain` 返回
   `SecKeychainCopyDefault: A default keychain could not be found.`
   `security login-keychain` 返回 `The specified keychain could not be found.`

   实测对照（在隔离 HOME 下执行）：

   ```text
   $ HOME=<managed-home> security default-keychain
   security: SecKeychainCopyDefault: A default keychain could not be found.
   $ HOME=<managed-home> security list-keychains
       "/Library/Keychains/System.keychain"        # ← login keychain 丢失
   ```

3. Security.framework 在默认钥匙串缺失时，任何针对 generic password 的 `security add/find-generic-password` 调用都会触发「找不到钥匙串 / 还原为默认」GUI 弹窗。而这正是 CCB 物化凭证的必走路径：
   - `_read_macos_keychain_claude_credentials()` → `security find-generic-password ...`（home.py:1011 附近）
   - `_seed_managed_macos_keychain_auth()` → `security add-generic-password -U ...`（home.py:1108 附近）

### 为什么新版 macOS 上旧的 fallback 失效

CCB 历史上有两条 macOS 兜底（见 `CHANGELOG.md` v? 「Claude Keychain Fallback」「Claude Keychain Preference Projection」）：

- 在 managed home 建 `Library/Keychains` → 真实 `~/Library/Keychains` 的符号链接；
- 拷贝 `Library/Preferences/com.apple.security.plist`，保留默认钥匙串偏好。

v8.6.12 出于安全原因**主动移除了这两条**（防止 managed provider logout 反向污染用户真实 login keychain），见 `home.py:685-704`：

```python
def _materialize_macos_keychain_preferences(source_home, target_layout, *, profile):
    target = target_layout.home_root / 'Library' / 'Preferences' / 'com.apple.security.plist'
    target_keychains = target_layout.home_root / 'Library' / 'Keychains'
    # Older CCB releases linked this path back to the user's real Keychains
    # directory.  That made a managed provider logout capable of mutating
    # the external login authority.  Credential inheritance is now copy-only,
    # so detach any legacy link before doing anything else.
    _remove_keychains_link(target_keychains)
    # A copied preference file can itself point Security.framework back to
    # the user's global keychain database, so remove legacy copies as well.
    _remove_file(target)
```

安全意图是对的（copy-only、不回写外部权威），但移除后**没有在隔离 HOME 内补上一个可用的默认钥匙串**。在旧版 macOS 上，Security.framework 可能用系统级默认或缓存偏好兜底，不弹窗；在新版 macOS 上：

- 用户真实 home 下 `~/Library/Preferences/com.apple.security.plist` **本就不存在**（钥匙串偏好由 securityd 守护进程内存持有，不落盘成该文件）；
- 隔离 HOME 既无 `Library/Keychains` 链接、又无 `Library/Preferences/com.apple.security.plist`、搜索列表也没有 login keychain；
- 于是 `default-keychain` 彻底解析不到 → 弹窗。

即：**「删除回写真实钥匙串的链接」正确，但「删除后隔离 HOME 没有任何可用默认钥匙串」是回归缺口。**

### 机器侧实测佐证（2026-09-04，单用户多项目）

同一台 Mac、同版本 v8.6.12，扫描所有项目 managed Claude home 的 `security default-keychain`：

- ❌ 默认钥匙串解析失败（会弹）：近期物化过的 home（软链已被新版 unlink）。
- ✅ 正常：`doctrine/`、`test_ccb2/` 等 6 月由旧版 CCB 建的 home —— 它们 `Library/Keychains` 符号链接残留至今、且之后未再触发物化（物化会 unlink）。

对比直接证明：让隔离 HOME 能解析默认钥匙串的，就是 `Library/Keychains` 链接 / 钥匙串偏好；plock 与否不影响，缺了就弹。

## 3. 手动复现 / 验证命令

```bash
MH=<repo>/.ccb/agents/<agent>/provider-state/claude/home

# 复现：隔离 HOME 下默认钥匙串丢失
HOME="$MH" security default-keychain      # => A default keychain could not be found
HOME="$MH" security list-keychains        # => 只剩 System.keychain

# 触发弹窗的调用模式（CCB 物化路径）
HOME="$MH" security find-generic-password -a "$USER" -s "Claude Code-credentials" -w
```

手动临时止血（注意：**会被 CCB 下次物化 `_remove_keychains_link` 清掉**，非持久）：

```bash
REAL_LOGIN="$HOME/Library/Keychains/login.keychain-db"   # 真实用户 home
mkdir -p "$MH/Library/Preferences"
ln -s "$(dirname "$REAL_LOGIN")" "$MH/Library/Keychains"
HOME="$MH" security list-keychains -s "$REAL_LOGIN" /Library/Keychains/System.keychain
HOME="$MH" security default-keychain -s "$REAL_LOGIN"

# 验证：以下应静默成功、不再弹 GUI
HOME="$MH" security default-keychain
HOME="$MH" security add-generic-password -a probe -s ccb-diag -w x \
  && HOME="$MH" security delete-generic-password -a probe -s ccb-diag
```

## 4. 修复方案（PR 方向）

核心原则：**保持 copy-only 安全边界不变（managed logout 不得回写/污染用户真实 login keychain），但让隔离 HOME 下 Security.framework 始终能解析到一个可用的默认钥匙串，从而不弹 GUI。**

推荐方向（按偏好排序）：

### 方案 A（推荐）：物化后在隔离 HOME 内重建「只读、仅用于解析默认」的钥匙串上下文，且绝不回写外部

在 `_materialize_macos_keychain_preferences()`（home.py:685）删除 legacy 链接之后，新增一步「保证隔离 HOME 有可用默认钥匙串」：

- 不再把整个 `Library/Keychains` 软链回真实目录（避免回写风险）；
- 改为在受控的 keychain 搜索列表里显式包含用户真实 login keychain 的**读取**路径用于凭证发现，同时默认钥匙串指向一个 **managed-private keychain**（或 login keychain 的只读引用），使 `default-keychain` / `add-generic-password` 有落点：
  - 选项 A1：在隔离 HOME 内创建一个独立的 agent-private keychain（`Library/Keychains/ccb-<agent>.keychain-db`），用 `security create-keychain` + `security default-keychain -s` + `list-keychains -s <private> <login只读> System` 注册；managed seed 的 `add-generic-password` 写入 private keychain（本就 copy-only），login keychain 仅用于 `find-generic-password` 读取外部凭证。这样默认钥匙串永远存在、不弹窗，且 logout/cleanup 只删 private keychain，不碰外部 login。
  - 选项 A2（更轻量）：在隔离 HOME 写入一个最小 `Library/Preferences/com.apple.security.plist`，把默认钥匙串显式指向真实 login keychain 的**绝对路径**（新版 macOS 该文件缺失，但手工 `security default-keychain -s <abs>` 会生成它，实测有效）。风险：`add-generic-password` 默认会写进 login keychain——需把所有 managed 写操作（`_seed_managed_macos_keychain_auth` 的 `add-generic-password`）显式加 `-s <private-keychain>` 或确保写的是 agent-private service 且可接受落入 login。因此 A2 必须与「写操作定向到 private keychain」搭配，否则违背 copy-only。

> 综合建议采用 **A1**：读外部 login（find）走真实 login keychain 只读，写 managed 凭证（add）走 agent-private keychain，默认钥匙串设为 private keychain。既消除弹窗，又严格维持「不回写外部权威」。

### 方案 B：进程级环境约束，避免依赖 HOME 下的钥匙串偏好

调研在启动 Claude 子进程时，通过受控的 keychain 搜索列表 / `security` 调用包装，让 find/add 都显式传 keychain 路径（`-s`/指定 keychain 文件），不依赖「默认钥匙串」这一全局态：

- `_read_macos_keychain_claude_credentials()` 的 find 已按 service 查，可显式指定在用户真实 login keychain 上查（用绝对路径调用 `security find-generic-password <keychain-path>` 或先在子进程 `list-keychains -s <login> System`）；
- `_seed_managed_macos_keychain_auth()` 的 add 显式写入 agent-private keychain。

这样不依赖 Security.framework 的「默认钥匙串」，也就不会触发「还原为默认」弹窗。改动集中在两处 subprocess 调用，比方案 A 侵入小。

### 必须同时保证的安全约束（不可回退）

1. managed provider logout / 禁用凭证继承时，**只能**删 agent-private keychain / managed `.credentials.json`，绝不能 unlock/delete/modify 用户真实 `login.keychain-db`。
2. 不得恢复「把整个 `~/Library/Keychains` 软链进隔离 HOME 且默认可写」的旧行为。
3. `ccb doctor storage` 对新增的 agent-private keychain / 偏好文件要归类为 secret auth state（沿用现有 `storage_classification` 对 `Library/Keychains` 的 SECRET 分类，见 `lib/storage_classification/provider_home.py:225`），诊断 bundle 不导出其中内容。

## 5. 测试建议

- 新增/更新 macOS 单测：在隔离（fake）HOME 下物化后，断言
  - `security default-keychain` 可解析（非 `could not be found`）；
  - `security list-keychains` 包含 agent-private keychain；
  - 模拟 `find-generic-password`（外部凭证读取）与 `add-generic-password`（managed seed）均不触发 GUI（可用对 `security` 的 stub / 录制命令行断言，确保 add 指向 private keychain、find 指向 login）。
- 回归：禁用继承 → cleanup 后，真实 login keychain 中对应条目不被删除（agent-private service 与外部 service 命名隔离，见 `_managed_macos_keychain_service()` 的 `-<suffix>`）。
- 手动验收：全新项目 `ccb` 起一个 claude agent，确认启动不再弹「找不到钥匙串」。

## 6. 参考

- 源码：`lib/provider_backends/claude/launcher_runtime/home.py`
  - `_materialize_macos_keychain_preferences()` 685–697
  - `_remove_keychains_link()` 699
  - `_materialize_macos_keychain_auth()` 977
  - `_read_macos_keychain_claude_credentials()` 1004（`find-generic-password`）
  - `_seed_managed_macos_keychain_auth()` 1078（`add-generic-password`，1108）
  - `_managed_macos_keychain_service()` / `_macos_keychain_services()`（含 `CCB_KEYCHAIN_SERVICE_OVERRIDE`）
- 存储分类：`lib/storage_classification/provider_home.py:225`（`Library/Keychains` → SECRET / `macos_keychain_link`）
- 历史背景：`CHANGELOG.md` 搜索「Claude Keychain Fallback」「Claude Keychain Preference Projection」「Claude Keychain Override」（`CCB_KEYCHAIN_SERVICE_OVERRIDE`）
