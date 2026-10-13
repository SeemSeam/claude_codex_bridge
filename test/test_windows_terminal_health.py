from platforms.windows.terminal_health import classify, check_before_attach
from io import StringIO
import pytest


def evidence(version=(1, 24, 2607, 10001), signed=True):
    return dict(terminal='C:/WezTerm/wezterm.exe', exists=True, components=[
        dict(name=name, version=list(version), microsoft_signed=signed)
        for name in ['conpty.dll', 'OpenConsole.exe', 'x64\\OpenConsole.exe']])


def test_pair_classification_does_not_overclaim():
    assert classify(evidence())['status'] == 'verified-pair'
    assert classify(evidence((1, 30, 1, 1)))['status'] == 'newer-pair'
    assert classify(evidence((1, 18, 1, 1)))['status'] == 'known-risk'
    assert classify(evidence(signed=False))['status'] == 'unverified-signature'
    assert classify({})['status'] == 'unknown'
    data = evidence()
    data['components'][2]['version'] = [1, 0, 0, 0]
    assert classify(data)['status'] == 'incompatible'


@pytest.mark.parametrize('status', ['known-risk', 'incompatible', 'unverified-signature'])
def test_attach_rejects_bad_pair(monkeypatch, status):
    monkeypatch.setattr('platforms.windows.terminal_health.inspect_terminal',
                        lambda: dict(status=status, message='bad terminal'))
    with pytest.raises(RuntimeError, match='blocked attachment'):
        check_before_attach(StringIO())
