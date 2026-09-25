"""Chamados — aba de Pós-venda que centraliza os chamados abertos nas
plataformas (origem Margem / Logística / Devolução). Formato da aba
`Chamados` da planilha: Data | pedido bling | pedido marketplace | plataforma
| produto | sku | conta | status bling | origem | chamado | réplica | réplica
automática | alterar status bling | observação | valor.

Recurso de permissão: `chamados` (view/edit/delete). As regras/ações vivem em
app.services.chamados; aqui é CRUD + histórico + anexos + os botões.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
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
from fastapi.responses import HTMLResponse
from sqlalchemy import Text, case, cast, func, literal, or_, select
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db import get_session
from app.deps.auth import require_permission
from app.models import (
    BlingOrder,
    Chamado,
    ChamadoAnexo,
    ChamadoCerebro,
    ChamadoIaAvaliacao,
    ChamadoIaRegra,
    ChamadoLeitor,
    ChamadoMensagem,
    DevolucaoAnexo,
    Devolution,
    Logistica,
    User,
    UserRole,
)
from app.models.chamado import CANAIS, ORIGENS
from app.schemas.chamados import (
    AgentAnalisarIn,
    AgentAnalisarOut,
    AgentAnaliseIn,
    AgentAnaliseOut,
    AgentAprendizadoOut,
    AgentBloqueioOut,
    AgentCasoIn,
    AgentCasoLeituraOut,
    AgentCerebroIn,
    AgentCerebroOut,
    AgentChamadoAnaliseOut,
    AgentExemplosIn,
    AgentExemplosOut,
    AgentHistoricoIn,
    AgentHistoricoOut,
    AgentInstrucaoOut,
    AgentLeaseIn,
    AgentLeaseOut,
    AgentLeitorFilaIn,
    AgentLeituraIn,
    AgentLeituraOut,
    AgentLeituraResultadoIn,
    AgentLeituraResultadoOut,
    AgentMensagemOut,
    AgentPagamentoMlIn,
    AgentPagamentoMlItem,
    AgentPagamentoMlOut,
    AgentRecebidaIn,
    AgentRecebidaOut,
    AgentRegistrarIn,
    AgentRegistrarOut,
    AgentRegraOut,
    AgentResultadoIn,
    AgentShopeeProvaIn,
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
    ExcluirEstornoOut,
    ExcluirIn,
    ExcluirOut,
    ExclusaoLancamentoOut,
    ExclusaoPreviewOut,
    IaAvaliacaoOut,
    InstrucaoIn,
    JuridicoIn,
    JuridicoOut,
    ResolverIn,
    SituacoesOut,
)
from app.services import chamados as svc
from app.services import (
    chamados_devolucao,
    chamados_devolucao_sync,
    chamados_juridico,
    chamados_leitura,
)
from app.services.devolution_delete import EstornoFalhouError, excluir_lancamento
from app.services.texto_html import limpar_html

logger = structlog.get_logger()
router = APIRouter(prefix="/api/chamados", tags=["chamados"])

_ANEXO_TIPOS = {"image/png", "image/jpeg", "image/webp", "image/gif"}
_ANEXO_MAX_BYTES = 8 * 1024 * 1024  # 8 MB


def _autor(user: User) -> str:
    return (user.name or user.email or "").strip() or "usuário"


def _pode(user: User, resource: str, action: str) -> bool:
    """Mesma regra do `require_permission`, em bool (sem levantar 403) — pra
    rota que faz MAIS quando o usuário tem outra permissão (lixeira do chamado
    + devolucoes.delete)."""
    if user.role == UserRole.ADMIN:
        return True
    return bool(((user.permissions or {}).get(resource) or {}).get(action, False))


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
        texto=limpar_html(m.texto),
        canal=m.canal,
        status=m.status,
        erro=m.erro,
        autor_nome=m.autor_nome,
        enviada_at=m.enviada_at,
        created_at=m.created_at,
        anexos=[_anexo_out(a) for a in (m.anexos or [])],
    )


async def _custo_produto_map(
    session: AsyncSession, numeros: set[str]
) -> dict[str, tuple[Decimal | None, str | None]]:
    """19/09 (só mostrar): custo dos itens do pedido no espelho bling_orders, por
    nº Bling — SUM(preco_custo × quantidade) (linhas sem custo não somam) e o
    detalhe "sku × qtd; …" (todas as linhas, mesmo sem custo) — pra pessoa
    decidir lucro/prejuízo ao concluir. Uma query pra página."""
    limpos = {n for n in numeros if n}
    if not limpos:
        return {}
    qtd = func.coalesce(BlingOrder.item_quantidade, 1)
    custo_linha = case(
        (func.coalesce(BlingOrder.preco_custo, 0) > 0, BlingOrder.preco_custo * qtd),
        else_=0,
    )
    detalhe_linha = func.coalesce(BlingOrder.item_codigo, "?") + " × " + cast(qtd, Text)
    rows = await session.execute(
        select(
            BlingOrder.numero,
            func.sum(custo_linha),
            func.string_agg(
                detalhe_linha,
                aggregate_order_by(literal("; "), BlingOrder.item_index, BlingOrder.item_codigo),
            ),
        )
        .where(BlingOrder.numero.in_(list(limpos)))
        .group_by(BlingOrder.numero)
    )
    out: dict[str, tuple[Decimal | None, str | None]] = {}
    for numero, custo, detalhe in rows.all():
        if not numero:
            continue
        valor = Decimal(str(custo)).quantize(Decimal("0.01")) if custo else None
        out[str(numero)] = (valor if valor and valor > 0 else None, detalhe or None)
    return out


def _instrucao_pendente(msgs: list[ChamadoMensagem]) -> ChamadoMensagem | None:
    """19/09: a última `instrucao` nossa que o robô ainda não leu (mais nova que
    a última `analise`). A análise do cérebro é o que consome a instrução."""
    instrucoes = [m for m in msgs if m.tipo == "instrucao"]
    if not instrucoes:
        return None
    ultima = instrucoes[-1]
    analises = [m for m in msgs if m.tipo == "analise"]
    if analises and analises[-1].created_at >= ultima.created_at:
        return None
    return ultima


async def _to_out(session: AsyncSession, rows: list[Chamado]) -> list[ChamadoOut]:
    """Monta a saída em LOTE: status Bling vivo, custo do produto, histórico
    resumido (contagem, última fala, instrução pendente, status da aba — do
    CASO, juntando as linhas irmãs) e anexos da réplica automática — 5 queries
    pra página inteira."""
    if not rows:
        return []
    ids = [r.id for r in rows]
    numeros = {r.pedido_bling for r in rows if r.pedido_bling}
    status_map = await svc.status_bling_atual_map(session, numeros)
    custo_map = await _custo_produto_map(session, numeros)
    # 17/09 (Vinicius): a linha mostra a última FALA (nossa ou da plataforma —
    # análise do robô e evento não contam) e o Status, que depende de quem falou
    # por último e do que o cérebro pediu. A conversa completa (`historico`)
    # fica fora, como antes.
    #
    # 18/09: a conversa é do CASO, não da linha — a mesma consulta do ML vale pra
    # vários pedidos e o leitor grava a resposta numa linha, o cérebro replica por
    # outra (ver `_mensagens_do_caso`). As linhas irmãs podem estar fora da página
    # (resolvidas, outro filtro), por isso a busca é pelo protocolo + conta.
    chave_por_linha = {r.id: _chave_caso(r) for r in rows}
    irmas: dict[tuple[str, str], list[UUID]] = {}
    protocolos = {c[0] for c in chave_por_linha.values() if c}
    if protocolos:
        for cid, protocolo, conta in (
            await session.execute(
                select(Chamado.id, Chamado.chamado, Chamado.conta).where(Chamado.chamado.in_(protocolos))
            )
        ).all():
            irmas.setdefault(((protocolo or "").strip(), (conta or "").strip().lower()), []).append(cid)
    ids_caso = set(ids) | {cid for grupo in irmas.values() for cid in grupo}
    por_chamado: dict[UUID, list[ChamadoMensagem]] = {}
    for m in (
        await session.execute(
            select(ChamadoMensagem)
            .where(ChamadoMensagem.chamado_id.in_(ids_caso), ChamadoMensagem.tipo != TIPO_HISTORICO)
            .order_by(ChamadoMensagem.created_at, _ORDEM_SISTEMA_SQL, ChamadoMensagem.id)
        )
    ).scalars():
        por_chamado.setdefault(m.chamado_id, []).append(m)

    def mensagens_do_caso(r: Chamado) -> list[ChamadoMensagem]:
        chave = chave_por_linha[r.id]
        grupo = (set(irmas.get(chave, [])) if chave else set()) | {r.id}
        if len(grupo) == 1:
            return por_chamado.get(r.id, [])
        juntas = [m for cid in grupo for m in por_chamado.get(cid, [])]
        juntas.sort(key=_ordem_mensagem)
        return _sem_repetidas(juntas)
    anexos_auto: dict[UUID, list[ChamadoAnexoOut]] = {}
    for a in (
        await session.execute(
            select(ChamadoAnexo)
            .where(ChamadoAnexo.chamado_id.in_(ids), ChamadoAnexo.mensagem_id.is_(None))
            .order_by(ChamadoAnexo.created_at)
        )
    ).scalars():
        anexos_auto.setdefault(a.chamado_id, []).append(_anexo_out(a))

    quem_ids = {r.juridico_enviado_por for r in rows if r.juridico_enviado_por}
    nomes: dict[UUID, str] = {}
    if quem_ids:
        for u in (await session.execute(select(User).where(User.id.in_(quem_ids)))).scalars():
            nomes[u.id] = u.name or u.email

    out: list[ChamadoOut] = []
    for r in rows:
        msgs = mensagens_do_caso(r)
        o = ChamadoOut.model_validate(r)
        o.status_bling_atual = status_map.get(r.pedido_bling or "") or r.status_bling
        o.custo_produto, o.custo_detalhe = custo_map.get(r.pedido_bling or "", (None, None))
        o.mensagens_total = len(msgs)
        o.ultima_mensagem_at = msgs[-1].created_at if msgs else None
        falas = [m for m in msgs if m.direcao in ("enviada", "recebida")]
        entregues = [m for m in falas if m.status not in ("pendente", "falhou")]
        if entregues:
            u = entregues[-1]
            o.ultima_resposta_at = u.enviada_at or u.created_at
            o.ultima_resposta_direcao = u.direcao
            o.ultima_resposta_autor = u.autor_nome
        analises = [m for m in msgs if m.tipo == "analise"]
        ultima_analise = analises[-1] if analises else None
        instrucao = _instrucao_pendente(msgs)
        o.instrucao_pendente = instrucao.texto if instrucao else None
        # 19/09 (regra 5, prova): alguma fala NOSSA que saiu depois do status oficial?
        # Olhar só a última fala fazia a prova já enviada voltar a "pedir humano"
        # quando a Shopee respondia em seguida.
        nossa_apos_status = any(
            m.direcao == "enviada"
            and m.status in ("enviada", "registrada")
            and (r.status_plataforma_at is None or m.created_at > r.status_plataforma_at)
            for m in falas
        )
        o.status_aba, o.status_aba_at, o.status_aba_motivo = svc.status_e_motivo_da_aba(
            r,
            ultima_fala=falas[-1] if falas else None,
            ultima_analise=ultima_analise,
            analise_pede_humano=bool(
                ultima_analise and ultima_analise.texto.endswith(_ACAO_TXT["humano"])
            ),
            analise_pede_esperar=bool(
                ultima_analise and ultima_analise.texto.endswith(_ACAO_TXT["esperar"])
            ),
            instrucao_pendente=instrucao,
            nossa_fala_apos_status=nossa_apos_status,
        )
        o.auto_proximo_envio_at = svc.auto_proximo_envio(r)
        o.anexos_auto = anexos_auto.get(r.id, [])
        if r.juridico_enviado_por:
            o.juridico_enviado_por_nome = nomes.get(r.juridico_enviado_por)
        if r.juridico_token and r.juridico_enviado_at:
            o.juridico_link = chamados_juridico.link_dossie(r.juridico_token)
        o.juridico_enviados = [x for x in (r.juridico_destinatarios or "").split(",") if x.strip()]
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
    conta: str | None = Query(None),
    mostrar: str = Query("abertos", pattern="^(abertos|resolvidos|todos)$"),
    juridico: bool | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> ChamadoPage:
    conds = []
    if juridico:
        # Aba Jurídico: tudo que já foi encaminhado, aberto ou resolvido.
        conds.append(Chamado.juridico_enviado_at.is_not(None))
    elif mostrar == "abertos":
        conds.append(Chamado.resolvido.is_(False))
    elif mostrar == "resolvidos":
        conds.append(Chamado.resolvido.is_(True))
    if origem and origem in ORIGENS:
        conds.append(Chamado.origem == origem)
    cond_plataforma = None
    if plataforma:
        cond_plataforma = (
            func.lower(func.coalesce(Chamado.plataforma, "")) == plataforma.strip().lower()
        )
        conds.append(cond_plataforma)
    if conta and conta.strip():
        # Filtro por conta (Eduardo 15/09: "ex. ML Aguiar 2").
        conds.append(func.lower(func.coalesce(Chamado.conta, "")) == conta.strip().lower())
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
    # Contas do dropdown: só as da plataforma filtrada (quando há uma), uma
    # por nome ignorando caixa, na grafia que aparece na linha.
    q_contas = (
        select(func.min(Chamado.conta))
        .where(func.coalesce(Chamado.conta, "") != "")
        .group_by(func.lower(Chamado.conta))
        .order_by(func.lower(func.min(Chamado.conta)))
    )
    if cond_plataforma is not None:
        q_contas = q_contas.where(cond_plataforma)
    contas = [c for c in (await session.execute(q_contas)).scalars() if c]
    return ChamadoPage(
        items=await _to_out(session, rows),
        total=total,
        limit=limit,
        offset=offset,
        plataformas=plataformas,
        contas=contas,
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
    if data.get("consulta_portal") == "":
        data["consulta_portal"] = None  # campo apagado na tela
    for key, value in data.items():
        setattr(ch, key, value)
    if "observacao" in data and "consulta_portal" not in data:
        # 25/09 (294571): "abri na mão… id da consulta 2103…" na Observação liga a
        # consulta do Portal ao chamado — o executor de leitura passa a ler lá.
        chamados_leitura.ligar_consulta_do_texto(ch, data["observacao"])
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


# 15/09: conversa COMPLETA da página do caso (ver agent_historico). direcao=sistema →
# o cérebro (/agent/analisar), a fila (/agent/lease) e os leitores (recebida) ignoram.
TIPO_HISTORICO = "historico"

# 15/09 (Eduardo, consulta 478705311): a MESMA consulta do ML fica ligada a vários
# pedidos (uma linha da aba por pedido). O leitor grava a resposta numa linha só
# (o 1º pedido do grupo) e o histórico ficava picado — o pedido certo aparecia
# vazio. Aqui o histórico é do CASO: todas as linhas da conta com o mesmo
# protocolo NUMÉRICO (texto livre tipo "Disputa na venda" não agrupa), sem repetir
# a mensagem gravada em todas as linhas (mesmo tipo e texto em até 2 min).
#
# 18/09 (Vinicius, mesma consulta 478538390 × 3 pedidos): a coluna Status e a
# Últ. resposta da lista usam a MESMA junção (`_chave_caso` + `_sem_repetidas`)
# — olhando só a própria linha, o 290397 ficava "Plataforma respondeu" 3 dias
# depois de o robô já ter replicado pela linha irmã.
def _chave_caso(ch: Chamado) -> tuple[str, str] | None:
    """(protocolo, conta) que junta as linhas de um mesmo caso; None quando o
    protocolo não é numérico (texto livre não agrupa)."""
    protocolo = (ch.chamado or "").strip()
    if protocolo.isdigit() and len(protocolo) >= 6:
        return protocolo, (ch.conta or "").strip().lower()
    return None


# 15/09 (Eduardo: "colocar a mensagem e depois caso encerrado"): a fala e o evento
# "Chamado marcado como resolvido" nascem na MESMA transação (mesmo created_at) — o
# desempate pelo id (uuid aleatório) punha o encerrado antes da mensagem. Empate: sistema por último.
# e entre os eventos de sistema do mesmo instante, a análise vem antes do
# "Chamado marcado como resolvido/reaberto" (tipo sistema).
_ORDEM_SISTEMA_SQL = case(
    ((ChamadoMensagem.direcao == "sistema") & (ChamadoMensagem.tipo == "sistema"), 2),
    (ChamadoMensagem.direcao == "sistema", 1),
    else_=0,
)


def _ordem_mensagem(m: ChamadoMensagem) -> tuple:
    """Mesma ordem do SQL acima, pra juntar em memória as linhas irmãs de um caso."""
    sistema = 2 if (m.direcao == "sistema" and m.tipo == "sistema") else 1 if m.direcao == "sistema" else 0
    return m.created_at, sistema, str(m.id)


def _sem_repetidas(rows: list[ChamadoMensagem]) -> list[ChamadoMensagem]:
    """Tira a mensagem gravada em todas as linhas do caso (mesma direção, tipo e
    texto em até 2 min). `rows` já em ordem cronológica."""
    vistos: dict[tuple, object] = {}
    out: list[ChamadoMensagem] = []
    for m in rows:
        chave = (m.direcao, m.tipo, (m.texto or "").strip())
        ant = vistos.get(chave)
        if ant is not None and m.created_at and ant and abs((m.created_at - ant).total_seconds()) <= 120:
            continue
        vistos[chave] = m.created_at
        out.append(m)
    return out


async def _mensagens_do_caso(session: AsyncSession, ch: Chamado, *, com_anexos: bool = False) -> list[ChamadoMensagem]:
    chave = _chave_caso(ch)
    if chave is not None:
        ids = (
            await session.execute(
                select(Chamado.id).where(
                    Chamado.chamado == chave[0],
                    func.lower(func.coalesce(Chamado.conta, "")) == chave[1],
                )
            )
        ).scalars().all() or [ch.id]
    else:
        ids = [ch.id]
    q = select(ChamadoMensagem).where(ChamadoMensagem.chamado_id.in_(ids))
    if com_anexos:
        q = q.options(selectinload(ChamadoMensagem.anexos))
    rows = (await session.execute(q.order_by(ChamadoMensagem.created_at, _ORDEM_SISTEMA_SQL, ChamadoMensagem.id))).scalars().all()
    if len(ids) == 1:
        return list(rows)
    return _sem_repetidas(list(rows))


# ------------------------------------------------------------- histórico / réplica


@router.get("/{chamado_id}/mensagens", response_model=list[ChamadoMensagemOut])
async def list_mensagens(
    chamado_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[User, Depends(require_permission("chamados", "view"))],
) -> list[ChamadoMensagemOut]:
    ch = await _get(session, chamado_id)
    rows = await _mensagens_do_caso(session, ch, com_anexos=True)
    out = [_mensagem_out(m) for m in rows]
    # 24/09: as análises da IA de Chamado levam o ✓/✗ (a pessoa avalia no histórico)
    nomes_ia = set(
        (
            await session.execute(
                select(ChamadoCerebro.nome).where(ChamadoCerebro.revoked_at.is_(None))
            )
        ).scalars()
    )
    ids_ia = [m.id for m in rows if m.tipo == "analise" and m.autor_nome in nomes_ia]
    if ids_ia:
        avs = (
            await session.execute(
                select(ChamadoIaAvaliacao, User)
                .outerjoin(User, User.id == ChamadoIaAvaliacao.updated_by)
                .where(ChamadoIaAvaliacao.mensagem_id.in_(ids_ia))
            )
        ).all()
        por_msg = {
            av.mensagem_id: IaAvaliacaoOut(
                certo=av.certo,
                correcao=av.correcao,
                autor=_autor(u) if u is not None else None,
                quando=av.updated_at,
            )
            for av, u in avs
        }
        for o in out:
            if o.id in ids_ia:
                o.da_ia = True
                o.avaliacao_ia = por_msg.get(o.id)
    return out


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
    if msg.status != "falhou":
        # 25/09 (296550): respondemos num Encerrado sem decisão → o caso seguiu
        # (a coluna vai pra Aguard. Plataforma e a varredura volta a ler).
        evento = svc.sair_de_encerrado(ch, "respondemos à plataforma")
        if evento is not None:
            session.add(evento)
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


# ------------------------------------------------------------- jurídico


@router.post("/{chamado_id}/juridico", response_model=JuridicoOut)
async def encaminhar_juridico(
    chamado_id: UUID,
    body: JuridicoIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("chamados", "edit"))],
) -> JuridicoOut:
    """Encaminha o chamado ao jurídico: aviso no Threema (destinatários do
    Informar `juridico`) com o link do dossiê (histórico + fotos), carimba
    quem/quando e registra no histórico. Pode repetir (reenvio)."""
    ch = await _get(session, chamado_id)
    try:
        r = await chamados_juridico.encaminhar(session, ch, user, body.observacao)
    except svc.ChamadoError as e:
        raise HTTPException(422, detail={"code": e.code}) from e
    await session.commit()
    await session.refresh(ch)
    out = await _one_out(session, ch)
    return JuridicoOut(chamado=out, sent=r["sent"], failed=r["failed"], link=r["link"])


async def _chamado_por_token(session: AsyncSession, token: str) -> Chamado:
    token = (token or "").strip()
    if len(token) < 16:
        raise HTTPException(404, detail={"code": "chamado_not_found"})
    ch = (
        await session.execute(select(Chamado).where(Chamado.juridico_token == token))
    ).scalar_one_or_none()
    if ch is None or ch.juridico_enviado_at is None:
        raise HTTPException(404, detail={"code": "chamado_not_found"})
    return ch


@router.get("/juridico/dossie/{token}", response_class=HTMLResponse)
async def dossie_juridico(
    token: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HTMLResponse:
    """PÚBLICO (por token secreto): dossiê do chamado pro jurídico — cabeçalho,
    devolução, histórico completo e fotos. É o link que vai no Threema."""
    ch = await _chamado_por_token(session, token)
    d = await chamados_juridico.dados_dossie(session, ch)
    return HTMLResponse(
        chamados_juridico.render_html(d, link=chamados_juridico.link_dossie(token)),
        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"},
    )


@router.get("/juridico/dossie/{token}/anexo/{tipo}/{anexo_id}")
async def dossie_juridico_anexo(
    token: str,
    tipo: str,
    anexo_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Foto/vídeo do dossiê (tipo `c` = anexo do chamado, `d` = da devolução),
    só se pertencer ao chamado do token."""
    ch = await _chamado_por_token(session, token)
    a = None
    ok = False
    if tipo == "c":
        a = await session.get(ChamadoAnexo, anexo_id)
        ok = a is not None and a.chamado_id == ch.id
    elif tipo == "d":
        a = await session.get(DevolucaoAnexo, anexo_id)
        if a is not None:
            dev = await session.get(Devolution, a.devolution_id)
            ok = dev is not None and bool(
                (ch.pedido_bling and dev.pedido_bling == ch.pedido_bling)
                or str(dev.id) == (ch.origem_ref or "")
            )
    if not ok or a is None:
        raise HTTPException(404, detail={"code": "chamado_anexo_not_found"})
    return Response(
        content=a.blob,
        media_type=a.content_type,
        headers={
            "Content-Disposition": f'inline; filename="{a.filename}"',
            "Cache-Control": "no-store",
        },
    )


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
    """Marca resolvido (ou reabre). Ao resolver, `valor_recuperado` (lucro/prejuízo
    em R$) é OBRIGATÓRIO — Eduardo 15/09: "deixar como campo obrigatório antes de
    aceitar o resolver". `situacao` (nova situação do pedido no Bling, ex.
    Resolvido / Perdimento) é OBRIGATÓRIA quando o chamado tem `pedido_bling` —
    Vinicius 19/09: a janela mostra "status atual do Bling e o que vai trocar,
    obrigatório" (422 `chamado_situacao_obrigatoria`); chamado sem pedido no
    Bling não tem o que trocar, continua opcional. Reabrir não exige nada."""
    ch = await _get(session, chamado_id)
    if body.resolvido and body.valor_recuperado is None:
        raise HTTPException(422, detail={"code": "chamado_valor_obrigatorio"})
    if body.resolvido and (ch.pedido_bling or "").strip() and not body.situacao:
        raise HTTPException(422, detail={"code": "chamado_situacao_obrigatoria"})
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
    # 23/09: a observação da janela É a da coluna (vem preenchida com ela).
    if body.resolvido and "observacao" in body.model_fields_set:
        ch.observacao = body.observacao
    session.add(
        svc.marcar_resolvido(
            ch,
            body.resolvido,
            autor_nome=_autor(user),
            valor=body.valor_recuperado if body.resolvido else None,
            observacao=ch.observacao if body.resolvido else None,
        )
    )
    await session.commit()
    await session.refresh(ch)
    return await _one_out(session, ch)


# ------------------------------------------------------------- lixeira
# Vinicius, 21/09/2026 (caso 294263: a operadora lançou "Não recebido" nos 2
# itens e o pacote chegou): a lixeira do histórico apaga o chamado E os
# lançamentos de devolução do mesmo pedido que a pessoa escolher, com a nova
# situação do Bling obrigatória igual ao resolver. A escolha é POR LINHA porque
# num pedido com 2 linhas uma pode ter voltado pro estoque e a outra não — a
# que voltou é estornada no Bling ao excluir (services/devolution_delete, a
# mesma regra do DELETE /api/devolutions/{id}). O DELETE /{chamado_id} antigo
# continua: só o chamado.


async def _lancamentos_do_chamado(session: AsyncSession, ch: Chamado) -> list[Devolution]:
    """Linhas de devolução que a lixeira pode levar junto: TODAS as do pedido
    Bling do chamado (kit = várias linhas, 1 chamado); sem pedido, só a linha
    que abriu o chamado (`origem_ref`), se ainda existir."""
    numero = (ch.pedido_bling or "").strip()
    if numero:
        rows = await session.execute(
            select(Devolution)
            .where(func.trim(Devolution.pedido_bling) == numero)
            .order_by(Devolution.created_at, Devolution.id)
        )
        return list(rows.scalars().all())
    try:
        ref = UUID(ch.origem_ref or "")
    except ValueError:
        return []
    dev = (
        await session.execute(select(Devolution).where(Devolution.id == ref))
    ).scalar_one_or_none()
    return [dev] if dev is not None else []


def _estornavel(dev: Devolution) -> bool:
    return bool(dev.estoque_mov_bling_id) and dev.estoque_mov_revertido_at is None


@router.get("/{chamado_id}/exclusao", response_model=ExclusaoPreviewOut)
async def exclusao_preview(
    chamado_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("chamados", "delete"))],
) -> ExclusaoPreviewOut:
    """O que a janela da lixeira mostra antes de apagar: situação viva do pedido
    no Bling, se a nova situação é obrigatória, se a disputa já foi aberta na
    plataforma e os lançamentos de devolução do pedido (marcados por padrão os
    de motivo que abre chamado)."""
    ch = await _get(session, chamado_id)
    numero = (ch.pedido_bling or "").strip()
    status_map = await svc.status_bling_atual_map(session, {numero}) if numero else {}
    abertura_enviada = (
        await session.execute(
            select(ChamadoMensagem.id)
            .where(
                ChamadoMensagem.chamado_id == ch.id,
                ChamadoMensagem.tipo == "abertura",
                ChamadoMensagem.status == "enviada",
            )
            .limit(1)
        )
    ).scalar_one_or_none() is not None
    # 21/09: chamado de devolução SUBSTITUÍDO por outro mais novo do mesmo pedido
    # (troca de motivo) — as linhas agora alimentam o chamado novo; a lixeira
    # deste não as pré-marca, senão apagar o Encerrado levaria a linha viva junto.
    substituido = False
    if ch.origem == "devolucao" and (numero or (ch.origem_ref or "").strip()):
        mesmo_caso = (
            Chamado.pedido_bling == numero
            if numero
            else Chamado.origem_ref == (ch.origem_ref or "").strip()
        )
        substituido = (
            await session.execute(
                select(Chamado.id)
                .where(
                    Chamado.origem == "devolucao",
                    mesmo_caso,
                    Chamado.created_at > ch.created_at,
                )
                .limit(1)
            )
        ).scalar_one_or_none() is not None
    lancamentos = [
        ExclusaoLancamentoOut(
            id=dev.id,
            sku=dev.sku,
            produtos=dev.produtos,
            condicao_produto=dev.condicao_produto,
            motivo_devolucao=dev.motivo_devolucao,
            data_devolvido_estoque=dev.data_devolvido_estoque,
            estoque_estornavel=_estornavel(dev),
            estoque_mov_sku=dev.estoque_mov_sku,
            estoque_mov_qty=dev.estoque_mov_qty,
            marcado_padrao=svc.motivo_pede_chamado(dev) and not substituido,
        )
        for dev in await _lancamentos_do_chamado(session, ch)
    ]
    return ExclusaoPreviewOut(
        chamado_id=ch.id,
        pedido_bling=ch.pedido_bling,
        plataforma=ch.plataforma,
        status_bling_atual=status_map.get(numero) or ch.status_bling,
        exige_situacao=bool(numero),
        abertura_enviada=abertura_enviada,
        pode_excluir_lancamentos=_pode(user, "devolucoes", "delete"),
        lancamentos=lancamentos,
    )


@router.post("/{chamado_id}/excluir", response_model=ExcluirOut)
async def excluir_chamado(
    chamado_id: UUID,
    body: ExcluirIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("chamados", "delete"))],
) -> ExcluirOut:
    """Apaga o chamado e os lançamentos escolhidos. Ordem: valida tudo, troca a
    situação no Bling (se o Bling recusar, nada é apagado), exclui as linhas uma
    a uma com commit por linha (o estorno feito no Bling nunca fica sem a
    exclusão correspondente; se um estorno falhar, para ali e o chamado FICA),
    e só então apaga o chamado."""
    ch = await _get(session, chamado_id)
    if (ch.pedido_bling or "").strip() and not body.situacao:
        raise HTTPException(422, detail={"code": "chamado_situacao_obrigatoria"})
    if body.devolucoes and not _pode(user, "devolucoes", "delete"):
        raise HTTPException(403, detail={"code": "devolucoes_delete_forbidden"})
    por_id = {dev.id: dev for dev in await _lancamentos_do_chamado(session, ch)}
    escolhidas: list[Devolution] = []
    for did in dict.fromkeys(body.devolucoes):
        dev = por_id.get(did)
        if dev is None:
            raise HTTPException(422, detail={"code": "devolucao_fora_do_pedido"})
        escolhidas.append(dev)

    situacao_aplicada: str | None = None
    if body.situacao:
        try:
            res = await svc.aplicar_status_bling(session, ch, body.situacao)
        except svc.ChamadoError as e:
            raise HTTPException(422, detail={"code": e.code}) from e
        except Exception as e:  # noqa: BLE001
            raise HTTPException(
                502, detail={"code": "chamado_status_bling_erro", "erro": str(e)[:300]}
            ) from e
        ch.alterar_status_bling = body.situacao
        situacao_aplicada = res["situacao"]
        # Commit antes das exclusões: o Bling já mudou, então o snapshot e o
        # histórico ficam certos mesmo que um estorno falhe e o chamado sobreviva.
        await session.commit()

    excluidos = 0
    estornos: list[ExcluirEstornoOut] = []
    for dev in escolhidas:
        # Guardados antes: o rollback do estorno que falha expira o objeto.
        dev_id, sku, qty = dev.id, dev.estoque_mov_sku, dev.estoque_mov_qty
        try:
            r = await excluir_lancamento(session, dev)
        except EstornoFalhouError as e:
            logger.warning(
                "chamado_excluir_estorno_falhou",
                chamado_id=str(chamado_id),
                devolution_id=str(dev_id),
                excluidos=excluidos,
            )
            raise HTTPException(
                502,
                detail={
                    "code": "estoque_estorno_falhou",
                    "message": e.message,
                    "lancamentos_excluidos": excluidos,
                },
            ) from e
        excluidos += 1
        if r.get("estoque_estornado"):
            estornos.append(ExcluirEstornoOut(sku=sku, qty=qty, mensagem=r.get("mensagem")))

    pedido_bling = ch.pedido_bling
    await session.delete(ch)
    await session.commit()
    logger.info(
        "chamado_excluido",
        id=str(chamado_id),
        pedido_bling=pedido_bling,
        lancamentos_excluidos=excluidos,
        estornos=len(estornos),
        situacao=situacao_aplicada,
        autor=_autor(user),
    )
    return ExcluirOut(
        ok=True,
        lancamentos_excluidos=excluidos,
        estornos=estornos,
        situacao=situacao_aplicada,
    )


@router.post("/{chamado_id}/reler", response_model=ChamadoOut)
async def reler_na_plataforma(
    chamado_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("chamados", "edit"))],
) -> ChamadoOut:
    """Relê o caso na plataforma AGORA (o cron faz isso de hora em hora, no
    minuto :25). Vinicius 22/09: quando a TikTok está com um prazo correndo —
    "sem resposta, a plataforma aprova o reembolso sozinha" — esperar a próxima
    janela é caro. Traz status, arbitragem, o que o comprador escreveu e anexou
    e o que a plataforma espera de nós. Plataforma sem API responde 422.

    Caso aberto NA TELA (22/09, 292592) não tem API pra consultar: o protocolo é
    de tela e a Shopee responde "The return you queried doesn't exist". Aqui
    "Atualizar" passa a significar FURAR A FILA da leitura — o caso vai pro topo
    e o robô o lê no próximo poll, em vez de bater numa porta que não existe."""
    ch = await _get(session, chamado_id)
    if await chamados_leitura.e_caso_de_tela(session, ch):
        cid = ch.id  # antes do commit: ele expira a linha (ver `sync_um`)
        await chamados_leitura.furar_a_fila(session, ch)
        await session.refresh(ch)
        logger.info("chamado_reler_fila_leitura", chamado_id=str(cid), autor=_autor(user))
        return await _one_out(session, ch)
    r = await chamados_devolucao_sync.sync_um(session, ch)
    await session.refresh(ch)
    if (ch.consulta_portal or "").strip() or chamados_leitura.e_devolucao_shopee_da_api(ch):
        # 25/09 (294571/296012): o que só a TELA mostra (consulta do Portal, "Upload
        # Evidence" da 2ª disputa) não vem pela API — o Atualizar também põe o caso
        # na frente da fila do executor de leitura (lê em até 10 min).
        await chamados_leitura.furar_a_fila(session, ch)
        await session.refresh(ch)
    if not r.get("lido") and not (ch.consulta_portal or "").strip():
        raise HTTPException(
            422, detail={"code": r.get("erro") or "chamado_sem_api", "plataforma": r.get("plataforma")}
        )
    return await _one_out(session, ch)


@router.post("/{chamado_id}/instrucao", response_model=ChamadoOut)
async def instruir_robo(
    chamado_id: UUID,
    body: InstrucaoIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(require_permission("chamados", "edit"))],
) -> ChamadoOut:
    """19/09 (Vinicius): recado de uma pessoa PRO ROBÔ — "responde que o pacote foi
    entregue dia 12", "desiste desse". Entra no histórico como `instrucao`
    (direcao=sistema, não vai pra plataforma); a linha vira Análise Robô até o
    cérebro ler no `/agent/analisar` e responder (a análise dele consome).
    Vale em qualquer canal e em chamado Encerrado (é o jeito de o robô voltar num
    caso que a plataforma fechou). Em Concluído (resolvido por pessoa) NÃO: a
    pessoa reabre pela aba antes de instruir — 422 `chamado_concluido` (19/09)."""
    ch = await _get(session, chamado_id)
    if ch.resolvido:
        raise HTTPException(422, detail={"code": "chamado_concluido"})
    m = svc.nova_mensagem(
        ch,
        texto=body.texto,
        tipo="instrucao",
        direcao="sistema",
        autor_nome=_autor(user),
        autor_id=user.id,
        status="registrada",
    )
    session.add(m)
    chamados_leitura.ligar_consulta_do_texto(ch, body.texto)  # 25/09: link do Portal na instrução
    await session.commit()
    await session.refresh(ch)
    logger.info("chamado_instrucao", chamado_id=str(ch.id), autor=_autor(user))
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


@dataclass(frozen=True)
class _Cerebro:
    """Quem chama as rotas do cérebro. `row` = cérebro cadastrado com senha
    própria (Hermes, 24/09); `None` = o token antigo (NF), que é o cérebro do
    Eduardo. `substituido` = token antigo depois que um cadastrado virou
    `exclusivo` — ele para de ver e de decidir, sem erro do lado dele."""

    row: ChamadoCerebro | None
    substituido: bool = False

    @property
    def nome(self) -> str | None:
        return self.row.nome if self.row is not None else None


# last_used_at / legado_ignorado_at: grava no máximo 1×/min (o cérebro chama a cada rodada)
_CEREBRO_CARIMBO = timedelta(minutes=1)


async def _cerebro(
    session: Annotated[AsyncSession, Depends(get_session)],
    x_agent_token: Annotated[str | None, Header(alias="X-Agent-Token")] = None,
) -> _Cerebro:
    token = (x_agent_token or "").strip()
    if not token:
        raise HTTPException(401, detail={"code": "chamados_agent_unauthorized"})
    agora = datetime.now(UTC)
    legado = get_settings().nf_agent_token
    if legado and secrets.compare_digest(token, legado):
        dono = (
            await session.execute(
                select(ChamadoCerebro)
                .where(ChamadoCerebro.exclusivo.is_(True), ChamadoCerebro.revoked_at.is_(None))
                .limit(1)
            )
        ).scalar_one_or_none()
        if dono is not None and (
            dono.legado_ignorado_at is None or dono.legado_ignorado_at < agora - _CEREBRO_CARIMBO
        ):
            dono.legado_ignorado_at = agora
            await session.commit()
        return _Cerebro(row=None, substituido=dono is not None)
    row = (
        await session.execute(
            select(ChamadoCerebro).where(
                ChamadoCerebro.token_hash == hashlib.sha256(token.encode()).hexdigest(),
                ChamadoCerebro.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(401, detail={"code": "chamados_agent_unauthorized"})
    if row.last_used_at is None or row.last_used_at < agora - _CEREBRO_CARIMBO:
        row.last_used_at = agora
        await session.commit()
    return _Cerebro(row=row)


def _so_cadastrado(cerebro: _Cerebro) -> ChamadoCerebro:
    if cerebro.row is None:
        raise HTTPException(403, detail={"code": "cerebro_sem_cadastro"})
    return cerebro.row


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
            observacao=body.observacao,
            # Data do chamado = HOJE (quando o robô abriu). Antes ficava vazia e
            # `preencher_do_pedido` punha a data da VENDA (08/09: chamado novo da
            # Marquezini com data 10/08 afundava na lista, ordenada por data —
            # "só não gravou no davinci em chamados").
            data=datetime.now(svc.SAO_PAULO).date(),
        )
        await svc.preencher_do_pedido(session, ch)
        if ch.alterar_status_bling is None:
            ch.alterar_status_bling = svc.STATUS_ABERTURA_POR_ORIGEM.get(ch.origem)
        session.add(ch)
        await session.flush()
        session.add(
            svc.registrar_sistema(ch, f"Chamado registrado pelo {AUTOR_ROBO} (origem {ch.origem})")
        )
    if body.chamado:
        ch.chamado = body.chamado
        # Quem registra aqui é o robô, e ele só tem protocolo porque abriu na tela.
        ch.chamado_de_tela = True
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


@agent_router.post("/historico", response_model=AgentHistoricoOut, dependencies=_agent_dep)
async def agent_historico(
    body: AgentHistoricoIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AgentHistoricoOut:
    """Leitor da página do caso guarda a conversa COMPLETA (falas desde a abertura na
    plataforma) numa mensagem só por chamado, atualizada quando muda. É contexto pra
    quem analisa na aba — não é resposta nova: `direcao=sistema`, então o cérebro,
    a fila de envio e a deduplicação dos leitores não enxergam."""
    ch: Chamado | None = None
    if body.chamado_id:
        ch = await _get(session, body.chamado_id)
    elif body.chamado:
        q = select(Chamado).where(Chamado.chamado == body.chamado)
        if body.pedido_bling:
            q = q.where(Chamado.pedido_bling == body.pedido_bling)
        ch = (
            await session.execute(q.order_by(Chamado.created_at.desc()).limit(1))
        ).scalar_one_or_none()
    if ch is None:
        raise HTTPException(404, detail={"code": "chamado_not_found"})
    texto = limpar_html(body.texto.strip())
    m = (
        await session.execute(
            select(ChamadoMensagem)
            .where(ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.tipo == TIPO_HISTORICO)
            .order_by(ChamadoMensagem.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()
    if m is not None:
        if (m.texto or "").strip() == texto:
            return AgentHistoricoOut(chamado_id=ch.id, mensagem_id=m.id, alterado=False)
        m.texto = texto
    else:
        m = svc.nova_mensagem(
            ch, texto=texto, tipo=TIPO_HISTORICO, direcao="sistema",
            autor_nome="página do caso", status="registrada",
        )
        session.add(m)
    await session.commit()
    await session.refresh(m)
    return AgentHistoricoOut(chamado_id=ch.id, mensagem_id=m.id, alterado=True)


@agent_router.post("/pagamento-ml", response_model=AgentPagamentoMlOut)
async def agent_pagamento_ml(
    body: AgentPagamentoMlIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    _quem: Annotated[_Cerebro, Depends(_cerebro)],
) -> AgentPagamentoMlOut:
    """15/09 (Eduardo: "sim dá para fazer"): pagamento da venda de cada pedido na API
    do ML — aprovação, liberação pra loja, estorno (e QUEM pagou o estorno) e envio.
    O agente usa pra responder com fato e pra encerrar sem prejuízo quando o ML
    cobriu o comprador e o valor ficou com a loja (482061374 / 285250)."""
    from app.services.chamados_pagamento_ml import pagamento_da_venda

    clientes: dict = {}
    itens: list[AgentPagamentoMlItem] = []
    for pedido in dict.fromkeys(p.strip() for p in body.pedidos_bling if p and p.strip()):
        try:
            itens.append(AgentPagamentoMlItem(**(await pagamento_da_venda(session, pedido, clientes))))
        except Exception as exc:  # noqa: BLE001 — um pedido com erro não derruba o lote
            itens.append(AgentPagamentoMlItem(pedido_bling=pedido, ok=False, erro=f"{type(exc).__name__}: {str(exc)[:160]}"))
    await session.commit()  # tokens do ML renovados durante as consultas
    return AgentPagamentoMlOut(pedidos=itens)


@agent_router.post("/shopee-prova")
async def agent_shopee_prova(
    body: AgentShopeeProvaIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    quem: Annotated[_Cerebro, Depends(_cerebro)],
) -> dict:
    """25/09 (296012): a Shopee reabriu a disputa pelo chat e pede evidência
    ("Upload Evidence"), mas o sync só manda prova com `seller_proof` PENDING.
    `consultar` mostra o que a API diz da disputa/prova; `enviar` manda as fotos
    da devolução + o texto pelo upload_proof e registra no histórico."""
    from app.services import chamados_devolucao_sync as sync

    ia = _so_cadastrado(quem)
    ch = await session.get(Chamado, body.chamado_id)
    if ch is None:
        raise HTTPException(404, detail={"code": "chamado_nao_encontrado"})
    if sync.cd.plataforma_de(ch.plataforma) != sync.cd.PLAT_SHOPEE:
        raise HTTPException(409, detail={"code": "chamado_nao_e_shopee"})
    try:
        out = await sync.prova_shopee_agente(
            session, ch, enviar=body.acao == "enviar", texto=body.texto, autor=ia.nome
        )
    except svc.ChamadoError as e:
        raise HTTPException(409, detail={"code": str(e)}) from e
    await session.commit()
    return out


@agent_router.post("/lease", response_model=AgentLeaseOut, dependencies=_agent_dep)
async def agent_lease(
    body: AgentLeaseIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AgentLeaseOut:
    """Entrega ao robô as réplicas pendentes de canal robô (manuais e
    automáticas) e marca-as `enviando`. Tarefa presa em `enviando` há mais de
    30 min volta pra fila. Chamado resolvido não gera tarefa."""
    limite_stale = datetime.now(UTC) - _LEASE_STALE
    sem_protocolo = or_(Chamado.chamado.is_(None), func.trim(Chamado.chamado) == "")
    conds = [
        ChamadoMensagem.canal == "robo",
        ChamadoMensagem.direcao == "enviada",
        Chamado.resolvido.is_(False),
        or_(
            ChamadoMensagem.status == "pendente",
            (ChamadoMensagem.status == "enviando") & (ChamadoMensagem.updated_at < limite_stale),
        ),
    ]
    # 21/09: ABERTURA de chamado já Encerrado (ex. motivo trocado e outro chamado
    # aberto no lugar) não volta pro robô — nem presa em `enviando`. Réplica e
    # instrução em Encerrado continuam saindo (é o jeito de o robô voltar num caso).
    conds.append(or_(ChamadoMensagem.tipo != "abertura", svc.NAO_ENCERRADO_SQL))
    if body.tipo == "abrir":
        conds.append(sem_protocolo)
    elif body.tipo == "responder":
        conds.append(~sem_protocolo)
    # Plataforma: TikTok/Shopee (regra da aba Status → robô abre no Seller
    # Center) só saem pra quem pede por elas; o consumidor padrão (robô do
    # formulário do ML) nunca as recebe — senão tentaria abrir no lugar errado.
    plat_col = func.lower(func.trim(func.coalesce(Chamado.plataforma, "")))
    plat = (body.plataforma or "").strip().lower()
    if plat == "ml":
        conds.append(plat_col.in_(_PLATAFORMA_ML))
    elif plat:
        aceitas = _PLATAFORMAS_SELLER_CENTER.get(plat, (plat,))
        conds.append(plat_col.in_(aceitas))
    else:
        conds.append(plat_col.not_in(_PLATAFORMAS_SO_COM_PEDIDO))
    rows = (
        await session.execute(
            select(ChamadoMensagem, Chamado)
            .join(Chamado, Chamado.id == ChamadoMensagem.chamado_id)
            .options(selectinload(ChamadoMensagem.anexos))
            .where(*conds)
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
                    texto=limpar_html(m.texto),
                    anexos=anexos,
                )
            )
        await session.commit()
        logger.info("chamado_agent_lease", tarefas=len(ids))
    return AgentLeaseOut(tarefas=tarefas)


@agent_router.post("/leitura", response_model=AgentLeituraOut, dependencies=_agent_dep)
async def agent_leitura(
    body: AgentLeituraIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AgentLeituraOut:
    """Casos que o robô deve RELER na tela da plataforma (Vinicius 22/09, 292592).

    Fila separada do `/lease` de propósito: no lease vale o invariante "tudo que
    ele te dá, você POSTA" (toda tarefa carrega uma fala nossa). Leitura não posta
    nada, e misturar as duas obrigaria a afrouxar `mensagem_id`/`texto` — um robô
    que implementasse errado escreveria um texto vazio na conversa com o cliente.
    Aqui não existe `texto` pra postar por engano.

    O caso sai da fila por 3 h (24 h se está frio) assim que é entregue; robô que
    morre no meio não trava nada — o claim vence em 30 min. Depois de ler, o robô
    é OBRIGADO a chamar `/leitura/resultado`, mesmo sem novidade."""
    casos = await chamados_leitura.fila(
        session,
        plataformas=body.plataformas,
        limite=body.limite,
        conta=body.conta,
    )
    return AgentLeituraOut(
        casos=[
            AgentCasoLeituraOut(
                chamado_id=c.id,
                chamado=(c.chamado or "").strip(),
                chamado_url=(c.chamado_url or "").strip() or None,
                pedido_bling=c.pedido_bling,
                pedido_marketplace=c.pedido_marketplace,
                conta=c.conta,
                plataforma=c.plataforma,
                leitura_robo_at=c.leitura_robo_at,
            )
            for c in casos
        ]
    )


@agent_router.post(
    "/leitura/resultado", response_model=AgentLeituraResultadoOut, dependencies=_agent_dep
)
async def agent_leitura_resultado(
    body: AgentLeituraResultadoIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AgentLeituraResultadoOut:
    """O que o robô leu na página do caso.

    `falas[]` CONTA como resposta da plataforma (mexe na coluna "Últ. resposta" e
    no Status da aba, com a hora que a TELA mostra); `historico` é só contexto.
    Duas travas no servidor deixam a regra do robô ser "na dúvida, mande": fala
    igual a uma NOSSA é descartada como eco, e fala repetida não entra de novo.
    `ok: false` não grava nada e abre ocorrência na Ouvidoria — leitura que parou
    de funcionar tem que ser vista."""
    ch = await _get(session, body.chamado_id)
    r = await chamados_leitura.registrar(
        session,
        ch,
        ok=body.ok,
        erro=body.erro,
        falas=[
            chamados_leitura.FalaLida(texto=f.texto, quando=f.quando, autor=f.autor)
            for f in body.falas
        ],
        historico=body.historico,
        encerrado=body.encerrado,
    )
    return AgentLeituraResultadoOut(
        chamado_id=ch.id,
        falas_novas=r.falas_novas,
        ecos=r.ecos,
        duplicadas=r.duplicadas,
        historico_alterado=r.historico_alterado,
        encerrado=r.encerrado,
        proxima_leitura_at=await chamados_leitura.proxima_leitura(session, ch, ok=body.ok),
    )


# ---- executor de leitura de chamado (24/09, 296012) --------------------------
# A recusa escrita da Shopee só existe no Seller Center ("Histórico da
# Solicitação"); a API diz só "aguardando análise". Um executor no Mac Santiago
# abre a devolução pelo AdsPower e devolve o que leu. Senha PRÓPRIA
# (`chamados_leitores`): com o NF_AGENT_TOKEN ele poderia postar na conversa com
# o cliente; com esta, só pede a lista e devolve a leitura.


async def _leitor(
    session: Annotated[AsyncSession, Depends(get_session)],
    x_agent_token: Annotated[str | None, Header(alias="X-Agent-Token")] = None,
) -> ChamadoLeitor:
    token = (x_agent_token or "").strip()
    row = None
    if token:
        row = (
            await session.execute(
                select(ChamadoLeitor).where(
                    ChamadoLeitor.token_hash == hashlib.sha256(token.encode()).hexdigest(),
                    ChamadoLeitor.revoked_at.is_(None),
                )
            )
        ).scalar_one_or_none()
    if row is None:
        raise HTTPException(401, detail={"code": "chamados_leitor_unauthorized"})
    agora = datetime.now(UTC)
    if row.last_used_at is None or row.last_used_at < agora - _CEREBRO_CARIMBO:
        row.last_used_at = agora
        await session.commit()
    return row


@agent_router.post("/leitor/fila", response_model=AgentLeituraOut)
async def agent_leitor_fila(
    body: AgentLeitorFilaIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    _quem: Annotated[ChamadoLeitor, Depends(_leitor)],
) -> AgentLeituraOut:
    """Devoluções da Shopee contestadas pela API pra reler na tela. `chamado` = nº
    da solicitação; o robô busca pelo `pedido_marketplace`. Mesma cadência e claim
    do `/agent/leitura` (3 h / 24 h frio / claim 30 min); `espiar` não marca."""
    casos = await chamados_leitura.fila_devolucao_shopee(
        session,
        limite=body.limite,
        contas=body.contas,
        espiar=body.espiar,
        portal=body.portal,
        consultas=body.consultas,
    )
    out: list[AgentCasoLeituraOut] = []
    for c in casos:
        de_tela = chamados_leitura.e_portal_shopee(c)
        if body.consultas:
            tipo = chamados_leitura.tipo_de_leitura(c)
        else:  # executor que só conhece devolução/portal-de-tela (v1.1)
            tipo = "portal" if de_tela else "devolucao"
        consulta = chamados_leitura.consulta_do_portal(c) if tipo != "devolucao" else None
        out.append(
            AgentCasoLeituraOut(
                chamado_id=c.id,
                chamado=(c.chamado or "").strip(),
                chamado_url=(
                    chamados_leitura.url_do_portal(c)
                    if de_tela
                    else (c.chamado_url or "").strip() or None
                ),
                pedido_bling=c.pedido_bling,
                pedido_marketplace=(c.pedido_marketplace or "").strip() or None,
                conta=c.conta,
                plataforma=c.plataforma,
                leitura_robo_at=c.leitura_robo_at,
                tipo=tipo,
                consulta_portal=consulta,
                consulta_url=chamados_leitura.url_do_portal(c) if consulta else None,
            )
        )
    return AgentLeituraOut(casos=out)


@agent_router.post("/leitor/resultado", response_model=AgentLeituraResultadoOut)
async def agent_leitor_resultado(
    body: AgentLeituraResultadoIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    _quem: Annotated[ChamadoLeitor, Depends(_leitor)],
) -> AgentLeituraResultadoOut:
    """O que o executor leu — mesmas regras do `/agent/leitura/resultado` (falas
    com a hora da tela, eco e repetida descartados, `ok: false` vira ocorrência).
    Só aceita chamado do tipo que a fila dele entrega."""
    ch = await _get(session, body.chamado_id)
    if not (
        chamados_leitura.e_devolucao_shopee_da_api(ch)
        or chamados_leitura.consulta_do_portal(ch) is not None
    ):
        raise HTTPException(409, detail={"code": "chamado_fora_do_leitor"})
    r = await chamados_leitura.registrar(
        session,
        ch,
        ok=body.ok,
        erro=body.erro,
        falas=[
            chamados_leitura.FalaLida(texto=f.texto, quando=f.quando, autor=f.autor)
            for f in body.falas
        ],
        historico=body.historico,
        encerrado=body.encerrado,
        pendencias=body.pendencias,
    )
    return AgentLeituraResultadoOut(
        chamado_id=ch.id,
        falas_novas=r.falas_novas,
        ecos=r.ecos,
        duplicadas=r.duplicadas,
        historico_alterado=r.historico_alterado,
        encerrado=r.encerrado,
        pendencias_novas=r.pendencias_novas,
        proxima_leitura_at=await chamados_leitura.proxima_leitura(session, ch, ok=body.ok),
    )


@agent_router.post("/resultado", response_model=ChamadoMensagemOut, dependencies=_agent_dep)
async def agent_resultado(
    body: AgentResultadoIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ChamadoMensagemOut:
    """Robô devolve o resultado de uma tarefa: enviada (+ protocolo/URL quando
    foi abertura) ou falhou (+ erro). 19/09 (Vinicius: "envio falhou → fila do
    robô; se não conseguir, humano"): falha que não pede gente volta a mensagem
    pra `pendente` (o lease reentrega) até `MAX_TENTATIVAS_ROBO`; na última, ou
    quando o erro pede humano (foto, quebra-cabeça, login), fica `falhou` e a
    linha vira Análise Humano."""
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
    encerrado = ch.resolvido or ch.status_plataforma in svc.STATUS_FINAIS
    if body.ok:
        m.status = "enviada"
        m.enviada_at = datetime.now(UTC)
        m.erro = None
        if m.tipo == "abertura" and encerrado:
            # 21/09: o robô abriu DEPOIS de o chamado ficar Encerrado (motivo
            # trocado/retirado enquanto a tarefa estava com ele): a marca faz a
            # tela Devoluções mostrar "retire a contestação no painel".
            m.erro = chamados_devolucao.ERRO_RETIRAR_CONTESTACAO
            session.add(
                svc.registrar_sistema(
                    ch,
                    "Atenção: o robô abriu na tela da plataforma"
                    f"{' (protocolo ' + body.chamado + ')' if body.chamado else ''} depois de o "
                    "chamado ficar Encerrado — desista dela no painel da plataforma; o DaVinci "
                    "não tem como cancelar pela API.",
                )
            )
        if body.chamado and not (ch.chamado or "").strip():
            ch.chamado = body.chamado
            ch.chamado_url = body.chamado_url or ch.chamado_url
            # 22/09: este número veio DA TELA. É o que tira o caso da varredura por
            # API (que não conhece protocolo de tela) e o põe na fila de leitura do
            # robô — ver `chamados.CASO_DE_TELA_SQL`.
            ch.chamado_de_tela = True
            session.add(
                svc.registrar_sistema(ch, f"Protocolo {body.chamado} capturado pelo {AUTOR_ROBO}")
            )
            # Chamado que a Logística encaminhou ao robô do formulário: o
            # protocolo volta pra linha da Logística (deixa de ser pendência lá).
            if ch.origem == "logistica" and ch.origem_ref:
                try:
                    lrow = await session.get(Logistica, UUID(str(ch.origem_ref)))
                except ValueError:
                    lrow = None
                if lrow is not None and not (lrow.chamado or "").strip():
                    lrow.chamado = body.chamado
                    lrow.chamado_auto_at = datetime.now(UTC)
                    lrow.chamado_auto_erro = None
        elif body.chamado_url and not ch.chamado_url:
            ch.chamado_url = body.chamado_url
    else:
        erro = (body.erro or "falha no robô")[:300]
        tentativas = (m.tentativas or 0) + 1
        if m.tipo == "abertura" and encerrado:
            # 21/09: o chamado foi Encerrado enquanto o robô estava com a tarefa
            # (motivo trocado, outro chamado no lugar): a abertura antiga não
            # volta pra fila.
            m.tentativas = tentativas
            m.status = "registrada"
            m.erro = chamados_devolucao.ERRO_CONTESTACAO_CANCELADA
            session.add(
                svc.registrar_sistema(
                    ch, f"Tarefa do robô falhou ({erro}) e o chamado já está Encerrado — cancelada."
                )
            )
        elif not svc._erro_pede_humano(erro) and tentativas < svc.MAX_TENTATIVAS_ROBO:
            m.tentativas = tentativas
            m.erro = erro
            m.status = "pendente"
            session.add(
                svc.registrar_sistema(
                    ch,
                    f"Envio falhou (tentativa {tentativas} de {svc.MAX_TENTATIVAS_ROBO}): "
                    f"{erro} — volta pra fila do robô",
                )
            )
        else:
            m.tentativas = tentativas
            m.status = "falhou"
            m.erro = erro
    await session.commit()
    await session.refresh(m)
    logger.info(
        "chamado_agent_resultado", mensagem_id=str(m.id), status=m.status, tentativas=m.tentativas
    )
    return _mensagem_out(m)


# Estado Encerrado porque o robô viu o caso fechado na plataforma. Mora em
# `services.chamados` desde 22/09: o monitor (`/agent/recebida`) e a leitura da
# tela (`/agent/leitura/resultado`) precisam exatamente do mesmo comportamento.
_monitor_encerrou = svc.encerrado_pelo_robo


@agent_router.post("/recebida", response_model=AgentRecebidaOut, dependencies=_agent_dep)
async def agent_recebida(
    body: AgentRecebidaIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AgentRecebidaOut:
    """Monitor leu uma resposta da plataforma: entra no histórico como
    `recebida` (autor monitor). Com `resolvido=true` o chamado vai pro estado
    Encerrado (status oficial `encerrado` + evento) — 19/09 (Vinicius): não
    fecha; uma pessoa conclui pela aba. `resolvido` na resposta continua
    refletindo `ch.resolvido`."""
    ch: Chamado | None = None
    if body.chamado_id:
        ch = await _get(session, body.chamado_id)
    elif body.chamado:
        # O monitor do Tuta só conhece o PROTOCOLO (assunto "Serviço ao Cliente
        # [Case: 479770243]"); o pedido é opcional e só desempata.
        q = select(Chamado).where(Chamado.chamado == body.chamado)
        if body.pedido_bling:
            q = q.where(Chamado.pedido_bling == body.pedido_bling)
        ch = (
            await session.execute(q.order_by(Chamado.created_at.desc()).limit(1))
        ).scalar_one_or_none()
    if ch is None:
        raise HTTPException(404, detail={"code": "chamado_not_found"})
    texto = body.texto.strip()
    if body.resumo:
        texto = f"{body.resumo.strip()}\n\n{texto}"
    bruto = texto
    texto = limpar_html(texto)
    # Idempotente: o monitor relê a caixa a cada rodada — a mesma resposta do
    # ML (mesmo texto) não entra duas vezes no histórico.
    dup = (
        await session.execute(
            select(ChamadoMensagem)
            .where(
                ChamadoMensagem.chamado_id == ch.id,
                ChamadoMensagem.direcao == "recebida",
                ChamadoMensagem.texto.in_({texto, bruto}),
            )
            .order_by(ChamadoMensagem.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if dup is not None:
        if body.resolvido and _monitor_encerrou(session, ch):
            await session.commit()
        return AgentRecebidaOut(chamado_id=ch.id, mensagem_id=dup.id, resolvido=ch.resolvido)
    m = svc.nova_mensagem(
        ch,
        texto=texto,
        tipo="resposta",
        direcao="recebida",
        autor_nome=AUTOR_MONITOR,
        status="registrada",
    )
    m.canal = "robo"
    # 22/09: a hora que a PLATAFORMA mostra, quando o monitor souber informar —
    # sem ela a fala entra com a hora do POST e a coluna "Últ. resposta" mente.
    # Data no futuro (relógio torto, parse errado) fica com a hora de agora.
    if body.quando is not None:
        quando = body.quando
        if quando.tzinfo is None:
            quando = quando.replace(tzinfo=svc.SAO_PAULO)
        if quando <= datetime.now(UTC) + chamados_leitura.FUTURO_TOLERADO:
            m.created_at = quando
            m.enviada_at = quando
    session.add(m)
    if body.resolvido:
        _monitor_encerrou(session, ch)
    await session.commit()
    await session.refresh(m)
    return AgentRecebidaOut(chamado_id=ch.id, mensagem_id=m.id, resolvido=ch.resolvido)


# ---- cérebro dos chamados (robô do Tuta, 08/09) -----------------------------
AUTOR_CEREBRO = "cérebro"
# 17/09 (Eduardo, "Plataforma respondeu" e ninguém respondeu): o cérebro reabriu às 09:00
# os chamados que o acompanhamento fechou às 08:25 com a decisão da plataforma (290985 e
# 289899 GANHOS, 290920) — leu "Shopee PAGOU a compensação" como resposta nova e mandou
# pra humano. Reabrir sozinho só vale pro que o monitor antigo fechou cedo demais (o
# motivo original) ou o próprio cérebro fechou; decisão da plataforma e pessoa, não.
# 19/09: monitor e cérebro não fecham mais nada (viram estado Encerrado) — isto
# só vale pros chamados que eles fecharam ANTES de 19/09. Chamado fechado por
# pessoa volta a abrir apenas com instrução (ver `agent_analise`).
_FECHOU_REABRIVEL = (
    f"Chamado marcado como resolvido por {AUTOR_MONITOR}%",
    f"Chamado marcado como resolvido por {AUTOR_CEREBRO}%",
)


def _ultimo_fechamento():
    """Texto do último evento "Chamado marcado como resolvido…" do chamado (correlacionado)."""
    return (
        select(ChamadoMensagem.texto)
        .where(
            ChamadoMensagem.chamado_id == Chamado.id,
            ChamadoMensagem.tipo == "sistema",
            ChamadoMensagem.texto.like("Chamado marcado como resolvido%"),
        )
        .order_by(ChamadoMensagem.created_at.desc())
        .limit(1)
        .correlate(Chamado)
        .scalar_subquery()
    )


async def _cerebro_pode_reabrir(session: AsyncSession, ch: Chamado) -> bool:
    ult = (
        await session.execute(
            select(ChamadoMensagem.texto)
            .where(
                ChamadoMensagem.chamado_id == ch.id,
                ChamadoMensagem.tipo == "sistema",
                ChamadoMensagem.texto.like("Chamado marcado como resolvido%"),
            )
            .order_by(ChamadoMensagem.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if ult is None:
        return True
    return any(ult.startswith(p.rstrip("%")) for p in _FECHOU_REABRIVEL)
_ACAO_TXT = {
    "esperar": "aguardar a plataforma",
    "responder": "réplica enfileirada pro robô",
    "resolver": "robô sugere fechar",
    "humano": "precisa de humano",
}
_PLATAFORMA_ML = ("ml", "mercado livre", "mercadolivre", "meli")
# Plataformas sem API de reclamação: o chamado da aba Status vai pro robô abrir
# no Seller Center, e só o robô que PEDE por elas no lease as recebe.
_PLATAFORMAS_SELLER_CENTER: dict[str, tuple[str, ...]] = {
    "tiktok": ("tiktok", "tik tok", "tiktok shop"),
    "shopee": ("shopee",),
}
_PLATAFORMAS_SO_COM_PEDIDO: tuple[str, ...] = tuple(
    p for aceitas in _PLATAFORMAS_SELLER_CENTER.values() for p in aceitas
)


async def _anexos_da_abertura(session: AsyncSession, ch: Chamado) -> list[ChamadoAnexo]:
    abertura_ids = (
        await session.execute(
            select(ChamadoMensagem.id).where(
                ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.tipo == "abertura"
            )
        )
    ).scalars().all()
    cond = ChamadoAnexo.mensagem_id.is_(None)
    if abertura_ids:
        cond = or_(cond, ChamadoAnexo.mensagem_id.in_(abertura_ids))
    return list(
        (
            await session.execute(
                select(ChamadoAnexo)
                .where(ChamadoAnexo.chamado_id == ch.id, cond)
                .order_by(ChamadoAnexo.created_at)
            )
        ).scalars().all()
    )


async def _plataforma_falou_depois(session: AsyncSession, ch: Chamado) -> bool:
    """A plataforma mandou mensagem DEPOIS de o chamado entrar no status atual?"""
    ult = (
        await session.execute(
            select(func.max(ChamadoMensagem.created_at)).where(
                ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.direcao == "recebida"
            )
        )
    ).scalar_one_or_none()
    return ult is not None and ult > ch.status_plataforma_at


def _bloqueio_de(
    msgs: list[ChamadoMensagem],
) -> tuple[ChamadoMensagem | None, AgentBloqueioOut | None]:
    """19/09: a última fala NOSSA está presa na API (pendente/enviando com erro em
    ERROS_ESPERA_PLATAFORMA)? Devolve (a mensagem, o bloqueio pro robô)."""
    nossas = [m for m in msgs if m.direcao == "enviada"]
    if not nossas:
        return None, None
    m = nossas[-1]
    erro = (m.erro or "").strip()
    if m.status in ("pendente", "enviando") and erro in svc.ERROS_ESPERA_PLATAFORMA:
        return m, AgentBloqueioOut(
            erro=erro, motivo=svc.MOTIVO_DO_ERRO.get(erro), desde=m.enviada_at or m.created_at
        )
    return None, None


def _ultima_fala_nossa(ch: Chamado):
    return (
        select(ChamadoMensagem)
        .where(ChamadoMensagem.chamado_id == ch.id, ChamadoMensagem.direcao == "enviada")
        .order_by(ChamadoMensagem.created_at.desc(), ChamadoMensagem.id.desc())
        .limit(1)
    )


def _trabalho_do_cerebro(plataforma: str | None, canais: list[str]):
    """Os chamados com trabalho pro cérebro (a regra está no `agent_analisar`) —
    a mesma consulta serve a lista da IA e o "esperando" da aba IA de Chamado."""

    def _ult(cond):
        return (
            select(
                ChamadoMensagem.chamado_id, func.max(ChamadoMensagem.created_at).label("ult")
            )
            .where(cond)
            .group_by(ChamadoMensagem.chamado_id)
            .subquery()
        )

    rec = _ult(ChamadoMensagem.direcao == "recebida")
    ana = _ult(ChamadoMensagem.tipo == "analise")
    ins = _ult(ChamadoMensagem.tipo == "instrucao")
    env = _ult(ChamadoMensagem.direcao == "enviada")
    blq = _ult(
        (ChamadoMensagem.direcao == "enviada")
        & ChamadoMensagem.status.in_(("pendente", "enviando"))
        & ChamadoMensagem.erro.in_(sorted(svc.ERROS_ESPERA_PLATAFORMA))
    )
    fechou = _ultimo_fechamento()
    resposta_nova = rec.c.ult.is_not(None) & or_(ana.c.ult.is_(None), rec.c.ult > ana.c.ult)
    bloqueio = (
        blq.c.ult.is_not(None)
        & (blq.c.ult == env.c.ult)
        & or_(ana.c.ult.is_(None), ana.c.ult < blq.c.ult)
    )
    # Instrução pendente: qualquer canal/plataforma, inclusive Encerrado — mas
    # não Concluído: a pessoa fechou, o robô não tem o que fazer (e a réplica
    # ficaria presa, o lease só entrega de chamado aberto). Quem quiser o robô
    # num Concluído reabre pela aba; a instrução volta a valer sozinha.
    instrucao = (
        ins.c.ult.is_not(None)
        & or_(ana.c.ult.is_(None), ins.c.ult > ana.c.ult)
        & Chamado.resolvido.is_(False)
    )
    encerrado = func.coalesce(Chamado.status_plataforma, "").in_(
        sorted(svc.STATUS_FINAIS)
    ) & Chamado.resolvido.is_(False)
    # resolvido pela plataforma/pessoa não volta pro cérebro (17/09)
    nao_fechado_por_gente = or_(
        Chamado.resolvido.is_(False),
        fechou.is_(None),
        *[fechou.like(p) for p in _FECHOU_REABRIVEL],
    )
    # 25/09 (296550): Encerrado não cega a IA quando a plataforma fala DEPOIS do
    # encerramento — o `resolver` dela é só sugestão e o caso pode seguir vivo.
    falou_depois = rec.c.ult > Chamado.status_plataforma_at
    ramo_resposta = (
        Chamado.canal.in_(canais)
        & or_(~encerrado, falou_depois)
        & nao_fechado_por_gente
        & resposta_nova
    )
    if plataforma:
        plat = plataforma.strip().lower()
        aceitas = _PLATAFORMA_ML if plat == "ml" else (plat,)
        ramo_resposta = ramo_resposta & func.lower(
            func.coalesce(Chamado.plataforma, "")
        ).in_(aceitas)
    # bloqueio: a fala presa é nossa e o caso está vivo — qualquer canal/plataforma
    ramo_bloqueio = bloqueio & ~encerrado & Chamado.resolvido.is_(False)
    return (
        select(Chamado)
        .outerjoin(rec, rec.c.chamado_id == Chamado.id)
        .outerjoin(ana, ana.c.chamado_id == Chamado.id)
        .outerjoin(ins, ins.c.chamado_id == Chamado.id)
        .outerjoin(env, env.c.chamado_id == Chamado.id)
        .outerjoin(blq, blq.c.chamado_id == Chamado.id)
        .where(or_(instrucao, ramo_bloqueio, ramo_resposta))
        # 24/09 (Vinicius: "quando eu apertar instrução, ela consegue cortar fila?"):
        # instrução de pessoa passa na frente de tudo; o resto, mais antigo primeiro.
        .order_by(
            case((instrucao, 0), else_=1),
            func.greatest(rec.c.ult, ins.c.ult, blq.c.ult),
            Chamado.created_at,
        )
    )


@agent_router.post("/analisar", response_model=AgentAnalisarOut)
async def agent_analisar(
    body: AgentAnalisarIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    cerebro: Annotated[_Cerebro, Depends(_cerebro)],
) -> AgentAnalisarOut:
    """Chamados com trabalho pro cérebro:
    - resposta da plataforma ainda não analisada (nenhuma `analise`, ou a última
      é mais velha que a última `recebida`) — inclui chamados que o monitor
      antigo fechou na primeira resposta (479765445, 08/09);
    - 19/09 `bloqueio`: a última fala nossa está presa na API (plataforma não
      libera) e nenhuma análise veio depois dela — Vinicius: o robô procura
      outro caminho (`responder` vira tarefa no Seller Center);
    - 19/09 `instrucao`: recado de pessoa mais novo que a última análise —
      qualquer canal, mesmo Encerrado (em Concluído a aba nem aceita instrução);
      a análise consome.
    Fora isso, Encerrado (status final sem pessoa fechar) e resolvido pela
    plataforma/pessoa não voltam pro cérebro (17/09).

    Os filtros `canais` e `plataforma` valem SÓ pro ramo "resposta nova" (o robô
    do Tuta pede canal robô/ML). Instrução e bloqueio saem pra quem chamar, em
    qualquer canal e plataforma (19/09): a pessoa mandou, ou a API travou — se o
    robô não atende aquela plataforma ele responde `humano` com o motivo, mas
    tem que VER o chamado.

    24/09: com um cérebro cadastrado `exclusivo` (Hermes), o token antigo recebe
    lista vazia — o cérebro do Eduardo para de ver sem quebrar nada do lado dele."""
    if cerebro.substituido:
        logger.info("chamados_cerebro_legado_ignorado", rota="analisar")
        return AgentAnalisarOut(chamados=[])
    if cerebro.row is not None and not cerebro.row.ligada:
        return AgentAnalisarOut(chamados=[])

    rows = (
        await session.execute(_trabalho_do_cerebro(body.plataforma, body.canais).limit(body.limite))
    ).scalars().all()
    return AgentAnalisarOut(chamados=[await _item_do_cerebro(session, ch) for ch in rows])


async def _item_do_cerebro(session: AsyncSession, ch: Chamado) -> AgentChamadoAnaliseOut:
    """O chamado como o cérebro enxerga: a conversa do CASO inteiro (linhas irmãs
    da mesma consulta), instrução/bloqueio pendentes e os prints da abertura."""
    msgs = await _mensagens_do_caso(session, ch)
    anexos = await _anexos_da_abertura(session, ch)
    pend = _instrucao_pendente(msgs)
    _m, bloq = _bloqueio_de(msgs)
    return AgentChamadoAnaliseOut(
        chamado_id=ch.id,
        chamado=ch.chamado,
        chamado_url=ch.chamado_url,
        pedido_bling=ch.pedido_bling,
        pedido_marketplace=ch.pedido_marketplace,
        conta=ch.conta,
        plataforma=ch.plataforma,
        canal=ch.canal,
        origem=ch.origem,
        resolvido=ch.resolvido,
        valor_recuperado=ch.valor_recuperado,
        valor_sugerido=ch.valor_sugerido,
        status_plataforma=ch.status_plataforma,
        observacao=ch.observacao,
        created_at=ch.created_at,
        mensagens=[
            AgentMensagemOut(
                id=m.id,
                direcao=m.direcao,
                tipo=m.tipo,
                status=m.status,
                autor_nome=m.autor_nome,
                created_at=m.created_at,
                texto=limpar_html(m.texto),
            )
            for m in msgs
        ],
        anexos_abertura=[a.id for a in anexos],
        replicas_robo=sum(
            1 for m in msgs if m.direcao == "enviada" and m.autor_nome == AUTOR_CEREBRO
        ),
        analises=sum(1 for m in msgs if m.tipo == "analise"),
        instrucao=(
            AgentInstrucaoOut(
                texto=pend.texto, autor=pend.autor_nome, quando=pend.created_at
            )
            if pend
            else None
        ),
        bloqueio=bloq,
    )


@agent_router.post("/analise", response_model=AgentAnaliseOut)
async def agent_analise(
    body: AgentAnaliseIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    cerebro: Annotated[_Cerebro, Depends(_cerebro)],
) -> AgentAnaliseOut:
    """Decisão do cérebro sobre a última resposta (ou instrução/bloqueio): grava
    a `analise` no histórico (é o marcador de "já vi" — consome a instrução) e
    executa a ação. `responder` reabre o chamado se preciso (o lease só entrega
    réplica de chamado aberto) e enfileira a réplica (canal robô, pendente;
    `abertura` se o chamado ainda não tem protocolo) — com os prints da
    abertura copiados quando `reanexar_abertura`. 19/09: `responder` em canal
    api COM bloqueio é aceito — o robô assume por outro caminho (Seller Center)
    e a fala presa na API sai da fila. `resolver` NÃO fecha: põe o chamado em
    Encerrado; `valor_recuperado` (qualquer ação) vira `valor_sugerido`.
    `humano` só anota (e reabre se `reabrir`).

    24/09: o cérebro cadastrado assina a análise ("Análise do robô Hermes …");
    o token antigo, depois da troca de guarda (`exclusivo`), leva 409."""
    if cerebro.substituido:
        logger.info("chamados_cerebro_legado_ignorado", rota="analise")
        raise HTTPException(409, detail={"code": "cerebro_substituido"})
    if cerebro.row is not None and not cerebro.row.ligada:
        # desligada na aba IA de Chamado: não decide nada
        raise HTTPException(409, detail={"code": "ia_desligada"})
    ch = await _get(session, body.chamado_id)
    # antes de gravar a análise (que consome a instrução)
    com_instrucao = _instrucao_pendente(await _mensagens_do_caso(session, ch)) is not None
    assumido: ChamadoMensagem | None = None
    # 25/09 (Vinicius, 296550: "a IA consegue responder pela própria API?"): no
    # canal `api` do Mercado Livre, com instrução de PESSOA, a IA responde direto
    # na reclamação (mesmo envio da Réplica manual). Sem instrução, segue só
    # sugerindo (`humano`). Shopee/TikTok não têm API de mensagem na disputa.
    via_api = (
        body.acao == "responder"
        and ch.canal == "api"
        and com_instrucao
        and svc._eh_ml(ch)
        and not (ch.origem_ref or "").startswith("tiktok_reembolso:")
    )
    if body.acao == "responder" and ch.canal != "robo" and not via_api:
        # Réplica de robô só sai pelo robô de browser (Tuta no ML; Seller Center
        # na Shopee/TikTok). Manual do ML: o robô ASSUME o chamado (Eduardo
        # 09/09: "para os manuais nós vamos tomar conta") — canal vira robô.
        # Canal `api` (devolução) só quando a plataforma NÃO libera pela API
        # (bloqueio) — Vinicius 19/09: o robô procura outro caminho; a fala
        # presa sai da fila com `substituida_pelo_robo`. Manual de outra
        # plataforma e api sem bloqueio: não tem por onde responder.
        e_ml = (ch.plataforma or "").strip().lower() in _PLATAFORMA_ML
        presa = (await session.execute(_ultima_fala_nossa(ch))).scalar_one_or_none()
        presa, bloqueio = _bloqueio_de([presa] if presa is not None else [])
        if ch.canal == "api" and presa is not None:
            presa.status = "falhou"
            presa.erro = "substituida_pelo_robo"
            aviso = (
                f"Chamado assumido pelo robô: a plataforma não libera pela API "
                f"({bloqueio.motivo or bloqueio.erro}) — o robô abre/responde no Seller Center"
            )
        elif ch.canal == "manual" and e_ml:
            aviso = "Chamado assumido pelo robô: as réplicas passam a sair pelo e-mail (Tuta)"
        else:
            raise HTTPException(
                422,
                detail={"code": "canal_sem_robo", "canal": ch.canal, "plataforma": ch.plataforma},
            )
        ch.canal = "robo"
        assumido = svc.nova_mensagem(
            ch,
            texto=aviso,
            tipo="sistema",
            direcao="sistema",
            autor_nome=AUTOR_CEREBRO,
            status="registrada",
        )
        assumido.canal = "robo"
        session.add(assumido)
    analise = svc.nova_mensagem(
        ch,
        texto=(
            f"Análise da {cerebro.nome} [{body.classe}]: " if cerebro.nome
            else f"Análise do robô [{body.classe}]: "
        )
        + f"{body.resumo} → {_ACAO_TXT[body.acao]}",
        tipo="analise",
        direcao="sistema",
        # 24/09: a IA de Chamado assina com o nome (a aba lista "o que ela decidiu"
        # por aqui); o cérebro antigo segue "cérebro".
        autor_nome=cerebro.nome or AUTOR_CEREBRO,
        status="registrada",
    )
    analise.canal = "robo"
    session.add(analise)
    replica: ChamadoMensagem | None = None
    # `esperar`+`reabrir`: o monitor antigo fechava o chamado na 1ª leitura —
    # se o caso ainda está vivo no ML (só a abertura, ou fomos nós que falamos
    # por último), o cérebro reabre pra aba mostrar que está em andamento.
    # 19/09 (ajuste): instrução NÃO reabre chamado Concluído por pessoa nem fechado
    # pela plataforma — quem quer o robô de volta num Concluído reabre pela aba
    # antes (o `/instrucao` recusa em resolvido, 422 `chamado_concluido`). Reabrir
    # sozinho continua valendo só pro que monitor/cérebro fecharam antes de 19/09.
    reabrir = body.acao == "responder" or (body.acao in ("humano", "esperar") and body.reabrir)
    if reabrir and ch.resolvido and await _cerebro_pode_reabrir(session, ch):
        session.add(svc.marcar_resolvido(ch, False, autor_nome=AUTOR_CEREBRO))
    if (
        body.acao == "responder"
        and com_instrucao
        and not ch.resolvido
        and ch.status_plataforma == svc.STATUS_ENCERRADO
    ):
        # Encerrado SEM decisão (monitor/robô disseram que a plataforma fechou) e a
        # pessoa mandou responder: o caso segue — sai do Encerrado. Ganhamos/perdemos
        # (decisão lida da API) ficam.
        ch.status_plataforma = None
        ch.status_plataforma_at = None
        session.add(
            svc.registrar_sistema(ch, "Chamado saiu de Encerrado: o robô vai responder por instrução")
        )
    if (
        body.acao != "resolver"
        and ch.status_plataforma == svc.STATUS_ENCERRADO
        and ch.status_plataforma_at is not None
        and await _plataforma_falou_depois(session, ch)
    ):
        # 25/09 (296550): a plataforma voltou a falar depois do Encerrado e a IA não
        # sugeriu fechar de novo — o caso está vivo.
        evento = svc.sair_de_encerrado(ch, "a plataforma voltou a falar depois do encerramento")
        if evento is not None:
            session.add(evento)
    if via_api:
        replica = svc.nova_mensagem(
            ch,
            texto=(body.texto_replica or "").strip(),
            tipo="replica",
            direcao="enviada",
            autor_nome=cerebro.nome or AUTOR_CEREBRO,
            status="pendente",
        )
        session.add(replica)
        await session.flush()
        await svc.enviar_mensagem(session, ch, replica)  # falha vira `falhou` + erro
    elif body.acao == "responder":
        replica = svc.nova_mensagem(
            ch,
            texto=(body.texto_replica or "").strip(),
            tipo="abertura" if not (ch.chamado or "").strip() else "replica",
            direcao="enviada",
            autor_nome=AUTOR_CEREBRO,
            status="pendente",
        )
        replica.canal = "robo"
        session.add(replica)
        await session.flush()
        if body.reanexar_abertura:
            for a in await _anexos_da_abertura(session, ch):
                session.add(
                    ChamadoAnexo(
                        chamado_id=ch.id,
                        mensagem_id=replica.id,
                        filename=a.filename,
                        content_type=a.content_type,
                        size_bytes=a.size_bytes,
                        blob=a.blob,
                        created_by=a.created_by,
                    )
                )
    if body.valor_recuperado is not None:
        # 19/09: sugestão — a pessoa confirma ao concluir (nunca mais grava direto).
        ch.valor_sugerido = body.valor_recuperado
    if body.observacao:
        atual = (ch.observacao or "").strip()
        ch.observacao = f"{atual}\n{body.observacao}" if atual else body.observacao
    if (
        body.acao == "resolver"
        and not ch.resolvido
        # Já decidido (ganhamos/perdemos lidos da API, ou Encerrado de antes): o
        # status fica como está e a sugestão de valor (acima) é tudo que o
        # `resolver` grava — sem evento repetido (19/09).
        and ch.status_plataforma not in svc.STATUS_FINAIS
        and svc.set_status_plataforma(ch, svc.STATUS_ENCERRADO)
    ):
        ch.auto_ligada = False  # não há mais a quem cobrar
        session.add(
            svc.registrar_sistema(
                ch, "Robô sugere fechar o chamado — aguardando fechamento por uma pessoa"
            )
        )
        # Tarefa presa na fila (abertura/réplica `pendente`) não faz sentido num
        # chamado que o próprio robô mandou fechar — sai da fila como `registrada`
        # (mesmo tratamento do `encerrar_chamado_por_motivo`), senão o lease a
        # entregava de novo e a coluna ficava "na fila do robô" pra sempre.
        presas = (
            await session.execute(
                select(ChamadoMensagem).where(
                    ChamadoMensagem.chamado_id == ch.id,
                    ChamadoMensagem.direcao == "enviada",
                    ChamadoMensagem.status == "pendente",
                )
            )
        ).scalars().all()
        for presa in presas:
            presa.status = "registrada"
            # abertura cancelada usa o código que a tela Devoluções já conhece
            presa.erro = "contestacao_cancelada" if presa.tipo == "abertura" else None
        if presas:
            session.add(
                svc.registrar_sistema(
                    ch,
                    f"{len(presas)} mensagem(ns) pendente(s) na fila cancelada(s) — o robô "
                    "sugeriu fechar o chamado",
                )
            )
    await session.commit()
    await session.refresh(analise)
    logger.info(
        "chamado_agent_analise",
        chamado_id=str(ch.id),
        classe=body.classe,
        acao=body.acao,
        replica=str(replica.id) if replica else None,
        cerebro=cerebro.nome,
    )
    return AgentAnaliseOut(
        chamado_id=ch.id,
        analise_id=analise.id,
        replica_id=replica.id if replica else None,
        resolvido=ch.resolvido,
    )


@agent_router.post("/exemplos", response_model=AgentExemplosOut)
async def agent_exemplos(
    body: AgentExemplosIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    cerebro: Annotated[_Cerebro, Depends(_cerebro)],
) -> AgentExemplosOut:
    """24/09 (Hermes): casos que já passaram pelo cérebro — conversa inteira,
    análises, e como terminaram (status oficial, valor sugerido × valor que a
    pessoa gravou ao concluir). Mais nova análise primeiro."""
    _so_cadastrado(cerebro)
    ana = (
        select(
            ChamadoMensagem.chamado_id, func.max(ChamadoMensagem.created_at).label("ult")
        )
        .where(ChamadoMensagem.tipo == "analise")
        .group_by(ChamadoMensagem.chamado_id)
        .subquery()
    )
    q = select(Chamado).join(ana, ana.c.chamado_id == Chamado.id)
    if body.desde is not None:
        q = q.where(ana.c.ult >= body.desde)
    if body.plataforma:
        plat = body.plataforma.lower()
        aceitas = _PLATAFORMA_ML if plat == "ml" else (plat,)
        q = q.where(func.lower(func.coalesce(Chamado.plataforma, "")).in_(aceitas))
    total = (await session.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (
        await session.execute(
            q.order_by(ana.c.ult.desc(), Chamado.id).offset(body.offset).limit(body.limite)
        )
    ).scalars().all()
    return AgentExemplosOut(
        total=total, chamados=[await _item_do_cerebro(session, ch) for ch in rows]
    )


@agent_router.post("/caso", response_model=AgentAnalisarOut)
async def agent_caso(
    body: AgentCasoIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    cerebro: Annotated[_Cerebro, Depends(_cerebro)],
) -> AgentAnalisarOut:
    """24/09 (Hermes): "no chamado do pedido X, vê como está / faz tal coisa" — o
    chamado pedido, no mesmo formato do `/agent/analisar`, esteja ele pendente ou
    não. Abertos primeiro, depois o mais novo; no máximo 5 (um pedido pode ter
    chamado de margem, de logística e de devolução)."""
    _so_cadastrado(cerebro)
    q = select(Chamado)
    if body.chamado_id:
        q = q.where(Chamado.id == body.chamado_id)
    if body.pedido_bling:
        q = q.where(Chamado.pedido_bling == body.pedido_bling)
    if body.chamado:
        q = q.where(Chamado.chamado == body.chamado)
    rows = (
        await session.execute(
            q.order_by(Chamado.resolvido, Chamado.created_at.desc()).limit(5)
        )
    ).scalars().all()
    return AgentAnalisarOut(chamados=[await _item_do_cerebro(session, ch) for ch in rows])


# Quanto do aprendizado vai pra IA em cada passada (as mais novas).
_APRENDIZADO_ERROS = 30
_APRENDIZADO_ACERTOS = 20


@agent_router.post("/cerebro", response_model=AgentCerebroOut)
async def agent_cerebro(
    body: AgentCerebroIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    cerebro: Annotated[_Cerebro, Depends(_cerebro)],
) -> AgentCerebroOut:
    """24/09: troca de guarda. `exclusivo: true` = este cérebro passa a ser o
    único (o token antigo para de ver e de decidir); `false` devolve a vez pro
    antigo; `null` só consulta."""
    row = _so_cadastrado(cerebro)
    if body.exclusivo is not None and body.exclusivo != row.exclusivo:
        if body.exclusivo:
            outro = (
                await session.execute(
                    select(ChamadoCerebro).where(
                        ChamadoCerebro.exclusivo.is_(True),
                        ChamadoCerebro.revoked_at.is_(None),
                        ChamadoCerebro.id != row.id,
                    )
                )
            ).scalar_one_or_none()
            if outro is not None:
                raise HTTPException(
                    409, detail={"code": "outro_cerebro_exclusivo", "cerebro": outro.nome}
                )
        row.exclusivo = body.exclusivo
        await session.commit()
        logger.info("chamados_cerebro_exclusivo", cerebro=row.nome, exclusivo=row.exclusivo)
    regras = (
        await session.execute(
            select(ChamadoIaRegra)
            .where(ChamadoIaRegra.ativa.is_(True))
            .order_by(ChamadoIaRegra.created_at)
        )
    ).scalars().all()
    # 24/09: o que a pessoa corrigiu (✗) e confirmou (✓) — mais novas primeiro
    avaliadas = (
        await session.execute(
            select(ChamadoIaAvaliacao, ChamadoMensagem, Chamado)
            .join(ChamadoMensagem, ChamadoMensagem.id == ChamadoIaAvaliacao.mensagem_id)
            .join(Chamado, Chamado.id == ChamadoIaAvaliacao.chamado_id)
            .order_by(ChamadoIaAvaliacao.updated_at.desc())
            .limit(200)
        )
    ).all()
    erradas = [x for x in avaliadas if not x[0].certo][:_APRENDIZADO_ERROS]
    certas = [x for x in avaliadas if x[0].certo][:_APRENDIZADO_ACERTOS]
    return AgentCerebroOut(
        nome=row.nome,
        exclusivo=row.exclusivo,
        ligada=row.ligada,
        last_used_at=row.last_used_at,
        legado_ignorado_at=row.legado_ignorado_at,
        regras=[
            AgentRegraOut(quando=r.quando, faca=r.faca, plataforma=r.plataforma) for r in regras
        ],
        aprendizado=[
            AgentAprendizadoOut(
                certo=av.certo,
                pedido_bling=ch.pedido_bling,
                plataforma=ch.plataforma,
                decisao=m.texto,
                correcao=av.correcao,
            )
            for av, m, ch in erradas + certas
        ],
    )


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
