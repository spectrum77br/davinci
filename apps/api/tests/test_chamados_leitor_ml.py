"""Executor de leitura lê a consulta do formulário de ajuda do ML (Vinicius, 30/09/2026).

298394 (consulta 484465159, Forpaper): a parte de chamados do ML sai do
computador do Eduardo pro Mac Santiago. A resposta do ML era lida pelo monitor
do e-mail do Tuta, do lado dele; a página mercadolivre.com.br/cases/detail/<N>
mostra a conversa inteira, mas só com o DIA de cada fala ("24 de setembro").

O que é garantido aqui:
- a consulta do ML só vai pra quem pede `ml_lojas`, e só das lojas pedidas
  (o nome da loja no chamado varia: "forpaper", "ML Forpaper");
- a fala só com o dia entra uma vez (reler a página não duplica) e com a hora
  puxada pro fim do dia;
- a resposta que o monitor do e-mail já tinha gravado não entra de novo.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.models import Chamado, ChamadoLeitor, ChamadoMensagem
from app.services import chamados as svc
from app.services import chamados_leitura
from sqlalchemy import select

pytestmark = pytest.mark.asyncio

_LEITOR = "tok-leitor-ml-teste"  # noqa: S105
_HDR = {"X-Agent-Token": _LEITOR}

CONSULTA = "484465159"
RESPOSTA_ML = (
    "Olá, FORPAPER COMERCIO E LOCACAO DE MAQUINAS E EQUIPAMENTOS LTDA.\n\n"
    "Entendo que você tem problemas com a reclamação N.° 484242407 correspondente à venda "
    "N.° 2000018557795716.\n\nNão se preocupe! Vamos encaminhar o seu caso para a equipe "
    "especializada analisar detalhadamente toda a situação e poder emitir uma conclusão."
)


@pytest.fixture(autouse=True)
async def _leitor(db, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "nf_agent_token", "tok-nf-teste")
    db.add(
        ChamadoLeitor(
            nome="Executor de leitura (teste ML)",
            token_hash=hashlib.sha256(_LEITOR.encode()).hexdigest(),
        )
    )
    await db.commit()


async def _consulta_ml(
    db,
    *,
    consulta: str = CONSULTA,
    conta: str = "forpaper",
    plataforma: str = "Mercado Livre",
    de_tela: bool = True,
    resolvido: bool = False,
    status_plataforma: str | None = None,
    chamado_url: str | None = f"https://www.mercadolivre.com.br/cases/detail/{CONSULTA}",
) -> Chamado:
    """Como o 298394: aberto pelo robô no formulário de ajuda do ML, protocolo
    capturado na tela."""
    ch = Chamado(
        id=uuid4(),
        pedido_bling="298394",
        pedido_marketplace="2000015125791563",
        plataforma=plataforma,
        conta=conta,
        origem="logistica",
        chamado=consulta,
        chamado_url=chamado_url,
        canal="robo",
        chamado_de_tela=de_tela,
        resolvido=resolvido,
        status_plataforma=status_plataforma,
    )
    db.add(ch)
    await db.flush()
    m = svc.nova_mensagem(
        ch,
        texto="Olá, preciso de ajuda com a venda. O produto foi enviado e entregue.",
        tipo="abertura",
        direcao="enviada",
        autor_nome="sistema",
        status="enviada",
    )
    m.canal = "robo"
    m.created_at = m.enviada_at = datetime(2026, 9, 24, 8, 25, tzinfo=svc.SAO_PAULO)
    db.add(m)
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
        json={"chamado_id": str(ch.id), "falas": falas, "historico": "Consulta 484465159…"},
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _recebidas(db, ch: Chamado) -> list[ChamadoMensagem]:
    return list(
        (
            await db.execute(
                select(ChamadoMensagem)
                .where(ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.direcao == "recebida")
                .order_by(ChamadoMensagem.created_at)
            )
        ).scalars()
    )


# ------------------------------------------------------------------ a fila


@pytest.mark.parametrize(
    ("conta", "chave"),
    [
        ("forpaper", "forpaper"),
        ("ML Forpaper", "forpaper"),
        ("Mercado Livre Aguiar 2", "aguiar2"),
        ("Aguiar 2 - ML", "aguiar2"),
        ("Lucas MEI", "lucasmei"),
    ],
)
async def test_loja_ml_tira_a_plataforma_do_nome(conta, chave):
    assert chamados_leitura.loja_ml(conta) == chave


async def test_consulta_ml_so_entra_quando_o_executor_pede(client, db):
    """O executor de hoje (sem `ml_lojas`) não recebe consulta do ML — ele buscaria
    o pedido no Seller Center da Shopee."""
    ch = await _consulta_ml(db)
    assert await _fila(client, portal=True, consultas=True, espiar=True) == []
    casos = await _fila(client, contas=[], ml_lojas=["forpaper"])
    assert [c["chamado_id"] for c in casos] == [str(ch.id)]
    assert casos[0]["tipo"] == "ml_consulta"
    assert casos[0]["chamado_url"] == f"https://www.mercadolivre.com.br/cases/detail/{CONSULTA}"


async def test_consulta_ml_sem_url_monta_a_pagina_pelo_numero(client, db):
    await _consulta_ml(db, chamado_url=None)
    (caso,) = await _fila(client, ml_lojas=["forpaper"], espiar=True)
    assert caso["chamado_url"] == f"https://www.mercadolivre.com.br/cases/detail/{CONSULTA}"


async def test_ml_lojas_filtra_pela_loja_com_perfil(client, db):
    forpaper = await _consulta_ml(db, conta="ML Forpaper")
    aguiar = await _consulta_ml(db, consulta="484000001", conta="ML Aguiar 2")
    so_forpaper = await _fila(client, ml_lojas=["forpaper"], espiar=True)
    assert [c["chamado_id"] for c in so_forpaper] == [str(forpaper.id)]
    ambos = await _fila(client, ml_lojas=["forpaper", "aguiar2"], espiar=True)
    assert {c["chamado_id"] for c in ambos} == {str(forpaper.id), str(aguiar.id)}


async def test_contas_da_shopee_e_lojas_do_ml_sao_independentes(client, db):
    """Loja do ML sem perfil da Shopee (e vice-versa): cada ramo usa a lista dele."""
    shopee = Chamado(
        id=uuid4(), pedido_bling="292592", pedido_marketplace="260826D2E44FBF",
        plataforma="shopee", conta="Shopee Marquezini", origem="devolucao",
        chamado="2101308949814067207", canal="robo", chamado_de_tela=True,
    )
    db.add(shopee)
    await db.commit()
    ml = await _consulta_ml(db)
    so_ml = await _fila(client, contas=[], portal=True, ml_lojas=["forpaper"], espiar=True)
    assert [c["chamado_id"] for c in so_ml] == [str(ml.id)]
    os_dois = await _fila(
        client, contas=["Shopee Marquezini"], portal=True, ml_lojas=["forpaper"], espiar=True
    )
    assert {c["chamado_id"]: c["tipo"] for c in os_dois} == {
        str(shopee.id): "portal",
        str(ml.id): "ml_consulta",
    }
    assert await _fila(client, contas=[], portal=True, ml_lojas=[], espiar=True) == []


@pytest.mark.parametrize(
    "kw",
    [
        {"de_tela": False},  # nº de reclamação da API: o sync lê pela API
        {"resolvido": True},
        {"status_plataforma": svc.STATUS_ENCERRADO},
        {"plataforma": "shopee"},
        {"consulta": "MLB-484465159"},
    ],
)
async def test_consulta_ml_exclui(client, db, kw):
    await _consulta_ml(db, **kw)
    assert await _fila(client, ml_lojas=["forpaper"]) == []


# ------------------------------------------------------ o que ele leu de volta


async def test_resposta_do_ml_so_com_o_dia_entra_uma_vez_no_fim_do_dia(client, db):
    ch = await _consulta_ml(db)
    fala = {
        "texto": RESPOSTA_ML,
        "quando": "2026-09-24T00:00:00-03:00",
        "autor": "Mercado Livre",
        "so_dia": True,
    }
    r = await _resultado(client, ch, [fala])
    assert r["falas_novas"] == 1
    (m,) = await _recebidas(db, ch)
    assert m.created_at == datetime(2026, 9, 24, 23, 59, 59, tzinfo=svc.SAO_PAULO)
    assert m.autor_nome == "Mercado Livre"
    # reler a página no dia seguinte: mesma fala, mesmo dia → não duplica
    r = await _resultado(client, ch, [fala])
    assert r["falas_novas"] == 0 and r["duplicadas"] == 1
    assert len(await _recebidas(db, ch)) == 1


async def test_duas_respostas_no_mesmo_dia_ficam_na_ordem_da_pagina(client, db):
    """483240330: o ML respondeu duas vezes em 18/09 (Vanessa e Bianca)."""
    ch = await _consulta_ml(db)
    dia = "2026-09-18T00:00:00-03:00"
    primeira = RESPOSTA_ML
    segunda = "Olá, boa tarde, Camila! Tudo bem? Me chamo Bianca, sou representante do ML."
    r = await _resultado(
        client,
        ch,
        [
            {"texto": primeira, "quando": dia, "so_dia": True},
            {"texto": segunda, "quando": dia, "so_dia": True},
        ],
    )
    assert r["falas_novas"] == 2
    a, b = await _recebidas(db, ch)
    assert (a.texto, b.texto) == (primeira, segunda)
    assert b.created_at == datetime(2026, 9, 18, 23, 59, 59, tzinfo=svc.SAO_PAULO)
    assert a.created_at == b.created_at - timedelta(seconds=1)


async def test_resposta_do_ml_de_hoje_entra_com_a_hora_da_leitura(client, db):
    ch = await _consulta_ml(db)
    hoje = datetime.now(svc.SAO_PAULO).date()
    fala = {
        "texto": RESPOSTA_ML,
        "quando": f"{hoje.isoformat()}T00:00:00-03:00",
        "autor": "Mercado Livre",
        "so_dia": True,
    }
    antes = datetime.now(UTC)
    await _resultado(client, ch, [fala])
    (m,) = await _recebidas(db, ch)
    assert antes - timedelta(seconds=5) <= m.created_at <= datetime.now(UTC)
    r = await _resultado(client, ch, [fala])
    assert r["duplicadas"] == 1 and len(await _recebidas(db, ch)) == 1


async def test_resposta_que_o_monitor_do_email_ja_trouxe_nao_entra_de_novo(client, db):
    """O monitor do Eduardo gravou a resposta com o cabeçalho e o rodapé da página."""
    ch = await _consulta_ml(db)
    monitor = svc.nova_mensagem(
        ch,
        texto=(
            f"Mercado Livre 24 de setembro\n{RESPOSTA_ML}\n\n"
            "Retomar consulta\n\nMais informações"
        ),
        tipo="resposta",
        direcao="recebida",
        autor_nome="monitor",
        status="registrada",
    )
    monitor.created_at = datetime(2026, 9, 24, 9, 7, tzinfo=svc.SAO_PAULO)
    db.add(monitor)
    await db.commit()
    r = await _resultado(
        client,
        ch,
        [{"texto": RESPOSTA_ML, "quando": "2026-09-24T00:00:00-03:00", "so_dia": True}],
    )
    assert r["falas_novas"] == 0 and r["duplicadas"] == 1
    assert len(await _recebidas(db, ch)) == 1


async def test_nao_escreve_em_consulta_ml_que_nao_e_de_tela(client, db):
    ch = await _consulta_ml(db, de_tela=False)
    r = await client.post(
        "/api/chamados/agent/leitor/resultado",
        headers=_HDR,
        json={"chamado_id": str(ch.id), "falas": [], "historico": "x"},
    )
    assert r.status_code == 409
