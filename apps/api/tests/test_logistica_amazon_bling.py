"""logistica_amazon_bling — o que o Bling dá pro pedido Amazon: serviço (canal),
rastreio dos Correios, previsão de entrega (objeto de postagem) e contato
(e-mail de retransmissão da Amazon). Bling falso."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, Logistica
from app.services import logistica_amazon_bling as svc


class FakeBling:
    def __init__(self):
        self.pedidos = {
            26856107922: {
                "transporte": {
                    "volumes": [
                        {
                            "id": 16221786970,
                            "servico": "SEDEX",
                            "codigoRastreamento": "AD912266053BR",
                        }
                    ]
                },
                "contato": {"id": 1, "nome": "Rosana Vieira de Melo"},
            },
            26854999840: {
                "transporte": {
                    "volumes": [
                        {
                            "id": 16219728520,
                            "servico": "Logistica Amazon Dba",
                            "codigoRastreamento": "",
                        }
                    ]
                },
                "contato": {"id": 2, "nome": "Cliente DBA"},
            },
            333: {
                "transporte": {
                    "volumes": [{"id": 9, "servico": "PAC", "codigoRastreamento": "AA000000001BR"}]
                },
                "contato": {"id": 3, "nome": "Fulano"},
            },
        }
        self.objetos = {
            16221786970: {
                "dataSaida": "2026-09-14",
                "prazoEntregaPrevisto": 7,
                "rastreamento": {"codigo": "AD912266053BR", "descricao": "Objeto postado"},
            },
            9: {"dataSaida": "2026-09-14", "prazoEntregaPrevisto": 3},
        }
        self.contatos = {
            1: {
                "email": "n340cj40yxfjsq4@marketplace.amazon.com.br",
                "nome": "Rosana Vieira de Melo",
            },
            2: {"email": "f711abc@marketplace.amazon.com.br", "nome": "Cliente DBA"},
            3: {"email": "fulano@gmail.com", "nome": "Fulano"},
        }
        self.objetos_pedidos: list[int] = []

    async def get_order(self, bid):
        return self.pedidos[bid]

    async def get_logistica_objeto(self, oid):
        self.objetos_pedidos.append(oid)
        return self.objetos[oid]

    async def get_contato(self, cid):
        return self.contatos[cid]


def _linha(numero: str, **kw) -> Logistica:
    base = {
        "data": date.today(),
        "pedido_bling": numero,
        "plataforma": "Amazon",
        "conta": "kia",
        "meli_status": {},
    }
    base.update(kw)
    return Logistica(**base)


@pytest.mark.asyncio
async def test_enrich_preenche_envio_proprio_dba_e_ignora_email_real(db: AsyncSession):
    db.add_all(
        [
            _linha("296762", meli_status={"order_status": "Shipped", "fulfillment_channel": "MFN"}),
            _linha(
                "296709", meli_status={"order_status": "Shipped", "easyship_status": "PickedUp"}
            ),
            _linha("333"),
            _linha("444"),  # sem espelho no bling_orders
            BlingOrder(numero="296762", bling_id=26856107922),
            BlingOrder(numero="296709", bling_id=26854999840),
            BlingOrder(numero="333", bling_id=333),
        ]
    )
    await db.commit()
    fake = FakeBling()

    out = await svc.enrich_pendentes(db, client=fake)

    assert out["alvo"] == 4 and out["lidos"] == 3
    assert out["sem_bling_id"] == 1 and out["falhas"] == 0
    rows = {r.pedido_bling: r for r in (await db.execute(select(Logistica))).scalars().all()}

    proprio = rows["296762"]
    assert proprio.amazon_canal == "proprio"
    assert proprio.servico_envio == "SEDEX"
    assert proprio.rastreio == "AD912266053BR"
    assert proprio.postagem_data == date(2026, 9, 14)
    assert proprio.previsao_correios == date(2026, 9, 23)  # 14/09 + 7 dias úteis
    assert proprio.cliente_nome == "Rosana Vieira de Melo"
    assert proprio.cliente_email == "n340cj40yxfjsq4@marketplace.amazon.com.br"
    assert proprio.bling_enriquecido_em is not None

    dba = rows["296709"]
    assert dba.amazon_canal == "dba" and dba.servico_envio == "Logistica Amazon Dba"
    assert dba.rastreio is None and dba.previsao_correios is None
    assert dba.cliente_email == "f711abc@marketplace.amazon.com.br"
    # DBA não consulta objeto de postagem (a Amazon entrega).
    assert 16219728520 not in fake.objetos_pedidos

    real = rows["333"]
    assert real.amazon_canal == "proprio" and real.rastreio == "AA000000001BR"
    assert real.cliente_email is None  # e-mail real de cliente nunca é guardado
    assert real.previsao_correios == date(2026, 9, 17)

    assert rows["444"].bling_enriquecido_em is not None  # carimbado pra não repetir

    # Rodada seguinte: quem está completo não volta; o 333 (sem e-mail relay)
    # só depois do intervalo de releitura.
    out2 = await svc.enrich_pendentes(db, client=fake)
    assert out2["alvo"] == 0


@pytest.mark.asyncio
async def test_enrich_nao_apaga_rastreio_manual_nem_troca_por_codigo_estranho(db: AsyncSession):
    db.add_all(
        [
            _linha("296762", rastreio="MANUAL123", amazon_canal="proprio"),
            BlingOrder(numero="296762", bling_id=26856107922),
        ]
    )
    await db.commit()
    fake = FakeBling()
    await svc.enrich_pendentes(db, client=fake)
    row = (await db.execute(select(Logistica))).scalars().one()
    # Código dos Correios de verdade substitui o manual.
    assert row.rastreio == "AD912266053BR"
