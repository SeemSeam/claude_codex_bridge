from __future__ import annotations

import json
from pathlib import Path

from agents.models import ProviderProfileSpec
from provider_backends.qoder.skills import materialize_qoder_skills


def test_qoder_skills_project_into_config_home_and_are_idempotent(tmp_path: Path) -> None:
    home = tmp_path / 'managed-home'
    provider_skill = home / 'skills' / 'provider-help' / 'SKILL.md'
    provider_skill.parent.mkdir(parents=True)
    provider_skill.write_text('provider help\n', encoding='utf-8')

    active = materialize_qoder_skills(home, profile=ProviderProfileSpec(inherit_skills=True))
    repeated = materialize_qoder_skills(home, profile=ProviderProfileSpec(inherit_skills=True))

    assert active == ('ask', 'ccb-clear')
    assert repeated == active
    assert (home / 'skills' / 'ask' / 'SKILL.md').is_file()
    assert (home / 'skills' / 'ccb-clear' / 'SKILL.md').is_file()
    assert (home / 'skills' / 'ask.ccb-projection.json').is_file()
    assert (home / 'skills' / 'ccb-clear.ccb-projection.json').is_file()
    assert provider_skill.read_text(encoding='utf-8') == 'provider help\n'

    disabled = materialize_qoder_skills(home, profile=ProviderProfileSpec(inherit_skills=False))

    assert disabled == ()
    assert not (home / 'skills' / 'ask').exists()
    assert not (home / 'skills' / 'ccb-clear').exists()
    assert provider_skill.read_text(encoding='utf-8') == 'provider help\n'


def test_qoder_skills_preserve_unmarked_user_owned_conflicts(tmp_path: Path) -> None:
    home = tmp_path / 'managed-home'
    conflict = home / 'skills' / 'ask' / 'SKILL.md'
    conflict.parent.mkdir(parents=True)
    conflict.write_text('user owned ask\n', encoding='utf-8')

    active = materialize_qoder_skills(home, profile=ProviderProfileSpec(inherit_skills=True))

    assert active == ('ccb-clear',)
    assert conflict.read_text(encoding='utf-8') == 'user owned ask\n'
    assert not (home / 'skills' / 'ask.ccb-projection.json').exists()
    marker = home / 'skills' / 'ccb-clear.ccb-projection.json'
    payload = json.loads(marker.read_text(encoding='utf-8'))
    assert payload['label'] == 'qoder-ccb-skill:ccb-clear'
