"""Chamados — aba de Pós-venda que centraliza os chamados abertos nas
plataformas (origem Margem / Logística / Devolução). Formato da aba
`Chamados` da planilha: Data | pedido bling | pedido marketplace | plataforma
| produto | sku | conta | status bling | origem | chamado | réplica | réplica
automática | alterar status bling | monitoramento.

Recurso de permissão: `chamados` (view/edit/delete). As regras/ações vivem em
app.services.chamados; aqui é CRUD + histórico + anexos + os botões.
"""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db import get_session
from app.deps.auth import require_permission
from app.models import Chamado, ChamadoAnexo, ChamadoMensagem, User
from app.models.chamado import CANAIS, ORIGENS
from app.schemas.chamados import (
    AgentLeaseIn,
    AgentLeaseOut,
    AgentRecebidaIn,
    AgentRecebidaOut,
    AgentRegistrarIn,
    AgentRegistrarOut,
    AgentResultadoIn,
    AgentTarefaOut,
    AlterarStatusIn,
    AlterarStatusOut,
    ChamadoAnexoOut,
    ChamadoCreate,
    ChamadoLookupOut,
    ChamadoMensagemOut,
    ChamadoOut,
    ChamadoPage,
    ChamadoPatch,
    ResolverIn,
    SituacoesOut,
)
from app.services import chamados as svc

logger = structlog.get_logger()
router = APIRouter(prefix="/api/chamados", tags=["chamados"])

_ANEXO_TIPOS = {"image/png", "image/jpeg", "image/webp", "image/gif"}
_ANEXO_MAX_BYTES = 8 * 1024 * 1024  # 8 MB


def _autor(user: User) -> str:
    return (user.name or user.email or "").strip() or "usuário"


def _anexo_out(a: ChamadoAnexo) -> ChamadoAnexoOut:
    return ChamadoAnexoOut(
        id=a.id,
        mensagem_id=a.mensagem_id,
        filename=a.filename,
        content_type=a.content_type,
        size_bytes=a.size_bytes,
        created_at=a.created_at,
    )


def _mensagem_out(m: ChamadoMensagem) -> ChamadoMensagemOut:
    return ChamadoMensagemOut(
        id=m.id,
        chamado_id=m.chamado_id,
        direcao=m.direcao,
        tipo=m.tipo,
        texto=m.texto,
        canal=m.canal,
        status=m.status,
        erro=m.erro,
        autor_nome=m.autor_nome,
        enviada_at=m.enviada_at,
        created_at=m.created_at,
        anexos=[_anexo_out(a) for a in (m.anexos or [])],
    )


async def _to_out(session: AsyncSession, rows: list[Chamado]) -> list[ChamadoOut]:
    """Monta a saída em LOTE: status Bling vivo, contagem/última mensagem e
    anexos da réplica automática — 3 queries pra página inteira."""
    if not rows:
        return []
    ids = [r.id for r in rows]
    status_map = await svc.status_bling_atual_map(
        session, {r.pedido_bling for r in rows if r.pedido_bling}
    )
    contagem = {
        cid: (total, ultima)
        for cid, total, ultima in (
            await session.execute(
                select(
                    ChamadoMensagem.chamado_id,
                    func.count(ChamadoMensagem.id),
                    func.max(ChamadoMensagem.created_at),
                )
                .where(ChamadoMensagem.chamado_id.in_(ids))
                .group_by(ChamadoMensagem.chamado_id)
            )
        ).all()
    }
    anexos_auto: dict[UUID, list[ChamadoAnexoOut]] = {}
    for a in (
        await session.execute(
            select(ChamadoAnexo)
            .where(ChamadoAnexo.chamado_id.in_(ids), ChamadoAnexo.mensagem_id.is_(None))
            .order_by(ChamadoAnexo.created_at)
        )
    ).scalars():
        anexos_auto.setdefault(a.chamado_id, []).append(_anexo_out(a))

    out: list[ChamadoOut] = []
    for r in rows:
        total, ultima = contagem.get(r.id, (0, None))
        o = ChamadoOut.model_validate(r)
        o.status_bling_atual = status_map.get(r.pedido_bling or "") or r.status_bling
        o.mensagens_total = int(total or 0)
        o.ultima_mensagem_at = ultima
        o.auto_proximo_envio_at = svc.auto_proximo_envio(r)
        o.anexos_auto = anexos_auto.get(r.id, [])
        out.append(o)
    return out


async def _get(session: AsyncSession, chamado_id: UUID) -> Chamado:
    ch = (
        await session.execute(select(Chamado).where(Chamado.id == chamado_id))
    ).scalar_one_or_none()
    if ch is None:
        raise HTTPException(404, detail={"code": "chamado_not_found"})
    return ch


async def _one_out(session: AsyncSession, ch: Chamado) -> ChamadoOut:
    return (await _to_out(session, [ch]))[0]


def _ler_anexo(file: UploadFile, raw: bytes) -> None:
    ctype = (file.content_type or "").lower()
    if ctype not in _ANEXO_TIPOS:
        raise HTTPException(400, detail={"code": "chamado_anexo_tipo_invalido"})
    if not raw:
        raise HTTPException(400, detail={"code": "chamado_anexo_vazio"})
    if len(raw) > _ANEXO_MAX_BYTES:
        raise HTTPException(413, detail={"code": "chamado_anexo_muito_grande"})


# ------------------------------------------------------------- estáticas
# (antes das rotas /{chamado_id} — path param UUID não cai pro próximo match)


@router.get("/situacoes", response_model=SituacoesOut)
async def situacoes(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("chamados", "view"))],
) -> SituacoesOut:
    """Nomes das situações do Bling (dropdown de "alterar status bling")."""
    return SituacoesOut(nomes=await svc.situacoes_nomes(session))


@router.get("/pedido-lookup", response_model=ChamadoLookupOut)
async def pedido_lookup(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("chamados", "view"))],
    pedido: str = Query(..., min_length=1),
) -> ChamadoLookupOut:
    info = await svc.lookup_pedido(session, pedido)
    if info is None:
        raise HTTPException(404, detail={"code": "chamado_pedido_nao_encontrado"})
    return ChamadoLookupOut(**info)


@router.get("/anexos/{anexo_id}")
async def get_anexo(
    anexo_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("chamados", "view"))],
) -> Response:
    a = (
        await session.execute(select(ChamadoAnexo).where(ChamadoAnexo.id == anexo_id))
    ).scalar_one_or_none()
    if a is None:
        raise HTTPException(404, detail={"code": "chamado_anexo_not_found"})
    return Response(
        content=a.blob,
        media_type=a.content_type,
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.delete("/anexos/{anexo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_anexo(
    anexo_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("chamados", "edit"))],
) -> None:
    a = (
        await session.execute(select(ChamadoAnexo).where(ChamadoAnexo.id == anexo_id))
    ).scalar_one_or_none()
    if a is None:
        raise HTTPException(404, detail={"code": "chamado_anexo_not_found"})
    await session.delete(a)
    await session.commit()


# ------------------------------------------------------------- listagem / CRUD


@router.get("", response_model=ChamadoPage)
async def list_chamados(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("chamados", "view"))],
    search: str | None = Query(None),
    origem: str | None = Query(None),
    plataforma: str | None = Query(None),
    mostrar: str = Query("abertos", pattern="^(abertos|resolvidos|todos)$"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> ChamadoPage:
    conds = []
    if mostrar == "abertos":
        conds.append(Chamado.resolvido.is_(False))
    elif mostrar == "resolvidos":
        conds.append(Chamado.resolvido.is_(True))
    if origem and origem in ORIGENS:
        conds.append(Chamado.origem == origem)
    if plataforma:
        conds.append(
            func.lower(func.coalesce(Chamado.plataforma, "")) == plataforma.strip().lower()
        )
    if search and search.strip():
        q = f"%{search.strip()}%"
        conds.append(
            or_(
                Chamado.pedido_bling.ilike(q),
                Chamado.pedido_marketplace.ilike(q),
                Chamado.conta.ilike(q),
                Chamado.produto.ilike(q),
                Chamado.sku.ilike(q),
                Chamado.chamado.ilike(q),
                Chamado.observacao.ilike(q),
            )
        )
    base = select(Chamado).where(*conds)
    total = int(
        (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one() or 0
    )
    rows = list(
        (
            await session.execute(
                base.order_by(Chamado.data.desc().nulls_last(), Chamado.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    plataformas = [
        p
        for p in (
            await session.execute(
                select(func.lower(Chamado.plataforma))
                .distinct()
                .order_by(func.lower(Chamado.plataforma))
            )
        ).scalars()
        if p
    ]
    return ChamadoPage(
        items=await _to_out(session, rows),
        total=total,
        limit=limit,
        offset=offset,
        plataformas=plataformas,
    )


@router.post("", response_model=ChamadoOut, status_code=status.HTTP_201_CREATED)
async def create_chamado(
    body: ChamadoCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("chamados", "edit"))],
) -> ChamadoOut:
    ch = Chamado(
        data=body.data,
        pedido_bling=body.pedido_bling,
        pedido_marketplace=body.pedido_marketplace,
        plataforma=body.plataforma,
        conta=body.conta,
        produto=body.produto,
        sku=body.sku,
        status_bling=body.status_bling,
        origem=body.origem,
        origem_ref=body.origem_ref,
        chamado=body.chamado,
        chamado_url=body.chamado_url,
        canal=body.canal,
        alterar_status_bling=body.alterar_status_bling,
        monitoramento=body.monitoramento,
        observacao=body.observacao,
        created_by=user.id,
    )
    # Espelho do pedido preenche o que veio vazio (data/pedidos/produto/sku/
    # conta/status). Sem pedido no espelho, fica o que o operador digitou.
    await svc.preencher_do_pedido(session, ch)
    if ch.alterar_status_bling is None:
        ch.alterar_status_bling = svc.STATUS_ABERTURA_POR_ORIGEM.get(ch.origem)
    if ch.data is None:
        ch.data = datetime.now(svc.SAO_PAULO).date()
    session.add(ch)
    await session.flush()
    session.add(
        svc.registrar_sistema(ch, f"Chamado registrado (origem {ch.origem}) por {_autor(user)}")
    )
    await session.commit()
    await session.refresh(ch)
    logger.info("chamado_created", id=str(ch.id), pedido_bling=ch.pedido_bling, origem=ch.origem)
    return await _one_out(session, ch)


@router.patch("/{chamado_id}", response_model=ChamadoOut)
async def patch_chamado(
    chamado_id: UUID,
    body: ChamadoPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("chamados", "edit"))],
) -> ChamadoOut:
    ch = await _get(session, chamado_id)
    data = body.model_dump(exclude_unset=True)
    if "canal" in data and data["canal"] not in CANAIS:
        raise HTTPException(422, detail={"code": "chamado_canal_invalido"})
    if "origem" in data and data["origem"] not in ORIGENS:
        raise HTTPException(422, detail={"code": "chamado_origem_invalida"})
    ligando_auto = bool(data.get("auto_ligada")) and not ch.auto_ligada
    for key, value in data.items():
        setattr(ch, key, value)
    if ligando_auto and ch.auto_ultimo_envio_at is None:
        # Ligar não dispara na hora: a 1ª réplica automática sai N dias depois.
        ch.auto_ultimo_envio_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(ch)
    return await _one_out(session, ch)


@router.delete("/{chamado_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chamado(
    chamado_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("chamados", "delete"))],
) -> None:
    ch = await _get(session, chamado_id)
    await session.delete(ch)
    await session.commit()


# ------------------------------------------------------------- histórico / réplica


@router.get("/{chamado_id}/mensagens", response_model=list[ChamadoMensagemOut])
async def list_mensagens(
    chamado_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("chamados", "view"))],
) -> list[ChamadoMensagemOut]:
    await _get(session, chamado_id)
    rows = (
        (
            await session.execute(
                select(ChamadoMensagem)
                .options(selectinload(ChamadoMensagem.anexos))
                .where(ChamadoMensagem.chamado_id == chamado_id)
                .order_by(ChamadoMensagem.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [_mensagem_out(m) for m in rows]


@router.post(
    "/{chamado_id}/mensagens",
    response_model=ChamadoMensagemOut,
    status_code=status.HTTP_201_CREATED,
)
async def replicar(
    chamado_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("chamados", "edit"))],
    texto: Annotated[str, Form()],
    files: Annotated[list[UploadFile], File()] = [],  # noqa: B006 — FastAPI lê o default por request
) -> ChamadoMensagemOut:
    """Réplica MANUAL: grava no histórico (quem/quando), guarda as fotos e
    despacha pelo canal do chamado (api → ML na hora; robo → fila; manual →
    só registro). Falha de envio não perde a mensagem: fica `falhou` + erro."""
    ch = await _get(session, chamado_id)
    texto = (texto or "").strip()
    if not texto:
        raise HTTPException(422, detail={"code": "chamado_mensagem_vazia"})
    lidos: list[tuple[UploadFile, bytes]] = []
    for f in files or []:
        raw = await f.read()
        _ler_anexo(f, raw)
        lidos.append((f, raw))

    msg = svc.nova_mensagem(
        ch, texto=texto, tipo="replica", autor_nome=_autor(user), autor_id=user.id
    )
    session.add(msg)
    await session.flush()
    for f, raw in lidos:
        session.add(
            ChamadoAnexo(
                chamado_id=ch.id,
                mensagem_id=msg.id,
                filename=(f.filename or "imagem").strip() or "imagem",
                content_type=(f.content_type or "").lower(),
                size_bytes=len(raw),
                blob=raw,
                created_by=user.id,
            )
        )
    await svc.enviar_mensagem(session, ch, msg)
    await session.commit()
    m = (
        await session.execute(
            select(ChamadoMensagem)
            .options(selectinload(ChamadoMensagem.anexos))
            .where(ChamadoMensagem.id == msg.id)
        )
    ).scalar_one()
    logger.info("chamado_replica", chamado_id=str(ch.id), canal=ch.canal, status=m.status)
    return _mensagem_out(m)


@router.post(
    "/{chamado_id}/anexos-auto",
    response_model=ChamadoAnexoOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_anexo_auto(
    chamado_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("chamados", "edit"))],
    file: Annotated[UploadFile, File(...)],
) -> ChamadoAnexoOut:
    """Foto da RÉPLICA AUTOMÁTICA (vai junto de `auto_mensagem` a cada envio)."""
    ch = await _get(session, chamado_id)
    raw = await file.read()
    _ler_anexo(file, raw)
    a = ChamadoAnexo(
        chamado_id=ch.id,
        mensagem_id=None,
        filename=(file.filename or "imagem").strip() or "imagem",
        content_type=(file.content_type or "").lower(),
        size_bytes=len(raw),
        blob=raw,
        created_by=user.id,
    )
    session.add(a)
    await session.commit()
    await session.refresh(a)
    return _anexo_out(a)


# ------------------------------------------------------------- botões


@router.post("/{chamado_id}/alterar-status-bling", response_model=AlterarStatusOut)
async def alterar_status_bling(
    chamado_id: UUID,
    body: AlterarStatusIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("chamados", "edit"))],
) -> AlterarStatusOut:
    ch = await _get(session, chamado_id)
    try:
        res = await svc.aplicar_status_bling(session, ch, body.situacao)
    except svc.ChamadoError as e:
        raise HTTPException(422, detail={"code": e.code}) from e
    except Exception as e:  # noqa: BLE001
        logger.warning("chamado_alterar_status_bling_falhou", id=str(chamado_id), err=str(e)[:300])
        raise HTTPException(
            502, detail={"code": "chamado_status_bling_erro", "erro": str(e)[:300]}
        ) from e
    ch.alterar_status_bling = body.situacao.strip()
    await session.commit()
    return AlterarStatusOut(**res)


@router.post("/{chamado_id}/resolver", response_model=ChamadoOut)
async def resolver(
    chamado_id: UUID,
    body: ResolverIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("chamados", "edit"))],
) -> ChamadoOut:
    """Marca resolvido (ou reabre). Com `situacao`, aplica junto a situação de
    fechamento no Bling (Resolvido / Perdimento na origem Logística)."""
    ch = await _get(session, chamado_id)
    if body.situacao:
        try:
            await svc.aplicar_status_bling(session, ch, body.situacao)
        except svc.ChamadoError as e:
            raise HTTPException(422, detail={"code": e.code}) from e
        except Exception as e:  # noqa: BLE001
            raise HTTPException(
                502, detail={"code": "chamado_status_bling_erro", "erro": str(e)[:300]}
            ) from e
        ch.alterar_status_bling = body.situacao
    session.add(svc.marcar_resolvido(ch, body.resolvido, autor_nome=_autor(user)))
    await session.commit()
    await session.refresh(ch)
    return await _one_out(session, ch)


# ============================================================= robô (agent)
# Contrato do robô de chamados (runner de frete + monitor), no padrão do
# executor de NF: X-Agent-Token (mesmo NF_AGENT_TOKEN), router separado e
# incluído ANTES do da aba (senão "/agent/lease" cairia em "/{chamado_id}").
#
#   POST /agent/registrar  robô abriu um chamado → linha na aba + histórico
#   POST /agent/lease      réplicas pendentes (canal robô) → tarefas abrir/responder
#   POST /agent/resultado  robô devolve enviada/falhou (+ protocolo ao abrir)
#   POST /agent/recebida   monitor grava a resposta da plataforma (+ resolvido)
#   POST /agent/anexo      robô guarda um print (evidência) no histórico
#   GET  /agent/anexos/{id} robô baixa a foto da réplica pra anexar no ML

agent_router = APIRouter(prefix="/api/chamados/agent", tags=["chamados"])

AUTOR_ROBO = "robô"
AUTOR_MONITOR = "monitor"
# Tarefa leased há mais tempo que isso volta pra fila (robô morreu no meio).
_LEASE_STALE = timedelta(minutes=30)


async def _require_agent_token(
    x_agent_token: Annotated[str | None, Header(alias="X-Agent-Token")] = None,
) -> None:
    expected = get_settings().nf_agent_token
    if not expected or not x_agent_token or not secrets.compare_digest(x_agent_token, expected):
        raise HTTPException(401, detail={"code": "chamados_agent_unauthorized"})


_agent_dep = [Depends(_require_agent_token)]


async def _chamado_aberto(session: AsyncSession, pedido_bling: str, origem: str) -> Chamado | None:
    return (
        await session.execute(
            select(Chamado)
            .where(
                Chamado.pedido_bling == pedido_bling,
                Chamado.origem == origem,
                Chamado.resolvido.is_(False),
            )
            .order_by(Chamado.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


@agent_router.post("/registrar", response_model=AgentRegistrarOut, dependencies=_agent_dep)
async def agent_registrar(
    body: AgentRegistrarIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AgentRegistrarOut:
    """Robô abriu (ou tentou abrir) um chamado: cria/atualiza a linha (canal
    robô) e grava a mensagem de abertura no histórico com o status do envio.
    Reenviar o mesmo pedido não duplica: atualiza protocolo/URL da linha aberta."""
    ch = await _chamado_aberto(session, body.pedido_bling, body.origem)
    criado = ch is None
    if ch is None:
        ch = Chamado(
            pedido_bling=body.pedido_bling,
            pedido_marketplace=body.pedido_marketplace,
            plataforma=body.plataforma,
            conta=body.conta,
            origem=body.origem,
            origem_ref=body.origem_ref,
            canal="robo",
            monitoramento=body.monitoramento,
            observacao=body.observacao,
        )
        await svc.preencher_do_pedido(session, ch)
        if ch.alterar_status_bling is None:
            ch.alterar_status_bling = svc.STATUS_ABERTURA_POR_ORIGEM.get(ch.origem)
        if ch.data is None:
            ch.data = datetime.now(svc.SAO_PAULO).date()
        session.add(ch)
        await session.flush()
        session.add(
            svc.registrar_sistema(ch, f"Chamado registrado pelo {AUTOR_ROBO} (origem {ch.origem})")
        )
    if body.chamado:
        ch.chamado = body.chamado
    if body.chamado_url:
        ch.chamado_url = body.chamado_url
    if body.observacao and not criado:
        ch.observacao = body.observacao
    msg = None
    if body.mensagem:
        msg = svc.nova_mensagem(
            ch,
            texto=body.mensagem,
            tipo="abertura",
            autor_nome=AUTOR_ROBO,
            status=body.status_envio,
        )
        msg.canal = "robo"
        msg.erro = body.erro
        if body.status_envio == "enviada":
            msg.enviada_at = datetime.now(UTC)
        session.add(msg)
        await session.flush()
    await session.commit()
    logger.info(
        "chamado_agent_registrar",
        chamado_id=str(ch.id),
        pedido=ch.pedido_bling,
        criado=criado,
        protocolo=ch.chamado,
    )
    return AgentRegistrarOut(chamado_id=ch.id, mensagem_id=msg.id if msg else None, criado=criado)


@agent_router.post("/lease", response_model=AgentLeaseOut, dependencies=_agent_dep)
async def agent_lease(
    body: AgentLeaseIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AgentLeaseOut:
    """Entrega ao robô as réplicas pendentes de canal robô (manuais e
    automáticas) e marca-as `enviando`. Tarefa presa em `enviando` há mais de
    30 min volta pra fila. Chamado resolvido não gera tarefa."""
    limite_stale = datetime.now(UTC) - _LEASE_STALE
    rows = (
        await session.execute(
            select(ChamadoMensagem, Chamado)
            .join(Chamado, Chamado.id == ChamadoMensagem.chamado_id)
            .options(selectinload(ChamadoMensagem.anexos))
            .where(
                ChamadoMensagem.canal == "robo",
                ChamadoMensagem.direcao == "enviada",
                Chamado.resolvido.is_(False),
                or_(
                    ChamadoMensagem.status == "pendente",
                    (ChamadoMensagem.status == "enviando")
                    & (ChamadoMensagem.updated_at < limite_stale),
                ),
            )
            .order_by(ChamadoMensagem.created_at)
            .limit(body.limite)
        )
    ).all()
    tarefas: list[AgentTarefaOut] = []
    if rows:
        ids = [m.id for m, _ in rows]
        anexos_auto = {}
        for a in (
            await session.execute(
                select(ChamadoAnexo).where(
                    ChamadoAnexo.chamado_id.in_([c.id for _, c in rows]),
                    ChamadoAnexo.mensagem_id.is_(None),
                )
            )
        ).scalars():
            anexos_auto.setdefault(a.chamado_id, []).append(a.id)
        for m, c in rows:
            m.status = "enviando"
            anexos = [a.id for a in (m.anexos or [])]
            if m.tipo == "replica_auto":
                anexos += anexos_auto.get(c.id, [])
            tarefas.append(
                AgentTarefaOut(
                    tipo="responder" if (c.chamado or "").strip() else "abrir",
                    mensagem_id=m.id,
                    chamado_id=c.id,
                    pedido_bling=c.pedido_bling,
                    pedido_marketplace=c.pedido_marketplace,
                    conta=c.conta,
                    plataforma=c.plataforma,
                    chamado=c.chamado,
                    chamado_url=c.chamado_url,
                    texto=m.texto,
                    anexos=anexos,
                )
            )
        await session.commit()
        logger.info("chamado_agent_lease", tarefas=len(ids))
    return AgentLeaseOut(tarefas=tarefas)


@agent_router.post("/resultado", response_model=ChamadoMensagemOut, dependencies=_agent_dep)
async def agent_resultado(
    body: AgentResultadoIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ChamadoMensagemOut:
    """Robô devolve o resultado de uma tarefa: enviada (+ protocolo/URL quando
    foi abertura) ou falhou (+ erro). Falha volta a mensagem pra `pendente`?
    Não — fica `falhou` visível no histórico; o operador reenvia se quiser."""
    m = (
        await session.execute(
            select(ChamadoMensagem)
            .options(selectinload(ChamadoMensagem.anexos))
            .where(ChamadoMensagem.id == body.mensagem_id)
        )
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(404, detail={"code": "chamado_mensagem_not_found"})
    ch = await _get(session, m.chamado_id)
    if body.ok:
        m.status = "enviada"
        m.enviada_at = datetime.now(UTC)
        m.erro = None
        if body.chamado and not (ch.chamado or "").strip():
            ch.chamado = body.chamado
            ch.chamado_url = body.chamado_url or ch.chamado_url
            session.add(
                svc.registrar_sistema(ch, f"Protocolo {body.chamado} capturado pelo {AUTOR_ROBO}")
            )
        elif body.chamado_url and not ch.chamado_url:
            ch.chamado_url = body.chamado_url
    else:
        m.status = "falhou"
        m.erro = (body.erro or "falha no robô")[:300]
    await session.commit()
    await session.refresh(m)
    logger.info("chamado_agent_resultado", mensagem_id=str(m.id), status=m.status)
    return _mensagem_out(m)


@agent_router.post("/recebida", response_model=AgentRecebidaOut, dependencies=_agent_dep)
async def agent_recebida(
    body: AgentRecebidaIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AgentRecebidaOut:
    """Monitor leu uma resposta da plataforma: entra no histórico como
    `recebida` (autor monitor). Com `resolvido=true` fecha o chamado."""
    ch: Chamado | None = None
    if body.chamado_id:
        ch = await _get(session, body.chamado_id)
    elif body.pedido_bling and body.chamado:
        ch = (
            await session.execute(
                select(Chamado)
                .where(Chamado.pedido_bling == body.pedido_bling, Chamado.chamado == body.chamado)
                .order_by(Chamado.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if ch is None:
        raise HTTPException(404, detail={"code": "chamado_not_found"})
    texto = body.texto.strip()
    if body.resumo:
        texto = f"{body.resumo.strip()}\n\n{texto}"
    m = svc.nova_mensagem(
        ch,
        texto=texto,
        tipo="resposta",
        direcao="recebida",
        autor_nome=AUTOR_MONITOR,
        status="registrada",
    )
    m.canal = "robo"
    session.add(m)
    if body.resolvido:
        session.add(svc.marcar_resolvido(ch, True, autor_nome=AUTOR_MONITOR))
    await session.commit()
    await session.refresh(m)
    return AgentRecebidaOut(chamado_id=ch.id, mensagem_id=m.id, resolvido=ch.resolvido)


@agent_router.post(
    "/anexo",
    response_model=ChamadoAnexoOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=_agent_dep,
)
async def agent_anexo(
    session: Annotated[AsyncSession, Depends(get_session)],
    chamado_id: Annotated[UUID, Form()],
    file: Annotated[UploadFile, File(...)],
    mensagem_id: Annotated[UUID | None, Form()] = None,
) -> ChamadoAnexoOut:
    """Print/evidência capturado pelo robô, ligado ao chamado (e à mensagem,
    quando informada) — aparece no histórico da aba."""
    ch = await _get(session, chamado_id)
    if mensagem_id is not None:
        m = (
            await session.execute(select(ChamadoMensagem).where(ChamadoMensagem.id == mensagem_id))
        ).scalar_one_or_none()
        if m is None or m.chamado_id != ch.id:
            raise HTTPException(404, detail={"code": "chamado_mensagem_not_found"})
    raw = await file.read()
    _ler_anexo(file, raw)
    a = ChamadoAnexo(
        chamado_id=ch.id,
        mensagem_id=mensagem_id,
        filename=(file.filename or "print").strip() or "print",
        content_type=(file.content_type or "").lower(),
        size_bytes=len(raw),
        blob=raw,
    )
    session.add(a)
    await session.commit()
    await session.refresh(a)
    return _anexo_out(a)


@agent_router.get("/anexos/{anexo_id}", dependencies=_agent_dep)
async def agent_get_anexo(
    anexo_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    a = (
        await session.execute(select(ChamadoAnexo).where(ChamadoAnexo.id == anexo_id))
    ).scalar_one_or_none()
    if a is None:
        raise HTTPException(404, detail={"code": "chamado_anexo_not_found"})
    return Response(content=a.blob, media_type=a.content_type)
