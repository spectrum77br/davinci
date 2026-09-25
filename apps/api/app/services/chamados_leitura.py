"""Leitura do caso na TELA da plataforma pelo robô (Vinicius, 22/09/2026).

O buraco: quando não há caminho pela API, o robô abre o chamado no Seller Center
(ou no Portal de Atendimento ao Vendedor da Shopee) e devolve o protocolo. A
partir dali o caso fica órfão — a plataforma responde na tela e nada traz essa
fala pro painel. Medido no 292592: o Agente Shopee respondeu em 19/09 às 21:42 e
três dias depois a aba Chamados não sabia. O contrato do robô só tinha metade:
`POST /agent/lease` entrega o que TEMOS A DIZER (sempre com um texto pra postar),
e nada nunca disse ao robô QUAIS casos reler.

Este módulo é a metade que faltava, em duas funções:

  `fila`      — quais casos reler agora (e marca a entrega, pra não repetir);
  `registrar` — o que o robô leu: falas da plataforma, a página inteira, e se o
                caso fechou.

## Por que fila própria, e não um tipo novo no `/agent/lease`

Hoje vale um invariante simples do lado do robô: *tudo que o lease te dá, você
POSTA* — `AgentTarefaOut` exige `mensagem_id` e `texto`, e o texto sai de uma
mensagem nossa pendente. Enfiar leitura ali obrigaria a afrouxar os dois campos,
e um robô que implementasse errado postaria um texto vazio na conversa com o
cliente. Fila separada mantém o invariante e não pede nenhuma linha de mudança no
robô que já consome o lease.

## A diferença que o robô precisa entender (é a única)

`falas[]` CONTA como resposta da plataforma: vira mensagem `recebida`, mexe na
coluna "Últ. resposta" e no Status da aba. `historico` é SÓ CONTEXTO: uma
mensagem `historico` por chamado, atualizada quando muda, fora do cálculo de
status. Na dúvida sobre o que é fala deles, o robô manda — o guarda de eco daqui
descarta a nossa própria fala voltando da página, e o dedupe por texto deixa
reler a mesma página de 3 em 3 h sem sujar o histórico.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chamado, ChamadoMensagem
from app.services import chamados as chamados_svc
from app.services.texto_html import limpar_html

logger = structlog.get_logger()

# Cadência. Caso com fala recente merece browser de 3 em 3 h; caso frio (ninguém
# falou há mais de duas semanas) uma vez por dia — cada leitura é uma sessão de
# navegador logada no Seller Center, e acesso demais é risco de bloqueio da conta.
INTERVALO = timedelta(hours=3)
INTERVALO_FRIO = timedelta(hours=24)
FRIO = timedelta(days=15)
# Entrega em curso: mesmo número do `_LEASE_STALE` das tarefas de envio.
CLAIM_STALE = timedelta(minutes=30)

AUTOR_ROBO_LEITURA = "página do caso"
TIPO_HISTORICO = "historico"
# Data de tela ilegível (parse errado, relógio do robô torto) não derruba a
# leitura inteira — é clampada e deixa rastro no histórico.
FUTURO_TOLERADO = timedelta(days=1)
PASSADO_TOLERADO = timedelta(days=90)


@dataclass(slots=True)
class FalaLida:
    """Uma fala da plataforma lida na tela. `quando` é a hora que a TELA mostra —
    é ela que vai pro histórico, não a hora do POST: era isso que se perdia."""

    texto: str
    quando: datetime
    autor: str | None = None


@dataclass(slots=True)
class Resultado:
    falas_novas: int = 0
    ecos: int = 0
    duplicadas: int = 0
    historico_alterado: bool = False
    encerrado: bool = False
    pendencias_novas: int = 0


# ------------------------------------------------------------------ a fila


def _ultima_fala_at():
    """Quando alguém (nós ou a plataforma) falou por último neste chamado."""
    return (
        select(func.max(func.coalesce(ChamadoMensagem.enviada_at, ChamadoMensagem.created_at)))
        .where(
            ChamadoMensagem.chamado_id == Chamado.id,
            ChamadoMensagem.direcao.in_(("enviada", "recebida")),
        )
        .correlate(Chamado)
        .scalar_subquery()
    )


async def fila(
    session: AsyncSession,
    *,
    plataformas: list[str],
    limite: int = 10,
    conta: str | None = None,
    agora: datetime | None = None,
) -> list[Chamado]:
    """Casos que o robô deve reler AGORA, e marca a entrega na mesma transação.

    Entra: caso aberto na TELA (`CASO_DE_TELA_SQL`), vivo, sem decisão final, com
    protocolo, da plataforma que o robô declarou atender, fora do claim e fora da
    cadência.

    NÃO exige `chamado_url`. A primeira versão exigia, e isso abria um buraco:
    `chamado_url` é opcional no `/agent/resultado`, e o caso sem URL tinha saído da
    varredura por API e era recusado pela fila — ficava sem ninguém lendo, em
    silêncio. O protocolo basta: o robô sabe em que plataforma está e acha a página
    por ele.

    Não entra: caso aberto pela API (esse o `sync_respostas` lê de hora em hora),
    Encerrado (a plataforma já decidiu — falta pessoa), Concluído.

    `ORDER BY leitura_robo_at NULLS FIRST` drena do mais atrasado: o caso que
    nunca foi lido vem primeiro e nenhum morre de fome. `SKIP LOCKED` cobre dois
    polls simultâneos do mesmo robô."""
    agora = agora or datetime.now(UTC)
    aceitas: set[str] = set()
    for p in plataformas:
        aceitas.update(chamados_svc.apelidos_da_plataforma(p))
    if not aceitas:
        return []
    ultima_fala = _ultima_fala_at()
    conds = [
        Chamado.resolvido.is_(False),
        chamados_svc.NAO_ENCERRADO_SQL,
        chamados_svc.CASO_DE_TELA_SQL,
        func.coalesce(func.trim(Chamado.chamado), "") != "",
        func.lower(func.trim(func.coalesce(Chamado.plataforma, ""))).in_(sorted(aceitas)),
        or_(
            Chamado.leitura_robo_claim_at.is_(None),
            Chamado.leitura_robo_claim_at < agora - CLAIM_STALE,
        ),
        or_(
            Chamado.leitura_robo_at.is_(None),
            and_(
                ultima_fala >= agora - FRIO,
                Chamado.leitura_robo_at < agora - INTERVALO,
            ),
            and_(
                or_(ultima_fala.is_(None), ultima_fala < agora - FRIO),
                Chamado.leitura_robo_at < agora - INTERVALO_FRIO,
            ),
        ),
    ]
    if conta and conta.strip():
        conds.append(
            func.lower(func.trim(func.coalesce(Chamado.conta, ""))) == conta.strip().lower()
        )
    rows = (
        (
            await session.execute(
                select(Chamado)
                .where(*conds)
                .order_by(Chamado.leitura_robo_at.asc().nulls_first(), Chamado.created_at)
                .limit(limite)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    for ch in rows:
        ch.leitura_robo_claim_at = agora
    await session.commit()
    logger.info(
        "chamados_leitura_fila",
        casos=len(rows),
        plataformas=sorted(aceitas),
        conta=conta or None,
    )
    return list(rows)


def condicoes_devolucao_shopee() -> list:
    """Quem É da fila do executor de leitura, sem cadência nem claim: devolução
    da Shopee contestada pela API (o mesmo conjunto que o
    `chamados_devolucao_sync` acompanha), viva e sem decisão final. O Vigia
    Robô Leitura de Chamados (Ouvidoria) usa a mesma régua pra cobrar caso
    que ficou sem leitura."""
    from app.services import chamados_devolucao_sync as acompanhamento

    abertura_acompanhada = (
        select(ChamadoMensagem.id)
        .where(
            ChamadoMensagem.chamado_id == Chamado.id,
            ChamadoMensagem.tipo == "abertura",
            or_(
                ChamadoMensagem.status == "enviada",
                and_(
                    ChamadoMensagem.status == "falhou",
                    ChamadoMensagem.erro.in_(acompanhamento.ABERTURA_FALHOU_ACOMPANHA),
                ),
            ),
        )
        .correlate(Chamado)
        .exists()
    )
    return [
        Chamado.origem == "devolucao",
        Chamado.canal == "api",
        Chamado.resolvido.is_(False),
        chamados_svc.NAO_ENCERRADO_SQL,
        ~chamados_svc.CASO_DE_TELA_SQL,
        func.coalesce(func.trim(Chamado.chamado), "") != "",
        func.coalesce(func.trim(Chamado.pedido_marketplace), "") != "",
        func.lower(func.trim(func.coalesce(Chamado.plataforma, ""))).in_(
            sorted(chamados_svc.apelidos_da_plataforma("shopee"))
        ),
        abertura_acompanhada,
    ]


# 25/09 (Vinicius, 292592): chamado da Shopee aberto NA TELA pelo Portal de
# Atendimento ao Vendedor — `chamado` é o ID da consulta (só dígitos) e a página
# é seller-service.cs.shopee.com.br/detail/<ID>. Nem a API nem a leitura do
# Seller Center chegavam nele: o Agente Shopee respondeu em 19/09 e em 23/09
# (compensação em análise) e o chamado seguia "Aguard. Plataforma" desde 19/09.
PORTAL_SHOPEE_URL = "https://seller-service.cs.shopee.com.br/detail/{}"
_CONSULTA_PORTAL = r"^\d{15,}$"


def condicoes_portal_shopee() -> list:
    """Consulta do Portal de Atendimento ao Vendedor da Shopee que o executor de
    leitura relê: caso aberto na tela, Shopee, vivo, sem decisão final, com o ID
    da consulta no `chamado`."""
    return [
        Chamado.resolvido.is_(False),
        chamados_svc.NAO_ENCERRADO_SQL,
        chamados_svc.CASO_DE_TELA_SQL,
        func.trim(func.coalesce(Chamado.chamado, "")).op("~")(_CONSULTA_PORTAL),
        func.lower(func.trim(func.coalesce(Chamado.plataforma, ""))).in_(
            sorted(chamados_svc.apelidos_da_plataforma("shopee"))
        ),
    ]


def condicoes_do_leitor(*, portal: bool = True, consultas: bool = True) -> list:
    """Tudo que é da fila do executor de leitura: devolução contestada pela API
    (Seller Center), com `portal` o caso aberto na tela pelo Portal, e com
    `consultas` o chamado com consulta do Portal ligada (294571)."""
    ramos = [and_(*condicoes_devolucao_shopee())]
    if portal:
        ramos.append(and_(*condicoes_portal_shopee()))
    if consultas:
        ramos.append(and_(*condicoes_consulta_ligada()))
    return [or_(*ramos)]


def e_portal_shopee(ch: Chamado) -> bool:
    """Caso aberto NA TELA pelo Portal (o `chamado` é o ID da consulta)."""
    return (
        bool(ch.chamado_de_tela)
        and (ch.plataforma or "").strip().lower() in chamados_svc.apelidos_da_plataforma("shopee")
        and re.match(_CONSULTA_PORTAL, (ch.chamado or "").strip()) is not None
    )


def consulta_do_portal(ch: Chamado) -> str | None:
    """A consulta do Portal a ler neste chamado: a ligada à mão (`consulta_portal`,
    294571) ou, no caso aberto na tela pelo Portal, o próprio `chamado`."""
    ligada = (ch.consulta_portal or "").strip()
    if ligada:
        return ligada
    return (ch.chamado or "").strip() if e_portal_shopee(ch) else None


def url_do_portal(ch: Chamado) -> str:
    return PORTAL_SHOPEE_URL.format(consulta_do_portal(ch) or "")


# 25/09 (294571): "abri na mão, tem que consultar por https://seller-service…
# id da consulta 2103108212314644514" — a pessoa escreve assim na Observação ou
# numa instrução; o chamado guarda o ID em `consulta_portal`.
_LINK_PORTAL = re.compile(r"seller-service\.cs\.shopee\.com\.br/detail/(\d{15,})")
_ID_PORTAL = re.compile(r"consulta\D{0,40}?(\d{19})", re.IGNORECASE)


def extrair_consulta_portal(texto: str | None) -> str | None:
    t = texto or ""
    m = _LINK_PORTAL.search(t) or _ID_PORTAL.search(t)
    return m.group(1) if m else None


def ligar_consulta_do_texto(ch: Chamado, texto: str | None) -> bool:
    """Preenche `consulta_portal` a partir do texto (Observação/instrução) se o
    chamado é da Shopee, ainda não tem consulta e o texto traz uma."""
    if (ch.consulta_portal or "").strip() or e_portal_shopee(ch):
        return False
    if (ch.plataforma or "").strip().lower() not in chamados_svc.apelidos_da_plataforma("shopee"):
        return False
    achada = extrair_consulta_portal(texto)
    if not achada:
        return False
    ch.consulta_portal = achada
    return True


def condicoes_consulta_ligada() -> list:
    """Chamado da Shopee com consulta do Portal ligada e sem pessoa ter concluído —
    lida mesmo com a devolução já decidida (294571: "perdemos" na disputa e a
    consulta seguia aberta; é ela que pode virar o jogo)."""
    return [
        Chamado.resolvido.is_(False),
        func.coalesce(func.trim(Chamado.consulta_portal), "") != "",
        func.lower(func.trim(func.coalesce(Chamado.plataforma, ""))).in_(
            sorted(chamados_svc.apelidos_da_plataforma("shopee"))
        ),
    ]


def tipo_de_leitura(ch: Chamado) -> str:
    """O que o executor lê neste caso: `devolucao` (Seller Center), `portal` ou
    `ambos`. Com consulta do Portal ligada, a devolução é lida MESMO decidida
    (25/09, 294571: "perdemos" na API e a 2ª disputa aberta pelo atendente pedindo
    evidência até 26/09 — só a tela do Seller Center mostrava)."""
    portal = consulta_do_portal(ch) is not None
    devolucao = e_devolucao_shopee_da_api(ch) and (
        portal or not (ch.resolvido or ch.status_plataforma in chamados_svc.STATUS_FINAIS)
    )
    if devolucao and portal:
        return "ambos"
    return "portal" if portal else "devolucao"


async def fila_devolucao_shopee(
    session: AsyncSession,
    *,
    limite: int = 10,
    contas: list[str] | None = None,
    espiar: bool = False,
    portal: bool = False,
    consultas: bool = False,
    agora: datetime | None = None,
) -> list[Chamado]:
    """Devoluções da Shopee contestadas PELA API que o executor de leitura deve
    reler no Seller Center — a fila do `/agent/leitor/fila`.

    Vinicius 24/09 (296012): a API da Shopee só dá códigos; a fala do agente
    ("não será possível aprovar sua solicitação…") mora no "Histórico da
    Solicitação" da tela. Às 16:09 a API ainda dizia "aguardando análise" de uma
    recusa das 15:59, e a IA de Chamado mandou aguardar. A `fila` acima NÃO
    serve: ela entrega só caso aberto NA TELA (protocolo de tela), e estes têm o
    `return_sn` da API — o robô acha a devolução pelo número do PEDIDO.

    Entra o mesmo conjunto que o `chamados_devolucao_sync` acompanha pela API
    (abertura enviada, ou falhada porque a disputa já existia/fechou), só Shopee,
    vivo e sem decisão final. Cadência e claim são os da `fila`.

    `contas`: só as lojas que o robô tem perfil pra abrir — sem isso, caso de
    loja sem perfil voltaria sempre primeiro (NULLS FIRST) e tomaria o lugar
    dos outros. `espiar`: devolve sem marcar a entrega (modo seco do robô, e a
    conferência de quais casos entrariam). `portal` (25/09): entrega também as
    consultas do Portal de Atendimento — só quando o executor pede, pra versão
    antiga dele (que só sabe buscar o pedido no Seller Center) não ler o lugar
    errado."""
    agora = agora or datetime.now(UTC)
    ultima_fala = _ultima_fala_at()
    conds = [
        *condicoes_do_leitor(portal=portal, consultas=consultas),
        or_(
            Chamado.leitura_robo_claim_at.is_(None),
            Chamado.leitura_robo_claim_at < agora - CLAIM_STALE,
        ),
        or_(
            Chamado.leitura_robo_at.is_(None),
            and_(
                ultima_fala >= agora - FRIO,
                Chamado.leitura_robo_at < agora - INTERVALO,
            ),
            and_(
                or_(ultima_fala.is_(None), ultima_fala < agora - FRIO),
                Chamado.leitura_robo_at < agora - INTERVALO_FRIO,
            ),
        ),
    ]
    if contas is not None:
        nomes = sorted({c.strip().lower() for c in contas if (c or "").strip()})
        if not nomes:
            return []
        conds.append(func.lower(func.trim(func.coalesce(Chamado.conta, ""))).in_(nomes))
    q = (
        select(Chamado)
        .where(*conds)
        .order_by(Chamado.leitura_robo_at.asc().nulls_first(), Chamado.created_at)
        .limit(limite)
    )
    if espiar:
        return list((await session.execute(q)).scalars().all())
    rows = (await session.execute(q.with_for_update(skip_locked=True))).scalars().all()
    for ch in rows:
        ch.leitura_robo_claim_at = agora
    await session.commit()
    logger.info("chamados_leitor_fila", casos=len(rows), contas=len(contas or []))
    return list(rows)


def e_devolucao_shopee_da_api(ch: Chamado) -> bool:
    """O chamado é do tipo que o executor de leitura atende — o `/agent/leitor/
    resultado` recusa o resto (a senha dele não escreve em qualquer chamado)."""
    return (
        (ch.origem or "") == "devolucao"
        and (ch.canal or "") == "api"
        and not bool(ch.chamado_de_tela)
        and (ch.plataforma or "").strip().lower() in chamados_svc.apelidos_da_plataforma("shopee")
    )


async def proxima_leitura(
    session: AsyncSession, ch: Chamado, *, ok: bool, agora: datetime | None = None
) -> datetime:
    """Quando a fila devolve este caso — pelos MESMOS três ramos que a `fila` usa.

    Antes isto era sempre "+3 h", e mentia em dois dos três casos documentados: o
    caso frio volta em 24 h, e a leitura que falhou volta pelo vencimento do claim,
    em 30 min. Campo informativo que mente é pior que campo ausente: o robô se
    organiza por ele."""
    agora = agora or datetime.now(UTC)
    if not ok:
        return agora + CLAIM_STALE
    ultima = (
        await session.execute(
            select(func.max(func.coalesce(ChamadoMensagem.enviada_at, ChamadoMensagem.created_at)))
            .where(
                ChamadoMensagem.chamado_id == ch.id,
                ChamadoMensagem.direcao.in_(("enviada", "recebida")),
            )
        )
    ).scalar_one_or_none()
    if ultima is not None and ultima.tzinfo is None:
        ultima = ultima.replace(tzinfo=UTC)
    frio = ultima is None or ultima < agora - FRIO
    return agora + (INTERVALO_FRIO if frio else INTERVALO)


# ------------------------------------------------- o que o robô leu de volta


async def _nossas_falas(session: AsyncSession, ch: Chamado) -> set[str]:
    """Textos que NÓS mandamos neste chamado — o guarda de eco compara com eles."""
    textos = (
        (
            await session.execute(
                select(ChamadoMensagem.texto).where(
                    ChamadoMensagem.chamado_id == ch.id,
                    ChamadoMensagem.direcao == "enviada",
                )
            )
        )
        .scalars()
        .all()
    )
    return {_chave(t) for t in textos if (t or "").strip()}


async def _ja_recebidas(session: AsyncSession, ch: Chamado) -> set[tuple[str, datetime | None]]:
    """(texto, quando) das falas da plataforma que já estão no histórico.

    A chave inclui a HORA de propósito. Só pelo texto, uma fala NOVA com texto
    idêntico a uma antiga sumia em silêncio — e as plataformas respondem com frase
    padronizada o tempo todo ("Estamos analisando sua solicitação"). Como reler a
    mesma página devolve o mesmo `quando`, a proteção contra o texto refluído
    continua valendo."""
    linhas = (
        await session.execute(
            select(ChamadoMensagem.texto, ChamadoMensagem.enviada_at, ChamadoMensagem.created_at)
            .where(
                ChamadoMensagem.chamado_id == ch.id,
                ChamadoMensagem.direcao == "recebida",
            )
        )
    ).all()
    return {
        (_chave(t), (enviada or criada))
        for t, enviada, criada in linhas
        if (t or "").strip()
    }


def _chave(texto: str | None) -> str:
    """Chave de comparação: sem HTML e com os espaços normalizados. A página do
    Seller Center reflui o texto entre uma leitura e outra — comparar cru faria a
    MESMA resposta entrar de novo a cada 3 h e o caso ficaria preso em
    "plataforma respondeu"."""
    return " ".join(limpar_html(texto or "").split()).strip().lower()


def _quando_valido(ch: Chamado, quando: datetime, agora: datetime) -> tuple[datetime, bool]:
    """A hora da tela, com tolerância dos DOIS lados. Data ingênua é lida como
    horário de São Paulo (é o fuso das telas que o robô abre).

    O futuro é óbvio (relógio torto). O passado é o perigoso: "19/09" lido com o ano
    errado enterra a fala no fundo do histórico, a linha não reage, e como o dedupe
    guarda (texto, hora) a releitura seguinte não conserta — o erro fica. Por isso
    qualquer data absurdamente anterior ao nascimento do chamado também é clampada,
    com o mesmo aviso. A folga é generosa (`PASSADO_TOLERADO`): caso da plataforma
    pode ser legitimamente mais velho que a linha do DaVinci."""
    if quando.tzinfo is None:
        quando = quando.replace(tzinfo=chamados_svc.SAO_PAULO)
    if quando > agora + FUTURO_TOLERADO:
        return agora, True
    nascimento = ch.created_at
    if nascimento is not None:
        if nascimento.tzinfo is None:
            nascimento = nascimento.replace(tzinfo=UTC)
        if quando < nascimento - PASSADO_TOLERADO:
            return nascimento, True
    return quando, False


async def gravar_historico(session: AsyncSession, ch: Chamado, texto: str) -> bool:
    """A conversa COMPLETA da página numa mensagem só por chamado, atualizada
    quando muda. `direcao=sistema`: é contexto pra quem analisa, não resposta —
    o cérebro, a fila de envio e o cálculo do Status não a enxergam."""
    texto = limpar_html(texto or "").strip()
    if not texto:
        return False
    m = (
        await session.execute(
            select(ChamadoMensagem)
            .where(
                ChamadoMensagem.chamado_id == ch.id,
                ChamadoMensagem.tipo == TIPO_HISTORICO,
            )
            .order_by(ChamadoMensagem.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()
    if m is not None:
        if (m.texto or "").strip() == texto:
            return False
        m.texto = texto
        return True
    session.add(
        chamados_svc.nova_mensagem(
            ch,
            texto=texto,
            tipo=TIPO_HISTORICO,
            direcao="sistema",
            autor_nome=AUTOR_ROBO_LEITURA,
            status="registrada",
        )
    )
    return True


async def registrar(
    session: AsyncSession,
    ch: Chamado,
    *,
    ok: bool = True,
    erro: str | None = None,
    falas: list[FalaLida] | None = None,
    historico: str | None = None,
    encerrado: bool = False,
    pendencias: list[str] | None = None,
    agora: datetime | None = None,
) -> Resultado:
    """O robô voltou da tela. Commita.

    `pendencias` (25/09, 296012): o que a tela PEDE de nós com prazo ("A Shopee
    pede evidência até 26/09/2026 …"). Entra UMA vez por texto como fala da
    plataforma — a linha sai de "Aguard. Plataforma", a IA de Chamado lê e a
    pessoa vê o prazo antes de vencer.

    `ok=False`: nada é gravado e `leitura_robo_at` NÃO avança (o caso continua
    "não lido"); o claim de 30 min vira o backoff natural. Leitura que para de
    funcionar tem que ser vista — foi o silêncio que criou este problema: quem
    cobra é o Vigia Robô Leitura de Chamados (caso que não avança)."""
    agora = agora or datetime.now(UTC)
    plat = (ch.plataforma or "").strip().lower() or None
    if not ok:
        ch.leitura_robo_claim_at = agora
        await session.commit()
        logger.warning(
            "chamados_leitura_falhou", chamado_id=str(ch.id), plat=plat, err=(erro or "")[:200]
        )
        return Resultado()

    out = Resultado()
    nossas = await _nossas_falas(session, ch)
    vistas = await _ja_recebidas(session, ch)
    for fala in falas or []:
        texto = limpar_html(fala.texto or "").strip()
        if not texto:
            continue
        chave = _chave(texto)
        if chave in nossas:
            # A nossa própria fala voltando da página. Deixá-la entrar como
            # `recebida` jogaria a coluna "Últ. resposta" pro lado errado e a
            # linha pediria gente à toa.
            out.ecos += 1
            continue
        quando, clampada = _quando_valido(ch, fala.quando, agora)
        if (chave, quando) in vistas:
            out.duplicadas += 1
            continue
        m = chamados_svc.nova_mensagem(
            ch,
            texto=texto,
            tipo="resposta",
            direcao="recebida",
            autor_nome=(fala.autor or "").strip() or AUTOR_ROBO_LEITURA,
            status="registrada",
        )
        m.canal = "robo"
        m.created_at = quando
        m.enviada_at = quando
        session.add(m)
        vistas.add((chave, quando))
        out.falas_novas += 1
        if clampada:
            session.add(
                chamados_svc.registrar_sistema(
                    ch,
                    "Hora da fala ilegível na tela da plataforma — gravada com a hora da leitura.",
                )
            )
    if pendencias:
        ja = {
            _chave(t)
            for t in (
                await session.execute(
                    select(ChamadoMensagem.texto).where(
                        ChamadoMensagem.chamado_id == ch.id,
                        ChamadoMensagem.direcao == "recebida",
                    )
                )
            ).scalars()
        }
        for p in pendencias:
            texto = (p or "").strip()
            if not texto or _chave(texto) in ja:
                continue
            m = chamados_svc.nova_mensagem(
                ch,
                texto=texto,
                tipo="resposta",
                direcao="recebida",
                autor_nome="Shopee (pedido na tela)",
                status="registrada",
            )
            m.canal = "robo"
            m.created_at = m.enviada_at = agora
            session.add(m)
            ja.add(_chave(texto))
            out.pendencias_novas += 1
    if historico:
        out.historico_alterado = await gravar_historico(session, ch, historico)
    if encerrado:
        chamados_svc.encerrado_pelo_robo(session, ch)
    out.encerrado = bool(ch.resolvido or ch.status_plataforma in chamados_svc.STATUS_FINAIS)
    ch.leitura_robo_at = agora
    ch.leitura_robo_claim_at = None
    await session.commit()
    logger.info(
        "chamados_leitura_registrada",
        chamado_id=str(ch.id),
        plat=plat,
        falas_novas=out.falas_novas,
        ecos=out.ecos,
        duplicadas=out.duplicadas,
        historico=out.historico_alterado,
        encerrado=out.encerrado,
    )
    return out


async def furar_a_fila(session: AsyncSession, ch: Chamado) -> None:
    """Botão Atualizar num caso de tela: não há API pra consultar, então o que
    "atualizar" pode significar é "lê este antes dos outros" — zera a âncora e o
    caso vai pro topo da fila (NULLS FIRST) no próximo poll do robô."""
    ch.leitura_robo_at = None
    ch.leitura_robo_claim_at = None
    await session.commit()


async def e_caso_de_tela(session: AsyncSession, ch: Chamado) -> bool:
    """Este chamado foi aberto na TELA? (a abertura saiu pelo robô, o número em
    `chamado` é protocolo de tela e nenhuma API sabe responder por ele)"""
    achou = (
        await session.execute(
            select(ChamadoMensagem.id)
            .where(
                ChamadoMensagem.chamado_id == ch.id,
                ChamadoMensagem.tipo == "abertura",
                ChamadoMensagem.canal == "robo",
                ChamadoMensagem.status == "enviada",
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return achou is not None
