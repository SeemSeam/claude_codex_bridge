from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from rust_helpers import RUST_HELPER_BIN_ENV, RUST_HELPERS_ENV
from rust_helpers_storage import RUST_STORAGE_SCAN_ENV
from storage.paths import PathLayout
from storage.path_helpers import RuntimeStatePlacement
from storage_classification import summarize_storage, summarize_storage_compact
from storage_classification.provider_home import classify_provider_home


def _write(path: Path, text: str = 'x') -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def _records_by_suffix(payload: dict[str, object]) -> dict[str, dict[str, object]]:
    return {str(item['relative_path']): item for item in payload['entries']}


def _write_helper(path: Path, body: str) -> Path:
    path.write_text('#!/usr/bin/env python3\n' + body, encoding='utf-8')
    path.chmod(0o755)
    return path


def _storage_inventory_stub_helper(path: Path) -> Path:
    return _write_helper(
        path,
        """import json, os, sys
from pathlib import Path

if sys.argv[1:] == ['--capabilities']:
    print(json.dumps({'schema_version': 1, 'capabilities': ['storage.scan.inventory']}))
else:
    request = json.loads(sys.stdin.read())
    records = []
    seen = set()
    for root in request['payload']['roots']:
        root_path = Path(root['path'])
        if not root_path.exists():
            continue
        for current, dirs, files in os.walk(root_path, followlinks=False):
            current_path = Path(current)
            safe_dirs = []
            for dirname in dirs:
                candidate = current_path / dirname
                if candidate.is_symlink():
                    paths = [candidate]
                else:
                    safe_dirs.append(dirname)
                    paths = []
                for path in paths:
                    stat = path.lstat()
                    identity = (stat.st_dev, stat.st_ino)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    records.append({
                        'path': str(path),
                        'relative_path': str(path.relative_to(root_path)),
                        'root_kind': root['root_kind'],
                        'size_bytes': stat.st_size,
                        'is_symlink': path.is_symlink(),
                    })
            dirs[:] = safe_dirs
            for filename in files:
                path = current_path / filename
                stat = path.lstat()
                identity = (stat.st_dev, stat.st_ino)
                if identity in seen:
                    continue
                seen.add(identity)
                records.append({
                    'path': str(path),
                    'relative_path': str(path.relative_to(root_path)),
                    'root_kind': root['root_kind'],
                    'size_bytes': stat.st_size,
                    'is_symlink': path.is_symlink(),
                })
    print(json.dumps({'schema_version': 1, 'ok': True, 'capability': request['capability'], 'payload': records}))
""",
    )


def _storage_summary_stub_helper(path: Path) -> Path:
    return _write_helper(
        path,
        """import json, sys

if sys.argv[1:] == ['--capabilities']:
    print(json.dumps({'schema_version': 1, 'capabilities': ['storage.scan.summary']}))
else:
    request = json.loads(sys.stdin.read())
    limit = request['payload'].get('top_entries_limit', 50)
    entries = [
        {
            'path': '/repo/.ccb/agents/main/provider-state/codex/home/auth.json',
            'relative_path': 'agents/main/provider-state/codex/home/auth.json',
            'storage_class': 'secret',
            'size_bytes': 9,
            'provider': 'codex',
            'agent': 'main',
            'active': None,
            'is_active_version': None,
            'reachable_from_current_symlink': None,
            'reclaimable': None,
            'reason': 'provider_secret',
            'root_kind': 'project',
        },
        {
            'path': '/repo/.ccb/ccb.config',
            'relative_path': 'ccb.config',
            'storage_class': 'authority',
            'size_bytes': 3,
            'provider': None,
            'agent': None,
            'active': None,
            'is_active_version': None,
            'reachable_from_current_symlink': None,
            'reclaimable': None,
            'reason': None,
            'root_kind': 'project',
        },
    ]
    print(json.dumps({'schema_version': 1, 'ok': True, 'capability': request['capability'], 'payload': {
        'total_bytes': 12,
        'total_count': 2,
        'by_class': {'authority': {'bytes': 3, 'count': 1}, 'secret': {'bytes': 9, 'count': 1}},
        'by_provider': {'codex': {'bytes': 9, 'count': 1}},
        'by_agent': {'main': {'bytes': 9, 'count': 1}},
        'entries': entries[:limit],
    }}))
""",
    )


def _stable_summary(payload: dict[str, object]) -> dict[str, object]:
    stable = dict(payload)
    stable.pop('generated_at', None)
    stable['entries'] = sorted(
        [dict(item) for item in payload['entries']],
        key=lambda item: (str(item['relative_path']), str(item['path'])),
    )
    return stable


def test_provider_home_classifier_preserves_secret_precedence_and_unknowns(tmp_path: Path) -> None:
    provider_home = tmp_path / 'repo' / '.ccb' / 'agents' / 'agent1' / 'provider-state' / 'unknownai' / 'home'
    secret_path = provider_home / 'auth.json'
    unknown_path = provider_home / 'notes.txt'

    secret = classify_provider_home(
        secret_path,
        'agents/agent1/provider-state/unknownai/home/auth.json',
        'UnknownAI',
        'agent1',
        ('auth.json',),
        size=2,
        root_kind='project',
    )
    unknown = classify_provider_home(
        unknown_path,
        'agents/agent1/provider-state/unknownai/home/notes.txt',
        'UnknownAI',
        'agent1',
        ('notes.txt',),
        size=5,
        root_kind='project',
    )

    assert secret.storage_class.value == 'secret'
    assert secret.provider == 'unknownai'
    assert secret.reason == 'provider_secret'
    assert unknown.storage_class.value == 'unknown'
    assert unknown.provider == 'unknownai'


@pytest.mark.parametrize(
    ('provider', 'remainder'),
    (
        ('deepseek', ('.deepcode', 'settings.json')),
        ('kimi', ('.kimi-code', 'config.toml')),
        ('kiro', ('data', 'kiro-cli', 'data.sqlite3')),
        ('mimo', ('.mimocode', 'token.json')),
        ('opencode', ('data', 'opencode', 'account.json')),
        ('omp', ('.omp', 'agent', 'agent.db')),
        ('crush', ('data', 'providers.json')),
        ('zai', ('.zai', 'user-settings.json')),
    ),
)
def test_auth_bearing_mixed_provider_files_are_secret(
    tmp_path: Path,
    provider: str,
    remainder: tuple[str, ...],
) -> None:
    path = tmp_path.joinpath(*remainder)

    entry = classify_provider_home(
        path,
        f'agents/agent1/provider-state/{provider}/home/{"/".join(remainder)}',
        provider,
        'agent1',
        remainder,
        size=3,
        root_kind='project',
    )

    assert entry.storage_class.value == 'secret'
    assert entry.reason == 'provider_mixed_auth_state'


@pytest.mark.parametrize(
    'name',
    (
        'agent.db-wal',
        'agent.db-shm',
        'config.yml',
        'config.yaml',
        'models.yml',
        'models.yaml',
        'models.json',
        'oauth.json',
        'settings.json',
    ),
)
def test_omp_auth_capable_projection_files_are_secret(
    tmp_path: Path,
    name: str,
) -> None:
    remainder = ('.omp', 'agent', name)
    entry = classify_provider_home(
        tmp_path.joinpath(*remainder),
        f'agents/omp1/provider-state/omp/home/{"/".join(remainder)}',
        'omp',
        'omp1',
        remainder,
        size=3,
        root_kind='project',
    )

    assert entry.storage_class.value == 'secret'
    assert entry.reason == 'provider_mixed_auth_state'


@pytest.mark.parametrize('provider', ('qoder', 'qoderclicn'))
def test_qoder_config_auth_root_is_secret_and_cache_is_rebuildable(
    tmp_path: Path,
    provider: str,
) -> None:
    agent = f'{provider}1'
    provider_home = (
        tmp_path / 'repo' / '.ccb' / 'agents' / agent / 'provider-state' / provider / 'home'
    )
    auth_path = provider_home / '.auth' / 'session.json'
    cache_path = provider_home / '.cache' / 'endpoint-cache.json'

    auth = classify_provider_home(
        auth_path,
        f'agents/{agent}/provider-state/{provider}/home/.auth/session.json',
        provider,
        agent,
        ('.auth', 'session.json'),
        size=2,
        root_kind='project',
    )
    cache = classify_provider_home(
        cache_path,
        f'agents/{agent}/provider-state/{provider}/home/.cache/endpoint-cache.json',
        provider,
        agent,
        ('.cache', 'endpoint-cache.json'),
        size=2,
        root_kind='project',
    )

    assert auth.storage_class.value == 'secret'
    assert auth.reason == f'{provider}_auth_state'
    assert cache.storage_class.value == 'rebuildable_cache'


@pytest.mark.parametrize('provider', ('qoder', 'qoderclicn'))
def test_qoder_config_skills_are_projected(
    tmp_path: Path,
    provider: str,
) -> None:
    path = tmp_path / 'home' / 'skills' / 'ask' / 'SKILL.md'
    entry = classify_provider_home(
        path,
        f'agents/agent1/provider-state/{provider}/home/skills/ask/SKILL.md',
        provider,
        'agent1',
        ('skills', 'ask', 'SKILL.md'),
        size=2,
        root_kind='project',
    )

    assert entry.storage_class.value == 'projected_config'
    assert entry.reason == 'qoder_skill_projection'


@pytest.mark.parametrize(
    ('remainder', 'storage_class'),
    (
        (('.credentials.yaml',), 'secret'),
        (('.env',), 'secret'),
        (('settings.yaml',), 'projected_config'),
        (('AGENTS.md',), 'projected_config'),
        (('skills', 'ask', 'SKILL.md'), 'projected_config'),
        (('sessions', 'session-1.jsonl'), 'session'),
        (('.cache', 'catalog.json'), 'rebuildable_cache'),
    ),
)
def test_dsh_managed_home_storage_boundaries(
    tmp_path: Path,
    remainder: tuple[str, ...],
    storage_class: str,
) -> None:
    path = tmp_path.joinpath(*remainder)
    entry = classify_provider_home(
        path,
        f'agents/dsh1/provider-state/dsh/home/{"/".join(remainder)}',
        'dsh',
        'dsh1',
        remainder,
        size=3,
        root_kind='project',
    )

    assert entry.storage_class.value == storage_class


def test_storage_classification_keeps_provider_authority_and_cache_separate(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    ccb = project_root / '.ccb'
    codex_home = ccb / 'agents' / 'agent1' / 'provider-state' / 'codex' / 'home'
    claude_home = ccb / 'agents' / 'agent2' / 'provider-state' / 'claude' / 'home'
    gemini_home = ccb / 'agents' / 'agent3' / 'provider-state' / 'gemini' / 'home'
    opencode_state = ccb / 'agents' / 'agent4' / 'provider-state' / 'opencode'
    kimi_state = ccb / 'agents' / 'agent5' / 'provider-state' / 'kimi'
    mimo_state = ccb / 'agents' / 'agent6' / 'provider-state' / 'mimo'
    qwen_state = ccb / 'agents' / 'agent7' / 'provider-state' / 'qwen'
    cursor_state = ccb / 'agents' / 'agent8' / 'provider-state' / 'cursor'
    copilot_state = ccb / 'agents' / 'agent9' / 'provider-state' / 'copilot'
    crush_state = ccb / 'agents' / 'agent10' / 'provider-state' / 'crush'
    kiro_state = ccb / 'agents' / 'agent11' / 'provider-state' / 'kiro'
    pi_state = ccb / 'agents' / 'agent12' / 'provider-state' / 'pi'
    grok_state = ccb / 'agents' / 'agent13' / 'provider-state' / 'grok'
    droid_state = ccb / 'agents' / 'agent14' / 'provider-state' / 'droid'

    _write(ccb / 'ccb.config', 'agent1:codex\n')
    _write(ccb / 'ccb_memory.md', '# shared memory\n')
    _write(ccb / 'history' / 'handoff.md', '# handoff\n')
    _write(ccb / 'workspaces' / 'agent1' / 'notes.txt', 'workspace change\n')
    _write(ccb / 'shared-cache' / 'claude' / 'versions' / '2.1.137' / 'claude', 'shared bin\n')
    _write(ccb / 'agents' / 'agent1' / 'runtime.json', '{}\n')
    _write(ccb / 'agents' / 'agent1' / 'memory.md', '# private memory\n')
    _write(ccb / 'state' / 'memory.seed.json', '{}\n')
    _write(ccb / 'runtime' / 'memory' / 'agent1.md', '# memory\n')
    _write(ccb / 'runtime' / 'skills' / 'agent4' / 'opencode' / 'ask.md', '# ask\n')
    _write(codex_home / 'sessions' / '2026' / 'session.jsonl')
    _write(codex_home / '.ccb-session-namespace.json', '{}\n')
    _write(codex_home / 'auth.json', '{}\n')
    _write(codex_home / '.ccb-auth-projection.json', '{}\n')
    _write(codex_home / 'company-codex-api-key', 'secret\n')
    _write(codex_home / 'company-codex.config.toml', 'profile\n')
    _write(codex_home / 'config.toml', '# config\n')
    _write(codex_home / '.tmp' / 'plugins' / 'plugins' / 'demo' / 'SKILL.md')
    _write(codex_home / '.tmp' / 'plugins.sha', 'abc\n')
    source_skills = tmp_path / 'source-codex-home' / 'skills'
    source_skills.mkdir(parents=True, exist_ok=True)
    if hasattr(os, 'symlink'):
        os.symlink(source_skills, codex_home / 'skills')
        _write(
            codex_home / 'skills.ccb-projection.json',
            json.dumps(
                {
                    'record_type': 'ccb_projected_asset',
                    'label': 'codex-inherited-skills',
                    'source': str(source_skills),
                }
            )
            + '\n',
        )

    _write(claude_home / '.claude' / '.claude.json', '{}\n')
    _write(claude_home / '.claude.json', '{}\n')
    _write(claude_home / '.claude' / '.credentials.json', '{}\n')
    _write(claude_home / '.config' / 'claude-code' / 'auth.json', '{}\n')
    _write(claude_home / '.claude' / 'settings.json', '{}\n')
    source_keychains = tmp_path / 'source-home' / 'Library' / 'Keychains'
    source_keychains.mkdir(parents=True, exist_ok=True)
    if hasattr(os, 'symlink'):
        (claude_home / 'Library').mkdir(parents=True, exist_ok=True)
        os.symlink(source_keychains, claude_home / 'Library' / 'Keychains')
    _write(claude_home / '.local' / 'share' / 'claude' / 'versions' / '2.1.137' / 'claude', 'bin\n')
    if hasattr(os, 'symlink'):
        (claude_home / '.local' / 'bin').mkdir(parents=True, exist_ok=True)
        os.symlink('../share/claude/versions/2.1.137/claude', claude_home / '.local' / 'bin' / 'claude')

    _write(gemini_home / '.gemini' / 'tmp' / 'checkpoint.json', '{}\n')
    _write(gemini_home / '.gemini' / 'oauth_creds.json', '{}\n')
    _write(gemini_home / '.gemini' / 'settings.json', '{}\n')
    _write(gemini_home / '.npm' / '_cacache' / 'content-v2' / 'sha512' / 'aa' / 'blob')
    _write(opencode_state / 'opencode.json', '{}\n')
    _write(kimi_state / 'inherited-skills' / 'ask' / 'SKILL.md', '# ask\n')
    _write(kimi_state / 'overlay-skills' / 'trellis-check' / 'SKILL.md', '# trellis-check\n')
    _write(mimo_state / 'mimocode.json', '{}\n')
    _write(mimo_state / 'home' / 'data' / 'mimocode.db', 'db\n')
    _write(mimo_state / 'home' / 'cache' / 'bin' / 'mimo', 'bin\n')
    _write(qwen_state / 'home' / '.cache' / 'compiled-provider-file')
    _write(cursor_state / 'inherited-skills' / 'ask' / 'SKILL.md', '# ask\n')
    _write(copilot_state / 'home' / '.config' / 'copilot' / 'session.json', '{}\n')
    _write(copilot_state / 'home' / 'config.json', '{}\n')
    _write(copilot_state / 'home' / '.ccb-installed-plugins-projection.json', '{}\n')
    _write(copilot_state / 'home' / 'installed-plugins' / 'fixture-marketplace' / 'fixture-plugin' / 'plugin.json', '{}\n')
    _write(copilot_state / 'home' / 'plugin-data' / 'fixture-marketplace' / 'fixture-plugin' / 'state.json', '{}\n')
    _write(copilot_state / 'home' / 'mcp-secrets' / 'fixture.json', '{}\n')
    _write(copilot_state / 'data' / 'cache' / 'marketplace.json', '{}\n')
    _write(crush_state / 'data' / 'crush.db', 'db\n')
    _write(kiro_state / 'home' / 'logs' / 'chat.log', 'log\n')
    _write(pi_state / 'home' / '.pi' / 'agent' / 'settings.json', '{}\n')
    _write(pi_state / 'sessions' / 'session.jsonl', '{}\n')
    _write(grok_state / 'home' / '.grok' / 'sessions' / 'session.jsonl', '{}\n')
    _write(grok_state / 'home' / '.grok' / 'skills' / 'ask' / 'SKILL.md', '# ask\n')
    _write(grok_state / 'home' / '.grok' / 'skills' / 'ccb-diagnose' / 'SKILL.md', '# diagnose\n')
    _write(grok_state / 'home' / '.grok' / 'skills' / 'help' / 'SKILL.md', '# help\n')
    _write(droid_state / 'home' / '.factory' / 'auth.v2.file', 'ciphertext\n')
    _write(droid_state / 'home' / '.factory' / 'auth.v2.key', 'key\n')
    _write(droid_state / 'home' / '.factory' / 'sessions' / 'session.jsonl', '{}\n')

    payload = summarize_storage(PathLayout(project_root))
    records = _records_by_suffix(payload)

    assert payload['shared_cache_root'] == str(ccb / 'shared-cache')
    assert payload['shared_cache_root_usable'] is True
    assert payload['shared_cache_status'] == 'enabled'
    assert payload['shared_cache_reason'] == 'enabled'
    assert records['agents/agent1/runtime.json']['storage_class'] == 'authority'
    assert records['agents/agent1/memory.md']['storage_class'] == 'user_content'
    assert records['agents/agent1/memory.md']['reason'] == 'agent_private_memory'
    assert records['ccb_memory.md']['storage_class'] == 'user_content'
    assert records['ccb_memory.md']['reason'] == 'project_shared_memory'
    assert records['state/memory.seed.json']['storage_class'] == 'authority'
    assert records['state/memory.seed.json']['reason'] == 'project_memory_seed'
    assert records['runtime/memory/agent1.md']['storage_class'] == 'runtime_ephemeral'
    assert records['runtime/memory/agent1.md']['reason'] == 'project_memory_bundle'
    assert records['runtime/skills/agent4/opencode/ask.md']['storage_class'] == 'projected_config'
    assert records['runtime/skills/agent4/opencode/ask.md']['provider'] == 'opencode'
    assert records['runtime/skills/agent4/opencode/ask.md']['reason'] == 'provider_skill_instruction'
    assert records['history/handoff.md']['storage_class'] == 'user_content'
    assert records['workspaces/agent1/notes.txt']['storage_class'] == 'workspace'
    assert records['shared-cache/claude/versions/2.1.137/claude']['storage_class'] == 'rebuildable_cache'
    assert records['shared-cache/claude/versions/2.1.137/claude']['provider'] == 'claude'
    assert records['shared-cache/claude/versions/2.1.137/claude']['reason'] == 'shared_cache'
    assert records['agents/agent1/provider-state/codex/home/sessions/2026/session.jsonl']['storage_class'] == 'session'
    assert records['agents/agent1/provider-state/codex/home/.ccb-session-namespace.json']['storage_class'] == 'session'
    assert records['agents/agent1/provider-state/codex/home/auth.json']['storage_class'] == 'secret'
    assert records['agents/agent1/provider-state/codex/home/.ccb-auth-projection.json']['storage_class'] == 'secret'
    assert records['agents/agent1/provider-state/codex/home/company-codex-api-key']['storage_class'] == 'secret'
    assert records['agents/agent1/provider-state/codex/home/company-codex.config.toml']['storage_class'] == 'secret'
    assert records['agents/agent1/provider-state/codex/home/config.toml']['storage_class'] == 'projected_config'
    if hasattr(os, 'symlink'):
        assert records['agents/agent1/provider-state/codex/home/skills']['storage_class'] == 'projected_config'
    assert (
        records['agents/agent1/provider-state/codex/home/.tmp/plugins/plugins/demo/SKILL.md']['storage_class']
        == 'startup_authority_bundle'
    )
    assert records['agents/agent1/provider-state/codex/home/.tmp/plugins.sha']['storage_class'] == 'startup_authority_bundle'

    assert records['agents/agent2/provider-state/claude/home/.claude/.claude.json']['storage_class'] == 'secret'
    assert (
        records['agents/agent2/provider-state/claude/home/.claude/.claude.json']['reason']
        == 'claude_trust_mcp_authority'
    )
    assert records['agents/agent2/provider-state/claude/home/.claude.json']['storage_class'] == 'secret'
    assert records['agents/agent2/provider-state/claude/home/.claude/.credentials.json']['storage_class'] == 'secret'
    assert records['agents/agent2/provider-state/claude/home/.config/claude-code/auth.json']['storage_class'] == 'secret'
    if hasattr(os, 'symlink'):
        assert records['agents/agent2/provider-state/claude/home/Library/Keychains']['storage_class'] == 'secret'
        assert records['agents/agent2/provider-state/claude/home/Library/Keychains']['reason'] == 'macos_keychain_link'
    assert records['agents/agent2/provider-state/claude/home/.claude/settings.json']['storage_class'] == 'projected_config'
    assert (
        records['agents/agent2/provider-state/claude/home/.local/share/claude/versions/2.1.137/claude']['storage_class']
        == 'rebuildable_cache'
    )
    assert records['agents/agent2/provider-state/claude/home/.local/share/claude/versions/2.1.137/claude']['active'] is False
    assert (
        records['agents/agent2/provider-state/claude/home/.local/share/claude/versions/2.1.137/claude'][
            'is_active_version'
        ]
        is True
    )
    assert (
        records['agents/agent2/provider-state/claude/home/.local/share/claude/versions/2.1.137/claude'][
            'reachable_from_current_symlink'
        ]
        is True
    )
    assert records['agents/agent2/provider-state/claude/home/.local/bin/claude']['active'] is True
    assert records['agents/agent2/provider-state/claude/home/.local/bin/claude']['is_active_version'] is False

    assert records['agents/agent3/provider-state/gemini/home/.gemini/tmp/checkpoint.json']['storage_class'] == 'session'
    assert records['agents/agent3/provider-state/gemini/home/.gemini/oauth_creds.json']['storage_class'] == 'secret'
    assert records['agents/agent3/provider-state/gemini/home/.gemini/settings.json']['storage_class'] == 'projected_config'
    assert (
        records['agents/agent3/provider-state/gemini/home/.npm/_cacache/content-v2/sha512/aa/blob']['storage_class']
        == 'rebuildable_cache'
    )
    assert records['agents/agent4/provider-state/opencode/opencode.json']['storage_class'] == 'projected_config'
    assert records['agents/agent5/provider-state/kimi/inherited-skills/ask/SKILL.md']['storage_class'] == 'projected_config'
    assert (
        records['agents/agent5/provider-state/kimi/overlay-skills/trellis-check/SKILL.md']['storage_class']
        == 'projected_config'
    )
    assert records['agents/agent6/provider-state/mimo/mimocode.json']['storage_class'] == 'projected_config'
    assert records['agents/agent6/provider-state/mimo/home/data/mimocode.db']['storage_class'] == 'session'
    assert records['agents/agent6/provider-state/mimo/home/cache/bin/mimo']['storage_class'] == 'rebuildable_cache'
    assert records['agents/agent7/provider-state/qwen/home/.cache/compiled-provider-file']['storage_class'] == 'rebuildable_cache'
    assert records['agents/agent8/provider-state/cursor/inherited-skills/ask/SKILL.md']['storage_class'] == 'projected_config'
    assert records['agents/agent9/provider-state/copilot/home/.config/copilot/session.json']['storage_class'] == 'session'
    assert records['agents/agent9/provider-state/copilot/home/.config/copilot/session.json']['reason'] == 'native_cli_provider_state'
    assert records['agents/agent9/provider-state/copilot/home/config.json']['storage_class'] == 'secret'
    assert records['agents/agent9/provider-state/copilot/home/config.json']['reason'] == 'copilot_mixed_auth_application_state'
    assert records['agents/agent9/provider-state/copilot/home/.ccb-installed-plugins-projection.json']['storage_class'] == 'projected_config'
    assert records['agents/agent9/provider-state/copilot/home/installed-plugins/fixture-marketplace/fixture-plugin/plugin.json']['storage_class'] == 'projected_config'
    assert records['agents/agent9/provider-state/copilot/home/plugin-data/fixture-marketplace/fixture-plugin/state.json']['storage_class'] == 'session'
    assert records['agents/agent9/provider-state/copilot/home/mcp-secrets/fixture.json']['storage_class'] == 'secret'
    assert records['agents/agent9/provider-state/copilot/data/cache/marketplace.json']['storage_class'] == 'rebuildable_cache'
    assert records['agents/agent9/provider-state/copilot/data/cache/marketplace.json']['reason'] == 'copilot_agent_local_cache'
    assert records['agents/agent10/provider-state/crush/data/crush.db']['storage_class'] == 'session'
    assert records['agents/agent10/provider-state/crush/data/crush.db']['reason'] == 'native_cli_provider_state'
    assert records['agents/agent11/provider-state/kiro/home/logs/chat.log']['storage_class'] == 'session'
    assert records['agents/agent12/provider-state/pi/home/.pi/agent/settings.json']['storage_class'] == 'session'
    assert records['agents/agent12/provider-state/pi/sessions/session.jsonl']['storage_class'] == 'session'
    assert records['agents/agent13/provider-state/grok/home/.grok/sessions/session.jsonl']['storage_class'] == 'session'
    assert records['agents/agent13/provider-state/grok/home/.grok/sessions/session.jsonl']['reason'] == 'native_cli_provider_state'
    assert records['agents/agent13/provider-state/grok/home/.grok/skills/ask/SKILL.md']['storage_class'] == 'projected_config'
    assert records['agents/agent13/provider-state/grok/home/.grok/skills/ccb-diagnose/SKILL.md']['storage_class'] == 'projected_config'
    assert records['agents/agent13/provider-state/grok/home/.grok/skills/help/SKILL.md']['storage_class'] == 'session'
    assert records['agents/agent14/provider-state/droid/home/.factory/auth.v2.file']['storage_class'] == 'secret'
    assert records['agents/agent14/provider-state/droid/home/.factory/auth.v2.key']['storage_class'] == 'secret'
    assert records['agents/agent14/provider-state/droid/home/.factory/sessions/session.jsonl']['storage_class'] == 'session'


def test_storage_summary_rust_inventory_path_matches_python_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    ccb = project_root / '.ccb'
    _write(ccb / 'ccb.config', 'main:codex\n')
    _write(ccb / 'agents' / 'main' / 'runtime.json', '{}\n')
    _write(ccb / 'agents' / 'main' / 'provider-state' / 'codex' / 'home' / 'auth.json', '{}\n')
    _write(ccb / 'agents' / 'main' / 'provider-state' / 'codex' / 'home' / 'sessions' / 's.jsonl', '{}\n')
    _write(ccb / 'ccbd' / 'state.json', '{}\n')
    outside = tmp_path / 'outside'
    _write(outside / 'target.txt', 'outside\n')
    if hasattr(os, 'symlink'):
        os.symlink(outside / 'target.txt', ccb / 'agents' / 'main' / 'outside-link')

    layout = PathLayout(project_root)
    monkeypatch.setenv(RUST_STORAGE_SCAN_ENV, '0')
    monkeypatch.delenv(RUST_HELPER_BIN_ENV, raising=False)
    python_payload = summarize_storage(layout)

    helper = _storage_inventory_stub_helper(tmp_path / 'helper.py')
    monkeypatch.delenv(RUST_STORAGE_SCAN_ENV, raising=False)
    monkeypatch.delenv(RUST_HELPERS_ENV, raising=False)
    monkeypatch.setenv(RUST_HELPER_BIN_ENV, str(helper))
    helper_payload = summarize_storage(layout)

    assert _stable_summary(helper_payload) == _stable_summary(python_payload)


def test_storage_summary_default_auto_falls_back_when_helper_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project_root = tmp_path / 'repo'
    ccb = project_root / '.ccb'
    _write(ccb / 'ccb.config', 'main:codex\n')
    layout = PathLayout(project_root)
    monkeypatch.delenv(RUST_STORAGE_SCAN_ENV, raising=False)
    monkeypatch.delenv(RUST_HELPERS_ENV, raising=False)
    monkeypatch.setenv(RUST_HELPER_BIN_ENV, str(tmp_path / 'missing-helper'))

    payload = summarize_storage(layout)

    assert _records_by_suffix(payload)['ccb.config']['storage_class'] == 'authority'


def test_storage_summary_global_zero_disables_default_auto(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project_root = tmp_path / 'repo'
    ccb = project_root / '.ccb'
    _write(ccb / 'ccb.config', 'main:codex\n')
    layout = PathLayout(project_root)

    def _unexpected_helper(*_args, **_kwargs):
        raise AssertionError('storage helper must not be imported when globally disabled')

    monkeypatch.delenv(RUST_STORAGE_SCAN_ENV, raising=False)
    monkeypatch.setenv(RUST_HELPERS_ENV, '0')
    monkeypatch.setattr('rust_helpers_storage.scan_storage_inventory', _unexpected_helper)

    payload = summarize_storage(layout)

    assert _records_by_suffix(payload)['ccb.config']['storage_class'] == 'authority'


def test_storage_summary_required_missing_helper_raises_without_python_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project_root = tmp_path / 'repo'
    ccb = project_root / '.ccb'
    _write(ccb / 'ccb.config', 'main:codex\n')
    layout = PathLayout(project_root)
    monkeypatch.setenv(RUST_STORAGE_SCAN_ENV, 'required')
    monkeypatch.setenv(RUST_HELPER_BIN_ENV, str(tmp_path / 'missing-helper'))

    with pytest.raises(RuntimeError, match='no Python fallback'):
        summarize_storage(layout)


def test_storage_compact_summary_uses_explicit_rust_summary_helper(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project_root = tmp_path / 'repo'
    ccb = project_root / '.ccb'
    _write(ccb / 'ccb.config', 'main:codex\n')
    layout = PathLayout(project_root)
    helper = _storage_summary_stub_helper(tmp_path / 'summary_helper.py')
    monkeypatch.setenv('CCB_RUST_STORAGE_SUMMARY', '1')
    monkeypatch.setenv(RUST_HELPER_BIN_ENV, str(helper))

    payload = summarize_storage_compact(layout, entries_limit=1)

    assert payload['summary_mode'] == 'compact'
    assert payload['summary_helper_used'] is True
    assert payload['total_count'] == 2
    assert payload['entries_truncated'] is True
    assert payload['entries_limit'] == 1
    assert [entry['relative_path'] for entry in payload['entries']] == [
        'agents/main/provider-state/codex/home/auth.json',
    ]


def test_storage_classification_surfaces_profile_backed_runtime_home(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    profile_home = project_root / '.ccb' / 'provider-profiles' / 'agent2' / 'codex'
    _write(profile_home / 'sessions' / '2026' / 'session.jsonl')
    _write(profile_home / 'auth.json', '{}\n')
    _write(profile_home / '.tmp' / 'plugins' / 'plugins' / 'demo' / 'SKILL.md')

    payload = summarize_storage(PathLayout(project_root))
    records = _records_by_suffix(payload)

    assert records['provider-profiles/agent2/codex/sessions/2026/session.jsonl']['storage_class'] == 'session'
    assert records['provider-profiles/agent2/codex/auth.json']['storage_class'] == 'secret'
    assert (
        records['provider-profiles/agent2/codex/.tmp/plugins/plugins/demo/SKILL.md']['storage_class']
        == 'startup_authority_bundle'
    )


def test_path_layout_exposes_provider_shared_cache_under_runtime_state_root(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    layout = PathLayout(project_root)

    assert layout.shared_cache_dir == layout.runtime_state_root / 'shared-cache'
    assert layout.provider_shared_cache_dir('codex') == layout.shared_cache_dir / 'codex'


def test_path_layout_ensures_provider_shared_cache_manifest(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    layout = PathLayout(project_root)

    cache_dir = layout.ensure_provider_shared_cache_dir('codex', created_at='2026-05-11T00:00:00Z')
    manifest = json.loads((cache_dir / 'MANIFEST.json').read_text(encoding='utf-8'))

    assert cache_dir == layout.shared_cache_dir / 'codex'
    assert manifest['record_type'] == 'ccb_shared_cache_manifest'
    assert manifest['provider'] == 'codex'
    assert manifest['project_id'] == layout.project_id
    assert manifest['runtime_state_root'] == str(layout.runtime_state_root)
    assert manifest['entries'] == []


def test_path_layout_keeps_legacy_external_cache_read_only(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo'
    xdg_cache = tmp_path / 'xdg-cache'
    monkeypatch.setenv('XDG_CACHE_HOME', str(xdg_cache))
    layout = PathLayout(project_root)

    cache_dir = layout.provider_external_cache_dir('claude')

    assert cache_dir == xdg_cache / 'ccb' / 'projects' / layout.project_id[:16] / 'provider-cache' / 'claude'
    assert not cache_dir.exists()


def test_path_layout_ensures_user_provider_cache_manifest(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo'
    xdg_cache = tmp_path / 'xdg-cache'
    monkeypatch.setenv('XDG_CACHE_HOME', str(xdg_cache))
    layout = PathLayout(project_root)

    cache_dir = layout.ensure_provider_user_cache_dir('gemini', created_at='2026-07-23T00:00:00Z')
    manifest = json.loads((cache_dir / 'MANIFEST.json').read_text(encoding='utf-8'))

    assert cache_dir == xdg_cache / 'ccb' / 'provider-cache' / 'gemini'
    assert manifest['record_type'] == 'ccb_user_provider_cache_manifest'
    assert manifest['provider'] == 'gemini'
    assert manifest['scope'] == 'user'
    assert manifest['entries'] == []
    assert not (xdg_cache / 'ccb' / 'projects').exists()


def test_storage_summary_reports_legacy_and_user_provider_cache_boundaries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    xdg_cache = tmp_path / 'xdg-cache'
    monkeypatch.setenv('XDG_CACHE_HOME', str(xdg_cache))
    layout = PathLayout(project_root)
    _write(layout.provider_external_cache_dir('claude') / 'versions' / '2.1.218', 'legacy')
    _write(layout.provider_user_cache_dir('gemini') / 'npm' / '_cacache' / 'blob', 'shared')

    payload = summarize_storage(layout)

    assert payload['legacy_provider_cache_root'] == str(layout.external_provider_cache_root)
    assert payload['legacy_provider_cache_present'] is True
    assert payload['legacy_provider_cache_bytes'] >= len('legacy')
    assert payload['user_provider_cache_root'] == str(layout.user_provider_cache_root)
    assert payload['user_provider_cache_present'] is True
    assert payload['user_provider_cache_bytes'] is None
    assert payload['user_provider_cache_size_status'] == 'not_scanned_shared_scope'


def test_path_layout_rejects_noncanonical_shared_cache_provider(tmp_path: Path) -> None:
    layout = PathLayout(tmp_path / 'repo')

    for provider in ('Claude Code', 'claude', 'gemini'):
        try:
            layout.provider_shared_cache_dir(provider)
        except ValueError as exc:
            assert 'provider must be one of' in str(exc)
        else:
            raise AssertionError(f'expected {provider} shared cache to be rejected')

    for provider in ('claude', 'codex', 'Gemini CLI'):
        try:
            layout.provider_user_cache_dir(provider)
        except ValueError as exc:
            assert 'provider must be one of' in str(exc)
        else:
            raise AssertionError(f'expected {provider} user cache to be rejected')


def test_storage_summary_hides_shared_cache_root_when_drvfs_is_not_relocated(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    layout = PathLayout(project_root)
    layout.ccb_dir.mkdir(parents=True, exist_ok=True)
    object.__setattr__(
        layout,
        '_runtime_state_placement',
        RuntimeStatePlacement(
            anchor_path=layout.ccb_dir,
            effective_path=layout.ccb_dir,
            root_kind='project',
            relocation_reason=None,
            filesystem_hint='wsl_drvfs',
        ),
    )
    object.__setattr__(layout, '_state_root', layout.ccb_dir)

    payload = summarize_storage(layout)

    assert payload['shared_cache_root'] is None
    assert payload['shared_cache_root_usable'] is False
    assert payload['shared_cache_reason'] == 'wsl_drvfs_requires_runtime_relocation'


def test_path_layout_refuses_to_create_shared_cache_on_drvfs_without_relocation(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    layout = PathLayout(project_root)
    layout.ccb_dir.mkdir(parents=True, exist_ok=True)
    object.__setattr__(
        layout,
        '_runtime_state_placement',
        RuntimeStatePlacement(
            anchor_path=layout.ccb_dir,
            effective_path=layout.ccb_dir,
            root_kind='project',
            relocation_reason=None,
            filesystem_hint='wsl_drvfs',
        ),
    )
    object.__setattr__(layout, '_state_root', layout.ccb_dir)

    try:
        layout.ensure_provider_shared_cache_dir('codex')
    except RuntimeError as exc:
        assert 'requires relocated runtime state' in str(exc)
    else:
        raise AssertionError('expected unsafe drvfs shared-cache creation to fail')
