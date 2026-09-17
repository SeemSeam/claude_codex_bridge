# Claude explicit API-key approval

Date: 2026-09-17
Status: implemented locally; not committed or published.

Managed trust preparation read ANTHROPIC_API_KEY only from managed settings.
Explicit profile/agent credentials deliberately travel through launcher env,
so their approval suffix could be missing regardless of inheritance flags.

Trust preparation now receives agent extra_env and reuses launcher explicit
environment collection and allowed ambient inheritance. Explicit credentials
take precedence, then managed settings credentials, then permitted ambient
credentials. An explicit token does not approve an unrelated ambient API key.
Only CCB-owned trust state is written. No changes to source Claude settings,
external login, API route selection, or protocol conversion.

Eight new materialization cases cover profile keys, agent override keys,
ambient keys and explicit tokens with inheritance both enabled and disabled.
They check approval suffixes, no stale-key approval, no complete key in trust,
and unchanged source directory. Existing inherited-settings approval remains
covered. Provider profiles/v2 launch/v3 loader: 364 passed; Claude launcher
environment suite: 16 passed. `git diff --check` passed.

This verifies the CCB projection defect. It does not prove that every request
to api.anthropic.com was caused by missing approval, or that an OpenAI-only
endpoint can serve Claude's API protocol. Existing agents must rematerialize
their managed home through the normal restart/remount path to receive the fix;
no live agent was restarted and no credentials were edited during repair.
