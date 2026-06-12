# OPTIMIZATION_PLAN.md 执行记录

执行日期: 2026-06-13 | 基线: v7.4.3, 测试基线 2570 passed / 2 skipped
最终状态: 2586 passed / 2 skipped / 0 failed(新增 16 个测试)

## 已完成

- **phase0.1** (4b96d83) 通讯链路静默异常接入 `~/.ccb/logs/comm.log`(`CCB_COMM_LOG_DIR` 可覆盖)。
- **phase0.2** (ee67ffc) codex/claude/gemini/opencode 四处假 "✅ Sent" 改为 "📤 Written, delivery unconfirmed"。
- **phase1.1** (a72f6aa) `PersistentFifoReader`:bridge 持久持有 FIFO 读端 + dummy 写端防 EOF 风暴,selectors 超时等待替代 50ms sleep 轮询。200 条连发零丢失回归测试。
- **phase1.2** (877268a) `write_fifo_line`:O_NONBLOCK + 0.1/0.3/0.9s 退避重试,失败抛 `CommDeliveryError`;>4KB 消息走 spool 文件 + FIFO 指针行,bridge 读后解析并删除。
- **phase1.3** (a0732a6) ACK:bridge 读到消息立即原子写 `acks/<marker>.ack`;`ask_async` 返回三态 `DeliveryResult`(bool 兼容);`ask_sync` 区分"发送未确认"与"回复超时"。
- **phase2.1** (c1b85f7) 取消标志:`cancel` 时写 `agents/<name>/cancel_flags/<job_id>.cancel`,下发执行的 prompt(仅内存副本)注入自查停止指令。

## 与计划的偏差

1. **任务 2.2(硬打断)无需实施**:调查发现 `ExecutionService.cancel()` 已经通过
   `interrupt_and_clear_runtime_target`(`lib/provider_execution/common_runtime/terminal.py:26`)
   向 pane 发送 C-c/Escape/C-u,`ccb cancel <job_id>` CLI 也已存在
   (`lib/cli/services/cancel.py`)。计划中的 `--force` 子开关不需要——默认行为已含硬打断。
2. **ask_sync/ask_async 对无 `input_fifo` 属性的 comm 对象做了防御**(测试桩
   `_Comm` 没有该属性,曾导致 1 个测试失败,已修复:getattr 缺省跳过 ACK 等待)。
3. **测试环境注意**:套件运行期间禁止编辑 lib/ —— 测试会启动 ccbd 子进程实时导入源码,
   中途编辑造成过 6 个误报失败。另:套件会遗留 ccbd 守护进程,重跑前
   `pkill -9 -f ccbd/main.py`。
4. 本机无 pytest,使用 `.venv-test/` 虚拟环境(未提交)。

## Phase 3(已完成)

- **phase3.1** (a48c74f) `lib/provider_core/transport.py`:MessageTransport 抽象,
  FifoTransport(POSIX)/ SpoolDirTransport(Windows inbox 目录,原子改名,
  time_ns+pid+counter 命名免锁排序)。`ensure_fifo` 在 Windows 上不再调
  `os.mkfifo`(原本直接 AttributeError),改为创建 inbox 目录。
  发送端与 bridge 读端均经 `create_transport`(全库唯一平台决策点)。
  对等测试:同一套场景(200 连发、超时、丢读者)参数化跑两种 transport。
- **phase3.2** (7f21f7b) `platform_info.py` 收敛 is_windows/is_macos/is_wsl;
  runtime_lock、pane matching、terminal env 已切换。
  **偏差**:backend_env.py 保留 `sys.platform` —— 其测试 monkeypatch 该接缝,
  收敛会绕过测试桩(曾导致 2 个测试失败,已回退)。
  **偏差**:inbox 序号未用计划中的 .seq 文件 + 文件锁,改用
  time_ns+pid+counter 文件名,免锁且天然有序。
- 注意:test_v2_phase2_entrypoint.py 的 gemini 重启用例在全量并发下偶发失败
  (残留 ccbd 守护进程干扰),单独重跑稳定通过;遇到时先 pkill ccbd 再重跑该文件确认。

## 未完成(后续)

- Phase 4(可选):裸路径收发经由 message_bureau 留痕。
