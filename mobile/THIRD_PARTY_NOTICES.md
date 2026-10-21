# CCB Mobile Third-Party Notices

## Paseo Provider Control Semantics

- Project: Paseo
- Source: https://github.com/getpaseo/paseo
- Pinned source commit: `b599d38a772f621e0001abfb90a769de11c8cd8b`
- Copyright: Copyright 2025-present Mohamed Boudra
- License: GNU Affero General Public License v3.0 or later
- CCB modifications: Python gateway/ccbd and Flutter adaptations for CCB's
  project/window/agent/session authority, device scopes, Relay transport, and
  restart-required configuration model.

Source-to-target mapping:

| Paseo source | CCB adaptation |
| :--- | :--- |
| `packages/protocol/src/agent-types.ts` | `lib/provider_control/session_usage.py`, `mobile/app/lib/models/ccb_provider_control.dart` |
| `packages/protocol/src/messages.ts` | `lib/mobile_gateway/service.py`, Flutter repository/transport Provider-control interfaces |
| `packages/protocol/src/provider-manifest.ts` | `lib/cli/services/config_ui.py`, ProjectView Provider capability records |
| `packages/server/src/server/agent/provider-snapshot-manager.ts` | bounded transcript revision cache and Provider runtime snapshots |
| `packages/server/src/services/quota-fetcher/` | `lib/provider_control/quota.py` |
| `packages/app/src/provider-selection/` | `mobile/app/lib/features/provider_control/provider_control_sheet.dart` |
| `packages/app/src/provider-usage/` | Flutter session-usage and account-quota sections |

No Paseo daemon, React Native component, asset, icon, credential, or runtime
process is bundled. The implementation adapts data contracts, normalization,
state semantics, and interaction behavior to CCB's existing architecture.

## OMP Collab Protocol

- Project: oh-my-pi, https://github.com/can1357/oh-my-pi
- Reference release: v18.11.0, Collab wire protocol v3 and local host registry v1.
- License: MIT (upstream sources and notices remain with the installed OMP distribution).
- CCB implements a private loopback relay and encrypted guest in
  `lib/provider_backends/omp/collab_runtime.mjs`, and a restricted model/editor
  companion in the managed extension. The installed upstream runtime is not
  copied into the mobile application; capabilities stay on the server.
- Native verification fixtures load the installed release's real CollabHost,
  SessionManager and native addon. Deterministic fixture outputs do not establish
  external model quality or paid-provider reliability.

