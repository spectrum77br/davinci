"""Isolated installer checks; no application config, network or production files."""

import hashlib
import importlib.util
import stat
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[4] / "scripts/hermes/install_stock_threema.py"
SPEC = importlib.util.spec_from_file_location("hermes_stock_installer", SOURCE)
installer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = installer
SPEC.loader.exec_module(installer)


@pytest.fixture
def installation(tmp_path):
    source = tmp_path / "repository/watchdog.py"
    source.parent.mkdir()
    source.write_text("# new watchdog\n")
    environment = tmp_path / "production.env"
    environment.write_text(
        "THREEMA_ESTOQUE_GATEWAY_ID=*DVESTIM\n"
        "THREEMA_ESTOQUE_GATEWAY_SECRET='test secret # literal $NOT_EXPANDED'\n"
        'OTHER_JSON={"ignored":true}\n'
    )
    script = tmp_path / "hermes/scripts/watchdog.py"
    script.parent.mkdir(parents=True)
    script.write_text("# original watchdog\n")
    args = installer.parser().parse_args(
        [
            "--source",
            str(source),
            "--env-file",
            str(environment),
            "--script",
            str(script),
            "--profile",
            str(tmp_path / "hermes/stock.env"),
            "--backup-dir",
            str(tmp_path / "backups"),
            "--baseline-sha256",
            hashlib.sha256(script.read_bytes()).hexdigest(),
        ]
    )
    return args


def snapshot(root):
    return {
        str(path.relative_to(root)): (
            path.read_bytes(),
            stat.S_IMODE(path.stat().st_mode),
            path.stat().st_mtime_ns,
        )
        for path in root.rglob("*")
        if path.is_file()
    }


def test_check_has_no_mutations(installation, tmp_path):
    before = snapshot(tmp_path)
    directories = sorted(str(path) for path in tmp_path.rglob("*") if path.is_dir())
    changes = installer.plan_install(installation)
    assert [change.path for change in changes] == [installation.profile, installation.script]
    assert snapshot(tmp_path) == before
    assert sorted(str(path) for path in tmp_path.rglob("*") if path.is_dir()) == directories
    assert not installation.backup_dir.exists()
    assert not installer.parser().parse_args([]).apply


def test_apply_installs_profile_first_and_keeps_private_backups(installation, monkeypatch):
    installation.profile.write_text("old profile\n")
    installation.profile.chmod(0o644)
    original_script = installation.script.read_bytes()
    writes = []
    real_write = installer._atomic_write

    def observed_write(path, content, mode):
        if path == installation.script:
            assert installer.read_stock_profile(
                installation.profile
            ) == installer.read_stock_profile(installation.env_file)
        writes.append(path)
        real_write(path, content, mode)

    monkeypatch.setattr(installer, "_atomic_write", observed_write)
    installer.apply_install(installation, installer.plan_install(installation))
    assert writes == [installation.profile, installation.script]
    assert installation.script.read_bytes() == installation.source.read_bytes()
    assert stat.S_IMODE(installation.profile.stat().st_mode) == 0o600
    assert stat.S_IMODE(installation.script.stat().st_mode) == 0o644
    backups = list(installation.backup_dir.iterdir())
    assert len(backups) == 2
    assert {path.read_bytes() for path in backups} == {b"old profile\n", original_script}
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in backups)
    assert stat.S_IMODE(installation.backup_dir.stat().st_mode) == 0o700
    assert b"$NOT_EXPANDED" in installation.profile.read_bytes()
    assert b"OTHER_JSON" not in installation.profile.read_bytes()


@pytest.mark.parametrize(
    "body",
    [
        "THREEMA_ESTOQUE_GATEWAY_ID=*DVESTIM\n",
        "THREEMA_ESTOQUE_GATEWAY_SECRET=secret\n",
        "THREEMA_ESTOQUE_GATEWAY_ID=*DAVINCC\nTHREEMA_ESTOQUE_GATEWAY_SECRET=secret\n",
        "THREEMA_ESTOQUE_GATEWAY_ID=DVESTIM\nTHREEMA_ESTOQUE_GATEWAY_SECRET=secret\n",
        'THREEMA_ESTOQUE_GATEWAY_ID=*DVESTIM\nTHREEMA_ESTOQUE_GATEWAY_SECRET="secret\\nnewline"\n',
        'THREEMA_ESTOQUE_GATEWAY_ID=*DVESTIM\nTHREEMA_ESTOQUE_GATEWAY_SECRET="not closed\n',
        "THREEMA_ESTOQUE_GATEWAY_ID=*DVESTIM\n"
        "THREEMA_ESTOQUE_GATEWAY_SECRET=one\nTHREEMA_ESTOQUE_GATEWAY_SECRET=two\n",
    ],
)
def test_invalid_configuration_is_rejected_without_writes(installation, tmp_path, body):
    installation.env_file.write_text(body)
    before = snapshot(tmp_path)
    with pytest.raises(installer.InstallError):
        installer.plan_install(installation)
    assert snapshot(tmp_path) == before
    assert not installation.profile.exists()
    assert not installation.backup_dir.exists()


def test_drift_rejected_before_writing_profile(installation, tmp_path):
    installation.script.write_text("# unknown edit\n")
    before = snapshot(tmp_path)
    with pytest.raises(installer.InstallError, match="changed outside"):
        installer.plan_install(installation)
    assert snapshot(tmp_path) == before
    assert not installation.profile.exists()


def test_drift_between_check_and_apply_is_rejected(installation):
    changes = installer.plan_install(installation)
    installation.script.write_text("# concurrent edit\n")
    with pytest.raises(installer.InstallError):
        installer.apply_install(installation, changes)
    assert not installation.profile.exists()
    assert not installation.backup_dir.exists()


def test_second_apply_is_idempotent(installation, tmp_path):
    installer.apply_install(installation, installer.plan_install(installation))
    before = snapshot(tmp_path)
    assert installer.plan_install(installation) == []
    installer.apply_install(installation, [])
    assert snapshot(tmp_path) == before


def test_dotenv_quotes_comments_and_literal_dollar(installation):
    installation.env_file.write_text(
        'export THREEMA_ESTOQUE_GATEWAY_ID = "*DVESTIM" # comment\n'
        'THREEMA_ESTOQUE_GATEWAY_SECRET="test \\"quote\\" '
        '\\\\ path ${LITERAL} # retained" # comment\n'
    )
    profile = installer.read_stock_profile(installation.env_file)
    installation.profile.write_bytes(profile)
    assert installer.read_stock_profile(installation.profile) == profile
    assert b"${LITERAL}" in profile
    assert b"# retained" in profile
    assert b"# comment" not in profile


def test_error_output_does_not_include_secret(installation, capsys):
    installation.env_file.write_text(
        "THREEMA_ESTOQUE_GATEWAY_ID=*DAVINCC\nTHREEMA_ESTOQUE_GATEWAY_SECRET=DO_NOT_PRINT_THIS_SECRET\n"
    )
    assert (
        installer.main(
            [
                "--source",
                str(installation.source),
                "--env-file",
                str(installation.env_file),
                "--script",
                str(installation.script),
                "--profile",
                str(installation.profile),
                "--baseline-sha256",
                installation.baseline_sha256,
            ]
        )
        == 1
    )
    assert "DO_NOT_PRINT_THIS_SECRET" not in capsys.readouterr().out
