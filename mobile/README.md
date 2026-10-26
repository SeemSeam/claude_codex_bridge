# CCB Mobile

Native Android/iOS/iPadOS remote controller for server-side CCB tmux projects.

This directory is the authoritative CCB Mobile source inside the CCB monorepo.
The legacy standalone `ccb_mobile` repository is retired as an implementation
surface and now exists only for migration/runtime compatibility notes.

CCB and provider CLIs run on the server; the mobile app is a controller for
project discovery, agent switching, terminal access, Markdown reading,
notifications, local/Tailnet gateway access, and file transfer.

## v8.7.19: readable complete content / 可读完整内容

“Complete content” replaces the raw JSON original viewer with a full-screen Markdown reader. Assistant prose is shown first; reasoning, tool calls and tool results are independently folded. Server content schema v2 sends semantic fragments with stable block identities, continuation markers and immutable versions, never the original serialized item envelope. Pages carry at most 32 KiB of source text within the existing 192 KiB response budget; the phone retains only the current page and refetches previous pages by fenced cursor. Split Markdown code fences receive display-only context without modifying source text. Update gateway and app together; schema v1 JSON slices are deliberately not rendered as fallback.

“查看完整内容”改为全屏 Markdown 阅读页，正文优先，思考、工具调用及结果独立折叠，不再展示 JSON 包装字段、转义换行或字节偏移。服务端 v2 协议按语义块分页，保持块身份、续页标记及不可变版本；单页原始文本最多 32 KiB，仍受 192 KiB 响应预算限制。手机只保留当前页，上一页通过绑定游标重新读取；代码围栏跨页使用仅用于显示的上下文，不改变源文字。网关和 App 需一起升级，不以原始 JSON 分片作为兼容显示。

Qualification: 105 targeted Python tests, 21 runtime tests and 54 targeted Flutter tests passed; Flutter analysis reported no issues. Android API 35, actual OMP 18.12.6 native host and encrypted Relay passed the full long-history workflow, readable Markdown/code rendering, independent execution disclosures, next/previous navigation and close. The fixture traversed 202 history items and reconstructed six semantic blocks across 29 content pages with exact projected-source text hashes. Evidence: `~/.local/state/ccb-mobile-complete-content-vm-final/`. Source changes only; not installed or published.


## v8.7.19: native OMP questions / OMP 原生交互问题

Live Collab `ui-request` select/editor dialogs appear as a separate interactive card, with option descriptions and custom text input. Submit uses the native `ui-response` channel, not a chat prompt. Session, generation, namespace and request ID fences reject stale answers; disconnect/end clears pending requests, and uncertain delivery is never automatically retried. While a dialog is pending, ordinary prompts cannot answer it and queued prompts do not drain through it. Requests are ephemeral, not restored from phone cache. Bounded requests support up to 16 options and 512-character responses; unsupported/oversized dialogs must be answered on the desktop.

手机端将 Collab 原生选择/编辑对话显示为独立可交互卡片，支持选项说明与自定义文字。提交走原生 `ui-response`，不再把选项当普通聊天消息排队。会话、代次、命名空间与请求编号共同防止旧答案误投；断线或结束清除请求，发送结果不明时不自动重试。请求不写入手机缓存。支持最多 16 个选项与 512 字符回复；超出有界限制的对话仍需桌面处理。服务端桥、gateway 与 App 需要一起更新；不要重启忙碌 Agent 来应用升级。

## v8.7.18: stable OMP history and bounded latest bubbles / 稳定长历史与有界最新气泡

OMP Mobile reads complete user-anchored turns from bounded native parent-chain pages rather than requiring a full in-memory conversation replica. Native pages contain at most 128 records and 256 KiB; oversized records use lossless byte chunks, and the existing original reader pages complete Unicode text and tool results. The live bridge retains at most 512 recent entries / 4 MiB. History-query errors preserve a healthy connection and loaded phone content; owner or transport failures remain explicit. Append-stable cursors are fenced to the native session and branch, so a real branch switch cannot merge unrelated history.

OMP Mobile 改为按原生父链分页读取完整回合，不再要求先复制全部历史。每页最多 128 条、256 KiB，超大记录分块无损读取，完整 Unicode 正文与工具结果继续通过原文阅读器分页查看。实时桥只保留最多 512 条 / 4 MiB 近期记录；历史查询失败不会关闭健康连接或清除手机已加载内容。游标支持同分支追加，并在真实会话/分支切换时失效，避免串历史。

Oversized reply bubbles display a bounded structured latest-content window: at most 8 entries / 24 blocks and 24 KiB of serialized ASCII JSON including the original-content cursor. Latest assistant prose is prioritized (up to 8,000 characters, reduced when byte limits require); recent tool/reasoning snippets are bounded to 1,500 characters each. Refresh replaces this window under the same bubble identity rather than accumulating older windows. Complete originals are unchanged. User inputs retain a bounded readable tail.

超长回复不再只剩“超出内联限制”提示：气泡直接显示有界的最新正文及可折叠工具/推理信息，最多 8 条记录、24 个块，含原文游标的实际序列化内容不超过 24 KiB。优先保留最新正文（最多 8,000 字符，字节预算不足时缩短），工具/推理单块最多 1,500 字符；同一气泡刷新时替换窗口，不累积旧窗口，完整原文不变。

Bounded-bubble verification: 101 Python regressions, 21 runtime tests and 62 targeted Flutter tests passed; Flutter analysis reported no issues. Android API 35 with real encrypted Relay/native host passed oversized bubble text/process rendering, same-bubble live tail ONE→TWO replacement, complete original hash, long-history traversal, branch isolation and FIFO. Evidence: `~/.local/state/ccb-mobile-bounded-bubble-vm-pass/`.

Upgrade both the server gateway and mobile app to v8.7.18. Existing OMP processes load the new native bridge after a normal restart when idle; do not restart busy agents or discard history to apply it.

Local Android API 35 emulator qualification passed through the real encrypted Relay, native OMP 18.12.6 host and generated bridge: 28,702 records, 141,678,063 raw history bytes, 63,257,173 replicated message bytes, and a 19,001-record metadata gap. All 200 conversation items were read across 99 pages; the 987,082-byte Unicode/tool original matched its SHA-256 after 31 content pages. Injected history failures preserved the live connection and loaded content without additional join/leave notices; append replay, genuine branch isolation, FIFO and foreground/background lifecycle checks passed. Native pages stayed within 262,144 bytes. Evidence: `~/.local/state/ccb-mobile-history-wire-vm-evidence/`. This is isolated emulator validation, not proof of activation in the live nozeli process.

请同时升级服务端 gateway 与手机 App 至 v8.7.18。已有 OMP 进程需在安全空闲时正常重启以加载新原生桥；不要为升级中断忙碌 Agent 或清空历史。

[Full bilingual notes](../docs/releases/v8.7.18.md).


## v8.7.17: OMP reply receipts / OMP 回复收据

OMP reply delivery uses bounded, owner-bound native `reply_deliver` / `reply_status` calls. A `ccb-reply` arrives as an aside at a safe step boundary; only its persisted native `custom_message` counts as a receipt and releases the mailbox. This does not mean the agent has finished subsequent business processing. The composer and active ask anchor are preserved, no concurrent turn starts, and ambiguous sends are queried instead of replayed. Existing OMP processes load the new extension after a normal idle restart; do not restart a busy session.

OMP 回复采用有界、绑定归属的原生 `reply_deliver` / `reply_status` 调用，在安全步骤边界将 `ccb-reply` 作为 aside 接收。只有已持久化的原生 `custom_message` 才算收据并释放邮箱，不代表 Agent 已完成后续业务处理。保留输入草稿与活跃 ask 锚点，不启动并发回合；结果不明时查询状态，不重放。已有 OMP 进程需空闲后正常重启以加载新扩展，不要重启忙碌会话。

[Full bilingual notes](../docs/releases/v8.7.17.md).

## Android Alpha Release

CCB Mobile v8.7.19 release APK:

- [Download ccb-mobile-v8.7.19.apk](https://github.com/SeemSeam/claude_codex_bridge/releases/download/v8.7.19/ccb-mobile-v8.7.19.apk)
- Server setup entrypoint: `ccb update mobile`
- App source: [`app/`](app/)

### Android app updates

The Android app checks the latest GitHub release after startup and also exposes
a manual **Check for updates** action in the connection settings. Release
metadata and the APK SHA-256 digest are accepted only from the canonical GitHub
repository. The preferred API path also requires the manifest URL, APK URL,
size, and SHA-256 to match GitHub's release-asset metadata. When the APK itself
is difficult to download directly, its bytes may fall back through
`gh-proxy.com`, `ghfast.top`, and `ghproxy.net`, in that order, but proxy
responses must match the trusted manifest's exact size and SHA-256 digest.
Proxies are never trusted to provide release metadata. Android still requires
the user to approve the signed APK update; the app does not perform silent
installation.

The app is designed for real server-side CCB projects, not a demo-only flow.
It connects to the server-wide mobile gateway, lists mounted CCB projects,
renders agent transcripts, sends pane-native text input, opens terminal views,
and supports image/document upload and download through the authenticated
gateway.

Selected-agent headers also expose the active Provider/model/thinking state.
The Provider sheet uses capability-driven model and effort choices, separates
configured/pending state from the active native session, and displays native
session usage plus optional account quota. These semantics are directly
aligned with pinned open-source Paseo Provider contracts; see
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

### Native OMP conversation turns

OMP conversations are projected from the live Collab snapshot and events into
stable user-anchored turns. One reply bubble contains the latest assistant answer;
visible reasoning, intermediate messages, tool calls and results remain in an
ordered, initially collapsed execution process. Tool calls retain their native
call IDs. Missing outcomes and unfinished tools are not presented as success.

`app/lib/native_session/native_session.dart` exports the reusable v1 turn/block
model and `NativeTurnBody`. This module has no CCB project, job, terminal or
localization dependency: hosts supply their Markdown renderer and labels, so a
future Nozeli mobile client can consume the same content contract. CCB stores
the optional `native_turn` projection with its conversation snapshots.

Only Linux servers enable the new OMP Collab route: a private loopback relay
and owner-bound local bridge attach to the existing TUI. Linux verifies socket
ownership through `/proc`. macOS retains the original OMP terminal-input and
native-session-history route; it does not start the Collab supervisor or expose
the new live model controls. Other Providers retain their existing routes.

The mobile app selects Collab only when the server advertises
`provider_control.runtime_source=collab`; an OMP provider name alone is not enough.
Existing Linux OMP processes must be restarted normally to load the supervisor.
Unavailable Linux Collab produces an explicit disconnected banner, never a silent
terminal/history fallback. Explicit project views refresh native OMP activity for
working counts; live conversation and task invalidation watches follow the selected
foreground agent and pause while the app is backgrounded.

OMP model/effort choices come from the running agent's native model registry.
The Provider sheet switches that same session through a restricted native
companion, confirms host readback, and does not edit project configuration or
restart the agent. Busy sessions, desktop drafts and stale session/model
revisions reject changes; observable mutation races report an unknown outcome.

The selected OMP workspace mirrors the native status bar: current run elapsed
time, the current working step, and Todo done/total plus the in-progress item.
While working, these rows live inside the current reply bubble and remain visible
even when its prose is collapsed. Idle/disconnected state stays above the timeline;
the running state is not duplicated in a separate header.
The native completion extension captures the run clock inside the OMP process
and reports monotonic elapsed milliseconds; the app extrapolates with its own
Stopwatch, not the phone's wall clock or message timestamps. Reconnecting a guest
does not restart an already-observed native run clock. Missing clock evidence is
explicitly unavailable, including old extensions or attachment after native start.
OMP working bubbles do not derive a second run clock from message timestamps;
the native status bar is the authoritative live clock.

Todo counts come from committed native snapshots on the active parent chain:
successful `todo` results and `user_todo_edit` entries, not tool arguments or text
parsing. Read-only/failed Todo calls do not replace the plan; an explicit empty
snapshot clears it. Completed and abandoned tasks count as closed, matching the
native Todo HUD. Long history scans and retained item previews are bounded, while
the counts remain exact. The current step follows native working-intent events.

Normal chat shows assistant prose without Execution process or recent tool-call
lists. Complete content remains an explicit reader for full semantic originals.
Background agents do not make an idle main run busy. On disconnect, live time and
step/Todo claims stop; cached metadata is not presented as current evidence.

The bridge supplies `snapshot.runtime`; authenticated project-view agent records
and conversation responses expose `omp_runtime`. Runtime metadata is capped at
12 KiB with active work prioritized and explicit truncation; native IDs bind task
lifecycle/progress without matching names or spawn indices. Only bounded visible
text snippets and allowlisted progress fields are retained, not complete
transcripts, arbitrary progress objects, tool arguments or session-file paths. Older
history pages cannot replace a newer runtime observation. Update server and app
together; load the new supervisor on existing OMP processes with a normal idle
restart, never by interrupting a busy session.

Collab v3 requires no extra subscription or verbosity switch. The native extension
adds an owner-bound `inspect.status_bar`, carried to the app as
`omp_runtime.status_bar`; elapsed/observation-only updates do not invalidate the
conversation or force timeline scrolling. Older running extensions cannot gain
these fields just by updating files: update server and app together, then load the
new extension with a normal idle restart. Never interrupt a busy session to apply it.

OMP 工作区现在以原生状态栏为主：本轮运行时间、当前 step、Todo 完成数（如 0/3）及进行中的任务。运行时间由 OMP 原生进程记录，手机使用单调时钟续算，不用消息时间或手机时区推测；Todo 来自当前分支已提交的原生快照，失败或只读调用不会覆盖计划，明确清空后才显示 0/0。普通聊天已移除 Execution process 和近期工具调用列表，只保留助手正文及用户主动打开的完整内容阅读入口。旧进程缺少原生字段时明确提示不可用，请更新服务端和 App 后在会话空闲时正常重启加载，不要中断忙碌会话。

Linux OMP accepts consecutive mobile sends into its persisted, session-fenced FIFO.
Queued user messages remain visible after refresh/restart, with explicit Queued,
unconfirmed, sent or failed delivery state; held inputs show Needs review and are
never silently resent into another session. Queue acceptance does not reset the
running turn's status. Exact native entry association preserves the original
idempotency-key identity without matching message text. Long queued bodies share
the same bounded inline/original-reader budget as native history.
Upstream Collab v3 does not provide request-level prompt admission receipts;
unconfirmed delivery is retained until exact native evidence is available.
Session/generation fences and retained idempotency keys prevent cross-session
delivery and duplicate handoffs. Terminal shortcuts remain
available only in Open Terminal. Native select/editor questions use the interactive
card described above; remote session branching is not implemented.

The focused Codex composer exposes **Queue / 排队** instead of a raw Tab key.
It uploads attachments through the existing flow, sends `/queue <draft>` with
Enter, and keeps the visible user bubble as the original message. Empty queue
clicks send nothing. Other providers do not expose this Codex-specific action;
OMP Collab retains its existing persisted FIFO through normal Send.

Validation commands (installed OMP 18.11 sources and the Android SDK required):

```sh
bun test test/omp_collab_runtime.test.mjs test/omp_model_companion.test.mjs
PYTHONPATH=lib .venv/bin/python test/omp_collab_tui_smoke.py
bun test/omp_collab_native_smoke.mjs /absolute/path/to/pi-coding-agent
.venv/bin/python test/omp_mobile_emulator_backend.py --upstream /absolute/path/to/pi-coding-agent --run
```

For the actual encrypted CCB Relay large-history lane, add `--relay` to the
Android harness command. It starts an isolated TLS Relay plus host connector,
checks history above 2 MiB and an oversized single turn, and verifies original
content UTF-8 byte offsets and SHA-256 without increasing the 512 KiB Relay cap.

OMP inline pages use a conservative 192 KiB serialized budget. Oversized turns
show a structured latest-content bubble capped at 24 KiB, 8 entries and 24 blocks;
the secondary Read original action opens complete content in versioned UTF-8-safe
pages of at most 32 KiB. Changed session/generation/content invalidates the read
cursor instead of mixing snapshots. Both the updated server and app are required;
installing the APK alone does not fix an old server's oversized responses.

`CCB_MOBILE_TEST_BUILD=1 flutter build apk --debug` uses the separate
`io.ccb.mobile.ccb_mobile.relaytest` application ID so repair test builds do not
replace the production installation or its paired-device data.

The repair was exercised on Android API 35 over the real TLS CCB Relay:
196 history items across four pages; a 987,082-byte original reconstructed
across 31 pages with exact SHA-256; send/live/tools/model/stale-fence/reconnect
and background-pause checks passed without increasing the Relay cap.
Evidence: `/home/bfly/.local/state/ccb-relay-fix-test/relay-run8`.



The Android harness uses production Flutter workspace/model controls, authenticated
HTTP gateway, encrypted Collab guest, real upstream CollabHost and SessionManager.
Its model output/catalog scenarios are deterministic fixtures, not paid-provider
quality evidence. The actual TUI smoke separately exercises real native model and
effort APIs without submitting any prompt. Android API 35 verification covered
two live-to-durable turns, folded tool results, model readback without restart,
stale revisions, explicit failure, disconnect/reconnect, generation fencing and
background refresh suspension. The final ancestry/cross-launch-key revision was
rechecked with 853 Flutter tests passing (one skipped), clean static analysis,
175 gateway/OMP Python tests, 13 Bun protocol/model tests, the actual native TUI
model smoke and the Android journey. Local final evidence is retained at
`/home/bfly/.local/state/ccb-omp-final-android` (three submitted fixture prompts,
one model change, zero terminal calls and zero external model calls).

The harness disables Bun's implicit package installation so an isolated HOME
cannot silently download a newer native dependency instead of the pinned release.
The current-project paid-model run separately exposed omitted metadata ancestors;
the companion now exports bounded ID/parent-ID links only, preserving original
turn ownership without exposing the omitted record contents. New OMP mobile
submissions use cross-launch unique keys; explicit retries retain the same key.



### LAN pairing and recovery

For a LAN QR/code, connect the phone and computer to the same trusted Wi-Fi,
wired LAN, or phone hotspot. Avoid guest/client-isolated Wi-Fi, and allow local
network access through any active VPN. Android shows an advisory before a LAN
claim when no local-network transport is visible; **Continue anyway** remains
available because the phone itself may be providing the hotspot.

If a paired LAN route becomes unreachable, the app keeps the profile and shows
a persistent network notice with **Retry** and **Diagnostics**. Check the
same-network, VPN, guest-isolation, and firewall conditions first. If the
computer received a new LAN address, rerun `ccb update mobile` on the computer
and scan the new code.

## Plan Tree

- [Mobile tmux control plan](docs/plantree/plans/mobile-tmux-control/README.md)
- [Remote access roadmap](docs/plantree/plans/mobile-tmux-control/topics/remote-access-roadmap.md)
- [Native Flutter blueprint](docs/plantree/plans/mobile-tmux-control/topics/native-flutter-ccb-blueprint.md)

## Project Layout

```text
app/                 Flutter mobile app
docs/plantree/       Planning tree and design decisions
tools/               Emulator, gateway, and acceptance helpers
```

iOS/iPadOS remain source-supported targets, but v8.6.13 release validation is
Android-focused.
