import pytest

from aionuvo.exceptions import DeniedActionError
from aionuvo.safety import (
    DENIED_ACTIONS,
    check_action,
    check_http_url,
    check_post_url,
    is_read_only,
)


@pytest.mark.parametrize("action", sorted(DENIED_ACTIONS))
def test_denied_actions_raise(action, monkeypatch):
    monkeypatch.delenv("NUVO_I_KNOW", raising=False)
    with pytest.raises(DeniedActionError):
        check_action(action)


@pytest.mark.parametrize("action", ["GetVolume", "SetVolume", "GroupCreate"])
def test_allowed_actions_pass(action, monkeypatch):
    monkeypatch.delenv("NUVO_I_KNOW", raising=False)
    check_action(action)


def test_override(monkeypatch):
    monkeypatch.setenv("NUVO_I_KNOW", "1")
    check_action("RestoreFactoryDefaults")
    check_post_url("http://10.0.0.1/firmware.fcgi")


def test_override_requires_exact_value(monkeypatch):
    monkeypatch.setenv("NUVO_I_KNOW", "yes")
    with pytest.raises(DeniedActionError):
        check_action("SystemJoin")


@pytest.mark.parametrize(
    "url", ["http://10.0.0.1/firmware.fcgi", "http://10.0.0.1:80/FIRMWARE.fcgi/"]
)
def test_firmware_post_denied(url, monkeypatch):
    monkeypatch.delenv("NUVO_I_KNOW", raising=False)
    with pytest.raises(DeniedActionError):
        check_post_url(url)


def test_other_post_allowed(monkeypatch):
    monkeypatch.delenv("NUVO_I_KNOW", raising=False)
    check_post_url("http://10.0.0.1:52657/ZoneService/control")


def test_read_only():
    assert is_read_only("GetVolume")
    assert is_read_only("Browse")
    assert not is_read_only("SetVolume")
    assert not is_read_only("GroupCreate")


@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.1/api/setData?path=x&value=1",
        "http://10.0.0.1/update.fcgi",
        "http://10.0.0.1/diagnostics_execute.fcgi",
    ],
)
def test_web_ui_writes_denied(url, monkeypatch):
    monkeypatch.delenv("NUVO_I_KNOW", raising=False)
    with pytest.raises(DeniedActionError):
        check_http_url(url)


def test_web_ui_reads_allowed(monkeypatch):
    monkeypatch.delenv("NUVO_I_KNOW", raising=False)
    check_http_url("http://10.0.0.1/api/getData?path=x&roles=value")
    check_http_url("http://10.0.0.1/diagnostics.fcgi")
