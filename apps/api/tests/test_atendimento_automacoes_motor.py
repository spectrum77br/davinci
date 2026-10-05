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
from app.security.cipher import encrypt_json
from app.services.atendimento import automacoes, automacoes_comparar, clientes, enviar, gravar, ia
from app.services.atendimento import automacoes_catalogo as cat
from app.services.atendimento.constantes import ResultadoEnvio

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
    monkeypatch.setattr(enviar, "enviar_resposta", _aboom("enviar_resposta"))
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

    # Entregue às 21h de SP de 04/10 → saía às 9h de 05/10 (ainda vale: simula).
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
    assert entregue["251004ENTREGUE"].devido_em == datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
    assert entregue["251004ENTREGUE"].estado == "simulado"
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
    assert linhas[com_duoke.id].duoke_diferenca_s == 20
    assert linhas[sem_duoke.id].duoke == "nao_mandou"
    # A leitura da loja parou antes do fim da janela: continua pendente.
    assert linhas[da_parada.id].duoke == "pendente"


async def test_so_duoke_e_o_menu_de_fim_de_sessao(db, proibido):
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
    r = await client.patch(url, json={"modo": "enviar", "desliguei_no_duoke": True})
    assert r.status_code == 200, r.text
    assert r.json()["rearmadas"] == 1 and r.json()["regra"]["enviar_desde"]
    [linha] = await _linhas(db)
    assert linha.estado == "agendado" and linha.decidido_em is None


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
