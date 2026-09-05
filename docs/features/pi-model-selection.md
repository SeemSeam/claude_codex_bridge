# Pi Model Selection in CCB Config UI

## Feature Overview

CCB Config UI supports model selection for a pane whose CCB provider is `pi`.
Pi accepts qualified model selection with:

```text
pi --model <provider>/<model>
```

Pi also stores custom provider and model definitions in its local
`models.json`. This feature exposes the safe subset of that catalog in the
existing Config UI model selector, persists the selected qualified identifier
in CCB's existing `model` field, and compiles it to Pi's `--model` startup flag.

Status: model selection and Codex/Pi thinking support are implemented and
verified.

## Codex And Pi Thinking Extension

The extension is limited to Codex and Pi. Astra (`gpt-6-astra`, qualified with
the configured provider for Pi) must expose `low`, `medium`, `high`, `xhigh`,
and `max` in that order. Other providers retain their existing behavior.

- Codex accepts Astra cache entries and retains cache-first discovery. Its
  backend and frontend fallbacks include Astra with the known five-level
  catalog. A usable cache is never supplemented with invented entries.
- Pi reads model-specific `reasoning` and `thinkingLevelMap` metadata and
  compiles the existing `thinking` field to `--thinking <level>`.
- Pi's custom Astra entry requires an explicit mapping for `xhigh` and `max`;
  Pi 0.84.4 otherwise clamps these levels to `high`. No managed runtime is
  restarted by this configuration/code change.
- Existing Codex cache metadata stays authoritative: Astra levels are ordered
  and limited to the five supported values, without expanding a restricted
  cache entry. Other Codex models retain their existing levels, including
  `ultra` where configured. Cache discovery and service caching are unchanged.
- Verification covers catalogs, parameter validation, TOML round trips, the
  Pi launch command, and the Config UI selector. Actual API requests are
  outside this verification scope.

## Goals And Scope

In scope:

- Discover Pi models from the source user's effective Pi model catalog.
- Display each item as the exact qualified identifier `provider/model`.
- Save the identifier through the existing static agent `model` setting.
- Compile Pi models to `--model provider/model` without `--provider`.
- Read Pi thinking capabilities and compile the existing `thinking` field to
  `--thinking <level>` through the shared shortcut module.
- Preserve existing V2 and V3 config rendering and validation behavior.
- Fail closed when the catalog is missing or malformed and never expose secrets.

Out of scope:

- Editing `models.json`, provider URLs, API keys, headers, or authentication.
- Remote API probes or claims that a configured endpoint is currently healthy.
- Adding a separate Pi-provider field to the CCB schema.
- Changing Pi headless job execution, whose command currently does not consume
  an `AgentSpec`; static CCB pane launch is the target of this feature.

## Confirmed Upstream Contract

The design was checked against local Pi `0.84.4` and current Pi documentation.
Both state that `--model` accepts `provider/id`. Pi first interprets the prefix
before `/` as a provider when it matches a registered provider, so a separate
`--provider` argument is neither required nor desired for this feature.

Pi's model configuration defaults to `<source-home>/.pi/agent/models.json`.
CCB already copies this file into each managed Pi home when provider config
inheritance is enabled and starts Pi with `PI_CODING_AGENT_DIR` pointing at that
managed directory.

## User Flow

1. The user selects `pi` as the pane provider.
2. The model selector remains enabled and contains `inherit` followed by the
   qualified models discovered from Pi's catalog.
3. Each option uses the same exact string for value and visible label, for
   example `local/gemini-3.8-flash-high` or `pay/gpt-5.6-terra`.
4. Choosing an option writes the qualified string to the pane's agent overlay.
5. Preview and save render it as:

   ```toml
   [agents.<agent>]
   model = "pay/gpt-5.6-terra"
   ```

6. On the normal CCB restart/apply path, the Pi pane starts with:

   ```text
   pi --model pay/gpt-5.6-terra
   ```

7. Choosing `inherit` removes the agent-local `model` override and leaves Pi to
   resolve its configured default.

## Catalog Discovery

### Source resolution

The Pi-specific catalog reader in `lib/cli/services/config_ui.py` uses this
normal source:

```text
current_provider_source_home() / ".pi" / "agent" / "models.json"
```

It uses `current_provider_source_home()` rather than raw `HOME`, because CCB can
run inside a managed environment where `HOME` points at an agent-specific
private directory. For testability, the public capability builder accepts an
optional explicit Pi models path, matching the existing Codex cache injection
pattern.

The implementation reads the source catalog only. Scanning managed agent homes
would make stale per-agent snapshots compete with the source of truth and could
combine different catalogs into one misleading list.

### Parsing and normalization

Expected input shape:

```json
{
  "providers": {
    "local": {
      "models": [
        { "id": "gemini-3.8-flash-high" }
      ]
    }
  }
}
```

Parsing rules:

- The root must be an object and `providers` must be an object.
- A provider key must trim to a non-empty string and must not contain `/`.
- `models` must be an array; non-object entries are ignored.
- A model `id` must trim to a non-empty string.
- Emit only `{id, label, reasoning_levels, default_reasoning_level,
  context_window_max_tokens}` using the existing capability model shape.
- Set both `id` and `label` to `provider/model-id`.
- Preserve provider insertion order and model array order.
- Deduplicate by the final qualified identifier, keeping the first occurrence.
- Do not reject `/` inside a model ID. Pi splits only the first slash, and some
  upstream model IDs legitimately contain further slashes.
- Models without `reasoning: true` expose no thinking selector.
- Read `thinkingLevelMap` using Pi 0.84.4 semantics: `null` disables a level,
  a string enables it, omitted standard levels use Pi defaults, and omitted
  `xhigh`/`max` levels remain unsupported. Malformed mappings yield no levels.
- Return only supported level names in Pi order, never the provider's mapping
  values. CCB does not write or normalize the source catalog.

Security rule: never copy arbitrary fields from `models.json`. In particular,
`apiKey`, `baseUrl`, `headers`, command substitutions, and provider display
metadata must not cross the `/api/capabilities` boundary. Error messages must
not include file contents.

### Failure behavior

Missing, unreadable, malformed, or structurally invalid files produce an empty
Pi model list. Capability generation and the rest of Config UI remain usable.
The provider still reports model-shortcut support so an already configured
qualified value can be preserved, but the UI does not invent catalog entries.

The capability record for Pi becomes:

```json
{
  "id": "pi",
  "model_shortcut": true,
  "api_shortcut": false,
  "model_source": "pi_models_json",
  "models": [
    {
      "id": "local/gemini-3.8-flash-high",
      "label": "local/gemini-3.8-flash-high",
      "reasoning_levels": [],
      "default_reasoning_level": null,
      "context_window_max_tokens": null
    }
  ],
  "custom_model": false,
  "static_thinking": true
}
```

No capability schema-version bump is required because this uses existing fields
with existing meanings.

## Frontend Interaction And State

The existing model `<select>` is retained. This is a configuration tool, so a
dense native selector is preferable to a new modal or custom picker.

- Online catalog available: show `inherit` followed by all discovered qualified
  models. Pi does not show the generic `Custom model...` option.
- Empty/unavailable catalog: show only `inherit` and a concise note that no
  configured Pi models were found. Keep the selector enabled so an existing
  value can be retained, but offer no new model choice.
- Existing value missing from the current catalog: preserve it as a selected
  option. Do not silently reset it to `inherit` when capabilities refresh.
- Provider change away from Pi: retain the current existing behavior that
  clears model and thinking overlays to avoid cross-provider inheritance.
- Thinking selector: enabled for a catalog model with non-empty reasoning
  levels. `inherit` clears the override. Selecting a new model retains a
  supported level and clears an incompatible one, matching existing behavior.
- Fallback/offline page data: mark Pi as model-shortcut capable with an empty
  catalog and custom entry disabled. The static HTML must not embed
  machine-local model names.

The source note uses a localized, user-facing "Pi local models" label rather
than exposing an internal token or absolute path. Empty text remains adjacent
to the field and is accessible as normal text; color alone does not convey
state.

## Startup And Validation Design

Pi is registered in the shared maps in `lib/provider_model_shortcuts.py`:

```python
_PROVIDER_MODEL_FLAGS["pi"] = ("--model",)
_PROVIDER_MODEL_STARTUP_FLAGS["pi"] = "--model"
```

This single shared registration intentionally drives all existing contracts:

- `supported_provider_model_shortcuts()` marks Pi as writable.
- `AgentSpec` compiles `model` into `("--model", qualified_id)`.
- duplicate `--model` in explicit `startup_args` is rejected.
- V3 workflow/default validation uses the same compiler.
- config rendering strips the generated args and preserves the structured
  `model` field.
- `provider_backends/pi/launcher.py` already appends `spec.startup_args`, so no
  Pi-launcher special case is needed.

Thinking follows the existing Claude/Codex pattern in
`lib/provider_thinking_shortcuts.py`: Pi registers its CLI-level superset
(`off`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`), compiles to
`--thinking`, detects conflicting explicit flags, and strips generated flags
when rendering. `AgentSpec` and the launcher remain generic. The catalog
restricts the UI to each model's capabilities; Pi performs final native
model-specific validation. A structured `thinking` flag takes precedence over
Pi's model-pattern thinking suffix according to Pi's CLI contract.

The model string is treated as an opaque non-empty value by the shared compiler.
The Pi dropdown emits only qualified values from the catalog. Manually authored
TOML remains compatible and is ultimately validated by Pi at startup; Config UI
does not add an arbitrary model-entry path for Pi.

## Data And Persistence

There is no new persisted field or migration. Selection uses existing fields:

```toml
[agents.agent_name]
model = "provider/model-id"
thinking = "high"
```

The Pi provider prefix belongs inside this string. CCB must not split it into a
new field, and must not persist the generated `--model` or `--thinking` arguments.

For a custom Astra model to expose exactly five levels, its Pi-native model
entry needs `reasoning: true` and this metadata:

```json
"thinkingLevelMap": {
  "off": null,
  "minimal": null,
  "low": "low",
  "medium": "medium",
  "high": "high",
  "xhigh": "xhigh",
  "max": "max"
}
```

This is provider configuration supplied by its owner, not a new CCB setting or
a Config UI write operation. Existing one-way inheritance copies it into a
managed Pi home on normal launch. Refresh the Config UI service to rebuild its
cached capabilities after changing the catalog.

The value may contain `/`, `:`, dots, or hyphens because it is TOML string data
and Pi owns model-pattern interpretation. CCB's existing TOML serializer handles
escaping.

## Compatibility And Cross-Surface Impact

Adding Pi to the shared shortcut set also changes project/mobile provider
capability records from unsupported to `restart_required`. This matches the
common capability contract and is covered by shared-contract assertions.

The mobile gateway reuses Config UI catalogs and receives the same sanitized Pi
model list without a new endpoint. This is a compatible consequence and does
not require a mobile UI redesign.

Existing configs using explicit Pi `startup_args = ["--model", "..."]` remain
valid when no structured `model` is present. Combining both forms becomes a
visible validation error, matching Codex, Claude, Gemini, and OpenCode.

## Verification Coverage

### Catalog and security tests

`test/test_config_ui.py` covers:

- multiple providers and models become ordered `provider/model` rows;
- duplicate qualified IDs keep the first entry;
- model IDs containing an additional slash are preserved;
- empty providers, missing files, malformed JSON, and wrong field types yield
  an empty list without failing the capability response;
- payload serialization contains no API key, URL, header, or source path;
- Pi advertises `model_shortcut = true`, `custom_model = false`,
  `static_thinking = true`, and `model_source = "pi_models_json"`;
- the embedded fallback capability marks Pi writable but embeds no local models.

### Config compiler and rendering tests

`test/test_v2_config_loader.py` and the shared V3 cases prove:

- `model = "pay/gpt-5.6-terra"` compiles to
  `("--model", "pay/gpt-5.6-terra")`;
- unrelated startup arguments follow the generated model prefix;
- structured model plus explicit `--model` is rejected;
- rendered TOML retains the qualified `model` and omits generated startup args.

### Pi launch test

A focused case beside the existing native Pi launcher tests in
`test/test_v2_runtime_launch.py` proves the quoted command contains:

```text
pi --model pay/gpt-5.6-terra
```

and does not contain `--provider`.

### Acceptance check

Acceptance used a temporary source home with a non-secret fixture catalog to
start Config UI, select Pi and a qualified model, render the draft, and verify
the saved config. The launch test builds the Pi start command with a stub
`PI_START_CMD`; the automated suite does not call a live model API.

## Delivered Slices

1. Shared launch contract: register Pi's `--model` shortcut and add compiler,
   conflict, round-trip, and launch tests.
2. Safe discovery: add the Pi catalog reader and capability payload tests.
3. UI states: update fallback capability and localized source/empty messaging;
   verify current selections survive empty/changed catalogs.
4. Contract documentation: `docs/ccb-config-layout-contract.md` lists Pi as a
   supported mapping, and this document records the delivered behavior and
   verification results.

## Acceptance Criteria

- Selecting `pi` no longer shows the unsupported-shortcut message.
- The dropdown shows exactly the qualified models from the effective source
  `models.json`, including `local/gemini-3.8-flash-high` and
  `pay/gpt-5.6-terra` for the reported local fixture.
- Saving persists the exact qualified identifier in `model`.
- Normal Pi pane launch receives exactly one `--model <qualified-id>` pair and
  no generated `--provider` flag.
- Invalid/unavailable catalogs do not break Config UI or erase an existing
  configured model.
- `/api/capabilities` exposes no Pi credentials, endpoints, headers, or paths.
- Other providers retain their existing behavior; Codex cache precedence and
  non-Astra levels remain covered by regression tests.

## Verification Status

- Design discovery: complete.
- Local Pi CLI contract: verified against version `0.84.4`.
- Current upstream documentation: verified through Context7 and installed docs.
- Implementation: complete in the shared model/thinking shortcut compilers,
  Config UI capability service, and existing browser selector.
- Catalog acceptance: Codex fallback and the local Pi `pay/gpt-6-astra` entry
  both expose `low`, `medium`, `high`, `xhigh`, `max`. Pi 0.84.4's native
  `getSupportedThinkingLevels` and `clampThinkingLevel` confirm that the
  explicit mapping preserves `max` rather than lowering it to `high`.
- Automated and shared-contract verification on 2026-09-06:
  `env NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost
  python3 -m pytest -q test/test_config_ui.py
  test/test_provider_thinking_shortcuts.py test/test_v2_config_loader.py
  test/test_v2_runtime_launch.py test/test_mobile_gateway_service.py
  test/test_v3_config_loader.py test/test_provider_control_settings.py` passes
  with 522 tests. Localhost bypass is limited to the test process because the
  host proxy otherwise returns HTTP 503 for local test servers.
- Browser verification: Playwright with installed Chrome, using a temporary
  project and the real Config UI HTTP handlers, selected all five levels for
  both providers, saved/reloaded `max`, checked the 390px mobile control and
  Codex fallback after an API failure, and reported no JavaScript errors.
  The temporary server was stopped after verification.
- Static verification: `python3 -m py_compile` for all modified Python source
  and test files, plus `git diff --check`, passes.

## Known Limitations And Follow-Ups

- An agent with `provider_profile.inherit_config = false` will not receive the
  source `models.json`; a globally discovered custom model may therefore fail
  when that isolated agent starts. Agent-profile-aware catalog filtering or a
  save-time warning can be added later.
- The catalog proves local configuration, not provider authentication or remote
  endpoint availability. Pi remains the runtime authority and reports launch
  errors normally.
- V3 workflow policy and loop role profiles retain their existing
  `low`/`medium`/`high` validation. This change targets static agent overlays
  in Config UI and does not expand workflow-policy enums.
- Headless Pi execution currently builds a job command without agent model
  policy. Propagating static model selection into that path requires a separate
  execution-contract change.
