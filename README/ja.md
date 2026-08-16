<div align="center">

# CCB - モバイルアプリが登場しました！

**軽快なマルチエージェント TUI と、安定したクロスプロバイダー協調レイヤー**<br>
**Codex、Claude、Gemini などの CLI Agent を、見える・制御できる・直接引き継げるワークフローで連携**

<p>
  <img src="https://img.shields.io/badge/version-8.6.7-orange.svg" alt="version">
  <img src="https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20WSL-lightgrey.svg" alt="platform">
  <img src="https://img.shields.io/badge/providers-17%20CLI%20families-0B7285.svg" alt="providers">
</p>

<p>
  <img src="https://img.shields.io/badge/Codex-111111?style=flat-square&logo=openai&logoColor=white" alt="Codex">
  <img src="https://img.shields.io/badge/Claude-D97757?style=flat-square&logo=anthropic&logoColor=white" alt="Claude">
  <img src="https://img.shields.io/badge/Gemini-4285F4?style=flat-square&logo=googlegemini&logoColor=white" alt="Gemini">
  <img src="https://img.shields.io/badge/Grok-000000?style=flat-square&logo=x&logoColor=white" alt="Grok CLI">
  <img src="https://img.shields.io/badge/Kimi-111111?style=flat-square&logo=moonshotai&logoColor=white" alt="Kimi">
  <img src="https://img.shields.io/badge/MiMo-FF6900?style=flat-square&logo=xiaomi&logoColor=white" alt="MiMo">
  <img src="https://img.shields.io/badge/Qwen-6A5CFF?style=flat-square" alt="Qwen">
  <img src="https://img.shields.io/badge/Cursor-111111?style=flat-square" alt="Cursor">
  <img src="https://img.shields.io/badge/Copilot-111111?style=flat-square&logo=githubcopilot&logoColor=white" alt="GitHub Copilot">
  <img src="https://img.shields.io/badge/Crush-FF5A5F?style=flat-square" alt="Crush">
  <img src="https://img.shields.io/badge/Kiro-6D5EF6?style=flat-square" alt="Kiro">
  <img src="https://img.shields.io/badge/Pi-111111?style=flat-square" alt="Pi">
  <img src="https://img.shields.io/badge/Z.ai-111111?style=flat-square" alt="Z.ai">
  <img src="https://img.shields.io/badge/OpenCode-111111?style=flat-square" alt="OpenCode">
  <img src="https://img.shields.io/badge/Antigravity-6D5EF6?style=flat-square&logo=google&logoColor=white" alt="Antigravity">
  <img src="https://img.shields.io/badge/Droid-3DDC84?style=flat-square&logo=android&logoColor=white" alt="Droid">
</p>

[中文](zh.md) | [English](../README.md) | **日本語** | [Français](fr.md) | [Deutsch](de.md) | [العربية](ar.md) | [Español](es.md) | [Português](pt.md) | [한국어](ko.md) | [Русский](ru.md)

[クイックスタート](#quick-start) · [Mobile App](#mobile-app) · [Rich モード](#rich-mode) · [エージェント設定](#configure-agents) · [ユーザーガイド](../docs/manuals/user-guide/) · [開発者ガイド](../docs/manuals/developer-guide/)

<p align="center">
  <img src="../assets/readme_v7/ccb-hero-en-light.png" alt="CCB の可視マルチエージェント CLI ワークスペース" width="960">
</p>

</div>

<a id="why-ccb"></a>

## なぜ CCB か？

- `A -> B -> C`、`A,B -> C`、`A -> B,C` のような複雑な協調関係に対応する、安定した agent 間通信。
- すべての agent は完全なネイティブ端末で、表示レイアウトを確認しながら直接操作できます。
- バックグラウンド daemon が動き続けるため、前面 UI を閉じてもプロジェクト状態を保てます。
- Hub 機能により、1 つのコマンドで複数の CLI provider を並行実行できます。
- モバイルリモートコントローラーにより、provider をまたいだ音声操作、ファイル転送、リモート端末アクセスができます。

<a id="how-to-install"></a>

## インストール方法

npm 管理の CCB は npm でインストールまたは更新します。

```bash
npm install -g @seemseam/ccb@latest
```

GitHub release パッケージまたはソースからのインストールでは、CCB のトランザクション updater を使います。

```bash
ccb update
```

npm 管理のインストールでは、`ccb update` は同等の npm コマンドを表示するだけで、vendored payload を直接変更しません。

<details>
<summary><b>GitHub release パッケージとソースインストールのフォールバック</b></summary>

npm が使いにくい環境では、[Releases](https://github.com/SeemSeam/claude_codex_bridge/releases) から環境に合うパッケージをダウンロードして展開し、インストールします。

```bash
tar -xzf ccb-*.tar.gz
cd ccb-*
./install.sh install
```

ソースからのインストールは、開発または一時的な回避策としてのみ推奨します。

```bash
git clone https://github.com/SeemSeam/claude_codex_bridge.git
cd claude_codex_bridge
./install.sh install
```

ソースインストールでは、グローバルな `ccb` / `ask` が現在の checkout にリンクされます。通常のユーザーには npm パッケージを推奨します。

</details>

<a id="quick-start"></a>

## クイックスタート

### 1. 起動

作業ディレクトリで実行します。

```bash
ccb
```

起動時に `.ccb` を自動作成できない、またはプロジェクトアンカーが見つからないと表示された場合は、手動で `.ccb` を作成します。

```bash
mkdir -p .ccb
```

<a id="configure-agents"></a>

### 2. プロジェクト設定を作成

空のプロジェクトは軽量に起動します。CCB は `main` window を 1 つだけ開き、マシン上で利用可能な最初の対応 CLI を使って `demo` という agent を 1 つ作成します。Multi-Agent チームはデフォルトではマウントされません。

CCB sidebar 左上の **⚙ 設定** をクリックすると、ローカル設定コントロールパネルが開きます。`ccb config ui` でも起動できます。

<p align="center">
  <img src="../assets/readme_v7/config-control-panel.png" alt="デフォルト demo agent を編集する CCB 設定コントロールパネル" width="960">
</p>

パネルでは windows、pane 分割、provider、model、thinking レベル、API override、workspace、Rich モード、sidebar を設定できます。保存前の検証、reload dry-run、保護付き hot reload に対応します。

高度な Multi-Agent トポロジーが必要な場合は、画面上で agent を追加するか `.ccb/ccb.config` を手動作成します。`,` と `;` で縦積みと横分割を制御でき、`A,B;C,D` はほぼ 4 pane の配置です。

```toml
version = 2

[windows]
main = "main:codex"
work = "worker1:codex(worktree), worker2:claude(worktree)"
review = "reviewer:claude, qa:gemini"

[ui.sidebar]
mode = "every_window"
width = "15%"
bottom_height = 20
agents_height = "50%"
comms_height = "15%"
tips_height = "35%"
comms_limit = 3
```

設定を検証してワークスペースを起動します。

```bash
ccb config validate
ccb
```

### 3. 協調作業を始める

任意の agent pane に直接入力できます。agent 同士を協調させることもできます。

```text
/ask reviewer review the latest parser changes and list blocking issues.
```

workflow の中で agent が `/ask` を呼び出し、委任や引き継ぎを行うこともできます。継続的な調整には agent memory またはプロジェクト共有記憶 `.ccb/ccb_memory.md` を使ってください。

<a id="mobile-app"></a>

## モバイルリモート操作（Android）

スマートフォンから CCB を操作する方法を推奨します。すべての CCB プロジェクトへ接続し、各 agent を操作し、音声入力とファイル転送を利用できます。

```bash
ccb update mobile
```

このコマンドがインストールと設定を案内します。

<p align="center">
  <img src="../assets/readme_v7/mobile-control-chat.jpg" alt="CCB Mobile agent チャット" width="180">
  <img src="../assets/readme_v7/mobile-control-terminal.jpg" alt="CCB Mobile 端末操作" width="180">
  <img src="../assets/readme_v7/mobile-control-files.jpg" alt="CCB Mobile ファイル転送" width="180">
  <img src="../assets/readme_v7/mobile-control-pairing.jpg" alt="CCB Mobile ペアリングと接続" width="180">
</p>

<details>
<summary><b>Mobile App の詳細、安全境界、ソース</b></summary>

CCB 8.6.7 では Flutter 版 CCB Mobile のソースが [`mobile/`](../mobile/) に含まれ、Android APK は GitHub Releases で公開されています。

- [CCB Mobile v8.6.7 APK をダウンロード](https://github.com/SeemSeam/claude_codex_bridge/releases/download/v8.6.7/ccb-mobile-v8.6.7.apk)
- App ソース：[`mobile/app`](../mobile/app)
- サーバー gateway ソース：[`lib/mobile_gateway`](../lib/mobile_gateway)

スマートフォンアプリは、サーバー上で動く実際の CCB プロジェクトのリモートコントローラーです。server-wide mobile gateway からマウント済みプロジェクトを見つけ、window/agent を切り替え、agent の会話コンテキストを表示し、pane-native 入力でテキストを送り、terminal view を開き、認証済み gateway 経由で画像や文書をアップロード/ダウンロードできます。

安全境界：

- CCB gateway は `127.0.0.1:8787` など loopback のみに bind します。
- リモートアクセスには Tailscale Serve を使い、Tailscale Funnel は使いません。
- CCB は Tailscale パスワード、OAuth token、admin API token を保存せず、tailnet ACL/grants も自動変更しません。
- スマートフォンが受け取るのは pairing profile で許可された scope だけです。例：view、content、terminal、file upload、file download。

</details>

<a id="rich-mode"></a>

## Rich メディア端末

端末内でファイルツリーを閲覧し、ファイルを開き、文書を編集し、メディアをプレビューできます。

<p align="center">
  <img src="../assets/readme_v7/rich-workbench.png" alt="WezTerm 内で Yazi preview を使う CCB rich メディアワークベンチ" width="860">
</p>

```bash
ccb update rich
```

rich mode を有効にすると、通常の `ccb` は CCB-managed rich WezTerm 内で実行中でない限り、rich WezTerm launcher を自動で開きます。通常の端末起動に戻すには `ccb uninstall rich` を実行します。

<a id="agent-roles"></a>

## Agent Roles Spec とロールカタログ

CCB は [Agent Roles Spec](https://github.com/SeemSeam/agent-roles-spec) をサポートします。これは専門 agent をパッケージ化する host-neutral な仕様で、skills、memory、tool 依存をインストール可能、マウント可能、削除可能な Role Pack にまとめられます。このリポジトリは公開 role catalog でもあります。

| Role | 用途 |
| :--- | :--- |
| `agentroles.ccb_self` | CCB の自己保守、設定支援、実行診断、保護付き復旧、workflow orchestration。 |
| `agentroles.archi` | アーキテクチャレビュー、境界確認、結合分析、保守性リスク、後続 gate 提案。 |
| `agentroles.frontend_engineer` | フロントエンド設計と実装、デザインシステム、アクセシビリティ、ブラウザ QA、レビュー付き AGY 委任。 |
| `agentroles.mobile_app_engineer` | iOS、Android、React Native、Expo、Flutter、SwiftUI、Jetpack Compose などのモバイル設計と実装。 |
| `agentroles.mother` | Role 作成、role source 監査、role research、blueprint 設計、Agent Roles 仕様準拠チェック。 |
| `agentroles.su_ccb` | 要件分析、計画、派遣、review gate、アーカイブ、復旧を含む SU-CCB workflow 操作。 |

<a id="config-memory"></a>

## 設定と共有記憶

通常のプロジェクト設定には **⚙ 設定** パネルを使用します。Agent による設定支援や runtime 診断が必要な場合、`ccb_self` はオプションの Role Pack として引き続き利用でき、`ccb roles add agentroles.ccb_self:codex` で追加できます。

`.ccb/ccb_memory.md` はプロジェクト全体の共有記憶文書です。チーム協調ルール、プロジェクト制約、長期コンテキスト、agent 引き継ぎの約束を書くのに適しています。複数 provider の private memory に同じ説明をコピーするより、安定した cross-agent 情報はここに置く方が確実です。

<a id="contact"></a>

## 連絡先

- Email: `bfly123@126.com`
- [Telegram group & contact / TG 群与联系](https://t.me/+BKn03v8I_ehmYzRk)
- WeChat: `seemseam-com`

<p align="center">
  <img src="../assets/weixin.png?v=0a86422d" alt="WeChat group" width="240">
</p>

<a id="community"></a>

## コミュニティと謝辞

テスト、フィードバック、議論で支援してくれた [Linux.do community](https://linux.do) に感謝します。

sidebar のアイデアと示唆を提供してくれた [tmux-agent-sidebar](https://github.com/hiroppy/tmux-agent-sidebar) に感謝します。

<a id="release-notes"></a>

## リリースノート

<details open>
<summary><b>v8.4.0</b> - 暗号化 Mobile Relay、簡単なペアリング、安定したプロジェクト ID、Codex 再接続</summary>

- エンドツーエンド暗号化 Relay、1 回限りの招待、multiplex stream、公式またはセルフホスト構成を追加しました。
- Tailscale、private LAN、Relay の選択を `ccb update mobile` に移し、スマートフォン側は QR またはコード入力だけにしました。
- 署名済み APK を Android に渡す前に、公式 GitHub metadata、サイズ、SHA-256 を検証します。
- プロジェクト移動後も ID を維持し、システム theme 追従と境界付きの Codex reconnect を追加しました。

</details>

<details>
<summary><b>v8.3.1</b> - Provider 更新の一元化、安全なキャッシュ廃止、永続的な Config UI アクセス</summary>

- 対応する Provider の更新を `ccb update` に集約し、正確なバージョン確認、辞退、バージョン単位のスキップを提供します。実行中の pane は自動再起動しません。
- プロジェクト単位の Claude/Gemini ソフトウェアキャッシュを廃止し、所有権を検証できる旧データだけを削除します。稼働中のプロジェクト、session、認証情報は保持します。
- token 値を表示せずに、Config UI の固定 loopback ポートと保護された token ソースを設定できます。
- サーバー停止中も shutdown finalizer を維持し、Rich mode の Yazi をコンパクトな 2 カラム構成にしました。
- CLI、npm、Linux、macOS、Android とすべてのリリース成果物を 8.3.1 に同期しました。

</details>

<details>
<summary><b>v8.3.0</b> - 正確な provider turn、job の整合性、プロジェクト内 Mobile ターミナル</summary>

- Kimi、Claude、Qoder をそれぞれのネイティブな turn、activation、session、completion 契約に結び付けました。
- 正確な active job への follow-up、相関した実行フェーズ、孤立 inbound の診断、終端 cancellation 結果を追加しました。
- provider 拡張と Copilot plugin を、投影 asset の明示的な ownership 保護付きで継承します。
- npm 管理インストールの更新を npm に委譲し、marker-only worktree を保守的に廃止します。
- Mobile の chat と terminal を選択中の project workspace 内に保ち、すべての release surface を 8.3.0 に同期しました。

</details>

<details>
<summary><b>v8.2.1</b> - 決定的な起動、操作可能な認証復旧、Android バックグラウンド接続</summary>

- 起動世代フェンス、有限の readiness 証明、操作数とタイムラインの診断を追加しました。
- 復旧不能な Provider 認証の再起動ループを停止し、必要なログイン操作を表示します。
- オプトインの Android バックグラウンド接続と Agent ごとに 1 つの working reply 状態を追加しました。
- Linux、macOS、npm、署名済み Android artifact を 8.2.1 に同期しました。

</details>

<details>
<summary><b>v8.2.0</b> - 起動高速化、Provider 修正、Mobile の信頼性向上</summary>

- lifecycle と ownership の検証を維持しながら、ccbd 起動時の重複処理を削減しました。
- Grok fullscreen 起動、Claude credential 種別、model/thinking 選択、Codex ask/reply 配信を修正・強化しました。
- Mobile の recovery、chat、terminal、添付、download、FCM を改善し、Linux、macOS、npm、署名済み Android artifact を 8.2.0 に同期しました。

</details>

<details open>
<summary><b>v8.0.14</b> - README ディレクトリ整理とモバイル公開面の同期</summary>

- ルートの `README.md` は English の GitHub ホームページに戻りました。
- ローカライズ済み README は [`README/`](./) 配下にまとまり、中国語版は [`zh.md`](zh.md) です。
- Mobile App links、package metadata、release notes は 8.0.14 APK を指します。

</details>

<details>
<summary><b>v8.0.12</b> - Release CI の移植性と README 多言語化</summary>

- mobile host registry tests は一時 Unix socket を短い `/tmp/ccb-sock-*` パスに置くようになり、macOS CI の `AF_UNIX path too long` 失敗を避けます。
- `ccb update mobile`、README links、package metadata、mobile release manifest は 8.0.12 APK を指すようになりました。
- v8.0.12 で共通セクション構造の多言語 README を導入しました。現在のローカライズ済みファイルは `README/` ディレクトリにあります。

</details>

<details>
<summary><b>v8.0.0</b> - CCB Mobile Monorepo リリース</summary>

- Flutter 版 CCB Mobile ソースが正式に本リポジトリへ入り、Android APK が GitHub Releases で公開されました。
- server-wide mobile project discovery、pairing、認証 gateway routes、pane-native message input、conversation context rendering、terminal access、画像/文書 upload/download を追加しました。
- `ccb update mobile` を Tailscale Tailnet onboarding の統一 entrypoint にしつつ、gateway は loopback-only、Funnel 不使用、token 非保存、ACL/grants 自動変更なしを維持しました。

</details>

<details>
<summary><b>v7.7.0</b> - Runtime Accelerator リリース強化</summary>

- Release artifacts に任意の Rust `ccb-runtime-accelerator` が含まれ、sidecar が期待される installed Codex agent が Python hot path に黙って fallback しなくなりました。
- プロジェクトパスにより Unix socket path が長すぎる場合、accelerator socket は短い per-user runtime socket root に自動配置されます。
- callback repair と Codex binding cache invalidation を強化し、regression、long-idle Codex soak、Claude callback、mixed-provider integration の証拠を記録しました。

</details>

<details>
<summary><b>v7.6.19</b> - 長時間 ask のデフォルト待機ポリシー</summary>

- 通常の長時間 `ask` は heartbeat 診断だけで `incomplete/heartbeat_timeout` として終了せず、実際の provider/completion 結果を待ち続けます。
- Codex、Claude、Gemini の pane-backed no-terminal timeout はデフォルトで明示 opt-in になり、明示的な reliability timeout policy は引き続き利用できます。
- 32 分の source-runtime ask smoke により、タスクが 30 分以上 running のまま継続し、その後 `result_message` で完了し、`heartbeat_timeout` や `incomplete` の証拠が出ないことを確認しました。

</details>

完全な履歴は [CHANGELOG.md](../CHANGELOG.md) を参照してください。
