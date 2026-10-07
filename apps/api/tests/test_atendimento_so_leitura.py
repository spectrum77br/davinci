"""Atendimento em SÓ LEITURA para a equipe (Eduardo, 07/10/2026).

"Pode liberar pras outras pessoas do DaVinci verem pra já obtermos
feedbacks, mas claro por enquanto só leitura ... continua só sugerindo ali se
clicar." Com a trava da fase de observação ligada (`rota.SO_ADMIN`, como vai
para produção):

- toda pessoa ATIVA (admin ou não; menos o operador de estoque, que o web
  prende no /controle-estoque) LÊ todas as rotas de leitura, respeitando o
  escopo por equipe (quem tem equipe vê só as lojas dela; o Direct do
  Instagram e as lojas sem integração só para quem vê tudo);
- quem só lê pede a sugestão da IA ("Sugerir agora", com teto por hora e
  dentro do teto DIÁRIO da IA) e dá 👍/👎 nela — sem trocar a nota que OUTRA
  pessoa deu (que vem marcada `de_outra_pessoa`), mas preenchendo a linha
  sem nota —, pede a prévia de uma automática e registra a abertura do
  AdsPower (`rota.ROTAS_DE_QUEM_LE`);
- qualquer outra escrita de quem só lê é 403 `atendimento_so_leitura`, MESMO
  para admin fora de ATENDIMENTO_USUARIOS;
- quem está na lista mexe em tudo, como antes;
- o /api/auth/me traz `atendimento` (vê) e `atendimento_mexe` (mexe).

Os outros testes do atendimento desligam a trava (`SO_ADMIN = False`) porque
cobrem a permissão fina, que volta quando a fase de observação acabar.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import update

from app.config import get_settings
from app.main import app
from app.models import (
    AtendimentoCanal,
    AtendimentoRascunho,
    DmConversa,
    DmMensagem,
    Integration,
    IntegrationPlatform,
    User,
    UserRole,
    UserStatus,
)
from app.models.pricing import StoreInfo
from app.redis_client import redis
from app.routers import atendimento as rota
from app.security.cipher import encrypt_json
from app.services import vigia_leitura_atendimento
from app.services.atendimento import gravar, ia

URL = "/api/atendimento"
AGORA = datetime.now(UTC)
DONO = "dono@davinci-test.com"
TUDO = {"atendimento": {"view": True, "edit": True, "delete": True}}
SO_LEITURA = "atendimento_so_leitura"


@pytest.fixture(autouse=True)
def _fase_de_observacao(monkeypatch):
    """Como em produção: trava ligada, a lista com UM e-mail, nada liga loja nem IA."""
    monkeypatch.setattr(rota, "SO_ADMIN", True)
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_usuarios", DONO)
    for nome in (
        "atendimento_leitura_ativa",
        "atendimento_ia_ativa",
        "atendimento_envio_ativo",
        "atendimento_auto_ativo",
        "atendimento_simulador",
        "atendimento_alerta_telegram",
    ):
        monkeypatch.setattr(s, nome, False)
    # Sem o teto diário da IA (o contador do dia mora no Redis, entre testes);
    # o teste do teto o liga.
    monkeypatch.setattr(s, "atendimento_ia_teto_diario", 0)
    return s


# ─────────────── fábrica ───────────────


async def _loja(db, dono: User, nome: str) -> tuple[Integration, AtendimentoCanal]:
    integ = Integration(
        user_id=dono.id,
        platform=IntegrationPlatform.SHOPEE,
        name=nome,
        credentials=encrypt_json({"access_token": "t"}),
    )
    db.add(integ)
    await db.flush()
    canal = AtendimentoCanal(
        integration_id=integ.id, plataforma="shopee", canal="chat", modo="observar", status="ok"
    )
    db.add(canal)
    await db.commit()
    return integ, canal


async def _conversa(db, integ, canal, externo_id: str):
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=integ,
        plataforma="shopee",
        canal_nome="chat",
        externo_id=externo_id,
        pedido_marketplace=None,
        comprador_nome="Comprador",
    )
    await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id=f"{externo_id}-c",
        autor="cliente",
        texto=f"pergunta {externo_id}",
        enviada_em=AGORA - timedelta(hours=1),
    )
    await db.commit()
    return conversa


async def _rascunho(db, conversa, status: str = "pendente") -> AtendimentoRascunho:
    r = AtendimentoRascunho(
        conversa_id=conversa.id,
        texto="Seu pedido saiu hoje.",
        categoria="rastreio",
        confianca=0.9,
        precisa_humano=False,
        status=status,
    )
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return r


async def _dm(db) -> DmConversa:
    c = DmConversa(conta="charlots_br", participante_id="178414", participante_nome="Maria")
    db.add(c)
    await db.flush()
    db.add(
        DmMensagem(
            conversa_id=c.id,
            mid="m1",
            direcao="recebida",
            texto="tem na cor azul?",
            ocorrido_em=AGORA - timedelta(hours=2),
        )
    )
    await db.commit()
    return c


@pytest.fixture
async def dono(make_user) -> User:
    """O admin da lista (ATENDIMENTO_USUARIOS): mexe em tudo."""
    return await make_user(email=DONO, role=UserRole.ADMIN)


@pytest.fixture
async def leitor(make_user) -> User:
    """Pessoa ativa comum, sem permissão nenhuma e sem equipe: lê tudo, não mexe."""
    return await make_user()


def _rotas_da_caixa() -> list[APIRoute]:
    """Toda rota do app sob /api/atendimento, menos as do robô (token próprio)."""
    return [
        r
        for r in app.routes
        if isinstance(r, APIRoute)
        and r.path.startswith(URL + "/")
        and not r.path.startswith(URL + "/robo/")
    ]


def _chamadas() -> list[tuple[str, str, str]]:
    """(método, molde, caminho com os {parâmetros} preenchidos) de cada rota da caixa."""
    saida = []
    for r in _rotas_da_caixa():
        caminho = re.sub(r"\{[^}]+\}", str(uuid4()), r.path)
        for metodo in sorted(r.methods - {"HEAD", "OPTIONS"}):
            saida.append((metodo, r.path, caminho))
    return saida


# ─────────────── a trava: em toda rota, e o que passa para quem só lê ───────────────


def test_trava_em_todas_as_rotas_e_a_lista_de_quem_le_existe():
    assert rota.SO_ADMIN is True, "a caixa segue na fase de observação"
    rotas = _rotas_da_caixa()
    assert len(rotas) >= 40
    for r in rotas:
        deps = [d.call for d in r.dependant.dependencies]
        assert rota._so_admin in deps, f"{sorted(r.methods)} {r.path} sem a trava da caixa"
    # Cada escrita liberada para quem lê é uma rota de verdade (molde + método).
    existentes = {(m, r.path) for r in rotas for m in r.methods}
    assert rota.ROTAS_DE_QUEM_LE <= existentes, rota.ROTAS_DE_QUEM_LE - existentes
    assert {p for _, p in rota.ROTAS_DE_QUEM_LE} == {
        f"{URL}/conversas/{{conversa_id}}/rascunho",
        f"{URL}/rascunhos/{{rascunho_id}}/avaliacao",
        f"{URL}/automacoes/previa",
        f"{URL}/adspower/aberto",
    }
    # A nota interna, o responder, a foto, a etiqueta etc. NÃO entram.
    for proibida in ("/notas", "/responder", "/foto", "/etiqueta", "/descartar", "/conferir"):
        assert not any(p.endswith(proibida) for _, p in rota.ROTAS_DE_QUEM_LE), proibida
    # O robô fica de fora: sem a trava, só com o token dele.
    do_robo = [
        r for r in app.routes if isinstance(r, APIRoute) and r.path.startswith(URL + "/robo/")
    ]
    assert {r.path for r in do_robo} == {f"{URL}/robo/pulso", f"{URL}/robo/eventos"}
    for r in do_robo:
        assert rota._so_admin not in [d.call for d in r.dependant.dependencies]


@pytest.mark.parametrize(
    "quem",
    ["comum_sem_permissao", "comum_com_o_recurso_inteiro", "admin_fora_da_lista"],
)
async def test_quem_so_le_le_tudo_e_nao_mexe_em_nada(client, make_user, auth_as, quem):
    if quem == "admin_fora_da_lista":
        u = await make_user(role=UserRole.ADMIN)
    else:
        u = await make_user(permissions=TUDO if quem == "comum_com_o_recurso_inteiro" else None)
    auth_as(u)
    chamadas = _chamadas()
    assert chamadas
    for metodo, molde, caminho in chamadas:
        r = await client.request(metodo, caminho)
        if metodo == "GET" or (metodo, molde) in rota.ROTAS_DE_QUEM_LE:
            # Passa da trava (cai no 404/409/422 da própria rota, ou 200).
            assert r.status_code not in (401, 403), (metodo, caminho, r.status_code, r.text)
        else:
            assert r.status_code == 403, (metodo, caminho, r.status_code, r.text)
            assert r.json()["detail"]["code"] == SO_LEITURA, (metodo, caminho)
            assert "Só leitura por enquanto" in r.json()["detail"]["detail"]


async def test_quem_esta_na_lista_mexe_como_antes(client, auth_as, dono):
    auth_as(dono)
    for metodo, _molde, caminho in _chamadas():
        r = await client.request(metodo, caminho)
        assert r.status_code not in (401, 403), (metodo, caminho, r.status_code, r.text)


async def test_operador_de_estoque_e_inativo_nao_entram(client, db, make_user, auth_as):
    operador = await make_user(permissions={"controle_estoque": {"view": True, "edit": True}})
    operador.stock_tags = ["ci"]
    await db.commit()
    auth_as(operador)
    r = await client.get(f"{URL}/conversas")
    assert r.status_code == 403 and r.json()["detail"] == {"code": "atendimento_restrito"}
    # Com outra permissão ele é supervisor (o web solta a trava): lê.
    operador.permissions = {**operador.permissions, "margem": {"view": True}}
    await db.commit()
    assert (await client.get(f"{URL}/conversas")).status_code == 200
    suspenso = await make_user(status=UserStatus.SUSPENDED)
    auth_as(suspenso)
    r = await client.get(f"{URL}/conversas")
    assert r.status_code == 403 and r.json()["detail"] == {"code": "atendimento_restrito"}


async def test_sem_login_e_401(client, auth_as):
    auth_as(None)
    r = await client.get(f"{URL}/conversas")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "unauthenticated"


async def test_robo_segue_no_token_sem_login(client, auth_as, monkeypatch):
    monkeypatch.setattr(get_settings(), "atendimento_robo_token", "tok-de-teste")
    auth_as(None)
    r = await client.post(f"{URL}/robo/pulso", json={})
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "robo_nao_autorizado"
    r = await client.post(
        f"{URL}/robo/pulso", json={}, headers={"Authorization": "Bearer tok-de-teste"}
    )
    assert r.status_code == 422


# ─────────────── o fluxo de quem só lê, numa conversa de verdade ───────────────


@pytest.fixture
def ia_falsa(monkeypatch, _fase_de_observacao):
    """ "Sugerir agora" sem provedor: a IA falsa grava uma sugestão pendente."""
    monkeypatch.setattr(_fase_de_observacao, "atendimento_ia_ativa", True)
    pedidos: list[str] = []

    async def gerar_rascunho(session, conversa, *, forcar=False):
        pedidos.append(str(conversa.id))
        # Como a de verdade: a sugestão nova aposenta a pendente.
        await session.execute(
            update(AtendimentoRascunho)
            .where(
                AtendimentoRascunho.conversa_id == conversa.id,
                AtendimentoRascunho.status == "pendente",
            )
            .values(status="substituido")
        )
        r = AtendimentoRascunho(
            conversa_id=conversa.id,
            texto="Oi! Seu pedido sai hoje.",
            categoria="rastreio",
            confianca=0.8,
            precisa_humano=False,
        )
        session.add(r)
        await session.flush()
        return r

    monkeypatch.setattr(ia, "gerar_rascunho", gerar_rascunho)
    return pedidos


@pytest.mark.parametrize("quem", ["comum", "admin_fora_da_lista"])
async def test_quem_so_le_ve_sugere_e_avalia_mas_nao_responde(
    client, db, make_user, auth_as, dono, ia_falsa, quem
):
    integ, canal = await _loja(db, dono, "kfa")
    conversa = await _conversa(db, integ, canal, "c1")
    u = await make_user(role=UserRole.ADMIN if quem == "admin_fora_da_lista" else UserRole.USER)
    await redis.delete(rota._chave_sugerir_pessoa(u.id))
    auth_as(u)

    lista = await client.get(f"{URL}/conversas")
    assert lista.status_code == 200
    assert [i["id"] for i in lista.json()["itens"]] == [str(conversa.id)]
    assert (await client.get(f"{URL}/conversas/{conversa.id}")).status_code == 200
    assert (await client.get(f"{URL}/conversas/{conversa.id}/abas")).status_code == 200
    assert (await client.get(f"{URL}/resumo")).status_code == 200
    assert (await client.get(f"{URL}/canais")).status_code == 200
    assert (await client.get(f"{URL}/metricas")).status_code == 200
    assert (await client.get(f"{URL}/regras")).status_code == 200
    assert (await client.get(f"{URL}/modelos")).status_code == 200
    assert (await client.get(f"{URL}/automacoes")).status_code == 200

    # "Sugerir agora": 200, e a sugestão fica guardada (nada sai).
    r = await client.post(f"{URL}/conversas/{conversa.id}/rascunho")
    assert r.status_code == 200, r.text
    rascunho_id = r.json()["rascunho"]["id"]
    assert ia_falsa == [str(conversa.id)]
    # 👍 e 👎 com correção: 200 (a nota é dela).
    r = await client.post(f"{URL}/rascunhos/{rascunho_id}/avaliacao", json={"nota": "ok"})
    assert r.status_code == 200, r.text
    assert r.json()["avaliacao"]["nota"] == "ok"
    r = await client.post(
        f"{URL}/rascunhos/{rascunho_id}/avaliacao",
        json={"nota": "erro", "correcao": "Faltou o código de rastreio."},
    )
    assert r.status_code == 200, r.text
    # A prévia de uma automática (só renderiza).
    r = await client.post(f"{URL}/automacoes/previa", json={"automacao": "nao-existe"})
    assert r.status_code == 404

    # Mexer: 403 com o código claro, e nada mudou.
    escritas = [
        ("POST", f"/conversas/{conversa.id}/responder", {"texto": "oi"}),
        ("PATCH", f"/conversas/{conversa.id}", {"situacao": "fechada"}),
        ("PATCH", f"/conversas/{conversa.id}", {"atribuido_a": str(u.id)}),
        ("PATCH", f"/conversas/{conversa.id}", {"ia_pausada": True}),
        ("POST", f"/conversas/{conversa.id}/etiqueta", {"etiqueta": "pos_venda"}),
        ("POST", f"/conversas/{conversa.id}/notas", {"texto": "recado"}),
        ("POST", f"/conversas/{conversa.id}/pedido/atualizar", None),
        ("POST", f"/rascunhos/{rascunho_id}/descartar", {"motivo": "não serve"}),
        ("PATCH", f"/canais/{canal.id}", {"modo": "copiloto"}),
        ("POST", "/regras", {"quando": "a", "faca": "b"}),
        ("POST", "/modelos", {"titulo": "t", "texto": "x"}),
        ("POST", "/sincronizar", None),
        ("PATCH", f"/automacoes/shopee_entregue/{integ.id}", {"modo": "simular"}),
        ("POST", "/automacoes/shopee_entregue/simular-nas-lojas-do-duoke", None),
    ]
    for metodo, caminho, corpo in escritas:
        r = await client.request(metodo, URL + caminho, json=corpo)
        assert r.status_code == 403, (metodo, caminho, r.status_code, r.text)
        assert r.json()["detail"]["code"] == SO_LEITURA, (metodo, caminho)
    await db.refresh(conversa)
    await db.refresh(canal)
    assert conversa.situacao != "fechada"
    assert conversa.atribuido_a is None and conversa.ia_pausada is False
    assert canal.modo == "observar"

    # O dono, na mesma conversa: passa da trava (o envio desligado é outro 409).
    auth_as(dono)
    r = await client.post(f"{URL}/conversas/{conversa.id}/responder", json={"texto": "oi"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "envio_desligado"
    r = await client.patch(f"{URL}/conversas/{conversa.id}", json={"ia_pausada": True})
    assert r.status_code == 200 and r.json()["conversa"]["ia_pausada"] is True


async def test_quem_so_le_nao_troca_a_nota_de_outra_pessoa(client, db, make_user, auth_as, dono):
    integ, canal = await _loja(db, dono, "kfa")
    conversa = await _conversa(db, integ, canal, "c1")
    do_dono = await _rascunho(db, conversa)
    # Uma pendente por conversa: a outra já foi respondida por fora (a do "IA × equipe").
    livre = await _rascunho(db, conversa, status="substituido")
    leitor = await make_user()
    outro_leitor = await make_user()

    auth_as(dono)
    r = await client.post(f"{URL}/rascunhos/{do_dono.id}/avaliacao", json={"nota": "ok"})
    assert r.status_code == 200, r.text

    # Quem só lê não passa por cima do 👍 do dono.
    auth_as(leitor)
    r = await client.post(
        f"{URL}/rascunhos/{do_dono.id}/avaliacao", json={"nota": "erro", "correcao": "não"}
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "avaliacao_de_outra_pessoa"
    # A sugestão sem nota é dela; e a própria nota ela troca.
    r = await client.post(f"{URL}/rascunhos/{livre.id}/avaliacao", json={"nota": "ok"})
    assert r.status_code == 200
    r = await client.post(
        f"{URL}/rascunhos/{livre.id}/avaliacao", json={"nota": "erro", "correcao": "faltou o prazo"}
    )
    assert r.status_code == 200 and r.json()["avaliacao"]["nota"] == "erro"
    # Outra pessoa que só lê também não troca a dela.
    auth_as(outro_leitor)
    r = await client.post(f"{URL}/rascunhos/{livre.id}/avaliacao", json={"nota": "ok"})
    assert r.status_code == 409

    # A tela sabe de quem é cada nota (quem só lê não vê 👍/👎 na de outra pessoa).
    marcas = await _de_outra_pessoa(client, conversa.id)
    assert marcas == {str(do_dono.id): True, str(livre.id): True}
    auth_as(leitor)
    marcas = await _de_outra_pessoa(client, conversa.id)
    assert marcas == {str(do_dono.id): True, str(livre.id): False}

    # Quem mexe troca qualquer uma (como antes: a linha passa a ser dele).
    auth_as(dono)
    r = await client.post(f"{URL}/rascunhos/{livre.id}/avaliacao", json={"nota": "ok"})
    assert r.status_code == 200 and r.json()["avaliacao"]["nota"] == "ok"
    assert r.json()["avaliacao"]["de_outra_pessoa"] is False
    detalhe = (await client.get(f"{URL}/conversas/{conversa.id}")).json()
    notas = {s["id"]: (s["avaliacao"] or {}).get("nota") for s in detalhe["sugestoes"]}
    assert notas == {str(do_dono.id): "ok", str(livre.id): "ok"}
    assert await _de_outra_pessoa(client, conversa.id) == {
        str(do_dono.id): False,
        str(livre.id): False,
    }


async def _de_outra_pessoa(client, conversa_id) -> dict[str, bool]:
    r = await client.get(f"{URL}/conversas/{conversa_id}")
    assert r.status_code == 200, r.text
    return {
        s["id"]: s["avaliacao"]["de_outra_pessoa"]
        for s in r.json()["sugestoes"]
        if s["avaliacao"] is not None
    }


async def test_quem_so_le_preenche_a_nota_da_linha_sem_nota(client, db, make_user, auth_as, dono):
    """O "descartar" de quem mexe (e o envio) cria a linha da avaliação SEM nota:
    quem só lê ainda dá o 👍/👎 nela — sem trocar a ação nem o motivo."""
    integ, canal = await _loja(db, dono, "kfa")
    conversa = await _conversa(db, integ, canal, "c1")
    r = await _rascunho(db, conversa)
    leitor = await make_user()
    outro_leitor = await make_user()

    auth_as(dono)
    resp = await client.post(f"{URL}/rascunhos/{r.id}/descartar", json={"motivo": "tom inadequado"})
    assert resp.status_code == 200, resp.text
    # Linha sem nota: nada para marcar na tela.
    assert await _de_outra_pessoa(client, conversa.id) == {}

    auth_as(leitor)
    resp = await client.post(
        f"{URL}/rascunhos/{r.id}/avaliacao",
        json={"nota": "erro", "correcao": "Faltou o código de rastreio."},
    )
    assert resp.status_code == 200, resp.text
    av = resp.json()["avaliacao"]
    assert (av["acao"], av["motivo"], av["nota"]) == ("descartou", "tom inadequado", "erro")
    assert av["de_outra_pessoa"] is False
    # Agora a nota é dela: outra pessoa que só lê não a troca.
    auth_as(outro_leitor)
    resp = await client.post(f"{URL}/rascunhos/{r.id}/avaliacao", json={"nota": "ok"})
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "avaliacao_de_outra_pessoa"
    assert await _de_outra_pessoa(client, conversa.id) == {str(r.id): True}


async def test_sugerir_tem_teto_por_hora_para_quem_so_le(
    client, db, make_user, auth_as, dono, ia_falsa
):
    integ, canal = await _loja(db, dono, "kfa")
    conversa = await _conversa(db, integ, canal, "c1")
    leitor = await make_user()
    chave = rota._chave_sugerir_pessoa(leitor.id)
    await redis.set(chave, rota.SUGERIR_POR_HORA_QUEM_LE - 1, ex=60)
    try:
        auth_as(leitor)
        r = await client.post(f"{URL}/conversas/{conversa.id}/rascunho")
        assert r.status_code == 200, r.text
        r = await client.post(f"{URL}/conversas/{conversa.id}/rascunho")
        assert r.status_code == 429
        assert r.json()["detail"]["code"] == "limite_sugestoes"
        assert len(ia_falsa) == 1, "o teto barra antes de chamar a IA"
    finally:
        await redis.delete(chave)
    # Quem mexe não tem teto (nem conta).
    await redis.delete(rota._chave_sugerir_pessoa(dono.id))
    auth_as(dono)
    for _ in range(2):
        assert (await client.post(f"{URL}/conversas/{conversa.id}/rascunho")).status_code == 200
    assert await redis.get(rota._chave_sugerir_pessoa(dono.id)) is None


async def test_contador_por_hora_sem_validade_ganha_validade(
    client, db, make_user, auth_as, dono, ia_falsa
):
    """O processo caiu entre o INCR e o EXPIRE: a chave sem validade travaria a
    pessoa para sempre — o próximo pedido repõe a validade."""
    integ, canal = await _loja(db, dono, "kfa")
    conversa = await _conversa(db, integ, canal, "c1")
    leitor = await make_user()
    chave = rota._chave_sugerir_pessoa(leitor.id)
    await redis.set(chave, 3)  # sem validade
    try:
        assert await redis.ttl(chave) == -1
        auth_as(leitor)
        r = await client.post(f"{URL}/conversas/{conversa.id}/rascunho")
        assert r.status_code == 200, r.text
        assert 0 < await redis.ttl(chave) <= rota._JANELA_SUGERIR_S
    finally:
        await redis.delete(chave)


async def test_sugerir_de_quem_so_le_conta_no_teto_diario_da_ia(
    client, db, make_user, auth_as, dono, ia_falsa, _fase_de_observacao, monkeypatch
):
    """O pedido pela tela não passa pelo teto diário dentro da IA (`forcar`); o
    de quem só lê conta no MESMO contador do dia (o freio de gasto do cron) e
    para quando ele acaba. Quem mexe segue fora dele, como antes."""
    integ, canal = await _loja(db, dono, "kfa")
    conversa = await _conversa(db, integ, canal, "c1")
    leitor = await make_user()
    monkeypatch.setattr(_fase_de_observacao, "atendimento_ia_teto_diario", 2)
    dia = datetime.now(ia.SAO_PAULO).strftime("%Y-%m-%d")
    chave = ia._CHAVE_TETO.format(_fase_de_observacao.database_schema, dia)
    await redis.delete(chave)
    await redis.delete(rota._chave_sugerir_pessoa(leitor.id))
    try:
        auth_as(leitor)
        for _ in range(2):
            r = await client.post(f"{URL}/conversas/{conversa.id}/rascunho")
            assert r.status_code == 200, r.text
        r = await client.post(f"{URL}/conversas/{conversa.id}/rascunho")
        assert r.status_code == 429
        assert r.json()["detail"] == rota.TETO_DIARIO_IA
        assert len(ia_falsa) == 2, "o teto barra antes de chamar a IA"
        # Quem mexe não é barrado nem conta (o pedido dele é o de sempre).
        auth_as(dono)
        assert (await client.post(f"{URL}/conversas/{conversa.id}/rascunho")).status_code == 200
        assert len(ia_falsa) == 3
        assert int(await redis.get(chave)) == 3  # as 2 do leitor + a barrada
    finally:
        await redis.delete(chave)
        await redis.delete(rota._chave_sugerir_pessoa(leitor.id))


# ─────────────── escopo por equipe, com a trava ligada ───────────────


async def test_equipe_so_ve_as_proprias_lojas_em_todas_as_rotas(
    client, db, make_user, auth_as, dono, monkeypatch
):
    integ_a, canal_a = await _loja(db, dono, "loja-a")
    integ_b, canal_b = await _loja(db, dono, "loja-b")
    conv_a = await _conversa(db, integ_a, canal_a, "a1")
    conv_b = await _conversa(db, integ_b, canal_b, "b1")
    rasc_b = await _rascunho(db, conv_b)
    await _dm(db)
    db.add(
        StoreInfo(
            user_id=dono.id,
            platform="shopee",
            account_name="loja-a",
            sales_team=7,
            integration_id=integ_a.id,
        )
    )
    await db.commit()
    membro = await make_user()  # sem permissão nenhuma: só a equipe
    membro.sales_teams = [7]
    await db.commit()

    # A faixa "lojas sem ler": uma linha de cada loja e uma sem integração (robô).
    base = vigia_leitura_atendimento.LeituraParada(
        chave="x", tipo="loja", plataforma="shopee", loja="?", motivo="sem permissão",
        acao="reautorizar", desde=AGORA, nunca_leu=True, minutos=600, limite_min=30,
    )  # fmt: skip

    async def leitura_parada(session, *, agora=None, nomes=None):
        return [
            replace(base, chave=f"loja:{integ_a.id}", integration_id=integ_a.id),
            replace(base, chave=f"loja:{integ_b.id}", integration_id=integ_b.id),
            replace(base, chave="canal:robo", plataforma="temu", canal_id=uuid4()),
        ]

    monkeypatch.setattr(vigia_leitura_atendimento, "leitura_parada", leitura_parada)

    auth_as(membro)
    ids = [i["id"] for i in (await client.get(f"{URL}/conversas")).json()["itens"]]
    assert ids == [str(conv_a.id)], "nem a loja de fora nem o Direct do Instagram"
    resumo = (await client.get(f"{URL}/resumo")).json()
    assert [lj["conta"] for lj in resumo["lojas"]] == ["loja-a"]
    assert all(p["plataforma"] != "instagram" for p in resumo["plataformas"])
    assert [lp["integration_id"] for lp in resumo["leitura_parada"]] == [str(integ_a.id)]
    assert [c["id"] for c in (await client.get(f"{URL}/canais")).json()] == [str(canal_a.id)]
    metricas = (await client.get(f"{URL}/metricas")).json()
    assert {lj.get("integration_id") for lj in metricas["lojas"]} <= {str(integ_a.id)}
    automacoes = (await client.get(f"{URL}/automacoes", params={"plataforma": "shopee"})).json()
    lojas_aut = {lj["integration_id"] for a in automacoes["automacoes"] for lj in a["lojas"]}
    assert lojas_aut == {str(integ_a.id)}
    registro = (await client.get(f"{URL}/automacoes/registro")).json()
    assert all(x["integration_id"] == str(integ_a.id) for x in registro["linhas"])
    # A conversa de fora da equipe não existe para ele, em rota nenhuma.
    for caminho in (
        f"/conversas/{conv_b.id}",
        f"/conversas/{conv_b.id}/abas",
        f"/conversas/{conv_b.id}/painel",
        f"/conversas/{conv_b.id}/reclamacoes",
        f"/conversas/{conv_b.id}/avaliacoes",
        f"/conversas/{conv_b.id}/carrinho",
    ):
        r = await client.get(URL + caminho)
        assert r.status_code == 404, (caminho, r.status_code, r.text)
    r = await client.post(f"{URL}/conversas/{conv_b.id}/rascunho")
    assert r.status_code in (404, 409), r.text  # IA desligada = 409 antes de tudo
    r = await client.post(f"{URL}/rascunhos/{rasc_b.id}/avaliacao", json={"nota": "ok"})
    assert r.status_code == 404
    # A da equipe abre.
    assert (await client.get(f"{URL}/conversas/{conv_a.id}")).status_code == 200
    assert (await client.get(f"{URL}/conversas/{conv_a.id}/abas")).status_code == 200

    # Sem equipe: vê as duas lojas e o Direct (como antes).
    sem_equipe = await make_user()
    auth_as(sem_equipe)
    ids = {i["id"] for i in (await client.get(f"{URL}/conversas")).json()["itens"]}
    assert {str(conv_a.id), str(conv_b.id)} <= ids
    assert any(i.startswith("ig:") for i in ids)
    resumo = (await client.get(f"{URL}/resumo")).json()
    assert len(resumo["leitura_parada"]) == 3


# ─────────────── /api/auth/me ───────────────


async def _me(client) -> dict:
    r = await client.get("/api/auth/me")
    assert r.status_code == 200, r.text
    return r.json()


async def test_me_diz_quem_ve_e_quem_mexe(client, db, make_user, auth_as, dono):
    auth_as(dono)
    me = await _me(client)
    assert (me["atendimento"], me["atendimento_mexe"]) == (True, True)

    so_leem = (
        await make_user(role=UserRole.ADMIN),
        await make_user(),
        await make_user(permissions=TUDO),
    )
    for u in so_leem:
        auth_as(u)
        me = await _me(client)
        assert (me["atendimento"], me["atendimento_mexe"]) == (True, False), u.email

    operador = await make_user(permissions={"controle_estoque": {"view": True}})
    operador.stock_tags = ["sp"]
    await db.commit()
    auth_as(operador)
    me = await _me(client)
    assert (me["atendimento"], me["atendimento_mexe"]) == (False, False)

    auth_as(await make_user(status=UserStatus.PENDING))
    me = await _me(client)
    assert (me["atendimento"], me["atendimento_mexe"]) == (False, False)


async def test_novo_usuario_nasce_sem_o_recurso_fino(client, make_user, auth_as, dono):
    """O recurso `atendimento` continua fora da tela de Permissões: o cadastro
    grava tudo desligado (quem vê e quem mexe vêm do /me, não do JSON)."""
    auth_as(dono)
    email = f"novo-{uuid4().hex[:8]}@davinci-test.com"
    r = await client.post("/api/users", json={"email": email, "name": "Novo"})
    assert r.status_code == 201, r.text
    assert r.json()["permissions"]["atendimento"] == {
        "view": False,
        "edit": False,
        "delete": False,
    }
