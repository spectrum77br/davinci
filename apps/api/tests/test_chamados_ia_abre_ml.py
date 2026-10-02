"""IA de Chamado abre a consulta do ML pelo Fale conosco (30/09/2026, passo 4).

O chamado do ML sem mediação aberta e sem devolução pra revisar ia pro "robô do
formulário de ajuda" do Eduardo. Agora quem abre é a IA de Chamado no Mac Santiago
(assistente › atendente › E-mail › formulário — é conversa, então é a IA).

O que é garantido aqui:
- a IA sempre pode OLHAR a fila de aberturas (teste); pegar e devolver só com
  `abre_ml` ligado — e ligado, o token antigo (Eduardo) não recebe mais essas
  aberturas no `/agent/lease`;
- pegar marca `enviando` (a mesma abertura não é pega duas vezes);
- abriu: o nº da consulta vira protocolo de tela e a leitura do Mac Santiago
  passa a ler a consulta; não abriu: volta pra fila (regras do robô antigo).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.models import Chamado, ChamadoCerebro, ChamadoMensagem
from app.services import chamados as svc
from app.services import chamados_leitura

pytestmark = pytest.mark.asyncio

_NF = "tok-nf-legado"  # noqa: S105
_IA = "tok-ia-de-chamado"  # noqa: S105
IA = {"X-Agent-Token": _IA}
NF = {"X-Agent-Token": _NF}


@pytest.fixture(autouse=True)
async def ia(db, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "nf_agent_token", _NF)
    row = ChamadoCerebro(
        nome="IA de Chamado", token_hash=hashlib.sha256(_IA.encode()).hexdigest(), ligada=True
    )
    db.add(row)
    await db.commit()
    return row


async def _abertura(
    db, *, plataforma: str = "Mercado Livre", pedido: str = "298394"
) -> ChamadoMensagem:
    """Como o 298394 em 24/09: sem reclamação do comprador → robô do formulário."""
    ch = Chamado(
        id=uuid4(),
        pedido_bling=pedido,
        pedido_marketplace="2000015125791563",
        plataforma=plataforma,
        conta="forpaper",
        origem="logistica",
        canal="robo",
    )
    db.add(ch)
    await db.flush()
    m = svc.nova_mensagem(
        ch,
        texto="Olá, preciso de ajuda com a venda. O produto foi entregue.",
        tipo="abertura",
        direcao="enviada",
        autor_nome="sistema",
        status="pendente",
    )
    m.canal = "robo"
    m.created_at = datetime(2026, 9, 24, 10, 55, tzinfo=UTC)
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return m


async def _fila_ia(client) -> list[dict]:
    r = await client.post("/api/chamados/agent/abrir-ml/fila", headers=IA, json={})
    assert r.status_code == 200, r.text
    return r.json()["tarefas"]


async def _lease_antigo(client) -> list[dict]:
    r = await client.post(
        "/api/chamados/agent/lease", headers=NF, json={"tipo": "abrir", "plataforma": "ml"}
    )
    assert r.status_code == 200, r.text
    return r.json()["tarefas"]


async def _assumir(client, ligar: bool = True) -> dict:
    r = await client.post("/api/chamados/agent/cerebro", headers=IA, json={"abre_ml": ligar})
    assert r.status_code == 200, r.text
    return r.json()


async def test_sem_abre_ml_a_ia_so_olha_e_o_eduardo_segue(client, db):
    m = await _abertura(db)
    (t,) = await _fila_ia(client)
    assert t["mensagem_id"] == str(m.id) and t["tipo"] == "abrir"
    assert t["pedido_marketplace"] == "2000015125791563"
    await db.refresh(m)
    assert m.status == "pendente"  # olhar não marca
    r = await client.post(
        "/api/chamados/agent/abrir-ml/pegar", headers=IA, json={"mensagem_id": str(m.id)}
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "ia_nao_abre_ml"
    assert [x["mensagem_id"] for x in await _lease_antigo(client)] == [str(m.id)]


async def test_assumir_tira_a_abertura_do_ml_do_robo_antigo(client, db):
    m = await _abertura(db)
    assert (await _assumir(client))["abre_ml"] is True
    assert await _lease_antigo(client) == []
    r = await client.post("/api/chamados/agent/lease", headers=NF, json={})
    assert r.json()["tarefas"] == []
    await db.refresh(m)
    assert m.status == "pendente"
    assert (await _assumir(client, False))["abre_ml"] is False  # devolve a vez
    assert [x["mensagem_id"] for x in await _lease_antigo(client)] == [str(m.id)]


async def test_fila_da_ia_so_tem_abertura_do_ml(client, db):
    await _abertura(db, plataforma="shopee", pedido="292592")
    assert await _fila_ia(client) == []


async def test_pegar_marca_e_ninguem_pega_de_novo(client, db):
    m = await _abertura(db)
    await _assumir(client)
    r = await client.post(
        "/api/chamados/agent/abrir-ml/pegar", headers=IA, json={"mensagem_id": str(m.id)}
    )
    assert r.status_code == 200 and r.json()["mensagem_id"] == str(m.id), r.text
    await db.refresh(m)
    assert m.status == "enviando"
    r = await client.post(
        "/api/chamados/agent/abrir-ml/pegar", headers=IA, json={"mensagem_id": str(m.id)}
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "abertura_fora_da_fila"
    assert await _fila_ia(client) == []


async def test_abriu_vira_consulta_de_tela_e_entra_na_leitura(client, db):
    m = await _abertura(db)
    await _assumir(client)
    await client.post(
        "/api/chamados/agent/abrir-ml/pegar", headers=IA, json={"mensagem_id": str(m.id)}
    )
    r = await client.post(
        "/api/chamados/agent/abrir-ml/resultado",
        headers=IA,
        json={"mensagem_id": str(m.id), "ok": True, "consulta": "485999001"},
    )
    assert r.status_code == 200 and r.json()["status"] == "enviada", r.text
    ch = await db.get(Chamado, m.chamado_id)
    await db.refresh(ch)
    assert ch.chamado == "485999001" and ch.chamado_de_tela is True
    assert ch.chamado_url == "https://www.mercadolivre.com.br/cases/detail/485999001"
    assert ch.leitura_robo_at is None and chamados_leitura.e_consulta_ml(ch)


async def test_abriu_sem_numero_da_consulta_e_recusado(client, db):
    m = await _abertura(db)
    await _assumir(client)
    r = await client.post(
        "/api/chamados/agent/abrir-ml/resultado",
        headers=IA,
        json={"mensagem_id": str(m.id), "ok": True},
    )
    assert r.status_code == 422


async def test_nao_abriu_volta_pra_fila(client, db):
    m = await _abertura(db)
    await _assumir(client)
    await client.post(
        "/api/chamados/agent/abrir-ml/pegar", headers=IA, json={"mensagem_id": str(m.id)}
    )
    r = await client.post(
        "/api/chamados/agent/abrir-ml/resultado",
        headers=IA,
        json={"mensagem_id": str(m.id), "ok": False, "erro": "o assistente não ofereceu atendente"},
    )
    assert r.status_code == 200 and r.json()["status"] == "pendente", r.text
    assert [t["mensagem_id"] for t in await _fila_ia(client)] == [str(m.id)]


async def test_resultado_so_de_abertura_do_ml(client, db):
    m = await _abertura(db, plataforma="shopee", pedido="292592")
    await _assumir(client)
    r = await client.post(
        "/api/chamados/agent/abrir-ml/resultado",
        headers=IA,
        json={"mensagem_id": str(m.id), "ok": True, "consulta": "485999001"},
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "abertura_fora_da_ia"
    x = await db.get(ChamadoMensagem, m.id)
    assert x.status == "pendente"


async def test_humano_vai_direto_pra_pessoa(client, db):
    """O assistente do ML contou algo que muda o caso (297130: o reembolso parcial
    foi proposta nossa) — não adianta tentar de novo: vai pra Análise Humano."""
    m = await _abertura(db)
    await _assumir(client)
    await client.post(
        "/api/chamados/agent/abrir-ml/pegar", headers=IA, json={"mensagem_id": str(m.id)}
    )
    r = await client.post(
        "/api/chamados/agent/abrir-ml/resultado",
        headers=IA,
        json={
            "mensagem_id": str(m.id),
            "ok": False,
            "erro": "humano: o assistente disse que o reembolso parcial foi proposta nossa",
        },
    )
    assert r.status_code == 200 and r.json()["status"] == "falhou", r.text
    assert await _fila_ia(client) == []


async def test_parado_no_captcha_avisa_e_vai_pra_pessoa(client, db, monkeypatch):
    from app.services import chamados_ia_aviso

    avisos = []

    async def _avisar(session, ch, parado, resumo):
        avisos.append((ch.pedido_bling, parado))
        return "avisado"

    monkeypatch.setattr(chamados_ia_aviso, "avisar", _avisar)
    m = await _abertura(db)
    await _assumir(client)
    await client.post(
        "/api/chamados/agent/abrir-ml/pegar", headers=IA, json={"mensagem_id": str(m.id)}
    )
    r = await client.post(
        "/api/chamados/agent/abrir-ml/resultado",
        headers=IA,
        json={"mensagem_id": str(m.id), "ok": False, "parado": "captcha"},
    )
    assert r.status_code == 200 and r.json()["status"] == "falhou", r.text
    assert r.json()["erro"].startswith("humano: parou na tela (captcha)")
    assert avisos == [("298394", "captcha")]


async def _abertura_que_falhou_no_eduardo(db) -> ChamadoMensagem:
    """296985 (jlas2): a abertura falhou 3× no robô do Eduardo em 24/09."""
    m = await _abertura(db, pedido="296985")
    m.status = "falhou"
    m.erro = "não consegui selecionar E-mail no formulário"
    await db.commit()
    return m


async def test_replica_manual_sem_consulta_e_a_abertura_da_ia(client, db, make_user, auth_as):
    """02/10 (296985): o Cairo mandou o texto de novo pelo "Enfileirar pro robô"
    num chamado do ML que nunca foi aberto. Isso grava RÉPLICA — e ninguém pegava
    (a IA só via "abertura"; o Eduardo já não recebe ML)."""
    velha = await _abertura_que_falhou_no_eduardo(db)
    await _assumir(client)
    auth_as(await make_user(permissions={"chamados": {"view": True, "edit": True}}))
    r = await client.post(
        f"/api/chamados/{velha.chamado_id}/mensagens", data={"texto": "Olá, preciso de ajuda"}
    )
    assert r.status_code == 201 and r.json()["status"] == "pendente", r.text
    nova = r.json()["id"]
    assert r.json()["tipo"] == "replica"

    (t,) = await _fila_ia(client)
    assert t["mensagem_id"] == nova and t["tipo"] == "abrir"
    assert t["texto"] == "Olá, preciso de ajuda"
    assert await _lease_antigo(client) == []

    r = await client.post(
        "/api/chamados/agent/abrir-ml/pegar", headers=IA, json={"mensagem_id": nova}
    )
    assert r.status_code == 200, r.text
    r = await client.post(
        "/api/chamados/agent/abrir-ml/resultado",
        headers=IA,
        json={"mensagem_id": nova, "ok": True, "consulta": "486000123"},
    )
    assert r.status_code == 200 and r.json()["status"] == "enviada", r.text
    ch = await db.get(Chamado, velha.chamado_id)
    await db.refresh(ch)
    assert ch.chamado == "486000123" and chamados_leitura.e_consulta_ml(ch)
    await db.refresh(velha)
    assert velha.status == "falhou"  # a abertura antiga fica como estava
    assert await _fila_ia(client) == []


async def test_replica_de_consulta_ja_aberta_nao_e_da_ia(client, db):
    """Com a consulta aberta, a réplica é das mãos do ML (responder) — a IA não
    abre outra consulta nem marca a réplica."""
    m = await _abertura(db)
    ch = await db.get(Chamado, m.chamado_id)
    ch.chamado = "485999001"
    rep = svc.nova_mensagem(
        ch, texto="Retomando", tipo="replica", direcao="enviada", autor_nome="cairo sa",
        status="pendente",
    )
    rep.canal = "robo"
    db.add(rep)
    await db.commit()
    await _assumir(client)
    assert await _fila_ia(client) == []
    r = await client.post(
        "/api/chamados/agent/abrir-ml/resultado",
        headers=IA,
        json={"mensagem_id": str(rep.id), "ok": False, "erro": "x"},
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "abertura_fora_da_ia"
    await db.refresh(rep)
    assert rep.status == "pendente"
