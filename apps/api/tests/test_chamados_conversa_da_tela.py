"""A conversa que a IA de Chamado teve na tela entra no chamado (13/11/2026).

296423 (Shopee KFA, malas descartadas pela SPX): a IA pediu a compensação à
atendente Ana pelo Assistente do Vendedor e só a decisão dela (coluna da direita)
chegou ao chamado. A "Conversa com a plataforma" ficou em branco e o chamado "sem
nº". O protocolo do chat da atendente não é consulta do Portal (conferido na tela
da KFA: /detail/<nº> abre vazio, a busca diz "Sem consulta"), então nenhum robô
de leitura traz essa conversa — quem tem o texto é a IA.

O que é garantido aqui:
- `conversa` vira fala nossa (réplica JÁ enviada, fora de qualquer fila) e fala da
  plataforma (resposta), com a hora da tela;
- a IA não acorda de novo pra ler a resposta que ela mesma gravou, nem quando a
  hora vem adiantada;
- mandar a mesma conversa de novo não duplica;
- `protocolo` vira o nº do chamado só quando ele está sem nº, e não põe o caso na
  fila de leitura do Portal.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import Chamado, ChamadoMensagem
from app.services import chamados as svc
from app.services import chamados_leitura

pytestmark = pytest.mark.asyncio

_TOKEN = "tok-nf-conversa-tela"  # noqa: S105
_HDR = {"X-Agent-Token": _TOKEN}

PROTOCOLO = "2120860248672489476"
PEDIDO_NOSSO = (
    "Olá, quero pedir compensação pelo pedido 260911QXMNRMVH: a SPX marcou "
    '"Pedido descartado" em 22/09 e o produto não voltou.'
)
RESPOSTA_ANA = (
    "Já existe uma solicitação de compensação aberta para este pedido, protocolo "
    f"{PROTOCOLO}, em análise. Se aprovada, o valor cai na carteira em até 15 dias úteis."
)


@pytest.fixture(autouse=True)
async def _token(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "nf_agent_token", _TOKEN)


async def _chamado(db, *, chamado: str | None = None) -> Chamado:
    """Como o 296423: aberto pela Logística, canal robô, com instrução pra IA."""
    ch = Chamado(
        id=uuid4(),
        pedido_bling="296423",
        pedido_marketplace="260911QXMNRMVH",
        plataforma="shopee",
        conta="kfa",
        origem="logistica",
        canal="robo",
        chamado=chamado,
    )
    db.add(ch)
    await db.flush()
    db.add(svc.registrar_sistema(ch, "Chamado registrado (origem logistica) por cairo sa"))
    ins = svc.nova_mensagem(
        ch,
        texto="Abrir na Shopee pedido de compensação por produto descartado pela logística.",
        tipo="instrucao",
        direcao="sistema",
        autor_nome="cairo sa",
        status="registrada",
    )
    ins.created_at = datetime.now(UTC) - timedelta(hours=3)
    db.add(ins)
    await db.commit()
    await db.refresh(ch)
    return ch


def _sp(h: int, m: int, *, dias: int = 0) -> str:
    """Hora da tela (São Paulo, sem fuso) de hoje − `dias`."""
    d = datetime.now(svc.SAO_PAULO).date() - timedelta(days=dias)
    return f"{d.isoformat()}T{h:02d}:{m:02d}"


async def _decidir(client, ch: Chamado, **extra) -> dict:
    body = {
        "chamado_id": str(ch.id),
        "classe": "shopee_descarte_compensacao_em_analise",
        "resumo": "Feito na tela (KFA - Shopee): pedi a compensação à atendente Ana.",
        "acao": "esperar",
        **extra,
    }
    r = await client.post("/api/chamados/agent/analise", headers=_HDR, json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def _falas(db, ch: Chamado) -> list[ChamadoMensagem]:
    return list(
        (
            await db.execute(
                select(ChamadoMensagem)
                .where(
                    ChamadoMensagem.chamado_id == ch.id,
                    ChamadoMensagem.direcao.in_(("enviada", "recebida")),
                )
                .order_by(ChamadoMensagem.created_at)
            )
        ).scalars()
    )


async def _ia_ve(client, ch: Chamado) -> bool:
    r = await client.post(
        "/api/chamados/agent/analisar",
        headers=_HDR,
        json={"limite": 100, "plataforma": None, "canais": ["robo", "api", "manual"]},
    )
    assert r.status_code == 200, r.text
    return str(ch.id) in {c["chamado_id"] for c in r.json()["chamados"]}


CONVERSA = [
    {"quem": "nos", "texto": PEDIDO_NOSSO, "quando": _sp(11, 2, dias=1)},
    {
        "quem": "plataforma",
        "texto": RESPOSTA_ANA,
        "quando": _sp(11, 5, dias=1),
        "autor": "Ana · atendente Shopee",
    },
]


async def test_conversa_da_tela_entra_no_chamado_e_o_protocolo_vira_o_numero(client, db):
    ch = await _chamado(db)
    assert await _ia_ve(client, ch)  # a instrução do Cairo está pendente

    out = await _decidir(client, ch, conversa=CONVERSA, protocolo=PROTOCOLO)
    assert out["conversa_gravada"] == 2

    nossa, dela = await _falas(db, ch)
    assert (nossa.direcao, nossa.tipo, nossa.status, nossa.canal) == (
        "enviada", "replica", "enviada", "robo",
    )
    assert nossa.autor_nome == "IA de Chamado · na tela"
    assert nossa.texto == PEDIDO_NOSSO
    assert (dela.direcao, dela.tipo, dela.autor_nome) == (
        "recebida", "resposta", "Ana · atendente Shopee",
    )
    # a hora da TELA (São Paulo), não a hora do POST
    assert dela.created_at.astimezone(svc.SAO_PAULO).strftime("%H:%M") == "11:05"
    assert dela.enviada_at == dela.created_at

    await db.refresh(ch)
    assert ch.chamado == PROTOCOLO
    # protocolo do chat não é consulta do Portal: nada de fila de leitura
    assert ch.chamado_de_tela is not True
    assert not chamados_leitura.e_portal_shopee(ch)
    sistema = (
        await db.execute(
            select(ChamadoMensagem.texto).where(
                ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.tipo == "sistema"
            )
        )
    ).scalars().all()
    assert any(f"Protocolo {PROTOCOLO} anotado" in t for t in sistema)

    # A análise é a mais nova: a IA não acorda pra ler a fala que ela mesma gravou,
    # e a linha fica aguardando a plataforma.
    assert not await _ia_ve(client, ch)
    msgs = await _falas(db, ch)
    analise = (
        await db.execute(
            select(ChamadoMensagem).where(
                ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.tipo == "analise"
            )
        )
    ).scalar_one()
    codigo, _quando = svc.status_da_aba(
        ch,
        ultima_fala=msgs[-1],
        ultima_analise=analise,
        analise_pede_humano=False,
        analise_pede_esperar=True,
    )
    assert codigo == svc.ABA_AGUARD_PLATAFORMA


async def test_hora_adiantada_nao_acorda_a_ia(client, db):
    """A IA errou o fuso (hora UTC como se fosse de Brasília): a fala ficaria no
    futuro e pareceria resposta nova depois da análise."""
    ch = await _chamado(db)
    adiantada = (datetime.now(svc.SAO_PAULO) + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M")
    out = await _decidir(
        client,
        ch,
        conversa=[{"quem": "plataforma", "texto": RESPOSTA_ANA, "quando": adiantada}],
    )
    assert out["conversa_gravada"] == 1
    (dela,) = await _falas(db, ch)
    assert dela.created_at <= datetime.now(UTC)
    assert dela.updated_at <= dela.created_at + timedelta(seconds=1)
    assert dela.autor_nome == "Shopee · na tela"
    assert not await _ia_ve(client, ch)


async def test_mesma_conversa_de_novo_nao_duplica(client, db):
    ch = await _chamado(db)
    assert (await _decidir(client, ch, conversa=CONVERSA))["conversa_gravada"] == 2
    # Rodada seguinte: a IA manda a conversa inteira de novo + uma fala nova.
    nova = {"quem": "nos", "texto": "Obrigado, vamos aguardar.", "quando": _sp(11, 7, dias=1)}
    out = await _decidir(client, ch, conversa=[*CONVERSA, nova])
    assert out["conversa_gravada"] == 1
    assert [m.texto for m in await _falas(db, ch)] == [
        PEDIDO_NOSSO,
        RESPOSTA_ANA,
        "Obrigado, vamos aguardar.",
    ]


async def test_falas_do_mesmo_minuto_ficam_na_ordem_da_tela(client, db):
    ch = await _chamado(db)
    mesmo = _sp(10, 30, dias=1)
    conversa = [
        {"quem": "nos", "texto": "Quero falar com um atendente.", "quando": mesmo},
        {"quem": "plataforma", "texto": "Conectando com um atendente.", "quando": mesmo},
        {"quem": "plataforma", "texto": "Olá, sou a Ana. Como posso ajudar?", "quando": mesmo},
    ]
    assert (await _decidir(client, ch, conversa=conversa))["conversa_gravada"] == 3
    assert [m.texto for m in await _falas(db, ch)] == [f["texto"] for f in conversa]


async def test_nossa_fala_mandada_como_da_plataforma_fica_de_fora(client, db):
    ch = await _chamado(db)
    out = await _decidir(
        client,
        ch,
        conversa=[
            {"quem": "nos", "texto": PEDIDO_NOSSO, "quando": _sp(11, 2, dias=1)},
            {"quem": "plataforma", "texto": PEDIDO_NOSSO, "quando": _sp(11, 3, dias=1)},
        ],
    )
    assert out["conversa_gravada"] == 1
    assert [m.direcao for m in await _falas(db, ch)] == ["enviada"]


async def test_protocolo_nao_troca_o_numero_que_o_chamado_ja_tem(client, db):
    ch = await _chamado(db, chamado="2609200HRVP6SWU")  # return_sn da devolução
    await _decidir(client, ch, protocolo=PROTOCOLO)
    await db.refresh(ch)
    assert ch.chamado == "2609200HRVP6SWU"


async def test_sem_conversa_nada_muda(client, db):
    ch = await _chamado(db)
    out = await _decidir(client, ch)
    assert out["conversa_gravada"] == 0
    assert await _falas(db, ch) == []
    await db.refresh(ch)
    assert ch.chamado is None


@pytest.mark.parametrize(
    "extra",
    [
        {"protocolo": "12 34"},
        {"conversa": [{"quem": "eles", "texto": "x", "quando": "2026-11-12T11:05"}]},
        {"conversa": [{"quem": "nos", "texto": "", "quando": "2026-11-12T11:05"}]},
        {"conversa": [{"quem": "nos", "texto": "x"}]},
    ],
)
async def test_decisao_com_conversa_ou_protocolo_invalido_e_recusada(client, db, extra):
    ch = await _chamado(db)
    r = await client.post(
        "/api/chamados/agent/analise",
        headers=_HDR,
        json={
            "chamado_id": str(ch.id),
            "classe": "x",
            "resumo": "y",
            "acao": "esperar",
            **extra,
        },
    )
    assert r.status_code == 422, r.text
