"""Referência de classificação Meli -> Status Bling (Pós-venda / Chamados).

Gerado da aba "Status Meli" de atualização2.xlsx (curadoria manual). NÃO é um
classificador determinístico: a mesma assinatura de status do Meli pode mapear
para vários Status Bling. `sugerir()` devolve os candidatos que a planilha já
viu pra os campos preenchidos, ordenados por frequência — é só uma dica pro
operador, nunca decide sozinho.
"""

from __future__ import annotations

# Ordem dos 8 campos de status do Meli que compõem a assinatura.
FIELD_ORDER: list[str] = [
    "order_status",
    "ship_status",
    "ship_substatus",
    "cancel_group",
    "return_status",
    "claim_stage",
    "claim_status",
    "benefited",
]

FIELD_LABELS: dict[str, str] = {
    "order_status": "Status do pedido",
    "ship_status": "Status do envio",
    "ship_substatus": "Substatus do envio",
    "cancel_group": "Quem cancelou",
    "return_status": "Status da devolução",
    "claim_stage": "Estágio da disputa",
    "claim_status": "Status da mediação",
    "benefited": "Beneficiado",
}

# Valores distintos vistos por campo (pra popular os selects do formulário).
FIELD_OPTIONS: dict[str, list[str]] = {
    "order_status": [
        "cancelled",
        "paid",
        "partially_refunded",
        "partially_refunded (reembolso) colocar na planilha",
    ],
    "ship_status": [
        "cancelled",
        "cancelled (erro de envio)",
        "delivered",
        "not_delivered",
        "pending",
        "ready_to_ship",
        "shipped",
    ],
    "ship_substatus": [
        "at_the_door rota de entrega",
        "bad_address end incorreto",
        "buffered ag lib etiqueta, com data programada",
        "claimed_me reclamaçao",
        "confiscated",
        "delayed atrasado",
        "dropped_off",
        "fraudulent",
        "in_hub",
        "in_hub centro distribuiçao",
        "in_packing_list",
        "in_packing_list (",
        "invoice_pending",
        "invoice_pending ag lib etiqueta",
        "lost",
        "lost perdido",
        "no_action_taken sem açao",
        "not_localized problema endereço",
        "not_picked_up_at_hub",
        "not_visited não entregue",
        "out_for_delivery",
        "out_for_delivery sai entrega",
        "picked_up",
        "printed",
        "ready_to_print etiqueta impressa",
        "receiver_absent destinataro ausente",
        "refused_delivery",
        "retained retido",
        "returned devoluçao",
        "returned_to_hub",
        "returned_to_hub devoluçao c vistoria",
        "returning_to_hub devoluçao c vistoria",
        "returning_to_sender",
        "returning_to_sender devoluçao",
        "soon_deliver enviado",
        "soon_to_be_returned devolvendo",
        "stale",
        "stale parado",
        "waiting_authority retido",
        "waiting_for_withdrawal ag retirada cliente",
    ],
    "cancel_group": [
        "buyer",
        "delivery",
        "fraud",
        "internal",
        "item",
        "mediations",
        "seller",
        "shipment",
    ],
    "return_status": [
        "cancelled",
        "delivered",
        "ready_to_ship",
        "shipped",
    ],
    "claim_stage": [
        "claim",
        "dispute",
        "none",
        "recontact",
    ],
    "claim_status": [
        "closed",
        "opened",
    ],
    "benefited": [
        "complainant",
        "complainant reembolsa cliente",
        "respondent",
        "respondent reembolsa vendedor",
    ],
}

# Tradução PT dos valores crus que a API do Meli devolve (tokens em inglês).
# Chaveado por campo → {token: rótulo}. `traduzir_valor` cai no token cru
# quando não há tradução (ex.: substatus novo que o Meli passe a emitir).
VALUE_LABELS_PT: dict[str, dict[str, str]] = {
    "order_status": {
        "paid": "Pago",
        "cancelled": "Cancelado",
        "confirmed": "Confirmado",
        "payment_required": "Aguardando pagamento",
        "payment_in_process": "Pagamento em processo",
        "partially_paid": "Parcialmente pago",
        "partially_refunded": "Reembolso parcial",
        "invalid": "Inválido",
    },
    "ship_status": {
        "pending": "Pendente",
        "handling": "Em preparação",
        "ready_to_ship": "Pronto p/ envio",
        "shipped": "Enviado",
        "delivered": "Entregue",
        "not_delivered": "Não entregue",
        "cancelled": "Cancelado",
        "to_be_agreed": "A combinar",
        "active": "Ativo",
    },
    "ship_substatus": {
        "ready_to_print": "Etiqueta pronta p/ imprimir",
        "printed": "Etiqueta impressa",
        "in_packing_list": "Na lista de coleta",
        "invoice_pending": "Aguardando NF",
        "picked_up": "Coletado",
        "in_hub": "No centro de distribuição",
        "dropped_off": "Entregue à agência",
        "first_mile": "Primeira milha",
        "buffered": "Programado",
        "out_for_delivery": "Saiu p/ entrega",
        "at_the_door": "Na porta / rota de entrega",
        "delivered": "Entregue",
        "receiver_absent": "Destinatário ausente",
        "not_visited": "Não visitado",
        "delayed": "Atrasado",
        "stale": "Parado",
        "lost": "Perdido",
        "bad_address": "Endereço incorreto",
        "not_localized": "Endereço não localizado",
        "refused_delivery": "Entrega recusada",
        "confiscated": "Confiscado",
        "fraudulent": "Fraude",
        "retained": "Retido",
        "waiting_authority": "Retido na alfândega",
        "waiting_for_withdrawal": "Aguardando retirada",
        "no_action_taken": "Sem ação",
        "not_picked_up_at_hub": "Não coletado no hub",
        "claimed_me": "Reclamação aberta",
        "returned": "Devolvido",
        "returned_to_hub": "Devolvido ao hub",
        "returning_to_hub": "Retornando ao hub",
        "returning_to_sender": "Retornando ao remetente",
        "soon_to_be_returned": "Em devolução",
        "soon_deliver": "A caminho",
    },
    "cancel_group": {
        "buyer": "Comprador",
        "seller": "Vendedor",
        "mediations": "Mediação",
        "delivery": "Entrega",
        "fraud": "Fraude",
        "internal": "Interno",
        "item": "Item",
        "shipment": "Envio",
        "respondent": "Vendedor",
        "complainant": "Comprador",
    },
    "return_status": {
        "pending": "Pendente",
        "ready_to_ship": "Pronto p/ envio",
        "shipped": "Enviado",
        "delivered": "Entregue",
        "cancelled": "Cancelado",
    },
    "claim_stage": {
        "claim": "Reclamação",
        "dispute": "Mediação",
        "recontact": "Recontato",
        "none": "Nenhum",
    },
    "claim_status": {
        "opened": "Aberta",
        "closed": "Fechada",
    },
    "benefited": {
        "complainant": "Comprador",
        "respondent": "Vendedor",
    },
}


def traduzir_valor(field: str, value: str | None) -> str:
    """Rótulo PT de um valor cru do Meli; cai no próprio token quando não há
    tradução cadastrada."""
    v = (value or "").strip()
    if not v:
        return ""
    return VALUE_LABELS_PT.get(field, {}).get(v, v)


def assinatura_pt(meli_status: dict[str, str] | None) -> str:
    """Assinatura em PT p/ exibir na coluna "Status Plataforma": os valores
    não-vazios dos 8 campos (na ORDEM fixa), traduzidos, juntados por " | "."""
    if not meli_status:
        return ""
    partes = [
        traduzir_valor(f, meli_status.get(f))
        for f in FIELD_ORDER
        if (meli_status.get(f) or "").strip()
    ]
    return " | ".join(p for p in partes if p)


def localizacao_pt(meli_status: dict[str, str] | None) -> str:
    """Proxy do "último local" a partir do envio do Meli: substatus do envio
    traduzido (mais específico) ou, na falta, o status do envio. O ML NÃO expõe
    o local físico — isto é a situação do envio na plataforma (opção 3)."""
    m = meli_status or {}
    sub = traduzir_valor("ship_substatus", m.get("ship_substatus"))
    if sub:
        return sub
    return traduzir_valor("ship_status", m.get("ship_status"))


# 223 linhas curadas (campos + status_bling resultante).
RULES: list[dict[str, str]] = [
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "internal", "return_status": "delivered", "claim_stage": "dispute", "claim_status": "closed", "benefited": "complainant", "status_bling": "Aguardando Devolução"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "delivered", "claim_stage": "claim", "claim_status": "closed", "benefited": "complainant", "status_bling": "Aguardando Devolução"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "delivered", "claim_stage": "dispute", "claim_status": "closed", "benefited": "complainant", "status_bling": "Aguardando Devolução"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "delivered", "claim_stage": "claim", "claim_status": "closed", "benefited": "complainant", "status_bling": "Manutenção"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "delivered", "claim_stage": "dispute", "claim_status": "closed", "benefited": "complainant", "status_bling": "Manutenção"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "delivered", "claim_stage": "recontact", "claim_status": "closed", "benefited": "complainant", "status_bling": "Manutenção"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "delivered", "claim_stage": "dispute", "claim_status": "closed", "benefited": "complainant", "status_bling": "Perdimento"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "delivered", "claim_stage": "dispute", "claim_status": "closed", "benefited": "complainant", "status_bling": "Problemas"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "delivered", "claim_stage": "claim", "claim_status": "closed", "benefited": "complainant", "status_bling": "Resolvido"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "delivered", "claim_stage": "dispute", "claim_status": "closed", "benefited": "complainant", "status_bling": "Resolvido"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "delivered", "claim_stage": "recontact", "claim_status": "closed", "benefited": "complainant", "status_bling": "Resolvido"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "delivered", "claim_stage": "recontact", "claim_status": "opened", "benefited": "complainant", "status_bling": "Resolvido"},
    {"order_status": "partially_refunded (reembolso) colocar na planilha", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "delivered", "claim_stage": "dispute", "claim_status": "closed", "benefited": "complainant", "status_bling": "Em aberto"},
    {"order_status": "partially_refunded", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "delivered", "claim_stage": "dispute", "claim_status": "closed", "benefited": "complainant", "status_bling": "Manutenção"},
    {"order_status": "partially_refunded", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "delivered", "claim_stage": "dispute", "claim_status": "closed", "benefited": "complainant", "status_bling": "Problemas"},
    {"order_status": "partially_refunded", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "delivered", "claim_stage": "dispute", "claim_status": "closed", "benefited": "complainant", "status_bling": "Resolvido"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "internal", "return_status": "ready_to_ship", "claim_stage": "dispute", "claim_status": "closed", "benefited": "complainant", "status_bling": "Aguardando Devolução"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "ready_to_ship", "claim_stage": "claim", "claim_status": "closed", "benefited": "complainant", "status_bling": "Aguardando Devolução"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "ready_to_ship", "claim_stage": "dispute", "claim_status": "closed", "benefited": "complainant", "status_bling": "Aguardando Devolução"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "ready_to_ship", "claim_stage": "dispute", "claim_status": "closed", "benefited": "complainant", "status_bling": "Em andamento"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "ready_to_ship", "claim_stage": "dispute", "claim_status": "closed", "benefited": "complainant", "status_bling": "Entregue"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "ready_to_ship", "claim_stage": "dispute", "claim_status": "closed", "benefited": "complainant", "status_bling": "Problemas"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "internal", "return_status": "shipped", "claim_stage": "dispute", "claim_status": "closed", "benefited": "complainant", "status_bling": "Aguardando Devolução"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "shipped", "claim_stage": "claim", "claim_status": "closed", "benefited": "complainant", "status_bling": "Aguardando Devolução"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "shipped", "claim_stage": "dispute", "claim_status": "closed", "benefited": "complainant", "status_bling": "Aguardando Devolução"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "shipped", "claim_stage": "dispute", "claim_status": "closed", "benefited": "complainant", "status_bling": "Em andamento"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "", "claim_stage": "claim", "claim_status": "closed", "benefited": "complainant", "status_bling": "Aguardando Devolução"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "", "claim_stage": "dispute", "claim_status": "closed", "benefited": "complainant", "status_bling": "Aguardando Devolução"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "", "claim_stage": "claim", "claim_status": "closed", "benefited": "complainant", "status_bling": "Manutenção"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "", "claim_stage": "dispute", "claim_status": "closed", "benefited": "complainant", "status_bling": "Manutenção"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "", "claim_stage": "dispute", "claim_status": "closed", "benefited": "complainant", "status_bling": "Problemas"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "", "claim_stage": "claim", "claim_status": "closed", "benefited": "complainant", "status_bling": "Resolvido"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "", "claim_stage": "dispute", "claim_status": "closed", "benefited": "complainant", "status_bling": "Resolvido"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "", "claim_stage": "recontact", "claim_status": "closed", "benefited": "complainant", "status_bling": "Resolvido"},
    {"order_status": "partially_refunded", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "", "claim_stage": "dispute", "claim_status": "closed", "benefited": "complainant", "status_bling": "Aguardando Devolução"},
    {"order_status": "partially_refunded", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "", "claim_stage": "dispute", "claim_status": "closed", "benefited": "complainant", "status_bling": "Em andamento"},
    {"order_status": "partially_refunded", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "", "claim_stage": "claim", "claim_status": "closed", "benefited": "complainant", "status_bling": "Entregue"},
    {"order_status": "partially_refunded", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "", "claim_stage": "dispute", "claim_status": "closed", "benefited": "complainant", "status_bling": "Entregue"},
    {"order_status": "partially_refunded", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "", "claim_stage": "recontact", "claim_status": "closed", "benefited": "complainant", "status_bling": "Entregue"},
    {"order_status": "partially_refunded", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "", "claim_stage": "recontact", "claim_status": "opened", "benefited": "complainant", "status_bling": "Entregue"},
    {"order_status": "partially_refunded", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "", "claim_stage": "dispute", "claim_status": "closed", "benefited": "complainant", "status_bling": "Manutenção"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "cancelled", "claim_stage": "dispute", "claim_status": "closed", "benefited": "complainant reembolsa cliente", "status_bling": "Problemas"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "cancelled", "claim_stage": "dispute", "claim_status": "closed", "benefited": "respondent", "status_bling": "Aguardando Devolução"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "cancelled", "claim_stage": "dispute", "claim_status": "closed", "benefited": "respondent", "status_bling": "Entregue"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "delivered", "claim_stage": "dispute", "claim_status": "closed", "benefited": "respondent", "status_bling": "Entregue"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "delivered", "claim_stage": "dispute", "claim_status": "closed", "benefited": "respondent", "status_bling": "Resolvido"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "delivered", "claim_stage": "claim", "claim_status": "closed", "benefited": "respondent", "status_bling": "Aguardando Devolução"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "delivered", "claim_stage": "dispute", "claim_status": "closed", "benefited": "respondent", "status_bling": "Aguardando Devolução"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "delivered", "claim_stage": "claim", "claim_status": "closed", "benefited": "respondent", "status_bling": "Entregue"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "delivered", "claim_stage": "dispute", "claim_status": "closed", "benefited": "respondent", "status_bling": "Entregue"},
    {"order_status": "partially_refunded", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "delivered", "claim_stage": "dispute", "claim_status": "closed", "benefited": "respondent", "status_bling": "Resolvido"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "ready_to_ship", "claim_stage": "dispute", "claim_status": "closed", "benefited": "respondent", "status_bling": "Entregue"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "returned devoluçao", "cancel_group": "mediations", "return_status": "", "claim_stage": "dispute", "claim_status": "closed", "benefited": "respondent", "status_bling": "Resolvido"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "", "claim_stage": "dispute", "claim_status": "closed", "benefited": "respondent", "status_bling": "Entregue"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "", "claim_stage": "dispute", "claim_status": "closed", "benefited": "respondent", "status_bling": "Manutenção"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "", "claim_stage": "dispute", "claim_status": "closed", "benefited": "respondent", "status_bling": "Resolvido"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "", "claim_stage": "dispute", "claim_status": "closed", "benefited": "respondent", "status_bling": "Aguardando Devolução"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "", "claim_stage": "claim", "claim_status": "closed", "benefited": "respondent", "status_bling": "Entregue"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "", "claim_stage": "dispute", "claim_status": "closed", "benefited": "respondent", "status_bling": "Entregue"},
    {"order_status": "partially_refunded", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "", "claim_stage": "dispute", "claim_status": "closed", "benefited": "respondent", "status_bling": "Entregue"},
    {"order_status": "partially_refunded", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "", "claim_stage": "recontact", "claim_status": "closed", "benefited": "respondent", "status_bling": "Entregue"},
    {"order_status": "partially_refunded", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "", "claim_stage": "recontact", "claim_status": "opened", "benefited": "respondent", "status_bling": "Entregue"},
    {"order_status": "partially_refunded", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "", "claim_stage": "dispute", "claim_status": "closed", "benefited": "respondent", "status_bling": "Resolvido"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "cancelled", "claim_stage": "dispute", "claim_status": "closed", "benefited": "respondent reembolsa vendedor", "status_bling": "Resolvido"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "cancelled", "claim_stage": "dispute", "claim_status": "closed", "benefited": "", "status_bling": "Resolvido"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "cancelled", "claim_stage": "claim", "claim_status": "closed", "benefited": "", "status_bling": "Aguardando Devolução"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "cancelled", "claim_stage": "dispute", "claim_status": "closed", "benefited": "", "status_bling": "Aguardando Devolução"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "cancelled", "claim_stage": "dispute", "claim_status": "closed", "benefited": "", "status_bling": "Em andamento"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "cancelled", "claim_stage": "claim", "claim_status": "closed", "benefited": "", "status_bling": "Entregue"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "cancelled", "claim_stage": "dispute", "claim_status": "closed", "benefited": "", "status_bling": "Entregue"},
    {"order_status": "partially_refunded", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "cancelled", "claim_stage": "dispute", "claim_status": "closed", "benefited": "", "status_bling": "Entregue"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "delivered", "claim_stage": "claim", "claim_status": "opened", "benefited": "", "status_bling": "Aguardando Devolução"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "delivered", "claim_stage": "dispute", "claim_status": "opened", "benefited": "", "status_bling": "Aguardando Devolução"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "delivered", "claim_stage": "dispute", "claim_status": "opened", "benefited": "", "status_bling": "Problemas"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "delivered", "claim_stage": "claim", "claim_status": "opened", "benefited": "", "status_bling": "Resolvido"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "delivered", "claim_stage": "dispute", "claim_status": "opened", "benefited": "", "status_bling": "Resolvido"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "ready_to_ship", "claim_stage": "claim", "claim_status": "opened", "benefited": "", "status_bling": "Aguardando Devolução"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "ready_to_ship", "claim_stage": "dispute", "claim_status": "opened", "benefited": "", "status_bling": "Aguardando Devolução"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "ready_to_ship", "claim_stage": "claim", "claim_status": "opened", "benefited": "", "status_bling": "Em andamento"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "ready_to_ship", "claim_stage": "dispute", "claim_status": "opened", "benefited": "", "status_bling": "Em andamento"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "ready_to_ship", "claim_stage": "claim", "claim_status": "opened", "benefited": "", "status_bling": "Entregue"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "ready_to_ship", "claim_stage": "dispute", "claim_status": "opened", "benefited": "", "status_bling": "Entregue"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "shipped", "claim_stage": "claim", "claim_status": "opened", "benefited": "", "status_bling": "Aguardando Devolução"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "shipped", "claim_stage": "dispute", "claim_status": "opened", "benefited": "", "status_bling": "Aguardando Devolução"},
    {"order_status": "cancelled", "ship_status": "cancelled", "ship_substatus": "", "cancel_group": "buyer", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Aguardando Cancelamento"},
    {"order_status": "cancelled", "ship_status": "cancelled", "ship_substatus": "", "cancel_group": "buyer", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Cancelado"},
    {"order_status": "cancelled", "ship_status": "cancelled", "ship_substatus": "", "cancel_group": "buyer", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Em aberto"},
    {"order_status": "cancelled", "ship_status": "cancelled (erro de envio)", "ship_substatus": "", "cancel_group": "buyer", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Enviado Etiqueta"},
    {"order_status": "cancelled", "ship_status": "cancelled", "ship_substatus": "", "cancel_group": "buyer", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Resolvido"},
    {"order_status": "cancelled", "ship_status": "cancelled", "ship_substatus": "", "cancel_group": "buyer", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Verificar Cancelamento"},
    {"order_status": "cancelled", "ship_status": "", "ship_substatus": "", "cancel_group": "buyer", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Cancelado"},
    {"order_status": "cancelled", "ship_status": "cancelled", "ship_substatus": "", "cancel_group": "delivery", "return_status": "", "claim_stage": "none", "claim_status": "closed", "benefited": "", "status_bling": "Cancelado"},
    {"order_status": "cancelled", "ship_status": "cancelled", "ship_substatus": "fraudulent", "cancel_group": "fraud", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Cancelado"},
    {"order_status": "cancelled", "ship_status": "cancelled", "ship_substatus": "fraudulent", "cancel_group": "fraud", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Verificar Cancelamento"},
    {"order_status": "cancelled", "ship_status": "pending", "ship_substatus": "fraudulent", "cancel_group": "fraud", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Cancelado"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "returned devoluçao", "cancel_group": "fraud", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Resolvido"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "returned devoluçao", "cancel_group": "fraud", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Aguardando Devolução"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "returned_to_hub devoluçao c vistoria", "cancel_group": "fraud", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Aguardando Devolução"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "returned_to_hub", "cancel_group": "fraud", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Em andamento"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "confiscated", "cancel_group": "internal", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Problemas"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "returned devoluçao", "cancel_group": "internal", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Aguardando Devolução"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "returned devoluçao", "cancel_group": "internal", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Erro no Envio"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "returned devoluçao", "cancel_group": "internal", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Problemas"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "returned devoluçao", "cancel_group": "internal", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Resolvido"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "returned_to_hub", "cancel_group": "internal", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Aguardando Devolução"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "returned_to_hub", "cancel_group": "internal", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Em andamento"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "returned_to_hub", "cancel_group": "internal", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Erro no Envio"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "returning_to_hub devoluçao c vistoria", "cancel_group": "internal", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Aguardando Devolução"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "returning_to_sender devoluçao", "cancel_group": "internal", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Aguardando Devolução"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "returning_to_sender", "cancel_group": "internal", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Em andamento"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "returning_to_sender", "cancel_group": "internal", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Erro no Envio"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "returning_to_sender", "cancel_group": "internal", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Problemas"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "soon_to_be_returned devolvendo", "cancel_group": "internal", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Aguardando Devolução"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "waiting_authority retido", "cancel_group": "internal", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Em andamento"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "waiting_authority retido", "cancel_group": "internal", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Enviado Etiqueta"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "waiting_authority retido", "cancel_group": "internal", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Erro no Envio"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "waiting_authority retido", "cancel_group": "internal", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Problemas"},
    {"order_status": "cancelled", "ship_status": "cancelled", "ship_substatus": "", "cancel_group": "internal", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Cancelado"},
    {"order_status": "cancelled", "ship_status": "", "ship_substatus": "", "cancel_group": "internal", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Resolvido"},
    {"order_status": "cancelled", "ship_status": "cancelled", "ship_substatus": "", "cancel_group": "item", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Cancelado"},
    {"order_status": "cancelled", "ship_status": "cancelled", "ship_substatus": "", "cancel_group": "item", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Verificar Cancelamento"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "claimed_me reclamaçao", "cancel_group": "mediations", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Entregue"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "lost perdido", "cancel_group": "mediations", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Entregue"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "returned devoluçao", "cancel_group": "mediations", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Aguardando Devolução"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "returned devoluçao", "cancel_group": "mediations", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Em andamento"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "returned devoluçao", "cancel_group": "mediations", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Resolvido"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "returned_to_hub", "cancel_group": "mediations", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Aguardando Devolução"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "returned_to_hub", "cancel_group": "mediations", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Em andamento"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "returning_to_sender", "cancel_group": "mediations", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Aguardando Devolução"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "returning_to_sender", "cancel_group": "mediations", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Resolvido"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "waiting_authority retido", "cancel_group": "mediations", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Aguardando Devolução"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "waiting_authority retido", "cancel_group": "mediations", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Problemas"},
    {"order_status": "cancelled", "ship_status": "cancelled", "ship_substatus": "", "cancel_group": "mediations", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Cancelado"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Cancelado"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Entregue"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "", "claim_stage": "claim", "claim_status": "closed", "benefited": "", "status_bling": "Entregue"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Manutenção"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Problemas"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Resolvido"},
    {"order_status": "cancelled", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "mediations", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Verificar Cancelamento"},
    {"order_status": "cancelled", "ship_status": "cancelled", "ship_substatus": "", "cancel_group": "seller", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Cancelado"},
    {"order_status": "cancelled", "ship_status": "cancelled", "ship_substatus": "", "cancel_group": "seller", "return_status": "", "claim_stage": "none", "claim_status": "closed", "benefited": "", "status_bling": "Cancelado"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "fraudulent", "cancel_group": "shipment", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Resolvido"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "lost", "cancel_group": "shipment", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Entregue"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "lost", "cancel_group": "shipment", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Resolvido"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "retained retido", "cancel_group": "shipment", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Perdimento"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "retained retido", "cancel_group": "shipment", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Resolvido"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "returned devoluçao", "cancel_group": "shipment", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Aguardando Devolução"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "returned devoluçao", "cancel_group": "shipment", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Problemas"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "returned devoluçao", "cancel_group": "shipment", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Resolvido"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "returned_to_hub", "cancel_group": "shipment", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Aguardando Devolução"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "returned_to_hub", "cancel_group": "shipment", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Em andamento"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "returning_to_sender", "cancel_group": "shipment", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Aguardando Devolução"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "returning_to_sender", "cancel_group": "shipment", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Em andamento"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "returning_to_sender", "cancel_group": "shipment", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Entregue"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "returning_to_sender", "cancel_group": "shipment", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Problemas"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "returning_to_sender", "cancel_group": "shipment", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Resolvido"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "waiting_authority retido", "cancel_group": "shipment", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Em andamento"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "waiting_authority retido", "cancel_group": "shipment", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Erro no Envio"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "waiting_authority retido", "cancel_group": "shipment", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Problemas"},
    {"order_status": "cancelled", "ship_status": "not_delivered", "ship_substatus": "waiting_authority retido", "cancel_group": "shipment", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Resolvido"},
    {"order_status": "cancelled", "ship_status": "cancelled", "ship_substatus": "", "cancel_group": "shipment", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Aguardando Cancelamento"},
    {"order_status": "cancelled", "ship_status": "cancelled", "ship_substatus": "", "cancel_group": "shipment", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Cancelado"},
    {"order_status": "cancelled", "ship_status": "cancelled", "ship_substatus": "", "cancel_group": "shipment", "return_status": "", "claim_stage": "none", "claim_status": "closed", "benefited": "", "status_bling": "Cancelado"},
    {"order_status": "cancelled", "ship_status": "", "ship_substatus": "", "cancel_group": "shipment", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Cancelado"},
    {"order_status": "paid", "ship_status": "shipped", "ship_substatus": "at_the_door rota de entrega", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Em andamento"},
    {"order_status": "paid", "ship_status": "shipped", "ship_substatus": "bad_address end incorreto", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Em andamento"},
    {"order_status": "paid", "ship_status": "pending", "ship_substatus": "buffered ag lib etiqueta, com data programada", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Aguardando Cancelamento"},
    {"order_status": "paid", "ship_status": "pending", "ship_substatus": "buffered ag lib etiqueta, com data programada", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Em aberto"},
    {"order_status": "paid", "ship_status": "shipped", "ship_substatus": "delayed atrasado", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Em andamento"},
    {"order_status": "paid", "ship_status": "ready_to_ship", "ship_substatus": "dropped_off", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Em andamento"},
    {"order_status": "paid", "ship_status": "ready_to_ship", "ship_substatus": "dropped_off", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Enviado Etiqueta"},
    {"order_status": "paid", "ship_status": "not_delivered", "ship_substatus": "fraudulent", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Resolvido"},
    {"order_status": "paid", "ship_status": "ready_to_ship", "ship_substatus": "in_hub centro distribuiçao", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Em andamento"},
    {"order_status": "paid", "ship_status": "ready_to_ship", "ship_substatus": "in_hub", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Enviado Etiqueta"},
    {"order_status": "paid", "ship_status": "ready_to_ship", "ship_substatus": "in_packing_list (", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Em andamento"},
    {"order_status": "paid", "ship_status": "ready_to_ship", "ship_substatus": "in_packing_list", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Enviado Etiqueta"},
    {"order_status": "paid", "ship_status": "ready_to_ship", "ship_substatus": "invoice_pending ag lib etiqueta", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Aguardando Cancelamento"},
    {"order_status": "paid", "ship_status": "ready_to_ship", "ship_substatus": "invoice_pending", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Atendido"},
    {"order_status": "paid", "ship_status": "ready_to_ship", "ship_substatus": "invoice_pending", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Em aberto"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "no_action_taken sem açao", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Entregue"},
    {"order_status": "paid", "ship_status": "shipped", "ship_substatus": "not_localized problema endereço", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Em andamento"},
    {"order_status": "paid", "ship_status": "shipped", "ship_substatus": "not_picked_up_at_hub", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Em andamento"},
    {"order_status": "paid", "ship_status": "shipped", "ship_substatus": "not_visited não entregue", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Em andamento"},
    {"order_status": "paid", "ship_status": "shipped", "ship_substatus": "out_for_delivery sai entrega", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Em andamento"},
    {"order_status": "paid", "ship_status": "shipped", "ship_substatus": "out_for_delivery", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Entregue"},
    {"order_status": "paid", "ship_status": "shipped", "ship_substatus": "out_for_delivery", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Enviado Etiqueta"},
    {"order_status": "paid", "ship_status": "ready_to_ship", "ship_substatus": "picked_up", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Em andamento"},
    {"order_status": "paid", "ship_status": "ready_to_ship", "ship_substatus": "picked_up", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Enviado Etiqueta"},
    {"order_status": "paid", "ship_status": "ready_to_ship", "ship_substatus": "printed", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Aguardando Cancelamento"},
    {"order_status": "paid", "ship_status": "ready_to_ship", "ship_substatus": "printed", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Em aberto"},
    {"order_status": "paid", "ship_status": "ready_to_ship", "ship_substatus": "printed", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Enviado Etiqueta"},
    {"order_status": "paid", "ship_status": "ready_to_ship", "ship_substatus": "ready_to_print etiqueta impressa", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Em aberto"},
    {"order_status": "paid", "ship_status": "shipped", "ship_substatus": "receiver_absent destinataro ausente", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Em andamento"},
    {"order_status": "paid", "ship_status": "shipped", "ship_substatus": "refused_delivery", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Aguardando Devolução"},
    {"order_status": "paid", "ship_status": "shipped", "ship_substatus": "refused_delivery", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Em andamento"},
    {"order_status": "paid", "ship_status": "not_delivered", "ship_substatus": "retained retido", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Em andamento"},
    {"order_status": "paid", "ship_status": "shipped", "ship_substatus": "soon_deliver enviado", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Em andamento"},
    {"order_status": "paid", "ship_status": "shipped", "ship_substatus": "stale parado", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Em andamento"},
    {"order_status": "paid", "ship_status": "shipped", "ship_substatus": "stale", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Entregue"},
    {"order_status": "paid", "ship_status": "shipped", "ship_substatus": "waiting_for_withdrawal ag retirada cliente", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Em andamento"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Aguardando Devolução"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "", "claim_stage": "claim", "claim_status": "closed", "benefited": "", "status_bling": "Aguardando Devolução"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "", "claim_stage": "claim", "claim_status": "opened", "benefited": "", "status_bling": "Aguardando Devolução"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "", "claim_stage": "dispute", "claim_status": "opened", "benefited": "", "status_bling": "Aguardando Devolução"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Em andamento"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "", "claim_stage": "claim", "claim_status": "opened", "benefited": "", "status_bling": "Em andamento"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "", "claim_stage": "dispute", "claim_status": "opened", "benefited": "", "status_bling": "Em andamento"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Entregue"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "", "claim_stage": "claim", "claim_status": "closed", "benefited": "", "status_bling": "Entregue"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "", "claim_stage": "claim", "claim_status": "opened", "benefited": "", "status_bling": "Entregue"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "", "claim_stage": "dispute", "claim_status": "closed", "benefited": "", "status_bling": "Entregue"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "", "claim_stage": "dispute", "claim_status": "opened", "benefited": "", "status_bling": "Entregue"},
    {"order_status": "partially_refunded", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Entregue"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Enviado Etiqueta"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Manutenção"},
    {"order_status": "partially_refunded", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Problemas"},
    {"order_status": "paid", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Resolvido"},
    {"order_status": "partially_refunded", "ship_status": "delivered", "ship_substatus": "", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Resolvido"},
    {"order_status": "paid", "ship_status": "shipped", "ship_substatus": "", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Em andamento"},
    {"order_status": "paid", "ship_status": "shipped", "ship_substatus": "", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Entregue"},
    {"order_status": "paid", "ship_status": "shipped", "ship_substatus": "", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Enviado Etiqueta"},
    {"order_status": "paid", "ship_status": "", "ship_substatus": "", "cancel_group": "", "return_status": "", "claim_stage": "", "claim_status": "", "benefited": "", "status_bling": "Entregue"},
]


def sugerir(selection: dict[str, str | None]) -> list[dict[str, object]]:
    """Dado um dict parcial dos 8 campos, casa as regras cujos campos PREENCHIDOS
    batem exatamente (campos vazios do operador são ignorados = curinga) e
    devolve os Status Bling candidatos ordenados por frequência decrescente.
    """
    sel = {f: (str(selection.get(f) or "").strip()) for f in FIELD_ORDER}
    active = {f: v for f, v in sel.items() if v}
    if not active:
        # Sem nenhum campo preenchido não faz sentido sugerir tudo.
        return []
    counts: dict[str, int] = {}
    for rule in RULES:
        if all(rule[f] == v for f, v in active.items()):
            counts[rule["status_bling"]] = counts.get(rule["status_bling"], 0) + 1
    out = [{"status_bling": k, "matches": v} for k, v in counts.items()]
    out.sort(key=lambda x: (-x["matches"], x["status_bling"]))
    return out
