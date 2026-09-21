"""Keep the GitHub landing-page version aligned with packaged source."""
import json
from pathlib import Path
import re


def test_current_readme_badges_and_downloads_match_version():
    root = Path(__file__).resolve().parents[1]
    version = (root / 'VERSION').read_text().strip()
    assert json.loads((root / 'package.json').read_text())['version'] == version
    for readme in [root / 'README.md', *sorted((root / 'README').glob('*.md'))]:
        content = readme.read_text()
        badges = re.findall(r'img\.shields\.io/badge/version-([\d.]+)-', content)
        assert badges == [version], str(readme)
        summaries = re.findall(r'<summary><b>v([\d.]+)</b>', content)
        assert summaries and summaries[0] == version, str(readme)
        downloads = re.findall(r'releases/download/v([\d.]+)/ccb-mobile-v([\d.]+)\.apk', content)
        assert downloads and all(pair == (version, version) for pair in downloads), str(readme)
    notes = (root / 'docs' / 'releases' / f'v{version}.md').read_text()
    assert '## English' in notes and '## 中文' in notes
