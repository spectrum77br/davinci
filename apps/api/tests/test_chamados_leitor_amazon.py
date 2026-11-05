"""Executor de leitura lê o caso do suporte da Amazon (Vinicius, 05/11/2026).

288967 (caso 23057891781, KFA): a resposta do suporte ao vendedor só existia no
Seller Central e no e-mail principal do caso, e o chamado não ficava sabendo.

O que é garantido aqui:
- o caso só vai pra quem pede `amazon_lojas`, e só das lojas pedidas (o nome da
  loja no chamado varia: "kfa", "Amazon KFA");
- vale o chamado aberto à mão: basta o nº do caso (só dígitos) no `chamado`;
- reler a página não duplica a fala;
- a resposta acorda a IA de Chamado, mesmo com análise feita entre a hora em que
  a Amazon respondeu e a hora em que o robô leu.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.models import Chamado, ChamadoLeitor, ChamadoMensagem, UserRole
from app.routers.chamados import _trabalho_do_cerebro
from app.services import chamados as svc
from app.services import chamados_leitura
from sqlalchemy import select

pytestmark = pytest.mark.asyncio

_LEITOR = "tok-leitor-amazon-teste"  # noqa: S105
_HDR = {"X-Agent-Token": _LEITOR}

CASO = "23057891781"
RESPOSTA = (
    "Olá vendedor,\n\nAcionamos a equipe de transporte para localizar o pacote do pedido "
    "701-6560199-9046606. Retornaremos assim que tivermos uma atualização."
)


@pytest.fixture(autouse=True)
async def _leitor(db, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "nf_agent_token", "tok-nf-teste")
    db.add(
        ChamadoLeitor(
            nome="Executor de leitura (teste Amazon)",
            token_hash=hashlib.sha256(_LEITOR.encode()).hexdigest(),
        )
    )
    await db.commit()


async def _caso_amazon(
    db,
    *,
    caso: str = CASO,
    conta: str = "Amazon KFA",
    plataforma: str = "amazon",
    resolvido: bool = False,
    status_plataforma: str | None = None,
) -> Chamado:
    """Como o 288967: chamado aberto pela devolução, caso gravado à mão."""
    ch = Chamado(
        id=uuid4(),
        pedido_bling="288967",
        pedido_marketplace="701-6560199-9046606",
        plataforma=plataforma,
        conta=conta,
        origem="devolucao",
        chamado=caso,
        canal="manual",
        resolvido=resolvido,
        status_plataforma=status_plataforma,
    )
    db.add(ch)
    await db.commit()
    await db.refresh(ch)
    return ch


async def _fila(client, **body) -> list[dict]:
    r = await client.post("/api/chamados/agent/leitor/fila", headers=_HDR, json=body)
    assert r.status_code == 200, r.text
    return r.json()["casos"]


async def _resultado(client, ch: Chamado, falas: list[dict]) -> dict:
    r = await client.post(
        "/api/chamados/agent/leitor/resultado",
        headers=_HDR,
        json={"chamado_id": str(ch.id), "ok": True, "falas": falas, "historico": "Caso na Amazon"},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _fala(quando: datetime) -> dict:
    return {"texto": RESPOSTA, "quando": quando.isoformat(), "autor": "Amazon (Rafael C.)"}


async def _recebidas(db, ch: Chamado) -> list[ChamadoMensagem]:
    return list(
        (
            await db.execute(
                select(ChamadoMensagem).where(
                    ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.direcao == "recebida"
                )
            )
        ).scalars()
    )


async def _ia_ve(db, ch: Chamado) -> bool:
    rows = (await db.execute(_trabalho_do_cerebro(None, ["robo", "api", "manual"]))).scalars()
    return ch.id in {c.id for c in rows}


async def test_caso_so_vai_pra_quem_pede_a_loja(client, db):
    await _caso_amazon(db)
    assert await _fila(client, contas=[], espiar=True) == []
    assert await _fila(client, contas=[], espiar=True, amazon_lojas=["kia"]) == []
    casos = await _fila(client, contas=[], espiar=True, amazon_lojas=["kfa"])
    assert [c["tipo"] for c in casos] == ["amazon_caso"]
    assert casos[0]["chamado"] == CASO
    assert casos[0]["chamado_url"].endswith(f"view-case?caseID={CASO}")


@pytest.mark.parametrize("conta", ["kfa", "KFA", "Amazon KFA", "KFA - Amazon"])
async def test_nome_da_loja_no_chamado_varia(client, db, conta):
    await _caso_amazon(db, conta=conta)
    assert len(await _fila(client, contas=[], espiar=True, amazon_lojas=["kfa"])) == 1


@pytest.mark.parametrize(
    "extra",
    [
        {"caso": "52020-49750-0878840"},  # SAFE-T, não é caso
        {"caso": "701-6560199-9046606"},
        {"resolvido": True},
        {"status_plataforma": "encerrado"},
        {"plataforma": "shopee"},
    ],
)
async def test_fora_da_fila(client, db, extra):
    await _caso_amazon(db, **extra)
    assert await _fila(client, contas=[], espiar=True, amazon_lojas=["kfa"]) == []


async def test_entrega_marca_e_respeita_a_cadencia(client, db):
    ch = await _caso_amazon(db)
    assert len(await _fila(client, contas=[], amazon_lojas=["kfa"])) == 1
    # entregue: não volta enquanto o robô está lendo
    assert await _fila(client, contas=[], amazon_lojas=["kfa"]) == []
    await _resultado(client, ch, [])
    assert await _fila(client, contas=[], amazon_lojas=["kfa"]) == []


async def test_fala_entra_como_recebida_com_a_hora_da_amazon(client, db):
    ch = await _caso_amazon(db)
    quando = datetime(2026, 11, 5, 15, 30, tzinfo=UTC)
    out = await _resultado(client, ch, [_fala(quando)])
    assert out["falas_novas"] == 1
    (m,) = await _recebidas(db, ch)
    assert m.autor_nome == "Amazon (Rafael C.)"
    assert m.tipo == "resposta"
    assert m.created_at == quando


async def test_reler_a_mesma_pagina_nao_duplica(client, db):
    ch = await _caso_amazon(db)
    quando = datetime(2026, 11, 5, 15, 30, tzinfo=UTC)
    await _resultado(client, ch, [_fala(quando)])
    out = await _resultado(client, ch, [_fala(quando)])
    assert out["falas_novas"] == 0
    assert out["duplicadas"] == 1
    assert len(await _recebidas(db, ch)) == 1


async def test_respondidas_nao_fecha_o_chamado(client, db):
    ch = await _caso_amazon(db)
    out = await _resultado(client, ch, [_fala(datetime(2026, 11, 5, 15, 30, tzinfo=UTC))])
    assert out["encerrado"] is False
    await db.refresh(ch)
    assert ch.resolvido is False


async def test_resposta_acorda_a_ia(client, db):
    ch = await _caso_amazon(db)
    assert not await _ia_ve(db, ch)
    await _resultado(client, ch, [_fala(datetime(2026, 11, 5, 15, 30, tzinfo=UTC))])
    assert await _ia_ve(db, ch)


async def _analise(db, ch: Chamado, quando: datetime) -> None:
    m = svc.nova_mensagem(
        ch,
        texto="Análise da IA de Chamado [teste]: esperar.",
        tipo="analise",
        direcao="sistema",
        autor_nome="IA de Chamado",
        status="registrada",
    )
    m.created_at = quando
    db.add(m)
    await db.commit()


async def test_resposta_lida_depois_da_analise_acorda_a_ia(client, db):
    """A Amazon respondeu, a IA analisou por outro motivo e só depois o robô leu:
    pela hora da Amazon a fala pareceria já analisada."""
    ch = await _caso_amazon(db)
    agora = datetime.now(UTC)
    await _analise(db, ch, agora - timedelta(hours=1))
    await _resultado(client, ch, [_fala(agora - timedelta(hours=2))])
    assert await _ia_ve(db, ch)
    # analisou depois da leitura: a fala foi vista e a IA não volta
    await _analise(db, ch, datetime.now(UTC) + timedelta(seconds=1))
    assert not await _ia_ve(db, ch)


async def _fala_antiga(db, ch: Chamado, quando: datetime, *, canal: str) -> ChamadoMensagem:
    """Fala que chegou agora, com a hora da plataforma antes da última análise."""
    m = svc.nova_mensagem(
        ch, texto=RESPOSTA, tipo="resposta", direcao="recebida", autor_nome="x", status="registrada"
    )
    m.canal = canal
    m.created_at = m.enviada_at = quando
    db.add(m)
    await db.commit()
    return m


@pytest.mark.parametrize("plataforma", ["shopee", "Mercado Livre"])
async def test_fala_lida_na_tela_acorda_a_ia_no_ml_e_na_shopee(db, plataforma):
    ch = await _caso_amazon(db, plataforma=plataforma)
    agora = datetime.now(UTC)
    await _analise(db, ch, agora - timedelta(hours=1))
    await _fala_antiga(db, ch, agora - timedelta(hours=2), canal="robo")
    assert await _ia_ve(db, ch)


async def test_fala_da_api_segue_pela_hora_da_plataforma(db):
    """A fala da API é regravada quando o link assinado muda: pela hora da gravação
    ela acordaria a IA a cada passada (TikTok, 303842)."""
    ch = await _caso_amazon(db, plataforma="tiktok")
    agora = datetime.now(UTC)
    await _analise(db, ch, agora - timedelta(hours=1))
    await _fala_antiga(db, ch, agora - timedelta(hours=2), canal="api")
    assert not await _ia_ve(db, ch)


async def test_lixeirinha_nao_acorda_a_ia(db):
    """Esconder a fala mexe na linha, e isso não é resposta nova."""
    ch = await _caso_amazon(db)
    agora = datetime.now(UTC)
    m = await _fala_antiga(db, ch, agora - timedelta(hours=2), canal="robo")
    await _analise(db, ch, datetime.now(UTC) + timedelta(seconds=1))
    assert not await _ia_ve(db, ch)
    m.excluida_at = datetime.now(UTC) + timedelta(seconds=2)
    await db.commit()
    assert not await _ia_ve(db, ch)


async def test_atualizar_fura_a_fila(client, db, make_user, auth_as):
    ch = await _caso_amazon(db)
    ch.leitura_robo_at = datetime.now(UTC)
    await db.commit()
    auth_as(await make_user(role=UserRole.ADMIN))
    r = await client.post(f"/api/chamados/{ch.id}/reler")
    assert r.status_code == 200, r.text
    await db.refresh(ch)
    assert ch.leitura_robo_at is None


async def test_resultado_recusa_chamado_que_nao_e_caso(client, db):
    ch = await _caso_amazon(db, caso="52020-49750-0878840")
    r = await client.post(
        "/api/chamados/agent/leitor/resultado",
        headers=_HDR,
        json={"chamado_id": str(ch.id), "ok": True, "falas": []},
    )
    assert r.status_code == 409


async def test_loja_amazon():
    assert chamados_leitura.loja_amazon("Amazon KFA") == "kfa"
    assert chamados_leitura.loja_amazon("kfa") == "kfa"
    assert chamados_leitura.loja_amazon("KFA - Amazon") == "kfa"
    assert chamados_leitura.loja_amazon(None) == ""
