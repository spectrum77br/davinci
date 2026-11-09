from types import SimpleNamespace

import pytest
from test_channels import event, routes
from test_core import balance, run

from app.erp_local.channels import ChannelFlow
from app.erp_local.core import ERPError
from app.erp_local.integration_registry import build, prepare, record_bindings, snapshot


def reference(*, store_id="204437665", platform="ml", extra=None, conflict=False):
    account = {
        "id": "store-uuid",
        "platform": platform,
        "name": "Nome sem autoridade",
        "status": "active",
        "archived_at": None,
        "bling_loja_id": store_id,
        "credentials_present": True,
        "token_expires_at": None,
        "last_test_at": None,
        "last_test_ok": None,
    }
    integrations = [account, *([extra] if extra else [])]
    stores = [
        {"integration_id": "store-uuid", "bling_store_id": "206343613" if conflict else store_id}
    ]
    return SimpleNamespace(all_rows=lambda kind: integrations if kind == "integrations" else stores)


def test_exact_ids_and_provider_type_are_required_names_never_bind():
    policies, unbound = build(reference(store_id="missing"))
    assert policies[0]["status"] == "identity_review"
    assert len(unbound) == 68
    policies, _ = build(reference(platform="amazon"))
    assert policies[0]["status"] == "identity_review"
    policies, _ = build(reference(conflict=True))
    assert policies[0]["status"] == "identity_review"
    policies, _ = build(reference())
    assert policies[0]["status"] == "shadow"
    assert policies[0]["data"]["cancellation"] == "review"
    assert policies[0]["data"]["own_publication"] == "disabled"


def test_duplicate_active_bindings_are_held_for_identity_review():
    other = {
        "id": "other-account",
        "platform": "ml",
        "name": "Outra",
        "status": "active",
        "archived_at": None,
        "bling_loja_id": "204437665",
        "credentials_present": True,
        "token_expires_at": None,
        "last_test_at": None,
        "last_test_ok": None,
    }
    policies, _ = build(reference(extra=other))
    assert all(p["status"] == "identity_review" for p in policies)


def test_store_with_stock_disabled_does_not_prepare_publishable_intents(erp):
    routes(erp)
    prepare(erp, reference())
    flow = ChannelFlow(erp)
    flow.receive(event(), source="provider_read")
    intents = flow.snapshot()["intents"]
    assert intents and all(i["status"] == "blocked" for i in intents)
    assert all(i["reason"] == "source_stock_disabled" for i in intents)
    assert balance(erp) == (8, 1, 7)


def test_observed_rule_holds_cancellation_and_prevents_reservation_from_later_confirmation(erp):
    prepare(erp, reference())
    flow = ChannelFlow(erp)
    flow.receive(event(), source="provider_read")
    assert balance(erp) == (8, 1, 7)
    cancelled = event("cancel", "order.cancelled", minute=1)
    result = flow.receive(cancelled, source="provider_read")
    assert result["status"] == "review"
    assert result["outcome"]["code"] == "cancellation_policy_review"
    assert balance(erp) == (8, 1, 7)
    assert flow.receive(event("late", minute=2), source="provider_read")["status"] == "review"
    assert balance(erp) == (8, 1, 7)
    run(
        erp,
        "channel.policy.override",
        {
            "channel": "ml",
            "store": "store-uuid",
            "cancellation": "cancel",
            "reason": "Decisão local para teste explícito",
        },
    )
    flow.process(result["id"])
    assert balance(erp) == (8, 0, 8)
    assert erp.snapshot()["orders"][0]["status"] == "cancelled"
    flow.process(result["id"])
    assert balance(erp) == (8, 0, 8)


def test_direct_cancellation_is_preserved_for_the_store_that_maps_to_cancelled(erp):
    prepare(erp, reference(store_id="206343613"))
    flow = ChannelFlow(erp)
    flow.receive(event(), source="provider_read")
    result = flow.receive(event("cancel", "order.cancelled", minute=1), source="provider_read")
    assert result["status"] == "processed"
    assert balance(erp) == (8, 0, 8)


def test_unknown_account_cannot_auto_cancel_even_without_an_existing_order(erp):
    flow = ChannelFlow(erp)
    result = flow.receive(event("cancel", "order.cancelled", minute=1), source="provider_read")
    assert result["status"] == "review"
    assert flow.receive(event("late", minute=2), source="provider_read")["status"] == "review"
    assert erp.snapshot()["orders"] == []
    assert balance(erp) == (8, 0, 8)


def test_existing_binding_projection_reconciles_only_exact_ids_and_rejects_secrets(erp):
    proof = {
        "version": 1,
        "source": "davinci_existing_account_bindings",
        "read_only": "on",
        "captured_at": "2026-11-05T12:00:00+00:00",
        "bindings": [
            {
                "id": "proof-row",
                "plataforma": "ml",
                "integration_id": "store-uuid",
                "bling_loja_id": "204437665",
                "ativo": True,
                "atualizado_em": "2026-11-05T11:00:00+00:00",
            }
        ],
    }
    record_bindings(erp, proof)
    assert record_bindings(erp, proof)["replayed"]
    prepare(erp, reference(store_id=None))
    with erp.connection() as db:
        assert snapshot(db)["policies"][0]["status"] == "shadow"
    with pytest.raises(ERPError):
        record_bindings(erp, {**proof, "credentials": "not allowed"})
    with pytest.raises(ERPError):
        record_bindings(erp, {**proof, "read_only": "off"})
    with pytest.raises(ERPError):
        record_bindings(erp, {**proof, "captured_at": "2026-11-04T12:00:00+00:00"})


def test_business_source_change_expires_override_without_stock_effect(erp, monkeypatch):
    first = prepare(erp, reference())
    before = erp.snapshot()["movements"]
    assert first["bound_active"] == 1
    assert prepare(erp, reference())["replayed"]
    run(
        erp,
        "channel.policy.override",
        {
            "channel": "ml",
            "store": "store-uuid",
            "cancellation": "cancel",
            "reason": "Teste de regra",
        },
    )
    from app.erp_local import integration_registry

    observed = integration_registry.observed_profiles()
    profile = next(p for p in observed["profiles"] if p["bling_store_id"] == "204437665")
    profile["source_stock_enabled"] = not profile["source_stock_enabled"]
    monkeypatch.setattr(integration_registry, "observed_profiles", lambda: observed)
    prepare(erp, reference())
    with erp.connection() as db:
        own = snapshot(db)["policies"][0]
    assert own["data"]["override"]["status"] == "needs_review"
    assert erp.snapshot()["movements"] == before
    with pytest.raises(ERPError):
        run(
            erp,
            "channel.policy.override",
            {"channel": "ml", "store": "unknown", "cancellation": "cancel", "reason": "Teste"},
        )


def test_authorization_metadata_changes_do_not_revoke_own_business_override(erp):
    source = reference()
    prepare(erp, source)
    run(
        erp,
        "channel.policy.override",
        {
            "channel": "ml",
            "store": "store-uuid",
            "cancellation": "cancel",
            "reason": "Regra própria conferida",
        },
    )
    account = source.all_rows("integrations")[0]
    account.update(
        updated_at="2026-11-05T19:00:00Z",
        last_test_ok=True,
        last_test_at="2026-11-05T19:00:00Z",
        token_expires_at="2026-11-06T00:00:00Z",  # noqa: S106 - date, not a token
    )
    prepare(erp, source)
    with erp.connection() as db:
        current = snapshot(db)["policies"][0]["data"]
    assert current["override"]["status"] == "active"
    assert current["override"]["cancellation"] == "cancel"
