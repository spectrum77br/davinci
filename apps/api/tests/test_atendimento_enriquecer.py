# ruff: noqa: S105, S106  (tokens de clientes falsos, nada real)
"""Pedido, produto e foto das lojas na caixa (`enriquecer`), com clientes FALSOS.

Os payloads seguem os formatos REAIS medidos em produção em 28/09/2026 (só
chaves e tipos, `scratchpad/formatos_pedido.txt`): Shopee `get_order_detail`
(com os campos opcionais do painel), `get_item_base_info`, `get_model_list`,
`get_tracking_number`, `get_tracking_info`; ML `/orders/{id}`,
`/shipments/{id}` (`x-format-new`) e `/items?ids=`.

O que estes testes garantem:

- os mapas de status/pagamento em português, os valores em reais (float) e
  as fotos certas (`image_info.image_url`, `image_url_list[0]`,
  `secure_thumbnail` com `-I` → `-O`);
- o cartão de produto fica 24 h no Redis e não volta à loja; sem a loja, sai
  do nosso catálogo (`listings.thumbnail_url` pelo `external_id`);
- a COTA por rodada: 25 pedidos novos → 20 idas à loja; o resto entra na
  rodada seguinte, pela sobra da cota; o retrato só é renovado a cada 30 min;
- falha da loja (erro, 404, cliente sem o método) NÃO derruba o sync;
- NENHUM dado pessoal do comprador no retrato (nome, endereço, CPF, telefone),
  mesmo quando a resposta da loja os traz;
- os clientes de verdade só fazem GET, com os parâmetros certos;
- "Hora de envio" e "Tempo concluído" (`enviado_em`/`concluido_em`) saem do
  evento certo — sem evento, None, nunca um chute pelo status;
- cada retrato que dá certo alimenta o índice de pedidos do comprador
  (`indice.registrar_pedido`) com o id do comprador na plataforma, que NÃO
  entra no retrato; índice ausente ou quebrado não derruba o retrato.
"""

from __future__ import annotations

import json
import sys
import types
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    Integration,
    IntegrationPlatform,
    Listing,
    User,
)
from app.security.cipher import encrypt_json
from app.services import atendimento as pacote_atendimento
from app.services.atendimento import enriquecer
from app.services.atendimento import ml as ml_atd
from app.services.atendimento import shopee as shopee_atd
from app.services.marketplaces.ml import MercadoLivreClient
from app.services.marketplaces.shopee import ShopeeClient

SHOP = 111
COMPRADOR = 555
AGORA = datetime.now(UTC).replace(microsecond=0)
T_PEDIDO = int(datetime(2026, 9, 27, 15, 0, tzinfo=UTC).timestamp())
FOTO_SHOPEE = "https://down-br.img.susercontent.com/file/br-11134207-7r98o-foto-item"
FOTO_ANUNCIO = "https://down-br.img.susercontent.com/file/br-11134207-7r98o-anuncio-1"

# Dados pessoais que a loja devolve e que NÃO podem chegar ao retrato.
NOME_REAL = "Maria Aparecida da Silva"
ENDERECO = "Rua das Flores, 123 - Jardim Paulista"
TELEFONE = "5511999998888"
CPF = "123.456.789-00"
APELIDO = "maria.silva77"

_AsyncClientReal = httpx.AsyncClient


# ─────────────── infraestrutura falsa ───────────────


class RedisFalso:
    """O pedaço do Redis que o cache do cartão usa, em memória (com o TTL anotado)."""

    def __init__(self) -> None:
        self.dados: dict[str, str] = {}
        self.ttl: dict[str, int | None] = {}

    async def get(self, chave: str) -> str | None:
        return self.dados.get(chave)

    async def set(self, chave: str, valor: str, ex: int | None = None, nx: bool = False):
        self.dados[chave] = valor
        self.ttl[chave] = ex
        return True


class RedisFora:
    async def get(self, chave: str):
        raise ConnectionError("redis fora")

    async def set(self, *a, **kw):
        raise ConnectionError("redis fora")


@pytest.fixture(autouse=True)
def redis_falso(monkeypatch) -> RedisFalso:
    r = RedisFalso()
    monkeypatch.setattr(enriquecer, "redis", r)
    return r


@pytest.fixture
def relogio(monkeypatch):
    atual = {"agora": AGORA}
    monkeypatch.setattr(enriquecer, "_agora", lambda: atual["agora"])
    return atual


@pytest.fixture
def teto(monkeypatch):
    monkeypatch.setattr(get_settings(), "atendimento_sync_max_conversas", 40)


def _erro_http(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://api.exemplo/x")
    return httpx.HTTPStatusError(
        f"HTTP {status}", request=req, response=httpx.Response(status, request=req)
    )


# ─────────────── payloads Shopee (formato medido) ───────────────


def pedido_shopee(
    sn: str,
    *,
    status: str = "READY_TO_SHIP",
    logistica: str = "LOGISTICS_READY",
    total: Any = 766.19,
    pagamento: str = "Credit Card",
    pago: bool = True,
    com_pessoais: bool = True,
) -> dict:
    p = {
        "actual_shipping_fee": 0,
        "advance_package": False,
        "booking_sn": "",
        "buyer_preference_for_partial_cancellation": 0,
        "can_full_cancel_order": True,
        "can_partial_cancel_order": False,
        "cod": False,
        "create_time": T_PEDIDO,
        "currency": "BRL",
        "days_to_ship": 2,
        "estimated_shipping_fee": 22.5,
        "hot_listing_order": False,
        "invoice_data": {
            "number": "",
            "series_number": "",
            "access_key": "",
            "issue_date": 0,
            "total_value": 0,
            "products_total_value": 0,
            "tax_code": "",
            "status": "pending",
        },
        "is_buyer_shop_collection": False,
        "item_list": [
            {
                "active_qty": 1,
                "add_on_deal": False,
                "add_on_deal_id": 0,
                "cancel_requested_qty": 0,
                "cancelled_qty": 0,
                "consultation_id": "",
                "image_info": {"image_url": FOTO_SHOPEE},
                "is_b2c_owned_item": False,
                "is_prescription_item": False,
                "item_id": 22334455,
                "item_name": "Mala de Bordo ABS Rodinhas 360 Cadeado TSA",
                "item_sku": "MALA-BASE",
                "line_item_id": 1,
                "main_item": False,
                "model_discounted_price": 833,
                "model_id": 9001,
                "model_name": "Preta,P",
                "model_original_price": 999.9,
                "model_quantity_purchased": 1,
                "model_sku": "MALA-PRETA-P",
                "order_item_id": 22334455,
                "product_location_id": ["BRZ"],
                "promotion_group_id": 0,
                "promotion_id": 0,
                "promotion_list": [{"promotion_type": "product_promotion", "promotion_id": 1}],
                "promotion_type": "",
                "return_requested_qty": 0,
                "returned_qty": 0,
                "weight": 3,
                "wholesale": False,
            }
        ],
        "message_to_seller": "",
        "note": "",
        "order_sn": sn,
        "order_status": status,
        "package_list": [
            {
                "package_number": "OFG1234567890123",
                "group_shipment_id": None,
                "logistics_status": logistica,
                "shipping_carrier": "Shopee Xpress",
                "item_list": [
                    {
                        "item_id": 22334455,
                        "model_id": 9001,
                        "model_quantity": 1,
                        "order_item_id": 22334455,
                        "promotion_group_id": 0,
                        "product_location_id": "BRZ",
                    }
                ],
                "parcel_chargeable_weight_gram": 3000,
                "allow_self_design_awb": True,
                "logistics_channel_id": 91003,
                "sorting_group": "",
            }
        ],
        "pay_time": T_PEDIDO + 60 if pago else 0,
        "payment_method": pagamento,
        "region": "BR",
        "reverse_shipping_fee": 0,
        "ship_by_date": T_PEDIDO + 2 * 86400,
        "shipping_carrier": "Shopee Xpress",
        "total_amount": total,
        "update_time": T_PEDIDO + 3600,
    }
    if com_pessoais:
        # Não pedimos, mas a Shopee pode mandar: tem de ficar de fora do retrato.
        p["buyer_user_id"] = 998877
        p["buyer_username"] = APELIDO
        p["buyer_cpf_id"] = CPF
        p["message_to_seller"] = f"Entregar para {NOME_REAL}"
        p["recipient_address"] = {
            "name": NOME_REAL,
            "phone": TELEFONE,
            "full_address": ENDERECO,
            "zipcode": "01415000",
        }
    return p


def item_shopee(item_id: int, *, com_variacao: bool = True, preco: float = 129.9) -> dict:
    it = {
        "item_id": item_id,
        "category_id": 100637,
        "item_name": f"Mochila Executiva Notebook {item_id}",
        "description": "descrição longa do anúncio",
        "item_sku": f"MOC-{item_id}",
        "create_time": T_PEDIDO,
        "update_time": T_PEDIDO,
        "attribute_list": [],
        "image": {
            "image_id_list": ["a" * 32, "b" * 32],
            "image_url_list": [FOTO_ANUNCIO, FOTO_ANUNCIO + "-2"],
            "image_ratio": "1:1",
        },
        "weight": "1",
        "dimension": {"package_length": 40, "package_width": 30, "package_height": 15},
        "logistic_info": [],
        "pre_order": {"is_pre_order": False, "days_to_ship": 2},
        "condition": "NEW",
        "size_chart": "",
        "item_status": "NORMAL",
        "has_model": com_variacao,
        "promotion_id": 0,
        "has_promotion": False,
        "brand": {"brand_id": 0, "original_brand_name": "NoBrand"},
        "item_dangerous": 0,
        "description_type": "normal",
        "deboost": "FALSE",
        "authorised_brand_id": 0,
        "is_fulfillment_by_shopee": False,
        "tag": {"kit": False},
        "purchase_limit_info": {"min_purchase_limit": 1},
    }
    if not com_variacao:
        it["price_info"] = [
            {"currency": "BRL", "original_price": preco + 20, "current_price": preco}
        ]
    return it


def modelos_shopee(*precos: tuple[float, float]) -> dict:
    return {
        "tier_variation": [{"name": "Cor", "option_list": [{"option": "Preta"}]}],
        "model": [
            {
                "model_id": 7000 + i,
                "model_name": f"Cor {i}",
                "model_sku": f"MOC-{i}",
                "price_info": [{"current_price": atual, "original_price": original}],
            }
            for i, (atual, original) in enumerate(precos)
        ],
    }


class ShopeeFalso:
    """Imita o `ShopeeClient`: o chat (para a rodada) e as leituras de pedido/produto."""

    def __init__(self) -> None:
        self.shop_id = SHOP
        self.conversas: dict[str, dict] = {}
        self.mensagens: dict[str, list[dict]] = {}
        self.pedidos: dict[str, dict | Exception] = {}
        self.produtos: dict[str, dict] = {}
        self.modelos: dict[str, dict] = {}
        self.rastreios: dict[str, tuple[str | None, dict]] = {}
        self.erro_produto: Exception | None = None
        self.chamadas: list[tuple[str, str]] = []

    def conversa(self, cid: str, *msgs: dict, avatar: str = "") -> None:
        self.mensagens.setdefault(cid, []).extend(msgs)
        ultima = max(self.mensagens[cid], key=lambda m: m["created_timestamp"])
        self.conversas[cid] = {
            "conversation_id": cid,
            "to_id": COMPRADOR,
            "to_name": "comprador_teste",
            "to_avatar": avatar,
            "shop_id": SHOP,
            "unread_count": 1,
            "pinned": False,
            "last_read_message_id": "0",
            "latest_message_id": ultima["message_id"],
            "latest_message_type": ultima["message_type"],
            "latest_message_content": ultima["content"],
            "latest_message_from_id": ultima["from_id"],
            "last_message_timestamp": ultima["created_timestamp"] * 10**9,
            "last_message_option": 0,
            "max_general_option_hide_time": "9223372036854775",
            "mute": False,
            "opposite_last_deliver_msg_id": "0",
            "opposite_last_read_msg_id": "0",
        }

    def contagem(self, nome: str) -> int:
        return sum(1 for n, _ in self.chamadas if n == nome)

    # — chat —
    async def chat_unread_count(self) -> int:
        return len(self.conversas)

    async def chat_conversation_list(
        self, *, direction="older", tipo="all", page_size=25, next_timestamp_nano=None
    ) -> dict:
        lista = sorted(
            self.conversas.values(), key=lambda c: c["last_message_timestamp"], reverse=True
        )
        if next_timestamp_nano is not None:
            lista = [c for c in lista if c["last_message_timestamp"] < int(next_timestamp_nano)]
        pagina = lista[:page_size]
        ultimo = pagina[-1] if pagina else None
        return {
            "page_result": {
                "page_size": len(pagina),
                "next_cursor": {
                    "next_message_time_nano": str(ultimo["last_message_timestamp"])
                    if ultimo
                    else "0",
                    "conversation_id": "0",
                },
                "more": len(lista) > page_size,
            },
            "conversations": pagina,
        }

    async def chat_mensagens_pagina(self, cid, *, offset=None, page_size=25) -> dict:
        msgs = sorted(self.mensagens.get(cid, []), key=lambda m: -m["created_timestamp"])
        return {"messages": msgs, "page_result": {"next_offset": "", "page_size": page_size}}

    # — pedido e produto (só leitura) —
    async def get_order_detail_completo(self, order_sn: str) -> dict:
        self.chamadas.append(("pedido", order_sn))
        p = self.pedidos.get(order_sn)
        if isinstance(p, Exception):
            raise p
        return p or {}

    async def get_item_base_info(self, item_ids: list) -> list[dict]:
        self.chamadas.append(("produto", ",".join(str(i) for i in item_ids)))
        if self.erro_produto is not None:
            raise self.erro_produto
        return [self.produtos[str(i)] for i in item_ids if str(i) in self.produtos]

    async def get_model_list(self, item_id) -> dict:
        self.chamadas.append(("variacoes", str(item_id)))
        return self.modelos.get(str(item_id), {"model": []})

    async def get_tracking_number(self, order_sn: str) -> str | None:
        self.chamadas.append(("rastreio", order_sn))
        return self.rastreios.get(order_sn, (None, {}))[0]

    async def get_tracking_info(self, order_sn: str) -> dict:
        self.chamadas.append(("eventos", order_sn))
        return self.rastreios.get(order_sn, (None, {}))[1]


_seq = iter(range(10**6))


def msg_shopee(
    cid: str, quando: datetime, *, tipo: str = "text", content: dict | None = None
) -> dict:
    return {
        "message_id": f"71{next(_seq):017d}",
        "from_id": COMPRADOR,
        "to_id": 222,
        "from_shop_id": 987654,
        "to_shop_id": SHOP,
        "message_type": tipo,
        "content": content if content is not None else {"text": "oi"},
        "conversation_id": cid,
        "created_timestamp": int(quando.timestamp()),
        "region": "BR",
        "status": "normal",
        "message_option": 0,
        "source": "new_webchat",
        "source_content": {},
        "quoted_msg": None,
    }


# ─────────────── payloads ML (formato medido) ───────────────

SELLER = 999000111
PACK = "2000009876543210"
ORDEM = "2000001111111111"
ENVIO = "44001234567"
ITEM_ML = "MLB1234567890"
THUMB_ML = "http://http2.mlstatic.com/D_812345-MLB70000000000_072023-I.jpg"
SECURE_ML = "https://http2.mlstatic.com/D_812345-MLB70000000000_072023-I.jpg"
FOTO_ML_GRANDE = "https://http2.mlstatic.com/D_812345-MLB70000000000_072023-O.jpg"


def ordem_ml(order_id: str = ORDEM, *, status: str = "paid", tipo_pag: str = "credit_card") -> dict:
    return {
        "id": int(order_id),
        "date_created": "2026-09-27T12:00:00.000-04:00",
        "last_updated": "2026-09-27T12:05:00.000-04:00",
        "date_closed": "2026-09-27T12:01:00.000-04:00",
        "pack_id": int(PACK),
        "fulfilled": None,
        "buying_mode": "buy_equals_pay",
        "shipping_cost": None,
        "mediations": [],
        "total_amount": 199.9,
        "paid_amount": 224.8,
        "order_items": [
            {
                "item": {
                    "id": ITEM_ML,
                    "title": "Fone Bluetooth Sem Fio Cancelamento de Ruído",
                    "category_id": "MLB1234",
                    "variation_id": None,
                    "seller_custom_field": None,
                    "variation_attributes": [
                        {"name": "Cor", "id": "COLOR", "value_id": "52049", "value_name": "Preto"},
                        {"name": "Voltagem", "id": "VOLT", "value_id": "1", "value_name": "Bivolt"},
                    ],
                    "warranty": "Garantia do vendedor: 90 dias",
                    "condition": "new",
                    "seller_sku": "FONE-PT",
                    "global_price": None,
                    "net_weight": None,
                    "user_product_id": "MLBU1234567890",
                    "release_date": None,
                    "attributes": [],
                },
                "quantity": 2,
                "requested_quantity": {"measure": "unit", "value": 2},
                "picked_quantity": None,
                "unit_price": 99.95,
                "currency_id": "BRL",
                "manufacturing_days": None,
                "sale_fee": 17.99,
                "listing_type_id": "gold_special",
                "base_exchange_rate": None,
                "base_currency_id": None,
                "element_id": None,
                "discounts": None,
                "bundle": None,
                "compat_id": None,
                "stock": None,
                "kit_instance_id": None,
                "gross_price": 99.95,
            }
        ],
        "currency_id": "BRL",
        "payments": [
            {
                "id": 1234567890,
                "order_id": int(order_id),
                "payer_id": 555000222,
                "collector": {"id": SELLER},
                "card_id": None,
                "reason": "Fone Bluetooth Sem Fio Cancelamento de Ruído",
                "site_id": "MLB",
                "payment_method_id": "master" if tipo_pag == "credit_card" else "account_money",
                "currency_id": "BRL",
                "installments": 3,
                "issuer_id": "1234",
                "atm_transfer_reference": {"transaction_id": None, "company_id": None},
                "coupon_id": None,
                "activation_uri": None,
                "operation_type": "regular_payment",
                "payment_type": tipo_pag,
                "available_actions": ["refund"],
                "status": "approved",
                "status_code": None,
                "status_detail": "accredited",
                "transaction_amount": 199.9,
                "transaction_amount_refunded": 0.0,
                "taxes_amount": 0.0,
                "shipping_cost": 24.9,
                "coupon_amount": 0.0,
                "overpaid_amount": 0.0,
                "total_paid_amount": 224.8,
                "installment_amount": None,
                "deferred_period": None,
                "date_approved": "2026-09-27T12:00:30.000-04:00",
                "transaction_order_id": None,
                "date_created": "2026-09-27T12:00:10.000-04:00",
                "date_last_modified": "2026-09-27T12:00:30.000-04:00",
                "marketplace_fee": 17.99,
                "reference_id": None,
                "authorization_code": None,
            }
        ],
        "shipping": {"id": int(ENVIO)},
        "status": status,
        "status_detail": None,
        "tags": ["paid", "not_delivered", "pack_order"],
        "static_tags": ["catalog_ok"],
        "feedback": {"seller": None, "buyer": None},
        "context": {"channel": "marketplace", "site": "MLB", "flows": []},
        "seller": {"id": SELLER},
        "buyer": {
            "id": 555000222,
            "nickname": APELIDO,
            "first_name": "Maria",
            "last_name": "Aparecida da Silva",
            "billing_info": {"id": CPF},
        },
        "taxes": {"amount": None, "currency_id": None, "id": None},
        "cancel_detail": None,
        "manufacturing_ending_date": None,
        "order_request": {"change": None, "return": None},
        "related_orders": None,
    }


def envio_ml(*, status: str = "shipped", substatus: str = "out_for_delivery") -> dict:
    return {
        "substatus_history": [
            {
                "date": "2026-09-27T12:00:30.000-04:00",
                "substatus": "shipment_paid",
                "status": "pending",
            }
        ],
        "snapshot_packing": {"snapshot_id": "x" * 36, "pack_hash": "a"},
        "receiver_id": 555000222,
        "base_cost": 24.9,
        "status_history": {
            "date_shipped": "2026-09-28T09:00:00.000-04:00",
            "date_returned": None,
            "date_delivered": None,
            "date_first_visit": None,
            "date_not_delivered": None,
            "date_cancelled": None,
            "date_handling": "2026-09-27T12:00:30.000-04:00",
            "date_ready_to_ship": "2026-09-27T18:00:00.000-04:00",
        },
        "type": "forward",
        "return_details": None,
        "sender_id": SELLER,
        "mode": "me2",
        "order_cost": 199,
        "service_id": 1,
        "tracking_number": "MEL44001234567FMDOF01",
        "id": int(ENVIO),
        "tracking_method": "MEL Distribution",
        "last_updated": "2026-09-28T10:30:00.000-04:00",
        "substatus": substatus,
        "status": status,
        "date_created": "2026-09-27T12:00:30.000-04:00",
        "receiver_address": {
            "country": {"id": "BR", "name": "Brasil"},
            "city": {"id": "BR-SP-44", "name": "São Paulo"},
            "street_name": "Rua das Flores",
            "street_number": "123",
            "zip_code": "01415000",
            "receiver_name": NOME_REAL,
            "receiver_phone": TELEFONE,
            "comment": ENDERECO,
        },
    }


def anuncio_ml(item_id: str = ITEM_ML, *, preco: float = 99.95, original: float | None = 149.9):
    return {
        "id": item_id,
        "title": "Fone Bluetooth Sem Fio Cancelamento de Ruído",
        "price": preco,
        "original_price": original,
        "permalink": f"https://produto.mercadolivre.com.br/{item_id}-fone",
        "thumbnail": THUMB_ML,
        "secure_thumbnail": SECURE_ML,
        "variations": [],
    }


class MLFalso:
    """Imita o `MercadoLivreClient`: pergunta, pós-venda e as leituras novas."""

    def __init__(self) -> None:
        self.creds = {"user_id": SELLER, "access_token": "tok"}
        self.perguntas: list[dict] = []
        self.packs: dict[str, list[dict]] = {}
        # GET /packs/{id} (o carrinho: `orders[].id` e o envio); sem → 404.
        self.packs_api: dict[str, dict | Exception] = {}
        self.ordens: dict[str, dict | Exception] = {}
        self.envios: dict[str, dict | Exception] = {}
        self.anuncios: dict[str, dict] = {}
        self.chamadas: list[tuple[str, str]] = []

    def contagem(self, nome: str) -> int:
        return sum(1 for n, _ in self.chamadas if n == nome)

    async def perguntas_recebidas(self, *, status="UNANSWERED", offset=0, limit=50) -> dict:
        fonte = self.perguntas if status == "UNANSWERED" else []
        return {"total": len(fonte), "limit": limit, "questions": fonte[offset : offset + limit]}

    async def detalhe_pergunta(self, question_id) -> dict:
        raise _erro_http(404)

    async def get_item(self, item_id) -> dict:
        self.chamadas.append(("get_item", item_id))
        return {"id": item_id, "title": "título pelo get_item"}

    async def mensagens_nao_lidas(self, tag="post_sale") -> dict:
        return {
            "user_id": SELLER,
            "total": len(self.packs),
            "results": [
                {"resource": f"/packs/{p}/sellers/{SELLER}", "count": 1} for p in self.packs
            ],
        }

    async def mensagens_do_pack(self, pack_id, seller_id, *, offset=0, limit=20) -> dict:
        msgs = self.packs[str(pack_id)]
        return {
            "paging": {"limit": limit, "offset": offset, "total": len(msgs)},
            "conversation_status": {"status": "active", "substatus": None, "claim_ids": []},
            "messages": msgs[offset : offset + limit],
            "seller_max_message_length": 350,
            "buyer_max_message_length": 3500,
        }

    async def search_orders(self, *, seller_id, date_from, date_to, limit=50, offset=0) -> dict:
        return {"results": [], "paging": {"total": 0, "offset": offset, "limit": limit}}

    async def get_pack(self, pack_id) -> dict:
        self.chamadas.append(("pack", str(pack_id)))
        p = self.packs_api.get(str(pack_id))
        if isinstance(p, Exception):
            raise p
        if p is None:
            raise _erro_http(404)
        return p

    async def pedido(self, order_id) -> dict:
        self.chamadas.append(("pedido", str(order_id)))
        o = self.ordens.get(str(order_id))
        if isinstance(o, Exception):
            raise o
        if o is None:
            raise _erro_http(404)
        return o

    async def envio(self, shipment_id) -> dict:
        self.chamadas.append(("envio", str(shipment_id)))
        e = self.envios.get(str(shipment_id))
        if isinstance(e, Exception):
            raise e
        return e or {}

    async def itens(self, ids: list[str]) -> list[dict]:
        self.chamadas.append(("itens", ",".join(ids)))
        return [self.anuncios[i] for i in ids if i in self.anuncios]


def pergunta_ml(qid: int, item: str = ITEM_ML) -> dict:
    return {
        "date_created": "2026-09-28T08:00:00.0000000-04:00",
        "item_id": item,
        "seller_id": SELLER,
        "status": "UNANSWERED",
        "text": "Tem na cor azul?",
        "tags": [],
        "ai_categories": [],
        "id": qid,
        "answer": None,
        "from": {"id": 555000222},
        "hold": False,
        "deleted_from_listing": False,
    }


def msg_pack(mid: str, quando: datetime, pedido: str | None = None) -> dict:
    """Mensagem de pack no formato REAL (doc do ML e `formato_ml_pack.txt`):
    `message_resources` = packs + sellers, `data.order_id` null — o pedido
    NÃO aparece. `pedido=` põe o recurso "orders" (o outro caminho do código)."""
    iso = quando.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    recursos = [{"id": PACK, "name": "packs"}, {"id": str(SELLER), "name": "sellers"}]
    if pedido:
        recursos.append({"id": pedido, "name": "orders"})
    return {
        "id": mid,
        "from": {"user_id": 555000222},
        "to": {"user_id": SELLER},
        "status": "available",
        "text": "Quando chega?",
        "message_date": {"received": iso, "created": iso},
        "message_moderation": {"status": "clean"},
        "message_attachments": None,
        "message_resources": recursos,
        "data": {"order_id": None},
    }


def pack_ml(*pedidos: str) -> dict:
    """GET /packs/{id}: os pedidos do carrinho e o envio (um só para todos)."""
    return {
        "id": int(PACK),
        "status": "released",
        "family_pack_id": None,
        "orders": [{"id": int(p)} for p in pedidos],
        "shipment": {"id": int(ENVIO)},
        "buyer": {"id": 555000222},
        "date_created": "2026-09-27T12:00:00.000-04:00",
        "last_updated": "2026-09-27T12:05:00.000-04:00",
    }


# ─────────────── fixtures de banco ───────────────


async def _integ(db: AsyncSession, user: User, plataforma: IntegrationPlatform, nome: str):
    integ = Integration(
        user_id=user.id,
        platform=plataforma,
        name=nome,
        credentials=encrypt_json({"access_token": "t", "refresh_token": "r", "shop_id": SHOP}),
    )
    db.add(integ)
    await db.commit()
    await db.refresh(integ)
    return integ


async def _canal(db: AsyncSession, integ: Integration, plataforma: str, canal: str):
    c = AtendimentoCanal(integration_id=integ.id, plataforma=plataforma, canal=canal)
    db.add(c)
    await db.commit()
    return c


async def _conversa_simples(
    db: AsyncSession, integ: Integration, *, plataforma: str, canal: str, externo: str, **kw
) -> AtendimentoConversa:
    c = AtendimentoConversa(
        integration_id=integ.id,
        plataforma=plataforma,
        canal=canal,
        conta=integ.name,
        externo_id=externo,
        dados=kw.pop("dados", {}),
        **kw,
    )
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return c


async def _conversas(db: AsyncSession) -> dict[str, AtendimentoConversa]:
    linhas = (await db.execute(select(AtendimentoConversa))).scalars().all()
    for c in linhas:
        await db.refresh(c)
    return {c.externo_id: c for c in linhas}


async def _mensagens(db: AsyncSession, conversa_id) -> list[AtendimentoMensagem]:
    linhas = (
        (
            await db.execute(
                select(AtendimentoMensagem)
                .where(AtendimentoMensagem.conversa_id == conversa_id)
                .order_by(AtendimentoMensagem.enviada_em)
            )
        )
        .scalars()
        .all()
    )
    for m in linhas:
        await db.refresh(m)
    return list(linhas)


CHAVES_RETRATO = {
    "fonte",
    "pedido",
    "status",
    "status_texto",
    "criado_em",
    "pago_em",
    "enviado_em",
    "concluido_em",
    "total",
    "valor_pago",
    "frete",
    "moeda",
    "pagamento_metodo",
    "itens",
    "logistica",
    "nf",
    "atualizado_em",
}
CHAVES_ITEM = {"titulo", "imagem", "variacao", "sku", "quantidade", "preco"}
CHAVES_LOGISTICA = {
    "transportadora",
    "rastreio",
    "status",
    "status_texto",
    "descricao",
    "atualizado_em",
}
CHAVES_CARTAO_PRODUTO = {
    "tipo",
    "item_id",
    "titulo",
    "imagem",
    "preco",
    "preco_original",
    "moeda",
    "link",
}
CHAVES_CARTAO_PEDIDO = {
    "tipo",
    "pedido",
    "status",
    "status_texto",
    "criado_em",
    "total",
    "moeda",
    "itens",
}


def _sem_pessoais(obj: Any) -> None:
    texto = json.dumps(obj, ensure_ascii=False)
    for proibido in (
        NOME_REAL,
        "Maria",
        "Aparecida",
        ENDERECO,
        "Rua das Flores",
        "01415000",
        TELEFONE,
        CPF,
        "12345678900",
        APELIDO,
        "998877",
        "555000222",
    ):
        assert proibido not in texto, f"dado pessoal no retrato: {proibido!r}"


# ─────────────── retrato Shopee ───────────────


async def test_retrato_shopee_valores_fotos_e_textos(db, make_user, relogio):
    integ = await _integ(db, await make_user(), IntegrationPlatform.SHOPEE, "Loja KFA")
    f = ShopeeFalso()
    f.pedidos["250927ABC"] = pedido_shopee(
        "250927ABC", status="SHIPPED", logistica="LOGISTICS_PICKUP_DONE", total=766
    )
    f.rastreios["250927ABC"] = (
        "BR2643431879151",
        {
            "logistics_status": "LOGISTICS_PICKUP_DONE",
            "order_sn": "250927ABC",
            "tracking_info": [
                {"update_time": T_PEDIDO + 7200, "description": "Pedido coletado",
                 "logistics_status": "LOGISTICS_PICKUP_DONE"},
                {"update_time": T_PEDIDO + 90000,
                 "description": f"Em trânsito - recebido por {NOME_REAL}, CPF {CPF}",
                 "logistics_status": "LOGISTICS_PICKUP_DONE"},
                {"update_time": T_PEDIDO + 3600, "description": "Etiqueta criada",
                 "logistics_status": "LOGISTICS_REQUEST_CREATED"},
            ],
        },
    )

    r = await enriquecer.retrato_pedido(db, integ, f, "shopee", "250927ABC")

    assert set(r) == CHAVES_RETRATO and set(r["logistica"]) == CHAVES_LOGISTICA
    assert (r["fonte"], r["pedido"], r["status"], r["status_texto"]) == (
        "shopee",
        "250927ABC",
        "SHIPPED",
        "Enviado",
    )
    # Valores em reais como float, mesmo quando a Shopee manda int.
    assert r["total"] == 766.0 and isinstance(r["total"], float)
    assert r["valor_pago"] == 766.0 and r["moeda"] == "BRL"
    # O frete que o COMPRADOR pagou não vem no pedido da Shopee (os
    # `*_shipping_fee` são o custo da logística): vazio, não R$ 22,50.
    assert r["frete"] is None
    assert r["pagamento_metodo"] == "Cartão de crédito"
    assert r["criado_em"] == datetime.fromtimestamp(T_PEDIDO, UTC).isoformat()
    assert r["pago_em"] == datetime.fromtimestamp(T_PEDIDO + 60, UTC).isoformat()
    (item,) = r["itens"]
    assert set(item) == CHAVES_ITEM
    assert item == {
        "titulo": "Mala de Bordo ABS Rodinhas 360 Cadeado TSA",
        "imagem": FOTO_SHOPEE,  # item_list[].image_info.image_url
        "variacao": "Preta,P",
        "sku": "MALA-PRETA-P",
        "quantidade": 1,
        "preco": 833.0,  # o preço com desconto, não o original
    }
    log = r["logistica"]
    assert log["transportadora"] == "Shopee Xpress"
    assert log["rastreio"] == "BR2643431879151"
    assert (log["status"], log["status_texto"]) == ("LOGISTICS_PICKUP_DONE", "Coletado")
    # O evento MAIS NOVO — com quem recebeu e o CPF: a descrição fica de
    # fora inteira (o horário do evento fica).
    assert log["descricao"] is None
    assert log["atualizado_em"] == datetime.fromtimestamp(T_PEDIDO + 90000, UTC).isoformat()
    assert r["nf"] == {"numero": None, "status": "pending"}
    assert r["atualizado_em"] == AGORA.isoformat(timespec="seconds")
    # "Hora de envio": o PRIMEIRO evento já coletado (a etiqueta criada antes
    # não conta); "Tempo concluído" só quando o pedido está COMPLETED.
    assert r["enviado_em"] == datetime.fromtimestamp(T_PEDIDO + 7200, UTC).isoformat()
    assert r["concluido_em"] is None
    _sem_pessoais(r)


@pytest.mark.parametrize(
    ("status", "texto"),
    list(enriquecer.STATUS_PEDIDO_SHOPEE.items()) + [("NOVO_STATUS", "NOVO_STATUS")],
)
async def test_mapa_de_status_do_pedido_shopee(db, make_user, status, texto):
    integ = await _integ(db, await make_user(), IntegrationPlatform.SHOPEE, "Loja")
    f = ShopeeFalso()
    f.pedidos["P1"] = pedido_shopee("P1", status=status)
    r = await enriquecer.retrato_pedido(db, integ, f, "shopee", "P1")
    assert (r["status"], r["status_texto"]) == (status, texto)


def test_mapas_de_texto_seguem_a_spec():
    spec = {
        "LOGISTICS_READY": "Pronto para coleta",
        "LOGISTICS_REQUEST_CREATED": "Coleta solicitada",
        "LOGISTICS_PICKUP_DONE": "Coletado",
        "LOGISTICS_DELIVERY_DONE": "Entregue",
        "LOGISTICS_DELIVERY_FAILED": "Falha na entrega",
        "LOGISTICS_LOST": "Extraviado",
    }
    assert spec.items() <= enriquecer.STATUS_LOGISTICA_SHOPEE.items()
    # Além da spec, os que o repositório já traduz (não sai código em inglês).
    assert enriquecer.STATUS_LOGISTICA_SHOPEE["LOGISTICS_PENDING_ARRANGE"] == (
        "Aguardando postagem"
    )
    assert enriquecer.STATUS_PEDIDO_SHOPEE["INVOICE_PENDING"] == "Aguardando nota fiscal"
    assert enriquecer.STATUS_PEDIDO_ML == {
        "paid": "Pago",
        "confirmed": "Confirmado",
        "payment_required": "Aguardando pagamento",
        "cancelled": "Cancelado",
        "invalid": "Inválido",
    }
    for codigo, texto in {
        "handling": "Em preparação",
        "ready_to_ship": "Pronto para enviar",
        "shipped": "A caminho",
        "delivered": "Entregue",
        "not_delivered": "Não entregue",
        "cancelled": "Cancelado",
    }.items():
        assert enriquecer.STATUS_ENVIO_ML[codigo] == texto
    for codigo, texto in {
        "credit_card": "Cartão de crédito",
        "account_money": "Saldo Mercado Pago",
        "ticket": "Boleto",
        "bank_transfer": "Pix/Transferência",
    }.items():
        assert enriquecer.PAGAMENTO_ML[codigo] == texto


@pytest.mark.parametrize(
    ("bruto", "texto"),
    [
        ("Credit Card", "Cartão de crédito"),
        ("Pix", "Pix"),
        ("PIX", "Pix"),
        ("Boleto", "Boleto"),
        ("SPayLater", "SPayLater"),
    ],
)
async def test_pagamento_shopee_em_portugues(db, make_user, bruto, texto):
    integ = await _integ(db, await make_user(), IntegrationPlatform.SHOPEE, "Loja")
    f = ShopeeFalso()
    f.pedidos["P1"] = pedido_shopee("P1", pagamento=bruto)
    assert (await enriquecer.retrato_pedido(db, integ, f, "shopee", "P1"))[
        "pagamento_metodo"
    ] == texto


async def test_shopee_sem_coleta_nem_pagamento_nao_busca_rastreio(db, make_user):
    """Antes da coleta combinada a Shopee devolve rastreio vazio (medido): nem pergunta."""
    integ = await _integ(db, await make_user(), IntegrationPlatform.SHOPEE, "Loja")
    f = ShopeeFalso()
    f.pedidos["P1"] = pedido_shopee("P1", status="READY_TO_SHIP", logistica="LOGISTICS_READY")
    f.pedidos["P2"] = pedido_shopee("P2", status="UNPAID", logistica="LOGISTICS_NOT_START",
                                    pago=False)
    r1 = await enriquecer.retrato_pedido(db, integ, f, "shopee", "P1")
    r2 = await enriquecer.retrato_pedido(db, integ, f, "shopee", "P2")
    assert f.contagem("rastreio") == f.contagem("eventos") == 0
    assert r1["logistica"]["status_texto"] == "Pronto para coleta"
    assert r1["logistica"]["rastreio"] is None
    assert (r2["status_texto"], r2["pago_em"], r2["valor_pago"]) == (
        "Aguardando pagamento",
        None,
        None,
    )


async def test_retrato_falha_da_loja_devolve_none_sem_levantar(db, make_user):
    integ = await _integ(db, await make_user(), IntegrationPlatform.SHOPEE, "Loja")
    f = ShopeeFalso()
    f.pedidos["ERRO"] = RuntimeError("shopee_order_detail error_server: busy")
    f.pedidos["REDE"] = httpx.ConnectTimeout("timeout")
    assert await enriquecer.retrato_pedido(db, integ, f, "shopee", "ERRO") is None
    assert await enriquecer.retrato_pedido(db, integ, f, "shopee", "REDE") is None
    assert await enriquecer.retrato_pedido(db, integ, f, "shopee", "SUMIU") is None
    # Cliente sem o método (TikTok, Amazon) e plataforma sem API: None, sem ida.
    assert await enriquecer.retrato_pedido(db, integ, object(), "shopee", "X") is None
    assert await enriquecer.retrato_pedido(db, integ, f, "tiktok", "X") is None
    assert await enriquecer.retrato_pedido(db, integ, f, "shopee", "") is None


# ─────────────── retrato ML ───────────────


async def test_retrato_ml_valores_envio_e_foto(db, make_user, relogio):
    integ = await _integ(db, await make_user(), IntegrationPlatform.ML, "ML KFA")
    f = MLFalso()
    f.ordens[ORDEM] = ordem_ml()
    f.envios[ENVIO] = envio_ml()
    f.anuncios[ITEM_ML] = anuncio_ml()

    r = await enriquecer.retrato_pedido(db, integ, f, "ml", ORDEM)

    assert set(r) == CHAVES_RETRATO and set(r["logistica"]) == CHAVES_LOGISTICA
    assert (r["fonte"], r["pedido"], r["status"], r["status_texto"]) == (
        "ml",
        ORDEM,
        "paid",
        "Pago",
    )
    assert (r["total"], r["valor_pago"], r["frete"]) == (199.9, 224.8, 24.9)
    assert r["pagamento_metodo"] == "Cartão de crédito"
    assert r["criado_em"] == "2026-09-27T16:00:00+00:00"  # -04:00 → UTC
    assert r["pago_em"] == "2026-09-27T16:00:30+00:00"
    (item,) = r["itens"]
    assert item == {
        "titulo": "Fone Bluetooth Sem Fio Cancelamento de Ruído",
        "imagem": FOTO_ML_GRANDE,  # secure_thumbnail com -I → -O
        "variacao": "Cor: Preto, Voltagem: Bivolt",
        "sku": "FONE-PT",
        "quantidade": 2,
        "preco": 99.95,
    }
    assert r["logistica"] == {
        "transportadora": "MEL Distribution",
        "rastreio": "MEL44001234567FMDOF01",
        "status": "shipped",
        "status_texto": "A caminho",
        "descricao": "Saiu para entrega",
        "atualizado_em": "2026-09-28T14:30:00+00:00",
    }
    assert r["nf"] == {"numero": None, "status": None}
    # `date_shipped` do envio; não entregue ainda: sem "Tempo concluído" (o
    # `date_closed` do pedido é o fechamento da VENDA, não a conclusão).
    assert r["enviado_em"] == "2026-09-28T13:00:00+00:00"
    assert r["concluido_em"] is None
    _sem_pessoais(r)
    # Uma ida para cada coisa: pedido, envio e as fotos (todas numa chamada).
    assert [n for n, _ in f.chamadas] == ["pedido", "envio", "itens"]


@pytest.mark.parametrize(
    ("tipo", "texto"),
    [
        ("credit_card", "Cartão de crédito"),
        ("account_money", "Saldo Mercado Pago"),
        ("ticket", "Boleto"),
        ("bank_transfer", "Pix/Transferência"),
    ],
)
async def test_pagamento_ml_em_portugues(db, make_user, tipo, texto):
    integ = await _integ(db, await make_user(), IntegrationPlatform.ML, "ML")
    f = MLFalso()
    f.ordens[ORDEM] = ordem_ml(tipo_pag=tipo)
    r = await enriquecer.retrato_pedido(db, integ, f, "ml", ORDEM)
    assert r["pagamento_metodo"] == texto


async def test_retrato_ml_sem_envio_e_sem_foto_ainda_sai(db, make_user):
    integ = await _integ(db, await make_user(), IntegrationPlatform.ML, "ML")
    f = MLFalso()
    f.ordens[ORDEM] = ordem_ml(status="cancelled")
    f.envios[ENVIO] = _erro_http(403)  # envio de outra conta/sem permissão
    r = await enriquecer.retrato_pedido(db, integ, f, "ml", ORDEM)
    assert r["status_texto"] == "Cancelado"
    assert r["logistica"]["status"] is None and r["logistica"]["rastreio"] is None
    assert r["itens"][0]["imagem"] is None  # o /items não achou o anúncio
    # Pedido que não é desta conta (404): None, sem levantar.
    assert await enriquecer.retrato_pedido(db, integ, f, "ml", "999") is None


# ─────────────── cartão de produto ───────────────


async def test_cartao_shopee_com_variacao_busca_preco_e_fica_24h_no_cache(
    db, make_user, redis_falso
):
    integ = await _integ(db, await make_user(), IntegrationPlatform.SHOPEE, "Loja")
    f = ShopeeFalso()
    f.produtos["4112503530"] = item_shopee(4112503530, com_variacao=True)
    f.modelos["4112503530"] = modelos_shopee((149.9, 199.9), (129.9, 179.9), (139.9, 139.9))

    c = await enriquecer.cartao_produto(db, integ, f, "shopee", 4112503530)

    assert set(c) == CHAVES_CARTAO_PRODUTO
    assert c == {
        "tipo": "produto",
        "item_id": "4112503530",
        "titulo": "Mochila Executiva Notebook 4112503530",
        "imagem": FOTO_ANUNCIO,  # image.image_url_list[0]
        "preco": 129.9,  # a menor variação ("a partir de")
        "preco_original": 139.9,
        "moeda": "BRL",
        "link": f"https://shopee.com.br/product/{SHOP}/4112503530",
    }
    chave = f"atd:prod:shopee:{integ.id}:4112503530"
    assert redis_falso.ttl[chave] == 24 * 3600
    # Segunda vez (outra conversa, mesma rodada ou não): do cache, sem ida à loja.
    antes = list(f.chamadas)
    assert await enriquecer.cartao_produto(db, integ, f, "shopee", "4112503530") == c
    assert f.chamadas == antes


async def test_cartao_shopee_sem_variacao_usa_price_info(db, make_user):
    integ = await _integ(db, await make_user(), IntegrationPlatform.SHOPEE, "Loja")
    f = ShopeeFalso()
    f.produtos["77"] = item_shopee(77, com_variacao=False, preco=59.9)
    c = await enriquecer.cartao_produto(db, integ, f, "shopee", "77")
    assert (c["preco"], c["preco_original"]) == (59.9, 79.9)
    assert f.contagem("variacoes") == 0


async def test_cartao_ml_foto_maior_e_link(db, make_user, redis_falso):
    integ = await _integ(db, await make_user(), IntegrationPlatform.ML, "ML")
    f = MLFalso()
    f.anuncios[ITEM_ML] = anuncio_ml()
    c = await enriquecer.cartao_produto(db, integ, f, "ml", ITEM_ML)
    assert c["imagem"] == FOTO_ML_GRANDE
    assert (c["preco"], c["preco_original"]) == (99.95, 149.9)
    assert c["link"].startswith("https://produto.mercadolivre.com.br/")
    # Só `thumbnail` em http: vira https (a tela é https) e também cresce.
    f.anuncios["MLB2"] = {**anuncio_ml("MLB2"), "secure_thumbnail": None}
    assert (await enriquecer.cartao_produto(db, integ, f, "ml", "MLB2"))["imagem"] == (
        FOTO_ML_GRANDE
    )


async def test_cartao_sem_loja_sai_do_catalogo_pelo_external_id(db, make_user, redis_falso):
    user = await make_user()
    integ = await _integ(db, user, IntegrationPlatform.SHOPEE, "Loja")
    outra = await _integ(db, user, IntegrationPlatform.SHOPEE, "Outra")
    base = {"user_id": user.id, "platform": IntegrationPlatform.SHOPEE, "external_id": "555"}
    db.add_all(
        [
            Listing(**base, integration_id=integ.id, title="Mala Bordo ABS - Preta",
                    thumbnail_url="https://cf.shopee.com.br/file/mala-preta", price=18990),
            Listing(**base, integration_id=integ.id, title="Mala Bordo ABS - Azul",
                    thumbnail_url=None, price=17990),
            # A mesma id numa OUTRA loja não conta.
            Listing(**base, integration_id=outra.id, title="Outro produto",
                    thumbnail_url="https://x/outra", price=100),
        ]
    )
    await db.commit()
    f = ShopeeFalso()
    f.erro_produto = RuntimeError("shopee_item_base_info error_item_not_found: x")

    c = await enriquecer.cartao_produto(db, integ, f, "shopee", "555")

    assert c["titulo"] == "Mala Bordo ABS"
    assert c["imagem"] == "https://cf.shopee.com.br/file/mala-preta"
    assert c["preco"] == 179.9  # centavos → reais, a menor variação
    # Do catálogo não vai para o cache: a próxima tentativa pergunta à loja.
    assert redis_falso.dados == {}
    # Sem API nenhuma (TikTok): também do catálogo.
    tiktok = await enriquecer.cartao_produto(db, integ, None, "tiktok", "555")
    assert tiktok["imagem"] == "https://cf.shopee.com.br/file/mala-preta"
    # Ninguém conhece: None (quem chama fica com o cartão só com o id).
    assert await enriquecer.cartao_produto(db, integ, f, "shopee", "999") is None


async def test_loja_sem_foto_completa_pelo_catalogo(db, make_user):
    user = await make_user()
    integ = await _integ(db, user, IntegrationPlatform.ML, "ML")
    db.add(
        Listing(user_id=user.id, integration_id=integ.id, platform=IntegrationPlatform.ML,
                external_id=ITEM_ML, title="x", thumbnail_url="https://mlstatic/cat.jpg",
                price=9990)
    )
    await db.commit()
    f = MLFalso()
    f.anuncios[ITEM_ML] = {**anuncio_ml(), "thumbnail": None, "secure_thumbnail": None}
    c = await enriquecer.cartao_produto(db, integ, f, "ml", ITEM_ML)
    assert c["titulo"] == "Fone Bluetooth Sem Fio Cancelamento de Ruído"  # a loja manda
    assert c["imagem"] == "https://mlstatic/cat.jpg"  # o catálogo completa


async def test_redis_fora_nao_impede_o_cartao(db, make_user, monkeypatch):
    monkeypatch.setattr(enriquecer, "redis", RedisFora())
    integ = await _integ(db, await make_user(), IntegrationPlatform.ML, "ML")
    f = MLFalso()
    f.anuncios[ITEM_ML] = anuncio_ml()
    assert (await enriquecer.cartao_produto(db, integ, f, "ml", ITEM_ML))["titulo"]


def test_cartao_incompleto_e_cartao_do_pedido():
    assert enriquecer.cartao_incompleto([enriquecer.cartao_produto_vazio("1")])
    assert enriquecer.cartao_incompleto([enriquecer.cartao_pedido_vazio("P")])
    assert enriquecer.cartao_incompleto([{"tipo": "produto", "id": "1"}])  # formato antigo
    assert not enriquecer.cartao_incompleto([{"tipo": "imagem", "url": "https://x"}])
    assert set(enriquecer.cartao_pedido_vazio("P")) == CHAVES_CARTAO_PEDIDO


# ─────────────── a conversa: renovação, cota, nunca levanta ───────────────


async def test_enriquecer_conversa_renova_a_cada_30_min_e_forcar(db, make_user, relogio):
    integ = await _integ(db, await make_user(), IntegrationPlatform.SHOPEE, "Loja")
    conversa = await _conversa_simples(
        db, integ, plataforma="shopee", canal="chat", externo="C1", pedido_marketplace="P1"
    )
    f = ShopeeFalso()
    f.pedidos["P1"] = pedido_shopee("P1")

    assert await enriquecer.enriquecer_conversa(db, conversa, integ, f) is True
    await db.commit()
    await db.refresh(conversa)
    retrato = conversa.dados["pedido_mkt"]
    assert retrato["pedido"] == "P1" and retrato["status_texto"] == "Pronto para enviar"
    assert conversa.dados["enriquecimento"]["pedido_mkt"] == {
        "id": "P1",
        "em": AGORA.isoformat(timespec="seconds"),
        "resultado": "ok",
        "falhas": 0,
        "proxima": (AGORA + timedelta(minutes=30)).isoformat(timespec="seconds"),
    }
    assert f.contagem("pedido") == 1

    relogio["agora"] = AGORA + timedelta(minutes=29)
    assert await enriquecer.enriquecer_conversa(db, conversa, integ, f) is False
    assert f.contagem("pedido") == 1  # dentro dos 30 min: nem pergunta

    # O botão "atualizar" da tela passa por cima dos 30 min.
    assert await enriquecer.enriquecer_conversa(db, conversa, integ, f, forcar=True) is True
    assert f.contagem("pedido") == 2

    relogio["agora"] = AGORA + timedelta(minutes=61)
    f.pedidos["P1"] = pedido_shopee("P1", status="SHIPPED", logistica="LOGISTICS_PICKUP_DONE")
    assert await enriquecer.enriquecer_conversa(db, conversa, integ, f) is True
    await db.commit()
    await db.refresh(conversa)
    assert conversa.dados["pedido_mkt"]["status_texto"] == "Enviado"

    # O pedido da conversa mudou: renova na hora, sem esperar os 30 min.
    conversa.pedido_marketplace = "P2"
    f.pedidos["P2"] = pedido_shopee("P2")
    assert await enriquecer.enriquecer_conversa(db, conversa, integ, f) is True
    assert conversa.dados["pedido_mkt"]["pedido"] == "P2"


async def test_falha_da_loja_carimba_e_nao_repete_a_cada_rodada(db, make_user, relogio):
    integ = await _integ(db, await make_user(), IntegrationPlatform.SHOPEE, "Loja")
    conversa = await _conversa_simples(
        db, integ, plataforma="shopee", canal="chat", externo="C1", pedido_marketplace="P1",
        dados={"pedido_mkt": {"pedido": "P1", "status_texto": "Enviado",
                              "atualizado_em": (AGORA - timedelta(hours=2)).isoformat()}},
    )
    f = ShopeeFalso()
    f.pedidos["P1"] = RuntimeError("shopee_order_detail error_server: x")

    assert await enriquecer.enriquecer_conversa(db, conversa, integ, f) is False
    # O painel fica com o que já tinha; a tentativa fica carimbada.
    assert conversa.dados["pedido_mkt"]["status_texto"] == "Enviado"
    assert conversa.dados["enriquecimento"]["pedido_mkt"]["id"] == "P1"
    relogio["agora"] = AGORA + timedelta(minutes=10)
    await enriquecer.enriquecer_conversa(db, conversa, integ, f)
    assert f.contagem("pedido") == 1


async def test_enriquecer_conversa_nunca_levanta(db, make_user, monkeypatch):
    integ = await _integ(db, await make_user(), IntegrationPlatform.SHOPEE, "Loja")
    conversa = await _conversa_simples(
        db, integ, plataforma="shopee", canal="chat", externo="C1", pedido_marketplace="P1"
    )

    class Explode:
        shop_id = SHOP

        async def get_order_detail_completo(self, sn):
            raise ZeroDivisionError("qualquer coisa")

    assert await enriquecer.enriquecer_conversa(db, conversa, integ, Explode()) is False

    async def quebra(*a, **kw):
        raise RuntimeError("erro inesperado de banco")

    monkeypatch.setattr(enriquecer, "_enriquecer", quebra)
    assert await enriquecer.enriquecer_conversa(db, conversa, integ, Explode()) is False
    monkeypatch.undo()
    # Sem pedido, TikTok, Amazon: nada a fazer, nada de ida.
    for plataforma in ("tiktok", "amazon"):
        outra = await _conversa_simples(
            db, integ, plataforma=plataforma, canal="chat", externo=f"X{plataforma}",
            pedido_marketplace="P9",
        )
        assert await enriquecer.enriquecer_conversa(db, outra, integ, Explode()) is False


async def test_cota_acabou_nao_carimba_para_tentar_na_proxima_rodada(db, make_user):
    integ = await _integ(db, await make_user(), IntegrationPlatform.SHOPEE, "Loja")
    conversa = await _conversa_simples(
        db, integ, plataforma="shopee", canal="chat", externo="C1", pedido_marketplace="P1"
    )
    f = ShopeeFalso()
    f.pedidos["P1"] = pedido_shopee("P1")
    cota = enriquecer.Cota(restam=0)
    assert await enriquecer.enriquecer_conversa(db, conversa, integ, f, cota=cota) is False
    assert "enriquecimento" not in (conversa.dados or {})
    assert f.contagem("pedido") == 0
    # A memória da cota: o mesmo pedido de novo na rodada não gasta nem vai à loja.
    cota = enriquecer.Cota(restam=1)
    assert await enriquecer.retrato_pedido(db, integ, f, "shopee", "P1", cota=cota)
    assert await enriquecer.retrato_pedido(db, integ, f, "shopee", "P1", cota=cota)
    assert (cota.restam, f.contagem("pedido")) == (0, 1)


# ─────────────── ganchos no sync da Shopee ───────────────


async def _canal_shopee(db: AsyncSession, make_user):
    integ = await _integ(db, await make_user(), IntegrationPlatform.SHOPEE, "Loja KFA")
    return integ, await _canal(db, integ, "shopee", "chat")


async def _rodar_shopee(db, canal, integ, f):
    r = await shopee_atd.sincronizar(db, canal, integ, f)
    await db.commit()
    return r


async def test_sync_shopee_cartoes_de_produto_e_pedido_e_painel(
    db, make_user, teto, relogio
):
    integ, canal = await _canal_shopee(db, make_user)
    f = ShopeeFalso()
    t = AGORA - timedelta(hours=1)
    f.conversa(
        "T",
        msg_shopee("T", t, tipo="item", content={"shop_id": SHOP, "item_id": 4112503530}),
        msg_shopee("T", t + timedelta(minutes=1), tipo="order",
                   content={"shop_id": SHOP, "order_sn": "250927ABC"}),
        msg_shopee("T", t + timedelta(minutes=2)),
    )
    f.produtos["4112503530"] = item_shopee(4112503530, com_variacao=False, preco=129.9)
    f.pedidos["250927ABC"] = pedido_shopee("250927ABC")

    r = await _rodar_shopee(db, canal, integ, f)

    assert r.status == "ok" and r.mensagens_novas == 3
    conv = (await _conversas(db))["T"]
    ms = await _mensagens(db, conv.id)
    produto, pedido = ms[0].anexos[0], ms[1].anexos[0]
    assert set(produto) == CHAVES_CARTAO_PRODUTO and set(pedido) == CHAVES_CARTAO_PEDIDO
    assert (produto["titulo"], produto["imagem"], produto["preco"]) == (
        "Mochila Executiva Notebook 4112503530",
        FOTO_ANUNCIO,
        129.9,
    )
    assert (pedido["pedido"], pedido["status_texto"], pedido["total"]) == (
        "250927ABC",
        "Pronto para enviar",
        766.19,
    )
    assert pedido["itens"][0]["imagem"] == FOTO_SHOPEE
    # O painel da conversa (o pedido ligado) sai da MESMA ida à loja do cartão.
    assert conv.pedido_marketplace == "250927ABC"
    assert conv.dados["pedido_mkt"]["pedido"] == "250927ABC"
    assert f.contagem("pedido") == 1
    _sem_pessoais(conv.dados["pedido_mkt"])
    _sem_pessoais(pedido)


async def test_sync_shopee_falha_da_loja_nao_derruba_a_rodada(db, make_user, teto):
    integ, canal = await _canal_shopee(db, make_user)
    f = ShopeeFalso()
    t = AGORA - timedelta(hours=1)
    f.conversa(
        "F",
        msg_shopee("F", t, tipo="item", content={"shop_id": SHOP, "item_id": 1}),
        msg_shopee("F", t + timedelta(minutes=1), tipo="order",
                   content={"shop_id": SHOP, "order_sn": "PX"}),
    )
    f.erro_produto = httpx.ReadTimeout("timeout")
    f.pedidos["PX"] = RuntimeError("shopee_order_detail error_server: busy")

    r = await _rodar_shopee(db, canal, integ, f)

    assert (r.status, r.mensagens_novas) == ("ok", 2)
    conv = (await _conversas(db))["F"]
    ms = await _mensagens(db, conv.id)
    assert ms[0].anexos == [enriquecer.cartao_produto_vazio("1")]
    assert ms[1].anexos == [enriquecer.cartao_pedido_vazio("PX")]
    assert "pedido_mkt" not in conv.dados

    # A loja voltou: a mensagem relida (conversa com movimento) completa o cartão.
    f.erro_produto = None
    f.produtos["1"] = item_shopee(1, com_variacao=False)
    f.pedidos["PX"] = pedido_shopee("PX")
    f.conversa("F", msg_shopee("F", t + timedelta(minutes=5)))
    await _rodar_shopee(db, canal, integ, f)
    ms = await _mensagens(db, conv.id)
    assert ms[0].anexos[0]["titulo"] == "Mochila Executiva Notebook 1"
    assert ms[1].anexos[0]["status"] == "READY_TO_SHIP"
    # O retrato que o cartão buscou vale para o painel na hora, mesmo dentro
    # dos 30 min da tentativa que tinha falhado (não custa ida à loja).
    conv = (await _conversas(db))["F"]
    assert conv.dados["pedido_mkt"]["pedido"] == "PX"
    assert f.contagem("pedido") == 2


async def test_sync_shopee_teto_de_20_por_rodada_e_o_resto_na_seguinte(
    db, make_user, teto
):
    integ, canal = await _canal_shopee(db, make_user)
    f = ShopeeFalso()
    for i in range(25):
        cid, sn = f"C{i:02d}", f"2509PED{i:02d}"
        f.conversa(
            cid,
            msg_shopee(cid, AGORA - timedelta(minutes=60 - i), tipo="order",
                       content={"shop_id": SHOP, "order_sn": sn}),
        )
        f.pedidos[sn] = pedido_shopee(sn)

    r1 = await _rodar_shopee(db, canal, integ, f)

    assert r1.status == "ok" and r1.conversas_novas == 25
    assert f.contagem("pedido") == enriquecer.MAX_POR_RODADA == 20
    convs = await _conversas(db)
    sem = [c for c in convs.values() if "pedido_mkt" not in c.dados]
    assert len(sem) == 5
    # Quem ficou sem não foi carimbado: entra na próxima rodada.
    assert all("enriquecimento" not in c.dados for c in sem)

    # Rodada seguinte, sem nada novo no chat: a sobra da cota completa as 5 —
    # o painel E o cartão da mensagem (que tinha ficado só com o número).
    r2 = await _rodar_shopee(db, canal, integ, f)
    assert r2.mensagens_novas == 0
    assert f.contagem("pedido") == 25
    convs = await _conversas(db)
    assert all(c.dados.get("pedido_mkt") for c in convs.values())
    for c in sem:
        (m,) = await _mensagens(db, c.id)
        assert m.anexos[0]["status_texto"] == "Pronto para enviar"

    # E a terceira não pergunta de novo (30 min).
    await _rodar_shopee(db, canal, integ, f)
    assert f.contagem("pedido") == 25


async def test_sync_shopee_grava_a_foto_do_comprador(db, make_user, teto):
    integ, canal = await _canal_shopee(db, make_user)
    f = ShopeeFalso()
    foto = "https://cf.shopee.com.br/file/avatar-comprador"
    f.conversa("A", msg_shopee("A", AGORA - timedelta(minutes=5)), avatar=foto)
    f.conversa("B", msg_shopee("B", AGORA - timedelta(minutes=6)), avatar="")
    f.conversa("C", msg_shopee("C", AGORA - timedelta(minutes=7)), avatar="javascript:x")
    await _rodar_shopee(db, canal, integ, f)
    convs = await _conversas(db)
    assert convs["A"].comprador_avatar == foto
    assert convs["B"].comprador_avatar is None  # vazio (o comum) não grava nada
    assert convs["C"].comprador_avatar is None  # o que não serve para <img>, também não

    # A lista seguinte vem com `to_avatar` vazio: a foto que já estava fica.
    f.conversa("A", msg_shopee("A", AGORA - timedelta(minutes=1)), avatar="")
    await _rodar_shopee(db, canal, integ, f)
    assert (await _conversas(db))["A"].comprador_avatar == foto


# ─────────────── ganchos no sync do ML ───────────────


async def test_sync_ml_pergunta_ganha_cartao_do_anuncio(db, make_user, relogio):
    integ = await _integ(db, await make_user(), IntegrationPlatform.ML, "ML KFA")
    canal = await _canal(db, integ, "ml", "pergunta")
    f = MLFalso()
    f.perguntas = [pergunta_ml(101), pergunta_ml(102)]  # duas perguntas, mesmo anúncio
    f.anuncios[ITEM_ML] = anuncio_ml()

    r = await ml_atd.sincronizar(db, canal, integ, f)
    await db.commit()

    assert r.status == "ok" and r.conversas_novas == 2
    convs = await _conversas(db)
    for externo in ("q:101", "q:102"):
        c = convs[externo]
        assert set(c.dados["produto"]) == CHAVES_CARTAO_PRODUTO
        assert c.dados["produto"]["imagem"] == FOTO_ML_GRANDE
        assert c.dados["produto"]["preco"] == 99.95
        # O título da lista veio do cartão: sem a ida ao /items/{id}.
        assert c.anuncio_titulo == "Fone Bluetooth Sem Fio Cancelamento de Ruído"
    assert f.contagem("itens") == 1  # a segunda pergunta usa o cache
    assert f.contagem("get_item") == 0
    assert "pedido_mkt" not in convs["q:101"].dados  # pergunta é pré-venda


async def test_sync_ml_pos_venda_ganha_retrato_pelo_order_id(db, make_user, relogio):
    integ = await _integ(db, await make_user(), IntegrationPlatform.ML, "ML KFA")
    canal = await _canal(db, integ, "ml", "pos_venda")
    f = MLFalso()
    # Mensagem com o recurso "orders" (o caminho em que o pedido aparece).
    f.packs[PACK] = [msg_pack("m1", AGORA - timedelta(minutes=20), pedido=ORDEM)]
    f.ordens[ORDEM] = ordem_ml()
    f.envios[ENVIO] = envio_ml()
    f.anuncios[ITEM_ML] = anuncio_ml()

    r = await ml_atd.sincronizar(db, canal, integ, f)
    await db.commit()

    assert r.status == "ok"
    conv = (await _conversas(db))[PACK]
    retrato = conv.dados["pedido_mkt"]
    assert retrato["pedido"] == ORDEM  # pelo order_id do pack, não pelo pack
    assert f.contagem("pack") == 0  # o pedido já estava à vista
    assert retrato["logistica"]["status_texto"] == "A caminho"
    _sem_pessoais(retrato)
    assert f.contagem("pedido") == 1


async def test_sync_ml_falha_da_loja_nao_derruba_a_rodada(db, make_user, relogio):
    integ = await _integ(db, await make_user(), IntegrationPlatform.ML, "ML KFA")
    canal = await _canal(db, integ, "ml", "pos_venda")
    f = MLFalso()
    f.packs[PACK] = [msg_pack("m1", AGORA - timedelta(minutes=20))]
    f.packs_api[PACK] = pack_ml(ORDEM)
    f.ordens[ORDEM] = _erro_http(500)

    r = await ml_atd.sincronizar(db, canal, integ, f)
    await db.commit()

    assert (r.status, r.mensagens_novas, r.erro) == ("ok", 1, None)
    conv = (await _conversas(db))[PACK]
    assert "pedido_mkt" not in conv.dados


# ─────────────── revisão: dado pessoal, valores, pack, espera ───────────────


def _com_evento(sn: str, descricao: str) -> tuple[str, dict]:
    return (
        "BR2643431879151",
        {
            "logistics_status": "LOGISTICS_DELIVERY_DONE",
            "order_sn": sn,
            "tracking_info": [
                {"update_time": T_PEDIDO + 90000, "description": descricao,
                 "logistics_status": "LOGISTICS_DELIVERY_DONE"},
            ],
        },
    )


@pytest.mark.parametrize(
    "descricao",
    [
        f"Recebido por {NOME_REAL} CPF {CPF}",
        "Recebido por: J. Silva Santos",
        f"Entregue para {NOME_REAL}",
        "Recebedor: Maria Aparecida - RG 12.345.678-9",
        "Objeto entregue a JOAO SOUZA (porteiro)",
        "Received by John Smith",
        "Tentativa de entrega - Rua das Flores 123",
        "Maria Silva recebeu o pedido",
        "Destinatário: Maria Silva",
        "Entregue - CEP 01415-000",
    ],
)
async def test_descricao_do_rastreio_com_dado_pessoal_fica_de_fora(db, make_user, descricao):
    """A máscara antiga deixava passar metade do CPF, o nome depois do "J." e
    toda frase sem "recebido por": com qualquer sinal, a descrição sai inteira."""
    integ = await _integ(db, await make_user(), IntegrationPlatform.SHOPEE, "Loja")
    f = ShopeeFalso()
    f.pedidos["P1"] = pedido_shopee("P1", status="COMPLETED", logistica="LOGISTICS_DELIVERY_DONE")
    f.rastreios["P1"] = _com_evento("P1", descricao)

    r = await enriquecer.retrato_pedido(db, integ, f, "shopee", "P1")

    log = r["logistica"]
    assert log["descricao"] is None
    # O estado e a hora do evento continuam no painel.
    assert (log["status_texto"], log["atualizado_em"]) == (
        "Entregue",
        datetime.fromtimestamp(T_PEDIDO + 90000, UTC).isoformat(),
    )
    texto = json.dumps(r, ensure_ascii=False)
    for pedaco in ("Maria", "Silva", "456.789", "JOAO", "John", "Flores", "12.345.678", "01415"):
        assert pedaco not in texto, pedaco


@pytest.mark.parametrize(
    "descricao",
    [
        "Pedido coletado pela transportadora",
        "Em trânsito para o centro de distribuição de Cajamar",
        "Destinatário ausente: nova tentativa no próximo dia útil",
        "Entregue na portaria",
    ],
)
async def test_descricao_do_rastreio_sem_dado_pessoal_entra(db, make_user, descricao):
    integ = await _integ(db, await make_user(), IntegrationPlatform.SHOPEE, "Loja")
    f = ShopeeFalso()
    f.pedidos["P1"] = pedido_shopee("P1", status="SHIPPED", logistica="LOGISTICS_PICKUP_DONE")
    f.rastreios["P1"] = _com_evento("P1", descricao)
    r = await enriquecer.retrato_pedido(db, integ, f, "shopee", "P1")
    assert r["logistica"]["descricao"] == descricao


async def test_shopee_invoice_pending_e_sem_rastreio_antes_de_sair(db, make_user):
    """INVOICE_PENDING em português; nada de rastreio antes da coleta combinada
    (PENDING_ARRANGE) nem sem pacote num pedido que não saiu — 2 idas à toa."""
    integ = await _integ(db, await make_user(), IntegrationPlatform.SHOPEE, "Loja")
    f = ShopeeFalso()
    f.pedidos["P1"] = pedido_shopee("P1", status="INVOICE_PENDING",
                                    logistica="LOGISTICS_NOT_START")
    f.pedidos["P2"] = pedido_shopee("P2", status="READY_TO_SHIP",
                                    logistica="LOGISTICS_PENDING_ARRANGE")
    f.pedidos["P3"] = {**pedido_shopee("P3", status="PROCESSED"), "package_list": []}
    f.pedidos["P4"] = {**pedido_shopee("P4", status="SHIPPED"), "package_list": []}

    r1, r2, _, _ = [
        await enriquecer.retrato_pedido(db, integ, f, "shopee", sn)
        for sn in ("P1", "P2", "P3", "P4")
    ]

    assert (r1["status"], r1["status_texto"]) == ("INVOICE_PENDING", "Aguardando nota fiscal")
    assert r2["logistica"]["status_texto"] == "Aguardando postagem"
    # Só o pedido que já saiu pergunta o rastreio.
    assert [c for c in f.chamadas if c[0] in ("rastreio", "eventos")] == [
        ("rastreio", "P4"),
        ("eventos", "P4"),
    ]


async def test_ml_frete_so_do_pagamento_aprovado(db, make_user):
    """`payments` traz toda tentativa: o cartão recusado antes do aprovado não dobra o frete."""
    integ = await _integ(db, await make_user(), IntegrationPlatform.ML, "ML")
    f = MLFalso()
    ordem = ordem_ml()
    aprovado = ordem["payments"][0]
    recusado = {**aprovado, "id": 1, "status": "rejected", "date_approved": None,
                "status_detail": "cc_rejected_other_reason"}
    f.ordens[ORDEM] = {**ordem, "payments": [recusado, aprovado]}

    r = await enriquecer.retrato_pedido(db, integ, f, "ml", ORDEM)

    assert r["frete"] == 24.9  # não 49,80
    assert (r["pagamento_metodo"], r["pago_em"]) == (
        "Cartão de crédito",
        "2026-09-27T16:00:30+00:00",
    )
    # Nenhum aprovado: não há frete pago a mostrar.
    f.ordens[ORDEM] = {**ordem, "payments": [recusado]}
    assert (await enriquecer.retrato_pedido(db, integ, f, "ml", ORDEM))["frete"] is None


async def test_sync_ml_pack_no_formato_real_resolve_o_pedido_pelo_packs(
    db, make_user, relogio
):
    """Formato real: nem `orders` nos recursos nem `data.order_id`. A conversa
    guarda o PACK, e `/orders/{pack}` é 404 — o retrato vem do `/packs`."""
    integ = await _integ(db, await make_user(), IntegrationPlatform.ML, "ML KFA")
    canal = await _canal(db, integ, "ml", "pos_venda")
    f = MLFalso()
    f.packs[PACK] = [msg_pack("m1", AGORA - timedelta(minutes=20))]
    f.packs_api[PACK] = pack_ml(ORDEM)
    f.ordens[ORDEM] = ordem_ml()
    f.envios[ENVIO] = envio_ml()
    f.anuncios[ITEM_ML] = anuncio_ml()

    r = await ml_atd.sincronizar(db, canal, integ, f)
    await db.commit()

    assert r.status == "ok"
    conv = (await _conversas(db))[PACK]
    assert conv.pedido_marketplace == PACK and "order_id" not in conv.dados
    retrato = conv.dados["pedido_mkt"]
    assert retrato["pedido"] == ORDEM
    assert (retrato["status_texto"], retrato["logistica"]["status_texto"]) == ("Pago", "A caminho")
    assert retrato["itens"][0]["imagem"] == FOTO_ML_GRANDE
    assert [c for c in f.chamadas if c[0] in ("pack", "pedido", "envio")] == [
        ("pack", PACK),
        ("pedido", ORDEM),
        ("envio", ENVIO),
    ]
    assert ("pedido", PACK) not in f.chamadas  # nunca o /orders/{pack}
    assert conv.dados["enriquecimento"]["pedido_mkt"]["resultado"] == "ok"
    _sem_pessoais(retrato)


async def test_ml_pack_com_varios_pedidos_junta_itens_e_valores(db, make_user):
    integ = await _integ(db, await make_user(), IntegrationPlatform.ML, "ML")
    f = MLFalso()
    segunda = "2000002222222222"
    f.packs_api[PACK] = pack_ml(ORDEM, segunda)
    f.ordens[ORDEM] = ordem_ml()
    outra = ordem_ml(segunda)
    outra["order_items"][0]["item"] = {
        **outra["order_items"][0]["item"], "id": "MLB2", "title": "Capa", "seller_sku": "CAPA",
        "variation_attributes": [],
    }
    outra["order_items"][0].update({"quantity": 1, "unit_price": 50.0})
    outra["payments"][0].update({"shipping_cost": 0.0, "transaction_amount": 50.0})
    outra.update({"total_amount": 50.0, "paid_amount": 50.0})
    f.ordens[segunda] = outra
    f.envios[ENVIO] = envio_ml()

    r = await enriquecer.retrato_pedido(db, integ, f, "ml", PACK, pack=True)

    assert r["pedido"] == PACK  # o carrinho é mostrado pelo pack
    assert [i["sku"] for i in r["itens"]] == ["FONE-PT", "CAPA"]
    assert (r["total"], r["valor_pago"], r["frete"]) == (249.9, 274.8, 24.9)
    assert f.chamadas[:3] == [("pack", PACK), ("pedido", ORDEM), ("pedido", segunda)]
    assert f.contagem("envio") == 1  # um envio para o carrinho inteiro
    assert f.contagem("itens") == 1  # as fotos numa ida só


async def test_ml_numero_que_nao_e_pack_vai_direto_ao_pedido(db, make_user):
    """Pedido sem carrinho usa o próprio order id no lugar do pack: `/packs` dá 404."""
    integ = await _integ(db, await make_user(), IntegrationPlatform.ML, "ML")
    f = MLFalso()
    f.ordens[PACK] = ordem_ml(PACK)
    r = await enriquecer.retrato_pedido(db, integ, f, "ml", PACK, pack=True)
    assert r["pedido"] == PACK
    assert [n for n, _ in f.chamadas][:2] == ["pack", "pedido"]


async def test_falha_espaca_as_tentativas_e_o_sucesso_zera(db, make_user, relogio):
    integ = await _integ(db, await make_user(), IntegrationPlatform.SHOPEE, "Loja")
    conversa = await _conversa_simples(
        db, integ, plataforma="shopee", canal="chat", externo="C1", pedido_marketplace="P1"
    )
    f = ShopeeFalso()
    f.pedidos["P1"] = RuntimeError("shopee_order_detail error_server: busy")

    for vez, espera in enumerate(
        (timedelta(minutes=30), timedelta(hours=2), timedelta(hours=12),
         timedelta(hours=24), timedelta(hours=24)),
        start=1,
    ):
        assert await enriquecer.enriquecer_conversa(db, conversa, integ, f) is False
        marca = conversa.dados["enriquecimento"]["pedido_mkt"]
        assert (marca["resultado"], marca["falhas"]) == ("erro", vez)
        proxima = datetime.fromisoformat(marca["proxima"])
        assert proxima - relogio["agora"] == espera
        # Um minuto antes da hora: nem pergunta à loja.
        idas = f.contagem("pedido")
        relogio["agora"] = proxima - timedelta(minutes=1)
        await enriquecer.enriquecer_conversa(db, conversa, integ, f)
        assert f.contagem("pedido") == idas
        relogio["agora"] = proxima

    f.pedidos["P1"] = pedido_shopee("P1")
    assert await enriquecer.enriquecer_conversa(db, conversa, integ, f) is True
    marca = conversa.dados["enriquecimento"]["pedido_mkt"]
    assert (marca["resultado"], marca["falhas"]) == ("ok", 0)


async def test_pedido_que_a_loja_nao_acha_espera_24h_e_o_botao_passa(db, make_user, relogio):
    integ = await _integ(db, await make_user(), IntegrationPlatform.SHOPEE, "Loja")
    conversa = await _conversa_simples(
        db, integ, plataforma="shopee", canal="chat", externo="C1", pedido_marketplace="SUMIU"
    )
    f = ShopeeFalso()  # `order_list` vazio: a Shopee não conhece o pedido

    await enriquecer.enriquecer_conversa(db, conversa, integ, f)

    marca = conversa.dados["enriquecimento"]["pedido_mkt"]
    assert marca["resultado"] == "nao_encontrado"
    assert datetime.fromisoformat(marca["proxima"]) == AGORA + timedelta(hours=24)
    relogio["agora"] = AGORA + timedelta(hours=23)
    await enriquecer.enriquecer_conversa(db, conversa, integ, f)
    assert f.contagem("pedido") == 1
    await enriquecer.enriquecer_conversa(db, conversa, integ, f, forcar=True)
    assert f.contagem("pedido") == 2


async def test_canal_nao_gasta_a_cota_toda_rodada_com_quem_sempre_falha(
    db, make_user, relogio
):
    """20 pós-vendas cujo pedido a loja não acha + 1 esperando resposta com o
    retrato de 5 h. Antes: 20 idas perdidas por rodada, PARA SEMPRE, e a que
    espera resposta nunca era renovada (sem retrato vinha primeiro)."""
    integ = await _integ(db, await make_user(), IntegrationPlatform.ML, "ML KFA")
    canal = await _canal(db, integ, "ml", "pos_venda")
    for i in range(20):
        await _conversa_simples(
            db, integ, plataforma="ml", canal="pos_venda", externo=f"X{i:02d}",
            pedido_marketplace=f"7000{i:02d}", dados={"order_id": f"7000{i:02d}"},
            aguardando_resposta=False, ultima_mensagem_em=AGORA - timedelta(hours=1),
        )
    velho = (AGORA - timedelta(hours=5)).isoformat()
    espera = await _conversa_simples(
        db, integ, plataforma="ml", canal="pos_venda", externo="ESPERA",
        pedido_marketplace=ORDEM,
        dados={"order_id": ORDEM, "pedido_mkt": {"pedido": ORDEM, "status_texto": "Velho",
                                                "atualizado_em": velho}},
        aguardando_resposta=True, ultima_mensagem_em=AGORA - timedelta(hours=2),
    )
    f = MLFalso()  # nenhum dos 20 existe: 404
    f.ordens[ORDEM] = ordem_ml()

    idas = []
    for _ in range(4):
        antes = f.contagem("pedido")
        await enriquecer.enriquecer_canal(db, canal, integ, f, enriquecer.Cota())
        idas.append(f.contagem("pedido") - antes)
        relogio["agora"] += timedelta(minutes=31)

    # 1ª: a que espera resposta + 19 (a cota é 20); 2ª: ela de novo + a 20ª,
    # que ficou de fora; depois, só ela — as que falharam esperam 24 h.
    assert idas == [20, 2, 1, 1]
    await db.refresh(espera)
    assert espera.dados["pedido_mkt"]["status_texto"] == "Pago"


async def test_pergunta_de_anuncio_apagado_nao_volta_a_cada_rodada(db, make_user, relogio):
    integ = await _integ(db, await make_user(), IntegrationPlatform.ML, "ML KFA")
    canal = await _canal(db, integ, "ml", "pergunta")
    conversa = await _conversa_simples(
        db, integ, plataforma="ml", canal="pergunta", externo="q:1", anuncio_id="MLB999",
        aguardando_resposta=True, ultima_mensagem_em=AGORA,
    )
    f = MLFalso()  # o /items não devolve o anúncio, e o catálogo não o tem

    await enriquecer.enriquecer_canal(db, canal, integ, f, enriquecer.Cota())
    await db.refresh(conversa)
    marca = conversa.dados["enriquecimento"]["produto"]
    assert marca["resultado"] == "nao_encontrado"

    relogio["agora"] = AGORA + timedelta(minutes=31)
    await enriquecer.enriquecer_canal(db, canal, integ, f, enriquecer.Cota())
    assert f.contagem("itens") == 1


async def test_conversa_que_troca_de_pedido_nao_fica_com_o_retrato_do_antigo(
    db, make_user, relogio
):
    integ = await _integ(db, await make_user(), IntegrationPlatform.SHOPEE, "Loja")
    canal = await _canal(db, integ, "shopee", "chat")
    conversa = await _conversa_simples(
        db, integ, plataforma="shopee", canal="chat", externo="C1", pedido_marketplace="A",
        aguardando_resposta=True, ultima_mensagem_em=AGORA,
    )
    f = ShopeeFalso()
    f.pedidos["A"] = pedido_shopee("A")
    await enriquecer.enriquecer_conversa(db, conversa, integ, f)
    await db.commit()
    assert conversa.dados["pedido_mkt"]["pedido"] == "A"

    # O comprador mandou o cartão do pedido B, e a loja falhou ao buscá-lo.
    conversa.pedido_marketplace = "B"
    f.pedidos["B"] = RuntimeError("shopee_order_detail error_server: busy")
    assert await enriquecer.enriquecer_conversa(db, conversa, integ, f) is False
    await db.commit()
    await db.refresh(conversa)
    assert "pedido_mkt" not in conversa.dados  # nada do A numa conversa sobre o B
    assert conversa.dados["enriquecimento"]["pedido_mkt"]["id"] == "B"

    # Sem retrato, a sobra da cota completa quando chega a hora (e a loja voltou).
    f.pedidos["B"] = pedido_shopee("B")
    relogio["agora"] = AGORA + timedelta(minutes=31)
    await enriquecer.enriquecer_canal(db, canal, integ, f, enriquecer.Cota())
    await db.refresh(conversa)
    assert conversa.dados["pedido_mkt"]["pedido"] == "B"


async def test_troca_de_pedido_sem_cota_tambem_tira_o_retrato_antigo(db, make_user, relogio):
    integ = await _integ(db, await make_user(), IntegrationPlatform.SHOPEE, "Loja")
    conversa = await _conversa_simples(
        db, integ, plataforma="shopee", canal="chat", externo="C1", pedido_marketplace="A"
    )
    f = ShopeeFalso()
    f.pedidos["A"] = pedido_shopee("A")
    await enriquecer.enriquecer_conversa(db, conversa, integ, f)
    await db.commit()

    conversa.pedido_marketplace = "B"
    await enriquecer.enriquecer_conversa(
        db, conversa, integ, f, cota=enriquecer.Cota(restam=0)
    )
    assert "pedido_mkt" not in conversa.dados
    # Não tentou: o carimbo continua o do A (a próxima rodada tenta o B na hora).
    assert conversa.dados["enriquecimento"]["pedido_mkt"]["id"] == "A"
    assert f.contagem("pedido") == 1


async def test_foto_do_catalogo_do_ml_em_https_e_no_tamanho_maior(
    db, make_user, redis_falso
):
    user = await make_user()
    integ = await _integ(db, user, IntegrationPlatform.ML, "ML")
    base = {"user_id": user.id, "integration_id": integ.id, "platform": IntegrationPlatform.ML}
    db.add_all(
        [
            Listing(**base, external_id=ITEM_ML, title="Fone", price=9990,
                    thumbnail_url="http://http2.mlstatic.com/D_990184-MLB70000000000_072023-I.jpg"),
            Listing(**base, external_id="MLB2", title="Capa", price=4990,
                    thumbnail_url="http://http2.mlstatic.com/D_990185-MLB2-I.webp"),
        ]
    )
    await db.commit()
    f = MLFalso()  # o /items não traz o ITEM_ML (anúncio fechado)

    c = await enriquecer.cartao_produto(db, integ, f, "ml", ITEM_ML)
    assert c["imagem"] == "https://http2.mlstatic.com/D_990184-MLB70000000000_072023-O.jpg"

    # A loja respondeu sem foto: o cartão que vai para o cache sai tratado também.
    f.anuncios["MLB2"] = {**anuncio_ml("MLB2"), "thumbnail": None, "secure_thumbnail": None}
    c2 = await enriquecer.cartao_produto(db, integ, f, "ml", "MLB2")
    assert c2["imagem"] == "https://http2.mlstatic.com/D_990185-MLB2-O.webp"
    cache = json.loads(redis_falso.dados[f"atd:prod:ml:{integ.id}:MLB2"])
    assert cache["imagem"] == c2["imagem"]


# ─────────────── os clientes de verdade: só GET, parâmetros certos ───────────────


def _transporte(monkeypatch, handler) -> list[httpx.Request]:
    feitos: list[httpx.Request] = []

    def registrar(request: httpx.Request) -> httpx.Response:
        feitos.append(request)
        return handler(request)

    def fabrica(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(registrar)
        return _AsyncClientReal(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fabrica)
    return feitos


async def test_shopee_de_verdade_so_le_e_nao_pede_dado_pessoal(db, make_user, monkeypatch):
    def api(request: httpx.Request) -> httpx.Response:
        caminho = request.url.path
        if caminho.endswith("/order/get_order_detail"):
            resposta = {"order_list": [pedido_shopee("250927ABC", status="SHIPPED",
                                                     logistica="LOGISTICS_PICKUP_DONE")]}
        elif caminho.endswith("/product/get_item_base_info"):
            resposta = {"item_list": [item_shopee(4112503530)]}
        elif caminho.endswith("/product/get_model_list"):
            resposta = modelos_shopee((129.9, 159.9))
        elif caminho.endswith("/logistics/get_tracking_number"):
            resposta = {"tracking_number": "BR123", "hint": ""}
        elif caminho.endswith("/logistics/get_tracking_info"):
            resposta = {"logistics_status": "LOGISTICS_PICKUP_DONE", "order_sn": "250927ABC",
                        "tracking_info": []}
        else:
            return httpx.Response(404, json={"error": "error_not_found", "message": caminho})
        return httpx.Response(200, json={"error": "", "message": "", "response": resposta})

    feitos = _transporte(monkeypatch, api)
    cliente = ShopeeClient({"shop_id": SHOP, "access_token": "t", "expires_at": 9_999_999_999})
    integ = await _integ(db, await make_user(), IntegrationPlatform.SHOPEE, "Loja")

    r = await enriquecer.retrato_pedido(db, integ, cliente, "shopee", "250927ABC")
    c = await enriquecer.cartao_produto(db, integ, cliente, "shopee", "4112503530")

    assert r["logistica"]["rastreio"] == "BR123" and c["preco"] == 129.9
    assert {p.method for p in feitos} == {"GET"}
    assert {p.url.path for p in feitos} == {
        "/api/v2/order/get_order_detail",
        "/api/v2/product/get_item_base_info",
        "/api/v2/product/get_model_list",
        "/api/v2/logistics/get_tracking_number",
        "/api/v2/logistics/get_tracking_info",
    }
    detalhe = next(p for p in feitos if p.url.path.endswith("/get_order_detail"))
    assert detalhe.url.params["order_sn_list"] == "250927ABC"
    campos = detalhe.url.params["response_optional_fields"].split(",")
    assert {"item_list", "total_amount", "payment_method", "pay_time", "package_list",
            "invoice_data"} <= set(campos)
    # O que não se pede não chega: nada de nome, CPF nem endereço do comprador.
    # Só o ID numérico (`buyer_user_id`, o `to_id` do chat) para o índice do
    # cartão "Cliente" — e ele não entra no retrato (`_sem_pessoais` abaixo).
    assert not [
        c
        for c in campos
        if ("buyer" in c and c != "buyer_user_id") or "recipient" in c or "address" in c
    ]
    assert {"buyer_user_id", "pickup_done_time"} <= set(campos)
    base = next(p for p in feitos if p.url.path.endswith("/get_item_base_info"))
    assert base.url.params["item_id_list"] == "4112503530"
    _sem_pessoais(r)


async def test_ml_de_verdade_so_le_com_formato_novo_do_envio(db, make_user, monkeypatch):
    def api(request: httpx.Request) -> httpx.Response:
        caminho = request.url.path
        if caminho == f"/orders/{ORDEM}":
            return httpx.Response(200, json=ordem_ml())
        if caminho == f"/shipments/{ENVIO}":
            return httpx.Response(200, json=envio_ml())
        if caminho == "/items":
            return httpx.Response(
                200,
                json=[
                    {"code": 200, "body": anuncio_ml()},
                    {"code": 404, "body": {"error": "not_found"}},
                ],
            )
        return httpx.Response(404, json={"error": "not_found"})

    feitos = _transporte(monkeypatch, api)
    cliente = MercadoLivreClient({"access_token": "tok", "refresh_token": "rt",
                                  "user_id": SELLER})
    integ = await _integ(db, await make_user(), IntegrationPlatform.ML, "ML")

    r = await enriquecer.retrato_pedido(db, integ, cliente, "ml", ORDEM)
    assert r["itens"][0]["imagem"] == FOTO_ML_GRANDE
    assert r["logistica"]["rastreio"] == "MEL44001234567FMDOF01"
    assert {p.method for p in feitos} == {"GET"}
    envio = next(p for p in feitos if p.url.path.startswith("/shipments/"))
    assert envio.headers.get("x-format-new") == "true"
    itens = next(p for p in feitos if p.url.path == "/items")
    assert itens.url.params["ids"] == ITEM_ML
    assert set(itens.url.params["attributes"].split(",")) == {
        "id", "title", "price", "original_price", "thumbnail", "secure_thumbnail",
        "permalink", "variations",
    }
    assert await cliente.itens([]) == []
    _sem_pessoais(r)


# ─────────────── hora de envio e tempo concluído (P4, "como no Duoke") ───────────────


def _evento(segundos: int, status: str, descricao: str = "Evento") -> dict:
    return {"update_time": T_PEDIDO + segundos, "description": descricao,
            "logistics_status": status}


def _iso_s(segundos: int) -> str:
    return datetime.fromtimestamp(T_PEDIDO + segundos, UTC).isoformat()


async def test_shopee_concluido_e_o_update_time_do_pedido_completed(db, make_user):
    integ = await _integ(db, await make_user(), IntegrationPlatform.SHOPEE, "Loja")
    f = ShopeeFalso()
    f.pedidos["P1"] = pedido_shopee("P1", status="COMPLETED",
                                    logistica="LOGISTICS_DELIVERY_DONE")
    f.rastreios["P1"] = (
        "BR1",
        {
            "logistics_status": "LOGISTICS_DELIVERY_DONE",
            "order_sn": "P1",
            "tracking_info": [
                _evento(86400 * 3, "LOGISTICS_DELIVERY_DONE", "Entregue"),
                _evento(3600, "LOGISTICS_REQUEST_CREATED", "Etiqueta criada"),
                _evento(9000, "LOGISTICS_PICKUP_DONE", "Coletado"),
            ],
        },
    )

    r = await enriquecer.retrato_pedido(db, integ, f, "shopee", "P1")

    assert r["enviado_em"] == _iso_s(9000)
    # O `update_time` do pedido de teste é T_PEDIDO + 3600.
    assert r["concluido_em"] == _iso_s(3600)


async def test_shopee_pickup_done_time_vale_mais_que_o_evento(db, make_user):
    """Quando a Shopee manda o horário exato da coleta (no pacote ou no pedido),
    ele vale; o evento do rastreio é o recurso para quando não vem."""
    integ = await _integ(db, await make_user(), IntegrationPlatform.SHOPEE, "Loja")
    f = ShopeeFalso()
    pedido = pedido_shopee("P1", status="SHIPPED", logistica="LOGISTICS_PICKUP_DONE")
    pedido["package_list"][0]["pickup_done_time"] = T_PEDIDO + 5000
    f.pedidos["P1"] = pedido
    f.rastreios["P1"] = ("BR1", {"logistics_status": "LOGISTICS_PICKUP_DONE",
                                 "tracking_info": [_evento(9000, "LOGISTICS_PICKUP_DONE")]})
    r = await enriquecer.retrato_pedido(db, integ, f, "shopee", "P1")
    assert (r["enviado_em"], r["concluido_em"]) == (_iso_s(5000), None)

    # No nível do pedido (campo opcional `pickup_done_time`) também vale.
    pedido2 = pedido_shopee("P2", status="SHIPPED", logistica="LOGISTICS_PICKUP_DONE")
    pedido2["pickup_done_time"] = T_PEDIDO + 4000
    f.pedidos["P2"] = pedido2
    r2 = await enriquecer.retrato_pedido(db, integ, f, "shopee", "P2")
    assert r2["enviado_em"] == _iso_s(4000)


async def test_shopee_eventos_no_vocabulario_da_doc_tambem_contam(db, make_user):
    """O vocabulário do evento não foi medido (o pedido sondado não tinha
    evento): `PICKED_UP`, da doc da Shopee, também marca a coleta."""
    integ = await _integ(db, await make_user(), IntegrationPlatform.SHOPEE, "Loja")
    f = ShopeeFalso()
    f.pedidos["P1"] = pedido_shopee("P1", status="SHIPPED", logistica="LOGISTICS_PICKUP_DONE")
    f.rastreios["P1"] = ("BR1", {"tracking_info": [
        _evento(2000, "ORDER_CREATED"), _evento(7000, "PICKED_UP"), _evento(9000, "DELIVERED"),
    ]})
    r = await enriquecer.retrato_pedido(db, integ, f, "shopee", "P1")
    assert r["enviado_em"] == _iso_s(7000)


async def test_shopee_sem_coleta_sem_hora_de_envio(db, make_user):
    """Pronto para enviar, ou enviado sem evento de coleta: None — nada de chute."""
    integ = await _integ(db, await make_user(), IntegrationPlatform.SHOPEE, "Loja")
    f = ShopeeFalso()
    f.pedidos["P1"] = pedido_shopee("P1", status="READY_TO_SHIP", logistica="LOGISTICS_READY")
    f.pedidos["P2"] = pedido_shopee("P2", status="SHIPPED", logistica="LOGISTICS_PICKUP_DONE")
    f.rastreios["P2"] = ("BR2", {"tracking_info": [_evento(3600, "LOGISTICS_REQUEST_CREATED")]})
    for numero in ("P1", "P2"):
        r = await enriquecer.retrato_pedido(db, integ, f, "shopee", numero)
        assert (r["enviado_em"], r["concluido_em"]) == (None, None)


async def test_ml_entregue_tem_hora_de_envio_e_tempo_concluido(db, make_user):
    integ = await _integ(db, await make_user(), IntegrationPlatform.ML, "ML")
    f = MLFalso()
    f.ordens[ORDEM] = ordem_ml()
    envio = envio_ml(status="delivered", substatus="")
    envio["status_history"]["date_delivered"] = "2026-09-30T15:20:00.000-04:00"
    f.envios[ENVIO] = envio
    r = await enriquecer.retrato_pedido(db, integ, f, "ml", ORDEM)
    assert r["enviado_em"] == "2026-09-28T13:00:00+00:00"
    assert r["concluido_em"] == "2026-09-30T19:20:00+00:00"


async def test_ml_sem_envio_pelo_ml_conclui_no_fechamento_da_venda(db, make_user):
    """Pedido sem Mercado Envios (retirada/a combinar): a venda fechada é tudo o
    que o ML sabe — `date_closed` vira o tempo concluído. Com envio, nunca."""
    integ = await _integ(db, await make_user(), IntegrationPlatform.ML, "ML")
    f = MLFalso()
    sem_envio = ordem_ml()
    sem_envio["shipping"] = {"id": None}
    f.ordens[ORDEM] = sem_envio
    r = await enriquecer.retrato_pedido(db, integ, f, "ml", ORDEM)
    assert (r["enviado_em"], r["concluido_em"]) == (None, "2026-09-27T16:01:00+00:00")
    assert f.contagem("envio") == 0

    # Com envio que falhou ao ler: não se sabe — None (não o date_closed).
    f.ordens["2000003333333333"] = ordem_ml("2000003333333333")
    f.envios[ENVIO] = _erro_http(500)
    r2 = await enriquecer.retrato_pedido(db, integ, f, "ml", "2000003333333333")
    assert (r2["enviado_em"], r2["concluido_em"]) == (None, None)


# ─────────────── índice de pedidos do comprador (P5) ───────────────


class IndiceFalso:
    """O `indice.registrar_pedido` do lote do cartão "Cliente", gravando as chamadas."""

    def __init__(self) -> None:
        self.linhas: list[dict] = []
        self.erro: Exception | None = None

    async def registrar_pedido(self, session, **kw) -> None:
        if self.erro is not None:
            raise self.erro
        self.linhas.append(kw)


@pytest.fixture
def indice_falso(monkeypatch) -> IndiceFalso:
    """Troca o módulo `indice` (exista ele ou não) por um falso.

    `from pacote import indice` olha primeiro o ATRIBUTO do pacote, depois o
    `sys.modules` — os dois apontam para o falso.
    """
    falso = IndiceFalso()
    modulo = types.ModuleType("app.services.atendimento.indice")
    modulo.registrar_pedido = falso.registrar_pedido
    monkeypatch.setitem(sys.modules, "app.services.atendimento.indice", modulo)
    monkeypatch.setattr(pacote_atendimento, "indice", modulo, raising=False)
    return falso


async def test_retrato_shopee_alimenta_o_indice_com_o_comprador_do_pedido(
    db, make_user, indice_falso
):
    integ = await _integ(db, await make_user(), IntegrationPlatform.SHOPEE, "Loja")
    f = ShopeeFalso()
    f.pedidos["250927ABC"] = pedido_shopee("250927ABC")  # traz buyer_user_id 998877

    r = await enriquecer.retrato_pedido(db, integ, f, "shopee", "250927ABC", comprador_id="555")

    [linha] = indice_falso.linhas
    assert linha == {
        "integration_id": integ.id,
        "plataforma": "shopee",
        "comprador_id": "998877",  # o do PEDIDO vale mais que o da conversa
        "pedido": "250927ABC",
        "criado_em": datetime.fromtimestamp(T_PEDIDO, UTC),
        "total": 766.19,
        "status": "READY_TO_SHIP",
        "itens_resumo": "1x Mala de Bordo ABS Rodinhas 360 Cadeado TSA (Preta,P)",
    }
    # O id do comprador vai para o índice — nunca para o retrato do painel.
    _sem_pessoais(r)


async def test_sem_comprador_no_pedido_o_indice_usa_o_da_conversa(
    db, make_user, indice_falso, relogio
):
    """Hoje o painel não pede `buyer_user_id` à Shopee: o comprador é o `to_id`
    da conversa (o mesmo id de usuário). Sem nenhum dos dois, nada é gravado."""
    integ = await _integ(db, await make_user(), IntegrationPlatform.SHOPEE, "Loja")
    f = ShopeeFalso()
    f.pedidos["P1"] = pedido_shopee("P1", com_pessoais=False)
    f.pedidos["P2"] = pedido_shopee("P2", com_pessoais=False)

    assert await enriquecer.retrato_pedido(db, integ, f, "shopee", "P1")
    assert indice_falso.linhas == []  # de quem é? não se sabe: não grava

    conversa = await _conversa_simples(
        db, integ, plataforma="shopee", canal="chat", externo="C1",
        pedido_marketplace="P2", comprador_id=str(COMPRADOR),
    )
    assert await enriquecer.enriquecer_conversa(db, conversa, integ, f) is True
    assert [(x["pedido"], x["comprador_id"]) for x in indice_falso.linhas] == [
        ("P2", str(COMPRADOR))
    ]


async def test_falha_da_loja_nao_vai_para_o_indice(db, make_user, indice_falso):
    integ = await _integ(db, await make_user(), IntegrationPlatform.SHOPEE, "Loja")
    f = ShopeeFalso()
    f.pedidos["ERRO"] = RuntimeError("shopee_order_detail error_server: busy")
    for numero in ("ERRO", "SUMIU"):
        assert (
            await enriquecer.retrato_pedido(db, integ, f, "shopee", numero, comprador_id="1")
            is None
        )
    assert indice_falso.linhas == []


async def test_ml_carrinho_uma_linha_por_pedido_com_o_buyer_id(db, make_user, indice_falso):
    integ = await _integ(db, await make_user(), IntegrationPlatform.ML, "ML")
    f = MLFalso()
    segunda = "2000002222222222"
    f.packs_api[PACK] = pack_ml(ORDEM, segunda)
    f.ordens[ORDEM] = ordem_ml()
    outra = ordem_ml(segunda, status="cancelled")
    outra.update({"total_amount": 50.0})
    f.ordens[segunda] = outra
    f.envios[ENVIO] = envio_ml()

    await enriquecer.retrato_pedido(db, integ, f, "ml", PACK, pack=True)

    assert [
        (x["pedido"], x["comprador_id"], x["status"], x["total"], x["plataforma"])
        for x in indice_falso.linhas
    ] == [
        (ORDEM, "555000222", "paid", 199.9, "ml"),
        (segunda, "555000222", "cancelled", 50.0, "ml"),
    ]
    assert indice_falso.linhas[0]["criado_em"] == datetime(2026, 9, 27, 16, 0, tzinfo=UTC)
    assert indice_falso.linhas[0]["itens_resumo"] == (
        "2x Fone Bluetooth Sem Fio Cancelamento de Ruído (Cor: Preto, Voltagem: Bivolt)"
    )


async def test_memoria_da_rodada_nao_grava_o_mesmo_pedido_duas_vezes(
    db, make_user, indice_falso, teto, relogio
):
    """No sync, o cartão da mensagem de pedido e o painel da conversa saem de UMA
    ida à loja — e o índice ganha UMA linha, com o comprador da conversa."""
    integ, canal = await _canal_shopee(db, make_user)
    f = ShopeeFalso()
    t = AGORA - timedelta(hours=1)
    f.conversa(
        "T",
        msg_shopee("T", t, tipo="order", content={"shop_id": SHOP, "order_sn": "250927ABC"}),
        msg_shopee("T", t + timedelta(minutes=1)),
    )
    f.pedidos["250927ABC"] = pedido_shopee("250927ABC", com_pessoais=False)

    r = await _rodar_shopee(db, canal, integ, f)

    assert r.status == "ok" and f.contagem("pedido") == 1
    assert [(x["pedido"], x["comprador_id"]) for x in indice_falso.linhas] == [
        ("250927ABC", str(COMPRADOR))
    ]


async def test_indice_quebrado_nao_derruba_o_retrato_nem_a_sessao(
    db, make_user, monkeypatch, relogio
):
    """Erro de BANCO dentro do índice (tabela que não existe) desfaz só o
    SAVEPOINT: o retrato chega ao painel e a sessão segue gravando."""
    from sqlalchemy import text

    async def registrar_quebrado(session, **kw) -> None:
        await session.execute(text("INSERT INTO tabela_que_nao_existe VALUES (1)"))

    modulo = types.ModuleType("app.services.atendimento.indice")
    modulo.registrar_pedido = registrar_quebrado
    monkeypatch.setitem(sys.modules, "app.services.atendimento.indice", modulo)
    monkeypatch.setattr(pacote_atendimento, "indice", modulo, raising=False)

    integ = await _integ(db, await make_user(), IntegrationPlatform.SHOPEE, "Loja")
    conversa = await _conversa_simples(
        db, integ, plataforma="shopee", canal="chat", externo="C1",
        pedido_marketplace="P1", comprador_id=str(COMPRADOR),
    )
    f = ShopeeFalso()
    f.pedidos["P1"] = pedido_shopee("P1")

    assert await enriquecer.enriquecer_conversa(db, conversa, integ, f) is True
    await db.commit()
    await db.refresh(conversa)
    assert conversa.dados["pedido_mkt"]["pedido"] == "P1"


async def test_sem_o_modulo_de_indice_o_retrato_sai(db, make_user, monkeypatch):
    """O painel do pedido não depende do cartão "Cliente": sem `indice`, segue."""
    monkeypatch.delattr(pacote_atendimento, "indice", raising=False)
    monkeypatch.setitem(sys.modules, "app.services.atendimento.indice", None)
    integ = await _integ(db, await make_user(), IntegrationPlatform.SHOPEE, "Loja")
    f = ShopeeFalso()
    f.pedidos["P1"] = pedido_shopee("P1")
    r = await enriquecer.retrato_pedido(db, integ, f, "shopee", "P1", comprador_id="555")
    assert r["pedido"] == "P1"


async def test_indice_de_verdade_grava_a_linha_do_pedido(db, make_user, relogio):
    """Com o módulo do cartão "Cliente" no lugar, a linha chega à tabela
    `atendimento_pedidos_comprador` (pulado enquanto o módulo não existe)."""
    pytest.importorskip("app.services.atendimento.indice")
    from app.models import AtendimentoPedidoComprador

    integ = await _integ(db, await make_user(), IntegrationPlatform.SHOPEE, "Loja")
    conversa = await _conversa_simples(
        db, integ, plataforma="shopee", canal="chat", externo="C1",
        pedido_marketplace="P1", comprador_id=str(COMPRADOR),
    )
    f = ShopeeFalso()
    f.pedidos["P1"] = pedido_shopee("P1", status="COMPLETED", com_pessoais=False)

    assert await enriquecer.enriquecer_conversa(db, conversa, integ, f) is True
    await db.commit()
    # De novo (o botão "atualizar"): a mesma linha, não outra.
    assert await enriquecer.enriquecer_conversa(db, conversa, integ, f, forcar=True) is True
    await db.commit()

    linhas = (
        await db.execute(
            select(AtendimentoPedidoComprador).where(
                AtendimentoPedidoComprador.integration_id == integ.id
            )
        )
    ).scalars().all()
    assert [(x.pedido, x.comprador_id, x.plataforma, x.status) for x in linhas] == [
        ("P1", str(COMPRADOR), "shopee", "COMPLETED")
    ]
    assert linhas[0].total == 766.19
