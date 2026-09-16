from __future__ import annotations

import json
from pathlib import Path

import pytest

from provider_backends.claude.launcher_runtime.home import materialize_claude_home_config
from provider_backends.opencode.launcher import materialize_opencode_memory_config
import provider_profiles.codex_home_config as codex_home_config
from provider_profiles.models import ProviderProfileSpec
from project_memory import materialize_runtime_memory_bundle


pytestmark = pytest.mark.usefixtures('stub_claude_private_keychain')


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def _assert_single_runtime_coordination(text: str) -> None:
    assert text.count('## CCB Runtime Coordination Rules') == 1
    assert text.count('command ask "$TARGET"') == 1


def test_realistic_provider_memory_context_composes_each_provider_bundle(tmp_path: Path) -> None:
    project_root = tmp_path / 'external-real-project'
    workspace_path = tmp_path / 'external-real-project-worktree'
    claude_source_home = tmp_path / 'source-claude-home'
    codex_source_home = tmp_path / 'source-codex-home'
    project_root.mkdir()
    workspace_path.mkdir()

    _write(
        project_root / '.ccb' / 'ccb_memory.md',
        '# CCB Project Memory\n\n'
        'SHARED-MEMORY-SENTINEL\n',
    )
    _write(project_root / 'CLAUDE.md', 'PROJECT-CLAUDE-SENTINEL\n')
    _write(project_root / 'AGENTS.md', 'PROJECT-AGENTS-SENTINEL\n')
    _write(project_root / 'GEMINI.md', 'PROJECT-GEMINI-SENTINEL\n')
    _write(
        project_root / 'opencode.json',
        json.dumps({'instructions': ['AGENTS.md'], 'model': 'test-model'}, ensure_ascii=False, indent=2) + '\n',
    )

    _write(project_root / '.ccb' / 'agents' / 'reviewer' / 'memory.md', 'CLAUDE-PRIVATE-SENTINEL\n')
    _write(project_root / '.ccb' / 'agents' / 'builder' / 'memory.md', 'CODEX-PRIVATE-SENTINEL\n')
    _write(project_root / '.ccb' / 'agents' / 'designer' / 'memory.md', 'OPENCODE-PRIVATE-SENTINEL\n')
    _write(project_root / '.ccb' / 'agents' / 'analyst' / 'memory.md', 'GEMINI-PRIVATE-SENTINEL\n')

    _write(
        claude_source_home / '.claude' / 'CLAUDE.md',
        'CLAUDE-USER-SENTINEL\n'
        '<!-- CCB_CONFIG_START -->\n'
        'OLD-CLAUDE-INSTALL-BLOCK\n'
        '<!-- CCB_CONFIG_END -->\n',
    )
    _write(
        codex_source_home / 'AGENTS.md',
        'CODEX-USER-SENTINEL\n'
        '<!-- CCB_ROLES_START -->\n'
        'OLD-CODEX-ROLES-BLOCK\n'
        '<!-- CCB_ROLES_END -->\n',
    )

    claude_layout = materialize_claude_home_config(
        project_root / '.ccb' / 'agents' / 'reviewer' / 'provider-state' / 'claude' / 'home',
        source_home=claude_source_home,
        project_root=project_root,
        agent_name='reviewer',
        workspace_path=workspace_path,
    )
    codex_home_config.materialize_codex_home_config(
        project_root / '.ccb' / 'agents' / 'builder' / 'provider-state' / 'codex' / 'home',
        source_home=codex_source_home,
        project_root=project_root,
        agent_name='builder',
        workspace_path=workspace_path,
    )
    opencode_config_path = project_root / '.ccb' / 'agents' / 'designer' / 'provider-state' / 'opencode' / 'opencode.json'
    opencode_config_path.parent.mkdir(parents=True, exist_ok=True)
    opencode_result = materialize_opencode_memory_config(
        project_root=project_root,
        agent_name='designer',
        workspace_path=workspace_path,
        config_path=opencode_config_path,
        profile=ProviderProfileSpec(),
        event_path=None,
        marker_path=project_root / '.ccb' / 'agents' / 'designer' / 'memory-projection.json',
    )
    gemini_materialization = materialize_runtime_memory_bundle(
        project_root,
        agent_name='analyst',
        provider='gemini',
        workspace_path=workspace_path,
    )

    claude_text = (claude_layout.claude_dir / 'CLAUDE.md').read_text(encoding='utf-8')
    codex_text = (
        project_root / '.ccb' / 'agents' / 'builder' / 'provider-state' / 'codex' / 'home' / 'AGENTS.md'
    ).read_text(encoding='utf-8')
    opencode_text = (project_root / '.ccb' / 'runtime' / 'memory' / 'designer.md').read_text(encoding='utf-8')
    gemini_text = gemini_materialization.path.read_text(encoding='utf-8')

    for text in (claude_text, codex_text, opencode_text, gemini_text):
        assert '# CCB Managed Agent Memory' in text
        assert 'SHARED-MEMORY-SENTINEL' in text
        _assert_single_runtime_coordination(text)

    assert 'provider: claude' in claude_text
    assert 'CLAUDE-USER-SENTINEL' in claude_text
    assert 'OLD-CLAUDE-INSTALL-BLOCK' not in claude_text
    assert 'PROJECT-CLAUDE-SENTINEL' not in claude_text
    assert 'CLAUDE-PRIVATE-SENTINEL' in claude_text

    assert 'provider: codex' in codex_text
    assert 'CODEX-USER-SENTINEL' in codex_text
    assert 'OLD-CODEX-ROLES-BLOCK' not in codex_text
    assert 'PROJECT-AGENTS-SENTINEL' not in codex_text
    assert 'CODEX-PRIVATE-SENTINEL' in codex_text

    assert opencode_result.env == {'OPENCODE_CONFIG': str(opencode_config_path)}
    opencode_config = json.loads(opencode_config_path.read_text(encoding='utf-8'))
    assert opencode_config['instructions'] == [
        'AGENTS.md',
        '.ccb/runtime/memory/designer.md',
        '.ccb/runtime/skills/designer/opencode/ask.md',
    ]
    assert (project_root / '.ccb' / 'runtime' / 'skills' / 'designer' / 'opencode' / 'ask.md').is_file()
    assert 'provider: opencode' in opencode_text
    assert 'PROJECT-AGENTS-SENTINEL' not in opencode_text
    assert 'OPENCODE-PRIVATE-SENTINEL' in opencode_text

    assert 'provider: gemini' in gemini_text
    assert 'PROJECT-GEMINI-SENTINEL' in gemini_text
    assert 'GEMINI-PRIVATE-SENTINEL' in gemini_text
