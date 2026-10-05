"""O motor das mensagens automáticas, com banco (05/10/2026).

Eduardo, 05/10/2026: recriar no DaVinci as automações do Duoke COMEÇANDO EM
MODO SECO — o DaVinci registra o que mandaria, para quem e quando, e NÃO
envia nada (docs/atendimento-automacoes.md). Aqui, ponta a ponta:

- cada FONTE de gatilho: mensagem do comprador, pedido pago no Bling e a
  varredura da Logística (entregue com o horário das 9h às 20h, pós-conclusão);
- UMA VEZ só (a rodada repetida não duplica), validade (`atrasado`), teto;
- SIMULAR NUNCA CHAMA A PLATAFORMA: com as chaves ligadas ou desligadas e a
  regra em `enviar` sem a chave, o adaptador, o cliente, o
  `enviar_automatica` e a própria rede (httpx) levantam se chamados — a
  rodada termina e `atendimento_mensagens` não ganha linha nenhuma;
- o modo ENVIAR (só com as duas chaves e a regra em `enviar`): manda uma vez,
  `enviando` preso vira `revisar` e nunca retenta, o Duoke que mandou depois
  do gatilho segura o envio e dispara o disjuntor;
- o COMPARADOR: mandou / não mandou (só com a leitura da loja em dia), só
  Duoke (e o menu de fim de sessão), o cartão do pedido do Duoke e o alerta
  de "mandaria para quem devolveu";
- a correção do `ia._humano_respondeu_recente` e a régua da fila com a
  mensagem do motor (`davinci_auto`);
- as ROTAS da aba: lista, registro sem texto, PATCH (enviar recusado sem a
  chave, texto inválido, rearmar na troca) e o "Simular nas lojas do Duoke"
  que nunca mexe em regra em `enviar`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, time, timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.historico import sql as hsql
from app.models import (
    AtendimentoAutomacaoRegistro,
    AtendimentoAutomacaoRegra,
    AtendimentoAvaliacaoLoja,
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoReclamacao,
    BlingOrder,
    Company,
    Integration,
    IntegrationPlatform,
    Logistica,
    Marketplace,
    Store,
    StoreStatus,
    User,
)
from app.routers import atendimento as rota
from app.routers import atendimento_automacoes as rota_auto
from app.security.cipher import encrypt_json
from app.services.atendimento import automacoes, automacoes_comparar, clientes, enviar, gravar, ia
from app.services.atendimento import automacoes_catalogo as cat
from app.services.atendimento import shopee as shopee_atd
from app.services.atendimento.constantes import ResultadoEnvio
from app.services.marketplaces.shopee import ShopeeClient

# 12h00 em São Paulo, minuto 0 (a rodada lê a Logística).
T = datetime(2026, 10, 5, 15, 0, tzinfo=UTC)
DESDE = T - timedelta(days=2)
MENU = (
    "Olá, por favor selecione sua dúvida e logo um dos nossos consultores irá atendê-lo!\n\n"
    "1 - Previsão de entrega / envio\n6 - Falar com Atendente"
)
SN = "251005ABCDEFGH"
PODE_TUDO = {"atendimento": {"view": True, "edit": True}}


# ── falsos ────────────────────────────────────────────────────────────────


class RedisFalso:
    def __init__(self) -> None:
        self.dados: dict[str, Any] = {}

    async def get(self, chave):
        return self.dados.get(chave)

    async def set(self, chave, valor, *, nx=False, ex=None):
        if nx and chave in self.dados:
            return None
        self.dados[chave] = valor
        return True

    async def delete(self, chave):
        self.dados.pop(chave, None)

    async def eval(self, _script, _n, chave, token):
        if self.dados.get(chave) == token:
            del self.dados[chave]
            return 1
        return 0


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


@pytest.fixture
def proibido(monkeypatch) -> list[str]:
    """Tudo o que sai para a plataforma LEVANTA — e anota quem foi chamado."""
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
    monkeypatch.setattr(httpx.AsyncClient, "send", _aboom("httpx"))
    return chamadas


# ── fábrica ───────────────────────────────────────────────────────────────


async def _dono(db: AsyncSession) -> User:
    from tests.conftest import _make_user

    return await _make_user(db)


async def _loja(
    db: AsyncSession,
    dono: User,
    nome: str = "barbosa",
    plataforma: str = "shopee",
    *,
    ultimo_ok: datetime | None = None,
    status: str = "ok",
) -> tuple[Integration, AtendimentoCanal]:
    plataformas = {
        "shopee": (IntegrationPlatform.SHOPEE, Marketplace.SHOPEE, "chat"),
        "tiktok": (IntegrationPlatform.TIKTOK, Marketplace.TIKTOK, "chat"),
        "ml": (IntegrationPlatform.ML, Marketplace.ML, "pos_venda"),
    }
    plat, mkt, canal_nome = plataformas[plataforma]
    empresa = Company(razao_social=f"{nome} ltda", apelido=f"{nome}-{uuid4().hex[:4]}")
    db.add(empresa)
    await db.flush()
    integ = Integration(
        user_id=dono.id, platform=plat, name=nome, credentials=encrypt_json({"access_token": "t"})
    )
    db.add(integ)
    await db.flush()
    db.add(
        Store(
            company_id=empresa.id,
            marketplace=mkt,
            status=StoreStatus.ACTIVE,
            integration_id=integ.id,
        )
    )
    canal = AtendimentoCanal(
        integration_id=integ.id,
        plataforma=plataforma,
        canal=canal_nome,
        modo="observar",
        status=status,
        ultimo_ok_em=ultimo_ok or T + timedelta(days=3),
    )
    db.add(canal)
    await db.commit()
    return integ, canal


async def _regra(
    db: AsyncSession, integ: Integration, codigo: str, modo: str = "simular", **campos
) -> AtendimentoAutomacaoRegra:
    aut = cat.CATALOGO[codigo]
    semente = cat.regra_semente(aut, integ.name)
    for campo in ("janela_inicio", "janela_fim", "atraso_min", "partes", "condicoes"):
        if campo in campos:
            semente[campo] = campos.pop(campo)
    r = AtendimentoAutomacaoRegra(
        automacao=codigo,
        integration_id=integ.id,
        plataforma=aut.plataforma,
        modo=modo,
        ligada_desde=campos.pop("ligada_desde", DESDE),
        partes=semente["partes"],
        atraso_min=semente["atraso_min"],
        janela_inicio=semente["janela_inicio"],
        janela_fim=semente["janela_fim"],
        condicoes=semente["condicoes"],
        **campos,
    )
    db.add(r)
    await db.commit()
    return r


async def _conversa(
    db: AsyncSession,
    integ: Integration,
    canal: AtendimentoCanal,
    *,
    comprador: str = "77",
    nome: str = "maria.silva",
    pedido: str | None = None,
    etiqueta: str | None = None,
) -> AtendimentoConversa:
    c = AtendimentoConversa(
        canal_id=canal.id,
        integration_id=integ.id,
        plataforma=canal.plataforma,
        canal=canal.canal,
        externo_id=uuid4().hex,
        comprador_id=comprador,
        comprador_nome=nome,
        pedido_marketplace=pedido,
        etiqueta=etiqueta,
        ultima_do_cliente_em=T - timedelta(minutes=3),
    )
    db.add(c)
    await db.commit()
    return c


async def _msg(
    db: AsyncSession,
    conversa: AtendimentoConversa,
    *,
    autor: str = "cliente",
    texto: str | None = "tem na cor azul?",
    em: datetime,
    visto: datetime | None = None,
    origem: str | None = None,
    payload: dict | None = None,
) -> AtendimentoMensagem:
    m = AtendimentoMensagem(
        conversa_id=conversa.id,
        externo_id=uuid4().hex,
        autor=autor,
        origem=origem or {"cliente": "cliente", "loja": "externo", "sistema": "sistema"}[autor],
        tipo="texto",
        texto=texto,
        enviada_em=em,
        status="recebida" if autor == "cliente" else "enviada",
        payload=payload or {},
        created_at=visto or em + timedelta(seconds=60),
    )
    db.add(m)
    await db.commit()
    return m


async def _rodada(agora: datetime = T, motor_desde: datetime = DESDE) -> dict:
    return await automacoes.rodada(agora=agora, motor_desde=motor_desde)


async def _linhas(db: AsyncSession, **filtro) -> list[AtendimentoAutomacaoRegistro]:
    q = select(AtendimentoAutomacaoRegistro).execution_options(populate_existing=True)
    for k, v in filtro.items():
        q = q.where(getattr(AtendimentoAutomacaoRegistro, k) == v)
    return list((await db.execute(q.order_by(AtendimentoAutomacaoRegistro.devido_em))).scalars())


async def _n_mensagens(db: AsyncSession) -> int:
    return int(await db.scalar(select(func.count()).select_from(AtendimentoMensagem)))


async def _agendada(
    db: AsyncSession,
    regra: AtendimentoAutomacaoRegra,
    *,
    evento: datetime,
    devido: datetime,
    pedido: str | None = None,
    conversa: AtendimentoConversa | None = None,
    chave: str | None = None,
    **campos,
) -> AtendimentoAutomacaoRegistro:
    """Uma linha do registro já descoberta (o gatilho sem passar pela descoberta)."""
    aut = cat.CATALOGO[regra.automacao]
    x = AtendimentoAutomacaoRegistro(
        automacao=regra.automacao,
        regra_id=regra.id,
        regra_versao=regra.versao,
        integration_id=regra.integration_id,
        plataforma=aut.plataforma,
        alvo=aut.alvo,
        chave=chave or (f"pedido:{pedido}" if pedido else f"k:{uuid4().hex}"),
        pedido=pedido,
        conversa_id=conversa.id if conversa is not None else None,
        evento_em=evento,
        visto_em=evento,
        devido_em=devido,
        estado=campos.pop("estado", "agendado"),
        duoke=campos.pop("duoke", "pendente"),
        **campos,
    )
    db.add(x)
    await db.commit()
    return x


def _logistica(sn: str, status: str, em: datetime, conta: str = "barbosa") -> Logistica:
    return Logistica(
        pedido_marketplace=sn,
        plataforma="shopee",
        conta=conta,
        data=em.date(),
        meli_status={"order_status": status},
        status_datas={"order_status": {"em": em.isoformat()}},
        status_lido_em=em,
    )


# O cartão do pedido que o Duoke manda junto com o texto da campanha.
def _cartao(sn: str) -> dict:
    return {"source": "openapi", "message_type": "order", "content": {"order_sn": sn}}


ENTREGUE_DUOKE = "Oi! Tudo bem? 😊 Confirmamos a entrega do seu pedido! Por se tratar de um produto"
POS_DUOKE = "Oi! Só passando para saber se está tudo certo com o seu produto. Se sim e puder"
RECEBIDO_DUOKE = "Oi! Recebemos seu pedido e já estamos preparando pra envio."


# ── Fontes, uma vez só, validade ──────────────────────────────────────────


async def test_mensagem_do_comprador_simula_o_menu_uma_vez_e_nada_sai(db, proibido):
    dono = await _dono(db)
    integ, canal = await _loja(db, dono)
    await _regra(db, integ, "shopee_menu")
    conversa = await _conversa(db, integ, canal)
    gatilho = await _msg(db, conversa, em=T - timedelta(minutes=3))
    antes = await _n_mensagens(db)

    resumo = await _rodada()
    assert resumo["mensagens"] == 1
    [linha] = await _linhas(db, automacao="shopee_menu")
    assert linha.estado == "simulado" and linha.modo == "simular" and linha.motivo is None
    assert linha.chave == f"conversa:{conversa.id}:msg:{gatilho.id}"
    assert linha.devido_em == T - timedelta(minutes=2)
    assert linha.gatilho_mensagem_id == gatilho.id and linha.duoke == "pendente"
    assert set(resumo["ms"]) >= {"mensagens", "pedidos", "decidir", "comparar"}

    # A rodada seguinte não duplica (chave única + "já mandado" na sessão).
    await _msg(db, conversa, em=T + timedelta(minutes=1))
    await _rodada(T + timedelta(minutes=2))
    assert len(await _linhas(db, automacao="shopee_menu")) == 1
    assert await _n_mensagens(db) == antes + 1  # só a do comprador que o teste pôs
    assert proibido == []


async def test_regra_desligada_e_loja_sem_regra_nao_simulam(db, proibido):
    dono = await _dono(db)
    integ, canal = await _loja(db, dono)
    await _regra(db, integ, "shopee_menu", modo="desligado")
    outra, canal2 = await _loja(db, dono, "aguiar")
    for i, c in ((integ, canal), (outra, canal2)):
        conversa = await _conversa(db, i, c)
        await _msg(db, conversa, em=T - timedelta(minutes=3))
    await _rodada()
    assert await _linhas(db) == []


async def test_pedido_pago_no_bling_vira_pedido_recebido_5_min_depois(db, proibido):
    dono = await _dono(db)
    integ, _ = await _loja(db, dono)
    await _regra(db, integ, "shopee_pedido_recebido")
    loja = (await db.execute(select(Store).where(Store.integration_id == integ.id))).scalar_one()
    for item, sn, situacao in ((0, SN, "6"), (1, SN, "6"), (0, "251005CANCELAD", "12")):
        db.add(
            BlingOrder(
                numero=f"9{item}{sn[-3:]}",
                numeroloja=sn,
                situacao=situacao,
                store_id=loja.id,
                item_index=item,
                data=T,
                created_at=T - timedelta(minutes=8),
            )
        )
    # Pedido de outra plataforma (não casa o padrão da Shopee): nada.
    db.add(
        BlingOrder(
            numero="777",
            numeroloja="2000012345678901",
            situacao="6",
            store_id=loja.id,
            item_index=0,
            data=T,
            created_at=T - timedelta(minutes=8),
        )
    )
    await db.commit()
    await _rodada()
    linhas = {x.pedido: x for x in await _linhas(db, automacao="shopee_pedido_recebido")}
    assert set(linhas) == {SN, "251005CANCELAD"}
    ok = linhas[SN]
    assert ok.chave == f"pedido:{SN}" and ok.alvo == "pedido"
    assert ok.evento_em == T - timedelta(minutes=8) and ok.devido_em == T - timedelta(minutes=3)
    assert ok.estado == "simulado"
    cancelado = linhas["251005CANCELAD"]
    assert (cancelado.estado, cancelado.motivo) == ("pulado", "pedido_cancelado")


async def test_logistica_entregue_espera_as_9h_e_pos_conclusao_sem_avaliacao(db, proibido):
    # 21h00 de 05/10 em São Paulo (minuto 0: a rodada lê a Logística).
    agora = datetime(2026, 10, 6, 0, 0, tzinfo=UTC)
    dono = await _dono(db)
    integ, canal = await _loja(db, dono, "barbosa")
    await _regra(db, integ, "shopee_entregue")
    await _regra(db, integ, "shopee_pos_conclusao")

    def _logi(sn, status, em, retorno=None):
        meli = {"order_status": status}
        if retorno:
            meli["return_status"] = retorno
        return Logistica(
            pedido_marketplace=sn,
            plataforma="shopee",
            conta=" Barbosa ",
            data=agora.date(),
            meli_status=meli,
            status_datas={"order_status": {"em": em.isoformat()}},
            status_lido_em=agora - timedelta(minutes=20),
        )

    # Entregue às 21h de SP de 04/10 → saía às 9h de 05/10; a rodada é às 21h de
    # 05/10, FORA do horário: espera as 9h de 06/10 (ainda vale até as 20h).
    db.add(_logi("251004ENTREGUE", "TO_CONFIRM_RECEIVE", datetime(2026, 10, 5, 0, 0, tzinfo=UTC)))
    # Entregue às 20h40 de SP de 05/10 (fora da janela) → 06/10 às 9h (agendado).
    db.add(_logi("251005NOITE000", "TO_CONFIRM_RECEIVE", datetime(2026, 10, 5, 23, 40, tzinfo=UTC)))
    # Concluído há 5 h: o pós sai 4 h depois (simula); um avaliado; um devolvido.
    db.add(_logi("251001CONCLUID", "COMPLETED", agora - timedelta(hours=5)))
    db.add(_logi("251001AVALIADO", "COMPLETED", agora - timedelta(hours=5)))
    db.add(_logi("251001DEVOLVID", "COMPLETED", agora - timedelta(hours=5), retorno="ACCEPTED"))
    db.add(_logi("251001SEMNADA0", "COMPLETED", agora - timedelta(hours=5)))
    db.add(_logi("251001SEMCONVE", "COMPLETED", agora - timedelta(hours=5)))
    db.add(
        AtendimentoAvaliacaoLoja(
            integration_id=integ.id,
            plataforma="shopee",
            comentario_id="c1",
            pedido="251001AVALIADO",
            estrelas=5,
            criado_em=agora - timedelta(hours=2),
        )
    )
    # A devolução ENCERRADA também conta (o Duoke não manda para quem devolveu).
    db.add(
        AtendimentoReclamacao(
            integration_id=integ.id,
            plataforma="shopee",
            externo_id="r1",
            tipo="devolucao",
            status="CLOSED",
            pedido_marketplace="251001CONCLUID",
            encerrada_em=agora - timedelta(hours=1),
        )
    )
    await db.commit()
    # O pós só vai a quem tem conversa com a loja (medido: o Duoke não manda sem):
    # a conversa achada pelo cartão do pedido da campanha.
    conversa = await _conversa(db, integ, canal, comprador="31")
    await _msg(
        db,
        conversa,
        autor="loja",
        texto=None,
        em=agora - timedelta(days=9),
        payload={
            "source": "openapi",
            "message_type": "order",
            "content": {"order_sn": "251001SEMNADA0"},
        },
    )
    await _rodada(agora)
    entregue = {x.pedido: x for x in await _linhas(db, automacao="shopee_entregue")}
    assert entregue["251004ENTREGUE"].estado == "agendado"
    assert entregue["251004ENTREGUE"].devido_em == datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
    noite = entregue["251005NOITE000"]
    assert noite.estado == "agendado"
    assert noite.evento_em == datetime(2026, 10, 5, 23, 40, tzinfo=UTC)
    assert noite.devido_em == datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
    pos = {x.pedido: x for x in await _linhas(db, automacao="shopee_pos_conclusao")}
    assert pos["251001SEMNADA0"].estado == "simulado"
    assert pos["251001SEMNADA0"].conversa_id == conversa.id
    assert pos["251001SEMNADA0"].devido_em == agora - timedelta(hours=1)
    assert (pos["251001SEMCONVE"].estado, pos["251001SEMCONVE"].motivo) == (
        "pulado",
        "sem_conversa",
    )
    assert (pos["251001CONCLUID"].estado, pos["251001CONCLUID"].motivo) == ("pulado", "devolucao")
    assert (pos["251001AVALIADO"].estado, pos["251001AVALIADO"].motivo) == ("pulado", "avaliou")
    assert (pos["251001DEVOLVID"].estado, pos["251001DEVOLVID"].motivo) == ("pulado", "devolucao")

    # O entregue que concluiu antes da hora: `ja_concluido`.
    linha = (
        await db.execute(select(Logistica).where(Logistica.pedido_marketplace == "251005NOITE000"))
    ).scalar_one()
    linha.meli_status = {"order_status": "COMPLETED"}
    await db.commit()
    await _rodada(datetime(2026, 10, 6, 12, 2, tzinfo=UTC))
    noite = (await _linhas(db, pedido="251005NOITE000", automacao="shopee_entregue"))[0]
    assert (noite.estado, noite.motivo) == ("pulado", "ja_concluido")
    # E o que esperou a abertura sai às 9h02 (no horário).
    cedo = (await _linhas(db, pedido="251004ENTREGUE", automacao="shopee_entregue"))[0]
    assert cedo.estado == "simulado"
    assert cedo.decidido_em == datetime(2026, 10, 6, 12, 2, tzinfo=UTC)


async def test_passou_da_validade_vira_atrasado_fora_da_conta(db, proibido):
    dono = await _dono(db)
    integ, canal = await _loja(db, dono)
    await _regra(db, integ, "shopee_menu")
    conversa = await _conversa(db, integ, canal)
    await _msg(db, conversa, em=T - timedelta(minutes=3))
    # A descoberta acha (lookback de 40 min), mas a decisão só roda 1 h depois.
    await automacoes.descobrir_mensagens(
        db, await automacoes.regras_por_chave(db), agora=T, motor_desde=DESDE
    )
    await db.commit()
    await automacoes.decidir_vencidas(db, agora=T + timedelta(hours=1))
    [linha] = await _linhas(db)
    assert (linha.estado, linha.motivo, linha.divergencia) == (
        "pulado",
        "atrasado",
        "motor_atrasado",
    )


async def test_teto_do_dia_no_modo_seco_vira_diferenca_combinada(db, proibido, _chaves):
    _chaves.atendimento_automacoes_teto_dia = 1
    dono = await _dono(db)
    integ, canal = await _loja(db, dono)
    await _regra(db, integ, "shopee_menu")
    for comprador in ("1", "2"):
        conversa = await _conversa(db, integ, canal, comprador=comprador)
        await _msg(db, conversa, em=T - timedelta(minutes=3))
    await _rodada()
    estados = sorted((x.estado, x.motivo, x.divergencia) for x in await _linhas(db))
    assert estados == [("pulado", "teto_dia", "teto_dia"), ("simulado", None, None)]


async def test_fila_de_uma_regra_nao_segura_as_outras(db, proibido, monkeypatch):
    monkeypatch.setattr(automacoes, "MAX_DECISOES", 3)
    monkeypatch.setattr(automacoes, "MAX_POR_REGRA", 2)
    dono = await _dono(db)
    a, _ = await _loja(db, dono, "barbosa")
    b, _ = await _loja(db, dono, "kfa")
    regra_a = await _regra(db, a, "shopee_menu")
    regra_b = await _regra(db, b, "shopee_menu")
    for i in range(4):  # as mais velhas, todas da mesma regra
        await _agendada(
            db, regra_a, evento=T - timedelta(minutes=20), devido=T - timedelta(minutes=19 - i)
        )
    nova = await _agendada(
        db, regra_b, evento=T - timedelta(minutes=3), devido=T - timedelta(minutes=2)
    )
    contagem = await automacoes.decidir_vencidas(db, agora=T)
    assert sum(contagem.values()) == 3
    linhas = await _linhas(db)
    assert {x.id: x.estado for x in linhas}[nova.id] != "agendado", "a outra loja entra na rodada"
    assert sum(x.estado == "agendado" for x in linhas) == 2


async def test_insert_em_lotes(db, monkeypatch):
    monkeypatch.setattr(automacoes, "LOTE_INSERT", 2)
    dono = await _dono(db)
    integ, _ = await _loja(db, dono)
    regra = await _regra(db, integ, "shopee_pedido_recebido")
    aut = cat.CATALOGO["shopee_pedido_recebido"]
    linhas = [
        automacoes._linha_registro(
            aut, regra, chave=f"pedido:{i}", agora=T, evento_em=T, devido_em=T, pedido=str(i)
        )
        for i in range(5)
    ]
    assert await automacoes._gravar_candidatos(db, linhas) == 5
    assert await automacoes._gravar_candidatos(db, linhas) == 0  # a chave única segura
    await db.commit()
    assert len(await _linhas(db)) == 5


async def test_limpeza_do_registro_guarda_90_dias_e_nunca_o_que_decide(db):
    dono = await _dono(db)
    integ, _ = await _loja(db, dono)
    regra = await _regra(db, integ, "shopee_menu")
    velho = T - timedelta(days=100)
    apagar = await _agendada(db, regra, evento=velho, devido=velho, estado="simulado")
    preso = await _agendada(db, regra, evento=velho, devido=velho)  # agendado: fica
    novo = await _agendada(db, regra, evento=T, devido=T, estado="simulado")
    assert await automacoes.limpar_registro(db, agora=T) == 1
    await db.commit()
    assert {x.id for x in await _linhas(db)} == {preso.id, novo.id}
    assert apagar.id not in {x.id for x in await _linhas(db)}


async def test_a_seguinte_nasce_no_horario_da_regra_dela(db, proibido):
    dono = await _dono(db)
    integ, _ = await _loja(db, dono)
    regra = await _regra(db, integ, "shopee_duvida_2h")
    await _regra(db, integ, "shopee_duvida_26h", janela_inicio=time(9), janela_fim=time(20))
    # O 2 h sai às 21h de SP; o 26 h seria às 21h do dia seguinte: anda para as 9h.
    devido = datetime(2026, 10, 6, 0, 0, tzinfo=UTC)
    await _agendada(db, regra, evento=devido - timedelta(hours=2), devido=devido)
    await automacoes.decidir_vencidas(db, agora=devido + timedelta(minutes=1))
    [seguinte] = await _linhas(db, automacao="shopee_duvida_26h")
    assert seguinte.devido_em == datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


# ── Simular NUNCA chama a plataforma ──────────────────────────────────────


@pytest.mark.parametrize(
    "envio_geral,envio_automacoes,modo",
    [
        (False, False, "simular"),
        (True, True, "simular"),
        (False, False, "enviar"),
        (True, False, "enviar"),
        (False, True, "enviar"),
    ],
    ids=[
        "tudo_desligado",
        "chaves_ligadas_regra_simular",
        "enviar_sem_chaves",
        "enviar_sem_a_chave_nova",
        "enviar_sem_o_envio_geral",
    ],
)
async def test_simular_nunca_chama_a_plataforma(
    db, proibido, _chaves, envio_geral, envio_automacoes, modo
):
    _chaves.atendimento_envio_ativo = envio_geral
    _chaves.atendimento_automacoes_envio = envio_automacoes
    dono = await _dono(db)
    integ, canal = await _loja(db, dono, "barbosa")
    for codigo in (
        "shopee_menu",
        "shopee_convite",
        "shopee_duvida_2h",
        "shopee_pedido_recebido",
        "shopee_pos_conclusao",
    ):
        await _regra(db, integ, codigo, modo=modo, enviar_desde=DESDE if modo == "enviar" else None)
    tt, tt_canal = await _loja(db, dono, "mini", "tiktok")
    await _regra(db, tt, "tiktok_aguarde", modo=modo)
    ml, ml_canal = await _loja(db, dono, "kfa", "ml")
    await _regra(db, ml, "ml_menu", modo=modo)
    for i, c in ((integ, canal), (tt, tt_canal), (ml, ml_canal)):
        conversa = await _conversa(db, i, c, pedido=None)
        await _msg(db, conversa, em=T - timedelta(minutes=15))
    loja = (await db.execute(select(Store).where(Store.integration_id == integ.id))).scalar_one()
    db.add(
        BlingOrder(
            numero="1",
            numeroloja=SN,
            situacao="6",
            store_id=loja.id,
            item_index=0,
            data=T,
            created_at=T - timedelta(minutes=8),
        )
    )
    await _conversa(db, integ, canal, comprador="88", pedido="251001CONCLUID")
    db.add(
        Logistica(
            pedido_marketplace="251001CONCLUID",
            plataforma="shopee",
            conta="barbosa",
            data=T.date(),
            meli_status={"order_status": "COMPLETED"},
            status_datas={"order_status": {"em": (T - timedelta(hours=5)).isoformat()}},
            status_lido_em=T - timedelta(minutes=20),
        )
    )
    await db.commit()
    antes = await _n_mensagens(db)

    await _rodada()
    await _rodada(T + timedelta(hours=3))  # o "ficou alguma dúvida" (2 h)

    assert proibido == []
    assert await _n_mensagens(db) == antes
    linhas = await _linhas(db)
    simuladas = {x.automacao for x in linhas if x.estado == "simulado"}
    assert {
        "shopee_menu",
        "shopee_convite",
        "shopee_pedido_recebido",
        "shopee_pos_conclusao",
        "tiktok_aguarde",
        "ml_menu",
        "shopee_duvida_2h",
    } <= simuladas
    assert not [x for x in linhas if x.estado in ("enviando", "enviado", "revisar", "falhou")]
    if modo == "enviar":
        # Regra em enviar sem as duas chaves: simulado, com o motivo que a tela pinta de vermelho.
        assert {x.motivo for x in linhas if x.estado == "simulado"} == {"envio_desligado"}
    # A 26 h só nasce com a regra dela ligada (aqui não está).
    assert not [x for x in linhas if x.automacao == "shopee_duvida_26h"]


# ── Modo enviar (as duas chaves + a regra em enviar) ──────────────────────


class AdaptadorFalso:
    def __init__(self, resultado: ResultadoEnvio | None = None) -> None:
        self.envios: list[str] = []
        self.resultado = resultado or ResultadoEnvio(ok=True, externo_id=None)

    async def enviar_texto(self, session, conversa, integration, cliente, texto):
        self.envios.append(texto)
        return self.resultado


class AdaptadorComAutoReply(AdaptadorFalso):
    """O adaptador que manda cada parte e a resposta automática, como o da Shopee
    (`enviar_parte(auto_reply=…)`): o texto da campanha em `auto_replies`, o texto
    normal em `envios`, o cartão e a figurinha em `partes`."""

    def __init__(self) -> None:
        super().__init__()
        self.auto_replies: list[str] = []
        self.partes: list[str] = []

    async def enviar_parte(
        self,
        session,
        conversa,
        integration,
        cliente,
        parte,
        *,
        to_id=None,
        pedido=None,
        auto_reply=False,
    ):
        if parte.get("tipo") != "texto":
            self.partes.append(parte["tipo"])
        elif auto_reply:
            self.auto_replies.append(parte["texto"])
        else:
            self.envios.append(parte["texto"])
        return self.resultado


@pytest.fixture
def auto_reply_ligado(monkeypatch, _chaves) -> AdaptadorComAutoReply:
    """Tudo ligado E o envio por resposta automática no adaptador (o check de verdade,
    `enviar.auto_reply_no_adaptador`, olha a `enviar_parte` deste adaptador)."""
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
def envio_ligado(monkeypatch, _chaves) -> AdaptadorFalso:
    _chaves.atendimento_envio_ativo = True
    _chaves.atendimento_automacoes_envio = True
    falso = AdaptadorFalso()
    monkeypatch.setattr(enviar, "adaptador", lambda _plataforma: falso)

    async def _cliente(_integ):
        return object()

    monkeypatch.setattr(clientes, "cliente_da_integracao", _cliente)
    return falso


async def test_enviar_manda_uma_vez_marcado_e_nunca_de_novo(db, envio_ligado):
    dono = await _dono(db)
    integ, canal = await _loja(db, dono)
    await _regra(db, integ, "shopee_menu", modo="enviar", enviar_desde=DESDE)
    conversa = await _conversa(db, integ, canal)
    await _msg(db, conversa, em=T - timedelta(minutes=5))
    await _rodada()
    [linha] = await _linhas(db)
    assert (linha.estado, linha.modo, linha.tentativas) == ("enviado", "enviar", 1)
    assert len(envio_ligado.envios) == 1 and envio_ligado.envios[0].startswith("Olá, por favor")
    [nossa] = (
        (
            await db.execute(
                select(AtendimentoMensagem).where(AtendimentoMensagem.origem == "davinci_auto")
            )
        )
        .scalars()
        .all()
    )
    assert nossa.payload["automacao"]["codigo"] == "shopee_menu"
    assert nossa.payload["automacao"]["registro_id"] == str(linha.id)
    assert linha.mensagem_ids == [str(nossa.id)]
    # A conversa continua esperando a pessoa (a automática não fecha a vez).
    await db.refresh(conversa)
    assert conversa.aguardando_resposta is True
    await _rodada(T + timedelta(minutes=2))
    await _rodada(T + timedelta(minutes=4))
    assert len(envio_ligado.envios) == 1


async def test_enviar_espera_a_leitura_e_nao_manda_se_o_duoke_mandou(db, envio_ligado):
    dono = await _dono(db)
    integ, canal = await _loja(db, dono, ultimo_ok=T - timedelta(minutes=10))
    regra = await _regra(
        db, integ, "shopee_menu", modo="enviar", enviar_desde=T - timedelta(hours=1)
    )
    conversa = await _conversa(db, integ, canal)
    await _msg(db, conversa, em=T - timedelta(minutes=5))
    # A leitura da loja parou antes do `devido_em` + a espera: não decide ainda.
    await _rodada()
    [linha] = await _linhas(db)
    assert linha.estado == "agendado" and envio_ligado.envios == []
    # O Duoke ainda mandou (depois da troca) e a leitura chegou: pula e dispara o disjuntor.
    await _msg(db, conversa, autor="loja", texto=MENU, em=T - timedelta(minutes=4))
    canal_db = await db.get(AtendimentoCanal, canal.id)
    canal_db.ultimo_ok_em = T + timedelta(minutes=1)
    await db.commit()
    await _rodada(T + timedelta(minutes=2))
    [linha] = await _linhas(db)
    assert (linha.estado, linha.motivo) == ("pulado", "duoke_mandou")
    assert envio_ligado.envios == []
    await db.refresh(regra)
    assert regra.modo == "simular" and regra.disjuntor_motivo == "duoke_ainda_ligado"


async def test_enviando_preso_vira_revisar_e_nunca_retenta(db, envio_ligado):
    dono = await _dono(db)
    integ, canal = await _loja(db, dono)
    regra = await _regra(db, integ, "shopee_menu", modo="enviar", enviar_desde=DESDE)
    conversa = await _conversa(db, integ, canal)
    db.add(
        AtendimentoAutomacaoRegistro(
            automacao="shopee_menu",
            regra_id=regra.id,
            integration_id=integ.id,
            plataforma="shopee",
            alvo="conversa",
            chave="conversa:x:msg:y",
            conversa_id=conversa.id,
            evento_em=T - timedelta(minutes=40),
            devido_em=T - timedelta(minutes=39),
            estado="enviando",
            tentativas=1,
            duoke="pendente",
            updated_at=T - timedelta(minutes=20),
        )
    )
    await db.commit()
    await _rodada()
    [linha] = await _linhas(db)
    assert (linha.estado, linha.erro) == ("revisar", "envio_interrompido")
    await _rodada(T + timedelta(minutes=2))
    assert envio_ligado.envios == []


async def test_campanha_da_shopee_nao_sai_sem_o_auto_reply(db, envio_ligado):
    dono = await _dono(db)
    integ, canal = await _loja(db, dono)
    await _regra(db, integ, "shopee_convite", modo="enviar", enviar_desde=DESDE)
    conversa = await _conversa(db, integ, canal)
    await _msg(db, conversa, em=T - timedelta(minutes=5))
    await _rodada()
    [linha] = await _linhas(db)
    assert (linha.estado, linha.motivo) == ("pulado", "campanha_sem_auto_reply")
    assert envio_ligado.envios == []


async def test_campanha_nao_sai_com_a_chave_ligada_sem_o_envio_por_auto_reply(
    db, envio_ligado, _chaves, client, pessoa
):
    """A chave do auto_reply sozinha NÃO libera a campanha: sem o envio por resposta
    automática no adaptador (`enviar_parte(auto_reply=…)`), ela sairia como mensagem
    normal. Aqui o adaptador (falso) só tem o `enviar_texto`."""
    _chaves.atendimento_automacoes_shopee_auto_reply = True
    assert enviar.auto_reply_no_adaptador("shopee") is False  # o adaptador sem o envio
    integ, canal = await _loja(db, pessoa)
    await _regra(db, integ, "shopee_convite", modo="enviar", enviar_desde=DESDE)
    conversa = await _conversa(db, integ, canal)
    await _msg(db, conversa, em=T - timedelta(minutes=5))
    await _rodada()
    [linha] = await _linhas(db)
    assert (linha.estado, linha.motivo) == ("pulado", "campanha_sem_auto_reply")
    assert envio_ligado.envios == []
    with pytest.raises(enviar.EnvioRecusado) as e:
        await enviar.enviar_automatica(
            db,
            conversa,
            codigo="shopee_convite",
            texto="Já segue?",
            registro_id=None,
            regra_versao=1,
        )
    assert e.value.code == "campanha_sem_auto_reply"
    assert envio_ligado.envios == []
    # A tela: a chave ligada, o adaptador sem o envio — Enviar travado na campanha.
    r = await client.get("/api/atendimento/automacoes", params={"plataforma": "shopee"})
    corpo = r.json()
    assert corpo["chaves"]["shopee_auto_reply"] is True
    assert corpo["chaves"]["shopee_auto_reply_adaptador"] is False
    convite = next(a for a in corpo["automacoes"] if a["codigo"] == "shopee_convite")
    assert "campanha_sem_auto_reply" in convite["lojas"][0]["por_que_nao_enviar"]
    menu = next(a for a in corpo["automacoes"] if a["codigo"] == "shopee_menu")
    assert "campanha_sem_auto_reply" not in menu["lojas"][0]["por_que_nao_enviar"]


async def test_campanha_nao_sai_com_o_adaptador_de_verdade_e_a_chave_desligada(
    db, monkeypatch, _chaves, client, pessoa
):
    """O adaptador da Shopee de verdade JÁ manda como resposta automática
    (`enviar_parte(auto_reply=…)`, 05/10): com a chave do auto_reply desligada (a
    produção), a campanha não sai — nem com conversa, nem sem — e nada vai à
    plataforma."""
    chamadas: list[str] = []

    def _aboom(nome):
        async def f(*_a, **_k):
            chamadas.append(nome)
            raise AssertionError(f"{nome} chamado com a chave do auto_reply desligada")

        return f

    monkeypatch.setattr(clientes, "cliente_da_integracao", _aboom("cliente_da_integracao"))
    monkeypatch.setattr(ShopeeClient, "chat_send_message", _aboom("chat_send_message"))
    monkeypatch.setattr(
        ShopeeClient, "chat_send_autoreply_message", _aboom("chat_send_autoreply_message")
    )
    rede = httpx.AsyncClient.send
    monkeypatch.setattr(httpx.AsyncClient, "send", _aboom("httpx"))
    _chaves.atendimento_envio_ativo = True
    _chaves.atendimento_automacoes_envio = True
    assert _chaves.atendimento_automacoes_shopee_auto_reply is False
    assert enviar.adaptador("shopee") is shopee_atd  # o adaptador de verdade
    assert enviar.auto_reply_no_adaptador("shopee") is True
    assert enviar.campanha_por_auto_reply() is False
    integ, canal = await _loja(db, pessoa)
    await _regra(db, integ, "shopee_convite", modo="enviar", enviar_desde=DESDE)
    await _regra(db, integ, "shopee_pedido_recebido", modo="enviar", enviar_desde=DESDE)
    conversa = await _conversa(db, integ, canal)
    await _msg(db, conversa, em=T - timedelta(minutes=5))
    await _rodada()
    [linha] = await _linhas(db, automacao="shopee_convite")
    assert (linha.estado, linha.motivo) == ("pulado", "campanha_sem_auto_reply")
    with pytest.raises(enviar.EnvioRecusado) as e:
        await enviar.enviar_automatica(
            db,
            conversa,
            codigo="shopee_convite",
            texto="Já segue?",
            registro_id=None,
            regra_versao=1,
        )
    assert e.value.code == "campanha_sem_auto_reply"
    with pytest.raises(enviar.EnvioRecusado) as e:
        await enviar.enviar_automatica_sem_conversa(
            db,
            integration_id=integ.id,
            codigo="shopee_pedido_recebido",
            to_id="555",
            pedido=SN,
            parte={"tipo": "texto", "texto": "Recebemos o seu pedido!"},
            registro_id=None,
            regra_versao=1,
        )
    assert e.value.code == "campanha_sem_auto_reply"
    assert chamadas == []
    assert (
        await db.scalar(
            select(func.count())
            .select_from(AtendimentoMensagem)
            .where(AtendimentoMensagem.origem == "davinci_auto")
        )
        == 0
    )
    # A tela: o adaptador pronto, a chave desligada — Enviar travado na campanha.
    monkeypatch.setattr(httpx.AsyncClient, "send", rede)  # o cliente de teste da API
    r = await client.get("/api/atendimento/automacoes", params={"plataforma": "shopee"})
    corpo = r.json()
    assert corpo["chaves"]["shopee_auto_reply"] is False
    assert corpo["chaves"]["shopee_auto_reply_adaptador"] is True
    convite = next(a for a in corpo["automacoes"] if a["codigo"] == "shopee_convite")
    assert "campanha_sem_auto_reply" in convite["lojas"][0]["por_que_nao_enviar"]


async def test_auto_reply_sem_o_envio_por_ele_nunca_sai_como_mensagem_normal(
    db, envio_ligado, pessoa
):
    """A última trava (`_chamar_plataforma_automatica`): pedido o `auto_reply` a um
    adaptador sem a `enviar_parte` que o aceite, nada sai — `sem_auto_reply`, e o
    `enviar_texto` nunca é chamado."""
    integ, canal = await _loja(db, pessoa)
    conversa = await _conversa(db, integ, canal)
    r = await enviar._chamar_plataforma_automatica(
        db,
        conversa,
        integ,
        "shopee",
        {"tipo": "texto", "texto": "Já segue?"},
        auto_reply=True,
    )
    assert (r.ok, r.erro) == (False, "sem_auto_reply")
    assert envio_ligado.envios == []


async def test_campanha_com_o_auto_reply_sai_por_ele_e_nunca_como_mensagem_normal(
    db, auto_reply_ligado
):
    dono = await _dono(db)
    integ, canal = await _loja(db, dono)
    regra = await _regra(db, integ, "shopee_pos_conclusao", modo="enviar", enviar_desde=DESDE)
    await _conversa(db, integ, canal, pedido=SN)
    db.add(_logistica(SN, "COMPLETED", T - timedelta(hours=4, minutes=10)))
    await db.commit()
    await _agendada(
        db,
        regra,
        pedido=SN,
        evento=T - timedelta(hours=4, minutes=10),
        devido=T - timedelta(minutes=10),
    )
    await _rodada()
    [linha] = await _linhas(db)
    assert (linha.estado, linha.motivo) == ("enviado", None)
    assert len(auto_reply_ligado.auto_replies) == 1
    assert auto_reply_ligado.auto_replies[0].startswith("Oi, maria.silva! Só passando")
    assert auto_reply_ligado.envios == [], "a campanha nunca vai pelo enviar_texto"


@pytest.mark.parametrize("caso", ["entregue", "pedido_recebido", "pos"])
async def test_enviar_do_pedido_nao_manda_se_o_duoke_mandou_depois_do_gatilho(
    db, auto_reply_ligado, caso
):
    """Pedido recebido, entregue e pós: a mesma régua do comparador (o cartão do
    pedido do Duoke, ou a conversa do pedido) antes de mandar. A 1ª que acha o
    Duoke dispara o disjuntor NA HORA: o resto do lote já decide em simular."""
    dono = await _dono(db)
    integ, canal = await _loja(db, dono)
    agora = datetime(2026, 10, 6, 12, 2, tzinfo=UTC)  # 9h02 em São Paulo
    pedidos = [f"251006LOTE{i:04d}" for i in range(3)]
    if caso == "entregue":
        codigo, texto, evento, devido, duoke_em = (
            "shopee_entregue",
            ENTREGUE_DUOKE,
            datetime(2026, 10, 6, 0, 30, tzinfo=UTC),  # entregue às 21h30 de SP
            datetime(2026, 10, 6, 12, 0, tzinfo=UTC),  # o nosso: às 9h
            datetime(2026, 10, 6, 5, 0, tzinfo=UTC),  # o lote do Duoke: 2h da manhã
        )
        status = "TO_CONFIRM_RECEIVE"
    elif caso == "pedido_recebido":
        codigo, texto, evento, devido, duoke_em = (
            "shopee_pedido_recebido",
            RECEBIDO_DUOKE,
            agora - timedelta(minutes=10),
            agora - timedelta(minutes=5),
            agora - timedelta(minutes=5),  # o Duoke 5 min depois do Bling
        )
        status = None
    else:
        codigo, texto, evento, devido, duoke_em = (
            "shopee_pos_conclusao",
            POS_DUOKE,
            agora - timedelta(hours=4, minutes=7),
            agora - timedelta(minutes=7),
            agora - timedelta(minutes=6),
        )
        status = "COMPLETED"
    regra = await _regra(db, integ, codigo, modo="enviar", enviar_desde=agora - timedelta(hours=12))
    for i, sn in enumerate(pedidos):
        if status:
            db.add(_logistica(sn, status, evento))
        await db.commit()
        if caso == "pos":
            # Sem cartão: o pós do Duoke casa pela conversa do pedido.
            conversa = await _conversa(db, integ, canal, comprador=f"c{i}", pedido=sn)
        else:
            conversa = await _conversa(db, integ, canal, comprador=f"c{i}")
            await _msg(db, conversa, autor="loja", texto=None, em=duoke_em, payload=_cartao(sn))
        await _msg(db, conversa, autor="sistema", texto=texto, em=duoke_em + timedelta(seconds=1))
        await _agendada(db, regra, pedido=sn, evento=evento, devido=devido + timedelta(seconds=i))
    await _rodada(agora)
    linhas = sorted(await _linhas(db, automacao=codigo), key=lambda x: x.devido_em)
    assert (linhas[0].estado, linhas[0].motivo) == ("pulado", "duoke_mandou")
    # O disjuntor na hora: as outras duas do lote decidem em simular (nada sai).
    assert [x.estado for x in linhas[1:]] == ["simulado", "simulado"]
    assert auto_reply_ligado.envios == [] and auto_reply_ligado.auto_replies == []
    await db.refresh(regra)
    assert (regra.modo, regra.disjuntor_motivo) == ("simular", "duoke_ainda_ligado")


async def test_enviar_fora_do_horario_espera_a_abertura_e_o_rearme_tambem(db, auto_reply_ligado):
    """O horário vale na DECISÃO: o entregue rearmado às 22h não sai às 22h02 — espera
    as 9h (dentro da validade) e sai, pela resposta automática."""
    dono = await _dono(db)
    integ, canal = await _loja(db, dono)
    regra = await _regra(db, integ, "shopee_entregue")
    await _conversa(db, integ, canal, pedido=SN)
    evento = datetime(2026, 10, 5, 21, 0, tzinfo=UTC)  # entregue às 18h de SP
    db.add(_logistica(SN, "TO_CONFIRM_RECEIVE", evento))
    await db.commit()
    await _agendada(
        db,
        regra,
        pedido=SN,
        evento=evento,
        devido=evento,
        estado="simulado",
        duoke="nao_mandou",
        decidido_em=evento,
        modo="simular",
    )
    # A troca às 22h de SP: o PATCH põe em enviar e rearma.
    regra.modo = "enviar"
    regra.enviar_desde = datetime(2026, 10, 6, 1, 0, tzinfo=UTC)
    await db.commit()
    assert await rota_auto.rearmar(db, regra, agora=datetime(2026, 10, 6, 1, 0, tzinfo=UTC)) == 1
    await db.commit()
    await _rodada(datetime(2026, 10, 6, 1, 2, tzinfo=UTC))  # 22h02
    [linha] = await _linhas(db)
    assert linha.estado == "agendado" and linha.devido_em == datetime(
        2026, 10, 6, 12, 0, tzinfo=UTC
    )
    assert auto_reply_ligado.auto_replies == []
    await _rodada(datetime(2026, 10, 6, 6, 2, tzinfo=UTC))  # 3h02: ainda fechado
    assert auto_reply_ligado.auto_replies == []
    await _rodada(datetime(2026, 10, 6, 12, 2, tzinfo=UTC))  # 9h02
    [linha] = await _linhas(db)
    assert linha.estado == "enviado" and len(auto_reply_ligado.auto_replies) == 1


async def test_rearme_so_do_que_ainda_sai_no_horario(db):
    dono = await _dono(db)
    integ, _ = await _loja(db, dono)
    regra = await _regra(db, integ, "shopee_menu", janela_inicio=time(9), janela_fim=time(20))
    # Menu (vale 30 min) simulado às 21h58 de SP; a troca às 22h: só sairia às 9h — não rearma.
    await _agendada(
        db,
        regra,
        evento=datetime(2026, 10, 6, 0, 57, tzinfo=UTC),
        devido=datetime(2026, 10, 6, 0, 58, tzinfo=UTC),
        estado="simulado",
        duoke="nao_mandou",
    )
    assert await rota_auto.rearmar(db, regra, agora=datetime(2026, 10, 6, 1, 0, tzinfo=UTC)) == 0


async def test_enviar_depois_da_transicao_nao_espera_a_leitura(db, envio_ligado):
    """Passados os dias da transição, a espera do Duoke e da leitura só atrasaria o
    menu: ele sai na rodada seguinte ao `devido_em`, mesmo com a leitura parada."""
    dono = await _dono(db)
    integ, canal = await _loja(db, dono, ultimo_ok=T - timedelta(minutes=10))
    await _regra(
        db,
        integ,
        "shopee_menu",
        modo="enviar",
        enviar_desde=T - automacoes.TRANSICAO - timedelta(hours=1),
    )
    conversa = await _conversa(db, integ, canal)
    await _msg(db, conversa, em=T - timedelta(minutes=3))
    await _rodada()
    [linha] = await _linhas(db)
    assert linha.estado == "enviado" and len(envio_ligado.envios) == 1


async def test_regra_relida_no_envio_recusa_se_saiu_de_enviar(db, envio_ligado):
    dono = await _dono(db)
    integ, canal = await _loja(db, dono)
    await _regra(db, integ, "shopee_menu", modo="simular")
    conversa = await _conversa(db, integ, canal)
    with pytest.raises(enviar.EnvioRecusado) as e:
        await enviar.enviar_automatica(
            db, conversa, codigo="shopee_menu", texto=MENU, registro_id=None, regra_versao=1
        )
    assert e.value.code == "regra_nao_envia"
    # E o freio geral para tudo, até a regra em enviar.
    await _regra(db, integ, "shopee_opcao_6", modo="enviar")
    get_settings().atendimento_envio_ativo = False
    with pytest.raises(enviar.EnvioRecusado) as e:
        await enviar.enviar_automatica(
            db,
            conversa,
            codigo="shopee_opcao_6",
            texto="Descreva",
            registro_id=None,
            regra_versao=1,
        )
    assert e.value.code == "envio_desligado"
    assert envio_ligado.envios == []


# ── Comparador ────────────────────────────────────────────────────────────


async def test_comparador_mandou_nao_mandou_e_leitura_parada(db, proibido):
    dono = await _dono(db)
    integ, canal = await _loja(db, dono, ultimo_ok=T + timedelta(hours=1))
    parada, canal_parado = await _loja(db, dono, "kfa", ultimo_ok=T - timedelta(hours=2))
    await _regra(db, integ, "shopee_menu")
    await _regra(db, parada, "shopee_menu")
    com_duoke = await _conversa(db, integ, canal, comprador="1")
    sem_duoke = await _conversa(db, integ, canal, comprador="2")
    da_parada = await _conversa(db, parada, canal_parado, comprador="3")
    for c in (com_duoke, sem_duoke, da_parada):
        await _msg(db, c, em=T - timedelta(minutes=40))
    duoke = await _msg(
        db,
        com_duoke,
        autor="loja",
        texto=MENU,
        em=T - timedelta(minutes=39) + timedelta(seconds=20),
    )
    # A NOSSA mensagem (marca do motor) nunca casa consigo mesma.
    await _msg(
        db,
        sem_duoke,
        autor="loja",
        origem="davinci_auto",
        texto=MENU,
        em=T - timedelta(minutes=39),
        payload={"automacao": {"codigo": "shopee_menu"}},
    )
    await _rodada(motor_desde=T - timedelta(hours=1))
    linhas = {x.conversa_id: x for x in await _linhas(db, automacao="shopee_menu")}
    assert linhas[com_duoke.id].duoke == "mandou"
    assert linhas[com_duoke.id].duoke_mensagem_id == duoke.id
    # A linha passou da validade (o motor viu tarde): não sairia — a diferença
    # fica contra o `devido_em`.
    assert linhas[com_duoke.id].estado == "pulado"
    assert linhas[com_duoke.id].duoke_diferenca_s == 20
    assert linhas[sem_duoke.id].duoke == "nao_mandou"
    # A leitura da loja parou antes do fim da janela: continua pendente.
    assert linhas[da_parada.id].duoke == "pendente"


async def test_diferenca_e_da_hora_em_que_a_nossa_sairia_e_o_atraso_real(db, proibido):
    """Medido do `devido_em`, o menu batia "na mesma hora" (0 s) que o Duoke; a nossa
    sai na rodada (minutos pares) depois do `devido_em`: a diferença é da DECISÃO, e a
    conta mostra o atraso real do DaVinci (do gatilho até a hora em que sairia)."""
    dono = await _dono(db)
    integ, canal = await _loja(db, dono, ultimo_ok=T + timedelta(hours=1))
    await _regra(db, integ, "shopee_menu")
    conversa = await _conversa(db, integ, canal)
    await _msg(db, conversa, em=T - timedelta(minutes=3), visto=T - timedelta(minutes=2))
    await _msg(db, conversa, autor="loja", texto=MENU, em=T - timedelta(minutes=2, seconds=-10))
    await _rodada(motor_desde=T - timedelta(hours=1))
    await _rodada(T + timedelta(minutes=30), motor_desde=T - timedelta(hours=1))
    [linha] = await _linhas(db, automacao="shopee_menu")
    assert (linha.estado, linha.decidido_em, linha.duoke) == ("simulado", T, "mandou")
    # Duoke às T − 1 min 50 s; a nossa às T (o devido era T − 2 min).
    assert linha.duoke_diferenca_s == -110
    conta = await automacoes_comparar.estatisticas(
        db, desde=T - timedelta(days=1), ate=T + timedelta(hours=1)
    )
    menu = conta[("shopee_menu", integ.id)]
    assert (menu["diferenca_mediana_s"], menu["atraso_mediana_s"]) == (-110, 180)
    total = automacoes_comparar.somar(list(conta.values()), cat.CATALOGO["shopee_menu"])
    assert total["atraso_mediana_s"] == 180
    assert "_atrasos" not in automacoes_comparar.sem_internos(total)


async def test_diferenca_do_enviado_e_da_mensagem_que_saiu(db, proibido):
    dono = await _dono(db)
    integ, canal = await _loja(db, dono, ultimo_ok=T + timedelta(hours=1))
    regra = await _regra(db, integ, "shopee_menu", modo="enviar", enviar_desde=DESDE)
    conversa = await _conversa(db, integ, canal)
    nossa = await _msg(
        db,
        conversa,
        autor="loja",
        origem="davinci_auto",
        texto=cat.TEXTO_MENU,
        em=T - timedelta(minutes=30),
        payload={"automacao": {"codigo": "shopee_menu"}},
    )
    await _agendada(
        db,
        regra,
        conversa=conversa,
        evento=T - timedelta(minutes=32),
        devido=T - timedelta(minutes=31),
        estado="enviado",
        modo="enviar",
        decidido_em=T - timedelta(minutes=29),
        mensagem_ids=[str(nossa.id)],
    )
    await _msg(db, conversa, autor="loja", texto=MENU, em=T - timedelta(minutes=29, seconds=30))
    await automacoes_comparar.comparar(db, agora=T, motor_desde=T - timedelta(days=1))
    await db.commit()
    [linha] = await _linhas(db)
    # Duoke − a NOSSA mensagem gravada (não o devido_em, nem a decisão).
    assert (linha.duoke, linha.duoke_diferenca_s) == ("mandou", 30)


async def test_so_duoke_e_o_menu_de_fim_de_sessao(db, proibido, monkeypatch):
    monkeypatch.setattr(automacoes_comparar, "LOTE_INSERT", 1)  # em lotes, o mesmo resultado
    dono = await _dono(db)
    integ, canal = await _loja(db, dono)
    await _regra(db, integ, "shopee_menu", ligada_desde=T - timedelta(days=5))
    conversa = await _conversa(db, integ, canal)
    # O comprador escreveu e o Duoke mandou o menu, mas o motor não viu o gatilho.
    await _msg(db, conversa, em=T - timedelta(hours=3), visto=T - timedelta(hours=3))
    perdido = await _msg(
        db, conversa, autor="loja", texto=MENU, em=T - timedelta(hours=3) + timedelta(minutes=1)
    )
    # O menu de "fim de sessão": 12 h depois do anterior, sem mensagem do comprador.
    outra = await _conversa(db, integ, canal, comprador="9")
    sessao = await _msg(db, outra, autor="loja", texto=MENU, em=T - timedelta(hours=2))
    resumo = await automacoes_comparar.comparar(db, agora=T, motor_desde=T - timedelta(hours=10))
    await db.commit()
    assert resumo["so_duoke"] == 2
    so = {x.duoke_mensagem_id: x for x in await _linhas(db, estado="so_duoke")}
    assert so[perdido.id].automacao == "shopee_menu" and so[perdido.id].divergencia is None
    assert so[perdido.id].chave == f"duoke:{perdido.id}"
    assert so[sessao.id].divergencia == "menu_fim_de_sessao"
    # Idempotente: a mesma mensagem não vira outra linha.
    resumo = await automacoes_comparar.comparar(
        db, agora=T + timedelta(minutes=2), motor_desde=T - timedelta(hours=10)
    )
    assert resumo["so_duoke"] == 0


async def test_pedido_recebido_casa_pelo_cartao_do_duoke(db, proibido):
    dono = await _dono(db)
    integ, canal = await _loja(db, dono)
    await _regra(db, integ, "shopee_pedido_recebido")
    loja = (await db.execute(select(Store).where(Store.integration_id == integ.id))).scalar_one()
    db.add(
        BlingOrder(
            numero="1",
            numeroloja=SN,
            situacao="6",
            store_id=loja.id,
            item_index=0,
            data=T,
            created_at=T - timedelta(minutes=50),
        )
    )
    await db.commit()
    # O Duoke abriu a conversa: o cartão do pedido e o texto 1 s depois (como `sistema`).
    conversa = await _conversa(db, integ, canal, comprador="55")
    await _msg(
        db,
        conversa,
        autor="loja",
        texto=None,
        em=T - timedelta(minutes=45),
        payload={"source": "openapi", "message_type": "order", "content": {"order_sn": SN}},
    )
    texto = await _msg(
        db,
        conversa,
        autor="sistema",
        texto="Oi! Recebemos seu pedido e já estamos preparando pra envio.",
        em=T - timedelta(minutes=45) + timedelta(seconds=1),
    )
    await _rodada(motor_desde=T - timedelta(hours=2))
    [linha] = await _linhas(db, automacao="shopee_pedido_recebido")
    assert linha.estado == "simulado"
    assert (linha.duoke, linha.duoke_mensagem_id) == ("mandou", texto.id)


async def test_so_davinci_para_quem_devolveu_vira_alerta(db, proibido):
    dono = await _dono(db)
    integ, canal = await _loja(db, dono)
    regra = await _regra(db, integ, "shopee_pos_conclusao")
    db.add(
        AtendimentoAutomacaoRegistro(
            automacao="shopee_pos_conclusao",
            regra_id=regra.id,
            integration_id=integ.id,
            plataforma="shopee",
            alvo="pedido",
            chave=f"pedido:{SN}",
            pedido=SN,
            evento_em=T - timedelta(hours=14),
            devido_em=T - timedelta(hours=10),
            decidido_em=T - timedelta(hours=10),
            estado="simulado",
            modo="simular",
            duoke="pendente",
        )
    )
    db.add(
        AtendimentoReclamacao(
            integration_id=integ.id,
            plataforma="shopee",
            externo_id="r9",
            tipo="devolucao",
            status="ACCEPTED",
            pedido_marketplace=SN,
            encerrada_em=T - timedelta(hours=1),
        )
    )
    await db.commit()
    await automacoes_comparar.comparar(db, agora=T, motor_desde=T - timedelta(days=1))
    await db.commit()
    [linha] = await _linhas(db)
    assert (linha.duoke, linha.alerta) == ("nao_mandou", "devolucao")
    conta = await automacoes_comparar.estatisticas(db, desde=T - timedelta(days=1), ate=T)
    resumo = conta[("shopee_pos_conclusao", integ.id)]
    assert resumo["so_davinci"] == 1 and resumo["alertas"] == 1
    assert resumo["pode_trocar"] is False
    assert "mandaria para quem devolveu, cancelou ou reclamou" in resumo["por_que_nao"]


async def test_disjuntor_quando_o_duoke_manda_depois_da_troca(db, proibido):
    dono = await _dono(db)
    integ, canal = await _loja(db, dono)
    regra = await _regra(
        db, integ, "shopee_menu", modo="enviar", enviar_desde=T - timedelta(hours=2)
    )
    conversa = await _conversa(db, integ, canal)
    db.add(
        AtendimentoAutomacaoRegistro(
            automacao="shopee_menu",
            regra_id=regra.id,
            integration_id=integ.id,
            plataforma="shopee",
            alvo="conversa",
            chave="conversa:k",
            conversa_id=conversa.id,
            evento_em=T - timedelta(minutes=31),
            devido_em=T - timedelta(minutes=30),
            decidido_em=T - timedelta(minutes=30),
            estado="enviado",
            modo="enviar",
            duoke="pendente",
        )
    )
    await db.commit()
    await _msg(db, conversa, autor="loja", texto=MENU, em=T - timedelta(minutes=29))
    await automacoes_comparar.comparar(db, agora=T, motor_desde=T - timedelta(days=1))
    await db.commit()
    await db.refresh(regra)
    assert (regra.modo, regra.disjuntor_motivo) == ("simular", "duoke_ainda_ligado")
    assert regra.enviar_desde is None


def test_resumir_precisao_cobertura_e_criterio():
    aut = cat.CATALOGO["shopee_menu"]
    bom = automacoes_comparar.resumir(
        {"bateu_mandou": 98, "bateu_nao_mandou": 0, "so_davinci": 1, "so_duoke": 1}, aut
    )
    assert bom["precisao"] == round(98 / 99, 4) and bom["pode_trocar"] is True
    ruim = automacoes_comparar.resumir({"bateu_mandou": 90, "so_davinci": 0, "so_duoke": 10}, aut)
    assert ruim["cobertura"] == 0.9 and ruim["pode_trocar"] is False
    # Opção: a precisão não se mede pelo Duoke (ele responde 12 h depois).
    opcao = automacoes_comparar.resumir(
        {"bateu_mandou": 40, "so_davinci": 30}, cat.CATALOGO["shopee_opcao_6"]
    )
    assert opcao["precisao"] is None and opcao["pode_trocar"] is True
    poucos = automacoes_comparar.resumir({"bateu_mandou": 5}, aut)
    assert poucos["pode_trocar"] is False


# ── IA, fila e Histórico ──────────────────────────────────────────────────


async def test_humano_respondeu_recente_nao_conta_mensagem_automatica(db):
    dono = await _dono(db)
    integ, canal = await _loja(db, dono)
    conversa = await _conversa(db, integ, canal)
    agora = datetime.now(UTC)
    await _msg(db, conversa, em=agora - timedelta(hours=2))
    await _msg(
        db, conversa, autor="loja", texto=MENU, em=agora - timedelta(hours=2) + timedelta(minutes=1)
    )
    await _msg(
        db,
        conversa,
        autor="loja",
        texto="Olá, a sua mensagem foi recebida. Há mais mensagens",
        em=agora - timedelta(hours=1),
    )
    await _msg(
        db,
        conversa,
        autor="loja",
        texto=None,
        em=agora - timedelta(minutes=50),
        payload={"source": "openapi", "message_type": "order", "content": {"order_sn": SN}},
    )
    await _msg(
        db,
        conversa,
        autor="loja",
        origem="davinci_auto",
        texto=cat.TEXTO_AGUARDE,
        em=agora - timedelta(minutes=40),
        payload={"automacao": {"codigo": "shopee_aguarde"}},
    )
    await _msg(
        db,
        conversa,
        autor="loja",
        texto="Bom dia! Já te respondo",
        em=agora - timedelta(minutes=30),
        origem="externo",
    )
    falhou = await db.scalar(
        select(AtendimentoMensagem).where(AtendimentoMensagem.texto == "Bom dia! Já te respondo")
    )
    falhou.status = "falhou"
    await db.commit()
    assert await ia._humano_respondeu_recente(db, conversa) is False
    await _msg(
        db,
        conversa,
        autor="loja",
        texto="Pode mandar a foto do defeito?",
        em=agora - timedelta(minutes=5),
    )
    assert await ia._humano_respondeu_recente(db, conversa) is True


def test_fila_mensagem_do_motor_nao_fecha_a_vez():
    conversa = AtendimentoConversa(plataforma="shopee", canal="chat", externo_id="c")
    cliente = AtendimentoMensagem(
        autor="cliente",
        origem="cliente",
        tipo="texto",
        texto="oi",
        enviada_em=T,
        status="recebida",
        payload={},
    )
    nossa = AtendimentoMensagem(
        autor="loja",
        origem="davinci_auto",
        tipo="texto",
        texto="Olá! qualquer texto que mudou",
        enviada_em=T + timedelta(minutes=1),
        status="enviada",
        payload={"automacao": {"codigo": "shopee_menu", "parte": "texto"}},
    )
    gravar.recalcular(conversa, [cliente, nossa])
    assert conversa.aguardando_resposta is True
    # Só as partes equivalentes às do Duoke que fechavam o turno só de cartão.
    assert gravar._fechava_a_vez(nossa) is False
    convite = AtendimentoMensagem(
        autor="loja",
        origem="davinci_auto",
        tipo="texto",
        texto="zé já segue nossa loja aqui no TikTok?",
        status="enviada",
        payload={"automacao": {"codigo": "tiktok_convite", "parte": "texto"}},
    )
    assert gravar._fechava_a_vez(convite) is True


def test_registro_fora_do_historico_e_a_regra_dentro():
    assert hsql.a_cobrir(["atendimento_automacao_registros", "atendimento_automacao_regras"]) == [
        "atendimento_automacao_regras"
    ]


# ── Rotas da aba ──────────────────────────────────────────────────────────


@pytest.fixture
async def pessoa(make_user, auth_as):
    u = await make_user(permissions=PODE_TUDO)
    auth_as(u)
    return u


async def test_lista_catalogo_por_loja_regra_padrao_e_chaves(db, client, pessoa):
    integ, _ = await _loja(db, pessoa, "barbosa")
    await _regra(db, integ, "shopee_menu")
    r = await client.get("/api/atendimento/automacoes", params={"plataforma": "shopee"})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["chaves"]["envio_automacoes"] is False
    assert corpo["faixa"]["nivel"] == "info"
    por_codigo = {a["codigo"]: a for a in corpo["automacoes"]}
    assert set(por_codigo) == {a.codigo for a in cat.por_plataforma("shopee")}
    loja = por_codigo["shopee_menu"]["lojas"][0]
    assert loja["regra"]["modo"] == "simular" and loja["regra"]["padrao"] is False
    assert loja["duoke_hoje"] is True
    assert loja["pode_enviar"] is False and "envio_desligado" in loja["por_que_nao_enviar"]
    sem = por_codigo["shopee_aguarde"]["lojas"][0]
    assert sem["regra"]["padrao"] is True and sem["regra"]["modo"] == "desligado"


async def test_registro_e_estatisticas_sem_texto_nenhum(db, client, pessoa, monkeypatch):
    integ, canal = await _loja(db, pessoa, "barbosa")
    await _regra(db, integ, "shopee_menu")
    conversa = await _conversa(db, integ, canal, nome="fulana.secreta")
    await _msg(
        db,
        conversa,
        texto="meu cpf é 123.456.789-00, cadê meu pedido?",
        em=T - timedelta(minutes=3),
    )
    await _rodada()
    r = await client.get("/api/atendimento/automacoes/registro")
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert len(corpo["linhas"]) == 1 and corpo["linhas"][0]["estado"] == "simulado"
    bruto = json.dumps(corpo, ensure_ascii=False)
    for proibido_ in ("123.456.789", "cadê", "fulana.secreta", "por favor selecione"):
        assert proibido_ not in bruto
    r = await client.get("/api/atendimento/automacoes/estatisticas", params={"dias": 30})
    assert r.status_code == 200, r.text
    assert "fulana.secreta" not in r.text


async def test_patch_enviar_recusado_sem_a_chave_e_texto_invalido(db, client, pessoa, _chaves):
    integ, _ = await _loja(db, pessoa, "barbosa")
    url = f"/api/atendimento/automacoes/shopee_menu/{integ.id}"
    r = await client.patch(url, json={"modo": "enviar", "desliguei_no_duoke": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "envio_desligado"
    _chaves.atendimento_automacoes_envio = True
    r = await client.patch(url, json={"modo": "enviar", "desliguei_no_duoke": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "envio_geral_desligado"
    _chaves.atendimento_envio_ativo = True
    r = await client.patch(url, json={"modo": "enviar"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "confirmar_duoke"
    # Campanha da Shopee sem o auto_reply confirmado.
    r = await client.patch(
        f"/api/atendimento/automacoes/shopee_convite/{integ.id}",
        json={"modo": "enviar", "desliguei_no_duoke": True},
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "campanha_sem_auto_reply"
    # Texto que o validador barra (contato fora da loja).
    r = await client.patch(
        url, json={"partes": [{"tipo": "texto", "texto": "Chama no whatsapp 11 99999-8888"}]}
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "texto_invalido"
    # A opção 4 não liga sem texto.
    r = await client.patch(
        f"/api/atendimento/automacoes/shopee_opcao_4/{integ.id}", json={"modo": "simular"}
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "sem_texto"
    assert (await db.scalar(select(func.count()).select_from(AtendimentoAutomacaoRegra))) == 0


async def test_patch_cria_regra_sobe_versao_e_rearma_na_troca(db, client, pessoa, _chaves):
    integ, canal = await _loja(db, pessoa, "barbosa")
    url = f"/api/atendimento/automacoes/shopee_menu/{integ.id}"
    r = await client.patch(url, json={"modo": "simular"})
    assert r.status_code == 200, r.text
    assert r.json()["regra"]["modo"] == "simular" and r.json()["regra"]["ligada_desde"]
    r = await client.patch(
        url,
        json={
            "partes": [
                {"tipo": "texto", "texto": "Oi, {comprador}! Escolha uma opção: 1, 2 ou 6."}
            ],
            "janela_inicio": "08:00",
            "janela_fim": "22:00",
        },
    )
    assert r.status_code == 200, r.text
    regra = r.json()["regra"]
    assert regra["versao"] == 2 and regra["janela_inicio"] == "08:00"
    # Uma linha simulada há pouco, que o Duoke não mandou: rearma na troca.
    regra_db = (await db.execute(select(AtendimentoAutomacaoRegra))).scalar_one()
    agora = datetime.now(UTC)
    db.add(
        AtendimentoAutomacaoRegistro(
            automacao="shopee_menu",
            regra_id=regra_db.id,
            integration_id=integ.id,
            plataforma="shopee",
            alvo="conversa",
            chave="conversa:r",
            evento_em=agora - timedelta(minutes=3),
            devido_em=agora - timedelta(minutes=2),
            decidido_em=agora - timedelta(minutes=2),
            estado="simulado",
            modo="simular",
            duoke="nao_mandou",
        )
    )
    await db.commit()
    _chaves.atendimento_automacoes_envio = True
    _chaves.atendimento_envio_ativo = True
    # O critério da troca não passou NESTA loja (1 caso): pede a confirmação.
    r = await client.patch(url, json={"modo": "enviar", "desliguei_no_duoke": True})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "criterio_nao_passou"
    assert "poucos casos (1 de 30)" in r.json()["detail"]["motivos"]
    r = await client.patch(
        url, json={"modo": "enviar", "desliguei_no_duoke": True, "troca_sem_criterio": True}
    )
    assert r.status_code == 200, r.text
    assert r.json()["rearmadas"] == 1 and r.json()["regra"]["enviar_desde"]
    [linha] = await _linhas(db)
    assert linha.estado == "agendado" and linha.decidido_em is None


async def test_criterio_da_troca_e_da_loja_em_7_dias(db, client, pessoa, _chaves):
    """O selo da automação soma as lojas; a troca é loja por loja: a lista traz o
    critério de cada loja (sempre 7 dias), e o PATCH para enviar exige o da loja —
    ou a confirmação explícita de quem troca sem ele."""
    _chaves.atendimento_automacoes_envio = True
    _chaves.atendimento_envio_ativo = True
    boa, _ = await _loja(db, pessoa, "barbosa")
    fraca, _ = await _loja(db, pessoa, "kfa")
    agora = datetime.now(UTC)
    for integ, n in ((boa, 40), (fraca, 3)):
        regra = await _regra(db, integ, "shopee_menu")
        for i in range(n):
            await _agendada(
                db,
                regra,
                evento=agora - timedelta(hours=3, minutes=i),
                devido=agora - timedelta(hours=3, minutes=i) + timedelta(minutes=1),
                estado="simulado",
                modo="simular",
                decidido_em=agora - timedelta(hours=3),
                duoke="mandou",
            )
    r = await client.get("/api/atendimento/automacoes", params={"plataforma": "shopee", "dias": 30})
    assert r.status_code == 200, r.text
    menu = next(a for a in r.json()["automacoes"] if a["codigo"] == "shopee_menu")
    por_loja = {x["integracao"]: x for x in menu["lojas"]}
    assert (
        por_loja["barbosa"]["pode_trocar"] is True
        and por_loja["barbosa"]["por_que_nao_trocar"] == []
    )
    assert por_loja["kfa"]["pode_trocar"] is False
    assert por_loja["kfa"]["por_que_nao_trocar"] == ["poucos casos (3 de 30)"]
    # A soma das lojas passaria (43 casos) — e não vale para a loja fraca.
    assert menu["total_periodo"]["pode_trocar"] is True
    r = await client.patch(
        f"/api/atendimento/automacoes/shopee_menu/{fraca.id}",
        json={"modo": "enviar", "desliguei_no_duoke": True},
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "criterio_nao_passou"
    assert r.json()["detail"]["motivos"] == ["poucos casos (3 de 30)"]
    r = await client.patch(
        f"/api/atendimento/automacoes/shopee_menu/{boa.id}",
        json={"modo": "enviar", "desliguei_no_duoke": True},
    )
    assert r.status_code == 200, r.text
    r = await client.patch(
        f"/api/atendimento/automacoes/shopee_menu/{fraca.id}",
        json={"modo": "enviar", "desliguei_no_duoke": True, "troca_sem_criterio": True},
    )
    assert r.status_code == 200, r.text


async def test_simular_nas_lojas_do_duoke_nunca_mexe_em_enviar(db, client, pessoa):
    barbosa, _ = await _loja(db, pessoa, "barbosa")
    kfa, _ = await _loja(db, pessoa, "kfa")
    mini, _ = await _loja(db, pessoa, "mini")
    await _loja(db, pessoa, "aguiar")  # fora do Duoke: nada
    await _regra(db, kfa, "shopee_menu", modo="enviar", enviar_desde=DESDE)
    await _regra(db, mini, "shopee_menu", modo="desligado")
    r = await client.post("/api/atendimento/automacoes/shopee_menu/simular-nas-lojas-do-duoke")
    assert r.status_code == 200, r.text
    assert r.json() == {"criadas": 1, "ligadas": 1, "mantidas": 1}
    modos = {
        x.integration_id: x.modo
        for x in (
            await db.execute(
                select(AtendimentoAutomacaoRegra).execution_options(populate_existing=True)
            )
        ).scalars()
    }
    assert modos == {barbosa.id: "simular", kfa.id: "enviar", mini.id: "simular"}


async def test_previa_renderiza_sem_enviar(client, pessoa):
    r = await client.post(
        "/api/atendimento/automacoes/previa", json={"automacao": "shopee_pos_conclusao"}
    )
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["motivos"] == []
    assert corpo["partes"][0]["texto"].startswith("Oi, maria.silva! Só passando")
    assert corpo["sem_nome"][0]["texto"].startswith("Oi! Só passando")


def test_janela_das_regras_e_time():
    assert cat.janela_da_regra(
        type("R", (), {"janela_inicio": time(9), "janela_fim": time(20)})(),
        cat.CATALOGO["shopee_menu"],
    ) == (time(9), time(20))


# ── O script que refaz a fila com a régua nova ────────────────────────────


async def test_script_da_fila_seco_so_conta_e_gravar_grava(db):
    from scripts import atendimento_fila_recalcular as script

    dono = await _dono(db)
    integ, canal = await _loja(db, dono)
    conversa = await _conversa(db, integ, canal)
    agora = datetime.now(UTC)
    await _msg(db, conversa, em=agora - timedelta(hours=2))
    # O cartão do pedido da campanha: a régua velha contava como resposta da loja.
    await _msg(
        db, conversa, autor="loja", texto=None, em=agora - timedelta(hours=1), payload=_cartao(SN)
    )
    conversa.aguardando_resposta = False
    conversa.ultima_da_loja_em = agora - timedelta(hours=1)
    conversa.ultima_mensagem_em = agora - timedelta(hours=1)
    await db.commit()
    seco = await script.recalcular(seco=True, agora=agora)
    assert (seco["mudaram"], seco["entraram_na_fila"]) == (1, 1)
    assert seco["mudaram_por_plataforma"] == {"shopee": 1}
    await db.refresh(conversa)
    assert conversa.aguardando_resposta is False, "o seco não grava"
    gravou = await script.recalcular(seco=False, agora=agora)
    assert gravou["entraram_na_fila"] == 1
    await db.refresh(conversa)
    assert conversa.aguardando_resposta is True
    assert (await script.recalcular(seco=True, agora=agora))["mudaram"] == 0
