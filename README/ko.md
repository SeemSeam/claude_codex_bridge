<div align="center">

# CCB - 모바일 앱이 도착했습니다!

**가벼운 멀티 에이전트 TUI와 안정적인 크로스 프로바이더 협업 계층**<br>
**Codex, Claude, Gemini 등 CLI Agent를 보이고 제어 가능하며 직접 이어받을 수 있는 워크플로로 조율**

<p>
  <img src="https://img.shields.io/badge/version-8.6.6-orange.svg" alt="version">
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

[中文](zh.md) | [English](../README.md) | [日本語](ja.md) | [Français](fr.md) | [Deutsch](de.md) | [العربية](ar.md) | [Español](es.md) | [Português](pt.md) | **한국어** | [Русский](ru.md)

[빠른 시작](#quick-start) · [Mobile App](#mobile-app) · [Rich 모드](#rich-mode) · [에이전트 설정](#configure-agents) · [사용자 가이드](../docs/manuals/user-guide/) · [개발자 가이드](../docs/manuals/developer-guide/)

<p align="center">
  <img src="../assets/readme_v7/ccb-hero-en-light.png" alt="CCB의 보이는 멀티 에이전트 CLI 작업 공간" width="960">
</p>

</div>

<a id="why-ccb"></a>

## 왜 CCB인가?

- `A -> B -> C`, `A,B -> C`, `A -> B,C` 같은 복잡한 협업 그래프를 위한 안정적인 agent 간 통신.
- 모든 agent는 완전한 네이티브 터미널이며, 배치를 눈으로 확인하고 직접 개입할 수 있습니다.
- 백그라운드 daemon이 실행되어 전면 UI를 닫아도 프로젝트 상태를 유지합니다.
- Hub 기능: 하나의 명령으로 여러 CLI provider를 병렬 실행합니다.
- 모바일 원격 컨트롤러: provider를 넘나드는 음성 제어, 파일 전송, 원격 터미널 접근.

<a id="how-to-install"></a>

## 설치 방법

npm이 관리하는 CCB는 npm으로 설치하거나 업데이트합니다.

```bash
npm install -g @seemseam/ccb@latest
```

GitHub release 패키지 또는 소스 설치에는 CCB의 트랜잭션 updater를 사용합니다.

```bash
ccb update
```

npm 관리 설치에서 `ccb update`는 동일한 npm 명령만 표시하며 vendored payload를 직접 변경하지 않습니다.

<details>
<summary><b>GitHub release 패키지와 소스 설치 대체 경로</b></summary>

npm을 쓰기 어려운 환경이라면 [Releases](https://github.com/SeemSeam/claude_codex_bridge/releases)에서 맞는 패키지를 내려받고 압축을 푼 뒤 설치합니다.

```bash
tar -xzf ccb-*.tar.gz
cd ccb-*
./install.sh install
```

소스 설치는 개발이나 임시 대체 용도로만 권장합니다.

```bash
git clone https://github.com/SeemSeam/claude_codex_bridge.git
cd claude_codex_bridge
./install.sh install
```

소스 설치는 전역 `ccb` / `ask` 명령을 현재 checkout으로 연결합니다. 일반 사용자는 npm 패키지를 권장합니다.

</details>

<a id="quick-start"></a>

## 빠른 시작

### 1. 실행

작업 디렉터리에서 실행합니다.

```bash
ccb
```

시작 시 `.ccb`를 자동으로 만들 수 없거나 프로젝트 앵커가 없다고 나오면 `.ccb`를 직접 만듭니다.

```bash
mkdir -p .ccb
```

<a id="configure-agents"></a>

### 2. 프로젝트 설정 만들기

빈 프로젝트는 가볍게 시작합니다. CCB는 `main` window 하나만 열고 머신에서 사용 가능한 첫 번째 지원 CLI를 선택해 `demo`라는 agent 하나를 만듭니다. 멀티 에이전트 팀은 더 이상 기본으로 마운트되지 않습니다.

CCB sidebar 왼쪽 위의 **⚙ 설정** 아이콘을 클릭하면 로컬 설정 제어판이 열립니다. `ccb config ui` 명령으로도 실행할 수 있습니다.

<p align="center">
  <img src="../assets/readme_v7/config-control-panel.png" alt="기본 demo agent를 편집하는 CCB 설정 제어판" width="960">
</p>

제어판에서 windows, pane 분할, provider, 모델, thinking 단계, API override, workspace, Rich 모드와 sidebar를 설정할 수 있습니다. 저장 전 검증, reload dry-run, 보호된 hot reload를 지원합니다.

고급 멀티 에이전트 토폴로지가 필요하면 화면에서 agent를 추가하거나 `.ccb/ccb.config`를 직접 만드세요. `,`와 `;`는 세로 쌓기와 가로 분할을 제어하며 `A,B;C,D`는 네 pane 배치에 가깝습니다.

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

설정을 검증하고 작업 공간을 시작합니다.

```bash
ccb config validate
ccb
```

### 3. 협업 시작

원하는 agent pane에 직접 입력하거나 agent들이 협업하게 할 수 있습니다.

```text
/ask reviewer review the latest parser changes and list blocking issues.
```

workflow orchestration 중 agent가 `/ask`를 호출해 위임과 인계를 수행할 수도 있습니다. 지속적인 조율에는 agent memory 또는 프로젝트 공유 메모리 파일 `.ccb/ccb_memory.md`를 사용하세요.

<a id="mobile-app"></a>

## 모바일 원격 제어 (Android)

휴대폰에서 CCB를 제어하는 권장 방식은 모든 CCB 프로젝트에 연결하고, 각 agent를 제어하며, 음성 입력과 파일 전송을 사용할 수 있습니다.

```bash
ccb update mobile
```

이 명령은 설치와 설정을 안내합니다.

<p align="center">
  <img src="../assets/readme_v7/mobile-control-chat.jpg" alt="CCB Mobile agent 대화" width="180">
  <img src="../assets/readme_v7/mobile-control-terminal.jpg" alt="CCB Mobile 터미널 제어" width="180">
  <img src="../assets/readme_v7/mobile-control-files.jpg" alt="CCB Mobile 파일 전송" width="180">
  <img src="../assets/readme_v7/mobile-control-pairing.jpg" alt="CCB Mobile 페어링과 연결" width="180">
</p>

<details>
<summary><b>Mobile App 세부 정보, 안전 경계, 소스</b></summary>

CCB 8.6.6은 Flutter CCB Mobile 소스를 [`mobile/`](../mobile/)에 포함하며 Android APK를 GitHub Releases로 배포합니다.

- [CCB Mobile v8.6.6 APK 다운로드](https://github.com/SeemSeam/claude_codex_bridge/releases/download/v8.6.6/ccb-mobile-v8.6.6.apk)
- 앱 소스: [`mobile/app`](../mobile/app)
- 서버 gateway 소스: [`lib/mobile_gateway`](../lib/mobile_gateway)

휴대폰 앱은 서버에서 실행 중인 실제 CCB 프로젝트의 원격 컨트롤러입니다. server-wide mobile gateway에서 마운트된 프로젝트를 찾고, window/agent를 전환하며, agent 대화 컨텍스트를 표시하고, pane-native 입력으로 텍스트를 보내고, terminal view를 열며, 인증된 gateway로 이미지와 문서를 업로드/다운로드할 수 있습니다.

안전 경계:

- CCB gateway는 `127.0.0.1:8787` 같은 loopback에만 bind합니다.
- 원격 접근은 Tailscale Serve를 사용하며 Tailscale Funnel은 사용하지 않습니다.
- CCB는 Tailscale 비밀번호, OAuth token, admin API token을 저장하지 않고 tailnet ACLs/grants를 자동 수정하지 않습니다.
- 휴대폰은 pairing profile에서 허용한 scope만 받습니다. 예: view, content, terminal, file upload, file download.

</details>

<a id="rich-mode"></a>

## Rich 미디어 터미널

터미널 안에서 파일 트리를 탐색하고, 파일을 열고, 문서를 편집하고, 미디어를 미리 볼 수 있습니다.

<p align="center">
  <img src="../assets/readme_v7/rich-workbench.png" alt="WezTerm에서 Yazi preview를 사용하는 CCB rich media workbench" width="860">
</p>

```bash
ccb update rich
```

rich mode가 활성화되면 일반 `ccb`는 이미 CCB-managed rich WezTerm 세션 안에서 실행 중인 경우를 제외하고 rich WezTerm launcher를 자동으로 엽니다. 일반 터미널 시작으로 돌아가려면 `ccb uninstall rich`를 실행합니다.

<a id="agent-roles"></a>

## Agent Roles Spec 및 역할 카탈로그

CCB는 전문 agent를 패키징하기 위한 host-neutral 명세인 [Agent Roles Spec](https://github.com/SeemSeam/agent-roles-spec)을 지원합니다. skills, memory, tool dependencies를 설치 가능하고 마운트 가능하며 제거 가능한 Role Pack으로 묶을 수 있습니다. 해당 저장소는 공개 role catalog 역할도 합니다.

| Role | 목적 |
| :--- | :--- |
| `agentroles.ccb_self` | CCB 자체 유지관리, 설정 지원, runtime 진단, 보호된 복구, workflow orchestration. |
| `agentroles.archi` | 아키텍처 리뷰, 경계 점검, 결합도 분석, 유지보수성 위험, 후속 gate 조언. |
| `agentroles.frontend_engineer` | 프론트엔드 설계와 구현, design systems, 접근성, 브라우저 QA, 검토된 AGY 위임. |
| `agentroles.mobile_app_engineer` | iOS, Android, React Native, Expo, Flutter, SwiftUI, Jetpack Compose 등 모바일 설계와 구현. |
| `agentroles.mother` | Role 생성, role source 감사, role research, blueprint 설계, Agent Roles 명세 준수 점검. |
| `agentroles.su_ccb` | 요구사항 분석, 계획, dispatch, review gates, 보관, 복구를 포함한 SU-CCB workflow 운영. |

<a id="config-memory"></a>

## 설정과 공유 메모리

일반 프로젝트 설정에는 **⚙ 설정** 제어판을 사용하세요. Agent 기반 설정 지원과 runtime 진단이 필요하면 `ccb_self`를 선택적 Role Pack으로 `ccb roles add agentroles.ccb_self:codex` 명령을 통해 추가할 수 있습니다.

`.ccb/ccb_memory.md`는 프로젝트 전체 공유 메모리 문서입니다. 팀 협업 규칙, 프로젝트 제약, 장기 컨텍스트, agent 인계 규칙을 기록하는 데 사용하세요. 여러 provider private memory에 같은 내용을 복사하기보다 안정적인 cross-agent 정보는 여기에 두는 편이 더 안정적입니다.

<a id="contact"></a>

## 연락처

- Email: `bfly123@126.com`
- [Telegram group & contact / TG 群与联系](https://t.me/+BKn03v8I_ehmYzRk)
- WeChat: `seemseam-com`

<p align="center">
  <img src="../assets/weixin.png?v=7335e843" alt="WeChat 그룹" width="240">
</p>

<a id="community"></a>

## 커뮤니티와 감사

테스트, 피드백, 토론을 지원해 준 [Linux.do community](https://linux.do)에 감사드립니다.

sidebar 아이디어와 영감을 준 [tmux-agent-sidebar](https://github.com/hiroppy/tmux-agent-sidebar)에 감사드립니다.

<a id="release-notes"></a>

## 릴리스 노트

<details open>
<summary><b>v8.4.0</b> - 암호화 Mobile Relay, 간단한 페어링, 안정적인 프로젝트 ID와 Codex 재연결</summary>

- 종단 간 암호화 Relay, 일회용 초대, 다중화 stream, 공식 또는 자체 호스팅 모드를 추가했습니다.
- Tailscale, private LAN, Relay 선택을 `ccb update mobile`로 옮기고 휴대폰은 QR 스캔이나 코드 입력만 하도록 단순화했습니다.
- 서명된 APK를 Android에 전달하기 전에 공식 GitHub metadata, 크기와 SHA-256을 검증합니다.
- 프로젝트 이동 후에도 ID를 유지하고 시스템 theme 연동과 제한된 Codex reconnect를 통합했습니다.

</details>

<details>
<summary><b>v8.3.1</b> - Provider 업데이트 통합, 안전한 캐시 폐기, 지속 가능한 Config UI 접근</summary>

- 지원되는 Provider 업그레이드를 `ccb update`로 통합하고 정확한 버전 확인, 거절, 버전별 건너뛰기를 제공합니다. 활성 pane은 자동으로 재시작하지 않습니다.
- 프로젝트별 Claude/Gemini 소프트웨어 캐시를 폐기하고 소유권이 검증된 레거시 데이터만 정리합니다. 활성 프로젝트, session, 인증 데이터는 보존합니다.
- token 값을 노출하지 않으면서 Config UI의 고정 loopback 포트와 보호된 token 소스를 설정할 수 있습니다.
- 서버가 중지되는 동안 shutdown finalizer를 유지하고 Rich mode의 Yazi를 간결한 2열 레이아웃으로 변경했습니다.
- CLI, npm, Linux, macOS, Android와 모든 릴리스 산출물을 8.3.1로 동기화했습니다.

</details>

<details>
<summary><b>v8.3.0</b> - 정확한 provider turn, job 무결성, 프로젝트 내부 Mobile 터미널</summary>

- Kimi, Claude, Qoder 실행을 각각의 네이티브 turn, activation, session, completion 계약에 바인딩했습니다.
- 정확한 active job follow-up, 연계된 실행 단계, 고아 inbound 진단, 종결 cancellation 결과를 추가했습니다.
- provider 확장과 Copilot plugin을 투영 asset의 명시적 ownership 보호와 함께 상속합니다.
- npm-managed 설치의 업그레이드를 npm에 위임하고 marker-only worktree를 보수적으로 정리합니다.
- Mobile chat과 terminal을 선택한 project workspace 안에 유지하고 모든 release surface를 8.3.0으로 동기화했습니다.

</details>

<details>
<summary><b>v8.2.1</b> - 결정적 시작, 실행 가능한 인증 복구, Android 백그라운드 연결</summary>

- 시작 세대 펜스, 제한된 준비 상태 증명, 작업 수와 타임라인 진단을 추가했습니다.
- 복구할 수 없는 Provider 인증 재시작 루프를 중지하고 필요한 로그인 작업을 표시합니다.
- 사용자가 활성화하는 Android 백그라운드 연결과 Agent별 하나의 working reply 상태를 추가했습니다.
- Linux, macOS, npm, 서명된 Android artifact를 8.2.1로 동기화했습니다.

</details>

<details>
<summary><b>v8.2.0</b> - 시작 속도 향상, Provider 수정, Mobile 안정성</summary>

- lifecycle 및 ownership 검증을 유지하면서 ccbd 시작 경로의 반복 작업을 줄였습니다.
- Grok fullscreen 시작, Claude credential 종류 보존, model/thinking 선택 유지, Codex ask/reply 전달을 수정하고 강화했습니다.
- Mobile recovery, chat, terminal, 첨부, download, FCM을 개선하고 Linux, macOS, npm, 서명된 Android artifact를 8.2.0으로 동기화했습니다.

</details>

<details open>
<summary><b>v8.0.14</b> - README 디렉터리 정리와 모바일 릴리스 표면 동기화</summary>

- 루트 `README.md`는 다시 영어 GitHub 홈페이지입니다.
- 지역화된 README는 이제 [`README/`](./) 아래에 있으며 중국어는 [`zh.md`](zh.md)입니다.
- Mobile App 링크, package metadata, release notes는 8.0.14 APK를 가리킵니다.

</details>

<details>
<summary><b>v8.0.12</b> - Release CI 이식성과 README 다국어화</summary>

- mobile host registry 테스트는 이제 임시 Unix sockets를 짧은 `/tmp/ccb-sock-*` 경로 아래에 두어 macOS CI의 `AF_UNIX path too long` 실패를 피합니다.
- `ccb update mobile`, README 링크, package metadata, mobile release manifest가 이제 8.0.12 APK를 가리킵니다.
- v8.0.12에서 공통 섹션 구조의 다국어 README 세트를 도입했습니다. 현재 지역화 파일은 `README/` 디렉터리에 있습니다.

</details>

<details>
<summary><b>v8.0.0</b> - CCB Mobile Monorepo 릴리스</summary>

- Flutter CCB Mobile 소스가 공식적으로 이 저장소에 들어왔고 Android APK가 GitHub Releases로 배포되었습니다.
- server-wide mobile project discovery, pairing, authenticated gateway routes, pane-native message input, conversation context rendering, terminal access, 이미지/문서 업로드 및 다운로드가 추가되었습니다.
- `ccb update mobile`을 통합 Tailscale Tailnet onboarding 진입점으로 승격하면서 gateway는 loopback-only로 유지하고 Funnel을 쓰지 않으며 token을 저장하지 않고 ACLs/grants를 자동 수정하지 않습니다.

</details>

<details>
<summary><b>v7.7.0</b> - Runtime Accelerator 릴리스 강화</summary>

- release artifacts에 선택적 Rust `ccb-runtime-accelerator`가 포함되며, sidecar가 예상되는 설치형 Codex agent가 Python hot path로 조용히 fallback하지 않습니다.
- 프로젝트 경로 때문에 Unix socket path가 너무 길어지면 accelerator socket은 짧은 per-user runtime socket root로 자동 이동합니다.
- callback repair와 Codex binding cache invalidation을 강화하고 regression, long-idle Codex soak, Claude callback, mixed-provider integration 증거를 기록했습니다.

</details>

<details>
<summary><b>v7.6.19</b> - 장시간 ask 기본 대기 정책</summary>

- 일반적인 장시간 `ask`는 heartbeat 진단만으로 `incomplete/heartbeat_timeout`으로 종료하지 않고 실제 provider/completion 결과를 계속 기다립니다.
- Codex, Claude, Gemini의 pane-backed no-terminal timeout은 기본적으로 명시적 opt-in이 되었고, 명시적 reliability timeout policy는 계속 사용할 수 있습니다.
- 32분 source-runtime ask smoke로 작업이 30분 넘게 running 상태를 유지한 뒤 `result_message`로 완료되고 `heartbeat_timeout` 또는 `incomplete` 증거가 없음을 확인했습니다.

</details>

전체 기록은 [CHANGELOG.md](../CHANGELOG.md)를 참조하세요.
