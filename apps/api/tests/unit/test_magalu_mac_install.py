"""Instalação e túnel isolados: arquivos temporários, sem launchctl/SSH reais."""

import errno
import importlib.util
import json
import os
import plistlib
import socket
import stat
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[4] / "scripts"


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


installer = _load("magalu_mac_installer", "install_magalu_mac_proxy.py")
tunnel = _load("magalu_mac_tunnel_test", "magalu_mac_tunnel.py")
remote = {"__name__": "magalu_remote_test"}
exec(compile(tunnel.REMOTE_PREPARE_SCRIPT, "magalu_remote_prepare", "exec"), remote)  # noqa: S102
watcher = {"__name__": "magalu_watcher_test"}
exec(compile(tunnel.REMOTE_WATCH_SCRIPT, "magalu_remote_watch", "exec"), watcher)  # noqa: S102


@pytest.fixture
def socket_dir():
    # O caminho padrão do pytest no macOS ultrapassa o limite de AF_UNIX.
    with tempfile.TemporaryDirectory(prefix="magalu-", dir="/tmp") as directory:
        yield Path(directory).resolve()


@pytest.fixture
def installation(tmp_path, monkeypatch):
    source = tmp_path / "sources"
    source.mkdir()
    for filename in ("magalu_mac_proxy.py", "magalu_mac_tunnel.py"):
        (source / filename).write_text("# fixture " + filename + "\n", encoding="utf-8")
    home = tmp_path / "home"
    calls = []

    def launchctl(command, **kwargs):
        assert command[0] == "/bin/launchctl"
        assert kwargs == {"capture_output": True, "text": True, "check": False}
        calls.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(installer.subprocess, "run", launchctl)
    return SimpleNamespace(home=home, source=source, calls=calls)


def _snapshot(root):
    return {
        str(path.relative_to(root)): (
            stat.S_IMODE(path.lstat().st_mode),
            path.lstat().st_mtime_ns,
            path.read_bytes() if path.is_file() and not path.is_symlink() else None,
        )
        for path in root.rglob("*")
    }


def _args(installation, mode=None):
    return ([mode] if mode else []) + [
        "--home", str(installation.home), "--source-dir", str(installation.source),
    ]


def test_default_check_does_not_write_or_run_launchctl(installation, tmp_path):
    before = _snapshot(tmp_path)
    assert installer.main(_args(installation)) == 1
    assert _snapshot(tmp_path) == before
    assert installation.calls == []
    assert not installation.home.exists()


def test_apply_installs_private_files_and_ordered_jobs(installation, monkeypatch, capsys):
    generated = []

    def token(length):
        generated.append(length)
        return "FAKE_PRIVATE_PROXY_PASSWORD"

    monkeypatch.setattr(installer.secrets, "token_urlsafe", token)
    assert installer.main(_args(installation, "--apply")) == 0
    plan = installer.plan_install(installation.home, installation.source)
    assert plan["issues"] == []
    assert generated == [32]
    assert stat.S_IMODE(plan["runtime"].stat().st_mode) == 0o700
    assert plan["credentials"] == {
        "username": "davinci-magalu", "password": "FAKE_PRIVATE_PROXY_PASSWORD",
    }
    for path in [plan["credentials_path"]] + list(plan["artifacts"]) + plan["logs"]:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    for stem in ("proxy", "tunnel"):
        assert (plan["runtime"] / (stem + ".py")).read_bytes() == (
            installation.source / ("magalu_mac_" + stem + ".py")
        ).read_bytes()
    for label in installer.LABELS:
        plist = plistlib.loads((plan["agents"] / (label + ".plist")).read_bytes())
        assert plist["RunAtLoad"] is True and plist["KeepAlive"] is True
        assert plist["ThrottleInterval"] == 10
        assert plist["Umask"] == 0o077
        assert plist["ProgramArguments"][0] == "/usr/bin/python3"
        assert Path(plist["StandardOutPath"]).parent == plan["runtime"]
        assert Path(plist["StandardErrorPath"]).parent == plan["runtime"]
        assert "FAKE_PRIVATE_PROXY_PASSWORD" not in str(plist)
    proxy = plistlib.loads((plan["agents"] / (installer.LABELS[0] + ".plist")).read_bytes())
    assert proxy["ProgramArguments"][2:] == [
        "--credentials", str(plan["credentials_path"]), "--port", "13129",
    ]
    domain = "gui/" + str(os.getuid())
    assert installation.calls == [
        [installer.LAUNCHCTL, "bootout", domain + "/" + installer.LABELS[1]],
        [installer.LAUNCHCTL, "bootout", domain + "/" + installer.LABELS[0]],
        [installer.LAUNCHCTL, "bootstrap", domain,
         str(plan["agents"] / (installer.LABELS[0] + ".plist"))],
        [installer.LAUNCHCTL, "kickstart", domain + "/" + installer.LABELS[0]],
        [installer.LAUNCHCTL, "bootstrap", domain,
         str(plan["agents"] / (installer.LABELS[1] + ".plist"))],
        [installer.LAUNCHCTL, "kickstart", domain + "/" + installer.LABELS[1]],
    ]
    assert "FAKE_PRIVATE_PROXY_PASSWORD" not in capsys.readouterr().out


def test_reinstall_preserves_credentials_logs_and_files(installation, monkeypatch, tmp_path):
    assert installer.main(_args(installation, "--apply")) == 0
    plan = installer.plan_install(installation.home, installation.source)
    plan["logs"][0].write_text("previous log\n", encoding="utf-8")
    before = _snapshot(tmp_path)
    monkeypatch.setattr(
        installer.secrets, "token_urlsafe", lambda size: pytest.fail("rotated credentials"),
    )
    assert installer.main(_args(installation, "--check")) == 0
    assert _snapshot(tmp_path) == before
    assert len(installation.calls) == 6
    assert installer.main(_args(installation, "--apply")) == 0
    assert _snapshot(tmp_path) == before
    assert len(installation.calls) == 12


@pytest.mark.parametrize("body,mode", [
    ("{invalid PRIVATE_VALUE", 0o600),
    (json.dumps({"username": "wrong", "password": "PRIVATE_VALUE"}), 0o600),
    (json.dumps({"username": "davinci-magalu", "password": "PRIVATE_VALUE with space"}), 0o600),
    (json.dumps({"username": "davinci-magalu", "password": "PRIVATE_VALUE"}), 0o644),
    (json.dumps({"username": "davinci-magalu", "password": "PRIVATE_VALUE" * 100}), 0o600),
])
def test_bad_credentials_rejected_before_any_writes(
    installation, tmp_path, body, mode, capsys,
):
    plan = installer.plan_install(installation.home, installation.source)
    plan["runtime"].mkdir(parents=True)
    plan["credentials_path"].write_text(body, encoding="utf-8")
    plan["credentials_path"].chmod(mode)
    before = _snapshot(tmp_path)
    assert installer.main(_args(installation, "--apply")) == 2
    assert _snapshot(tmp_path) == before
    assert installation.calls == []
    output = capsys.readouterr()
    assert "PRIVATE_VALUE" not in output.out + output.err


@pytest.mark.parametrize("kind", ["runtime", "credentials", "script", "plist", "log"])
def test_symlink_destination_rejected_before_writes(installation, tmp_path, kind):
    plan = installer.plan_install(installation.home, installation.source)
    destination = {
        "runtime": plan["runtime"],
        "credentials": plan["credentials_path"],
        "script": plan["runtime"] / "proxy.py",
        "plist": next(path for path in plan["artifacts"] if path.suffix == ".plist"),
        "log": plan["logs"][0],
    }[kind]
    outside = tmp_path / "outside"
    outside.mkdir()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.symlink_to(outside)
    before = _snapshot(tmp_path)
    assert installer.main(_args(installation, "--apply")) == 2
    assert _snapshot(tmp_path) == before
    assert installation.calls == []


def test_invalid_source_rejected_before_writes(installation, tmp_path):
    (installation.source / "magalu_mac_proxy.py").write_text("def broken(\n", encoding="utf-8")
    before = _snapshot(tmp_path)
    assert installer.main(_args(installation, "--apply")) == 2
    assert _snapshot(tmp_path) == before
    assert installation.calls == []


def test_existing_job_not_loaded_is_accepted(installation, monkeypatch):
    monkeypatch.setattr(installer.subprocess, "run", lambda command, **kwargs:
                        SimpleNamespace(returncode=3 if command[1] == "bootout" else 0))
    assert installer.main(_args(installation, "--apply")) == 0


def test_activation_failure_is_reported(installation, monkeypatch, capsys):
    monkeypatch.setattr(installer.subprocess, "run", lambda command, **kwargs:
                        SimpleNamespace(returncode=5, stderr="PRIVATE_SUBPROCESS_OUTPUT"))
    assert installer.main(_args(installation, "--apply")) == 2
    assert "PRIVATE_SUBPROCESS_OUTPUT" not in capsys.readouterr().err


def test_kickstart_failure_is_reported_before_starting_next_job(installation, monkeypatch, capsys):
    calls = []

    def launchctl(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=5 if command[1] == "kickstart" else 0)

    monkeypatch.setattr(installer.subprocess, "run", launchctl)
    assert installer.main(_args(installation, "--apply")) == 2
    assert calls[-1] == [
        installer.LAUNCHCTL, "kickstart", "gui/" + str(os.getuid()) + "/" + installer.LABELS[0],
    ]
    assert sum(command[1] == "bootstrap" for command in calls) == 1
    assert "Não foi possível ativar o serviço " + installer.LABELS[0] in capsys.readouterr().err


def test_socket_preparation_creates_private_directory_and_removes_only_stale(socket_dir):
    path = socket_dir / "remote" / "magalu.sock"
    assert remote["prepare_socket"](path) == "ausente"
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stale:
        stale.bind(str(path))
    assert path.exists()
    assert remote["prepare_socket"](path) == "obsoleto removido"
    assert not path.exists()


def test_active_socket_is_never_removed(socket_dir):
    path = socket_dir / "magalu.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as active:
        active.bind(str(path))
        active.listen(1)
        with pytest.raises(RuntimeError, match="listener ativo"):
            remote["prepare_socket"](path)
        assert path.exists()


@pytest.mark.parametrize("kind", ["file", "symlink", "directory"])
def test_remote_non_socket_is_never_removed(tmp_path, kind):
    path = tmp_path / "magalu.sock"
    if kind == "file":
        path.write_text("preserve", encoding="utf-8")
    elif kind == "symlink":
        path.symlink_to(tmp_path / "elsewhere")
    else:
        path.mkdir()
    with pytest.raises(RuntimeError, match="não é um socket"):
        remote["prepare_socket"](path)
    assert path.exists() or path.is_symlink()


def test_probe_timeout_preserves_socket(socket_dir, monkeypatch):
    path = socket_dir / "magalu.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stale:
        stale.bind(str(path))

    class Probe:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def settimeout(self, value):
            assert value == 2.0

        def connect(self, destination):
            raise OSError(errno.ETIMEDOUT, "timeout")

    monkeypatch.setattr(remote["socket"], "socket", lambda *args: Probe())
    with pytest.raises(RuntimeError, match="preservado"):
        remote["prepare_socket"](path)
    assert path.exists()


def test_tunnel_prepares_then_starts_ssh_with_fixed_destinations(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "argv", ["tunnel.py"])

    def prepare(command, **kwargs):
        calls.append(("prepare", command))
        assert kwargs["input"] == tunnel.REMOTE_PREPARE_SCRIPT
        assert kwargs["timeout"] == 30
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(tunnel.subprocess, "run", prepare)
    def run_tunnel(command):
        calls.append(("tunnel", command))
        raise SystemExit(0)

    monkeypatch.setattr(tunnel, "_run_tunnel", run_tunnel)
    with pytest.raises(SystemExit) as stopped:
        tunnel.main()
    assert stopped.value.code == 0
    assert calls[0][1][-4:] == ["davinci-prod", "/usr/bin/python3", "-", tunnel.REMOTE_SOCKET]
    command = calls[1][1]
    assert command[0] == "/usr/bin/ssh" and command[-2] == "davinci-prod"
    assert "-N" not in command and "-T" in command
    assert command[-1].startswith("exec /usr/bin/python3 -c ")
    for option in (
        "BatchMode=yes", "ClearAllForwardings=no", "ExitOnForwardFailure=yes",
        "ServerAliveInterval=30", "ServerAliveCountMax=3",
    ):
        assert option in command
    assert "ClearAllForwardings=no" not in calls[0][1]
    assert command[command.index("-R") + 1] == tunnel.REMOTE_SOCKET + ":127.0.0.1:13129"
    assert not any("GatewayPorts" in argument or "BindUnlink" in argument for argument in command)


def test_tunnel_does_not_launch_when_socket_preparation_fails(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["tunnel.py"])
    monkeypatch.setattr(tunnel.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=1, stderr="socket ativo; preservado"))
    monkeypatch.setattr(tunnel, "_run_tunnel", lambda *args: pytest.fail("must not start tunnel"))
    monkeypatch.setattr(tunnel.time, "sleep", lambda seconds: tunnel._stop(15, None))
    with pytest.raises(SystemExit) as stopped:
        tunnel.main()
    assert stopped.value.code == 143


def test_wrapper_retries_preparation_and_ssh_exit_until_stopped(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["tunnel.py"])
    events = []
    preparation_codes = iter([1, 0, 0])
    tunnel_codes = iter([255, 143])

    def prepare(*args, **kwargs):
        status = next(preparation_codes)
        events.append(("prepare", status))
        return SimpleNamespace(returncode=status, stderr="")

    def run_tunnel(command):
        status = next(tunnel_codes)
        events.append(("ssh", status))
        if status == 143:
            tunnel._stop(15, None)
        return status

    monkeypatch.setattr(tunnel.subprocess, "run", prepare)
    monkeypatch.setattr(tunnel, "_run_tunnel", run_tunnel)
    monkeypatch.setattr(tunnel.time, "sleep", lambda seconds: events.append(("sleep", seconds)))
    with pytest.raises(SystemExit) as stopped:
        tunnel.main()
    assert stopped.value.code == 143
    assert events == [
        ("prepare", 1), ("sleep", 10),
        ("prepare", 0), ("ssh", 255), ("sleep", 10),
        ("prepare", 0), ("ssh", 143),
    ]


@pytest.mark.parametrize("error", [
    tunnel.subprocess.TimeoutExpired("ssh", 30), OSError("simulated connection failure"),
])
def test_wrapper_retries_preparation_exception(monkeypatch, error):
    monkeypatch.setattr(sys, "argv", ["tunnel.py"])
    attempts = []
    waits = []

    def prepare(*args, **kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            raise error
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(tunnel.subprocess, "run", prepare)
    monkeypatch.setattr(tunnel, "_run_tunnel", lambda command: tunnel._stop(15, None))
    monkeypatch.setattr(tunnel.time, "sleep", waits.append)
    with pytest.raises(SystemExit) as stopped:
        tunnel.main()
    assert stopped.value.code == 143
    assert len(attempts) == 2 and waits == [10]


@pytest.mark.parametrize("reason", ["eof", "timeout", "heartbeats_then_timeout"])
def test_remote_watch_removes_own_socket_after_heartbeat_stops(socket_dir, monkeypatch, reason):
    path = socket_dir / "watch.sock"
    events = iter([[42], [42], []] if reason == "heartbeats_then_timeout" else
                  [[42]] if reason == "eof" else [[]])
    reads = []

    def read(fd, count):
        reads.append((fd, count))
        return b"" if reason == "eof" else b"."

    def ready(inputs, writes, errors, timeout):
        assert inputs == [42] and timeout == 75
        return next(events), [], []

    monkeypatch.setattr(watcher["select"], "select", ready)
    monkeypatch.setattr(watcher["os"], "read", read)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as active:
        active.bind(str(path))
        active.listen(1)
        watcher["watch_socket"](path, heartbeat_fd=42)
        assert not path.exists()
    assert len(reads) == {"eof": 1, "timeout": 0, "heartbeats_then_timeout": 2}[reason]


def test_remote_watch_never_removes_replacement_socket(socket_dir, monkeypatch):
    path = socket_dir / "watch.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as old:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as replacement:
            old.bind(str(path))

            def ready(*args):
                path.unlink()
                replacement.bind(str(path))
                replacement.listen(1)
                return [], [], []

            monkeypatch.setattr(watcher["select"], "select", ready)
            watcher["watch_socket"](path)
            assert path.exists()


def test_remote_watch_rejects_non_socket_without_removing_it(tmp_path):
    path = tmp_path / "watch.sock"
    path.write_text("preserve", encoding="utf-8")
    with pytest.raises(RuntimeError, match="não é um socket"):
        watcher["watch_socket"](path)
    assert path.read_text(encoding="utf-8") == "preserve"


class HeartbeatStream:
    def __init__(self):
        self.writes = []
        self.closed = False

    def write(self, value):
        self.writes.append(value)

    def flush(self):
        pass

    def close(self):
        self.closed = True


def test_local_wrapper_sends_heartbeat_and_observes_child_exit(monkeypatch):
    class Child:
        def __init__(self):
            self.stdin = HeartbeatStream()
            self.returncode = None
            self.waits = []

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            self.waits.append(timeout)
            if len(self.waits) == 1:
                raise tunnel.subprocess.TimeoutExpired("ssh", timeout)
            self.returncode = 7
            return 7

    child = Child()
    monkeypatch.setattr(tunnel.subprocess, "Popen", lambda command, stdin: child)
    assert tunnel._run_tunnel(["fake-ssh"]) == 7
    assert child.stdin.writes == [b".", b"."]
    assert child.stdin.closed is True
    assert child.waits == [15, 15]


def test_local_wrapper_cleans_child_on_sigterm_even_if_terminate_times_out(monkeypatch):
    class Child:
        def __init__(self):
            self.stdin = HeartbeatStream()
            self.returncode = None
            self.terminated = False
            self.killed = False

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            if timeout == 15:
                tunnel._stop(15, None)
            if not self.killed:
                raise tunnel.subprocess.TimeoutExpired("ssh", timeout)
            return self.returncode

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True
            self.returncode = -9

    child = Child()
    monkeypatch.setattr(tunnel.subprocess, "Popen", lambda command, stdin: child)
    with pytest.raises(SystemExit) as result:
        tunnel._run_tunnel(["fake-ssh"])
    assert result.value.code == 143
    assert child.stdin.closed and child.terminated and child.killed
