"""Projection validation for the initial GET-only Bling read; no network code."""

from datetime import datetime

from app.erp_local.core import ERPError


def validate_live_read(data):
    fields = {
        "source",
        "started_at",
        "captured_at",
        "db_read_only",
        "no_token_refresh",
        "balances",
        "deposits",
        "structures",
        "calls",
        "failures",
    }
    if not isinstance(data, dict) or set(data) != fields:
        raise ERPError("invalid_initial_read", "Formato da leitura inicial inválido.")
    if (
        data["source"] != "bling_api_read"
        or data["db_read_only"] != "on"
        or data["no_token_refresh"] is not True
    ):
        raise ERPError(
            "invalid_initial_read", "A leitura inicial não confirma o isolamento esperado."
        )
    for key in ["started_at", "captured_at"]:
        try:
            if datetime.fromisoformat(data[key]).tzinfo is None:
                raise ValueError
        except (ValueError, TypeError):
            raise ERPError("invalid_initial_read", "Data da leitura inicial inválida.") from None
    if len(data["balances"]) > 10000 or len(data["structures"]) > 100:
        raise ERPError("invalid_initial_read", "Volume da leitura inicial inválido.")
    seen = set()
    for row in data["balances"]:
        if set(row) != {"id", "physical", "available", "deposits"} or row["id"] in seen:
            raise ERPError("invalid_initial_read", "Saldo inicial fora da projeção permitida.")
        seen.add(row["id"])
        if any(set(d) != {"id", "physical", "available"} for d in row["deposits"]):
            raise ERPError("invalid_initial_read", "Depósito fora da projeção permitida.")
    for row in data["deposits"]:
        if set(row) != {"id", "name", "active", "default", "excluded_from_total"}:
            raise ERPError(
                "invalid_initial_read", "Cadastro de depósito fora da projeção permitida."
            )
    for row in data["structures"]:
        if set(row) != {"id", "sku", "name", "format", "status", "components"} or any(
            set(c) != {"id", "quantity"} for c in row["components"]
        ):
            raise ERPError("invalid_initial_read", "Composição fora da projeção permitida.")
    for call in data["calls"]:
        if set(call) != {"method", "path", "status"} or call["method"] != "GET":
            raise ERPError(
                "invalid_initial_read", "A coleta inicial contém uma ação não permitida."
            )
    for failure in data["failures"]:
        if set(failure) not in ({"code"}, {"path", "status"}):
            raise ERPError("invalid_initial_read", "Erro da coleta fora da projeção permitida.")
    return data
