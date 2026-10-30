"""As automações do Duoke que faltavam e a prévia "como o cliente receberia" (05/10/2026).

Eduardo, 05/10/2026 (à noite): "por enquanto deixe só pra mostrar que ele
enviaria mesmo corretamente, mas não enviar essas mensagens padrão; quero todas
do Duoke aqui também em todas as plataformas possíveis". Aqui, com banco:

- o PEDIDO NÃO PAGO com cupom ("carrinho"): o índice como fonte (o não pago de
  hora em hora), o cupom pela faixa do valor e o tipo da loja, celular abaixo de
  R$ 1.000 fora, o pago no Bling, um por comprador em 24 h (também no mesmo
  lote), o comparador pelo cartão do Duoke e a diferença combinada
  `visto_de_hora_em_hora` (a linha pulada e o "só Duoke");
- a RESPOSTA DA AVALIAÇÃO (4–5★ e 1–3★): a leitura das avaliações como fonte, a
  conversa do comprador, a resposta de pessoa, o comparador pela resposta
  pública gravada (e o chat do Duoke), o "só Duoke" pela avaliação;
- o "PEDIDO RECEBIDO" do TikTok: o aviso de pedido da TikTok como gatilho, a
  comparação na conversa;
- SÓ SIMULAÇÃO: nada chama a plataforma, nem com a regra em `enviar` e todas as
  chaves ligadas (o motor decide em `simular` com `so_simulacao`, o `enviar.py`
  recusa e o PATCH devolve 409);
- a PRÉVIA da linha do registro (`GET /automacoes/registro/{id}/previa`): as
  partes exatas (o cartão com o nº, o texto com o usuário do comprador, o cupom,
  a figurinha, a resposta pública), o Duoke de verdade ao lado (com as partes
  que ele manda junto), nada gravado, nada do comprador, 404 fora da equipe.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select, text

from app.config import get_settings
from app.deps.team_scope import TeamScope
from app.models import (
    AtendimentoAutomacaoRegistro,
    AtendimentoAvaliacaoLoja,
    AtendimentoMensagem,
    AtendimentoPedidoComprador,
    BlingOrder,
    Store,
)
from app.routers import atendimento as rota
from app.routers import atendimento_automacoes as rota_auto
from app.services.atendimento import (
    automacoes,
    automacoes_comparar,
    automacoes_previa,
    clientes,
    enviar,
)
from app.services.atendimento import automacoes_catalogo as cat
from app.services.atendimento import shopee as shopee_atd
from app.services.atendimento.constantes import ResultadoEnvio
from app.services.marketplaces.shopee import ShopeeClient
from tests.test_atendimento_automacoes_motor import (
    DESDE,
    PODE_TUDO,
    AdaptadorComAutoReply,
    RedisFalso,
    T,
    _agendada,
    _cartao,
    _conversa,
    _dono,
    _linhas,
    _loja,
    _msg,
    _n_mensagens,
    _regra,
    _rodada,
)

SN = "251005NAOPAGO1"
CARRINHO_DUOKE = (
    "Oi! 👋 Notamos que você deixou alguns itens no carrinho e queremos te dar uma ajudinha "
    "para finalizar sua compra 😄\n\nPreparamos cupons exclusivos para você economizar:\n\n"
    "💸 R$20 OFF"
)
AVISO_TIKTOK = "Agradecemos pelo seu pedido! Confirme se o seu endereço de envio está correto."
RECEBIDO_DUOKE = "Oi! Recebemos seu pedido e já estamos preparando pra envio."
SEGREDO = "SEGREDO-DO-COMPRADOR meu cpf 123.456.789-00"


# ── chaves e falsos ───────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _redis(monkeypatch) -> RedisFalso:
    r = RedisFalso()
    monkeypatch.setattr(automacoes, "redis", r)
    return r


@pytest.fixture(autouse=True)
def _chaves(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_leitura_ativa", True)
    monkeypatch.setattr(s, "atendimento_automacoes_ativa", True)
    monkeypatch.setattr(s, "atendimento_automacoes_envio", False)
    monkeypatch.setattr(s, "atendimento_envio_ativo", False)
    monkeypatch.setattr(s, "atendimento_automacoes_shopee_auto_reply", False)
    monkeypatch.setattr(s, "atendimento_automacoes_teto_dia", 400)
    monkeypatch.setattr(s, "atendimento_simulador", False)
    monkeypatch.setattr(s, "shopee_mensagens_comprador", True)
    monkeypatch.setattr(rota, "SO_ADMIN", False)
    return s


@pytest.fixture(autouse=True)
async def _sem_financeiro(db):
    """O escrow do financeiro não está na limpeza do conftest: cada teste limpa o seu."""
    await db.execute(text("DELETE FROM marketplace_order_financials"))
    await db.commit()
    yield
    await db.execute(text("DELETE FROM marketplace_order_financials"))
    await db.commit()


def _proibir(monkeypatch, *, rede: bool) -> list[str]:
    """Tudo o que sai para a plataforma LEVANTA — e anota quem foi chamado.

    `rede=False`: o `httpx` fica (o cliente de teste da API passa por ele); o
    resto do caminho até a plataforma levanta do mesmo jeito.
    """
    chamadas: list[str] = []

    def _boom(nome):
        def f(*_a, **_k):
            chamadas.append(nome)
            raise AssertionError(f"{nome} chamado no modo seco")

        return f

    def _aboom(nome):
        async def f(*_a, **_k):
            chamadas.append(nome)
            raise AssertionError(f"{nome} chamado no modo seco")

        return f

    monkeypatch.setattr(enviar, "adaptador", _boom("adaptador"))
    monkeypatch.setattr(enviar, "enviar_automatica", _aboom("enviar_automatica"))
    monkeypatch.setattr(
        enviar, "enviar_automatica_sem_conversa", _aboom("enviar_automatica_sem_conversa")
    )
    monkeypatch.setattr(enviar, "enviar_resposta", _aboom("enviar_resposta"))
    monkeypatch.setattr(shopee_atd, "enviar_parte", _aboom("shopee.enviar_parte"))
    monkeypatch.setattr(ShopeeClient, "chat_send_message", _aboom("chat_send_message"))
    monkeypatch.setattr(
        ShopeeClient, "chat_send_autoreply_message", _aboom("chat_send_autoreply_message")
    )
    monkeypatch.setattr(clientes, "cliente_da_integracao", _aboom("cliente_da_integracao"))
    if rede:
        monkeypatch.setattr(httpx.AsyncClient, "send", _aboom("httpx"))
    return chamadas


@pytest.fixture
def proibido(monkeypatch) -> list[str]:
    return _proibir(monkeypatch, rede=True)


@pytest.fixture
def sem_plataforma(monkeypatch) -> list[str]:
    """O `proibido` para os testes que chamam a API (o cliente de teste usa o httpx)."""
    return _proibir(monkeypatch, rede=False)


@pytest.fixture
def tudo_ligado(monkeypatch, _chaves) -> AdaptadorComAutoReply:
    """Todas as chaves de envio ligadas E o envio por resposta automática no adaptador."""
    _chaves.atendimento_envio_ativo = True
    _chaves.atendimento_automacoes_envio = True
    _chaves.atendimento_automacoes_shopee_auto_reply = True
    falso = AdaptadorComAutoReply()
    monkeypatch.setattr(enviar, "adaptador", lambda _plataforma: falso)
    assert enviar.auto_reply_no_adaptador("shopee") is True

    async def _cliente(_integ):
        return object()

    monkeypatch.setattr(clientes, "cliente_da_integracao", _cliente)
    return falso


@pytest.fixture
async def pessoa(make_user, auth_as):
    u = await make_user(permissions=PODE_TUDO)
    auth_as(u)
    return u


# ── fábrica ───────────────────────────────────────────────────────────────


async def _indice(
    db,
    integ,
    pedido: str,
    *,
    status: str = "UNPAID",
    total: float | None = 1200.0,
    criado: datetime,
    comprador: str = "901",
) -> AtendimentoPedidoComprador:
    p = AtendimentoPedidoComprador(
        integration_id=integ.id,
        plataforma="shopee",
        comprador_id=comprador,
        pedido=pedido,
        criado_em=criado,
        total=total,
        status=status,
        atualizado_em=T - timedelta(minutes=1),
    )
    db.add(p)
    await db.commit()
    return p


async def _bling_pago(db, integ, pedido: str, *, em: datetime) -> None:
    loja = (await db.execute(select(Store).where(Store.integration_id == integ.id))).scalar_one()
    db.add(
        BlingOrder(
            numero=f"9{uuid4().hex[:6]}",
            numeroloja=pedido,
            situacao="6",
            store_id=loja.id,
            item_index=0,
            data=em,
            created_at=em,
        )
    )
    await db.commit()


async def _avaliacao(
    db,
    integ,
    comentario: str,
    *,
    estrelas: int,
    criado: datetime,
    pedido: str | None = None,
    comprador: str | None = "77",
    nome: str | None = "maria.silva",
    resposta: str | None = None,
    resposta_em: datetime | None = None,
    conferida: datetime | None = None,
) -> AtendimentoAvaliacaoLoja:
    a = AtendimentoAvaliacaoLoja(
        integration_id=integ.id,
        plataforma="shopee",
        comentario_id=comentario,
        pedido=pedido,
        comprador_id=comprador,
        comprador_nome_loja=nome,
        estrelas=estrelas,
        texto=SEGREDO,
        resposta_loja=resposta,
        resposta_em=resposta_em,
        criado_em=criado,
        pode_responder=True,
        dados={"conferida_em": conferida.isoformat()} if conferida else {},
        atualizado_em=criado + timedelta(minutes=20),
    )
    db.add(a)
    await db.commit()
    return a


async def _registros(db) -> list[tuple]:
    return [
        tuple(r)
        for r in (
            await db.execute(
                select(
                    AtendimentoAutomacaoRegistro.id,
                    AtendimentoAutomacaoRegistro.estado,
                    AtendimentoAutomacaoRegistro.conversa_id,
                    AtendimentoAutomacaoRegistro.updated_at,
                ).order_by(AtendimentoAutomacaoRegistro.id)
            )
        ).all()
    ]


# ── O pedido não pago com cupom ("carrinho") ──────────────────────────────


async def test_nao_pago_do_indice_simula_com_o_cupom_da_faixa_e_nada_sai(db, proibido):
    dono = await _dono(db)
    barbosa, _ = await _loja(db, dono, "barbosa")
    inova, _ = await _loja(db, dono, "inova")
    await _regra(db, barbosa, "shopee_nao_pago")
    await _regra(db, inova, "shopee_nao_pago")
    # Visto pelo índice 50 min depois da criação (o :22): devido aos 30 min.
    await _indice(db, barbosa, "A" + SN[1:], criado=T - timedelta(minutes=50), comprador="901")
    await _indice(db, barbosa, "B" + SN[1:], total=950, criado=T - timedelta(minutes=50))
    await _indice(db, barbosa, "C" + SN[1:], total=1600, criado=T - timedelta(minutes=55))
    await _bling_pago(db, barbosa, "C" + SN[1:], em=T - timedelta(minutes=10))
    await _indice(db, barbosa, "D" + SN[1:], status="READY_TO_SHIP", criado=T - timedelta(hours=1))
    # O mesmo comprador, outro não pago no mesmo lote: um por comprador em 24 h.
    await _indice(db, barbosa, "E" + SN[1:], total=2100, criado=T - timedelta(minutes=45))
    await _indice(db, barbosa, "F" + SN[1:], criado=T - timedelta(hours=5), comprador="903")
    await _indice(db, inova, "G" + SN[1:], total=300, criado=T - timedelta(minutes=40))
    await _indice(
        db, barbosa, "H" + SN[1:], total=None, criado=T - timedelta(minutes=40), comprador="904"
    )
    antes = await _n_mensagens(db)

    resumo = await _rodada()
    assert resumo["nao_pagos"] == 6, "o READY_TO_SHIP e o de 5 h atrás ficam fora"
    linhas = {x.pedido[0]: x for x in await _linhas(db, automacao="shopee_nao_pago")}
    assert set(linhas) == {"A", "B", "C", "E", "G", "H"}
    a = linhas["A"]
    assert (a.estado, a.motivo, a.modo) == ("simulado", None, "simular")
    assert a.chave == f"pedido:A{SN[1:]}" and a.alvo == "pedido" and a.comprador_id == "901"
    assert a.evento_em == T - timedelta(minutes=50) and a.devido_em == T - timedelta(minutes=20)
    assert a.decidido_em == T, "decidida quando o DaVinci vê (o atraso fica no registro)"
    assert (linhas["B"].estado, linhas["B"].motivo) == ("pulado", "abaixo_do_minimo")
    assert (linhas["C"].estado, linhas["C"].motivo) == ("pulado", "pedido_pago")
    assert (linhas["E"].estado, linhas["E"].motivo) == ("pulado", "ja_recebeu")
    assert (linhas["G"].estado, linhas["G"].motivo) == ("simulado", None), "mala: R$ 5"
    assert (linhas["H"].estado, linhas["H"].motivo) == ("pulado", "sem_valor")
    assert await _n_mensagens(db) == antes
    assert proibido == []
    # A rodada seguinte não duplica (a chave).
    await _rodada(T + timedelta(minutes=2))
    assert len(await _linhas(db, automacao="shopee_nao_pago")) == 6


async def test_nao_pago_um_por_comprador_em_24_h_pelo_registro(db, proibido):
    """Como o Duoke (14 dias até 05/10/2026): com o carrinho anterior a menos de
    24 h, nenhum (0 de 16); a partir de 27 h, de novo (26 vezes)."""
    dono = await _dono(db)
    integ, _ = await _loja(db, dono, "barbosa")
    regra = await _regra(db, integ, "shopee_nao_pago")
    assert regra.condicoes == {"um_por_comprador_h": 24}
    # O DaVinci já "mandou" (simulou) um carrinho ao 901 há 3 dias e ao 903 há 5 h.
    await _agendada(
        db,
        regra,
        evento=T - timedelta(days=3, minutes=30),
        devido=T - timedelta(days=3),
        pedido="Z" + SN[1:],
        estado="simulado",
        comprador_id="901",
    )
    await _agendada(
        db,
        regra,
        evento=T - timedelta(hours=5, minutes=30),
        devido=T - timedelta(hours=5),
        pedido="Y" + SN[1:],
        estado="simulado",
        comprador_id="903",
    )
    await _indice(db, integ, "A" + SN[1:], criado=T - timedelta(minutes=50), comprador="901")
    await _indice(db, integ, "B" + SN[1:], criado=T - timedelta(minutes=50), comprador="902")
    await _indice(db, integ, "D" + SN[1:], criado=T - timedelta(minutes=50), comprador="903")
    await _rodada()
    linhas = {x.pedido[0]: x for x in await _linhas(db, automacao="shopee_nao_pago")}
    assert (linhas["A"].estado, linhas["A"].motivo) == ("simulado", None), "3 dias: de novo"
    assert (linhas["B"].estado, linhas["B"].motivo) == ("simulado", None)
    assert (linhas["D"].estado, linhas["D"].motivo) == ("pulado", "ja_recebeu"), "5 h"
    # A condição da regra vale sobre o padrão (7 dias: o de 3 dias atrás conta).
    regra.condicoes = {"um_por_comprador_h": 168}
    await db.commit()
    await _indice(db, integ, "C" + SN[1:], criado=T - timedelta(minutes=40), comprador="904")
    await _agendada(
        db,
        regra,
        evento=T - timedelta(days=3, minutes=30),
        devido=T - timedelta(days=3),
        pedido="X" + SN[1:],
        estado="simulado",
        comprador_id="904",
    )
    await _rodada(T + timedelta(minutes=2))
    linhas = {x.pedido[0]: x for x in await _linhas(db, automacao="shopee_nao_pago")}
    assert (linhas["C"].estado, linhas["C"].motivo) == ("pulado", "ja_recebeu")
    assert proibido == []


async def test_nao_pago_compara_pelo_cartao_e_o_visto_tarde_e_combinado(db, proibido):
    dono = await _dono(db)
    integ, canal = await _loja(db, dono, "barbosa")
    await _regra(db, integ, "shopee_nao_pago")
    conversa = await _conversa(db, integ, canal, comprador="901")
    # 1) Não pago ainda: o Duoke mandou aos 30 min (cartão + texto) — bateu.
    await _indice(db, integ, "A" + SN[1:], criado=T - timedelta(minutes=50))
    duoke_em = T - timedelta(minutes=20)
    await _msg(db, conversa, autor="loja", texto=None, em=duoke_em, payload=_cartao("A" + SN[1:]))
    texto = await _msg(
        db, conversa, autor="sistema", texto=CARRINHO_DUOKE, em=duoke_em + timedelta(seconds=1)
    )
    # 2) Pago DEPOIS dos 30 min (o Duoke mandou) e antes de o DaVinci ver: combinada.
    outra = await _conversa(db, integ, canal, comprador="902", nome="joao.x")
    await _indice(db, integ, "C" + SN[1:], criado=T - timedelta(minutes=55), comprador="902")
    await _bling_pago(db, integ, "C" + SN[1:], em=T - timedelta(minutes=10))
    await _msg(
        db,
        outra,
        autor="loja",
        texto=None,
        em=T - timedelta(minutes=25),
        payload=_cartao("C" + SN[1:]),
    )
    await _msg(db, outra, autor="sistema", texto=CARRINHO_DUOKE, em=T - timedelta(minutes=25))
    await _rodada()
    linhas = {x.pedido[0]: x for x in await _linhas(db, automacao="shopee_nao_pago")}
    a = linhas["A"]
    assert (a.estado, a.duoke, a.duoke_mensagem_id) == ("simulado", "mandou", texto.id)
    assert a.duoke_diferenca_s == int((duoke_em + timedelta(seconds=1) - T).total_seconds())
    c = linhas["C"]
    assert (c.estado, c.motivo, c.duoke) == ("pulado", "pedido_pago", "mandou")
    assert c.divergencia == "visto_de_hora_em_hora", "fora da conta: o índice é de hora em hora"
    conta = await automacoes_comparar.estatisticas(
        db, desde=T - timedelta(days=1), ate=T + timedelta(days=1)
    )
    k = conta[("shopee_nao_pago", integ.id)]
    assert (k["bateu_mandou"], k["so_duoke"], k["combinada"]) == (1, 0, 1)
    assert k["atraso_mediana_s"] == 50 * 60, "o atraso real: da criação até a decisão"
    # 3) O "só Duoke" de um pedido que o DaVinci nunca viu não pago (pagou antes do
    #    :22): também a diferença combinada.
    terceira = await _conversa(db, integ, canal, comprador="905", nome="ana.y")
    em3 = T + timedelta(minutes=30)
    await _msg(db, terceira, autor="loja", texto=None, em=em3, payload=_cartao("J" + SN[1:]))
    await _msg(db, terceira, autor="sistema", texto=CARRINHO_DUOKE, em=em3)
    # O índice o viu já pago (na 1ª rodada); o Bling, 10 min depois do carrinho.
    await _indice(
        db,
        integ,
        "J" + SN[1:],
        status="READY_TO_SHIP",
        criado=em3 - timedelta(minutes=30),
        comprador="905",
    )
    await _bling_pago(db, integ, "J" + SN[1:], em=em3 + timedelta(minutes=10))
    await _rodada(T + timedelta(hours=4))
    [so] = await _linhas(db, automacao="shopee_nao_pago", estado="so_duoke")
    assert (so.pedido, so.divergencia) == ("J" + SN[1:], "visto_de_hora_em_hora")
    assert proibido == []


async def test_nao_pago_so_duoke_so_e_combinado_com_o_pedido_criado_logo_antes(db, proibido):
    """O "só Duoke" do carrinho só sai da conta (`visto_de_hora_em_hora`) quando
    o DaVinci NUNCA viu o pedido não pago por causa do índice de hora em hora:
    o pedido foi criado logo antes do carrinho (não um antigo da conversa), o
    DaVinci não tem linha dele, e ele foi pago (Bling) até 2 h depois da criação
    ou está cancelado. Sem cartão (o 2º carrinho da conversa vem sem), o pedido
    é o do comprador no índice criado logo antes."""
    dono = await _dono(db)
    integ, canal = await _loja(db, dono, "barbosa")
    regra = await _regra(db, integ, "shopee_nao_pago")
    em = T - timedelta(hours=4)
    criado = em - timedelta(minutes=30)

    async def _carrinho(comprador, *, cartao=None, pedido_conversa=None):
        c = await _conversa(
            db, integ, canal, comprador=comprador, nome=f"c{comprador}", pedido=pedido_conversa
        )
        if cartao:
            await _msg(
                db,
                c,
                autor="loja",
                texto=None,
                em=em - timedelta(seconds=1),
                payload=_cartao(cartao),
            )
        await _msg(db, c, autor="sistema", texto=CARRINHO_DUOKE, em=em)
        return c

    # A) sem cartão; o pedido da conversa é ANTIGO (de ontem, cancelado): falha de verdade.
    antigo = "A" + SN[1:]
    await _indice(
        db, integ, antigo, status="CANCELLED", criado=T - timedelta(days=1), comprador="911"
    )
    a = await _carrinho("911", pedido_conversa=antigo)
    # B) sem cartão (2º carrinho da conversa); o comprador criou um pedido 30 min
    #    antes, pago 50 min depois da criação: o índice nunca o viu não pago.
    novo = "B" + SN[1:]
    await _indice(
        db, integ, "B0" + SN[2:], status="COMPLETED", criado=T - timedelta(days=2), comprador="912"
    )
    await _indice(db, integ, novo, status="READY_TO_SHIP", criado=criado, comprador="912")
    await _bling_pago(db, integ, novo, em=criado + timedelta(minutes=50))
    b = await _carrinho("912", pedido_conversa="B0" + SN[2:])
    # C) com cartão; ainda não pago no índice e sem linha nossa: falha de verdade.
    await _indice(db, integ, "C" + SN[1:], criado=criado, comprador="913")
    c = await _carrinho("913", cartao="C" + SN[1:])
    # D) com cartão; cancelado (e sem linha nossa): mudou antes de o índice olhar.
    await _indice(db, integ, "D" + SN[1:], status="CANCELLED", criado=criado, comprador="914")
    d = await _carrinho("914", cartao="D" + SN[1:])
    # E) com cartão; pago só 3 h depois da criação: o índice o veria não pago.
    await _indice(db, integ, "E" + SN[1:], status="READY_TO_SHIP", criado=criado, comprador="915")
    await _bling_pago(db, integ, "E" + SN[1:], em=criado + timedelta(hours=3))
    e = await _carrinho("915", cartao="E" + SN[1:])
    # F) com cartão; cancelado, mas o DaVinci TEM linha dele (o viu não pago).
    await _indice(db, integ, "F" + SN[1:], status="CANCELLED", criado=criado, comprador="916")
    await _agendada(
        db,
        regra,
        evento=criado,
        devido=em,
        pedido="F" + SN[1:],
        estado="pulado",
        motivo="ja_recebeu",
        comprador_id="916",
    )
    f = await _carrinho("916", cartao="F" + SN[1:])
    # G) o cartão é de um pedido ANTIGO (de ontem, cancelado): não é deste carrinho.
    await _indice(
        db, integ, "G" + SN[1:], status="CANCELLED", criado=T - timedelta(days=1), comprador="917"
    )
    g = await _carrinho("917", cartao="G" + SN[1:])

    regras = await automacoes.regras_por_chave(db, ativas=False)
    n = await automacoes_comparar._so_duoke(db, agora=T, motor_desde=DESDE, regras=regras)
    await db.commit()
    assert n == 7
    so = {x.conversa_id: x for x in await _linhas(db, estado="so_duoke")}
    assert (so[a.id].pedido, so[a.id].divergencia) == (antigo, None)
    assert (so[b.id].pedido, so[b.id].divergencia) == (novo, "visto_de_hora_em_hora")
    assert (so[c.id].pedido, so[c.id].divergencia) == ("C" + SN[1:], None)
    assert (so[d.id].pedido, so[d.id].divergencia) == ("D" + SN[1:], "visto_de_hora_em_hora")
    assert (so[e.id].pedido, so[e.id].divergencia) == ("E" + SN[1:], None)
    assert (so[f.id].pedido, so[f.id].divergencia) == ("F" + SN[1:], None)
    assert (so[g.id].pedido, so[g.id].divergencia) == ("G" + SN[1:], None)
    conta = await automacoes_comparar.estatisticas(
        db, desde=T - timedelta(days=1), ate=T + timedelta(days=1)
    )
    k = conta[("shopee_nao_pago", integ.id)]
    assert (k["so_duoke"], k["combinada"]) == (5, 2)
    assert proibido == []


async def test_so_simula_nem_com_a_regra_em_enviar_e_tudo_ligado(
    db, tudo_ligado, client, pessoa, _chaves
):
    integ, canal = await _loja(db, pessoa, "barbosa")
    # A regra em ENVIAR (direto no banco: o PATCH recusa) e todas as chaves ligadas.
    await _regra(db, integ, "shopee_nao_pago", modo="enviar", enviar_desde=DESDE)
    await _indice(db, integ, "A" + SN[1:], criado=T - timedelta(minutes=50))
    await _rodada()
    [linha] = await _linhas(db, automacao="shopee_nao_pago")
    assert (linha.estado, linha.modo, linha.motivo) == ("simulado", "simular", "so_simulacao")
    assert tudo_ligado.envios == [] and tudo_ligado.partes == []
    assert tudo_ligado.auto_replies == []
    # O envio recusa a automação que só simula, com qualquer chave.
    # (A resposta da avaliação saiu desta lista em 29/10/2026: ela envia.)
    for codigo, plataforma in (
        ("shopee_nao_pago", "shopee"),
        ("tiktok_pedido_recebido", "tiktok"),
    ):
        with pytest.raises(enviar.EnvioRecusado) as e:
            enviar._travas_gerais_automatica(plataforma, codigo, "chat")
        assert e.value.code == "so_simulacao"
    # A tela: Enviar travado com o porquê; o PATCH devolve 409.
    r = await client.get("/api/atendimento/automacoes", params={"plataforma": "shopee"})
    assert r.status_code == 200, r.text
    aut = next(a for a in r.json()["automacoes"] if a["codigo"] == "shopee_nao_pago")
    assert aut["so_simulacao"] == "cupom_nao_confirmado" and "cupom" in aut["so_simulacao_texto"]
    assert set(aut["placeholders"]) == {"comprador", "valor_cupom"}
    loja = aut["lojas"][0]
    assert loja["pode_enviar"] is False and loja["por_que_nao_enviar"][0] == "so_simulacao"
    # A resposta da avaliação (29/10/2026) vai para Enviar com tudo pronto.
    url = f"/api/atendimento/automacoes/shopee_avaliacao_boa/{integ.id}"
    r = await client.patch(
        url, json={"modo": "enviar", "desliguei_no_duoke": True, "troca_sem_criterio": True}
    )
    assert r.status_code == 200 and r.json()["regra"]["modo"] == "enviar", r.text
    r = await client.patch(url, json={"modo": "simular"})
    assert r.status_code == 200 and r.json()["regra"]["modo"] == "simular"
    # A regra que JÁ está em enviar (direto no banco) não fica nele: qualquer
    # mudança sem tirá-la de lá é recusada; para simular ou desligado, passa.
    url = f"/api/atendimento/automacoes/shopee_nao_pago/{integ.id}"
    for corpo in ({"atraso_min": 31}, {"modo": "enviar", "desliguei_no_duoke": True}):
        r = await client.patch(url, json=corpo)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "so_simulacao", corpo
    regra_enviar = (await automacoes.regras_por_chave(db, ativas=False))[
        ("shopee_nao_pago", integ.id)
    ]
    await db.refresh(regra_enviar)
    assert regra_enviar.modo == "enviar" and regra_enviar.atraso_min == 30, "nada mudou"
    r = await client.patch(url, json={"modo": "simular"})
    assert r.status_code == 200 and r.json()["regra"]["modo"] == "simular"
    # A resposta PÚBLICA só na resposta da avaliação (nunca num texto do chat).
    r = await client.patch(
        f"/api/atendimento/automacoes/shopee_menu/{integ.id}",
        json={"partes": [{"tipo": "resposta_publica", "texto": "Obrigado!"}]},
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "parte_invalida"
    # O texto com o cupom passa no validador (o valor de exemplo).
    r = await client.patch(
        f"/api/atendimento/automacoes/shopee_nao_pago/{integ.id}",
        json={
            "partes": [
                {"tipo": "cartao_pedido"},
                {"tipo": "texto", "texto": "Oi! Cupom de R${valor_cupom} para você."},
            ]
        },
    )
    assert r.status_code == 200, r.text
    r = await client.post(
        "/api/atendimento/automacoes/previa", json={"automacao": "shopee_nao_pago"}
    )
    assert r.status_code == 200 and r.json()["motivos"] == []
    assert "💸 R$20 OFF" in r.json()["partes"][1]["texto"]
    r = await client.post(
        "/api/atendimento/automacoes/previa", json={"automacao": "shopee_avaliacao_ruim"}
    )
    # A 1–3★ (29/10/2026, "Automática, texto padrão"): a pública padrão e a pergunta.
    assert [p["tipo"] for p in r.json()["partes"]] == ["resposta_publica", "texto"]
    assert r.json()["partes"][0]["texto"] == cat.TEXTO_AVALIACAO_RUIM_PUBLICA
    assert r.json()["partes"][1]["texto"].startswith("Oi, maria.silva! Vimos a sua avaliação")


# ── A resposta da avaliação ───────────────────────────────────────────────


async def test_avaliacao_simula_e_compara_com_a_resposta_publica_do_duoke(db, proibido):
    dono = await _dono(db)
    integ, canal = await _loja(db, dono, "atv")
    await _regra(db, integ, "shopee_avaliacao_boa")
    await _regra(db, integ, "shopee_avaliacao_ruim")
    conversa = await _conversa(db, integ, canal, comprador="77", pedido=SN)
    criada = T - timedelta(hours=3)
    chat_em = criada + timedelta(minutes=80)
    boa = await _avaliacao(
        db,
        integ,
        "c1",
        estrelas=5,
        criado=criada,
        pedido=SN,
        resposta="Obrigado pela confiança! 🙏 Volte sempre que precisar!",
        resposta_em=chat_em + timedelta(minutes=59),
    )
    chat = await _msg(
        db,
        conversa,
        autor="sistema",
        texto="Obrigado pela confiança, maria.silva! 🙏 Volte sempre que precisar!",
        em=chat_em,
    )
    ruim = await _avaliacao(
        db, integ, "c2", estrelas=2, criado=criada, pedido="251005OUTRO0001", comprador="78"
    )
    pessoa_resp = await _avaliacao(
        db,
        integ,
        "c3",
        estrelas=4,
        criado=criada,
        pedido="251005OUTRO0002",
        comprador="79",
        resposta="Que bom que gostou, obrigada!",
        resposta_em=criada + timedelta(minutes=30),
    )
    # Sem conversa: o Duoke respondeu só a pública (o chat não está na caixa).
    await _avaliacao(
        db,
        integ,
        "c4",
        estrelas=4,
        criado=criada,
        pedido="251005OUTRO0003",
        comprador="80",
        nome="outro.nome",
        resposta="Obrigado pela confiança! 🙏 Volte sempre que precisar!",
        resposta_em=criada + timedelta(minutes=130),
    )
    # Sem resposta e sem a leitura das avaliações passar da janela: fica conferindo.
    await _avaliacao(
        db, integ, "c6", estrelas=5, criado=criada, pedido="251005OUTRO0004", comprador="81"
    )
    # O Duoke responde esta só 18 h depois (a KFA, 1.079 min em 7 dias).
    tarde = await _avaliacao(
        db, integ, "c8", estrelas=5, criado=criada, pedido="251005OUTRO0005", comprador="82"
    )
    del boa, ruim, pessoa_resp
    resumo = await _rodada()
    assert resumo["avaliacoes"] == 6
    por = {x.chave: x for x in await _linhas(db) if x.chave.startswith("avaliacao:")}
    x1 = por["avaliacao:c1"]
    assert (x1.automacao, x1.estado, x1.alvo) == ("shopee_avaliacao_boa", "simulado", "avaliacao")
    assert x1.conversa_id == conversa.id and x1.devido_em == criada + timedelta(hours=1)
    assert (x1.duoke, x1.duoke_mensagem_id, x1.duoke_em) == ("mandou", chat.id, chat_em)
    x2 = por["avaliacao:c2"]
    # A 1–3★ sem conversa com o comprador (29/10/2026, "Automática, texto padrão"):
    # sairia só a pública (a pergunta no chat não tem por onde ir).
    assert (x2.automacao, x2.estado, x2.motivo, x2.duoke) == (
        "shopee_avaliacao_ruim",
        "simulado",
        None,
        "pendente",
    )
    x3 = por["avaliacao:c3"]
    assert (x3.estado, x3.motivo) == ("pulado", "pessoa_respondeu")
    x4 = por["avaliacao:c4"]
    assert (x4.estado, x4.duoke, x4.duoke_mensagem_id) == ("simulado", "mandou", None)
    assert x4.duoke_em == criada + timedelta(minutes=130) - cat.ATRASO_RESPOSTA_EM_SHOPEE
    assert por["avaliacao:c6"].estado == "simulado"

    async def _conferida(em, *comentarios):
        await db.execute(
            text(
                "UPDATE atendimento_avaliacoes_loja SET dados = CAST(:d AS jsonb) "
                "WHERE comentario_id = ANY(:c)"
            ),
            {"d": json.dumps({"conferida_em": em.isoformat()}), "c": list(comentarios)},
        )
        await db.commit()

    # 8 h depois, relidas e ainda sem resposta: a janela (24 h) não fechou.
    await _conferida(T + timedelta(hours=6), "c2", "c8")
    await _rodada(T + timedelta(hours=6))
    por = {x.chave: x for x in await _linhas(db) if x.chave.startswith("avaliacao:")}
    assert por["avaliacao:c2"].duoke == "pendente"
    assert por["avaliacao:c8"].duoke == "pendente", "com 8 h seria 'só DaVinci'"
    # 18 h depois, o Duoke responde a c8; a janela fecha (24 h) e a leitura passa dela.
    tarde.resposta_loja = cat.TEXTO_AVALIACAO_BOA_PUBLICA
    tarde.resposta_em = criada + timedelta(hours=18) + cat.ATRASO_RESPOSTA_EM_SHOPEE
    await db.commit()
    await _conferida(T + timedelta(hours=22), "c2", "c8")
    await _rodada(T + timedelta(hours=22))
    por = {x.chave: x for x in await _linhas(db) if x.chave.startswith("avaliacao:")}
    assert por["avaliacao:c2"].duoke == "nao_mandou", "só DaVinci"
    assert por["avaliacao:c3"].duoke == "nao_mandou", "a pessoa respondeu: bateu (nenhum dos dois)"
    assert por["avaliacao:c6"].duoke == "pendente", "a leitura das avaliações não passou da janela"
    c8 = por["avaliacao:c8"]
    assert (c8.duoke, c8.duoke_em) == ("mandou", criada + timedelta(hours=18))
    assert proibido == []


async def test_avaliacao_com_a_nota_mudada_nao_responde(db, proibido):
    dono = await _dono(db)
    integ, _ = await _loja(db, dono, "barbosa")
    regra = await _regra(db, integ, "shopee_avaliacao_boa")
    criada = T - timedelta(hours=2)
    await _avaliacao(db, integ, "c7", estrelas=2, criado=criada, pedido=SN)
    # A linha nasceu com 5 estrelas; o comprador mudou para 2 antes da decisão.
    await _agendada(db, regra, evento=criada, devido=T - timedelta(minutes=1), chave="avaliacao:c7")
    await _rodada()
    [linha] = await _linhas(db, automacao="shopee_avaliacao_boa")
    assert (linha.estado, linha.motivo) == ("pulado", "estrelas_mudaram")
    assert proibido == []


async def test_so_duoke_da_avaliacao_sem_linha_nossa(db):
    dono = await _dono(db)
    integ, _ = await _loja(db, dono, "barbosa")
    await _regra(db, integ, "shopee_avaliacao_boa")
    criada = T - timedelta(hours=10)
    await _avaliacao(
        db,
        integ,
        "c9",
        estrelas=5,
        criado=criada,
        pedido=SN,
        resposta="Obrigado pela confiança! 🙏 Volte sempre que precisar!",
        resposta_em=criada + timedelta(hours=2),
    )
    regras = await automacoes.regras_por_chave(db, ativas=False)
    n = await automacoes_comparar._so_duoke_avaliacoes(
        db, agora=T, motor_desde=DESDE, regras=regras
    )
    assert n == 1
    await db.commit()
    [so] = await _linhas(db, estado="so_duoke")
    assert (so.automacao, so.chave, so.pedido) == ("shopee_avaliacao_boa", "duoke:avaliacao:c9", SN)
    assert so.duoke_em == criada + timedelta(hours=2) - cat.ATRASO_RESPOSTA_EM_SHOPEE
    # De novo: nada novo; com a nossa linha da mesma avaliação, nunca.
    assert (
        await automacoes_comparar._so_duoke_avaliacoes(
            db, agora=T, motor_desde=DESDE, regras=regras
        )
        == 0
    )
    await _avaliacao(
        db,
        integ,
        "c10",
        estrelas=5,
        criado=criada,
        resposta="Obrigado pela confiança! 🙏 Volte sempre que precisar!",
        resposta_em=criada + timedelta(hours=2),
    )
    regra = regras[("shopee_avaliacao_boa", integ.id)]
    await _agendada(
        db, regra, evento=criada, devido=criada + timedelta(hours=1), chave="avaliacao:c10"
    )
    assert (
        await automacoes_comparar._so_duoke_avaliacoes(
            db, agora=T, motor_desde=DESDE, regras=regras
        )
        == 0
    )


# ── O "pedido recebido" do TikTok ─────────────────────────────────────────


async def test_tiktok_pedido_recebido_pelo_aviso_da_tiktok_compara_na_conversa(db, proibido):
    dono = await _dono(db)
    mini, canal = await _loja(db, dono, "mini", "tiktok")
    injox, canal_injox = await _loja(db, dono, "injox", "tiktok")
    await _regra(db, mini, "tiktok_pedido_recebido")
    conversa = await _conversa(db, mini, canal, comprador="tt-1", pedido="577000111")
    aviso = await _msg(
        db,
        conversa,
        autor="sistema",
        texto=AVISO_TIKTOK,
        em=T - timedelta(minutes=10),
        payload={"type": "TEXT", "sender": {"role": "ROBOT"}},
    )
    duoke_em = T - timedelta(minutes=5)
    await _msg(
        db,
        conversa,
        autor="loja",
        texto=None,
        em=duoke_em,
        # Como vem da TikTok (e está gravado em produção): o `content` é JSON EM TEXTO.
        payload={
            "type": "ORDER_CARD",
            "sender": {"role": "CUSTOMER_SERVICE"},
            "content": json.dumps({"order_id": "577000111"}),
        },
    )
    recebido = await _msg(
        db,
        conversa,
        autor="loja",
        texto=RECEBIDO_DUOKE,
        em=duoke_em + timedelta(seconds=1),
        payload={"type": "TEXT", "sender": {"role": "CUSTOMER_SERVICE"}},
    )
    # O aviso em espanhol (com o "¡"): também é o gatilho.
    espanhol = await _conversa(db, mini, canal, comprador="tt-3", nome="pepe.x")
    aviso_es = await _msg(
        db,
        espanhol,
        autor="sistema",
        texto="¡Gracias por tu pedido! Confirma que tu dirección de envío es correcta.",
        em=T - timedelta(minutes=12),
    )
    # A loja sem a regra (o Duoke não manda lá): nada.
    outra = await _conversa(db, injox, canal_injox, comprador="tt-2")
    await _msg(db, outra, autor="sistema", texto=AVISO_TIKTOK, em=T - timedelta(minutes=10))
    resumo = await _rodada()
    assert resumo["tiktok_pedidos"] == 2
    linhas = {x.conversa_id: x for x in await _linhas(db, automacao="tiktok_pedido_recebido")}
    assert set(linhas) == {conversa.id, espanhol.id}
    linha = linhas[conversa.id]
    assert linha.chave == f"conversa:{conversa.id}:msg:{aviso.id}" and linha.alvo == "pedido"
    assert linha.devido_em == T - timedelta(minutes=5) and linha.estado == "simulado"
    assert (linha.duoke, linha.duoke_mensagem_id) == ("mandou", recebido.id)
    assert linhas[espanhol.id].chave == f"conversa:{espanhol.id}:msg:{aviso_es.id}"
    # A prévia: o cartão do Duoke com o nº (o `content` em texto) e o nosso com o
    # pedido da conversa — o mesmo.
    previa = await automacoes_previa.montar(db, linha, nome_loja="mini")
    dk = previa["duoke"]["partes"]
    assert [p["tipo"] for p in dk] == ["cartao_pedido", "texto"]
    assert dk[0]["pedido"] == "577000111"
    dv = previa["davinci"]["partes"]
    assert dv[0]["tipo"] == "cartao_pedido" and dv[0]["pedido"] == "577000111"
    assert "conversa" in dv[0]["nota"]
    # O da conversa (o que o DaVinci sabe) vem antes; sem ele, o do cartão do Duoke.
    outro = SimpleNamespace(pedido_marketplace="578000222")
    assert automacoes_previa._cartao_tiktok(dk, outro) == "578000222"
    assert automacoes_previa._cartao_tiktok(dk, None) == "577000111"
    assert automacoes_previa._cartao_tiktok([], None) is None
    assert proibido == []


# ── A prévia "como o cliente receberia" ───────────────────────────────────


async def test_previa_monta_as_partes_exatas_e_o_duoke_ao_lado_sem_gravar(
    db, client, pessoa, sem_plataforma
):
    integ, canal = await _loja(db, pessoa, "barbosa")
    await _regra(db, integ, "shopee_nao_pago")
    await _regra(db, integ, "shopee_duvida_26h")
    await _regra(db, integ, "shopee_entregue")
    conversa = await _conversa(db, integ, canal, comprador="901", nome="maria.silva")
    await _msg(db, conversa, texto=SEGREDO, em=T - timedelta(hours=2))
    # O carrinho: simulado e casado com o Duoke (cartão + texto).
    await _indice(db, integ, SN, total=1650, criado=T - timedelta(minutes=50))
    duoke_em = T - timedelta(minutes=20)
    await _msg(db, conversa, autor="loja", texto=None, em=duoke_em, payload=_cartao(SN))
    await _msg(
        db, conversa, autor="sistema", texto=CARRINHO_DUOKE, em=duoke_em + timedelta(seconds=1)
    )
    await _rodada()
    [carrinho] = await _linhas(db, automacao="shopee_nao_pago")
    assert carrinho.duoke == "mandou"
    antes_reg = await _registros(db)
    antes_msg = await _n_mensagens(db)

    r = await client.get(f"/api/atendimento/automacoes/registro/{carrinho.id}/previa")
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert set(corpo) == {"linha", "automacao", "davinci", "duoke"}
    assert corpo["linha"]["id"] == str(carrinho.id) and corpo["linha"]["loja"]
    assert corpo["automacao"]["so_simulacao"] == "cupom_nao_confirmado"
    dv = corpo["davinci"]
    assert dv["sairia"] is True and dv["de_verdade"] is False and dv["hora_tipo"] == "sairia"
    assert dv["valores"] == {"valor_cupom": "25"}, "R$ 1.650 numa loja de celular"
    assert [p["tipo"] for p in dv["partes"]] == ["cartao_pedido", "texto"]
    assert dv["partes"][0]["pedido"] == SN
    assert "💸 R$25 OFF" in dv["partes"][1]["texto"]
    assert dv["versao_regra"] == dv["versao_da_linha"] == 1
    dk = corpo["duoke"]
    assert dk["comparacao"] == "bateu" and dk["estado"] == "mandou"
    assert [p["tipo"] for p in dk["partes"]] == ["cartao_pedido", "texto"]
    assert dk["partes"][0]["pedido"] == SN and dk["partes"][1]["principal"] is True
    assert "R$20 OFF" in dk["partes"][1]["texto"]
    assert dk["partes"][1]["diferenca_s"] == int(
        (duoke_em + timedelta(seconds=1) - T).total_seconds()
    )
    # Nada gravado, nada saiu, nada do comprador.
    assert await _registros(db) == antes_reg
    assert await _n_mensagens(db) == antes_msg
    assert "SEGREDO" not in r.text and "123.456.789" not in r.text
    assert sem_plataforma == []

    # O "caso ainda esteja em dúvida" (texto + figurinha) e o entregue (o nome dentro).
    r26 = await _regra_e_linha(db, integ, conversa, "shopee_duvida_26h")
    entregue = await _regra_e_linha(db, integ, conversa, "shopee_entregue", pedido=SN)
    figurinha_em = T - timedelta(minutes=3)
    sticker = {
        "source": "openapi",
        "message_type": "sticker",
        "content": {
            "sticker_id": "0007",
            "sticker_package_id": "br_shoppito",
            "image_url": "https://cf.shopee.com.br/x.png",
        },
    }
    texto26 = await _msg(
        db,
        conversa,
        autor="sistema",
        texto="Tudo bem? Caso ainda esteja em dúvida, posso te explicar melhor sobre o produto",
        em=figurinha_em,
    )
    await _msg(
        db,
        conversa,
        autor="loja",
        texto=None,
        em=figurinha_em + timedelta(seconds=1),
        payload=sticker,
    )
    r26.duoke = "mandou"
    r26.duoke_mensagem_id = texto26.id
    r26.duoke_em = figurinha_em
    await db.commit()
    corpo = (await client.get(f"/api/atendimento/automacoes/registro/{r26.id}/previa")).json()
    assert [p["tipo"] for p in corpo["davinci"]["partes"]] == ["texto", "figurinha"]
    assert corpo["davinci"]["partes"][1]["figurinha"] == "0007"
    assert corpo["davinci"]["partes"][1]["imagem_url"] == "https://cf.shopee.com.br/x.png"
    assert [p["tipo"] for p in corpo["duoke"]["partes"]] == ["texto", "figurinha"]
    corpo = (await client.get(f"/api/atendimento/automacoes/registro/{entregue.id}/previa")).json()
    assert corpo["davinci"]["partes"][0] == {
        **corpo["davinci"]["partes"][0],
        "tipo": "cartao_pedido",
        "pedido": SN,
    }
    assert corpo["davinci"]["partes"][1]["texto"].startswith("Oi, maria.silva! Tudo bem?")
    assert corpo["davinci"]["comprador"] == "maria.silva"
    assert corpo["duoke"]["partes"] == [] and corpo["duoke"]["comparacao"] == "pendente"
    assert corpo["duoke"]["janela_de"] and corpo["duoke"]["janela_ate"]

    # A regra mudou depois da decisão: a prévia é a de agora, e diz a versão.
    regra = (await automacoes.regras_por_chave(db))[("shopee_nao_pago", integ.id)]
    regra.versao = 2
    await db.commit()
    corpo = (await client.get(f"/api/atendimento/automacoes/registro/{carrinho.id}/previa")).json()
    assert (corpo["davinci"]["versao_regra"], corpo["davinci"]["versao_da_linha"]) == (2, 1)


async def _regra_e_linha(db, integ, conversa, codigo, *, pedido=None):
    regra = (await automacoes.regras_por_chave(db))[(codigo, integ.id)]
    return await _agendada(
        db,
        regra,
        evento=T - timedelta(hours=1),
        devido=T - timedelta(minutes=5),
        pedido=pedido,
        conversa=conversa,
        estado="simulado",
        decidido_em=T - timedelta(minutes=4),
        modo="simular",
    )


async def test_previa_da_avaliacao_publica_e_chat_e_a_resposta_do_duoke(db, client, pessoa):
    integ, canal = await _loja(db, pessoa, "atv")
    await _regra(db, integ, "shopee_avaliacao_ruim")
    conversa = await _conversa(db, integ, canal, comprador="77", nome="joana.k", pedido=SN)
    criada = T - timedelta(hours=3)
    await _avaliacao(
        db,
        integ,
        "c5",
        estrelas=1,
        criado=criada,
        pedido=SN,
        nome="joana.k",
        resposta=cat.TEXTO_AVALIACAO_RUIM_PUBLICA,
        resposta_em=criada + timedelta(minutes=139),
    )
    await _msg(
        db,
        conversa,
        autor="sistema",
        texto="joana.k " + cat.TEXTO_AVALIACAO_RUIM_PUBLICA,
        em=criada + timedelta(minutes=80),
    )
    await _rodada()
    [linha] = await _linhas(db, automacao="shopee_avaliacao_ruim")
    assert linha.duoke == "mandou"
    corpo = (await client.get(f"/api/atendimento/automacoes/registro/{linha.id}/previa")).json()
    dv = corpo["davinci"]["partes"]
    # 29/10/2026: a pública padrão e a pergunta no chat (a versão da linha, pela chave).
    assert [p["tipo"] for p in dv] == ["resposta_publica", "texto"]
    assert dv[0]["texto"] == cat.TEXTO_AVALIACAO_RUIM_PUBLICA and dv[0]["principal"]
    assert dv[1]["texto"].startswith("Oi, joana.k! ") and "?" in dv[1]["texto"]
    dk = corpo["duoke"]["partes"]
    assert [p["tipo"] for p in dk] == ["resposta_publica", "texto"]
    assert dk[0]["em"].startswith((criada + timedelta(minutes=80)).isoformat()[:16])
    assert "59 min" in dk[0]["nota"]
    assert "SEGREDO" not in json.dumps(corpo), "o texto da avaliação (do comprador) nunca sai"


async def test_previa_404_fora_da_equipe_e_linha_que_nao_existe(db, client, pessoa, monkeypatch):
    integ, canal = await _loja(db, pessoa, "barbosa")
    regra = await _regra(db, integ, "shopee_menu")
    conversa = await _conversa(db, integ, canal)
    linha = await _agendada(db, regra, evento=T, devido=T, conversa=conversa)
    r = await client.get(f"/api/atendimento/automacoes/registro/{uuid4()}/previa")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "registro_nao_encontrado"

    async def _sem_lojas(_session, _user):
        return TeamScope(unrestricted=False)

    monkeypatch.setattr(rota_auto, "escopo_do_atendimento", _sem_lojas)
    r = await client.get(f"/api/atendimento/automacoes/registro/{linha.id}/previa")
    assert r.status_code == 404
    n = await db.scalar(select(func.count()).select_from(AtendimentoMensagem))
    assert n == 0


# ── A avaliação ENVIA (29/10/2026) ────────────────────────────────────────
# Eduardo: "as avaliações ele deveria conseguir mandar a mensagem
# automaticamente: de 5 estrelas manda uma agradecendo e tudo; e um 3 abaixo
# tem que ter ali a questão perguntar e tentar entender melhor o porquê dessa nota".


class AdaptadorComPublica(AdaptadorComAutoReply):
    """O da Shopee com a resposta pública da avaliação (`responder_comentario`)."""

    def __init__(self) -> None:
        super().__init__()
        self.publicas: list[tuple[str, str]] = []
        self.resultado_publica = ResultadoEnvio(ok=True, externo_id="resposta:x")

    async def responder_comentario(self, cliente, comentario, texto, *, conversa_id=None):
        self.publicas.append((comentario, texto))
        return self.resultado_publica


@pytest.fixture
def publica_ligada(monkeypatch, _chaves) -> AdaptadorComPublica:
    """Tudo ligado, a resposta automática e a pública no adaptador."""
    _chaves.atendimento_envio_ativo = True
    _chaves.atendimento_automacoes_envio = True
    _chaves.atendimento_automacoes_shopee_auto_reply = True
    falso = AdaptadorComPublica()
    monkeypatch.setattr(enviar, "adaptador", lambda _plataforma: falso)

    async def _cliente(_integ):
        return object()

    monkeypatch.setattr(clientes, "cliente_da_integracao", _cliente)
    return falso


# Passada a TRANSIÇÃO (a espera dela pelo Duoke e pela avaliação relida tem os
# testes próprios, abaixo).
DEPOIS_DA_TRANSICAO = T - automacoes.TRANSICAO - timedelta(days=1)


async def _cenario_enviar(db, nome_loja="atv", enviar_desde=DEPOIS_DA_TRANSICAO):
    dono = await _dono(db)
    integ, canal = await _loja(db, dono, nome_loja)
    boa = await _regra(db, integ, "shopee_avaliacao_boa", modo="enviar", enviar_desde=enviar_desde)
    ruim = await _regra(
        db, integ, "shopee_avaliacao_ruim", modo="enviar", enviar_desde=enviar_desde
    )
    # A regra de produção ainda tem a semente antiga (os textos do Duoke).
    for regra in (boa, ruim):
        regra.partes = [dict(p) for p in cat.PARTES_SEMENTE_ANTIGA[regra.automacao]]
    await db.commit()
    return integ, canal, boa, ruim


async def test_avaliacao_boa_agradece_e_a_ruim_responde_em_publico_e_pergunta(db, publica_ligada):
    integ, canal, boa, ruim = await _cenario_enviar(db)
    criada = T - timedelta(hours=2)
    c5 = await _conversa(db, integ, canal, comprador="77", pedido=SN)
    c2 = await _conversa(db, integ, canal, comprador="78", nome="joao.z", pedido="251005OUTRO0001")
    a5 = await _avaliacao(db, integ, "101", estrelas=5, criado=criada, pedido=SN)
    await _avaliacao(
        db, integ, "102", estrelas=2, criado=criada, pedido="251005OUTRO0001", comprador="78"
    )
    await _rodada()
    por = {x.chave: x for x in await _linhas(db)}
    x5, x2 = por["avaliacao:101"], por["avaliacao:102"]
    assert (x5.estado, x5.modo, x5.conversa_id) == ("enviado", "enviar", c5.id)
    assert (x2.estado, x2.modo, x2.conversa_id) == ("enviado", "enviar", c2.id)
    # A 4–5★ com uma das versões novas (nunca o texto do Duoke); a 1–3★ com o texto
    # padrão de hoje (29/10/2026, "Automática, texto padrão").
    [(comentario, publica), (comentario2, publica2)] = sorted(publica_ligada.publicas)
    assert comentario == "101" and publica in cat.variantes(cat.TEXTO_AGRADECER_PUBLICA)
    assert comentario2 == "102" and publica2 == cat.TEXTO_AVALIACAO_RUIM_PUBLICA
    # O chat das duas como resposta automática; a da 1–3★ é a pergunta.
    agradece, pergunta = sorted(publica_ligada.auto_replies, key=lambda t: "?" in t)
    assert agradece.startswith("Oi, maria.silva! ") and "?" not in agradece
    assert pergunta.startswith("Oi, joao.z! ") and "?" in pergunta
    assert publica_ligada.envios == [] and publica_ligada.partes == []
    # A avaliação ganhou a resposta, marcada como da automação (a marca em voo saiu).
    await db.refresh(a5)
    assert a5.resposta_loja == publica and a5.dados["automacao"] == "shopee_avaliacao_boa"
    a2 = (
        await db.execute(
            select(AtendimentoAvaliacaoLoja)
            .where(AtendimentoAvaliacaoLoja.comentario_id == "102")
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert a2.resposta_loja == cat.TEXTO_AVALIACAO_RUIM_PUBLICA
    assert a2.dados["automacao"] == "shopee_avaliacao_ruim"
    assert a2.dados["registro_id"] == str(x2.id)
    assert cat.MARCA_PUBLICA_EM_VOO not in a2.dados
    # O comparador não lê a nossa pública como a do Duoke — nem a da 1–3★, com o
    # texto dele: nada de disjuntor, nem "só Duoke".
    await _rodada(T + timedelta(minutes=4))
    await db.refresh(boa)
    await db.refresh(ruim)
    assert (boa.modo, ruim.modo) == ("enviar", "enviar")
    [x5] = await _linhas(db, chave="avaliacao:101")
    assert x5.duoke == "pendente", "nem 'mandou': a pública é nossa"
    [x2] = await _linhas(db, chave="avaliacao:102")
    assert x2.duoke == "pendente"
    assert not await _linhas(db, chave="duoke:avaliacao:102")
    # Nunca duas: a rodada seguinte não manda de novo; a nota que muda (5 → 2)
    # não faz a 1–3★ perguntar a quem já foi agradecido.
    a5.estrelas = 2
    await db.commit()
    linha = await _agendada(
        db,
        (await automacoes.regras_por_chave(db))[("shopee_avaliacao_ruim", integ.id)],
        evento=criada,
        devido=T,
        conversa=c5,
        chave="avaliacao:101",
    )
    # E a 1–3★ que vira 5★ (2 → 5) não ganha o agradecimento por cima da pública.
    a2.estrelas = 5
    await db.commit()
    linha2 = await _agendada(
        db,
        (await automacoes.regras_por_chave(db))[("shopee_avaliacao_boa", integ.id)],
        evento=criada,
        devido=T,
        conversa=c2,
        chave="avaliacao:102",
    )
    await _rodada(T + timedelta(minutes=6))
    await db.refresh(linha)
    await db.refresh(linha2)
    assert (linha.estado, linha.motivo) == ("pulado", "avaliacao_ja_atendida")
    assert (linha2.estado, linha2.motivo) == ("pulado", "avaliacao_ja_atendida")
    assert len(publica_ligada.publicas) == 2 and len(publica_ligada.auto_replies) == 2


async def test_avaliacao_com_pessoa_queixa_duoke_e_ia_no_automatico(db, publica_ligada):
    integ, canal, boa, ruim = await _cenario_enviar(db, "mega")
    dono = await _dono(db)
    criada = T - timedelta(hours=2)
    # 4–5★ com a conversa atribuída a alguém: só a pública (o chat é da pessoa).
    c1 = await _conversa(db, integ, canal, comprador="71", pedido="251005OUTRO0011")
    c1.atribuido_a = dono.id
    # 1–3★ com a IA pausada na conversa: não pergunta, só a pública (29/10/2026).
    c2 = await _conversa(db, integ, canal, comprador="72", pedido="251005OUTRO0012")
    c2.ia_pausada = True
    await db.commit()
    await _avaliacao(
        db, integ, "201", estrelas=5, criado=criada, pedido="251005OUTRO0011", comprador="71"
    )
    await _avaliacao(
        db, integ, "202", estrelas=1, criado=criada, pedido="251005OUTRO0012", comprador="72"
    )
    # 5★ com queixa escrita: fica para pessoa (diferença combinada).
    q = await _avaliacao(db, integ, "203", estrelas=5, criado=criada, comprador="73")
    q.texto = "Gostei, mas veio sem o carregador"
    # 5★ que o Duoke já respondeu depois da troca: não duplica, e o disjuntor.
    await _avaliacao(
        db,
        integ,
        "204",
        estrelas=5,
        criado=criada,
        comprador="74",
        resposta=cat.TEXTO_AVALIACAO_BOA_PUBLICA,
        resposta_em=criada + timedelta(minutes=70),
    )
    await db.commit()
    await _rodada()
    por = {x.chave: x for x in await _linhas(db)}
    assert por["avaliacao:201"].estado == "enviado"
    assert sorted(c for c, _ in publica_ligada.publicas) == ["201", "202"], "só as públicas"
    assert por["avaliacao:202"].estado == "enviado"
    assert dict(publica_ligada.publicas)["202"] == cat.TEXTO_AVALIACAO_RUIM_PUBLICA
    x3 = por["avaliacao:203"]
    assert (x3.estado, x3.motivo, x3.divergencia) == (
        "pulado",
        "queixa_no_comentario",
        "queixa_no_comentario",
    )
    assert (por["avaliacao:204"].estado, por["avaliacao:204"].motivo) == (
        "pulado",
        "duoke_mandou",
    )
    assert publica_ligada.auto_replies == [], "nenhum chat: atribuída, pausada, queixa, Duoke"
    await db.refresh(boa)
    assert (boa.modo, boa.disjuntor_motivo) == ("simular", "duoke_ainda_ligado")


async def test_avaliacao_na_loja_com_a_ia_no_automatico_so_com_a_condicao(db, proibido):
    dono = await _dono(db)
    integ, canal = await _loja(db, dono, "kia")
    canal.modo = "auto"
    await db.commit()
    await _regra(db, integ, "shopee_avaliacao_boa")
    await _regra(db, integ, "shopee_avaliacao_ruim", condicoes={"com_ia_no_automatico": True})
    criada = T - timedelta(hours=2)
    await _conversa(db, integ, canal, comprador="61", pedido="251005OUTRO0021")
    await _conversa(db, integ, canal, comprador="62", pedido="251005OUTRO0022")
    await _avaliacao(
        db, integ, "301", estrelas=5, criado=criada, pedido="251005OUTRO0021", comprador="61"
    )
    await _avaliacao(
        db, integ, "302", estrelas=3, criado=criada, pedido="251005OUTRO0022", comprador="62"
    )
    await _rodada()
    por = {x.chave: x for x in await _linhas(db)}
    # 29/10/2026 (a revisão): sem a condição, a IA no automático segura só o CHAT —
    # a pública sai (a do Duoke de hoje, em Barbosa, Kia e Vita, não some na troca).
    assert (por["avaliacao:301"].estado, por["avaliacao:301"].motivo) == ("simulado", None)
    assert por["avaliacao:302"].estado == "simulado"
    assert proibido == []


async def test_patch_da_avaliacao_a_publica_da_ruim_vale_e_a_semente_mostra_o_padrao(
    db, client, pessoa
):
    """29/10/2026 ("Automática, texto padrão"): o PATCH aceita a pública na 1–3★ (o
    422 `publica_com_pessoa` saiu) e a tela mostra a semente antiga como o padrão
    novo, com a pública padrão e a pergunta."""
    integ, _ = await _loja(db, pessoa, "atv")
    regra = await _regra(db, integ, "shopee_avaliacao_ruim")
    regra.partes = [dict(p) for p in cat.PARTES_SEMENTE_ANTIGA["shopee_avaliacao_ruim"]]
    await db.commit()
    r = await client.get("/api/atendimento/automacoes", params={"plataforma": "shopee"})
    aut = next(a for a in r.json()["automacoes"] if a["codigo"] == "shopee_avaliacao_ruim")
    assert "pública" in aut["nome"]
    partes = aut["lojas"][0]["regra"]["partes"]
    assert [p["tipo"] for p in partes] == ["resposta_publica", "texto"]
    assert partes[0]["texto"] == cat.TEXTO_AVALIACAO_RUIM_PUBLICA
    assert partes[1]["texto"] == cat.TEXTO_ENTENDER_CHAT
    assert aut["lojas"][0]["regra"]["condicoes"] == {"com_ia_no_automatico": False}
    assert aut["so_simulacao"] is None
    url = f"/api/atendimento/automacoes/shopee_avaliacao_ruim/{integ.id}"
    editadas = [
        {"tipo": "resposta_publica", "texto": "Sentimos muito pelo ocorrido."},
        {"tipo": "texto", "texto": "Oi, {comprador}! O que aconteceu?"},
    ]
    r = await client.patch(url, json={"partes": editadas})
    assert r.status_code == 200, r.text
    assert r.json()["regra"]["partes"] == editadas
    await db.refresh(regra)
    assert regra.partes == editadas, "o texto editado fica"


# ── A troca da avaliação (29/10/2026, a revisão) ─────────────────────────


async def test_avaliacao_na_transicao_espera_o_p99_do_duoke_e_a_avaliacao_relida(
    db, publica_ligada
):
    integ, canal, boa, _ = await _cenario_enviar(db, enviar_desde=DESDE)
    criada = T - timedelta(hours=1, minutes=5)
    await _conversa(db, integ, canal, comprador="77", pedido=SN)
    a = await _avaliacao(db, integ, "401", estrelas=5, criado=criada, pedido=SN)
    # O `devido_em` (1 h) passou, mas na transição a decisão espera o Duoke.
    await _rodada()
    [x] = await _linhas(db, chave="avaliacao:401")
    assert x.estado == "agendado" and publica_ligada.publicas == []
    # Passada a espera (5 h depois da avaliação), com a avaliação lida antes dela: espera.
    depois = criada + timedelta(hours=5, minutes=10)
    await _rodada(depois)
    [x] = await _linhas(db, chave="avaliacao:401")
    assert x.estado == "agendado" and publica_ligada.publicas == []
    # Relida depois da espera, sem a resposta do Duoke: sai.
    a.atualizado_em = criada + timedelta(hours=5, minutes=5)
    await db.commit()
    await _rodada(depois + timedelta(minutes=2))
    [x] = await _linhas(db, chave="avaliacao:401")
    assert x.estado == "enviado"
    assert [c for c, _ in publica_ligada.publicas] == ["401"]
    assert len(publica_ligada.auto_replies) == 1
    await db.refresh(boa)
    assert boa.modo == "enviar"


async def test_avaliacao_na_transicao_o_duoke_que_responde_depois_da_hora_dispara_o_disjuntor(
    db, publica_ligada
):
    """O Eduardo marca "desliguei no Duoke", mas a resposta de avaliação dele (outra
    opção do painel) segue ligada: a dele sai 1h20 depois da avaliação; a nossa, com
    a espera 0, saía em 1 h — e o comprador recebia as duas."""
    integ, canal, boa, _ = await _cenario_enviar(db, enviar_desde=DESDE)
    criada = T - timedelta(hours=1, minutes=5)
    conversa = await _conversa(db, integ, canal, comprador="77", pedido=SN)
    a = await _avaliacao(db, integ, "402", estrelas=5, criado=criada, pedido=SN)
    await _rodada()
    assert publica_ligada.publicas == [] and publica_ligada.auto_replies == []
    duoke_em = criada + timedelta(minutes=80)
    a.resposta_loja = cat.TEXTO_AVALIACAO_BOA_PUBLICA
    a.resposta_em = duoke_em + cat.ATRASO_RESPOSTA_EM_SHOPEE
    a.atualizado_em = criada + timedelta(hours=5, minutes=5)
    await db.commit()
    await _msg(
        db,
        conversa,
        autor="sistema",
        texto="Obrigado pela confiança, maria.silva! 🙏 Volte sempre que precisar!",
        em=duoke_em,
    )
    await _rodada(criada + timedelta(hours=5, minutes=10))
    [x] = await _linhas(db, chave="avaliacao:402")
    assert (x.estado, x.motivo) == ("pulado", "duoke_mandou")
    assert publica_ligada.publicas == [] and publica_ligada.auto_replies == []
    await db.refresh(boa)
    assert (boa.modo, boa.disjuntor_motivo) == ("simular", "duoke_ainda_ligado")


# ── A 1–3★ responde em público (29/10/2026, à tarde) ─────────────────────
# Eduardo: "Automática, texto padrão". O texto padrão da 1–3★ é o do Duoke: quem
# mandou se sabe pela marca na avaliação, nunca pelo texto.


async def test_avaliacao_ruim_o_duoke_ainda_ligado_pula_e_dispara_o_disjuntor(db, publica_ligada):
    """A regra em Enviar com o Duoke ainda ligado na loja: a pública padrão SEM a nossa
    marca é dele — a nossa não sai (nem a pública nem a pergunta) e a regra volta para
    Simular, como na 4–5★ (antes, em Enviar, ela era lida como a da equipe)."""
    integ, canal, boa, ruim = await _cenario_enviar(db, "vortan")
    criada = T - timedelta(hours=2)
    await _conversa(db, integ, canal, comprador="81", nome="ana.k", pedido="251005OUTRO0041")
    await _avaliacao(
        db,
        integ,
        "601",
        estrelas=2,
        criado=criada,
        pedido="251005OUTRO0041",
        comprador="81",
        resposta=cat.TEXTO_AVALIACAO_RUIM_PUBLICA,
        resposta_em=criada + timedelta(minutes=50) + cat.ATRASO_RESPOSTA_EM_SHOPEE,
    )
    await _rodada()
    [x] = await _linhas(db, chave="avaliacao:601")
    assert (x.estado, x.motivo) == ("pulado", "duoke_mandou")
    assert publica_ligada.publicas == [] and publica_ligada.auto_replies == []
    await db.refresh(ruim)
    await db.refresh(boa)
    assert (ruim.modo, ruim.disjuntor_motivo) == ("simular", "duoke_ainda_ligado")
    assert boa.modo == "enviar", "só a regra da faixa"


async def test_avaliacao_ruim_pessoa_que_respondeu_antes_segura_so_a_publica(db, publica_ligada):
    """Respondida por PESSOA antes da nossa (outro texto na Shopee, ou o padrão pela
    caixa "Responder em público" do DaVinci): nunca duas públicas — mas a pergunta no
    chat ainda vai (29/10/2026, a revisão: a 1–3★ vira pendência da equipe em 1 h, na
    mesma hora da automática, e a pergunta é o pedido do dono). Não é o Duoke: nada
    de disjuntor."""
    integ, canal, _, ruim = await _cenario_enviar(db, "minas")
    criada = T - timedelta(hours=2)
    await _conversa(db, integ, canal, comprador="82", pedido="251005OUTRO0042")
    await _conversa(db, integ, canal, comprador="83", pedido="251005OUTRO0043")
    await _avaliacao(
        db,
        integ,
        "602",
        estrelas=1,
        criado=criada,
        pedido="251005OUTRO0042",
        comprador="82",
        resposta="Olá! Já te chamamos no chat para resolver.",
        resposta_em=criada + timedelta(minutes=30),
    )
    a3 = await _avaliacao(
        db,
        integ,
        "603",
        estrelas=3,
        criado=criada,
        pedido="251005OUTRO0043",
        comprador="83",
        resposta=cat.TEXTO_AVALIACAO_RUIM_PUBLICA,
        resposta_em=criada + timedelta(minutes=30),
    )
    a3.dados = {**(a3.dados or {}), "respondida_pelo_davinci": True}
    await db.commit()
    await _rodada()
    por = {x.chave: x for x in await _linhas(db)}
    for c in ("602", "603"):
        assert (por[f"avaliacao:{c}"].estado, por[f"avaliacao:{c}"].motivo) == (
            "enviado",
            None,
        ), c
    assert publica_ligada.publicas == [], "nunca duas públicas"
    assert len(publica_ligada.auto_replies) == 2, "a pergunta vai"
    assert all("?" in t for t in publica_ligada.auto_replies)
    await db.refresh(ruim)
    assert (ruim.modo, ruim.disjuntor_motivo) == ("enviar", None)


async def test_avaliacao_ruim_pessoa_que_respondeu_no_modo_seco_e_sem_chat_nao_vai_nada(
    db, publica_ligada
):
    """A pública de PESSOA: em Enviar sem como mandar a pergunta (sem conversa), nada
    vai; no modo seco a linha pula como antes (o Duoke não manda nada depois da pessoa
    — a comparação não fica torta)."""
    integ, _, _, _ = await _cenario_enviar(db, "inova")
    seco, canal_seco = await _loja(db, await _dono(db), "mega")
    await _regra(db, seco, "shopee_avaliacao_ruim")
    await _conversa(db, seco, canal_seco, comprador="86", pedido="251005OUTRO0046")
    criada = T - timedelta(hours=2)
    resposta = {
        "resposta": "Olá! Já te chamamos no chat para resolver.",
        "resposta_em": criada + timedelta(minutes=30),
    }
    await _avaliacao(db, integ, "605", estrelas=1, criado=criada, comprador="85", **resposta)
    await _avaliacao(
        db,
        seco,
        "606",
        estrelas=2,
        criado=criada,
        pedido="251005OUTRO0046",
        comprador="86",
        **resposta,
    )
    await _rodada()
    por = {x.chave: x for x in await _linhas(db)}
    for c in ("605", "606"):
        x = por[f"avaliacao:{c}"]
        assert (x.estado, x.motivo) == ("pulado", "pessoa_respondeu"), c
    assert publica_ligada.publicas == [] and publica_ligada.auto_replies == []


async def test_avaliacao_ruim_espera_a_resposta_de_pessoa_que_esta_saindo(db, publica_ligada):
    """Nunca duas públicas: a resposta de pessoa pela caixa da avaliação saindo agora
    (`enviando`) devolve a linha para a próxima rodada sem chamar a Shopee; saída, a
    nossa não vai."""
    from app.services.atendimento import avaliacoes

    integ, canal, _, _ = await _cenario_enviar(db, "mega")
    criada = T - timedelta(hours=2)
    await _conversa(db, integ, canal, comprador="84", pedido="251005OUTRO0044")
    a = await _avaliacao(
        db, integ, "604", estrelas=2, criado=criada, pedido="251005OUTRO0044", comprador="84"
    )
    conversa_av = await avaliacoes.garantir_conversa(db, a, T)
    await db.commit()
    m = await _msg(db, conversa_av, autor="loja", origem="davinci_humano", texto="Oi.", em=T)
    m.status, m.enviada_em, m.created_at = "enviando", None, datetime.now(UTC)
    await db.commit()
    await _rodada()
    [x] = await _linhas(db, chave="avaliacao:604")
    assert x.estado == "agendado"
    assert publica_ligada.publicas == [] and publica_ligada.auto_replies == []
    # Saiu: a avaliação ganha a resposta da pessoa (como `depois_da_resposta`).
    m.status, m.enviada_em = "enviada", T
    a.resposta_loja, a.resposta_em = "Oi.", T
    a.dados = {**(a.dados or {}), "respondida_pelo_davinci": True}
    await db.commit()
    await _rodada(T + timedelta(minutes=2))
    [x] = await _linhas(db, chave="avaliacao:604")
    # A pública é da pessoa; a pergunta no chat ainda vai (29/10/2026, a revisão).
    assert (x.estado, x.motivo) == ("enviado", None)
    assert publica_ligada.publicas == [] and len(publica_ligada.auto_replies) == 1


class _CaixaDaPessoa(AdaptadorComPublica):
    """O da Shopee com a caixa "Responder em público" da PESSOA (a conversa `avaliacao`)."""

    def __init__(self) -> None:
        super().__init__()
        self.de_pessoa: list[str] = []

    async def responder_avaliacao(self, session, conversa, integration, cliente, texto):
        self.de_pessoa.append(texto)
        return ResultadoEnvio(ok=True, externo_id=f"resposta:{conversa.externo_id}")


async def _caixa_da_pessoa(db, monkeypatch, nome_loja, comentario, comprador, pedido):
    falso = _CaixaDaPessoa()
    monkeypatch.setattr(enviar, "adaptador", lambda _plataforma: falso)
    integ, canal, _, _ = await _cenario_enviar(db, nome_loja)
    canal.modo = "copiloto"  # a pessoa responde pela caixa (a loja fora de observar)
    await db.commit()
    await _conversa(db, integ, canal, comprador=comprador, pedido=pedido)
    a = await _avaliacao(
        db,
        integ,
        comentario,
        estrelas=1,
        criado=T - timedelta(hours=2),
        pedido=pedido,
        comprador=comprador,
    )
    from app.services.atendimento import avaliacoes

    await avaliacoes.garantir_conversa(db, a, T)
    await db.commit()
    return falso, a


async def test_caixa_da_pessoa_rele_a_avaliacao_e_nao_manda_a_segunda(
    db, client, pessoa, publica_ligada, monkeypatch
):
    """29/10/2026 (a revisão): a rota POST /avaliacoes/{id}/responder carrega a
    avaliação no começo do pedido; a automática sai inteira nesse meio (até 5 s da
    trava da conversa). O envio da pessoa relê a avaliação travada: 409
    `ja_respondida`, a pública não vai duas vezes, e o `dados` da nossa fica."""
    from app.services.atendimento import avaliacoes

    falso, a = await _caixa_da_pessoa(db, monkeypatch, "kfa", "905", "95", "251005REV00005")
    original = avaliacoes.conversa_para_responder

    async def _no_meio(session, av, **k):
        await _rodada()
        return await original(session, av, **k)

    monkeypatch.setattr(avaliacoes, "conversa_para_responder", _no_meio)
    r = await client.post(
        f"/api/atendimento/avaliacoes/{a.id}/responder",
        json={"texto": "Oi, sentimos muito, vamos resolver pelo chat."},
    )
    [x] = await _linhas(db, chave="avaliacao:905")
    assert x.estado == "enviado" and falso.publicas == [("905", cat.TEXTO_AVALIACAO_RUIM_PUBLICA)]
    assert r.status_code == 409 and r.json()["detail"]["code"] == "ja_respondida"
    assert falso.de_pessoa == [], "a segunda pública não sai"
    novo = (
        await db.execute(
            select(AtendimentoAvaliacaoLoja)
            .where(AtendimentoAvaliacaoLoja.id == a.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert novo.resposta_loja == cat.TEXTO_AVALIACAO_RUIM_PUBLICA
    assert novo.dados["automacao"] == "shopee_avaliacao_ruim"


async def test_caixa_da_pessoa_e_automacao_ao_mesmo_tempo_se_esperam(
    db, pessoa, publica_ligada, monkeypatch
):
    """A pessoa passou da conferência da caixa (a avaliação travada) e a automática
    roda AO MESMO TEMPO (outra sessão): ela espera a trava, vê a linha em voo da
    pessoa e volta para a próxima rodada (`envio_em_andamento`) — que manda só a
    pergunta no chat. Nunca duas públicas."""
    import app.db as _db

    falso, a = await _caixa_da_pessoa(db, monkeypatch, "minas", "902", "92", "251005REV00002")
    from app.services.atendimento import avaliacoes

    conversa_av = await avaliacoes.conversa_para_responder(db, a)
    await db.commit()
    eu = await db.scalar(text("select pg_backend_pid()"))
    original = enviar._conferir_repetido
    rodada: list[asyncio.Task] = []
    esperou: list[bool] = []

    async def _no_meio(session, conversa, normalizado, **k):
        # Entre a conferência da caixa e a linha em voo da pessoa.
        if not rodada:
            rodada.append(asyncio.create_task(_rodada()))
            for _ in range(100):
                await asyncio.sleep(0.1)
                async with _db.SessionLocal() as outra:
                    presa = await outra.scalar(
                        text(
                            "select count(*) from pg_stat_activity "
                            "where :eu = any(pg_blocking_pids(pid))"
                        ),
                        {"eu": eu},
                    )
                if presa:
                    esperou.append(True)
                    break
        return await original(session, conversa, normalizado, **k)

    monkeypatch.setattr(enviar, "_conferir_repetido", _no_meio)
    m = await enviar.enviar_resposta(
        db, conversa_av, "Oi, sentimos muito, já te chamamos no chat.", user=pessoa
    )
    await rodada[0]
    assert esperou == [True], "a automática esperou a trava da avaliação"
    assert m.status == "enviada" and len(falso.de_pessoa) == 1
    [x] = await _linhas(db, chave="avaliacao:902")
    assert x.estado == "agendado" and falso.publicas == []
    await _rodada(T + timedelta(minutes=2))
    [x] = await _linhas(db, chave="avaliacao:902")
    assert x.estado == "enviado" and falso.publicas == [], "só a pergunta"
    assert len(falso.auto_replies) == 1


async def test_avaliacao_ruim_a_marca_em_voo_diz_que_a_publica_e_nossa(
    db, publica_ligada, monkeypatch
):
    """No ambíguo (timeout) e no banco que falha depois de a Shopee aceitar, a
    resposta que a leitura trouxer — o texto padrão, igual ao do Duoke — é NOSSA pela
    marca em voo: não dispara o disjuntor nem vira "só Duoke"; e a caixa "Responder em
    público" espera até a avaliação ser relida 30 min depois sem resposta."""
    from sqlalchemy.exc import DBAPIError

    from app.services.atendimento import avaliacoes

    integ, canal, _, ruim = await _cenario_enviar(db, "inova")
    criada = T - timedelta(hours=2)
    await _conversa(db, integ, canal, comprador="85", pedido="251005OUTRO0045")
    await _conversa(db, integ, canal, comprador="86", pedido="251005OUTRO0046")
    # 605: a Shopee não respondeu (timeout) — a linha vai para `revisar`, sem a pergunta.
    publica_ligada.resultado_publica = ResultadoEnvio(ok=False, ambiguo=True, erro="timeout")
    a5 = await _avaliacao(
        db, integ, "605", estrelas=1, criado=criada, pedido="251005OUTRO0045", comprador="85"
    )
    await _rodada()
    [x5] = await _linhas(db, chave="avaliacao:605")
    assert x5.estado == "revisar" and publica_ligada.auto_replies == []
    await db.refresh(a5)
    marca = a5.dados[cat.MARCA_PUBLICA_EM_VOO]
    assert (marca["automacao"], marca["registro_id"]) == ("shopee_avaliacao_ruim", str(x5.id))
    assert avaliacoes.publica_em_voo(a5) and cat.publica_da_automacao(a5.dados)
    # A caixa "Responder em público" espera (nunca duas públicas).
    conversa_av = await avaliacoes.garantir_conversa(db, a5, T)
    await db.commit()
    with pytest.raises(enviar.EnvioRecusado) as e:
        await enviar._destino_avaliacao(db, conversa_av, origem="davinci_humano")
    assert e.value.code == enviar.RECUSA_ENVIO_EM_ANDAMENTO
    # Relida 10 min depois, ainda espera; 31 min depois sem resposta: a nossa não saiu.
    em = datetime.fromisoformat(marca["em"])
    original = dict(a5.dados)
    for minutos, espera in ((10, True), (31, False)):
        a5.dados = {**original, "conferida_em": (em + timedelta(minutes=minutos)).isoformat()}
        assert avaliacoes.publica_em_voo(a5) is espera, minutos
    a5.dados = original
    await db.commit()
    # 606: a Shopee aceitou e o banco falhou depois — a linha saiu (com a pergunta).
    publica_ligada.resultado_publica = ResultadoEnvio(ok=True, externo_id="resposta:606")

    async def _banco_caiu(*_a, **_k):
        raise DBAPIError("UPDATE", {}, Exception("conexão caiu"))

    monkeypatch.setattr(avaliacoes, "gravar_resposta_automatica", _banco_caiu)
    a6 = await _avaliacao(
        db, integ, "606", estrelas=3, criado=criada, pedido="251005OUTRO0046", comprador="86"
    )
    await _rodada(T + timedelta(minutes=2))
    [x6] = await _linhas(db, chave="avaliacao:606")
    assert x6.estado == "enviado" and len(publica_ligada.auto_replies) == 1
    await db.refresh(a6)
    assert a6.resposta_loja is None and cat.MARCA_PUBLICA_EM_VOO in a6.dados
    # A leitura traz as duas respostas, com o texto padrão (o do Duoke) e sem
    # `dados.automacao`: pela marca, são nossas.
    for a in (a5, a6):
        a.resposta_loja = cat.TEXTO_AVALIACAO_RUIM_PUBLICA
        a.resposta_em = T + cat.ATRASO_RESPOSTA_EM_SHOPEE
        a.atualizado_em = T + timedelta(minutes=30)
    await db.commit()
    await _rodada(T + timedelta(hours=1))
    await db.refresh(ruim)
    assert (ruim.modo, ruim.disjuntor_motivo) == ("enviar", None)
    for c in ("605", "606"):
        [x] = await _linhas(db, chave=f"avaliacao:{c}")
        assert x.duoke != "mandou", c
        assert not await _linhas(db, chave=f"duoke:avaliacao:{c}"), c
    assert len(publica_ligada.publicas) == 2, "nenhuma pública de novo"


async def test_avaliacao_ruim_a_shopee_que_recusa_tira_a_marca(db, publica_ligada):
    """A Shopee recusou a pública (nada saiu): a marca em voo sai — a caixa
    "Responder em público" fica livre — e a pergunta não vai sozinha."""
    from app.services.atendimento import avaliacoes

    integ, canal, _, ruim = await _cenario_enviar(db, "jlas")
    criada = T - timedelta(hours=2)
    await _conversa(db, integ, canal, comprador="87", pedido="251005OUTRO0047")
    publica_ligada.resultado_publica = ResultadoEnvio(ok=False, erro="shopee error_inner")
    a = await _avaliacao(
        db, integ, "607", estrelas=2, criado=criada, pedido="251005OUTRO0047", comprador="87"
    )
    await _rodada()
    [x] = await _linhas(db, chave="avaliacao:607")
    assert x.estado == "falhou" and publica_ligada.auto_replies == []
    assert [c for c, _ in publica_ligada.publicas] == ["607"]
    a = (
        await db.execute(
            select(AtendimentoAvaliacaoLoja)
            .where(AtendimentoAvaliacaoLoja.id == a.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert cat.MARCA_PUBLICA_EM_VOO not in a.dados and a.resposta_loja is None
    assert not avaliacoes.publica_em_voo(a)
    await db.refresh(ruim)
    assert ruim.modo == "enviar"
