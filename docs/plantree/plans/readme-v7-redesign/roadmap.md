# README v7 Redesign Roadmap

Date: 2026-06-12

## Done

- Inventoried current README structure, version marker, and public media assets.
- Confirmed current source version marker is `7.0.2`.
- Identified current README drift:
  - Chinese update section still says `CCB v6`.
  - Chinese README lacks the English README's newer `[windows]` migration
    explanation.
  - tmux onboarding is limited to copy/paste guidance.
  - v7 sidebar/window operation is present mainly in changelog and contracts,
    not in the user journey.
- Created the `docs/plantree/` planning entrypoint and registered this plan.
- Promoted the user's README redesign idea from the ideas inbox into this plan.
- Recorded maintainer decisions for README scope, audience, media defaults,
  changelog placement, v7 update examples, tmux scope, and conservative
  platform wording in
  [decisions/002-readme-publication-defaults.md](decisions/002-readme-publication-defaults.md).
- Added the opening multi-agent necessity and approach-comparison plan in
  [topics/multi-agent-positioning-and-comparison.md](topics/multi-agent-positioning-and-comparison.md).
- Corrected the Hive comparison target to OpenHive at
  `https://github.com/aden-hive/hive`.
- Added maintainer-specified single-agent limitations to the opening narrative:
  role mixing, context focus loss, complexity ceiling, cost pressure,
  management/tool concentration, and serial waiting.
- Added source-backed research notes for Claude Code native multi-agent and
  OpenHive in
  [topics/multi-agent-research-notes.md](topics/multi-agent-research-notes.md).
- Expanded the multi-agent comparison into a two-layer README design: a visible
  decision table plus folded detail table.
- Added the operation demo video/audio plan, including screenshots, short clips,
  Bilibili-hosted walkthroughs, and a subtitles-first audio recommendation.
- Generated draft `ccb_test2` screenshot assets under `assets/readme_v7/` and
  mapped them to README hero/detail uses.
- Surveyed high-star adjacent README structures from OpenHands, AutoGen,
  CrewAI, OpenHive, and Claude Squad, then translated the useful patterns into
  a CCB-specific implementation blueprint.
- Verified CCB-managed tmux defaults in `ccb_test2`: `Ctrl-b` prefix,
  `mouse on`, `set-clipboard on`, and fallback bindings for pane focus,
  window switching, copy mode, and detach.
- Added the concrete
  [topics/readme-implementation-blueprint.md](topics/readme-implementation-blueprint.md)
  covering section order, visible/folded split, screenshot captions, tmux
  guidance, config examples, and `ccb-config` skill copy.
- Recorded final maintainer decisions: regenerate real terminal screenshots for
  public README media, use npm-first install plus `ccb update` wording, and document
  native Windows support as v5-only with newer versions unsupported natively.
- Captured real dark terminal screenshots from `ccb_test2` and generated
  annotated Chinese/English README hero images under `assets/readme_v7/`.
- Rewrote `README_zh.md` and `README.md` around the agreed v7 task-first
  structure, including the opening multi-agent comparison, v7 UI tour, tmux
  onboarding, config examples, `ccb-config` workflow, platform notes, credits,
  and changelog link.
- Verified README local links and image paths.
- Removed the folded sidebar/Codex/Claude local detail screenshot blocks and
  deleted their unused crop assets.
- Kept Quick Start but changed the config starting point to a v7 `[windows]`
  topology example, then added config-capability tables and explicit
  `ccb-config` discussion guidance.
- Folded long config examples and `ccb-config` write-flow details to preserve a
  lighter first-read path.
- Simplified the opening multi-agent meaning/comparison copy and folded the
  detailed tradeoff tables to reduce first-read weight.
- Captured maintainer feedback on 2026-06-12 that the current homepage remains
  too text-heavy, visually plain, and unfocused for a GitHub first screen.
- Added the homepage-specific polish plan in
  [topics/homepage-showcase-polish.md](topics/homepage-showcase-polish.md),
  using the new `assets/ccb-promo.png` image and external README structure
  patterns as design inputs.
- Received reviewer1 homepage polish review and recorded it in
  [history/reviewer1-homepage-polish-2026-06-12.md](history/reviewer1-homepage-polish-2026-06-12.md).
- Resolved the blocking hero asset ambiguity with
  [decisions/004-homepage-hero-asset-strategy.md](decisions/004-homepage-hero-asset-strategy.md):
  public READMEs should use canonical language-specific hero images under
  `assets/readme_v7/`; `assets/ccb-promo.png` remains promotional/reference
  material unless a later decision changes that.
- Added public README links to the user and developer manuals, and strengthened
  the `ccb_self` positioning as CCB's built-in self-understanding expert for
  usage, config design, diagnostics, recovery, and workflow repair.
- Changed the recommended first-install path from GitHub release packages to
  npm package install with `npm install -g @seemseam/ccb`; release packages now
  remain a fallback when npm is unavailable.
- Clarified that subsequent updates use `ccb update`, not another npm install
  command.
- Stabilized the README homepage design direction in
  [decisions/005-readme-design-non-drift-contract.md](decisions/005-readme-design-non-drift-contract.md)
  so future edits do not drift back to rationale-first, old install-default, or
  screenshot-late layouts.
- Generated canonical first-screen hero assets:
  `assets/readme_v7/ccb-hero-zh.png` and
  `assets/readme_v7/ccb-hero-en.png`.
- Rewrote the top of `README_zh.md` and `README.md` into the stable
  product-first order: hero, three values, npm new install plus `ccb update`,
  UI tour, `CCB 是什么` / `What Is CCB`, then rationale and comparison.
- Replaced the homepage hero pair with the newer promo-style CCB composition,
  including Chinese and English variants under `assets/readme_v7/`, and kept
  `assets/ccb-promo.png` as source/reference material rather than the direct
  README path.
- Added a compact first-screen supported-CLI logo/badge strip for Codex,
  Claude, Gemini, Kimi, OpenCode, Antigravity, and Droid.
- Reprioritized the public support strip across all localized READMEs to show
  Codex, Claude, Pi, OMP, Kimi, and OpenCode first. Removed `zai`, the
  unofficial `deepseek` Deep Code CLI, and the official `dsh` DeepSeek Harness
  from current support promotion while retaining all provider implementation
  code and historical release notes.
- Follow-up release review blocked npm-first publication until the source tree
  restored the `@seemseam/ccb` npm package surface.
- Earlier chose `7.4.4` as the next patch version instead of reusing the
  existing `v7.4.3` tag; the combined release candidate now targets `v7.5.0`.
- Added the npm package manifest, npm CLI runner wrappers, and tag-triggered
  Trusted Publishing workflow needed to support the README's npm-first install
  guidance.
- Aligned npm license metadata with the repository license using SPDX
  `AGPL-3.0-only`.

## In Progress

- Validate the restored npm package surface with `npm pack --dry-run`, version
  synchronization checks, and release workflow review.
- Send the updated release candidate back through reviewer/archi review.
- Preserve the promo-style canonical hero pair and supported-CLI strip during
  release-surface validation edits.

## Next

- Optionally tighten release asset naming after the release packaging path is
  confirmed against the final public assets.
- Later pass: resume deferred demo/video design.

## Deferred

- Adding a separate documentation website. This plan keeps the immediate target
  to GitHub README files and repo-local assets.
- Creating raw recording archives in git. Commit optimized public media only.

## Phase Gates

Phase 1 is complete when:

- Remaining open questions are verified or explicitly accepted as follow-up
  risks.
- The fold/visible section split is agreed.
- The sanitized demo project scenario is agreed.

Phase 2 is complete when:

- Required media assets exist in a stable folder, likely `assets/readme_v7/`.
- Each asset has a documented scene purpose, alt text, and privacy check.
- Animated assets have acceptable file size and a static fallback.

Phase 3 is complete when:

- Both public READMEs teach the same v7 workflow.
- v7 windows/sidebar configuration is no longer buried only in the changelog.
- A non-tmux user can start, switch focus/windows, copy/scroll/paste, detach or
  recover, ask another agent, and stop/rebuild from README guidance.

Phase 4 is complete when:

- README links and asset paths resolve.
- Public examples avoid secrets and local-only paths.
- Config examples match the current config contract.
- Any command examples that require current CLI behavior have been smoke-tested
  or explicitly marked as examples.
