"""External stock watchdog: isolated credentials and no real network/subprocesses."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def watchdog(monkeypatch, tmp_path):
    # requests belongs to the external Hermes runtime, not the DaVinci API.
    monkeypatch.setitem(
        sys.modules, "requests", SimpleNamespace(get=Mock(side_effect=AssertionError("No network")))
    )
    monkeypatch.setattr(sys, "path", list(sys.path))
    source = Path(__file__).resolve().parents[4] / "scripts/hermes/bling_negative_stock_watch.py"
    spec = importlib.util.spec_from_file_location("stock_watch_test", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    profile = tmp_path / "threema-estoque.env"
    profile.write_text(
        'THREEMA_ESTOQUE_GATEWAY_ID="*DVESTIM"\n'
        'THREEMA_ESTOQUE_GATEWAY_SECRET="stock-test-secret"\n'
    )
    sender = tmp_path / "sender.py"
    sender.touch()
    monkeypatch.setattr(module, "THREEMA_ESTOQUE_ENV", profile)
    monkeypatch.setattr(module, "THREEMA_SEND", sender)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    monkeypatch.setattr(module.subprocess, "run", Mock())
    monkeypatch.setattr(module.requests, "get", Mock(side_effect=AssertionError("No network")))
    return module


def test_stock_sender_is_explicit_and_preserves_recipients(watchdog, monkeypatch):
    monkeypatch.setenv("THREEMA_ID", "*DVCLOG1")
    monkeypatch.setenv("THREEMA_SECRET", "legacy-test-secret")
    monkeypatch.setenv("THREEMA_PRIVKEY", "legacy-test-key")
    monkeypatch.setenv("THREEMA_PRIVKEY_ESTOQUE", "stale-test-key")
    recipients = ["TEST0001", "TEST0002"]

    watchdog.send_threema("stock alert", recipients, dry_run=False)

    calls = watchdog.subprocess.run.call_args_list
    assert len(calls) == len(recipients)
    for call, recipient in zip(calls, recipients, strict=True):
        command = call.args[0]
        assert command[2:] == ["--profile", "estoque", "--to", recipient, "--text", "stock alert"]
        env = call.kwargs["env"]
        assert env["THREEMA_ID_ESTOQUE"] == "*DVESTIM"
        assert env["THREEMA_SECRET_ESTOQUE"] == "stock-test-secret"  # noqa: S105 — fictitious
        assert env["THREEMA_PRIVKEY_ESTOQUE"] == ""
    assert watchdog.os.environ["THREEMA_ID"] == "*DVCLOG1"
    assert watchdog.os.environ["THREEMA_PRIVKEY_ESTOQUE"] == "stale-test-key"


@pytest.mark.parametrize(
    "profile",
    [
        None,
        "",
        'THREEMA_ESTOQUE_GATEWAY_ID="*DVESTIM"\n',
        'THREEMA_ESTOQUE_GATEWAY_SECRET="do-not-log-this"\n',
        'THREEMA_ESTOQUE_GATEWAY_ID="bad-id"\nTHREEMA_ESTOQUE_GATEWAY_SECRET="do-not-log-this"\n',
        'THREEMA_ESTOQUE_GATEWAY_ID="*DVESTIM"\nTHREEMA_ESTOQUE_GATEWAY_SECRET=do-not-log-this\n',
        'THREEMA_ESTOQUE_GATEWAY_ID="*DVESTIM"\nTHREEMA_ESTOQUE_GATEWAY_SECRET=""\n',
        'THREEMA_ESTOQUE_GATEWAY_ID="*DVESTIM"\nTHREEMA_ESTOQUE_GATEWAY_SECRET=123\n',
        'THREEMA_ESTOQUE_GATEWAY_ID="*DVESTIM"\nTHREEMA_ESTOQUE_GATEWAY_ID="*DVCLOG1"\n'
        'THREEMA_ESTOQUE_GATEWAY_SECRET="do-not-log-this"\n',
    ],
)
def test_missing_or_invalid_stock_config_never_falls_back(watchdog, monkeypatch, profile):
    monkeypatch.setenv("THREEMA_ID", "*DVCLOG1")
    monkeypatch.setenv("THREEMA_SECRET", "legacy-test-secret")
    if profile is None:
        watchdog.THREEMA_ESTOQUE_ENV.unlink()
    else:
        watchdog.THREEMA_ESTOQUE_ENV.write_text(profile)
    with pytest.raises(RuntimeError) as error:
        watchdog.send_threema("alert", ["TEST0001"], dry_run=False)
    assert "do-not-log-this" not in str(error.value)
    watchdog.subprocess.run.assert_not_called()


def test_quoted_secret_is_loaded_literally(watchdog):
    literal = 'test-$NOT_EXPANDED-"-\\'
    watchdog.THREEMA_ESTOQUE_ENV.write_text(
        'THREEMA_ESTOQUE_GATEWAY_ID="*DVESTIM"\n'
        + "THREEMA_ESTOQUE_GATEWAY_SECRET="
        + json.dumps(literal)
        + "\n"
    )
    assert watchdog.estoque_sender_env()["THREEMA_SECRET_ESTOQUE"] == literal


@pytest.fixture
def pipeline(watchdog, monkeypatch):
    monkeypatch.setattr(watchdog.sys, "argv", ["watchdog", "--recipients", "TEST0001,TEST0002"])
    monkeypatch.setattr(watchdog, "load_env_file", Mock())
    monkeypatch.setattr(watchdog, "db_connect", Mock())
    monkeypatch.setattr(watchdog, "get_bling_access_token", Mock(return_value="fake-token"))
    monkeypatch.setattr(watchdog, "load_candidates", Mock(return_value=[{"id": 1}]))
    monkeypatch.setattr(watchdog, "bling_stock_saldos", Mock(return_value=[]))
    negative = {
        "id": 1,
        "sku": "TEST.SKU",
        "nome": "test product",
        "saldo_virtual_bling": -1,
        "saldo_fisico_bling": 0,
    }
    monkeypatch.setattr(watchdog, "find_negatives", Mock(return_value=[negative]))
    monkeypatch.setattr(watchdog, "load_state", Mock(return_value={}))
    monkeypatch.setattr(watchdog, "save_state", Mock())
    return watchdog


def test_state_is_saved_only_after_success(pipeline):
    def delivered(*args, **kwargs):
        pipeline.save_state.assert_not_called()

    pipeline.subprocess.run.side_effect = delivered
    assert pipeline.main() == 0
    assert pipeline.subprocess.run.call_count == 2
    pipeline.save_state.assert_called_once()


def test_failed_send_does_not_suppress_next_retry(pipeline):
    pipeline.subprocess.run.side_effect = RuntimeError("simulated failure")
    with pytest.raises(RuntimeError, match="simulated failure"):
        pipeline.main()
    pipeline.save_state.assert_not_called()


def test_missing_credentials_do_not_suppress_next_retry(pipeline):
    pipeline.THREEMA_ESTOQUE_ENV.unlink()
    with pytest.raises(RuntimeError, match="Perfil Threema"):
        pipeline.main()
    pipeline.subprocess.run.assert_not_called()
    pipeline.save_state.assert_not_called()


def test_empty_recipients_do_not_mark_alert_as_delivered(pipeline):
    pipeline.sys.argv[-1] = ""
    with pytest.raises(RuntimeError, match="Nenhum destinatário"):
        pipeline.main()
    pipeline.subprocess.run.assert_not_called()
    pipeline.save_state.assert_not_called()


def test_partial_delivery_remains_eligible_for_retry(pipeline):
    pipeline.subprocess.run.side_effect = [None, RuntimeError("second recipient failed")]
    with pytest.raises(RuntimeError, match="second recipient"):
        pipeline.main()
    assert pipeline.subprocess.run.call_count == 2
    pipeline.save_state.assert_not_called()


def test_unchanged_negative_stock_does_not_send(pipeline):
    fingerprint = pipeline.fingerprint(pipeline.find_negatives.return_value)
    pipeline.load_state.return_value = {"fingerprint": fingerprint}
    assert pipeline.main() == 0
    pipeline.subprocess.run.assert_not_called()
    pipeline.save_state.assert_not_called()


@pytest.mark.parametrize("negative", [True, False])
def test_dry_run_never_sends_or_changes_state(pipeline, negative):
    pipeline.sys.argv.append("--dry-run")
    pipeline.THREEMA_ESTOQUE_ENV.unlink()
    if not negative:
        pipeline.find_negatives.return_value = []
    assert pipeline.main() == 0
    pipeline.subprocess.run.assert_not_called()
    pipeline.save_state.assert_not_called()


def test_recovered_stock_clears_previous_fingerprint_without_alert(pipeline):
    pipeline.find_negatives.return_value = []
    assert pipeline.main() == 0
    pipeline.subprocess.run.assert_not_called()
    pipeline.save_state.assert_called_once_with(pipeline.fingerprint([]), 0)
