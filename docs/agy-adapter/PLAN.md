# agy provider — execution adapter implementation plan

Branch: `feat/agy-execution-adapter`
Status cursor: see `## Progress cursor` near the bottom — update it after every milestone.

## 1. 背景与根因

### 现象

把一个 agent 配为 `provider = "agy"`，跑 `ccb ask <agent> "..."`：

1. `ccb ask` 立刻返回 `accepted job=...`
2. `ccb pend <agent>` 显示 `status: running` `reply:` 空
3. `ccb trace <job_id>` 显示 attempt 永远停在 `state=delivering`
4. agy 自己的 tmux pane 里**完全没有**任何输入被注入，agy CLI 一直停在 `>` 提示符
5. ccbd 重启时 `ccb ping <agent>` 报：
   ```
   restore_mode: resubmit_required
   restore_reason: adapter_missing
   restore_detail: provider agy has no registered execution adapter
   ```

### 根因

`lib/provider_backends/agy/__init__.py`

```python
def build_backend() -> ProviderBackend:
    return ProviderBackend(
        manifest=build_manifest(),
        execution_adapter=None,   # ← 缺
        session_binding=build_session_binding(),
        runtime_launcher=build_runtime_launcher(),
    )
```

agy backend 显式把 `execution_adapter` 置 None，且 `provider_backends/agy/` 下根本没有
`execution.py`。其他生产可用的 provider（claude / codex / gemini / opencode / droid）都形如：

```python
from .execution import build_execution_adapter
...
execution_adapter=build_execution_adapter(),
```

后果：
- `ccb` 可以启动 agy 的 tmux pane（`runtime_launcher` 存在）
- 但 `ccb ask` 没有「把 prompt 投递到 pane / 读 reply」的执行路径
- ccbd 的 mailbox 把 attempt 停在 `delivering`，永不推进

`OPTIONAL_PROVIDER_NAMES` 里包含 `agy`（`provider_core/registry_runtime/builtin_backends.py`），
所以 agy backend 会被 `build_default_execution_adapters` 调用到，但因为
`execution_adapter=None`，注册环节直接被 `ProviderBackendRegistry.execution_adapters()` 过滤掉了：

```python
def execution_adapters(self):
    return [b.execution_adapter for b in self._backends.values()
            if b.execution_adapter is not None]
```

→ 注册表里没有 agy → `adapter_missing`。

## 2. agy 现状摸排

`lib/provider_backends/agy/` 已有：
- `__init__.py`  build_backend（待修）
- `manifest.py`  声明 `CompletionFamily.TERMINAL_TEXT_QUIET`、`SelectorFamily.FINAL_MESSAGE`、`CompletionSourceKind.TERMINAL_TEXT`
- `session.py`   基于 `provider_backends.pane_log_support`，把 `.agy-session` 文件读成 `AgyProjectSession`
- `launcher.py`  `simple_tmux` 模式，pane 起 agy CLI，工作目录 = workspace_path

→ 已经具备 pane 起得来的能力。缺的只是 execution 那一层。

manifest 已经定调：completion 检测走 **terminal-text-quiet**，selector 走 **final-message** —
意味着 adapter 不需要解析 agy 写出来的 JSONL（agy 也没有），只需要：

1. 把 prompt 字符串通过 tmux send-keys 打进 pane
2. 监听 pane 输出，等「画面安静 N 秒」或显式的 done marker
3. 抓 pane 中模型回复段当 reply

## 3. 设计与对照参考

### 对照表

| Provider | completion family | reply 来源 | 抓 reply 方式 |
|---|---|---|---|
| claude | session_event_log | 内置 JSONL session log | 解析 jsonl |
| codex | session_event_log | codex JSONL log | 解析 jsonl |
| droid | TERMINAL_TEXT_QUIET | pane / droid log | log + pane 抓取 |
| opencode | session_event_log | `~/.local/share/opencode` storage | 读 storage |
| **agy（目标）** | TERMINAL_TEXT_QUIET | **pane 输出** | pane 抓取 + done marker / 静默检测 |

droid 是结构最接近的，但 droid 还有 `comm.py` + `comm_runtime/` 一整套抓 droid log 的支撑。
agy 没有可用的 log 文件 → 只能纯靠 pane 抓取。

### 可复用的现成工具

`lib/provider_backends/pane_log_support/` 是给所有 pane-based provider 准备的共享层，已经被
codex / gemini / opencode / droid session 复用：
- `reader.py` / `reader_runtime/`  pane 输出读取
- `communicator.py`                 send-keys 抽象
- `parsing.py`                       文本解析助手
- `lifecycle*.py`                    生命周期

→ agy execution adapter **只在这套支撑上薄薄包一层**，不再自造轮子。

## 4. 里程碑

### M1 — Stub adapter（先脱离 delivering）

**目的**：让 `ccb ask` 至少能把 prompt 真的打进 agy pane，
且 ccbd 立刻把 attempt 标 terminal，job 状态从 `running/delivering` 走到 `completed`，
reply 可以是空或者写「(stub mode, see agy pane)」。

**交付物**：
- 新增 `lib/provider_backends/agy/execution.py`
  - 类 `AgyProviderAdapter` 实现 `provider = "agy"`、`start`、`poll`、`resume`
  - `start(job, context, now)`：
    - 通过 `agy.session.load_project_session(work_dir)` 拿 session
    - `terminal_runtime.get_backend_for_session(session.data)` 拿 backend
    - backend.send(prompt + Enter)
    - 返回 `ProviderSubmission(status=INCOMPLETE, runtime_state={"prompt_sent": True, "sent_at": now, "pane_id": ...})`
  - `poll(submission, now)`：直接返回 terminal `ProviderPollResult`，
    `decision.terminal=True`，reply 用预置 stub 文字 / 空串
  - `resume()`：照搬 droid 写法，返回 None（resubmit_required）
  - `build_execution_adapter()` 工厂
- 修改 `lib/provider_backends/agy/__init__.py`：
  - `from .execution import build_execution_adapter`
  - `execution_adapter=build_execution_adapter()`

**验证**：
1. `ccb kill && ccb`（重启 ccbd 让新 adapter 被加载）
2. `ccb ask <agent> "M1 stub test"`
3. `ccb pend <agent> 5` → status `completed`，不再卡 delivering
4. `tmux ... capture-pane -p` → 能看到 `M1 stub test` 被键入 agy pane

**回滚**：
- `git checkout main`
- 然后 `ccb kill && ccb` 重启

### M2 — 完整 adapter（pane done marker + 静默检测兜底）

**目的**：实现真正的「等 agy 答完 → 抓 reply → 上报」链路，能在 ccb 里直接看到 agy 回答内容。

**交付物**：
- `lib/provider_backends/agy/comm.py`     `AgyPaneReader`：pane snapshot + ANSI 剥离
- `lib/provider_backends/agy/protocol.py` prompt 包装（`CCB_REQ_ID` / `CCB_DONE` 锚点）+ reply 抽取
- `lib/provider_backends/agy/execution_runtime/`  辅助
  - `start.py`    submission 流程、prompt 注入、runtime state 初始化
  - `poll.py`     `pane_quiet` 状态机（hash 内容 + done marker + 静默窗口）
  - `helpers.py`  共享工具：路径解析、hash、时间差
- 更新 `execution.py` 由 stub 重构为 shim，委托给 `execution_runtime/`

**完成检测策略**（按优先级）：

1. **黄金路径**：done marker 命中（pane 里出现 `CCB_DONE: <id>` 至少 2 次，最后一个是模型的）
   → `status=COMPLETED, reason=pane_done_marker, confidence=OBSERVED`
2. **兜底**：reply 非空 + 观测时长 ≥ 2s + pane hash ≥ 4s 无变化
   → `status=COMPLETED, reason=pane_text_quiet, confidence=DEGRADED`
3. **硬超时**：总等待 ≥ 300s
   → `status=FAILED, reason=pane_quiet_timeout`
4. **送达失败**：start 阶段 send_text 抛错
   → `status=FAILED, reason=send_failed:<err>`

**为什么用 done marker 计数而非位置区分**：

Antigravity TUI 把 prompt 回显和模型回答以**相同缩进**渲染，echo-DONE 和 model-DONE
靠行前缀无法区分。协议靠**顺序**：prompt 指令模型「把 CCB_DONE 写在最后一行」，
所以最后一个 DONE 是模型的，倒数第二个（若有）是 echo。0 个 → 还在写；1 个 → 只看到 echo；
≥2 个 → 取最后两个之间的内容当 reply。

**验证**：
1. `ccb ask <agent> "回答 1+1 等于几"`
2. `ccb pend <agent> 5` → status `completed`，reply 包含 `2`
3. `ccb trace <job>` → terminal decision 中 `reason=pane_done_marker`, `confidence=observed`

### M3 — 单元测试 + 边界覆盖

**交付物**：
- `pytest` 覆盖 `extract_reply_for_req` 关键场景：多 DONE 计数、banner 过滤、空快照
- `poll_submission` 状态机的 happy path / quiet path / timeout path 测试
- 长 reply（>10KB）与 carriage return 输出（pip 进度条等）的 hash 抖动测试

## 5. 工程约束

- 改动只能在 `lib/provider_backends/agy/` 范围内 + 改 1 个 import 在 `agy/__init__.py`。
  不动 `provider_core/`、`provider_execution/`、`pane_log_support/` 任何代码。
- 保持 manifest 已声明的 `TERMINAL_TEXT_QUIET` 不变；任何对 manifest 的调整需要写理由。
- 不引入新外部 Python 依赖。
- 注释为零是底线；只有非显然的「为什么」才写一行。
- M1 stub 必须能独立通过验证项，不能为 M2 留接口债。

## 6. 回滚预案

任何时候出问题：

```bash
git checkout main
ccb kill && ccb
```

ccb 装在 source 模式（doctor: `install_mode: source`）时，切回 main 即恢复改动前的版本。

## 7. 已知踩坑

- ccbd 重启会重建 namespace（lifecycle.jsonl 有记录），重启后跑的所有 agy job 都会重新走 adapter；
  所以每次改完 execution.py 都必须 `ccb kill && ccb`，不能只重启 agy pane。
- WSL 等长 namespace 场景下 ccb 的 socket 会走 runtime 短路径备选位置
  （doctor `socket_fallback_reason: path_too_long`），改动不影响这条逻辑。
- agy CLI 启动慢且界面带 ASCII logo / 输入框 banner，pane 抓取要跳过这些 banner。
- agy CLI 与 ccb 不同宿主（例如 agy 在 Windows / ccb 在 WSL）时，agy pane 显示的工作目录
  与 ccb 侧 `work_dir` 字符串形态不同。对 adapter 来说只需要把 `work_dir` 透传给 backend，
  agy 那头自己处理路径。

## 8. Progress cursor

> 每完成一个 milestone 都要更新这里。续命用。

- [x] M0 — Plan 文档
- [x] M1 — Stub adapter（合并入本 PR 的 M2 commit）
- [x] M2 — pane_quiet adapter（done marker + 静默兜底，运行时验证通过）
- [ ] M3 — 单元测试 + 边界覆盖

当前状态：**M1 + M2 合并为单个 commit 提交 PR；M3 留作后续 PR**

### 验证记录

环境：ccb v7.3.3（dev channel），agy 1.0.6（Antigravity CLI / Gemini 3.1 Pro 后端）

**Smoke**：

- `ccb ping ccbd` → healthy
- `ccb ask <agent> "reply pong"` → `accepted`
- `ccb pend <agent> 5` →
  - `status: completed`
  - `reply: pong`
  - `completion_reason: pane_done_marker`
  - `completion_confidence: observed`

**多步任务**（10+ tool calls，~2 分钟）：

- reply 2886 字节完整捕获
- prompt 回显里的 banner 指令被 `_BANNER_INSTRUCTIONS` 过滤，无污染
- 终止：`pane_done_marker` / OBSERVED（黄金路径）

### 回滚

```bash
git checkout main
ccb kill && ccb
```
