# ruff: noqa: S105, S106  (tokens de clientes falsos, nada real)
"""As outras compras do comprador na aba Pedido (06/11/2026), pela API.

O caso de produção: o chat da Shopee está no pedido cancelado; a compradora tem outro
em Aguardando Cancelamento no Bling, que não aparecia no painel.

- GET /conversas/{id}/pedidos-comprador: só banco, a lista inteira, a mais nova
  primeiro, sem o pedido da conversa; a linha do Bling, com os SKUs dos itens (a busca da
  tela casa neles: o resumo só tem o nome do anúncio); só no chat de um comprador por
  loja (Shopee e TikTok, sem afiliado, com comprador); a trava da rota do detalhe;
- POST .../retratos: só os pedidos daquele comprador naquela loja; Redis, depois UMA
  ida à loja pelo que falta; leitura desligada, teto, TikTok e erro da loja devolvem o
  que houver, sem erro; o que falhou fica 5 min sem voltar à loja; quem só lê também abre;
- POST .../{pedido}/atualizar: ignora o cache e a marca da falha, com a mesma trava do
  botão de hoje (quem responde, os 409, 60 s, os tetos) e sem prender o botão do pedido
  da conversa;
- nada disso toca na conversa: nem `dados`, nem o pedido, nem os carimbos, nem o índice.

O cliente da loja é falso: nenhuma API de marketplace de verdade.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoPedidoComprador,
    BlingOrder,
    Integration,
    IntegrationPlatform,
    SituacaoBling,
    User,
    UserRole,
)
from app.redis_client import redis
from app.routers import atendimento as rota
from app.security.cipher import encrypt_json
from app.services.atendimento import clientes, enriquecer, gravar
from tests.lojas_da_pessoa import dar_lojas

URL = "/api/atendimento"
COMPRADORA = "778899"
DONO = "dono-compras@davinci-test.com"
AGORA = datetime.now(UTC)
PLATAFORMA = {
    "shopee": IntegrationPlatform.SHOPEE,
    "tiktok": IntegrationPlatform.TIKTOK,
    "ml": IntegrationPlatform.ML,
}


def detalhe_shopee(sn: str, *, status: str = "CANCELLED", **extra) -> dict:
    """O pedido no formato medido do `get_order_detail` (só o que o retrato lê)."""
    return {
        "order_sn": sn,
        "order_status": status,
        "create_time": int(AGORA.timestamp()) - 3600,
        "pay_time": int(AGORA.timestamp()) - 3500,
        "total_amount": 78.65,
        "currency": "BRL",
        "payment_method": "Pix",
        "shipping_carrier": "Shopee Xpress",
        "package_list": [{"logistics_status": "LOGISTICS_NOT_START", "shipping_carrier": "SPX"}],
        "item_list": [
            {
                "item_id": 123,
                "item_name": "Mala de Bordo",
                "image_info": {"image_url": "https://down-br.img.susercontent.com/file/f"},
                "model_name": "Preta",
                "model_sku": "MALA-P",
                "model_quantity_purchased": 1,
                "model_discounted_price": 78.65,
            }
        ],
        **extra,
    }


class ClienteFalso:
    """Só o lote; o rastreio por pedido nem existe aqui (não pode ser chamado)."""

    def __init__(self) -> None:
        self.lotes: list[list[str]] = []
        self.erro: Exception | None = None
        self.pedidos: dict[str, dict] = {}

    async def get_order_detail_lote(self, order_sns: list[str]) -> list[dict]:
        self.lotes.append(list(order_sns))
        if self.erro is not None:
            raise self.erro
        return [self.pedidos[sn] for sn in order_sns if sn in self.pedidos]


@pytest.fixture(autouse=True)
def _fase_de_producao(monkeypatch):
    """Como em produção: a trava da caixa ligada, a leitura das lojas ligada."""
    monkeypatch.setattr(rota, "SO_ADMIN", True)
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_usuarios", DONO)
    monkeypatch.setattr(s, "atendimento_leitura_ativa", True)
    return s


@pytest.fixture
def cliente(monkeypatch) -> ClienteFalso:
    falso = ClienteFalso()

    async def cliente_da_integracao(integration):
        return falso

    monkeypatch.setattr(clientes, "cliente_da_integracao", cliente_da_integracao)
    return falso


@pytest.fixture
async def dono(make_user, auth_as) -> User:
    u = await make_user(email=DONO, role=UserRole.ADMIN)
    auth_as(u)
    return u


async def _loja(db, dono: User, plataforma: str = "shopee", *, status: str = "ok", canal="chat"):
    integ = Integration(
        user_id=dono.id,
        platform=PLATAFORMA[plataforma],
        name=f"loja-{uuid4().hex[:6]}",
        credentials=encrypt_json({"access_token": "t"}),
    )
    db.add(integ)
    await db.flush()
    c = AtendimentoCanal(
        integration_id=integ.id, plataforma=plataforma, canal=canal, modo="observar", status=status
    )
    db.add(c)
    await db.commit()
    return integ, c


async def _chat(db, integ, canal, *, pedido="CHAT1", comprador=COMPRADORA, **campos):
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=integ,
        plataforma=canal.plataforma,
        canal_nome=canal.canal,
        externo_id=f"ext-{uuid4().hex[:8]}",
        comprador_nome="Comprador",
        comprador_id=comprador,
        pedido_marketplace=pedido,
        **campos,
    )
    await db.commit()
    return conversa


async def _compra(
    db, integ, pedido, *, comprador=COMPRADORA, status="CANCELLED", dias=1, total=78.65
):
    db.add(
        AtendimentoPedidoComprador(
            integration_id=integ.id,
            plataforma=integ.platform.value,
            comprador_id=comprador,
            pedido=pedido,
            criado_em=AGORA - timedelta(days=dias) if dias is not None else None,
            total=total,
            status=status,
            itens_resumo="1× Mala de Bordo",
            atualizado_em=AGORA,
        )
    )
    await db.commit()


async def _bling(db, pedido, numero, situacao, *, itens=1, skus=()):
    for i in range(itens):
        db.add(
            BlingOrder(
                numero=numero,
                numeroloja=pedido,
                bling_id=27271814523,
                situacao=situacao,
                data=AGORA,
                item_index=i,
                item_codigo=skus[i] if i < len(skus) else None,
            )
        )
    await db.commit()


async def _situacoes(db):
    existentes = set((await db.execute(select(SituacaoBling.id))).scalars().all())
    for sid, nome in ((12, "Cancelado"), (83955, "Aguardando Cancelamento")):
        if sid not in existentes:
            db.add(SituacaoBling(id=sid, nome=nome))
    await db.commit()


async def _sem_memoria(integ, *pedidos, conversa=None, user=None):
    """Limpa o Redis do cenário (o mesmo Redis entre os testes)."""
    for p in pedidos:
        await redis.delete(enriquecer.chave_retrato_compra(integ.id, p))
        await redis.delete(enriquecer.chave_falha_retrato_compra(integ.id, p))
        if conversa is not None:
            await redis.delete(rota._chave_pedido_da_compra(conversa.id, p))
    await redis.delete(rota._chave_limite_retratos(integ.id))
    await redis.delete(rota._chave_limite_loja(integ.id))
    if user is not None:
        await redis.delete(rota._chave_limite_pessoa(user.id))


# ─────────────── GET: a lista, só do banco ───────────────


async def test_lista_o_caso_de_producao(client, db, dono, cliente):
    """O chat no pedido cancelado; o outro, em Aguardando Cancelamento, aparece."""
    integ, canal = await _loja(db, dono)
    c = await _chat(db, integ, canal, pedido="2610312QN3T9UG")
    await _sem_memoria(integ)
    await _situacoes(db)
    await _compra(db, integ, "2610312QN3T9UG", dias=7)
    await _compra(db, integ, "2610301X726956", status="PROCESSED", dias=8)
    await _compra(db, integ, "261103BVD9K1CB", dias=3)
    await _bling(db, "2610301X726956", "306788", "83955", itens=2)
    await _bling(db, "2610312QN3T9UG", "306797", "12")

    r = await client.get(f"{URL}/conversas/{c.id}/pedidos-comprador")

    assert r.status_code == 200
    corpo = r.json()
    assert [i["pedido"] for i in corpo["itens"]] == ["261103BVD9K1CB", "2610301X726956"]
    novo, ag = corpo["itens"]
    assert novo["status_texto"] == "Cancelado" and novo["bling"] is None
    assert ag["status"] == "PROCESSED" and ag["status_texto"] == "Processado"
    assert ag["total"] == 78.65 and ag["itens_resumo"] == "1× Mala de Bordo"
    assert ag["criado_em"] is not None
    assert ag["bling"] == {
        "numero": "306788",
        "situacao_id": "83955",
        "situacao": "Aguardando Cancelamento",
        "link": "https://www.bling.com.br/vendas.php#edit/27271814523",
        "skus": [],
    }, "as duas linhas de item viram uma"
    assert ag["link_plataforma"] == {
        "url": "https://seller.shopee.com.br/portal/sale/order?search=2610301X726956",
        "rotulo": "Abrir na Shopee",
    }
    assert cliente.lotes == [], "a lista nunca vai à loja"
    # Sem marca de histórico: a de cobertura não existe em nenhuma loja em produção.
    assert set(corpo) == {"itens"}
    await _sem_memoria(integ)


async def test_lista_traz_os_skus_do_bling_para_a_busca_da_tela(client, db, dono, cliente):
    """06/11/2026: o resumo do índice é o nome do anúncio, sem SKU (em produção, nenhum dos
    3.314 pedidos da Shopee com SKU no Bling, em 30 dias). O SKU vem do espelho, uma linha
    por item, na mesma resposta: a tela casa nele sem nova consulta."""
    integ, canal = await _loja(db, dono)
    c = await _chat(db, integ, canal, pedido="CONV1")
    await _sem_memoria(integ, "KIT1", conversa=c, user=dono)
    await _compra(db, integ, "CONV1", dias=1)
    await _compra(db, integ, "KIT1", dias=30)
    await _compra(db, integ, "SEM_BLING", dias=40)
    await _bling(db, "CONV1", "900001", "12", skus=("QWX-00",))
    await _bling(db, "KIT1", "900002", "12", itens=3, skus=("QWX-55", "ZK9-77", "QWX-55"))

    r = await client.get(f"{URL}/conversas/{c.id}/pedidos-comprador")

    kit, sem_bling = r.json()["itens"]
    assert kit["pedido"] == "KIT1" and kit["itens_resumo"] == "1× Mala de Bordo"
    assert kit["bling"]["skus"] == ["QWX-55", "ZK9-77"], "as linhas de item, sem repetir"
    assert sem_bling["bling"] is None
    assert cliente.lotes == [], "a lista nunca vai à loja"

    # O "atualizar" devolve a mesma linha: o cartão não perde o SKU na mão da pessoa.
    cliente.pedidos["KIT1"] = detalhe_shopee("KIT1")
    r = await client.post(f"{URL}/conversas/{c.id}/pedidos-comprador/KIT1/atualizar")
    assert r.json()["item"]["bling"]["skus"] == ["QWX-55", "ZK9-77"]
    await _sem_memoria(integ, "KIT1", conversa=c, user=dono)


async def test_lista_so_deste_comprador_nesta_loja(client, db, dono):
    integ, canal = await _loja(db, dono)
    outra, _ = await _loja(db, dono)
    c = await _chat(db, integ, canal)
    await _compra(db, integ, "MEU1", dias=2)
    await _compra(db, integ, "SEM_DATA", dias=None)
    await _compra(db, integ, "DE_OUTRO", comprador="111", dias=1)
    await _compra(db, outra, "DE_OUTRA_LOJA", dias=1)

    r = await client.get(f"{URL}/conversas/{c.id}/pedidos-comprador")

    assert [i["pedido"] for i in r.json()["itens"]] == ["MEU1", "SEM_DATA"], "sem data, no fim"


async def test_lista_inteira_e_chat_sem_pedido_ligado(client, db, dono):
    """A lista vem toda: quantas aparecem é da tela. Sem pedido ligado, nenhuma fica de fora."""
    integ, canal = await _loja(db, dono)
    c = await _chat(db, integ, canal, pedido=None)
    numeros = [f"P{i:02}" for i in range(1, 17)]
    for dias, sn in enumerate(numeros, start=1):
        await _compra(db, integ, sn, dias=dias)

    r = await client.get(f"{URL}/conversas/{c.id}/pedidos-comprador")

    assert [i["pedido"] for i in r.json()["itens"]] == numeros


@pytest.mark.parametrize(
    ("plataforma", "canal", "campos"),
    [
        ("ml", "chat", {}),
        ("shopee", "email", {}),
        ("shopee", "reclamacao", {}),
        ("shopee", "chat", {"comprador": None}),
        ("shopee", "chat", {"dados": {"afiliado": True}}),
    ],
)
async def test_lista_fora_do_chat_do_comprador_nao_aparece(
    client, db, dono, plataforma, canal, campos
):
    integ, can = await _loja(db, dono, plataforma, canal=canal)
    extras = dict(campos)
    comprador = extras.pop("comprador", COMPRADORA)
    dados = extras.pop("dados", None)
    c = await _chat(db, integ, can, comprador=comprador)
    if dados:
        c.dados = {**(c.dados or {}), **dados}
        await db.commit()
    await _compra(db, integ, "OUTRO1")

    r = await client.get(f"{URL}/conversas/{c.id}/pedidos-comprador")

    assert r.status_code == 200
    assert r.json() == {"itens": []}


async def test_tiktok_lista_e_link_do_tiktok(client, db, dono, cliente):
    integ, canal = await _loja(db, dono, "tiktok")
    c = await _chat(db, integ, canal, pedido="5800")
    await _sem_memoria(integ, "5801", conversa=c)
    await _compra(db, integ, "5800", status="UNPAID", dias=1)
    await _compra(db, integ, "5801", status="AWAITING_SHIPMENT", dias=2)

    r = await client.get(f"{URL}/conversas/{c.id}/pedidos-comprador")

    corpo = r.json()
    (item,) = corpo["itens"]
    assert item["pedido"] == "5801" and item["status_texto"] == "Aguardando envio"
    assert item["link_plataforma"] == {
        "url": "https://seller-br.tiktok.com/order/detail?order_no=5801",
        "rotulo": "Abrir no TikTok",
    }
    # O TikTok não tem retrato pela API: o cartão fica com o índice, sem ir à loja.
    r = await client.post(
        f"{URL}/conversas/{c.id}/pedidos-comprador/retratos", json={"pedidos": ["5801"]}
    )
    assert r.status_code == 200 and r.json() == {"retratos": {}}
    assert cliente.lotes == []
    r = await client.post(f"{URL}/conversas/{c.id}/pedidos-comprador/5801/atualizar")
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "sem_pedido_na_plataforma")


async def test_a_trava_e_a_da_rota_do_detalhe(client, db, dono, make_user, auth_as):
    """As lojas por pessoa: de quem não tem a loja, 404, como o detalhe; o id errado, também."""
    integ, canal = await _loja(db, dono)
    c = await _chat(db, integ, canal)
    await _compra(db, integ, "OUTRO1")
    da_outra_loja, _ = await _loja(db, dono)
    leitor = await make_user()
    await dar_lojas(db, leitor, da_outra_loja)
    auth_as(leitor)

    assert (await client.get(f"{URL}/conversas/{c.id}/pedidos-comprador")).status_code == 404
    assert (await client.get(f"{URL}/conversas/{c.id}")).status_code == 404
    r = await client.post(
        f"{URL}/conversas/{c.id}/pedidos-comprador/retratos", json={"pedidos": []}
    )
    assert r.status_code == 404
    assert (await client.get(f"{URL}/conversas/{uuid4()}/pedidos-comprador")).status_code == 404
    assert (await client.get(f"{URL}/conversas/ig:{uuid4()}/pedidos-comprador")).status_code == 404

    await dar_lojas(db, leitor, integ)
    r = await client.get(f"{URL}/conversas/{c.id}/pedidos-comprador")
    assert [i["pedido"] for i in r.json()["itens"]] == ["OUTRO1"]


# ─────────────── POST retratos: em lote, à parte ───────────────


async def test_retratos_validam_o_comprador_e_a_loja(client, db, dono, cliente):
    integ, canal = await _loja(db, dono)
    outra, _ = await _loja(db, dono)
    c = await _chat(db, integ, canal)
    await _sem_memoria(integ, "MEU1", "MEU2", "DE_OUTRO", "DE_OUTRA_LOJA", "INVENTADO")
    await _compra(db, integ, "MEU1")
    await _compra(db, integ, "MEU2")
    await _compra(db, integ, "DE_OUTRO", comprador="111")
    await _compra(db, outra, "DE_OUTRA_LOJA")
    for sn in ("MEU1", "MEU2", "DE_OUTRO", "DE_OUTRA_LOJA", "INVENTADO"):
        cliente.pedidos[sn] = detalhe_shopee(sn, cancel_reason="Out of Stock")

    r = await client.post(
        f"{URL}/conversas/{c.id}/pedidos-comprador/retratos",
        json={"pedidos": ["MEU1", "DE_OUTRO", "DE_OUTRA_LOJA", "INVENTADO", "MEU2", "MEU1"]},
    )

    assert r.status_code == 200
    assert cliente.lotes == [["MEU1", "MEU2"]], "quem não for fica fora, sem ir à loja por ele"
    retratos = r.json()["retratos"]
    assert set(retratos) == {"MEU1", "MEU2"}
    assert retratos["MEU1"]["status_texto"] == "Cancelado"
    assert retratos["MEU1"]["cancelamento"] == "Out of Stock"
    assert retratos["MEU1"]["pagamento_metodo"]
    assert retratos["MEU1"]["itens"][0]["sku"] == "MALA-P"
    await _sem_memoria(integ, "MEU1", "MEU2")


async def test_retratos_cache_depois_uma_ida_pelo_que_falta(client, db, dono, cliente):
    integ, canal = await _loja(db, dono)
    c = await _chat(db, integ, canal)
    await _sem_memoria(integ, "A", "B", "C")
    for sn in ("A", "B", "C"):
        await _compra(db, integ, sn)
        cliente.pedidos[sn] = detalhe_shopee(sn)
    url = f"{URL}/conversas/{c.id}/pedidos-comprador/retratos"

    assert set((await client.post(url, json={"pedidos": ["A"]})).json()["retratos"]) == {"A"}
    assert cliente.lotes == [["A"]]
    # Reabrir a aba: A vem do Redis; só B e C vão à loja, juntos.
    r = await client.post(url, json={"pedidos": ["A", "B", "C"]})
    assert set(r.json()["retratos"]) == {"A", "B", "C"}
    assert cliente.lotes == [["A"], ["B", "C"]]
    # Reabrir de novo: tudo do Redis, nenhuma ida.
    r = await client.post(url, json={"pedidos": ["A", "B", "C"]})
    assert set(r.json()["retratos"]) == {"A", "B", "C"} and len(cliente.lotes) == 2
    assert await redis.ttl(enriquecer.chave_retrato_compra(integ.id, "A")) > 30 * 60
    await _sem_memoria(integ, "A", "B", "C")


async def test_retratos_nada_disso_toca_na_conversa(client, db, dono, cliente):
    integ, canal = await _loja(db, dono)
    c = await _chat(db, integ, canal, pedido="CHAT1")
    c.dados = {
        **(c.dados or {}),
        "pedido_mkt": {"pedido": "CHAT1", "status": "COMPLETED"},
        "enriquecimento": {"pedido_mkt": {"id": "CHAT1", "em": "2026-11-06T10:00:00+00:00"}},
    }
    await db.commit()
    await _sem_memoria(integ, "OUTRO1", conversa=c, user=dono)
    await _compra(db, integ, "OUTRO1", status="UNPAID")
    cliente.pedidos["OUTRO1"] = detalhe_shopee("OUTRO1", status="COMPLETED")
    await db.refresh(c)
    antes = (copy.deepcopy(c.dados), c.pedido_marketplace, c.etiqueta, c.updated_at)

    r = await client.post(
        f"{URL}/conversas/{c.id}/pedidos-comprador/retratos", json={"pedidos": ["OUTRO1"]}
    )
    assert r.json()["retratos"]["OUTRO1"]["status"] == "COMPLETED"
    r = await client.post(f"{URL}/conversas/{c.id}/pedidos-comprador/OUTRO1/atualizar")
    assert r.json()["atualizado"] is True

    await db.refresh(c)
    assert (c.dados, c.pedido_marketplace, c.etiqueta, c.updated_at) == antes
    # E o índice também não muda aqui: quem escreve nele é o job.
    linha = await db.scalar(
        select(AtendimentoPedidoComprador).where(AtendimentoPedidoComprador.pedido == "OUTRO1")
    )
    await db.refresh(linha)
    assert linha.status == "UNPAID"
    await _sem_memoria(integ, "OUTRO1", conversa=c, user=dono)


async def test_retratos_sem_ir_a_loja_nos_portoes_e_sem_erro(
    client, db, dono, cliente, _fase_de_producao
):
    integ, canal = await _loja(db, dono)
    c = await _chat(db, integ, canal)
    off, off_canal = await _loja(db, dono, status="desligado")
    parada = await _chat(db, off, off_canal)
    await _sem_memoria(integ, "A", "B")
    await _sem_memoria(off, "Z")
    await _compra(db, integ, "A")
    await _compra(db, integ, "B")
    await _compra(db, off, "Z")
    await redis.set(
        enriquecer.chave_retrato_compra(integ.id, "A"),
        '{"pedido": "A", "status": "CANCELLED"}',
        ex=60,
    )
    url = f"{URL}/conversas/{c.id}/pedidos-comprador/retratos"

    # A leitura desligada: fica o que está no Redis.
    _fase_de_producao.atendimento_leitura_ativa = False
    r = await client.post(url, json={"pedidos": ["A", "B"]})
    assert r.status_code == 200 and set(r.json()["retratos"]) == {"A"}
    _fase_de_producao.atendimento_leitura_ativa = True
    # O canal desligado.
    r = await client.post(
        f"{URL}/conversas/{parada.id}/pedidos-comprador/retratos", json={"pedidos": ["Z"]}
    )
    assert r.status_code == 200 and r.json() == {"retratos": {}}
    assert cliente.lotes == []
    # O erro da loja: 200, com o que houver.
    cliente.erro = RuntimeError("shopee_order_detail_lote error_server: fora")
    r = await client.post(url, json={"pedidos": ["A", "B"]})
    assert r.status_code == 200 and set(r.json()["retratos"]) == {"A"}
    assert cliente.lotes == [["B"]]
    await _sem_memoria(integ, "A", "B")


async def test_retratos_tem_teto_proprio_por_loja(client, db, dono, cliente, monkeypatch):
    """Percorrer conversas não vira rajada, nem gasta o teto do "atualizar" do pedido."""
    monkeypatch.setattr(rota, "LIMITE_RETRATOS_COMPRA_POR_LOJA", 2)
    integ, canal = await _loja(db, dono)
    c = await _chat(db, integ, canal)
    numeros = ["A", "B", "C"]
    await _sem_memoria(integ, *numeros, user=dono)
    for sn in numeros:
        await _compra(db, integ, sn)
        cliente.pedidos[sn] = detalhe_shopee(sn)
    url = f"{URL}/conversas/{c.id}/pedidos-comprador/retratos"

    for sn in numeros:
        r = await client.post(url, json={"pedidos": [sn]})
        assert r.status_code == 200

    assert cliente.lotes == [["A"], ["B"]], "o terceiro no minuto nem vai à loja"
    assert await redis.ttl(rota._chave_limite_retratos(integ.id)) > 0
    assert await redis.get(rota._chave_limite_loja(integ.id)) is None
    await _sem_memoria(integ, *numeros, user=dono)


async def test_retratos_sem_redis_nao_vai_a_loja(client, db, dono, cliente, monkeypatch):
    integ, canal = await _loja(db, dono)
    c = await _chat(db, integ, canal)
    await _sem_memoria(integ, "A")
    await _compra(db, integ, "A")
    cliente.pedidos["A"] = detalhe_shopee("A")

    async def fora(*a, **kw):
        raise ConnectionError("redis fora")

    monkeypatch.setattr(rota, "_contar_na_janela", fora)
    r = await client.post(
        f"{URL}/conversas/{c.id}/pedidos-comprador/retratos", json={"pedidos": ["A"]}
    )

    assert r.status_code == 200 and r.json() == {"retratos": {}} and cliente.lotes == []


# A falha de retrato (06/11/2026): reabrir a aba não volta à loja por 5 min.


async def test_retrato_que_a_loja_nao_devolve_ou_com_erro_nao_volta_a_loja(
    client, db, dono, cliente
):
    integ, canal = await _loja(db, dono)
    c = await _chat(db, integ, canal)
    numeros = ["BOM", "SUMIU", "ERRO"]
    await _sem_memoria(integ, *numeros)
    for sn in numeros:
        await _compra(db, integ, sn)
    cliente.pedidos["BOM"] = detalhe_shopee("BOM")
    url = f"{URL}/conversas/{c.id}/pedidos-comprador/retratos"

    async def pedir(*pedidos) -> set[str]:
        r = await client.post(url, json={"pedidos": list(pedidos)})
        assert r.status_code == 200
        return set(r.json()["retratos"])

    # A loja não devolve um: só ele ganha a marca, com a validade curta.
    assert await pedir("BOM", "SUMIU") == {"BOM"}
    assert cliente.lotes == [["BOM", "SUMIU"]]
    assert 0 < await redis.ttl(enriquecer.chave_falha_retrato_compra(integ.id, "SUMIU")) <= 300
    assert await redis.get(enriquecer.chave_falha_retrato_compra(integ.id, "BOM")) is None
    # Reabrir a aba: o bom vem do Redis e o que falhou nem vai.
    assert await pedir("BOM", "SUMIU") == {"BOM"}
    assert len(cliente.lotes) == 1

    # Erro da loja: quem foi no lote ganha a marca; o marcado antes não vai junto.
    cliente.erro = RuntimeError("shopee_order_detail_lote error_server: fora")
    assert await pedir("SUMIU", "ERRO") == set()
    assert cliente.lotes[1:] == [["ERRO"]]
    cliente.erro = None
    cliente.pedidos["ERRO"] = detalhe_shopee("ERRO")
    assert await pedir("ERRO") == set() and len(cliente.lotes) == 2, "dentro dos 5 min, não vai"

    # Passou a validade: vai de novo.
    await redis.delete(enriquecer.chave_falha_retrato_compra(integ.id, "ERRO"))
    assert await pedir("ERRO") == {"ERRO"}
    assert cliente.lotes[2:] == [["ERRO"]]
    await _sem_memoria(integ, *numeros)


async def test_a_marca_da_falha_nao_gasta_o_teto_e_portoes_nao_marcam(
    client, db, dono, cliente, monkeypatch, _fase_de_producao
):
    integ, canal = await _loja(db, dono)
    c = await _chat(db, integ, canal)
    await _sem_memoria(integ, "A", "B")
    await _compra(db, integ, "A")
    await _compra(db, integ, "B")
    url = f"{URL}/conversas/{c.id}/pedidos-comprador/retratos"
    falha = enriquecer.chave_falha_retrato_compra

    # Leitura desligada e teto do minuto não são falha da loja: voltam a tentar ao reabrir.
    _fase_de_producao.atendimento_leitura_ativa = False
    await client.post(url, json={"pedidos": ["A"]})
    _fase_de_producao.atendimento_leitura_ativa = True
    monkeypatch.setattr(rota, "LIMITE_RETRATOS_COMPRA_POR_LOJA", 0)
    await client.post(url, json={"pedidos": ["A"]})
    assert await redis.get(falha(integ.id, "A")) is None and cliente.lotes == []

    # Tudo marcado: nem conta no teto do minuto.
    monkeypatch.setattr(rota, "LIMITE_RETRATOS_COMPRA_POR_LOJA", 20)
    await redis.delete(rota._chave_limite_retratos(integ.id))
    await enriquecer.marcar_falha_de_retrato(integ.id, ["A", "B"])
    r = await client.post(url, json={"pedidos": ["A", "B"]})
    assert r.status_code == 200 and r.json() == {"retratos": {}}
    assert await redis.get(rota._chave_limite_retratos(integ.id)) is None
    await _sem_memoria(integ, "A", "B")


async def test_cliente_da_loja_que_nao_abre_tambem_marca(client, db, dono, monkeypatch):
    """O token que não renova, por exemplo: reabrir a aba não tenta de novo na hora."""
    integ, canal = await _loja(db, dono)
    c = await _chat(db, integ, canal)
    await _sem_memoria(integ, "A", conversa=c, user=dono)
    await _compra(db, integ, "A")
    tentativas: list[str] = []

    async def nao_abre(integration):
        tentativas.append(str(integration.id))
        raise RuntimeError("shopee_token_refresh error_auth: fora")

    monkeypatch.setattr(clientes, "cliente_da_integracao", nao_abre)
    url = f"{URL}/conversas/{c.id}/pedidos-comprador"

    for _ in range(2):
        r = await client.post(f"{url}/retratos", json={"pedidos": ["A"]})
        assert r.status_code == 200 and r.json() == {"retratos": {}}
    assert len(tentativas) == 1

    # O que falha no botão também marca (que ele tenta mesmo com a marca, o teste abaixo vê).
    await redis.delete(enriquecer.chave_falha_retrato_compra(integ.id, "A"))
    r = await client.post(f"{url}/A/atualizar")
    assert (r.json()["atualizado"], r.json()["motivo"]) == (False, "falhou")
    assert len(tentativas) == 2
    assert await redis.get(enriquecer.chave_falha_retrato_compra(integ.id, "A")) is not None
    await _sem_memoria(integ, "A", conversa=c, user=dono)


async def test_o_atualizar_do_cartao_ignora_a_marca_da_falha(client, db, dono, cliente):
    integ, canal = await _loja(db, dono)
    c = await _chat(db, integ, canal)
    await _sem_memoria(integ, "A", conversa=c, user=dono)
    await _compra(db, integ, "A")
    url = f"{URL}/conversas/{c.id}/pedidos-comprador"

    assert (await client.post(f"{url}/retratos", json={"pedidos": ["A"]})).json()["retratos"] == {}
    assert cliente.lotes == [["A"]]
    assert await redis.get(enriquecer.chave_falha_retrato_compra(integ.id, "A")) is not None

    # A loja voltou a devolver: o botão vai à loja, mesmo com a marca.
    cliente.pedidos["A"] = detalhe_shopee("A")
    r = await client.post(f"{url}/A/atualizar")
    assert r.json()["atualizado"] is True and cliente.lotes == [["A"], ["A"]]
    # Reabrir a aba: o retrato novo vale mais que a marca, sem ir à loja.
    r = await client.post(f"{url}/retratos", json={"pedidos": ["A"]})
    assert set(r.json()["retratos"]) == {"A"} and len(cliente.lotes) == 2

    # A falha do botão não apaga o retrato que já havia.
    await redis.delete(rota._chave_pedido_da_compra(c.id, "A"))
    cliente.pedidos.clear()
    r = await client.post(f"{url}/A/atualizar")
    assert r.json()["atualizado"] is False and r.json()["retrato"]["pedido"] == "A"
    r = await client.post(f"{url}/retratos", json={"pedidos": ["A"]})
    assert set(r.json()["retratos"]) == {"A"} and len(cliente.lotes) == 3
    await _sem_memoria(integ, "A", conversa=c, user=dono)


async def test_quem_ve_abre_os_retratos_e_o_atualizar_e_de_quem_responde(
    client, db, dono, make_user, auth_as, cliente, monkeypatch
):
    """Como o "atualizar" de hoje: na fase da caixa, toda pessoa que vê a loja responde;
    com a permissão fina de volta, só ver abre os retratos e o botão recusa."""
    integ, canal = await _loja(db, dono)
    c = await _chat(db, integ, canal)
    await _sem_memoria(integ, "A", conversa=c)
    await _compra(db, integ, "A")
    cliente.pedidos["A"] = detalhe_shopee("A")
    url = f"{URL}/conversas/{c.id}/pedidos-comprador"

    equipe = await make_user()
    await dar_lojas(db, equipe, integ)
    auth_as(equipe)
    assert (await client.get(url)).status_code == 200
    r = await client.post(f"{url}/retratos", json={"pedidos": ["A"]})
    assert r.status_code == 200 and set(r.json()["retratos"]) == {"A"}
    assert (await client.post(f"{url}/A/atualizar")).json()["atualizado"] is True

    monkeypatch.setattr(rota, "SO_ADMIN", False)
    so_ve = await make_user(permissions={"atendimento": {"view": True}})
    await dar_lojas(db, so_ve, integ)
    auth_as(so_ve)
    assert (await client.get(url)).status_code == 200
    assert (await client.post(f"{url}/retratos", json={"pedidos": ["A"]})).status_code == 200
    assert (await client.post(f"{url}/A/atualizar")).status_code == 403
    await _sem_memoria(integ, "A", conversa=c, user=equipe)


# ─────────────── POST atualizar: o botão do cartão ───────────────


async def test_atualizar_ignora_o_cache_e_regrava(client, db, dono, cliente):
    integ, canal = await _loja(db, dono)
    c = await _chat(db, integ, canal)
    await _sem_memoria(integ, "A", conversa=c, user=dono)
    await _situacoes(db)
    await _compra(db, integ, "A", status="PROCESSED")
    await _bling(db, "A", "306788", "83955")
    await redis.set(
        enriquecer.chave_retrato_compra(integ.id, "A"),
        '{"pedido": "A", "status": "PROCESSED"}',
        ex=600,
    )
    cliente.pedidos["A"] = detalhe_shopee("A", status="CANCELLED")

    r = await client.post(f"{URL}/conversas/{c.id}/pedidos-comprador/A/atualizar")

    corpo = r.json()
    assert r.status_code == 200 and cliente.lotes == [["A"]]
    assert corpo["atualizado"] is True and corpo["motivo"] is None
    assert corpo["retrato"]["status"] == "CANCELLED"
    assert corpo["item"]["bling"]["situacao"] == "Aguardando Cancelamento"
    # Regravou o cache, agora com a validade do status final.
    assert await redis.ttl(enriquecer.chave_retrato_compra(integ.id, "A")) > 30 * 60
    await _sem_memoria(integ, "A", conversa=c, user=dono)


async def test_atualizar_a_mesma_trava_do_botao_de_hoje(client, db, dono, cliente, monkeypatch):
    integ, canal = await _loja(db, dono)
    c = await _chat(db, integ, canal)
    numeros = ["A", "B", "C"]
    await _sem_memoria(integ, *numeros, conversa=c, user=dono)
    for sn in numeros:
        await _compra(db, integ, sn)
        cliente.pedidos[sn] = detalhe_shopee(sn)

    # 60 s por cartão: o segundo clique não vai à loja.
    r1 = await client.post(f"{URL}/conversas/{c.id}/pedidos-comprador/A/atualizar")
    assert r1.json()["atualizado"] is True
    r2 = await client.post(f"{URL}/conversas/{c.id}/pedidos-comprador/A/atualizar")
    assert (r2.json()["atualizado"], r2.json()["motivo"]) == (False, "recente")
    assert r2.json()["retrato"]["status"] == "CANCELLED", "o que já havia"
    assert cliente.lotes == [["A"]]
    assert 0 < await redis.ttl(rota._chave_pedido_da_compra(c.id, "A")) <= rota.TRAVA_PEDIDO_S
    # Não prende o botão do pedido da conversa.
    assert await redis.get(rota._chave_pedido(c.id)) is None

    # Os tetos do minuto são os do botão: acima deles, sem ir à loja e a trava sai.
    monkeypatch.setattr(rota, "LIMITE_PEDIDO_POR_LOJA", 1)
    r3 = await client.post(f"{URL}/conversas/{c.id}/pedidos-comprador/B/atualizar")
    assert (r3.status_code, r3.json()["motivo"]) == (200, "limite")
    assert await redis.get(rota._chave_pedido_da_compra(c.id, "B")) is None
    assert cliente.lotes == [["A"]]
    monkeypatch.setattr(rota, "LIMITE_PEDIDO_POR_LOJA", 10)
    await redis.delete(rota._chave_limite_loja(integ.id))

    # Falha da loja: 200, com o `motivo`.
    cliente.erro = RuntimeError("shopee_order_detail_lote error_server: fora")
    r4 = await client.post(f"{URL}/conversas/{c.id}/pedidos-comprador/B/atualizar")
    assert (r4.status_code, r4.json()["atualizado"], r4.json()["motivo"]) == (
        200,
        False,
        "sem_alteracao",
    )
    assert r4.json()["item"]["pedido"] == "B", "a linha do índice e do Bling vem do mesmo jeito"
    await _sem_memoria(integ, *numeros, conversa=c, user=dono)


async def test_atualizar_recusas(client, db, dono, cliente, _fase_de_producao):
    integ, canal = await _loja(db, dono)
    c = await _chat(db, integ, canal, pedido="CHAT1")
    off, off_canal = await _loja(db, dono, status="desligado")
    parada = await _chat(db, off, off_canal)
    await _sem_memoria(integ, "A", conversa=c, user=dono)
    await _compra(db, integ, "A")
    await _compra(db, off, "Z")
    await _compra(db, integ, "DE_OUTRO", comprador="111")

    async def post(conversa, pedido) -> tuple[int, str]:
        r = await client.post(f"{URL}/conversas/{conversa.id}/pedidos-comprador/{pedido}/atualizar")
        return r.status_code, r.json()["detail"]["code"]

    # De outro comprador, de outra loja ou inventado: o número vem da tela.
    assert await post(c, "DE_OUTRO") == (404, "pedido_nao_encontrado")
    assert await post(c, "Z") == (404, "pedido_nao_encontrado")
    assert await post(c, "INVENTADO") == (404, "pedido_nao_encontrado")
    assert await post(parada, "Z") == (409, "canal_desligado")
    _fase_de_producao.atendimento_leitura_ativa = False
    assert await post(c, "A") == (409, "leitura_desligada")
    assert cliente.lotes == []
    assert await redis.get(rota._chave_pedido_da_compra(c.id, "A")) is None, "nada preso"
    await _sem_memoria(integ, "A", conversa=c, user=dono)


async def test_o_atualizar_do_pedido_da_conversa_segue_com_as_mesmas_recusas(client, db, dono):
    """Os portões saíram do corpo da rota para `_loja_que_le_o_pedido`: os mesmos, na ordem."""
    tiktok, tt = await _loja(db, dono, "tiktok")
    c = await _chat(db, tiktok, tt)
    r = await client.post(f"{URL}/conversas/{c.id}/pedido/atualizar")
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "sem_pedido_na_plataforma")
