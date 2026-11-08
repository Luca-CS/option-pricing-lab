"""Settings (ADR-0012): one object from the environment, then the env file of the data folder, then the command line;
a start that cannot be safe or sane stops with a clear message."""

import pytest
from pydantic import ValidationError

from optlab.server.settings import Settings, default_data, repository_root


@pytest.fixture
def data(tmp_path, monkeypatch):
    for key in (
        "OPTLAB_DATA",
        "OPTLAB_STORE",
        "OPTLAB_LAKE",
        "OPTLAB_HOST",
        "OPTLAB_PORT",
        "OPTLAB_LAN",
        "OPTLAB_ENGINE",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OPTLAB_DATA", str(tmp_path))
    return tmp_path


def test_paths_default_under_the_data_folder(data):
    s = Settings.load()
    assert s.data == data.resolve() and s.store == data.resolve() / "store" and s.lake == data.resolve() / "lake"
    assert s.logs == data.resolve() / "logs" and s.backups == data.resolve() / "backups"
    assert s.results == repository_root() / "results"
    assert s.host == "127.0.0.1" and s.port == 8765 and not s.lan and s.engine == "auto"


def test_the_env_file_then_the_environment_then_the_command_line(data, monkeypatch):
    (data / "config").mkdir()
    (data / "config" / "optlab.env").write_text("OPTLAB_PORT=9001\nOPTLAB_ENGINE=cpu\n", encoding="utf-8")
    assert Settings.load().port == 9001 and Settings.load().engine == "cpu"
    monkeypatch.setenv("OPTLAB_PORT", "9002")
    assert Settings.load().port == 9002  # the environment wins over the file
    assert Settings.load(port=9003).port == 9003  # and the command line over both
    assert Settings.load(port=None).port == 9002  # an option left at its default changes nothing


def test_an_unknown_key_in_the_env_file_stops_the_start(data):
    (data / "config").mkdir()
    (data / "config" / "optlab.env").write_text("OPTLAB_PROT=9001\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="optlab_prot|OPTLAB_PROT|extra"):
        Settings.load()


def test_listening_beyond_this_machine_needs_the_lan_flag(data, monkeypatch):
    for host in ("127.0.0.1", "localhost", "::1", "[::1]"):
        assert Settings.load(host=host).host == host
    for host in ("0.0.0.0", "192.168.1.20", "optlab.local"):
        with pytest.raises(ValidationError, match="beyond this machine"):
            Settings.load(host=host)
    monkeypatch.setenv("OPTLAB_LAN", "true")
    assert Settings.load(host="0.0.0.0").lan


def test_the_development_default_is_the_data_folder_beside_the_main_checkout(monkeypatch):
    monkeypatch.delenv("OPTLAB_DATA", raising=False)
    d = default_data()
    repo = repository_root()
    assert repo is not None
    assert d.name in ("optlab-data", "data") and (d.name == "data" or d.parent.is_dir())
    assert Settings.load().data == d.resolve()
