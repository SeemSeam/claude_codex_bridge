# Provider Extension Inheritance Audit

Date: 2026-07-20

Role: cross-provider capability and storage-boundary audit
Status: R1/R2 landed; R11 provider follow-up committed on its qualified branch
Related: [roadmap](../roadmap.md), [ordered repair slices](ordered-repair-slices.md)

## First-Principles Contract

A managed provider home is correct only when all five conditions hold:

1. Configuration that enables an extension and the extension authority needed
   to load it are visible in the same managed session.
2. Read-only source authority may be shared only through a provider-supported
   seed boundary or an immutable content-addressed snapshot.
3. Provider-writable marketplace, cache, plugin-data, and runtime state remains
   agent-local.
4. CCB replaces or removes only targets carrying a valid matching CCB ownership
   marker. Path equality and residence below provider-state are not ownership.
5. Inheritance opt-out and hard role policy prevent source capability exposure.

Copying an entire provider home violates secret/session isolation. Symlinking a
writable cache violates source and cross-agent isolation. Projecting only an
`enabledPlugins`-style setting violates startup completeness.

## Audit Matrix

| Provider | Official capability/storage evidence | Current CCB behavior | Result | Follow-up |
| :--- | :--- | :--- | :--- | :--- |
| Codex | Current plugin layouts include immutable bundle authority plus writable marketplace/cache state observed by PR257. | CCB isolates `CODEX_HOME`; merged PR269 keeps `.tmp/plugins` content-addressed and converts `.tmp/marketplaces` plus `plugins/cache` to marker-owned local seeds. | Fixed and merged in R1 (`06e1a46a`, merge `aed27abf`). | Retain rollback, missing-source, upgrade, and two-agent regressions. |
| Claude | [Claude environment variables](https://code.claude.com/docs/en/env-vars) define read-only `CLAUDE_CODE_PLUGIN_SEED_DIR`; the misleadingly named `CLAUDE_CODE_PLUGIN_CACHE_DIR` selects the full writable plugins root. | PR269 added source seed plus per-agent cache isolation. This R11 candidate also bootstraps and path-rebases a new writable root before the first interactive scan, captures complete CLI help, preserves standard setting sources, and fail-closes opt-out/hard-role sessions onto empty/restricted roots. | Fixed in the candidate; real first-session fixture load passed on Claude Code 2.1.206 with its installed path inside the managed cache. | Keep the offline marketplace fixture and clean-home pane test as regression authority. |
| Gemini | [Gemini extension reference](https://geminicli.com/docs/extensions/reference/) loads extensions from `<home>/.gemini/extensions`; [Gemini configuration](https://geminicli.com/docs/get-started/configuration-v1/) says `GEMINI_CLI_HOME` replaces the user storage root. | The candidate seeds source `.gemini/extensions/` into a marker-owned agent-local directory and honors config/hard-role opt-out. | Fixed in the candidate; real `gemini extensions list` reported the fixture active from managed state. | Retain source-missing, opt-out, two-agent, and real CLI coverage. |
| Qwen | [Qwen extensions](https://qwenlm.github.io/qwen-code-docs/en/users/extension/introduction/) load global extensions under `.qwen/extensions`; [Qwen configuration](https://qwenlm.github.io/qwen-code-docs/en/users/configuration/settings/) defines `QWEN_HOME` as the global credentials/settings/skills/state root. | The candidate seeds source `extensions/` into the isolated `QWEN_HOME` and honors config/hard-role opt-out. | Source and launcher-chain fix complete; no Qwen executable is installed on this host. | Require a real first-session Qwen extension check before claiming provider-runtime qualification. |
| GitHub Copilot | [Copilot config-directory reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-config-dir-reference) says changing `COPILOT_HOME` hides existing config, extensions, installed plugins, permissions, and sessions; [plugin reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-plugin-reference) separates installed plugins, marketplace cache, and plugin data. | CCB assigns a fresh per-agent `COPILOT_HOME` without config/plugin projection. | Confirmed same class; directory-only copy would be incomplete. | Design a config-aware seed plus local plugin-data/cache boundary. |
| Droid | [Factory plugin documentation](https://docs.factory.ai/cli/configuration/plugins) stores user-scoped plugins and marketplace settings under `~/.factory/`. | The candidate copies only the plugin tree into agent-local `FACTORY_HOME`, rebases registry paths, and marker-merges only `enabledPlugins`; sessions, auth, and unrelated settings are not copied. | Fixed in the candidate; real `droid plugin list` loaded the local projection and source checksums stayed unchanged. | Retain malformed-registry fail-closed and local-path tests. |
| OpenCode | [OpenCode config](https://opencode.ai/docs/config/) merges global config before `OPENCODE_CONFIG`; [OpenCode plugins](https://opencode.ai/docs/plugins/) loads global plugins from `~/.config/opencode/plugins/` and npm cache from `~/.cache/opencode/node_modules/`. | CCB supplies a generated `OPENCODE_CONFIG` but does not replace `HOME` or XDG roots. | Not the same missing-plugin defect. Global mutable cache sharing is a separate isolation/performance risk. | Keep out of R1/R2; audit shared cache only if failures are observed. |
| Kimi | [Kimi skills](https://moonshotai.github.io/kimi-cli/en/customization/skills.html) supports normal discovery plus explicit `--skills-dir`. | CCB does not replace user `HOME` and explicitly appends project, user, inherited, role, and overlay skill roots. | No same skill-inheritance defect under the current launcher. Plugin-specific behavior still needs a separate capability test. | No immediate fix. |
| Pi | [Pi extensions](https://github.com/earendil-works/pi-mono/blob/main/packages/coding-agent/docs/extensions.md) defines global auto-discovery under `~/.pi/agent/extensions/`; [Pi packages](https://github.com/earendil-works/pi-mono/blob/main/packages/coding-agent/docs/packages.md) stores npm and Git installs below the agent directory; `PI_CODING_AGENT_DIR` relocates that directory. | The current candidate refreshes `models.json`, rewrites `settings.json` package and extension references, copies OpenPI's companion setup file, and stores immutable npm, Git, local-package, and direct-extension authority once in the user-scoped CCB provider cache. Agent auth, sessions, logs, writable cache, and runtime data remain private. | Fixed in the candidate. Two-Agent sharing, update switching, opt-out, and marker-ownership tests passed; a fresh managed home on Pi 0.84.4 loaded the projected extension flag and all three small configuration files through the real CCB materialization entrypoint. | Retain old content-addressed snapshots until a separately accepted cross-project retirement rule can prove no live Agent still references them; do not delete them during restart. |
| Grok, Cursor, Kiro, MiMo, Z.ai, AGY, Crush | No reviewed official source in this audit proves a stable user plugin registry and relocation/seed contract for the CCB-managed version. | Several launchers isolate a home or provider state and inject CCB skills, but storage semantics differ. | Unproven, not cleared. | Require official path semantics plus one installed-extension runtime repro before implementation. |

## Cross-Cutting Ownership Risk

The plugin gap is not the only destructive projection surface. Current helpers
for packaged inherited skills and some Claude/Droid trees still use
`allow_unmarked_replace=True`. That flag can replace an unmarked directory when
the source appears or remove it when inheritance is disabled/source is absent.
This is the same ownership-policy smell, but it is not silently broadened into
R1/R2 because those paths have different compatibility and migration contracts.

R12 owns that later slice. Its inventory covers packaged Kimi skills, Claude
skills/commands, and Droid skills; Decision 007 removes the bypass, validates
the generic marker schema, permits only exact-source legacy symlink adoption,
and preserves every ordinary unmarked directory and foreign marker.

R12 is verified by the atomic commit selected with `Repair-Slice: R12`; all
remaining production `allow_unmarked_replace=True` call sites are removed.

## Ordered Follow-Up

1. Keep merged R1/R2 behavior covered while landing the Claude first-session
   hardening with the R11 provider fixes.
2. Qualify Gemini and Droid against real CLIs and keep Qwen explicitly
   source-qualified until its executable is available.
3. Defer Copilot until its mixed config/auth/plugin schema and local writable
   data boundary are frozen; do not copy its whole config file speculatively.
4. Audit remaining isolated-home providers only from official capability and
   runtime evidence; absence of a local test installation is not proof that a
   provider has no extension problem.
5. Run the generic `allow_unmarked_replace` ownership-hardening slice after the
   provider-specific compatibility paths are documented.

## Acceptance Evidence For A Provider Follow-Up

- A source-home installed extension is visible in the first managed session.
- Inheritance-disabled startup does not expose it.
- Two agents load it while writable state remains different and source files do
  not change.
- Missing source and malformed/foreign marker cases preserve local data.
- Source update, rollback, restart, WSL/Windows path handling where applicable,
  and cleanup are covered.
- The provider-specific isolation and storage contracts are updated in the same
  patch.
