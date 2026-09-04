"""Logística — casos de pós-venda a acompanhar (CRUD manual) + aba Status.

Aba "Logística": formato da planilha (Data | pedido bling | pedido marketplace
| plataforma | conta | STATUS PLATAFORMA | rastreio | localização | STATUS
BLING | Chamado). Cada linha guarda os dados do pedido + a assinatura de status
do Meli (`meli_status`); a partir dela, `/sugestao` devolve os Status Bling
candidatos que a curadoria da planilha já viu — é só uma dica, a classificação
final é do operador.

Aba "Status": cadastro/referência do que fazer pra cada STATUS PLATAFORMA
(alterar status bling, abrir chamado, mensagem). Por enquanto é só cadastro —
não escreve no Bling automaticamente.

Gated pelo recurso `logistica`.
"""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

import structlog
from arq.jobs import Job, JobStatus
from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import desc, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_session
from app.deps.auth import require_permission
from app.deps.team_scope import TeamScope, resolve_team_scope
from app.models import (
    BlingOrder,
    Logistica,
    LogisticaStatus,
    LogisticaStatusAnexo,
    SituacaoBling,
    User,
)
from app.config import get_settings
from app.schemas.logistica import (
    AnexoOut,
    CandidatoOut,
    EnviarThreemaIn,
    EnviarThreemaOut,
    LogisticaCreate,
    LogisticaOut,
    LogisticaPatch,
    LogisticaProdutoOut,
    LogisticaStatusCreate,
    LogisticaStatusOut,
    LogisticaStatusPatch,
    MensagemBlingOut,
    MensagemBlingPreviewOut,
    OpcoesOut,
    RecarregarOut,
    RecarregarStatusOut,
    StatusBlingOut,
    StatusBlingPreviewOut,
    StatusDetalheOut,
    SugestaoIn,
    SugestaoOut,
    ThreemaDestinatarioOut,
)
from app.services import (
    logistica_amazon,
    logistica_bling,
    logistica_datas,
    logistica_match,
    logistica_meli,
    logistica_rules,
    logistica_shopee,
    logistica_tiktok,
    threema,
)
from app.worker_pool import get_arq_pool

logger = structlog.get_logger()
router = APIRouter(prefix="/api/logistica", tags=["logistica"])

# Chave canônica -> rótulo gravado em `logistica.plataforma` (o filtro da aba por
# marketplace manda a chave; futuras abas Shopee/Amazon só trocam o valor).
_PLATAFORMA_LABELS = {
    "ml": "Mercado Livre",
    "shopee": "Shopee",
    "amazon": "Amazon",
    "tiktok": "TikTok",
    "magalu": "Magalu",
}


def _status_detalhe(c: Logistica) -> list[StatusDetalheOut]:
    """Assinatura da plataforma aberta linha a linha + a data de cada campo
    (`logistica.status_datas`). É o que o balãozinho da coluna mostra."""
    datas = c.status_datas if isinstance(c.status_datas, dict) else {}
    out: list[StatusDetalheOut] = []
    for linha in logistica_rules.detalhe_para(c.plataforma, c.meli_status or {}):
        carimbo = datas.get(linha["campo"])
        carimbo = carimbo if isinstance(carimbo, dict) else {}
        out.append(
            StatusDetalheOut(
                campo=linha["campo"],
                rotulo=linha["rotulo"],
                valor=linha["valor"],
                em=str(carimbo["em"]) if carimbo.get("em") else None,
                fonte=str(carimbo["fonte"]) if carimbo.get("fonte") else None,
            )
        )
    return out


async def _produtos_map(
    session: AsyncSession, numeros: set[str | None]
) -> dict[str, list[LogisticaProdutoOut]]:
    """Itens (nome + SKU) de cada pedido, pelo espelho `bling_orders` (uma linha
    por item, casada pelo `numero` == `pedido_bling` — o mesmo join do escopo de
    equipe). Em LOTE: a listagem tem milhares de linhas, uma query por linha
    mataria a página. Dedup por (sku, nome) — o espelho pode repetir o pedido."""
    limpos = {n for n in numeros if n}
    if not limpos:
        return {}
    res = await session.execute(
        select(
            BlingOrder.numero,
            BlingOrder.item_codigo,
            BlingOrder.item_descricao,
            BlingOrder.item_quantidade,
        )
        .where(BlingOrder.numero.in_(list(limpos)))
        .order_by(BlingOrder.numero, BlingOrder.item_index)
    )
    out: dict[str, list[LogisticaProdutoOut]] = {}
    vistos: dict[str, set[tuple[str, str]]] = {}
    for numero, sku, nome, qtd in res:
        if not numero or (not sku and not nome):
            continue
        chave = (sku or "", nome or "")
        if chave in vistos.setdefault(numero, set()):
            continue
        vistos[numero].add(chave)
        out.setdefault(numero, []).append(
            LogisticaProdutoOut(sku=sku, nome=nome, quantidade=qtd)
        )
    return out


async def _produtos_for(session: AsyncSession, c: Logistica) -> list[LogisticaProdutoOut]:
    """Itens de UMA linha (endpoints de create/patch/ações por linha)."""
    if not c.pedido_bling:
        return []
    m = await _produtos_map(session, {c.pedido_bling})
    return m.get(c.pedido_bling, [])


def _to_out(
    c: Logistica,
    rules: list[LogisticaStatus] | None = None,
    produtos: list[LogisticaProdutoOut] | None = None,
) -> LogisticaOut:
    """`rules` = candidatas da aba Status que casam a chave deste pedido (máquina
    de estados). A regra ATIVA (desambiguada pela situação atual do Bling) alimenta
    match/setinha/resumo; o conjunto alimenta monitorar/resolvido."""
    rules = rules or []
    rule = logistica_match.regra_ativa(rules, c.status_bling)
    return LogisticaOut(
        id=c.id,
        data=c.data,
        pedido_bling=c.pedido_bling,
        pedido_marketplace=c.pedido_marketplace,
        plataforma=c.plataforma,
        conta=c.conta,
        produtos=produtos or [],
        meli_status=c.meli_status or {},
        status_plataforma=logistica_rules.assinatura_para(c.plataforma, c.meli_status or {}),
        status_detalhe=_status_detalhe(c),
        rastreio=c.rastreio,
        localizacao=c.localizacao,
        localizacao_at=c.localizacao_at,
        divergencia=c.divergencia,
        status_bling=c.status_bling,
        chamado=c.chamado,
        observacao=c.observacao,
        acao_match=rule is not None,
        acao_status_id=rule.id if rule is not None else None,
        acao_resumo=logistica_match.resumo_acoes(rule),
        acao_monitorar=logistica_match.deve_monitorar(rules, c.status_bling),
        # "Problemas" no Bling ignora o resolvido das regras por 360 dias —
        # o painel nunca esconde um pedido com problema por causa de regra. Idem
        # pra devolução viva sem regra pro estado atual (buraco na matriz): sem
        # isso o pedido some do painel esperando uma transição que não existe.
        acao_resolvido=logistica_match.estado_resolvido(
            rules, c.status_bling, threema_enviado=c.threema_enviado_at is not None
        )
        and not logistica_match.problema_bling_visivel(c.status_bling, c.data)
        and not logistica_match.devolucao_travada(
            rules,
            plataforma=c.plataforma,
            meli_status=c.meli_status or {},
            status_bling=c.status_bling,
        ),
        created_by=c.created_by,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


async def _match_rules(session: AsyncSession, c: Logistica) -> list[LogisticaStatus]:
    """Candidatas da aba Status que casam a chave (assinatura PT) do pedido."""
    rows = (await session.execute(select(LogisticaStatus))).scalars().all()
    assinatura = logistica_rules.assinatura_para(c.plataforma, c.meli_status or {})
    return logistica_match.find_matching_rules(
        list(rows), assinatura=assinatura, plataforma=c.plataforma
    )


def _to_status_out(s: LogisticaStatus) -> LogisticaStatusOut:
    return LogisticaStatusOut(
        id=s.id,
        plataforma=s.plataforma,
        status_plataforma=s.status_plataforma,
        status_atual=s.status_atual,
        alterar_status_bling=s.alterar_status_bling,
        monitoramento=s.monitoramento,
        abrir_chamado=s.abrir_chamado,
        abrir_reembolso=s.abrir_reembolso,
        mensagem_chamado=s.mensagem_chamado,
        mensagem_bling=s.mensagem_bling,
        mensagem_threema=s.mensagem_threema,
        threema_recipients=s.threema_recipients,
        anexos=[
            AnexoOut(
                id=a.id,
                filename=a.filename,
                content_type=a.content_type,
                size_bytes=a.size_bytes,
                created_at=a.created_at,
            )
            for a in s.anexos
        ],
        created_by=s.created_by,
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


def _clean(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    return v or None


def _clean_meli(m: dict[str, str] | None) -> dict[str, str]:
    """Mantém só os campos conhecidos com valor não-vazio."""
    if not m:
        return {}
    return {
        f: str(m[f]).strip()
        for f in logistica_rules.FIELD_ORDER
        if m.get(f) and str(m[f]).strip()
    }


# ---- Opções + sugestão ----


@router.get("/opcoes", response_model=OpcoesOut)
async def opcoes(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "view"))],
) -> OpcoesOut:
    nomes = (
        await session.execute(
            select(SituacaoBling.nome).distinct().order_by(SituacaoBling.nome)
        )
    ).scalars().all()
    return OpcoesOut(
        field_order=logistica_rules.FIELD_ORDER,
        field_labels=logistica_rules.FIELD_LABELS,
        field_options=logistica_rules.FIELD_OPTIONS,
        status_bling_options=[n for n in nomes if n],
    )


@router.post("/sugestao", response_model=SugestaoOut)
async def sugestao(
    body: SugestaoIn,
    _user: Annotated[User, Depends(require_permission("logistica", "view"))],
) -> SugestaoOut:
    cand = logistica_rules.sugerir(body.meli_status)
    return SugestaoOut(candidatos=[CandidatoOut(**c) for c in cand])


# ---- Aba Status (cadastro) ----


async def _load_status(session: AsyncSession, status_id: UUID) -> LogisticaStatus | None:
    return (
        await session.execute(
            select(LogisticaStatus)
            .options(selectinload(LogisticaStatus.anexos))
            .where(LogisticaStatus.id == status_id)
        )
    ).scalar_one_or_none()


@router.get("/status", response_model=list[LogisticaStatusOut])
async def list_status(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "view"))],
) -> list[LogisticaStatusOut]:
    stmt = (
        select(LogisticaStatus)
        .options(selectinload(LogisticaStatus.anexos))
        .order_by(LogisticaStatus.status_plataforma)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_status_out(s) for s in rows]


@router.post("/status", response_model=LogisticaStatusOut, status_code=status.HTTP_201_CREATED)
async def create_status(
    body: LogisticaStatusCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("logistica", "edit"))],
) -> LogisticaStatusOut:
    s = LogisticaStatus(
        plataforma=_clean(body.plataforma),
        status_plataforma=_clean(body.status_plataforma),
        status_atual=_clean(body.status_atual),
        alterar_status_bling=_clean(body.alterar_status_bling),
        monitoramento=bool(body.monitoramento),
        abrir_chamado=bool(body.abrir_chamado),
        abrir_reembolso=bool(body.abrir_reembolso),
        mensagem_chamado=_clean(body.mensagem_chamado),
        mensagem_bling=_clean(body.mensagem_bling),
        mensagem_threema=_clean(body.mensagem_threema),
        threema_recipients=_clean(body.threema_recipients),
        created_by=user.id,
    )
    session.add(s)
    await session.commit()
    s = await _load_status(session, s.id)
    return _to_status_out(s)


@router.patch("/status/{status_id}", response_model=LogisticaStatusOut)
async def patch_status(
    status_id: UUID,
    body: LogisticaStatusPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "edit"))],
) -> LogisticaStatusOut:
    s = await _load_status(session, status_id)
    if s is None:
        raise HTTPException(404, detail={"code": "logistica_status_not_found"})

    data = body.model_dump(exclude_unset=True)
    if "plataforma" in data:
        s.plataforma = _clean(data["plataforma"])
    if "status_plataforma" in data:
        s.status_plataforma = _clean(data["status_plataforma"])
    if "status_atual" in data:
        s.status_atual = _clean(data["status_atual"])
    if "alterar_status_bling" in data:
        s.alterar_status_bling = _clean(data["alterar_status_bling"])
    if "monitoramento" in data:
        s.monitoramento = bool(data["monitoramento"])
    if "abrir_chamado" in data:
        s.abrir_chamado = bool(data["abrir_chamado"])
    if "abrir_reembolso" in data:
        s.abrir_reembolso = bool(data["abrir_reembolso"])
    if "mensagem_chamado" in data:
        s.mensagem_chamado = _clean(data["mensagem_chamado"])
    if "mensagem_bling" in data:
        s.mensagem_bling = _clean(data["mensagem_bling"])
    if "mensagem_threema" in data:
        s.mensagem_threema = _clean(data["mensagem_threema"])
    if "threema_recipients" in data:
        s.threema_recipients = _clean(data["threema_recipients"])

    await session.commit()
    s = await _load_status(session, s.id)
    return _to_status_out(s)


@router.get("/threema/destinatarios", response_model=list[ThreemaDestinatarioOut])
async def threema_destinatarios(
    _user: Annotated[User, Depends(require_permission("logistica", "view"))],
) -> list[ThreemaDestinatarioOut]:
    """Lista `[{id, nome}]` dos destinatários do Threema (do `.env`) pro seletor
    de quem recebe a mensagem."""
    s = get_settings()
    return [
        ThreemaDestinatarioOut(**d)
        for d in threema.parse_recipient_directory(
            s.threema_recipient_names, s.threema_recipients
        )
    ]


@router.post("/status/{status_id}/enviar-threema", response_model=EnviarThreemaOut)
async def enviar_threema(
    status_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "edit"))],
    payload: EnviarThreemaIn | None = None,
) -> EnviarThreemaOut:
    """Envia a `mensagem_threema` desta linha da aba Status pros destinatários
    escolhidos (`payload.recipients`; vazio/ausente = lista fixa do `.env`).
    `pedido`/`loja` opcionais entram no topo da mensagem. 404 se a linha some;
    422 sem mensagem/sem destinatário/sem config."""
    payload = payload or EnviarThreemaIn()
    s = await _load_status(session, status_id)
    if s is None:
        raise HTTPException(404, detail={"code": "logistica_status_not_found"})
    texto = (s.mensagem_threema or "").strip()
    if not texto:
        raise HTTPException(422, detail={"code": "logistica_sem_mensagem_threema"})
    if payload.recipients is not None and not payload.recipients:
        raise HTTPException(422, detail={"code": "logistica_sem_destinatario_threema"})
    # Destinatários: escolha explícita do body > lista salva na regra (👤) > .env.
    recipients = payload.recipients
    if recipients is None:
        recipients = threema.parse_recipients(s.threema_recipients) or None
    texto = threema.compose_texto(texto, pedido=payload.pedido, loja=payload.loja)
    try:
        result = await threema.ThreemaClient().send_to_all(
            texto, recipients=recipients or None
        )
    except threema.ThreemaConfigError as e:
        raise HTTPException(422, detail={"code": str(e)}) from e
    logger.info(
        "logistica_threema_enviado",
        status_id=str(status_id),
        sent=len(result["sent"]),
        failed=len(result["failed"]),
    )
    return EnviarThreemaOut(sent=result["sent"], failed=result["failed"])


@router.delete("/status/{status_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_status(
    status_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "edit"))],
) -> None:
    s = (
        await session.execute(select(LogisticaStatus).where(LogisticaStatus.id == status_id))
    ).scalar_one_or_none()
    if s is None:
        raise HTTPException(404, detail={"code": "logistica_status_not_found"})
    await session.delete(s)
    await session.commit()
    return None


# ---- Anexos (imagens) da aba Status ----

_ANEXO_TIPOS = {"image/png", "image/jpeg", "image/webp", "image/gif"}
_ANEXO_MAX_BYTES = 8 * 1024 * 1024  # 8 MB


@router.post(
    "/status/{status_id}/anexos",
    response_model=AnexoOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_status_anexo(
    status_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("logistica", "edit"))],
    file: Annotated[UploadFile, File(...)],
) -> AnexoOut:
    s = (
        await session.execute(select(LogisticaStatus).where(LogisticaStatus.id == status_id))
    ).scalar_one_or_none()
    if s is None:
        raise HTTPException(404, detail={"code": "logistica_status_not_found"})

    ctype = (file.content_type or "").lower()
    if ctype not in _ANEXO_TIPOS:
        raise HTTPException(400, detail={"code": "logistica_anexo_tipo_invalido"})
    raw = await file.read()
    if not raw:
        raise HTTPException(400, detail={"code": "logistica_anexo_vazio"})
    if len(raw) > _ANEXO_MAX_BYTES:
        raise HTTPException(413, detail={"code": "logistica_anexo_muito_grande"})

    a = LogisticaStatusAnexo(
        status_id=status_id,
        filename=(file.filename or "imagem").strip() or "imagem",
        content_type=ctype,
        size_bytes=len(raw),
        blob=raw,
        created_by=user.id,
    )
    session.add(a)
    await session.commit()
    await session.refresh(a)
    return AnexoOut(
        id=a.id,
        filename=a.filename,
        content_type=a.content_type,
        size_bytes=a.size_bytes,
        created_at=a.created_at,
    )


@router.get("/anexos/{anexo_id}")
async def get_status_anexo(
    anexo_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "view"))],
) -> Response:
    a = (
        await session.execute(
            select(LogisticaStatusAnexo).where(LogisticaStatusAnexo.id == anexo_id)
        )
    ).scalar_one_or_none()
    if a is None:
        raise HTTPException(404, detail={"code": "logistica_anexo_not_found"})
    return Response(
        content=a.blob,
        media_type=a.content_type,
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.delete("/anexos/{anexo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_status_anexo(
    anexo_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "edit"))],
) -> None:
    a = (
        await session.execute(
            select(LogisticaStatusAnexo).where(LogisticaStatusAnexo.id == anexo_id)
        )
    ).scalar_one_or_none()
    if a is None:
        raise HTTPException(404, detail={"code": "logistica_anexo_not_found"})
    await session.delete(a)
    await session.commit()
    return None


# ---- Aba Logística (casos) ----


def _team_scope_clause(scope: TeamScope):
    """Restringe a Logística à equipe do usuário (não-admin com equipe). Casa
    por `conta` (nome da conta normalizado) OU por `pedido_bling` de um pedido
    das lojas da equipe (bling_orders.loja == bling_store_id). Retorna None
    quando irrestrito (admin / sem equipe). Sem chaves = clause que zera."""
    if scope.unrestricted:
        return None
    ors = []
    if scope.account_names:
        ors.append(func.lower(func.btrim(Logistica.conta)).in_(scope.account_names))
    if scope.bling_store_ids:
        ors.append(
            Logistica.pedido_bling.in_(
                select(BlingOrder.numero).where(
                    BlingOrder.loja.in_(scope.bling_store_ids)
                )
            )
        )
    if not ors:
        return text("1=0")
    return or_(*ors)


@router.get("", response_model=list[LogisticaOut])
async def list_logistica(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("logistica", "view"))],
    plataforma: Annotated[str | None, Query()] = None,
) -> list[LogisticaOut]:
    # Mais recentes primeiro (data desc, depois criação desc).
    stmt = select(Logistica).order_by(
        desc(Logistica.data.is_(None)), desc(Logistica.data), desc(Logistica.created_at)
    )
    if plataforma:
        label = _PLATAFORMA_LABELS.get(plataforma.strip().lower(), plataforma)
        stmt = stmt.where(Logistica.plataforma == label)
    # Escopo por equipe: não-admin com equipe(s) só vê as linhas das lojas da
    # sua equipe (admin / sem-equipe = irrestrito).
    team_clause = _team_scope_clause(await resolve_team_scope(session, user))
    if team_clause is not None:
        stmt = stmt.where(team_clause)
    rows = (await session.execute(stmt)).scalars().all()
    # Casa cada pedido com a regra da aba Status (carrega o índice uma vez).
    status_rows = list((await session.execute(select(LogisticaStatus))).scalars().all())
    # Nome + SKU dos itens de todos os pedidos da página, numa query só.
    produtos_map = await _produtos_map(session, {c.pedido_bling for c in rows})
    out: list[LogisticaOut] = []
    for c in rows:
        assinatura = logistica_rules.assinatura_para(c.plataforma, c.meli_status or {})
        cands = logistica_match.find_matching_rules(
            status_rows, assinatura=assinatura, plataforma=c.plataforma
        )
        out.append(_to_out(c, cands, produtos=produtos_map.get(c.pedido_bling or "")))
    return out


@router.post("/recarregar", response_model=RecarregarOut)
async def recarregar(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "edit"))],
) -> RecarregarOut:
    """Enfileira a recarga em massa (re-enriquece o status do Meli de TODAS as
    linhas ML e aplica no Bling a mudança de situação das que casam uma regra da
    aba Status). Roda em background porque pode passar do timeout do Cloudflare.
    O front acompanha pelo `job_id` (GET /recarregar/{job_id}) e repuxa a lista
    enquanto isso."""
    pool = await get_arq_pool()
    job = await pool.enqueue_job("logistica_recarregar")
    job_id = job.job_id if job else None
    logger.info("logistica_recarregar_enqueued", job_id=job_id)
    return RecarregarOut(enqueued=True, job_id=job_id)


@router.get("/recarregar/{job_id}", response_model=RecarregarStatusOut)
async def recarregar_status(
    job_id: str,
    _user: Annotated[User, Depends(require_permission("logistica", "edit"))],
) -> RecarregarStatusOut:
    """Andamento da recarga em massa do botão. O front consulta a cada poll pra
    manter o spinner até o job REALMENTE acabar (antes eram 4 min fixos: recarga
    mais longa parecia quebrada e a lista parava de atualizar antes do fim) e
    pra mostrar o resumo no toast. `failed` = o job rodou e estourou; que não
    aconteça — mas se acontecer o operador fica sabendo em vez de olhar um
    spinner mudo."""
    pool = await get_arq_pool()
    job = Job(job_id, pool)
    st = await job.status()
    if st == JobStatus.complete:
        info = await job.result_info()
        if info is None:
            return RecarregarStatusOut(status="not_found")
        if not info.success:
            return RecarregarStatusOut(status="failed")
        resumo = (
            {k: v for k, v in info.result.items() if isinstance(v, int)}
            if isinstance(info.result, dict)
            else None
        )
        return RecarregarStatusOut(status="complete", resumo=resumo)
    return RecarregarStatusOut(status=st.value)


@router.post("", response_model=LogisticaOut, status_code=status.HTTP_201_CREATED)
async def create_logistica(
    body: LogisticaCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("logistica", "edit"))],
) -> LogisticaOut:
    c = Logistica(
        data=body.data,
        pedido_bling=_clean(body.pedido_bling),
        pedido_marketplace=_clean(body.pedido_marketplace),
        plataforma=_clean(body.plataforma),
        conta=_clean(body.conta),
        meli_status=_clean_meli(body.meli_status),
        # Linha nascendo com status preenchido na mão: carimba agora.
        status_datas=logistica_datas.merge_datas(
            anterior_status={}, novo_status=_clean_meli(body.meli_status)
        ),
        rastreio=_clean(body.rastreio),
        localizacao=_clean(body.localizacao),
        status_bling=_clean(body.status_bling),
        chamado=_clean(body.chamado),
        observacao=_clean(body.observacao),
        created_by=user.id,
    )
    session.add(c)
    await session.commit()
    await session.refresh(c)
    logger.info("logistica_created", id=str(c.id), pedido_bling=c.pedido_bling)
    return _to_out(c, await _match_rules(session, c), produtos=await _produtos_for(session, c))


@router.post("/{logistica_id}/atualizar-meli", response_model=LogisticaOut)
async def atualizar_meli(
    logistica_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "edit"))],
) -> LogisticaOut:
    """Puxa a assinatura de status do Meli (8 campos) da API do ML e grava em
    `meli_status`. Só vale pra pedidos de Mercado Livre com pedido de
    marketplace e conta com integração ML."""
    c = (
        await session.execute(select(Logistica).where(Logistica.id == logistica_id))
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "logistica_not_found"})
    try:
        await logistica_meli.enrich_row(session, c)
    except logistica_meli.MeliEnrichError as e:
        raise HTTPException(422, detail={"code": e.code}) from e
    except Exception as e:  # noqa: BLE001
        logger.warning("logistica_meli_atualizar_falhou", id=str(logistica_id), err=str(e)[:200])
        raise HTTPException(502, detail={"code": "logistica_meli_erro"}) from e
    try:
        await logistica_bling.sync_status_bling_row(session, c)
    except Exception as e:  # noqa: BLE001
        logger.warning("logistica_sync_status_bling_falhou", id=str(logistica_id), err=str(e)[:200])
    await session.commit()
    await session.refresh(c)
    return _to_out(c, await _match_rules(session, c), produtos=await _produtos_for(session, c))


@router.post("/{logistica_id}/atualizar-shopee", response_model=LogisticaOut)
async def atualizar_shopee(
    logistica_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "edit"))],
) -> LogisticaOut:
    """Puxa o order_status da Shopee (API v2) e grava em `meli_status`. Só vale
    pra pedidos Shopee com pedido de marketplace e conta com integração Shopee."""
    c = (
        await session.execute(select(Logistica).where(Logistica.id == logistica_id))
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "logistica_not_found"})
    try:
        await logistica_shopee.enrich_row(session, c)
    except logistica_shopee.ShopeeEnrichError as e:
        raise HTTPException(422, detail={"code": e.code}) from e
    except Exception as e:  # noqa: BLE001
        logger.warning("logistica_shopee_atualizar_falhou", id=str(logistica_id), err=str(e)[:200])
        raise HTTPException(502, detail={"code": "logistica_shopee_erro"}) from e
    try:
        await logistica_bling.sync_status_bling_row(session, c)
    except Exception as e:  # noqa: BLE001
        logger.warning("logistica_sync_status_bling_falhou", id=str(logistica_id), err=str(e)[:200])
    await session.commit()
    await session.refresh(c)
    return _to_out(c, await _match_rules(session, c), produtos=await _produtos_for(session, c))


@router.post("/{logistica_id}/atualizar-tiktok", response_model=LogisticaOut)
async def atualizar_tiktok(
    logistica_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "edit"))],
) -> LogisticaOut:
    """Puxa o status do pedido TikTok (Order API 202309) + rastreio + localização
    e grava em `meli_status`. Só vale pra pedidos TikTok com pedido de
    marketplace e conta com integração TikTok."""
    c = (
        await session.execute(select(Logistica).where(Logistica.id == logistica_id))
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "logistica_not_found"})
    try:
        await logistica_tiktok.enrich_row(session, c)
    except logistica_tiktok.TikTokEnrichError as e:
        raise HTTPException(422, detail={"code": e.code}) from e
    except Exception as e:  # noqa: BLE001
        logger.warning("logistica_tiktok_atualizar_falhou", id=str(logistica_id), err=str(e)[:200])
        raise HTTPException(502, detail={"code": "logistica_tiktok_erro"}) from e
    try:
        await logistica_bling.sync_status_bling_row(session, c)
    except Exception as e:  # noqa: BLE001
        logger.warning("logistica_sync_status_bling_falhou", id=str(logistica_id), err=str(e)[:200])
    await session.commit()
    await session.refresh(c)
    return _to_out(c, await _match_rules(session, c), produtos=await _produtos_for(session, c))


@router.post("/{logistica_id}/atualizar-amazon", response_model=LogisticaOut)
async def atualizar_amazon(
    logistica_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "edit"))],
) -> LogisticaOut:
    """Puxa o status do pedido Amazon (OrderStatus + EasyShip) e grava em
    `meli_status`. Só vale pra pedidos Amazon com pedido de marketplace e conta
    com integração Amazon."""
    c = (
        await session.execute(select(Logistica).where(Logistica.id == logistica_id))
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "logistica_not_found"})
    try:
        await logistica_amazon.enrich_row(session, c)
    except logistica_amazon.AmazonEnrichError as e:
        raise HTTPException(422, detail={"code": e.code}) from e
    except Exception as e:  # noqa: BLE001
        logger.warning("logistica_amazon_atualizar_falhou", id=str(logistica_id), err=str(e)[:200])
        raise HTTPException(502, detail={"code": "logistica_amazon_erro"}) from e
    try:
        await logistica_bling.sync_status_bling_row(session, c)
    except Exception as e:  # noqa: BLE001
        logger.warning("logistica_sync_status_bling_falhou", id=str(logistica_id), err=str(e)[:200])
    await session.commit()
    await session.refresh(c)
    return _to_out(c, await _match_rules(session, c), produtos=await _produtos_for(session, c))


@router.post("/{logistica_id}/enviar-threema", response_model=EnviarThreemaOut)
async def enviar_threema_pedido(
    logistica_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "edit"))],
    payload: EnviarThreemaIn | None = None,
) -> EnviarThreemaOut:
    """Envia a Mensagem Threema da regra que CASA com este pedido (coligação da
    aba Status), já com `Pedido X | Loja Y` no topo — vindos da própria linha do
    marketplace. Destinatários: escolha explícita do body > lista salva na regra
    (👤) > lista fixa do `.env`. 404 se a linha some; 422 sem regra com mensagem
    Threema / sem destinatário / sem config."""
    payload = payload or EnviarThreemaIn()
    c = (
        await session.execute(select(Logistica).where(Logistica.id == logistica_id))
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "logistica_not_found"})
    # Só regra aplicável ao estado atual do Bling (a mesma que deixa a linha
    # pendente no painel) — regra de outro estado não empresta a mensagem.
    rule = next(
        (
            r
            for r in logistica_match.regras_aplicaveis(
                await _match_rules(session, c), c.status_bling
            )
            if (r.mensagem_threema or "").strip()
        ),
        None,
    )
    if rule is None:
        raise HTTPException(422, detail={"code": "logistica_sem_mensagem_threema"})
    if payload.recipients is not None and not payload.recipients:
        raise HTTPException(422, detail={"code": "logistica_sem_destinatario_threema"})
    recipients = payload.recipients
    if recipients is None:
        recipients = threema.parse_recipients(rule.threema_recipients) or None
    texto = threema.compose_texto(
        (rule.mensagem_threema or "").strip(),
        pedido=c.pedido_marketplace or c.pedido_bling,
        loja=c.plataforma,
    )
    try:
        result = await threema.ThreemaClient().send_to_all(texto, recipients=recipients or None)
    except threema.ThreemaConfigError as e:
        raise HTTPException(422, detail={"code": str(e)}) from e
    # Enviou pra ao menos um → o aviso foi feito: carimba e o pedido resolve
    # (a Mensagem Threema deixa de contar como pendência) e some do painel.
    if result["sent"]:
        c.threema_enviado_at = datetime.now(UTC)
        await session.commit()
    logger.info(
        "logistica_threema_pedido_enviado",
        id=str(logistica_id),
        sent=len(result["sent"]),
        failed=len(result["failed"]),
    )
    return EnviarThreemaOut(sent=result["sent"], failed=result["failed"])


async def _mensagem_chamado_para(session: AsyncSession, c: Logistica) -> str | None:
    """Mensagem do chamado da regra da aba Status que vale AGORA pra linha:
    chave (assinatura PT) casada — específica da plataforma > geral — E
    aplicável ao estado atual do Bling (status_atual igual ou curinga). Regra
    de outro estado não empresta mensagem. None se nenhuma aplicável tiver."""
    cands = await _match_rules(session, c)
    for r in logistica_match.regras_aplicaveis(cands, c.status_bling):
        msg = (r.mensagem_chamado or "").strip()
        if msg:
            return msg
    return None


@router.post("/{logistica_id}/enviar-chamado", response_model=LogisticaOut)
async def enviar_chamado(
    logistica_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "edit"))],
) -> LogisticaOut:
    """Abre o chamado (mediação) direto no Mercado Livre pro pedido da linha e
    manda a mensagem da regra da aba Status (que casa com o Status Plataforma)
    pro mediador do ML. Grava o claim_id em `chamado`. Só vale pra pedidos de ML
    que já tenham uma reclamação aberta pelo comprador (a API do ML não deixa o
    vendedor abrir reclamação do zero)."""
    c = (
        await session.execute(select(Logistica).where(Logistica.id == logistica_id))
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "logistica_not_found"})
    mensagem = await _mensagem_chamado_para(session, c)
    if not mensagem:
        raise HTTPException(422, detail={"code": "logistica_sem_mensagem_chamado"})
    try:
        await logistica_meli.enviar_chamado_for_row(session, c, mensagem)
    except logistica_meli.MeliEnrichError as e:
        raise HTTPException(422, detail={"code": e.code}) from e
    except Exception as e:  # noqa: BLE001
        logger.warning("logistica_enviar_chamado_falhou", id=str(logistica_id), err=str(e)[:300])
        raise HTTPException(502, detail={"code": "logistica_chamado_erro", "erro": str(e)[:300]}) from e
    await session.commit()
    await session.refresh(c)
    logger.info("logistica_chamado_enviado", id=str(logistica_id), chamado=c.chamado)
    return _to_out(c, await _match_rules(session, c), produtos=await _produtos_for(session, c))


@router.post("/{logistica_id}/mensagem-bling/preview", response_model=MensagemBlingPreviewOut)
async def preview_mensagem_bling(
    logistica_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "edit"))],
) -> MensagemBlingPreviewOut:
    """Dry-run: mostra o que SERIA escrito nas Observações do pedido Bling
    (linha datada com a `mensagem_bling` da regra casada, no topo), SEM
    escrever nada. Faz só um GET do pedido pra montar o corpo do PUT."""
    c = (
        await session.execute(select(Logistica).where(Logistica.id == logistica_id))
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "logistica_not_found"})
    try:
        data = await logistica_bling.preview_mensagem_bling(session, c)
    except logistica_bling.BlingObsError as e:
        raise HTTPException(422, detail={"code": e.code}) from e
    except Exception as e:  # noqa: BLE001
        logger.warning("logistica_mensagem_bling_preview_falhou", id=str(logistica_id), err=str(e)[:300])
        raise HTTPException(502, detail={"code": "logistica_mensagem_bling_erro", "erro": str(e)[:300]}) from e
    return MensagemBlingPreviewOut(**data)


@router.post("/{logistica_id}/mensagem-bling", response_model=MensagemBlingOut)
async def aplicar_mensagem_bling(
    logistica_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "edit"))],
) -> MensagemBlingOut:
    """Aplica a Mensagem Bling: anexa a linha datada com a `mensagem_bling` da
    regra casada NO TOPO das Observações (Dados adicionais) do pedido no Bling
    via PUT (reenvio do pedido inteiro sanitizado). Nunca sobrescreve o que já
    estava."""
    c = (
        await session.execute(select(Logistica).where(Logistica.id == logistica_id))
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "logistica_not_found"})
    try:
        data = await logistica_bling.apply_mensagem_bling(session, c)
    except logistica_bling.BlingObsError as e:
        raise HTTPException(422, detail={"code": e.code}) from e
    except Exception as e:  # noqa: BLE001
        logger.warning("logistica_mensagem_bling_falhou", id=str(logistica_id), err=str(e)[:300])
        raise HTTPException(502, detail={"code": "logistica_mensagem_bling_erro", "erro": str(e)[:300]}) from e
    await session.commit()
    logger.info("logistica_mensagem_bling_ok", id=str(logistica_id), bling_order_id=data.get("bling_order_id"))
    return MensagemBlingOut(**data)


@router.post("/{logistica_id}/alterar-status-bling/preview", response_model=StatusBlingPreviewOut)
async def preview_alterar_status_bling(
    logistica_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "edit"))],
) -> StatusBlingPreviewOut:
    """Dry-run: lê a situação ATUAL do pedido no Bling e mostra atual -> alvo
    (o `alterar_status_bling` da regra casada), SEM mudar nada. Faz só um GET."""
    c = (
        await session.execute(select(Logistica).where(Logistica.id == logistica_id))
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "logistica_not_found"})
    try:
        data = await logistica_bling.preview_alterar_status_bling(session, c)
    except logistica_bling.BlingObsError as e:
        raise HTTPException(422, detail={"code": e.code}) from e
    except Exception as e:  # noqa: BLE001
        logger.warning("logistica_alterar_status_bling_preview_falhou", id=str(logistica_id), err=str(e)[:300])
        raise HTTPException(502, detail={"code": "logistica_alterar_status_bling_erro", "erro": str(e)[:300]}) from e
    # O preview sincroniza `status_bling` com a situação viva do Bling (dentro do
    # _resolve_status). Persiste pra o painel se auto-corrigir mesmo em "nada a fazer".
    await session.commit()
    return StatusBlingPreviewOut(**data)


@router.post("/{logistica_id}/alterar-status-bling", response_model=StatusBlingOut)
async def aplicar_alterar_status_bling(
    logistica_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "edit"))],
) -> StatusBlingOut:
    """Muda a situação do pedido no Bling para a `alterar_status_bling` da regra
    casada (via PATCH /situacoes — não reenvia o pedido) e sincroniza o
    `status_bling` local da linha."""
    c = (
        await session.execute(select(Logistica).where(Logistica.id == logistica_id))
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "logistica_not_found"})
    try:
        data = await logistica_bling.apply_alterar_status_bling(session, c)
    except logistica_bling.BlingObsError as e:
        raise HTTPException(422, detail={"code": e.code}) from e
    except Exception as e:  # noqa: BLE001
        logger.warning("logistica_alterar_status_bling_falhou", id=str(logistica_id), err=str(e)[:300])
        raise HTTPException(502, detail={"code": "logistica_alterar_status_bling_erro", "erro": str(e)[:300]}) from e
    await session.commit()
    logger.info("logistica_alterar_status_bling_ok", id=str(logistica_id), bling_order_id=data.get("bling_order_id"))
    return StatusBlingOut(**data)


@router.patch("/{logistica_id}", response_model=LogisticaOut)
async def patch_logistica(
    logistica_id: UUID,
    body: LogisticaPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "edit"))],
) -> LogisticaOut:
    c = (
        await session.execute(select(Logistica).where(Logistica.id == logistica_id))
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "logistica_not_found"})

    data = body.model_dump(exclude_unset=True)
    if "data" in data:
        c.data = data["data"]
    if "pedido_bling" in data:
        c.pedido_bling = _clean(data["pedido_bling"])
    if "pedido_marketplace" in data:
        c.pedido_marketplace = _clean(data["pedido_marketplace"])
    if "plataforma" in data:
        c.plataforma = _clean(data["plataforma"])
    if "conta" in data:
        c.conta = _clean(data["conta"])
    if "meli_status" in data:
        # Edição na mão também carimba: o campo que o operador mudar passa a
        # contar a partir de agora (fonte "davinci"); o que ele não mexeu
        # mantém a data que já tinha.
        novo_status = _clean_meli(data["meli_status"])
        c.status_datas = logistica_datas.aplicar(c, novo_status)
        c.meli_status = novo_status
    if "rastreio" in data:
        c.rastreio = _clean(data["rastreio"])
    if "localizacao" in data:
        c.localizacao = _clean(data["localizacao"])
    if "status_bling" in data:
        c.status_bling = _clean(data["status_bling"])
    if "chamado" in data:
        c.chamado = _clean(data["chamado"])
    if "observacao" in data:
        c.observacao = _clean(data["observacao"])

    await session.commit()
    await session.refresh(c)
    return _to_out(c, await _match_rules(session, c), produtos=await _produtos_for(session, c))


@router.delete("/{logistica_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_logistica(
    logistica_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("logistica", "edit"))],
) -> None:
    c = (
        await session.execute(select(Logistica).where(Logistica.id == logistica_id))
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, detail={"code": "logistica_not_found"})
    await session.delete(c)
    await session.commit()
    logger.info("logistica_deleted", id=str(logistica_id))
    return None
