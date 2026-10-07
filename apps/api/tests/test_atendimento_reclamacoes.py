# ruff: noqa: S105  (tokens de teste de um cliente falso, nada real)
"""Reclamações do Mercado Livre no atendimento (RF2, 01/10/2026).

Sem rede: o ML é o `MercadoLivreClient` de verdade com a ida HTTP
(`_request`) trocada por um ML FALSO no formato medido em produção em
01/10/2026 (conta aguiar, sondagem só GET): a busca `claims/search` pelo dono
do token, o claim com `players[].available_actions`, as mensagens com
`sender_role`/`receiver_role` e `hash` (sem id), a devolução v2 com o
endereço do comprador (que NÃO pode ser guardado), o motivo e a reputação.

O caso real: pedido Bling 297840 / ML 2000018509205724 (conta aguiar),
reclamação 5582543195 — mediação encerrada em 01/10 pelo mediador, 10
mensagens (7 do mediador, 3 da loja) e o pack SEM mensagem nenhuma (por isso
não havia conversa nenhuma no atendimento).

O que se mede: a reclamação vira linha em `atendimento_reclamacoes` e
conversa `canal = 'reclamacao'` (mesmo sem mensagem), com o mediador como
`mediador`, bloqueada (só leitura), com o prazo da plataforma; a etiqueta
muda na conversa da reclamação E no pack do mesmo pedido, e volta quando
encerra; só GET; poucas chamadas (nada relido sem mudança, teto por conta);
erro de uma conta não para as outras; o cron só com a leitura ligada; a rota
do cartão com a mesma trava do atendimento.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoReclamacao,
    BlingOrder,
    Integration,
    IntegrationPlatform,
    User,
    UserRole,
)
from app.routers import atendimento as rota_atendimento
from app.security.cipher import encrypt_json
from app.services.atendimento import etiqueta, etiqueta_fatos, reclamacoes
from app.services.atendimento.constantes import (
    AUTOR_LOJA,
    AUTOR_MEDIADOR,
    AUTOR_SISTEMA,
    CANAL_RECLAMACAO,
    CONVERSA_BLOQUEADA,
)
from app.services.atendimento.reclamacoes import (
    acao_pendente,
    data_ml,
    mensagem_da_reclamacao,
    tipo_da_reclamacao,
)
from app.services.marketplaces.ml import MercadoLivreClient

T0 = datetime(2026, 10, 1, 18, 0, tzinfo=UTC)
PEDIDO_ML = "2000018509205724"
PEDIDO_BLING = "297840"
RECLAMACAO_ML = "5582543195"
SELLER = 204897261
ENDERECO_SECRETO = "Rua do Comprador Secreto"

# A rota do cartão vem do `app.main` (router `atendimento_reclamacoes`
# registrado lá) — sem incluir à mão.


# ─────────────── o ML falso ───────────────


def _claim(
    cid: str = RECLAMACAO_ML,
    *,
    order: str = PEDIDO_ML,
    status: str = "opened",
    stage: str = "claim",
    tipo: str = "mediations",
    reason: str = "PDD9949",
    acoes: list[dict] | None = None,
    last_updated: str = "2026-10-01T11:20:25.000-04:00",
    criada: str = "2026-09-24T08:15:03.000-04:00",
    resolution: dict | None = None,
) -> dict:
    """Um claim no formato da busca (15 chaves, medido em 01/10/2026)."""
    return {
        "id": int(cid),
        "resource_id": int(order),
        "status": status,
        "type": tipo,
        "stage": stage,
        "parent_id": None,
        "resource": "order",
        "reason_id": reason,
        "fulfilled": True,
        "quantity_type": "total",
        "players": [
            {"role": "complainant", "type": "buyer", "user_id": 111, "available_actions": []},
            {
                "role": "respondent",
                "type": "seller",
                "user_id": SELLER,
                "available_actions": acoes or [],
            },
            {"role": "mediator", "type": "internal", "user_id": 1, "available_actions": []},
        ],
        "resolution": resolution,
        "site_id": "MLB",
        "date_created": criada,
        "last_updated": last_updated,
    }


def _encerrada_297840(**kw) -> dict:
    return _claim(
        status="closed",
        stage="dispute",
        resolution={
            "reason": "change_cancelled_meli",
            "date_created": "2026-10-01T11:20:25.000-04:00",
            "benefited": [],
            "closed_by": "mediator",
            "applied_coverage": False,
        },
        **kw,
    )


def _msg(i: int, de: str, para: str, texto: str, quando: str, *, hash_: str | None = None) -> dict:
    return {
        "sender_role": de,
        "receiver_role": para,
        "message": texto,
        "translated_message": None,
        "date_created": quando,
        "last_updated": quando,
        "message_date": quando,
        "date_read": quando,
        "attachments": [],
        "status": "available",
        "stage": "dispute",
        "message_moderation": {
            "status": "clean",
            "reason": None,
            "source": "online",
            "date_moderated": quando,
        },
        "repeated": False,
        "hash": hash_ if hash_ is not None else f"hash-{i}",
    }


def _mensagens_297840() -> list[dict]:
    """7 do mediador para a loja e 3 da loja para o mediador (como em produção)."""
    saida = []
    for i in range(10):
        de, para = ("mediator", "respondent") if i % 3 != 2 else ("respondent", "mediator")
        quando = f"2026-09-{25 + i // 4:02d}T{10 + i:02d}:46:56.070-04:00"
        saida.append(_msg(i, de, para, f"mensagem {i}", quando))
    return saida


def _devolucao_ml() -> dict:
    return {
        "id": 77,
        "last_updated": "2026-10-01T15:18:51.702+00:00",
        "shipments": [
            {
                "shipment_id": 55,
                "status": "cancelled",
                "tracking_number": None,
                "destination": {
                    "name": "Comprador",
                    "shipping_address": {"street_name": ENDERECO_SECRETO, "zip_code": "01000"},
                },
                "type": "return",
            }
        ],
        "refund_at": "shipped",
        "date_closed": "2026-10-01T11:20:24.641-04:00",
        "resource_type": "order",
        "date_created": "2026-09-25T19:48:33.307+00:00",
        "claim_id": int(RECLAMACAO_ML),
        "status_money": "retained",
        "resource_id": int(PEDIDO_ML),
        "orders": [{"order_id": int(PEDIDO_ML), "item_id": "MLB123", "context_type": "x"}],
        "subtype": None,
        "status": "cancelled",
    }


class MLFalso:
    """A API do ML por conta (`creds.user_id`), com a ida HTTP registrada."""

    def __init__(self) -> None:
        self.contas: dict[int, dict[str, Any]] = {}
        self.chamadas: list[tuple[str, str, dict]] = []

    def conta(self, seller: int = SELLER) -> dict[str, Any]:
        return self.contas.setdefault(
            seller,
            {
                "abertas": [],
                "encerradas": [],
                "mensagens": {},
                "devolucoes": {},
                "reputacao": {},
                "detalhe": {},
                "pedidos": {},
                "falhas": {},
            },
        )

    def chamadas_de(self, trecho: str) -> list[tuple[str, str, dict]]:
        return [c for c in self.chamadas if trecho in c[1]]

    def responder(self, seller: int, method: str, path: str, params: dict) -> httpx.Response:
        self.chamadas.append((method, path, dict(params)))
        c = self.conta(seller)

        def r(status: int, corpo: Any = None) -> httpx.Response:
            return httpx.Response(
                status,
                json=corpo if corpo is not None else {},
                request=httpx.Request(method, f"https://api.mercadolibre.com{path}"),
            )

        for trecho, status in c["falhas"].items():
            if trecho in path:
                return r(status, {"code": "erro_falso"})
        if path == "/post-purchase/v1/claims/search":
            lista = c["abertas"] if params.get("status") == "opened" else c["encerradas"]
            ini, lim = int(params.get("offset", 0)), int(params.get("limit", 50))
            return r(
                200,
                {
                    "paging": {"total": len(lista), "offset": ini, "limit": lim},
                    "data": lista[ini : ini + lim],
                },
            )
        partes = path.strip("/").split("/")
        if path.startswith("/post-purchase/v1/claims/reasons/"):
            rid = partes[-1]
            return r(
                200,
                {
                    "id": rid,
                    "flow": "post_purchase_delivered",
                    "name": "Produto diferente do anunciado",
                    "detail": "x",
                },
            )
        if path.endswith("/messages"):
            return r(200, c["mensagens"].get(partes[-2], []))
        if path.endswith("/affects-reputation"):
            rep = c["reputacao"].get(partes[-2])
            return r(200, rep) if rep else r(404, {"code": "not_found"})
        if path.startswith("/post-purchase/v2/claims/") and path.endswith("/returns"):
            dev = c["devolucoes"].get(partes[-2])
            return r(200, dev) if dev else r(404, {"code": "not_found"})
        if path.startswith("/post-purchase/v1/claims/"):
            claim = c["detalhe"].get(partes[-1])
            return r(200, claim) if claim else r(404, {"code": "not_found"})
        if path.startswith("/orders/"):
            pedido = c["pedidos"].get(partes[-1])
            return r(200, pedido) if pedido else r(404, {"code": "not_found"})
        return r(404, {"code": "rota_desconhecida"})


@pytest.fixture
def ml(monkeypatch) -> MLFalso:
    falso = MLFalso()

    async def _request(self, method, path, *, params=None, json=None):  # noqa: A002
        return falso.responder(int(self.creds.get("user_id") or 0), method, path, params or {})

    monkeypatch.setattr(MercadoLivreClient, "_request", _request)
    reclamacoes._MOTIVOS.clear()
    return falso


async def _fabrica(integration: Integration) -> MercadoLivreClient:
    from app.security.cipher import decrypt_json

    return MercadoLivreClient(decrypt_json(integration.credentials))


async def _conta(db: AsyncSession, user: User, nome: str = "aguiar", seller: int = SELLER):
    integ = Integration(
        user_id=user.id,
        platform=IntegrationPlatform.ML,
        name=nome,
        credentials=encrypt_json(
            {"access_token": "t", "refresh_token": "r", "user_id": seller, "expires_at": 9e9}
        ),
    )
    db.add(integ)
    await db.commit()
    # Só o id: os testes expiram a sessão (a rodada grava na sessão dela).
    return SimpleNamespace(id=integ.id)


async def _bling_297840(db: AsyncSession, situacao: str = "83953") -> None:
    db.add(
        BlingOrder(
            bling_id=297840,
            numero=PEDIDO_BLING,
            numeroloja=PEDIDO_ML,
            situacao=situacao,
            loja="204897261",
            data=T0 - timedelta(days=14),
            item_index=0,
            item_codigo="SKU-1",
        )
    )
    await db.commit()


async def _sync(integ, agora: datetime = T0) -> dict:
    return await reclamacoes.sincronizar_reclamacoes_ml(
        integ.id, fabrica_cliente=_fabrica, agora=agora
    )


async def _conversa_da_reclamacao(db: AsyncSession, cid: str = RECLAMACAO_ML):
    return (
        await db.execute(
            select(AtendimentoConversa).where(
                AtendimentoConversa.canal == CANAL_RECLAMACAO,
                AtendimentoConversa.externo_id == cid,
            )
        )
    ).scalar_one_or_none()


async def _linha(db: AsyncSession, cid: str = RECLAMACAO_ML) -> AtendimentoReclamacao | None:
    return (
        await db.execute(
            select(AtendimentoReclamacao).where(AtendimentoReclamacao.externo_id == cid)
        )
    ).scalar_one_or_none()


async def _mensagens(db: AsyncSession, conversa: AtendimentoConversa):
    return (
        (
            await db.execute(
                select(AtendimentoMensagem)
                .where(AtendimentoMensagem.conversa_id == conversa.id)
                .order_by(AtendimentoMensagem.enviada_em)
            )
        )
        .scalars()
        .all()
    )


# ─────────────── o caso real ───────────────


async def test_caso_297840_reclamacao_encerrada_vira_conversa_mesmo_sem_pack(db, make_user, ml):
    """O pack sem mensagem não tinha conversa: a reclamação ganha a dela."""
    integ = await _conta(db, await make_user())
    await _bling_297840(db)
    c = ml.conta()
    c["encerradas"] = [_encerrada_297840()]
    c["mensagens"][RECLAMACAO_ML] = _mensagens_297840()
    c["devolucoes"][RECLAMACAO_ML] = _devolucao_ml()
    c["reputacao"][RECLAMACAO_ML] = {"affects_reputation": "affected", "has_incentive": False}
    assert await etiqueta_fatos.conversas_do_pedido_bling(db, PEDIDO_BLING) == []

    resumo = await _sync(integ)

    assert resumo["erros"] == 0 and resumo["novas"] == 1 and resumo["conversas_novas"] == 1
    db.expire_all()
    linha = await _linha(db)
    assert linha is not None
    assert (linha.plataforma, linha.tipo, linha.status) == ("ml", "mediacao", "closed")
    assert linha.pedido_marketplace == PEDIDO_ML
    assert linha.integration_id == integ.id
    assert linha.encerrada_em == datetime(2026, 10, 1, 15, 20, 25, tzinfo=UTC)
    assert linha.aberta_em == datetime(2026, 9, 24, 12, 15, 3, tzinfo=UTC)
    assert linha.prazo_em is None
    assert linha.motivo == "Produto diferente do anunciado"
    assert linha.dados["reputacao"] == "affected"
    assert linha.dados["devolucao"]["status"] == "cancelled"
    assert linha.dados["resolucao"]["encerrada_por"] == "mediator"
    # O endereço que a devolução do ML traz NUNCA é guardado.
    assert ENDERECO_SECRETO not in json.dumps(linha.dados, default=str)

    conversa = await _conversa_da_reclamacao(db)
    assert conversa is not None and linha.conversa_id == conversa.id
    assert (conversa.plataforma, conversa.integration_id) == ("ml", integ.id)
    assert conversa.pedido_marketplace == PEDIDO_ML
    assert conversa.canal_id is None
    assert conversa.situacao == CONVERSA_BLOQUEADA
    assert conversa.bloqueio_motivo == reclamacoes.BLOQUEIO_SO_LEITURA
    assert conversa.aguardando_resposta is False and conversa.nao_lidas == 0
    assert conversa.dados["claim_ids"] == []  # encerrada
    assert ENDERECO_SECRETO not in json.dumps(conversa.dados, default=str)
    msgs = await _mensagens(db, conversa)
    autores = [m.autor for m in msgs]
    assert autores.count(AUTOR_MEDIADOR) == 7
    assert autores.count(AUTOR_LOJA) == 3
    assert autores.count(AUTOR_SISTEMA) == 2  # aberta + encerrada
    assert msgs[0].texto.startswith("Mediação 5582543195 aberta no Mercado Livre")
    assert "Produto diferente do anunciado" in msgs[0].texto
    assert msgs[-1].texto.startswith("Mediação encerrada no Mercado Livre")
    assert "change_cancelled_meli" in msgs[-1].texto
    # A loja que respondeu pelo Duoke é `externo`; o mediador, o sistema.
    assert {m.origem for m in msgs if m.autor == AUTOR_LOJA} == {"externo"}
    assert {m.origem for m in msgs if m.autor == AUTOR_MEDIADOR} == {"sistema"}

    # O pedido do Bling chega à conversa; encerrada → Pós-venda.
    assert [x.id for x in await etiqueta_fatos.conversas_do_pedido_bling(db, PEDIDO_BLING)] == [
        conversa.id
    ]
    assert conversa.etiqueta == "pos_venda"
    # Só leitura: só GET, nada de marcar como lido.
    assert ml.chamadas and {m for m, _, _ in ml.chamadas} == {"GET"}
    assert not any("mark_as_read" in p for _, _, p in ml.chamadas)


async def test_reclamacao_aberta_muda_a_etiqueta_do_pack_e_volta(db, make_user, ml):
    """Pós-venda → Reclamação (com prazo do ML) → Mediação → encerrada → Pós-venda."""
    integ = await _conta(db, await make_user())
    await _bling_297840(db)
    pack = AtendimentoConversa(
        integration_id=integ.id,
        plataforma="ml",
        canal="pos_venda",
        externo_id="2000009999999999",
        pedido_marketplace=PEDIDO_ML,
        dados={"pack_id": "2000009999999999", "order_id": PEDIDO_ML},
        ultima_mensagem_em=T0 - timedelta(days=3),
    )
    db.add(pack)
    await db.commit()
    await etiqueta.recalcular_etiqueta(db, pack, motivo="cron", agora=T0 - timedelta(days=1))
    await db.commit()
    assert pack.etiqueta == "pos_venda"
    pack_id = pack.id

    prazo = "2026-10-05T22:32:00.000-04:00"
    c = ml.conta()
    c["abertas"] = [
        _claim(
            acoes=[
                {"action": "send_message_to_complainant", "mandatory": True, "due_date": prazo},
                {"action": "refund", "mandatory": False},
                {"action": "open_dispute", "mandatory": False},
            ],
            last_updated="2026-10-01T10:00:00.000-04:00",
        )
    ]
    c["mensagens"][RECLAMACAO_ML] = [
        _msg(1, "complainant", "respondent", "veio diferente", "2026-10-01T09:00:00.000-04:00")
    ]

    await _sync(integ)
    db.expire_all()
    linha = await _linha(db)
    assert linha.tipo == "reclamacao" and linha.encerrada_em is None
    assert linha.prazo_em == data_ml(prazo)
    assert linha.dados["acao_pendente"] == "send_message_to_complainant"
    assert "comprador" in linha.dados["acao_texto"]
    conversa = await _conversa_da_reclamacao(db)
    # A vez é da loja pela PLATAFORMA, com o prazo do ML: entra em "Falta responder".
    assert conversa.aguardando_resposta is True
    assert conversa.prazo_resposta_em == data_ml(prazo)
    assert conversa.dados["claim_ids"] == [RECLAMACAO_ML]
    assert conversa.etiqueta == "reclamacao"
    pack = await db.get(AtendimentoConversa, pack_id)
    assert pack.etiqueta == "reclamacao"
    hist = await etiqueta.historico_da_conversa(db, pack_id)
    assert [(h.de, h.para) for h in hist] == [("pos_venda", "reclamacao")]
    assert hist[-1].motivo == ("Reclamação 5582543195 aberta no ML (leitura das reclamações do ML)")

    # O cartão: a rota devolve a mesma reclamação nas duas conversas.
    abertas = await reclamacoes.reclamacoes_da_conversa(db, pack)
    assert [r.externo_id for r in abertas] == [RECLAMACAO_ML]
    tela = reclamacoes.para_tela(abertas[0])
    assert tela["status_rotulo"] == "Aberta — com o comprador"
    assert tela["url_plataforma"] == (
        "https://www.mercadolivre.com.br/vendas/2000009999999999/detalhe"
    )

    # O ML entra como mediador: Mediação (continua Reclamação na etiqueta).
    c["abertas"] = [_claim(stage="dispute", last_updated="2026-10-01T12:00:00.000-04:00")]
    c["mensagens"][RECLAMACAO_ML].append(
        _msg(2, "mediator", "respondent", "Mediação iniciada", "2026-10-01T11:59:00.000-04:00")
    )
    await _sync(integ, T0 + timedelta(minutes=10))
    db.expire_all()
    linha = await _linha(db)
    assert linha.tipo == "mediacao" and linha.prazo_em is None
    conversa = await _conversa_da_reclamacao(db)
    assert conversa.aguardando_resposta is False  # sem ação da loja: não é a vez dela
    assert reclamacoes.para_tela(linha)["status_rotulo"] == "Em mediação no Mercado Livre"

    # Encerrada (some das abertas, aparece nas encerradas): volta a Pós-venda.
    c["abertas"] = []
    c["encerradas"] = [
        _claim(
            status="closed",
            stage="dispute",
            last_updated="2026-10-01T14:00:00.000-04:00",
            resolution={
                "reason": "item_returned",
                "date_created": "2026-10-01T14:00:00.000-04:00",
                "benefited": ["respondent"],
                "closed_by": "mediator",
            },
        )
    ]
    await _sync(integ, T0 + timedelta(minutes=20))
    db.expire_all()
    pack = await db.get(AtendimentoConversa, pack_id)
    conversa = await _conversa_da_reclamacao(db)
    assert pack.etiqueta == "pos_venda" and conversa.etiqueta == "pos_venda"
    hist = await etiqueta.historico_da_conversa(db, pack_id)
    assert [(h.de, h.para) for h in hist] == [
        ("pos_venda", "reclamacao"),
        ("reclamacao", "pos_venda"),
    ]
    assert hist[-1].motivo.startswith("Reclamação encerrada")
    linha = await _linha(db)
    assert reclamacoes.para_tela(linha)["status_rotulo"] == "Encerrada — a favor da loja"
    sistema = [m.texto for m in await _mensagens(db, conversa) if m.autor == AUTOR_SISTEMA]
    assert sistema[-1].startswith("Mediação encerrada no Mercado Livre — decisão a favor da loja")


async def test_reclamacao_aberta_sem_mensagem_tambem_ganha_conversa(db, make_user, ml):
    integ = await _conta(db, await make_user())
    c = ml.conta()
    c["abertas"] = [_claim(tipo="returns", stage="claim")]
    # Sem mensagem, sem devolução, sem reputação (404).
    await _sync(integ)
    db.expire_all()
    linha = await _linha(db)
    assert linha.tipo == "devolucao"
    conversa = await _conversa_da_reclamacao(db)
    assert conversa is not None
    msgs = await _mensagens(db, conversa)
    assert [m.autor for m in msgs] == [AUTOR_SISTEMA]
    assert msgs[0].texto.startswith("Devolução 5582543195 aberta no Mercado Livre")
    assert conversa.ultima_mensagem_em is not None  # entra na lista pela data
    assert conversa.etiqueta == "devolucao"


async def test_sem_mudanca_nao_chama_de_novo_e_nao_duplica(db, make_user, ml):
    integ = await _conta(db, await make_user())
    c = ml.conta()
    c["abertas"] = [_claim(last_updated="2026-10-01T10:00:00.000-04:00")]
    c["mensagens"][RECLAMACAO_ML] = [
        _msg(1, "complainant", "respondent", "oi", "2026-10-01T09:00:00.000-04:00")
    ]
    await _sync(integ)
    ml.chamadas.clear()

    resumo = await _sync(integ, T0 + timedelta(minutes=5))
    # Só as duas buscas: a reclamação não mexeu e foi lida há menos de
    # RELER_ABERTA — nada é relido nem escrito.
    assert [p for _, p, _ in ml.chamadas] == ["/post-purchase/v1/claims/search"] * 2
    assert resumo["lidas"] == 0 and resumo["atualizadas"] == 0

    # Mexeu (mensagem nova): relê, sem duplicar a que já tinha.
    c["abertas"] = [_claim(last_updated="2026-10-01T13:00:00.000-04:00")]
    c["mensagens"][RECLAMACAO_ML].append(
        _msg(2, "complainant", "respondent", "e aí?", "2026-10-01T12:00:00.000-04:00")
    )
    resumo = await _sync(integ, T0 + timedelta(minutes=20))
    assert resumo["lidas"] == 1 and resumo["mensagens"] == 1
    db.expire_all()
    conversa = await _conversa_da_reclamacao(db)
    assert [m.autor for m in await _mensagens(db, conversa)].count("cliente") == 2
    # O motivo veio do cache (uma ida ao ML só).
    assert len(ml.chamadas_de("/claims/reasons/")) == 0


async def test_aberta_e_relida_mesmo_sem_mudar_last_updated(db, make_user, ml):
    """05/10/2026: mensagem nova nem sempre muda o `last_updated` da reclamação
    (a do comprador na 5586923869 da Kia entrou 43 h depois). A ABERTA é
    relida a cada RELER_ABERTA; a encerrada que não mexeu, não."""
    integ = await _conta(db, await make_user())
    c = ml.conta()
    c["abertas"] = [_claim(last_updated="2026-10-01T10:00:00.000-04:00")]
    c["mensagens"][RECLAMACAO_ML] = [
        _msg(1, "complainant", "respondent", "oi", "2026-10-01T09:00:00.000-04:00")
    ]
    await _sync(integ)
    # Mensagem nova do comprador SEM mudar o last_updated.
    c["mensagens"][RECLAMACAO_ML].append(
        _msg(2, "complainant", "respondent", "e aí?", "2026-10-01T14:30:00.000-04:00")
    )
    ml.chamadas.clear()

    resumo = await _sync(integ, T0 + reclamacoes.RELER_ABERTA)
    assert resumo["lidas"] == 1 and resumo["mensagens"] == 1
    assert ml.chamadas_de(f"/claims/{RECLAMACAO_ML}/messages")
    # Só as mensagens: devolução e reputação só quando o last_updated muda
    # (buscá-las a cada rodada dava HTTP 429 no /returns).
    assert [p for _, p, _ in ml.chamadas if "/claims/search" not in p] == [
        f"/post-purchase/v1/claims/{RECLAMACAO_ML}/messages"
    ]
    db.expire_all()
    conversa = await _conversa_da_reclamacao(db)
    assert [m.autor for m in await _mensagens(db, conversa)].count("cliente") == 2
    linha = await _linha(db)
    assert data_ml(linha.dados["lido_em"]) == T0 + reclamacoes.RELER_ABERTA

    # Logo depois: não relê de novo (nem duplica).
    ml.chamadas.clear()
    resumo = await _sync(integ, T0 + reclamacoes.RELER_ABERTA + timedelta(minutes=1))
    assert resumo["lidas"] == 0
    assert [p for _, p, _ in ml.chamadas] == ["/post-purchase/v1/claims/search"] * 2


async def test_releitura_nao_toma_a_vez_da_reclamacao_nova(db, make_user, ml, monkeypatch):
    monkeypatch.setattr(reclamacoes, "MAX_LEITURAS_RODADA", 1)
    integ = await _conta(db, await make_user())
    c = ml.conta()
    c["abertas"] = [_claim("1001", order="2001")]
    await _sync(integ)
    # Chega outra aberta; a 1001 já pede releitura pelo tempo.
    c["abertas"] = [_claim("1001", order="2001"), _claim("1002", order="2002")]
    resumo = await _sync(integ, T0 + reclamacoes.RELER_ABERTA)
    assert resumo["lidas"] == 2 and resumo["adiadas"] == 0
    db.expire_all()
    assert await _conversa_da_reclamacao(db, "1002") is not None


async def test_encerrada_sem_mudar_nao_e_relida(db, make_user, ml):
    integ = await _conta(db, await make_user())
    c = ml.conta()
    c["encerradas"] = [_encerrada_297840()]
    await _sync(integ)
    ml.chamadas.clear()
    resumo = await _sync(integ, T0 + timedelta(hours=1))
    assert resumo["lidas"] == 0
    assert not ml.chamadas_de("/messages")


async def test_cota_por_conta_espalha_a_leitura(db, make_user, ml, monkeypatch):
    monkeypatch.setattr(reclamacoes, "MAX_LEITURAS_RODADA", 1)
    # Só a cota da PRIMEIRA leitura aqui (a releitura das abertas tem a sua).
    monkeypatch.setattr(reclamacoes, "MAX_RELEITURAS_RODADA", 0)
    integ = await _conta(db, await make_user())
    c = ml.conta()
    c["abertas"] = [_claim("1001", order="2001"), _claim("1002", order="2002")]
    c["encerradas"] = [_encerrada_297840()]

    resumo = await _sync(integ)
    assert (resumo["lidas"], resumo["adiadas"]) == (1, 2)
    # As linhas entram todas (a etiqueta já as vê); a conversa, só a lida.
    assert await db.scalar(select(func.count()).select_from(AtendimentoReclamacao)) == 3
    db.expire_all()
    assert await _conversa_da_reclamacao(db, "1001") is not None
    assert await _conversa_da_reclamacao(db, "1002") is None

    await _sync(integ, T0 + timedelta(minutes=10))
    await _sync(integ, T0 + timedelta(minutes=20))
    db.expire_all()
    for cid in ("1001", "1002", RECLAMACAO_ML):
        assert await _conversa_da_reclamacao(db, cid) is not None, cid
    assert len(ml.chamadas_de("/messages")) == 3  # cada uma lida UMA vez


async def test_aberta_que_sumiu_das_listas_e_relida_pelo_id(db, make_user, ml):
    integ = await _conta(db, await make_user())
    c = ml.conta()
    c["abertas"] = [_claim()]
    await _sync(integ)
    # Encerrou há mais tempo que a janela das encerradas: só pelo id.
    c["abertas"] = []
    c["detalhe"][RECLAMACAO_ML] = _encerrada_297840(last_updated="2026-09-01T10:00:00.000-04:00")
    await _sync(integ, T0 + timedelta(minutes=10))
    db.expire_all()
    linha = await _linha(db)
    assert linha.encerrada_em is not None
    assert ("GET", f"/post-purchase/v1/claims/{RECLAMACAO_ML}", {}) in ml.chamadas


async def test_cancelamentos_do_ml_ficam_de_fora(db, make_user, ml):
    integ = await _conta(db, await make_user())
    c = ml.conta()
    c["abertas"] = [_claim("9001", tipo="cancel_purchase", stage="none")]
    c["encerradas"] = [_claim("9002", tipo="cancel_sale", stage="none", status="closed")]
    resumo = await _sync(integ)
    assert resumo["listadas"] == 0
    assert await db.scalar(select(func.count()).select_from(AtendimentoReclamacao)) == 0
    assert not ml.chamadas_de("/messages")


async def test_leitura_que_falha_nao_repete_toda_rodada(db, make_user, ml):
    integ = await _conta(db, await make_user())
    c = ml.conta()
    c["abertas"] = [_claim()]
    c["falhas"]["/messages"] = 500
    resumo = await _sync(integ)
    assert resumo["erros"] == 1
    db.expire_all()
    linha = await _linha(db)
    assert linha is not None and linha.conversa_id is None  # a linha entra; a conversa, depois
    assert await _conversa_da_reclamacao(db) is None

    await _sync(integ, T0 + timedelta(minutes=10))
    assert len(ml.chamadas_de("/messages")) == 1  # não insistiu
    c["falhas"].clear()
    await _sync(integ, T0 + timedelta(hours=1, minutes=5))
    db.expire_all()
    assert await _conversa_da_reclamacao(db) is not None


async def test_erro_de_uma_conta_nao_para_as_outras(db, make_user, ml):
    user = await make_user()
    ruim = await _conta(db, user, "ruim", seller=1)
    boa = await _conta(db, user, "aguiar", seller=SELLER)
    ml.conta(1)["falhas"]["/claims/search"] = 503
    ml.conta()["abertas"] = [_claim()]

    total = await reclamacoes.sincronizar_todas_ml(fabrica_cliente=_fabrica, agora=T0)
    assert total["contas"] == 2 and total["contas_com_erro"] == 1
    db.expire_all()
    assert (await _linha(db)).integration_id == boa.id
    assert ruim.id != boa.id


async def test_conta_parada_ou_desligada_fica_de_fora(db, make_user, ml):
    from app.models import AtendimentoCanal

    user = await make_user()
    desligada = await _conta(db, user, "desligada", seller=1)
    ligada = await _conta(db, user, "aguiar")
    db.add(
        AtendimentoCanal(
            integration_id=desligada.id, plataforma="ml", canal="pos_venda", status="desligado"
        )
    )
    await db.commit()
    assert await reclamacoes.contas_ml(db, T0) == [ligada.id]


class RedisFalso:
    def __init__(self) -> None:
        self.dados: dict[str, Any] = {}

    async def set(self, chave, valor, *, nx=False, ex=None):
        if nx and chave in self.dados:
            return None
        self.dados[chave] = valor
        return True

    async def eval(self, _script, _n, chave, token):
        if self.dados.get(chave) == token:
            del self.dados[chave]
            return 1
        return 0


async def test_cron_so_roda_com_a_leitura_ligada_e_uma_vez(db, make_user, ml, monkeypatch):
    redis = RedisFalso()
    monkeypatch.setattr(reclamacoes, "redis", redis)
    monkeypatch.setattr(reclamacoes, "_agora", lambda: T0)
    integ = await _conta(db, await make_user())
    ml.conta()["abertas"] = [_claim()]

    monkeypatch.setattr(get_settings(), "atendimento_leitura_ativa", False)
    monkeypatch.setattr(get_settings(), "atendimento_reclamacoes_ativa", True)
    assert await reclamacoes.atendimento_reclamacoes(None, fabrica_cliente=_fabrica) is None
    assert ml.chamadas == []
    # Leitura ligada, mas o interruptor PRÓPRIO desligado (o padrão): nenhum GET.
    monkeypatch.setattr(get_settings(), "atendimento_leitura_ativa", True)
    monkeypatch.setattr(get_settings(), "atendimento_reclamacoes_ativa", False)
    assert await reclamacoes.atendimento_reclamacoes(None, fabrica_cliente=_fabrica) is None
    assert ml.chamadas == []

    monkeypatch.setattr(get_settings(), "atendimento_reclamacoes_ativa", True)
    redis.dados[reclamacoes.CHAVE_TRAVA] = "outra-rodada"
    assert await reclamacoes.atendimento_reclamacoes(None, fabrica_cliente=_fabrica) == {
        "pulado": True
    }
    redis.dados.clear()
    resumo = await reclamacoes.atendimento_reclamacoes(None, fabrica_cliente=_fabrica)
    assert resumo["ml"]["novas"] == 1 and "devolucoes" in resumo
    assert redis.dados == {}  # a trava foi solta
    db.expire_all()
    assert (await _linha(db)).integration_id == integ.id


# ─────────────── a rota do cartão ───────────────


async def test_rota_do_cartao_mesma_trava_do_atendimento(
    client, db, make_user, auth_as, ml, monkeypatch
):
    integ = await _conta(db, await make_user())
    prazo = "2026-10-05T22:32:00.000-04:00"
    ml.conta()["abertas"] = [
        _claim(
            acoes=[{"action": "send_message_to_complainant", "mandatory": True, "due_date": prazo}]
        )
    ]
    await _sync(integ)
    db.expire_all()
    conversa = await _conversa_da_reclamacao(db)

    # Fase de observação (07/10/2026): toda pessoa ativa lê o cartão (é GET).
    auth_as(await make_user(role=UserRole.USER))
    r = await client.get(f"/api/atendimento/conversas/{conversa.id}/reclamacoes")
    assert r.status_code == 200 and r.json()["abertas"] == 1

    monkeypatch.setattr(rota_atendimento, "SO_ADMIN", False)
    auth_as(await make_user(role=UserRole.ADMIN))
    r = await client.get(f"/api/atendimento/conversas/{conversa.id}/reclamacoes")
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["abertas"] == 1
    assert corpo["prazo_mais_curto"].startswith("2026-10-06T02:32")
    item = corpo["itens"][0]
    assert item["numero"] == RECLAMACAO_ML
    assert (item["tipo"], item["tipo_rotulo"], item["aberta"]) == ("reclamacao", "Reclamação", True)
    assert item["motivo"] == "Produto diferente do anunciado"
    assert item["acao_pendente"] == "Responder ao comprador na reclamação do Mercado Livre"
    assert item["url_plataforma"].endswith(f"/vendas/{PEDIDO_ML}/detalhe")

    nenhuma = "00000000-0000-0000-0000-000000000000"
    r = await client.get(f"/api/atendimento/conversas/{nenhuma}/reclamacoes")
    assert r.status_code == 404
    r = await client.get("/api/atendimento/conversas/ig:abc/reclamacoes")
    assert r.status_code == 200 and r.json()["itens"] == []


# ─────────────── as peças puras e o cliente ───────────────


@pytest.mark.parametrize(
    ("tipo", "stage", "esperado"),
    [
        ("mediations", "claim", "reclamacao"),
        ("mediations", "dispute", "mediacao"),
        ("returns", "claim", "devolucao"),
        ("returns", "dispute", "mediacao"),
        ("change", "claim", "devolucao"),
        ("mediations", "recontact", "reclamacao"),
        ("cancel_purchase", "none", None),
        ("cancel_sale", "none", None),
    ],
)
def test_tipo_da_reclamacao(tipo, stage, esperado):
    assert tipo_da_reclamacao({"type": tipo, "stage": stage}) == esperado


def test_acao_pendente_obrigatoria_primeiro():
    cedo = "2026-10-02T10:00:00.000-04:00"
    tarde = "2026-10-05T10:00:00.000-04:00"
    claim = _claim(
        acoes=[
            {"action": "refund", "mandatory": False, "due_date": cedo},
            {"action": "send_message_to_complainant", "mandatory": True, "due_date": tarde},
        ]
    )
    assert acao_pendente(claim) == ("send_message_to_complainant", data_ml(tarde))
    sem_obrigatoria = _claim(acoes=[{"action": "refund", "mandatory": False, "due_date": cedo}])
    assert acao_pendente(sem_obrigatoria) == ("refund", data_ml(cedo))
    assert acao_pendente(_claim(acoes=[{"action": "refund"}])) == (None, None)
    assert acao_pendente(_claim(status="closed", acoes=[{"action": "x", "due_date": cedo}])) == (
        None,
        None,
    )


def test_datas_do_ml():
    assert data_ml("2026-09-25T15:46:56.070-04:00") == datetime(
        2026, 9, 25, 19, 46, 56, 70000, tzinfo=UTC
    )
    assert data_ml("2026-10-01T15:18:51.702123456+00:00") == datetime(
        2026, 10, 1, 15, 18, 51, 702123, tzinfo=UTC
    )
    assert data_ml("lixo") is None and data_ml(None) is None


def test_mensagem_sem_hash_tem_chave_estavel():
    m = _msg(1, "mediator", "respondent", "texto", "2026-09-25T15:46:56.070-04:00", hash_="")
    a, b = mensagem_da_reclamacao(m), mensagem_da_reclamacao(dict(m))
    assert a.externo_id == b.externo_id and a.externo_id.startswith("c:")
    assert a.autor == AUTOR_MEDIADOR and a.payload["papel"] == "mediator"
    vazia = {**m, "message": "  ", "attachments": []}
    assert mensagem_da_reclamacao(vazia) is None
    foto = {**m, "message": None, "attachments": [{"filename": "f.jpg", "type": "image/jpeg"}]}
    assert mensagem_da_reclamacao(foto).tipo == "imagem"


async def test_cliente_ml_so_le(monkeypatch):
    feitas: list[tuple[str, str, dict]] = []

    async def _request(self, method, path, *, params=None, json=None):  # noqa: A002
        feitas.append((method, path, dict(params or {})))
        status = 404 if "returns" in path or "affects" in path else 200
        if path.endswith("/messages"):
            status = 500
        return httpx.Response(
            status, json={"data": []}, request=httpx.Request(method, "https://x" + path)
        )

    monkeypatch.setattr(MercadoLivreClient, "_request", _request)
    cliente = MercadoLivreClient({"access_token": "t", "user_id": 1, "expires_at": 9e9})
    await cliente.buscar_reclamacoes(status="closed", limit=500, sort="last_updated:desc")
    assert feitas[-1] == (
        "GET",
        "/post-purchase/v1/claims/search",
        {"status": "closed", "offset": 0, "limit": 100, "sort": "last_updated:desc"},
    )
    assert await cliente.devolucao_da_reclamacao(1) is None
    assert await cliente.reclamacao_afeta_reputacao(1) is None
    with pytest.raises(httpx.HTTPStatusError):
        # Erro NÃO vira "sem mensagem" (o get_claim_messages antigo devolvia []).
        await cliente.mensagens_da_reclamacao(1)
    assert {m for m, _, _ in feitas} == {"GET"}


async def test_encerrada_sem_cota_tira_da_fila_sem_reler(db, make_user, ml, monkeypatch):
    """A reclamação acabou mas a rodada não pode reler (cota): a fila anda igual."""
    integ = await _conta(db, await make_user())
    prazo = "2026-10-05T22:32:00.000-04:00"
    c = ml.conta()
    c["abertas"] = [
        _claim(
            acoes=[{"action": "send_message_to_complainant", "mandatory": True, "due_date": prazo}],
            last_updated="2026-10-01T10:00:00.000-04:00",
        )
    ]
    await _sync(integ)
    db.expire_all()
    assert (await _conversa_da_reclamacao(db)).aguardando_resposta is True

    monkeypatch.setattr(reclamacoes, "MAX_LEITURAS_RODADA", 0)
    c["abertas"] = []
    c["encerradas"] = [_encerrada_297840()]
    ml.chamadas.clear()
    resumo = await _sync(integ, T0 + timedelta(minutes=10))
    assert resumo["adiadas"] == 1 and not ml.chamadas_de("/messages")
    db.expire_all()
    conversa = await _conversa_da_reclamacao(db)
    assert conversa.aguardando_resposta is False and conversa.prazo_resposta_em is None
    assert conversa.dados["claim_ids"] == []
    assert conversa.etiqueta == "pos_venda"
    assert (await _linha(db)).encerrada_em is not None


async def test_conversa_da_reclamacao_abre_na_caixa_e_nao_envia(
    client, db, make_user, auth_as, ml, monkeypatch
):
    """A conversa nova (`canal = 'reclamacao'`, sem canal de leitura) abre no
    detalhe e na lista do router do atendimento e não deixa responder — nem
    com o envio ligado (bloqueada: só leitura)."""
    integ = await _conta(db, await make_user())
    ml.conta()["abertas"] = [_claim()]
    ml.conta()["mensagens"][RECLAMACAO_ML] = [
        _msg(1, "complainant", "respondent", "veio diferente", "2026-10-01T09:00:00.000-04:00")
    ]
    await _sync(integ)
    db.expire_all()
    conversa = await _conversa_da_reclamacao(db)

    monkeypatch.setattr(rota_atendimento, "SO_ADMIN", False)
    monkeypatch.setattr(get_settings(), "atendimento_envio_ativo", True)
    auth_as(await make_user(role=UserRole.ADMIN))
    r = await client.get(f"/api/atendimento/conversas/{conversa.id}")
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["conversa"]["canal"] == "reclamacao"
    assert corpo["envio"]["pode_enviar"] is False
    r = await client.get("/api/atendimento/conversas")
    assert str(conversa.id) in {i["id"] for i in r.json()["itens"]}
