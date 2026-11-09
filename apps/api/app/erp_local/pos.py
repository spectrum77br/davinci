"""Own POS checkout. All effects stay in one local database transaction.

No Pix creation, acquirer charge or fiscal issuance is attempted. 'received'
means an operator-confirmed local receipt, not provider authorization.
"""

from decimal import Decimal
from uuid import uuid4

from app.erp_local import cash_register, finance, payment_methods, sales
from app.erp_local.core import ERPError, dumps, number, text, total

SCHEMA = """
CREATE TABLE IF NOT EXISTS pos_receipt_proofs (
 id TEXT PRIMARY KEY, bank_id TEXT REFERENCES finance_banks(id), reference TEXT NOT NULL,
 order_id TEXT REFERENCES documents(id), method_id TEXT NOT NULL, amount TEXT NOT NULL,
 data TEXT NOT NULL, UNIQUE(bank_id,reference));
"""
COMMANDS = {"pos.save", "pos.finalize", "pos.cancel"}


def session(db, identifier, version):
    row, data = cash_register.get(db, identifier)
    if row["status"] != "open":
        raise ERPError("pos_cash_closed", "Abra o caixa antes de registrar a venda.", 409)
    if type(version) is not int or version != data["version"]:
        raise ERPError(
            "pos_cash_version", "O caixa mudou. Atualize a venda antes de continuar.", 409
        )
    return row, data


def apply(erp, db, key, kind, payload, actor):
    fields = {
        "pos.save": {"cash_id", "cash_version", "order"},
        "pos.finalize": {"id", "version", "cash_version", "tenders", "cash_received"},
        "pos.cancel": {"id", "version", "cash_version", "reason"},
    }
    if not isinstance(payload, dict) or set(payload) - fields[kind]:
        raise ERPError("pos_fields", "Campos da venda presencial não reconhecidos.")
    if kind == "pos.save":
        cash, cash_data = session(db, payload.get("cash_id"), payload.get("cash_version"))
        order = payload.get("order")
        allowed = sales.SAVE_FIELDS - {
            "proposal_id",
            "proposal_version",
            "status",
            "channel",
            "store",
        }
        if not isinstance(order, dict) or set(order) - allowed:
            raise ERPError("pos_fields", "Dados do pedido presencial inválidos.")
        if order.get("id"):
            old_row, old = erp.document(db, order["id"], "order")
            if old.get("pos", {}).get("cash_id") != cash["id"] or old_row["status"] != "draft":
                raise ERPError("pos_order", "A venda não está em digitação neste caixa.", 409)
        order = {
            **order,
            "channel": cash_data["channel"],
            "store": cash_data["store"],
            "status": "draft",
            "customer_name": order.get("customer_name") or "Consumidor",
        }
        if not order.get("external_id"):
            sequence = (
                db.execute("SELECT COUNT(*) FROM documents WHERE kind='order'").fetchone()[0] + 1
            )
            while True:
                candidate = "PDV-" + str(sequence).zfill(6)
                external_key = dumps([cash_data["channel"], cash_data["store"], candidate])
                if not db.execute(
                    "SELECT 1 FROM documents WHERE external_key=?", (external_key,)
                ).fetchone():
                    break
                sequence += 1
            order["external_id"] = candidate
        result = sales.apply(erp, db, key, "order.save", order, pos_cash_id=cash["id"])
        row, data = erp.document(db, result["id"], "order")
        data["pos"] = {"cash_id": cash["id"], "saved_by": actor, "stage": "draft"}
        erp.update_document(db, row["id"], "draft", data)
        frozen = {
            "number": data["external_id"],
            "total": data["total"],
            "order_version": data["version"],
            "tenders": [],
            "attached_at": erp.now(),
            "attached_by": actor,
        }
        existing = db.execute(
            "SELECT session_id FROM cash_sales WHERE order_id=?", (row["id"],)
        ).fetchone()
        if existing and existing[0] != cash["id"]:
            raise ERPError("pos_order", "A venda está vinculada a outro caixa.", 409)
        db.execute(
            "INSERT INTO cash_sales VALUES (?,?,'active',?) ON CONFLICT(order_id) "
            "DO UPDATE SET data=excluded.data,status='active'",
            (row["id"], cash["id"], dumps(frozen)),
        )
        register = cash_register.persist(erp, db, cash, cash_data)
        return {**result, "cash_version": register["version"], "stage": "draft"}
    row, data = erp.document(db, payload.get("id"), "order")
    pos = data.get("pos")
    if not pos:
        raise ERPError("pos_order", "Selecione uma venda presencial própria.", 409)
    cash, cash_data = session(db, pos["cash_id"], payload.get("cash_version"))
    if type(payload.get("version")) is not int or payload["version"] != data["version"]:
        raise ERPError("pos_version", "A venda mudou. Atualize antes de continuar.", 409)
    if row["status"] != "draft" or pos["stage"] != "draft":
        raise ERPError("pos_finalized", "A venda já foi finalizada. Use o fluxo de devolução.", 409)
    if kind == "pos.cancel":
        reason = text(payload.get("reason"), "motivo do cancelamento")
        erp.apply(db, key, "order.cancel", {"id": row["id"]}, _pos=True)
        _, cancelled = erp.document(db, row["id"], "order")
        cancelled["pos"] = {
            **pos,
            "stage": "cancelled",
            "cancelled_by": actor,
            "cancelled_at": erp.now(),
            "cancellation_reason": reason,
        }
        erp.update_document(db, row["id"], "cancelled", cancelled)
        cash_register.apply(
            erp,
            db,
            "cash.void",
            {
                "id": cash["id"],
                "version": cash_data["version"],
                "order_id": row["id"],
                "reason": reason,
            },
            actor,
        )
        return {"id": row["id"], "stage": "cancelled", "external_writes": False}
    raw = payload.get("tenders")
    grand_total = Decimal(data["total"])
    if not isinstance(raw, list) or len(raw) > 20 or (grand_total > 0 and not raw):
        raise ERPError("pos_tenders", "Informe as formas de pagamento da venda.")
    tenders, method_ids, installments, forecasts = [], set(), [], []
    cash_total = Decimal(0)
    proof_keys = set()
    for tender in raw:
        if not isinstance(tender, dict) or set(tender) - {
            "method_id",
            "amount",
            "receipt",
            "bank_id",
            "reference",
        }:
            raise ERPError("pos_tenders", "Campos de pagamento não reconhecidos.")
        method = payment_methods.freeze(db, tender.get("method_id"))
        if method["id"] in method_ids or method["applicability"] not in {"receivable", "both"}:
            raise ERPError("pos_tenders", "Forma de pagamento repetida ou não aplicável.")
        method_ids.add(method["id"])
        if method["integrated"]:
            raise ERPError(
                "pos_connector",
                "Este pagamento exige o conector próprio de Pix/POS, "
                "que ainda não está homologado.",
                409,
            )
        amount = number(tender.get("amount"), money=True)
        receipt = tender.get("receipt", "open")
        if receipt not in {"open", "received"}:
            raise ERPError("pos_receipt", "Escolha a receber ou recebimento conferido.")
        if method["type"] == "cash":
            if method["days"] != [0] or receipt != "received":
                raise ERPError("pos_cash", "Dinheiro exige recebimento à vista conferido.")
            cash_total += amount
        if receipt == "open" and not data.get("contact"):
            raise ERPError(
                "pos_customer", "Selecione o cadastro do cliente para a venda a receber."
            )
        bank, reference = None, ""
        if receipt == "received":
            bank = tender.get("bank_id") or method.get("bank_id")
            if (
                not bank
                or not db.execute("SELECT 1 FROM finance_banks WHERE id=?", (bank,)).fetchone()
            ):
                raise ERPError("pos_bank", "Escolha a conta financeira do recebimento conferido.")
            reference = (
                "Dinheiro " + row["id"] + " " + method["id"]
                if method["type"] == "cash"
                else text(tender.get("reference"), "comprovante do recebimento")
            )
            if (bank, reference) in proof_keys:
                raise ERPError(
                    "pos_receipt_duplicate", "O mesmo comprovante foi repetido na venda."
                )
            proof_keys.add((bank, reference))
            if (
                db.execute(
                    "SELECT 1 FROM pos_receipt_proofs WHERE bank_id=? AND reference=?",
                    (bank, reference),
                ).fetchone()
                or db.execute(
                    "SELECT 1 FROM finance_payments WHERE external_key=?",
                    (dumps([bank, reference]),),
                ).fetchone()
            ):
                raise ERPError("pos_receipt_duplicate", "Este comprovante já foi registrado.", 409)
        plan, expected_fees = payment_methods.plan(method, amount, data["date"])
        start = len(installments)
        installments.extend(plan)
        forecasts.extend(expected_fees)
        tenders.append(
            {
                "method": method,
                "amount": str(amount),
                "receipt": receipt,
                "bank_id": bank,
                "reference": reference,
                "installment_indices": list(range(start, len(installments))),
            }
        )
    if sum((Decimal(t["amount"]) for t in tenders), Decimal(0)) != grand_total:
        raise ERPError("pos_total", "As formas de pagamento devem fechar o total da venda.")
    received_cash = number(payload.get("cash_received", "0"), money=True, zero=True)
    if received_cash < cash_total or (not cash_total and received_cash):
        raise ERPError("pos_cash", "Confira o valor recebido em dinheiro e o troco.")
    sales.apply(erp, db, key, "order.confirm", {"id": row["id"]}, pos_cash_id=cash["id"])
    current_row, current = erp.document(db, row["id"], "order")
    current.update(installments=installments, payment_forecasts=forecasts)
    current.pop("payment_method", None)
    erp.update_document(db, row["id"], current_row["status"], current)
    sales.apply(erp, db, key, "order.accounts", {"id": row["id"]}, pos_cash_id=cash["id"])
    finance.ensure_schedules(db)
    header = sales.receipt(db, row["id"])
    rows = (
        db.execute(
            "SELECT * FROM finance_installments WHERE account_id=? AND active=1 ORDER BY rowid",
            (header["id"],),
        ).fetchall()
        if header
        else []
    )
    if len(rows) != len(installments):
        raise ERPError("pos_plan", "As parcelas exigem conferência antes de finalizar.", 409)
    for index, tender in enumerate(tenders):
        tender["payments"] = []
        if tender["receipt"] != "received":
            continue
        proof_id = str(uuid4())
        db.execute(
            "INSERT INTO pos_receipt_proofs VALUES (?,?,?,?,?,?,?)",
            (
                proof_id,
                tender["bank_id"],
                tender["reference"],
                row["id"],
                tender["method"]["id"],
                tender["amount"],
                dumps({"actor": actor, "created_at": erp.now(), "mode": "operator_confirmed"}),
            ),
        )
        for plan_index in tender["installment_indices"]:
            external = "PDV-" + row["id"] + "-" + str(index) + "-" + str(plan_index)
            payment = finance.post_payment(
                erp,
                db,
                key,
                {
                    "installment_id": rows[plan_index]["id"],
                    "amount": rows[plan_index]["amount"],
                    "date": data["date"],
                    "bank_id": tender["bank_id"],
                    "external_id": external,
                },
            )
            tender["payments"].append(payment["id"])
    sales.apply(erp, db, key, "order.stock", {"id": row["id"]}, pos_cash_id=cash["id"])
    _, final = erp.document(db, row["id"], "order")
    final["pos"] = {
        **pos,
        "stage": "finalized",
        "finalized_at": erp.now(),
        "finalized_by": actor,
        "tenders": tenders,
        "cash_received": str(received_cash),
        "change": str(received_cash - cash_total),
        "external_payment_confirmation": False,
    }
    erp.update_document(db, row["id"], "shipped", final)
    cash_register.apply(
        erp,
        db,
        "cash.attach",
        {
            "id": cash["id"],
            "version": cash_data["version"],
            "order_id": row["id"],
            "order_version": final["version"],
            "tenders": [{"method_id": t["method"]["id"], "amount": t["amount"]} for t in tenders],
        },
        actor,
    )
    return {
        "id": row["id"],
        "stage": "finalized",
        "version": final["version"],
        "change": final["pos"]["change"],
        "external_writes": False,
        "fiscal_issued": False,
        "external_payment_confirmation": False,
    }


def receipt_guard(db, bank, reference):
    if db.execute(
        "SELECT 1 FROM pos_receipt_proofs WHERE bank_id=? AND reference=?", (bank, reference)
    ).fetchone():
        raise ERPError(
            "pos_receipt_duplicate", "Este comprovante já foi usado no caixa presencial.", 409
        )


def print_html(erp, db, identifier):
    from html import escape

    row, data = erp.document(db, identifier, "order")
    pos = data.get("pos")
    if not pos or pos["stage"] != "finalized":
        raise ERPError("pos_receipt", "Finalize a venda antes de imprimir o comprovante.", 409)

    def money(value, places=2):
        return f"{Decimal(value):,.{places}f}".replace(",", "_").replace(".", ",").replace("_", ".")

    e = lambda value: escape(str(value))  # noqa: E731 - bounded HTML text escaping
    items = "".join(
        "<tr>"
        + "".join(
            "<td>" + e(v) + "</td>"
            for v in [
                data["product_snapshots"].get(i["sku"], {}).get("name", i["sku"]),
                i["sku"],
                i["quantity"].replace(".", ","),
                money(i["unit_price"], 4),
                money(total([i])),
            ]
        )
        + "</tr>"
        for i in data["items"]
    )
    tenders = "".join(
        "<tr>"
        + "".join(
            "<td>" + e(v) + "</td>"
            for v in [
                t["method"]["name"],
                money(t["amount"]),
                "Conferido manualmente no checkout" if t["receipt"] == "received" else "A receber",
            ]
        )
        + "</tr>"
        for t in pos["tenders"]
    )
    return (
        '<!doctype html><html lang="pt-BR"><meta charset="utf-8">'
        '<link rel="stylesheet" href="/assets/print.css">'
        "<title>DaVinci · Comprovante de venda</title>"
        "<h1>DaVinci · Comprovante interno de venda</h1><p>Não é documento fiscal.</p><p>"
        + e(data["external_id"])
        + " · "
        + e("/".join(data["date"].split("-")[::-1]))
        + "</p><p>Cliente: "
        + e(data["customer_name"])
        + "</p>"
        "<table><thead><tr><th>Produto</th><th>Código</th><th>Quantidade</th>"
        "<th>Unitário (R$)</th><th>Total (R$)</th></tr></thead><tbody>"
        + items
        + "</tbody></table><p>Frete: R$ "
        + money(data["freight"])
        + " · Outras despesas: R$ "
        + money(data["charges"])
        + " · Desconto: R$ "
        + money(data["discount"])
        + "</p><h2>Total R$ "
        + money(data["total"])
        + "</h2><table><thead><tr><th>Forma de pagamento</th><th>Valor (R$)</th>"
        "<th>Registro no checkout</th></tr></thead><tbody>"
        + tenders
        + "</tbody></table><p>Recebido em dinheiro: R$ "
        + money(pos["cash_received"])
        + " · Troco: R$ "
        + money(pos["change"])
        + "</p><p>"
        + e(data.get("notes", ""))
        + "</p></html>"
    )
