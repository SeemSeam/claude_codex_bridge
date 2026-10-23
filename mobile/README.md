# CCB Mobile

Native Android/iOS/iPadOS remote controller for server-side CCB tmux projects.

This directory is the authoritative CCB Mobile source inside the CCB monorepo.
The legacy standalone `ccb_mobile` repository is retired as an implementation
surface and now exists only for migration/runtime compatibility notes.

CCB and provider CLIs run on the server; the mobile app is a controller for
project discovery, agent switching, terminal access, Markdown reading,
notifications, local/Tailnet gateway access, and file transfer.

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

CCB Mobile v8.7.18 release APK:

- [Download ccb-mobile-v8.7.18.apk](https://github.com/SeemSeam/claude_codex_bridge/releases/download/v8.7.18/ccb-mobile-v8.7.18.apk)
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
terminal/history fallback. Collab refresh is limited to the selected foreground
agent and pauses while the app is backgrounded.

OMP model/effort choices come from the running agent's native model registry.
The Provider sheet switches that same session through a restricted native
companion, confirms host readback, and does not edit project configuration or
restart the agent. Busy sessions, desktop drafts and stale session/model
revisions reject changes; observable mutation races report an unknown outcome.

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
available only in Open Terminal; interactive approvals and remote session
branching are not implemented by this connection.

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
