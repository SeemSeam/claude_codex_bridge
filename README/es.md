<div align="center">

# CCB - ¡La app móvil ya llegó!

**Un TUI multiagente ligero con una capa estable de colaboración entre proveedores**<br>
**Coordina Codex, Claude, Gemini y otros agentes CLI en flujos visibles y controlables que puedes tomar directamente**

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

[中文](zh.md) | [English](../README.md) | [日本語](ja.md) | [Français](fr.md) | [Deutsch](de.md) | [العربية](ar.md) | **Español** | [Português](pt.md) | [한국어](ko.md) | [Русский](ru.md)

[Inicio rápido](#quick-start) · [Mobile App](#mobile-app) · [Modo Rich](#rich-mode) · [Configurar agentes](#configure-agents) · [Guía de usuario](../docs/manuals/user-guide/) · [Guía de desarrollo](../docs/manuals/developer-guide/)

<p align="center">
  <img src="../assets/readme_v7/ccb-hero-en-light.png" alt="Espacio de trabajo CLI multiagente visible de CCB" width="960">
</p>

</div>

<a id="why-ccb"></a>

## ¿Por qué CCB?

- Comunicación estable entre agentes para grafos complejos como `A -> B -> C`, `A,B -> C` y `A -> B,C`.
- Cada agente es una terminal nativa completa, con control visible del diseño y toma directa.
- El daemon en segundo plano conserva el estado del proyecto aunque se cierre la interfaz frontal.
- Capacidad Hub: ejecutar varios CLI providers en paralelo desde un solo comando.
- Controlador móvil remoto: control por voz entre providers, transferencia de archivos y acceso a terminal remoto.

<a id="how-to-install"></a>

## Cómo instalar

Instala o actualiza con npm:

```bash
npm install -g @seemseam/ccb
```

Después de instalar CCB, usa su updater:

```bash
ccb update
```

<details>
<summary><b>Paquete de GitHub release e instalación desde fuente como respaldo</b></summary>

Si npm no es conveniente en tu entorno, descarga el paquete adecuado desde [Releases](https://github.com/SeemSeam/claude_codex_bridge/releases), descomprímelo e instálalo:

```bash
tar -xzf ccb-*.tar.gz
cd ccb-*
./install.sh install
```

La instalación desde fuente está pensada solo para desarrollo o respaldo temporal:

```bash
git clone https://github.com/SeemSeam/claude_codex_bridge.git
cd claude_codex_bridge
./install.sh install
```

La instalación desde fuente enlaza los comandos globales `ccb` / `ask` al checkout actual. Los usuarios normales deberían preferir el paquete npm.

</details>

<a id="quick-start"></a>

## Inicio rápido

### 1. Iniciar

Ejecuta esto desde tu directorio de trabajo:

```bash
ccb
```

Si el arranque indica que `.ccb` no puede crearse automáticamente o que falta el ancla del proyecto, créala manualmente:

```bash
mkdir -p .ccb
```

<a id="configure-agents"></a>

### 2. Crear la configuración del proyecto

Un proyecto vacío arranca de forma ligera: CCB abre una sola window `main` con un agente llamado `demo` y selecciona el primer CLI compatible disponible en el equipo. Ya no monta un equipo multiagente por defecto.

Haz clic en **⚙ Configuración** en la esquina superior izquierda de la sidebar de CCB para abrir el panel de configuración local. También puedes ejecutar `ccb config ui`.

<p align="center">
  <img src="../assets/readme_v7/config-control-panel.png" alt="Panel de configuración de CCB para el agente demo predeterminado" width="960">
</p>

El panel configura windows, divisiones de panes, providers, modelos, niveles de thinking, API overrides, workspaces, modo Rich y sidebar. Valida antes de guardar y admite reload dry-run y hot reload protegido.

Para una topología multiagente avanzada, añade agentes visualmente o crea `.ccb/ccb.config` manualmente. `,` y `;` controlan el apilamiento vertical y las divisiones horizontales; `A,B;C,D` se aproxima a cuatro panes.

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

Valida la configuración e inicia el espacio de trabajo:

```bash
ccb config validate
ccb
```

### 3. Colaborar

Puedes escribir directamente en cualquier agent pane o hacer que los agentes colaboren:

```text
/ask reviewer review the latest parser changes and list blocking issues.
```

Los agentes también pueden llamar a `/ask` durante la orquestación de workflows para delegar y entregar trabajo. Usa la memoria de agente o el archivo compartido del proyecto `.ccb/ccb_memory.md` para coordinación duradera.

<a id="mobile-app"></a>

## Control remoto móvil (Android)

La forma recomendada de controlar CCB desde un teléfono puede conectarse a todos los proyectos CCB, controlar cada agente, aceptar entrada de voz y transferir archivos.

```bash
ccb update mobile
```

Este comando guía la instalación y configuración.

<p align="center">
  <img src="../assets/readme_v7/mobile-control-chat.jpg" alt="Chat de agente en CCB Mobile" width="180">
  <img src="../assets/readme_v7/mobile-control-terminal.jpg" alt="Control de terminal en CCB Mobile" width="180">
  <img src="../assets/readme_v7/mobile-control-files.jpg" alt="Transferencia de archivos en CCB Mobile" width="180">
  <img src="../assets/readme_v7/mobile-control-pairing.jpg" alt="Emparejamiento y conexión en CCB Mobile" width="180">
</p>

<details>
<summary><b>Detalles de Mobile App, límite de seguridad y fuente</b></summary>

CCB Mobile no cambia en la versión de escritorio v8.1.6; el último APK Android conservado es v8.1.4:

- [Descargar CCB Mobile v8.1.4 APK](https://github.com/SeemSeam/claude_codex_bridge/releases/download/v8.1.4/ccb-mobile-v8.1.4.apk)
- Fuente de la app: [`mobile/app`](../mobile/app)
- Fuente del gateway del servidor: [`lib/mobile_gateway`](../lib/mobile_gateway)

La app del teléfono es un controlador remoto para proyectos CCB reales que corren en un servidor. Puede descubrir proyectos montados desde el mobile gateway server-wide, cambiar windows y agents, renderizar contexto de conversación, enviar texto por entrada pane-native, abrir vista terminal y subir/descargar imágenes y documentos por el gateway autenticado.

Límite de seguridad:

- El gateway CCB solo se enlaza a loopback, por ejemplo `127.0.0.1:8787`.
- El acceso remoto usa Tailscale Serve, no Tailscale Funnel.
- CCB no guarda contraseñas de Tailscale, OAuth tokens, admin API tokens, ni modifica automáticamente ACLs/grants del tailnet.
- El teléfono recibe solo los scopes autorizados por el pairing profile, como view, content, terminal, file upload y file download.

</details>

<a id="rich-mode"></a>

## Terminal multimedia Rich

Explora árboles de archivos, abre archivos, edita documentos y previsualiza medios dentro de la terminal.

<p align="center">
  <img src="../assets/readme_v7/rich-workbench.png" alt="Workbench multimedia Rich de CCB con vista previa Yazi en WezTerm" width="860">
</p>

```bash
ccb update rich
```

Después de activar rich mode, `ccb` abre automáticamente el rich WezTerm launcher salvo que ya esté dentro de una sesión rich WezTerm gestionada por CCB. Ejecuta `ccb uninstall rich` para volver al inicio normal de terminal.

<a id="agent-roles"></a>

## Agent Roles Spec y catálogo de roles

CCB admite [Agent Roles Spec](https://github.com/SeemSeam/agent-roles-spec), una especificación host-neutral para empaquetar agentes especialistas. Puede agrupar skills, memoria y dependencias de herramientas en Role Packs instalables, montables y removibles. Ese repositorio también funciona como catálogo público de roles.

| Role | Propósito |
| :--- | :--- |
| `agentroles.ccb_self` | Automantenimiento de CCB, ayuda de configuración, diagnóstico runtime, recuperación protegida y orquestación de workflows. |
| `agentroles.archi` | Revisión de arquitectura, verificación de límites, análisis de acoplamiento, riesgos de mantenibilidad y recomendaciones de gates. |
| `agentroles.frontend_engineer` | Diseño e implementación frontend, design systems, accesibilidad, QA de navegador y delegación AGY revisada. |
| `agentroles.mobile_app_engineer` | Diseño e implementación móvil para iOS, Android, React Native, Expo, Flutter, SwiftUI, Jetpack Compose y más. |
| `agentroles.mother` | Creación de roles, auditoría de role source, investigación de roles, diseño de blueprints y controles de cumplimiento de Agent Roles. |
| `agentroles.su_ccb` | Operaciones workflow SU-CCB para análisis de requisitos, planificación, dispatch, review gates, archivado y recuperación. |

<a id="config-memory"></a>

## Configuración y memoria compartida

Para la configuración normal del proyecto, usa el panel **⚙ Configuración**. Si quieres configuración asistida por un agente y diagnóstico runtime, `ccb_self` sigue disponible como Role Pack opcional y se añade con `ccb roles add agentroles.ccb_self:codex`.

`.ccb/ccb_memory.md` es el documento de memoria compartida de todo el proyecto. Úsalo para reglas de colaboración del equipo, restricciones del proyecto, contexto duradero y convenciones de entrega entre agentes. La información estable entre agentes debe vivir ahí en vez de copiarse en varias memorias privadas de providers.

<a id="contact"></a>

## Contacto

- Email: `bfly123@126.com`
- [Telegram group & contact / TG 群与联系](https://t.me/+BKn03v8I_ehmYzRk)
- WeChat: `seemseam-com`

<p align="center">
  <img src="../assets/weixin.jpg" alt="Grupo WeChat" width="240">
</p>

<a id="community"></a>

## Comunidad y créditos

Gracias a la [comunidad Linux.do](https://linux.do) por pruebas, comentarios y discusión.

Gracias a [tmux-agent-sidebar](https://github.com/hiroppy/tmux-agent-sidebar) por las ideas e inspiración de la sidebar.

<a id="release-notes"></a>

## Notas de versión

<details open>
<summary><b>v8.0.14</b> - Limpieza del directorio README y superficie mobile</summary>

- El `README.md` raíz vuelve a ser la portada GitHub en inglés.
- Los README localizados ahora viven en [`README/`](./), con chino en [`zh.md`](zh.md).
- Los enlaces de Mobile App, package metadata y release notes apuntan al APK 8.0.14.

</details>

<details>
<summary><b>v8.0.12</b> - Portabilidad de Release CI y localización del README</summary>

- Las pruebas mobile host registry ahora colocan sockets Unix temporales bajo una ruta corta `/tmp/ccb-sock-*`, evitando fallos `AF_UNIX path too long` en macOS CI.
- `ccb update mobile`, los enlaces del README, los metadatos del paquete y el mobile release manifest ahora apuntan al APK 8.0.12.
- v8.0.12 introdujo los README multilingues con una estructura de secciones compartida; los archivos localizados actuales viven en el directorio `README/`.

</details>

<details>
<summary><b>v8.0.0</b> - Publicación de CCB Mobile Monorepo</summary>

- El código Flutter de CCB Mobile entró oficialmente en este repositorio, con el APK Android publicado mediante GitHub Releases.
- Se agregó descubrimiento server-wide de proyectos móviles, pairing, rutas gateway autenticadas, entrada pane-native, renderizado de contexto de conversación, acceso terminal y subida/descarga de imágenes y documentos.
- `ccb update mobile` pasó a ser el punto de entrada unificado de onboarding de Tailscale Tailnet, manteniendo el gateway solo en loopback, sin Funnel, sin guardar tokens y sin modificar ACLs/grants automáticamente.

</details>

<details>
<summary><b>v7.7.0</b> - Endurecimiento de publicación de Runtime Accelerator</summary>

- Los release artifacts ahora incluyen el Rust `ccb-runtime-accelerator` opcional; los agentes Codex instalados ya no caen silenciosamente al Python hot path cuando se espera el sidecar.
- Cuando la ruta del proyecto hace demasiado larga la ruta Unix socket, el accelerator socket se mueve automáticamente a una raíz runtime corta por usuario.
- Se reforzó callback repair y la invalidación de cache de binding Codex, con evidencia de regresión, long-idle Codex soak, callback Claude e integración mixed-provider.

</details>

<details>
<summary><b>v7.6.19</b> - Política de espera predeterminada para ask largos</summary>

- Los `ask` largos normales siguen esperando resultados reales de provider/completion en vez de terminar como `incomplete/heartbeat_timeout` solo por diagnósticos heartbeat.
- Los no-terminal timeouts pane-backed de Codex, Claude y Gemini ahora son opt-in explícito por defecto, manteniendo disponibles las políticas explícitas de reliability timeout.
- Un smoke source-runtime ask de 32 minutos confirmó que una tarea puede permanecer running más de 30 minutos y luego completar con `result_message`, sin evidencia de `heartbeat_timeout` ni `incomplete`.

</details>

Consulta el historial completo en [CHANGELOG.md](../CHANGELOG.md).
