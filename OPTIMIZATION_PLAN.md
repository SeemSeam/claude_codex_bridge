# CCB 通讯可靠性改造 — 实施任务书

> 版本: v1.0 (2026-06-13)
> 适用代码版本: v7.4.3
> 本文档面向执行模型: 每个任务自包含, 按顺序执行, 每个任务完成后必须跑验收命令再进入下一个。
> **重要**: 文中 file:line 引用基于 v7.4.3 调查结果, 动手前先打开文件确认行号仍然准确; 若代码已变动, 以本文描述的"行为特征"定位代码, 不要盲改。

---

## 一、背景: 用户报告的三个核心症状

1. **静默丢消息**: cc(Claude) 给 cx(Codex) 发消息, 发送方显示 "✅ Sent", 接收方从未收到, 双方都以为成功。间歇性发生。
2. **平台行为不一致**: macOS 和 Windows/PC 上行为不同, Windows 上通讯机制更不可靠。
3. **任务无法撤销**: 主控(cc) 派错任务后被真人纠正, 但 cx 手里的任务无法打断, 必须等它跑完。

## 二、根因分析(已完成的调查结论)

### 2.1 存在两条并行通讯路径

- **路径 A (设计良好, 但未被全程使用)**: ccbd 中央守护进程
  - `lib/ccbd/app.py` — 守护进程, Unix socket JSON-RPC
  - `lib/ccbd/services/dispatcher.py:93-96` — JobDispatcher, 持有 JobStore + MessageBureauFacade
  - `lib/message_bureau/facade.py:30-160` — 消息状态机 (QUEUED→DELIVERING→COMPLETED/FAILED/CANCELLED/SUPERSEDED/DEAD_LETTER)
  - `lib/mailbox_kernel/service.py:43-220` — 收件箱内核, 有 `claim_next()/ack_reply()/abandon()/supersede()` 和投递租约 (DeliveryLease)
  - `lib/message_bureau/control_queue_runtime/ack.py:29-64` — ACK 机制已实现
- **路径 B (裸路径, 丢消息的来源)**: 直接 FIFO 写入
  - `lib/provider_backends/codex/comm_runtime/communicator_io_runtime/asking.py:10-35` — `send_message()` 直接 `open(fifo, "w")` 写一行 JSON, `ask_async()` 只要不抛异常就打印 "✅ Sent" 并 return True
  - `lib/provider_backends/codex/bridge_runtime/runtime_io.py:14-24` — 读端每个循环 **打开 FIFO→读一行→关闭**, 异常时 `except (OSError, JSONDecodeError): return None` 静默吞掉
  - `lib/provider_backends/codex/bridge_runtime/service.py:68-75` — 读循环里 `time.sleep(idle_sleep)` 默认 50ms, **睡眠期间 FIFO 无读者**, 此时写端 open 会阻塞或报错 → 这就是间歇性丢消息的时序窗口

### 2.2 最后一公里无确认

- 发送方的"成功" = `open+write+flush` 没抛异常, **没有任何"对方已读取"的确认**。
- claude 侧 tmux 路径同样: `lib/provider_backends/claude/comm_runtime/communicator_facade.py:86-90` — `send_text()` 无返回值, 函数无条件 `return True`。
- 发送失败无重试、无退避、无超时。

### 2.3 Windows 上 FIFO 根本不存在

- `lib/provider_backends/codex/launcher_runtime/runtime_state.py:22-27` — `ensure_fifo()` 直接调 `os.mkfifo(path, mode)`, **无平台检查**。Windows 上 `os.mkfifo` 不存在, 抛 AttributeError, 且无 fallback。
- WSL 检测代码存在但只用于 HOME 路径设置, 不影响 FIFO。

### 2.4 取消状态传不到工作 pane

- message_bureau 有 CANCELLED/SUPERSEDED 状态, mailbox_kernel 有 `abandon()/supersede()`, **但这些只改库里的状态**, 没有任何机制通知/打断正在 tmux pane 里跑的 agent。agent 不检查取消标志, 也没人给它的 pane 发中断键。

### 2.5 全库 913 处 `except Exception`

大量静默吞错, 通讯路径上的失败被埋掉, 用户看不到任何告警。

## 三、改造原则

1. **建于现有设施之上, 不新建 broker**。mailbox_kernel/message_bureau/jobs 的状态机和持久化是好的, 问题在裸路径和最后一公里。
2. **不引入 SQLite**。现有 JSON/JSONL 原子写已够用, 引入数据库是无谓的迁移风险。
3. **每个任务独立可验证**, 改完跑 `./ccb_test`(或 `python -m pytest test/ -x -q`)回归。
4. **行为兼容优先**: 对外 CLI/MCP 接口不变, 只改内部可靠性。

---

## 四、任务清单(按顺序执行)

### Phase 0 — 让失败可见(最低风险, 先做)

#### 任务 0.1: 通讯路径上的静默异常全部加日志

**目标**: 不改任何行为, 只把通讯链路上被吞掉的异常记录下来。

**改动文件**:
- `lib/provider_backends/codex/bridge_runtime/runtime_io.py:23` — `except (OSError, json.JSONDecodeError): return None` 改为捕获后先写日志再 return None。
- `lib/provider_backends/codex/comm_runtime/communicator_io_runtime/asking.py:33` — `except Exception` 分支除了 print, 同时写结构化日志。
- 搜索 `lib/provider_backends/*/comm_runtime/` 和 `lib/provider_backends/*/bridge_runtime/` 下所有 `except` 后直接 `return None`/`pass`/`continue` 的位置, 逐一加日志。

**实现要求**:
- 新建 `lib/provider_core/comm_logging.py`, 提供 `get_comm_logger(name)`, 使用标准 logging, 写入 `~/.ccb/logs/comm.log`(目录从现有配置体系取, 搜索代码中已有的日志目录约定并复用; 如果项目已有统一 logger 设施, 用现有的, 不要重复造)。
- 日志格式必须含: 时间戳、provider、方向(send/recv)、fifo 路径或 pane id、异常类型和 message。
- 日志写入本身的失败必须被吞掉(日志不能反过来弄崩通讯)。

**验收**:
1. `python -m pytest test/ -x -q` 全绿(或与改动前 baseline 相同的失败集)。
2. 人为制造一次失败(把某个 session 的 input_fifo 替换为普通文件后发消息), 确认 comm.log 里出现对应记录。

---

#### 任务 0.2: 发送成功语义收紧 — 禁止假 "✅"

**目标**: `ask_async()` 在没有读端确认前不再打印 "✅ Sent", 改为 "📤 Written (unconfirmed)"。这是诚实性修复, 为 Phase 1 的真确认做铺垫。

**改动文件**:
- `lib/provider_backends/codex/comm_runtime/communicator_io_runtime/asking.py:25-35`
- claude 侧: `lib/provider_backends/claude/comm_runtime/communicator_facade.py:86-90` — `_send_via_terminal` 的 docstring 和调用方提示语同步修改。

**验收**: 现有测试中所有断言 "✅ Sent" 字样的测试同步更新; `grep -rn "Sent to Codex" lib/ test/` 确认无遗漏。

---

### Phase 1 — 修复 FIFO 最后一公里(核心修复)

#### 任务 1.1: 读端持久持有 FIFO, 消灭 50ms 失聪窗口

**目标**: bridge 读循环不再"每读一行就关 FIFO"。

**改动文件**: `lib/provider_backends/codex/bridge_runtime/runtime_io.py` 和 `service.py`

**实现要求**:
- 在 `BridgeRuntimeState` 上持有一个长期打开的 FIFO 读文件对象。
- 正确处理 FIFO 的 EOF 语义: 当所有写端关闭后 `readline()` 返回 `''`(EOF), 此时**不要关闭重开盲等**, 用如下模式:
  ```python
  # 打开时同时持有一个写端, 防止 EOF 风暴:
  # read fd 用 os.open(path, os.O_RDONLY | os.O_NONBLOCK) 打开,
  # 再 os.open(path, os.O_WRONLY) 持有一个 dummy 写端,
  # 之后用 selectors 监听 read fd 可读, readline 永不见 EOF。
  ```
- 用 `selectors.DefaultSelector` + 超时轮询替代 `time.sleep(idle_sleep)`: select 阻塞等数据, 超时则检查 `self._running`。CPU 占用更低且零失聪窗口。
- bridge 退出时(`stop()`)关闭两个 fd, 删除行为保持与现状一致。
- 处理一行内多条消息/半条消息: 维护一个字节缓冲, 按 `\n` 切分, 不完整的尾部留到下次。

**验收**:
1. 新增测试 `test/test_bridge_fifo_persistent_reader.py`:
   - 启动 bridge 读循环(用现有 test/stubs 的方式), 连续高频发送 200 条消息(无间隔), 断言 200 条全部被接收、顺序正确。
   - 这个测试在旧实现下应当能复现丢失(先在旧代码上跑一次确认它确实 fail, 再应用修复)。
2. 现有 `test/test_ccbd_comms_recover.py` 等通讯相关测试全绿。

---

#### 任务 1.2: 写端加超时与重试

**目标**: 发送方 open FIFO 永不无限阻塞; 瞬时失败自动重试。

**改动文件**: `lib/provider_backends/codex/comm_runtime/communicator_io_runtime/asking.py`

**实现要求**:
- 用 `os.open(path, os.O_WRONLY | os.O_NONBLOCK)` 尝试打开; 若抛 `OSError(ENXIO)`(无读者), 按 0.1s/0.3s/0.9s 退避重试 3 次, 全部失败则抛 `CommDeliveryError`(新建异常类型, 放 `lib/provider_core/` 下), 上层捕获后明确报 "❌ 无法投递: 接收端未在监听"。
- 写入用 `os.write` 一次性写完整行(FIFO 上 ≤ PIPE_BUF=512B/4KB 的写是原子的; 超长消息见下一条)。
- **超长消息处理**: 若 JSON 超过 4096 字节, 不直接写 FIFO 正文, 改为把正文落盘到 spool 文件(`<session_dir>/spool/<marker>.json`), FIFO 里只写一行 `{"marker":..., "spool": "<path>"}` 指针。读端看到 spool 字段就去读文件。这同时解决了大消息的原子性问题。
- 重试和最终失败都写 comm.log(用任务 0.1 的 logger)。

**验收**:
1. 新增测试: 无读者时发送 → 断言 3 次重试后抛 `CommDeliveryError`, 总耗时 < 2s。
2. 新增测试: 8KB 大消息 → 断言走 spool 路径且读端完整收到。

---

#### 任务 1.3: 读取确认(真 ACK)

**目标**: 发送方能确认"bridge 已读到这条消息", 才打 "✅"。

**实现要求**:
- bridge 读到一条消息并解析成功后, 立即写确认文件: `<session_dir>/acks/<marker>.ack`(内容为时间戳, 原子写: 先写 tmp 再 rename)。marker 已存在于消息体中(`asking.py:11-16` 生成)。
- 发送方 `ask_async()` 写完 FIFO 后, 轮询 ack 文件最多 5s(0.05s 间隔, 指数放宽到 0.5s); 拿到 ack → 打 "✅ Delivered"; 超时 → 打 "⚠️ Written but unconfirmed (receiver may be busy)" 并写 comm.log, **return True 改为返回三态**: 新建 `DeliveryResult` 枚举 (DELIVERED / UNCONFIRMED / FAILED), 调用方按需处理(搜索所有 `ask_async` 调用点同步适配, 保持向后兼容可加 `bool(result)` 语义: DELIVERED/UNCONFIRMED 为真, FAILED 为假)。
- acks 目录定期清理: bridge 每次启动时删除 24h 前的 ack 文件。
- `ask_sync()` 复用同一机制, send 阶段确认后再进入等回复阶段; 这样能区分 "发送失败" 和 "回复超时" 两种错误, 错误信息分别明示。

**验收**:
1. 新增测试: 正常收发 → DeliveryResult.DELIVERED, ack 文件存在。
2. 新增测试: bridge 不在跑 → FAILED(由任务 1.2 的重试失败触发)。
3. 新增测试: bridge 暂停(收到但不处理)场景模拟 → UNCONFIRMED。

---

### Phase 2 — 任务取消打通到 pane(解决"派错任务无法撤回")

#### 任务 2.1: 取消标志的落地与检查协议

**目标**: 主控取消一个任务后, 工作 agent 能在工作间隙感知并停手。

**实现要求**:
- message_bureau 已有 CANCELLED/SUPERSEDED 状态(`lib/message_bureau/` 的 AttemptState)。新增: 当任务被标记取消时, 在目标 agent 的 session 目录写 `cancel_flags/<job_id>.cancel` 标志文件(原子写)。挂接点在 `lib/ccbd/services/dispatcher.py` 的 `cancel()` 路径(先读懂现有 cancel() 做了什么, 在状态落库之后追加写标志文件)。
- 在发给 agent 的任务消息模板中(找到 message_bureau 组装下发 prompt 的位置), 注入一段标准指令: "开始每个子步骤前, 检查文件 <cancel_flag_path> 是否存在; 若存在, 立即停止当前任务, 回复 CANCELLED 并等待新指令。" — 这是协议层约定, 让 agent 自查。
- 提供 CLI: `ccb cancel <job_id>`(若已有类似命令则增强之; 先 `grep -rn "def.*cancel" lib/cli/` 确认现状)。

**验收**: 新增集成测试: 派一个长任务给 stub provider → 标记取消 → 断言 cancel 标志文件出现、job 状态变为 CANCELLED。

#### 任务 2.2: 硬打断 — 向 pane 发送中断键

**目标**: 软标志之外的强制手段, 立即打断正在生成的 agent。

**实现要求**:
- 新增 `lib/terminal_runtime/` 下的 `interrupt_pane(pane_id)`: 通过现有 tmux 封装(复用 `lib/terminal_runtime/tmux_send.py` 的 `tmux_run_fn` 模式)发送 `send-keys -t <pane> Escape`(Codex/Claude CLI 均以 Esc 打断生成; 各 provider 的打断键允许在 provider backend 里覆写, 在 provider 抽象上加 `interrupt_keys() -> list[str]`, 默认 `["Escape"]`)。
- `ccb cancel <job_id> --force` 触发: 先写软标志, 再向该 job 绑定的 pane 发打断键, 然后通过路径 A 给该 agent 发一条高优先级消息说明任务已取消。
- 打断后不自动派新任务, 把控制权交还主控/用户。

**验收**: 集成测试(标记 `provider_blackbox`, 需要真实 tmux): 在 stub pane 里跑一个长命令 → `ccb cancel --force` → 断言命令被中断。

---

### Phase 3 — Windows/跨平台传输层

#### 任务 3.1: 传输抽象 + Windows fallback

**目标**: Windows 上通讯走文件收件箱轮询, 与 FIFO 行为对齐; macOS/Linux 保持 FIFO。

**实现要求**:
- 新建 `lib/provider_core/transport.py`, 定义 `MessageTransport` 抽象: `send_line(text) -> None`(可抛 CommDeliveryError)、`read_lines() -> Iterator[str]`、`close()`。
- 实现 `FifoTransport`(包装 Phase 1 的成果)和 `SpoolDirTransport`(Windows): 写端把每条消息原子写入 `<session_dir>/inbox/<单调序号>-<marker>.json`(先写 `.tmp` 再 `os.replace`), 读端按文件名排序轮询消费后删除。序号用 `<session_dir>/inbox/.seq` 文件 + 文件锁递增(Windows 上用 `msvcrt.locking`, POSIX 用 `fcntl.flock`; 项目里 `lib/provider_core/runtime_lock.py` 已有跨平台锁, **先读它, 能复用就复用**)。
- `ensure_fifo()`(`launcher_runtime/runtime_state.py:22-27`)改为: POSIX 走 mkfifo, Windows 创建 inbox 目录。选择逻辑集中在 transport 工厂函数 `create_transport(session_info)` 里, **全库只允许这一处做平台判断**。
- ACK 机制(任务 1.3)在两种 transport 下行为一致(ack 本来就是文件, 天然跨平台)。

**验收**:
1. SpoolDirTransport 的单元测试在 macOS 上也能跑(它不依赖 Windows API, 锁用条件分支测 POSIX 路径)。
2. 同一套收发测试参数化跑两种 transport, 断言行为一致(200 条连发不丢、大消息、ACK)。

#### 任务 3.2: 平台判断收敛(顺手做)

把通讯路径上散落的 `os.name == 'nt'`/WSL 检测收敛到 `lib/provider_core/platform_info.py`: `is_windows()/is_wsl()/is_macos()` 三个函数 + 模块级缓存。只改通讯链路涉及的文件(`runtime_lock.py`, `terminal_runtime/backend_env.py`, `pane_registry_runtime/common_runtime/matching.py:20`), 其他 10+ 处留待后续, 不在本期范围。

**验收**: `grep -rn "os.name" lib/provider_core/ lib/terminal_runtime/` 仅剩 platform_info.py 一处。

---

### Phase 4 — 收口: 裸路径并入调度路径(可选, 最后做)

#### 任务 4.1: ask_async/ask_sync 经由 ccbd 记录

**目标**: 路径 B 的每次收发都在 message_bureau 留痕, 使所有消息可查、可重放、可取消。

**实现要求**:
- 不改变投递机制(仍走 transport), 只在发送前调 message_bureau 的 `record_submission()`、确认后调对应的状态更新(先读 `lib/ccbd/services/dispatcher_runtime/submission_recording.py` 学习现有用法)。
- ccbd 不在跑时降级为纯 transport 模式(现在的行为), 写 comm.log 提示。

**验收**: 发一条消息后, 通过现有的查询接口(找 `lib/ccbd/project_view/` 或 CLI 的消息列表命令)能看到这条记录及其 DELIVERED 状态。

---

## 五、执行约束(给实施模型的硬性要求)

1. **顺序执行**, 每个任务一个独立 commit, commit message 用 `phaseN.M: <概要>` 格式。
2. 动手前先跑 `python -m pytest test/ -q` 记录 baseline 失败集; 之后每个任务完成时, 失败集不得新增。
3. 涉及 tmux 的集成测试若本机环境跑不了, 标记 skip 并在 commit message 注明, 不得删除测试。
4. 不重命名/移动现有公共 API(被 test/ 或 mcp/ 或 bin/ 引用的符号); 新能力一律新增符号。
5. 每个 `except Exception` 的收紧只允许发生在本文点名的文件; 其余 900+ 处不动。
6. 禁止引入新第三方依赖(标准库 only); 禁止引入 SQLite。
7. 遇到与本文档描述不符的代码现状(行号漂移、机制已变), 停下来在 PLAN_NOTES.md 里记录差异和你的调整决策, 再继续。

## 六、不在本期范围(明确排除)

- 拆分巨型模块(project_view/service.py 等) — 另立项目。
- 轮询全面改 async/事件驱动 — Phase 1 的 selectors 已消除最痛的轮询。
- 全库 913 处异常处理治理 — 只治通讯链路。
- plans/、archive/ 等仓库卫生清理 — 与可靠性无关, 单独半天任务。
