"""A API da tela `/atendimento`.

- permissão `atendimento` (view lê, edit responde, delete apaga) e escopo
  por equipe pela loja da conversa;
- a fila com filtros e paginação por `antes_de`, misturando as DMs do
  Instagram (só leitura);
- o detalhe traz mensagens, sugestão, pedido e se dá para enviar AGORA;
- responder: trava → 409, texto reprovado → 422, simulador → 200;
- modo `auto` (e as categorias que saem sozinhas) só admin liga;
- manual (regras), respostas prontas (modelos), sincronizar e métricas.

Validador, contexto e IA são de outros lotes: entram falsos, pelo contrato.
"""

from __future__ import annotations

import sys
import types
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import worker_pool
from app.config import get_settings
from app.models import (
    AtendimentoAvaliacao,
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoRascunho,
    DmConversa,
    DmMensagem,
    Integration,
    IntegrationPlatform,
    User,
    UserRole,
)
from app.models.pricing import StoreInfo
from app.redis_client import redis
from app.routers import atendimento as rota
from app.security.cipher import encrypt_json
from app.services.atendimento import contexto, gravar, ia, validador

URL = "/api/atendimento"
AGORA = datetime.now(UTC)
PODE_TUDO = {"atendimento": {"view": True, "edit": True}}


# ─────────────── falsos (contratos dos outros lotes) ───────────────


@pytest.fixture(autouse=True)
def _permissao_fina(monkeypatch):
    """A caixa está SÓ ADMIN por enquanto (rota.SO_ADMIN, 30/09/2026). Os
    testes daqui usam não-admin com o recurso `atendimento` porque cobrem a
    permissão fina (view/edit/delete, equipe, `auto` só admin), que volta a
    valer quando a caixa abrir para a equipe. A trava de admin tem os testes
    dela em test_atendimento_so_admin.py."""
    monkeypatch.setattr(rota, "SO_ADMIN", False)


@pytest.fixture(autouse=True)
def _cerebro_falso(monkeypatch):
    def normalizar(texto: str, *, plataforma: str, canal: str) -> str:
        return " ".join((texto or "").split())

    def validar(texto: str, *, plataforma: str, canal: str, origem: str) -> list[str]:
        return ["Contato fora da loja (WhatsApp)."] if "whatsapp" in texto.lower() else (
            [] if texto.strip() else ["Resposta vazia."]
        )

    async def contexto_da_conversa(session, conversa):
        return {
            "pedido": {"numero": "123", "numeroloja": conversa.pedido_marketplace},
            "logistica": None,
            "chamados": [],
            "devolucoes": [],
        }

    monkeypatch.setattr(validador, "normalizar", normalizar)
    monkeypatch.setattr(validador, "validar", validar)
    monkeypatch.setattr(contexto, "contexto_da_conversa", contexto_da_conversa)


@pytest.fixture(autouse=True)
def _chaves(monkeypatch):
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
    return s


@pytest.fixture
async def pessoa(make_user, auth_as) -> User:
    u = await make_user(permissions=PODE_TUDO)
    auth_as(u)
    return u


@pytest.fixture
def manual_sem_conflito(monkeypatch):
    """O `manual.py` falso (outro lote), pelo contrato: nada bate com nada.

    Os testes daqui são do CRUD e da trava do automático; a conferência de
    conflito tem os seus em test_atendimento_parte2_api.py.
    """
    modulo = types.ModuleType(rota._MANUAL)

    async def conflitos_da_regra(session, regra_dados, ignorar_id=None):
        return []

    async def conflitos_existentes(session):
        return []

    async def categorias_ativas(session):
        return [{"id": c, "nome": c} for c in ("rastreio", "outro")]

    modulo.conflitos_da_regra = conflitos_da_regra
    modulo.conflitos_existentes = conflitos_existentes
    modulo.categorias_ativas = categorias_ativas
    monkeypatch.setitem(sys.modules, rota._MANUAL, modulo)
    return modulo


# ─────────────── fábrica ───────────────


async def _loja(
    db: AsyncSession, dono: User, nome: str = "kfa", modo: str = "observar"
) -> tuple[Integration, AtendimentoCanal]:
    integ = Integration(
        user_id=dono.id,
        platform=IntegrationPlatform.SHOPEE,
        name=nome,
        credentials=encrypt_json({"access_token": "t"}),
    )
    db.add(integ)
    await db.flush()
    canal = AtendimentoCanal(
        integration_id=integ.id, plataforma="shopee", canal="chat", modo=modo, status="ok"
    )
    db.add(canal)
    await db.commit()
    return integ, canal


async def _conversa(
    db: AsyncSession,
    integ: Integration,
    canal: AtendimentoCanal,
    externo_id: str,
    *,
    cliente_ha: timedelta,
    loja_ha: timedelta | None = None,
    pedido: str | None = None,
    nome: str = "Comprador",
) -> AtendimentoConversa:
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=integ,
        plataforma="shopee",
        canal_nome="chat",
        externo_id=externo_id,
        pedido_marketplace=pedido,
        comprador_nome=nome,
    )
    await gravar.gravar_mensagem(
        db,
        conversa,
        externo_id=f"{externo_id}-c",
        autor="cliente",
        texto=f"pergunta {externo_id}",
        enviada_em=AGORA - cliente_ha,
    )
    if loja_ha is not None:
        await gravar.gravar_mensagem(
            db,
            conversa,
            externo_id=f"{externo_id}-l",
            autor="loja",
            texto=f"resposta {externo_id}",
            enviada_em=AGORA - loja_ha,
        )
    await db.commit()
    return conversa


async def _rascunho(db: AsyncSession, conversa: AtendimentoConversa, **campos):
    r = AtendimentoRascunho(
        conversa_id=conversa.id,
        texto=campos.pop("texto", "Seu pedido saiu hoje."),
        categoria="rastreio",
        confianca=0.9,
        precisa_humano=False,
        **campos,
    )
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return r


async def _fila(db, make_user):
    """Cinco conversas da Shopee (SLA 12 h), uma em cada situação da fila."""
    dono = await make_user()
    integ, canal = await _loja(db, dono)
    fila = {
        "respondida": await _conversa(
            db, integ, canal, "D", cliente_ha=timedelta(hours=1), loja_ha=timedelta(minutes=30)
        ),
        "aguardando": await _conversa(db, integ, canal, "A", cliente_ha=timedelta(hours=3)),
        "vencendo": await _conversa(db, integ, canal, "C", cliente_ha=timedelta(hours=11)),
        "vencida": await _conversa(
            db, integ, canal, "B", cliente_ha=timedelta(hours=13), pedido="250925XYZ"
        ),
        "fechada": await _conversa(db, integ, canal, "E", cliente_ha=timedelta(hours=5)),
    }
    fila["fechada"].situacao = "fechada"
    await gravar.recalcular_conversa(db, fila["fechada"])
    await db.commit()
    await _rascunho(db, fila["aguardando"])
    return integ, canal, fila


def _ids(resposta) -> list[str]:
    return [i["id"] for i in resposta.json()["itens"]]


# ─────────────── permissão ───────────────


async def test_sem_permissao_e_403(client, make_user, auth_as, db):
    auth_as(await make_user())
    r = await client.get(f"{URL}/conversas")
    assert r.status_code == 403
    assert r.json()["detail"]["resource"] == "atendimento"

    auth_as(await make_user(permissions={"atendimento": {"view": True}}))
    assert (await client.get(f"{URL}/conversas")).status_code == 200
    r = await client.post(f"{URL}/conversas/{uuid4()}/responder", json={"texto": "oi"})
    assert r.status_code == 403
    r = await client.post(f"{URL}/regras", json={"quando": "a", "faca": "b"})
    assert r.status_code == 403


# ─────────────── lista ───────────────


async def test_menu_filtrar_e_bolinha_de_pendentes(client, db, make_user, pessoa):
    """01/10/2026: Todas / Falta responder + menu Filtrar, como o Duoke.

    "automatica" = esperando, mas só a resposta automática falou depois do
    comprador; pré/pós-venda pelo canal e pelo pedido; e a bolinha
    (`pendentes`) conta as mensagens do comprador desde a última resposta de
    verdade — a automática não zera.
    """
    integ, canal, fila = await _fila(db, make_user)
    ids = {k: str(v.id) for k, v in fila.items()}
    f = await _conversa(db, integ, canal, "F", cliente_ha=timedelta(minutes=40))
    await gravar.gravar_mensagem(
        db, f, externo_id="F-c2", autor="cliente", texto="alô?",
        enviada_em=AGORA - timedelta(minutes=35),
    )
    await gravar.gravar_mensagem(
        db, f, externo_id="F-auto", autor="loja",
        texto="Olá, a sua mensagem foi recebida. Há mais mensagens neste momento",
        enviada_em=AGORA - timedelta(minutes=25),
    )
    await db.commit()
    ids["auto"] = str(f.id)

    async def filtro(nome: str) -> list[str]:
        resposta = await client.get(f"{URL}/conversas", params={"filtro": nome})
        assert resposta.status_code == 200
        return _ids(resposta)

    assert await filtro("automatica") == [ids["auto"]]
    assert ids["auto"] in await filtro("aguardando")
    assert await filtro("pre_venda") == [
        ids["auto"], ids["respondida"], ids["aguardando"], ids["vencendo"]
    ]
    assert await filtro("pos_venda") == [ids["vencida"]]

    itens = {i["id"]: i for i in (await client.get(f"{URL}/conversas")).json()["itens"]}
    assert itens[ids["auto"]]["pendentes"] == 2
    assert itens[ids["auto"]]["nao_lidas"] == 0  # o "não lida" da plataforma zerou
    assert itens[ids["aguardando"]]["pendentes"] == 1
    assert itens[ids["respondida"]]["pendentes"] == 0
    detalhe = (await client.get(f"{URL}/conversas/{ids['auto']}")).json()
    assert detalhe["conversa"]["pendentes"] == 2


async def test_lista_filtros_e_paginacao(client, db, make_user, pessoa):
    _integ, _canal, fila = await _fila(db, make_user)
    ids = {k: str(v.id) for k, v in fila.items()}

    r = await client.get(f"{URL}/conversas")
    assert r.status_code == 200
    # Mais recente primeiro; a fechada (resolvida) sai de "todas".
    assert _ids(r) == [ids["respondida"], ids["aguardando"], ids["vencendo"], ids["vencida"]]
    assert r.json()["proximo"] is None
    item = r.json()["itens"][1]
    assert item["tem_rascunho"] is True
    assert item["aguardando_resposta"] is True
    assert item["somente_leitura"] is False
    assert item["ultima_mensagem_resumo"] == "pergunta A"

    async def filtro(nome: str) -> list[str]:
        resposta = await client.get(f"{URL}/conversas", params={"filtro": nome})
        assert resposta.status_code == 200
        return _ids(resposta)

    # "Falta responder" pelo PRAZO mais curto (01/10/2026), não pela recência.
    assert await filtro("aguardando") == [ids["vencida"], ids["vencendo"], ids["aguardando"]]
    assert await filtro("vencendo") == [ids["vencendo"]]
    assert await filtro("vencidas") == [ids["vencida"]]
    assert await filtro("com_rascunho") == [ids["aguardando"]]
    assert await filtro("fechadas") == [ids["fechada"]]
    assert await filtro("minhas") == []

    r = await client.get(f"{URL}/conversas", params={"q": "250925xyz"})
    assert _ids(r) == [ids["vencida"]]
    r = await client.get(f"{URL}/conversas", params={"plataforma": "ml"})
    assert _ids(r) == []

    # Paginação por `antes_de`.
    r1 = await client.get(f"{URL}/conversas", params={"limite": 2})
    assert _ids(r1) == [ids["respondida"], ids["aguardando"]]
    proximo = r1.json()["proximo"]
    assert proximo is not None
    r2 = await client.get(f"{URL}/conversas", params={"limite": 2, "antes_de": proximo})
    assert _ids(r2) == [ids["vencendo"], ids["vencida"]]
    assert r2.json()["proximo"] is None

    # "Falta responder": a página seguinte vem pelo cursor do PRAZO.
    p1 = await client.get(f"{URL}/conversas", params={"filtro": "aguardando", "limite": 2})
    assert _ids(p1) == [ids["vencida"], ids["vencendo"]]
    cursor = p1.json()["proximo"]
    assert cursor.startswith("prazo:") and cursor.endswith(f"|{ids['vencendo']}")
    p2 = await client.get(
        f"{URL}/conversas", params={"filtro": "aguardando", "limite": 2, "antes_de": cursor}
    )
    assert (_ids(p2), p2.json()["proximo"]) == ([ids["aguardando"]], None)
    # Cursor da outra ordem (ou lixo) não vira página errada: 422.
    for errado in (proximo, "prazo:lixo|x", "prazo:", "ontem"):
        r = await client.get(
            f"{URL}/conversas",
            params={"filtro": "aguardando" if errado == proximo else "todas", "antes_de": errado},
        )
        assert (r.status_code, r.json()["detail"]["code"]) == (422, "cursor_invalido"), errado

    r = await client.get(f"{URL}/conversas", params={"filtro": "qualquer"})
    assert (r.status_code, r.json()["detail"]["code"]) == (422, "filtro_invalido")


async def test_paginacao_nao_parte_empate_de_horario(client, db, make_user, pessoa):
    """Relógio em segundos: duas conversas no mesmo segundo não podem se perder no corte."""
    dono = await make_user()
    integ, canal = await _loja(db, dono)
    x = await _conversa(db, integ, canal, "X", cliente_ha=timedelta(hours=1))
    y = await _conversa(db, integ, canal, "Y", cliente_ha=timedelta(hours=2))
    z = await _conversa(db, integ, canal, "Z", cliente_ha=timedelta(hours=2))
    assert y.ultima_mensagem_em == z.ultima_mensagem_em

    r1 = await client.get(f"{URL}/conversas", params={"limite": 2})
    assert _ids(r1) == [str(x.id)]
    r2 = await client.get(
        f"{URL}/conversas", params={"limite": 2, "antes_de": r1.json()["proximo"]}
    )
    assert sorted(_ids(r2)) == sorted([str(y.id), str(z.id)])
    assert r2.json()["proximo"] is None


# ─────────────── detalhe ───────────────


async def test_detalhe_com_rascunho_contexto_e_envio(client, db, make_user, pessoa, _chaves):
    _integ, canal, fila = await _fila(db, make_user)
    c = fila["aguardando"]

    r = await client.get(f"{URL}/conversas/{c.id}")
    assert r.status_code == 200
    d = r.json()
    assert d["conversa"]["id"] == str(c.id)
    assert d["conversa"]["comprador_nome"] == "Comprador"
    assert [m["texto"] for m in d["mensagens"]] == ["pergunta A"]
    assert d["mensagens"][0]["autor"] == "cliente"
    assert d["mensagens"][0]["autor_nome"] == "Comprador"
    assert d["rascunho"]["texto"] == "Seu pedido saiu hoje."
    assert d["contexto"]["pedido"]["numero"] == "123"
    assert d["envio"] == {
        "pode_enviar": False,
        "motivo": "O envio pelo DaVinci está desligado (ATENDIMENTO_ENVIO_ATIVO).",
        "codigo": "envio_desligado",
        "limite_caracteres": 1000,
        "modo": "observar",
        "sla_horas": 12,
        "modo_observacao": True,
    }

    _chaves.atendimento_envio_ativo = True
    d = (await client.get(f"{URL}/conversas/{c.id}")).json()
    assert (d["envio"]["pode_enviar"], d["envio"]["codigo"]) == (False, "canal_em_observacao")
    assert d["envio"]["modo_observacao"] is True

    canal.modo = "humano"
    await db.commit()
    d = (await client.get(f"{URL}/conversas/{c.id}")).json()
    assert d["envio"]["pode_enviar"] is True
    assert d["envio"]["modo"] == "humano"
    assert d["envio"]["modo_observacao"] is False

    assert (await client.get(f"{URL}/conversas/{uuid4()}")).status_code == 404
    assert (await client.get(f"{URL}/conversas/lixo")).status_code == 404


async def test_contexto_que_falha_nao_esconde_a_conversa(
    client, db, make_user, pessoa, monkeypatch
):
    _integ, _canal, fila = await _fila(db, make_user)

    async def quebra(session, conversa):
        raise RuntimeError("bling fora do ar")

    monkeypatch.setattr(contexto, "contexto_da_conversa", quebra)
    r = await client.get(f"{URL}/conversas/{fila['vencida'].id}")
    assert r.status_code == 200
    # As mesmas chaves do contexto vazio de verdade (inclusive `nota_fiscal`):
    # a tela não precisa distinguir "sem pedido" de "o contexto falhou".
    assert r.json()["contexto"] == contexto.vazio() == {
        "pedido": None,
        "logistica": None,
        "chamados": [],
        "devolucoes": [],
        "nota_fiscal": None,
        "outras_perguntas": [],
        "reclamacoes": [],
        "avaliacoes": [],
    }


# ─────────────── responder ───────────────


async def test_responder_409_422_e_200(client, db, make_user, pessoa, _chaves):
    _integ, canal, fila = await _fila(db, make_user)
    c = fila["aguardando"]
    url = f"{URL}/conversas/{c.id}/responder"

    r = await client.post(url, json={"texto": "Seu pedido saiu."})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "envio_desligado"

    _chaves.atendimento_envio_ativo = True
    _chaves.atendimento_simulador = True
    r = await client.post(url, json={"texto": "Seu pedido saiu."})
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "canal_em_observacao")

    canal.modo = "humano"
    await db.commit()
    r = await client.post(url, json={"texto": "me chama no whatsapp"})
    assert r.status_code == 422
    assert r.json()["detail"] == {
        "code": "texto_invalido",
        "detail": ["Contato fora da loja (WhatsApp)."],
    }

    rascunho = (
        await db.execute(
            select(AtendimentoRascunho).where(AtendimentoRascunho.conversa_id == c.id)
        )
    ).scalar_one()
    r = await client.post(
        url, json={"texto": "Seu pedido saiu hoje.", "rascunho_id": str(rascunho.id)}
    )
    assert r.status_code == 200
    m = r.json()["mensagem"]
    assert (m["status"], m["autor"], m["origem"]) == ("enviada", "loja", "davinci_humano")
    assert m["autor_nome"] == pessoa.email
    assert m["texto"] == "Seu pedido saiu hoje."

    d = (await client.get(f"{URL}/conversas/{c.id}")).json()
    assert d["conversa"]["aguardando_resposta"] is False
    assert d["rascunho"] is None
    assert [x["autor"] for x in d["mensagens"]] == ["cliente", "loja"]
    av = (await db.execute(select(AtendimentoAvaliacao))).scalar_one()
    assert av.acao == "enviou_igual"


# ─────────────── PATCH conversa ───────────────


async def test_patch_conversa(client, db, make_user, pessoa):
    _integ, _canal, fila = await _fila(db, make_user)
    url = f"{URL}/conversas/{fila['aguardando'].id}"

    r = await client.patch(url, json={"atribuido_a": str(pessoa.id), "ia_pausada": True})
    assert r.status_code == 200
    c = r.json()["conversa"]
    assert (c["atribuido_a"], c["atribuido_a_nome"], c["ia_pausada"]) == (
        str(pessoa.id),
        pessoa.email,
        True,
    )
    r = await client.get(f"{URL}/conversas", params={"filtro": "minhas"})
    assert _ids(r) == [str(fila["aguardando"].id)]

    c = (await client.patch(url, json={"situacao": "fechada"})).json()["conversa"]
    assert (c["situacao"], c["aguardando_resposta"], c["prazo_resposta_em"]) == (
        "fechada",
        False,
        None,
    )
    c = (await client.patch(url, json={"situacao": "aberta"})).json()["conversa"]
    assert (c["situacao"], c["aguardando_resposta"]) == ("aberta", True)

    c = (await client.patch(url, json={"sem_resposta_necessaria": True})).json()["conversa"]
    assert (c["sem_resposta_necessaria"], c["aguardando_resposta"]) == (True, False)

    c = (await client.patch(url, json={"atribuido_a": None})).json()["conversa"]
    assert c["atribuido_a"] is None
    r = await client.patch(url, json={"atribuido_a": str(uuid4())})
    assert (r.status_code, r.json()["detail"]["code"]) == (422, "usuario_inexistente")


# ─────────────── sugestão: pedir, descartar, avaliar ───────────────


async def test_pedir_rascunho(client, db, make_user, pessoa, _chaves, monkeypatch):
    _integ, _canal, fila = await _fila(db, make_user)
    c = fila["vencida"]
    url = f"{URL}/conversas/{c.id}/rascunho"

    r = await client.post(url)
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "ia_desligada")

    async def gerar(session, conversa, *, forcar=False):
        assert forcar is True
        r = AtendimentoRascunho(conversa_id=conversa.id, texto="Sugestão.", categoria="outro")
        session.add(r)
        await session.flush()
        return r

    monkeypatch.setattr(ia, "gerar_rascunho", gerar)
    _chaves.atendimento_ia_ativa = True
    r = await client.post(url)
    assert r.status_code == 200
    assert r.json()["rascunho"]["texto"] == "Sugestão."
    assert r.json()["motivo"] is None

    # Sem sugestão, a tela recebe o PORQUÊ (TELA-12), não só `rascunho: null`.
    async def nada(session, conversa, *, forcar=False):
        return None

    async def motivo(session, conversa):
        return "provedor_falhou"

    monkeypatch.setattr(ia, "gerar_rascunho", nada)
    monkeypatch.setattr(ia, "motivo_sem_rascunho", motivo)
    r = await client.post(url)
    assert r.status_code == 200
    assert r.json() == {"rascunho": None, "motivo": "provedor_falhou"}


async def test_descartar_e_avaliar(client, db, make_user, pessoa):
    _integ, _canal, fila = await _fila(db, make_user)
    r_pend = (
        await db.execute(
            select(AtendimentoRascunho).where(
                AtendimentoRascunho.conversa_id == fila["aguardando"].id
            )
        )
    ).scalar_one()

    # Pendente também se avalia (modo observação: nada sai pelo DaVinci) —
    # com a ação `observou`, que nunca vira exemplo para a IA.
    r = await client.post(f"{URL}/rascunhos/{r_pend.id}/avaliacao", json={"nota": "ok"})
    assert r.status_code == 200
    assert (r.json()["avaliacao"]["acao"], r.json()["avaliacao"]["nota"]) == ("observou", "ok")

    r = await client.post(
        f"{URL}/rascunhos/{r_pend.id}/descartar", json={"motivo": "prometeu prazo"}
    )
    assert r.status_code == 200
    assert r.json()["rascunho"]["status"] == "descartado"
    r = await client.post(f"{URL}/rascunhos/{r_pend.id}/descartar", json={"motivo": "de novo"})
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "rascunho_nao_pendente")

    r = await client.post(
        f"{URL}/rascunhos/{r_pend.id}/avaliacao",
        json={"nota": "erro", "correcao": "Nunca prometa prazo."},
    )
    assert r.status_code == 200
    av = r.json()["avaliacao"]
    assert (av["acao"], av["motivo"], av["nota"], av["correcao"]) == (
        "descartou",
        "prometeu prazo",
        "erro",
        "Nunca prometa prazo.",
    )

    # Sugestão enviada pelo automático (sem avaliação): a nota da pessoa cria a linha.
    auto = await _rascunho(db, fila["respondida"], status="enviado")
    r = await client.post(f"{URL}/rascunhos/{auto.id}/avaliacao", json={"nota": "ok"})
    assert r.status_code == 200
    assert (r.json()["avaliacao"]["acao"], r.json()["avaliacao"]["nota"]) == ("enviou_igual", "ok")

    r = await client.post(f"{URL}/rascunhos/{uuid4()}/avaliacao", json={"nota": "ok"})
    assert r.status_code == 404


# ─────────────── canais ───────────────


async def test_canal_modo_auto_so_admin(client, db, make_user, auth_as, pessoa):
    _integ, canal = await _loja(db, await make_user())
    url = f"{URL}/canais/{canal.id}"

    r = await client.patch(url, json={"modo": "humano"})
    assert (r.status_code, r.json()["modo"]) == (200, "humano")
    r = await client.patch(url, json={"modo": "auto"})
    assert (r.status_code, r.json()["detail"]["code"]) == (403, "so_admin")
    r = await client.patch(url, json={"auto_categorias": ["rastreio"]})
    assert (r.status_code, r.json()["detail"]["code"]) == (403, "so_admin")
    r = await client.patch(url, json={"modo": "qualquer"})
    assert r.status_code == 422

    auth_as(await make_user(role=UserRole.ADMIN))
    r = await client.patch(url, json={"modo": "auto", "auto_categorias": ["reembolso"]})
    assert (r.status_code, r.json()["detail"]["code"]) == (422, "categoria_so_humano")
    r = await client.patch(
        url, json={"modo": "auto", "auto_categorias": ["nota_fiscal", "rastreio", "rastreio"]}
    )
    assert r.status_code == 200
    assert (r.json()["modo"], r.json()["auto_categorias"]) == ("auto", ["rastreio", "nota_fiscal"])

    # Não-admin pode DESLIGAR o automático.
    auth_as(pessoa)
    r = await client.patch(url, json={"modo": "copiloto"})
    assert (r.status_code, r.json()["modo"]) == (200, "copiloto")

    lista = (await client.get(f"{URL}/canais")).json()
    assert [(c["conta"], c["sla_horas"], c["limite_caracteres"]) for c in lista] == [
        ("kfa", 12, 1000)
    ]


# ─────────────── manual e respostas prontas ───────────────


async def test_regras_crud(client, make_user, auth_as, pessoa, manual_sem_conflito):
    r = await client.post(
        f"{URL}/regras",
        json={"quando": "perguntar do rastreio", "faca": "mande o {rastreio}", "plataforma": "ml"},
    )
    assert r.status_code == 201
    regra = r.json()
    assert (regra["plataforma"], regra["canal"], regra["ativa"]) == ("ml", None, True)
    assert regra["updated_at"] is not None
    # Sem tipo/categoria/prioridade (tela antiga): regra geral, como antes da parte 2.
    assert (regra["tipo"], regra["categoria"], regra["prioridade"], regra["em_conflito"]) == (
        "categoria",
        None,
        100,
        False,
    )

    r = await client.post(
        f"{URL}/regras", json={"quando": "x", "faca": "y", "plataforma": "ml", "canal": "chat"}
    )
    assert (r.status_code, r.json()["detail"]["code"]) == (422, "canal_invalido")
    r = await client.post(f"{URL}/regras", json={"quando": "x", "faca": "y", "plataforma": "ebay"})
    assert r.status_code == 422

    r = await client.patch(
        f"{URL}/regras/{regra['id']}", json={"ativa": False, "plataforma": None}
    )
    assert (r.json()["ativa"], r.json()["plataforma"]) == (False, None)
    r = await client.patch(f"{URL}/regras/{regra['id']}", json={"quando": "  "})
    assert (r.status_code, r.json()["detail"]["code"]) == (422, "quando_vazio")
    assert [x["id"] for x in (await client.get(f"{URL}/regras")).json()["regras"]] == [
        regra["id"]
    ]

    # Apagar exige `delete`.
    r = await client.delete(f"{URL}/regras/{regra['id']}")
    assert r.status_code == 403
    auth_as(await make_user(permissions={"atendimento": {"view": True, "delete": True}}))
    assert (await client.delete(f"{URL}/regras/{regra['id']}")).status_code == 204
    assert (await client.get(f"{URL}/regras")).json() == {"regras": [], "conflitos": []}


async def test_modelos_crud(client, make_user, auth_as, pessoa):
    geral = (
        await client.post(f"{URL}/modelos", json={"titulo": "Obrigado", "texto": "Obrigado!"})
    ).json()
    shopee = (
        await client.post(
            f"{URL}/modelos",
            json={"titulo": "Prazo", "texto": "Envio em até 2 dias úteis.", "plataforma": "shopee",
                  "canal": "chat", "ordem": -1},
        )
    ).json()
    await client.post(
        f"{URL}/modelos", json={"titulo": "ML", "texto": "Olá!", "plataforma": "ml"}
    )

    todos = (await client.get(f"{URL}/modelos")).json()
    assert [m["titulo"] for m in todos] == ["Prazo", "ML", "Obrigado"]
    da_shopee = (await client.get(f"{URL}/modelos", params={"plataforma": "shopee"})).json()
    assert [m["id"] for m in da_shopee] == [shopee["id"], geral["id"]]

    r = await client.patch(f"{URL}/modelos/{geral['id']}", json={"ativo": False, "ordem": 5})
    assert (r.json()["ativo"], r.json()["ordem"]) == (False, 5)
    assert (await client.delete(f"{URL}/modelos/{geral['id']}")).status_code == 403
    auth_as(await make_user(permissions={"atendimento": {"view": True, "delete": True}}))
    assert (await client.delete(f"{URL}/modelos/{geral['id']}")).status_code == 204


# ─────────────── Instagram (só leitura) ───────────────


async def _dm(db: AsyncSession) -> DmConversa:
    c = DmConversa(conta="charlots_br", participante_id="178414", participante_nome="Maria")
    db.add(c)
    await db.flush()
    db.add_all(
        [
            DmMensagem(
                conversa_id=c.id,
                mid="m1",
                direcao="recebida",
                texto="tem na cor azul?",
                ocorrido_em=AGORA - timedelta(hours=2),
            ),
            # O que o robô TERIA dito (modo seco) não aparece.
            DmMensagem(
                conversa_id=c.id,
                direcao="enviada",
                texto="resposta seca",
                status="seco",
                ocorrido_em=AGORA - timedelta(minutes=110),
            ),
        ]
    )
    await db.commit()
    return c


async def test_instagram_aparece_so_leitura(client, db, make_user, pessoa):
    _integ, _canal, fila = await _fila(db, make_user)
    dm = await _dm(db)
    ig = f"ig:{dm.id}"

    r = await client.get(f"{URL}/conversas")
    ids = _ids(r)
    # Entre a respondida (30 min) e a aguardando (3 h), pela última mensagem (2 h).
    assert ids[:3] == [str(fila["respondida"].id), ig, str(fila["aguardando"].id)]
    item = next(i for i in r.json()["itens"] if i["id"] == ig)
    assert (item["plataforma"], item["somente_leitura"], item["aguardando_resposta"]) == (
        "instagram",
        True,
        True,
    )
    assert item["ultima_mensagem_resumo"] == "tem na cor azul?"
    assert _ids(await client.get(f"{URL}/conversas", params={"plataforma": "instagram"})) == [ig]
    assert _ids(await client.get(f"{URL}/conversas", params={"filtro": "com_rascunho"})) == [
        str(fila["aguardando"].id)
    ]

    d = (await client.get(f"{URL}/conversas/{ig}")).json()
    assert [m["texto"] for m in d["mensagens"]] == ["tem na cor azul?"]
    # A tela manda o id com encodeURIComponent (`ig%3A...`): chega igual.
    assert (await client.get(f"{URL}/conversas/ig%3A{dm.id}")).json()["conversa"]["id"] == ig
    assert d["envio"]["pode_enviar"] is False
    assert d["envio"]["codigo"] == "somente_leitura"
    assert d["conversa"]["comprador_id"] == "178414"

    r = await client.post(f"{URL}/conversas/{ig}/responder", json={"texto": "Temos sim!"})
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "somente_leitura")
    r = await client.patch(f"{URL}/conversas/{ig}", json={"situacao": "fechada"})
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "somente_leitura")

    # "Falta responder" pelo prazo: a DM (24 h desde a mensagem de 2 h atrás)
    # entra depois das da Shopee (12 h), em qualquer página.
    fila_prazo = [str(fila[k].id) for k in ("vencida", "vencendo", "aguardando")] + [ig]
    assert _ids(await client.get(f"{URL}/conversas", params={"filtro": "aguardando"})) == (
        fila_prazo
    )
    p1 = await client.get(f"{URL}/conversas", params={"filtro": "aguardando", "limite": 3})
    assert _ids(p1) == fila_prazo[:3]
    p2 = await client.get(
        f"{URL}/conversas",
        params={"filtro": "aguardando", "limite": 3, "antes_de": p1.json()["proximo"]},
    )
    assert (_ids(p2), p2.json()["proximo"]) == ([ig], None)
    # DM não tem etiqueta: some dos filtros por etiqueta.
    for params in ({"filtro": "pre_venda"}, {"etiqueta": "pre_venda"}):
        assert ig not in _ids(await client.get(f"{URL}/conversas", params=params)), params

    resumo = (await client.get(f"{URL}/resumo")).json()
    assert {
        "plataforma": "instagram",
        "aguardando": 1,
        "vencendo": 0,
        "vencidas": 0,
        "a_conferir": 0,
        "nao_lidas": 0,
        "etiquetas": {},
    } in resumo["plataformas"]


# ─────────────── escopo por equipe ───────────────


async def test_equipe_so_ve_as_proprias_lojas(client, db, make_user, auth_as):
    dono = await make_user()
    integ_a, canal_a = await _loja(db, dono, "loja-a")
    integ_b, canal_b = await _loja(db, dono, "loja-b")
    conv_a = await _conversa(db, integ_a, canal_a, "a1", cliente_ha=timedelta(hours=1))
    conv_b = await _conversa(db, integ_b, canal_b, "b1", cliente_ha=timedelta(hours=2))
    await _dm(db)
    db.add(
        StoreInfo(user_id=dono.id, platform="shopee", account_name="loja-a",
                  sales_team=7, integration_id=integ_a.id)
    )
    await db.commit()
    membro = await make_user(permissions=PODE_TUDO)
    membro.sales_teams = [7]
    await db.commit()
    auth_as(membro)

    assert _ids(await client.get(f"{URL}/conversas")) == [str(conv_a.id)]
    assert (await client.get(f"{URL}/conversas/{conv_b.id}")).status_code == 404
    assert [c["id"] for c in (await client.get(f"{URL}/canais")).json()] == [str(canal_a.id)]
    r = await client.post(f"{URL}/conversas/{conv_b.id}/responder", json={"texto": "oi"})
    assert r.status_code == 404
    resumo = (await client.get(f"{URL}/resumo")).json()
    assert [lj["conta"] for lj in resumo["lojas"]] == ["loja-a"]
    assert all(p["plataforma"] != "instagram" for p in resumo["plataformas"])


# ─────────────── resumo, sincronizar, métricas ───────────────


async def test_resumo(client, db, make_user, pessoa, _chaves):
    await _fila(db, make_user)
    _chaves.atendimento_simulador = True
    r = (await client.get(f"{URL}/resumo")).json()
    shopee = next(p for p in r["plataformas"] if p["plataforma"] == "shopee")
    assert (shopee["aguardando"], shopee["vencendo"], shopee["vencidas"]) == (3, 1, 1)
    assert {p["plataforma"] for p in r["plataformas"]} >= {"shopee", "ml", "tiktok", "amazon"}
    (loja,) = r["lojas"]
    assert (loja["conta"], loja["aguardando"], loja["vencidas"]) == ("kfa", 3, 1)
    assert [c["plataforma"] for c in r["canais"]] == ["shopee"]
    assert r["flags"]["simulador"] is True
    assert r["flags"]["envio_ativo"] is False


async def test_sincronizar_throttle_e_fila(client, pessoa, _chaves, monkeypatch):
    await redis.delete(rota.CHAVE_SINCRONIZAR)
    r = await client.post(f"{URL}/sincronizar")
    assert r.json() == {"enfileirado": False, "motivo": "leitura_desligada"}

    enfileirados: list[str] = []

    class _Pool:
        async def enqueue_job(self, nome, *args, **kwargs):
            enfileirados.append(nome)

    async def pool():
        return _Pool()

    monkeypatch.setattr(worker_pool, "get_arq_pool", pool)
    _chaves.atendimento_leitura_ativa = True
    try:
        assert (await client.post(f"{URL}/sincronizar")).json() == {
            "enfileirado": True,
            "motivo": None,
        }
        assert (await client.post(f"{URL}/sincronizar")).json() == {
            "enfileirado": False,
            "motivo": "recente",
        }
    finally:
        await redis.delete(rota.CHAVE_SINCRONIZAR)
    assert enfileirados == ["atendimento_sincronizar"]


async def test_metricas_basicas(client, db, make_user, pessoa):
    dono = await make_user()
    integ, canal = await _loja(db, dono)
    c = await _conversa(
        db, integ, canal, "m1", cliente_ha=timedelta(minutes=100), loja_ha=timedelta(minutes=70)
    )
    # Segundo turno do cliente, ainda no prazo e sem resposta: não conta no %.
    await gravar.gravar_mensagem(
        db,
        c,
        externo_id="m1-c2",
        autor="cliente",
        texto="e agora?",
        enviada_em=AGORA - timedelta(minutes=60),
    )
    await gravar.gravar_mensagem(
        db,
        c,
        externo_id="m1-c3",
        autor="cliente",
        texto="alô?",
        enviada_em=AGORA - timedelta(minutes=50),
    )
    r1 = await _rascunho(db, c, status="editado")
    db.add(AtendimentoAvaliacao(rascunho_id=r1.id, acao="editou", texto_final="x"))
    await db.commit()

    m = (await client.get(f"{URL}/metricas", params={"dias": 7})).json()
    assert m["dias"] == 7
    (loja,) = m["lojas"]
    assert (loja["conta"], loja["plataforma"]) == ("kfa", "shopee")
    assert (loja["recebidas"], loja["respondidas"]) == (2, 1)
    assert loja["mediana_primeira_resposta_min"] == pytest.approx(30.0, abs=0.2)
    assert loja["p90_primeira_resposta_min"] == pytest.approx(30.0, abs=0.2)
    assert loja["pct_no_prazo"] == 100.0
    assert m["ia"] == {
        "rascunhos": 1,
        "enviou_igual": 0,
        "editou": 1,
        "descartou": 0,
        "escreveu_do_zero": 0,
        "nota_ok": 0,
        "nota_erro": 0,
    }


# ─────────────── revisão de 25/09 ───────────────


async def test_envio_a_conferir_filtro_resumo_e_conferir(client, db, make_user, pessoa):
    """Resposta nossa em `revisar` (timeout, envio interrompido) conta como
    resposta — a conversa sai da fila para ninguém responder por cima —, mas
    tem que aparecer em algum lugar: selo, filtro "A conferir", contador. A
    pessoa confere na plataforma e diz se saiu."""
    _integ, _canal, fila = await _fila(db, make_user)
    c = fila["aguardando"]
    revisar = AtendimentoMensagem(
        conversa_id=c.id, autor="loja", origem="davinci_humano", texto="Seu pedido saiu.",
        status="revisar", erro="timeout",
    )
    db.add(revisar)
    await gravar.recalcular_conversa(db, c)
    await db.commit()
    assert c.aguardando_resposta is False

    r = await client.get(f"{URL}/conversas", params={"filtro": "a_conferir"})
    assert _ids(r) == [str(c.id)]
    assert r.json()["itens"][0]["envio_a_conferir"] is True
    todas = {i["id"]: i for i in (await client.get(f"{URL}/conversas")).json()["itens"]}
    assert todas[str(fila["vencida"].id)]["envio_a_conferir"] is False
    resumo = (await client.get(f"{URL}/resumo")).json()
    assert resumo["a_conferir"] == 1
    shopee = next(p for p in resumo["plataformas"] if p["plataforma"] == "shopee")
    assert shopee["a_conferir"] == 1
    d = (await client.get(f"{URL}/conversas/{c.id}")).json()
    assert d["conversa"]["envio_a_conferir"] is True

    # Não saiu: falhou, e a conversa volta para a fila.
    r = await client.post(f"{URL}/mensagens/{revisar.id}/conferir", json={"saiu": False})
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["mensagem"]["status"] == "falhou"
    assert corpo["conversa"]["aguardando_resposta"] is True
    assert corpo["conversa"]["envio_a_conferir"] is False
    # Já conferida: não confere de novo.
    r = await client.post(f"{URL}/mensagens/{revisar.id}/conferir", json={"saiu": True})
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "mensagem_nao_revisar")

    # Saiu: vira enviada e a conversa segue respondida.
    outra = AtendimentoMensagem(
        conversa_id=c.id, autor="loja", origem="davinci_ia", texto="Já saiu.", status="revisar"
    )
    db.add(outra)
    await db.commit()
    corpo = (
        await client.post(f"{URL}/mensagens/{outra.id}/conferir", json={"saiu": True})
    ).json()
    assert corpo["mensagem"]["status"] == "enviada"
    assert corpo["conversa"]["aguardando_resposta"] is False
    await db.refresh(outra)
    assert outra.payload["conferido"]["saiu"] is True
    assert (await client.get(f"{URL}/resumo")).json()["a_conferir"] == 0

    r = await client.post(f"{URL}/mensagens/{uuid4()}/conferir", json={"saiu": True})
    assert r.status_code == 404


async def test_conferir_exige_edit_e_escopo(client, db, make_user, auth_as):
    dono = await make_user()
    integ, canal = await _loja(db, dono)
    c = await _conversa(db, integ, canal, "X", cliente_ha=timedelta(hours=1))
    m = AtendimentoMensagem(
        conversa_id=c.id, autor="loja", origem="davinci_humano", texto="oi", status="revisar"
    )
    db.add(m)
    await db.commit()
    auth_as(await make_user(permissions={"atendimento": {"view": True}}))
    r = await client.post(f"{URL}/mensagens/{m.id}/conferir", json={"saiu": True})
    assert r.status_code == 403


async def test_detalhe_aposenta_envio_preso(client, db, make_user, pessoa):
    """Envio morto no meio (deploy) com a leitura desligada: o detalhe o
    aposenta (vira `revisar`, "A conferir") em vez de mostrar "enviando…"
    para sempre com a caixa travada."""
    _integ, _canal, fila = await _fila(db, make_user)
    c = fila["aguardando"]
    preso = AtendimentoMensagem(
        conversa_id=c.id, autor="loja", origem="davinci_humano", texto="Olá", status="enviando"
    )
    db.add(preso)
    await db.commit()
    await db.execute(
        update(AtendimentoMensagem)
        .where(AtendimentoMensagem.id == preso.id)
        .values(created_at=AGORA - timedelta(hours=2))
    )
    await db.commit()
    d = (await client.get(f"{URL}/conversas/{c.id}")).json()
    loja = [m for m in d["mensagens"] if m["autor"] == "loja"]
    assert [(m["status"], m["erro"]) for m in loja] == [("revisar", "envio_interrompido")]
    assert d["conversa"]["envio_a_conferir"] is True


async def test_responder_com_ultima_vista_devolve_conversa_mudou(
    client, db, make_user, pessoa, _chaves
):
    _chaves.atendimento_envio_ativo = True
    _chaves.atendimento_simulador = True
    _integ, canal, fila = await _fila(db, make_user)
    canal.modo = "humano"
    await db.commit()
    c = fila["respondida"]  # a loja respondeu DEPOIS da pergunta do cliente
    vista = (
        await db.execute(
            select(AtendimentoMensagem).where(
                AtendimentoMensagem.conversa_id == c.id, AtendimentoMensagem.autor == "cliente"
            )
        )
    ).scalar_one()
    url = f"{URL}/conversas/{c.id}/responder"
    r = await client.post(url, json={"texto": "Oi!", "ultima_vista_id": str(vista.id)})
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "conversa_mudou")
    assert "fora do DaVinci" in r.json()["detail"]["detail"]
    r = await client.post(
        url, json={"texto": "Oi!", "ultima_vista_id": str(vista.id), "confirmar": True}
    )
    assert r.status_code == 200


async def test_patch_conversa_ocupada_pelo_sync_e_409(
    client, db, make_user, pessoa, monkeypatch
):
    import app.db as _db

    monkeypatch.setattr(gravar, "ESPERA_TRAVA_TELA", "300ms")
    _integ, _canal, fila = await _fila(db, make_user)
    c = fila["aguardando"]
    async with _db.SessionLocal() as sync_s:
        x = await sync_s.get(AtendimentoConversa, c.id)
        x.nao_lidas = 9
        await sync_s.flush()  # a rodada do sync segura a linha
        r = await client.patch(f"{URL}/conversas/{c.id}", json={"situacao": "fechada"})
        assert (r.status_code, r.json()["detail"]["code"]) == (409, "conversa_ocupada")
        await sync_s.rollback()
    r = await client.patch(f"{URL}/conversas/{c.id}", json={"situacao": "fechada"})
    assert r.status_code == 200


async def _amazon(db: AsyncSession, dono: User, nome: str, **kw) -> Integration:
    integ = Integration(
        user_id=dono.id,
        platform=IntegrationPlatform.AMAZON,
        name=nome,
        credentials=encrypt_json({"refresh_token": "t"}),
        **kw,
    )
    db.add(integ)
    await db.commit()
    return integ


async def test_amazon_sem_conta_escolher_a_conta(client, db, make_user, pessoa, _chaves):
    """O e-mail da Amazon que não disse a conta: a tela pede para escolher, e a
    conversa passa a ter loja (e canal) — e a poder ser respondida."""
    _chaves.atendimento_envio_ativo = True
    dono = await make_user()
    lyra = await _amazon(db, dono, "lyra")
    arquivada = await _amazon(db, dono, "velha", archived_at=AGORA)
    shopee, _canal = await _loja(db, dono, "kfa")
    c, _ = await gravar.upsert_conversa(
        db, canal=None, integration=None, plataforma="amazon", canal_nome="email",
        externo_id="<thread-1@marketplace.amazon.com.br>", conta="(conta não identificada)",
    )
    await gravar.gravar_mensagem(
        db, c, externo_id="<m1>", autor="cliente", texto="Cadê meu pedido?",
        enviada_em=AGORA - timedelta(hours=1),
    )
    await db.commit()

    d = (await client.get(f"{URL}/conversas/{c.id}")).json()
    assert d["envio"]["codigo"] == "sem_integracao"
    assert d["envio"]["motivo"] == "Escolha de qual conta Amazon é esta conversa."
    assert d["envio"]["modo"] is None

    url = f"{URL}/conversas/{c.id}"
    for integ, esperado in ((arquivada, 422), (shopee, 422)):
        r = await client.patch(url, json={"integration_id": str(integ.id)})
        assert (r.status_code, r.json()["detail"]["code"]) == (esperado, "integracao_invalida")
    r = await client.patch(url, json={"integration_id": str(lyra.id)})
    assert r.status_code == 200
    conv = r.json()["conversa"]
    assert (conv["integration_id"], conv["conta"]) == (str(lyra.id), "lyra")
    await db.refresh(c)
    canal = await db.get(AtendimentoCanal, c.canal_id)
    assert (canal.integration_id, canal.canal, canal.modo) == (lyra.id, "email", "observar")
    d = (await client.get(url)).json()
    assert (d["envio"]["codigo"], d["envio"]["modo"]) == ("canal_em_observacao", "observar")

    # Conversa que JÁ tem loja: a loja não se troca por aqui.
    r = await client.patch(url, json={"integration_id": str(lyra.id)})
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "integracao_fixa")


async def test_regra_que_vale_para_loja_no_auto_so_admin(
    client, db, make_user, auth_as, pessoa, manual_sem_conflito
):
    """O manual entra no prompt das lojas no automático, que só admin liga:
    quem não é admin não muda o que sai sozinho."""
    dono = await make_user()
    _integ, canal = await _loja(db, dono, modo="auto")
    # Regra de outra plataforma (sem loja no auto): quem tem edit pode.
    r = await client.post(f"{URL}/regras", json={"quando": "a", "faca": "b", "plataforma": "ml"})
    assert r.status_code == 201
    ml = r.json()
    # Geral ou da Shopee: vale para a loja no auto — só admin.
    for corpo in (
        {"quando": "a", "faca": "b"},
        {"quando": "a", "faca": "b", "plataforma": "shopee"},
    ):
        r = await client.post(f"{URL}/regras", json=corpo)
        assert (r.status_code, r.json()["detail"]["code"]) == (403, "so_admin")
    # Inativa não entra no prompt: pode criar; ATIVAR é que é só admin.
    r = await client.post(f"{URL}/regras", json={"quando": "a", "faca": "b", "ativa": False})
    assert r.status_code == 201
    inativa = r.json()
    r = await client.patch(f"{URL}/regras/{inativa['id']}", json={"ativa": True})
    assert (r.status_code, r.json()["detail"]["code"]) == (403, "so_admin")
    # Levar a regra do ML para todas as plataformas também não.
    r = await client.patch(f"{URL}/regras/{ml['id']}", json={"plataforma": None})
    assert (r.status_code, r.json()["detail"]["code"]) == (403, "so_admin")

    admin = await make_user(role=UserRole.ADMIN)
    auth_as(admin)
    r = await client.post(f"{URL}/regras", json={"quando": "a", "faca": "b"})
    assert r.status_code == 201
    geral = r.json()
    # Apagar a regra que vale para o auto: só admin (quem não é não tira trava).
    auth_as(await make_user(permissions={"atendimento": {"view": True, "delete": True}}))
    r = await client.delete(f"{URL}/regras/{geral['id']}")
    assert (r.status_code, r.json()["detail"]["code"]) == (403, "so_admin")
    # Loja sai do auto: volta a ser de quem tem edit.
    canal.modo = "copiloto"
    await db.commit()
    assert (await client.delete(f"{URL}/regras/{geral['id']}")).status_code == 204


async def test_metricas_contam_vencida_e_ignoram_nao_precisa_de_resposta(
    client, db, make_user, pessoa
):
    """`pct_no_prazo` é a métrica dos 80 h da Amazon: turno vencido sem
    resposta DESCONTA; "não precisa de resposta" (o "obrigado") não conta."""
    dono = await make_user()
    integ, canal = await _loja(db, dono)
    # 1 turno respondido no prazo (30 min).
    await _conversa(
        db, integ, canal, "ok", cliente_ha=timedelta(minutes=100), loja_ha=timedelta(minutes=70)
    )
    # 1 turno vencido sem resposta (Shopee: 12 h).
    await _conversa(db, integ, canal, "vencida", cliente_ha=timedelta(hours=13))
    m = (await client.get(f"{URL}/metricas", params={"dias": 7})).json()
    (loja,) = m["lojas"]
    assert (loja["recebidas"], loja["respondidas"], loja["pct_no_prazo"]) == (2, 1, 50.0)

    # "Não precisa de resposta" num turno vencido: sai da conta.
    obrigado = await _conversa(db, integ, canal, "obrigado", cliente_ha=timedelta(hours=14))
    obrigado.sem_resposta_necessaria = True
    await gravar.recalcular_conversa(db, obrigado)
    await db.commit()
    (loja,) = (await client.get(f"{URL}/metricas", params={"dias": 7})).json()["lojas"]
    assert (loja["recebidas"], loja["pct_no_prazo"]) == (2, 50.0)


async def test_metricas_com_mais_de_32767_conversas(client, db, make_user, pessoa):
    """O asyncpg aceita no máximo 32.767 parâmetros: a lista de ids das
    conversas do período (90 dias, todas as lojas) virava 500."""
    await db.execute(
        text(
            "INSERT INTO atendimento_conversas (id, plataforma, canal, externo_id, conta,"
            " situacao, nao_lidas, aguardando_resposta, sem_resposta_necessaria, ia_pausada,"
            " dados, ultima_mensagem_em, created_at, updated_at)"
            " SELECT gen_random_uuid(), 'amazon', 'email', 'x' || g, 'massa', 'aberta', 0,"
            " false, false, false, '{}'::jsonb, now(), now(), now()"
            " FROM generate_series(1, 33000) g"
        )
    )
    await db.commit()
    r = await client.get(f"{URL}/metricas", params={"dias": 90})
    assert r.status_code == 200
