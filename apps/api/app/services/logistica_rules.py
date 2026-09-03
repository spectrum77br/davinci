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


# --- Shopee ---------------------------------------------------------------
# A assinatura da Shopee é o `order_status` do pedido (a API v2 já entrega isso
# em lote via get_order_status_map). É um vocabulário PRÓPRIO — nada a ver com
# os 8 campos do Meli — então a chave que o usuário cadastra na aba Status pra
# Shopee é só esse rótulo (ex. "Devolução solicitada"). Camadas futuras
# (rastreio/devolução detalhada) podem enriquecer, mas o order_status já casa a
# maioria dos casos de pós-venda (cancelamento, devolução, concluído).
SHOPEE_STATUS_LABELS_PT: dict[str, str] = {
    "UNPAID": "Não pago",
    "READY_TO_SHIP": "Pronto p/ envio",
    "PROCESSED": "Processado",
    "RETRY_SHIP": "Reenvio",
    "SHIPPED": "Enviado",
    "TO_CONFIRM_RECEIVE": "Aguardando confirmação",
    "IN_CANCEL": "Cancelamento em andamento",
    "CANCELLED": "Cancelado",
    "TO_RETURN": "Devolução solicitada",
    "COMPLETED": "Concluído",
    "INVOICE_PENDING": "Nota pendente",
}

# Rótulos de `Logistica.plataforma` que representam cada marketplace.
_ML_PLATAFORMAS = {"mercado livre", "mercadolivre", "ml"}
_SHOPEE_PLATAFORMAS = {"shopee"}
_TIKTOK_PLATAFORMAS = {"tiktok", "tik tok", "tiktok shop"}
_AMAZON_PLATAFORMAS = {"amazon"}


# Situações da returns API em que o caso de devolução está ENCERRADO (desistido
# ou fechado). Qualquer outra (REQUESTED/PROCESSING/ACCEPTED/JUDGING/
# REFUND_PAID/SELLER_DISPUTE) conta como devolução VIVA.
_SHOPEE_RETURN_ENCERRADO = {"CANCELLED", "CLOSED"}


def assinatura_shopee(status: dict[str, str] | None) -> str:
    """Assinatura em PT da Shopee = o `order_status` traduzido. Vazio se não
    houver status.

    Exceção: devolução aberta DEPOIS da entrega não aparece no order_status —
    o pedido segue COMPLETED enquanto a returns API mostra o caso vivo (real:
    pedido 290580, 27/08). O sweep de pós-venda grava esse sinal em
    `return_status`; havendo um vivo, a assinatura vira "Devolução solicitada"
    pra regra da aba Status disparar igual ao TO_RETURN clássico."""
    ret = ((status or {}).get("return_status") or "").strip().upper()
    if ret and ret not in _SHOPEE_RETURN_ENCERRADO:
        return SHOPEE_STATUS_LABELS_PT["TO_RETURN"]
    v = ((status or {}).get("order_status") or "").strip().upper()
    if not v:
        return ""
    return SHOPEE_STATUS_LABELS_PT.get(v, v.replace("_", " ").title())


# --- TikTok ---------------------------------------------------------------
# A assinatura do TikTok é o `status` do pedido (Order API 202309): um único
# campo com vocabulário PRÓPRIO. O rastreio físico vem dos eventos de tracking
# (descrição em inglês) e alimenta a localização — não entra na assinatura.
TIKTOK_STATUS_LABELS_PT: dict[str, str] = {
    "UNPAID": "Não pago",
    "ON_HOLD": "Em espera",
    "AWAITING_SHIPMENT": "Aguardando envio",
    "AWAITING_COLLECTION": "Aguardando coleta",
    "PARTIALLY_SHIPPING": "Envio parcial",
    "IN_TRANSIT": "Em trânsito",
    "DELIVERED": "Entregue",
    "COMPLETED": "Concluído",
    "CANCELLED": "Cancelado",
}


# Situações da returns API do TikTok em que o caso de devolução está ENCERRADO
# (desistido ou recusado). Qualquer outra (REQUEST_PENDING, AWAITING_BUYER_SHIP,
# BUYER_SHIPPED_ITEM, ..._COMPLETE, ...) conta como devolução VIVA. As duas
# grafias de REJECT por segurança (a doc da TikTok oscila).
_TIKTOK_RETURN_ENCERRADO = {
    "RETURN_OR_REFUND_REQUEST_CANCEL",
    "RETURN_OR_REFUND_REQUEST_REJECT",
    "REFUND_OR_RETURN_REQUEST_REJECT",
}


def assinatura_tiktok(status: dict[str, str] | None) -> str:
    """Assinatura em PT do TikTok = o `order_status` traduzido. Vazio se não
    houver status.

    Exceção: o vocabulário de order_status do TikTok NEM TEM devolução — o
    pedido segue DELIVERED/COMPLETED enquanto a returns API mostra o caso vivo
    (real: 585411441781475242 e cia., 28/08). O sweep de pós-venda grava esse
    sinal em `return_status`; havendo um vivo, a assinatura vira "Devolução
    solicitada" (mesmo rótulo da Shopee) pra regra da aba Status disparar."""
    ret = ((status or {}).get("return_status") or "").strip().upper()
    if ret and ret not in _TIKTOK_RETURN_ENCERRADO:
        return "Devolução solicitada"
    v = ((status or {}).get("order_status") or "").strip().upper()
    if not v:
        return ""
    return TIKTOK_STATUS_LABELS_PT.get(v, v.replace("_", " ").title())


# --- Devolução (Shopee/TikTok) em PT, pra aba Acompanhamento -----------------
# Eduardo 03/09: "tem mais um monte de pedido entregue e só vem em
# acompanhamentos" — a coluna "Última localização" mostrava a ENTREGA original
# ("Pedido entregue") enquanto a devolução, aberta depois, seguia viva. Aqui o
# status da devolução vira texto de gente, e a aba o mostra no lugar da entrega
# quando o caso está vivo (a entrega vai pro tooltip).
_SHOPEE_RETURN_LABELS_PT = {
    "REQUESTED": "Devolução solicitada pelo cliente — aguardando análise",
    "PROCESSING": "Devolução em processamento (Shopee)",
    "JUDGING": "Devolução em análise pela Shopee",
    "ACCEPTED": "Devolução aceita",
    "SELLER_DISPUTE": "Devolução contestada pelo vendedor",
    "REFUND_PAID": "Reembolso pago pela Shopee",
}
_TIKTOK_RETURN_LABELS_PT = {
    "RETURN_OR_REFUND_REQUEST_PENDING": "Devolução solicitada — aguardando resposta",
    "AWAITING_BUYER_SHIP": "Devolução aprovada — aguardando o cliente enviar",
    "BUYER_SHIPPED_ITEM": "Cliente enviou o item de volta",
    "RETURN_OR_REFUND_REQUEST_SUCCESS": "Devolução concluída (TikTok)",
    "RETURN_OR_REFUND_REQUEST_COMPLETE": "Devolução concluída (TikTok)",
}
# ML: `return_status` = status do ENVIO da devolução (shipment do return do
# claim — logistica_meli.returns_por_pedido / build_enrichment).
_ML_RETURN_LABELS_PT = {
    "PENDING": "Devolução pendente (Mercado Livre)",
    "READY_TO_SHIP": "Devolução aprovada — aguardando o cliente postar",
    "HANDLING": "Devolução em preparação (Mercado Livre)",
    "SHIPPED": "Devolução a caminho (Mercado Envios)",
    "DELIVERED": "Devolução entregue ao vendedor",
    "NOT_DELIVERED": "Devolução não entregue — verificar com o Mercado Livre",
}
_ML_RETURN_ENCERRADO = {"CANCELLED", "CANCELED", "CLOSED", "EXPIRED", "REJECTED"}


def devolucao_status_pt(plataforma: str | None, status: dict[str, str] | None) -> str | None:
    """Texto em PT da devolução VIVA de Shopee/TikTok/ML, ou None quando não
    há caso aberto (sem `return_status`, ou encerrado — cancelado/recusado).
    Status vivo sem tradução vira "Devolução: <STATUS>" (nunca esconde)."""
    ret = ((status or {}).get("return_status") or "").strip().upper()
    if not ret:
        return None
    p = (plataforma or "").strip().lower()
    if p in _SHOPEE_PLATAFORMAS:
        if ret in _SHOPEE_RETURN_ENCERRADO:
            return None
        return _SHOPEE_RETURN_LABELS_PT.get(ret, f"Devolução: {ret}")
    if p in _TIKTOK_PLATAFORMAS:
        if ret in _TIKTOK_RETURN_ENCERRADO:
            return None
        return _TIKTOK_RETURN_LABELS_PT.get(ret, f"Devolução: {ret}")
    if p in _ML_PLATAFORMAS:
        if ret in _ML_RETURN_ENCERRADO:
            return None
        return _ML_RETURN_LABELS_PT.get(ret, f"Devolução: {ret}")
    return None


# Valores dos campos do Status Plataforma que dizem "o pacote está voltando /
# a venda caiu" — o carimbo (status_datas) desses campos é a melhor estimativa
# de QUANDO o pedido entrou em devolução quando não há caso de devolução no
# marketplace (recusa/não entrega) e o Bling não expõe o histórico pela API.
_SINAL_DEVOLUCAO = {
    "ml": {
        "ship_substatus": {
            "returning_to_sender", "returned_to_hub", "refused_delivery", "refused",
            "returned_to_sender", "returned", "receiver_absent", "not_delivered",
        },
        "order_status": {"cancelled"},
        "return_status": None,  # qualquer valor
    },
    "shopee": {
        "order_status": {"CANCELLED", "TO_RETURN", "IN_CANCEL"},
        "logistics_status": {"LOGISTICS_DELIVERY_FAILED", "LOGISTICS_LOST", "LOGISTICS_RETURNED"},
        "return_status": None,
    },
    "tiktok": {
        "order_status": {"CANCELLED"},
        "return_status": None,
    },
    "amazon": {
        "easyship_status": {
            "RETURNINGTOSELLER", "RETURNEDTOSELLER", "REJECTEDBYBUYER", "UNDELIVERABLE",
            "RETURNING", "RETURNED",
        },
        "order_status": {"CANCELED", "CANCELLED"},
    },
}


def data_entrada_devolucao_estimada(
    plataforma: str | None,
    meli_status: dict[str, str] | None,
    status_datas: dict[str, dict[str, str]] | None,
) -> str | None:
    """ISO do carimbo mais ANTIGO entre os campos do Status Plataforma cujo
    valor atual sinaliza devolução/queda da venda (ver _SINAL_DEVOLUCAO). None
    quando nada sinaliza ou não há carimbo. Usado pelo Acompanhamento como
    "Em devolução desde" quando o marketplace não tem caso de devolução."""
    p = (plataforma or "").strip().lower()
    if p in _ML_PLATAFORMAS:
        key = "ml"
    elif p in _SHOPEE_PLATAFORMAS:
        key = "shopee"
    elif p in _TIKTOK_PLATAFORMAS:
        key = "tiktok"
    elif p in _AMAZON_PLATAFORMAS:
        key = "amazon"
    else:
        return None
    ms = meli_status or {}
    sd = status_datas or {}
    candidatos: list[str] = []
    for campo, valores in _SINAL_DEVOLUCAO[key].items():
        atual = str(ms.get(campo) or "").strip()
        if not atual:
            continue
        if valores is not None and atual.upper() not in {v.upper() for v in valores}:
            continue
        em = (sd.get(campo) or {}).get("em") if isinstance(sd.get(campo), dict) else None
        if em:
            candidatos.append(str(em))
    return min(candidatos) if candidatos else None


# --- Amazon ---------------------------------------------------------------
# A Amazon NÃO expõe número de rastreio pelo Orders API (o /shipment dá 403 sem
# escopo), então a assinatura combina o `order_status` (OrderStatus) com o
# `easyship_status` (EasyShipShipmentStatus) — os dois sinais que a API dá.
AMAZON_ORDER_LABELS_PT: dict[str, str] = {
    "PENDING": "Pendente",
    "UNSHIPPED": "Não enviado",
    "PARTIALLYSHIPPED": "Parcialmente enviado",
    "SHIPPED": "Enviado",
    "CANCELED": "Cancelado",
    "CANCELLED": "Cancelado",
    "UNFULFILLABLE": "Não atendível",
    "INVOICEUNCONFIRMED": "Nota não confirmada",
    "PENDINGAVAILABILITY": "Aguardando disponibilidade",
}
AMAZON_EASYSHIP_LABELS_PT: dict[str, str] = {
    "PENDINGPICKUP": "Aguardando coleta",
    "LABELCANCELED": "Etiqueta cancelada",
    "PICKEDUP": "Coletado",
    "OUTFORDELIVERY": "Saiu p/ entrega",
    "DAMAGED": "Avariado",
    "DELIVERED": "Entregue",
    "REJECTEDBYBUYER": "Recusado pelo comprador",
    "UNDELIVERABLE": "Não entregável",
    "RETURNEDTOSELLER": "Devolvido ao vendedor",
    "RETURNINGTOSELLER": "Retornando ao vendedor",
    "LOST": "Extraviado",
    "OUTFORRETURN": "Saiu p/ devolução",
    "RETURNED": "Devolvido",
}


def _amz_label(mapping: dict[str, str], value: str | None) -> str:
    v = (value or "").strip()
    if not v:
        return ""
    return mapping.get(v.upper(), v)


def assinatura_amazon(status: dict[str, str] | None) -> str:
    """Assinatura em PT da Amazon = OrderStatus + EasyShipShipmentStatus
    traduzidos, juntados por " | " (omite os ausentes)."""
    m = status or {}
    partes = [
        _amz_label(AMAZON_ORDER_LABELS_PT, m.get("order_status")),
        _amz_label(AMAZON_EASYSHIP_LABELS_PT, m.get("easyship_status")),
    ]
    return " | ".join(p for p in partes if p)


# Campos que compõem a assinatura de CADA plataforma, na ordem de exibição, com
# o rótulo PT de cada um. O Meli usa os 8 de FIELD_ORDER/FIELD_LABELS; as outras
# têm vocabulário próprio e bem mais curto.
SHOPEE_LOG_LABELS_PT: dict[str, str] = {
    "LOGISTICS_REQUEST_CREATED": "Coleta solicitada",
    "LOGISTICS_PICKUP_DONE": "Coletado",
    "LOGISTICS_PICKUP_RETRY": "Nova tentativa de coleta",
    "LOGISTICS_PICKUP_FAILED": "Falha na coleta",
    "LOGISTICS_DELIVERY_DONE": "Entregue",
    "LOGISTICS_DELIVERY_FAILED": "Falha na entrega",
    "LOGISTICS_REQUEST_CANCELED": "Coleta cancelada",
    "LOGISTICS_COD_REJECTED": "Pagamento na entrega recusado",
    "LOGISTICS_READY": "Pronto p/ envio",
    "LOGISTICS_INVALID": "Envio inválido",
    "LOGISTICS_LOST": "Extraviado",
    "LOGISTICS_PENDING_ARRANGE": "Aguardando postagem",
}

_CAMPOS_POR_PLATAFORMA: dict[str, tuple[list[str], dict[str, str], dict[str, dict[str, str]]]] = {
    "shopee": (
        ["order_status", "logistics_status"],
        {"order_status": "Status do pedido", "logistics_status": "Status do envio"},
        {"order_status": SHOPEE_STATUS_LABELS_PT, "logistics_status": SHOPEE_LOG_LABELS_PT},
    ),
    "tiktok": (
        ["order_status"],
        {"order_status": "Status do pedido"},
        {"order_status": TIKTOK_STATUS_LABELS_PT},
    ),
    "amazon": (
        ["order_status", "easyship_status"],
        {"order_status": "Status do pedido", "easyship_status": "Status do envio"},
        {
            "order_status": AMAZON_ORDER_LABELS_PT,
            "easyship_status": AMAZON_EASYSHIP_LABELS_PT,
        },
    ),
}


def _config_campos(
    plataforma: str | None,
) -> tuple[list[str], dict[str, str], dict[str, dict[str, str]]]:
    p = (plataforma or "").strip().lower()
    if p in _SHOPEE_PLATAFORMAS:
        return _CAMPOS_POR_PLATAFORMA["shopee"]
    if p in _TIKTOK_PLATAFORMAS:
        return _CAMPOS_POR_PLATAFORMA["tiktok"]
    if p in _AMAZON_PLATAFORMAS:
        return _CAMPOS_POR_PLATAFORMA["amazon"]
    return FIELD_ORDER, FIELD_LABELS, VALUE_LABELS_PT


def detalhe_para(
    plataforma: str | None, status: dict[str, str] | None
) -> list[dict[str, str]]:
    """A assinatura ABERTA em linhas: `[{"campo","rotulo","valor"}]`, uma por
    campo preenchido, na ordem da plataforma e já traduzida pra PT.

    É a mesma informação de `assinatura_para` (que junta tudo num texto só),
    mas com o campo identificado — é o que o balãozinho da coluna "Status
    Plataforma" mostra pra poder pendurar a data de cada linha ao lado."""
    m = status or {}
    ordem, rotulos, valores = _config_campos(plataforma)
    fora_da_ordem = [k for k in m if k not in ordem]
    linhas: list[dict[str, str]] = []
    for campo in [*ordem, *fora_da_ordem]:
        bruto = str(m.get(campo) or "").strip()
        if not bruto:
            continue
        mapa = valores.get(campo, {})
        pt = mapa.get(bruto) or mapa.get(bruto.upper()) or bruto
        linhas.append(
            {
                "campo": campo,
                "rotulo": rotulos.get(campo) or campo,
                "valor": pt,
            }
        )
    return linhas


def assinatura_para(plataforma: str | None, status: dict[str, str] | None) -> str:
    """Assinatura de "Status Plataforma" da linha, despachando pela plataforma:
    Shopee usa `assinatura_shopee` (order_status), TikTok `assinatura_tiktok`
    (status), Amazon `assinatura_amazon` (OrderStatus + EasyShip); as demais
    usam a assinatura de 8 campos do Meli (`assinatura_pt`)."""
    p = (plataforma or "").strip().lower()
    if p in _SHOPEE_PLATAFORMAS:
        return assinatura_shopee(status)
    if p in _TIKTOK_PLATAFORMAS:
        return assinatura_tiktok(status)
    if p in _AMAZON_PLATAFORMAS:
        return assinatura_amazon(status)
    return assinatura_pt(status)


def localizacao_pt(meli_status: dict[str, str] | None) -> str:
    """Proxy do "último local" a partir do envio do Meli: substatus do envio
    traduzido (mais específico) ou, na falta, o status do envio. O ML NÃO expõe
    o local físico — isto é a situação do envio na plataforma (opção 3)."""
    m = meli_status or {}
    sub = traduzir_valor("ship_substatus", m.get("ship_substatus"))
    if sub:
        return sub
    return traduzir_valor("ship_status", m.get("ship_status"))


def localizacao_completa(
    status_pt: str, *, destino: str | None = None, previsao: str | None = None
) -> str:
    """Compõe a localização proxy da rede própria do ML (Flex/Coletas/Full, que
    não tem local ao vivo): o status do envio em PT + destino (cidade/UF) +
    previsão de entrega, quando houver — `Saiu p/ entrega → São Paulo/SP ·
    previsão 16/07`. Partes ausentes são omitidas."""
    out = (status_pt or "").strip()
    d = (destino or "").strip()
    if d:
        out = f"{out} → {d}" if out else d
    p = (previsao or "").strip()
    if p:
        pv = f"previsão {p}"
        out = f"{out} · {pv}" if out else pv
    return out


# Trechos da descrição dos Correios (17track) que indicam ENTREGA ao
# destinatário — o sinal físico de que o cliente recebeu o pacote.
_CORREIOS_ENTREGUE = ("entregue ao destinat",)
# Trechos que indicam um PROBLEMA definitivo no envio físico (não é mero
# "em trânsito", que não vale divergência pra não virar ruído).
_CORREIOS_PROBLEMA = (
    "devolv",
    "extravi",
    "roubo",
    "avaria",
    "recusad",
    "endereço incorreto",
    "endereco incorreto",
    "aguardando retirada",
    "não retirado",
    "nao retirado",
    "objeto não localizado",
    "objeto nao localizado",
)


def detectar_divergencia(
    meli_status: dict[str, str] | None, localizacao: str | None
) -> str | None:
    """Explica, em texto, a divergência entre o status do Mercado Livre e o
    rastreio físico dos Correios (`localizacao` vinda do 17track). Retorna None
    quando batem ou não há sinal físico claro (mero "em trânsito").

    Casos sinalizados (conservador — só entrega, nos dois sentidos):
      * Correios consta ENTREGUE ao destinatário mas o ML NÃO consta entrega
        (cancelado/retido/não entregue) — o mais perigoso: cliente recebeu.
      * ML consta ENTREGUE mas o físico mostra PROBLEMA (devolvido/extraviado).
    """
    loc = (localizacao or "").strip()
    if not loc:
        return None
    low = loc.lower()
    m = meli_status or {}
    ml_entregue = (m.get("ship_status") or "").strip().lower() == "delivered"
    fisico_entregue = any(k in low for k in _CORREIOS_ENTREGUE)
    fisico_problema = any(k in low for k in _CORREIOS_PROBLEMA)
    ml_txt = assinatura_pt(m) or "sem status"
    if fisico_entregue and not ml_entregue:
        return (
            f"Correios: entregue ao destinatário. Mercado Livre: {ml_txt}. "
            "Cliente recebeu, mas o ML não consta a entrega."
        )
    if ml_entregue and fisico_problema:
        return f"Mercado Livre: entregue. Correios: {loc}. O físico mostra problema."
    return None


# logistics_status FÍSICO da SPX (get_tracking_info) que importa pro cruzamento
# com o order_status COMERCIAL da Shopee.
_SHOPEE_LOG_ENTREGUE = {"LOGISTICS_DELIVERY_DONE"}
_SHOPEE_LOG_PROBLEMA = {
    "LOGISTICS_DELIVERY_FAILED",
    "LOGISTICS_LOST",
    "LOGISTICS_PICKUP_FAILED",
}
# order_status em que o pedido NÃO deveria ter chegado ao cliente.
_SHOPEE_ORDER_ABERTO = {"CANCELLED", "IN_CANCEL", "TO_RETURN"}


def detectar_divergencia_shopee(
    meli_status: dict[str, str] | None, localizacao: str | None = None
) -> str | None:
    """Divergência da Shopee: cruza o `order_status` COMERCIAL com o
    `logistics_status` FÍSICO da SPX (ambos vêm da Shopee, mas são sinais
    distintos que podem discordar). Conservador — só os dois sentidos perigosos
    de entrega; retorna None se não há sinal físico ou se batem."""
    m = meli_status or {}
    order = (m.get("order_status") or "").strip().upper()
    log = (m.get("logistics_status") or "").strip().upper()
    if not log:
        return None
    if log in _SHOPEE_LOG_ENTREGUE and order in _SHOPEE_ORDER_ABERTO:
        return (
            f"SPX: entregue ao destinatário. Pedido: {assinatura_shopee(m)}. "
            "Cliente recebeu, mas o pedido consta cancelamento/devolução."
        )
    if order == "COMPLETED" and log in _SHOPEE_LOG_PROBLEMA:
        loc = (localizacao or log).strip()
        return f"Pedido: concluído. SPX: {loc}. O físico mostra problema."
    return None


# --- TikTok divergência ---------------------------------------------------
# order_status COMERCIAL × último evento de rastreio FÍSICO (descrição em
# inglês). Conservador — só os dois sentidos de entrega.
_TIKTOK_ORDER_ABERTO = {"CANCELLED"}
_TIKTOK_FISICO_ENTREGUE = ("delivered", "entregue")
_TIKTOK_FISICO_PROBLEMA = (
    "returned",
    "return to",
    "failed",
    "lost",
    "undeliverable",
    "rejected",
)


def detectar_divergencia_tiktok(
    meli_status: dict[str, str] | None, localizacao: str | None = None
) -> str | None:
    """Divergência do TikTok: cruza o `order_status` COMERCIAL com o último
    evento de rastreio FÍSICO (descrição em inglês, em `localizacao`).
    Conservador — só os dois sentidos de entrega; None se não há sinal físico."""
    loc = (localizacao or "").strip()
    if not loc:
        return None
    low = loc.lower()
    m = meli_status or {}
    order = (m.get("order_status") or "").strip().upper()
    fisico_entregue = any(k in low for k in _TIKTOK_FISICO_ENTREGUE)
    fisico_problema = any(k in low for k in _TIKTOK_FISICO_PROBLEMA)
    if fisico_entregue and order in _TIKTOK_ORDER_ABERTO:
        return (
            f"Rastreio: entregue ao destinatário. Pedido: {assinatura_tiktok(m)}. "
            "Cliente recebeu, mas o pedido consta cancelamento."
        )
    if order in {"COMPLETED", "DELIVERED"} and fisico_problema:
        return f"Pedido: {assinatura_tiktok(m)}. Rastreio: {loc}. O físico mostra problema."
    return None


# --- Amazon divergência ---------------------------------------------------
# OrderStatus COMERCIAL × EasyShipShipmentStatus FÍSICO (ambos da Amazon, mas
# sinais distintos que podem discordar).
_AMZ_ORDER_ABERTO = {"CANCELED", "CANCELLED"}
_AMZ_EASYSHIP_ENTREGUE = {"DELIVERED"}
_AMZ_EASYSHIP_PROBLEMA = {
    "LOST",
    "UNDELIVERABLE",
    "RETURNEDTOSELLER",
    "DAMAGED",
    "REJECTEDBYBUYER",
}


def detectar_divergencia_amazon(
    meli_status: dict[str, str] | None, localizacao: str | None = None
) -> str | None:
    """Divergência da Amazon: cruza o `order_status` (OrderStatus) COMERCIAL com
    o `easyship_status` (EasyShipShipmentStatus) FÍSICO. None se não há sinal
    físico (easyship vazio) ou se batem."""
    m = meli_status or {}
    order = (m.get("order_status") or "").strip().upper()
    easy = (m.get("easyship_status") or "").strip().upper()
    if not easy:
        return None
    if easy in _AMZ_EASYSHIP_ENTREGUE and order in _AMZ_ORDER_ABERTO:
        return (
            f"Entrega: concluída. Pedido: {assinatura_amazon(m)}. "
            "Cliente recebeu, mas o pedido consta cancelamento."
        )
    if order == "SHIPPED" and easy in _AMZ_EASYSHIP_PROBLEMA:
        return (
            f"Pedido: enviado. Entrega: "
            f"{_amz_label(AMAZON_EASYSHIP_LABELS_PT, easy)}. O físico mostra problema."
        )
    return None


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
