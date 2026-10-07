"""A API da tela "igual ao Duoke" e do primeiro teste em produção, só observando (28/09/2026).

- /resumo: a barra de lojas vem de TODOS os canais (até com zero), com as
  não lidas da PLATAFORMA (o mesmo número do Duoke) e a saúde do canal;
- lista: foto do comprador e o tipo da última mensagem ("[Pedido]"...);
- detalhe: retrato do pedido na plataforma (`dados.pedido_mkt`), cartão do
  anúncio (`dados.produto`), sugestões da IA que não saíram pelo DaVinci com
  a resposta real ao lado (IA × equipe) e `envio.modo_observacao`;
- POST /conversas/{id}/pedido/atualizar: só Shopee/ML, trava de 60 s no
  Redis, conversa travada enquanto a loja responde, falha da loja não é 500;
- avaliação de sugestão pendente/substituída/bloqueada (upsert, uma por
  sugestão) e as notas nas métricas.

O enriquecimento (`services/atendimento/enriquecer.py`) é de outro lote:
entra falso, pelo contrato (`enriquecer_conversa(session, conversa,
integration, cliente, *, forcar)`), no lugar do módulo inteiro.
"""

from __future__ import annotations

import sys
import types
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.db as _db
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
)
from app.redis_client import redis
from app.routers import atendimento as rota
from app.security.cipher import encrypt_json
from app.services.atendimento import clientes, contexto, gravar, validador

URL = "/api/atendimento"
AGORA = datetime.now(UTC)
PODE_TUDO = {"atendimento": {"view": True, "edit": True}}

PLATAFORMA_ENUM = {
    "shopee": IntegrationPlatform.SHOPEE,
    "ml": IntegrationPlatform.ML,
    "tiktok": IntegrationPlatform.TIKTOK,
    "amazon": IntegrationPlatform.AMAZON,
}
CANAL_PADRAO = {"shopee": "chat", "ml": "pergunta", "tiktok": "chat", "amazon": "email"}

RETRATO = {
    "fonte": "shopee",
    "pedido": "250928ABC",
    "status": "COMPLETED",
    "status_texto": "Concluído",
    "total": 766.19,
    "moeda": "BRL",
    "itens": [{"titulo": "Mala de bordo 20", "imagem": "https://cf.shopee/x.jpg",
               "variacao": "Preta", "sku": "MALA20", "quantidade": 1, "preco": 833.0}],
    "logistica": {"transportadora": "Shopee Xpress", "rastreio": "BR123", "status": None},
}
PRODUTO = {"tipo": "produto", "item_id": "MLB1", "titulo": "Mala 20", "preco": 199.9,
           "imagem": "https://http2.mlstatic.com/x-O.jpg", "moeda": "BRL", "link": None}


# ─────────────── falsos e chaves ───────────────


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
        return [] if texto.strip() else ["Resposta vazia."]

    async def contexto_da_conversa(session, conversa):
        return contexto.vazio()

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
def leitura(_chaves):
    """A leitura das lojas ligada: sem ela o "atualizar" nem vai à loja."""
    _chaves.atendimento_leitura_ativa = True
    return _chaves


@pytest.fixture
async def pessoa(make_user, auth_as) -> User:
    u = await make_user(permissions=PODE_TUDO)
    auth_as(u)
    return u


class _Enriquecer:
    """O `enriquecer.py` falso: registra a chamada e grava o retrato em `dados`."""

    def __init__(self) -> None:
        self.chamadas: list[dict] = []
        self.resultado: bool = True
        self.erro: Exception | None = None
        self.retrato: dict = RETRATO

    async def enriquecer_conversa(self, session, conversa, integration, cliente, *, forcar=False):
        self.chamadas.append(
            {"conversa_id": conversa.id, "integration_id": integration.id,
             "cliente": cliente, "forcar": forcar}
        )
        # Mexe na conversa ANTES de falhar: o router tem de desfazer.
        conversa.dados = {**(conversa.dados or {}), "pedido_mkt": self.retrato}
        if conversa.plataforma == "ml":
            conversa.dados = {**conversa.dados, "produto": PRODUTO}
        await session.flush()
        if self.erro is not None:
            raise self.erro
        return self.resultado


@pytest.fixture
def enriquecer(monkeypatch) -> _Enriquecer:
    falso = _Enriquecer()
    modulo = types.ModuleType(rota._ENRIQUECER)
    modulo.enriquecer_conversa = falso.enriquecer_conversa
    monkeypatch.setitem(sys.modules, rota._ENRIQUECER, modulo)

    cliente = object()

    async def cliente_da_integracao(integration):
        return cliente

    monkeypatch.setattr(clientes, "cliente_da_integracao", cliente_da_integracao)
    falso.cliente = cliente
    return falso


# ─────────────── fábrica ───────────────


async def _loja(
    db: AsyncSession,
    dono: User,
    nome: str,
    plataforma: str = "shopee",
    *,
    canais: dict[str, str] | None = None,
    modo: str = "observar",
    **kw,
) -> tuple[Integration, dict[str, AtendimentoCanal]]:
    """Integração + os canais dela ({canal: status})."""
    integ = Integration(
        user_id=dono.id,
        platform=PLATAFORMA_ENUM[plataforma],
        name=nome,
        credentials=encrypt_json({"access_token": "t"}),
        **kw,
    )
    db.add(integ)
    await db.flush()
    feitos: dict[str, AtendimentoCanal] = {}
    for canal, status in (canais or {CANAL_PADRAO[plataforma]: "ok"}).items():
        feitos[canal] = AtendimentoCanal(
            integration_id=integ.id, plataforma=plataforma, canal=canal, modo=modo, status=status
        )
        db.add(feitos[canal])
    await db.commit()
    return integ, feitos


async def _conversa(
    db: AsyncSession,
    integ: Integration | None,
    canal: AtendimentoCanal | None,
    externo_id: str,
    *,
    plataforma: str = "shopee",
    cliente_ha: timedelta | None = timedelta(hours=1),
    **campos,
) -> AtendimentoConversa:
    conversa, _ = await gravar.upsert_conversa(
        db,
        canal=canal,
        integration=integ,
        plataforma=plataforma,
        canal_nome=canal.canal if canal is not None else CANAL_PADRAO[plataforma],
        externo_id=externo_id,
        comprador_nome="Comprador",
        **campos,
    )
    if cliente_ha is not None:
        await gravar.gravar_mensagem(
            db, conversa, externo_id=f"{externo_id}-c", autor="cliente",
            texto=f"pergunta {externo_id}", enviada_em=AGORA - cliente_ha,
        )
    await db.commit()
    return conversa


async def _msg(db, conversa, externo_id, autor, texto, ha: timedelta, **kw) -> AtendimentoMensagem:
    m, _ = await gravar.gravar_mensagem(
        db, conversa, externo_id=externo_id, autor=autor, texto=texto,
        enviada_em=AGORA - ha, **kw,
    )
    await db.commit()
    return m


async def _sugestao(
    db, conversa, *, gatilho: AtendimentoMensagem | None, criada_ha: timedelta, **campos
) -> AtendimentoRascunho:
    r = AtendimentoRascunho(
        conversa_id=conversa.id,
        mensagem_gatilho_id=gatilho.id if gatilho is not None else None,
        texto=campos.pop("texto", "Seu pedido já saiu, segue o rastreio."),
        categoria=campos.pop("categoria", "rastreio"),
        confianca=0.9,
        precisa_humano=False,
        created_at=AGORA - criada_ha,
        **campos,
    )
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return r


# ─────────────── /resumo: a barra de lojas ───────────────


async def test_resumo_barra_com_todas_as_lojas_nao_lidas_e_saude(client, db, make_user, pessoa):
    dono = await make_user()
    kfa, kfa_c = await _loja(db, dono, "kfa")
    await _loja(db, dono, "Inova")  # conectada, sem conversa nenhuma
    dream, dream_c = await _loja(
        db, dono, "DREAM2", "ml", canais={"pergunta": "ok", "pos_venda": "sem_escopo"}
    )
    dream_c["pos_venda"].ultimo_erro = "HTTP 403 forbidden"
    await _loja(db, dono, "ATV", "tiktok", canais={"chat": "sem_escopo"})
    await _loja(db, dono, "kfa", "amazon", canais={"email": "desligado"})
    mini, mini_c = await _loja(db, dono, "Mini", "tiktok", canais={"chat": "erro"})
    mini_c["chat"].ultimo_erro = "timeout na API"
    await db.commit()

    await _conversa(db, kfa, kfa_c["chat"], "k1", nao_lidas=3)
    await _conversa(db, kfa, kfa_c["chat"], "k2", nao_lidas=2, cliente_ha=timedelta(hours=13))
    # Pergunta do ML: prazo de 1 h — 30 min atrás ainda está no prazo.
    await _conversa(
        db, dream, dream_c["pergunta"], "p1", plataforma="ml", nao_lidas=1,
        cliente_ha=timedelta(minutes=30),
    )
    # Amazon cujo e-mail não disse a conta: loja sem canal (status None).
    await _conversa(db, None, None, "<t1@amazon>", plataforma="amazon")

    r = await client.get(f"{URL}/resumo")
    assert r.status_code == 200
    lojas = [
        (lj["plataforma"], lj["conta"], lj["nao_lidas"], lj["aguardando"], lj["vencidas"],
         lj["status_canal"])
        for lj in r.json()["lojas"]
    ]
    assert lojas == [
        ("amazon", None, 0, 1, 0, None),
        ("amazon", "kfa", 0, 0, 0, "desligado"),
        ("ml", "DREAM2", 1, 1, 0, "sem_escopo"),
        ("shopee", "Inova", 0, 0, 0, "ok"),
        ("shopee", "kfa", 5, 2, 1, "ok"),
        ("tiktok", "ATV", 0, 0, 0, "sem_escopo"),
        ("tiktok", "Mini", 0, 0, 0, "erro"),
    ]
    motivo = {(lj["plataforma"], lj["conta"]): lj["status_motivo"] for lj in r.json()["lojas"]}
    assert motivo[("shopee", "kfa")] is None
    # O ML tem dois canais: o motivo diz QUAL não está sendo lido.
    assert motivo[("ml", "DREAM2")].startswith("Pós-venda: Sem permissão")
    assert "HTTP 403" in motivo[("ml", "DREAM2")]
    assert motivo[("tiktok", "ATV")].startswith("Sem permissão")
    assert motivo[("tiktok", "Mini")] == "A última leitura falhou: timeout na API"
    assert motivo[("amazon", "kfa")] == "Leitura desligada para esta loja"

    plataformas = {p["plataforma"]: p for p in r.json()["plataformas"]}
    assert (plataformas["shopee"]["nao_lidas"], plataformas["ml"]["nao_lidas"]) == (5, 1)
    assert plataformas["tiktok"]["nao_lidas"] == 0


# ─────────────── lista: foto e tipo da última mensagem ───────────────


async def test_lista_com_foto_e_tipo_da_ultima_mensagem(client, db, make_user, pessoa):
    dono = await make_user()
    integ, canais = await _loja(db, dono, "kfa")
    canal = canais["chat"]
    foto = "https://cf.shopee.com.br/file/avatar_tn"
    tipos = {}
    for i, tipo in enumerate(("pedido", "produto", "imagem", "video", "texto")):
        c = await _conversa(
            db, integ, canal, f"t{i}", cliente_ha=None,
            comprador_avatar=foto if tipo == "pedido" else None,
        )
        # Primeiro um texto, depois a mensagem do tipo: vale a ÚLTIMA.
        await _msg(db, c, f"t{i}-a", "cliente", "oi", timedelta(hours=10 + i))
        await _msg(
            db, c, f"t{i}-b", "cliente", None if tipo != "texto" else "e aí?",
            timedelta(hours=1 + i), tipo=tipo,
        )
        tipos[str(c.id)] = tipo
    vazia = await _conversa(db, integ, canal, "vazia", cliente_ha=None)
    dm = DmConversa(conta="charlots_br", participante_id="178414", participante_nome="Maria")
    db.add(dm)
    await db.flush()
    db.add(DmMensagem(conversa_id=dm.id, mid="m1", direcao="recebida", texto="tem azul?",
                      ocorrido_em=AGORA - timedelta(minutes=5)))
    await db.commit()

    itens = {i["id"]: i for i in (await client.get(f"{URL}/conversas")).json()["itens"]}
    esperado = {"pedido": "pedido", "produto": "produto", "imagem": "imagem",
                "video": "outro", "texto": "texto"}
    for cid, tipo in tipos.items():
        assert itens[cid]["ultima_mensagem_tipo"] == esperado[tipo], tipo
        assert itens[cid]["comprador_avatar"] == (foto if tipo == "pedido" else None)
    # Sem mensagem nenhuma: sem tipo (a prévia fica vazia).
    assert itens[str(vazia.id)]["ultima_mensagem_tipo"] is None
    ig = itens[f"ig:{dm.id}"]
    assert (ig["comprador_avatar"], ig["ultima_mensagem_tipo"]) == (None, "texto")

    pedido_id = next(cid for cid, t in tipos.items() if t == "pedido")
    d = (await client.get(f"{URL}/conversas/{pedido_id}")).json()
    assert (d["conversa"]["comprador_avatar"], d["conversa"]["ultima_mensagem_tipo"]) == (
        foto,
        "pedido",
    )
    d = (await client.get(f"{URL}/conversas/ig:{dm.id}")).json()
    assert d["envio"]["modo_observacao"] is True
    assert (d["pedido_mkt"], d["produto"], d["sugestoes"]) == (None, None, [])


# ─────────────── detalhe: pedido, produto, sugestões, observação ───────────────


async def test_detalhe_traz_retrato_do_pedido_e_cartao_do_anuncio(
    client, db, make_user, pessoa
):
    dono = await make_user()
    integ, canais = await _loja(db, dono, "kfa")
    ml, ml_c = await _loja(db, dono, "DREAM2", "ml")
    com = await _conversa(
        db, integ, canais["chat"], "c1", pedido_marketplace="250928ABC",
        dados={"pedido_mkt": RETRATO, "to_id": 9},
    )
    pergunta = await _conversa(
        db, ml, ml_c["pergunta"], "q1", plataforma="ml", dados={"produto": PRODUTO}
    )
    sem = await _conversa(db, integ, canais["chat"], "c2")
    torta = await _conversa(db, integ, canais["chat"], "c3", dados={"pedido_mkt": "lixo"})

    d = (await client.get(f"{URL}/conversas/{com.id}")).json()
    assert d["pedido_mkt"] == RETRATO
    assert d["produto"] is None
    d = (await client.get(f"{URL}/conversas/{pergunta.id}")).json()
    assert (d["pedido_mkt"], d["produto"]) == (None, PRODUTO)
    for c in (sem, torta):
        d = (await client.get(f"{URL}/conversas/{c.id}")).json()
        assert (d["pedido_mkt"], d["produto"]) == (None, None)


async def test_sugestoes_ia_x_resposta_real(client, db, make_user, pessoa):
    """O teste em observação: o Duoke responde, a IA "teria respondido" —
    lado a lado, com a nota da pessoa."""
    dono = await make_user()
    integ, canais = await _loja(db, dono, "kfa")
    c = await _conversa(db, integ, canais["chat"], "obs", cliente_ha=None)

    p1 = await _msg(db, c, "p1", "cliente", "cadê meu pedido?", timedelta(hours=5))
    r1 = await _sugestao(db, c, gatilho=p1, criada_ha=timedelta(hours=4, minutes=58))
    # O Duoke respondeu por fora: a sugestão vira `substituido` (gravar).
    real = await _msg(
        db, c, "l1", "loja", "Já saiu! Rastreio BR123.", timedelta(hours=4, minutes=55)
    )
    await db.refresh(r1)
    assert r1.status == "substituido"
    # Bloqueada (a IA não conseguiu texto que possa sair) para a mesma pergunta.
    r_bloq = await _sugestao(
        db, c, gatilho=p1, criada_ha=timedelta(hours=4, minutes=57), status="bloqueado",
        texto=None, validador_erros=["Prometeu prazo em números."],
    )
    # Enviada pelo DaVinci: NÃO é sugestão "que não saiu".
    await _sugestao(db, c, gatilho=p1, criada_ha=timedelta(hours=4, minutes=56), status="enviado")
    # Descartada e sem gatilho (a mensagem sumiu): vale a hora da sugestão.
    r_desc = await _sugestao(
        db, c, gatilho=None, criada_ha=timedelta(hours=3), status="descartado"
    )
    p2 = await _msg(db, c, "p2", "cliente", "e a nota fiscal?", timedelta(hours=1))
    r2 = await _sugestao(db, c, gatilho=p2, criada_ha=timedelta(minutes=58))
    # Resposta nossa que FALHOU não é resposta real (o cliente não recebeu).
    db.add(AtendimentoMensagem(
        conversa_id=c.id, autor="loja", origem="davinci_humano", texto="Segue a NF.",
        status="falhou", enviada_em=AGORA - timedelta(minutes=50),
    ))
    await db.commit()

    r = await client.post(
        f"{URL}/rascunhos/{r1.id}/avaliacao", json={"nota": "erro", "correcao": "Mande o link."}
    )
    assert r.status_code == 200

    d = (await client.get(f"{URL}/conversas/{c.id}")).json()
    sug = d["sugestoes"]
    assert [s["id"] for s in sug] == [str(r1.id), str(r_bloq.id), str(r_desc.id), str(r2.id)]
    s1 = sug[0]
    assert (s1["status"], s1["categoria"], s1["confianca"], s1["precisa_humano"]) == (
        "substituido", "rastreio", 0.9, False,
    )
    assert s1["mensagem_gatilho_id"] == str(p1.id)
    # Quem avaliou é quem está vendo: a nota não é "de outra pessoa".
    assert s1["avaliacao"] == {
        "nota": "erro",
        "correcao": "Mande o link.",
        "de_outra_pessoa": False,
    }
    assert s1["resposta_real"]["mensagem_id"] == str(real.id)
    assert (s1["resposta_real"]["texto"], s1["resposta_real"]["origem"]) == (
        "Já saiu! Rastreio BR123.",
        "externo",
    )
    assert s1["resposta_real"]["enviada_em"] is not None
    assert (sug[1]["texto"], sug[1]["validador_erros"]) == (None, ["Prometeu prazo em números."])
    assert sug[1]["resposta_real"]["mensagem_id"] == str(real.id)
    # Sem gatilho, 3 h atrás: a resposta de 4h55 é ANTERIOR — não é a dela.
    assert (sug[2]["mensagem_gatilho_id"], sug[2]["resposta_real"]) == (None, None)
    s2 = sug[3]
    assert (s2["status"], s2["avaliacao"], s2["resposta_real"]) == ("pendente", None, None)
    # A pendente continua sendo também o `rascunho` (a tela atual usa).
    assert d["rascunho"]["id"] == str(r2.id)
    assert d["envio"]["modo_observacao"] is True


async def test_modo_observacao_canal_ou_envio_desligado(client, db, make_user, pessoa, _chaves):
    dono = await make_user()
    integ, canais = await _loja(db, dono, "kfa")
    c = await _conversa(db, integ, canais["chat"], "m1")
    amazon = await _conversa(db, None, None, "<t@amazon>", plataforma="amazon")

    async def modo(cid) -> bool:
        return (await client.get(f"{URL}/conversas/{cid}")).json()["envio"]["modo_observacao"]

    assert await modo(c.id) is True  # envio desligado (e canal em observar)
    assert await modo(amazon.id) is True
    _chaves.atendimento_envio_ativo = True
    assert await modo(c.id) is True  # canal em observar
    canais["chat"].modo = "copiloto"
    await db.commit()
    assert await modo(c.id) is False
    # Amazon sem conta, envio ligado: não há canal em observar — a tela pede a conta.
    assert await modo(amazon.id) is False
    _chaves.atendimento_envio_ativo = False
    assert await modo(c.id) is True


# ─────────────── avaliação em observação ───────────────


async def test_avaliar_pendente_upsert_e_promove_quando_a_loja_responde(
    client, db, make_user, pessoa
):
    dono = await make_user()
    integ, canais = await _loja(db, dono, "kfa")
    c = await _conversa(db, integ, canais["chat"], "av", cliente_ha=None)
    p1 = await _msg(db, c, "p1", "cliente", "chegou quebrado", timedelta(hours=2))
    r = await _sugestao(db, c, gatilho=p1, criada_ha=timedelta(minutes=110),
                        texto="Sinto muito! Vamos resolver.")
    url = f"{URL}/rascunhos/{r.id}/avaliacao"

    av1 = (await client.post(url, json={"nota": "ok"})).json()["avaliacao"]
    assert (av1["acao"], av1["nota"], av1["texto_final"], av1["similaridade"]) == (
        "observou", "ok", None, None,
    )
    av2 = (await client.post(url, json={"nota": "erro", "correcao": "Peça foto."})).json()[
        "avaliacao"
    ]
    assert av2["id"] == av1["id"]  # uma por sugestão: atualiza
    assert (av2["acao"], av2["nota"], av2["correcao"]) == ("observou", "erro", "Peça foto.")
    await db.refresh(r)
    assert r.status == "pendente"  # avaliar não mexe na sugestão

    # O Duoke respondeu: a sugestão sai da caixa e a avaliação vira a de
    # verdade NA HORA, com a resposta real — sem precisar de nova nota (a
    # tela esconde o 👍 já dado; ninguém reenviaria).
    await _msg(db, c, "l1", "loja", "Sinto muito! Pode mandar uma foto?", timedelta(minutes=100))
    await db.refresh(r)
    assert r.status == "substituido"
    linha = await db.scalar(
        select(AtendimentoAvaliacao).where(AtendimentoAvaliacao.rascunho_id == r.id)
    )
    await db.refresh(linha)
    assert (linha.acao, linha.texto_final, linha.nota, linha.correcao) == (
        "escreveu_do_zero", "Sinto muito! Pode mandar uma foto?", "erro", "Peça foto.",
    )
    assert 0 < linha.similaridade < 1
    av3 = (await client.post(url, json={"nota": "ok"})).json()["avaliacao"]
    assert av3["id"] == av1["id"]
    assert (av3["acao"], av3["texto_final"], av3["nota"], av3["correcao"]) == (
        "escreveu_do_zero", "Sinto muito! Pode mandar uma foto?", "ok", None,
    )
    assert 0 < av3["similaridade"] < 1
    total = await db.scalar(
        select(func.count()).select_from(AtendimentoAvaliacao)
        .where(AtendimentoAvaliacao.rascunho_id == r.id)
    )
    assert total == 1

    # Bloqueada também se avalia (a IA não soube: o 👎 ensina).
    bloq = await _sugestao(db, c, gatilho=p1, criada_ha=timedelta(minutes=105),
                           status="bloqueado", texto=None)
    av = (await client.post(f"{URL}/rascunhos/{bloq.id}/avaliacao", json={"nota": "erro"})).json()
    assert (av["avaliacao"]["acao"], av["avaliacao"]["nota"]) == ("descartou", "erro")

    m = (await client.get(f"{URL}/metricas", params={"dias": 7})).json()["ia"]
    assert (m["nota_ok"], m["nota_erro"]) == (1, 1)


async def test_resposta_pelo_davinci_nao_promove_a_nota_na_gravacao(
    client, db, make_user, pessoa
):
    """A resposta que sai PELO DaVinci é avaliada pelo `enviar` (enviou igual ou
    editou, com o que a pessoa escolheu): a gravação só promove a de FORA."""
    dono = await make_user()
    integ, canais = await _loja(db, dono, "kfa")
    c = await _conversa(db, integ, canais["chat"], "pelo-davinci", cliente_ha=None)
    p1 = await _msg(db, c, "p1", "cliente", "cadê meu pedido?", timedelta(hours=2))
    r = await _sugestao(db, c, gatilho=p1, criada_ha=timedelta(minutes=110))
    url = f"{URL}/rascunhos/{r.id}/avaliacao"
    assert (await client.post(url, json={"nota": "ok"})).status_code == 200

    await _msg(db, c, "l1", "loja", "Já saiu, segue o rastreio.", timedelta(minutes=100),
               origem="davinci_humano")

    await db.refresh(r)
    assert r.status == "substituido"
    linha = await db.scalar(
        select(AtendimentoAvaliacao).where(AtendimentoAvaliacao.rascunho_id == r.id)
    )
    await db.refresh(linha)
    assert (linha.acao, linha.texto_final) == ("observou", None)


async def test_avaliar_duas_ao_mesmo_tempo_vira_uma(client, db, make_user, pessoa, monkeypatch):
    """Dois cliques no 👍: o segundo INSERT bate no UNIQUE e vira atualização."""
    dono = await make_user()
    integ, canais = await _loja(db, dono, "kfa")
    c = await _conversa(db, integ, canais["chat"], "corrida")
    r = await _sugestao(db, c, gatilho=None, criada_ha=timedelta(minutes=5))
    primeira = AtendimentoAvaliacao(rascunho_id=r.id, acao="observou", nota="ok")
    db.add(primeira)
    await db.commit()

    real = rota._avaliacao_de
    cega = {"vez": True}

    async def _cega(session, rascunho_id):
        if cega["vez"]:
            cega["vez"] = False
            return None  # "não achei" — mas o outro clique já gravou
        return await real(session, rascunho_id)

    monkeypatch.setattr(rota, "_avaliacao_de", _cega)
    r2 = await client.post(f"{URL}/rascunhos/{r.id}/avaliacao", json={"nota": "erro"})
    assert r2.status_code == 200
    assert r2.json()["avaliacao"]["id"] == str(primeira.id)
    await db.refresh(primeira)
    assert primeira.nota == "erro"


async def test_nota_refeita_por_outra_pessoa_fica_no_nome_dela(
    client, db, make_user, auth_as, pessoa
):
    """A deu 👍; B muda para 👎 com a correção dela: a linha é de B (antes
    ficava no nome de A, com o texto de B)."""
    dono = await make_user()
    integ, canais = await _loja(db, dono, "kfa")
    c = await _conversa(db, integ, canais["chat"], "autoria")
    r = await _sugestao(db, c, gatilho=None, criada_ha=timedelta(minutes=5))
    url = f"{URL}/rascunhos/{r.id}/avaliacao"
    assert (await client.post(url, json={"nota": "ok"})).status_code == 200

    outra = await make_user(permissions=PODE_TUDO)
    auth_as(outra)
    resp = await client.post(url, json={"nota": "erro", "correcao": "texto de B"})
    assert resp.status_code == 200

    linha = await db.scalar(
        select(AtendimentoAvaliacao).where(AtendimentoAvaliacao.rascunho_id == r.id)
    )
    await db.refresh(linha)
    assert (linha.user_id, linha.nota, linha.correcao) == (outra.id, "erro", "texto de B")


# ─────────────── POST /conversas/{id}/pedido/atualizar ───────────────


async def _limpar(*conversas) -> None:
    for c in conversas:
        await redis.delete(rota._chave_pedido(c.id))


async def test_atualizar_pedido_recusas_limpas(client, db, make_user, auth_as, pessoa, enriquecer):
    dono = await make_user()
    _tt, tt_c = await _loja(db, dono, "ATV", "tiktok")
    tiktok = await _conversa(db, _tt, tt_c["chat"], "tt", plataforma="tiktok")
    amz, amz_c = await _loja(db, dono, "kfa", "amazon")
    amazon = await _conversa(db, amz, amz_c["email"], "<a@b>", plataforma="amazon")
    orfa = await _conversa(db, None, None, "orfa")  # a loja foi desconectada
    velha, velha_c = await _loja(db, dono, "velha", archived_at=AGORA)
    arquivada = await _conversa(db, velha, velha_c["chat"], "arq")

    async def codigo(cid: str) -> tuple[int, str]:
        r = await client.post(f"{URL}/conversas/{cid}/pedido/atualizar")
        return r.status_code, r.json()["detail"]["code"]

    assert await codigo(f"ig:{uuid4()}") == (409, "sem_pedido_na_plataforma")
    assert await codigo(str(tiktok.id)) == (409, "sem_pedido_na_plataforma")
    assert await codigo(str(amazon.id)) == (409, "sem_pedido_na_plataforma")
    assert await codigo(str(orfa.id)) == (409, "sem_integracao")
    assert await codigo(str(arquivada.id)) == (409, "sem_integracao")
    assert await codigo(str(uuid4())) == (404, "conversa_nao_encontrada")
    assert enriquecer.chamadas == []

    auth_as(await make_user(permissions={"atendimento": {"view": True}}))
    r = await client.post(f"{URL}/conversas/{tiktok.id}/pedido/atualizar")
    assert r.status_code == 403


async def test_atualizar_pedido_respeita_a_leitura_desligada(
    client, db, make_user, pessoa, enriquecer, _chaves
):
    """A chave geral (e o canal `desligado`) valem para o botão: sem ir à loja."""
    dono = await make_user()
    integ, canais = await _loja(db, dono, "kfa")
    c = await _conversa(db, integ, canais["chat"], "desl", pedido_marketplace="P1")
    _off, off_c = await _loja(db, dono, "parada", canais={"chat": "desligado"})
    parada = await _conversa(db, _off, off_c["chat"], "parada", pedido_marketplace="P2")
    await _limpar(c, parada)
    try:
        _chaves.atendimento_leitura_ativa = False
        r = await client.post(f"{URL}/conversas/{c.id}/pedido/atualizar")
        assert (r.status_code, r.json()["detail"]["code"]) == (409, "leitura_desligada")

        _chaves.atendimento_leitura_ativa = True
        r = await client.post(f"{URL}/conversas/{parada.id}/pedido/atualizar")
        assert (r.status_code, r.json()["detail"]["code"]) == (409, "canal_desligado")

        assert enriquecer.chamadas == []
        # Recusado antes da trava: nada ficou preso no Redis.
        assert await redis.get(rota._chave_pedido(c.id)) is None
    finally:
        await _limpar(c, parada)


async def test_atualizar_pedido_tem_teto_por_loja_e_por_pessoa(
    client, db, make_user, pessoa, enriquecer, leitura, monkeypatch
):
    """Percorrer a lista clicando não vira rajada na loja: acima do teto do
    minuto, o painel fica com o que tem (`limite`) e a loja não é chamada."""
    monkeypatch.setattr(rota, "LIMITE_PEDIDO_POR_LOJA", 2)
    monkeypatch.setattr(rota, "LIMITE_PEDIDO_POR_PESSOA", 3)
    dono = await make_user()
    integ, canais = await _loja(db, dono, "kfa")
    outra, outra_c = await _loja(db, dono, "marq")
    convs = [
        await _conversa(db, integ, canais["chat"], f"lim{i}", pedido_marketplace=f"P{i}")
        for i in range(3)
    ]
    extra = [
        await _conversa(db, outra, outra_c["chat"], f"out{i}", pedido_marketplace=f"Q{i}")
        for i in range(2)
    ]
    chaves = [
        rota._chave_limite_loja(integ.id),
        rota._chave_limite_loja(outra.id),
        rota._chave_limite_pessoa(pessoa.id),
    ]
    await _limpar(*convs, *extra)
    try:
        motivos = [
            (await client.post(f"{URL}/conversas/{x.id}/pedido/atualizar")).json()["motivo"]
            for x in convs
        ]
        # 2 por loja: a terceira conversa da MESMA loja fica com o que tem.
        assert motivos == [None, None, "limite"]
        assert len(enriquecer.chamadas) == 2
        # Não foi à loja: a trava daquela conversa foi solta.
        assert await redis.get(rota._chave_pedido(convs[2].id)) is None
        # Outra loja ainda cabe — até o teto da PESSOA (3 no minuto).
        motivos = [
            (await client.post(f"{URL}/conversas/{x.id}/pedido/atualizar")).json()["motivo"]
            for x in extra
        ]
        assert motivos == [None, "limite"]
        assert len(enriquecer.chamadas) == 3
        # O contador tem validade (não trava a loja para sempre).
        assert 0 < await redis.ttl(chaves[0]) <= rota.JANELA_LIMITE_PEDIDO_S
    finally:
        await _limpar(*convs, *extra)
        for chave in chaves:
            await redis.delete(chave)


async def test_atualizar_pedido_renova_e_trava_60s(
    client, db, make_user, pessoa, enriquecer, leitura
):
    dono = await make_user()
    integ, canais = await _loja(db, dono, "kfa")
    c = await _conversa(db, integ, canais["chat"], "at", pedido_marketplace="250928ABC",
                        dados={"to_id": 9})
    ml, ml_c = await _loja(db, dono, "DREAM2", "ml")
    q = await _conversa(db, ml, ml_c["pergunta"], "q", plataforma="ml")
    await _limpar(c, q)
    try:
        r = await client.post(f"{URL}/conversas/{c.id}/pedido/atualizar")
        assert r.status_code == 200
        assert r.json() == {
            "pedido_mkt": RETRATO, "produto": None, "atualizado": True, "motivo": None
        }
        (chamada,) = enriquecer.chamadas
        assert chamada == {
            "conversa_id": c.id, "integration_id": integ.id,
            "cliente": enriquecer.cliente, "forcar": True,
        }
        await db.refresh(c)
        # Gravou (commit) e não apagou as outras chaves de `dados`.
        assert c.dados == {"to_id": 9, "pedido_mkt": RETRATO}
        assert await redis.ttl(rota._chave_pedido(c.id)) > 0

        # Clique repetido dentro de 60 s: nem vai à loja.
        r = await client.post(f"{URL}/conversas/{c.id}/pedido/atualizar")
        assert r.json() == {
            "pedido_mkt": RETRATO, "produto": None, "atualizado": False, "motivo": "recente"
        }
        assert len(enriquecer.chamadas) == 1

        # A trava é POR CONVERSA; a pergunta do ML traz o cartão do anúncio.
        r = await client.post(f"{URL}/conversas/{q.id}/pedido/atualizar")
        assert (r.json()["produto"], r.json()["atualizado"]) == (PRODUTO, True)

        # O enriquecimento foi à loja e não renovou: o retrato que havia fica.
        await _limpar(c)
        enriquecer.resultado = False
        r = await client.post(f"{URL}/conversas/{c.id}/pedido/atualizar")
        assert (r.json()["atualizado"], r.json()["motivo"]) == (False, "sem_alteracao")
        assert r.json()["pedido_mkt"] == RETRATO
    finally:
        await _limpar(c, q)


async def test_atualizar_pedido_falha_da_loja_nao_e_500_e_desfaz(
    client, db, make_user, pessoa, enriquecer, monkeypatch, leitura
):
    dono = await make_user()
    integ, canais = await _loja(db, dono, "kfa")
    c = await _conversa(db, integ, canais["chat"], "falha", dados={"pedido_mkt": {"pedido": "V"}})
    await _limpar(c)
    try:
        enriquecer.erro = RuntimeError("shopee fora do ar")
        r = await client.post(f"{URL}/conversas/{c.id}/pedido/atualizar")
        assert r.status_code == 200
        assert r.json() == {
            "pedido_mkt": {"pedido": "V"}, "produto": None, "atualizado": False,
            "motivo": "falhou",
        }
        await db.refresh(c)
        assert c.dados == {"pedido_mkt": {"pedido": "V"}}  # o que o falso mexeu foi desfeito

        # Nem montar o cliente deu (credencial quebrada): mesma resposta.
        await _limpar(c)

        async def quebra(integration):
            raise ValueError("credencial ilegível")

        monkeypatch.setattr(clientes, "cliente_da_integracao", quebra)
        r = await client.post(f"{URL}/conversas/{c.id}/pedido/atualizar")
        assert (r.status_code, r.json()["motivo"]) == (200, "falhou")
    finally:
        await _limpar(c)


async def test_atualizar_pedido_com_o_sync_gravando_e_409_e_solta_a_trava(
    client, db, make_user, pessoa, enriquecer, monkeypatch, leitura
):
    monkeypatch.setattr(gravar, "ESPERA_TRAVA_TELA", "300ms")
    dono = await make_user()
    integ, canais = await _loja(db, dono, "kfa")
    c = await _conversa(db, integ, canais["chat"], "ocupada")
    await _limpar(c)
    try:
        async with _db.SessionLocal() as sync_s:
            x = await sync_s.get(AtendimentoConversa, c.id)
            x.nao_lidas = 4
            await sync_s.flush()  # a rodada do sync segura a linha
            r = await client.post(f"{URL}/conversas/{c.id}/pedido/atualizar")
            assert (r.status_code, r.json()["detail"]["code"]) == (409, "conversa_ocupada")
            await sync_s.rollback()
        assert enriquecer.chamadas == []
        # Não foi à loja: a trava de 60 s foi solta, dá para tentar já.
        assert await redis.get(rota._chave_pedido(c.id)) is None
        r = await client.post(f"{URL}/conversas/{c.id}/pedido/atualizar")
        assert (r.status_code, r.json()["atualizado"]) == (200, True)
    finally:
        await _limpar(c)


async def test_atualizar_pedido_sem_redis_nao_vai_a_loja(
    client, db, make_user, pessoa, enriquecer, monkeypatch, leitura
):
    dono = await make_user()
    integ, canais = await _loja(db, dono, "kfa")
    c = await _conversa(db, integ, canais["chat"], "sem-redis")

    class _RedisFora:
        async def set(self, *a, **kw):
            raise ConnectionError("redis fora")

    monkeypatch.setattr(rota, "redis", _RedisFora())
    r = await client.post(f"{URL}/conversas/{c.id}/pedido/atualizar")
    assert (r.status_code, r.json()["detail"]["code"]) == (503, "trava_indisponivel")
    assert enriquecer.chamadas == []


async def test_atualizar_pedido_com_o_enriquecimento_de_verdade(
    client, db, make_user, pessoa, monkeypatch, leitura
):
    """O router com o `enriquecer.py` real (só a ida à loja é falsa): a trava
    que o router segura e a que o enriquecimento pega são a mesma transação,
    e o retrato chega a `dados` sem apagar as outras chaves."""
    import importlib

    enriquecer_real = importlib.import_module(rota._ENRIQUECER)
    idas: list[tuple[str, str]] = []

    async def retrato_pedido(session, integration, cliente, plataforma, pedido, **kw):
        idas.append((plataforma, pedido))
        return dict(RETRATO)

    async def cliente_da_integracao(integration):
        return object()

    monkeypatch.setattr(enriquecer_real, "retrato_pedido", retrato_pedido)
    monkeypatch.setattr(clientes, "cliente_da_integracao", cliente_da_integracao)
    dono = await make_user()
    integ, canais = await _loja(db, dono, "kfa")
    c = await _conversa(db, integ, canais["chat"], "real", pedido_marketplace="250928ABC",
                        dados={"to_id": 9})
    await _limpar(c)
    try:
        r = await client.post(f"{URL}/conversas/{c.id}/pedido/atualizar")
        assert r.status_code == 200
        assert (r.json()["atualizado"], r.json()["pedido_mkt"]) == (True, RETRATO)
        assert idas == [("shopee", "250928ABC")]
        await db.refresh(c)
        assert c.dados["to_id"] == 9
        assert c.dados["pedido_mkt"] == RETRATO
        d = (await client.get(f"{URL}/conversas/{c.id}")).json()
        assert d["pedido_mkt"] == RETRATO
    finally:
        await _limpar(c)
