"""Ouvidoria › Robôs — a tela que mostra o que os robôs da casa estão fazendo.

Vinicius, 21/09/2026: seção nova no menu, a última antes de Admin. Duas
abas: **Robôs** (catálogo: modo ligado/silencioso/desligado, última rodada,
quem avisa, saúde, botão "Rodar agora") e **Ocorrências** (o que os robôs
encontraram: quem precisa de pessoa, o que fechou sozinho, o que alguém
tratou).

Recurso de permissão: `ouvidoria` (view lê; edit muda modo/destinatários,
roda agora, trata/ignora/reabre). As regras vivem em `services/ouvidoria`;
aqui é leitura + os botões.

`RUNNERS` é o registro "chave do robô → função que roda uma varredura". O
import é tardio de propósito: o serviço de cada robô puxa clients de
marketplace, Bling, etc., e o router não pode custar isso no boot da API.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session, is_unique_violation
from app.deps.auth import require_permission
from app.models import OuvidoriaOcorrencia, OuvidoriaRobo, OuvidoriaRodada, User
from app.schemas.ouvidoria import (
    ContaOut,
    DestinatarioOut,
    OcorrenciaOut,
    OcorrenciasPage,
    PorRoboOut,
    ResumoOut,
    RoboDetalheOut,
    RoboOut,
    RoboPatch,
    RodadaOut,
    RodarOut,
    StatusFiltro,
)
from app.services import ouvidoria as svc
from app.services import threema

logger = structlog.get_logger()
router = APIRouter(prefix="/api/ouvidoria", tags=["ouvidoria"])


def _runner(chave: str) -> Callable[[], Callable[[], Awaitable[dict]]]:
    """Fábrica do "Rodar agora" de um robô cujo serviço se chama
    `app.services.<chave>` e expõe `<chave>_sweep` — é a convenção dos 6
    robôs de 22/09. Import tardio: o serviço puxa clients de marketplace e
    Bling, e o boot da API não paga isso."""

    def _fabrica() -> Callable[[], Awaitable[dict]]:
        import importlib

        mod = importlib.import_module(f"app.services.{chave}")
        return getattr(mod, f"{chave}_sweep")

    return _fabrica


def _lock(chave: str) -> Callable[[], tuple[int, int]]:
    """(namespace, chave) do advisory lock do sweep, também por import tardio."""

    def _fabrica() -> tuple[int, int]:
        import importlib

        from app.services.advisory_lock import SYNC_NAMESPACE

        mod = importlib.import_module(f"app.services.{chave}")
        return SYNC_NAMESPACE, mod._SWEEP_LOCK_KEY  # noqa: SLF001

    return _fabrica


_ROBOS_COM_SWEEP = (
    "vigia_importacao",
    "vigia_credenciais",
    "vigia_ingest_bling",
    "vigia_correios",
    "vigia_marketing_comandos",
    "vigia_margem",
    "vigia_chamados",
)
# chave do robô → fábrica que devolve a coroutine da varredura (import tardio).
RUNNERS: dict[str, Callable[[], Callable[[], Awaitable[dict]]]] = {
    chave: _runner(chave) for chave in _ROBOS_COM_SWEEP
}
# chave do robô → (namespace, chave) do advisory lock do sweep, pra "Rodar
# agora" enxergar uma rodada do cron em andamento.
LOCKS: dict[str, Callable[[], tuple[int, int]]] = {
    chave: _lock(chave) for chave in _ROBOS_COM_SWEEP
}
# Robôs que ESTE processo está rodando por "Rodar agora" (o lock do banco
# cobre rodadas em paralelo; isto cobre o clique repetido antes de o lock
# ser pego), e o intervalo mínimo entre duas rodadas pedidas à mão.
_EM_EXECUCAO: set[str] = set()
COOLDOWN_S = 60


def _autor(user: User) -> str:
    return (user.name or user.email or "").strip() or "usuário"


async def _nomes_threema(session: AsyncSession) -> dict[str, str]:
    """ID → nome pra coluna "Avisa": o mesmo diretório do seletor (usuários
    ativos com Threema no cadastro + apelidos legados do `.env`)."""
    return {d["id"]: d["nome"] for d in await threema.diretorio(session)}


def _robo_out(
    robo: OuvidoriaRobo, contagens: dict, agora: datetime, nomes: dict[str, str]
) -> dict:
    ids, origem = svc.destinatarios(robo, robo.chave)
    c = contagens.get(robo.chave) or {}
    return {
        "chave": robo.chave,
        "nome": robo.nome,
        "descricao": robo.descricao,
        "area": robo.area,
        "cadencia_texto": robo.cadencia_texto,
        "plataformas": list(robo.plataformas or []),
        "modo": robo.modo,
        "modo_alterado_por": robo.modo_alterado_por,
        "modo_alterado_em": robo.modo_alterado_em,
        "arquivado_em": robo.arquivado_em,
        "arquivado_por": robo.arquivado_por,
        "threema_recipients": robo.threema_recipients,
        "threema_destinatarios": [
            DestinatarioOut(id=i, nome=nomes.get(i, i)) for i in ids
        ],
        "threema_origem": origem,
        "reaviso_horas": robo.reaviso_horas,
        "config": svc.config_do_robo(robo, robo.chave),
        "config_rotulos": svc.rotulos_config(robo.chave),
        "ultima_rodada_em": robo.ultima_rodada_em,
        "ultima_rodada_ok": robo.ultima_rodada_ok,
        "ultima_rodada_resumo": robo.ultima_rodada_resumo,
        "ultima_rodada_duracao_ms": robo.ultima_rodada_duracao_ms,
        "ultima_falha_em": robo.ultima_falha_em,
        "ultima_falha_erro": robo.ultima_falha_erro,
        "abertas": c.get("abertas", 0),
        "abertas_pessoa": c.get("abertas_pessoa", 0),
        "rodadas_hoje": c.get("rodadas_hoje", 0),
        "rodadas_hoje_ok": c.get("rodadas_hoje_ok", 0),
        "saude": svc.saude(robo, agora),
    }


async def _get_robo(session: AsyncSession, chave: str) -> OuvidoriaRobo:
    robo = await session.get(OuvidoriaRobo, chave)
    if robo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "robo_nao_encontrado"})
    return robo


# ─── robôs ─────────────────────────────────────────────────────────────────


@router.get("/robos", response_model=list[RoboOut])
async def listar_robos(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("ouvidoria", "view"))],
) -> list[RoboOut]:
    """Catálogo com contagens (abertas, rodadas hoje) e saúde. Sincroniza o
    catálogo antes de listar: robô novo no código aparece na tela antes do
    primeiro tick do worker."""
    await svc.sincronizar_catalogo(session)
    await session.commit()
    agora = datetime.now(UTC)
    robos = (
        (await session.execute(select(OuvidoriaRobo).order_by(OuvidoriaRobo.nome)))
        .scalars()
        .all()
    )
    contagens = await svc.contagens_por_robo(session, agora)
    nomes = await _nomes_threema(session)
    return [RoboOut(**_robo_out(r, contagens, agora, nomes)) for r in robos]


@router.get("/robos/{chave}", response_model=RoboDetalheOut)
async def detalhe_robo(
    chave: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("ouvidoria", "view"))],
) -> RoboDetalheOut:
    """Detalhe + últimas 20 rodadas + contas que o robô não conseguiu olhar
    (ocorrências `conta:…` abertas)."""
    robo = await _get_robo(session, chave)
    agora = datetime.now(UTC)
    contagens = await svc.contagens_por_robo(session, agora)
    rodadas = (
        (
            await session.execute(
                select(OuvidoriaRodada)
                .where(OuvidoriaRodada.robo_chave == chave)
                .order_by(OuvidoriaRodada.iniciada_em.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    contas = (
        (
            await session.execute(
                select(OuvidoriaOcorrencia)
                .where(
                    OuvidoriaOcorrencia.robo_chave == chave,
                    OuvidoriaOcorrencia.fechada_em.is_(None),
                    OuvidoriaOcorrencia.chave.like("conta:%"),
                )
                .order_by(OuvidoriaOcorrencia.conta)
            )
        )
        .scalars()
        .all()
    )
    return RoboDetalheOut(
        **_robo_out(robo, contagens, agora, await _nomes_threema(session)),
        rodadas=[
            RodadaOut(
                id=r.id,
                iniciada_em=r.iniciada_em,
                terminada_em=r.terminada_em,
                duracao_ms=(
                    int((r.terminada_em - r.iniciada_em).total_seconds() * 1000)
                    if r.terminada_em
                    else None
                ),
                ok=r.ok,
                resumo=r.resumo,
                contadores=r.contadores or {},
                erro=r.erro,
            )
            for r in rodadas
        ],
        contas=[
            ContaOut(
                conta=o.conta,
                plataforma=o.plataforma,
                titulo=o.titulo,
                aberta_em=o.aberta_em,
                dados=o.dados or {},
            )
            for o in contas
        ],
    )


@router.get("/threema/destinatarios", response_model=list[DestinatarioOut])
async def threema_destinatarios(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("ouvidoria", "view"))],
) -> list[DestinatarioOut]:
    """Quem pode receber os avisos: `[{id, nome}]` pro seletor da tela
    (usuários ativos com Threema em Admin › Usuários + apelidos do `.env`)."""
    return [DestinatarioOut(**d) for d in await threema.diretorio(session)]


@router.patch("/robos/{chave}", response_model=RoboOut)
async def editar_robo(
    chave: str,
    body: RoboPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("ouvidoria", "edit"))],
) -> RoboOut:
    """Modo, destinatários, re-aviso, config e a lixeira do painel. Mudança de
    modo carimba quem e quando (a tela mostra "desligado por Fulano em 21/09
    16:10"); `arquivado` idem."""
    robo = await _get_robo(session, chave)
    # Valida tudo antes de gravar qualquer coisa: a tela manda o objeto
    # inteiro, e uma tolerância de 100000 min (dedo a mais) ou um texto no
    # lugar do número mudaria o robô calado — ver `Parametro` no serviço.
    try:
        config = svc.validar_config(chave, body.config) if body.config is not None else None
        # A tela manda pessoas (nome ou ID); nome vira ID pelo diretório.
        destinatarios = (
            svc.resolver_destinatarios(
                body.threema_recipients, await threema.diretorio(session)
            )
            if body.threema_recipients is not None
            else None
        )
    except svc.OuvidoriaError as e:
        raise _erro(e) from e
    if body.modo is not None and body.modo != robo.modo:
        robo.modo = body.modo
        robo.modo_alterado_por = _autor(user)
        robo.modo_alterado_em = datetime.now(UTC)
        logger.info("ouvidoria_modo", robo=chave, modo=body.modo, por=_autor(user))
    if body.arquivado is not None and body.arquivado != (robo.arquivado_em is not None):
        # Lixeira do painel: tira da LISTA e mais nada. O modo fica como está
        # — robô arquivado que está ligado continua rodando e avisando (foi a
        # escolha do Vinicius em 22/09). Some da vista, não do trabalho.
        agora = datetime.now(UTC)
        robo.arquivado_em = agora if body.arquivado else None
        robo.arquivado_por = _autor(user) if body.arquivado else None
        logger.info(
            "ouvidoria_arquivado", robo=chave, arquivado=body.arquivado, por=_autor(user)
        )
    if body.threema_recipients is not None:
        robo.threema_recipients = destinatarios
    if body.reaviso_horas is not None:
        robo.reaviso_horas = body.reaviso_horas
    if config is not None:
        # Mescla por cima do que está salvo: chave conhecida que a tela não
        # mandou não some (cairia no padrão sem ninguém perceber).
        robo.config = {**(robo.config or {}), **config}
    await session.commit()
    await session.refresh(robo)
    agora = datetime.now(UTC)
    contagens = await svc.contagens_por_robo(session, agora)
    return RoboOut(**_robo_out(robo, contagens, agora, await _nomes_threema(session)))


async def _rodada_em_andamento(session: AsyncSession, chave: str) -> bool:
    """O cron (worker) ou outro processo da API está no meio de uma rodada
    deste robô? Olha o advisory lock do sweep em `pg_locks` — só leitura, sem
    tentar pegar o lock (pegar aqui o seguraria até o fim da request)."""
    trava = LOCKS.get(chave)
    if trava is None:
        return False
    try:
        ns, key = trava()
    except ImportError:
        # Serviço do robô ainda não está neste deploy: sem lock pra olhar, o
        # cooldown de COOLDOWN_S continua segurando o clique repetido.
        logger.warning("ouvidoria_lock_sem_servico", robo=chave)
        return False
    got = (
        await session.execute(
            text(
                "SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND granted "
                "AND classid = :ns AND objid = :key LIMIT 1"
            ),
            {"ns": ns, "key": key},
        )
    ).scalar()
    return bool(got)


@router.post("/robos/{chave}/rodar", response_model=RodarOut)
async def rodar_robo(
    chave: str,
    background: BackgroundTasks,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("ouvidoria", "edit"))],
) -> RodarOut:
    """"Rodar agora": agenda uma varredura em background e responde na hora.
    Robô desligado também roda (a pessoa pediu explicitamente) — o modo só
    governa o tick automático. Sem runner cadastrado → 409 `robo_sem_runner`.

    Uma rodada custa ~100–150 chamadas de marketplace, então o botão não
    pode virar metralhadora: 409 `rodada_em_andamento` se este processo (ou
    o cron, pelo advisory lock) já está rodando o robô, e 409
    `rodada_recente` se a última rodada terminou há menos de COOLDOWN_S —
    a tela traduz os dois."""
    robo = await _get_robo(session, chave)
    fabrica = RUNNERS.get(chave)
    if fabrica is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "robo_sem_runner"})
    if chave in _EM_EXECUCAO or await _rodada_em_andamento(session, chave):
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail={"code": "rodada_em_andamento"}
        )
    agora = datetime.now(UTC)
    if robo.ultima_rodada_em and agora - robo.ultima_rodada_em < timedelta(seconds=COOLDOWN_S):
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "rodada_recente"})
    try:
        runner = fabrica()
    except ImportError as e:
        # Robô no catálogo cujo serviço não veio neste deploy: a tela diz o
        # mesmo que diria pra um robô sem runner, em vez de 500.
        logger.warning("ouvidoria_runner_sem_servico", robo=chave, error=str(e)[:200])
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail={"code": "robo_sem_runner"}
        ) from e
    _EM_EXECUCAO.add(chave)

    async def _rodar() -> None:
        try:
            resultado = await runner()
            if (resultado or {}).get("skipped") == "lock_busy":
                # O cron pegou o lock entre a checagem e o início: nada rodou
                # e nenhuma rodada foi gravada — fica no log, a rodada do
                # cron aparece na tela do mesmo jeito.
                logger.info("ouvidoria_rodar_agora_ja_rodando", robo=chave, por=_autor(user))
                return
            logger.info("ouvidoria_rodar_agora", robo=chave, por=_autor(user), **(resultado or {}))
        except Exception:  # noqa: BLE001 — a rodada já gravou a falha; aqui é só o log
            logger.exception("ouvidoria_rodar_agora_falhou", robo=chave)
        finally:
            _EM_EXECUCAO.discard(chave)

    background.add_task(_rodar)
    return RodarOut(agendado=True, chave=chave)


# ─── ocorrências ───────────────────────────────────────────────────────────


def _ocorrencia_out(o: OuvidoriaOcorrencia, nomes: dict[str, str]) -> OcorrenciaOut:
    out = OcorrenciaOut.model_validate(o)
    out.robo_nome = nomes.get(o.robo_chave)
    return out


@router.get("/ocorrencias", response_model=OcorrenciasPage)
async def listar_ocorrencias(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("ouvidoria", "view"))],
    robo: str | None = None,
    plataforma: str | None = None,
    conta: str | None = None,
    status_: Annotated[StatusFiltro, Query(alias="status")] = "abertas",
    precisa_pessoa: bool | None = None,
    q: str | None = None,
    dias: Annotated[int, Query(ge=1, le=365)] = 7,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> OcorrenciasPage:
    """Tabela única do que os robôs encontraram. `dias` vale pras FECHADAS
    (fechada_em nos últimos N dias); aberta é aberta, aparece sempre — senão
    um problema velho sumiria da tela justamente por ser velho. `resumo` e
    `por_robo` são globais (não seguem o filtro), pros StatCards e os chips."""
    agora = datetime.now(UTC)
    Oc = OuvidoriaOcorrencia  # noqa: N806
    conds = []
    if robo:
        conds.append(Oc.robo_chave == robo)
    if plataforma:
        conds.append(func.lower(Oc.plataforma) == plataforma.strip().lower())
    if conta:
        conds.append(Oc.conta.ilike(f"%{conta.strip()}%"))
    if precisa_pessoa is not None:
        conds.append(Oc.precisa_pessoa.is_(precisa_pessoa))
    if q and q.strip():
        like = f"%{q.strip()}%"
        conds.append(
            or_(
                Oc.pedido.ilike(like),
                Oc.titulo.ilike(like),
                Oc.detalhe.ilike(like),
                Oc.conta.ilike(like),
                Oc.chave.ilike(like),
            )
        )
    corte = agora - timedelta(days=dias)
    if status_ == "abertas":
        conds.append(Oc.fechada_em.is_(None))
    elif status_ == "fechadas":
        conds.append(Oc.fechada_em >= corte)
    else:
        conds.append(or_(Oc.fechada_em.is_(None), Oc.fechada_em >= corte))

    total = int(
        (await session.execute(select(func.count()).select_from(Oc).where(*conds))).scalar()
        or 0
    )
    # Abertas primeiro (as que precisam de pessoa no topo), depois as mais
    # recentes — é a ordem em que alguém olha a tela.
    rows = (
        (
            await session.execute(
                select(Oc)
                .where(*conds)
                .order_by(
                    Oc.fechada_em.is_not(None),
                    Oc.precisa_pessoa.desc(),
                    Oc.aberta_em.desc(),
                )
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    robos = {
        r.chave: r.nome
        for r in (await session.execute(select(OuvidoriaRobo))).scalars().all()
    }
    contagens = await svc.contagens_por_robo(session, agora)
    return OcorrenciasPage(
        itens=[_ocorrencia_out(o, robos) for o in rows],
        total=total,
        resumo=ResumoOut(**await svc.resumo_ocorrencias(session, agora)),
        por_robo=[
            PorRoboOut(chave=ch, nome=nome, abertas=(contagens.get(ch) or {}).get("abertas", 0))
            for ch, nome in sorted(robos.items(), key=lambda kv: kv[1])
        ],
    )


def _erro(e: svc.OuvidoriaError) -> HTTPException:
    """OuvidoriaError → HTTP: não encontrada 404, validação 422 (com a frase
    pra tela em `message`), o resto conflito 409."""
    detail: dict = {"code": e.code}
    if e.message:
        detail["message"] = e.message
    if e.code == "ocorrencia_nao_encontrada":
        return HTTPException(status.HTTP_404_NOT_FOUND, detail=detail)
    if e.code in ("config_invalida", "destinatarios_invalidos"):
        return HTTPException(422, detail=detail)
    return HTTPException(status.HTTP_409_CONFLICT, detail=detail)


async def _fechar(
    session: AsyncSession, ocorrencia_id: UUID, user: User, fechamento: str
) -> OcorrenciaOut:
    try:
        row = await svc.tratar(session, ocorrencia_id, usuario=_autor(user), fechamento=fechamento)
    except svc.OuvidoriaError as e:
        raise _erro(e) from e
    await session.commit()
    await session.refresh(row)
    robo = await session.get(OuvidoriaRobo, row.robo_chave)
    return _ocorrencia_out(row, {row.robo_chave: robo.nome} if robo else {})


@router.post("/ocorrencias/{ocorrencia_id}/tratar", response_model=OcorrenciaOut)
async def tratar_ocorrencia(
    ocorrencia_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("ouvidoria", "edit"))],
) -> OcorrenciaOut:
    """Botão Tratado: a pessoa resolveu. Se o problema voltar depois de 24 h,
    o robô abre linha nova."""
    return await _fechar(session, ocorrencia_id, user, "tratada")


@router.post("/ocorrencias/{ocorrencia_id}/ignorar", response_model=OcorrenciaOut)
async def ignorar_ocorrencia(
    ocorrencia_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("ouvidoria", "edit"))],
) -> OcorrenciaOut:
    """Botão Ignorar: o robô NUNCA mais reabre esta chave (só reabrindo à mão)."""
    return await _fechar(session, ocorrencia_id, user, "ignorada")


@router.post("/ocorrencias/{ocorrencia_id}/reabrir", response_model=OcorrenciaOut)
async def reabrir_ocorrencia(
    ocorrencia_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("ouvidoria", "edit"))],
) -> OcorrenciaOut:
    """Desfaz um Tratado/Ignorar. 409 `ocorrencia_duplicada` se o robô já
    abriu outra linha da mesma chave nesse meio-tempo — o serviço checa
    antes, mas entre a checagem e o commit o robô (ou outra aba) pode
    inserir a aberta; aí é o índice único que avisa, e vira o mesmo 409."""
    try:
        row = await svc.reabrir(session, ocorrencia_id, usuario=_autor(user))
    except svc.OuvidoriaError as e:
        raise _erro(e) from e
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        if not is_unique_violation(e):
            raise
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail={"code": "ocorrencia_duplicada"}
        ) from e
    await session.refresh(row)
    robo = await session.get(OuvidoriaRobo, row.robo_chave)
    return _ocorrencia_out(row, {row.robo_chave: robo.nome} if robo else {})
