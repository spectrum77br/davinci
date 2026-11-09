"""Physical till sessions, distinct from financial accounts and bank receipts.

Opening/closing counts and classifying sales never create revenue or payments.
The production POS, fiscal issuer and optional between-account movement remain
separate connectors; this module has no network or banking access.
"""

import json
from decimal import Decimal
from uuid import uuid4

from app.erp_local import cash_refunds
from app.erp_local.core import ERPError, dumps, number, text

SCHEMA = """
CREATE TABLE IF NOT EXISTS cash_sessions (
 id TEXT PRIMARY KEY, register_key TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS cash_one_open ON cash_sessions(register_key) WHERE status='open';
CREATE TABLE IF NOT EXISTS cash_operations (
 id TEXT PRIMARY KEY, session_id TEXT REFERENCES cash_sessions(id),
 external_key TEXT UNIQUE NOT NULL, kind TEXT NOT NULL, amount TEXT NOT NULL,
 reversed INTEGER NOT NULL DEFAULT 0, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cash_sales (
 order_id TEXT PRIMARY KEY REFERENCES documents(id), session_id TEXT REFERENCES cash_sessions(id),
 status TEXT NOT NULL, data TEXT NOT NULL);
"""
COMMANDS = {
    "cash.open",
    "cash.supply",
    "cash.withdraw",
    "cash.reverse",
    "cash.attach",
    "cash.void",
    "cash.close",
    "cash.reopen",
    "cash.verify",
    "cash.unverify",
} | cash_refunds.COMMANDS


def get(db, identifier):
    row = db.execute("SELECT * FROM cash_sessions WHERE id=?", (identifier,)).fetchone()
    if not row:
        raise ERPError("cash_not_found", "Controle de caixa não encontrado.", 404)
    return row, json.loads(row["data"])


def summaries(erp, db, identifier):
    from app.erp_local.returns import principal_paid

    row, data = get(db, identifier)
    grouped = {
        "cash": {
            "method_id": "cash",
            "name": "Dinheiro",
            "type": "cash",
            "sales": Decimal(0),
            "expected": Decimal(data["opening"]),
        }
    }
    supplies, withdrawals, pending, sales = Decimal(0), Decimal(0), [], []
    for op in db.execute(
        "SELECT * FROM cash_operations WHERE session_id=? AND reversed=0", (identifier,)
    ):
        if op["kind"] == "supply":
            supplies += Decimal(op["amount"])
        else:
            withdrawals += Decimal(op["amount"])
    grouped["cash"]["expected"] += supplies - withdrawals
    for sale in db.execute("SELECT * FROM cash_sales WHERE session_id=?", (identifier,)):
        current, order = erp.document(db, sale["order_id"], "order")
        frozen = json.loads(sale["data"])
        returned = (
            (current["status"] == "returned" or order.get("situation") == "partial_return")
            and order["total"] == frozen["total"]
            and principal_paid(db, sale["order_id"]) >= Decimal(frozen["total"])
            and (
                row["status"] == "open"
                or cash_refunds.returned_after_close(db, data, sale["order_id"], order)
            )
        )
        sales.append(
            {
                **dict(sale),
                "data": frozen,
                "order_status": current["status"],
                "order_version": order.get("version", 0),
                "return_preserves_sale": returned,
                "number": order["external_id"],
            }
        )
        if sale["status"] == "void":
            continue
        if not returned and (
            order.get("version", 0) != frozen["order_version"]
            or order["total"] != frozen["total"]
            or current["status"] in {"cancelled", "returned"}
        ):
            pending.append({"id": sale["order_id"], "reason": "sale_changed"})
            continue
        if not returned and current["status"] != "shipped":
            pending.append({"id": sale["order_id"], "reason": "sale_unfinished"})
            continue
        for tender in frozen["tenders"]:
            method = tender["method"]
            key = "cash" if method["type"] == "cash" else method["id"]
            group = grouped.setdefault(
                key,
                {
                    "method_id": key,
                    "name": method["name"],
                    "type": method["type"],
                    "sales": Decimal(0),
                    "expected": Decimal(0),
                },
            )
            amount = Decimal(tender["amount"])
            group["sales"] += amount
            group["expected"] += amount
    refunds = cash_refunds.add_summary(db, identifier, row, data, grouped, pending, sales)
    return {
        "groups": [
            {
                **g,
                "sales": str(g["sales"]),
                "expected": str(g["expected"]),
                "refunds": str(g.get("refunds", Decimal(0))),
            }
            for g in grouped.values()
        ],
        "supplies": str(supplies),
        "withdrawals": str(withdrawals),
        "pending": pending,
        "sales": sales,
        "refunds": refunds,
        "refund_candidates": cash_refunds.candidates(db, data) if row["status"] == "open" else [],
        "cash_expected": str(grouped["cash"]["expected"]),
    }


def persist(erp, db, row, data, status=None):
    data = {**data, "version": data["version"] + 1, "updated_at": erp.now()}
    db.execute(
        "UPDATE cash_sessions SET data=?,status=? WHERE id=?",
        (dumps(data), status or row["status"], row["id"]),
    )
    return {
        "id": row["id"],
        "version": data["version"],
        "status": status or row["status"],
        "external_writes": False,
        "financial_effect": False,
    }


def apply(erp, db, kind, payload, actor):
    from app.erp_local import payment_methods

    extra = {
        "cash.open": {"register", "channel", "store", "opening"},
        "cash.supply": {"reference", "amount", "reason"},
        "cash.withdraw": {"reference", "amount", "reason"},
        "cash.reverse": {"operation_id", "reason"},
        "cash.attach": {"order_id", "order_version", "tenders"},
        "cash.void": {"order_id", "reason"},
        "cash.close": {"counted", "reason"},
        "cash.reopen": {"reason"},
        "cash.verify": {"reason"},
        "cash.unverify": {"reason"},
        "cash.refund": {"payment_id", "method_id", "reason"},
        "cash.refund.reverse": {"refund_id", "reason"},
    }
    if not isinstance(payload, dict) or set(payload) - {"id", "version", *extra[kind]}:
        raise ERPError("cash_fields", "Campos do controle de caixa não reconhecidos.")
    if kind == "cash.open":
        if payload.get("id") or payload.get("version"):
            raise ERPError("cash_fields", "A abertura cria um novo controle de caixa.")
        register = text(payload.get("register"), "identificação do caixa")
        channel, store = text(payload.get("channel"), "canal"), text(payload.get("store"), "loja")
        if (channel, store) != ("local", "manual") and not db.execute(
            "SELECT 1 FROM store_policies WHERE channel=? AND store=? AND status='shadow'",
            (channel, store),
        ).fetchone():
            raise ERPError("cash_store", "Escolha uma loja própria identificada.")
        register_key = dumps([channel, store, register.casefold()])
        if db.execute(
            "SELECT 1 FROM cash_sessions WHERE register_key=? AND status='open'", (register_key,)
        ).fetchone():
            raise ERPError("cash_already_open", "Este caixa já está aberto nesta loja.", 409)
        identifier = str(uuid4())
        data = {
            "register": register,
            "channel": channel,
            "store": store,
            "opening": str(number(payload.get("opening", "0"), money=True, zero=True)),
            "opened_at": erp.now(),
            "opened_by": actor,
            "version": 1,
        }
        db.execute(
            "INSERT INTO cash_sessions VALUES (?,?,?,?)",
            (identifier, register_key, "open", dumps(data)),
        )
        return {"id": identifier, "version": 1, "status": "open", "financial_effect": False}
    row, data = get(db, payload.get("id"))
    if type(payload.get("version")) is not int or payload["version"] != data["version"]:
        raise ERPError("cash_version", "O caixa mudou. Atualize antes de continuar.", 409)
    if kind == "cash.verify":
        if row["status"] != "closed":
            raise ERPError("cash_status", "Somente caixa fechado pode ser conferido.", 409)
        current = summaries(erp, db, row["id"])
        expected = {g["method_id"]: g["expected"] for g in current["groups"]}
        if current["pending"] or expected != data["closure"]["expected"]:
            raise ERPError(
                "cash_review", "As vendas mudaram após o fechamento. Concilie o caixa.", 409
            )
        data.update(
            verified_at=erp.now(),
            verified_by=actor,
            verification_reason=text(payload.get("reason"), "motivo da conferência"),
        )
        return persist(erp, db, row, data, "verified")
    if kind == "cash.unverify":
        if row["status"] != "verified":
            raise ERPError(
                "cash_status", "Somente caixa conferido permite retirar a conferência.", 409
            )
        history = data.get("verifications", []) + [
            {
                "verified_at": data["verified_at"],
                "verified_by": data["verified_by"],
                "verification_reason": data["verification_reason"],
                "withdrawn_at": erp.now(),
                "withdrawn_by": actor,
                "withdrawal_reason": text(
                    payload.get("reason"), "motivo da retirada de conferência"
                ),
            }
        ]
        for field in ("verified_at", "verified_by", "verification_reason"):
            data.pop(field, None)
        data["verifications"] = history
        return persist(erp, db, row, data, "closed")
    if kind == "cash.reopen":
        if row["status"] != "closed":
            raise ERPError(
                "cash_status", "Somente caixa fechado e não conferido pode reabrir.", 409
            )
        if db.execute(
            "SELECT 1 FROM cash_sessions WHERE register_key=? AND status='open'",
            (row["register_key"],),
        ).fetchone():
            raise ERPError("cash_already_open", "Já há outra abertura para este caixa.", 409)
        if any(
            json.loads(r[0]).get("source_cash_id") == row["id"]
            for r in db.execute(
                "SELECT data FROM cash_refunds WHERE reversed=0 AND session_id!=?", (row["id"],)
            )
        ):
            raise ERPError(
                "cash_later_refund",
                "Há reembolso desta venda em outro período. "
                "Concilie primeiro a classificação posterior.",
                409,
            )
        history = data.get("closures", []) + [data["closure"]]
        data = {k: v for k, v in data.items() if k != "closure"}
        data.update(
            closures=history,
            reopened_at=erp.now(),
            reopened_by=actor,
            reopen_reason=text(payload.get("reason"), "motivo da reabertura"),
        )
        return persist(erp, db, row, data, "open")
    if row["status"] != "open":
        raise ERPError("cash_status", "A operação exige um caixa aberto.", 409)
    if kind in cash_refunds.COMMANDS:
        return cash_refunds.apply(erp, db, kind, payload, row, data, actor)
    summary = summaries(erp, db, row["id"])
    if kind in {"cash.supply", "cash.withdraw"}:
        reference = text(payload.get("reference"), "comprovante")
        external_key = dumps([row["id"], reference])
        amount = number(payload.get("amount"), money=True)
        operation_kind = "supply" if kind == "cash.supply" else "withdraw"
        if db.execute(
            "SELECT 1 FROM cash_operations WHERE external_key=?", (external_key,)
        ).fetchone():
            raise ERPError("cash_duplicate", "O comprovante já foi registrado neste caixa.", 409)
        if operation_kind == "withdraw" and amount > Decimal(summary["cash_expected"]):
            raise ERPError(
                "cash_insufficient", "A sangria ultrapassa o dinheiro previsto no caixa.", 409
            )
        op_id = str(uuid4())
        db.execute(
            "INSERT INTO cash_operations VALUES (?,?,?,?,?,0,?)",
            (
                op_id,
                row["id"],
                external_key,
                operation_kind,
                str(amount),
                dumps(
                    {
                        "reference": reference,
                        "reason": text(payload.get("reason"), "motivo"),
                        "actor": actor,
                        "created_at": erp.now(),
                    }
                ),
            ),
        )
        return {**persist(erp, db, row, data), "operation_id": op_id}
    if kind == "cash.reverse":
        op = db.execute(
            "SELECT * FROM cash_operations WHERE id=? AND session_id=? AND reversed=0",
            (payload.get("operation_id"), row["id"]),
        ).fetchone()
        if not op:
            raise ERPError("cash_operation", "Operação ativa não encontrada neste caixa.", 404)
        if op["kind"] == "supply" and Decimal(op["amount"]) > Decimal(summary["cash_expected"]):
            raise ERPError(
                "cash_insufficient", "O suprimento já foi utilizado. Concilie o caixa.", 409
            )
        details = json.loads(op["data"])
        details.update(
            reversed_at=erp.now(),
            reversed_by=actor,
            reversal_reason=text(payload.get("reason"), "motivo do estorno"),
        )
        db.execute(
            "UPDATE cash_operations SET reversed=1,data=? WHERE id=?", (dumps(details), op["id"])
        )
        return persist(erp, db, row, data)
    if kind in {"cash.attach", "cash.void"}:
        order, sale = erp.document(db, payload.get("order_id"), "order")
        existing = db.execute(
            "SELECT * FROM cash_sales WHERE order_id=?", (order["id"],)
        ).fetchone()
        if (sale["channel"], sale["store"]) != (data["channel"], data["store"]):
            raise ERPError("cash_sale_store", "A venda pertence a outra loja.", 409)
        if existing and existing["session_id"] != row["id"]:
            raise ERPError("cash_sale_duplicate", "A venda está vinculada a outro caixa.", 409)
        if kind == "cash.void":
            if (
                not existing
                or existing["status"] == "void"
                or order["status"]
                not in {
                    "cancelled",
                    "returned",
                }
            ):
                raise ERPError(
                    "cash_sale_review", "Concilie primeiro o cancelamento/devolução da venda.", 409
                )
            from app.erp_local.returns import principal_paid

            if principal_paid(db, order["id"]) > 0:
                raise ERPError(
                    "cash_paid_return",
                    "A venda possui recebimento. Preserve a venda "
                    "e classifique a baixa do reembolso separadamente.",
                    409,
                )
            frozen = json.loads(existing["data"])
            frozen.update(
                voided_at=erp.now(),
                voided_by=actor,
                void_reason=text(payload.get("reason"), "motivo da conciliação"),
            )
            db.execute(
                "UPDATE cash_sales SET status='void',data=? WHERE order_id=?",
                (dumps(frozen), order["id"]),
            )
            return persist(erp, db, row, data)
        if type(payload.get("order_version")) is not int or payload["order_version"] != sale.get(
            "version", 0
        ):
            raise ERPError("cash_sale_version", "A venda mudou. Atualize antes de vincular.", 409)
        if order["status"] not in {"draft", "reserved", "shipped"}:
            raise ERPError(
                "cash_sale_review", "A venda exige conciliação de cancelamento/devolução."
            )
        raw = payload.get("tenders")
        if not isinstance(raw, list) or len(raw) > 20 or not raw and Decimal(sale["total"]) > 0:
            raise ERPError("cash_tenders", "Informe de uma a 20 formas de pagamento.")
        tenders, method_ids = [], set()
        for tender in raw:
            if not isinstance(tender, dict) or set(tender) != {"method_id", "amount"}:
                raise ERPError("cash_tenders", "Confira os campos de pagamento.")
            method = payment_methods.freeze(db, tender["method_id"])
            if method["id"] in method_ids or method["applicability"] not in {"receivable", "both"}:
                raise ERPError("cash_tenders", "Forma repetida ou não aplicável à venda.")
            method_ids.add(method["id"])
            tenders.append({"method": method, "amount": str(number(tender["amount"], money=True))})
        if sum((Decimal(t["amount"]) for t in tenders), Decimal(0)) != Decimal(sale["total"]):
            raise ERPError("cash_tenders", "As formas de pagamento devem fechar o total da venda.")
        if sale.get("pos", {}).get("stage") == "finalized":
            recorded = {t["method"]["id"]: Decimal(t["amount"]) for t in sale["pos"]["tenders"]}
            if recorded != {t["method"]["id"]: Decimal(t["amount"]) for t in tenders}:
                raise ERPError("pos_tenders", "As formas diferem do checkout registrado.", 409)
        frozen = {
            "number": sale["external_id"],
            "total": sale["total"],
            "order_version": sale.get("version", 0),
            "tenders": tenders,
            "attached_at": erp.now(),
            "attached_by": actor,
        }
        db.execute(
            "INSERT INTO cash_sales VALUES (?,?,'active',?) ON CONFLICT(order_id) "
            "DO UPDATE SET data=excluded.data,status='active'",
            (order["id"], row["id"], dumps(frozen)),
        )
        return persist(erp, db, row, data)
    if kind == "cash.close":
        if Decimal(summary["cash_expected"]) < 0:
            raise ERPError(
                "cash_insufficient", "Concilie o saldo negativo do caixa antes de fechar.", 409
            )
        if summary["pending"]:
            raise ERPError(
                "cash_pending", "Finalize ou concilie as vendas vinculadas antes de fechar.", 409
            )
        counted = payload.get("counted")
        expected = {g["method_id"]: g["expected"] for g in summary["groups"]}
        if not isinstance(counted, dict) or set(counted) != set(expected):
            raise ERPError("cash_count", "Informe todas as formas de pagamento do fechamento.")
        counted = {m: str(number(v, money=True, zero=True)) for m, v in counted.items()}
        differences = {m: str(Decimal(counted[m]) - Decimal(expected[m])) for m in counted}
        reason = text(payload["reason"], "motivo da diferença") if payload.get("reason") else ""
        if any(Decimal(d) for d in differences.values()) and not reason:
            raise ERPError("cash_difference", "Registre o motivo da diferença antes de fechar.")
        data["closure"] = {
            "groups": summary["groups"],
            "closed_at": erp.now(),
            "closed_by": actor,
            "reason": reason,
            "expected": expected,
            "counted": counted,
            "differences": differences,
            "event_seq": db.execute("SELECT coalesce(max(seq),0) FROM events").fetchone()[0],
            "sales": [
                {
                    "id": s["order_id"],
                    "data": s["data"],
                    "order_status": s["order_status"],
                    "order_version": s["order_version"],
                    "return_preserves_sale": s["return_preserves_sale"],
                }
                for s in summary["sales"]
            ],
            "refunds": summary["refunds"],
        }
        return persist(erp, db, row, data, "closed")
    raise ERPError("cash_operation", "Operação de caixa não reconhecida.")


def snapshot(erp, db):
    result = []
    for row in db.execute("SELECT * FROM cash_sessions ORDER BY rowid DESC"):
        summary = summaries(erp, db, row["id"])
        data = json.loads(row["data"])
        result.append(
            {
                **dict(row),
                "data": data,
                "summary": summary,
                "operations": [
                    {**dict(op), "data": json.loads(op["data"])}
                    for op in db.execute(
                        "SELECT * FROM cash_operations WHERE session_id=? ORDER BY rowid DESC",
                        (row["id"],),
                    )
                ],
                "financial_effect": False,
            }
        )
    return result


def print_html(erp, db, identifier):
    from html import escape

    row, data = get(db, identifier)
    report = summaries(erp, db, identifier)
    closure = data.get("closure")
    groups = closure["groups"] if closure else report["groups"]

    def money(value):
        return f"{Decimal(value):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")

    state = {"open": "Aberto", "closed": "Fechado", "verified": "Conferido"}[row["status"]]
    e = lambda value: escape(str(value))  # noqa: E731 - bounded HTML text escaping
    lines = "".join(
        "<tr>"
        + "".join(
            "<td>" + e(v) + "</td>"
            for v in [
                group["name"],
                money(group["sales"]),
                money(group.get("refunds", "0")),
                money(group["expected"]),
                money(closure["counted"][group["method_id"]]) if closure else "—",
                money(closure["differences"][group["method_id"]]) if closure else "—",
            ]
        )
        + "</tr>"
        for group in groups
    )
    operations = "".join(
        "<tr>"
        + "".join(
            "<td>" + e(v) + "</td>"
            for v in [
                "Suprimento" if op["kind"] == "supply" else "Sangria",
                money(op["amount"]),
                json.loads(op["data"])["reference"],
                json.loads(op["data"])["reason"],
                "Estornada" if op["reversed"] else "Registrada",
            ]
        )
        + "</tr>"
        for op in db.execute(
            "SELECT * FROM cash_operations WHERE session_id=? ORDER BY rowid", (identifier,)
        )
    )
    refunds = "".join(
        "<tr>"
        + "".join(
            "<td>" + e(v) + "</td>"
            for v in [
                r["data"]["number"],
                r["data"]["method"]["name"],
                money(r["amount"]),
                r["data"]["reason"],
                "Estornada" if r["reversed"] else "Classificada",
            ]
        )
        + "</tr>"
        for r in (closure.get("refunds", []) if closure else report["refunds"])
    )
    return (
        '<!doctype html><html lang="pt-BR"><meta charset="utf-8">'
        '<link rel="stylesheet" href="/assets/print.css"><title>DaVinci · Controle de caixa</title>'
        "<h1>DaVinci · Controle de caixa</h1><p>"
        + e(data["register"])
        + " · "
        + e(state)
        + "</p><p>Operador da abertura: "
        + e(data["opened_by"])
        + "</p><p>Troco inicial: R$ "
        + money(data["opening"])
        + "</p><p>Relatório interno de conferência física.</p>"
        "<table><thead><tr><th>Forma de pagamento</th><th>Vendas (R$)</th>"
        "<th>Reembolsos (R$)</th><th>Previsto (R$)</th>"
        "<th>Informado (R$)</th><th>Diferença (R$)</th></tr></thead><tbody>"
        + lines
        + "</tbody></table><h2>Sangrias e suprimentos</h2><table><thead><tr><th>Tipo</th>"
        "<th>Valor (R$)</th><th>Comprovante</th><th>Observação</th><th>Situação</th>"
        "</tr></thead><tbody>"
        + operations
        + "</tbody></table><h2>Reembolsos do período</h2><table><thead><tr><th>Venda</th>"
        "<th>Forma</th><th>Valor (R$)</th><th>Motivo</th><th>Situação</th></tr></thead><tbody>"
        + refunds
        + "</tbody></table><p>Motivo do fechamento: "
        + e(closure["reason"] if closure else "Ainda aberto")
        + "</p></html>"
    )
