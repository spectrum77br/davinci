"""Flex por anúncio — a tela (Pós-venda → Logística, aba Flex › Anúncios).

Projeto Flex, etapa 3 (services/flex_motor). Mesmo recurso da Logística:
`logistica:view` para ver, `logistica:edit` para agir (sincronizar, aprovar,
emergência); admin passa sempre (`require_permission`).

  GET  /api/flex/config        modo, contas (com o Flex DA CONTA: assinatura
                               do ML / canal da loja Shopee, e a descoberta),
                               números (nada de segredo)
  GET  /api/flex/anuncios      estado por anúncio (filtros, busca e o resumo
                               do topo da tela)
  POST /api/flex/sincronizar   roda o motor agora (job no worker), no modo atual
  POST /api/flex/anuncios/{conta}/{anuncio}/aprovar   aprova LIGAR (rodada
                               ocupada: a ligação vai para a fila do worker)
  POST /api/flex/emergencia    tira as aprovações e põe na fila o job que
                               desliga tudo das contas permitidas
                               (observar/desligado: só simula)
  GET  /api/flex/emergencia/ultima   a última emergência (andamento)
  GET  /api/flex/emergencia/{id}     o andamento de uma emergência
  GET  /api/flex/log           últimas linhas da trilha
  GET  /api/flex/pedidos       pedidos Flex detectados (com o aviso do .sp;
                               `abertos=true` tira os que já saíram — menos
                               os que saíram sem passar pelo .sp e esperam o
                               acerto do estoque)
  POST /api/flex/pedidos/{bling_id}/acertado   a pessoa acertou o estoque
                               no Bling (o saldo Flex para de descontar)

Quem vê (`flex_usuarios`, 05/10/2026): só as pessoas da lista — para as
outras, inclusive admin, TODA rota daqui responde o 404 de rota que não existe
(como Sistema › Histórico) e o router fica fora da documentação (/api/docs).
`GET /api/flex/acesso` é como a tela descobre se mostra a aba.

Escopo por equipe (deps/team_scope): usuário com equipe só vê e só mexe nas
contas da equipe — anúncios, resumo, trilha, pedidos, aprovar e emergência
(admin e quem não tem equipe: tudo), como a Logística (`?envio=flex`).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, case, exists, false, func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import worker_pool
from app.config import get_settings
from app.db import get_session
from app.deps.auth import get_current_user, require_permission
from app.deps.team_scope import resolve_team_scope
from app.models import (
    BlingOrder,
    FlexAnuncioEstado,
    FlexConta,
    FlexEmergencia,
    FlexLog,
    FlexPedido,
    Integration,
    Listing,
    ProductLink,
    User,
)
from app.models.flex import FLEX_ACOES
from app.schemas.flex import (
    FlexAcertoOut,
    FlexAnuncioOut,
    FlexAnunciosOut,
    FlexAprovarOut,
    FlexConfigOut,
    FlexContaOut,
    FlexEmergenciaOut,
    FlexLogOut,
    FlexPedidoOut,
    FlexResumoOut,
    FlexSincronizarOut,
)
from app.services import flex_config, flex_envio, flex_motor, flex_textos

logger = structlog.get_logger()


async def _so_quem_ve(user: Annotated[User | None, Depends(get_current_user)]) -> None:
    # Igual ao 404 do FastAPI para rota desconhecida: quem não está na lista
    # não descobre nem que o Flex existe.
    if not flex_config.pode_ver(user):
        raise HTTPException(404, detail="Not Found")


router = APIRouter(
    prefix="/api/flex",
    tags=["flex"],
    include_in_schema=False,
    dependencies=[Depends(_so_quem_ve)],
)

Ver = Annotated[User, Depends(require_permission("logistica", "view"))]
Agir = Annotated[User, Depends(require_permission("logistica", "edit"))]
Sessao = Annotated[AsyncSession, Depends(get_session)]


def _plataforma_txt(integ: Integration | None) -> str | None:
    if integ is None:
        return None
    return getattr(integ.platform, "value", str(integ.platform))


async def _escopo(session: AsyncSession, user: User) -> frozenset[UUID] | None:
    """Contas (integration_id) que o usuário enxerga; None = todas (admin ou
    sem equipe). A mesma régua das outras telas (deps/team_scope)."""
    scope = await resolve_team_scope(session, user)
    return None if scope.unrestricted else frozenset(scope.integration_ids)


def _no_escopo(coluna, escopo: frozenset[UUID] | None):
    """Filtro da coluna de conta pelo escopo (None = sem filtro)."""
    if escopo is None:
        return None
    return coluna.in_(list(escopo)) if escopo else false()


async def _permitidas(session: AsyncSession) -> frozenset[UUID]:
    """As contas em que o motor mexe AGORA (flex_contas, ativas, ML/Shopee).
    Conta que saiu da lista fica com o estado antigo no banco — o "esperando
    aprovação" dela não vale mais (o motor não regrava conta fora da lista)."""
    ids = flex_config.contas()
    return frozenset((await flex_motor.integracoes_permitidas(session, ids)).keys())


@router.get("/acesso")
async def acesso() -> dict[str, bool]:
    """A tela mostra a aba Flex só quando isto responde 200."""
    return {"ok": True}


@router.get("/config", response_model=FlexConfigOut)
async def config(session: Sessao, user: Ver) -> FlexConfigOut:
    s = get_settings()
    cfg = flex_motor.ConfigFlex.das_configuracoes()
    escopo = await _escopo(session, user)
    ids = sorted(
        (i for i in flex_config.contas() if escopo is None or i in escopo), key=str
    )
    permitidas = await flex_motor.integracoes_permitidas(session, ids)
    # O Flex de cada conta como o motor deixou (a tela não chama a plataforma).
    linhas = (
        {
            c.integration_id: c
            for c in (
                await session.execute(select(FlexConta).where(FlexConta.integration_id.in_(ids)))
            )
            .scalars()
            .all()
        }
        if ids
        else {}
    )
    contas = []
    for i in ids:
        integ = permitidas.get(i)
        c = linhas.get(i)
        plataforma = _plataforma_txt(integ)
        motivo = None
        if c is not None and c.flex_ativo is False:
            motivo = flex_motor.bloqueio_da_conta(
                c.plataforma,
                flex_motor.ContaFlex(c.plataforma, c.flex_ativo, c.status, c.detalhe),
            )
        contas.append(
            FlexContaOut(
                id=i,
                nome=integ.name if integ is not None else None,
                plataforma=plataforma,
                existe=integ is not None,
                flex_ativo=c.flex_ativo if c else None,
                flex_status=c.status if c else None,
                flex_detalhe=c.detalhe if c else None,
                flex_motivo=motivo,
                flex_lido_em=c.lido_em if c else None,
                flex_erro=c.erro if c else None,
                descoberta_em=c.descoberta_em if c else None,
                descoberta_ok=c.descoberta_ok if c else None,
                descoberta_total=c.descoberta_total if c else None,
                descoberta_novos=c.descoberta_novos if c else None,
                descoberta_erro=c.descoberta_erro if c else None,
            )
        )
    m = flex_config.modo()
    return FlexConfigOut(
        modo=m,
        pode_escrever=flex_config.pode_escrever(m),
        contas=contas,
        n_liga=cfg.n_liga,
        n_desliga=cfg.n_desliga,
        kits=cfg.kits,
        max_anuncios_por_familia=cfg.max_anuncios_por_familia,
        teto_escritas_por_rodada=max(0, int(s.flex_teto_escritas_por_rodada or 0)),
        shopee_canais=sorted(flex_envio.canais_shopee_flex()),
        shopee_escrita=bool(s.flex_shopee_escrita),
        intervalo_min=max(1, int(s.flex_intervalo_min or 15)),
        pedido_no_sp=bool(s.flex_pedido_no_sp),
    )


def _importado_do_estado():
    """O anúncio importado (`listings`) da linha do estado. Na Shopee a
    importação às vezes grava `item_model` (um por modelo) — o anúncio é a
    primeira parte (o mesmo `_id_anuncio` do motor)."""
    est = FlexAnuncioEstado
    return and_(
        Listing.integration_id == est.integration_id,
        or_(
            Listing.external_id == est.external_id,
            and_(
                est.plataforma == flex_envio.PLATAFORMA_SHOPEE,
                func.split_part(Listing.external_id, "_", 1) == est.external_id,
            ),
        ),
    )


def _titulo_subq():
    """Título do anúncio — o do primeiro vínculo que tiver; sem vínculo (o
    anúncio só importado, que o motor desliga), o da importação. Antes o só
    importado vinha sem título e a busca pelo nome não o achava."""
    do_vinculo = (
        select(func.min(ProductLink.listing_title))
        .where(
            ProductLink.integration_id == FlexAnuncioEstado.integration_id,
            ProductLink.external_id == FlexAnuncioEstado.external_id,
        )
        .correlate(FlexAnuncioEstado)
        .scalar_subquery()
    )
    da_importacao = (
        select(func.min(Listing.title))
        .where(_importado_do_estado())
        .correlate(FlexAnuncioEstado)
        .scalar_subquery()
    )
    return func.coalesce(do_vinculo, da_importacao)


def _anuncio_out(
    est: FlexAnuncioEstado,
    conta: str | None,
    titulo: str | None,
    permitidas: frozenset[UUID] | None = None,
) -> FlexAnuncioOut:
    out = FlexAnuncioOut.model_validate(est)
    out.conta = conta
    out.titulo = titulo
    out.motivo_claro = flex_textos.motivo_claro(est.motivo)
    if permitidas is not None and est.integration_id not in permitidas:
        # Conta fora de flex_contas: ninguém aprova (nem o motor regrava) —
        # "esperando aprovação" ali seria um aviso que nada limpa.
        out.aguardando_aprovacao = False
    return out


def _aguardando(permitidas: frozenset[UUID]):
    """Esperando aprovação DE VERDADE: só nas contas em que o motor mexe."""
    est = FlexAnuncioEstado
    if not permitidas:
        return false()
    return and_(est.aguardando_aprovacao.is_(True), est.integration_id.in_(list(permitidas)))


async def _resumo(
    session: AsyncSession, escopo: frozenset[UUID] | None, permitidas: frozenset[UUID]
) -> FlexResumoOut:
    """O quadro do topo da tela: todos os anúncios avaliados, sem os filtros
    da lista (o dono vê de cara quantos esperam por ele) — dentro do escopo
    da equipe."""
    est = FlexAnuncioEstado
    q = select(
        func.count(),
        func.count().filter(est.observado == "ligado"),
        func.count().filter(_aguardando(permitidas)),
        func.count().filter(est.observado == "ligado", est.desejado != "ligado"),
        func.count().filter(est.observado.is_(None)),
    ).select_from(est)
    filtro = _no_escopo(est.integration_id, escopo)
    if filtro is not None:
        q = q.where(filtro)
    row = (await session.execute(q)).one()
    return FlexResumoOut(
        avaliados=int(row[0] or 0),
        ligados=int(row[1] or 0),
        aguardando=int(row[2] or 0),
        desligar=int(row[3] or 0),
        nao_lidos=int(row[4] or 0),
    )


@router.get("/anuncios", response_model=FlexAnunciosOut)
async def anuncios(
    session: Sessao,
    user: Ver,
    integration_id: UUID | None = None,
    plataforma: Literal["ml", "shopee"] | None = None,
    desejado: Literal["ligado", "desligado", "inelegivel"] | None = None,
    # O que a plataforma disse da última leitura; `nao_lido` = ainda não leu.
    observado: Literal["ligado", "desligado", "nao_lido"] | None = None,
    aguardando: bool | None = None,
    # `desligar=true`: ligado na plataforma, mas a regra não quer — o mesmo
    # número do resumo ("ligados que deveriam desligar").
    desligar: bool = False,
    # Busca da tela: id do anúncio, família ou título (sem curinga: `%`/`_`
    # digitados são letras).
    busca: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> FlexAnunciosOut:
    escopo = await _escopo(session, user)
    permitidas = await _permitidas(session)
    filtros = []
    if (f := _no_escopo(FlexAnuncioEstado.integration_id, escopo)) is not None:
        filtros.append(f)
    if integration_id is not None:
        filtros.append(FlexAnuncioEstado.integration_id == integration_id)
    if plataforma is not None:
        filtros.append(FlexAnuncioEstado.plataforma == plataforma)
    if desejado is not None:
        filtros.append(FlexAnuncioEstado.desejado == desejado)
    if observado == "nao_lido":
        filtros.append(FlexAnuncioEstado.observado.is_(None))
    elif observado is not None:
        filtros.append(FlexAnuncioEstado.observado == observado)
    if aguardando is True:
        filtros.append(_aguardando(permitidas))
    elif aguardando is False:
        filtros.append(not_(_aguardando(permitidas)))
    if desligar:
        filtros.append(FlexAnuncioEstado.observado == "ligado")
        filtros.append(FlexAnuncioEstado.desejado != "ligado")
    termo = (busca or "").strip()
    if termo:
        filtros.append(
            or_(
                FlexAnuncioEstado.external_id.icontains(termo, autoescape=True),
                FlexAnuncioEstado.familias.icontains(termo, autoescape=True),
                exists().where(
                    ProductLink.integration_id == FlexAnuncioEstado.integration_id,
                    ProductLink.external_id == FlexAnuncioEstado.external_id,
                    ProductLink.listing_title.icontains(termo, autoescape=True),
                ),
                exists().where(
                    _importado_do_estado(),
                    Listing.title.icontains(termo, autoescape=True),
                ),
            )
        )
    total = int(
        await session.scalar(select(func.count()).select_from(FlexAnuncioEstado).where(*filtros))
        or 0
    )
    rows = await session.execute(
        select(FlexAnuncioEstado, Integration.name, _titulo_subq())
        .outerjoin(Integration, Integration.id == FlexAnuncioEstado.integration_id)
        .where(*filtros)
        # Quem espera uma pessoa primeiro; depois o que está/quer ligado.
        .order_by(
            case((_aguardando(permitidas), 0), else_=1),
            case((FlexAnuncioEstado.observado == "ligado", 0), else_=1),
            case((FlexAnuncioEstado.desejado == "ligado", 0), else_=1),
            FlexAnuncioEstado.atualizado_em.desc(),
            FlexAnuncioEstado.external_id,
        )
        .limit(limit)
        .offset(offset)
    )
    return FlexAnunciosOut(
        total=total,
        itens=[_anuncio_out(est, nome, titulo, permitidas) for est, nome, titulo in rows.all()],
        resumo=await _resumo(session, escopo, permitidas),
    )


@router.post("/sincronizar", response_model=FlexSincronizarOut)
async def sincronizar(user: Agir) -> FlexSincronizarOut:
    """Roda o motor agora, no modo em vigor (o worker pega o job em
    segundos — a rodada pode levar minutos com muitos anúncios, por isso não
    roda dentro do pedido HTTP). Modo desligado: não faz nada."""
    m = flex_config.modo()
    if m == flex_config.MODO_DESLIGADO:
        return FlexSincronizarOut(enfileirado=False, modo=m, motivo="flex_modo=desligado")
    if not flex_config.contas():
        return FlexSincronizarOut(enfileirado=False, modo=m, motivo="nenhuma conta em flex_contas")
    pool = await worker_pool.get_arq_pool()
    # Um clique por minuto vira UMA rodada (id do job pelo minuto).
    job = await pool.enqueue_job(
        "flex_motor_run", str(user.id), _job_id=f"flex_motor_manual:{int(time.time() // 60)}"
    )
    return FlexSincronizarOut(
        enfileirado=job is not None,
        modo=m,
        motivo=None if job is not None else "já há uma rodada pedida neste minuto",
    )


async def _estado_out(session: AsyncSession, integration_id: UUID, external_id: str):
    row = (
        await session.execute(
            select(FlexAnuncioEstado, Integration.name, _titulo_subq())
            .outerjoin(Integration, Integration.id == FlexAnuncioEstado.integration_id)
            .where(
                FlexAnuncioEstado.integration_id == integration_id,
                FlexAnuncioEstado.external_id == external_id,
            )
        )
    ).first()
    if row is None:
        return None
    est, nome, titulo = row
    return _anuncio_out(est, nome, titulo, await _permitidas(session))


_ERRO_HTTP = {
    "conta_nao_permitida": status.HTTP_403_FORBIDDEN,
    "nao_avaliado": status.HTTP_404_NOT_FOUND,
}


@router.post(
    "/anuncios/{integration_id}/{external_id}/aprovar", response_model=FlexAprovarOut
)
async def aprovar(
    integration_id: UUID, external_id: str, session: Sessao, user: Agir
) -> FlexAprovarOut:
    """Aprova LIGAR o Flex neste anúncio (a orientação do ML é que a ativação
    seja decisão deliberada do vendedor). Em piloto/ativo o motor aplica na
    hora; em observar fica registrada. Depois de uma recusa da plataforma,
    é também o "tente de novo". Conta fora da equipe do usuário: 403."""
    escopo = await _escopo(session, user)
    if escopo is not None and integration_id not in escopo:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={"code": "fora_do_escopo", "detalhe": "a conta não é da sua equipe"},
        )
    try:
        res = await flex_motor.aprovar(integration_id, external_id.strip(), por=user.id)
    except flex_motor.FlexRegraError as exc:
        raise HTTPException(
            _ERRO_HTTP.get(exc.codigo, status.HTTP_409_CONFLICT),
            detail={"code": exc.codigo, "detalhe": exc.detalhe},
        ) from exc
    na_fila = False
    if res.get("ocupado"):
        # A rodada do motor estava no meio (segura a trava de 4 a 8 min a cada
        # 15): em vez de "tenta na próxima rodada" — que esperava a fila
        # inteira de desligar —, um job tenta de novo daqui a pouco, só para
        # este anúncio. Um pedido por anúncio (o id do job).
        ext = external_id.strip()
        try:
            pool = await worker_pool.get_arq_pool()
            job = await pool.enqueue_job(
                "flex_aprovado_run",
                str(integration_id),
                ext,
                str(user.id),
                _job_id=f"flex_aprovado:{integration_id}:{ext}",
                _defer_by=30,
            )
            na_fila = True
            if job is None:
                logger.info("flex_aprovado_ja_na_fila", external_id=ext)
        except Exception as exc:  # noqa: BLE001 — sem fila: a varredura liga
            logger.warning("flex_aprovado_fila_falhou", erro=str(exc)[:200])
    session.expire_all()
    return FlexAprovarOut(
        aprovado=bool(res.get("aprovado")),
        modo=str(res.get("modo")),
        aplicado=bool(res.get("aplicado")),
        motor=res.get("motor"),
        na_fila=na_fila,
        estado=await _estado_out(session, integration_id, external_id.strip()),
    )


def _emergencia_out(linha: FlexEmergencia, *, ja_em_andamento: bool = False) -> FlexEmergenciaOut:
    resumo = dict(linha.resumo or {})
    return FlexEmergenciaOut(
        id=linha.id,
        status=linha.status,
        modo=linha.modo,
        escreve=bool(resumo.get("escreve")),
        pedido_em=linha.pedido_em,
        iniciado_em=linha.iniciado_em,
        terminado_em=linha.terminado_em,
        erro=linha.erro,
        ja_em_andamento=ja_em_andamento,
        resumo=resumo,
    )


def _emergencias_visiveis(user: User, escopo: frozenset[UUID] | None):
    """Admin e quem não tem equipe vê todas; usuário com equipe, as que pediu."""
    return None if escopo is None else FlexEmergencia.por == user.id


# Uma emergência "na fila" há mais que isto, ou "rodando" sem andamento
# gravado há mais que isto (o job grava a cada poucos segundos), é de um job
# que morreu (worker reiniciado no meio): o botão volta a criar outra.
_EMERGENCIA_VALE = timedelta(minutes=10)


@router.post("/emergencia", response_model=FlexEmergenciaOut)
async def emergencia(session: Sessao, user: Agir) -> FlexEmergenciaOut:
    """Desliga o Flex de tudo nas contas permitidas e tira toda aprovação.

    O pedido HTTP só tira as aprovações (na hora — nada volta a ligar) e põe
    na fila o job do worker (`flex_emergencia_run`) que desliga. Antes as
    escritas rodavam aqui dentro, em série, até 300 por clique: minutos com o
    botão girando e 7 cliques numa conta grande. A tela consulta o andamento
    em GET /emergencia/{id}. Em observar (ou desligado) só simula — sem
    efeito nenhum. Usuário com equipe: só as contas da equipe. Já há uma
    emergência na fila ou rodando: devolve ela (não cria outra)."""
    escopo = await _escopo(session, user)
    logger.warning(
        "flex_emergencia_pedida",
        user_id=str(user.id),
        contas_escopo=None if escopo is None else len(escopo),
    )
    corte = datetime.now(UTC) - _EMERGENCIA_VALE
    filtros = [
        or_(
            and_(FlexEmergencia.status == "na_fila", FlexEmergencia.pedido_em >= corte),
            and_(FlexEmergencia.status == "rodando", FlexEmergencia.atualizado_em >= corte),
        )
    ]
    if (f := _emergencias_visiveis(user, escopo)) is not None:
        filtros.append(f)
    andamento = (
        await session.execute(
            select(FlexEmergencia)
            .where(*filtros)
            .order_by(FlexEmergencia.pedido_em.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if andamento is not None:
        return _emergencia_out(andamento, ja_em_andamento=True)

    prep = await flex_motor.preparar_emergencia(por=user.id, contas_escopo=escopo)
    if prep.get("id") is None:
        resumo = {k: v for k, v in prep.items() if k != "id"}
        return FlexEmergenciaOut(
            modo=str(prep.get("modo")), escreve=bool(prep.get("escreve")), resumo=resumo
        )
    emergencia_id = int(prep["id"])
    try:
        pool = await worker_pool.get_arq_pool()
        await pool.enqueue_job(
            "flex_emergencia_run", emergencia_id, _job_id=f"flex_emergencia:{emergencia_id}"
        )
    except Exception as exc:  # noqa: BLE001 — sem fila: avisa (as aprovações já saíram)
        logger.error("flex_emergencia_fila_falhou", id=emergencia_id, erro=str(exc)[:200])
        linha = await session.get(FlexEmergencia, emergencia_id)
        if linha is not None:
            linha.status = "falhou"
            linha.erro = f"não deu para pôr o job na fila: {str(exc)[:300]}"
            linha.terminado_em = datetime.now(UTC)
            await session.commit()
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "fila_indisponivel",
                "detalhe": "as aprovações já saíram, mas o desligamento não começou",
            },
        ) from exc
    linha = await session.get(FlexEmergencia, emergencia_id)
    return _emergencia_out(linha)


@router.get("/emergencia/ultima", response_model=FlexEmergenciaOut | None)
async def emergencia_ultima(session: Sessao, user: Ver) -> FlexEmergenciaOut | None:
    """A última emergência que o usuário pode ver — a tela retoma o
    andamento depois de recarregar a página."""
    q = select(FlexEmergencia).order_by(FlexEmergencia.pedido_em.desc()).limit(1)
    if (f := _emergencias_visiveis(user, await _escopo(session, user))) is not None:
        q = q.where(f)
    linha = (await session.execute(q)).scalar_one_or_none()
    return None if linha is None else _emergencia_out(linha)


@router.get("/emergencia/{emergencia_id}", response_model=FlexEmergenciaOut)
async def emergencia_andamento(
    emergencia_id: int, session: Sessao, user: Ver
) -> FlexEmergenciaOut:
    q = select(FlexEmergencia).where(FlexEmergencia.id == emergencia_id)
    if (f := _emergencias_visiveis(user, await _escopo(session, user))) is not None:
        q = q.where(f)
    linha = (await session.execute(q)).scalar_one_or_none()
    if linha is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "emergencia_nao_encontrada"})
    return _emergencia_out(linha)


@router.get("/log", response_model=list[FlexLogOut])
async def log(
    session: Sessao,
    user: Ver,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    integration_id: UUID | None = None,
    external_id: str | None = None,
    acao: str | None = None,
) -> list[FlexLogOut]:
    if acao is not None and acao not in FLEX_ACOES:
        raise HTTPException(422, detail={"code": "acao_invalida"})
    filtros = []
    if (f := _no_escopo(FlexLog.integration_id, await _escopo(session, user))) is not None:
        filtros.append(f)
    if integration_id is not None:
        filtros.append(FlexLog.integration_id == integration_id)
    if external_id:
        filtros.append(FlexLog.external_id == external_id.strip())
    if acao:
        filtros.append(FlexLog.acao == acao)
    rows = await session.execute(
        select(FlexLog, Integration.name)
        .outerjoin(Integration, Integration.id == FlexLog.integration_id)
        .where(*filtros)
        .order_by(FlexLog.criado_em.desc(), FlexLog.id.desc())
        .limit(limit)
    )
    out: list[FlexLogOut] = []
    for linha, nome in rows.all():
        item = FlexLogOut.model_validate(linha)
        item.conta = nome
        out.append(item)
    return out


def _pedido_no_escopo(scope):
    """Pedido Flex da equipe: pela conta gravada OU pela loja do pedido no
    Bling (o pedido marcado só pela Logística ainda não tem conta)."""
    if scope.unrestricted:
        return None
    ors = []
    if scope.integration_ids:
        ors.append(FlexPedido.integration_id.in_(list(scope.integration_ids)))
    if scope.bling_store_ids:
        ors.append(
            exists().where(
                BlingOrder.bling_id == FlexPedido.bling_id,
                BlingOrder.loja.in_(list(scope.bling_store_ids)),
            )
        )
    return or_(*ors) if ors else false()


def _saiu_sem_sp():
    """Saiu (em andamento/atendido) sem ter ido ao .sp e sem o acerto do
    estoque: continua na lista (e no desconto do saldo Flex). Filtro largo do
    banco — a conferência pelos SKUs (só conta peça com lote fora do .sp) é
    `flex_motor.acerto_pendente`, na resposta."""
    return and_(
        FlexPedido.no_sp.is_(False),
        FlexPedido.acertado_em.is_(None),
        exists().where(
            BlingOrder.bling_id == FlexPedido.bling_id,
            BlingOrder.situacao.in_(flex_motor.SITUACOES_SAIU),
        ),
    )


@router.get("/pedidos", response_model=list[FlexPedidoOut])
async def pedidos(
    session: Sessao,
    user: Ver,
    so_alerta: bool = False,
    # Só os que ainda esperam alguém: some o pedido que no Bling já saiu (em
    # andamento), foi atendido, cancelado ou excluído — a régua do saldo Flex.
    # Pedido que o espelho ainda não tem continua (não se sabe: mostra). O que
    # SAIU sem passar pelo .sp continua até o acerto do estoque.
    abertos: bool = False,
    dias: Annotated[int, Query(ge=1, le=90)] = 30,
    limit: Annotated[int, Query(ge=1, le=1000)] = 300,
) -> list[FlexPedidoOut]:
    """Pedidos Flex detectados nos últimos `dias`, os com aviso (o .sp não
    cobre — etapa 2) primeiro, depois pelo prazo de despacho. `so_alerta`:
    os com aviso E os que saíram sem passar pelo .sp (acerto pendente) —
    estes, sem o corte de dias: valem até alguém acertar."""
    corte = datetime.now(UTC) - timedelta(days=dias)
    filtros = [or_(FlexPedido.detectado_em >= corte, _saiu_sem_sp())]
    if (f := _pedido_no_escopo(await resolve_team_scope(session, user))) is not None:
        filtros.append(f)
    if so_alerta:
        filtros.append(or_(FlexPedido.alerta.is_not(None), _saiu_sem_sp()))
    if abertos:
        filtros.append(
            or_(
                ~exists().where(
                    BlingOrder.bling_id == FlexPedido.bling_id,
                    BlingOrder.situacao.in_(flex_motor.SITUACOES_FECHADAS),
                ),
                _saiu_sem_sp(),
            )
        )
    rows = await session.execute(
        select(FlexPedido, Integration.name)
        .outerjoin(Integration, Integration.id == FlexPedido.integration_id)
        .where(and_(*filtros))
        .order_by(
            case((FlexPedido.alerta.is_not(None), 0), else_=1),
            FlexPedido.prazo.asc().nulls_last(),
            FlexPedido.detectado_em.desc(),
        )
        .limit(limit)
    )
    linhas = rows.all()
    ids = [fp.bling_id for fp, _ in linhas]
    itens: dict[int, dict] = {}
    if ids:
        for r in (
            await session.execute(
                select(
                    BlingOrder.bling_id,
                    BlingOrder.numero,
                    BlingOrder.situacao,
                    BlingOrder.item_codigo,
                )
                .where(BlingOrder.bling_id.in_(ids))
                .order_by(BlingOrder.bling_id, BlingOrder.item_index)
            )
        ).all():
            d = itens.setdefault(
                int(r.bling_id), {"numero": r.numero, "situacao": r.situacao, "skus": []}
            )
            if r.item_codigo:
                d["skus"].append(r.item_codigo)
    out: list[FlexPedidoOut] = []
    for fp, nome in linhas:
        info = itens.get(fp.bling_id, {})
        skus = info.get("skus", [])
        pendente = flex_motor.acerto_pendente(info.get("situacao"), fp.no_sp, fp.acertado_em, skus)
        # O banco trouxe largo (`_saiu_sem_sp`); aqui só fica o que espera
        # mesmo o acerto — o resto segue as réguas de sempre.
        if not pendente:
            if fp.detectado_em < corte:
                continue
            if so_alerta and fp.alerta is None:
                continue
            if abertos and info.get("situacao") in flex_motor.SITUACOES_FECHADAS:
                continue
        out.append(
            FlexPedidoOut(
                bling_id=fp.bling_id,
                numero=info.get("numero"),
                plataforma=fp.plataforma,
                integration_id=fp.integration_id,
                conta=nome,
                numeroloja=fp.numeroloja,
                envio_tipo=fp.envio_tipo,
                prazo=fp.prazo,
                detectado_em=fp.detectado_em,
                no_sp=fp.no_sp,
                alerta=fp.alerta,
                situacao=info.get("situacao"),
                skus=skus,
                acerto_pendente=pendente,
                acertado_em=fp.acertado_em,
            )
        )
    return out


@router.post("/pedidos/{bling_id}/acertado", response_model=FlexAcertoOut)
async def acertado(bling_id: int, session: Sessao, user: Agir) -> FlexAcertoOut:
    """A pessoa acertou no Bling o estoque do pedido Flex que saiu de São
    Bernardo sem passar pelo .sp (a transferência do lote vendido para o .sp):
    o saldo Flex para de descontar o pedido. Só para pedido que saiu."""
    escopo = await resolve_team_scope(session, user)
    filtros = [FlexPedido.bling_id == bling_id]
    if (f := _pedido_no_escopo(escopo)) is not None:
        filtros.append(f)
    fp = (await session.execute(select(FlexPedido).where(*filtros))).scalar_one_or_none()
    if fp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "pedido_nao_encontrado"})
    itens = (
        await session.execute(
            select(BlingOrder.situacao, BlingOrder.item_codigo).where(
                BlingOrder.bling_id == bling_id
            )
        )
    ).all()
    situacao = itens[0][0] if itens else None
    if not flex_motor.acerto_pendente(situacao, fp.no_sp, fp.acertado_em, [c for _, c in itens]):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "sem_acerto_pendente",
                "detalhe": "o pedido não saiu sem passar pelo .sp (ou já foi acertado)",
            },
        )
    agora = datetime.now(UTC)
    fp.acertado_em = agora
    fp.acertado_por = user.id
    fp.atualizado_em = agora
    session.add(
        FlexLog(
            integration_id=fp.integration_id,
            plataforma=fp.plataforma,
            bling_id=fp.bling_id,
            sku=", ".join(c for _, c in itens if c) or None,
            acao="acertar_estoque",
            modo=flex_config.modo(),
            resultado="ok",
            motivo="estoque do pedido Flex acertado no Bling (saiu sem passar pelo .sp)",
            por=user.id,
        )
    )
    await session.commit()
    return FlexAcertoOut(bling_id=bling_id, acertado_em=agora)
