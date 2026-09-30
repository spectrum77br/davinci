"""Mãos do ML no Mac Santiago — responder na consulta do formulário de ajuda (30/09/2026).

298394 (consulta 484465159, Forpaper): a réplica do Cairo ficou "pendente" desde
29/09 porque ninguém pedia a fila "responder" do ML (o robô do Eduardo respondia
pelo e-mail do Tuta e parou ~24/09). Quem responde agora é o executor do Mac
Santiago, com uma senha própria (`chamados_leitores.responde_ml`).

O que é garantido aqui:
- só a senha com `responde_ml` abre a fila de respostas; a de leitura não;
- a fila entrega réplica pendente de consulta do ML (e só dela); `espiar` não marca;
- com as mãos do ML ativas, o token antigo (Eduardo) não recebe mais essas
  tarefas — senão os dois postariam a mesma réplica;
- o resultado segue as regras do robô antigo e, enviada, põe a consulta no topo
  da fila de leitura (a conversa com a nossa fala entra no histórico);
- as mãos do ML baixam a foto da réplica;
- réplica cancelada na lixeira não sai e não conta no Status da aba.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.models import Chamado, ChamadoAnexo, ChamadoLeitor, ChamadoMensagem
from app.services import chamados as svc

pytestmark = pytest.mark.asyncio

_MAOS = "tok-maos-ml-teste"  # noqa: S105
_LEITOR = "tok-so-leitura-teste"  # noqa: S105
_NF = "tok-nf-teste"  # noqa: S105
PERMS = {"chamados": {"view": True, "edit": True, "delete": True}}
TEXTO_A = (
    "A resposta cita a venda 2000018557795716, mas este chamado é da venda 2000015125791563 "
    "(entregue, estorno de R$ 310,03 em 23/09 sem devolução do produto). Peço a análise da "
    "venda correta."
)


@pytest.fixture(autouse=True)
async def _senhas(db, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "nf_agent_token", _NF)
    maos = ChamadoLeitor(
        nome="Mãos do ML (teste)",
        token_hash=hashlib.sha256(_MAOS.encode()).hexdigest(),
        responde_ml=True,
    )
    db.add_all(
        [
            maos,
            ChamadoLeitor(
                nome="Leitura (teste)", token_hash=hashlib.sha256(_LEITOR.encode()).hexdigest()
            ),
        ]
    )
    await db.commit()
    return maos


async def _consulta(
    db, *, plataforma: str = "Mercado Livre", de_tela: bool = True, consulta: str = "484465159",
    status: str = "pendente", tipo: str = "replica",
) -> tuple[Chamado, ChamadoMensagem]:
    ch = Chamado(
        id=uuid4(), pedido_bling="298394", pedido_marketplace="2000015125791563",
        plataforma=plataforma, conta="forpaper", origem="logistica", chamado=consulta,
        chamado_url=f"https://www.mercadolivre.com.br/cases/detail/{consulta}",
        canal="robo", chamado_de_tela=de_tela, leitura_robo_at=datetime.now(UTC),
    )
    db.add(ch)
    await db.flush()
    m = svc.nova_mensagem(
        ch, texto=TEXTO_A, tipo=tipo, direcao="enviada", autor_nome="cairo sa", status=status
    )
    m.canal = "robo"
    m.created_at = datetime(2026, 9, 29, 16, 41, tzinfo=UTC)
    db.add(m)
    await db.commit()
    await db.refresh(ch)
    await db.refresh(m)
    return ch, m


async def _fila(client, token: str = _MAOS, **body):
    return await client.post(
        "/api/chamados/agent/leitor/responder/fila", headers={"X-Agent-Token": token}, json=body
    )


async def _resultado(client, m: ChamadoMensagem, **body):
    return await client.post(
        "/api/chamados/agent/leitor/responder/resultado",
        headers={"X-Agent-Token": _MAOS},
        json={"mensagem_id": str(m.id), **body},
    )


# ------------------------------------------------------------------ quem entra


async def test_so_a_senha_das_maos_abre_a_fila_de_respostas(client, db):
    await _consulta(db)
    assert (await _fila(client, _LEITOR)).status_code == 403
    assert (await _fila(client, _NF)).status_code == 401
    assert (await _fila(client, "outra")).status_code == 401


# ------------------------------------------------------------------ a fila


async def test_fila_entrega_a_replica_da_consulta_do_ml(client, db):
    ch, m = await _consulta(db)
    r = await _fila(client, espiar=True)
    assert r.status_code == 200, r.text
    (t,) = r.json()["tarefas"]
    assert t["mensagem_id"] == str(m.id) and t["tipo"] == "responder"
    assert t["chamado"] == "484465159" and t["texto"] == TEXTO_A
    assert t["criada_em"].startswith("2026-09-29T16:41")  # a trava de "já enviada" usa
    await db.refresh(m)
    assert m.status == "pendente"  # espiar não marca
    (t,) = (await _fila(client)).json()["tarefas"]
    await db.refresh(m)
    assert m.status == "enviando"
    assert (await _fila(client)).json()["tarefas"] == []  # já está com ele


@pytest.mark.parametrize(
    "kw",
    [
        {"plataforma": "shopee", "consulta": "2101308949814067207"},
        {"de_tela": False},  # nº de reclamação da API
        {"tipo": "abertura", "status": "pendente"},
        {"status": "enviada"},
        {"status": "cancelada"},
    ],
)
async def test_fila_das_maos_exclui(client, db, kw):
    await _consulta(db, **kw)
    assert (await _fila(client, espiar=True)).json()["tarefas"] == []


async def test_robo_antigo_nao_recebe_mais_a_resposta_do_ml(client, db, _senhas):
    """Com as mãos do ML ativas, o `/agent/lease` do token antigo pula a resposta
    da consulta do ML; revogada a senha das mãos, ele volta a receber."""
    _, m = await _consulta(db)
    hdr = {"X-Agent-Token": _NF}
    for body in ({"tipo": "responder", "plataforma": "ml"}, {}):
        r = await client.post("/api/chamados/agent/lease", headers=hdr, json=body)
        assert r.status_code == 200 and r.json()["tarefas"] == [], r.text
    _senhas.revoked_at = datetime.now(UTC)
    await db.commit()
    r = await client.post(
        "/api/chamados/agent/lease", headers=hdr, json={"tipo": "responder", "plataforma": "ml"}
    )
    assert [t["mensagem_id"] for t in r.json()["tarefas"]] == [str(m.id)]


# ------------------------------------------------------------------ o resultado


async def test_enviada_marca_e_poe_a_consulta_no_topo_da_leitura(client, db):
    ch, m = await _consulta(db)
    await _fila(client)
    r = await _resultado(client, m, ok=True)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "enviada"
    await db.refresh(ch)
    assert ch.leitura_robo_at is None  # o leitor lê a página na mesma passada


async def test_falha_volta_pra_fila_ate_o_limite(client, db):
    ch, m = await _consulta(db)
    await _fila(client)
    r = await _resultado(client, m, ok=False, erro="botão Enviar não acendeu")
    assert r.status_code == 200 and r.json()["status"] == "pendente", r.text
    await db.refresh(ch)
    assert ch.leitura_robo_at is not None  # falha não fura a fila de leitura


async def test_resultado_so_de_replica_da_consulta_do_ml(client, db):
    _, m = await _consulta(db, plataforma="shopee", consulta="2101308949814067207")
    r = await _resultado(client, m, ok=True)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "chamado_fora_das_maos"


async def test_maos_do_ml_baixam_a_foto_da_replica(client, db):
    ch, m = await _consulta(db)
    a = ChamadoAnexo(
        chamado_id=ch.id, mensagem_id=m.id, filename="print.png", content_type="image/png",
        size_bytes=3, blob=b"png",
    )
    db.add(a)
    await db.commit()
    (t,) = (await _fila(client, espiar=True)).json()["tarefas"]
    assert t["anexos"] == [str(a.id)]
    r = await client.get(
        f"/api/chamados/agent/anexos/{a.id}", headers={"X-Agent-Token": _MAOS}
    )
    assert r.status_code == 200 and r.content == b"png"
    r = await client.get(
        f"/api/chamados/agent/anexos/{a.id}", headers={"X-Agent-Token": _LEITOR}
    )
    assert r.status_code == 401


# ------------------------------------------------------------ réplica cancelada


async def test_replica_cancelada_nao_sai_nem_conta_no_status(client, db, make_user, auth_as):
    """298394: a IA pediu humano em 24/09; a réplica do Cairo (pendente) pôs a linha
    em "na fila do robô". Cancelada, a linha volta pro que era antes."""
    auth_as(await make_user(permissions=PERMS))
    ch, m = await _consulta(db)
    analise = svc.nova_mensagem(
        ch, texto="Análise da IA de Chamado [x]: conferir a venda → precisa de humano",
        tipo="analise", direcao="sistema", autor_nome="IA de Chamado", status="registrada",
    )
    analise.created_at = m.created_at - timedelta(days=5)
    db.add(analise)
    await db.commit()

    antes = (await client.get("/api/chamados", params={"search": "298394"})).json()
    linha = next(x for x in antes["items"] if x["id"] == str(ch.id))
    assert linha["status_aba"] == svc.ABA_ANALISE_ROBO  # "na fila do robô"

    assert (await client.delete(f"/api/chamados/mensagens/{m.id}")).status_code == 204
    assert (await _fila(client, espiar=True)).json()["tarefas"] == []
    depois = (await client.get("/api/chamados", params={"search": "298394"})).json()
    linha = next(x for x in depois["items"] if x["id"] == str(ch.id))
    assert linha["status_aba"] != svc.ABA_ANALISE_ROBO
