# ruff: noqa: S105, S106  (tokens de um cliente falso, nada real)
"""Adaptador do Mercado Livre: perguntas pré-venda e mensagens pós-venda.

Os dados do cliente falso seguem os formatos REAIS medidos em produção em
25/09/2026 (só chaves e tipos): `/my/received_questions/search` com
`api_version=4`, `/messages/unread` e `/messages/packs/{pack}/sellers/{seller}`.

O que estes testes garantem:

- UMA CONVERSA POR PERGUNTA (`q:<id>`): cada pergunta tem a sua fila e o seu
  prazo de 1 h, e responder uma não tira da fila a outra do mesmo comprador;
- pergunta sem resposta entra na fila com SLA de 1 h; respondida por fora sai;
  fechada/apagada pelo ML fecha a conversa (e reabre se voltar); resposta que
  o ML derruba (BANNED) vira `falhou` e fecha, sem reconsulta a cada rodada;
- pack com mensagens das duas pontas, bloqueio por prazo/cancelamento e a
  volta para ativo; a releitura de quem espera resposta pega a resposta dada
  no Duoke; a de quem teve movimento recente pega o comprador que voltou a
  escrever (lido no Duoke) e a moderação da nossa mensagem; a varredura dos
  pedidos recentes acha o pack que já saiu dos não lidos;
- moderação da mensagem da loja (em qualquer caixa, ou pelo `status`) e a
  que já vem na resposta do POST não contam como resposta;
- um pack com 403 não tira a conta da leitura (`erro`, não `sem_escopo`);
- rodar duas vezes não duplica nada; a nossa resposta voltando é adotada;
- envio de pergunta e de pós-venda: ok, recusa, bloqueio, ambíguo;
- NENHUM GET de pack sai sem `mark_as_read=false` (o Duoke continua ligado).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    Integration,
    IntegrationPlatform,
    User,
)
from app.security.cipher import encrypt_json
from app.services.atendimento import gravar
from app.services.atendimento import ml as ml_atd
from app.services.atendimento.constantes import STATUS_CANAL
from app.services.marketplaces.ml import MercadoLivreClient

SELLER = 999000111
COMPRADOR = 555000222
PACK = "2000009876543210"
PEDIDO = "2000001111111111"
ITEM = "MLB1234567890"
T0 = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
_BRT_ML = timezone(timedelta(hours=-4))  # o ML devolve as perguntas em -04:00
_AsyncClientReal = httpx.AsyncClient


# ─────────────── formatos reais ───────────────


def _iso_pergunta(quando: datetime) -> str:
    """Como as perguntas vêm: fração de 7 dígitos e fuso -04:00 (str[33])."""
    return quando.astimezone(_BRT_ML).strftime("%Y-%m-%dT%H:%M:%S") + ".0000000-04:00"


def _iso_pack(quando: datetime) -> str:
    """Como as mensagens de pack vêm: `2026-09-25T12:00:00Z` (str[20])."""
    return quando.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pergunta(
    qid: int,
    *,
    quando: datetime = T0,
    item: str = ITEM,
    de: int = COMPRADOR,
    texto: str = "Tem na cor azul?",
    status: str = "UNANSWERED",
    resposta: str | None = None,
    resposta_em: datetime | None = None,
) -> dict:
    q = {
        "date_created": _iso_pergunta(quando),
        "item_id": item,
        "seller_id": SELLER,
        "status": status,
        "text": texto,
        "tags": [],
        "ai_categories": [],
        "id": qid,
        "answer": None,
        "from": {"id": de},
        "hold": False,
        "deleted_from_listing": False,
    }
    if resposta is not None:
        q["status"] = "ANSWERED"
        q["answer"] = {
            "text": resposta,
            "status": "ACTIVE",
            "date_created": _iso_pergunta(resposta_em or quando + timedelta(minutes=10)),
        }
    return q


def _msg_pack(
    mid: str,
    *,
    de: int,
    para: int,
    texto: str,
    quando: datetime,
    pedido: str | None = None,
    moderacao: str = "clean",
) -> dict:
    """Mensagem de pack no formato REAL (doc do ML e `formato_ml_pack.txt`):
    `message_resources` = packs + sellers e `data.order_id` null — o pedido
    não aparece. `pedido=` acrescenta o recurso "orders" (o outro caminho)."""
    iso = _iso_pack(quando)
    recursos = [{"id": PACK, "name": "packs"}, {"id": str(SELLER), "name": "sellers"}]
    if pedido:
        recursos.append({"id": pedido, "name": "orders"})
    return {
        "id": mid,
        "site_id": "MLB",
        "client_id": None,
        "from": {"user_id": de},
        "to": {"user_id": para},
        "status": "available",
        "subject": None,
        "text": texto,
        "message_date": {
            "received": iso,
            "available": iso,
            "notified": iso,
            "created": iso,
            "read": iso,
        },
        "message_moderation": {
            "is_automatic": False,
            "status": moderacao,
            "reason": None if moderacao == "clean" else "out_of_platform",
            "source": "online",
            "moderation_date": iso,
        },
        "message_attachments": None,
        "message_resources": recursos,
        "conversation_first_message": False,
        "data": {
            "last_message_session_id": None,
            "option_id": None,
            "order_creation_date": iso,
            "order_id": None,
            "original_conversation": None,
        },
    }


def _do_cliente(mid: str, texto: str, quando: datetime, **kw) -> dict:
    return _msg_pack(mid, de=COMPRADOR, para=SELLER, texto=texto, quando=quando, **kw)


def _da_loja(mid: str, texto: str, quando: datetime, **kw) -> dict:
    return _msg_pack(mid, de=SELLER, para=COMPRADOR, texto=texto, quando=quando, **kw)


def _status_conversa(status: str = "active", substatus: str | None = None) -> dict:
    return {
        "path": f"/packs/{PACK}/seller/{SELLER}",
        "status": status,
        "substatus": substatus,
        "status_date": _iso_pack(T0),
        "status_update_allowed": False,
        "claim_ids": [],
        "shipping_id": None,
        "data": None,
    }


def _erro_http(status: int, corpo: dict, metodo: str = "GET") -> httpx.HTTPStatusError:
    req = httpx.Request(metodo, "https://api.mercadolibre.com/x")
    resp = httpx.Response(status, json=corpo, request=req)
    return httpx.HTTPStatusError(f"HTTP {status}", request=req, response=resp)


def _resposta(status: int, corpo: dict | list) -> httpx.Response:
    return httpx.Response(
        status, json=corpo, request=httpx.Request("POST", "https://api.mercadolibre.com/x")
    )


class ClienteFalso:
    """O MercadoLivreClient, só com o que o adaptador usa, nos formatos reais."""

    def __init__(self) -> None:
        self.creds = {"user_id": SELLER, "access_token": "tok"}
        self.abertas: list[dict] = []
        self.respondidas: list[dict] = []
        self.detalhes: dict[str, dict | Exception] = {}
        self.nao_lidos: dict[str, int] = {}
        # pack -> {"status": conversation_status, "messages": [...]} | Exception
        self.packs: dict[str, dict | Exception] = {}
        self.itens: dict[str, dict] = {}
        # Pedidos recentes (GET /orders/search), para a varredura.
        self.pedidos: list[dict] = []
        self.falha_pedidos: Exception | None = None
        self.falha_leitura: Exception | None = None
        self.post: httpx.Response | Exception = _resposta(200, {"id": 1})
        self.chamadas: list[tuple] = []

    # — perguntas —
    async def perguntas_recebidas(self, *, status="UNANSWERED", offset=0, limit=50) -> dict:
        self.chamadas.append(("perguntas", status, offset, limit))
        if self.falha_leitura is not None:
            raise self.falha_leitura
        fonte = self.abertas if status == "UNANSWERED" else self.respondidas
        return {
            "total": len(fonte),
            "limit": limit,
            "questions": fonte[offset : offset + limit],
            "filters": {
                "limit": limit,
                "offset": offset,
                "api_version": "4",
                "is_admin": False,
                "sorts": [],
                "caller": SELLER,
                "seller": SELLER,
                "status": status,
            },
            "available_filters": [],
            "available_sorts": [],
        }

    async def detalhe_pergunta(self, question_id) -> dict:
        self.chamadas.append(("detalhe", str(question_id)))
        d = self.detalhes.get(str(question_id))
        if isinstance(d, Exception):
            raise d
        if d is None:
            raise _erro_http(404, {"error": "not_found", "status": 404})
        return d

    async def get_item(self, item_id) -> dict:
        self.chamadas.append(("item", item_id))
        return self.itens.get(item_id, {"id": item_id, "title": f"Anúncio {item_id}"})

    async def responder_pergunta(self, question_id, texto) -> httpx.Response:
        self.chamadas.append(("responder", str(question_id), texto))
        if isinstance(self.post, Exception):
            raise self.post
        return self.post

    # — pós-venda —
    async def mensagens_nao_lidas(self, tag="post_sale") -> dict:
        self.chamadas.append(("nao_lidas", tag))
        if self.falha_leitura is not None:
            raise self.falha_leitura
        return {
            "user_id": SELLER,
            "total": len(self.nao_lidos),
            "results": [
                {"resource": f"/packs/{p}/sellers/{SELLER}", "count": c}
                for p, c in self.nao_lidos.items()
            ],
        }

    async def mensagens_do_pack(self, pack_id, seller_id, *, offset=0, limit=20) -> dict:
        self.chamadas.append(("pack", str(pack_id), str(seller_id), offset))
        pack = self.packs[str(pack_id)]
        if isinstance(pack, Exception):
            raise pack
        msgs = pack["messages"]
        return {
            "paging": {"limit": limit, "offset": offset, "total": len(msgs)},
            "conversation_status": pack.get("status") or _status_conversa(),
            "messages": msgs[offset : offset + limit],
            "seller_max_message_length": 350,
            "buyer_max_message_length": 3500,
        }

    async def enviar_mensagem_pack(self, pack_id, seller_id, buyer_id, texto) -> httpx.Response:
        self.chamadas.append(("enviar_pack", str(pack_id), str(seller_id), str(buyer_id), texto))
        if isinstance(self.post, Exception):
            raise self.post
        return self.post

    async def search_orders(self, *, seller_id, date_from, date_to, limit=50, offset=0) -> dict:
        self.chamadas.append(("pedidos", offset, limit))
        if self.falha_pedidos is not None:
            raise self.falha_pedidos
        return {
            "query": None,
            "results": self.pedidos[offset : offset + limit],
            "sort": {"id": "date_asc", "name": "Date ascending"},
            "paging": {"total": len(self.pedidos), "offset": offset, "limit": limit},
        }

    def packs_lidos(self) -> list[str]:
        return [c[1] for c in self.chamadas if c[0] == "pack"]


# ─────────────── fixtures ───────────────


@pytest.fixture
def relogio(monkeypatch):
    """Relógio do adaptador controlado pelo teste."""
    atual = {"agora": T0 + timedelta(minutes=30)}
    monkeypatch.setattr(ml_atd, "_agora", lambda: atual["agora"])
    return atual


async def _integ(db: AsyncSession, user: User, nome: str = "ML KFA") -> Integration:
    integ = Integration(
        user_id=user.id,
        platform=IntegrationPlatform.ML,
        name=nome,
        credentials=encrypt_json({"access_token": "tok", "user_id": SELLER}),
    )
    db.add(integ)
    await db.commit()
    await db.refresh(integ)
    return integ


async def _canal(db: AsyncSession, make_user, canal: str) -> tuple[AtendimentoCanal, Integration]:
    user = await make_user()
    integ = await _integ(db, user)
    c = AtendimentoCanal(integration_id=integ.id, plataforma="ml", canal=canal)
    db.add(c)
    await db.commit()
    return c, integ


async def _rodar(db: AsyncSession, canal, integ, cliente):
    resultado = await ml_atd.sincronizar(db, canal, integ, cliente)
    await db.commit()
    return resultado


async def _conversa(db: AsyncSession, externo_id: str) -> AtendimentoConversa:
    conversa = (
        await db.execute(
            select(AtendimentoConversa).where(AtendimentoConversa.externo_id == externo_id)
        )
    ).scalar_one()
    await db.refresh(conversa)
    return conversa


async def _mensagens(db: AsyncSession, conversa: AtendimentoConversa) -> list[AtendimentoMensagem]:
    linhas = (
        (
            await db.execute(
                select(AtendimentoMensagem)
                .where(AtendimentoMensagem.conversa_id == conversa.id)
                # Pergunta e resposta podem ter o mesmo relógio do ML (a
                # banida vem com a data da pergunta): o desempate é a ordem
                # em que o DaVinci gravou, senão o teste pisca.
                .order_by(AtendimentoMensagem.enviada_em, AtendimentoMensagem.created_at)
            )
        )
        .scalars()
        .all()
    )
    for m in linhas:
        await db.refresh(m)
    return list(linhas)


async def _total_mensagens(db: AsyncSession) -> int:
    return int(await db.scalar(select(func.count()).select_from(AtendimentoMensagem)))


# ─────────────── perguntas ───────────────


def _q(qid: int) -> str:
    """O `externo_id` da conversa de uma pergunta (uma conversa por pergunta)."""
    return f"q:{qid}"


async def test_pergunta_sem_resposta_entra_na_fila_com_sla_de_1h(
    db: AsyncSession, make_user, relogio
):
    canal, integ = await _canal(db, make_user, "pergunta")
    cli = ClienteFalso()
    cli.abertas = [_pergunta(101, quando=T0)]

    r = await _rodar(db, canal, integ, cli)

    assert r.status == "ok"
    assert (r.conversas_novas, r.mensagens_novas, r.nao_lidas) == (1, 1, 1)
    conversa = await _conversa(db, _q(101))
    assert conversa.plataforma == "ml" and conversa.canal == "pergunta"
    assert conversa.conta == "ML KFA"
    assert conversa.comprador_id == str(COMPRADOR)
    assert conversa.anuncio_id == ITEM
    assert conversa.anuncio_titulo == f"Anúncio {ITEM}"
    # O elo da conversa com a pergunta: é por ele que o envio sabe o que responder.
    # (Além dele, `dados` guarda o carimbo do enriquecimento — o cartão do
    # anúncio; ver test_atendimento_enriquecer.py.)
    elo = ("question_id", "item_id", "from_id", "status_ml")
    assert {k: conversa.dados.get(k) for k in elo} == {
        "question_id": "101",
        "item_id": ITEM,
        "from_id": str(COMPRADOR),
        "status_ml": "UNANSWERED",
    }
    assert conversa.aguardando_resposta is True
    assert conversa.situacao == "aberta"
    assert conversa.nao_lidas == 1
    assert conversa.ultima_do_cliente_em == T0  # fração de 7 dígitos e -04:00 → UTC
    assert conversa.prazo_resposta_em == T0 + timedelta(hours=1)
    [msg] = await _mensagens(db, conversa)
    assert (msg.externo_id, msg.autor, msg.origem) == ("q:101", "cliente", "cliente")
    assert msg.payload["status"] == "UNANSWERED"
    await db.refresh(canal)
    assert canal.cursor["ultima_rodada"] == relogio["agora"].isoformat()
    # Leu as abertas E as respondidas recentes (para ver resposta dada por fora).
    assert ("perguntas", "UNANSWERED", 0, 50) in cli.chamadas
    assert ("perguntas", "ANSWERED", 0, 50) in cli.chamadas


async def test_pergunta_respondida_fora_do_davinci_sai_da_fila(
    db: AsyncSession, make_user, relogio
):
    canal, integ = await _canal(db, make_user, "pergunta")
    cli = ClienteFalso()
    cli.abertas = [_pergunta(101, quando=T0)]
    await _rodar(db, canal, integ, cli)

    # Alguém respondeu no Duoke: a pergunta sai das abertas e vai para as respondidas.
    cli.abertas = []
    cli.respondidas = [
        _pergunta(101, quando=T0, resposta="Temos sim!", resposta_em=T0 + timedelta(minutes=12))
    ]
    r = await _rodar(db, canal, integ, cli)

    assert (r.conversas_novas, r.conversas_atualizadas, r.mensagens_novas) == (0, 1, 1)
    conversa = await _conversa(db, _q(101))
    assert conversa.aguardando_resposta is False
    assert conversa.prazo_resposta_em is None
    assert conversa.situacao == "respondida"
    assert conversa.nao_lidas == 0
    assert conversa.dados["status_ml"] == "ANSWERED"
    q, a = await _mensagens(db, conversa)
    assert q.externo_id == "q:101"
    assert (a.externo_id, a.autor, a.origem, a.status) == ("a:101", "loja", "externo", "enviada")
    assert a.enviada_em == T0 + timedelta(minutes=12)
    # A resposta já estava gravada: não há reconsulta por id.
    assert not [c for c in cli.chamadas if c[0] == "detalhe"]


async def test_cada_pergunta_e_uma_conversa_mesmo_do_mesmo_comprador_e_anuncio(
    db: AsyncSession, make_user, relogio
):
    """D3: juntar as perguntas do mesmo comprador no mesmo anúncio fazia a
    resposta a uma tirar da fila a outra, ainda sem resposta no ML."""
    canal, integ = await _canal(db, make_user, "pergunta")
    cli = ClienteFalso()
    cli.abertas = [
        _pergunta(103, quando=T0 + timedelta(minutes=5), texto="E na cor preta?"),
        _pergunta(102, quando=T0, texto="Tem na cor azul?"),
        _pergunta(104, quando=T0, de=777, texto="Serve no modelo X?"),
    ]
    r = await _rodar(db, canal, integ, cli)

    assert (r.conversas_novas, r.mensagens_novas) == (3, 3)
    for qid, quando in ((102, T0), (103, T0 + timedelta(minutes=5)), (104, T0)):
        conversa = await _conversa(db, _q(qid))
        assert conversa.nao_lidas == 1
        assert conversa.aguardando_resposta is True
        assert conversa.prazo_resposta_em == quando + timedelta(hours=1)
        assert [m.externo_id for m in await _mensagens(db, conversa)] == [f"q:{qid}"]
    # O mesmo anúncio: o título foi buscado uma vez só (cache da rodada).
    assert [c for c in cli.chamadas if c[0] == "item"] == [("item", ITEM)]


async def test_rodar_duas_vezes_nao_duplica_nada(db: AsyncSession, make_user, relogio):
    canal, integ = await _canal(db, make_user, "pergunta")
    cli = ClienteFalso()
    cli.abertas = [_pergunta(201, quando=T0 + timedelta(minutes=1))]
    cli.respondidas = [_pergunta(200, quando=T0, resposta="Sim!", item="MLB999")]
    primeira = await _rodar(db, canal, integ, cli)
    total = await _total_mensagens(db)

    segunda = await _rodar(db, canal, integ, cli)

    assert (primeira.conversas_novas, primeira.mensagens_novas) == (2, 3)
    assert (segunda.conversas_novas, segunda.conversas_atualizadas, segunda.mensagens_novas) == (
        0,
        0,
        0,
    )
    assert await _total_mensagens(db) == total == 3


async def test_pergunta_fechada_pelo_ml_fecha_a_conversa_e_reabre_se_voltar(
    db: AsyncSession, make_user, relogio
):
    canal, integ = await _canal(db, make_user, "pergunta")
    cli = ClienteFalso()
    cli.abertas = [_pergunta(301, quando=T0)]
    await _rodar(db, canal, integ, cli)

    # Sumiu das abertas e não está entre as respondidas: pergunta-se pelo id.
    cli.abertas = []
    cli.detalhes["301"] = _pergunta(301, quando=T0, status="UNDER_REVIEW")
    r = await _rodar(db, canal, integ, cli)

    assert ("detalhe", "301") in cli.chamadas
    assert r.conversas_atualizadas == 1
    conversa = await _conversa(db, _q(301))
    assert conversa.situacao == "fechada"
    assert conversa.bloqueio_motivo == "UNDER_REVIEW"
    assert conversa.aguardando_resposta is False
    assert conversa.prazo_resposta_em is None
    assert conversa.dados["status_ml"] == "UNDER_REVIEW"
    [q] = await _mensagens(db, conversa)
    assert q.payload["status"] == "UNDER_REVIEW"

    # Fechada não é reconsultada toda rodada.
    cli.chamadas.clear()
    await _rodar(db, canal, integ, cli)
    assert not [c for c in cli.chamadas if c[0] == "detalhe"]

    # Saiu da revisão e voltou a UNANSWERED: a conversa reabre sozinha.
    cli.abertas = [_pergunta(301, quando=T0)]
    await _rodar(db, canal, integ, cli)
    conversa = await _conversa(db, _q(301))
    assert conversa.situacao == "aberta"
    assert conversa.bloqueio_motivo is None
    assert conversa.aguardando_resposta is True


async def test_pergunta_apagada_404_fecha_como_deleted(db: AsyncSession, make_user, relogio):
    canal, integ = await _canal(db, make_user, "pergunta")
    cli = ClienteFalso()
    cli.abertas = [_pergunta(401, quando=T0)]
    await _rodar(db, canal, integ, cli)

    cli.abertas = []  # detalhe sem cadastro no falso = 404
    await _rodar(db, canal, integ, cli)

    conversa = await _conversa(db, _q(401))
    assert (conversa.situacao, conversa.bloqueio_motivo) == ("fechada", "DELETED")


async def test_reconsulta_acha_resposta_antiga_dada_por_fora(
    db: AsyncSession, make_user, relogio
):
    """Respondida fora do DaVinci e já fora das 50 respondidas recentes."""
    canal, integ = await _canal(db, make_user, "pergunta")
    cli = ClienteFalso()
    cli.abertas = [_pergunta(501, quando=T0)]
    await _rodar(db, canal, integ, cli)

    cli.abertas = []
    cli.detalhes["501"] = _pergunta(501, quando=T0, resposta="Respondido pelo celular")
    await _rodar(db, canal, integ, cli)

    conversa = await _conversa(db, _q(501))
    assert conversa.situacao == "respondida"
    assert conversa.aguardando_resposta is False
    assert [m.origem for m in await _mensagens(db, conversa)] == ["cliente", "externo"]


async def test_fechada_pela_pessoa_nao_reabre_com_a_pergunta_ainda_aberta(
    db: AsyncSession, make_user, relogio
):
    canal, integ = await _canal(db, make_user, "pergunta")
    cli = ClienteFalso()
    cli.abertas = [_pergunta(601, quando=T0)]
    await _rodar(db, canal, integ, cli)
    conversa = await _conversa(db, _q(601))
    conversa.situacao = "fechada"  # a pessoa deu por resolvida na tela
    conversa.aguardando_resposta = False
    await db.commit()

    await _rodar(db, canal, integ, cli)

    conversa = await _conversa(db, _q(601))
    assert conversa.situacao == "fechada"


async def test_pergunta_nova_do_mesmo_comprador_e_outra_conversa(
    db: AsyncSession, make_user, relogio
):
    canal, integ = await _canal(db, make_user, "pergunta")
    cli = ClienteFalso()
    cli.abertas = [_pergunta(351, quando=T0)]
    await _rodar(db, canal, integ, cli)
    cli.abertas = []  # apagada (404)
    await _rodar(db, canal, integ, cli)
    assert (await _conversa(db, _q(351))).bloqueio_motivo == "DELETED"

    # O mesmo comprador pergunta de novo no mesmo anúncio: conversa própria.
    cli.abertas = [_pergunta(352, quando=T0 + timedelta(minutes=20), texto="Ainda tem?")]
    await _rodar(db, canal, integ, cli)

    nova = await _conversa(db, _q(352))
    assert (nova.situacao, nova.bloqueio_motivo) == ("aberta", None)
    assert nova.aguardando_resposta is True
    assert nova.prazo_resposta_em == T0 + timedelta(minutes=20, hours=1)
    apagada = await _conversa(db, _q(351))
    assert (apagada.situacao, apagada.bloqueio_motivo) == ("fechada", "DELETED")


async def test_resposta_banida_pelo_ml_fecha_a_conversa_e_para_de_reconsultar(
    db: AsyncSession, make_user, relogio
):
    """API-5: ANSWERED com a resposta BANNED (texto vazio) não é pergunta pendente.

    Antes, a `q:<id>` ficava "pendente" para sempre: a conversa na fila com
    alerta de 1 h e um GET /questions/{id} a cada rodada.
    """
    canal, integ = await _canal(db, make_user, "pergunta")
    cli = ClienteFalso()
    cli.abertas = [_pergunta(7, quando=T0)]
    await _rodar(db, canal, integ, cli)

    banida = _pergunta(7, quando=T0, resposta="x")
    banida["answer"] = {"text": "", "status": "BANNED", "date_created": _iso_pergunta(T0)}
    cli.abertas = []
    cli.detalhes["7"] = banida
    for _ in range(3):
        await _rodar(db, canal, integ, cli)

    assert [c for c in cli.chamadas if c[0] == "detalhe"] == [("detalhe", "7")]
    conversa = await _conversa(db, _q(7))
    assert (conversa.situacao, conversa.bloqueio_motivo) == ("fechada", "ANSWER_BANNED")
    assert conversa.aguardando_resposta is False and conversa.prazo_resposta_em is None
    q, a = await _mensagens(db, conversa)
    assert q.payload["status"] == "ANSWERED" and q.payload["resposta_status"] == "BANNED"
    assert (a.externo_id, a.autor, a.status, a.erro) == (
        "a:7",
        "loja",
        "falhou",
        "resposta_banned_ml",
    )
    # O ML não aceita outra resposta: nem tenta.
    r = await ml_atd.enviar_texto(db, conversa, integ, cli, "Oi")
    assert (r.ok, r.erro) == (False, "sem_pergunta_pendente")


async def test_nossa_resposta_derrubada_depois_vira_falhou(
    db: AsyncSession, make_user, relogio
):
    """A `a:<id>` já gravada como enviada muda quando o ML a derruba."""
    canal, integ = await _canal(db, make_user, "pergunta")
    cli = ClienteFalso()
    cli.abertas = [_pergunta(9, quando=T0)]
    await _rodar(db, canal, integ, cli)
    conversa = await _conversa(db, _q(9))
    # O envio pelo DaVinci (lote E) deixou a nossa linha com o id `a:9`.
    nossa = AtendimentoMensagem(
        conversa_id=conversa.id,
        externo_id="a:9",
        autor="loja",
        origem="davinci_humano",
        texto="Temos sim, na cor azul.",
        status="enviada",
        enviada_em=T0 + timedelta(minutes=5),
    )
    db.add(nossa)
    await db.flush()
    await gravar.recalcular_conversa(db, conversa)  # como o envio faz depois de enviar
    await db.commit()
    cli.abertas = []
    cli.respondidas = [
        _pergunta(
            9,
            quando=T0,
            resposta="Temos sim, na cor azul.",
            resposta_em=T0 + timedelta(minutes=5),
        )
    ]
    await _rodar(db, canal, integ, cli)
    conversa = await _conversa(db, _q(9))
    assert conversa.situacao == "respondida"

    # Depois a moderação do ML derruba a resposta publicada.
    derrubada = _pergunta(9, quando=T0, resposta="x")
    derrubada["answer"] = {"text": "", "status": "BANNED", "date_created": _iso_pergunta(T0)}
    cli.respondidas = [derrubada]
    await _rodar(db, canal, integ, cli)

    await db.refresh(nossa)
    assert (nossa.status, nossa.erro, nossa.texto) == (
        "falhou",
        "resposta_banned_ml",
        "Temos sim, na cor azul.",
    )
    conversa = await _conversa(db, _q(9))
    assert (conversa.situacao, conversa.bloqueio_motivo) == ("fechada", "ANSWER_BANNED")
    assert len(await _mensagens(db, conversa)) == 2  # nada duplicado


async def test_titulo_do_anuncio_no_maximo_10_chamadas_por_rodada(
    db: AsyncSession, make_user, relogio
):
    canal, integ = await _canal(db, make_user, "pergunta")
    cli = ClienteFalso()
    cli.abertas = [
        _pergunta(700 + i, quando=T0 + timedelta(minutes=i), item=f"MLB{i:010d}")
        for i in range(12)
    ]
    r = await _rodar(db, canal, integ, cli)

    assert r.conversas_novas == 12
    assert len([c for c in cli.chamadas if c[0] == "item"]) == 10
    sem_titulo = await db.scalar(
        select(func.count())
        .select_from(AtendimentoConversa)
        .where(AtendimentoConversa.anuncio_titulo.is_(None))
    )
    assert sem_titulo == 2  # ficam para a próxima rodada


async def test_abertas_leem_todas_as_paginas_ate_o_teto(
    db: AsyncSession, make_user, relogio, monkeypatch
):
    monkeypatch.setattr(get_settings(), "atendimento_sync_max_conversas", 60)
    canal, integ = await _canal(db, make_user, "pergunta")
    cli = ClienteFalso()
    cli.abertas = [
        _pergunta(1000 + i, quando=T0 - timedelta(minutes=i), de=10_000 + i) for i in range(130)
    ]
    r = await _rodar(db, canal, integ, cli)

    offsets = [c[2] for c in cli.chamadas if c[:2] == ("perguntas", "UNANSWERED")]
    assert offsets == [0, 50]  # 100 lidas ≥ teto de 60: para aí
    assert r.nao_lidas == 130  # o total que o ML diz ter
    assert r.conversas_novas == 100


@pytest.mark.parametrize("canal_nome", ["pergunta", "pos_venda"])
async def test_403_de_politica_vira_sem_escopo(
    db: AsyncSession, make_user, relogio, canal_nome
):
    canal, integ = await _canal(db, make_user, canal_nome)
    cli = ClienteFalso()
    cli.falha_leitura = _erro_http(
        403,
        {
            "code": "PA_UNAUTHORIZED_RESULT_FROM_POLICIES",
            "blocked_by": "PolicyAgent",
            "message": "At least one policy returned UNAUTHORIZED.",
            "status": 403,
        },
    )

    r = await _rodar(db, canal, integ, cli)

    assert r.status == "sem_escopo"
    assert r.status in STATUS_CANAL
    assert r.erro == "HTTP 403 PA_UNAUTHORIZED_RESULT_FROM_POLICIES"
    await db.refresh(canal)
    assert "ultima_rodada" not in canal.cursor  # rodada que falhou não anda o cursor


async def test_timeout_na_leitura_vira_erro(db: AsyncSession, make_user, relogio):
    canal, integ = await _canal(db, make_user, "pos_venda")
    cli = ClienteFalso()
    cli.falha_leitura = httpx.ReadTimeout("timeout")

    r = await _rodar(db, canal, integ, cli)

    assert (r.status, r.erro) == ("erro", "ReadTimeout")


# ─────────────── pós-venda ───────────────


async def test_pack_com_mensagens_das_duas_pontas(db: AsyncSession, make_user, relogio):
    canal, integ = await _canal(db, make_user, "pos_venda")
    cli = ClienteFalso()
    cli.nao_lidos = {PACK: 1}
    # O ML pode devolver fora de ordem: o adaptador ordena pela data.
    cli.packs[PACK] = {
        "messages": [
            _do_cliente(
                "c2", "Obrigado, e a nota fiscal?", T0 + timedelta(minutes=20), pedido=PEDIDO
            ),
            _do_cliente("c1", "Quando chega meu pedido?", T0, pedido=PEDIDO),
            _da_loja("l1", "Já foi enviado!", T0 + timedelta(minutes=10), pedido=PEDIDO),
        ]
    }

    r = await _rodar(db, canal, integ, cli)

    assert (r.status, r.erro) == ("ok", None)
    assert (r.conversas_novas, r.mensagens_novas, r.nao_lidas) == (1, 3, 1)
    conversa = await _conversa(db, PACK)
    assert conversa.canal == "pos_venda"
    assert conversa.comprador_id == str(COMPRADOR)
    assert conversa.pedido_marketplace == PEDIDO  # order_id do message_resources
    assert conversa.dados["pack_id"] == PACK
    assert conversa.dados["order_id"] == PEDIDO
    assert conversa.dados["limite"] == 350  # seller_max_message_length
    assert conversa.nao_lidas == 1
    assert conversa.aguardando_resposta is True
    assert conversa.prazo_resposta_em == T0 + timedelta(minutes=20, hours=24)
    msgs = await _mensagens(db, conversa)
    assert [(m.externo_id, m.autor, m.origem) for m in msgs] == [
        ("c1", "cliente", "cliente"),
        ("l1", "loja", "externo"),
        ("c2", "cliente", "cliente"),
    ]
    assert msgs[0].enviada_em == T0
    assert "moderacao" not in msgs[0].payload  # moderação "clean" não polui o payload
    assert cli.packs_lidos() == [PACK]


async def test_pedido_sem_order_id_usa_o_pack(db: AsyncSession, make_user, relogio):
    canal, integ = await _canal(db, make_user, "pos_venda")
    cli = ClienteFalso()
    cli.nao_lidos = {PACK: 1}
    # O formato real: o pedido não aparece nas mensagens.
    cli.packs[PACK] = {"messages": [_do_cliente("c1", "Oi", T0)]}

    await _rodar(db, canal, integ, cli)

    conversa = await _conversa(db, PACK)
    assert conversa.pedido_marketplace == PACK
    assert "order_id" not in conversa.dados


@pytest.mark.parametrize("substatus", ["blocked_by_time", "blocked_by_cancelled_order"])
async def test_bloqueio_do_ml_e_a_volta_para_ativa(
    db: AsyncSession, make_user, relogio, substatus
):
    canal, integ = await _canal(db, make_user, "pos_venda")
    cli = ClienteFalso()
    cli.nao_lidos = {PACK: 1}
    cli.packs[PACK] = {
        "status": _status_conversa("blocked", substatus),
        "messages": [_do_cliente("c1", "Cadê?", T0)],
    }
    await _rodar(db, canal, integ, cli)

    conversa = await _conversa(db, PACK)
    assert conversa.situacao == "bloqueada"
    assert conversa.bloqueio_motivo == substatus
    assert conversa.aguardando_resposta is True  # bloqueada continua pedindo ação
    assert conversa.dados["status_ml"] == "blocked"

    cli.packs[PACK]["status"] = _status_conversa("active")
    r = await _rodar(db, canal, integ, cli)

    conversa = await _conversa(db, PACK)
    assert conversa.situacao == "aberta"
    assert conversa.bloqueio_motivo is None
    assert r.conversas_atualizadas == 1


async def test_bloqueio_nao_devolve_a_fila_a_conversa_fechada_pela_pessoa(
    db: AsyncSession, make_user, relogio
):
    canal, integ = await _canal(db, make_user, "pos_venda")
    cli = ClienteFalso()
    cli.nao_lidos = {PACK: 1}
    cli.packs[PACK] = {"messages": [_do_cliente("c1", "Cadê?", T0)]}
    await _rodar(db, canal, integ, cli)
    conversa = await _conversa(db, PACK)
    conversa.situacao = "fechada"
    conversa.aguardando_resposta = False
    await db.commit()

    cli.packs[PACK]["status"] = _status_conversa("blocked", "blocked_by_time")
    await _rodar(db, canal, integ, cli)

    conversa = await _conversa(db, PACK)
    assert conversa.situacao == "fechada"


async def test_releitura_pega_a_resposta_dada_no_duoke(db: AsyncSession, make_user, relogio):
    canal, integ = await _canal(db, make_user, "pos_venda")
    cli = ClienteFalso()
    cli.nao_lidos = {PACK: 1}
    cli.packs[PACK] = {"messages": [_do_cliente("c1", "Cadê meu pedido?", T0)]}
    await _rodar(db, canal, integ, cli)

    # A equipe leu e respondeu no Duoke: some dos não lidos, e nenhum "não lido" novo.
    cli.nao_lidos = {}
    cli.packs[PACK]["messages"].append(_da_loja("l1", "Chega amanhã.", T0 + timedelta(minutes=40)))
    relogio["agora"] += timedelta(minutes=10)
    cli.chamadas.clear()
    r = await _rodar(db, canal, integ, cli)

    assert cli.packs_lidos() == [PACK]
    assert (r.conversas_atualizadas, r.mensagens_novas) == (1, 1)
    conversa = await _conversa(db, PACK)
    assert conversa.aguardando_resposta is False
    assert conversa.situacao == "respondida"
    assert conversa.nao_lidas == 0
    assert [m.origem for m in await _mensagens(db, conversa)] == ["cliente", "externo"]


async def test_releitura_respeita_5_min_e_teto_de_20_e_roda_todas(
    db: AsyncSession, make_user, relogio
):
    canal, integ = await _canal(db, make_user, "pos_venda")
    cli = ClienteFalso()
    packs = [f"20000000000{i:05d}" for i in range(25)]
    for i, p in enumerate(packs):
        cli.nao_lidos[p] = 1
        cli.packs[p] = {"messages": [_do_cliente(f"c{i}", "Oi", T0 + timedelta(seconds=i))]}
    await _rodar(db, canal, integ, cli)  # agora = T0+30min

    # 3 min depois: ninguém espera há mais de 5 min SEM releitura → nada relido.
    cli.nao_lidos = {}
    cli.chamadas.clear()
    relogio["agora"] += timedelta(minutes=3)
    await _rodar(db, canal, integ, cli)
    assert cli.packs_lidos() == []

    # Mais tarde: relê no máximo 20...
    relogio["agora"] += timedelta(minutes=10)
    cli.chamadas.clear()
    await _rodar(db, canal, integ, cli)
    primeira_leva = cli.packs_lidos()
    assert len(primeira_leva) == 20

    # ...e na rodada seguinte os que ficaram de fora vêm primeiro.
    relogio["agora"] += timedelta(minutes=6)
    cli.chamadas.clear()
    await _rodar(db, canal, integ, cli)
    segunda_leva = cli.packs_lidos()
    faltavam = set(packs) - set(primeira_leva)
    assert len(faltavam) == 5
    assert faltavam <= set(segunda_leva)


async def test_nao_lido_nao_e_relido_de_novo_na_mesma_rodada(
    db: AsyncSession, make_user, relogio
):
    canal, integ = await _canal(db, make_user, "pos_venda")
    cli = ClienteFalso()
    cli.nao_lidos = {PACK: 1}
    cli.packs[PACK] = {"messages": [_do_cliente("c1", "Oi", T0)]}
    await _rodar(db, canal, integ, cli)

    relogio["agora"] += timedelta(minutes=10)
    cli.nao_lidos = {PACK: 2}
    cli.packs[PACK]["messages"].append(_do_cliente("c2", "Alô?", T0 + timedelta(minutes=35)))
    cli.chamadas.clear()
    r = await _rodar(db, canal, integ, cli)

    assert cli.packs_lidos() == [PACK]  # uma vez, como não lido
    assert r.mensagens_novas == 1
    assert (await _conversa(db, PACK)).nao_lidas == 2


async def test_pos_venda_rodar_duas_vezes_nao_duplica(db: AsyncSession, make_user, relogio):
    canal, integ = await _canal(db, make_user, "pos_venda")
    cli = ClienteFalso()
    cli.nao_lidos = {PACK: 1}
    cli.packs[PACK] = {
        "messages": [_do_cliente("c1", "Oi", T0), _da_loja("l1", "Olá!", T0 + timedelta(minutes=1))]
    }
    await _rodar(db, canal, integ, cli)
    segunda = await _rodar(db, canal, integ, cli)

    assert (segunda.conversas_novas, segunda.conversas_atualizadas, segunda.mensagens_novas) == (
        0,
        0,
        0,
    )
    assert await _total_mensagens(db) == 2


async def test_com_mais_nao_lidos_que_o_teto_o_que_mudou_vai_na_frente(
    db: AsyncSession, make_user, relogio, monkeypatch
):
    canal, integ = await _canal(db, make_user, "pos_venda")
    cli = ClienteFalso()
    a, b, c, d = (f"200000000000000{i}" for i in range(4))
    for i, p in enumerate((a, b, c, d)):
        cli.packs[p] = {"messages": [_do_cliente(f"c{i}", "Oi", T0)]}
    cli.nao_lidos = {a: 1, b: 1, c: 1}
    await _rodar(db, canal, integ, cli)

    monkeypatch.setattr(get_settings(), "atendimento_sync_max_conversas", 2)
    cli.nao_lidos = {a: 1, b: 2, d: 1}  # a igual, b com mensagem nova, d novo
    cli.chamadas.clear()
    await _rodar(db, canal, integ, cli)

    assert sorted(cli.packs_lidos()) == sorted([b, d])


async def test_anexo_sem_texto(db: AsyncSession, make_user, relogio):
    canal, integ = await _canal(db, make_user, "pos_venda")
    cli = ClienteFalso()
    foto = _do_cliente("c1", "", T0)
    foto["text"] = ""
    foto["message_attachments"] = [
        {
            "filename": "123_abc.jpg",
            "original_filename": "foto.jpg",
            "type": "image/jpeg",
            "size": 1234,
            "date_created": _iso_pack(T0),
        }
    ]
    cli.nao_lidos = {PACK: 1}
    cli.packs[PACK] = {"messages": [foto]}

    await _rodar(db, canal, integ, cli)

    [msg] = await _mensagens(db, await _conversa(db, PACK))
    assert (msg.tipo, msg.texto) == ("imagem", None)
    assert msg.anexos == [
        {"arquivo": "123_abc.jpg", "nome": "foto.jpg", "tipo": "image/jpeg", "tamanho": 1234}
    ]


async def test_paginas_do_pack(db: AsyncSession, make_user, relogio):
    canal, integ = await _canal(db, make_user, "pos_venda")
    cli = ClienteFalso()
    cli.nao_lidos = {PACK: 1}
    cli.packs[PACK] = {
        "messages": [_do_cliente(f"c{i}", f"msg {i}", T0 + timedelta(minutes=i)) for i in range(25)]
    }

    r = await _rodar(db, canal, integ, cli)

    assert r.mensagens_novas == 25
    assert [c[3] for c in cli.chamadas if c[0] == "pack"] == [0, 20]


async def test_a_nossa_mensagem_voltando_e_adotada(db: AsyncSession, make_user, relogio):
    canal, integ = await _canal(db, make_user, "pos_venda")
    cli = ClienteFalso()
    cli.nao_lidos = {PACK: 1}
    cli.packs[PACK] = {"messages": [_do_cliente("c1", "Cadê?", T0)]}
    await _rodar(db, canal, integ, cli)
    conversa = await _conversa(db, PACK)
    # Envio ambíguo pelo DaVinci (timeout): ficou em `revisar`, sem id do ML.
    nossa = AtendimentoMensagem(
        conversa_id=conversa.id,
        autor="loja",
        origem="davinci_humano",
        texto="Olá! Seu pedido já foi enviado.",
        status="revisar",
        enviada_em=T0 + timedelta(minutes=30),
    )
    db.add(nossa)
    await db.commit()

    cli.packs[PACK]["messages"].append(
        _da_loja("l9", "Olá! Seu pedido já foi enviado.", T0 + timedelta(minutes=31))
    )
    r = await _rodar(db, canal, integ, cli)

    assert r.mensagens_novas == 0  # adoção não é mensagem nova
    msgs = await _mensagens(db, conversa)
    assert len(msgs) == 2
    await db.refresh(nossa)
    assert (nossa.externo_id, nossa.status, nossa.origem) == ("l9", "enviada", "davinci_humano")


async def test_mensagem_da_loja_barrada_pela_moderacao_nao_conta_como_resposta(
    db: AsyncSession, make_user, relogio
):
    canal, integ = await _canal(db, make_user, "pos_venda")
    cli = ClienteFalso()
    cli.nao_lidos = {PACK: 1}
    cli.packs[PACK] = {
        "messages": [
            _do_cliente("c1", "Cadê?", T0),
            _da_loja("l1", "Me chama no zap", T0 + timedelta(minutes=5), moderacao="rejected"),
        ]
    }

    await _rodar(db, canal, integ, cli)

    conversa = await _conversa(db, PACK)
    assert conversa.aguardando_resposta is True  # o comprador não recebeu nada
    loja = [m for m in await _mensagens(db, conversa) if m.autor == "loja"][0]
    assert (loja.status, loja.erro) == ("falhou", "moderada_ml")
    assert loja.payload["moderacao"]["status"] == "rejected"


async def test_pack_com_erro_nao_derruba_a_rodada(db: AsyncSession, make_user, relogio):
    canal, integ = await _canal(db, make_user, "pos_venda")
    cli = ClienteFalso()
    cli.nao_lidos = {PACK: 1, "2000000000000404": 1}
    cli.packs[PACK] = {"messages": [_do_cliente("c1", "Oi", T0)]}
    cli.packs["2000000000000404"] = _erro_http(404, {"error": "not_found", "status": 404})

    r = await _rodar(db, canal, integ, cli)

    assert r.status == "ok"
    assert r.conversas_novas == 1
    assert r.erro == "1 de 2 conversas com erro: HTTP 404 not_found"


async def test_um_pack_com_403_nao_tira_a_conta_da_leitura(db: AsyncSession, make_user, relogio):
    """API-15: a lista de não lidos respondeu, então o canal TEM permissão.

    Um 403 num pack só (pedido de outra conta, recurso restrito) virava
    `sem_escopo` — e o sync só volta a tentar canal sem escopo de hora em hora.
    """
    canal, integ = await _canal(db, make_user, "pos_venda")
    cli = ClienteFalso()
    cli.nao_lidos = {PACK: 1}
    cli.packs[PACK] = _erro_http(
        403, {"message": "User access token invalid for resource", "error": "forbidden"}
    )

    r = await _rodar(db, canal, integ, cli)

    assert r.status == "erro"
    assert r.erro == "1 de 1 conversas com erro: HTTP 403 forbidden"


async def test_pack_lido_no_duoke_antes_da_rodada_entra_pela_varredura(
    db: AsyncSession, make_user, relogio
):
    """API-1 (a): o comprador escreve e a equipe abre no Duoke antes da rodada.

    O pack já saiu de /messages/unread — sem a varredura dos pedidos
    recentes, a conversa nunca seria criada (nem SLA, nem fila, nem métrica).
    """
    canal, integ = await _canal(db, make_user, "pos_venda")
    cli = ClienteFalso()
    vazio = "2000000000000777"
    cli.pedidos = [
        {"id": int(PEDIDO), "pack_id": int(PACK), "status": "paid"},
        {"id": int(vazio), "pack_id": None, "status": "paid"},  # pedido sem conversa
    ]
    cli.packs[PACK] = {"messages": [_do_cliente("c1", "Cadê meu pedido?", T0)]}
    cli.packs[vazio] = {"messages": []}

    r = await _rodar(db, canal, integ, cli)

    assert (r.status, r.erro, r.conversas_novas, r.mensagens_novas) == ("ok", None, 1, 1)
    assert sorted(cli.packs_lidos()) == sorted([PACK, vazio])
    conversa = await _conversa(db, PACK)
    assert conversa.aguardando_resposta is True
    assert conversa.prazo_resposta_em == T0 + timedelta(hours=24)
    assert conversa.nao_lidas == 0
    # Pedido sem mensagem nenhuma não vira conversa vazia na lista.
    assert await db.scalar(select(func.count()).select_from(AtendimentoConversa)) == 1

    # Relido há menos de 30 min: a varredura não relê o mesmo pack.
    relogio["agora"] += timedelta(minutes=2)
    cli.chamadas.clear()
    await _rodar(db, canal, integ, cli)
    assert PACK not in cli.packs_lidos()


async def test_varredura_gira_pelas_paginas_dos_pedidos(
    db: AsyncSession, make_user, relogio, monkeypatch
):
    monkeypatch.setattr(ml_atd, "VARREDURA_PAGINA", 2)
    canal, integ = await _canal(db, make_user, "pos_venda")
    cli = ClienteFalso()
    packs = [f"20000000000{i:05d}" for i in range(5)]
    cli.pedidos = [{"id": int(p), "pack_id": None} for p in packs]
    for p in packs:
        cli.packs[p] = {"messages": []}

    offsets = []
    for _ in range(4):
        cli.chamadas.clear()
        await _rodar(db, canal, integ, cli)
        offsets.append([c[1] for c in cli.chamadas if c[0] == "pedidos"])
    assert offsets == [[0], [2], [4], [0]]  # 5 pedidos, 2 por rodada, e volta ao começo


async def test_varredura_que_falha_nao_para_a_leitura(db: AsyncSession, make_user, relogio):
    canal, integ = await _canal(db, make_user, "pos_venda")
    cli = ClienteFalso()
    cli.nao_lidos = {PACK: 1}
    cli.packs[PACK] = {"messages": [_do_cliente("c1", "Oi", T0)]}
    cli.falha_pedidos = _erro_http(403, {"error": "forbidden"})

    r = await _rodar(db, canal, integ, cli)

    assert (r.status, r.conversas_novas) == ("ok", 1)
    assert r.erro == "varredura de pedidos: HTTP 403 forbidden"


async def test_comprador_volta_a_escrever_em_conversa_respondida_lida_no_duoke(
    db: AsyncSession, make_user, relogio
):
    """API-1 (b): conversa já respondida, o comprador escreve de novo e alguém
    lê no Duoke antes da rodada. Não é não lido nem está aguardando: só a
    releitura das conversas com movimento recente acha a mensagem nova."""
    canal, integ = await _canal(db, make_user, "pos_venda")
    cli = ClienteFalso()
    cli.nao_lidos = {PACK: 1}
    cli.packs[PACK] = {
        "messages": [
            _do_cliente("m1", "Cadê?", T0),
            _da_loja("m2", "Chega amanhã.", T0 + timedelta(minutes=10)),
        ]
    }
    await _rodar(db, canal, integ, cli)
    assert (await _conversa(db, PACK)).aguardando_resposta is False

    cli.nao_lidos = {}
    cli.packs[PACK]["messages"].append(_do_cliente("m3", "Não chegou!", T0 + timedelta(minutes=35)))
    # Antes de 15 min da última leitura: ainda não relê.
    relogio["agora"] += timedelta(minutes=10)
    cli.chamadas.clear()
    await _rodar(db, canal, integ, cli)
    assert cli.packs_lidos() == []

    relogio["agora"] += timedelta(minutes=10)
    cli.chamadas.clear()
    r = await _rodar(db, canal, integ, cli)

    assert cli.packs_lidos() == [PACK]
    assert r.mensagens_novas == 1
    conversa = await _conversa(db, PACK)
    assert [m.externo_id for m in await _mensagens(db, conversa)] == ["m1", "m2", "m3"]
    assert conversa.aguardando_resposta is True
    assert conversa.prazo_resposta_em == T0 + timedelta(minutes=35, hours=24)


@pytest.mark.parametrize(
    ("moderacao", "status_msg"),
    [
        ("REJECTED", "available"),  # a caixa do exemplo antigo da doc
        ("rejected", "available"),
        ("clean", "rejected"),  # o `status` da própria mensagem também diz
        ("clean", "moderated"),
    ],
)
async def test_moderacao_da_loja_em_qualquer_caixa_nao_conta_como_resposta(
    db: AsyncSession, make_user, relogio, moderacao, status_msg
):
    """API-4: a comparação era exata com "rejected" e ignorava `messages[].status`."""
    canal, integ = await _canal(db, make_user, "pos_venda")
    cli = ClienteFalso()
    barrada = _da_loja("l1", "Me chama no zap", T0 + timedelta(minutes=5), moderacao=moderacao)
    barrada["status"] = status_msg
    cli.nao_lidos = {PACK: 1}
    cli.packs[PACK] = {"messages": [_do_cliente("c1", "Cadê?", T0), barrada]}

    await _rodar(db, canal, integ, cli)

    conversa = await _conversa(db, PACK)
    assert conversa.aguardando_resposta is True
    loja = [m for m in await _mensagens(db, conversa) if m.autor == "loja"][0]
    assert (loja.status, loja.erro) == ("falhou", "moderada_ml")


async def test_nossa_mensagem_moderada_depois_do_envio_e_vista_na_releitura(
    db: AsyncSession, make_user, relogio
):
    """API-3: o POST respondeu 2xx (moderação ainda pendente) e a conversa saiu
    da fila. Sem movimento do comprador o pack não era relido e a barreira da
    moderação nunca rodava para a NOSSA mensagem."""
    canal, integ = await _canal(db, make_user, "pos_venda")
    cli = ClienteFalso()
    cli.nao_lidos = {PACK: 1}
    cli.packs[PACK] = {"messages": [_do_cliente("c1", "Cadê?", T0)]}
    await _rodar(db, canal, integ, cli)
    conversa = await _conversa(db, PACK)
    nossa = AtendimentoMensagem(
        conversa_id=conversa.id,
        externo_id="m-nossa",
        autor="loja",
        origem="davinci_humano",
        texto="Chama no zap",
        status="enviada",
        enviada_em=T0 + timedelta(minutes=31),
    )
    db.add(nossa)
    await db.flush()
    await gravar.recalcular_conversa(db, conversa)
    await db.commit()
    assert conversa.aguardando_resposta is False

    # O ML moderou depois; ninguém escreveu, nada de não lido.
    cli.nao_lidos = {}
    cli.packs[PACK]["messages"].append(
        _da_loja("m-nossa", "Chama no zap", T0 + timedelta(minutes=31), moderacao="rejected")
    )
    relogio["agora"] += timedelta(minutes=20)
    await _rodar(db, canal, integ, cli)

    await db.refresh(nossa)
    assert (nossa.status, nossa.erro) == ("falhou", "moderada_ml")
    conversa = await _conversa(db, PACK)
    assert conversa.aguardando_resposta is True
    assert len(await _mensagens(db, conversa)) == 2


async def test_reclamacao_encerrada_sai_dos_dados_da_conversa(
    db: AsyncSession, make_user, relogio
):
    """COMPL-4 (lado do adaptador): `claim_ids` era gravado só quando havia
    reclamação — a encerrada ficava para sempre nos dados."""
    canal, integ = await _canal(db, make_user, "pos_venda")
    cli = ClienteFalso()
    com_claim = _status_conversa()
    com_claim["claim_ids"] = [5301234567]
    cli.nao_lidos = {PACK: 1}
    cli.packs[PACK] = {"status": com_claim, "messages": [_do_cliente("c1", "Oi", T0)]}
    await _rodar(db, canal, integ, cli)
    assert (await _conversa(db, PACK)).dados["claim_ids"] == ["5301234567"]

    cli.packs[PACK]["status"] = _status_conversa()  # claim_ids: []
    await _rodar(db, canal, integ, cli)
    assert (await _conversa(db, PACK)).dados["claim_ids"] == []


# ─────────────── envio ───────────────


async def _conversas_de_perguntas(db, make_user, relogio, perguntas: list[dict]):
    canal, integ = await _canal(db, make_user, "pergunta")
    cli = ClienteFalso()
    cli.abertas = [q for q in perguntas if q["status"] == "UNANSWERED"]
    cli.respondidas = [q for q in perguntas if q["status"] == "ANSWERED"]
    await _rodar(db, canal, integ, cli)
    return canal, integ, cli


async def test_envio_de_pergunta_responde_exatamente_a_pergunta_da_conversa(
    db: AsyncSession, make_user, relogio
):
    """D3: a conversa É a pergunta. Nada de "a pendente mais nova": responder
    na conversa da 802 responde a 802, mesmo com a 803 mais nova aberta."""
    _, integ, cli = await _conversas_de_perguntas(
        db,
        make_user,
        relogio,
        [
            _pergunta(801, quando=T0, resposta="Sim"),
            _pergunta(802, quando=T0 + timedelta(minutes=20)),
            _pergunta(803, quando=T0 + timedelta(minutes=25)),
        ],
    )
    cli.post = _resposta(200, {"id": 802, "status": "ANSWERED", "text": "pergunta"})

    r = await ml_atd.enviar_texto(db, await _conversa(db, "q:802"), integ, cli, "Temos em estoque.")

    assert r.ok is True
    assert r.externo_id == "a:802"
    assert r.payload == {"http": 200, "id": 802, "status": "ANSWERED"}  # sem texto de volta
    assert [c for c in cli.chamadas if c[0] == "responder"] == [
        ("responder", "802", "Temos em estoque.")
    ]


async def test_duas_perguntas_do_mesmo_comprador_responder_uma_nao_tira_a_outra_da_fila(
    db: AsyncSession, make_user, relogio, monkeypatch
):
    """API-2 / COMPL-1, ponta a ponta com o envio de verdade (`enviar_resposta`).

    Antes, as duas eram uma conversa só: a resposta à mais nova a deixava
    "respondida" e a outra ficava sem resposta no ML, fora da fila, do prazo
    e da IA.
    """
    from app.services.atendimento import clientes, enviar

    s = get_settings()
    monkeypatch.setattr(s, "atendimento_envio_ativo", True)
    monkeypatch.setattr(s, "atendimento_simulador", False)
    canal, integ, cli = await _conversas_de_perguntas(
        db,
        make_user,
        relogio,
        [
            _pergunta(802, quando=T0 + timedelta(minutes=20), texto="Tem azul?"),
            _pergunta(803, quando=T0 + timedelta(minutes=25), texto="E preto?"),
        ],
    )
    canal.modo = "humano"
    await db.commit()

    async def cliente_falso(integration):
        return cli

    monkeypatch.setattr(clientes, "cliente_da_integracao", cliente_falso)
    cli.post = _resposta(200, {"id": 803, "status": "ANSWERED"})

    nossa = await enviar.enviar_resposta(
        db, await _conversa(db, "q:803"), "Temos preto sim.", user=None
    )

    assert (nossa.status, nossa.externo_id) == ("enviada", "a:803")
    assert [c for c in cli.chamadas if c[0] == "responder"] == [
        ("responder", "803", "Temos preto sim.")
    ]
    respondida = await _conversa(db, "q:803")
    assert (respondida.aguardando_resposta, respondida.situacao) == (False, "respondida")
    outra = await _conversa(db, "q:802")
    assert outra.aguardando_resposta is True
    assert outra.prazo_resposta_em == T0 + timedelta(minutes=20, hours=1)

    # A rodada seguinte: o ML dá a 803 por respondida e a 802 segue aberta.
    cli.abertas = [_pergunta(802, quando=T0 + timedelta(minutes=20), texto="Tem azul?")]
    cli.respondidas = [
        _pergunta(
            803,
            quando=T0 + timedelta(minutes=25),
            texto="E preto?",
            resposta="Temos preto sim.",
            resposta_em=T0 + timedelta(minutes=31),
        )
    ]
    await _rodar(db, canal, integ, cli)
    outra = await _conversa(db, "q:802")
    assert (outra.aguardando_resposta, outra.situacao) == (True, "aberta")
    respondida = await _conversa(db, "q:803")
    assert respondida.dados["status_ml"] == "ANSWERED"
    assert len(await _mensagens(db, respondida)) == 2  # a nossa adotada, sem duplicar


async def test_envio_de_pergunta_sem_pendente(db: AsyncSession, make_user, relogio):
    _, integ, cli = await _conversas_de_perguntas(
        db, make_user, relogio, [_pergunta(811, quando=T0, resposta="Sim")]
    )

    r = await ml_atd.enviar_texto(db, await _conversa(db, "q:811"), integ, cli, "Oi")

    assert (r.ok, r.ambiguo, r.erro) == (False, False, "sem_pergunta_pendente")
    assert not [c for c in cli.chamadas if c[0] == "responder"]


async def test_envio_de_pergunta_recusado_4xx(db: AsyncSession, make_user, relogio):
    _, integ, cli = await _conversas_de_perguntas(
        db, make_user, relogio, [_pergunta(821, quando=T0)]
    )
    cli.post = _resposta(400, {"error": "question_closed", "message": "x", "status": 400})

    r = await ml_atd.enviar_texto(db, await _conversa(db, "q:821"), integ, cli, "Oi")

    assert (r.ok, r.ambiguo, r.bloqueio) == (False, False, None)
    assert r.erro == "HTTP 400 question_closed"


async def _conversa_pos_venda(db, make_user, relogio):
    canal, integ = await _canal(db, make_user, "pos_venda")
    cli = ClienteFalso()
    cli.nao_lidos = {PACK: 1}
    cli.packs[PACK] = {"messages": [_do_cliente("c1", "Cadê?", T0)]}
    await _rodar(db, canal, integ, cli)
    cli.chamadas.clear()
    return await _conversa(db, PACK), integ, cli


async def test_envio_pos_venda_ok(db: AsyncSession, make_user, relogio):
    conversa, integ, cli = await _conversa_pos_venda(db, make_user, relogio)
    cli.post = _resposta(
        201,
        {
            "id": "0f1e2d3c4b5a69788796a5b4c3d2e1f0",
            "status": "available",
            "text": "Chega amanhã",
            "message_moderation": {"status": "clean"},
        },
    )

    r = await ml_atd.enviar_texto(db, conversa, integ, cli, "Chega amanhã")

    assert (r.ok, r.externo_id) == (True, "0f1e2d3c4b5a69788796a5b4c3d2e1f0")
    assert cli.chamadas == [("enviar_pack", PACK, str(SELLER), str(COMPRADOR), "Chega amanhã")]


async def test_envio_pos_venda_barrado_pela_moderacao_na_resposta_e_falha(
    db: AsyncSession, make_user, relogio, monkeypatch
):
    """API-3: a moderação do pós-venda é decidida na criação ("online"). O POST
    respondia 2xx com `message_moderation.status=rejected` e a linha virava
    `enviada` — a conversa saía da fila com o comprador sem resposta.

    Ponta a ponta: o envio de verdade grava `falhou`, e quando a leitura traz
    a mensagem moderada ela casa com a NOSSA linha (pelo id do POST), sem
    virar uma segunda linha "externo".
    """
    from app.services.atendimento import clientes, enviar

    s = get_settings()
    monkeypatch.setattr(s, "atendimento_envio_ativo", True)
    monkeypatch.setattr(s, "atendimento_simulador", False)
    conversa, integ, cli = await _conversa_pos_venda(db, make_user, relogio)
    canal = await db.get(AtendimentoCanal, conversa.canal_id)
    canal.modo = "humano"
    await db.commit()

    async def cliente_falso(integration):
        return cli

    monkeypatch.setattr(clientes, "cliente_da_integracao", cliente_falso)
    cli.post = _resposta(
        201,
        {
            "id": "m-mod",
            "status": "available",
            "text": "Segue o rastreio do seu pedido.",
            "message_moderation": {"status": "rejected", "reason": "PERSONAL_DATA"},
        },
    )

    r = await ml_atd.enviar_texto(db, conversa, integ, cli, "Segue o rastreio do seu pedido.")
    assert (r.ok, r.ambiguo, r.erro, r.externo_id) == (False, False, "moderada_ml", None)
    assert r.payload["id"] == "m-mod"
    assert r.payload["moderacao"] == {"status": "rejected", "reason": "PERSONAL_DATA"}

    nossa = await enviar.enviar_resposta(db, conversa, "Segue o rastreio do seu pedido.", user=None)
    assert (nossa.status, nossa.erro) == ("falhou", "moderada_ml")
    conversa = await _conversa(db, PACK)
    assert conversa.aguardando_resposta is True  # o comprador não recebeu nada

    # A leitura traz a mensagem moderada: é a nossa, não uma resposta "por fora".
    cli.packs[PACK]["messages"].append(
        _da_loja(
            "m-mod",
            "Segue o rastreio do seu pedido.",
            datetime.now(UTC).replace(microsecond=0),
            moderacao="rejected",
        )
    )
    await _rodar(db, canal, integ, cli)
    loja = [m for m in await _mensagens(db, conversa) if m.autor == "loja"]
    assert [(m.externo_id, m.origem, m.status) for m in loja] == [
        ("m-mod", "davinci_humano", "falhou")
    ]
    assert (await _conversa(db, PACK)).aguardando_resposta is True


async def test_envio_pos_venda_em_conversa_bloqueada(db: AsyncSession, make_user, relogio):
    conversa, integ, cli = await _conversa_pos_venda(db, make_user, relogio)
    cli.post = _resposta(
        403,
        {
            "message": "The conversation is blocked",
            "error": "blocked_conversation_send_message_forbidden",
            "status": 403,
            "cause": [{"code": "blocked_by_time", "message": "blocked"}],
        },
    )

    r = await ml_atd.enviar_texto(db, conversa, integ, cli, "Oi")

    assert (r.ok, r.ambiguo) == (False, False)
    assert r.bloqueio == "blocked_by_time"
    assert r.erro == "HTTP 403 blocked_conversation_send_message_forbidden"


@pytest.mark.parametrize(
    ("falha", "ambiguo"),
    [
        (_resposta(500, {"error": "internal_error"}), True),
        (_resposta(503, {}), True),
        (httpx.ReadTimeout("timeout"), True),
        (httpx.RemoteProtocolError("caiu"), True),
        (httpx.ConnectError("sem rede"), False),  # nem chegou ao ML: recusa limpa
        (RuntimeError("ml_refresh_failed status=400"), False),  # token, antes do envio
    ],
)
async def test_envio_ambiguo_so_quando_pode_ter_saido(
    db: AsyncSession, make_user, relogio, falha, ambiguo
):
    conversa, integ, cli = await _conversa_pos_venda(db, make_user, relogio)
    cli.post = falha

    r = await ml_atd.enviar_texto(db, conversa, integ, cli, "Oi")

    assert r.ok is False
    assert r.ambiguo is ambiguo
    assert r.erro


async def test_envio_pos_venda_sem_comprador_nem_vendedor(db: AsyncSession, make_user, relogio):
    conversa, integ, cli = await _conversa_pos_venda(db, make_user, relogio)
    cli.creds = {}
    r = await ml_atd.enviar_texto(db, conversa, integ, cli, "Oi")
    assert (r.ok, r.erro) == (False, "sem_seller_id")

    cli.creds = {"user_id": SELLER}
    conversa.comprador_id = None
    r = await ml_atd.enviar_texto(db, conversa, integ, cli, "Oi")
    assert (r.ok, r.erro) == (False, "sem_comprador")
    assert not cli.chamadas
    await db.rollback()  # a alteração era só para o teste


# ─────────────── o cliente de verdade, no nível do HTTP ───────────────


def _transporte(monkeypatch, handler) -> list[httpx.Request]:
    """Troca a rede do httpx por um handler; devolve a lista de pedidos feitos."""
    feitos: list[httpx.Request] = []

    def registrar(request: httpx.Request) -> httpx.Response:
        feitos.append(request)
        return handler(request)

    def fabrica(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(registrar)
        return _AsyncClientReal(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fabrica)
    return feitos


def _cliente_real() -> MercadoLivreClient:
    return MercadoLivreClient({"access_token": "tok", "refresh_token": "rt", "user_id": SELLER})


PACK_SEM_CONVERSA = "2000000000000999"


def _api_ml_falsa(request: httpx.Request) -> httpx.Response:
    caminho = request.url.path
    if caminho == "/messages/unread":
        return httpx.Response(
            200,
            json={
                "user_id": SELLER,
                "total": 1,
                "results": [{"resource": f"/packs/{PACK}/sellers/{SELLER}", "count": 1}],
            },
        )
    if caminho == "/orders/search":
        # A varredura: um pedido cujo pack não tem conversa nenhuma.
        return httpx.Response(
            200,
            json={
                "results": [{"id": int(PACK_SEM_CONVERSA), "pack_id": None}],
                "paging": {"total": 1, "offset": 0, "limit": 10},
            },
        )
    if caminho.startswith("/messages/packs/") and request.method == "GET":
        vazio = PACK_SEM_CONVERSA in caminho
        return httpx.Response(
            200,
            json={
                "paging": {"limit": 20, "offset": 0, "total": 0 if vazio else 1},
                "conversation_status": _status_conversa(),
                "messages": [] if vazio else [_do_cliente("c1", "Oi", T0)],
                "seller_max_message_length": 350,
                "buyer_max_message_length": 3500,
            },
        )
    if caminho.startswith("/messages/packs/") and request.method == "POST":
        return httpx.Response(201, json={"id": "m-nova", "status": "available"})
    return httpx.Response(404, json={"error": "not_found"})


async def test_nenhum_get_de_pack_sai_sem_mark_as_read_false(
    db: AsyncSession, make_user, relogio, monkeypatch
):
    """O Duoke continua ligado: ler pelo DaVinci NUNCA pode marcar como lido.

    Roda a rodada inteira (não lidos + releitura + varredura dos pedidos) e o
    envio com o cliente de VERDADE, e confere cada pedido que saiu para o ML.
    """
    feitos = _transporte(monkeypatch, _api_ml_falsa)
    canal, integ = await _canal(db, make_user, "pos_venda")
    cliente = _cliente_real()

    await _rodar(db, canal, integ, cliente)
    relogio["agora"] += timedelta(minutes=30)
    await _rodar(db, canal, integ, cliente)  # o pack continua não lido e é relido
    await cliente.mensagens_do_pack(PACK, SELLER, offset=20, limit=5)  # chamada direta
    conversa = await _conversa(db, PACK)
    r = await ml_atd.enviar_texto(db, conversa, integ, cliente, "Olá!")
    assert r.ok and r.externo_id == "m-nova"

    gets_de_pack = [
        p for p in feitos if p.method == "GET" and p.url.path.startswith("/messages/packs/")
    ]
    # 2 rodadas × (o não lido + o pack da varredura) + a chamada direta.
    assert len(gets_de_pack) == 5
    assert sum(PACK_SEM_CONVERSA in p.url.path for p in gets_de_pack) == 2
    assert [p.url.path for p in feitos if p.url.path == "/orders/search"] == ["/orders/search"] * 2
    for pedido in feitos:
        if pedido.method == "GET" and pedido.url.path.startswith("/messages/"):
            assert pedido.url.params.get("mark_as_read") == "false" or (
                pedido.url.path == "/messages/unread"
            ), f"GET sem mark_as_read=false: {pedido.url}"
        # Nenhum caminho de "marcar como lido" é chamado, em hipótese alguma.
        assert "mark_as_read=true" not in str(pedido.url)
        assert "/read" not in pedido.url.path
    for pedido in gets_de_pack:
        assert pedido.url.params.get("mark_as_read") == "false"
        assert pedido.url.params.get("tag") == "post_sale"


async def test_metodos_novos_do_cliente_ml_montam_o_pedido_certo(monkeypatch):
    feitos = _transporte(
        monkeypatch, lambda req: httpx.Response(200, json={"questions": [], "total": 0})
    )
    cliente = _cliente_real()

    await cliente.perguntas_recebidas(status="ANSWERED", offset=50, limit=50)
    await cliente.detalhe_pergunta(123)
    await cliente.mensagens_nao_lidas()
    await cliente.responder_pergunta("123", "Temos sim")
    await cliente.enviar_mensagem_pack(PACK, SELLER, COMPRADOR, "Olá")

    busca, detalhe, nao_lidas, responder, enviar = feitos
    assert busca.url.path == "/my/received_questions/search"
    assert busca.url.params["api_version"] == "4"
    assert busca.url.params["status"] == "ANSWERED"
    assert (busca.url.params["offset"], busca.url.params["limit"]) == ("50", "50")
    assert busca.url.params["sort_types"] == "DESC"
    assert (detalhe.url.path, detalhe.url.params["api_version"]) == ("/questions/123", "4")
    assert nao_lidas.url.path == "/messages/unread"
    assert dict(nao_lidas.url.params) == {"role": "seller", "tag": "post_sale"}
    assert (responder.method, responder.url.path) == ("POST", "/answers")
    assert httpx.Response(200, content=responder.content).json() == {
        "question_id": 123,
        "text": "Temos sim",
    }
    assert (enviar.method, enviar.url.path) == ("POST", f"/messages/packs/{PACK}/sellers/{SELLER}")
    assert enviar.url.params["tag"] == "post_sale"
    assert httpx.Response(200, content=enviar.content).json() == {
        "from": {"user_id": SELLER},
        "to": {"user_id": COMPRADOR},
        "text": "Olá",
    }
    assert all(p.headers["Authorization"] == "Bearer tok" for p in feitos)


async def test_envio_nao_repete_em_5xx_mas_renova_token_no_401(monkeypatch):
    """O `_request` repete 502/503/504 — ótimo para ler, péssimo para enviar."""
    respostas = iter([httpx.Response(502, json={})])
    feitos = _transporte(monkeypatch, lambda req: next(respostas))
    cliente = _cliente_real()

    r = await cliente.enviar_mensagem_pack(PACK, SELLER, COMPRADOR, "Olá")
    assert r.status_code == 502
    assert len(feitos) == 1  # uma tentativa só: pode ter saído

    # 401 = token recusado, o ML não processou: renova e tenta de novo, uma vez.
    renovou = []

    async def refresh():
        renovou.append(True)
        cliente.creds["access_token"] = "tok-novo"

    monkeypatch.setattr(cliente, "refresh", refresh)
    respostas = iter([httpx.Response(401, json={}), httpx.Response(200, json={"id": 9})])
    feitos.clear()
    r = await cliente.responder_pergunta(9, "Sim")
    assert r.status_code == 200
    assert renovou == [True]
    assert [p.headers["Authorization"] for p in feitos] == ["Bearer tok", "Bearer tok-novo"]


async def test_pack_via_agente_do_ml_responde_ao_agente_e_guarda_o_comprador_real(
    db: AsyncSession, make_user, relogio
):
    """Migração do ML (desde 02/02/2026): no pack que passa pelo Agente de
    Mensageria, o GET traz o agente em `from` e o POST tem de ir para ele. O
    comprador real (visto em mensagem antiga) não pode ser trocado pelo agente."""
    agente = int(ml_atd.AGENTE_ML_BR)
    canal, integ = await _canal(db, make_user, "pos_venda")
    cli = ClienteFalso()
    cli.nao_lidos = {PACK: 1}
    cli.packs[PACK] = {
        "messages": [
            _do_cliente("c1", "Cadê?", T0),
            _msg_pack(
                "c2", de=agente, para=SELLER, texto="E agora?", quando=T0 + timedelta(hours=1)
            ),
        ]
    }
    await _rodar(db, canal, integ, cli)
    cli.chamadas.clear()
    conversa = await _conversa(db, PACK)

    assert conversa.comprador_id == str(COMPRADOR)
    assert conversa.dados["via_agente"] is True
    assert conversa.aguardando_resposta is True  # a mensagem do agente é do cliente

    cli.post = _resposta(201, {"id": "f" * 32, "status": "available", "text": "Chega amanhã"})
    r = await ml_atd.enviar_texto(db, conversa, integ, cli, "Chega amanhã")

    assert r.ok is True
    assert cli.chamadas == [("enviar_pack", PACK, str(SELLER), ml_atd.AGENTE_ML_BR, "Chega amanhã")]


async def test_pack_sem_agente_continua_indo_direto_ao_comprador(
    db: AsyncSession, make_user, relogio
):
    conversa, integ, cli = await _conversa_pos_venda(db, make_user, relogio)
    assert conversa.dados["via_agente"] is False
    cli.post = _resposta(201, {"id": "e" * 32, "status": "available", "text": "Oi"})

    await ml_atd.enviar_texto(db, conversa, integ, cli, "Oi")

    assert cli.chamadas == [("enviar_pack", PACK, str(SELLER), str(COMPRADOR), "Oi")]
