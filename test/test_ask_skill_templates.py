from __future__ import annotations

from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_shell_ask_skill_templates_use_short_ask_command() -> None:
    for relative_path in (
        'inherit_skills/claude_skills/ask/SKILL.md',
        'inherit_skills/claude_skills/ask/RUNTIME.md',
        'inherit_skills/codex_skills/ask/SKILL.md',
        'inherit_skills/droid_skills/ask/SKILL.md',
        'inherit_skills/droid_skills/ask.md',
        'inherit_skills/gemini_skills/ask/SKILL.md',
        'inherit_skills/grok_skills/ask/SKILL.md',
        'inherit_skills/kimi_skills/ask/SKILL.md',
        'inherit_skills/qoder_skills/ask/SKILL.md',
        'inherit_skills/qodercn_skills/ask/SKILL.md',
        'inherit_skills/mimo_skills/ask.md',
        'inherit_skills/opencode_skills/ask.md',
        'inherit_skills/qwen_skills/ask.md',
        'inherit_skills/zai_skills/ask.md',
    ):
        text = (REPO_ROOT / relative_path).read_text(encoding='utf-8')
        normalized = re.sub(r'\s+', ' ', text)
        assert 'command ask ' in text
        assert 'command ccb ask' not in text
        assert 'canonical `ccb ask`' not in text
        assert 'compatibility alias' not in text
        assert 'forwarded verbatim' not in text
        assert '## Decision Card' in text
        assert 'Before every ask, decide:' in text
        assert 'Need delegation? If no, answer directly.' in text
        assert '2. Result intent:' in text
        assert '`--silence`: publish/execute task; success result not needed.' in text
        assert 'Failures,' in text and 'required next actions still surface.' in text
        assert '`--compact`: result wanted, but only distilled' in text
        assert '`+ --artifact-reply`: consultation/analysis/report where full text should' in text
        assert 'plain `ask`: short question or short handoff where inline text is enough.' in text
        assert '`--chain`: active CCB parent job + child result required to finish.' in text
        assert 'Combine with `--compact` or `--artifact-reply` as needed.' in text
        assert '3. Request fidelity:' in text
        assert 'Prefer repo paths when the target can read files directly.' in text
        assert 'Avoid `--silence --artifact-reply`' in text
        assert 'diagnostics-only commands' in text
        assert 'explicit debugging requests' in text
        assert 'do not run `ask get` / `pend`' in text
        assert '`ping` / `watch`' in text
        assert 'Do not manually append output-policy text' in text
        assert 'Artifact flags are orthogonal to `--chain`, `--silence`, and `--compact`.' in text
        assert 'Automatic spill for text over 4 KiB is a fallback' in text
        assert 'In `A --silence -> B`, B still runs an active job.' in text
        assert 'In task chains, each needed-result hop uses `--chain`' in text
        assert 'Finish an inbound CCB task in its current turn.' in normalized
        assert 'If the original caller is a registered CCB agent' in normalized
        assert 'routes that turn\'s terminal result through the existing lineage' in normalized
        assert 'do not open a new `ask` to report completion' in normalized
        assert 'Direct CLI submitters read terminal results from control output' in normalized
        assert 'such as `watch` or `trace`.' in normalized
        assert 'If the current task is a CCB result-chain continuation' in text
        assert 'Do not use `ask`, `--chain`, or' in text
        assert 'continuation completion upstream' in text
        assert '`--silence` is not an active-job correction channel.' in text
        assert '`ccb followup <active_job_id> --message "<correction>"`' in text
        assert 'only `injected` is success' in text
        assert 'cancel and resubmit the complete' in text
        assert 'A `completed` CCB job means provider execution ended normally' in text
        assert 'command ask "$TARGET"' in text
        assert 'command ask --chain --artifact-reply "$TARGET"' in text
        legacy_chain_flag = '--' + 'call' + 'back'
        assert legacy_chain_flag not in text
        assert '[FLAGS...]' not in text
        assert re.search(r'[\u4e00-\u9fff]', text) is None


def test_powershell_ask_skill_template_uses_short_ask_command() -> None:
    text = (REPO_ROOT / 'inherit_skills/claude_skills/ask/SKILL.md.powershell').read_text(encoding='utf-8')
    normalized = re.sub(r'\s+', ' ', text)

    assert 'FilePath "ask"' in text
    assert 'ccb ask' not in text
    assert 'compatibility alias' not in text
    assert 'forwarded verbatim' not in text
    assert '## Decision Card' in text
    assert 'Before every ask, decide:' in text
    assert 'Need delegation? If no, answer directly.' in text
    assert '2. Result intent:' in text
    assert '`--silence`: publish/execute task; success result not needed.' in text
    assert 'Failures,' in text and 'required next actions still surface.' in text
    assert '`--compact`: result wanted, but only distilled' in text
    assert '`+ --artifact-reply`: consultation/analysis/report where full text should' in text
    assert 'plain `ask`: short question or short handoff where inline text is enough.' in text
    assert '`--chain`: active CCB parent job + child result required to finish.' in text
    assert 'Combine with `--compact` or `--artifact-reply` as needed.' in text
    assert '3. Request fidelity:' in text
    assert 'Prefer repo paths when the target can read files directly.' in text
    assert 'Avoid `--silence --artifact-reply`' in text
    assert 'diagnostics-only commands' in text
    assert 'explicit debugging requests' in text
    assert 'do not run `ask get` / `pend`' in text
    assert '`ping` / `watch`' in text
    assert 'Do not manually append output-policy text' in text
    assert 'Artifact flags are orthogonal to `--chain`, `--silence`, and `--compact`.' in text
    assert 'Automatic spill for text over 4 KiB is a fallback' in text
    assert 'In `A --silence -> B`, B still runs an active job.' in text
    assert 'In task chains, each needed-result hop uses `--chain`' in text
    assert 'Finish an inbound CCB task in its current turn.' in normalized
    assert 'If the original caller is a registered CCB agent' in normalized
    assert 'routes that turn\'s terminal result through the existing lineage' in normalized
    assert 'do not open a new `ask` to report completion' in normalized
    assert 'Direct CLI submitters read terminal results from control output' in normalized
    assert 'such as `watch` or `trace`.' in normalized
    assert 'If the current task is a CCB result-chain continuation' in text
    assert 'Do not use `ask`, `--chain`, or' in text
    assert 'continuation completion upstream' in text
    assert '`--silence` is not an active-job correction channel.' in text
    assert '`ccb followup <active_job_id> --message "<correction>"`' in text
    assert 'only `injected` is success' in text
    assert 'cancel and resubmit the complete' in text
    assert 'A `completed` CCB job means provider execution ended normally' in text
    assert '@("--chain", "--artifact-reply", "$TARGET")' in text
    legacy_chain_flag = '--' + 'call' + 'back'
    assert legacy_chain_flag not in text
    assert re.search(r'[\u4e00-\u9fff]', text) is None


def test_kimi_ask_skill_projects_structured_receipt_contract_only_to_kimi() -> None:
    kimi_text = (REPO_ROOT / 'inherit_skills/kimi_skills/ask/SKILL.md').read_text(encoding='utf-8')
    codex_text = (REPO_ROOT / 'inherit_skills/codex_skills/ask/SKILL.md').read_text(encoding='utf-8')

    assert '## Kimi Receipt Contract' in kimi_text
    for field in (
        'status:',
        'inspected:',
        'exact_files:',
        'findings:',
        'reject_cases:',
        'required_tests:',
        'no_open:',
        'blockers:',
    ):
        assert field in kimi_text
        assert field not in codex_text
    assert 'Process updates are invalid receipts' in kimi_text


def test_user_guide_requires_rematerialization_before_provider_adoption() -> None:
    text = (REPO_ROOT / 'docs/manuals/user-guide/chapters/05-communication-workflows.tex').read_text(
        encoding='utf-8'
    )
    normalized = re.sub(r'\s+', ' ', text)

    assert 'registered CCB agent' in normalized
    assert 'direct CLI' in normalized
    assert '先重新 materialize runtime memory' in normalized
    assert '再重启 provider 或启动新 session' in normalized
    assert '不要假设正在运行的 provider session 会热加载' in normalized
