import os
import sys

import pytest

from app import config, startup


def test_config_defaults_when_there_is_no_file(tmp_path):
    settings = config.load(tmp_path / "none.json")
    assert settings["model"] == "llama3.2:3b" and settings["keep_alive"] == "10m"


def test_config_file_overrides_only_what_it_names(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"keep_alive": "24h"}')
    assert config.get("keep_alive", path) == "24h" and config.get("model", path) == "llama3.2:3b"


def test_a_broken_config_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{ not json")
    assert config.get("keep_alive", path) == "10m"


def test_keep_alive_is_passed_to_ollama_in_its_own_format(tmp_path):
    path = tmp_path / "config.json"
    for written, expected in [("10m", "10m"), ("24h", "24h"), ("-1", -1), ("0", 0), ("600", 600)]:
        path.write_text('{"keep_alive": "%s"}' % written)
        assert config.keep_alive_value(path) == expected


def test_shortcut_script_quotes_awkward_paths():
    script = startup.shortcut_script(r"C:\Users\O'Neil\Startup\ATLAS scheduler.lnk",
                                     r"C:\Python 3\pythonw.exe", "-m app.scheduler", r"C:\My Projects\cal")
    assert "O''Neil" in script and "CreateShortcut(" in script and "WindowStyle = 7" in script
    assert "'-m app.scheduler'" in script and "'C:\\My Projects\\cal'" in script


def test_pythonw_is_preferred_when_it_exists(tmp_path, monkeypatch):
    fake = tmp_path / "python.exe"
    fake.write_text("")
    monkeypatch.setattr(sys, "executable", str(fake))
    assert startup.pythonw() == fake                          # no pythonw next to it
    (tmp_path / "pythonw.exe").write_text("")
    assert startup.pythonw() == tmp_path / "pythonw.exe"


@pytest.mark.skipif(os.name == "nt", reason="checks the message shown on other systems")
def test_the_windows_helpers_refuse_politely_elsewhere():
    with pytest.raises(SystemExit):
        startup.install()