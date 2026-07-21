from __future__ import annotations

import json
from pathlib import Path

from project_memory import (
    agent_private_memory_path,
    ensure_project_memory,
    load_memory_sources,
    materialize_runtime_memory_bundle,
    project_memory_path,
    runtime_memory_bundle_path,
    seed_metadata_path,
)
from project_memory.hashing import sha256_text
from project_memory.policy import should_include_source
from project_memory.template import DEFAULT_PROJECT_MEMORY
from storage.paths import PathLayout


def _write_project_memory(project_root: Path, text: str) -> None:
    path = project_memory_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def _legacy_v4_project_memory_template() -> str:
    return (
        '# CCB Project Memory\n\n'
        'This project uses CCB for visible multi-agent collaboration.\n\n'
        '## Collaboration\n\n'
        '- You are one agent in a CCB-managed project team.\n'
        '- Use CCB `ask` for project-level collaboration with configured agents.\n'
        '- Delegate with the goal, scope/files, assumptions, expected output, and verification needs.\n'
        '- Reply concisely with findings, changes, verification, blockers, and risks when relevant.\n\n'
        '## Ask Communication\n\n'
        'Preferred form:\n\n'
        '```text\n'
        '/ask <agent> <message>\n'
        '```\n\n'
        'Shell fallback:\n\n'
        '```bash\n'
        'command ask "$TARGET" <<\'EOF\'\n'
        '$MESSAGE\n'
        'EOF\n'
        '```\n\n'
        '- Submit once, then stop. Do not wait, poll, or run `pend`/`watch`/`ping` unless diagnostics were requested.\n'
        '- During an active CCB ask task, use `ask --chain` when a child result is needed to finish the current task; use `ask --silence` only for independent no-result-needed work.\n'
        '- Plain nested `ask` from an active task is rejected by CCB.\n'
    )


def test_ensure_project_memory_creates_template_and_seed(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    project_root.mkdir()

    result = ensure_project_memory(project_root, now='2026-05-11T00:00:00+00:00')

    assert result.created is True
    assert result.seed_written is True
    assert result.warning == ''
    memory_path = project_memory_path(project_root)
    assert memory_path.is_file()
    text = memory_path.read_text(encoding='utf-8')
    assert 'This project uses CCB for visible multi-agent collaboration.' in text
    assert 'Use CCB `ask` for project-level collaboration with configured agents.' in text
    assert 'Plain nested `ask` from an active task is' not in text
    assert 'command ask "$TARGET"' not in text
    assert 'Do not wait, poll, or run `pend`/`watch`/`ping`' not in text
    assert 'ccb -h' not in text
    seed = json.loads(seed_metadata_path(project_root).read_text(encoding='utf-8'))
    assert seed['record_type'] == 'ccb_project_memory_seed'
    assert seed['template_version'] == 5
    assert seed['memory_path'] == str(memory_path)
    assert seed['sha256'] == result.sha256


def test_ensure_project_memory_does_not_overwrite_existing_file(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    project_root.mkdir()
    memory_path = project_memory_path(project_root)
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text('# Team Memory\n\nKeep this custom text.\n', encoding='utf-8')

    result = ensure_project_memory(project_root)

    assert result.created is False
    assert result.seed_written is False
    assert memory_path.read_text(encoding='utf-8') == '# Team Memory\n\nKeep this custom text.\n'
    assert not seed_metadata_path(project_root).exists()


def test_ensure_project_memory_ignores_legacy_root_memory(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    project_root.mkdir()
    legacy_path = project_root / 'CCB.md'
    legacy_path.write_text('legacy shared memory\n', encoding='utf-8')

    result = ensure_project_memory(project_root)

    memory_path = project_memory_path(project_root)
    assert result.created is True
    assert result.seed_written is True
    text = memory_path.read_text(encoding='utf-8')
    assert 'This project uses CCB for visible multi-agent collaboration.' in text
    assert 'legacy shared memory' not in text
    assert legacy_path.read_text(encoding='utf-8') == 'legacy shared memory\n'


def test_ensure_project_memory_backfills_missing_seed_for_unedited_template(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    project_root.mkdir()
    first = ensure_project_memory(project_root, now='2026-05-11T00:00:00+00:00')
    seed_metadata_path(project_root).unlink()

    second = ensure_project_memory(project_root, now='2026-05-11T00:01:00+00:00')

    assert first.created is True
    assert second.created is False
    assert second.seed_written is True
    seed = json.loads(seed_metadata_path(project_root).read_text(encoding='utf-8'))
    assert seed['sha256'] == second.sha256


def test_ensure_project_memory_upgrades_unedited_seeded_old_template(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    project_root.mkdir()
    old_template = _legacy_v4_project_memory_template()
    memory_path = project_memory_path(project_root)
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text(old_template, encoding='utf-8')
    seed_metadata_path(project_root).parent.mkdir(parents=True, exist_ok=True)
    seed_metadata_path(project_root).write_text(
        json.dumps(
            {
                'schema_version': 1,
                'record_type': 'ccb_project_memory_seed',
                'template_version': 4,
                'memory_path': str(memory_path),
                'sha256': sha256_text(old_template),
                'created_at': '2026-06-01T00:00:00+00:00',
            },
            ensure_ascii=False,
            indent=2,
        )
        + '\n',
        encoding='utf-8',
    )

    result = ensure_project_memory(project_root, now='2026-06-07T00:00:00+00:00')

    assert result.created is False
    assert result.seed_written is True
    assert result.warning == ''
    assert memory_path.read_text(encoding='utf-8') == DEFAULT_PROJECT_MEMORY
    assert 'command ask "$TARGET"' not in memory_path.read_text(encoding='utf-8')
    seed = json.loads(seed_metadata_path(project_root).read_text(encoding='utf-8'))
    assert seed['template_version'] == 5
    assert seed['sha256'] == result.sha256


def test_ensure_project_memory_upgrades_unedited_legacy_template_without_seed(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    project_root.mkdir()
    old_template = _legacy_v4_project_memory_template()
    memory_path = project_memory_path(project_root)
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text(old_template, encoding='utf-8')

    result = ensure_project_memory(project_root, now='2026-06-07T00:00:00+00:00')

    assert result.created is False
    assert result.seed_written is True
    assert memory_path.read_text(encoding='utf-8') == DEFAULT_PROJECT_MEMORY
    seed = json.loads(seed_metadata_path(project_root).read_text(encoding='utf-8'))
    assert seed['template_version'] == 5
    assert seed['sha256'] == result.sha256


def test_ensure_project_memory_does_not_upgrade_edited_old_seed(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    project_root.mkdir()
    seeded_text = '# CCB Project Memory\n\n## Ask Communication\nseeded\n'
    edited_text = seeded_text + '\nUser edit.\n'
    memory_path = project_memory_path(project_root)
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text(edited_text, encoding='utf-8')
    seed_metadata_path(project_root).parent.mkdir(parents=True, exist_ok=True)
    seed_metadata_path(project_root).write_text(
        json.dumps(
            {
                'schema_version': 1,
                'record_type': 'ccb_project_memory_seed',
                'template_version': 4,
                'memory_path': str(memory_path),
                'sha256': sha256_text(seeded_text),
                'created_at': '2026-06-01T00:00:00+00:00',
            },
            ensure_ascii=False,
            indent=2,
        )
        + '\n',
        encoding='utf-8',
    )

    result = ensure_project_memory(project_root)

    assert result.created is False
    assert result.seed_written is False
    assert memory_path.read_text(encoding='utf-8') == edited_text


def test_load_memory_sources_reads_from_project_root_not_workspace(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    workspace = tmp_path / 'worktree'
    project_root.mkdir()
    workspace.mkdir()
    _write_project_memory(project_root, 'shared memory\n')
    (project_root / 'GEMINI.md').write_text('project gemini memory\n', encoding='utf-8')
    (workspace / 'GEMINI.md').write_text('workspace-only memory\n', encoding='utf-8')
    agent_private_memory_path(project_root, 'Agent3').parent.mkdir(parents=True)
    agent_private_memory_path(project_root, 'Agent3').write_text('private memory\n', encoding='utf-8')

    sources = load_memory_sources(project_root, agent_name='Agent3', provider='gemini')

    content_by_kind = {source.kind: source.content for source in sources}
    assert content_by_kind['ccb_shared'] == 'shared memory\n'
    assert content_by_kind['provider_native_project'] == 'project gemini memory\n'
    assert content_by_kind['agent_private'] == 'private memory\n'
    assert 'workspace-only memory' not in ''.join(content_by_kind.values())


def test_load_memory_sources_can_skip_provider_native_project_memory(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    project_root.mkdir()
    _write_project_memory(project_root, 'shared memory\n')
    (project_root / 'GEMINI.md').write_text('project gemini memory\n', encoding='utf-8')
    agent_private_memory_path(project_root, 'Agent1').parent.mkdir(parents=True)
    agent_private_memory_path(project_root, 'Agent1').write_text('private memory\n', encoding='utf-8')

    default_sources = load_memory_sources(project_root, agent_name='Agent1', provider='gemini')
    skipped_sources = load_memory_sources(
        project_root,
        agent_name='Agent1',
        provider='gemini',
        include_provider_native_project=False,
    )

    assert [source.kind for source in default_sources] == [
        'ccb_shared',
        'provider_native_project',
        'agent_private',
    ]
    assert [source.kind for source in skipped_sources] == ['ccb_shared', 'agent_private']
    assert 'project gemini memory' not in ''.join(source.content for source in skipped_sources)


def test_provider_memory_policy_excludes_native_project_for_duplicate_loading_providers() -> None:
    assert should_include_source('claude', 'provider_native_project') is False
    assert should_include_source('codex', 'provider_native_project') is False
    assert should_include_source('opencode', 'provider_native_project') is False
    assert should_include_source('gemini', 'provider_native_project') is True


def test_materialize_runtime_memory_bundle_writes_generated_bundle(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    workspace = tmp_path / 'worktree'
    project_root.mkdir()
    workspace.mkdir()
    _write_project_memory(project_root, 'shared ask rules\n')
    (project_root / 'CLAUDE.md').write_text('claude project rules\n', encoding='utf-8')
    agent_private_memory_path(project_root, 'agent1').parent.mkdir(parents=True)
    agent_private_memory_path(project_root, 'agent1').write_text('agent private rules\n', encoding='utf-8')

    result = materialize_runtime_memory_bundle(
        project_root,
        agent_name='agent1',
        provider='claude',
        workspace_path=workspace,
    )

    assert result.written is True
    assert result.warnings == ()
    bundle_path = runtime_memory_bundle_path(project_root, 'agent1')
    assert result.path == bundle_path
    text = bundle_path.read_text(encoding='utf-8')
    assert '# CCB Managed Agent Memory' in text
    assert '<!-- ccb-memory-bundle schema_version=1' in text
    assert 'provider: claude' in text
    assert f'workspace_path: {workspace.resolve()}' in text
    assert '## CCB Runtime Coordination Rules' in text
    assert 'CCB `ask` is submit-only' in text
    assert 'Do not wait, poll, or run `pend`/`watch`/`ping`' in text
    assert 'use `ask --chain` when a child result is needed' in text
    assert 'Finish an inbound CCB task in its current turn' in text
    assert "CCB routes that turn's terminal result to the caller" in text
    assert 'do not open a new `ask` to report completion to the original caller' in text
    assert 'During a CCB result-chain continuation, answer directly with the final result' in text
    assert 'do not use `ask`, `--chain`, or `--silence`' in text
    assert '`--silence` is not an active-job correction channel' in text
    assert 'cancel and resubmit it' in text
    assert 'A `completed` CCB job means provider execution ended normally' in text
    assert '## CCB Shared Project Memory' in text
    assert 'shared ask rules' in text
    assert text.index('## CCB Runtime Coordination Rules') < text.index('## CCB Shared Project Memory')
    assert '## Provider-Native Project Memory' not in text
    assert 'claude project rules' not in text
    assert '## Agent Private Memory' in text
    assert 'agent private rules' in text
    assert {source.kind for source in result.sources} == {
        'ccb_shared',
        'agent_private',
    }


def test_materialize_runtime_memory_bundle_skips_unchanged_write(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    project_root.mkdir()
    _write_project_memory(project_root, 'shared ask rules\n')

    first = materialize_runtime_memory_bundle(project_root, agent_name='agent1', provider='opencode')
    mtime = runtime_memory_bundle_path(project_root, 'agent1').stat().st_mtime_ns
    second = materialize_runtime_memory_bundle(project_root, agent_name='agent1', provider='opencode')

    assert first.written is True
    assert first.unchanged is False
    assert second.written is False
    assert second.unchanged is True
    assert runtime_memory_bundle_path(project_root, 'agent1').stat().st_mtime_ns == mtime


def test_project_memory_paths_follow_path_layout_runtime_state_root(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo'
    runtime_root = tmp_path / 'runtime-root'
    project_root.mkdir()
    layout = PathLayout(project_root)
    object.__setattr__(layout, '_state_root', runtime_root)

    result = materialize_runtime_memory_bundle(layout, agent_name='agent1', provider='claude')

    assert result.path == runtime_root / 'runtime' / 'memory' / 'agent1.md'
    assert result.path.is_file()
    assert seed_metadata_path(layout) == runtime_root / 'state' / 'memory.seed.json'
    assert seed_metadata_path(layout).is_file()


def test_materialize_runtime_memory_bundle_handles_invalid_agent_name(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    project_root.mkdir()

    result = materialize_runtime_memory_bundle(project_root, agent_name='bad/name', provider='claude')

    assert result.written is False
    assert result.sources == ()
    assert result.warnings
