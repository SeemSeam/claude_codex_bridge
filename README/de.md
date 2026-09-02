<div align="center">

# CCB - Die Mobile App ist da!

**Ein leichtgewichtiges Multi-Agent-TUI mit stabiler provider-uebergreifender Kollaborationsschicht**<br>
**Koordiniere Codex, Claude, Gemini und andere CLI-Agenten in sichtbaren, steuerbaren Workflows, die du direkt uebernehmen kannst**

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

[中文](zh.md) | [English](../README.md) | [日本語](ja.md) | [Français](fr.md) | **Deutsch** | [العربية](ar.md) | [Español](es.md) | [Português](pt.md) | [한국어](ko.md) | [Русский](ru.md)

[Schnellstart](#quick-start) · [Mobile App](#mobile-app) · [Rich-Modus](#rich-mode) · [Agents konfigurieren](#configure-agents) · [Benutzerhandbuch](../docs/manuals/user-guide/) · [Entwicklerhandbuch](../docs/manuals/developer-guide/)

<p align="center">
  <img src="../assets/readme_v7/ccb-hero-en-light.png" alt="Sichtbarer Multi-Agent-CLI-Arbeitsbereich von CCB" width="960">
</p>

</div>

<a id="why-ccb"></a>

## Warum CCB?

- Stabile Kommunikation zwischen Agents fuer komplexe Kollaborationsgraphen wie `A -> B -> C`, `A,B -> C` und `A -> B,C`.
- Jeder Agent ist ein vollstaendiges natives Terminal mit sichtbarer Layoutsteuerung und direkter Uebernahme.
- Der Hintergrund-daemon haelt den Projektstatus auch dann am Leben, wenn die Vordergrund-UI geschlossen wird.
- Hub-Faehigkeit: mehrere CLI providers parallel mit einem einzigen Befehl ausfuehren.
- Mobile Fernsteuerung: provider-uebergreifende Sprachsteuerung, Dateiuebertragung und Remote-Terminal-Zugriff.

<a id="how-to-install"></a>

## Installation

Installieren oder aktualisieren Sie eine von npm verwaltete CCB-Installation mit npm:

```bash
npm install -g @seemseam/ccb@latest
```

Für GitHub-release- oder Source-Installationen verwenden Sie den transaktionalen CCB updater:

```bash
ccb update
```

Bei einer von npm verwalteten Installation zeigt `ccb update` den entsprechenden npm-Befehl an und ändert das eingebettete Payload nicht.

<details>
<summary><b>GitHub-release-Pakete und Source-Install als Fallback</b></summary>

Wenn npm in Ihrer Umgebung unpraktisch ist, laden Sie das passende Paket von [Releases](https://github.com/SeemSeam/claude_codex_bridge/releases) herunter, entpacken es und installieren es:

```bash
tar -xzf ccb-*.tar.gz
cd ccb-*
./install.sh install
```

Die Installation aus dem Quellcode ist nur fuer Entwicklung oder temporaere Fallbacks gedacht:

```bash
git clone https://github.com/SeemSeam/claude_codex_bridge.git
cd claude_codex_bridge
./install.sh install
```

Eine Source-Installation verlinkt die globalen Befehle `ccb` / `ask` zurueck auf den Checkout. Normale Benutzer sollten das npm-Paket bevorzugen.

</details>

<a id="quick-start"></a>

## Schnellstart

### 1. Starten

Fuehren Sie dies in Ihrem Arbeitsverzeichnis aus:

```bash
ccb
```

Wenn der Start meldet, dass `.ccb` nicht automatisch erstellt werden kann oder der Projektanker fehlt, erstellen Sie `.ccb` manuell:

```bash
mkdir -p .ccb
```

<a id="configure-agents"></a>

### 2. Projektkonfiguration erstellen

Ein leeres Projekt startet schlank: CCB öffnet nur ein `main`-Window mit einem Agent namens `demo` und wählt das erste auf dem Rechner verfügbare unterstützte CLI. Ein Multi-Agent-Team wird nicht mehr standardmäßig gemountet.

Klicken Sie oben links in der CCB-Sidebar auf **⚙ Einstellungen**, um das lokale Konfigurationspanel zu öffnen. Alternativ können Sie `ccb config ui` ausführen.

<p align="center">
  <img src="../assets/readme_v7/config-control-panel.png" alt="CCB-Konfigurationspanel für den Standard-Agent demo" width="960">
</p>

Das Panel konfiguriert Windows, Pane-Splits, Provider, Modelle, Thinking-Level, API-Overrides, Workspaces, Rich-Modus und Sidebar. Es validiert Änderungen vor dem Speichern und unterstützt Reload-Dry-Runs sowie geschützten Hot Reload.

Für eine fortgeschrittene Multi-Agent-Topologie können Sie visuell weitere Agents hinzufügen oder `.ccb/ccb.config` manuell erstellen. `,` und `;` steuern dabei vertikale Stapelung und horizontale Splits; `A,B;C,D` entspricht etwa vier Panes.

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

Validieren Sie die Konfiguration und starten Sie den Arbeitsbereich:

```bash
ccb config validate
ccb
```

### 3. Zusammenarbeiten

Sie koennen direkt in ein beliebiges Agent-pane schreiben oder Agents zusammenarbeiten lassen:

```text
/ask reviewer review the latest parser changes and list blocking issues.
```

Agents koennen waehrend der Workflow-Orchestrierung auch `/ask` aufrufen, um Arbeit zu delegieren und zu uebergeben. Nutzen Sie Agent-memory oder die projektweite gemeinsame Memory-Datei `.ccb/ccb_memory.md` fuer dauerhafte Koordination.

<a id="mobile-app"></a>

## Mobile Fernsteuerung (Android)

Die empfohlene Steuerung von CCB per Smartphone kann sich mit allen CCB-Projekten verbinden, jeden Agent steuern, Spracheingabe annehmen und Dateien uebertragen.

```bash
ccb update mobile
```

Dieser Befehl fuehrt durch Installation und Konfiguration.

<p align="center">
  <img src="../assets/readme_v7/mobile-control-chat.jpg" alt="CCB Mobile Agent-Chat" width="180">
  <img src="../assets/readme_v7/mobile-control-terminal.jpg" alt="CCB Mobile Terminalsteuerung" width="180">
  <img src="../assets/readme_v7/mobile-control-files.jpg" alt="CCB Mobile Dateiuebertragung" width="180">
  <img src="../assets/readme_v7/mobile-control-pairing.jpg" alt="CCB Mobile Pairing und Verbindung" width="180">
</p>

<details>
<summary><b>Mobile-App-Details, Sicherheitsgrenze und Source</b></summary>

CCB 8.6.6 enthaelt den Flutter-Quellcode von CCB Mobile in [`mobile/`](../mobile/) und veroeffentlicht das Android APK ueber GitHub Releases:

- [CCB Mobile v8.6.6 APK herunterladen](https://github.com/SeemSeam/claude_codex_bridge/releases/download/v8.6.6/ccb-mobile-v8.6.6.apk)
- App-Source: [`mobile/app`](../mobile/app)
- Server-gateway-Source: [`lib/mobile_gateway`](../lib/mobile_gateway)

Die Smartphone-App ist eine Fernsteuerung fuer echte CCB-Projekte auf einem Server. Sie kann gemountete Projekte ueber das server-wide mobile gateway finden, windows und agents wechseln, Agent-Konversationen rendern, Text ueber pane-native input senden, eine Terminalansicht oeffnen und Bilder sowie Dokumente ueber das authentifizierte gateway hoch- und herunterladen.

Sicherheitsgrenze:

- Das CCB gateway bindet nur an loopback, zum Beispiel `127.0.0.1:8787`.
- Remote-Zugriff nutzt Tailscale Serve, nicht Tailscale Funnel.
- CCB speichert keine Tailscale-Passwoerter, OAuth tokens oder admin API tokens und aendert tailnet ACLs/grants nicht automatisch.
- Das Smartphone erhaelt nur die vom pairing profile erlaubten scopes, etwa view, content, terminal, file upload und file download.

</details>

<a id="rich-mode"></a>

## Rich-Medienterminal

Dateibaeume durchsuchen, Dateien oeffnen, Dokumente bearbeiten und Medien im Terminal anzeigen.

<p align="center">
  <img src="../assets/readme_v7/rich-workbench.png" alt="CCB Rich-Medien-Workbench mit Yazi-Vorschau in WezTerm" width="860">
</p>

```bash
ccb update rich
```

Nach Aktivierung des Rich-Modus oeffnet normales `ccb` automatisch den rich WezTerm launcher, sofern es nicht bereits in einer CCB-managed rich WezTerm session laeuft. Mit `ccb uninstall rich` kehren Sie zum normalen Terminalstart zurueck.

<a id="agent-roles"></a>

## Agent Roles Spec und Rollenkatalog

CCB unterstuetzt [Agent Roles Spec](https://github.com/SeemSeam/agent-roles-spec), eine host-neutrale Spezifikation zum Verpacken spezialisierter Agents. Skills, Memory und Tool-Abhaengigkeiten koennen in installierbare, mountbare und entfernbare Role Packs gebuendelt werden. Dieses Repository dient auch als oeffentlicher Rollenkatalog.

| Role | Zweck |
| :--- | :--- |
| `agentroles.ccb_self` | CCB-Selbstwartung, Konfigurationshilfe, Laufzeitdiagnose, geschuetzte Wiederherstellung und Workflow-Orchestrierung. |
| `agentroles.archi` | Architekturreview, Grenzpruefung, Kopplungsanalyse, Wartbarkeitsrisiken und Gate-Empfehlungen. |
| `agentroles.frontend_engineer` | Frontend-Design und Implementierung, Designsysteme, Barrierefreiheit, Browser-QA und gepruefte AGY-Delegation. |
| `agentroles.mobile_app_engineer` | Mobile-Design und Implementierung fuer iOS, Android, React Native, Expo, Flutter, SwiftUI, Jetpack Compose und mehr. |
| `agentroles.mother` | Rollenerstellung, Role-source-Audit, Rollenrecherche, Blueprint-Design und Agent-Roles-Spec-Compliance. |
| `agentroles.su_ccb` | SU-CCB-Workflowbetrieb fuer Anforderungsanalyse, Planung, Dispatch, Review-Gates, Archivierung und Wiederherstellung. |

<a id="config-memory"></a>

## Konfiguration und gemeinsame Memory

Für normale Projektkonfigurationen wird das **⚙ Einstellungen**-Panel empfohlen. Für Agent-gestützte Konfiguration und Laufzeitdiagnose bleibt `ccb_self` als optionales Role Pack verfügbar und kann mit `ccb roles add agentroles.ccb_self:codex` hinzugefügt werden.

`.ccb/ccb_memory.md` ist das projektweite gemeinsame Memory-Dokument. Nutzen Sie es fuer Teamregeln, Projektbeschraenkungen, langlebigen Kontext und Uebergabekonventionen zwischen Agents. Stabile agent-uebergreifende Informationen gehoeren dorthin, statt in mehrere provider-private Memory-Dateien kopiert zu werden.

<a id="contact"></a>

## Kontakt

- Email: `bfly123@126.com`
- [Telegram group & contact / TG 群与联系](https://t.me/+BKn03v8I_ehmYzRk)
- WeChat: `seemseam-com`

<p align="center">
  <img src="../assets/weixin.png?v=7335e843" alt="WeChat-Gruppe" width="240">
</p>

<a id="community"></a>

## Community und Danksagung

Danke an die [Linux.do community](https://linux.do) fuer Tests, Feedback und Diskussionen.

Danke an [tmux-agent-sidebar](https://github.com/hiroppy/tmux-agent-sidebar) fuer Sidebar-Ideen und Inspiration.

<a id="release-notes"></a>

## Release Notes

<details open>
<summary><b>v8.4.0</b> - Verschluesseltes Mobile Relay, einfaches Pairing, stabile Projekt-ID und Codex-Reconnect</summary>

- Fuegt Ende-zu-Ende-verschluesseltes Relay, einmalige Einladungen, multiplexte Streams sowie offizielle oder selbst gehostete Modi hinzu.
- Verschiebt die Auswahl von Tailscale, privatem LAN oder Relay nach `ccb update mobile`; das Telefon scannt nur QR oder gibt einen Code ein.
- Prueft offizielle GitHub-Metadaten, Groesse und SHA-256, bevor Android ein signiertes APK installiert.
- Bewahrt die Projekt-ID nach Verschiebungen, folgt dem System-Theme und integriert begrenztes opt-in Codex reconnect.

</details>

<details>
<summary><b>v8.3.1</b> - Einheitliche Provider-Updates, sichere Cache-Ablösung und persistenter Config-UI-Zugriff</summary>

- Buendelt unterstuetzte Provider-Upgrades in `ccb update`, einschliesslich exakter Versionspruefung, Ablehnen und versionsgenauem Ueberspringen, ohne aktive Panes neu zu starten.
- Ersetzt projektbezogene Claude-/Gemini-Software-Caches und bereinigt nur eindeutig CCB-eigene Altlasten; aktive Projekte, Sessions und Auth-Daten bleiben erhalten.
- Ermoeglicht einen festen Loopback-Port und eine geschuetzte Token-Quelle fuer die Config UI, ohne Token-Werte auszugeben.
- Erhaelt Shutdown-Finalizer waehrend des Serverstopps und nutzt im Rich Mode ein kompaktes Yazi-Layout mit zwei Spalten.
- Synchronisiert CLI, npm, Linux, macOS, Android und alle Release-Artefakte auf 8.3.1.

</details>

<details>
<summary><b>v8.3.0</b> - Exakte Provider-Turns, Job-Integritaet und projektgebundenes Mobile-Terminal</summary>

- Bindet Kimi, Claude und Qoder an ihre nativen Turn-, Aktivierungs-, Session- und Completion-Vertraege.
- Ergaenzt Follow-ups fuer den exakten aktiven Job, korrelierte Ausfuehrungsphasen, Diagnose verwaister Inbounds und terminale Cancellation-Ergebnisse.
- Uebernimmt Provider-Erweiterungen und Copilot-Plugins mit explizitem Ownership-Schutz fuer projizierte Assets.
- Delegiert npm-verwaltete Upgrades an npm und entfernt Marker-only-Worktrees konservativ.
- Haelt Mobile-Chat und -Terminal im Workspace des ausgewaehlten Projekts und synchronisiert alle Release-Oberflaechen mit 8.3.0.

</details>

<details>
<summary><b>v8.2.1</b> - Deterministischer Start, klare Auth-Recovery und Android-Hintergrundzugriff</summary>

- Fuegt durchgaengige Startup-Generationszaeune, begrenzte Readiness-Nachweise und Zeitliniendiagnosen hinzu.
- Stoppt aussichtslose Provider-Auth-Neustartschleifen und zeigt die erforderliche Login-Aktion an.
- Ergaenzt opt-in Android-Hintergrundverbindungen und genau einen laufenden Reply-Status pro Agent.
- Synchronisiert Linux-, macOS-, npm- und signierte Android-Artefakte auf 8.2.1.

</details>

<details>
<summary><b>v8.2.0</b> - Schnellerer Start, Provider-Fixes und Mobile-Zuverlaessigkeit</summary>

- Reduziert wiederholte Arbeit beim ccbd-Start, ohne Lifecycle- und Ownership-Pruefungen abzuschwaechen.
- Behebt Grok-Fullscreen-Start, erhaelt den Claude-Credential-Typ, stabilisiert Model-/Thinking-Auswahl und haertet Codex-Ask- sowie Reply-Bestaetigungen.
- Verbessert Mobile-Recovery, Chat, Terminal, Anhaenge, Downloads und FCM; Linux-, macOS-, npm- und signierte Android-Artefakte sind auf 8.2.0 synchronisiert.

</details>

<details open>
<summary><b>v8.0.14</b> - README-Verzeichnis bereinigt und Mobile-Release-Flaeche synchronisiert</summary>

- Das Root-`README.md` ist wieder die englische GitHub-Startseite.
- Lokalisierte README-Dateien liegen jetzt unter [`README/`](./), die chinesische Version unter [`zh.md`](zh.md).
- Mobile-App-Links, package metadata und release notes zeigen auf das 8.0.14 APK.

</details>

<details>
<summary><b>v8.0.12</b> - Release-CI-Portabilitaet und README-Lokalisierung</summary>

- Mobile-host-registry-Tests legen temporaere Unix sockets jetzt unter einem kurzen `/tmp/ccb-sock-*` Pfad ab und vermeiden dadurch `AF_UNIX path too long` Fehler in macOS CI.
- `ccb update mobile`, README-Links, package metadata und das mobile release manifest zeigen jetzt auf das 8.0.12 APK.
- v8.0.12 fuehrte mehrsprachige READMEs mit gemeinsamer Abschnittsstruktur ein; die aktuellen lokalisierten Dateien liegen im Verzeichnis `README/`.

</details>

<details>
<summary><b>v8.0.0</b> - CCB Mobile Monorepo Release</summary>

- Der Flutter-Source von CCB Mobile wurde offiziell in dieses Repository aufgenommen, mit Android APKs ueber GitHub Releases.
- Hinzu kamen server-wide mobile project discovery, pairing, authentifizierte gateway routes, pane-native message input, conversation context rendering, terminal access sowie Upload/Download von Bildern und Dokumenten.
- `ccb update mobile` wurde zum einheitlichen Tailscale-Tailnet-onboarding entrypoint, waehrend das gateway loopback-only bleibt, Funnel nicht nutzt, keine tokens speichert und ACLs/grants nicht automatisch aendert.

</details>

<details>
<summary><b>v7.7.0</b> - Runtime Accelerator Release-Hardening</summary>

- Release artifacts enthalten jetzt den optionalen Rust `ccb-runtime-accelerator`; installierte Codex agents fallen nicht mehr still auf den Python hot path zurueck, wenn der sidecar erwartet wird.
- Wenn ein Projektpfad den Unix-socket-Pfad zu lang macht, wechselt der accelerator socket automatisch zu einer kurzen per-user runtime socket root.
- Callback repair und Codex binding cache invalidation wurden gehaertet, mit aufgezeichneten Regression-, long-idle Codex soak-, Claude callback- und mixed-provider integration-Nachweisen.

</details>

<details>
<summary><b>v7.6.19</b> - Standard-Wartepolitik fuer lange ask-Aufrufe</summary>

- Normale lang laufende `ask`-Aufrufe warten weiter auf echte provider/completion-Ergebnisse, statt nur wegen heartbeat-Diagnosen als `incomplete/heartbeat_timeout` zu enden.
- Codex-, Claude- und Gemini-pane-backed no-terminal timeouts sind nun standardmaessig explizit opt-in; explizite reliability timeout policies bleiben verfuegbar.
- Ein 32-minuetiger source-runtime ask smoke bestaetigte, dass eine Aufgabe ueber 30 Minuten running bleiben und danach mit `result_message` abschliessen kann, ohne `heartbeat_timeout` oder `incomplete`.

</details>

Die vollstaendige Historie steht in [CHANGELOG.md](../CHANGELOG.md).
