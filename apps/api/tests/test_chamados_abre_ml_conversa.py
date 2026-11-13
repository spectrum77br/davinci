"""Abertura da consulta do ML: nota inteira e a conversa do chat (13/11/2026).

297587 (ML Marquezini, devolução "Não recebido"): a IA falou com o assistente do
Fale conosco ("a mala não foi entregue ao comprador… não há compensação pra
loja"), o chat fechou depois do E-mail e ela devolveu `humano` pedindo pra pessoa
conferir duas consultas. O servidor guardava só 300 letras da nota — o pedido pra
pessoa ficou na parte cortada — e a conversa com o assistente não ia pra lugar
nenhum.

O que é garantido aqui:
- a nota da IA entra inteira (até 2000 letras), também no aviso de nova tentativa;
- a `conversa` vira UMA nota na coluna da conversa, uma linha por fala com dia e
  hora de Brasília;
- nota não é fala: a abertura que falhou pedindo gente continua em Análise Humano
  e a IA não acorda pra ler a própria conversa.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import Chamado, ChamadoCerebro, ChamadoMensagem
from app.services import chamados as svc

pytestmark = pytest.mark.asyncio

_NF = "tok-nf-legado-conversa"  # noqa: S105
_IA = "tok-ia-de-chamado-conversa"  # noqa: S105
IA = {"X-Agent-Token": _IA}

ASSISTENTE = (
    "A mala não foi entregue ao comprador e segue em retorno sem movimento desde 29/09. "
    "O valor foi devolvido ao comprador e não há compensação para a loja."
)
NOTA_HUMANO = (
    "humano: não abri outra para não duplicar. Na tentativa das 10:37 o chat do Fale "
    "conosco (Marquezini - Mercado Livre) parou logo depois de escolhermos E-mail. Desde "
    "então, em Minhas consultas, há 2 consultas abertas hoje, Em revisão: 495516080 e "
    "495515391. As duas mostram a foto do anúncio desta venda (Mala M3 18 Preto), mas a "
    "página não diz o nº da venda e o chat não abre conversa nova. Pessoa: conferir se uma "
    "delas é da venda 2000018486518522 e ligar o nº ao chamado. Se nenhuma for, mandar "
    "abrir de novo."
)
CONVERSA = [
    {
        "quem": "nos",
        "texto": "Venda 2000018486518522: o pacote da devolução não chegou. Pedimos análise.",
        "quando": "2026-11-13T10:37",
    },
    {
        "quem": "plataforma",
        "texto": ASSISTENTE,
        "quando": "2026-11-13T10:38",
        "autor": "Assistente do ML",
    },
]


@pytest.fixture(autouse=True)
async def ia(db, monkeypatch, client, make_user, auth_as):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "nf_agent_token", _NF)
    auth_as(await make_user(permissions={"chamados": {"view": True, "edit": True}}))
    db.add(
        ChamadoCerebro(
            nome="IA de Chamado",
            token_hash=hashlib.sha256(_IA.encode()).hexdigest(),
            ligada=True,
        )
    )
    await db.commit()
    r = await client.post("/api/chamados/agent/cerebro", headers=IA, json={"abre_ml": True})
    assert r.status_code == 200 and r.json()["abre_ml"] is True, r.text


async def _abertura_pega(client, db) -> ChamadoMensagem:
    """Como o 297587: devolução do ML sem reclamação → a IA abre pelo Fale conosco."""
    ch = Chamado(
        id=uuid4(),
        pedido_bling="297587",
        pedido_marketplace="2000018486518522",
        plataforma="ml",
        conta="ML Marquezini",
        origem="devolucao",
        canal="robo",
    )
    db.add(ch)
    await db.flush()
    m = svc.nova_mensagem(
        ch,
        texto="O pacote da devolução ainda não chegou até nós.",
        tipo="abertura",
        direcao="enviada",
        autor_nome="sistema",
        status="pendente",
    )
    m.canal = "robo"
    m.created_at = datetime(2026, 11, 12, 13, 24, tzinfo=UTC)
    db.add(m)
    await db.commit()
    await db.refresh(m)
    r = await client.post(
        "/api/chamados/agent/abrir-ml/pegar", headers=IA, json={"mensagem_id": str(m.id)}
    )
    assert r.status_code == 200, r.text
    return m


async def _resultado(client, m: ChamadoMensagem, **body) -> dict:
    r = await client.post(
        "/api/chamados/agent/abrir-ml/resultado",
        headers=IA,
        json={"mensagem_id": str(m.id), **body},
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _mensagens(db, chamado_id) -> list[ChamadoMensagem]:
    # a API gravou por outra sessão: reler do banco por cima do que está em memória
    return list(
        (
            await db.execute(
                select(ChamadoMensagem)
                .where(ChamadoMensagem.chamado_id == chamado_id)
                .order_by(ChamadoMensagem.created_at)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )


async def _linha(client, pedido: str) -> dict:
    r = await client.get("/api/chamados", params={"search": pedido})
    assert r.status_code == 200, r.text
    (linha,) = r.json()["items"]
    return linha


async def _ia_ve(client, chamado_id) -> bool:
    r = await client.post(
        "/api/chamados/agent/analisar",
        headers=IA,
        json={"limite": 100, "plataforma": None, "canais": ["robo", "api", "manual"]},
    )
    assert r.status_code == 200, r.text
    return str(chamado_id) in {c["chamado_id"] for c in r.json()["chamados"]}


async def test_nota_humano_inteira_e_conversa_como_nota(client, db):
    assert len(NOTA_HUMANO) > 300
    m = await _abertura_pega(client, db)
    out = await _resultado(client, m, ok=False, erro=NOTA_HUMANO, conversa=CONVERSA)
    assert out["status"] == "falhou"

    msgs = await _mensagens(db, m.chamado_id)
    abertura = next(x for x in msgs if x.id == m.id)
    assert abertura.erro == NOTA_HUMANO  # inteira, com o pedido pra pessoa no fim

    notas = [x.texto for x in msgs if x.tipo == "sistema" and x.texto.startswith("Conversa ")]
    assert notas == [
        "Conversa no Fale conosco do ML (a consulta não foi aberta):\n"
        "13/11 10:37 · nós: Venda 2000018486518522: o pacote da devolução não chegou. "
        "Pedimos análise.\n"
        f"13/11 10:38 · Assistente do ML: {ASSISTENTE}"
    ]
    # nota não é fala: nenhuma enviada/recebida nova além da abertura
    assert [x.id for x in msgs if x.direcao in ("enviada", "recebida")] == [m.id]

    linha = await _linha(client, "297587")
    assert linha["status_aba"] == svc.ABA_ANALISE_HUMANO
    assert not await _ia_ve(client, m.chamado_id)


async def test_nova_tentativa_avisa_com_a_nota_inteira(client, db):
    longa = "Não saiu: " + "o assistente explicou o caso e o chat fechou. " * 12
    assert len(longa) > 300
    m = await _abertura_pega(client, db)
    out = await _resultado(client, m, ok=False, erro=longa.strip())
    assert out["status"] == "pendente"
    msgs = await _mensagens(db, m.chamado_id)
    aviso = next(x.texto for x in msgs if x.texto.startswith("Envio falhou (tentativa 1"))
    assert longa.strip() in aviso


async def test_abriu_com_conversa_guarda_a_nota_e_o_numero(client, db):
    m = await _abertura_pega(client, db)
    await _resultado(client, m, ok=True, consulta="495516080", conversa=CONVERSA)
    ch = await db.get(Chamado, m.chamado_id)
    await db.refresh(ch)
    assert ch.chamado == "495516080"
    msgs = await _mensagens(db, m.chamado_id)
    titulo = "Conversa no Fale conosco do ML, antes da consulta:"
    assert any(x.tipo == "sistema" and x.texto.startswith(titulo) for x in msgs)


async def test_nota_gigante_e_cortada_e_conversa_invalida_recusada(client, db):
    m = await _abertura_pega(client, db)
    r = await client.post(
        "/api/chamados/agent/abrir-ml/resultado",
        headers=IA,
        json={
            "mensagem_id": str(m.id),
            "ok": False,
            "erro": "x",
            "conversa": [{"quem": "eles", "texto": "x", "quando": "2026-11-13T10:37"}],
        },
    )
    assert r.status_code == 422
    out = await _resultado(client, m, ok=False, erro="humano: " + "y" * 5000)
    assert out["status"] == "falhou"
    x = await db.get(ChamadoMensagem, m.id)
    await db.refresh(x)
    assert len(x.erro) == 2000
