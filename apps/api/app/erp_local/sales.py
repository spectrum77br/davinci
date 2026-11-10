"""Own manual sales: save/edit, reserve, stock and accounts are separate actions.

Provider intake keeps its existing contract. New manual orders use explicit
financial posting and never execute channel/fiscal/carrier automations.
"""

from decimal import Decimal
from uuid import uuid4

from app.erp_local.core import ERPError, dumps, number, text, total
from app.erp_local.finance import day, paid, today

COMMANDS = {
    "order.save",
    "order.confirm",
    "order.accounts",
    "order.reverse_accounts",
    "order.stock",
    "order.reverse_stock",
    "order.situation",
}
SAVE_FIELDS = {
    "id",
    "version",
    "channel",
    "store",
    "external_id",
    "depot",
    "items",
    "customer_name",
    "contact_id",
    "refresh_contact",
    "date",
    "expected_date",
    "status",
    "freight",
    "discount",
    "charges",
    "notes",
    "internal_notes",
    "payment_method_id",
    "price_list_id",
    "price_quote_token",
    "seller_id",
    "proposal_id",
    "proposal_version",
    "transport",
    "intermediary",
    "purchase_order",
}
FREIGHT_MODES = {"0", "1", "2", "3", "4", "9"}


def _transport(raw):
    """Transportador block of Bling's order: who carries, who pays the freight, volumes, weight."""
    if raw in (None, ""):
        return None
    if not isinstance(raw, dict) or set(raw) - {
        "carrier",
        "carrier_cnpj",
        "freight_mode",
        "volumes",
        "gross_weight",
        "service",
    }:
        raise ERPError("sale_transport", "Dados do transportador não reconhecidos.")
    mode = str(raw.get("freight_mode") or "")
    if mode and mode not in FREIGHT_MODES:
        raise ERPError("sale_transport", "Escolha a modalidade do frete.")
    cnpj = "".join(c for c in str(raw.get("carrier_cnpj") or "") if c.isdigit())
    if cnpj and len(cnpj) != 14:
        raise ERPError("sale_transport", "CNPJ do transportador inválido.")
    volumes = str(raw.get("volumes") or "").strip()
    if volumes and (not volumes.isdigit() or int(volumes) > 9999):
        raise ERPError("sale_transport", "Quantidade de volumes inválida.")
    weight = str(raw.get("gross_weight") or "").strip().replace(",", ".")
    if weight:
        weight = str(number(weight, zero=True))
    out = {
        "carrier": str(raw.get("carrier") or "").strip()[:120],
        "carrier_cnpj": cnpj,
        "freight_mode": mode,
        "volumes": volumes,
        "gross_weight": weight,
        "service": str(raw.get("service") or "").strip()[:120],
    }
    return {k: v for k, v in out.items() if v} or None


def _intermediary(raw):
    """The marketplace that sold for us: NF-e asks its CNPJ and the seller's id there."""
    if raw in (None, ""):
        return None
    if not isinstance(raw, dict) or set(raw) - {"cnpj", "account"}:
        raise ERPError("sale_intermediary", "Dados do intermediador não reconhecidos.")
    cnpj = "".join(c for c in str(raw.get("cnpj") or "") if c.isdigit())
    if cnpj and len(cnpj) != 14:
        raise ERPError("sale_intermediary", "CNPJ do intermediador inválido.")
    account = str(raw.get("account") or "").strip()[:60]
    return {"cnpj": cnpj, "account": account} if cnpj else None


def receipt(db, identifier):
    return db.execute(
        "SELECT * FROM accounts WHERE document_id=? AND kind='receivable'", (identifier,)
    ).fetchone()


def cancel_receivable(erp, db, identifier):
    """Cancel only outstanding principal; keep cash facts and a refund obligation."""
    header = receipt(db, identifier)
    if not header or header["status"] == "cancelled":
        return
    installments = db.execute(
        "SELECT id FROM finance_installments WHERE account_id=? AND active=1", (header["id"],)
    ).fetchall()
    done = sum((paid(db, r[0]) for r in installments), Decimal(0))
    from app.erp_local.returns import refunded_total

    refund_remaining = max(Decimal(0), done - refunded_total(db, identifier))
    if refund_remaining:
        erp.account(db, identifier, "refund", str(refund_remaining))
    if header["status"] != "paid":
        db.execute("UPDATE accounts SET status='cancelled' WHERE id=?", (header["id"],))
        db.execute("UPDATE finance_installments SET active=0 WHERE account_id=?", (header["id"],))


def account_paid(db, header):
    return any(
        paid(db, r[0])
        for r in db.execute(
            "SELECT id FROM finance_installments WHERE account_id=?", (header["id"],)
        )
    )


def apply(erp, db, key, kind, p, *, pos_cash_id=None):
    from app.erp_local import (
        contacts,
        deposits,
        payment_methods,
        price_lists,
        product_details,
        proposals,
        sellers,
    )

    allowed = (
        SAVE_FIELDS
        if kind == "order.save"
        else {"id", "status"}
        if kind == "order.situation"
        else {"id"}
    )
    if not isinstance(p, dict) or set(p) - allowed:
        raise ERPError("sale_fields", "Campos do pedido não reconhecidos.")
    if kind == "order.save":
        previous, old = erp.document(db, p["id"], "order") if p.get("id") else (None, {})
        if old.get("pos") and old["pos"]["cash_id"] != pos_cash_id:
            raise ERPError("pos_flow", "Edite esta venda pela frente de caixa.", 409)
        proposal_source = proposals.sale_source(db, p, previous)
        version = old.get("version", 0)
        if isinstance(p.get("version", 0), bool) or p.get("version", 0) != version:
            raise ERPError("sale_version", "O pedido mudou. Atualize antes de salvar.", 409)
        if previous:
            if old.get("stock_allocation"):
                raise ERPError(
                    "sale_allocation", "Retorne ao lote original antes de editar esta venda.", 409
                )
            sellers.guard_edit(db, previous["id"])
            if previous["status"] not in {"draft", "reserved"}:
                raise ERPError(
                    "sale_edit",
                    "Somente pedidos em digitação ou reservados podem ser editados.",
                    409,
                )
            if db.execute(
                "SELECT 1 FROM logistics_shipments WHERE id=?", (previous["id"],)
            ).fetchone():
                raise ERPError(
                    "sale_shipping",
                    "O pedido já entrou na logística. Concilie a separação antes de editar.",
                    409,
                )
            header = receipt(db, previous["id"])
            if header and header["status"] != "cancelled":
                raise ERPError("sale_accounts", "Estorne as contas antes de editar o pedido.", 409)
        status = p.get("status", "reserved")
        if status not in {"draft", "reserved"}:
            raise ERPError("sale_status", "Escolha Em digitação ou Em aberto.")
        identifiers = {
            field: text(p.get(field), field) for field in ("channel", "store", "external_id")
        }
        if previous and any(old[field] != value for field, value in identifiers.items()):
            raise ERPError(
                "sale_identity",
                "Canal, loja e número vinculados ao pedido não podem ser trocados.",
                409,
            )
        external_key = dumps([identifiers[f] for f in ("channel", "store", "external_id")])
        if (
            not previous
            and db.execute(
                "SELECT 1 FROM documents WHERE external_key=?", (external_key,)
            ).fetchone()
        ):
            raise ERPError(
                "sale_duplicate", "Já existe um pedido com este número nesta conta.", 409
            )
        items = erp.items(db, p.get("items"))
        depot = erp.depot(db, p.get("depot"))
        deposits.require_active(db, depot, sale=True)
        amounts = {
            field: number(p.get(field, "0") or "0", money=True, zero=True)
            for field in ("freight", "discount", "charges")
        }
        subtotal = Decimal(total(items))
        if amounts["discount"] > subtotal + amounts["freight"] + amounts["charges"]:
            raise ERPError("sale_discount", "O desconto ultrapassa o total da venda.")
        grand_total = number(
            subtotal + amounts["freight"] + amounts["charges"] - amounts["discount"],
            money=True,
            zero=True,
        )
        customer = text(p.get("customer_name"), "cliente")
        frozen = None
        if p.get("contact_id"):
            if "refresh_contact" in p and not isinstance(p["refresh_contact"], bool):
                raise ERPError("sale_contact", "Opção de atualizar o contato inválida.")
            frozen = old.get("contact")
            if not frozen or frozen["id"] != p["contact_id"] or p.get("refresh_contact"):
                frozen = contacts.document_snapshot(db, p["contact_id"])
            if frozen["name"] != customer:
                raise ERPError(
                    "contact_name_conflict", "O cliente difere do cadastro selecionado.", 409
                )
        dates = {"date": day(p.get("date") or today())}
        if p.get("expected_date"):
            dates["expected_date"] = day(p["expected_date"])
        price_quote = price_lists.bind(
            erp,
            db,
            p,
            old,
            items,
            {
                "date": dates["date"],
                "contact_id": p.get("contact_id") or "",
                "payment_method_id": p.get("payment_method_id") or "",
                "state": (frozen or {}).get("state", ""),
                "channel": identifiers["channel"],
                "store": identifiers["store"],
            },
        )
        data = {
            **old,
            **identifiers,
            **dates,
            "version": version + 1,
            "created_at": old.get("created_at", erp.now()),
            "customer_name": customer,
            "depot": depot,
            "items": items,
            "requirements": erp.requirements(db, items),
            "subtotal": str(subtotal),
            **{k: str(v) for k, v in amounts.items()},
            "total": str(grand_total),
            "accounts_policy": "manual",
            "stock_posted": False,
            "product_snapshots": {
                i["sku"]: product_details.document_snapshot(erp, db, i["sku"]) for i in items
            },
            "situation": status,
        }
        for field, value in (
            ("transport", _transport(p.get("transport"))),
            ("intermediary", _intermediary(p.get("intermediary"))),
            ("purchase_order", str(p.get("purchase_order") or "").strip()[:60]),
        ):
            data.pop(field, None)
            if value:
                data[field] = value
        data.pop("seller", None)
        seller = sellers.bind(db, p.get("seller_id"), old, subtotal, amounts["discount"])
        if seller:
            data["seller"] = seller
        data.pop("price_quote", None)
        if price_quote:
            data["price_quote"] = price_quote
        payment = old.get("payment_method")
        if p.get("payment_method_id"):
            if not payment or payment["id"] != p["payment_method_id"]:
                payment = payment_methods.freeze(db, p["payment_method_id"])
        else:
            payment = None
        for field in ("payment_method", "installments", "payment_forecasts"):
            data.pop(field, None)
        if payment:
            installments, forecasts = payment_methods.plan(payment, grand_total, dates["date"])
            data.update(
                payment_method=payment, installments=installments, payment_forecasts=forecasts
            )
        data.pop("expected_date", None)
        data.update(dates)
        data.pop("contact", None)
        if frozen:
            data["contact"] = frozen
        if proposal_source:
            data["proposal"] = proposal_source
        for field in ["notes", "internal_notes"]:
            data[field] = text(p[field], field, 2000) if p.get(field) else ""
        identifier = previous["id"] if previous else str(uuid4())
        if previous and previous["status"] == "reserved":
            for sku, quantity in old["requirements"].items():
                erp.move(
                    db, key, sku, old["depot"], Decimal(0), -Decimal(quantity), "edição de pedido"
                )
        if status == "reserved":
            for sku, quantity in data["requirements"].items():
                erp.move(
                    db,
                    key,
                    sku,
                    depot,
                    Decimal(0),
                    Decimal(quantity),
                    "reserva " + identifiers["external_id"],
                )
        if previous:
            erp.update_document(db, identifier, status, data)
        else:
            db.execute(
                "INSERT INTO documents VALUES (?,?,?,?,?)",
                (identifier, "order", external_key, status, dumps(data)),
            )
        proposals.link_sale(erp, db, proposal_source, identifier)
        return {
            "id": identifier,
            "version": version + 1,
            "status": status,
            "external_writes": False,
        }
    doc, data = erp.document(db, p.get("id"), "order")
    if data.get("pos") and data["pos"]["cash_id"] != pos_cash_id:
        raise ERPError("pos_flow", "Conclua esta venda pela frente de caixa.", 409)
    if kind == "order.situation":
        status = p.get("status")
        if status not in {"draft", "reserved", "shipped", "cancelled", "returned"}:
            raise ERPError("sale_status", "Situação não reconhecida.")
        if status == "cancelled":
            return erp.apply(db, key, "order.cancel", {"id": doc["id"]})
        if status == "reserved" and doc["status"] == "draft":
            return apply(erp, db, key, "order.confirm", {"id": doc["id"]})
        if status == "draft" and doc["status"] == "reserved":
            header = receipt(db, doc["id"])
            if (header and header["status"] != "cancelled") or db.execute(
                "SELECT 1 FROM logistics_shipments WHERE id=?", (doc["id"],)
            ).fetchone():
                raise ERPError(
                    "sale_review", "Concilie contas e separação antes de voltar à digitação.", 409
                )
            for sku, quantity in data["requirements"].items():
                erp.move(
                    db,
                    key,
                    sku,
                    data["depot"],
                    Decimal(0),
                    -Decimal(quantity),
                    "retorno à digitação",
                )
            doc = {**dict(doc), "status": "draft"}
        elif (
            status == "draft"
            and doc["status"] != "draft"
            or status == "shipped"
            and doc["status"] != "shipped"
            or status == "returned"
            and doc["status"] != "returned"
            or status == "reserved"
            and doc["status"] not in {"reserved", "shipped"}
        ):
            raise ERPError(
                "sale_review", "Conclua ou estorne a movimentação física antes desta situação.", 409
            )
        data.update(situation=status, version=data.get("version", 0) + 1)
        erp.update_document(db, doc["id"], doc["status"], data)
        return {"id": doc["id"], "situation": status, "external_writes": False}
    if kind == "order.confirm":
        if doc["status"] == "reserved":
            return {"id": doc["id"], "unchanged": True}
        if doc["status"] != "draft":
            raise ERPError("sale_confirm", "Somente pedido em digitação pode ser confirmado.", 409)
        erp.items(db, data["items"])
        if data.get("fulfillment_items"):
            erp.items(db, data["fulfillment_items"])
        deposits.require_active(db, data["depot"], sale=True)
        for sku, quantity in data["requirements"].items():
            erp.move(
                db, key, sku, data["depot"], Decimal(0), Decimal(quantity), "confirmação de pedido"
            )
        data["situation"] = "reserved"
        data["version"] = data.get("version", 0) + 1
        erp.update_document(db, doc["id"], "reserved", data)
        return {"id": doc["id"], "status": "reserved", "external_writes": False}
    if kind in {"order.accounts", "order.reverse_accounts"}:
        if doc["status"] not in {"reserved", "shipped"}:
            raise ERPError(
                "sale_finance_status", "Confirme o pedido antes de lançar ou estornar contas.", 409
            )
        header = receipt(db, doc["id"])
        if kind == "order.reverse_accounts":
            if header and account_paid(db, header):
                raise ERPError(
                    "paid_account", "Estorne as baixas antes de estornar as contas.", 409
                )
            if header:
                db.execute("UPDATE accounts SET status='cancelled' WHERE id=?", (header["id"],))
                db.execute(
                    "UPDATE finance_installments SET active=0 WHERE account_id=?", (header["id"],)
                )
            data.update(accounts_posted=False, version=data.get("version", 0) + 1)
            erp.update_document(db, doc["id"], doc["status"], data)
            return {"id": doc["id"], "accounts_posted": False, "external_writes": False}
        if header and header["status"] != "cancelled":
            return {"id": doc["id"], "account_id": header["id"], "unchanged": True}
        if Decimal(data["total"]) == 0:
            return {"id": doc["id"], "zero_value": True, "external_writes": False}
        if header:
            if account_paid(db, header):
                raise ERPError(
                    "sale_finance_review",
                    "Concilie o histórico de baixas antes de relançar contas.",
                    409,
                )
            db.execute(
                "UPDATE accounts SET status='open',amount=? WHERE id=?",
                (data["total"], header["id"]),
            )
            account_id = header["id"]
        else:
            account_id = erp.account(db, doc["id"], "receivable", data["total"])
        data.update(accounts_posted=True, version=data.get("version", 0) + 1)
        erp.update_document(db, doc["id"], doc["status"], data)
        return {
            "id": doc["id"],
            "account_id": account_id,
            "accounts_posted": True,
            "external_writes": False,
        }
    if kind == "order.stock":
        data["accounts_policy"] = "manual"
        erp.update_document(db, doc["id"], doc["status"], data)
        result = erp.apply(db, key, "order.ship", {"id": doc["id"]}, _pos=bool(pos_cash_id))
        doc, data = erp.document(db, doc["id"], "order")
        data["stock_posted"] = True
        erp.update_document(db, doc["id"], doc["status"], data)
        return {**result, "external_writes": False}
    if db.execute(
        "SELECT 1 FROM return_cases WHERE order_id=? AND status!='cancelled'", (doc["id"],)
    ).fetchone():
        raise ERPError(
            "own_return_case", "Concilie a devolução registrada antes de estornar a saída.", 409
        )
    if db.execute("SELECT 1 FROM logistics_shipments WHERE id=?", (doc["id"],)).fetchone():
        raise ERPError(
            "logistics_required", "Use o recebimento físico da logística para este pedido.", 409
        )
    if doc["status"] == "reserved" and not data.get("stock_posted"):
        return {"id": doc["id"], "unchanged": True}
    if doc["status"] != "shipped":
        raise ERPError(
            "sale_stock_status", "O pedido não possui saída de estoque para estornar.", 409
        )
    for sku, quantity in data["requirements"].items():
        erp.move(
            db,
            key,
            sku,
            data["depot"],
            Decimal(quantity),
            Decimal(quantity),
            "estorno de estoque da venda",
            data.get("shipped_costs", {}).get(sku) or "unknown",
        )
    data["stock_posted"] = False
    data["version"] = data.get("version", 0) + 1
    erp.update_document(db, doc["id"], "reserved", data)
    return {"id": doc["id"], "status": "reserved", "external_writes": False}
