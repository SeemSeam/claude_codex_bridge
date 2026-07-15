<div align="center">

# CCB - мобильное приложение уже здесь!

**Легкий multi-agent TUI со стабильным слоем взаимодействия между провайдерами**<br>
**Координируйте Codex, Claude, Gemini и другие CLI Agent в видимых и управляемых процессах, которые можно напрямую взять под контроль**

<p>
  <img src="https://img.shields.io/badge/version-8.1.6-orange.svg" alt="version">
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

[中文](zh.md) | [English](../README.md) | [日本語](ja.md) | [Français](fr.md) | [Deutsch](de.md) | [العربية](ar.md) | [Español](es.md) | [Português](pt.md) | [한국어](ko.md) | **Русский**

[Быстрый старт](#quick-start) · [Mobile App](#mobile-app) · [Rich-режим](#rich-mode) · [Настройка agents](#configure-agents) · [Руководство пользователя](../docs/manuals/user-guide/) · [Руководство разработчика](../docs/manuals/developer-guide/)

<p align="center">
  <img src="../assets/readme_v7/ccb-hero-en-light.png" alt="Видимое multi-agent CLI рабочее пространство CCB" width="960">
</p>

</div>

<a id="why-ccb"></a>

## Почему CCB?

- Стабильная коммуникация между agents для сложных графов сотрудничества, таких как `A -> B -> C`, `A,B -> C` и `A -> B,C`.
- Каждый agent является полноценным нативным терминалом с видимым управлением layout и прямым перехватом управления.
- Фоновый daemon сохраняет состояние проекта даже после закрытия foreground UI.
- Возможность Hub: параллельно запускать несколько CLI providers одной командой.
- Мобильный удаленный контроллер: голосовое управление между providers, передача файлов и доступ к удаленному терминалу.

<a id="how-to-install"></a>

## Как установить

Установите или обновите через npm:

```bash
npm install -g @seemseam/ccb
```

После установки CCB используйте встроенный updater:

```bash
ccb update
```

<details>
<summary><b>GitHub release package и установка из исходников как fallback</b></summary>

Если npm неудобен в вашей среде, скачайте подходящий пакет из [Releases](https://github.com/SeemSeam/claude_codex_bridge/releases), распакуйте и установите:

```bash
tar -xzf ccb-*.tar.gz
cd ccb-*
./install.sh install
```

Установка из исходников предназначена только для разработки или временного fallback:

```bash
git clone https://github.com/SeemSeam/claude_codex_bridge.git
cd claude_codex_bridge
./install.sh install
```

Установка из исходников связывает глобальные команды `ccb` / `ask` с текущим checkout. Обычным пользователям рекомендуется npm package.

</details>

<a id="quick-start"></a>

## Быстрый старт

### 1. Запуск

Выполните в рабочем каталоге:

```bash
ccb
```

Если запуск сообщает, что `.ccb` не может быть создан автоматически или отсутствует project anchor, создайте `.ccb` вручную:

```bash
mkdir -p .ccb
```

<a id="configure-agents"></a>

### 2. Создать конфигурацию проекта

Пустой проект запускается в легком режиме: CCB открывает только одно window `main`, выбирает первый доступный на машине поддерживаемый CLI и создает одного agent с именем `demo`. Multi-agent команда больше не монтируется по умолчанию.

Нажмите **⚙ Настройки** в левом верхнем углу sidebar CCB, чтобы открыть локальную панель конфигурации. Ее также можно запустить командой `ccb config ui`.

<p align="center">
  <img src="../assets/readme_v7/config-control-panel.png" alt="Панель конфигурации CCB для стандартного agent demo" width="960">
</p>

Панель настраивает windows, splits panes, providers, models, thinking levels, API overrides, workspaces, Rich mode и sidebar. Перед сохранением выполняется проверка; доступны reload dry-run и защищенный hot reload.

Для расширенной multi-agent topology добавьте agents визуально или создайте `.ccb/ccb.config` вручную. `,` и `;` управляют вертикальной укладкой и горизонтальными splits; `A,B;C,D` близко к четырем panes.

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

Проверьте конфигурацию и запустите workspace:

```bash
ccb config validate
ccb
```

### 3. Совместная работа

Можно вводить текст напрямую в любой agent pane или позволить agents сотрудничать:

```text
/ask reviewer review the latest parser changes and list blocking issues.
```

Agents также могут вызывать `/ask` во время workflow orchestration, чтобы делегировать и передавать работу. Для устойчивой координации используйте agent memory или общий файл памяти проекта `.ccb/ccb_memory.md`.

<a id="mobile-app"></a>

## Мобильное удаленное управление (Android)

Рекомендуемый способ управления CCB с телефона может подключаться ко всем CCB projects, управлять каждым agent, принимать голосовой ввод и передавать файлы.

```bash
ccb update mobile
```

Эта команда проведет через установку и настройку.

<p align="center">
  <img src="../assets/readme_v7/mobile-control-chat.jpg" alt="Чат agent в CCB Mobile" width="180">
  <img src="../assets/readme_v7/mobile-control-terminal.jpg" alt="Управление терминалом в CCB Mobile" width="180">
  <img src="../assets/readme_v7/mobile-control-files.jpg" alt="Передача файлов в CCB Mobile" width="180">
  <img src="../assets/readme_v7/mobile-control-pairing.jpg" alt="Pairing и подключение в CCB Mobile" width="180">
</p>

<details>
<summary><b>Детали Mobile App, граница безопасности и исходники</b></summary>

CCB Mobile не изменяется в desktop v8.1.6; последняя сохранённая Android APK — v8.1.4:

- [Скачать CCB Mobile v8.1.4 APK](https://github.com/SeemSeam/claude_codex_bridge/releases/download/v8.1.4/ccb-mobile-v8.1.4.apk)
- Исходники app: [`mobile/app`](../mobile/app)
- Исходники server gateway: [`lib/mobile_gateway`](../lib/mobile_gateway)

Телефонное приложение является удаленным контроллером для реальных CCB projects, работающих на сервере. Оно может находить mounted projects через server-wide mobile gateway, переключать windows и agents, отображать контекст разговоров agents, отправлять текст через pane-native input, открывать terminal view и загружать/скачивать изображения и документы через authenticated gateway.

Граница безопасности:

- CCB gateway привязывается только к loopback, например `127.0.0.1:8787`.
- Удаленный доступ использует Tailscale Serve, а не Tailscale Funnel.
- CCB не хранит пароли Tailscale, OAuth tokens или admin API tokens и не изменяет автоматически tailnet ACLs/grants.
- Телефон получает только scopes, разрешенные pairing profile, например view, content, terminal, file upload и file download.

</details>

<a id="rich-mode"></a>

## Rich media terminal

Просматривайте деревья файлов, открывайте файлы, редактируйте документы и preview медиа внутри терминала.

<p align="center">
  <img src="../assets/readme_v7/rich-workbench.png" alt="CCB rich media workbench с Yazi preview в WezTerm" width="860">
</p>

```bash
ccb update rich
```

После включения rich mode обычный `ccb` автоматически открывает rich WezTerm launcher, если он еще не работает внутри CCB-managed rich WezTerm session. Выполните `ccb uninstall rich`, чтобы вернуться к обычному запуску терминала.

<a id="agent-roles"></a>

## Agent Roles Spec и каталог ролей

CCB поддерживает [Agent Roles Spec](https://github.com/SeemSeam/agent-roles-spec), host-neutral спецификацию для упаковки специализированных agents. Она может объединять skills, memory и tool dependencies в устанавливаемые, монтируемые и удаляемые Role Packs. Этот репозиторий также служит публичным каталогом ролей.

| Role | Назначение |
| :--- | :--- |
| `agentroles.ccb_self` | Самообслуживание CCB, помощь с конфигурацией, runtime diagnosis, protected recovery и workflow orchestration. |
| `agentroles.archi` | Архитектурный review, проверка границ, анализ связности, риски maintainability и рекомендации gate. |
| `agentroles.frontend_engineer` | Frontend design и implementation, design systems, accessibility, browser QA и проверенная AGY delegation. |
| `agentroles.mobile_app_engineer` | Mobile design и implementation для iOS, Android, React Native, Expo, Flutter, SwiftUI, Jetpack Compose и другого. |
| `agentroles.mother` | Создание ролей, role source audit, role research, blueprint design и проверки соответствия Agent Roles spec. |
| `agentroles.su_ccb` | Операции SU-CCB workflow для анализа требований, planning, dispatch, review gates, архивирования и recovery. |

<a id="config-memory"></a>

## Конфигурация и общая память

Для обычной конфигурации проекта используйте панель **⚙ Настройки**. Для настройки с помощью agent и runtime diagnosis `ccb_self` остается доступным как optional Role Pack и добавляется командой `ccb roles add agentroles.ccb_self:codex`.

`.ccb/ccb_memory.md` является проектным документом общей памяти. Используйте его для правил командной работы, ограничений проекта, долгоживущего контекста и соглашений handoff между agents. Стабильная информация между agents должна храниться там, а не копироваться в несколько provider-private memory files.

<a id="contact"></a>

## Контакты

- Email: `bfly123@126.com`
- [Telegram group & contact / TG 群与联系](https://t.me/+BKn03v8I_ehmYzRk)
- WeChat: `seemseam-com`

<p align="center">
  <img src="../assets/weixin.jpg" alt="Группа WeChat" width="240">
</p>

<a id="community"></a>

## Сообщество и благодарности

Спасибо [сообществу Linux.do](https://linux.do) за тестирование, обратную связь и обсуждения.

Спасибо [tmux-agent-sidebar](https://github.com/hiroppy/tmux-agent-sidebar) за идеи и вдохновение для sidebar.

<a id="release-notes"></a>

## Release Notes

<details open>
<summary><b>v8.0.14</b> - Упорядочен каталог README и mobile release surface</summary>

- Корневой `README.md` снова является английской страницей GitHub.
- Локализованные README теперь находятся в [`README/`](./), китайская версия - [`zh.md`](zh.md).
- Ссылки Mobile App, package metadata и release notes указывают на APK 8.0.14.

</details>

<details>
<summary><b>v8.0.12</b> - Переносимость Release CI и локализация README</summary>

- Тесты mobile host registry теперь размещают временные Unix sockets в коротком пути `/tmp/ccb-sock-*`, чтобы избежать ошибок `AF_UNIX path too long` в macOS CI.
- `ccb update mobile`, ссылки README, package metadata и mobile release manifest теперь указывают на APK 8.0.12.
- v8.0.12 добавил многоязычный набор README с общей структурой разделов; текущие локализованные файлы находятся в каталоге `README/`.

</details>

<details>
<summary><b>v8.0.0</b> - Релиз CCB Mobile Monorepo</summary>

- Flutter source CCB Mobile официально вошел в этот репозиторий, а Android APK публикуется через GitHub Releases.
- Добавлены server-wide mobile project discovery, pairing, authenticated gateway routes, pane-native message input, conversation context rendering, terminal access и upload/download изображений и документов.
- `ccb update mobile` стал единым entrypoint для Tailscale Tailnet onboarding, при этом gateway остается loopback-only, Funnel не используется, tokens не сохраняются и ACLs/grants не изменяются автоматически.

</details>

<details>
<summary><b>v7.7.0</b> - Укрепление релиза Runtime Accelerator</summary>

- Release artifacts теперь включают optional Rust `ccb-runtime-accelerator`; installed Codex agents больше не откатываются тихо на Python hot path, когда ожидается sidecar.
- Если путь проекта делает Unix socket path слишком длинным, accelerator socket автоматически переходит в короткий per-user runtime socket root.
- Усилены callback repair и Codex binding cache invalidation, с зафиксированными доказательствами regression, long-idle Codex soak, Claude callback и mixed-provider integration.

</details>

<details>
<summary><b>v7.6.19</b> - Политика ожидания по умолчанию для долгих ask</summary>

- Обычные долгие вызовы `ask` теперь продолжают ждать реальные provider/completion результаты вместо завершения как `incomplete/heartbeat_timeout` только из-за heartbeat diagnostics.
- Pane-backed no-terminal timeouts для Codex, Claude и Gemini теперь по умолчанию explicit opt-in, при этом explicit reliability timeout policies остаются доступными.
- 32-минутный source-runtime ask smoke подтвердил, что задача может оставаться running более 30 минут, затем завершиться с `result_message`, без признаков `heartbeat_timeout` или `incomplete`.

</details>

Полная история находится в [CHANGELOG.md](../CHANGELOG.md).
