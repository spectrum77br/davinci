# ruff: noqa: S105  (tokens de teste de um cliente falso, nada real)
"""A devolução da Shopee no chat do comprador (04/11/2026).

O dono: "vai ser basicamente igual, só quando um produto tiver devolução mantém
no mesmo chat, caso não, segue igual". O que estes testes seguram:

- o caso com chat do comprador ligado a OUTRO pedido liga no chat, sem segunda
  conversa, com e sem o índice; o chat de afiliado, o de outra loja e o fechado
  não contam; nada da conversa da devolução vai para o chat (linhas, bloqueio,
  a vez e o prazo da plataforma);
- o FOCO: o cartão novo e a página relida não tiram o chat do pedido devolvido;
  duas abertas, a de prazo mais curto; encerrou, volta para o citado; aberta há
  mais de 30 dias não prende; toda passada do cron reaplica;
- o chat sem devolução segue o último pedido, sem consulta extra;
- TikTok e ML como sempre;
- a lista, o /resumo e a Caixa Humano escondem igual; a linha sem chat fica;
- o retrato: pedido quando o foco muda, sem forçar, com teto; emprestado à
  conversa `reclamacao`;
- `para_tela` com os campos do bloco, e o texto do comprador fora.

Sem rede: a Shopee falsa dos testes da devolução (só GET).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoPedidoComprador,
    AtendimentoReclamacao,
    DevolucaoRastreio,
    Integration,
    IntegrationPlatform,
    Logistica,
    User,
)
from app.routers import atendimento as rota
from app.services.atendimento import (
    contexto,
    foco_devolucao,
    gravar,
    humano,
    painel,
    reclamacoes,
)
from app.services.atendimento import reclamacoes_devolucoes as rd
from app.services.atendimento.constantes import (
    CHAVE_PRAZO_PLATAFORMA,
    CHAVE_VEZ_DA_LOJA,
)
from tests.test_atendimento_reclamacoes_devolucoes import (
    SN,
    T0,
    TEXTO_DO_COMPRADOR,
    _conversas,
    _fabrica,
    _ligar,
    _linhas,
    _logistica,
    _loja,
    _mensagens,
    _mudar,
    _Plataforma,
    _rastreio,
)
from tests.test_atendimento_shopee import ShopeeFalso as ShopeeChat
from tests.test_atendimento_shopee import _canal as _canal_shopee
from tests.test_atendimento_shopee import _rodar as _rodar_shopee
from tests.test_atendimento_shopee import msg as msg_shopee

URL = "/api/atendimento"
A = SN  # o pedido devolvido
B = "251101BBBBBBBB"  # a outra compra, a que o chat citou por último
C = "251102CCCCCCCC"
COMPRADOR = "555"
PODE_TUDO = {"atendimento": {"view": True, "edit": True}}


@pytest.fixture
def teto(monkeypatch):
    monkeypatch.setattr(get_settings(), "atendimento_sync_max_conversas", 40)


@pytest.fixture(autouse=True)
def _relogio_do_foco(monkeypatch):
    """A leitura (`upsert_conversa`) mede a janela do foco pelo relógio; aqui, o dos casos."""
    monkeypatch.setattr(foco_devolucao, "_agora", lambda: T0 + timedelta(hours=1))


async def _chat(
    db: AsyncSession,
    integ: Integration | None,
    *,
    pedido: str | None = B,
    comprador: str | None = COMPRADOR,
    externo: str = "chat-1",
    plataforma: str = "shopee",
    dados: dict | None = None,
    situacao: str = "aberta",
) -> AtendimentoConversa:
    c = AtendimentoConversa(
        integration_id=integ.id if integ is not None else None,
        plataforma=plataforma,
        canal="chat",
        externo_id=externo,
        comprador_id=comprador,
        pedido_marketplace=pedido,
        situacao=situacao,
        dados=dados or {},
        ultima_mensagem_em=T0 - timedelta(days=2),
    )
    db.add(c)
    await db.commit()
    return c


async def _no_indice(
    db: AsyncSession, integ: Integration, pedido: str = A, comprador: str = COMPRADOR
) -> None:
    db.add(
        AtendimentoPedidoComprador(
            integration_id=integ.id,
            plataforma="shopee",
            comprador_id=comprador,
            pedido=pedido,
        )
    )
    await db.commit()


async def _linha(
    db: AsyncSession,
    conversa: AtendimentoConversa | None,
    pedido: str = A,
    *,
    externo: str | None = None,
    prazo: datetime | None = None,
    aberta_em: datetime | None = None,
    encerrada_em: datetime | None = None,
    plataforma: str = "shopee",
) -> AtendimentoReclamacao:
    r = AtendimentoReclamacao(
        integration_id=conversa.integration_id if conversa is not None else None,
        conversa_id=conversa.id if conversa is not None else None,
        plataforma=plataforma,
        externo_id=externo or f"RSN-{pedido}",
        tipo="devolucao",
        status="PROCESSING",
        pedido_marketplace=pedido,
        prazo_em=prazo,
        aberta_em=aberta_em or T0 - timedelta(days=1),
        encerrada_em=encerrada_em,
        dados={},
    )
    db.add(r)
    await db.commit()
    return r


async def _ler(db: AsyncSession, conversa: AtendimentoConversa, integ, pedido: str | None):
    """A leitura de sempre: o mesmo caminho dos quatro escritores do pedido do chat."""
    atual, _ = await gravar.upsert_conversa(
        db,
        canal=None,
        integration=integ,
        plataforma=conversa.plataforma,
        canal_nome=conversa.canal,
        externo_id=conversa.externo_id,
        pedido_marketplace=pedido,
    )
    await db.commit()
    await db.refresh(atual)
    return atual


def _espia_o_foco(monkeypatch) -> list[str]:
    chamadas: list[str] = []
    original = foco_devolucao.pedido_em_foco

    async def espia(session, conversa, agora=None):
        chamadas.append(conversa.externo_id)
        return await original(session, conversa, agora)

    monkeypatch.setattr(foco_devolucao, "pedido_em_foco", espia)
    return chamadas


# ─────────────── a decisão, pura ───────────────


@pytest.mark.parametrize(
    ("atual", "citado", "foco", "lido", "saida"),
    [
        # Leitura com foco: o pedido fica; o lido vai para o citado.
        ("A", None, "A", "B", ("A", "B")),
        ("A", "B", "A", "C", ("A", "C")),
        # O lido é o próprio foco: sem chave não nasce; com chave, ela anda.
        ("A", None, "A", "A", ("A", None)),
        ("A", "B", "A", "A", ("A", "A")),
        # Leitura sem foco: o pedido é o lido e a chave sai.
        ("A", "B", None, "C", ("C", None)),
        ("A", None, None, "B", ("B", None)),
        # Cron com foco: guarda o de antes, se ainda vazio.
        ("B", None, "A", None, ("A", "B")),
        ("B", "C", "A", None, ("A", "C")),
        ("A", None, "A", None, ("A", None)),
        (None, None, "A", None, ("A", None)),
        # Cron sem foco: volta para o citado; sem citado, nada.
        ("A", "B", None, None, ("B", None)),
        ("A", None, None, None, ("A", None)),
        (None, None, None, None, (None, None)),
    ],
)
def test_decidir_pedido(atual, citado, foco, lido, saida):
    assert foco_devolucao.decidir_pedido(atual, citado, foco, lido) == saida


@pytest.mark.parametrize(
    ("plataforma", "canal", "dados", "vale"),
    [
        ("shopee", "chat", {}, True),
        ("shopee", "chat", None, True),
        ("shopee", "chat", {"afiliado": True}, False),
        ("shopee", "reclamacao", {}, False),
        ("shopee", "email", {}, False),
        ("tiktok", "chat", {}, False),
        ("ml", "chat", {}, False),
        ("magalu", "chat", {}, False),
        ("amazon", "email", {}, False),
    ],
)
def test_vale_para(plataforma, canal, dados, vale):
    conversa = SimpleNamespace(plataforma=plataforma, canal=canal, dados=dados)
    assert foco_devolucao.vale_para(conversa) is vale


# ─────────────── o vínculo pelo comprador ───────────────


async def test_chat_em_outro_pedido_pelo_indice_sem_segunda_conversa(db, make_user):
    integ = await _loja(db, make_user)
    chat = await _chat(db, integ)
    chat_id = chat.id
    await _no_indice(db, integ)
    await _logistica(db)
    prazo = T0 + timedelta(days=2)
    await _rastreio(db, prazo=prazo)
    falsa = _Plataforma()

    resumo = await _ligar(db, fabrica_cliente=_fabrica(falsa))

    assert (resumo["novas"], resumo["conversas_novas"], resumo["erros"]) == (1, 0, 0)
    # Nenhuma conversa `reclamacao`; o índice já diz o comprador: nada de API dele.
    assert await _conversas(db, canal="reclamacao") == []
    assert falsa.pedidos_lidos == []
    [linha] = await _linhas(db)
    assert linha.conversa_id == chat_id
    chat = await db.get(AtendimentoConversa, chat_id)
    # O foco: o chat no pedido devolvido, com a citação guardada.
    assert chat.pedido_marketplace == A
    assert chat.dados["pedido_citado"] == B
    # O cartão aparece nele e a etiqueta muda na mesma rodada.
    assert [r.id for r in await reclamacoes.reclamacoes_da_conversa(db, chat)] == [linha.id]
    assert chat.etiqueta == "devolucao"
    # Nada da conversa da devolução vai para o chat.
    assert await _mensagens(db, chat_id) == []
    assert chat.situacao == "aberta" and chat.bloqueio_motivo is None
    assert CHAVE_VEZ_DA_LOJA not in chat.dados and CHAVE_PRAZO_PLATAFORMA not in chat.dados
    assert chat.aguardando_resposta is False and chat.prazo_resposta_em is None


async def test_chat_em_outro_pedido_sem_indice_pelo_comprador_lido_na_shopee(db, make_user):
    integ = await _loja(db, make_user)
    chat = await _chat(db, integ, comprador="998877")  # o `buyer_user_id` da Shopee falsa
    chat_id = chat.id
    await _logistica(db)
    await _rastreio(db)
    falsa = _Plataforma()

    resumo = await _ligar(db, fabrica_cliente=_fabrica(falsa))

    assert (resumo["conversas_novas"], resumo["erros"]) == (0, 0)
    assert falsa.pedidos_lidos == [A]
    [linha] = await _linhas(db)
    assert linha.conversa_id == chat_id
    assert await _conversas(db, canal="reclamacao") == []

    # A rodada seguinte não tem a leitura: o vínculo fica, sem conversa nova e
    # sem perguntar o comprador de novo (nem depois do tempo de tentar de novo).
    for depois in (timedelta(minutes=10), timedelta(hours=2), timedelta(hours=4)):
        resumo = await _ligar(db, T0 + depois, fabrica_cliente=_fabrica(falsa))
        assert resumo["conversas_novas"] == 0
    assert falsa.pedidos_lidos == [A]
    [linha] = await _linhas(db)
    assert linha.conversa_id == chat_id
    assert await _conversas(db, canal="reclamacao") == []


async def test_chat_em_outro_pedido_sem_a_api_nem_o_indice_ganha_a_conversa(db, make_user):
    """Sem saber o comprador (sem índice, sem API), a conversa nasce como antes, e o
    chat não é tocado: o vínculo só vem com o comprador conhecido."""
    integ = await _loja(db, make_user)
    chat = await _chat(db, integ)
    await _logistica(db)
    await _rastreio(db)
    resumo = await _ligar(db)
    assert resumo["conversas_novas"] == 1
    await db.refresh(chat)
    assert chat.pedido_marketplace == B and "pedido_citado" not in chat.dados


async def test_conversa_antiga_comprador_dela_acha_o_chat_e_ela_segue_com_as_linhas(db, make_user):
    """A conversa `reclamacao` de antes: o comprador dela acha o chat; a linha passa
    para o chat, e ela continua recebendo a história (a aba Reclamação)."""
    integ = await _loja(db, make_user)
    await _logistica(db)
    await _rastreio(db)
    falsa = _Plataforma()
    await _ligar(db, fabrica_cliente=_fabrica(falsa))
    [antiga] = await _conversas(db, canal="reclamacao")
    antiga_id = antiga.id
    assert antiga.comprador_id == "998877"
    linhas_antes = len(await _mensagens(db, antiga_id))
    leituras = len(falsa.pedidos_lidos)

    await db.refresh(integ)
    chat = await _chat(db, integ, comprador="998877")
    chat_id = chat.id
    resumo = await _ligar(db, T0 + timedelta(minutes=10), fabrica_cliente=_fabrica(falsa))
    assert resumo["conversas_novas"] == 0
    [linha] = await _linhas(db)
    assert linha.conversa_id == chat_id
    assert len(falsa.pedidos_lidos) == leituras
    assert [c.id for c in await _conversas(db, canal="reclamacao")] == [antiga_id]
    await db.refresh(chat)
    assert (chat.pedido_marketplace, chat.dados["pedido_citado"]) == (A, B)

    # Status novo: a história entra na conversa antiga, nunca no chat.
    await _mudar(db, A, "PROCESSING", T0 + timedelta(hours=1))
    await _ligar(db, T0 + timedelta(hours=1, minutes=5), fabrica_cliente=_fabrica(falsa))
    textos = [m.texto for m in await _mensagens(db, antiga_id)]
    assert len(textos) == linhas_antes + 1 and textos[-1] == "Devolução na Shopee: Em andamento."
    assert await _mensagens(db, chat_id) == []


async def test_afiliado_outra_loja_e_chat_fechado_nao_contam(db, make_user):
    """O índice e a reserva nunca escolhem esses: a conversa da devolução nasce."""
    integ = await _loja(db, make_user)
    outra = await _loja(db, make_user, nome="poofy")
    afiliado = await _chat(db, integ, externo="af", dados={"afiliado": True})
    fechado = await _chat(db, integ, externo="fe", situacao="fechada")
    de_outra_loja = await _chat(db, outra, externo="ot")
    await _no_indice(db, integ)
    await _logistica(db)
    await _rastreio(db)

    resumo = await _ligar(db, fabrica_cliente=_fabrica(_Plataforma()))

    assert resumo["conversas_novas"] == 1
    [conversa] = await _conversas(db, canal="reclamacao")
    [linha] = await _linhas(db)
    assert linha.conversa_id == conversa.id
    for chat in (afiliado, fechado, de_outra_loja):
        await db.refresh(chat)
        assert chat.pedido_marketplace == B and "pedido_citado" not in chat.dados


async def test_chat_mais_recente_do_comprador_que_serve_e_o_escolhido(db, make_user):
    """O afiliado (o mais recente) vem primeiro na busca pelo comprador: nunca é ele."""
    integ = await _loja(db, make_user)
    afiliado = await _chat(db, integ, externo="af", dados={"afiliado": True})
    afiliado.ultima_mensagem_em = T0
    chat = await _chat(db, integ, externo="co")
    chat_id = chat.id
    await db.commit()
    await _no_indice(db, integ)
    await _logistica(db)
    await _rastreio(db)
    await _ligar(db, fabrica_cliente=_fabrica(_Plataforma()))
    [linha] = await _linhas(db)
    assert linha.conversa_id == chat_id


async def test_chat_que_fechou_a_devolucao_ganha_a_conversa_e_volta_quando_reabre(db, make_user):
    """Fechado sai da frente: a devolução não pode ficar invisível."""
    integ = await _loja(db, make_user)
    chat = await _chat(db, integ, situacao="fechada")
    chat_id = chat.id
    await _no_indice(db, integ)
    await _logistica(db)
    await _rastreio(db)
    falsa = _Plataforma()
    await _ligar(db, fabrica_cliente=_fabrica(falsa))
    [conversa] = await _conversas(db, canal="reclamacao")
    conversa_id = conversa.id
    [linha] = await _linhas(db)
    assert linha.conversa_id == conversa_id

    chat = await db.get(AtendimentoConversa, chat_id)
    chat.situacao = "aberta"  # o comprador escreveu de novo
    await db.commit()
    await _ligar(db, T0 + timedelta(minutes=10), fabrica_cliente=_fabrica(falsa))
    [linha] = await _linhas(db)
    assert linha.conversa_id == chat_id
    assert [c.id for c in await _conversas(db, canal="reclamacao")] == [conversa_id]


async def test_chat_que_foi_para_outro_pedido_nao_perde_o_cartao(db, make_user):
    """O chat no pedido do caso (sem índice) e o caso aberto há mais de 30 dias: ele
    segue o último citado, o cartão fica nele e nenhuma conversa nasce."""
    integ = await _loja(db, make_user)
    chat = await _chat(db, integ, pedido=A)
    chat_id = chat.id
    await _logistica(db)
    await _rastreio(db)
    await _ligar(db)
    [linha] = await _linhas(db)
    assert linha.conversa_id == chat_id

    linha.aberta_em = T0 - timedelta(days=45)
    await db.commit()
    await db.refresh(integ)
    await db.refresh(chat)
    await _ler(db, chat, integ, B)
    resumo = await _ligar(db, T0 + timedelta(minutes=10))
    assert resumo["conversas_novas"] == 0
    assert await _conversas(db, canal="reclamacao") == []
    [linha] = await _linhas(db)
    assert linha.conversa_id == chat_id
    chat = await db.get(AtendimentoConversa, chat_id)
    await db.refresh(chat)
    assert chat.pedido_marketplace == B and "pedido_citado" not in chat.dados
    assert [r.id for r in await reclamacoes.reclamacoes_da_conversa(db, chat)] == [linha.id]
    assert chat.etiqueta == "devolucao"


async def test_caso_encerrado_nunca_ligado_nao_vai_para_o_chat(db, make_user):
    """Sem devolução aberta, nada muda: o caso velho não aparece num chat em que nunca esteve."""
    integ = await _loja(db, make_user)
    chat = await _chat(db, integ)
    await _no_indice(db, integ)
    await _logistica(db, status="CANCELLED", mudou=T0 - timedelta(days=2))
    resumo = await _ligar(db)
    assert (resumo["novas"], resumo["conversas_novas"]) == (1, 0)
    [linha] = await _linhas(db)
    assert linha.conversa_id is None
    await db.refresh(chat)
    assert chat.pedido_marketplace == B and "pedido_citado" not in chat.dados
    assert await reclamacoes.reclamacoes_da_conversa(db, chat) == []


# ─────────────── o foco ───────────────


async def test_cartao_novo_e_pagina_relida_nao_tiram_o_foco(db, make_user, monkeypatch, teto):
    """Pelo adaptador de verdade: a mensagem com o cartão de outra compra e a página
    relida (que reaplica a citação) não tiram o chat do pedido devolvido."""
    monkeypatch.setattr(foco_devolucao, "_agora", lambda: datetime.now(UTC))
    integ, canal = await _canal_shopee(db, await make_user())
    agora = datetime.now(UTC).replace(microsecond=0)
    falso = ShopeeChat()
    falso.conversa(
        "T", msg_shopee("T", agora - timedelta(hours=3), tipo="order", content={"order_sn": A})
    )
    await _rodar_shopee(db, canal, integ, falso)
    chat = (await db.execute(select(AtendimentoConversa))).scalar_one()
    assert chat.pedido_marketplace == A
    await _linha(db, chat, A, aberta_em=agora - timedelta(days=2))

    # O comprador manda o cartão de outra compra.
    falso.conversa(
        "T", msg_shopee("T", agora - timedelta(hours=2), tipo="order", content={"order_sn": B})
    )
    await _rodar_shopee(db, canal, integ, falso)
    await db.refresh(chat)
    assert chat.pedido_marketplace == A
    assert chat.dados["pedido_citado"] == B

    # E a citação solta (`source_content`) de mais uma, relida com a página inteira.
    falso.conversa(
        "T", msg_shopee("T", agora - timedelta(hours=1), source={"order_sn": C}, texto="e esse?")
    )
    await _rodar_shopee(db, canal, integ, falso)
    await db.refresh(chat)
    assert chat.pedido_marketplace == A
    assert chat.dados["pedido_citado"] == C

    # Um dos outros escritores (o `upsert_conversa` é o caminho de todos): igual.
    await _ler(db, chat, integ, B)
    assert (chat.pedido_marketplace, chat.dados["pedido_citado"]) == (A, B)
    # O comprador volta a citar o pedido devolvido: a chave acompanha.
    await _ler(db, chat, integ, A)
    assert (chat.pedido_marketplace, chat.dados["pedido_citado"]) == (A, A)

    # Encerrou: a leitura seguinte já segue o citado, sem a chave.
    linha = (await db.execute(select(AtendimentoReclamacao))).scalar_one()
    linha.encerrada_em = agora
    await db.commit()
    await _ler(db, chat, integ, B)
    assert chat.pedido_marketplace == B and "pedido_citado" not in chat.dados


async def test_duas_abertas_prazo_mais_curto_sem_prazo_por_ultimo(db, make_user):
    integ = await _loja(db, make_user)
    chat = await _chat(db, integ, pedido=A)
    # Sem prazo, mesmo mais nova, fica por último.
    await _linha(db, chat, C, aberta_em=T0)
    await _linha(db, chat, A, prazo=T0 + timedelta(days=3))
    assert await foco_devolucao.pedido_em_foco(db, chat, T0) == A
    # O prazo mais curto vence, mesmo com o da outra aberta antes.
    outra = await _linha(
        db, chat, B, prazo=T0 + timedelta(days=1), aberta_em=T0 - timedelta(days=9)
    )
    assert await foco_devolucao.pedido_em_foco(db, chat, T0) == B
    # Encerrou: a seguinte.
    outra.encerrada_em = T0
    await db.commit()
    assert await foco_devolucao.pedido_em_foco(db, chat, T0) == A


async def test_duas_abertas_empate_pela_mais_recente_depois_pelo_numero(db, make_user):
    integ = await _loja(db, make_user)
    chat = await _chat(db, integ, pedido=A)
    prazo = T0 + timedelta(days=1)
    await _linha(db, chat, A, externo="RSN-2", prazo=prazo, aberta_em=T0 - timedelta(days=2))
    await _linha(db, chat, B, externo="RSN-1", prazo=prazo, aberta_em=T0 - timedelta(days=2))
    assert await foco_devolucao.pedido_em_foco(db, chat, T0) == B  # o número
    await _linha(db, chat, C, externo="RSN-9", prazo=prazo, aberta_em=T0 - timedelta(days=1))
    assert await foco_devolucao.pedido_em_foco(db, chat, T0) == C  # a mais recente


async def test_duas_abertas_no_cron_o_chat_fica_na_mais_curta_e_guarda_o_citado(db, make_user):
    integ = await _loja(db, make_user)
    chat = await _chat(db, integ)
    chat_id = chat.id
    await _no_indice(db, integ, A)
    await _no_indice(db, integ, C)
    await _logistica(db, pedido=A, bling="1")
    await _logistica(db, pedido=C, bling="2")
    await _rastreio(db, bling="1", rid="RSN-A", prazo=T0 + timedelta(days=5))
    await _rastreio(db, bling="2", rid="RSN-C", prazo=T0 + timedelta(days=1))
    await _ligar(db, fabrica_cliente=_fabrica(_Plataforma()))
    chat = await db.get(AtendimentoConversa, chat_id)
    assert (chat.pedido_marketplace, chat.dados["pedido_citado"]) == (C, B)
    assert await _conversas(db, canal="reclamacao") == []
    # As duas no cartão.
    assert len(await reclamacoes.reclamacoes_da_conversa(db, chat)) == 2

    # A de prazo curto encerrou: o foco passa para a outra; o citado continua.
    await _mudar(db, C, "CANCELLED", T0 + timedelta(hours=1))
    await _ligar(db, T0 + timedelta(hours=1, minutes=5), fabrica_cliente=_fabrica(_Plataforma()))
    chat = await db.get(AtendimentoConversa, chat_id)
    assert (chat.pedido_marketplace, chat.dados["pedido_citado"]) == (A, B)
    # A última encerrou: volta para o citado, a chave sai e a etiqueta muda.
    await _mudar(db, A, "CANCELLED", T0 + timedelta(hours=2))
    await _ligar(db, T0 + timedelta(hours=2, minutes=5), fabrica_cliente=_fabrica(_Plataforma()))
    chat = await db.get(AtendimentoConversa, chat_id)
    assert chat.pedido_marketplace == B and "pedido_citado" not in chat.dados
    assert chat.etiqueta != "devolucao"


async def test_encerrou_o_chat_volta_e_a_etiqueta_sai_na_mesma_rodada(db, make_user):
    integ = await _loja(db, make_user)
    chat = await _chat(db, integ)
    chat_id = chat.id
    await _no_indice(db, integ)
    await _logistica(db)
    await _rastreio(db)
    falsa = _Plataforma()
    await _ligar(db, fabrica_cliente=_fabrica(falsa))
    chat = await db.get(AtendimentoConversa, chat_id)
    assert chat.etiqueta == "devolucao"

    await _mudar(db, A, "CANCELLED", T0 + timedelta(hours=1))
    resumo = await _ligar(db, T0 + timedelta(hours=1, minutes=5), fabrica_cliente=_fabrica(falsa))
    assert resumo["erros"] == 0
    chat = await db.get(AtendimentoConversa, chat_id)
    assert chat.pedido_marketplace == B and "pedido_citado" not in chat.dados
    assert chat.etiqueta != "devolucao"
    # Encerrado não prende, e o chat dele ainda mostra o cartão como encerrada.
    [linha] = await _linhas(db)
    assert linha.encerrada_em is not None and linha.conversa_id == chat_id

    # Nem na rodada seguinte, com o chat em outro pedido, algo se mexe.
    resumo = await _ligar(db, T0 + timedelta(hours=2), fabrica_cliente=_fabrica(falsa))
    assert (resumo["conversas_novas"], resumo["atualizadas"]) == (0, 0)
    chat = await db.get(AtendimentoConversa, chat_id)
    assert chat.pedido_marketplace == B


async def test_aberta_ha_mais_de_30_dias_nao_prende_e_o_cartao_fica(db, make_user):
    integ = await _loja(db, make_user)
    chat = await _chat(db, integ)
    chat_id = chat.id
    linha = await _linha(db, chat, A, aberta_em=T0 - timedelta(days=30, seconds=1))
    assert await foco_devolucao.pedido_em_foco(db, chat, T0) is None
    assert await gravar.aplicar_foco_da_devolucao(db, chat, agora=T0) is False
    assert chat.pedido_marketplace == B
    # A leitura segue o último citado normalmente.
    await _ler(db, chat, integ, C)
    assert chat.pedido_marketplace == C and "pedido_citado" not in chat.dados
    assert [r.id for r in await reclamacoes.reclamacoes_da_conversa(db, chat)] == [linha.id]
    # Exatamente 30 dias ainda prende.
    linha.aberta_em = T0 - timedelta(days=30)
    await db.commit()
    assert await foco_devolucao.pedido_em_foco(db, chat, T0) == A
    assert chat.id == chat_id


async def test_caso_que_passa_dos_30_dias_solta_o_chat_para_o_citado(db, make_user):
    integ = await _loja(db, make_user)
    chat = await _chat(db, integ)
    chat_id = chat.id
    await _no_indice(db, integ)
    await _logistica(db)
    await _rastreio(db)
    await _ligar(db)
    chat = await db.get(AtendimentoConversa, chat_id)
    assert (chat.pedido_marketplace, chat.dados["pedido_citado"]) == (A, B)
    # 31 dias depois da abertura (T0 - 1 dia), sem novidade nenhuma do caso.
    resumo = await _ligar(db, T0 + timedelta(days=30, minutes=1))
    assert (resumo["conversas_novas"], resumo["atualizadas"]) == (0, 0)
    chat = await db.get(AtendimentoConversa, chat_id)
    assert chat.pedido_marketplace == B and "pedido_citado" not in chat.dados
    [linha] = await _linhas(db)
    assert linha.encerrada_em is None and linha.conversa_id == chat_id


async def test_toda_passada_reaplica_o_foco_mesmo_sem_novidade(db, make_user):
    """A corrida com o sync (ele gravou o citado antes de a linha existir) se corrige."""
    integ = await _loja(db, make_user)
    chat = await _chat(db, integ)
    chat_id = chat.id
    await _no_indice(db, integ)
    await _logistica(db)
    await _rastreio(db)
    await _ligar(db)
    chat = await db.get(AtendimentoConversa, chat_id)
    chat.pedido_marketplace = C
    chat.dados = {k: v for k, v in chat.dados.items() if k != "pedido_citado"}
    await db.commit()

    resumo = await _ligar(db, T0 + timedelta(minutes=10))
    assert resumo["atualizadas"] == 0  # o caso não mudou
    chat = await db.get(AtendimentoConversa, chat_id)
    assert (chat.pedido_marketplace, chat.dados["pedido_citado"]) == (A, C)


async def test_passada_sem_nada_a_mudar_nao_trava_nem_escreve(db, make_user, monkeypatch):
    integ = await _loja(db, make_user)
    chat = await _chat(db, integ, pedido=A)
    await _linha(db, chat, A)
    trava = AsyncMock()
    monkeypatch.setattr(gravar, "_travar_se_livre", trava)
    assert await gravar.aplicar_foco_da_devolucao(db, chat, agora=T0) is False
    assert trava.await_count == 0


async def test_chat_ocupado_fica_para_a_proxima_passada(db, make_user, monkeypatch):
    integ = await _loja(db, make_user)
    chat = await _chat(db, integ)
    await _linha(db, chat, A)
    monkeypatch.setattr(gravar, "_travar_se_livre", AsyncMock(return_value=False))
    assert await gravar.aplicar_foco_da_devolucao(db, chat, agora=T0) is False
    assert chat.pedido_marketplace == B
    monkeypatch.undo()
    monkeypatch.setattr(foco_devolucao, "_agora", lambda: T0)
    assert await gravar.aplicar_foco_da_devolucao(db, chat, agora=T0) is True
    await db.commit()
    await db.refresh(chat)
    assert (chat.pedido_marketplace, chat.dados["pedido_citado"]) == (A, B)


# ─────────────── sem devolução, nada muda ───────────────


async def test_chat_sem_devolucao_segue_o_ultimo_pedido_sem_consulta_extra(
    db, make_user, monkeypatch
):
    integ = await _loja(db, make_user)
    chat = await _chat(db, integ)
    chamadas = _espia_o_foco(monkeypatch)

    # O mesmo pedido, sem a chave (a página relida de todo chat): zero consulta.
    await _ler(db, chat, integ, B)
    # A lista, sem pedido: zero consulta.
    await _ler(db, chat, integ, None)
    assert chamadas == []
    assert chat.pedido_marketplace == B and chat.dados == {}

    # Citou outro: UMA consulta, e ele segue o citado, sem chave.
    await _ler(db, chat, integ, C)
    assert chamadas == ["chat-1"]
    assert chat.pedido_marketplace == C and chat.dados == {}
    # E a etiqueta seguiu a régua de sempre.
    assert await _mensagens(db, chat.id) == []


async def test_a_chave_que_sobrou_paga_consulta_e_sai(db, make_user, monkeypatch):
    integ = await _loja(db, make_user)
    chat = await _chat(db, integ, pedido=A, dados={"pedido_citado": B, "outra": 1})
    chamadas = _espia_o_foco(monkeypatch)
    # Lido igual ao atual, mas a chave existe: confere, e sem foco ela sai.
    await _ler(db, chat, integ, A)
    assert chamadas == ["chat-1"]
    assert chat.pedido_marketplace == A and chat.dados == {"outra": 1}


async def test_leitura_mexe_em_dados_com_dicionario_novo(db, make_user):
    """JSONB: mutar no lugar não marca a coluna como suja."""
    integ = await _loja(db, make_user)
    chat = await _chat(db, integ, pedido=A, dados={"outra": 1})
    await _linha(db, chat, A)
    antes = chat.dados
    await _ler(db, chat, integ, B)
    assert antes == {"outra": 1}
    fresco = await db.scalar(
        select(AtendimentoConversa.dados).where(AtendimentoConversa.id == chat.id)
    )
    assert fresco == {"outra": 1, "pedido_citado": B}


# ─────────────── TikTok e ML como sempre ───────────────


async def test_tiktok_nao_liga_pelo_comprador_nem_ganha_foco(db, make_user, monkeypatch):
    integ = await _loja(db, make_user, nome="tt", plataforma=IntegrationPlatform.TIKTOK)
    chat = await _chat(db, integ, plataforma="tiktok")
    db.add(
        AtendimentoPedidoComprador(
            integration_id=integ.id, plataforma="tiktok", comprador_id=COMPRADOR, pedido="TT1"
        )
    )
    await db.commit()
    await _logistica(
        db,
        plataforma="TikTok",
        pedido="TT1",
        bling="288403",
        conta="tt",
        status="AWAITING_BUYER_SHIP",
        tipo="RETURN_AND_REFUND",
    )
    resumo = await _ligar(db)
    assert resumo["conversas_novas"] == 1
    [conversa] = await _conversas(db, "TT1", "reclamacao")
    [linha] = await _linhas(db, "TT1")
    assert linha.conversa_id == conversa.id
    await db.refresh(chat)
    assert chat.pedido_marketplace == B and "pedido_citado" not in chat.dados
    # Sem os campos do bloco.
    tela = reclamacoes.para_tela(linha, pedido_da_conversa="TT1")
    assert tela["do_pedido"] is False
    assert "rastreio_volta" not in linha.dados and "valor_lido_em" not in linha.dados

    # A linha ligada ao chat da TikTok não prende o pedido dele, sem consulta de foco.
    chamadas = _espia_o_foco(monkeypatch)
    await db.refresh(chat)
    await db.refresh(integ)
    linha.conversa_id = chat.id
    await db.commit()
    await _ler(db, chat, integ, "TT9")
    assert chat.pedido_marketplace == "TT9" and "pedido_citado" not in chat.dados
    assert chamadas == []
    assert await gravar.aplicar_foco_da_devolucao(db, chat, agora=T0) is False


async def test_ml_nao_ganha_foco(db, make_user, monkeypatch):
    integ = await _loja(db, make_user, nome="ml", plataforma=IntegrationPlatform.ML)
    chat = await _chat(db, integ, plataforma="ml")
    await _linha(db, chat, A, plataforma="ml")
    chamadas = _espia_o_foco(monkeypatch)
    await _ler(db, chat, integ, C)
    assert chat.pedido_marketplace == C and "pedido_citado" not in chat.dados
    assert chamadas == []
    assert await foco_devolucao.pedido_em_foco(db, chat, T0) is None


async def test_chat_de_afiliado_nao_ganha_foco(db, make_user, monkeypatch):
    integ = await _loja(db, make_user)
    chat = await _chat(db, integ, dados={"afiliado": True})
    await _linha(db, chat, A)
    chamadas = _espia_o_foco(monkeypatch)
    await _ler(db, chat, integ, C)
    assert chat.pedido_marketplace == C and "pedido_citado" not in chat.dados
    assert chamadas == []


# ─────────────── a lista, o /resumo e a Caixa Humano ───────────────


@pytest.fixture
async def pessoa(make_user, auth_as, monkeypatch) -> User:
    monkeypatch.setattr(rota, "SO_ADMIN", False)
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
    monkeypatch.setattr(s, "atendimento_humano_modos", "")
    monkeypatch.setattr(humano, "ESPERA_IA", timedelta(days=3650))
    u = await make_user(permissions=PODE_TUDO)
    auth_as(u)
    return u


async def _devolucao_na_caixa(
    db: AsyncSession, integ: Integration, externo: str, comprador: str | None
) -> AtendimentoConversa:
    """A conversa `reclamacao` da Shopee na Caixa Humano: esperando, com a IA pausada."""
    c = AtendimentoConversa(
        integration_id=integ.id,
        plataforma="shopee",
        canal="reclamacao",
        externo_id=externo,
        comprador_id=comprador,
        pedido_marketplace=externo,
        situacao="aberta",
        aguardando_resposta=True,
        ia_pausada=True,
        etiqueta="devolucao",
        dados={},
        ultima_mensagem_em=datetime.now(UTC),
        ultima_do_cliente_em=datetime.now(UTC),
    )
    db.add(c)
    await db.commit()
    return c


def _ids_da_lista(resposta) -> set[str]:
    assert resposta.status_code == 200, resposta.text
    return {i["id"] for i in resposta.json()["itens"]}


async def test_lista_resumo_e_caixa_humano_escondem_igual(client, db, make_user, pessoa):
    integ = await _loja(db, make_user)
    db.add(
        AtendimentoCanal(
            integration_id=integ.id, plataforma="shopee", canal="chat", modo="auto", status="ok"
        )
    )
    await db.commit()
    chat = await _chat(db, integ)
    com_chat = await _devolucao_na_caixa(db, integ, "COM-CHAT", COMPRADOR)
    sem_chat = await _devolucao_na_caixa(db, integ, "SEM-CHAT", "outro-comprador")
    sem_comprador = await _devolucao_na_caixa(db, integ, "SEM-COMPRADOR", None)
    visiveis = {str(sem_chat.id), str(sem_comprador.id)}

    assert await foco_devolucao.conversas_da_devolucao_com_chat(db) == {com_chat.id}

    # A lista, com qualquer filtro.
    r = await client.get(f"{URL}/conversas")
    assert _ids_da_lista(r) == visiveis | {str(chat.id)}
    r = await client.get(f"{URL}/conversas", params={"etiqueta": "devolucao"})
    assert _ids_da_lista(r) == visiveis
    r = await client.get(f"{URL}/conversas", params={"filtro": "devolucao"})
    assert _ids_da_lista(r) == visiveis
    r = await client.get(f"{URL}/conversas", params={"q": "COM-CHAT"})
    assert str(com_chat.id) not in _ids_da_lista(r)
    # A Caixa Humano.
    r = await client.get(f"{URL}/conversas", params={"caixa": "humano"})
    assert _ids_da_lista(r) == visiveis
    # O /resumo.
    res = (await client.get(f"{URL}/resumo")).json()
    assert res["humano"] == 2
    shopee_res = next(p for p in res["plataformas"] if p["plataforma"] == "shopee")
    assert shopee_res["humano"] == 2
    assert shopee_res["etiquetas"]["devolucao"] == 2
    [loja] = res["lojas"]
    assert loja["humano"] == 2
    # A contagem da Caixa Humano (o mesmo número da tela).
    assert (await humano.contar(db))["na_caixa"] == 2

    # O acesso direto por id continua abrindo, com a faixa de quem precisa de pessoa.
    r = await client.get(f"{URL}/conversas/{com_chat.id}")
    assert r.status_code == 200
    assert r.json()["conversa"]["id"] == str(com_chat.id)
    assert r.json()["conversa"]["humano"] is not None

    # O chat fechou: a linha volta para todos, sem nada apagado.
    chat.situacao = "fechada"
    await db.commit()
    assert await foco_devolucao.conversas_da_devolucao_com_chat(db) == frozenset()
    r = await client.get(f"{URL}/conversas", params={"caixa": "humano"})
    assert _ids_da_lista(r) == visiveis | {str(com_chat.id)}
    assert (await client.get(f"{URL}/resumo")).json()["humano"] == 3
    assert (await humano.contar(db))["na_caixa"] == 3


async def test_lista_nao_esconde_por_afiliado_outra_loja_ou_outra_plataforma(db, make_user):
    integ = await _loja(db, make_user)
    outra = await _loja(db, make_user, nome="poofy")
    await _chat(db, integ, externo="af", comprador="1", dados={"afiliado": True})
    await _chat(db, outra, externo="ot", comprador="2")
    await _chat(db, integ, externo="fe", comprador="3", situacao="fechada")
    tt = await _loja(db, make_user, nome="tt", plataforma=IntegrationPlatform.TIKTOK)
    await _chat(db, tt, externo="tt", comprador="4", plataforma="tiktok")
    for comprador in ("1", "2", "3"):
        await _devolucao_na_caixa(db, integ, f"DEV-{comprador}", comprador)
    db.add(
        AtendimentoConversa(
            integration_id=tt.id,
            plataforma="tiktok",
            canal="reclamacao",
            externo_id="DEV-4",
            comprador_id="4",
            dados={},
        )
    )
    await db.commit()
    assert await foco_devolucao.conversas_da_devolucao_com_chat(db) == frozenset()


def test_fora_da_lista_sem_ocultas_nao_filtra_nada():
    sql = str(foco_devolucao.fora_da_lista_sql(frozenset()).compile())
    assert "NOT IN" not in sql.upper()


# ─────────────── o retrato ───────────────


class _ClienteDoRetrato:
    """O que `enriquecer_conversa` usa do cliente da Shopee."""

    shop_id = 1

    def __init__(self, falhar: set[str] = frozenset()) -> None:
        self.pedidos: list[str] = []
        self.falhar = set(falhar)

    async def get_order_detail_completo(self, pedido: str) -> dict:
        self.pedidos.append(pedido)
        if pedido in self.falhar:
            raise RuntimeError("shopee_order_detail error_server: busy")
        return {
            "order_sn": pedido,
            "order_status": "COMPLETED",
            "create_time": 1759000000,
            "total_amount": 311.91,
            "currency": "BRL",
            "item_list": [
                {"item_id": 1, "item_name": "Item do pedido", "model_quantity_purchased": 1}
            ],
            "package_list": [],
        }

    async def get_tracking_number(self, pedido: str) -> str:
        return ""

    async def get_tracking_info(self, pedido: str) -> dict:
        return {}


def _fabrica_retrato(cliente):
    async def fabrica(_integ):
        return cliente

    return fabrica


async def test_retrato_pedido_quando_o_foco_muda_e_de_novo_quando_sai(db, make_user):
    integ = await _loja(db, make_user)
    retrato_b = {"pedido": B, "status": "COMPLETED", "itens": [{"titulo": "Do B"}]}
    chat = await _chat(db, integ, dados={"pedido_mkt": retrato_b})
    chat_id = chat.id
    await _no_indice(db, integ)
    await _logistica(db)
    await _rastreio(db)

    pendentes: set = set()
    await _ligar(db, retratos_pendentes=pendentes)
    assert pendentes == {chat_id}
    chat = await db.get(AtendimentoConversa, chat_id)
    # Antes do retrato novo, o de outro pedido ainda está guardado (a tela o trata).
    assert chat.dados["pedido_mkt"]["pedido"] == B

    cliente = _ClienteDoRetrato()
    feitos = await rd.renovar_retratos(db, pendentes, fabrica_cliente=_fabrica_retrato(cliente))
    assert feitos == 1 and cliente.pedidos == [A]
    await db.refresh(chat)
    assert chat.dados["pedido_mkt"]["pedido"] == A

    # Foco parado com o retrato certo: nada a pedir.
    pendentes = set()
    await _ligar(db, T0 + timedelta(minutes=10), retratos_pendentes=pendentes)
    assert pendentes == set()

    # Encerrou: o chat volta para o citado, e o retrato acompanha.
    await _mudar(db, A, "CANCELLED", T0 + timedelta(hours=1))
    pendentes = set()
    await _ligar(db, T0 + timedelta(hours=1, minutes=5), retratos_pendentes=pendentes)
    assert pendentes == {chat_id}
    assert await rd.renovar_retratos(db, pendentes, fabrica_cliente=_fabrica_retrato(cliente)) == 1
    assert cliente.pedidos == [A, B]
    await db.refresh(chat)
    assert chat.pedido_marketplace == B and chat.dados["pedido_mkt"]["pedido"] == B


async def test_retrato_na_conversa_reclamacao_sem_chat(db, make_user):
    await _loja(db, make_user)
    await _logistica(db)
    await _rastreio(db)
    pendentes: set = set()
    await _ligar(db, fabrica_cliente=_fabrica(_Plataforma()), retratos_pendentes=pendentes)
    [conversa] = await _conversas(db, canal="reclamacao")
    assert pendentes == {conversa.id}
    cliente = _ClienteDoRetrato()
    assert await rd.renovar_retratos(db, pendentes, fabrica_cliente=_fabrica_retrato(cliente)) == 1
    await db.refresh(conversa)
    assert conversa.dados["pedido_mkt"]["pedido"] == A
    # A vez e o prazo da conversa ficam como estavam.
    assert "reclamacao_id" in conversa.dados


async def test_retrato_sem_foco_nao_pede_nada(db, make_user):
    """Caso encerrado num chat sem retrato, sem foco: sem devolução aberta, nada muda."""
    integ = await _loja(db, make_user)
    chat = await _chat(db, integ, pedido=A)
    chat_id = chat.id
    await _logistica(db, status="CANCELLED", mudou=T0 - timedelta(days=2))
    pendentes: set = set()
    await _ligar(db, retratos_pendentes=pendentes)
    [linha] = await _linhas(db)
    assert linha.conversa_id == chat_id
    assert pendentes == set()


async def test_retrato_tem_teto_por_rodada_e_a_sobra_vai_na_proxima(db, make_user, monkeypatch):
    integ = await _loja(db, make_user)
    chats = []
    for i, pedido in enumerate(("P1", "P2", "P3")):
        chats.append(await _chat(db, integ, pedido=pedido, comprador=str(i), externo=f"c{i}"))
    monkeypatch.setattr(rd, "MAX_RETRATOS_RODADA", 2)
    pendentes = {c.id for c in chats}
    cliente = _ClienteDoRetrato()
    fabrica = _fabrica_retrato(cliente)
    assert await rd.renovar_retratos(db, pendentes, fabrica_cliente=fabrica) == 2
    assert len(cliente.pedidos) == 2
    # A próxima rodada: os que já têm retrato fresco não vão à loja de novo.
    assert await rd.renovar_retratos(db, pendentes, fabrica_cliente=fabrica) == 1
    assert sorted(cliente.pedidos) == ["P1", "P2", "P3"]


async def test_retrato_respeita_o_carimbo_de_falha(db, make_user):
    """Sem forçar: a loja falhou, e a rodada seguinte (10 min) não insiste."""
    integ = await _loja(db, make_user)
    chat = await _chat(db, integ, pedido=A)
    cliente = _ClienteDoRetrato(falhar={A})
    fabrica = _fabrica_retrato(cliente)
    assert await rd.renovar_retratos(db, {chat.id}, fabrica_cliente=fabrica) == 0
    assert cliente.pedidos == [A]
    await db.refresh(chat)
    assert chat.dados["enriquecimento"]["pedido_mkt"]["falhas"] == 1
    assert await rd.renovar_retratos(db, {chat.id}, fabrica_cliente=fabrica) == 0
    assert cliente.pedidos == [A]


async def test_retrato_nunca_derruba_o_cron(db, make_user, monkeypatch):
    integ = await _loja(db, make_user)
    chat = await _chat(db, integ, pedido=A)
    outro = await _chat(db, integ, pedido=C, externo="c2", comprador="2")

    async def fabrica_quebrada(_integ):
        raise RuntimeError("token_nao_renovou")

    assert await rd.renovar_retratos(db, {chat.id}, fabrica_cliente=fabrica_quebrada) == 0
    assert await rd.renovar_retratos(db, {chat.id}, fabrica_cliente=None) == 0
    assert await rd.renovar_retratos(db, set(), fabrica_cliente=fabrica_quebrada) == 0

    # Uma conversa que quebra de um jeito inesperado não leva as outras.
    original = rd.enriquecer.enriquecer_conversa
    primeiro = sorted([chat.id, outro.id], key=str)[0]

    async def quebra(session, conversa, *a, **k):
        if conversa.id == primeiro:
            raise RuntimeError("quebrou")
        return await original(session, conversa, *a, **k)

    monkeypatch.setattr(rd.enriquecer, "enriquecer_conversa", quebra)
    cliente = _ClienteDoRetrato()
    ids = {chat.id, outro.id}
    assert await rd.renovar_retratos(db, ids, fabrica_cliente=_fabrica_retrato(cliente)) == 1


async def test_cron_pede_o_retrato_depois_de_gravar(db, make_user, monkeypatch):
    """O cron de verdade: o caso liga no chat, o foco muda e o retrato vem na mesma rodada."""
    s = get_settings()
    monkeypatch.setattr(s, "atendimento_leitura_ativa", True)
    monkeypatch.setattr(s, "atendimento_reclamacoes_ativa", True)
    monkeypatch.setattr(reclamacoes, "_pegar_trava", AsyncMock(return_value=(True, None)))
    monkeypatch.setattr(reclamacoes, "_soltar_trava", AsyncMock())
    monkeypatch.setattr(reclamacoes, "sincronizar_todas_ml", AsyncMock(return_value={"contas": 0}))
    monkeypatch.setattr(reclamacoes, "_agora", lambda: T0)
    integ = await _loja(db, make_user)
    chat = await _chat(db, integ)
    chat_id = chat.id
    await _no_indice(db, integ)
    await _logistica(db)
    await _rastreio(db, rid=None)
    cliente = _ClienteDoRetrato()

    resumo = await reclamacoes.atendimento_reclamacoes(
        None, fabrica_cliente=_fabrica_retrato(cliente)
    )

    assert resumo["devolucoes"]["retratos"] == 1
    assert cliente.pedidos == [A]
    db.expire_all()
    chat = await db.get(AtendimentoConversa, chat_id)
    assert chat.pedido_marketplace == A
    assert chat.dados["pedido_mkt"]["pedido"] == A


async def test_retrato_emprestado_na_conversa_reclamacao(client, db, make_user, pessoa):
    integ = await _loja(db, make_user)
    devolucao = AtendimentoConversa(
        integration_id=integ.id,
        plataforma="shopee",
        canal="reclamacao",
        externo_id="RSN1",
        comprador_id=COMPRADOR,
        pedido_marketplace=A,
        situacao="bloqueada",
        dados={},
    )
    db.add(devolucao)
    do_chat = {"pedido_mkt": {"pedido": A, "status": "COMPLETED"}}
    chat = await _chat(db, integ, pedido=A, dados=do_chat)
    await db.commit()
    pedido_bling = SimpleNamespace(numero="290580")

    r = await client.get(f"{URL}/conversas/{devolucao.id}")
    assert r.status_code == 200
    assert r.json()["pedido_mkt"]["pedido"] == A
    assert (await painel.conversa_do_retrato(db, devolucao, pedido_bling)).id == chat.id

    # Retrato do chat de OUTRO pedido: não empresta.
    chat.dados = {"pedido_mkt": {"pedido": B, "status": "COMPLETED"}}
    await db.commit()
    r = await client.get(f"{URL}/conversas/{devolucao.id}")
    assert r.json()["pedido_mkt"] is None
    assert (await painel.conversa_do_retrato(db, devolucao, pedido_bling)).id == devolucao.id

    # A conversa com retrato próprio fica com ele.
    chat.dados = {"pedido_mkt": {"pedido": A, "status": "COMPLETED"}}
    devolucao.dados = {"pedido_mkt": {"pedido": A, "status": "SHIPPED"}}
    await db.commit()
    r = await client.get(f"{URL}/conversas/{devolucao.id}")
    assert r.json()["pedido_mkt"]["status"] == "SHIPPED"
    assert (await painel.conversa_do_retrato(db, devolucao, pedido_bling)).id == devolucao.id

    # O próprio chat continua o retrato de si mesmo; o chat de afiliado nunca empresta.
    assert (await painel.conversa_do_retrato(db, chat, pedido_bling)).id == chat.id
    devolucao.dados = {}
    chat.dados = {"afiliado": True, "pedido_mkt": {"pedido": A, "status": "COMPLETED"}}
    await db.commit()
    r = await client.get(f"{URL}/conversas/{devolucao.id}")
    assert r.json()["pedido_mkt"] is None


async def test_retrato_emprestado_so_na_shopee(client, db, make_user, pessoa):
    integ = await _loja(db, make_user, nome="tt", plataforma=IntegrationPlatform.TIKTOK)
    devolucao = AtendimentoConversa(
        integration_id=integ.id,
        plataforma="tiktok",
        canal="reclamacao",
        externo_id="RTT",
        pedido_marketplace="TT1",
        situacao="bloqueada",
        dados={},
    )
    db.add(devolucao)
    await _chat(
        db,
        integ,
        pedido="TT1",
        plataforma="tiktok",
        dados={"pedido_mkt": {"pedido": "TT1", "status": "COMPLETED"}},
    )
    await db.commit()
    r = await client.get(f"{URL}/conversas/{devolucao.id}")
    assert r.status_code == 200 and r.json()["pedido_mkt"] is None


# ─────────────── para a tela: o bloco do pedido ───────────────


async def test_para_tela_com_os_campos_do_bloco_e_sem_o_texto_do_comprador(
    client, db, make_user, pessoa
):
    integ = await _loja(db, make_user)
    chat = await _chat(db, integ)
    chat_id = chat.id
    await _no_indice(db, integ)
    await _logistica(db)
    prazo = T0 + timedelta(days=2)
    await _rastreio(db, prazo=prazo)
    dr = await db.get(DevolucaoRastreio, "290580")
    dr.rastreio_auto = "BR1234567890"
    dr.localizacao_auto = "Objeto em trânsito"
    await db.commit()
    falsa = _Plataforma()
    falsa.detalhe = {**falsa.detalhe, "refund_amount": 311.91, "currency": "BRL"}
    await _ligar(db, fabrica_cliente=_fabrica(falsa))

    [linha] = await _linhas(db)
    # O texto livre do comprador nunca é guardado.
    assert TEXTO_DO_COMPRADOR not in str(linha.dados)
    assert "text_reason" not in linha.dados

    volta = ("BR1234567890", "Objeto em trânsito")
    tela = reclamacoes.para_tela(linha, pedido_da_conversa=A)
    assert tela["do_pedido"] is True
    assert (tela["valor_reembolso"], tela["moeda"]) == (311.91, "BRL")
    assert (tela["rastreio_volta"], tela["situacao_volta"]) == volta
    assert TEXTO_DO_COMPRADOR not in str(tela)
    # A conversa em outro pedido, ou sem o pedido: não é do pedido dela.
    assert reclamacoes.para_tela(linha, pedido_da_conversa=B)["do_pedido"] is False
    assert reclamacoes.para_tela(linha)["do_pedido"] is False

    # Pela rota: o chat foi para o pedido devolvido.
    linha_id = linha.id
    await db.refresh(pessoa)  # `_ligar` expirou tudo, e a rota lê a pessoa da mesma sessão
    r = await client.get(f"{URL}/conversas/{chat_id}/reclamacoes")
    assert r.status_code == 200, r.text
    [item] = r.json()["itens"]
    assert item["do_pedido"] is True
    assert (item["valor_reembolso"], item["moeda"]) == (311.91, "BRL")
    assert (item["rastreio_volta"], item["situacao_volta"]) == volta
    assert TEXTO_DO_COMPRADOR not in r.text

    # Nada disso entra no contexto do agente (a lista explícita de `_reclamacoes`).
    chat = await db.get(AtendimentoConversa, chat_id)
    [ctx] = await contexto._reclamacoes(db, chat)
    for campo in ("do_pedido", "valor_reembolso", "moeda", "rastreio_volta", "situacao_volta"):
        assert campo not in ctx

    # Encerrou: não é mais do pedido.
    linha = await db.get(AtendimentoReclamacao, linha_id)
    linha.encerrada_em = T0
    await db.commit()
    assert reclamacoes.para_tela(linha, pedido_da_conversa=A)["do_pedido"] is False


async def test_para_tela_sem_os_dados_vem_null(client, db, make_user, pessoa):
    integ = await _loja(db, make_user)
    chat = await _chat(db, integ, pedido=A)
    await _linha(db, chat, A)
    r = await client.get(f"{URL}/conversas/{chat.id}/reclamacoes")
    [item] = r.json()["itens"]
    assert item["do_pedido"] is True
    for campo in ("valor_reembolso", "moeda", "rastreio_volta", "situacao_volta"):
        assert item[campo] is None


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        (311.91, (311.91, "BRL")),
        ("311.91", (311.91, "BRL")),
        (0, (None, None)),
        (None, (None, None)),
        ("", (None, None)),
        ("abc", (None, None)),
        (True, (None, None)),
        (-1, (None, None)),
    ],
)
def test_reembolso_shopee(bruto, esperado):
    assert rd.reembolso_shopee({"refund_amount": bruto, "currency": "BRL"}) == esperado


def test_reembolso_shopee_sem_detalhe():
    assert rd.reembolso_shopee(None) == (None, None)
    assert rd.reembolso_shopee({}) == (None, None)


async def test_caso_aberto_sem_valor_e_relido_uma_vez_e_o_motivo_nao_some(db, make_user):
    """A linha de antes do valor (`motivo_lido_status` igual, sem `valor_lido_em`) é relida
    na rodada seguinte; a releitura sem o motivo não o apaga; com o valor, não lê mais."""
    await _loja(db, make_user)
    await _logistica(db)
    await _rastreio(db)
    falsa = _Plataforma()
    await _ligar(db, fabrica_cliente=_fabrica(falsa))
    [linha] = await _linhas(db)
    assert linha.motivo == "Produto com defeito (não funciona)"
    assert linha.dados["valor_lido_em"] is not None
    assert "valor_reembolso" not in linha.dados
    # Como estava gravada antes desta entrega.
    linha.dados = {k: v for k, v in linha.dados.items() if k != "valor_lido_em"}
    await db.commit()

    falsa.detalhe = {
        **falsa.detalhe,
        "reason": "",
        "refund_amount": 99.5,
        "currency": "BRL",
    }
    resumo = await _ligar(db, T0 + timedelta(minutes=10), fabrica_cliente=_fabrica(falsa))
    assert resumo["motivos_lidos"] == 1
    [linha] = await _linhas(db)
    assert linha.motivo == "Produto com defeito (não funciona)"
    assert linha.dados["motivo_codigo"] == "FUNCTIONAL_DMG"
    assert (linha.dados["valor_reembolso"], linha.dados["moeda"]) == (99.5, "BRL")

    # Com o valor, o status igual não lê mais, nem dias depois.
    resumo = await _ligar(db, T0 + timedelta(days=5), fabrica_cliente=_fabrica(falsa))
    assert resumo["motivos_lidos"] == 0

    # A releitura que volta sem o valor não apaga o que já se sabia.
    falsa.detalhe = {k: v for k, v in falsa.detalhe.items() if k != "refund_amount"}
    await _mudar(db, A, "PROCESSING", T0 + timedelta(days=6))
    resumo = await _ligar(db, T0 + timedelta(days=6, minutes=5), fabrica_cliente=_fabrica(falsa))
    assert resumo["motivos_lidos"] == 1
    [linha] = await _linhas(db)
    assert linha.dados["valor_reembolso"] == 99.5


async def test_caso_sem_valor_na_shopee_nao_gasta_a_cota_toda_rodada(db, make_user):
    await _loja(db, make_user)
    await _logistica(db)
    await _rastreio(db)
    falsa = _Plataforma()
    await _ligar(db, fabrica_cliente=_fabrica(falsa))
    lidos = len([c for c in falsa.chamadas if c[1].endswith("get_return_detail")])
    assert lidos == 1

    # Nas rodadas seguintes não relê; depois de `REVER_VALOR_A_CADA`, uma vez.
    for horas in (1, 6, 23):
        await _ligar(db, T0 + timedelta(hours=horas), fabrica_cliente=_fabrica(falsa))
    assert len([c for c in falsa.chamadas if c[1].endswith("get_return_detail")]) == 1
    await _ligar(db, T0 + timedelta(hours=24), fabrica_cliente=_fabrica(falsa))
    assert len([c for c in falsa.chamadas if c[1].endswith("get_return_detail")]) == 2


async def test_releitura_do_valor_respeita_o_recuo_de_falha(db, make_user):
    await _loja(db, make_user)
    await _logistica(db)
    await _rastreio(db)
    falsa = _Plataforma()
    await _ligar(db, fabrica_cliente=_fabrica(falsa))
    [linha] = await _linhas(db)
    linha.dados = {k: v for k, v in linha.dados.items() if k != "valor_lido_em"}
    await db.commit()

    falsa.falhar = True
    resumo = await _ligar(db, T0 + timedelta(minutes=10), fabrica_cliente=_fabrica(falsa))
    assert resumo["erros_leitura"] == 1
    [linha] = await _linhas(db)
    assert linha.motivo == "Produto com defeito (não funciona)"  # não some
    falsa.falhar = False
    resumo = await _ligar(db, T0 + timedelta(minutes=20), fabrica_cliente=_fabrica(falsa))
    assert resumo["leituras"] == 0  # antes de `RETENTAR_FALHA`
    resumo = await _ligar(
        db, T0 + timedelta(minutes=10) + reclamacoes.RETENTAR_FALHA, fabrica_cliente=_fabrica(falsa)
    )
    assert resumo["motivos_lidos"] == 1


# ─────────────── nada de mensagem nova no chat ───────────────


async def test_o_chat_nao_ganha_mensagem_nem_muda_a_previa(db, make_user):
    integ = await _loja(db, make_user)
    chat = await _chat(db, integ)
    chat_id = chat.id
    chat.ultima_mensagem_resumo = "oi, tudo bem?"
    chat.ultima_mensagem_em = T0 - timedelta(hours=5)
    await db.commit()
    await _no_indice(db, integ)
    await _logistica(db)
    await _rastreio(db, prazo=T0 + timedelta(days=1))
    await _ligar(db, fabrica_cliente=_fabrica(_Plataforma()))
    chat = await db.get(AtendimentoConversa, chat_id)
    assert chat.ultima_mensagem_resumo == "oi, tudo bem?"
    assert chat.ultima_mensagem_em == T0 - timedelta(hours=5)
    total = await db.scalar(
        select(AtendimentoMensagem.id).where(AtendimentoMensagem.conversa_id == chat_id)
    )
    assert total is None


# ── A revisão de 04/11/2026: o que a primeira versão deixava escapar ─────────


async def _chat_preso_em_a(db, make_user, falsa=None):
    """O chat em B levado pelo cron para a devolução de A, com outro caso vivo na Logística."""
    integ = await _loja(db, make_user)
    chat = await _chat(db, integ)
    chat_id = chat.id
    await _no_indice(db, integ)
    await _logistica(db)
    await _rastreio(db)
    await _logistica(db, pedido="OUTRO-VIVO", bling="290581")
    await _ligar(db, fabrica_cliente=_fabrica(falsa or _Plataforma()))
    chat = await db.get(AtendimentoConversa, chat_id)
    assert chat.pedido_marketplace == A and chat.dados["pedido_citado"] == B
    return integ, chat_id


async def _faxina(db: AsyncSession, pedido: str = A) -> None:
    """`logistica_ingest.cleanup_finalizados`: o Bling finalizou, a linha some."""
    await db.execute(delete(Logistica).where(Logistica.pedido_marketplace == pedido))
    await db.commit()


async def test_caso_que_saiu_da_logistica_encerra_e_o_chat_volta_para_o_citado(db, make_user):
    falsa = _Plataforma()
    _, chat_id = await _chat_preso_em_a(db, make_user, falsa)
    await _faxina(db)

    resumo = await _ligar(db, T0 + timedelta(hours=1), fabrica_cliente=_fabrica(falsa))
    assert resumo["focos_soltos"] == 1
    chat = await db.get(AtendimentoConversa, chat_id)
    assert chat.pedido_marketplace == B and "pedido_citado" not in chat.dados
    assert chat.etiqueta != "devolucao"
    linha = (
        await db.execute(
            select(AtendimentoReclamacao).where(AtendimentoReclamacao.pedido_marketplace == A)
        )
    ).scalar_one()
    assert linha.encerrada_em is not None and linha.prazo_em is None
    assert linha.conversa_id == chat_id  # o cartão continua no chat, como encerrado


async def test_sem_nenhum_caso_na_logistica_nada_e_encerrado(db, make_user):
    """Logística vazia não prova que o caso acabou: pode ser a leitura. Nada se escreve."""
    _, chat_id = await _chat_preso_em_a(db, make_user)
    await db.execute(delete(Logistica))
    await db.commit()

    resumo = await _ligar(db, T0 + timedelta(hours=1))
    assert "focos_soltos" not in resumo
    linha = (
        await db.execute(
            select(AtendimentoReclamacao).where(AtendimentoReclamacao.pedido_marketplace == A)
        )
    ).scalar_one()
    assert linha.encerrada_em is None


async def test_passada_parcial_nao_encerra_o_que_nao_leu(db, make_user):
    """`pedidos` limita a leitura a alguns pedidos: o resto não saiu da Logística."""
    _, chat_id = await _chat_preso_em_a(db, make_user)
    resumo = await rd.ligar_devolucoes(db, agora=T0 + timedelta(hours=1), pedidos=["OUTRO-VIVO"])
    await db.commit()
    assert "focos_soltos" not in resumo
    chat = await db.get(AtendimentoConversa, chat_id)
    assert chat.pedido_marketplace == A


async def test_linha_orfa_antiga_nao_prende_o_chat_nem_e_encerrada(db, make_user):
    """As 147 de 04/11: abertas, fora da Logística, com o chat já no pedido, sem citado.
    Não prendem a citação de outra compra, e seguem como estão (encerrar é com o dono)."""
    integ = await _loja(db, make_user)
    chat = await _chat(db, integ, pedido=A)
    linha = await _linha(db, chat)
    linha.dados = {"fonte": "logistica"}
    await db.commit()
    assert await foco_devolucao.pedido_em_foco(db, chat) is None

    atual = await _ler(db, chat, integ, C)
    assert atual.pedido_marketplace == C and "pedido_citado" not in atual.dados
    linha_id = linha.id
    await _logistica(db, pedido="OUTRO-VIVO", bling="290581")
    await _ligar(db, T0 + timedelta(hours=1))
    linha = await db.get(AtendimentoReclamacao, linha_id)
    assert linha.encerrada_em is None


async def test_linha_de_outra_fonte_nao_depende_da_logistica(db, make_user):
    integ = await _loja(db, make_user)
    chat = await _chat(db, integ, pedido=A)
    await _linha(db, chat)  # sem `fonte`
    assert await foco_devolucao.pedido_em_foco(db, chat) == A


async def test_chat_em_foco_que_fecha_ganha_a_conversa_e_volta_quando_reabre(db, make_user):
    """Fechar o chat preso não pode deixar a devolução aberta invisível."""
    falsa = _Plataforma()
    _, chat_id = await _chat_preso_em_a(db, make_user, falsa)
    chat = await db.get(AtendimentoConversa, chat_id)
    chat.situacao = "fechada"
    await db.commit()

    await _ligar(db, T0 + timedelta(hours=1), fabrica_cliente=_fabrica(falsa))
    [conversa] = await _conversas(db, canal="reclamacao")
    conversa_id = conversa.id
    assert conversa.comprador_id == COMPRADOR  # do chat: a plataforma nem foi perguntada
    linha = (
        await db.execute(
            select(AtendimentoReclamacao).where(AtendimentoReclamacao.pedido_marketplace == A)
        )
    ).scalar_one()
    linha_id = linha.id
    assert linha.conversa_id == conversa_id and linha.encerrada_em is None
    assert await foco_devolucao.conversas_da_devolucao_com_chat(db) == frozenset()

    chat = await db.get(AtendimentoConversa, chat_id)
    chat.situacao = "aberta"
    await db.commit()
    await _ligar(db, T0 + timedelta(hours=2), fabrica_cliente=_fabrica(falsa))
    linha = await db.get(AtendimentoReclamacao, linha_id)
    assert linha.conversa_id == chat_id
    assert await foco_devolucao.conversas_da_devolucao_com_chat(db) == {conversa_id}
    chat = await db.get(AtendimentoConversa, chat_id)
    assert chat.pedido_marketplace == A


async def test_chat_ja_no_pedido_que_fecha_tambem_ganha_a_conversa(db, make_user):
    integ = await _loja(db, make_user)
    await _chat(db, integ, pedido=A, situacao="fechada")
    await _logistica(db)
    await _rastreio(db)
    await _ligar(db, fabrica_cliente=_fabrica(_Plataforma()))
    [conversa] = await _conversas(db, canal="reclamacao")
    assert conversa.comprador_id == COMPRADOR
    [linha] = await _linhas(db)
    assert linha.conversa_id == conversa.id


async def test_lista_nao_esconde_a_conversa_com_caso_aberto_ligado_a_ela(db, make_user):
    """O caso órfão fica na conversa dele: escondê-la o deixaria sem lugar na tela."""
    integ = await _loja(db, make_user)
    await _chat(db, integ)
    devolucao = await _devolucao_na_caixa(db, integ, "COM-LINHA", COMPRADOR)
    assert await foco_devolucao.conversas_da_devolucao_com_chat(db) == {devolucao.id}
    linha = await _linha(db, devolucao)
    assert await foco_devolucao.conversas_da_devolucao_com_chat(db) == frozenset()
    linha.encerrada_em = T0
    await db.commit()
    assert await foco_devolucao.conversas_da_devolucao_com_chat(db) == {devolucao.id}


async def test_caso_encerrado_com_conversa_escondida_passa_para_o_chat(db, make_user):
    """Encerrado sem conversa não vai para o chat; com a conversa que a lista esconde, vai:
    o histórico precisa de um caminho."""
    integ = await _loja(db, make_user)
    await _logistica(db)
    await _rastreio(db)
    falsa = _Plataforma()
    falsa.comprador = {"buyer_user_id": int(COMPRADOR), "buyer_username": "comprador_kfa"}
    await _ligar(db, fabrica_cliente=_fabrica(falsa))
    [conversa] = await _conversas(db, canal="reclamacao")
    conversa_id = conversa.id
    assert conversa.comprador_id == COMPRADOR
    await _mudar(db, A, "CANCELLED", T0 + timedelta(hours=1))
    await _ligar(db, T0 + timedelta(hours=1), fabrica_cliente=_fabrica(falsa))

    await db.refresh(integ)
    chat = await _chat(db, integ)
    chat_id = chat.id
    await _no_indice(db, integ)
    await _ligar(db, T0 + timedelta(hours=2), fabrica_cliente=_fabrica(falsa))
    [linha] = await _linhas(db)
    assert linha.conversa_id == chat_id and linha.encerrada_em is not None
    chat = await db.get(AtendimentoConversa, chat_id)
    assert chat.pedido_marketplace == B  # encerrado não prende
    assert await foco_devolucao.conversas_da_devolucao_com_chat(db) == {conversa_id}


async def test_conversa_sem_comprador_ganha_o_do_chat_e_sai_da_lista(db, make_user):
    integ = await _loja(db, make_user)
    await _logistica(db)
    await _rastreio(db)
    falsa = _Plataforma()
    falsa.comprador = {}
    await _ligar(db, fabrica_cliente=_fabrica(falsa))
    [conversa] = await _conversas(db, canal="reclamacao")
    conversa_id = conversa.id
    assert conversa.comprador_id is None

    await db.refresh(integ)
    chat = await _chat(db, integ)
    chat_id = chat.id
    await _no_indice(db, integ)
    await _ligar(db, T0 + timedelta(minutes=10), fabrica_cliente=_fabrica(falsa))
    conversa = await db.get(AtendimentoConversa, conversa_id)
    assert conversa.comprador_id == COMPRADOR
    [linha] = await _linhas(db)
    assert linha.conversa_id == chat_id
    assert await foco_devolucao.conversas_da_devolucao_com_chat(db) == {conversa_id}


async def test_prazo_vencido_nao_ganha_do_prazo_que_ainda_corre(db, make_user):
    """O prazo congelado de um caso que o sync não relê não tira o chat do que importa."""
    integ = await _loja(db, make_user)
    chat = await _chat(db, integ)
    await _linha(db, chat, A, prazo=T0 - timedelta(days=3))
    await _linha(db, chat, C, prazo=T0 + timedelta(days=2))
    assert await foco_devolucao.pedido_em_foco(db, chat) == C


async def test_citado_novo_trava_e_rele_a_linha_e_ocupada_nao_grava(db, make_user, monkeypatch):
    """O citado regrava `dados`: só com a linha travada. Ocupada: a próxima leitura grava."""
    integ = await _loja(db, make_user)
    chat = await _chat(db, integ, pedido=A)
    await _linha(db, chat)
    pedidas: list[bool] = []

    async def ocupada(_session, _conversa):
        pedidas.append(True)
        return False

    monkeypatch.setattr(gravar, "_travar_se_livre", ocupada)
    atual = await _ler(db, chat, integ, B)
    assert pedidas == [True]
    assert atual.pedido_marketplace == A and "pedido_citado" not in atual.dados

    # Sem mudança no citado, não pede a trava.
    monkeypatch.undo()
    monkeypatch.setattr(foco_devolucao, "_agora", lambda: T0 + timedelta(hours=1))
    atual = await _ler(db, atual, integ, B)
    assert atual.dados["pedido_citado"] == B
    pedidas.clear()
    monkeypatch.setattr(gravar, "_travar_se_livre", ocupada)
    atual = await _ler(db, atual, integ, B)
    assert pedidas == []


async def test_busca_pelo_numero_da_devolucao_acha_o_chat(client, db, make_user, pessoa):
    integ = await _loja(db, make_user)
    chat = await _chat(db, integ)
    await _linha(db, chat, externo="2611030A89AAMVT")
    r = await client.get(f"{URL}/conversas", params={"q": "2611030A89AAMVT"})
    assert _ids_da_lista(r) == {str(chat.id)}
    r = await client.get(f"{URL}/conversas", params={"q": "2611030A89"})
    assert _ids_da_lista(r) == set()


@pytest.mark.parametrize(
    "canal,dados,bloco",
    [
        ("chat", {}, True),
        ("reclamacao", {}, True),
        ("avaliacao", {}, False),
        ("chat", {"afiliado": True}, False),
    ],
)
async def test_bloco_do_pedido_so_no_chat_e_na_conversa_da_devolucao(
    client, db, make_user, pessoa, canal, dados, bloco
):
    integ = await _loja(db, make_user)
    conversa = AtendimentoConversa(
        integration_id=integ.id,
        plataforma="shopee",
        canal=canal,
        externo_id="c-1",
        comprador_id=COMPRADOR,
        pedido_marketplace=A,
        situacao="aberta",
        dados=dados,
    )
    db.add(conversa)
    await db.commit()
    await _linha(db, conversa)
    r = await client.get(f"{URL}/conversas/{conversa.id}/reclamacoes")
    assert r.status_code == 200, r.text
    assert [i["do_pedido"] for i in r.json()["itens"]] == [bloco]


def test_citou_outro_pedido():
    assert not foco_devolucao.citou_outro_pedido(SimpleNamespace(pedido_marketplace="A", dados={}))
    com_b = SimpleNamespace(pedido_marketplace="A", dados={"pedido_citado": "B"})
    assert foco_devolucao.citou_outro_pedido(com_b)
    com_a = SimpleNamespace(pedido_marketplace="A", dados={"pedido_citado": "A"})
    assert not foco_devolucao.citou_outro_pedido(com_a)
    vazia = SimpleNamespace(pedido_marketplace=None, dados=None)
    assert not foco_devolucao.citou_outro_pedido(vazia)
