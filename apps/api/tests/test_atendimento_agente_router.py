"""A tela do AGENTE (Claude) no /atendimento (20/10/2026).

- o canal: o botão `agente` (só admin, só com a chave Claude e numa
  plataforma do agente; a chave nunca sai — só `agente_disponivel`) e o
  `auto_orientacao` (só os assuntos de orientação, só com o agente ligado;
  o `auto_categorias` continua recusando os só-pessoa);
- o "Sugerir" no canal com o agente: prazo de 40 s (estourou → nada
  gravado e "a IA não terminou, tente de novo") e o teto em dólar;
- o rastro enxuto no rascunho do agente (ferramentas e ações de pessoa);
- o 👎 com correção → fato CANDIDATO mascarado + par `correcao`; a
  sugestão enviada igual/editada → par `davinci_igual`/`davinci_editou`;
  a resposta enviada sozinha nunca vira par;
- a lista de aprovação dos fatos (aba do Manual): filtros, escopo da
  equipe, aprovar (com o texto corrigido) e reprovar, só admin.

O cérebro (IA, validador, contexto) entra falso, pelo contrato, como nos
outros testes do router.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AtendimentoAvaliacao,
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoRascunho,
    Integration,
    IntegrationPlatform,
    Product,
    User,
    UserRole,
)
from app.models.atendimento import AtendimentoFato, AtendimentoPar
from app.models.pricing import StoreInfo
from app.redis_client import redis
from app.routers import atendimento as rota
from app.security.cipher import encrypt_json
from app.services.atendimento import (
    busca_texto,
    clientes,
    contexto,
    enviar,
    gravar,
    ia,
    validador,
)
from app.services.atendimento import shopee as adaptador_shopee
from app.services.atendimento.agente import custo as custo_agente
from app.services.atendimento.agente import ferramentas as ferramentas_agente
from app.services.atendimento.constantes import (
    ASSUNTOS_ORIENTACAO,
    ASSUNTOS_SO_PESSOA_NO_AGENTE,
    FATO_ESCOPOS,
    FATO_FONTES,
    ORIGEM_IA,
    PROMPT_VERSAO_AGENTE,
    ResultadoEnvio,
)

URL = "/api/atendimento"
# O teto de verdade (o `_teto_livre` troca; o teste do teto põe de volta).
_CONFERIR_TETO = custo_agente.conferir_teto
AGORA = datetime.now(UTC)
PODE_TUDO = {"atendimento": {"view": True, "edit": True}}
CHAVE = "sk-ant-teste-que-nunca-sai-do-servidor"
CLIENTE = "Oi, a capa preta do modelo A18 serve no S5? Comprei os dois aqui."
CORRECAO = (
    "Maria Souza, a capa do A18 não serve no S5: os dois têm tamanhos diferentes, "
    "a do S5 é vendida à parte."
)


# ─────────────── falsos e chaves ───────────────


@pytest.fixture(autouse=True)
def _permissao_fina(monkeypatch):
    """Como no test_atendimento_router: a permissão fina (a trava da fase de
    observação tem o teste dela no fim deste arquivo)."""
    monkeypatch.setattr(rota, "SO_ADMIN", False)


@pytest.fixture(autouse=True)
def _cerebro_falso(monkeypatch):
    def normalizar(texto: str, *, plataforma: str, canal: str) -> str:
        return " ".join((texto or "").split())

    def validar(texto: str, *, plataforma: str, canal: str, origem: str) -> list[str]:
        return [] if texto.strip() else ["Resposta vazia."]

    async def contexto_da_conversa(session, conversa):
        return {"pedido": None, "logistica": None, "chamados": [], "devolucoes": []}

    monkeypatch.setattr(validador, "normalizar", normalizar)
    monkeypatch.setattr(validador, "validar", validar)
    monkeypatch.setattr(contexto, "contexto_da_conversa", contexto_da_conversa)


@pytest.fixture(autouse=True)
def _chaves(monkeypatch):
    """Tudo desligado e SEM chave Claude (o .env local não vaza para cá)."""
    s = get_settings()
    for nome in (
        "atendimento_leitura_ativa",
        "atendimento_envio_ativo",
        "atendimento_ia_ativa",
        "atendimento_auto_ativo",
        "atendimento_alerta_telegram",
        "atendimento_simulador",
    ):
        monkeypatch.setattr(s, nome, False)
    monkeypatch.setattr(s, "atendimento_agente_ativo", True)
    monkeypatch.setattr(s, "atendimento_agente_api_key", "")
    monkeypatch.setattr(s, "atendimento_llm_api_key", "")
    monkeypatch.setattr(s, "atendimento_ia_teto_diario", 0)
    return s


@pytest.fixture
def com_chave(_chaves, monkeypatch):
    monkeypatch.setattr(_chaves, "atendimento_agente_api_key", CHAVE)
    return _chaves


@pytest.fixture(autouse=True)
async def _sem_fatos_soltos(db: AsyncSession):
    """Fato global/da plataforma não tem FK que a limpeza geral apague."""
    await db.execute(delete(AtendimentoPar))
    await db.execute(delete(AtendimentoFato))
    await db.commit()
    yield
    await db.execute(delete(AtendimentoPar))
    await db.execute(delete(AtendimentoFato))
    await db.commit()


@pytest.fixture(autouse=True)
def _teto_livre(monkeypatch):
    """O teto do agente lê o Redis do dia: fora o teste do teto, sempre cabe."""

    async def cabe(**_kw):
        return None

    monkeypatch.setattr(custo_agente, "conferir_teto", cabe)


@pytest.fixture
async def pessoa(make_user, auth_as) -> User:
    u = await make_user(permissions=PODE_TUDO)
    auth_as(u)
    return u


# ─────────────── fábrica ───────────────


async def _loja(
    db: AsyncSession,
    dono: User,
    *,
    nome: str = "kia",
    plataforma: str = "shopee",
    canal: str = "chat",
    modo: str = "copiloto",
    agente: bool = False,
    auto_orientacao: list[str] | None = None,
) -> tuple[Integration, AtendimentoCanal]:
    integ = Integration(
        user_id=dono.id,
        platform=IntegrationPlatform.SHOPEE,
        name=nome,
        credentials=encrypt_json({"access_token": "t"}),
    )
    db.add(integ)
    await db.flush()
    c = AtendimentoCanal(
        integration_id=integ.id,
        plataforma=plataforma,
        canal=canal,
        modo=modo,
        status="ok",
        agente=agente,
        auto_orientacao=auto_orientacao or [],
    )
    db.add(c)
    await db.commit()
    return integ, c


async def _conversa(
    db: AsyncSession,
    integ: Integration,
    canal: AtendimentoCanal,
    externo_id: str = "conv-1",
    *,
    texto: str = CLIENTE,
    nome: str = "Maria Souza",
) -> tuple[AtendimentoConversa, AtendimentoMensagem]:
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=integ,
        plataforma=canal.plataforma,
        canal_nome=canal.canal,
        externo_id=externo_id,
        comprador_nome=nome,
    )
    m, _criada = await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id=f"{externo_id}-c",
        autor="cliente",
        texto=texto,
        enviada_em=AGORA - timedelta(minutes=30),
    )
    await db.commit()
    return conversa, m


async def _rascunho(
    db: AsyncSession, conversa: AtendimentoConversa, gatilho: AtendimentoMensagem, **campos
) -> AtendimentoRascunho:
    r = AtendimentoRascunho(
        conversa_id=conversa.id,
        mensagem_gatilho_id=gatilho.id,
        texto=campos.pop("texto", "Serve sim, a capa do A18 cabe no S5 sem problema."),
        categoria=campos.pop("categoria", "duvida_produto"),
        confianca=0.9,
        precisa_humano=False,
        validador_ok=True,
        **campos,
    )
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return r


async def _fatos(db: AsyncSession) -> list[AtendimentoFato]:
    return list(
        (
            await db.execute(
                select(AtendimentoFato)
                .order_by(AtendimentoFato.id)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )


async def _pares(db: AsyncSession, conversa_id) -> list[AtendimentoPar]:
    return list(
        (
            await db.execute(
                select(AtendimentoPar)
                .where(AtendimentoPar.conversa_id == conversa_id)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )


def _fato(**campos) -> AtendimentoFato:
    campos.setdefault("escopo", "global")
    campos.setdefault("fonte", "manual")
    campos.setdefault("ativo", False)
    return AtendimentoFato(**campos)


# ─────────────── canal: o botão e o auto_orientacao ───────────────


async def test_canal_mostra_o_agente_so_com_a_chave_e_nunca_a_chave(
    client, db, make_user, auth_as, _chaves, monkeypatch
):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    _integ, shopee = await _loja(db, admin)
    _integ2, magalu = await _loja(db, admin, nome="mglu", plataforma="magalu", canal="chat")

    lista = {c["id"]: c for c in (await client.get(f"{URL}/canais")).json()}
    assert {k: lista[str(shopee.id)][k] for k in ("agente", "auto_orientacao")} == {
        "agente": False,
        "auto_orientacao": [],
    }
    assert [c["agente_disponivel"] for c in lista.values()] == [False, False]

    monkeypatch.setattr(_chaves, "atendimento_agente_api_key", CHAVE)
    r = await client.get(f"{URL}/canais")
    lista = {c["id"]: c for c in r.json()}
    assert lista[str(shopee.id)]["agente_disponivel"] is True
    # A Magalu não é plataforma do agente (o prompt dele não a cobre).
    assert lista[str(magalu.id)]["agente_disponivel"] is False
    assert CHAVE not in r.text and "sk-ant" not in r.text

    # A chave da IA de hoje também serve, se for Claude; a do Groq, nunca.
    monkeypatch.setattr(_chaves, "atendimento_agente_api_key", "")
    monkeypatch.setattr(_chaves, "atendimento_llm_api_key", "gsk_groq")
    lista = {c["id"]: c for c in (await client.get(f"{URL}/canais")).json()}
    assert lista[str(shopee.id)]["agente_disponivel"] is False
    monkeypatch.setattr(_chaves, "atendimento_llm_api_key", CHAVE)
    lista = {c["id"]: c for c in (await client.get(f"{URL}/canais")).json()}
    assert lista[str(shopee.id)]["agente_disponivel"] is True


async def test_flag_agente_disponivel_no_resumo(
    client, db, make_user, auth_as, _chaves, monkeypatch
):
    auth_as(await make_user(role=UserRole.ADMIN))
    r = await client.get(f"{URL}/resumo")
    assert r.status_code == 200
    assert r.json()["flags"]["agente_disponivel"] is False
    monkeypatch.setattr(_chaves, "atendimento_agente_api_key", CHAVE)
    r = await client.get(f"{URL}/resumo")
    assert r.json()["flags"]["agente_disponivel"] is True
    assert "sk-ant" not in r.text


async def test_botao_do_agente_so_admin_so_com_chave_e_na_plataforma_dele(
    client, db, make_user, auth_as, pessoa, _chaves, monkeypatch
):
    admin = await make_user(role=UserRole.ADMIN)
    _integ, canal = await _loja(db, admin)
    _i2, magalu = await _loja(db, admin, nome="mglu", plataforma="magalu", canal="chat")
    url = f"{URL}/canais/{canal.id}"

    r = await client.patch(url, json={"agente": True})
    assert (r.status_code, r.json()["detail"]["code"]) == (403, "so_admin")

    auth_as(admin)
    r = await client.patch(url, json={"agente": True})
    assert (r.status_code, r.json()["detail"]["code"]) == (422, "agente_indisponivel")

    monkeypatch.setattr(_chaves, "atendimento_agente_api_key", CHAVE)
    r = await client.patch(url, json={"agente": True})
    assert r.status_code == 200
    assert (r.json()["agente"], r.json()["agente_disponivel"]) == (True, True)
    assert CHAVE not in r.text
    r = await client.patch(f"{URL}/canais/{magalu.id}", json={"agente": True})
    assert (r.status_code, r.json()["detail"]["code"]) == (422, "agente_indisponivel")

    # Quem não é admin manda o mesmo valor (a tela reenviando): nada muda, passa.
    auth_as(pessoa)
    r = await client.patch(url, json={"agente": True, "modo": "humano"})
    assert (r.status_code, r.json()["agente"], r.json()["modo"]) == (200, True, "humano")
    # Desligar também é de admin (o agente decide o que o comprador recebe).
    r = await client.patch(url, json={"agente": False})
    assert (r.status_code, r.json()["detail"]["code"]) == (403, "so_admin")

    await db.refresh(canal)
    assert canal.agente is True


async def test_auto_orientacao_so_de_orientacao_e_so_com_o_agente(
    client, db, make_user, auth_as, pessoa, com_chave
):
    admin = await make_user(role=UserRole.ADMIN)
    _integ, canal = await _loja(db, admin, modo="auto")
    url = f"{URL}/canais/{canal.id}"
    auth_as(admin)

    r = await client.patch(url, json={"auto_orientacao": ["garantia"]})
    assert (r.status_code, r.json()["detail"]["code"]) == (422, "orientacao_sem_agente")

    # O `agente` novo vale no mesmo PATCH; ordem das constantes, sem repetir.
    r = await client.patch(
        url, json={"agente": True, "auto_orientacao": ["outro", "garantia", "garantia"]}
    )
    assert r.status_code == 200
    assert r.json()["auto_orientacao"] == ["garantia", "outro"]

    for fora in (["rastreio"], ["reclamacao_forte"], ["marca_autorizacao", "garantia"]):
        r = await client.patch(url, json={"auto_orientacao": fora})
        assert (r.status_code, r.json()["detail"]["code"]) == (422, "assunto_fora_da_orientacao")
        assert r.json()["detail"]["categorias"] == [c for c in fora if c != "garantia"]
    r = await client.patch(url, json={"auto_orientacao": ["Garantia!"]})
    assert r.status_code == 422

    # O legado continua recusando os só-pessoa, com o agente ligado ou não.
    r = await client.patch(url, json={"auto_categorias": ["garantia"]})
    assert (r.status_code, r.json()["detail"]["code"]) == (422, "categoria_so_humano")

    auth_as(pessoa)
    r = await client.patch(url, json={"auto_orientacao": ["cancelamento"]})
    assert (r.status_code, r.json()["detail"]["code"]) == (403, "so_admin")
    r = await client.patch(url, json={"auto_orientacao": ["outro", "garantia"]})
    assert r.status_code == 200

    auth_as(admin)
    r = await client.patch(url, json={"auto_orientacao": list(ASSUNTOS_ORIENTACAO)})
    assert r.json()["auto_orientacao"] == list(ASSUNTOS_ORIENTACAO)
    # Desligar o agente leva junto o que ele mandava sozinho.
    r = await client.patch(url, json={"agente": False})
    assert (r.status_code, r.json()["agente"], r.json()["auto_orientacao"]) == (200, False, [])
    await db.refresh(canal)
    assert (canal.agente, canal.auto_orientacao) == (False, [])


async def test_auto_agente_so_do_automatico_do_canal_fora_da_orientacao_e_so_admin(
    client, db, make_user, auth_as, pessoa, com_chave
):
    """Decisão 8 (revisão de 20/10/2026): o rascunho do agente só sai sozinho
    nos assuntos que o dono marca DEPOIS do eval; nasce vazio."""
    admin = await make_user(role=UserRole.ADMIN)
    _integ, canal = await _loja(db, admin, modo="auto")
    url = f"{URL}/canais/{canal.id}"
    auth_as(admin)
    r = await client.patch(url, json={"auto_categorias": ["rastreio", "prazo_envio"]})
    assert (r.status_code, r.json()["auto_agente"]) == (200, [])

    r = await client.patch(url, json={"auto_agente": ["rastreio"]})
    assert (r.status_code, r.json()["detail"]["code"]) == (422, "assunto_sem_agente")

    # O `agente` novo vale no mesmo PATCH; a ordem do `auto_categorias`, sem repetir.
    r = await client.patch(
        url, json={"agente": True, "auto_agente": ["prazo_envio", "rastreio", "rastreio"]}
    )
    assert r.status_code == 200
    assert r.json()["auto_agente"] == r.json()["auto_categorias"]
    assert sorted(r.json()["auto_agente"]) == ["prazo_envio", "rastreio"]

    # Fora do automático do canal, ou assunto de orientação: não.
    for fora, esperado in (
        (["duvida_produto"], ["duvida_produto"]),
        (["garantia", "rastreio"], ["garantia"]),
        (["outro"], ["outro"]),
    ):
        r = await client.patch(url, json={"auto_agente": fora})
        assert (r.status_code, r.json()["detail"]["code"]) == (422, "assunto_fora_do_automatico")
        assert r.json()["detail"]["categorias"] == esperado

    # Quem não é admin não muda; o mesmo valor (a tela reenviando) passa.
    auth_as(pessoa)
    r = await client.patch(url, json={"auto_agente": ["rastreio"]})
    assert (r.status_code, r.json()["detail"]["code"]) == (403, "so_admin")
    r = await client.patch(url, json={"auto_agente": ["rastreio", "prazo_envio"]})
    assert r.status_code == 200

    # O assunto que sai do automático do canal sai do agente também.
    auth_as(admin)
    r = await client.patch(url, json={"auto_categorias": ["rastreio"]})
    assert (r.json()["auto_categorias"], r.json()["auto_agente"]) == (["rastreio"], ["rastreio"])
    # Desligar o agente leva junto.
    r = await client.patch(url, json={"agente": False})
    assert (r.status_code, r.json()["agente"], r.json()["auto_agente"]) == (200, False, [])
    await db.refresh(canal)
    assert (canal.auto_orientacao, canal.auto_agente) == ([], [])


# ─────────────── "Sugerir" no canal com o agente ───────────────


@pytest.fixture
def ia_ligada(_chaves, monkeypatch):
    monkeypatch.setattr(_chaves, "atendimento_ia_ativa", True)
    return _chaves


async def _canal_com_agente(db, make_user, *, agente: bool = True):
    dono = await make_user()
    integ, canal = await _loja(db, dono, agente=agente)
    conversa, gatilho = await _conversa(db, integ, canal)
    return conversa, gatilho


async def test_sugerir_com_o_agente_estoura_o_prazo_e_nao_grava_nada(
    client, db, make_user, pessoa, com_chave, ia_ligada, monkeypatch
):
    assert rota.PRAZO_SUGERIR_AGENTE_S == 40  # spec 3.4 (o teste encurta abaixo)
    conversa, gatilho = await _canal_com_agente(db, make_user)
    monkeypatch.setattr(rota, "PRAZO_SUGERIR_AGENTE_S", 0.3)
    chamadas = []

    async def lento(session, c, *, forcar=False):
        chamadas.append(forcar)
        session.add(
            AtendimentoRascunho(
                conversa_id=c.id, mensagem_gatilho_id=gatilho.id, texto="Meio do caminho."
            )
        )
        await session.flush()
        await asyncio.sleep(10)
        return None

    monkeypatch.setattr(ia, "gerar_rascunho", lento)
    inicio = time.monotonic()
    r = await client.post(f"{URL}/conversas/{conversa.id}/rascunho")
    assert time.monotonic() - inicio < 5
    assert r.status_code == 200
    assert r.json() == {"rascunho": None, "motivo": "a IA não terminou, tente de novo"}
    assert chamadas == [True]
    n = await db.scalar(
        select(text("count(*)"))
        .select_from(AtendimentoRascunho)
        .where(AtendimentoRascunho.conversa_id == conversa.id)
    )
    assert n == 0


async def test_sugerir_que_gravou_no_ultimo_instante_devolve_a_sugestao(
    client, db, make_user, pessoa, com_chave, ia_ligada, monkeypatch
):
    """O corte caiu DEPOIS do commit do `_salvar`: a sugestão existe e volta."""
    conversa, gatilho = await _canal_com_agente(db, make_user)
    monkeypatch.setattr(rota, "PRAZO_SUGERIR_AGENTE_S", 0.3)

    async def gravou_e_pendurou(session, c, *, forcar=False):
        session.add(
            AtendimentoRascunho(
                conversa_id=c.id, mensagem_gatilho_id=gatilho.id, texto="Serve sim."
            )
        )
        await session.commit()
        await asyncio.sleep(10)

    monkeypatch.setattr(ia, "gerar_rascunho", gravou_e_pendurou)
    r = await client.post(f"{URL}/conversas/{conversa.id}/rascunho")
    assert r.status_code == 200
    assert (r.json()["rascunho"]["texto"], r.json()["motivo"]) == ("Serve sim.", None)


async def test_sugerir_sem_o_agente_nao_tem_o_prazo_da_tela(
    client, db, make_user, pessoa, com_chave, ia_ligada, monkeypatch
):
    conversa, _gatilho = await _canal_com_agente(db, make_user, agente=False)
    monkeypatch.setattr(rota, "PRAZO_SUGERIR_AGENTE_S", 0.05)

    async def demorado(session, c, *, forcar=False):
        await asyncio.sleep(0.3)
        r = AtendimentoRascunho(conversa_id=c.id, texto="Sugestão do legado.", categoria="outro")
        session.add(r)
        await session.flush()
        return r

    monkeypatch.setattr(ia, "gerar_rascunho", demorado)
    r = await client.post(f"{URL}/conversas/{conversa.id}/rascunho")
    assert r.status_code == 200
    assert r.json()["rascunho"]["texto"] == "Sugestão do legado."
    assert r.json()["rascunho"]["agente"] is None


async def test_sugerir_com_o_agente_respeita_o_teto_em_dolar(
    client, db, make_user, pessoa, com_chave, ia_ligada, monkeypatch
):
    conversa, _gatilho = await _canal_com_agente(db, make_user)
    chamadas = []

    async def gerar(session, c, *, forcar=False):
        chamadas.append(c.id)
        return None

    monkeypatch.setattr(ia, "gerar_rascunho", gerar)

    # O teto DE VERDADE (Redis): a conversa já gastou o dela hoje.
    monkeypatch.setattr(custo_agente, "conferir_teto", _CONFERIR_TETO)
    monkeypatch.setattr(com_chave, "atendimento_agente_teto_usd_conversa", 0.05)
    monkeypatch.setattr(com_chave, "atendimento_agente_teto_usd_dia", 5.0)
    _dia, chave_conversa = custo_agente._chaves(conversa.id, custo_agente.dia_de_hoje())
    await redis.set(chave_conversa, "0.06", ex=60)
    try:
        r = await client.post(f"{URL}/conversas/{conversa.id}/rascunho")
    finally:
        await redis.delete(chave_conversa)
    assert r.status_code == 429
    assert r.json()["detail"] == {"code": "teto_agente", "detail": rota.MOTIVO_TETO_AGENTE_CONVERSA}
    assert chamadas == []

    # O teto do DIA atingido no meio: o agente para sem rascunho e a tela diz por quê.
    vezes = []

    async def conferir(**_kw):
        vezes.append(1)
        if len(vezes) > 1:
            raise custo_agente.TetoEstourado(custo_agente.TETO_DIA, 5.01, 5.0)

    monkeypatch.setattr(custo_agente, "conferir_teto", conferir)
    r = await client.post(f"{URL}/conversas/{conversa.id}/rascunho")
    assert r.status_code == 200
    assert r.json() == {"rascunho": None, "motivo": rota.MOTIVO_TETO_AGENTE_DIA}
    assert len(chamadas) == 1


async def test_rascunho_do_agente_traz_o_rastro_enxuto(
    client, db, make_user, pessoa, com_chave, ia_ligada, monkeypatch
):
    conversa, gatilho = await _canal_com_agente(db, make_user)

    async def do_agente(session, c, *, forcar=False):
        r = AtendimentoRascunho(
            conversa_id=c.id,
            mensagem_gatilho_id=gatilho.id,
            texto=None,
            categoria="defeito",
            prompt_versao=PROMPT_VERSAO_AGENTE,
            fatos={
                "agente": {
                    "acao": "pessoa",
                    "parada": "fim",
                    "acoes_pessoa": ["analisar_evidencia", "analisar_evidencia", "excecao"],
                    "rastro": [
                        {"ferramenta": "ver_pedido", "args": {"numero": "x"}, "ms": 9, "ids": []},
                        {"ferramenta": "ver_anuncio", "args": {}, "ms": 5, "erro": "fora"},
                        {"ferramenta": "ver_pedido", "args": {}, "ms": 3},
                        {"sem_ferramenta": True},
                    ],
                }
            },
        )
        session.add(r)
        await session.flush()
        return r

    monkeypatch.setattr(ia, "gerar_rascunho", do_agente)
    r = await client.post(f"{URL}/conversas/{conversa.id}/rascunho")
    assert r.status_code == 200
    assert r.json()["rascunho"]["agente"] == {
        "ferramentas": ["ver_pedido", "ver_anuncio"],
        "ferramentas_com_erro": ["ver_anuncio"],
        "acoes_pessoa": [
            {"codigo": "analisar_evidencia", "rotulo": "Analisar foto ou vídeo"},
            {"codigo": "excecao", "rotulo": "Exceção à política"},
        ],
        "acao": "pessoa",
        "parada": "fim",
        # Rodada 4 (21/10/2026): os avisos das regras suaves (aqui, nenhum).
        "avisos": [],
    }
    # O detalhe da conversa traz o mesmo resumo (sem os argumentos).
    d = (await client.get(f"{URL}/conversas/{conversa.id}")).json()
    assert d["rascunho"]["agente"]["ferramentas"] == ["ver_pedido", "ver_anuncio"]
    assert "args" not in str(d["rascunho"]["agente"])


# ─────────────── 👎 com correção: fato candidato e par ───────────────


async def test_correcao_vira_fato_candidato_mascarado_e_par(
    client, db, make_user, pessoa, com_chave
):
    dono = await make_user()
    integ, canal = await _loja(db, dono)
    conversa, gatilho = await _conversa(db, integ, canal)
    r = await _rascunho(db, conversa, gatilho)

    resp = await client.post(
        f"{URL}/rascunhos/{r.id}/avaliacao", json={"nota": "erro", "correcao": CORRECAO}
    )
    assert resp.status_code == 200
    assert resp.json()["avaliacao"]["correcao"] == CORRECAO

    (f,) = await _fatos(db)
    assert (f.fonte, f.ativo, f.aprovado_por, f.chave) == (
        "correcao",
        False,
        None,
        f"correcao:{r.id}",
    )
    # Quem não é admin: a correção vale só na loja da conversa.
    assert (f.escopo, f.integration_id, f.plataforma, f.assunto) == (
        "loja",
        integ.id,
        "shopee",
        "duvida_produto",
    )
    assert "Maria" not in f.texto and "Souza" not in f.texto
    assert "a capa do A18 não serve no S5" in f.texto
    assert str(conversa.id) in f.referencia

    (par,) = await _pares(db, conversa.id)
    assert (par.fonte, par.resposta_msg_id, par.autor_admin) == ("correcao", gatilho.id, False)
    assert "Maria" not in par.resposta_gen and "capa do A18" in par.resposta_gen

    # A nota refeita na MESMA sugestão atualiza o mesmo candidato (não cria outro).
    resp = await client.post(
        f"{URL}/rascunhos/{r.id}/avaliacao",
        json={
            "nota": "erro",
            "correcao": "A capa do A18 não serve no S5 de jeito nenhum, confira.",
        },
    )
    assert resp.status_code == 200
    (f,) = await _fatos(db)
    assert f.texto.startswith("A capa do A18 não serve no S5 de jeito nenhum")

    # 👍 depois: o candidato que ninguém decidiu sai, e o par também.
    resp = await client.post(f"{URL}/rascunhos/{r.id}/avaliacao", json={"nota": "ok"})
    assert resp.status_code == 200
    assert await _fatos(db) == []
    assert await _pares(db, conversa.id) == []


async def test_correcao_de_admin_vale_na_plataforma_e_decisao_fica(
    client, db, make_user, auth_as, com_chave
):
    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    integ, canal = await _loja(db, admin)
    conversa, gatilho = await _conversa(db, integ, canal)
    r = await _rascunho(db, conversa, gatilho)

    await client.post(
        f"{URL}/rascunhos/{r.id}/avaliacao", json={"nota": "erro", "correcao": CORRECAO}
    )
    (f,) = await _fatos(db)
    assert (f.escopo, f.integration_id, f.plataforma) == ("plataforma", None, "shopee")
    (par,) = await _pares(db, conversa.id)
    assert par.autor_admin is True

    # Um admin já decidiu (aprovou): a nota refeita não mexe mais no fato.
    resp = await client.post(f"{URL}/fatos/{f.id}/aprovar", json={})
    assert resp.status_code == 200
    await client.post(f"{URL}/rascunhos/{r.id}/avaliacao", json={"nota": "ok"})
    (f2,) = await _fatos(db)
    assert (f2.id, f2.ativo, f2.texto) == (f.id, True, f.texto)


async def test_correcao_sem_chave_vira_so_candidato_e_falha_nao_derruba_a_nota(
    client, db, make_user, pessoa, _chaves, monkeypatch
):
    dono = await make_user()
    integ, canal = await _loja(db, dono)
    conversa, gatilho = await _conversa(db, integ, canal)
    r = await _rascunho(db, conversa, gatilho)

    # Ajuste r3: sem o agente no servidor, o sistema inteiro fica como em
    # origin — a nota grava e NADA do agente nasce (nem candidato, nem par).
    resp = await client.post(
        f"{URL}/rascunhos/{r.id}/avaliacao", json={"nota": "erro", "correcao": CORRECAO}
    )
    assert resp.status_code == 200
    assert await _fatos(db) == []
    assert await _pares(db, conversa.id) == []

    # Com a chave: o candidato (o dono aprova quando quiser).
    monkeypatch.setattr(_chaves, "atendimento_agente_api_key", CHAVE)
    resp = await client.post(
        f"{URL}/rascunhos/{r.id}/avaliacao", json={"nota": "erro", "correcao": CORRECAO}
    )
    assert resp.status_code == 200
    assert len(await _fatos(db)) == 1

    async def quebra(*_a, **_k):
        raise RuntimeError("banco fora")

    r2 = await _rascunho(
        db, conversa, gatilho, texto="Outra sugestão qualquer para avaliar.", status="bloqueado"
    )
    monkeypatch.setattr(rota, "_fato_da_correcao", quebra)
    resp = await client.post(
        f"{URL}/rascunhos/{r2.id}/avaliacao", json={"nota": "erro", "correcao": CORRECAO}
    )
    assert resp.status_code == 200
    av = await db.scalar(
        select(AtendimentoAvaliacao)
        .where(AtendimentoAvaliacao.rascunho_id == r2.id)
        .execution_options(populate_existing=True)
    )
    assert (av.nota, av.correcao) == ("erro", CORRECAO)
    assert len(await _fatos(db)) == 1


# ─────────────── a sugestão enviada vira par; a automática, nunca ───────────────


@pytest.fixture
def plataforma(monkeypatch, _chaves):
    """Adaptador falso da Shopee (como em test_atendimento_enviar) e o envio ligado."""
    monkeypatch.setattr(_chaves, "atendimento_envio_ativo", True)
    enviadas: list[str] = []

    async def enviar_texto(session, conversa, integration, cliente, texto):
        enviadas.append(texto)
        return ResultadoEnvio(ok=True, externo_id=f"plat-{len(enviadas)}", payload={})

    async def cliente_falso(integration):
        return object()

    monkeypatch.setattr(adaptador_shopee, "enviar_texto", enviar_texto)
    monkeypatch.setattr(clientes, "cliente_da_integracao", cliente_falso)
    return enviadas


async def test_sugestao_enviada_igual_ou_editada_vira_par_na_hora(
    db, make_user, com_chave, plataforma
):
    user = await make_user()
    # Ajuste r3: só no canal COM o agente (o cron cobre os outros).
    integ, canal = await _loja(db, user, modo="humano", agente=True)
    texto = "Serve sim, a capa do A18 cabe no S5 sem problema nenhum."

    conversa, gatilho = await _conversa(db, integ, canal, "igual")
    r = await _rascunho(db, conversa, gatilho, texto=texto)
    m = await enviar.enviar_resposta(db, conversa, texto, user=user, rascunho_id=r.id)
    assert m.status == "enviada"
    (par,) = await _pares(db, conversa.id)
    assert (par.fonte, par.resposta_msg_id) == ("davinci_igual", m.id)
    assert "Maria" not in par.pergunta_gen

    outra, gatilho2 = await _conversa(db, integ, canal, "editou")
    r2 = await _rascunho(db, outra, gatilho2, texto=texto)
    editado = "Não serve: a capa do A18 é de outro tamanho, a do S5 é vendida à parte."
    m2 = await enviar.enviar_resposta(db, outra, editado, user=user, rascunho_id=r2.id)
    (par2,) = await _pares(db, outra.id)
    assert (par2.fonte, par2.resposta_msg_id) == ("davinci_editou", m2.id)
    assert "vendida à parte" in par2.resposta_gen
    assert plataforma == [texto, editado]

    # Ajuste r3: com a chave no servidor, o envio num canal SEM o agente não
    # reindexa a conversa na hora (o cron de hora em hora a pega).
    integ_sem, canal_sem = await _loja(db, user, nome="outra", modo="humano")
    sem, gatilho3 = await _conversa(db, integ_sem, canal_sem, "sem-agente")
    r3 = await _rascunho(db, sem, gatilho3, texto=texto)
    m3 = await enviar.enviar_resposta(db, sem, texto, user=user, rascunho_id=r3.id)
    assert m3.status == "enviada" and await _pares(db, sem.id) == []


async def test_sem_chave_o_envio_nao_mexe_nos_pares_e_a_falha_nao_derruba(
    db, make_user, _chaves, plataforma, monkeypatch
):
    user = await make_user()
    integ, canal = await _loja(db, user, modo="humano")
    texto = "Serve sim, a capa do A18 cabe no S5 sem problema nenhum."
    conversa, gatilho = await _conversa(db, integ, canal, "sem-chave")
    r = await _rascunho(db, conversa, gatilho, texto=texto)
    m = await enviar.enviar_resposta(db, conversa, texto, user=user, rascunho_id=r.id)
    assert m.status == "enviada"
    assert await _pares(db, conversa.id) == []

    # Com a chave, o par que quebra fica no log: o envio e a avaliação seguem.
    monkeypatch.setattr(_chaves, "atendimento_agente_api_key", CHAVE)

    async def quebra(*_a, **_k):
        raise RuntimeError("tabela sem migration")

    monkeypatch.setattr(enviar, "indexar_pares_da_conversa", quebra)
    outra, gatilho2 = await _conversa(db, integ, canal, "quebra")
    r2 = await _rascunho(db, outra, gatilho2, texto=texto)
    m2 = await enviar.enviar_resposta(db, outra, texto, user=user, rascunho_id=r2.id)
    assert m2.status == "enviada"
    av = await db.scalar(
        select(AtendimentoAvaliacao).where(AtendimentoAvaliacao.rascunho_id == r2.id)
    )
    assert av.acao == "enviou_igual"
    assert await _pares(db, outra.id) == []


async def test_resposta_enviada_sozinha_nunca_vira_par(db, make_user, com_chave):
    user = await make_user()
    integ, canal = await _loja(db, user, modo="auto", agente=True)
    conversa, gatilho = await _conversa(db, integ, canal, "auto")
    r = await _rascunho(db, conversa, gatilho)
    m, _criada = await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id="auto-l",
        autor="loja",
        texto=r.texto,
        enviada_em=AGORA - timedelta(minutes=10),
        origem=ORIGEM_IA,
    )
    m.rascunho_id = r.id
    m.status = "enviada"
    # (A mensagem da loja gravada aqui aposenta a pendente; no envio de
    # verdade é o `_avaliar_rascunho` que a marca `enviado`.)
    r.status = "pendente"
    await db.flush()
    await enviar._avaliar_rascunho(
        db, conversa, m, rascunho=r, texto_digitado=r.texto, user=None, origem=ORIGEM_IA
    )
    await db.commit()
    await db.refresh(r)
    assert r.status == "enviado"
    assert (
        await db.scalar(
            select(AtendimentoAvaliacao).where(AtendimentoAvaliacao.rascunho_id == r.id)
        )
        is None
    )
    # Nem a indexação da conversa (a do cron) aproveita a resposta da IA.
    assert await enviar.indexar_pares_da_conversa(db, conversa) == 0
    await db.commit()
    assert await _pares(db, conversa.id) == []


# ─────────────── aprovação dos fatos (aba do Manual) ───────────────


async def _fatos_variados(db, make_user):
    dono = await make_user()
    integ_a, canal_a = await _loja(db, dono, nome="kia")
    integ_b, _canal_b = await _loja(db, dono, nome="mega")
    conversa, _g = await _conversa(db, integ_a, canal_a)
    produto = Product(user_id=dono.id, sku="dg089", name="Pulseira Apple Watch 44mm")
    db.add(produto)
    await db.flush()
    fatos = {
        "candidato_loja": _fato(
            escopo="loja",
            integration_id=integ_a.id,
            plataforma="shopee",
            fonte="correcao",
            assunto="duvida_produto",
            texto="A capa do A18 não serve no S5.",
            referencia=f"conversa:{conversa.id} rascunho:{uuid4()}",
        ),
        "ativo_global": _fato(
            texto="O celular é desbloqueado para qualquer operadora.",
            ativo=True,
            aprovado_por="regra aprovada no código",
            aprovado_em=AGORA,
        ),
        "reprovado_plataforma": _fato(
            escopo="plataforma",
            plataforma="shopee",
            fonte="recusa_padrao",
            texto="Não enviamos brinde nem amostra.",
            aprovado_por="Eduardo",
            aprovado_em=AGORA,
        ),
        "candidato_outra_loja": _fato(
            escopo="loja", integration_id=integ_b.id, texto="Envio só por transportadora."
        ),
        "candidato_produto": _fato(
            escopo="produto", product_id=produto.id, texto="A pulseira padrão vem junto."
        ),
    }
    db.add_all(fatos.values())
    await db.commit()
    return integ_a, integ_b, conversa, fatos


async def test_lista_de_fatos_com_filtros_contagem_e_escopo(client, db, make_user, auth_as):
    integ_a, _integ_b, conversa, fatos = await _fatos_variados(db, make_user)
    leitor = await make_user(permissions=PODE_TUDO)
    auth_as(leitor)

    r = await client.get(f"{URL}/fatos")
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["contagem"] == {"candidato": 3, "ativo": 1, "reprovado": 1}
    estados = [f["estado"] for f in corpo["fatos"]]
    assert estados == ["candidato", "candidato", "candidato", "ativo", "reprovado"]
    por_texto = {f["texto"]: f for f in corpo["fatos"]}
    loja = por_texto["A capa do A18 não serve no S5."]
    assert (loja["loja"], loja["conversa_id"], loja["fonte"], loja["escopo"]) == (
        "kia",
        str(conversa.id),
        "correcao",
        "loja",
    )
    assert por_texto["A pulseira padrão vem junto."]["produto"] == "Pulseira Apple Watch 44mm"
    assert por_texto["Não enviamos brinde nem amostra."]["aprovado_por"] == "Eduardo"

    def textos(resp):
        return sorted(f["texto"] for f in resp.json()["fatos"])

    r = await client.get(f"{URL}/fatos", params={"estado": "ativo"})
    assert textos(r) == ["O celular é desbloqueado para qualquer operadora."]
    assert r.json()["contagem"]["candidato"] == 3  # a contagem ignora o filtro de estado
    r = await client.get(f"{URL}/fatos", params={"fonte": "recusa_padrao"})
    assert textos(r) == ["Não enviamos brinde nem amostra."]
    r = await client.get(f"{URL}/fatos", params={"escopo": "loja", "estado": "candidato"})
    assert len(r.json()["fatos"]) == 2
    r = await client.get(
        f"{URL}/fatos", params={"plataforma": "shopee", "assunto": "duvida_produto"}
    )
    assert textos(r) == ["A capa do A18 não serve no S5."]
    r = await client.get(f"{URL}/fatos", params={"busca": "BRINDE"})
    assert textos(r) == ["Não enviamos brinde nem amostra."]
    r = await client.get(f"{URL}/fatos", params={"busca": "100%_"})
    assert r.json()["fatos"] == []
    for ruim in ({"estado": "x"}, {"fonte": "x"}, {"escopo": "x"}, {"plataforma": "x"}):
        assert (await client.get(f"{URL}/fatos", params=ruim)).status_code == 422

    # Escopo da equipe: o fato de loja só para quem vê a loja.
    db.add(
        StoreInfo(
            user_id=leitor.id,
            platform="shopee",
            account_name="loja-a",
            sales_team=7,
            integration_id=integ_a.id,
        )
    )
    membro = await make_user(permissions=PODE_TUDO)
    membro.sales_teams = [7]
    await db.commit()
    auth_as(membro)
    vistos = textos(await client.get(f"{URL}/fatos"))
    assert "Envio só por transportadora." not in vistos
    assert "A capa do A18 não serve no S5." in vistos
    assert "O celular é desbloqueado para qualquer operadora." in vistos
    assert (
        await client.post(f"{URL}/fatos/{fatos['candidato_loja'].id}/aprovar", json={})
    ).status_code == 403


async def test_aprovar_com_texto_corrigido_e_reprovar_so_admin(
    client, db, make_user, auth_as, pessoa
):
    _a, integ_b, _c, fatos = await _fatos_variados(db, make_user)
    alvo = fatos["candidato_loja"]

    r = await client.post(f"{URL}/fatos/{alvo.id}/aprovar", json={})
    assert (r.status_code, r.json()["detail"]["code"]) == (403, "so_admin")
    r = await client.post(f"{URL}/fatos/{alvo.id}/reprovar")
    assert (r.status_code, r.json()["detail"]["code"]) == (403, "so_admin")

    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    novo = (
        "A capa do A18 não serve no S5 — o S5 tem câmera maior; a capinha dele é vendida à parte."
    )
    r = await client.post(f"{URL}/fatos/{alvo.id}/aprovar", json={"texto": f"  {novo}  "})
    assert r.status_code == 200
    f = r.json()
    assert (f["estado"], f["ativo"], f["texto"], f["aprovado_por"]) == (
        "ativo",
        True,
        novo,
        admin.email,
    )
    assert f["aprovado_em"] is not None
    # O `tsv` acompanhou o texto novo (a busca do agente acha pela palavra nova).
    achado = await db.scalar(
        select(AtendimentoFato.id).where(
            AtendimentoFato.tsv.op("@@")(busca_texto.consulta_de("camera"))
        )
    )
    assert achado == alvo.id

    r = await client.post(f"{URL}/fatos/{alvo.id}/reprovar")
    assert r.status_code == 200
    assert (r.json()["estado"], r.json()["ativo"], r.json()["aprovado_por"]) == (
        "reprovado",
        False,
        admin.email,
    )
    r = await client.post(f"{URL}/fatos/{alvo.id}/aprovar", json={"texto": "x" * 2001})
    assert r.status_code == 422
    r = await client.post(f"{URL}/fatos/999999999/aprovar", json={})
    assert (r.status_code, r.json()["detail"]["code"]) == (404, "fato_nao_encontrado")

    # Aprovar sem texto mantém o texto e liga.
    outro = fatos["candidato_produto"]
    r = await client.post(f"{URL}/fatos/{outro.id}/aprovar", json={})
    assert (r.json()["estado"], r.json()["texto"]) == ("ativo", "A pulseira padrão vem junto.")
    assert integ_b.id  # (a outra loja não muda nada aqui)


async def test_fatos_na_fase_de_observacao_quem_so_le_ve_mas_nao_decide(
    client, db, make_user, auth_as, monkeypatch, _chaves
):
    monkeypatch.setattr(rota, "SO_ADMIN", True)
    monkeypatch.setattr(_chaves, "atendimento_usuarios", "dono@davinci-test.com")
    _a, _b, _c, fatos = await _fatos_variados(db, make_user)
    # Admin fora da lista de quem mexe: lê, não decide.
    auth_as(await make_user(role=UserRole.ADMIN))
    assert (await client.get(f"{URL}/fatos")).status_code == 200
    r = await client.post(f"{URL}/fatos/{fatos['candidato_loja'].id}/aprovar", json={})
    assert (r.status_code, r.json()["detail"]["code"]) == (403, "atendimento_so_leitura")
    # O dono (na lista) decide.
    auth_as(await make_user(role=UserRole.ADMIN, email="dono@davinci-test.com"))
    r = await client.post(f"{URL}/fatos/{fatos['candidato_loja'].id}/aprovar", json={})
    assert (r.status_code, r.json()["estado"]) == (200, "ativo")


# ─────────────── a tela fala o mesmo vocabulário ───────────────

TELA = Path(__file__).resolve().parents[2] / "web" / "components" / "AtendimentoPlataforma.vue"


def test_a_tela_usa_o_mesmo_vocabulario_do_agente():
    """As listas espelhadas no AtendimentoPlataforma.vue batem com as constantes."""
    if not TELA.exists():
        pytest.skip("sem o apps/web ao lado (imagem só da api)")
    fonte = TELA.read_text(encoding="utf-8")

    def lista(nome: str) -> list[str]:
        achou = re.search(rf"export const {nome} = \[(.*?)\]", fonte, re.S)
        assert achou, nome
        return re.findall(r"'([a-z_]+)'", achou.group(1))

    def chaves(nome: str) -> list[str]:
        achou = re.search(
            rf"export const {nome}: Record<string, string> = \{{(.*?)\n\}}", fonte, re.S
        )
        assert achou, nome
        return re.findall(r"^\s+([a-z_]+):", achou.group(1), re.M)

    assert lista("ASSUNTOS_ORIENTACAO") == list(ASSUNTOS_ORIENTACAO)
    # Ajuste r3: a lista "sempre pessoa confere" é a mesma do backend.
    assert lista("ASSUNTOS_SO_PESSOA_NO_AGENTE") == list(ASSUNTOS_SO_PESSOA_NO_AGENTE)
    assert chaves("FONTES_FATO") == list(FATO_FONTES)
    assert chaves("ESCOPOS_FATO") == list(FATO_ESCOPOS)
    assert chaves("FERRAMENTAS_AGENTE") == list(ferramentas_agente.NOMES)


# ─────────────── ajuste r3 de 20/10/2026 ───────────────


async def test_os_assuntos_de_produto_nunca_entram_no_auto_agente(
    client, db, make_user, auth_as, com_chave
):
    """A tela promete "sempre pessoa confere": a rota garante (422), e um valor
    antigo no banco sai na próxima gravação do canal."""
    admin = await make_user(role=UserRole.ADMIN)
    _integ, canal = await _loja(db, admin, modo="auto", agente=True)
    url = f"{URL}/canais/{canal.id}"
    auth_as(admin)
    r = await client.patch(url, json={"auto_categorias": ["rastreio", "duvida_produto"]})
    assert r.status_code == 200
    for produto in ASSUNTOS_SO_PESSOA_NO_AGENTE:
        r = await client.patch(url, json={"auto_agente": ["rastreio", produto]})
        assert (r.status_code, r.json()["detail"]["code"]) == (422, "assunto_fora_do_automatico")
        assert r.json()["detail"]["categorias"] == [produto]
    r = await client.patch(url, json={"auto_agente": ["rastreio"]})
    assert (r.status_code, r.json()["auto_agente"]) == (200, ["rastreio"])
    # O valor antigo (de antes do ajuste) sai quando o canal é gravado de novo.
    canal.auto_agente = ["rastreio", "duvida_produto"]
    await db.commit()
    r = await client.patch(url, json={"auto_categorias": ["rastreio", "duvida_produto"]})
    assert r.status_code == 200 and r.json()["auto_agente"] == ["rastreio"]


@pytest.mark.parametrize("agente", [True, False])
async def test_o_sugerir_de_quem_so_le_nao_tem_teto_de_chamadas(
    client, db, make_user, pessoa, com_chave, ia_ligada, monkeypatch, agente
):
    """28/10/2026, o dono ("tire total essa trava"): o "Sugerir" de quem só lê não
    consulta mais o teto diário de chamadas (nem o do agente, nem o do legado) —
    o freio é o teto em dólar do agente."""
    conversa, _gatilho = await _canal_com_agente(db, make_user, agente=agente)
    monkeypatch.setattr(rota, "SO_ADMIN", True)
    usadas: list[str] = []

    async def teto(chamadas: int = 1, *, chave: str = ia._CHAVE_TETO) -> bool:
        usadas.append(chave)
        return False

    monkeypatch.setattr(ia, "_dentro_do_teto", teto)
    r = await client.post(f"{URL}/conversas/{conversa.id}/rascunho")
    assert r.status_code != 429, r.text
    assert usadas == []
