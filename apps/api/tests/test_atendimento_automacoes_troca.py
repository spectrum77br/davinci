"""O que faltava antes da troca do Duoke pelo DaVinci (05/10/2026).

Eduardo, 05/10/2026: "então pode também fazer ali o que falta antes da troca".
Tudo atrás das chaves que já existem — nada sai com `ATENDIMENTO_AUTOMACOES_ENVIO`
ou `ATENDIMENTO_ENVIO_ATIVO` desligados (o teste de "simular nunca chama a
plataforma" roda de novo, agora com os caminhos novos):

1. ENVIO DA SHOPEE SEM CONVERSA E OS TIPOS QUE FALTAVAM — o cartão do pedido
   (`message_type=order`) antes do texto e a figurinha (`sticker`), no formato
   que o Duoke manda; o texto das campanhas como resposta automática
   (`send_autoreply_message`); sem conversa no DaVinci, pelo `to_id` do
   comprador do pedido (do índice — nenhuma chamada nova), com a conversa que a
   Shopee devolve gravada. O que sai volta na leitura como NOSSO (`davinci_auto`
   com a marca): pelo id, ou adotado pelo texto (o `auto_reply` volta como
   `sistema`) e pelo pedido/figurinha (cartão e figurinha não têm texto) — a
   régua o reconhece e o comparador não o confunde com o Duoke.
2. A RECONFERÊNCIA logo antes de enviar: devolução/reclamação (aberta ou
   encerrada), Bling no fluxo de devolução, cancelamento, pessoa que respondeu,
   o Duoke que mandou e o teto, relidos do banco — mudou, `pulado` com o motivo.
3. OS PEDIDOS FORA DA LOGÍSTICA (Resolvido/Cancelado/Perdimento no Bling): o
   entregue e o concluído pelo índice do cartão "Cliente".
4. A JUNÇÃO com as correções do motor (15f48c90): o horário da regra e o corte
   do modo seco valem também na reconferência, e o lote decide com o corte.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest
import respx
from sqlalchemy import func, select, text

from app.config import get_settings
from app.models import (
    AtendimentoAutomacaoRegistro,
    AtendimentoAutomacaoRegra,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoPedidoComprador,
    AtendimentoReclamacao,
    BlingOrder,
    IntegrationPlatform,
    Logistica,
    MarketplaceOrderFinancial,
    Store,
)
from app.routers import atendimento as rota
from app.services.atendimento import automacoes, automacoes_comparar, clientes, enviar, gravar
from app.services.atendimento import automacoes_catalogo as cat
from app.services.atendimento import shopee as shopee_atd
from app.services.atendimento.constantes import ResultadoEnvio, e_mensagem_automatica
from app.services.marketplaces.shopee import ShopeeClient
from tests.test_atendimento_automacoes_motor import (
    DESDE,
    MENU,
    SN,
    RedisFalso,
    T,
    _conversa,
    _dono,
    _linhas,
    _loja,
    _msg,
    _n_mensagens,
    _regra,
    _rodada,
)

COMPRADOR = "555"


# ── chaves e falsos ───────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _redis(monkeypatch) -> RedisFalso:
    r = RedisFalso()
    monkeypatch.setattr(automacoes, "redis", r)
    return r


@pytest.fixture(autouse=True)
def _chaves(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_leitura_ativa", True)
    monkeypatch.setattr(s, "atendimento_automacoes_ativa", True)
    monkeypatch.setattr(s, "atendimento_automacoes_envio", False)
    monkeypatch.setattr(s, "atendimento_envio_ativo", False)
    monkeypatch.setattr(s, "atendimento_automacoes_shopee_auto_reply", False)
    monkeypatch.setattr(s, "atendimento_automacoes_teto_dia", 400)
    monkeypatch.setattr(s, "atendimento_simulador", False)
    monkeypatch.setattr(s, "shopee_mensagens_comprador", True)
    monkeypatch.setattr(rota, "SO_ADMIN", False)
    return s


@pytest.fixture(autouse=True)
async def _sem_financeiro(db):
    """O escrow do financeiro não está na limpeza do conftest: cada teste limpa o seu."""
    await db.execute(text("DELETE FROM marketplace_order_financials"))
    await db.commit()
    yield
    await db.execute(text("DELETE FROM marketplace_order_financials"))
    await db.commit()


@pytest.fixture
def proibido(monkeypatch) -> list[str]:
    """Tudo o que sai para a plataforma LEVANTA — os caminhos novos inclusive."""
    chamadas: list[str] = []

    def _boom(nome):
        def f(*_a, **_k):
            chamadas.append(nome)
            raise AssertionError(f"{nome} chamado no modo seco")

        return f

    def _aboom(nome):
        async def f(*_a, **_k):
            chamadas.append(nome)
            raise AssertionError(f"{nome} chamado no modo seco")

        return f

    monkeypatch.setattr(enviar, "adaptador", _boom("adaptador"))
    monkeypatch.setattr(enviar, "enviar_automatica", _aboom("enviar_automatica"))
    monkeypatch.setattr(
        enviar, "enviar_automatica_sem_conversa", _aboom("enviar_automatica_sem_conversa")
    )
    monkeypatch.setattr(enviar, "enviar_resposta", _aboom("enviar_resposta"))
    monkeypatch.setattr(shopee_atd, "enviar_parte", _aboom("shopee.enviar_parte"))
    monkeypatch.setattr(shopee_atd, "enviar_texto", _aboom("shopee.enviar_texto"))
    monkeypatch.setattr(ShopeeClient, "chat_send_message", _aboom("chat_send_message"))
    monkeypatch.setattr(
        ShopeeClient, "chat_send_autoreply_message", _aboom("chat_send_autoreply_message")
    )
    monkeypatch.setattr(clientes, "cliente_da_integracao", _aboom("cliente_da_integracao"))
    monkeypatch.setattr(httpx.AsyncClient, "send", _aboom("httpx"))
    return chamadas


class ShopeeFalsa:
    """O adaptador da Shopee falso: anota cada parte e responde como a Shopee.

    `com_ids=False`: a resposta sem `message_id` (a leitura adota depois).
    """

    def __init__(self, *, com_ids: bool = True) -> None:
        self.envios: list[dict] = []
        self.com_ids = com_ids
        self.conversation_id = "9000001"
        self._seq = iter(range(1, 1000))
        # Recusa por tipo de parte ("cartao_pedido", "texto", "figurinha") ou
        # "auto_reply" (o texto da campanha): a Shopee responde este resultado.
        self.falhas: dict[str, ResultadoEnvio] = {}
        # Chamado antes de responder (o que muda no meio do envio).
        self.ao_enviar = None

    def _resposta(self) -> ResultadoEnvio:
        mid = f"23000000{next(self._seq):04d}"
        payload = {"conversation_id": self.conversation_id}
        if self.com_ids:
            payload["message_id"] = mid
        return ResultadoEnvio(ok=True, externo_id=mid if self.com_ids else None, payload=payload)

    async def enviar_texto(self, session, conversa, integration, cliente, texto):
        self.envios.append({"tipo": "texto", "texto": texto, "via": "enviar_texto"})
        return self._resposta()

    async def enviar_parte(
        self,
        session,
        conversa,
        integration,
        cliente,
        parte,
        *,
        to_id=None,
        pedido=None,
        auto_reply=False,
    ):
        chave = "auto_reply" if auto_reply else parte["tipo"]
        falha = self.falhas.get(chave)
        self.envios.append(
            {
                "tipo": parte["tipo"],
                "to_id": to_id or (conversa.comprador_id if conversa is not None else None),
                "sem_conversa": conversa is None,
                "pedido": pedido,
                "auto_reply": auto_reply,
                "texto": parte.get("texto"),
                "figurinha": parte.get("figurinha"),
                "pacote": parte.get("pacote"),
                "ok": falha is None or falha.ok,
            }
        )
        if self.ao_enviar is not None:
            self.ao_enviar(parte, auto_reply)
        return falha if falha is not None else self._resposta()


@pytest.fixture
def envio_ligado(monkeypatch, _chaves) -> ShopeeFalsa:
    """As duas chaves e o `auto_reply` confirmado: as campanhas da Shopee saem."""
    _chaves.atendimento_envio_ativo = True
    _chaves.atendimento_automacoes_envio = True
    _chaves.atendimento_automacoes_shopee_auto_reply = True
    falsa = ShopeeFalsa()
    monkeypatch.setattr(enviar, "adaptador", lambda _plataforma: falsa)

    async def _cliente(_integ):
        return object()

    monkeypatch.setattr(clientes, "cliente_da_integracao", _cliente)
    return falsa


# ── fábrica ───────────────────────────────────────────────────────────────


async def _store_id(db, integ):
    return (await db.execute(select(Store.id).where(Store.integration_id == integ.id))).scalar_one()


async def _pedido_bling(db, integ, sn=SN, *, situacao="6", criado=T - timedelta(minutes=8)):
    db.add(
        BlingOrder(
            numero=f"9{sn[-4:]}",
            numeroloja=sn,
            situacao=situacao,
            store_id=await _store_id(db, integ),
            item_index=0,
            data=criado,
            created_at=criado,
        )
    )
    await db.commit()


async def _indice(db, integ, sn=SN, *, status="READY_TO_SHIP", atualizado=None, criado=None):
    db.add(
        AtendimentoPedidoComprador(
            integration_id=integ.id,
            plataforma="shopee",
            comprador_id=COMPRADOR,
            pedido=sn,
            criado_em=criado or T - timedelta(days=3),
            status=status,
            atualizado_em=atualizado or T - timedelta(minutes=30),
        )
    )
    await db.commit()


async def _escrow(db, integ, sn=SN, nome="maria.silva"):
    db.add(
        MarketplaceOrderFinancial(
            platform=IntegrationPlatform.SHOPEE,
            integration_id=integ.id,
            external_order_id=sn,
            status="posted",
            raw={"escrow": {"order_sn": sn, "buyer_user_name": nome}},
        )
    )
    await db.commit()


def _logistica(sn, status, em, *, conta="barbosa", lido=T - timedelta(minutes=20), retorno=None):
    meli = {"order_status": status}
    if retorno:
        meli["return_status"] = retorno
    return Logistica(
        pedido_marketplace=sn,
        plataforma="shopee",
        conta=conta,
        data=em.date(),
        meli_status=meli,
        status_datas={"order_status": {"em": em.isoformat()}},
        status_lido_em=lido,
    )


async def _nossas(db) -> list[AtendimentoMensagem]:
    return list(
        (
            await db.execute(
                select(AtendimentoMensagem)
                .where(AtendimentoMensagem.origem == "davinci_auto")
                .order_by(AtendimentoMensagem.created_at)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )


async def _n_conversas(db) -> int:
    return int(await db.scalar(select(func.count()).select_from(AtendimentoConversa)))


# ── 1. O cliente e o adaptador da Shopee ──────────────────────────────────


def _cliente_shopee() -> ShopeeClient:
    import time

    return ShopeeClient(
        {
            "shop_id": 99,
            "access_token": "t",
            "refresh_token": "r",
            "expires_at": int(time.time()) + 3600,
        }
    )


@pytest.mark.parametrize(
    ("chamada", "caminho", "corpo"),
    [
        (
            lambda c: c.chat_send_message(77, order_sn=SN),
            "/api/v2/sellerchat/send_message",
            {"to_id": 77, "message_type": "order", "content": {"order_sn": SN}},
        ),
        (
            lambda c: c.chat_send_message(77, sticker_id="0007", sticker_package_id="br_shoppito"),
            "/api/v2/sellerchat/send_message",
            {
                "to_id": 77,
                "message_type": "sticker",
                "content": {"sticker_id": "0007", "sticker_package_id": "br_shoppito"},
            },
        ),
        (
            lambda c: c.chat_send_message(77, text="Oi"),
            "/api/v2/sellerchat/send_message",
            {"to_id": 77, "message_type": "text", "content": {"text": "Oi"}},
        ),
        (
            lambda c: c.chat_send_autoreply_message(77, text="Oi"),
            "/api/v2/sellerchat/send_autoreply_message",
            {"to_id": 77, "message_type": "text", "content": {"text": "Oi"}},
        ),
    ],
    ids=["cartao", "figurinha", "texto", "auto_reply"],
)
async def test_cliente_manda_o_formato_que_o_duoke_manda(chamada, caminho, corpo):
    import json

    c = _cliente_shopee()
    with respx.mock(base_url=c._base, assert_all_called=True) as router:
        rota_ = router.post(caminho).mock(
            return_value=httpx.Response(
                200, json={"response": {"message_id": "1", "conversation_id": "2"}}
            )
        )
        resp = await chamada(c)
    assert resp == {"message_id": "1", "conversation_id": "2"}
    assert json.loads(rota_.calls.last.request.content) == corpo


async def test_cliente_recusa_duas_partes_ou_figurinha_sem_pacote():
    c = _cliente_shopee()
    with respx.mock(base_url=c._base, assert_all_called=False) as router:
        with pytest.raises(ValueError):
            await c.chat_send_message(77, text="a", order_sn=SN)
        with pytest.raises(ValueError):
            await c.chat_send_message(77, sticker_id="0007")
        with pytest.raises(ValueError):
            await c.chat_send_autoreply_message(77, text="")
        assert len(router.calls) == 0


class ClienteFalso:
    def __init__(self, resposta: Any = None) -> None:
        self.chamadas: list[tuple[str, tuple, dict]] = []
        self.resposta = (
            resposta
            if resposta is not None
            else {
                "message_id": "88",
                "conversation_id": "99",
            }
        )

    async def _responder(self):
        if isinstance(self.resposta, BaseException):
            raise self.resposta
        return self.resposta

    async def chat_send_message(self, to_id, **k):
        self.chamadas.append(("send_message", (str(to_id),), k))
        return await self._responder()

    async def chat_send_autoreply_message(self, to_id, **k):
        self.chamadas.append(("send_autoreply_message", (str(to_id),), k))
        return await self._responder()


async def test_adaptador_manda_cada_parte_pelo_to_id():
    cli = ClienteFalso()
    r = await shopee_atd.enviar_parte(
        None, None, None, cli, {"tipo": "cartao_pedido"}, to_id=COMPRADOR, pedido=SN
    )
    assert r.ok and r.externo_id == "88" and r.payload["conversation_id"] == "99"
    await shopee_atd.enviar_parte(
        None, None, None, cli, {"tipo": "texto", "texto": "Oi!"}, to_id=COMPRADOR, auto_reply=True
    )
    await shopee_atd.enviar_parte(
        None, None, None, cli, {"tipo": "texto", "texto": "Oi!"}, to_id=COMPRADOR
    )
    await shopee_atd.enviar_parte(None, None, None, cli, dict(cat.PARTE_FIGURINHA), to_id=COMPRADOR)
    assert cli.chamadas == [
        ("send_message", (COMPRADOR,), {"order_sn": SN}),
        ("send_autoreply_message", (COMPRADOR,), {"text": "Oi!"}),
        ("send_message", (COMPRADOR,), {"text": "Oi!"}),
        ("send_message", (COMPRADOR,), {"sticker_id": "0007", "sticker_package_id": "br_shoppito"}),
    ]


@pytest.mark.parametrize(
    ("parte", "kw", "erro"),
    [
        ({"tipo": "texto", "texto": "Oi"}, {}, "shopee sem_comprador"),
        ({"tipo": "cartao_pedido"}, {"to_id": COMPRADOR}, "shopee cartao_sem_pedido"),
        (
            {"tipo": "figurinha", "figurinha": "0007"},
            {"to_id": COMPRADOR},
            "shopee figurinha_invalida",
        ),
        ({"tipo": "video"}, {"to_id": COMPRADOR}, "shopee parte_desconhecida"),
    ],
)
async def test_adaptador_parte_invalida_nem_chama_a_shopee(parte, kw, erro):
    cli = ClienteFalso()
    r = await shopee_atd.enviar_parte(None, None, None, cli, parte, **kw)
    assert (r.ok, r.erro) == (False, erro) and cli.chamadas == []


@pytest.mark.parametrize(
    ("falha", "ambiguo"),
    [
        (httpx.ReadTimeout("t"), True),
        (RuntimeError("shopee_chat_autoreply error_permission: no permission"), False),
        (RuntimeError("shopee_chat_autoreply error_server: busy"), True),
    ],
)
async def test_adaptador_resposta_automatica_com_erro_nunca_levanta(falha, ambiguo):
    r = await shopee_atd.enviar_parte(
        None,
        None,
        None,
        ClienteFalso(falha),
        {"tipo": "texto", "texto": "Oi"},
        to_id=COMPRADOR,
        auto_reply=True,
    )
    assert r.ok is False and r.ambiguo is ambiguo


# ── 1. O motor manda as partes, com e sem conversa ────────────────────────


async def _pedido_recebido_sem_conversa(db, *, indice=True, modo="enviar"):
    dono = await _dono(db)
    integ, canal = await _loja(db, dono)
    regra = await _regra(db, integ, "shopee_pedido_recebido", modo=modo, enviar_desde=DESDE)
    await _pedido_bling(db, integ)
    if indice:
        await _indice(db, integ)
    return integ, canal, regra


async def test_pedido_recebido_sem_conversa_sai_pelo_to_id_cartao_antes_do_texto(db, envio_ligado):
    integ, canal, _ = await _pedido_recebido_sem_conversa(db)
    await _escrow(db, integ)
    assert await _n_conversas(db) == 0
    await _rodada()
    [linha] = await _linhas(db, automacao="shopee_pedido_recebido")
    assert (linha.estado, linha.modo, linha.motivo) == ("enviado", "enviar", None)
    assert [(e["tipo"], e["auto_reply"], e["sem_conversa"]) for e in envio_ligado.envios] == [
        ("cartao_pedido", False, True),  # o cartão abre a conversa, como o Duoke (normal)
        ("texto", True, False),  # o texto da campanha como resposta automática
    ]
    assert {e["to_id"] for e in envio_ligado.envios} == {COMPRADOR}
    assert envio_ligado.envios[0]["pedido"] == SN
    # A conversa que a Shopee devolveu nasceu com o comprador e o pedido.
    [conversa] = (await db.execute(select(AtendimentoConversa))).scalars().all()
    assert (conversa.externo_id, conversa.comprador_id, conversa.pedido_marketplace) == (
        "9000001",
        COMPRADOR,
        SN,
    )
    assert conversa.comprador_nome == "maria.silva"  # o usuário do escrow
    assert conversa.canal_id == canal.id and linha.conversa_id == conversa.id
    cartao, texto_ = await _nossas(db)
    assert linha.mensagem_ids == [str(cartao.id), str(texto_.id)]
    assert cartao.payload["automacao"]["parte"] == "cartao_pedido"
    assert cartao.payload["automacao"]["pedido"] == SN
    assert cartao.tipo == "pedido" and cartao.status == "enviada" and cartao.externo_id
    assert texto_.payload["automacao"]["codigo"] == "shopee_pedido_recebido"
    assert texto_.texto == cat.TEXTO_PEDIDO_RECEBIDO and texto_.status == "enviada"
    # A régua: as duas são automáticas (não fecham a vez, a IA não aprende).
    assert all(e_mensagem_automatica(m.texto, m.payload) for m in (cartao, texto_))
    # A leitura seguinte traz as duas pelo id: não duplica.
    for m in (cartao, texto_):
        de_volta, criada = await gravar.gravar_mensagem(
            db,
            conversa,
            externo_id=m.externo_id,
            autor="loja",
            texto=m.texto,
            enviada_em=datetime.now(UTC),
            payload={"source": "openapi"},
        )
        assert de_volta.id == m.id and criada is False
    await db.commit()
    assert len(await _nossas(db)) == 2
    # Rodar de novo nunca manda outra vez.
    await _rodada(T + timedelta(minutes=2))
    assert len(envio_ligado.envios) == 2


async def test_a_leitura_adota_o_que_saiu_sem_id_auto_reply_e_cartao(db, envio_ligado):
    envio_ligado.com_ids = False
    integ, _canal, regra = await _pedido_recebido_sem_conversa(db)
    await _rodada()
    [linha] = await _linhas(db, automacao="shopee_pedido_recebido")
    assert linha.estado == "enviado"
    conversa = await db.get(AtendimentoConversa, linha.conversa_id)
    agora = datetime.now(UTC)
    # O cartão volta como mensagem da loja (sem texto): adota pelo pedido.
    cartao, criada = await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id="7001",
        autor="loja",
        texto=None,
        enviada_em=agora,
        tipo="pedido",
        payload={"source": "openapi", "message_type": "order", "content": {"order_sn": SN}},
    )
    assert criada is False and cartao.origem == "davinci_auto" and cartao.externo_id == "7001"
    # O texto da campanha volta como `auto_reply` = `sistema`: adota pelo texto.
    texto_, criada = await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id="7002",
        autor="sistema",
        texto=cat.TEXTO_PEDIDO_RECEBIDO,
        enviada_em=agora,
        payload={"source": "openapi", "status": "auto_reply", "message_type": "text"},
    )
    assert criada is False and texto_.origem == "davinci_auto" and texto_.externo_id == "7002"
    await db.commit()
    assert {m.externo_id for m in await _nossas(db)} == {"7001", "7002"}
    assert await _n_mensagens(db) == 2
    # O comparador não confunde o nosso com o Duoke: nada de "só Duoke" nem disjuntor.
    await automacoes_comparar.comparar(
        db, agora=T + timedelta(hours=3), motor_desde=T - timedelta(hours=10)
    )
    await db.commit()
    assert await _linhas(db, estado="so_duoke") == []
    [linha] = await _linhas(db, automacao="shopee_pedido_recebido")
    assert linha.duoke != "mandou"
    await db.refresh(regra)
    assert regra.modo == "enviar" and regra.disjuntor_em is None


async def test_auto_reply_de_volta_nunca_adota_a_resposta_de_uma_pessoa(db):
    dono = await _dono(db)
    integ, canal = await _loja(db, dono)
    conversa = await _conversa(db, integ, canal)
    agora = datetime.now(UTC)
    pessoa = AtendimentoMensagem(
        conversa_id=conversa.id,
        autor="loja",
        origem="davinci_humano",
        tipo="texto",
        texto="Já te respondo",
        enviada_em=agora,
        status="enviada",
        payload={},
    )
    db.add(pessoa)
    await db.commit()
    voltou, criada = await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id="8001",
        autor="sistema",
        texto="Já te respondo",
        enviada_em=agora,
        payload={"status": "auto_reply"},
    )
    assert criada is True and voltou.origem == "sistema" and voltou.id != pessoa.id
    # E o cartão do Duoke (sem marca nossa) continua `externo`.
    duoke, criada = await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id="8002",
        autor="loja",
        texto=None,
        enviada_em=agora,
        tipo="pedido",
        payload={"source": "openapi", "message_type": "order", "content": {"order_sn": SN}},
    )
    assert criada is True and duoke.origem == "externo"


async def test_sem_comprador_no_indice_espera_e_depois_pula_sem_chamar(db, envio_ligado):
    await _pedido_recebido_sem_conversa(db, indice=False)
    await _rodada()
    [linha] = await _linhas(db, automacao="shopee_pedido_recebido")
    assert linha.estado == "agendado"  # o índice (de hora em hora) ainda não trouxe
    await _rodada(T + timedelta(minutes=60))
    [linha] = await _linhas(db, automacao="shopee_pedido_recebido")
    assert linha.estado == "agendado"
    await _rodada(T + timedelta(minutes=90))
    [linha] = await _linhas(db, automacao="shopee_pedido_recebido")
    assert (linha.estado, linha.motivo) == ("pulado", "sem_comprador")
    assert envio_ligado.envios == [] and await _n_conversas(db) == 0


async def test_comprador_que_chega_ao_indice_destrava_o_envio(db, envio_ligado):
    integ, _, _ = await _pedido_recebido_sem_conversa(db, indice=False)
    await _rodada()
    await _indice(db, integ)
    await _rodada(T + timedelta(minutes=40))
    [linha] = await _linhas(db, automacao="shopee_pedido_recebido")
    assert linha.estado == "enviado" and linha.comprador_id == COMPRADOR
    assert [e["tipo"] for e in envio_ligado.envios] == ["cartao_pedido", "texto"]


async def test_conversa_de_quem_escreveu_antes_achada_pelo_usuario_do_escrow(db, envio_ligado):
    dono = await _dono(db)
    integ, canal = await _loja(db, dono)
    await _regra(db, integ, "shopee_pedido_recebido", modo="enviar", enviar_desde=DESDE)
    conversa = await _conversa(db, integ, canal, comprador="321", nome="joao.pereira")
    await _pedido_bling(db, integ)
    await _escrow(db, integ, nome="joao.pereira")
    await _rodada()
    [linha] = await _linhas(db, automacao="shopee_pedido_recebido")
    assert linha.estado == "enviado" and linha.conversa_id == conversa.id
    # Pela conversa que existe: sem `to_id` à parte, sem conversa nova.
    assert [(e["tipo"], e["sem_conversa"], e["to_id"]) for e in envio_ligado.envios] == [
        ("cartao_pedido", False, "321"),
        ("texto", False, "321"),
    ]
    assert await _n_conversas(db) == 1


async def test_duvida_26h_manda_o_texto_e_a_figurinha(db, envio_ligado):
    dono = await _dono(db)
    integ, canal = await _loja(db, dono)
    regra = await _regra(db, integ, "shopee_duvida_26h", modo="enviar", enviar_desde=DESDE)
    conversa = await _conversa(db, integ, canal)
    gatilho = await _msg(db, conversa, em=T - timedelta(hours=26, minutes=5))
    db.add(
        AtendimentoAutomacaoRegistro(
            automacao="shopee_duvida_26h",
            regra_id=regra.id,
            regra_versao=1,
            integration_id=integ.id,
            plataforma="shopee",
            alvo="conversa",
            chave=f"conversa:{conversa.id}:msg:{gatilho.id}",
            conversa_id=conversa.id,
            gatilho_mensagem_id=gatilho.id,
            comprador_id=conversa.comprador_id,
            evento_em=gatilho.enviada_em,
            visto_em=T - timedelta(hours=26),
            devido_em=T - timedelta(minutes=10),
            estado="agendado",
            duoke="pendente",
        )
    )
    await db.commit()
    await _rodada()
    [linha] = await _linhas(db, automacao="shopee_duvida_26h")
    assert linha.estado == "enviado"
    assert [(e["tipo"], e["auto_reply"]) for e in envio_ligado.envios] == [
        ("texto", True),
        ("figurinha", False),
    ]
    assert (envio_ligado.envios[1]["figurinha"], envio_ligado.envios[1]["pacote"]) == (
        "0007",
        "br_shoppito",
    )
    texto_, figurinha = await _nossas(db)
    assert figurinha.payload["automacao"]["parte"] == "figurinha"
    assert figurinha.payload["automacao"]["figurinha"] == "0007"
    # A figurinha volta da Shopee: adotada pela figurinha da marca.
    figurinha.externo_id = None
    await db.commit()
    voltou, criada = await gravar.gravar_mensagem(
        db,
        await db.get(AtendimentoConversa, conversa.id),
        externo_id="9101",
        autor="loja",
        texto="[Figurinha]",
        enviada_em=datetime.now(UTC),
        tipo="outro",
        payload={
            "source": "openapi",
            "message_type": "sticker",
            "content": {"sticker_id": "0007", "sticker_package_id": "br_shoppito"},
        },
    )
    assert criada is False and voltou.id == figurinha.id


async def test_entregue_com_conversa_manda_cartao_e_texto_com_o_nome(db, envio_ligado):
    dono = await _dono(db)
    integ, canal = await _loja(db, dono)
    await _regra(db, integ, "shopee_entregue", modo="enviar", enviar_desde=DESDE)
    await _conversa(db, integ, canal, pedido=SN)
    db.add(_logistica(SN, "TO_CONFIRM_RECEIVE", T - timedelta(hours=1)))
    await db.commit()
    await _rodada()
    [linha] = await _linhas(db, automacao="shopee_entregue")
    assert linha.estado == "enviado"
    assert [e["tipo"] for e in envio_ligado.envios] == ["cartao_pedido", "texto"]
    assert envio_ligado.envios[1]["texto"].startswith("Oi, maria.silva! Tudo bem?")


async def test_sem_auto_reply_a_campanha_sem_conversa_nao_sai(db, envio_ligado, _chaves):
    _chaves.atendimento_automacoes_shopee_auto_reply = False
    await _pedido_recebido_sem_conversa(db)
    await _rodada()
    [linha] = await _linhas(db, automacao="shopee_pedido_recebido")
    assert (linha.estado, linha.motivo) == ("pulado", "campanha_sem_auto_reply")
    assert envio_ligado.envios == [] and await _n_conversas(db) == 0


async def test_envio_sem_conversa_recusa_sem_as_chaves_e_nao_cria_conversa(
    db, envio_ligado, _chaves
):
    integ, _, regra = await _pedido_recebido_sem_conversa(db)
    _chaves.atendimento_envio_ativo = False
    with pytest.raises(enviar.EnvioRecusado) as e:
        await enviar.enviar_automatica_sem_conversa(
            db,
            integration_id=integ.id,
            codigo="shopee_pedido_recebido",
            to_id=COMPRADOR,
            pedido=SN,
            parte={"tipo": "cartao_pedido"},
            registro_id=None,
            regra_versao=1,
        )
    assert e.value.code == "envio_desligado"
    _chaves.atendimento_envio_ativo = True
    _chaves.atendimento_automacoes_envio = False
    with pytest.raises(enviar.EnvioRecusado) as e:
        await enviar.enviar_automatica_sem_conversa(
            db,
            integration_id=integ.id,
            codigo="shopee_pedido_recebido",
            to_id=COMPRADOR,
            pedido=SN,
            parte={"tipo": "cartao_pedido"},
            registro_id=None,
            regra_versao=1,
        )
    assert e.value.code == "automacoes_envio_desligado"
    _chaves.atendimento_automacoes_envio = True
    regra.modo = "simular"
    await db.commit()
    with pytest.raises(enviar.EnvioRecusado) as e:
        await enviar.enviar_automatica_sem_conversa(
            db,
            integration_id=integ.id,
            codigo="shopee_pedido_recebido",
            to_id=COMPRADOR,
            pedido=SN,
            parte={"tipo": "cartao_pedido"},
            registro_id=None,
            regra_versao=1,
        )
    assert e.value.code == "regra_nao_envia"
    assert envio_ligado.envios == [] and await _n_conversas(db) == 0


async def test_envio_sem_conversa_que_a_leitura_trouxe_antes_e_adotado(db, envio_ligado):
    integ, canal, _ = await _pedido_recebido_sem_conversa(db)
    # A leitura achou a conversa (o mesmo `conversation_id`) e a mensagem antes de nós.
    conversa = await _conversa(db, integ, canal, comprador=COMPRADOR, pedido=None)
    conversa.externo_id = envio_ligado.conversation_id
    await db.commit()
    envio_ligado._seq = iter([77])
    db.add(
        AtendimentoMensagem(
            conversa_id=conversa.id,
            externo_id="230000000077",
            autor="loja",
            origem="externo",
            tipo="pedido",
            texto=None,
            enviada_em=datetime.now(UTC),
            status="enviada",
            payload={"source": "openapi", "message_type": "order", "content": {"order_sn": SN}},
        )
    )
    await db.commit()
    saiu = await enviar.enviar_automatica_sem_conversa(
        db,
        integration_id=integ.id,
        codigo="shopee_pedido_recebido",
        to_id=COMPRADOR,
        pedido=SN,
        parte={"tipo": "cartao_pedido"},
        registro_id=None,
        regra_versao=1,
    )
    assert saiu.status == "enviada" and saiu.conversa_id == conversa.id
    [nossa] = await _nossas(db)
    assert nossa.externo_id == "230000000077" and nossa.payload["automacao"]["pedido"] == SN
    assert await _n_mensagens(db) == 1


# ── 2. A reconferência na hora de enviar ──────────────────────────────────


async def _entregue_pronto(db) -> tuple[Any, Any, Any, AtendimentoAutomacaoRegistro, Any]:
    """Um entregue em `enviar` decidido no lote como "manda" — antes da mudança."""
    dono = await _dono(db)
    integ, canal = await _loja(db, dono)
    regra = await _regra(
        db, integ, "shopee_entregue", modo="enviar", enviar_desde=T - timedelta(hours=2)
    )
    conversa = await _conversa(db, integ, canal, pedido=SN)
    db.add(_logistica(SN, "TO_CONFIRM_RECEIVE", T - timedelta(hours=1)))
    await db.commit()
    await automacoes.descobrir_logistica(
        db, await automacoes.regras_por_chave(db), agora=T, motor_desde=DESDE
    )
    await db.commit()
    [linha] = (
        (await db.execute(select(AtendimentoAutomacaoRegistro).with_for_update())).scalars().all()
    )
    ctx = await automacoes._contexto(db, [linha], agora=T)
    aut = cat.CATALOGO["shopee_entregue"]
    # O lote diria "manda": é a reconferência que vai pegar a mudança.
    assert automacoes._avaliar(ctx, aut, ctx.regras[(aut.codigo, integ.id)], linha).acao == "enviar"
    return integ, conversa, regra, linha, ctx


async def _devolucao_encerrada(db, integ, conversa):
    db.add(
        AtendimentoReclamacao(
            integration_id=integ.id,
            plataforma="shopee",
            externo_id=f"r-{uuid4().hex[:6]}",
            tipo="devolucao",
            status="CLOSED",
            pedido_marketplace=SN,
            encerrada_em=T - timedelta(minutes=5),
        )
    )


async def _cancelado_no_bling(db, integ, conversa):
    db.add(
        BlingOrder(
            numero="9cancel",
            numeroloja=SN,
            situacao="12",
            store_id=await _store_id(db, integ),
            item_index=0,
            data=T,
            created_at=T,
        )
    )


async def _resolvido_no_bling(db, integ, conversa):
    db.add(
        BlingOrder(
            numero="9resolv",
            numeroloja=SN,
            situacao="545902",
            store_id=await _store_id(db, integ),
            item_index=0,
            data=T,
            created_at=T,
        )
    )


async def _duoke_mandou(db, integ, conversa):
    agora = T - timedelta(minutes=1)
    for autor, texto_, payload in (
        ("loja", None, {"source": "openapi", "message_type": "order", "content": {"order_sn": SN}}),
        ("sistema", cat.TEXTO_ENTREGUE_CELULAR.replace("{comprador}", "maria"), {}),
    ):
        db.add(
            AtendimentoMensagem(
                conversa_id=conversa.id,
                externo_id=uuid4().hex,
                autor=autor,
                origem="externo" if autor == "loja" else "sistema",
                tipo="texto",
                texto=texto_,
                enviada_em=agora,
                status="enviada",
                payload=payload,
                created_at=agora + timedelta(seconds=30),
            )
        )
        agora += timedelta(seconds=1)


async def _teto_cheio(db, integ, conversa):
    get_settings().atendimento_automacoes_teto_dia = 1
    db.add(
        AtendimentoAutomacaoRegistro(
            automacao="shopee_pedido_recebido",
            integration_id=integ.id,
            plataforma="shopee",
            alvo="pedido",
            chave="pedido:OUTRO",
            pedido="251005OUTRO000",
            evento_em=T - timedelta(minutes=30),
            devido_em=T - timedelta(minutes=25),
            decidido_em=T - timedelta(minutes=1),
            estado="enviado",
            modo="enviar",
            duoke="pendente",
        )
    )


@pytest.mark.parametrize(
    ("mudanca", "motivo", "disjuntor"),
    [
        (_devolucao_encerrada, "devolucao", False),
        (_cancelado_no_bling, "pedido_cancelado", False),
        (_resolvido_no_bling, "devolucao", False),
        (_duoke_mandou, "duoke_mandou", True),
        (_teto_cheio, "teto_dia", False),
    ],
    ids=["devolucao_encerrada", "cancelado", "bling_resolvido", "duoke_mandou", "teto"],
)
async def test_reconferencia_na_hora_de_enviar_pula_o_que_mudou(
    db, envio_ligado, mudanca, motivo, disjuntor
):
    integ, conversa, regra, linha, ctx = await _entregue_pronto(db)
    # Muda DEPOIS da leitura do lote e ANTES do envio.
    await mudanca(db, integ, conversa)
    await db.commit()
    estado = await automacoes._decidir_uma(db, ctx, linha)
    await db.commit()
    [linha] = await _linhas(db, automacao="shopee_entregue")
    assert (estado, linha.estado, linha.motivo) == ("pulado", "pulado", motivo)
    assert envio_ligado.envios == [] and await _nossas(db) == []
    await db.refresh(regra)
    assert (regra.modo == "simular") is disjuntor


async def test_reconferencia_pessoa_que_respondeu_segura_o_aguarde(db, envio_ligado):
    dono = await _dono(db)
    integ, canal = await _loja(db, dono, "atv")
    await _regra(db, integ, "shopee_aguarde", modo="enviar", enviar_desde=DESDE)
    conversa = await _conversa(db, integ, canal)
    await _msg(db, conversa, em=T - timedelta(minutes=15))
    await automacoes.descobrir_mensagens(
        db, await automacoes.regras_por_chave(db), agora=T, motor_desde=DESDE
    )
    await db.commit()
    [linha] = (
        (await db.execute(select(AtendimentoAutomacaoRegistro).with_for_update())).scalars().all()
    )
    ctx = await automacoes._contexto(db, [linha], agora=T)
    aut = cat.CATALOGO["shopee_aguarde"]
    assert automacoes._avaliar(ctx, aut, ctx.regras[(aut.codigo, integ.id)], linha).acao == "enviar"
    # Uma pessoa respondeu enquanto a rodada decidia.
    await _msg(
        db,
        conversa,
        autor="loja",
        texto="Oi! Já vejo para você",
        em=T - timedelta(seconds=30),
        visto=T - timedelta(seconds=20),
    )
    await automacoes._decidir_uma(db, ctx, linha)
    await db.commit()
    [linha] = await _linhas(db, automacao="shopee_aguarde")
    assert (linha.estado, linha.motivo) == ("pulado", "pessoa_respondeu")
    assert envio_ligado.envios == []


async def test_reconferencia_fora_do_horario_espera_a_abertura(db, envio_ligado):
    """O horário da regra vale também na reconferência (a junção com as correções
    do motor): o lote decidiu às 12h, mas o envio só chegaria às 20h00m30 — a linha
    fica `agendado`, nada sai, e a rodada seguinte anda o `devido_em` para as 9h."""
    _, _, _, linha, ctx = await _entregue_pronto(db)
    fechado = datetime(2026, 10, 5, 23, 0, 30, tzinfo=UTC)  # 20h00m30 em São Paulo
    ctx.agora_real = lambda: fechado
    estado = await automacoes._decidir_uma(db, ctx, linha)
    await db.commit()
    [linha] = await _linhas(db, automacao="shopee_entregue")
    assert (estado, linha.estado) == ("agendado", "agendado")
    assert envio_ligado.envios == [] and await _nossas(db) == []
    await _rodada(datetime(2026, 10, 5, 23, 2, tzinfo=UTC))  # 20h02: ainda fechado
    [linha] = await _linhas(db, automacao="shopee_entregue")
    assert linha.estado == "agendado"
    assert linha.devido_em == datetime(2026, 10, 6, 12, 0, tzinfo=UTC)  # 9h em São Paulo
    assert envio_ligado.envios == []


async def test_reconferencia_rele_com_o_corte_do_modo_seco_do_lote(db, envio_ligado, monkeypatch):
    """A reconferência relê o contexto com o MESMO `motor_desde` do lote (o corte do
    modo seco): a decisão e a reconferência nunca discordam por terem lido coisas
    diferentes."""
    _, _, _, linha, ctx = await _entregue_pronto(db)
    ctx.motor_desde = DESDE
    vistos: list = []
    original = automacoes._contexto

    async def _espia(session, linhas, *, agora, motor_desde=None):
        vistos.append(motor_desde)
        return await original(session, linhas, agora=agora, motor_desde=motor_desde)

    monkeypatch.setattr(automacoes, "_contexto", _espia)
    await automacoes._decidir_uma(db, ctx, linha)
    await db.commit()
    assert vistos == [DESDE]
    [linha] = await _linhas(db, automacao="shopee_entregue")
    assert linha.estado == "enviado"


async def test_decisao_do_lote_mede_o_davinci_sozinho_no_modo_seco(db):
    """O `_contexto` da junção leva o corte do modo seco (`cortes`): a opção 6 do
    Duoke 12 h depois do menu (uma automação que o DaVinci simula) não segura o
    menu do DaVinci para a volta do comprador — vale a linha simulada dele, cuja
    sessão acabou 12 h depois da resposta na hora. Sem o corte, `ja_mandado`."""
    dono = await _dono(db)
    integ, canal = await _loja(db, dono)
    menu = await _regra(db, integ, "shopee_menu")
    opcao = await _regra(db, integ, "shopee_opcao_6")
    conversa = await _conversa(db, integ, canal)
    volta_em = T - timedelta(minutes=3)
    base = volta_em - timedelta(hours=13)
    menu_duoke = await _msg(db, conversa, autor="loja", texto=MENU, em=base)
    digito = await _msg(db, conversa, texto="6", em=base + timedelta(minutes=1))
    await _msg(db, conversa, autor="loja", texto=cat.TEXTO_OPCAO_6, em=base + timedelta(hours=12))
    volta = await _msg(db, conversa, texto="oi, e o meu pedido?", em=volta_em)
    for regra, gatilho, devido in (
        (menu, menu_duoke, base),
        (opcao, digito, base + timedelta(minutes=2)),
    ):
        db.add(
            AtendimentoAutomacaoRegistro(
                automacao=regra.automacao,
                regra_id=regra.id,
                integration_id=integ.id,
                plataforma="shopee",
                alvo="conversa",
                chave=f"conversa:{conversa.id}:msg:{gatilho.id}",
                conversa_id=conversa.id,
                gatilho_mensagem_id=gatilho.id,
                evento_em=gatilho.enviada_em,
                devido_em=devido,
                decidido_em=devido,
                estado="simulado",
                modo="simular",
                duoke="mandou",
            )
        )
    db.add(
        AtendimentoAutomacaoRegistro(
            automacao="shopee_menu",
            regra_id=menu.id,
            integration_id=integ.id,
            plataforma="shopee",
            alvo="conversa",
            chave=f"conversa:{conversa.id}:msg:{volta.id}",
            conversa_id=conversa.id,
            gatilho_mensagem_id=volta.id,
            evento_em=volta_em,
            devido_em=volta_em + timedelta(minutes=1),
            estado="agendado",
            duoke="pendente",
        )
    )
    await db.commit()
    await automacoes.decidir_vencidas(db, agora=T, motor_desde=DESDE)
    [linha] = [
        x for x in await _linhas(db, automacao="shopee_menu") if x.gatilho_mensagem_id == volta.id
    ]
    assert (linha.estado, linha.motivo) == ("simulado", None)


async def test_sem_mudanca_a_reconferencia_deixa_sair(db, envio_ligado):
    _, _, _, linha, ctx = await _entregue_pronto(db)
    await automacoes._decidir_uma(db, ctx, linha)
    await db.commit()
    [linha] = await _linhas(db, automacao="shopee_entregue")
    assert linha.estado == "enviado"
    assert [e["tipo"] for e in envio_ligado.envios] == ["cartao_pedido", "texto"]


# ── 3. Pedidos fora da Logística: o índice ────────────────────────────────


async def test_entregue_e_concluido_fora_da_logistica_pelo_indice(db, proibido):
    dono = await _dono(db)
    integ, canal = await _loja(db, dono, "barbosa")
    await _regra(db, integ, "shopee_entregue")
    await _regra(db, integ, "shopee_pos_conclusao")
    await _conversa(db, integ, canal, pedido="251004FORALOGI")
    # Fora da Logística, entregue há pouco (o índice viu às 11h30 de SP).
    await _indice(
        db,
        integ,
        "251004FORALOGI",
        status="TO_CONFIRM_RECEIVE",
        atualizado=T - timedelta(minutes=30),
    )
    # Concluído sem o motor ter visto entregue: não vira pós (pode ser velho).
    await _indice(
        db, integ, "251001JACONCLU", status="COMPLETED", atualizado=T - timedelta(minutes=30)
    )
    # Na Logística E no índice: uma linha só (a da Logística, a fonte com o horário).
    db.add(_logistica("251004NALOGIST", "TO_CONFIRM_RECEIVE", T - timedelta(hours=3)))
    await db.commit()
    await _indice(
        db,
        integ,
        "251004NALOGIST",
        status="TO_CONFIRM_RECEIVE",
        atualizado=T - timedelta(minutes=5),
    )
    # Pedido de mais de 30 dias: fora da janela da Shopee, nada.
    await _indice(
        db,
        integ,
        "250801ANTIGO00",
        status="TO_CONFIRM_RECEIVE",
        criado=T - timedelta(days=40),
    )
    resumo = await _rodada()
    assert resumo["indice"] == 1
    entregues = {x.pedido: x for x in await _linhas(db, automacao="shopee_entregue")}
    assert set(entregues) == {"251004FORALOGI", "251004NALOGIST"}
    fora = entregues["251004FORALOGI"]
    assert fora.evento_em == T - timedelta(minutes=30) and fora.comprador_id == COMPRADOR
    assert (fora.estado, fora.motivo) == ("simulado", None)  # o status vem do índice
    assert entregues["251004NALOGIST"].evento_em == T - timedelta(hours=3)
    assert await _linhas(db, automacao="shopee_pos_conclusao") == []
    # Concluiu depois (o índice viu): agora sim o pós, 4 h depois de visto.
    linha = (
        await db.execute(
            select(AtendimentoPedidoComprador).where(
                AtendimentoPedidoComprador.pedido == "251004FORALOGI"
            )
        )
    ).scalar_one()
    linha.status = "COMPLETED"
    linha.atualizado_em = T + timedelta(hours=5)
    await db.commit()
    await _rodada(T + timedelta(hours=5, minutes=10))
    [pos] = await _linhas(db, automacao="shopee_pos_conclusao")
    assert pos.pedido == "251004FORALOGI" and pos.devido_em == T + timedelta(hours=9)
    assert proibido == []


async def test_fora_da_logistica_resolvido_no_bling_nao_recebe_entregue(db, proibido):
    dono = await _dono(db)
    integ, canal = await _loja(db, dono, "barbosa")
    await _regra(db, integ, "shopee_entregue")
    await _conversa(db, integ, canal, pedido="251004RESOLVID")
    await _pedido_bling(
        db, integ, "251004RESOLVID", situacao="545902", criado=T - timedelta(days=5)
    )
    await _indice(
        db,
        integ,
        "251004RESOLVID",
        status="TO_CONFIRM_RECEIVE",
        atualizado=T - timedelta(minutes=30),
    )
    await _rodada()
    [linha] = await _linhas(db, automacao="shopee_entregue")
    assert (linha.estado, linha.motivo, linha.divergencia) == (
        "pulado",
        "devolucao",
        "exclusao_disputa",
    )


async def test_so_duoke_fora_da_logistica_so_e_combinado_sem_o_indice(db, proibido):
    dono = await _dono(db)
    integ, canal = await _loja(db, dono, "barbosa")
    await _regra(db, integ, "shopee_entregue", ligada_desde=T - timedelta(days=5))
    em = T - timedelta(hours=20)
    pedidos = {"251003SEMINDIC": None, "251003COMINDIC": "COMPLETED"}
    for sn, status in pedidos.items():
        conversa = await _conversa(db, integ, canal, comprador=sn[-3:])
        await _msg(
            db,
            conversa,
            autor="loja",
            texto=None,
            em=em,
            payload={"source": "openapi", "message_type": "order", "content": {"order_sn": sn}},
        )
        await _msg(
            db,
            conversa,
            autor="sistema",
            texto=cat.TEXTO_ENTREGUE_CELULAR.replace("{comprador}", "ana"),
            em=em + timedelta(seconds=1),
        )
        if status:
            await _indice(db, integ, sn, status=status, atualizado=T - timedelta(hours=2))
    await automacoes_comparar.comparar(db, agora=T, motor_desde=T - timedelta(days=3))
    await db.commit()
    so = {x.pedido: x for x in await _linhas(db, estado="so_duoke")}
    assert so["251003SEMINDIC"].divergencia == "sem_logistica"
    # O índice tinha o status: o DaVinci vê esse pedido — o "só Duoke" conta.
    assert so["251003COMINDIC"].divergencia is None


# ── Simular NUNCA chama a plataforma — de novo, com os caminhos novos ─────


@pytest.mark.parametrize(
    "envio_geral,envio_automacoes,modo",
    [
        (False, False, "simular"),
        (True, True, "simular"),
        (False, False, "enviar"),
        (True, False, "enviar"),
        (False, True, "enviar"),
    ],
    ids=[
        "tudo_desligado",
        "chaves_ligadas_regra_simular",
        "enviar_sem_chaves",
        "enviar_sem_a_chave_nova",
        "enviar_sem_o_envio_geral",
    ],
)
async def test_simular_nunca_chama_a_plataforma_nos_caminhos_novos(
    db, proibido, _chaves, envio_geral, envio_automacoes, modo
):
    _chaves.atendimento_envio_ativo = envio_geral
    _chaves.atendimento_automacoes_envio = envio_automacoes
    _chaves.atendimento_automacoes_shopee_auto_reply = True
    dono = await _dono(db)
    integ, canal = await _loja(db, dono, "barbosa")
    regras = {}
    for codigo in (
        "shopee_pedido_recebido",
        "shopee_entregue",
        "shopee_pos_conclusao",
        "shopee_duvida_26h",
    ):
        regras[codigo] = await _regra(
            db, integ, codigo, modo=modo, enviar_desde=DESDE if modo == "enviar" else None
        )
    # Pedido recebido SEM conversa, com o comprador no índice e o escrow.
    await _pedido_bling(db, integ)
    await _indice(db, integ)
    await _escrow(db, integ)
    # Entregue com conversa (cartão + texto) e um FORA da Logística (pelo índice).
    await _conversa(db, integ, canal, comprador="61", pedido="251004ENTREGUE")
    db.add(_logistica("251004ENTREGUE", "TO_CONFIRM_RECEIVE", T - timedelta(hours=1)))
    await db.commit()
    await _indice(
        db,
        integ,
        "251004FORALOGI",
        status="TO_CONFIRM_RECEIVE",
        atualizado=T - timedelta(minutes=20),
    )
    # O 26 h (texto + figurinha) numa conversa.
    conversa = await _conversa(db, integ, canal, comprador="62")
    gatilho = await _msg(db, conversa, em=T - timedelta(hours=26, minutes=5))
    db.add(
        AtendimentoAutomacaoRegistro(
            automacao="shopee_duvida_26h",
            regra_id=regras["shopee_duvida_26h"].id,
            integration_id=integ.id,
            plataforma="shopee",
            alvo="conversa",
            chave=f"conversa:{conversa.id}:msg:{gatilho.id}",
            conversa_id=conversa.id,
            gatilho_mensagem_id=gatilho.id,
            evento_em=gatilho.enviada_em,
            devido_em=T - timedelta(minutes=10),
            estado="agendado",
            duoke="pendente",
        )
    )
    await db.commit()
    mensagens, conversas = await _n_mensagens(db), await _n_conversas(db)

    await _rodada()
    await _rodada(T + timedelta(hours=2))

    assert proibido == []
    assert await _n_mensagens(db) == mensagens
    assert await _n_conversas(db) == conversas  # o envio sem conversa não criou nenhuma
    linhas = await _linhas(db)
    simuladas = {(x.automacao, x.pedido) for x in linhas if x.estado == "simulado"}
    assert {
        ("shopee_pedido_recebido", SN),
        ("shopee_entregue", "251004ENTREGUE"),
        ("shopee_entregue", "251004FORALOGI"),
        ("shopee_duvida_26h", None),
    } <= simuladas
    assert not [x for x in linhas if x.estado in ("enviando", "enviado", "revisar", "falhou")]
    if modo == "enviar":
        assert {x.motivo for x in linhas if x.estado == "simulado"} == {"envio_desligado"}
    for r in regras.values():
        await db.refresh(r)
        assert r.modo == modo


async def test_saiu_sem_conversa_e_o_banco_falhou_vira_revisar_nunca_retenta(
    db, envio_ligado, monkeypatch
):
    from sqlalchemy.exc import DBAPIError

    await _pedido_recebido_sem_conversa(db)

    async def _quebra(*_a, **_k):
        raise DBAPIError("insert", {}, Exception("caiu"))

    monkeypatch.setattr(gravar, "upsert_conversa", _quebra)
    await _rodada()
    [linha] = await _linhas(db, automacao="shopee_pedido_recebido")
    # O cartão SAIU (a Shopee aceitou): sem a linha gravada, `revisar` — e nunca de novo.
    assert (linha.estado, linha.erro) == ("revisar", "gravar_falhou: DBAPIError")
    assert [e["tipo"] for e in envio_ligado.envios] == ["cartao_pedido"]
    # O texto não foi: a mensagem ficou pela metade — o disjuntor, uma vez só.
    assert linha.motivo == "parte_2"
    [regra] = (await db.execute(select(AtendimentoAutomacaoRegra))).scalars().all()
    await db.refresh(regra)
    assert (regra.modo, regra.disjuntor_motivo) == ("simular", "parte_falhou")
    await _rodada(T + timedelta(minutes=2))
    assert len(envio_ligado.envios) == 1


# ── O envio que para no meio: o cartão nunca fica sozinho em todo pedido ──
# Revisão de 05/10/2026: o pedido recebido e o entregue são [cartão, texto] — o
# cartão sai como mensagem normal e o texto como `auto_reply` (formato ainda
# não medido). Se o texto falha DEPOIS do cartão, o comprador fica com o
# cartão sozinho; sem trava, isso se repetiria em todo pedido.

ERRO_PARAM = ResultadoEnvio(ok=False, erro="shopee error_param")
SN2, SN3 = "251005ABCDEFG2", "251005ABCDEFG3"


async def _regra_de(db, codigo: str) -> AtendimentoAutomacaoRegra:
    regra = (
        await db.execute(
            select(AtendimentoAutomacaoRegra)
            .where(AtendimentoAutomacaoRegra.automacao == codigo)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    return regra


async def test_auto_reply_recusado_depois_do_cartao_revisar_e_disjuntor_uma_vez(db, envio_ligado):
    envio_ligado.falhas["auto_reply"] = ERRO_PARAM
    integ, _canal, _ = await _pedido_recebido_sem_conversa(db)
    for sn in (SN2, SN3):
        await _pedido_bling(db, integ, sn)
        await _indice(db, integ, sn)
    await _rodada()
    # UM cartão sozinho (o 1º pedido), não três: o disjuntor voltou a regra a simular.
    assert [(e["tipo"], e["ok"]) for e in envio_ligado.envios] == [
        ("cartao_pedido", True),
        ("texto", False),
    ]
    linhas = await _linhas(db, automacao="shopee_pedido_recebido")
    [parou] = [x for x in linhas if x.estado == "revisar"]
    assert (parou.motivo, parou.erro) == ("parte_2", "shopee error_param")
    assert len(parou.mensagem_ids) == 2  # o cartão que saiu e o texto recusado
    assert sorted(x.estado for x in linhas if x is not parou) == ["simulado", "simulado"]
    regra = await _regra_de(db, "shopee_pedido_recebido")
    assert (regra.modo, regra.disjuntor_motivo) == ("simular", "parte_falhou")
    assert regra.disjuntor_em is not None and regra.enviar_desde is None
    # As mensagens: o cartão `enviada`, o texto `falhou` — nada mais.
    assert [(m.tipo, m.status) for m in await _nossas(db)] == [
        ("pedido", "enviada"),
        ("texto", "falhou"),
    ]
    await _rodada(T + timedelta(minutes=2))
    assert len(envio_ligado.envios) == 2


@pytest.mark.parametrize(
    ("ocupada", "estado"), [(2, "enviado"), (99, "revisar")], ids=["passa", "nao_passa"]
)
async def test_conversa_ocupada_na_2a_parte_tenta_de_novo_na_hora(
    db, envio_ligado, monkeypatch, ocupada, estado
):
    monkeypatch.setattr(automacoes, "ESPERA_PARTE_S", 0)
    original = enviar.travar_conversa
    vezes = {"n": 0}

    async def _ocupada(session, conversa):
        if vezes["n"] < ocupada:
            vezes["n"] += 1
            raise enviar.EnvioRecusado(enviar.RECUSA_CONVERSA_OCUPADA, "")
        return await original(session, conversa)

    monkeypatch.setattr(enviar, "travar_conversa", _ocupada)
    await _pedido_recebido_sem_conversa(db)
    await _rodada()
    [linha] = await _linhas(db, automacao="shopee_pedido_recebido")
    regra = await _regra_de(db, "shopee_pedido_recebido")
    if estado == "enviado":
        # A leitura soltou a conversa: o texto saiu na mesma rodada, logo atrás do cartão.
        assert linha.estado == "enviado" and vezes["n"] == 2
        assert [e["tipo"] for e in envio_ligado.envios] == ["cartao_pedido", "texto"]
        assert regra.modo == "enviar"
    else:
        assert vezes["n"] == automacoes.TENTATIVAS_PARTE
        assert (linha.estado, linha.motivo, linha.erro) == (
            "revisar",
            "parte_2",
            "conversa_ocupada",
        )
        assert [e["tipo"] for e in envio_ligado.envios] == ["cartao_pedido"]
        assert (regra.modo, regra.disjuntor_motivo) == ("simular", "parte_falhou")


async def test_saiu_sem_conversation_id_nao_manda_o_resto_e_dispara(db, envio_ligado):
    # O contrato do adaptador (`auto_reply` na assinatura: é como o `enviar` vê
    # que ele manda como resposta automática — `auto_reply_no_adaptador`).
    async def _sem_id(*_a, auto_reply=False, **_k):
        envio_ligado.envios.append({"tipo": "cartao_pedido"})
        return ResultadoEnvio(ok=True, externo_id="5", payload={"message_id": "5"})

    envio_ligado.enviar_parte = _sem_id
    await _pedido_recebido_sem_conversa(db)
    await _rodada()
    [linha] = await _linhas(db, automacao="shopee_pedido_recebido")
    assert (linha.estado, linha.motivo, linha.erro) == (
        "revisar",
        "parte_2",
        "shopee sem_conversation_id",
    )
    assert len(envio_ligado.envios) == 1
    regra = await _regra_de(db, "shopee_pedido_recebido")
    assert (regra.modo, regra.disjuntor_motivo) == ("simular", "parte_falhou")


async def test_operador_desligou_no_meio_revisar_sem_disjuntor(db, envio_ligado, _chaves):
    def _desliga(parte, _auto_reply):
        if parte["tipo"] == "cartao_pedido":
            _chaves.atendimento_automacoes_envio = False

    envio_ligado.ao_enviar = _desliga
    await _pedido_recebido_sem_conversa(db)
    await _rodada()
    [linha] = await _linhas(db, automacao="shopee_pedido_recebido")
    assert (linha.estado, linha.motivo, linha.erro) == (
        "revisar",
        "parte_2",
        "automacoes_envio_desligado",
    )
    assert [e["tipo"] for e in envio_ligado.envios] == ["cartao_pedido"]
    regra = await _regra_de(db, "shopee_pedido_recebido")
    # Foi o operador (a chave): nada mais sai mesmo — a regra fica como estava.
    assert (regra.modo, regra.disjuntor_em) == ("enviar", None)


async def _duvida_26h_pronta(db) -> tuple[Any, Any]:
    dono = await _dono(db)
    integ, canal = await _loja(db, dono)
    regra = await _regra(db, integ, "shopee_duvida_26h", modo="enviar", enviar_desde=DESDE)
    conversa = await _conversa(db, integ, canal)
    gatilho = await _msg(db, conversa, em=T - timedelta(hours=26, minutes=5))
    db.add(
        AtendimentoAutomacaoRegistro(
            automacao="shopee_duvida_26h",
            regra_id=regra.id,
            regra_versao=1,
            integration_id=integ.id,
            plataforma="shopee",
            alvo="conversa",
            chave=f"conversa:{conversa.id}:msg:{gatilho.id}",
            conversa_id=conversa.id,
            gatilho_mensagem_id=gatilho.id,
            comprador_id=conversa.comprador_id,
            evento_em=gatilho.enviada_em,
            visto_em=T - timedelta(hours=26),
            devido_em=T - timedelta(minutes=10),
            estado="agendado",
            duoke="pendente",
        )
    )
    await db.commit()
    return integ, regra


async def test_campanha_recusada_pela_plataforma_na_1a_parte_dispara(db, envio_ligado):
    envio_ligado.falhas["auto_reply"] = ERRO_PARAM
    await _duvida_26h_pronta(db)
    await _rodada()
    [linha] = await _linhas(db, automacao="shopee_duvida_26h")
    # Nada saiu (o texto é a 1ª parte; a figurinha nem foi) — mas todo
    # comprador daria o mesmo erro: a regra volta a simular na 1ª.
    assert (linha.estado, linha.motivo, linha.erro) == ("falhou", None, "shopee error_param")
    assert [(e["tipo"], e["ok"]) for e in envio_ligado.envios] == [("texto", False)]
    regra = await _regra_de(db, "shopee_duvida_26h")
    assert (regra.modo, regra.disjuntor_motivo) == ("simular", "plataforma_recusou")


async def test_mensagem_normal_recusada_na_1a_parte_nao_dispara(db, envio_ligado):
    envio_ligado.falhas["texto"] = ERRO_PARAM
    dono = await _dono(db)
    integ, canal = await _loja(db, dono, "atv")
    await _regra(db, integ, "shopee_aguarde", modo="enviar", enviar_desde=DESDE)
    conversa = await _conversa(db, integ, canal)
    await _msg(db, conversa, em=T - timedelta(minutes=15))
    await automacoes.descobrir_mensagens(
        db, await automacoes.regras_por_chave(db), agora=T, motor_desde=DESDE
    )
    await db.commit()
    [linha] = (
        (await db.execute(select(AtendimentoAutomacaoRegistro).with_for_update())).scalars().all()
    )
    ctx = await automacoes._contexto(db, [linha], agora=T)
    await automacoes._decidir_uma(db, ctx, linha)
    await db.commit()
    [linha] = await _linhas(db, automacao="shopee_aguarde")
    assert (linha.estado, linha.erro) == ("falhou", "shopee error_param")
    regra = await _regra_de(db, "shopee_aguarde")
    assert (regra.modo, regra.disjuntor_em) == ("enviar", None)


async def test_mesmo_comprador_dois_pedidos_o_texto_repetido_ja_esta_na_conversa(db, envio_ligado):
    integ, _canal, _ = await _pedido_recebido_sem_conversa(db)
    await _pedido_bling(db, integ, SN2)
    await _indice(db, integ, SN2)
    await _rodada()
    # O 2º pedido: o cartão dele sai; a MESMA frase acabou de sair nesta conversa
    # (a trava de repetido do envio) — não é mensagem pela metade, não dispara.
    assert [e["tipo"] for e in envio_ligado.envios] == ["cartao_pedido", "texto", "cartao_pedido"]
    primeiro, segundo = envio_ligado.envios[0]["pedido"], envio_ligado.envios[2]["pedido"]
    assert {primeiro, segundo} == {SN, SN2}
    linhas = {x.pedido: x for x in await _linhas(db, automacao="shopee_pedido_recebido")}
    assert {sn: x.estado for sn, x in linhas.items()} == {SN: "enviado", SN2: "enviado"}
    assert (len(linhas[primeiro].mensagem_ids), len(linhas[segundo].mensagem_ids)) == (2, 1)
    regra = await _regra_de(db, "shopee_pedido_recebido")
    assert (regra.modo, regra.disjuntor_em) == ("enviar", None)


async def test_prazo_da_rodada_nenhum_envio_novo_comeca_depois(db, envio_ligado, monkeypatch):
    await _pedido_recebido_sem_conversa(db)
    monkeypatch.setattr(automacoes, "ORCAMENTO_ENVIO_S", -1)
    resumo = await _rodada()
    [linha] = await _linhas(db, automacao="shopee_pedido_recebido")
    # Passou do prazo: fica para a próxima rodada (nunca começa um envio que o
    # timeout da rodada cortaria entre o cartão e o texto).
    assert linha.estado == "agendado" and envio_ligado.envios == []
    assert resumo["decididas"] == {"agendado": 1}
    monkeypatch.setattr(automacoes, "ORCAMENTO_ENVIO_S", 50)
    await _rodada(T + timedelta(minutes=2))
    [linha] = await _linhas(db, automacao="shopee_pedido_recebido")
    assert linha.estado == "enviado"
    assert [e["tipo"] for e in envio_ligado.envios] == ["cartao_pedido", "texto"]


async def test_teto_do_dia_conta_o_falhou_que_falou_com_a_plataforma(db):
    dono = await _dono(db)
    integ, _canal = await _loja(db, dono)
    for i, ids in enumerate(([], ["x"], ["y", "z"])):
        db.add(
            AtendimentoAutomacaoRegistro(
                automacao="shopee_pedido_recebido",
                integration_id=integ.id,
                plataforma="shopee",
                alvo="pedido",
                chave=f"pedido:TETO{i}",
                pedido=f"251005TETO000{i}",
                evento_em=T - timedelta(minutes=30),
                devido_em=T - timedelta(minutes=25),
                decidido_em=T - timedelta(minutes=1),
                estado="falhou",
                modo="enviar",
                duoke="pendente",
                mensagem_ids=ids,
            )
        )
    await db.commit()
    contagem = await automacoes._contagens_do_dia(db, agora=T)
    aut = cat.CATALOGO["shopee_pedido_recebido"]
    # O `falhou` sem mensagem não chegou à plataforma; os dois com mensagem contam.
    assert contagem[(integ.id, aut.codigo, "envio")] == 2
    assert contagem[(integ.id, aut.familia, "envio")] == 2


# ── A conversa achada pelo nome é a do comprador do pedido ────────────────


async def test_conversa_pelo_nome_de_outro_comprador_nunca_recebe(db, envio_ligado):
    dono = await _dono(db)
    integ, canal = await _loja(db, dono)
    await _regra(db, integ, "shopee_pedido_recebido", modo="enviar", enviar_desde=DESDE)
    # O índice diz que o pedido é do 555; a conversa com o mesmo usuário é do 999.
    outra = await _conversa(db, integ, canal, comprador="999", nome="maria.silva")
    await _pedido_bling(db, integ)
    await _indice(db, integ)
    await _escrow(db, integ, nome="maria.silva")
    # Já na descoberta: a conversa do mesmo usuário e de outro comprador não é gravada.
    await automacoes.descobrir_pedidos_pagos(
        db, await automacoes.regras_por_chave(db), agora=T, motor_desde=DESDE
    )
    await db.commit()
    [linha] = await _linhas(db, automacao="shopee_pedido_recebido")
    assert (linha.conversa_id, linha.comprador_id) == (None, COMPRADOR)
    # Com o comprador certo no nome, a conversa dele é a escolhida.
    dele = await _conversa(db, integ, canal, comprador=COMPRADOR, nome="maria.silva")
    achadas = await automacoes._conversas_pelo_nome(
        db, {(integ.id, SN): "maria.silva"}, compradores={(integ.id, SN): COMPRADOR}
    )
    assert achadas == {(integ.id, SN): dele.id}
    await db.delete(dele)
    await db.commit()
    await _rodada()
    [linha] = await _linhas(db, automacao="shopee_pedido_recebido")
    assert linha.estado == "enviado" and linha.conversa_id != outra.id
    assert {e["to_id"] for e in envio_ligado.envios} == {COMPRADOR}
    assert not [m for m in await _nossas(db) if m.conversa_id == outra.id]


async def test_nome_de_dois_compradores_sem_o_indice_nao_escolhe_nenhum(db, envio_ligado):
    dono = await _dono(db)
    integ, canal = await _loja(db, dono)
    await _regra(db, integ, "shopee_pedido_recebido", modo="enviar", enviar_desde=DESDE)
    await _conversa(db, integ, canal, comprador="998", nome="maria.silva")
    await _conversa(db, integ, canal, comprador="999", nome="maria.silva")
    await _pedido_bling(db, integ)
    await _escrow(db, integ, nome="maria.silva")
    await _rodada()
    [linha] = await _linhas(db, automacao="shopee_pedido_recebido")
    # Ambíguo: nenhuma das duas; espera o índice trazer o comprador.
    assert (linha.estado, linha.conversa_id) == ("agendado", None)
    assert envio_ligado.envios == []


async def test_conversa_gravada_de_outro_comprador_e_trocada_pelo_to_id(db, envio_ligado):
    dono = await _dono(db)
    integ, canal = await _loja(db, dono)
    regra = await _regra(db, integ, "shopee_pedido_recebido", modo="enviar", enviar_desde=DESDE)
    outra = await _conversa(db, integ, canal, comprador="999")
    await _indice(db, integ)
    db.add(
        AtendimentoAutomacaoRegistro(
            automacao="shopee_pedido_recebido",
            regra_id=regra.id,
            regra_versao=1,
            integration_id=integ.id,
            plataforma="shopee",
            alvo="pedido",
            chave=f"pedido:{SN}",
            pedido=SN,
            conversa_id=outra.id,  # gravada na descoberta, de outro comprador
            evento_em=T - timedelta(minutes=8),
            visto_em=T - timedelta(minutes=7),
            devido_em=T - timedelta(minutes=3),
            estado="agendado",
            duoke="pendente",
        )
    )
    await db.commit()
    await _rodada()
    [linha] = await _linhas(db, automacao="shopee_pedido_recebido")
    assert linha.estado == "enviado" and linha.conversa_id != outra.id
    assert [(e["tipo"], e["to_id"]) for e in envio_ligado.envios] == [
        ("cartao_pedido", COMPRADOR),
        ("texto", COMPRADOR),
    ]
