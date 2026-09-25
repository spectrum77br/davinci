"""Configuration patch cannot overwrite unrelated settings or expose credentials."""

from __future__ import annotations

import importlib.util
import stat
from pathlib import Path

import pytest


@pytest.fixture
def config():
    source = Path(__file__).resolve().parents[4] / "scripts/configure_magalu_proxy.py"
    spec = importlib.util.spec_from_file_location("magalu_config_test", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def credentials():
    return {"username": "davinci-magalu", "password": "fake" * 12}


def test_check_is_read_only_and_apply_preserves_other_settings(config, credentials, tmp_path):
    env = tmp_path / ".env"
    original = b'SOME_SETTING="exact value"\nMAGALU_PROXY_URL=http://old\nOTHER={"a":1}\n'
    env.write_bytes(original)
    assert config.configure(env, credentials)
    assert env.read_bytes() == original
    assert list(tmp_path.iterdir()) == [env]
    assert config.configure(env, credentials, apply=True)
    assert env.read_bytes().startswith(b'SOME_SETTING="exact value"\n')
    assert env.read_bytes().endswith(b'OTHER={"a":1}\n')
    assert b"@magalu_proxy:13129" in env.read_bytes()
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    backups = list(tmp_path.glob(".env.magalu-backup-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original
    assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600
    assert not config.configure(env, credentials, apply=True)
    assert list(tmp_path.glob(".env.magalu-backup-*")) == backups


def test_missing_key_appends_without_losing_last_line(config, credentials):
    result = config.candidate(b"OTHER=value", credentials)
    assert result.startswith(b"OTHER=value\nMAGALU_PROXY_URL=")


def test_duplicate_key_fails_without_changes(config, credentials, tmp_path):
    env = tmp_path / ".env"
    original = b"MAGALU_PROXY_URL=a\nexport MAGALU_PROXY_URL=b\n"
    env.write_bytes(original)
    with pytest.raises(ValueError, match="Duplicate"):
        config.configure(env, credentials, apply=True)
    assert env.read_bytes() == original
    assert list(tmp_path.iterdir()) == [env]


@pytest.mark.parametrize(
    "credentials",
    [
        {},
        {"username": "other", "password": "fake" * 12},
        {"username": "davinci-magalu", "password": "short"},
        {"username": "davinci-magalu", "password": "x" * 32 + "\nOTHER=changed"},
        {"username": "davinci-magalu", "password": 123},
    ],
)
def test_invalid_credentials_do_not_mutate_files(config, credentials, tmp_path):
    env = tmp_path / ".env"
    env.write_bytes(b"OTHER=value\n")
    with pytest.raises(ValueError):
        config.configure(env, credentials, apply=True)
    assert env.read_bytes() == b"OTHER=value\n"
    assert list(tmp_path.iterdir()) == [env]


def test_symlink_not_replaced(config, credentials, tmp_path):
    target = tmp_path / "real-env"
    target.write_bytes(b"OTHER=value\n")
    env = tmp_path / ".env"
    env.symlink_to(target)
    with pytest.raises(ValueError, match="regular"):
        config.configure(env, credentials, apply=True)
    assert env.is_symlink()
    assert target.read_bytes() == b"OTHER=value\n"
