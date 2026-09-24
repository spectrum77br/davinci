"""Executor de leitura de chamado (Vinicius, 24/09/2026 — 296012 / 2609200FUTKM4JD).

A recusa escrita da Shopee ("Histórico da Solicitação") só existe no Seller
Center; a API diz só "aguardando análise" (às 16:09 ainda dizia isso de uma
recusa das 15:59). Um executor no Mac Santiago relê a devolução pela tela.

O que é garantido aqui:
- a fila dele entrega devolução da Shopee contestada PELA API (e só ela);
- `contas` filtra pelas lojas que ele tem perfil; `espiar` não marca a entrega;
- senha própria: o token do robô de NF não abre a fila dele, e a dele não abre
  as rotas que postam na conversa com o cliente;
- o que ele leu vira resposta da Shopee com a hora da TELA;
- ele não escreve em chamado que a fila dele não entrega.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import Chamado, ChamadoLeitor, ChamadoMensagem
from app.services import chamados as svc

pytestmark = pytest.mark.asyncio

_LEITOR = "tok-leitor-teste"  # noqa: S105
_NF = "tok-nf-teste"  # noqa: S105
_HDR = {"X-Agent-Token": _LEITOR}

RETURN_SN = "2609200FUTKM4JD"
PEDIDO_SHOPEE = "260910MATESNVN"
RECUSA = (
    "Olá!\nAnalisamos sua solicitação referente ao pedido 260910MATESNVN, bem como as "
    "evidências enviadas para o caso.\n\nInformamos que, após a análise, não será possível "
    "aprovar sua solicitação."
)
QUANDO_RECUSOU = datetime(2026, 9, 24, 15, 59, tzinfo=svc.SAO_PAULO)


@pytest.fixture(autouse=True)
async def _leitor(db, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "nf_agent_token", _NF)
    row = ChamadoLeitor(
        nome="Executor de leitura (teste)",
        token_hash=hashlib.sha256(_LEITOR.encode()).hexdigest(),
    )
    db.add(row)
    await db.commit()
    return row


async def _devolucao(
    db,
    *,
    plataforma: str = "shopee",
    conta: str = "Shopee Vortan",
    canal: str = "api",
    origem: str = "devolucao",
    return_sn: str = RETURN_SN,
    pedido_mkt: str | None = PEDIDO_SHOPEE,
    abertura_status: str = "enviada",
    abertura_erro: str | None = None,
    de_tela: bool = False,
    resolvido: bool = False,
    status_plataforma: str | None = None,
) -> Chamado:
    """Um chamado como o do 296012: devolução Shopee contestada pela API (a
    abertura saiu pelo `returns/dispute` às 13:01)."""
    ch = Chamado(
        id=uuid4(),
        pedido_bling="296012",
        pedido_marketplace=pedido_mkt,
        plataforma=plataforma,
        conta=conta,
        origem=origem,
        chamado=return_sn,
        canal=canal,
        chamado_de_tela=de_tela,
        resolvido=resolvido,
        status_plataforma=status_plataforma,
    )
    db.add(ch)
    await db.flush()
    m = svc.nova_mensagem(
        ch,
        texto="Recebemos o pacote da devolução sem o produto dentro. Solicitamos a análise.",
        tipo="abertura",
        direcao="enviada",
        autor_nome="robô",
        status=abertura_status,
    )
    m.canal = "api"
    m.erro = abertura_erro
    m.created_at = m.enviada_at = datetime(2026, 9, 24, 13, 1, tzinfo=svc.SAO_PAULO)
    db.add(m)
    await db.commit()
    await db.refresh(ch)
    return ch


async def _fila(client, **body) -> list[dict]:
    r = await client.post("/api/chamados/agent/leitor/fila", headers=_HDR, json=body)
    assert r.status_code == 200, r.text
    return r.json()["casos"]


# ------------------------------------------------------------------ a fila


async def test_fila_entrega_a_devolucao_shopee_contestada_pela_api(client, db):
    ch = await _devolucao(db)
    casos = await _fila(client)
    assert [c["chamado_id"] for c in casos] == [str(ch.id)]
    assert casos[0]["chamado"] == RETURN_SN
    assert casos[0]["pedido_marketplace"] == PEDIDO_SHOPEE  # o robô busca por ele
    assert casos[0]["conta"] == "Shopee Vortan"
    assert "texto" not in casos[0]  # leitura nunca posta nada


@pytest.mark.parametrize(
    "kw",
    [
        {"plataforma": "ml"},
        {"plataforma": "tiktok"},
        {"de_tela": True},  # esse é da fila do robô de tela (`/agent/leitura`)
        {"canal": "robo"},
        {"origem": "logistica"},
        {"resolvido": True},
        {"status_plataforma": svc.STATUS_ENCERRADO},
        {"abertura_status": "pendente"},  # disputa ainda não saiu
        {"abertura_status": "falhou", "abertura_erro": "devolucao_sem_foto"},
        {"pedido_mkt": None},  # sem o nº do pedido o robô não acha a devolução
    ],
)
async def test_fila_exclui(client, db, kw):
    await _devolucao(db, **kw)
    assert await _fila(client) == []


async def test_disputa_feita_fora_do_davinci_tambem_entra(client, db):
    """Abertura "falhou" porque a disputa já existia: o caso segue vivo na Shopee
    e o acompanhamento por API também o segue (ABERTURA_FALHOU_ACOMPANHA)."""
    ch = await _devolucao(db, abertura_status="falhou", abertura_erro="shopee_ja_contestada")
    assert [c["chamado_id"] for c in await _fila(client)] == [str(ch.id)]


async def test_contas_filtra_pelas_lojas_com_perfil(client, db):
    vortan = await _devolucao(db)
    await _devolucao(db, conta="Shopee Marquezini", return_sn="2609200XXXXXXX1")
    casos = await _fila(client, contas=["shopee vortan", "Shopee Mega"])
    assert [c["chamado_id"] for c in casos] == [str(vortan.id)]
    assert await _fila(client, contas=[]) == []


async def test_espiar_nao_marca_e_a_entrega_de_verdade_marca(client, db):
    ch = await _devolucao(db)
    assert len(await _fila(client, espiar=True)) == 1
    assert len(await _fila(client, espiar=True)) == 1  # continua disponível
    await db.refresh(ch)
    assert ch.leitura_robo_claim_at is None
    assert len(await _fila(client)) == 1
    assert await _fila(client) == []  # entregue: claim de 30 min


async def test_depois_de_lido_volta_so_na_cadencia(client, db):
    ch = await _devolucao(db)
    ch.leitura_robo_at = datetime.now(UTC) - timedelta(hours=1)
    await db.commit()
    assert await _fila(client) == []
    ch.leitura_robo_at = datetime.now(UTC) - timedelta(hours=4)
    await db.commit()
    assert len(await _fila(client)) == 1


# --------------------------------------------------------------- a senha


async def test_sem_senha_ou_com_a_senha_do_robo_de_nf_nao_entra(client, db):
    await _devolucao(db)
    for hdr in ({}, {"X-Agent-Token": _NF}, {"X-Agent-Token": "chute"}):
        r = await client.post("/api/chamados/agent/leitor/fila", headers=hdr, json={})
        assert r.status_code == 401, (hdr, r.text)


async def test_senha_revogada_nao_entra(client, db, _leitor):
    _leitor.revoked_at = datetime.now(UTC)
    await db.commit()
    r = await client.post("/api/chamados/agent/leitor/fila", headers=_HDR, json={})
    assert r.status_code == 401


async def test_senha_do_leitor_nao_abre_as_rotas_que_postam(client, db):
    """Com a senha dele não dá pra pegar tarefa de postar na conversa do cliente."""
    for rota, body in (
        ("/api/chamados/agent/lease", {}),
        ("/api/chamados/agent/leitura", {"plataformas": ["shopee"]}),
    ):
        r = await client.post(rota, headers=_HDR, json=body)
        assert r.status_code == 401, (rota, r.text)


async def test_usar_a_senha_carimba_o_ultimo_uso(client, db, _leitor):
    await _fila(client)
    await db.refresh(_leitor)
    assert _leitor.last_used_at is not None


# ------------------------------------------------------ o que ele leu


async def test_recusa_lida_vira_resposta_da_shopee_com_a_hora_da_tela(client, db):
    ch = await _devolucao(db)
    await _fila(client)
    r = await client.post(
        "/api/chamados/agent/leitor/resultado",
        headers=_HDR,
        json={
            "chamado_id": str(ch.id),
            "falas": [
                {"texto": RECUSA, "quando": QUANDO_RECUSOU.isoformat(), "autor": "Agente da Shopee"}
            ],
            "historico": (
                f"Situação na Shopee: Requisição negada\n[24/09 15:59] Agente da Shopee: {RECUSA}"
            ),
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["falas_novas"] == 1
    fala = (
        await db.execute(
            select(ChamadoMensagem).where(
                ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.direcao == "recebida"
            )
        )
    ).scalar_one()
    assert fala.texto == RECUSA
    assert fala.autor_nome == "Agente da Shopee"
    assert fala.created_at == QUANDO_RECUSOU
    await db.refresh(ch)
    assert ch.leitura_robo_at is not None and ch.leitura_robo_claim_at is None


async def test_nao_escreve_em_chamado_que_nao_e_da_fila_dele(client, db):
    for kw in ({"plataforma": "ml"}, {"de_tela": True}, {"canal": "robo"}):
        ch = await _devolucao(db, return_sn=f"X{uuid4().hex[:10]}", **kw)
        r = await client.post(
            "/api/chamados/agent/leitor/resultado",
            headers=_HDR,
            json={
                "chamado_id": str(ch.id),
                "falas": [{"texto": "x", "quando": QUANDO_RECUSOU.isoformat()}],
            },
        )
        assert r.status_code == 409, (kw, r.text)
