# README Design Non-Drift Contract

Date: 2026-06-12
Last amended: 2026-09-05

## Context

The README v7 redesign has accumulated several design corrections after the
first implementation: the homepage was still too text-heavy, the hero asset
strategy needed language-specific canonical images, new installs should be
npm-first, and the README must visibly explain `ccb_self` as CCB's built-in
self-understanding expert. These rules should not drift back into older
release-first, rationale-first, or screenshot-late layouts.

This decision stabilizes the public README design direction. It supersedes
older release-first wording in earlier planning notes and should be read before
editing `README_zh.md` or `README.md`.

Related files:

- [../topics/homepage-showcase-polish.md](../topics/homepage-showcase-polish.md)
- [004-homepage-hero-asset-strategy.md](004-homepage-hero-asset-strategy.md)
- [../history/reviewer1-homepage-polish-2026-06-12.md](../history/reviewer1-homepage-polish-2026-06-12.md)

## Decision

The public README first-read path must stay product-first:

1. Product name and one-line positioning.
2. Quiet badges, preferably three and never more than four visible badges.
3. Language switch plus short navigation, including user and developer docs.
4. One dominant canonical hero image from `assets/readme_v7/`, using the newer
   promo-style CCB composition.
5. Three compact value points.
6. A compact supported-CLI logo/badge strip.
7. npm-first Quick Start.
8. Interface tour.
9. Multi-agent rationale and solution comparison.
10. Daily operation, tmux basics, configuration, collaboration, install details,
   FAQ, credits, and release notes.

Stable homepage claims:

- CCB is a visible, controllable multi-agent CLI workspace.
- CCB shows real provider CLIs in real terminal panes.
- CCB can mix Codex, Claude, Pi, OMP, Kimi, OpenCode, and related providers in
  one project-owned tmux workspace.
- The first-screen CLI support strip prioritizes Codex, Claude, Pi, and OMP,
  followed by Kimi, OpenCode, and the remaining publicly supported providers.
- The public support strip does not advertise the unofficial `deepseek` Deep
  Code CLI or `zai`. The official DeepSeek Harness Developer Preview provider, `dsh`, is also
  excluded from current public promotion.
- `ccb_self` is CCB's built-in self-understanding expert for usage guidance,
  active layout explanation, config design, runtime diagnostics, recovery, and
  workflow repair.
- New installs should recommend `npm install -g @seemseam/ccb@latest`.
  As of 2026-07-22, npm-managed updates remain owned by npm; release-package
  and source installs use `ccb update`. This supersedes the earlier single
  post-install updater rule; see
  [install-update-stability decision 001](../../install-update-stability/decisions/001-npm-owns-vendored-payload.md).

## Must Preserve

- Localized parity: `README.md` and the files under `README/` must preserve the
  same support-strip priority, section order, asset roles, install path, and
  `ccb_self` positioning.
- Canonical hero assets: public README hero references should use
  `assets/readme_v7/ccb-hero-zh.png` and
  `assets/readme_v7/ccb-hero-en.png`; these should be derived from the newer
  promo-style CCB image, not the older annotated screenshot copies.
- `assets/ccb-promo.png` remains source/reference material for the promo-style
  composition; public READMEs still use the canonical `assets/readme_v7/`
  language-specific files.
- The supported-CLI strip must stay near the top and remain structurally
  parallel in `README.md` and `README_zh.md`.
- User and developer manual links must remain discoverable near the top:
  `docs/manuals/user-guide/` and `docs/manuals/developer-guide/`.
- The first screen should show CCB before asking readers to process long
  rationale, comparison, config, changelog, or troubleshooting content.
- Advanced explanation should be folded or moved below the first action path
  when it competes with the hero, three values, or Quick Start.

## Must Avoid

- Do not put "Why multi agents" or the long approach comparison before the hero
  and Quick Start.
- Do not re-expand the header to seven badges or let badges dominate the first
  screen.
- Do not return to release-first installation wording as the recommended
  default.
- Do not use `seemseam@ccb` as an npm install command; the verified package
  name is `@seemseam/ccb`.
- Do not tell npm-managed users that `ccb update` mutates the vendored package
  payload. It prints the npm-owned update command; release/source installs keep
  the transactional updater.
- Do not directly use `assets/ccb-promo.png` as the public README hero without
  generating/updating the language-specific canonical hero pair.
- Do not replace the promo-style hero with the older
  `ccb-test2-terminal-annotated*.png` screenshots unless a new decision says
  why the visual direction changed.
- Do not drop the supported-CLI logo/badge strip when editing the first-read
  path.
- Do not restore `deepseek`, `zai`, or `dsh` to current public promotion without
  a new maintainer decision; retaining implementation code or historical
  release notes does not make them publicly advertised providers.
- Do not duplicate multiple large screenshots before Quick Start.
- Do not let release notes, detailed config examples, or tmux reference tables
  dominate the homepage.

## Change Control

Any future patch that changes one of these surfaces must update this decision
or add a replacement decision in the same patch:

- first-screen section order;
- recommended install/update path;
- hero image policy or canonical asset names;
- supported-CLI first-screen strip;
- top navigation/manual links;
- `ccb_self` positioning;
- visible/folded split for rationale, comparison, config, tmux, or release
  notes.

If a future README patch intentionally violates this contract, the patch should
state why the old rule no longer applies and link the replacement decision.

## Drift Checks

Before landing README homepage changes, run or manually verify:

```bash
rg -n "release-first|Release first|Release 优先|seemseam@ccb|@seemseam/ccb@latest|New users should start from a release package|首次安装推荐使用 \\[GitHub Releases\\]" README.md README docs/plantree/plans/readme-v7-redesign/README.md docs/plantree/plans/readme-v7-redesign/roadmap.md docs/plantree/plans/readme-v7-redesign/topics
rg -n "@seemseam/ccb|docs/manuals/user-guide|docs/manuals/developer-guide|ccb_self" README.md README
git diff --check -- README.md README docs/plantree/plans/readme-v7-redesign
```

Expected result:

- no stale release-first default wording;
- npm package name is `@seemseam/ccb`;
- updates use `ccb update`;
- manual links remain present;
- `ccb_self` remains positioned as the built-in CCB expert;
- Markdown diff has no whitespace errors.
