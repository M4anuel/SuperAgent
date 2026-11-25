"""Configuration recovery preserves evidence and propagates storage failures."""
import json
from pathlib import Path

import pytest

from superagent.config import DEFAULT_CONFIG, SuperagentConfig


@pytest.mark.parametrize("contents", [b'{"projects":', b'\xff\xfe', b'[]', b'null'])
def test_corrupt_config_is_preserved_before_defaults_are_saved(tmp_path, contents):
    config_file = tmp_path / "config.json"
    config_file.write_bytes(contents)

    with pytest.warns(RuntimeWarning, match="Invalid configuration preserved at") as caught:
        config = SuperagentConfig(tmp_path)

    backups = list(tmp_path.glob("config.json.corrupt-*.bak"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == contents
    assert str(backups[0]) in str(caught[0].message)
    assert config.list_projects() == []
    assert json.loads(config_file.read_text(encoding="utf-8")) == DEFAULT_CONFIG
    assert SuperagentConfig(tmp_path)._data == DEFAULT_CONFIG


def test_repeated_recovery_preserves_each_original(tmp_path):
    config_file = tmp_path / "config.json"
    for contents in [b'{"first":', b'{"second":']:
        config_file.write_bytes(contents)
        with pytest.warns(RuntimeWarning):
            SuperagentConfig(tmp_path)

    assert {p.read_bytes() for p in tmp_path.glob("*.bak")} == {
        b'{"first":', b'{"second":',
    }


def test_valid_project_registry_is_not_recovered(tmp_path):
    data = {"projects": {"repo": {"name": "repo", "path": str(tmp_path)}},
            "active_project": "repo", "custom_setting": "preserved"}
    contents = json.dumps(data).encode("utf-8")
    config_file = tmp_path / "config.json"
    config_file.write_bytes(contents)

    config = SuperagentConfig(tmp_path)

    assert config.active_project.name == "repo"
    assert config._data == data
    assert config_file.read_bytes() == contents
    assert not list(tmp_path.glob("*.bak"))


def test_read_failure_does_not_overwrite_config(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    contents = b'{"projects": {}}'
    config_file.write_bytes(contents)
    real_open = open

    def denied_open(file, *args, **kwargs):
        if Path(file) == config_file:
            raise PermissionError("read denied")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", denied_open)
    with pytest.raises(PermissionError, match="read denied"):
        SuperagentConfig(tmp_path)
    assert config_file.read_bytes() == contents
    assert not list(tmp_path.glob("*.bak"))


@pytest.mark.parametrize("failure", ["create", "fsync", "replace"])
def test_recovery_failure_keeps_original_and_surfaces_error(tmp_path, monkeypatch, failure):
    config_file = tmp_path / "config.json"
    contents = b'{"projects":'
    config_file.write_bytes(contents)

    def fail(*args, **kwargs):
        raise OSError("storage unavailable")

    target = {"create": "tempfile.NamedTemporaryFile", "fsync": "os.fsync",
              "replace": "os.replace"}[failure]
    monkeypatch.setattr(f"superagent.config.{target}", fail)

    with pytest.raises(OSError, match="storage unavailable"):
        SuperagentConfig(tmp_path)

    assert config_file.read_bytes() == contents
    backups = list(tmp_path.glob("*.bak"))
    if failure == "replace":
        assert len(backups) == 1
        assert backups[0].read_bytes() == contents
    else:
        assert backups == []
    assert not list(tmp_path.glob("*.tmp.*"))
